"""RED tests for OPTUNA-1 (sampler pin) + OPTUNA-6 (n_jobs from env) audit fixes.

Background — math re-audit findings (both HIGH):

OPTUNA-1: The main run_autotuner study site (autotuner.py:1500-1507) calls
``optuna.create_study(...)`` with no ``sampler=`` kwarg, accepting Optuna's
implicit default (TPESampler with seed=None). The BHY c(N) Yekutieli factor
in the Harvey & Liu haircut is calibrated to TPE-dependency assumptions; a
silent sampler swap (e.g. an Optuna minor-version default change) would
invalidate the calibration without any in-tree signal. The sampler must be
pinned to ``TPESampler`` with the seed sourced from a named env constant.

OPTUNA-6: The same site hardcodes ``n_jobs=-1``. Per project rule
"read n_jobs from .env; never hardcode CPU counts." Must be sourced from a
named env constant, defaulting to ``os.cpu_count() or 1`` when unset.

Reference site already exhibiting the correct pin pattern: ``run_calibration_sweep``
at autotuner.py:1877-1884 — explicit ``TPESampler(seed=random_state)`` + ``n_jobs=1``.

All tests are RED on cycle/fix-optuna1-6 @ 8d38a43 (the parent commit) and
must remain green after implementation. The site is located by AST walk over
``autotuner.py``, not by line numbers — refactors that re-flow the site
without restoring the pin will still fail.
"""

from __future__ import annotations

import ast
import importlib
import json
import os
import pathlib
import sys
import types

import optuna
import pytest


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "autotuner"
    / "optuna_sampler_pin_contract.json"
)


