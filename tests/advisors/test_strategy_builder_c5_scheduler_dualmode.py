"""C5 RED — weekly scheduler dual-mode injection (EDGE-2, AC-13 + AC-18 dual-mode).

Module under change: advisors/strategy_builder_scheduler.py::run_weekly_build

THE EDGE-2 GAP (empirically grounded, 2026-06-20):
  run_weekly_build (strategy_builder_scheduler.py:138) TODAY calls propose_strategies
  with NO community_candidates → the PRIMARY unattended path is built-new ONLY. The
  operator's dual-mode directive ("fully functional, not staged" — BUILD net-new AND
  SUGGEST objective-matched community) means the weekly run must ALSO inject
  objective-matched atlas-suggested candidates; otherwise AC-13's persisted
  provenance='atlas-suggested' is only ever produced via the rare on-demand route.

  GREEN (quint-eng / composer-alpaca-integration, ~6 lines in run_weekly_build):
  per objective, lazy-import build_plan_generator.load_atlas_candidates, call it with
  the objective, and forward the result to propose_strategies(community_candidates=...).
  Honest D-1: an Atlas failure degrades to built-new-only (no crash).

ADVERSARIAL FOCUS (a worse implementation must FAIL):
  - A weekly run that still passes NO community_candidates FAILS the injection check.
  - A weekly run that admits community UN-matched (ignoring the objective) FAILS the
    objective-matched check (load_atlas_candidates must receive the objective).
  - An Atlas failure that crashes the scheduler FAILS the D-1 degradation check; one
    that silently drops built-new too FAILS the built-new-still-runs check.

MOCKING: propose_strategies (orchestration under test, not the builder), the
idempotency seam, and load_atlas_candidates (no live Atlas). NEVER asserts exact
internals beyond the dual-mode behaviour the AC pins.

No live Atlas / Composer / Anthropic. No hardcoded producer values.
"""

from __future__ import annotations

import importlib

import pytest

_SCHEDULER_MODULES = (
    "advisors.strategy_builder_scheduler",
    "strategy_builder_scheduler",
)
_RUN_FN_NAMES = ("run_weekly_build", "run_builder", "run_all_objectives", "run_once")


def _load_scheduler():
    last_exc = None
    for modpath in _SCHEDULER_MODULES:
        try:
            return importlib.import_module(modpath)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    pytest.fail(
        f"no builder-scheduler module importable (tried {_SCHEDULER_MODULES}); "
        f"last error: {type(last_exc).__name__ if last_exc else 'none'}"
    )


def _resolve_run_fn(scheduler):
    for name in _RUN_FN_NAMES:
        fn = getattr(scheduler, name, None)
        if callable(fn):
            return fn
    raise AssertionError(f"scheduler exposes no runnable entry (expected one of {_RUN_FN_NAMES})")


def _force_fresh_week(scheduler, monkeypatch):
    for marker_name in ("_already_ran_this_week", "_is_this_weeks_row", "_has_run_this_week"):
        if hasattr(scheduler, marker_name):
            monkeypatch.setattr(scheduler, marker_name, lambda *a, **k: False, raising=False)


def _stub_propose(sbe, monkeypatch, scheduler, recorder):
    """Patch propose_strategies everywhere the scheduler may reference it; record kwargs."""

    def _fake_propose(objective, *a, **k):
        recorder.append(
            {
                "objective": getattr(objective, "value", objective),
                "community_candidates": k.get("community_candidates"),
            }
        )
        return sbe.ProposalRun(
            candidates=[],
            gated_batch=sbe._empty_gate_batch(),
            screened_survivors=[],
            observations_written=0,
        )

    monkeypatch.setattr(sbe, "propose_strategies", _fake_propose, raising=False)
    if hasattr(scheduler, "propose_strategies"):
        monkeypatch.setattr(scheduler, "propose_strategies", _fake_propose, raising=False)


# ===========================================================================
# EDGE-2 / AC-13 + AC-18 — weekly run injects objective-matched atlas candidates.
# ===========================================================================


