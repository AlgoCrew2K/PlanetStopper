"""Regression guard — bounded intraday-replay parallelism (memory-blowup fix).

Context
-------
On 2026-06-11 and 2026-06-12 the test suite committed >90 GB and bugchecked
the host (CRITICAL_PROCESS_DIED). Root cause: nested all-core parallelism.
``synthetic_history.generate_synthetic_history`` ran
``joblib.Parallel(n_jobs=-1)`` (all cores) INSIDE each pytest-xdist worker; the
fan-out was multiplicative (xdist workers x cores).

Fix (reproducibility-neutral)
-----------------------------
The replay's per-day work is deterministic and order-independent (each tick's
Monte-Carlo seed is content-derived via
``math_engine.derive_cycle_mc_seed(f"{sym_id}_{date_str}")``), so the degree of
parallelism cannot change numerical results. The ``Parallel`` call now reads its
``n_jobs`` from ``synthetic_history._resolve_replay_n_jobs()``:
  * unset / garbled env  -> -1 (all cores) — UNCHANGED production behavior
  * ALPHABOT_MAX_JOBS=N  -> N
tests/conftest.py sets ``ALPHABOT_MAX_JOBS=1`` for the whole suite so the test
environment runs the replay single-process while production stays all-core.

These guards pin the fix so a future edit can't silently re-introduce the bare
``Parallel(n_jobs=-1)`` or drop the env throttle.
"""

from __future__ import annotations

import ast
import os
import pathlib

import synthetic_history

_MODULE_PATH = pathlib.Path(synthetic_history.__file__)


# ---------------------------------------------------------------------------
# Resolver behavior — prod default unchanged, env override honored.
# ---------------------------------------------------------------------------


def test_resolver_defaults_to_minus_one_when_unset(monkeypatch):
    """Unset ALPHABOT_MAX_JOBS -> -1 (all cores): production behavior preserved."""
    monkeypatch.delenv("ALPHABOT_MAX_JOBS", raising=False)
    assert synthetic_history._resolve_replay_n_jobs() == -1


def test_resolver_honors_env_override(monkeypatch):
    """A valid integer in ALPHABOT_MAX_JOBS is returned verbatim."""
    monkeypatch.setenv("ALPHABOT_MAX_JOBS", "1")
    assert synthetic_history._resolve_replay_n_jobs() == 1
    monkeypatch.setenv("ALPHABOT_MAX_JOBS", "4")
    assert synthetic_history._resolve_replay_n_jobs() == 4


def test_resolver_falls_back_to_minus_one_on_garbled(monkeypatch):
    """A non-integer value must not raise — falls back to the prod default."""
    monkeypatch.setenv("ALPHABOT_MAX_JOBS", "not-a-number")
    assert synthetic_history._resolve_replay_n_jobs() == -1
    monkeypatch.setenv("ALPHABOT_MAX_JOBS", "")
    assert synthetic_history._resolve_replay_n_jobs() == -1


# ---------------------------------------------------------------------------
# Static guard — no bare Parallel(n_jobs=-1); the call routes via the resolver.
# ---------------------------------------------------------------------------


def _parallel_calls(tree: ast.AST) -> list[ast.Call]:
    """Return all ast.Call nodes whose callee is ``Parallel`` (any import form)."""
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name == "Parallel":
                calls.append(node)
    return calls


