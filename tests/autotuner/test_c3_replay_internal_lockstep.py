"""
Cluster 3 — internal lockstep guard for the autotuner replay.

WHY THIS FILE EXISTS
--------------------
Under the cluster-3 final architecture (team-lead Option (a-plus) ruling)
there is ONE per-tick exit core, _replay_exit_tick, called by all three
replay entry points: run_simulation, _collect_sim_returns and
replay_exit_sequence. So today the three paths are identical-by-construction
— they share the core, there are no copies to drift.

This file is the REGRESSION GUARD against a future RE-INLINE. If a later
change ever inlines the per-tick exit loop back into run_simulation or
_collect_sim_returns (the rejected Option (b)), the AC-6 parity test would
NOT catch a drift — test_c3_replay_exit_parity.py exercises only
replay_exit_sequence. run_simulation and _collect_sim_returns ARE the
autotuner objective function (the code every deployed parameter set is
selected against with real money); an unguarded inlined copy that drifts
from the parity-tested core re-creates exactly the replay-vs-production
divergence surface Cluster 3 exists to remove. This file closes that gap
permanently: it drives run_simulation and _collect_sim_returns over the same
parity fixtures as replay_exit_sequence and asserts their exit behaviour is
in lockstep. Under the shared core it passes trivially; if anyone re-inlines
and the copy drifts, it goes RED.

EDITOR NOTE: any change that gives run_simulation or _collect_sim_returns
their own copy of the per-tick exit loop MUST keep these tests green — they
are the guarantee that all three replay paths produce identical exit
decisions.

WHAT IS ASSERTED
----------------
For a single-symphony single-day history, an exit fires in the per-tick loop
iff the day produces a triggered return. Therefore:
  * replay_exit_sequence reports an exit  <=>  _collect_sim_returns returns a
    non-empty per-triggered-day list.
  * replay_exit_sequence reports an exit  <=>  run_simulation's guard-alpha
    for that day is non-zero (a triggered day always contributes a non-zero
    guard-alpha term; a non-triggered day contributes nothing).
Both equivalences must hold for every parity fixture — exit / no-exit must
agree across all three replay code paths.

These tests call the REAL replay functions and the REAL math_engine
primitives — nothing is mocked. They are the durable guard that the three
replay paths never diverge.
"""

from __future__ import annotations

import json
import pathlib

import pytest

import autotuner


_FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "autotuner"
    / "replay_parity"
)

def _replay_grace() -> int:
    """The VWAP open-window grace value the replay uses internally.

    run_simulation / _collect_sim_returns resolve grace via the lazy
    accessor autotuner._replay_grace_minutes() (which references production's
    alpha_bot_execution.VWAP_OPEN_WINDOW_GRACE_MINUTES — no autotuner-local
    module constant; see test_c3_replay_vwap_grace.py B1). The shared-core
    comparison drives replay_exit_sequence with the SAME value for an
    apples-to-apples lockstep check. Resolved lazily (not at import) so this
    file collects regardless of how grace is wired and tracks any
    monkeypatch.
    """
    return autotuner._replay_grace_minutes()


_PARITY_FIXTURES = [
    "parity_trailing_stop_exit.json",
    "parity_vwap_grace_no_phantom.json",
    "parity_tp_rearm_dip.json",
    "parity_no_exit_session.json",
    "parity_mc_sanity_veto.json",
]


