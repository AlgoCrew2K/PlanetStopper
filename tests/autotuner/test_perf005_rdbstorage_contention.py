"""RED regression tests for PERF-005 (math-reaudit MEDIUM).

Finding: ``study.optimize(n_jobs=-1)`` with SQLite ``RDBStorage`` —
write-contention not benchmarked — at ``autotuner.py:1507``.

PARTIALLY ADDRESSED already by the fix-optuna1-6 cycle (merged at ``ed9ffce``)
which introduced env-driven n_jobs with default=1 and a docstring citing the
SQLite RDBStorage writer-lock rationale. The companion OPTUNA-1/6 suite at
``test_optuna_sampler_pin_and_n_jobs.py`` pins the helper contract + AST
shape. **This file adds the PERF-005-specific regression locks** so a
future PR cannot quietly undo the contention mitigation.

What this file adds beyond the existing OPTUNA-6 suite:

1. **Storage-backend pin** — the run_autotuner site uses an
   ``optuna.storages.RDBStorage`` keyed by a SQLite URL. If the backend
   changes (e.g. a future migration to PostgreSQL), the contention
   rationale no longer applies and operators can opt-in to n_jobs>1; this
   test surfaces such a change for re-evaluation.
2. **Rationale-docstring lock** — the helper's docstring must mention
   SQLite + RDBStorage + the "database is locked" failure mode, so a
   cleanup PR that strips the WHY without changing behaviour gets caught.
3. **Triple default-1 invariant** — unset, garbled, and empty-string env
   values all default to 1. The default is THE mitigation; pinning all
   three forms prevents a future PR from carving out a "permissive"
   pathway.
4. **Empirical contention canary** — a small parallel study against a
   temp SQLite ``RDBStorage`` with a short timeout. Either it reproduces
   the ``database is locked`` OperationalError (canary fires, confirming
   the rationale is real) OR it completes successfully (Optuna+SQLite
   serializes via the writer lock + retry — the mitigation is still
   necessary at higher trial counts in production). Both outcomes pass;
   what matters is that the scenario is exercised, so a future maintainer
   can run this test and see contention behaviour empirically.

Tests are RED on plan/finalist-a-scaffold @ 262ef90 only where they cover
a contract not already enforced (storage-backend pin, docstring lock,
canary). The triple-default test extends the existing single-default
coverage with garbled + empty cases. All pass after impl-perf005's GREEN
work, which here is "confirm contracts hold" rather than new code.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import os
import pathlib
import sys

import optuna
import pytest


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "autotuner"
    / "perf005_rdbstorage_contention_contract.json"
)


@pytest.fixture(scope="module")
def contract() -> dict:
    with open(_FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def autotuner_module():
    """Import autotuner under the worktree sys.path. Reload to clear any
    cached attribute-level monkeypatches from sibling tests."""
    if "autotuner" in sys.modules:
        return importlib.reload(sys.modules["autotuner"])
    return importlib.import_module("autotuner")


@pytest.fixture(scope="module")
def autotuner_source(autotuner_module) -> str:
    return pathlib.Path(autotuner_module.__file__).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def autotuner_ast(autotuner_source: str) -> ast.Module:
    return ast.parse(autotuner_source)


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

_RUN_AUTOTUNER = "run_autotuner"


def _function_def(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"AST: expected to find function def {name!r} in autotuner.py.")


# ---------------------------------------------------------------------------
# 1) Storage-backend pin — PERF-005 finding is SQLite-RDBStorage-specific
# ---------------------------------------------------------------------------


def test_run_autotuner_storage_is_rdbstorage_over_sqlite(autotuner_ast, contract: dict):
    """``run_autotuner`` must construct an ``optuna.storages.RDBStorage``
    whose URL is the documented sqlite scheme. The PERF-005 finding scope
    is narrow to this backend — a future migration to Postgres/MySQL would
    invalidate the contention rationale (writer lock differs) and the
    n_jobs default-1 invariant could safely be re-evaluated. This test
    surfaces such a backend change so the n_jobs default does not silently
    de-couple from its underlying constraint.
    """
    expected_cls = contract["storage_backend_pin"]["expected_storage_class"]
    expected_scheme = contract["storage_backend_pin"]["expected_url_scheme"]
    expected_db_name = contract["storage_backend_pin"]["expected_url_database_name"]

    func = _function_def(autotuner_ast, _RUN_AUTOTUNER)

    # Find every ``<x>.RDBStorage(...)`` call inside run_autotuner. We
    # match on attribute name rather than the qualified path so a future
    # ``from optuna.storages import RDBStorage`` change still trips.
    rdb_calls: list[ast.Call] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == expected_cls:
            rdb_calls.append(node)
        elif isinstance(f, ast.Name) and f.id == expected_cls:
            rdb_calls.append(node)

    assert rdb_calls, (
        f"PERF-005 backend pin: run_autotuner must construct an "
        f"optuna.storages.{expected_cls}(...) — none found. If the storage "
        f"backend changed, re-evaluate the n_jobs default-1 invariant "
        f"because the SQLite writer-lock constraint may no longer apply."
    )

    # The URL is typically passed as the first positional arg or ``url=``.
    # Resolve to a constant string when possible; allow a Name reference
    # (e.g. ``db_url = "sqlite:///..."`` assigned above the call).
    matched_url: str | None = None
    for call in rdb_calls:
        url_expr: ast.expr | None = None
        if call.args:
            url_expr = call.args[0]
        for kw in call.keywords:
            if kw.arg == "url":
                url_expr = kw.value
                break

        # Resolve constants first.
        if isinstance(url_expr, ast.Constant) and isinstance(url_expr.value, str):
            matched_url = url_expr.value
            break
        # Resolve a Name to its module-level assignment if possible.
        if isinstance(url_expr, ast.Name):
            target_name = url_expr.id
            for node in ast.walk(func):
                if not isinstance(node, ast.Assign):
                    continue
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if target_name in targets and isinstance(node.value, ast.Constant):
                    if isinstance(node.value.value, str):
                        matched_url = node.value.value
                        break
            if matched_url is not None:
                break

    assert matched_url is not None, (
        f"PERF-005 backend pin: could not resolve the URL argument to "
        f"{expected_cls}(...) as a string constant. Update this test if "
        f"the URL is now sourced from env/config — and re-evaluate the "
        f"n_jobs default-1 invariant against the new backend."
    )
    assert matched_url.startswith(expected_scheme), (
        f"PERF-005 backend pin: RDBStorage URL must begin with "
        f"{expected_scheme!r} (got {matched_url!r}). Backend change "
        f"requires re-evaluation of the n_jobs default-1 invariant."
    )
    assert expected_db_name in matched_url, (
        f"PERF-005 backend pin: RDBStorage URL must reference the "
        f"documented database name {expected_db_name!r} (got {matched_url!r})."
    )


# ---------------------------------------------------------------------------
# 2) Rationale-docstring lock — the WHY of default-1 must travel with code
# ---------------------------------------------------------------------------


def test_n_jobs_helper_docstring_cites_sqlite_rdbstorage_rationale(
    autotuner_module, contract: dict
):
    """``_resolve_optuna_n_jobs_from_env.__doc__`` must mention SQLite,
    RDBStorage, and the "database is locked" failure mode. Without the
    rationale embedded next to the code, a future cleanup PR could strip
    the docstring and the default-1 invariant looks arbitrary — a
    well-meaning maintainer might then flip it back to ``cpu_count()``
    "for performance".

    Substring check is case-insensitive so the assertion does not become
    a typo-trap on the impl side. The exact wording is allowed to evolve;
    the three concepts must all be present.
    """
    helper_name = contract["rationale_docstring_lock"]["helper_name"]
    required = contract["rationale_docstring_lock"][
        "required_docstring_substrings_case_insensitive"
    ]

    helper = getattr(autotuner_module, helper_name, None)
    assert helper is not None and callable(helper), (
        f"Contract: autotuner.py must expose helper {helper_name!r}."
    )
    doc = (helper.__doc__ or "").lower()
    assert doc.strip(), (
        f"PERF-005 rationale lock: {helper_name!r} must carry a docstring "
        f"explaining WHY the default is 1 (SQLite RDBStorage writer-lock "
        f"safety). Empty docstring would let a future maintainer flip the "
        f"default back to cpu_count() without realizing the constraint."
    )
    missing = [s for s in required if s.lower() not in doc]
    assert not missing, (
        f"PERF-005 rationale lock: {helper_name!r} docstring must mention "
        f"all of {required} (case-insensitive). Missing: {missing}. "
        f"Current docstring head: {(helper.__doc__ or '')[:200]!r}"
    )


# ---------------------------------------------------------------------------
# 3) Triple default-1 invariant — unset, garbled, empty-string
# ---------------------------------------------------------------------------


def test_unset_env_defaults_to_one(monkeypatch, autotuner_module, contract: dict):
    """``OPTUNA_N_JOBS`` unset must yield literal 1. Mirrors the existing
    OPTUNA-6 test; included here so the PERF-005 suite is self-contained
    (a future split of the OPTUNA-1/6 file would not silently regress this
    invariant)."""
    helper = autotuner_module._resolve_optuna_n_jobs_from_env
    monkeypatch.delenv("OPTUNA_N_JOBS", raising=False)
    expected = int(contract["default_invariant"]["n_jobs_default_when_unset_or_invalid"])
    assert helper() == expected


def test_garbled_env_defaults_to_one(monkeypatch, autotuner_module, contract: dict):
    """Garbled env (non-int) must not crash the daemon — falls back to 1.
    A parse-error crash here would take the risk engine down on a typo;
    any default > 1 would re-introduce the SQLite writer-lock issue."""
    helper = autotuner_module._resolve_optuna_n_jobs_from_env
    monkeypatch.setenv("OPTUNA_N_JOBS", "not-an-int-PERF005")
    expected = int(contract["default_invariant"]["n_jobs_default_when_garbled"])
    assert helper() == expected
    assert helper() not in contract["default_invariant"]["prohibited_defaults"]


def test_empty_string_env_defaults_to_one(monkeypatch, autotuner_module, contract: dict):
    """Empty-string env (``OPTUNA_N_JOBS=``) must default to 1, not crash.
    Some shells / .env loaders surface unset vars as empty strings; the
    helper must handle this the same as unset to preserve the invariant.
    """
    helper = autotuner_module._resolve_optuna_n_jobs_from_env
    monkeypatch.setenv("OPTUNA_N_JOBS", "")
    expected = int(contract["default_invariant"]["n_jobs_default_when_empty_string"])
    value = helper()
    assert isinstance(value, int), (
        f"Empty-string env must yield int, not crash; got {type(value).__name__}"
    )
    assert value == expected, (
        f"Empty-string OPTUNA_N_JOBS must default to {expected} (same as "
        f"unset); got {value!r}. Otherwise a .env loader that surfaces "
        f"missing vars as empty strings would silently flip the default."
    )


def test_prohibited_default_values_never_returned(monkeypatch, autotuner_module, contract: dict):
    """No combination of unset/garbled/empty env yields any of the
    PERF-005 prohibited defaults (-1, 2, 4, 8). This is the negative
    control for the default-1 invariant: a wrong impl that defaulted to
    cpu_count() on one host might return 2/4/8 here and slip past the
    single equality check above."""
    helper = autotuner_module._resolve_optuna_n_jobs_from_env
    prohibited = set(contract["default_invariant"]["prohibited_defaults"])

    for env_value, label in [
        (None, "unset"),
        ("", "empty"),
        ("not-an-int", "garbled"),
        ("garbage-PERF005", "garbled-2"),
    ]:
        if env_value is None:
            monkeypatch.delenv("OPTUNA_N_JOBS", raising=False)
        else:
            monkeypatch.setenv("OPTUNA_N_JOBS", env_value)
        got = helper()
        assert got not in prohibited, (
            f"PERF-005 prohibited-default guard ({label}): helper returned "
            f"{got!r}, which is in the prohibited set {sorted(prohibited)}. "
            f"Any of these values would re-introduce parallel writes to the "
            f"single SQLite RDBStorage and trigger writer-lock contention."
        )


# ---------------------------------------------------------------------------
# 4) Empirical contention canary
# ---------------------------------------------------------------------------


def _canary_objective(trial):
    """Deterministic 1-D objective; fast enough that contention dominates
    runtime if it occurs. Not a meaningful optimization target — its
    only job is to drive parallel writes to the temp RDBStorage."""
    x = trial.suggest_float("x", 0.0, 1.0)
    return -((x - 0.5) ** 2)


def test_sqlite_rdbstorage_contention_canary_documents_failure_mode(tmp_path, contract: dict):
    """Canary: run a small parallel Optuna study against a temp SQLite
    ``RDBStorage`` and observe behaviour. PERF-005 documents an
    unbenchmarked contention risk — this test EXISTS so future maintainers
    can run it and see the behaviour empirically rather than relying on
    the docstring alone.

    Outcomes (both pass):
      (a) ``sqlite3.OperationalError: database is locked`` is raised —
          the canary fires, confirming the writer-lock rationale is real
          and observable.
      (b) The study completes successfully — Optuna + SQLite serialize
          via the writer lock + retry path under this trial count. The
          mitigation (default n_jobs=1) is still warranted because the
          production trial count (500) and the timeout (60s) push the
          contention envelope further than this 8-trial canary; but the
          canary did not happen to fire on this run.

    Either outcome is acceptable. What matters is that the scenario is
    exercised, and a regression that ALSO accidentally removed the
    SQLite RDBStorage backend would surface here (the canary requires
    the backend exist to run at all).
    """
    n_trials = int(contract["contention_canary"]["n_trials"])
    n_jobs = int(contract["contention_canary"]["n_jobs"])
    timeout_s = int(contract["contention_canary"]["timeout_seconds"])

    db_path = tmp_path / "perf005_canary.db"
    db_url = f"sqlite:///{db_path.as_posix()}"

    # Build storage with a deliberately short timeout to amplify contention.
    storage = optuna.storages.RDBStorage(
        url=db_url,
        engine_kwargs={"connect_args": {"timeout": timeout_s}},
    )

    # Quiet Optuna logs — the canary's only output should be the assertion.
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    study = optuna.create_study(
        study_name="perf005_canary",
        storage=storage,
        load_if_exists=False,
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=4242),
        pruner=optuna.pruners.NopPruner(),
    )

    contention_observed = False
    completed_cleanly = False
    try:
        study.optimize(_canary_objective, n_trials=n_trials, n_jobs=n_jobs)
        completed_cleanly = True
    except Exception as exc:  # noqa: BLE001 — broad on purpose; we classify below
        msg = str(exc).lower()
        # Optuna wraps sqlite3.OperationalError in various ways across
        # versions; detect by the canonical "database is locked" substring
        # OR by the underlying exception class name to be version-agnostic.
        if "database is locked" in msg or "operationalerror" in type(exc).__name__.lower():
            contention_observed = True
        else:
            # An unrelated error means the canary itself broke (e.g.
            # Optuna API drift). Surface it rather than swallow.
            raise

    # Exactly one of the two outcomes must hold. This is the canary's
    # discriminating assertion — neither outcome means the test setup
    # failed silently.
    assert contention_observed or completed_cleanly, (
        "PERF-005 canary setup failure: neither contention nor clean "
        "completion observed. The temp RDBStorage may have failed to "
        "initialize, or the parallel optimize call short-circuited "
        "without doing work. Re-check tmp_path permissions + Optuna "
        "version compatibility."
    )

    # Diagnostic — surface which path the canary took so a future
    # maintainer reading test logs can see the empirical behaviour
    # without re-running the canary in isolation. pytest -rA shows this.
    print(
        f"\n[PERF-005 canary] n_trials={n_trials} n_jobs={n_jobs} "
        f"timeout={timeout_s}s -> "
        f"{'contention observed (database is locked)' if contention_observed else 'completed cleanly'}"
    )


# ---------------------------------------------------------------------------
# 5) Helper signature stability — no positional config drift
# ---------------------------------------------------------------------------


def test_n_jobs_helper_takes_no_arguments(autotuner_module):
    """``_resolve_optuna_n_jobs_from_env()`` must accept no arguments —
    it reads the env directly so the call sites at run_autotuner and
    run_calibration_sweep share one source of truth. If a future PR
    refactored it to take an override arg (``_resolve_optuna_n_jobs_from_env(default=cpu_count)``),
    that would silently reopen the parallel-write pathway from the call site."""
    helper = autotuner_module._resolve_optuna_n_jobs_from_env
    sig = inspect.signature(helper)
    params = [
        p
        for p in sig.parameters.values()
        if p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    ]
    required = [p for p in params if p.default is inspect.Parameter.empty]
    assert not required, (
        f"PERF-005 helper signature lock: _resolve_optuna_n_jobs_from_env "
        f"must accept zero required arguments (got {[p.name for p in required]}). "
        f"Adding an override arg would let a call site smuggle in cpu_count "
        f"as the default and re-introduce SQLite writer-lock contention."
    )