def test_weekly_run_injects_atlas_candidates_per_objective(monkeypatch):
    """EDGE-2 (RED gap): run_weekly_build must inject objective-matched atlas candidates
    into propose_strategies for EACH objective. TODAY it passes no community_candidates.

    We stub load_atlas_candidates to return a sentinel per objective and assert
    propose_strategies received a NON-empty community_candidates for every objective."""
    scheduler = _load_scheduler()
    import advisors.build_plan_generator as bpg  # noqa: PLC0415
    import advisors.strategy_builder_engine as sbe  # noqa: PLC0415

    recorder: list[dict] = []
    _stub_propose(sbe, monkeypatch, scheduler, recorder)
    _force_fresh_week(scheduler, monkeypatch)

    seen_objectives: list = []

    def _fake_load_atlas(objective, *a, **k):
        seen_objectives.append(getattr(objective, "value", objective))
        # Return one sentinel CandidateInfo-shaped object tagged atlas-suggested.
        return [
            sbe.CandidateInfo(
                candidate_id=f"atlas-{getattr(objective, 'value', objective)}",
                tree={},
                template_id="community",
                params={"provenance": bpg.PROVENANCE_ATLAS_SUGGESTED},
                metrics={},
                backtest_error=None,
            )
        ]

    monkeypatch.setattr(bpg, "load_atlas_candidates", _fake_load_atlas, raising=False)
    if hasattr(scheduler, "load_atlas_candidates"):
        monkeypatch.setattr(scheduler, "load_atlas_candidates", _fake_load_atlas, raising=False)

    run_fn = _resolve_run_fn(scheduler)
    run_fn()

    # load_atlas_candidates must be called for each objective (objective-matched).
    assert set(seen_objectives) == {
        "diversify",
        "cut_drawdown",
        "lift_risk_adjusted",
        "volatility_mitigation",
    }, (
        "EDGE-2: the weekly run must call the objective-matched load_atlas_candidates for "
        f"every objective; got {seen_objectives}"
    )

    # Every per-objective propose_strategies call must carry NON-empty community_candidates.
    assert recorder, "propose_strategies must be called per objective"
    for rec in recorder:
        cc = rec.get("community_candidates")
        assert cc, (
            "EDGE-2 (RED gap): the weekly run must forward objective-matched atlas "
            f"candidates to propose_strategies (community_candidates=); objective "
            f"{rec['objective']} got community_candidates={cc!r}"
        )


def test_weekly_atlas_admission_is_objective_matched(monkeypatch):
    """EDGE-2/AC-12: load_atlas_candidates receives the SAME objective the run is building
    for (so admission is objective-shaped, not a global unranked pull)."""
    scheduler = _load_scheduler()
    import advisors.build_plan_generator as bpg  # noqa: PLC0415
    import advisors.strategy_builder_engine as sbe  # noqa: PLC0415

    recorder: list[dict] = []
    _stub_propose(sbe, monkeypatch, scheduler, recorder)
    _force_fresh_week(scheduler, monkeypatch)

    pairs: list[tuple] = []

    def _fake_load_atlas(objective, *a, **k):
        pairs.append(getattr(objective, "value", objective))
        return []

    monkeypatch.setattr(bpg, "load_atlas_candidates", _fake_load_atlas, raising=False)
    if hasattr(scheduler, "load_atlas_candidates"):
        monkeypatch.setattr(scheduler, "load_atlas_candidates", _fake_load_atlas, raising=False)

    run_fn = _resolve_run_fn(scheduler)
    run_fn()

    # The objective passed to load_atlas_candidates must match the objective the run built
    # (paired in order with the recorder's propose_strategies objective).
    proposed_objectives = [r["objective"] for r in recorder]
    assert pairs == proposed_objectives, (
        "EDGE-2/AC-12: each load_atlas_candidates objective must match the objective being "
        f"built; atlas objectives {pairs} != built objectives {proposed_objectives}"
    )


def test_weekly_run_degrades_when_atlas_unavailable_built_new_still_runs(monkeypatch):
    """EDGE-2 D-1: if load_atlas_candidates raises (Atlas down), the weekly run must NOT
    crash and must STILL run built-new (propose_strategies still called per objective, just
    with empty/absent community_candidates). Honest degradation, never a staged drop of the
    whole run."""
    scheduler = _load_scheduler()
    import advisors.build_plan_generator as bpg  # noqa: PLC0415
    import advisors.strategy_builder_engine as sbe  # noqa: PLC0415

    recorder: list[dict] = []
    _stub_propose(sbe, monkeypatch, scheduler, recorder)
    _force_fresh_week(scheduler, monkeypatch)

    def _atlas_down(objective, *a, **k):
        raise RuntimeError("Atlas/Mongo SRV resolution failed")

    monkeypatch.setattr(bpg, "load_atlas_candidates", _atlas_down, raising=False)
    if hasattr(scheduler, "load_atlas_candidates"):
        monkeypatch.setattr(scheduler, "load_atlas_candidates", _atlas_down, raising=False)

    run_fn = _resolve_run_fn(scheduler)
    # Must not raise — D-1.
    try:
        run_fn()
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"weekly run must not crash when Atlas is unavailable (D-1); raised {exc!r}")

    # Built-new still runs for all four objectives despite the Atlas failure.
    built_objectives = {r["objective"] for r in recorder}
    assert built_objectives == {
        "diversify",
        "cut_drawdown",
        "lift_risk_adjusted",
        "volatility_mitigation",
    }, (
        "EDGE-2 D-1: an Atlas failure must degrade to built-new-only, NOT drop the run — "
        f"propose_strategies must still run every objective; got {built_objectives}"
    )
    # Community candidates may be empty/None on the degraded path — never a crash.
    for rec in recorder:
        cc = rec.get("community_candidates")
        assert cc in (None, [], ()), (
            "EDGE-2 D-1: on an Atlas failure the community_candidates must be empty/absent "
            f"(built-new still runs); objective {rec['objective']} got {cc!r}"
        )
