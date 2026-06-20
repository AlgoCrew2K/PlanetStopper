"""RED tests — Component 4: weekly builder scheduler (AC-18).

A NEW standalone scheduler script (modeled on prism_scheduler.py + a WEEKLY systemd
timer) runs the real builder unattended for each objective and persists survivors via the
UNCHANGED downstream persist path (propose_strategies). AC-18 requires:
  - named constants (no magic numbers) — a MAX_ATTEMPTS-style bounded-retry constant,
  - an idempotency guard — a duplicate run in the SAME ISO-week is a no-op,
  - a D-1 error contract,
  - persistence via the unchanged downstream path (propose_strategies → insert_advisor_observation).

The scheduler module does not exist yet — the ImportError is the first RED signal.

WHAT IS MOCKED: propose_strategies (the scheduler's job is ORCHESTRATION + idempotency,
  NOT re-testing the builder — the builder is covered by test_builder_integration.py) and
  the idempotency-marker seam. NEVER asserts exact internals (DB vs file marker) — the impl
  owns that; the RED pins the BEHAVIOUR (per-objective run, same-week no-op, bounded retry).

The exact public function names are resolved leniently from a small set so the impl owns
naming; the tests pin behaviour.
"""

from __future__ import annotations

import importlib

import pytest

# Candidate module paths for the scheduler (impl picks one). The import-resolution itself
# is the first RED: none exists yet.
_SCHEDULER_MODULES = (
    "strategy_builder_scheduler",
    "advisors.strategy_builder_scheduler",
    "builder_scheduler",
)

# Candidate names for the "run all objectives once" entry the tests drive.
_RUN_FN_NAMES = ("run_weekly_build", "run_builder", "run_all_objectives", "run_once")


def _load_scheduler():
    """Import the scheduler module, or pytest.fail (a true FAILED RED, not a collection
    ERROR) when it does not exist yet. Calling this inside each test body — rather than a
    module-scoped fixture — keeps the missing-module signal a clean RED FAILURE, so the
    merge gate's zero-ERRORS check is not tripped by a not-yet-written module."""
    last_exc = None
    for modpath in _SCHEDULER_MODULES:
        try:
            return importlib.import_module(modpath)
        except ModuleNotFoundError as exc:
            last_exc = exc
        except Exception as exc:  # noqa: BLE001 — any other import error is also a RED
            last_exc = exc
    pytest.fail(
        f"no builder-scheduler module importable (tried {_SCHEDULER_MODULES}); "
        f"last error: {type(last_exc).__name__ if last_exc else 'none'} — "
        "Component 4 AC-18 requires a weekly builder scheduler script"
    )


def _resolve_run_fn(scheduler):  # scheduler is the imported module (from _load_scheduler)
    for name in _RUN_FN_NAMES:
        fn = getattr(scheduler, name, None)
        if callable(fn):
            return fn
    raise AssertionError(
        f"scheduler exposes no runnable entry (expected one of {_RUN_FN_NAMES})"
    )


# ===========================================================================
# AC-18 — bounded retry constant (no magic numbers).
# ===========================================================================


def test_scheduler_has_bounded_retry_constant():
    """AC-18: a named bounded-retry constant must exist (no magic numbers). Resolve from
    the prism_scheduler-style name; assert it is a small positive int."""
    scheduler = _load_scheduler()
    candidates = ("MAX_ATTEMPTS", "MAX_RETRIES", "MAX_RUN_ATTEMPTS")
    found = [getattr(scheduler, c) for c in candidates if hasattr(scheduler, c)]
    assert found, f"scheduler must expose a bounded-retry constant (one of {candidates})"
    val = found[0]
    assert isinstance(val, int) and 1 <= val <= 10, (
        f"the bounded-retry constant must be a small positive int; got {val!r}"
    )


# ===========================================================================
# AC-18 — runs per objective via the UNCHANGED propose_strategies persist path.
# ===========================================================================