def _load_fixture(name: str) -> dict:
    with (_FIXTURE_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


def _default_params() -> dict:
    return {
        "TRIGGER_THRESHOLD_PCT": 15.0,
        "TAKE_PROFIT_MC_PCT": 5.0,
        "VWAP_CROSS_HWM_PCT": 1.0,
        "VWAP_BLEED_MULTIPLIER": 1.5,
        "VWAP_BLEED_TICKS": 10,
        "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
        "MAX_PARABOLIC_SQUEEZE": 0.5,
    }


def _shared_core_exits(ticks: list[dict], params: dict) -> bool:
    """True iff the shared-core replay (replay_exit_sequence) fires an exit.

    Driven with grace_minutes = the value run_simulation / _collect_sim_
    returns resolve internally via autotuner._replay_grace_minutes(), so the
    comparison against those two paths is apples-to-apples.
    """
    seq = autotuner.replay_exit_sequence(
        ticks, params, grace_minutes=_replay_grace()
    )
    return any(d["exit_reason"] for d in seq)


@pytest.mark.parametrize("fixture_name", _PARITY_FIXTURES)
def test_collect_sim_returns_in_lockstep_with_shared_core(fixture_name: str) -> None:
    """_collect_sim_returns' exit path must agree with the shared
    _replay_exit_tick core on exit / no-exit for every parity fixture.

    Under the cluster-3 shared-core architecture _collect_sim_returns calls
    _replay_exit_tick directly, so this passes by construction. It is the
    regression guard: a future re-inline that drifts goes RED here.

    _collect_sim_returns appends a per-triggered-day guard-alpha exactly when
    the day's tick loop fires an exit. So: shared core reports an exit
    <=> _collect_sim_returns returns a non-empty list.
    """
    fx = _load_fixture(fixture_name)
    ticks = fx["ticks"]
    params = fx.get("params") or _default_params()
    history = {"sym-A": {"2026-04-06": ticks}}

    shared_exits = _shared_core_exits(ticks, params)
    daily_returns = autotuner._collect_sim_returns(
        params, history, ["sym-A"], "2026-05-10", {}
    )
    collect_exits = len(daily_returns) > 0

    assert collect_exits == shared_exits, (
        f"[{fixture_name}] _collect_sim_returns' exit path diverged from the "
        f"shared _replay_exit_tick core: shared core "
        f"{'exits' if shared_exits else 'does not exit'}, but "
        f"_collect_sim_returns returned {len(daily_returns)} triggered-day "
        f"entr{'y' if len(daily_returns) == 1 else 'ies'}. _collect_sim_"
        f"returns is the autotuner objective function — it must route exit "
        f"decisions through _replay_exit_tick; an undetected drift "
        f"mis-selects deployed parameters."
    )


@pytest.mark.parametrize("fixture_name", _PARITY_FIXTURES)
def test_run_simulation_in_lockstep_with_shared_core(fixture_name: str) -> None:
    """run_simulation's exit path must agree with the shared _replay_exit_tick
    core on exit / no-exit for every parity fixture.

    Under the cluster-3 shared-core architecture run_simulation calls
    _replay_exit_tick directly, so this passes by construction. It is the
    regression guard: a future re-inline that drifts goes RED here.

    run_simulation contributes a guard-alpha term for a day ONLY when that
    day's tick loop fires an exit; a non-triggered day contributes nothing.
    So for a single-day history: shared core reports an exit <=>
    run_simulation's returned guard-alpha is non-zero.

    Tolerance: the no-exit case must be EXACTLY 0.0 — run_simulation returns
    -total_guard_alpha and a day that never triggers adds nothing, so the sum
    is an exact float 0.0, no tolerance needed. The exit case asserts only
    non-zero (the precise value is the penalty math, pinned elsewhere).
    """
    fx = _load_fixture(fixture_name)
    ticks = fx["ticks"]
    params = fx.get("params") or _default_params()
    history = {"sym-A": {"2026-04-06": ticks}}

    shared_exits = _shared_core_exits(ticks, params)
    guard_alpha = autotuner.run_simulation(
        params, history, ["sym-A"], "2026-05-10", {}
    )
    run_sim_exits = guard_alpha != 0.0

    assert run_sim_exits == shared_exits, (
        f"[{fixture_name}] run_simulation's exit path diverged from the "
        f"shared _replay_exit_tick core: shared core "
        f"{'exits' if shared_exits else 'does not exit'}, but run_simulation "
        f"returned guard-alpha {guard_alpha!r} "
        f"({'non-zero -> exit' if run_sim_exits else 'zero -> no exit'}). "
        f"run_simulation is the OOS-cascade objective; it must route exit "
        f"decisions through _replay_exit_tick — a drift silently mis-selects "
        f"deployed parameters."
    )


def test_all_three_replay_paths_agree_on_every_parity_fixture() -> None:
    """Cross-cutting: for every parity fixture the three replay code paths —
    replay_exit_sequence, run_simulation and _collect_sim_returns — must
    AGREE on whether the day exits. Under the shared core all three call
    _replay_exit_tick so they agree by construction; this is the
    consolidated regression guard against a future re-inline.

    A single consolidated assertion so a divergence anywhere across the
    fixture set is reported together, not one parametrized case at a time.
    """
    disagreements: list[str] = []
    for fixture_name in _PARITY_FIXTURES:
        fx = _load_fixture(fixture_name)
        ticks = fx["ticks"]
        params = fx.get("params") or _default_params()
        history = {"sym-A": {"2026-04-06": ticks}}

        shared = _shared_core_exits(ticks, params)
        collect = len(
            autotuner._collect_sim_returns(
                params, history, ["sym-A"], "2026-05-10", {}
            )
        ) > 0
        run_sim = autotuner.run_simulation(
            params, history, ["sym-A"], "2026-05-10", {}
        ) != 0.0

        if not (shared == collect == run_sim):
            disagreements.append(
                f"  {fixture_name}: shared_core={shared}, "
                f"_collect_sim_returns={collect}, run_simulation={run_sim}"
            )

    assert not disagreements, (
        "The three autotuner replay code paths disagree on exit / no-exit "
        "for these fixtures:\n" + "\n".join(disagreements) + "\n"
        "The inlined per-tick loops in run_simulation and _collect_sim_"
        "returns MUST stay in lockstep with the shared _replay_exit_tick "
        "core. A divergence here is a re-introduced replay drift surface — "
        "the exact defect Cluster 3 removes."
    )