@pytest.fixture(scope="module")
def pin_contract() -> dict:
    with open(_FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def autotuner_module():
    """Import autotuner under the worktree sys.path; reload to clear any
    stale cached module state from sibling tests (some tests in this tree
    monkeypatch autotuner attributes at module level)."""
    if "autotuner" in sys.modules:
        return importlib.reload(sys.modules["autotuner"])
    return importlib.import_module("autotuner")


@pytest.fixture(scope="module")
def autotuner_source() -> str:
    import autotuner as _at

    return pathlib.Path(_at.__file__).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def autotuner_ast(autotuner_source: str) -> ast.Module:
    return ast.parse(autotuner_source)


# ---------------------------------------------------------------------------
# AST helpers — locate the run_autotuner main create_study site
# ---------------------------------------------------------------------------

_RUN_AUTOTUNER = "run_autotuner"
_RUN_CALIBRATION = "run_calibration_sweep"


def _function_def(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(
        f"AST: expected to find function def {name!r} in autotuner.py — "
        "the test cannot run without it. Did the function get renamed?"
    )


def _create_study_calls(func: ast.FunctionDef) -> list[ast.Call]:
    """Return every ``optuna.create_study(...)`` call inside ``func``."""
    calls: list[ast.Call] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        # Match ``optuna.create_study`` (Attribute on Name "optuna").
        if isinstance(f, ast.Attribute) and f.attr == "create_study":
            calls.append(node)
    return calls


def _study_optimize_calls(func: ast.FunctionDef) -> list[ast.Call]:
    """Return every ``<x>.optimize(...)`` call inside ``func``.

    The site uses a local name ``study``; we match on attribute name
    ``optimize`` so a refactor that renames the local still trips the test.
    """
    calls: list[ast.Call] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "optimize":
            calls.append(node)
    return calls


def _kw(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


# ---------------------------------------------------------------------------
# Module-level named constants for the env-var names (OPTUNA-1 + OPTUNA-6)
# ---------------------------------------------------------------------------


def test_autotuner_exposes_named_env_var_constants(autotuner_module, pin_contract):
    """``OPTUNA_SAMPLER_SEED`` and ``OPTUNA_N_JOBS`` env-var names must live
    as module-level named constants in autotuner.py — not as repeated string
    literals at the call site. Per Planet Stopper Coding Standards "No magic
    numbers in math_engine.py — every constant named + source comment";
    same discipline applies to env-var string keys driving the math engine's
    upstream sampler.
    """
    seed_env = pin_contract["constants"]["sampler_seed_env_var"]
    n_jobs_env = pin_contract["constants"]["n_jobs_env_var"]

    # Find at least one module-level attribute whose VALUE equals each env
    # name. We don't pin the attribute name itself (impl is free to call it
    # _OPTUNA_SAMPLER_SEED_ENV or OPTUNA_SAMPLER_SEED_ENV_VAR) but we DO
    # require it exist as a named constant — searched via value identity on
    # public + private module attrs.
    attrs = {
        name: getattr(autotuner_module, name)
        for name in dir(autotuner_module)
        if isinstance(getattr(autotuner_module, name, None), str)
    }
    assert seed_env in attrs.values(), (
        f"autotuner.py must expose a module-level named constant whose "
        f"value is {seed_env!r} (the OPTUNA-1 sampler-seed env var). "
        f"Found string constants: {sorted(attrs.values())[:20]}..."
    )
    assert n_jobs_env in attrs.values(), (
        f"autotuner.py must expose a module-level named constant whose "
        f"value is {n_jobs_env!r} (the OPTUNA-6 n_jobs env var). "
        f"Found string constants: {sorted(attrs.values())[:20]}..."
    )


# ---------------------------------------------------------------------------
# OPTUNA-1: explicit TPESampler at the run_autotuner site
# ---------------------------------------------------------------------------


def test_run_autotuner_create_study_passes_explicit_sampler(autotuner_ast):
    """The ``run_autotuner`` main study site must call ``create_study(...)``
    with an explicit ``sampler=`` kwarg — never relying on Optuna's
    implicit default. This guards the BHY haircut Yekutieli calibration
    (audit OPTUNA-1).
    """
    func = _function_def(autotuner_ast, _RUN_AUTOTUNER)
    calls = _create_study_calls(func)
    assert calls, (
        "AST: run_autotuner is expected to contain an optuna.create_study(...) "
        "call (none found). Did the call site move out of run_autotuner?"
    )
    missing = [c for c in calls if _kw(c, "sampler") is None]
    assert not missing, (
        f"OPTUNA-1: every optuna.create_study(...) call inside run_autotuner "
        f"must pass an explicit sampler= kwarg (got {len(missing)} site(s) "
        f"without one, at line(s) {[c.lineno for c in missing]}). "
        "Implicit default TPESampler(seed=None) breaks BHY haircut calibration."
    )


def test_run_autotuner_sampler_is_tpe_with_seed_kwarg(autotuner_ast):
    """The sampler must be ``optuna.samplers.TPESampler(seed=...)`` —
    same family as the calibration site; seed kwarg must be present
    (its expression is allowed to be anything that evaluates to int|None
    from the env)."""
    func = _function_def(autotuner_ast, _RUN_AUTOTUNER)
    calls = _create_study_calls(func)
    bad: list[str] = []
    for call in calls:
        sampler_expr = _kw(call, "sampler")
        if sampler_expr is None:
            continue  # handled by previous test
        # Expect a Call whose func is Attribute(...attr='TPESampler') OR
        # Name 'TPESampler'.
        if not isinstance(sampler_expr, ast.Call):
            bad.append(
                f"line {call.lineno}: sampler= must be a TPESampler(...) call, "
                f"got {type(sampler_expr).__name__}"
            )
            continue
        f = sampler_expr.func
        is_tpe = (isinstance(f, ast.Attribute) and f.attr == "TPESampler") or (
            isinstance(f, ast.Name) and f.id == "TPESampler"
        )
        if not is_tpe:
            bad.append(f"line {call.lineno}: sampler= must construct TPESampler, got {ast.dump(f)}")
            continue
        # Must pass an explicit ``seed=`` kwarg.
        if _kw(sampler_expr, "seed") is None:
            bad.append(
                f"line {call.lineno}: TPESampler(...) must pass an explicit "
                f"seed= kwarg (source it from the env-var constant)."
            )
    assert not bad, "OPTUNA-1 sampler pin violations:\n  - " + "\n  - ".join(bad)


def test_run_autotuner_sampler_seed_sourced_from_env_constant(
    autotuner_source: str, pin_contract: dict
):
    """The sampler seed must be sourced from os.environ keyed by the
    OPTUNA-1 named env-var constant. Plain integer literals (e.g. seed=42)
    or seed=None would defeat the audit fix."""
    seed_env = pin_contract["constants"]["sampler_seed_env_var"]
    # Look for the env-var name being read via os.environ / os.getenv inside
    # the run_autotuner function body. AST-walk the function.
    tree = ast.parse(autotuner_source)
    func = _function_def(tree, _RUN_AUTOTUNER)
    env_reads_for_seed = False
    for node in ast.walk(func):
        if not isinstance(node, ast.Constant):
            continue
        if node.value == seed_env:
            env_reads_for_seed = True
            break
        # Allow the impl to reference the named module-level constant
        # instead of the literal string; the constants-existence test
        # above already pins the constant -> value mapping, so a literal
        # of seed_env anywhere in the function is the explicit signal.
    assert env_reads_for_seed, (
        f"OPTUNA-1: the run_autotuner sampler seed must be sourced from the "
        f"env var {seed_env!r} (literal name expected to appear in the "
        f"function body, either as a module-level constant reference whose "
        f"value the constants test pins, or as an explicit os.environ key). "
        f"Could not find {seed_env!r} as a Constant inside run_autotuner."
    )


# ---------------------------------------------------------------------------
# OPTUNA-6: n_jobs read from env, never hardcoded -1
# ---------------------------------------------------------------------------


def test_run_autotuner_study_optimize_n_jobs_not_hardcoded_minus_one(autotuner_ast):
    """``study.optimize(..., n_jobs=...)`` inside run_autotuner must not
    pass the literal -1 (audit OPTUNA-6 prohibition)."""
    func = _function_def(autotuner_ast, _RUN_AUTOTUNER)
    calls = _study_optimize_calls(func)
    assert calls, "AST: expected at least one study.optimize(...) call in run_autotuner."
    offenders: list[str] = []
    for call in calls:
        n_jobs_expr = _kw(call, "n_jobs")
        if n_jobs_expr is None:
            # No n_jobs kwarg at all: Optuna defaults to 1. That's
            # acceptable (not a CPU-count hardcode). Skip.
            continue
        # Reject literal -1 (UnaryOp(USub, Constant(1))) OR Constant(-1).
        is_minus_one_unary = (
            isinstance(n_jobs_expr, ast.UnaryOp)
            and isinstance(n_jobs_expr.op, ast.USub)
            and isinstance(n_jobs_expr.operand, ast.Constant)
            and n_jobs_expr.operand.value == 1
        )
        is_minus_one_const = (
            isinstance(n_jobs_expr, ast.Constant)
            and isinstance(n_jobs_expr.value, int)
            and n_jobs_expr.value == -1
        )
        if is_minus_one_unary or is_minus_one_const:
            offenders.append(
                f"line {call.lineno}: study.optimize(..., n_jobs=-1) is the "
                f"OPTUNA-6 prohibited hardcode. Read from "
                f"os.environ[OPTUNA_N_JOBS_ENV] with a sane default instead."
            )
    assert not offenders, "OPTUNA-6 violations:\n  - " + "\n  - ".join(offenders)


def test_run_autotuner_n_jobs_sourced_from_env_constant(autotuner_source: str, pin_contract: dict):
    """The n_jobs value must be sourced from the OPTUNA-6 env-var constant
    inside run_autotuner."""
    n_jobs_env = pin_contract["constants"]["n_jobs_env_var"]
    tree = ast.parse(autotuner_source)
    func = _function_def(tree, _RUN_AUTOTUNER)
    found = any(
        isinstance(node, ast.Constant) and node.value == n_jobs_env for node in ast.walk(func)
    )
    assert found, (
        f"OPTUNA-6: run_autotuner must reference the env var {n_jobs_env!r} "
        f"to source n_jobs. Could not find {n_jobs_env!r} as a Constant "
        f"inside the function body."
    )


# ---------------------------------------------------------------------------
# Behavioural pass-through: env -> create_study(sampler=...) + optimize(n_jobs=...)
# ---------------------------------------------------------------------------


def test_env_seed_flows_through_to_create_study_sampler(
    monkeypatch, autotuner_module, pin_contract
):
    """Set ``OPTUNA_SAMPLER_SEED`` in the environment and confirm that the
    sampler passed to ``optuna.create_study`` carries that seed. The test
    intercepts ``optuna.create_study`` via monkeypatch and inspects the
    captured sampler.

    The site is exercised by invoking the smallest run_autotuner-internal
    construction path possible — but since run_autotuner has heavy
    preconditions (spec bundles, history fetch, DB), we instead intercept
    the import-time module reload + the helper that builds the sampler.
    The impl is REQUIRED to expose a helper (any private name) that
    constructs the sampler from env; this test pins behaviour via that
    boundary by asserting that the seed of TPESampler used at the site
    reflects the env.

    For test-writability without re-running the full daemon, we observe
    the env -> int conversion + TPESampler construction via a small
    indirection: we patch ``optuna.samplers.TPESampler`` to capture init
    kwargs, then call the public helper.
    """
    seed_env = pin_contract["constants"]["sampler_seed_env_var"]
    monkeypatch.setenv(seed_env, "1234567")

    # Capture TPESampler init kwargs.
    captured: list[dict] = []
    real_tpe = optuna.samplers.TPESampler

    class _CapturingTPE(real_tpe):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):  # noqa: D401
            captured.append({"args": args, "kwargs": kwargs})
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(optuna.samplers, "TPESampler", _CapturingTPE)
    # The autotuner module captures ``optuna.samplers.TPESampler`` at
    # call-time (it does ``optuna.samplers.TPESampler(...)`` inside the
    # function, not at import), so the monkeypatch is visible.

    # The impl MUST expose a helper that returns the sampler. Conventionally
    # ``_build_optuna_sampler_from_env()``; the contract is "callable on
    # the module that constructs a TPESampler from env-driven seed".
    helper = getattr(autotuner_module, "_build_optuna_sampler_from_env", None)
    assert helper is not None and callable(helper), (
        "OPTUNA-1 implementation contract: autotuner.py must expose a "
        "module-level helper `_build_optuna_sampler_from_env()` that reads "
        "OPTUNA_SAMPLER_SEED from os.environ and returns "
        "optuna.samplers.TPESampler(seed=...). This boundary is what the "
        "RED behavioural tests pin so the run_autotuner site cannot drift "
        "without breaking either the AST tests or this contract."
    )

    sampler = helper()
    assert isinstance(sampler, real_tpe), (
        f"_build_optuna_sampler_from_env() must return a TPESampler, got {type(sampler).__name__}"
    )
    assert captured, (
        "TPESampler was not constructed by the helper — the helper must "
        "instantiate optuna.samplers.TPESampler(seed=...)."
    )
    last_kwargs = captured[-1]["kwargs"]
    assert "seed" in last_kwargs, (
        "TPESampler(...) must be constructed with an explicit seed= kwarg "
        f"(got kwargs={last_kwargs})"
    )
    assert last_kwargs["seed"] == 1234567, (
        f"Env OPTUNA_SAMPLER_SEED=1234567 must flow through to "
        f"TPESampler(seed=1234567); got seed={last_kwargs['seed']!r}."
    )


def test_unset_seed_env_yields_seed_none(monkeypatch, autotuner_module, pin_contract):
    """When ``OPTUNA_SAMPLER_SEED`` is unset, the helper must return a
    TPESampler with seed=None — preserving prior wall-clock-seeded
    behaviour for operators who do not opt in to determinism."""
    seed_env = pin_contract["constants"]["sampler_seed_env_var"]
    monkeypatch.delenv(seed_env, raising=False)
    captured: list[dict] = []
    real_tpe = optuna.samplers.TPESampler

    class _CapturingTPE(real_tpe):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):  # noqa: D401
            captured.append(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(optuna.samplers, "TPESampler", _CapturingTPE)

    helper = getattr(autotuner_module, "_build_optuna_sampler_from_env", None)
    assert helper is not None
    helper()
    assert captured, "TPESampler must be constructed by the helper."
    assert captured[-1].get("seed", "MISSING") is None, (
        f"Unset OPTUNA_SAMPLER_SEED must produce seed=None (preserving "
        f"prior wall-clock-seeded behaviour); got seed={captured[-1].get('seed')!r}"
    )


def test_env_n_jobs_parsed_to_int(monkeypatch, autotuner_module, pin_contract):
    """Set ``OPTUNA_N_JOBS=3`` and confirm the helper returns the int 3."""
    n_jobs_env = pin_contract["constants"]["n_jobs_env_var"]
    monkeypatch.setenv(n_jobs_env, "3")

    helper = getattr(autotuner_module, "_resolve_optuna_n_jobs_from_env", None)
    assert helper is not None and callable(helper), (
        "OPTUNA-6 implementation contract: autotuner.py must expose a "
        "module-level helper `_resolve_optuna_n_jobs_from_env()` that "
        "returns int(os.environ[OPTUNA_N_JOBS]) when set, else a sane "
        "host-derived default (os.cpu_count() or 1). The boundary is what "
        "the behavioural RED tests pin."
    )
    value = helper()
    assert isinstance(value, int), (
        f"_resolve_optuna_n_jobs_from_env() must return int, got {type(value).__name__}"
    )
    assert value == 3, f"Env OPTUNA_N_JOBS=3 must flow through; got {value!r}"


def test_unset_n_jobs_env_falls_back_to_one_for_sqlite_safety(
    monkeypatch, autotuner_module, pin_contract
):
    """Unset env must yield literal ``1`` — never -1, never cpu_count.

    Default flipped from ``os.cpu_count() or 1`` to literal ``1`` after
    opt-optuna review: the run_autotuner study uses SQLite RDBStorage
    (autotuner.py:1544 — ``sqlite:///optuna_studies.db``). Parallel Optuna
    trials writing to one SQLite DB contend on the writer lock and produce
    ``sqlite3.OperationalError: database is locked``. n_jobs=1 is the only
    safe default for the SQLite backend; operators with Postgres/MySQL can
    opt-in via OPTUNA_N_JOBS=<N>.
    """
    n_jobs_env = pin_contract["constants"]["n_jobs_env_var"]
    monkeypatch.delenv(n_jobs_env, raising=False)

    helper = getattr(autotuner_module, "_resolve_optuna_n_jobs_from_env", None)
    assert helper is not None
    value = helper()
    assert isinstance(value, int), (
        f"helper must return int even when env is unset, got {type(value).__name__}"
    )
    expected = int(pin_contract["parallelism_contract"]["n_jobs_default_when_unset_or_invalid"])
    assert expected == 1, (
        "Fixture invariant: parallelism_contract.n_jobs_default_when_unset_or_invalid "
        f"must be 1 (SQLite RDBStorage safety); got {expected!r}"
    )
    assert value == expected, (
        f"Unset OPTUNA_N_JOBS must default to {expected} (literal 1 for "
        f"SQLite RDBStorage writer-lock safety); got {value!r}. "
        f"Returning os.cpu_count() here would saturate the writer lock and "
        f"produce 'database is locked' OperationalError under load."
    )
    assert value != -1, (
        "OPTUNA-6 prohibition: helper must never return -1 (audit fix is "
        "explicitly to eliminate the -1 hardcode)."
    )


def test_invalid_n_jobs_env_falls_back_to_safe_default(monkeypatch, autotuner_module, pin_contract):
    """A non-int env value must not crash the daemon — fall back to the
    same safe default (1) the unset case uses. The risk engine runs on a
    1-minute cadence; a parse-error crash here would take the engine down,
    and any default > 1 would re-introduce the SQLite writer-lock issue."""
    n_jobs_env = pin_contract["constants"]["n_jobs_env_var"]
    monkeypatch.setenv(n_jobs_env, "not-an-int")
    helper = getattr(autotuner_module, "_resolve_optuna_n_jobs_from_env", None)
    assert helper is not None
    value = helper()
    expected = int(pin_contract["parallelism_contract"]["n_jobs_default_when_unset_or_invalid"])
    assert isinstance(value, int), (
        f"helper must return int even on garbled env, got {type(value).__name__}"
    )
    assert value == expected, (
        f"Garbled OPTUNA_N_JOBS must fall back to the same safe default "
        f"({expected}) as the unset case; got {value!r}. Falling back to "
        f"cpu_count would silently saturate the SQLite writer lock on a typo."
    )
    assert value != -1


# ---------------------------------------------------------------------------
# Determinism property — sampler-level (not end-to-end run_autotuner)
# ---------------------------------------------------------------------------


def _toy_objective_factory():
    """Return a deterministic 1-D objective: maximize -(x-0.37)**2 on [0,1]."""

    def _objective(trial):
        x = trial.suggest_float("x", 0.0, 1.0)
        return -((x - 0.37) ** 2)

    return _objective


def test_pinned_seed_yields_identical_trial_sequences(pin_contract):
    """Two ``TPESampler(seed=<fixed int>)`` instances driving the same
    deterministic objective must produce identical x-suggestions across
    the run. This is the determinism property the OPTUNA-1 pin enables.

    Property-style assertion (no hardcoded producer x values): we assert
    *equality* of the two captured sequences, not the values themselves.
    """
    seed = 4242
    n_trials = int(pin_contract["determinism_property"]["n_trials"])

    def _run(seed_value):
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        sampler = optuna.samplers.TPESampler(seed=seed_value)
        study = optuna.create_study(direction="maximize", sampler=sampler)
        study.optimize(_toy_objective_factory(), n_trials=n_trials, n_jobs=1)
        return [t.params["x"] for t in study.trials]

    seq_a = _run(seed)
    seq_b = _run(seed)
    assert len(seq_a) == n_trials and len(seq_b) == n_trials, (
        f"Expected {n_trials} trials each; got len(a)={len(seq_a)}, len(b)={len(seq_b)}"
    )
    assert seq_a == seq_b, (
        "Determinism violated: two TPESampler(seed=4242) runs over the "
        "same deterministic objective produced DIFFERENT x-sequences. "
        f"a={seq_a}\nb={seq_b}"
    )


def test_unseeded_sampler_diverges_negative_control(pin_contract):
    """Negative control: two ``TPESampler(seed=None)`` instances on the
    same deterministic objective must NOT produce byte-identical
    sequences (otherwise the determinism test above would also pass on a
    sampler that ignored the seed kwarg, and the OPTUNA-1 fix would be
    vacuous).

    We assert *inequality* of the sequences. Optuna's first ~10 startup
    trials are random; with seed=None the RNG state differs, so at least
    one suggestion should differ. We run with enough trials to make the
    probability of a coincidental match astronomically small.
    """
    n_trials = int(pin_contract["determinism_property"]["n_trials"])

    def _run():
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        sampler = optuna.samplers.TPESampler(seed=None)
        study = optuna.create_study(direction="maximize", sampler=sampler)
        study.optimize(_toy_objective_factory(), n_trials=n_trials, n_jobs=1)
        return [t.params["x"] for t in study.trials]

    seq_a = _run()
    seq_b = _run()
    assert seq_a != seq_b, (
        "Negative control failed: two seed=None TPESampler runs produced "
        "byte-identical x-sequences. Either Optuna's default-seed behaviour "
        "changed (now deterministic without explicit seed) — in which case "
        "the OPTUNA-1 audit finding needs re-evaluation — or this test "
        "needs more trials. n_trials="
        f"{n_trials}. seq_a={seq_a}"
    )


# ---------------------------------------------------------------------------
# Calibration-site invariant — TPESampler pin preserved; n_jobs uniformly
# env-driven (OPTUNA-6 applied across all autotuner.py study sites)
# ---------------------------------------------------------------------------


def test_calibration_sweep_preserves_tpesampler_pin(autotuner_ast, pin_contract):
    """``run_calibration_sweep`` must keep its explicit
    ``TPESampler(seed=random_state)`` sampler. The OPTUNA-1/6 fix to the
    run_autotuner site must NOT regress the calibration sampler pin.

    Scope-expansion note (opt-optuna review): the previous version of this
    test ALSO pinned ``n_jobs=1`` as a literal. After review, OPTUNA-6 is
    applied uniformly — every n_jobs in autotuner.py must read from
    OPTUNA_N_JOBS — so the literal check moved to the new RED test below.
    """
    expected_sampler_cls = pin_contract["calibration_site_invariant"]["expected_sampler_class"]
    assert expected_sampler_cls.endswith("TPESampler")

    func = _function_def(autotuner_ast, _RUN_CALIBRATION)

    create_calls = _create_study_calls(func)
    assert create_calls, (
        "AST: run_calibration_sweep must contain at least one "
        "optuna.create_study(...) call — none found."
    )
    saw_tpe = False
    for call in create_calls:
        sampler_expr = _kw(call, "sampler")
        if sampler_expr is None:
            continue
        # Sampler may be bound to a local name (``sampler``) in the calibration
        # site. We resolve by also accepting Name references and looking for
        # an assignment ``<name> = TPESampler(...)`` in the same function.
        if isinstance(sampler_expr, ast.Name):
            target_name = sampler_expr.id
            for assign in ast.walk(func):
                if not isinstance(assign, ast.Assign):
                    continue
                targets = [t.id for t in assign.targets if isinstance(t, ast.Name)]
                if target_name in targets and isinstance(assign.value, ast.Call):
                    f = assign.value.func
                    if isinstance(f, ast.Attribute) and f.attr == "TPESampler":
                        saw_tpe = True
                        # Verify seed= is passed.
                        assert _kw(assign.value, "seed") is not None, (
                            f"calibration TPESampler must keep seed= kwarg (line {assign.lineno})."
                        )
        elif isinstance(sampler_expr, ast.Call):
            f = sampler_expr.func
            if (isinstance(f, ast.Attribute) and f.attr == "TPESampler") or (
                isinstance(f, ast.Name) and f.id == "TPESampler"
            ):
                saw_tpe = True
                assert _kw(sampler_expr, "seed") is not None, (
                    f"calibration TPESampler must keep seed= kwarg (line {call.lineno})."
                )
    assert saw_tpe, (
        "Calibration-site regression: run_calibration_sweep must continue "
        "to pass an explicit TPESampler to optuna.create_study(sampler=...)."
    )


def test_calibration_sweep_n_jobs_is_env_driven_not_literal_one(autotuner_ast):
    """``run_calibration_sweep.study.optimize(...)`` must NOT pass the
    literal ``n_jobs=1`` (or any other integer literal). OPTUNA-6 is
    applied uniformly: every n_jobs in autotuner.py reads from
    OPTUNA_N_JOBS (default 1 for SQLite RDBStorage safety).

    Scope-expansion note (opt-optuna review): this was previously the
    "reference pin pattern" and the literal ``1`` was treated as
    intentional. After review, the literal violates the OPTUNA-6 rule
    "never hardcode CPU counts" and must be replaced with the shared
    env-resolver. The default of 1 is preserved via the helper.
    """
    func = _function_def(autotuner_ast, _RUN_CALIBRATION)
    optimize_calls = _study_optimize_calls(func)
    assert optimize_calls, "AST: expected study.optimize(...) in run_calibration_sweep."

    offenders: list[str] = []
    for call in optimize_calls:
        n_jobs_expr = _kw(call, "n_jobs")
        if n_jobs_expr is None:
            continue  # No kwarg at all is acceptable (defaults to 1).
        # Any integer literal (positive, negative, or via UnaryOp) is a
        # hardcode and a violation of the expanded OPTUNA-6 rule.
        is_int_literal = isinstance(n_jobs_expr, ast.Constant) and isinstance(
            n_jobs_expr.value, int
        )
        is_unary_int_literal = (
            isinstance(n_jobs_expr, ast.UnaryOp)
            and isinstance(n_jobs_expr.operand, ast.Constant)
            and isinstance(n_jobs_expr.operand.value, int)
        )
        if is_int_literal or is_unary_int_literal:
            literal_repr = (
                str(n_jobs_expr.value) if is_int_literal else ("-" + str(n_jobs_expr.operand.value))
            )
            offenders.append(
                f"line {call.lineno}: study.optimize(..., n_jobs={literal_repr}) "
                f"is an OPTUNA-6 hardcode (project rule: never hardcode CPU "
                f"counts in autotuner.py). Source via "
                f"_resolve_optuna_n_jobs_from_env() instead."
            )
    assert not offenders, "OPTUNA-6 calibration-site violations:\n  - " + "\n  - ".join(offenders)


def test_calibration_sweep_references_n_jobs_env_constant(
    autotuner_source: str, pin_contract: dict
):
    """``run_calibration_sweep`` body must reference ``OPTUNA_N_JOBS`` —
    either via the named module-level constant or as a literal — so the
    env dependency is auditable at the call site. Mirrors the
    run_autotuner contract."""
    n_jobs_env = pin_contract["constants"]["n_jobs_env_var"]
    tree = ast.parse(autotuner_source)
    func = _function_def(tree, _RUN_CALIBRATION)
    found_literal = any(
        isinstance(node, ast.Constant) and node.value == n_jobs_env for node in ast.walk(func)
    )
    # The impl may instead reference the named constant via a Name node
    # whose binding is the module-level constant. We accept either path:
    # a string literal in-body OR a Name reference to a known constant
    # whose value is the env var.
    found_name_ref = False
    # Collect module-level string-constant assignments whose value is
    # the env-var name.
    seed_const_names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if isinstance(node.value, ast.Constant) and node.value.value == n_jobs_env:
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        seed_const_names.add(tgt.id)
    if seed_const_names:
        for node in ast.walk(func):
            if isinstance(node, ast.Name) and node.id in seed_const_names:
                found_name_ref = True
                break
    assert found_literal or found_name_ref, (
        f"OPTUNA-6 calibration expansion: run_calibration_sweep must "
        f"reference {n_jobs_env!r} (either as a literal in-body or via a "
        f"Name reference to the module-level constant whose value is "
        f"{n_jobs_env!r}). Could not find either."
    )


def test_calibration_sweep_uses_shared_n_jobs_helper_default(
    monkeypatch, autotuner_module, pin_contract
):
    """Behavioural lock-step: the shared helper
    ``_resolve_optuna_n_jobs_from_env()`` must return literal ``1`` when
    the env is unset — and the calibration site's default must be the
    same value. Test the helper's contract directly; the AST tests above
    pin that the calibration site sources its n_jobs through the env.

    This is the lock-step assertion that ensures the helper default flip
    (cpu_count -> 1) lands consistently across BOTH study sites.
    """
    n_jobs_env = pin_contract["constants"]["n_jobs_env_var"]
    monkeypatch.delenv(n_jobs_env, raising=False)

    helper = getattr(autotuner_module, "_resolve_optuna_n_jobs_from_env", None)
    assert helper is not None, (
        "Lock-step contract: _resolve_optuna_n_jobs_from_env() must exist "
        "as the single source of n_jobs default truth for BOTH run_autotuner "
        "and run_calibration_sweep."
    )
    expected = int(pin_contract["parallelism_contract"]["n_jobs_default_when_unset_or_invalid"])
    assert helper() == expected, (
        f"Lock-step default flip: helper must return {expected} when env is "
        f"unset (SQLite RDBStorage safety). The calibration site relies on "
        f"this default to preserve its prior n_jobs=1 behaviour without a "
        f"local literal."
    )