def test_no_bare_all_core_parallel_call():
    """Parallel(n_jobs=...) must not receive a bare literal; _resolve_replay_n_jobs
    must still be consulted inside generate_synthetic_history (env fallback preserved).

    Safety contract after DE-AUTOTUNE-OOM (2026-06-29):
    - The ``Parallel`` call receives n_jobs via a variable (e.g. ``effective_n_jobs``),
      NOT a literal such as ``-1``.  A literal inside an xdist worker is the exact
      construct that caused the multiplicative fan-out / host bugcheck.
    - ``_resolve_replay_n_jobs()`` is still called somewhere in the body of
      ``generate_synthetic_history`` — the env-bounded fallback path is preserved for
      callers that do not supply an explicit n_jobs override.  Removing it silently
      breaks production behavior for all non-autotune callers.

    Two-part check:
      (A) No ``ast.Constant`` at any ``Parallel(n_jobs=<here>)`` site.
      (B) ``_resolve_replay_n_jobs()`` is reachable (called) inside
          ``generate_synthetic_history``'s AST body.

    Regression oracle: revert ``Parallel(n_jobs=effective_n_jobs)`` to
    ``Parallel(n_jobs=-1)`` and (A) fails; remove the ``_resolve_replay_n_jobs()``
    call from the assignment and (B) fails.
    """
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    calls = _parallel_calls(tree)
    assert calls, "Expected at least one Parallel(...) call in synthetic_history.py"

    # (A) Parallel(n_jobs=...) must receive a Name (variable reference) or Call —
    # never a literal or any other constant-like expression.  Positive allowlist
    # rather than a negative blocklist: in Python's AST, -1 is UnaryOp(USub,
    # Constant(1)), not a Constant, so ``not isinstance(val, ast.Constant)`` has a
    # gap.  Requiring ast.Name | ast.Call closes it definitively.
    for call in calls:
        n_jobs_kw = next((kw for kw in call.keywords if kw.arg == "n_jobs"), None)
        assert n_jobs_kw is not None, (
            f"Parallel(...) at line {call.lineno} has no n_jobs= kwarg; it must "
            "route n_jobs through the resolver or an explicit kwarg (never rely on "
            "joblib's default of -1)."
        )
        val = n_jobs_kw.value
        # Only ast.Name (variable) or ast.Call (function call) are safe.
        # Constant (e.g. 1, 0) and UnaryOp (e.g. -1 = USub(Constant(1))) are both
        # unsafe — they hardcode the degree of parallelism.
        is_safe = isinstance(val, (ast.Name, ast.Call))
        assert is_safe, (
            f"Parallel(n_jobs=<literal or expression>) at line {call.lineno} — "
            f"got {type(val).__name__}({ast.dump(val)!r:.120}). "
            "Only a variable name or a function call is allowed. A bare literal "
            "(including -1, which is UnaryOp not Constant in the AST) re-introduces "
            "the unbounded fan-out. Use a variable derived from "
            "_resolve_replay_n_jobs() or an explicit caller kwarg."
        )

    # (B) _resolve_replay_n_jobs() must be called inside generate_synthetic_history —
    # the env fallback is what bounds all non-autotune callers (n_jobs=None default).
    func_node = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "generate_synthetic_history"
        ),
        None,
    )
    assert func_node is not None, (
        "generate_synthetic_history function not found in synthetic_history.py"
    )
    resolver_calls = [
        node
        for node in ast.walk(func_node)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_resolve_replay_n_jobs"
    ]
    assert resolver_calls, (
        "_resolve_replay_n_jobs() must be called somewhere inside "
        "generate_synthetic_history — the env fallback path must be preserved for "
        "callers that do not pass an explicit n_jobs. Removing it silently breaks "
        "env-bounded production behavior."
    )


def test_conftest_sets_job_caps_in_test_environment():
    """The autouse pytest_configure must default the parallelism caps to 1.

    This is the live throttle: it neutralizes both the joblib replay and Optuna
    n_jobs inside xdist workers. Because pytest_configure already ran for this
    session, the env vars must be present (whatever conftest/operator set them
    to). The static check below pins that conftest declares the defaults.
    """
    # Live: the session has been configured, so the var must exist.
    assert os.environ.get("ALPHABOT_MAX_JOBS"), (
        "ALPHABOT_MAX_JOBS is not set in the test environment — conftest "
        "pytest_configure must os.environ.setdefault it to '1'."
    )
    conftest = pathlib.Path(__file__).parent.parent / "conftest.py"
    src = conftest.read_text(encoding="utf-8")
    assert 'setdefault("ALPHABOT_MAX_JOBS", "1")' in src, (
        "tests/conftest.py must os.environ.setdefault('ALPHABOT_MAX_JOBS', '1') "
        "in pytest_configure to bound the joblib replay fan-out."
    )
    assert 'setdefault("OPTUNA_N_JOBS", "1")' in src, (
        "tests/conftest.py must os.environ.setdefault('OPTUNA_N_JOBS', '1') "
        "in pytest_configure to bound the Optuna study fan-out."
    )