def test_scheduler_runs_each_objective_via_propose_strategies(monkeypatch):
    """AC-18: the scheduler must invoke the real builder (propose_strategies) for EACH of
    the four objectives, persisting via the unchanged downstream path. We mock
    propose_strategies (orchestration is under test, not the builder) and assert it was
    called once per objective with a distinct objective value."""
    scheduler = _load_scheduler()
    import advisors.strategy_builder_engine as sbe  # noqa: PLC0415

    seen_objectives: list = []

    def _fake_propose(objective, *a, **k):
        seen_objectives.append(getattr(objective, "value", objective))
        return sbe.ProposalRun(
            candidates=[],
            gated_batch=sbe._empty_gate_batch(),
            screened_survivors=[],
            observations_written=0,
        )

    # Patch propose_strategies wherever the scheduler references it.
    monkeypatch.setattr(sbe, "propose_strategies", _fake_propose, raising=False)
    if hasattr(scheduler, "propose_strategies"):
        monkeypatch.setattr(scheduler, "propose_strategies", _fake_propose, raising=False)
    # Force the idempotency guard to treat this as a fresh week (not already-run).
    for marker_name in ("_already_ran_this_week", "_is_this_weeks_row", "_has_run_this_week"):
        if hasattr(scheduler, marker_name):
            monkeypatch.setattr(scheduler, marker_name, lambda *a, **k: False, raising=False)

    run_fn = _resolve_run_fn(scheduler)
    run_fn()

    # All FOUR objectives must have been driven (the AC-8 four-value contract).
    assert set(seen_objectives) == {
        "diversify",
        "cut_drawdown",
        "lift_risk_adjusted",
        "volatility_mitigation",
    }, f"scheduler must run all four objectives via propose_strategies; got {seen_objectives}"


# ===========================================================================
# AC-18 — idempotency guard: a duplicate run in the SAME ISO-week is a no-op.
# ===========================================================================


def test_scheduler_idempotency_same_week_is_noop(monkeypatch):
    """AC-18: a duplicate run in the same ISO-week must be a no-op — propose_strategies is
    NOT called again. We force the idempotency seam to report 'already ran this week' and
    assert the builder is not invoked."""
    scheduler = _load_scheduler()
    import advisors.strategy_builder_engine as sbe  # noqa: PLC0415

    call_count = {"n": 0}

    def _counting_propose(objective, *a, **k):
        call_count["n"] += 1
        return sbe.ProposalRun(
            candidates=[],
            gated_batch=sbe._empty_gate_batch(),
            screened_survivors=[],
            observations_written=0,
        )

    monkeypatch.setattr(sbe, "propose_strategies", _counting_propose, raising=False)
    if hasattr(scheduler, "propose_strategies"):
        monkeypatch.setattr(scheduler, "propose_strategies", _counting_propose, raising=False)

    # Force the idempotency guard to report "already ran this week".
    patched = False
    for marker_name in ("_already_ran_this_week", "_is_this_weeks_row", "_has_run_this_week"):
        if hasattr(scheduler, marker_name):
            monkeypatch.setattr(scheduler, marker_name, lambda *a, **k: True, raising=False)
            patched = True
    assert patched, (
        "scheduler must expose an idempotency-marker seam (one of "
        "_already_ran_this_week / _is_this_weeks_row / _has_run_this_week) so a same-week "
        "duplicate run can short-circuit"
    )

    run_fn = _resolve_run_fn(scheduler)
    run_fn()

    assert call_count["n"] == 0, (
        "a duplicate run in the same ISO-week must be a NO-OP — propose_strategies must not "
        f"be called; it was called {call_count['n']} time(s)"
    )


# ===========================================================================
# AC-18 — D-1 never-raises: a builder failure does not crash the scheduler.
# ===========================================================================


def test_scheduler_never_raises_on_builder_failure(monkeypatch):
    """AC-18 D-1: if propose_strategies raises for one objective, the scheduler must NOT
    propagate the raw exception — it degrades (logs the class name, continues / clean exit).
    We make propose_strategies raise and assert the run-fn does not raise."""
    scheduler = _load_scheduler()
    import advisors.strategy_builder_engine as sbe  # noqa: PLC0415

    def _boom(objective, *a, **k):
        raise RuntimeError("simulated builder failure with a /secret/path and api-key")

    monkeypatch.setattr(sbe, "propose_strategies", _boom, raising=False)
    if hasattr(scheduler, "propose_strategies"):
        monkeypatch.setattr(scheduler, "propose_strategies", _boom, raising=False)
    for marker_name in ("_already_ran_this_week", "_is_this_weeks_row", "_has_run_this_week"):
        if hasattr(scheduler, marker_name):
            monkeypatch.setattr(scheduler, marker_name, lambda *a, **k: False, raising=False)

    run_fn = _resolve_run_fn(scheduler)
    # Must not raise — D-1 honest degradation.
    try:
        run_fn()
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"scheduler must never raise on a builder failure (D-1); raised {exc!r}")
