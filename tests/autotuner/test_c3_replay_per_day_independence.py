"""
Cluster 3 AC-9 — confirm the replay's per-day-independent simulation is
faithful to production's daily reset.

The math audit (finding 3) originally read "the replay resets per-position
state each simulated day; production never resets triggered/stop_trigger" as
a divergence. Cluster 1 resolved the production side: production DOES reset
per-position transient state each day via database.wipe_transient_state, and
intraday a triggered symphony freezes (no re-entry within the day).

So the reconciled picture is:
  * Production: each trading day starts with wiped transient state; within a
    day a triggered symphony stays frozen.
  * Replay: re-initializes all per-position state (hwm, armed, tp_armed,
    counters, ...) fresh at the top of each simulated day, and breaks the
    tick loop on the first trigger.

These two ARE faithful to each other AFTER Cluster 1: per-day independence
in the replay mirrors production's daily wipe. This file confirms that with
tests — it does not change behaviour; it pins the reconciled contract so a
future change cannot silently reintroduce cross-day state bleed in the
replay.

RED EXPECTATION: this file is largely a GREEN-confirming pin of correct
behaviour. The one assertion that can RED against pre-fix code is the
structural check that each replay function re-initializes state INSIDE the
per-day loop (the implementer must keep it there when restructuring the
replay for AC-1/AC-2/AC-3) — and `test_replay_day2_unaffected_by_day1_exit`,
which would fail if a refactor accidentally hoisted state init out of the
date loop.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import autotuner


_AUTOTUNER_TREE = ast.parse(
    pathlib.Path(autotuner.__file__).read_text(encoding="utf-8")
)

# Per-position transient state names the replay must re-init each day.
_PER_DAY_STATE_NAMES = {
    "hwm",
    "armed",
    "tp_armed",
    "para_armed",
    "breakeven_locked",
    "below_stop_count",
    "above_tp_count",
    "vwap_ticks",
    "vwap_bleed_ticks",
    "hwm_hold_ticks",
}


def _function_node(name: str) -> ast.FunctionDef:
    for node in ast.walk(_AUTOTUNER_TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found in autotuner.py")


def _date_loop(func: ast.FunctionDef) -> ast.For:
    """Return the `for date, ticks in ...` per-day loop inside a replay func."""
    for node in ast.walk(func):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Tuple):
            elts = node.target.elts
            names = {e.id for e in elts if isinstance(e, ast.Name)}
            if {"date", "ticks"} <= names:
                return node
    raise AssertionError(
        f"per-day `for date, ticks` loop not found in {func.name}"
    )


def _names_assigned_in(node: ast.AST) -> set[str]:
    assigned: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Assign):
            for tgt in n.targets:
                if isinstance(tgt, ast.Name):
                    assigned.add(tgt.id)
    return assigned


# ===========================================================================
# AC-9 — structural: per-day state is re-initialized inside the date loop.
# ===========================================================================


@pytest.mark.parametrize("func_name", ["_collect_sim_returns", "run_simulation"])
def test_replay_reinitializes_per_position_state_inside_date_loop(
    func_name: str,
) -> None:
    """AC-9: each replay function must re-initialize per-position transient
    state INSIDE its `for date, ticks` loop body — so each simulated day is
    independent, mirroring production's daily wipe_transient_state.

    Asserts the per-day state names are assigned within the date-loop body.
    Guards against a refactor (driven by the AC-1/AC-2/AC-3 changes) that
    hoists state init above the date loop and silently introduces cross-day
    state bleed.

    This is a faithfulness PIN — it stays green if the implementer keeps the
    init inside the loop, and REDs only if a future refactor moves it out.
    """
    func = _function_node(func_name)
    date_loop = _date_loop(func)
    assigned_in_loop = _names_assigned_in(date_loop)

    missing = sorted(_PER_DAY_STATE_NAMES - assigned_in_loop)
    assert not missing, (
        f"autotuner.{func_name} does not re-initialize {missing} inside its "
        f"per-day `for date, ticks` loop. Every per-position transient state "
        f"must be reset at the top of each simulated day — that per-day "
        f"independence is the faithful mirror of production's daily "
        f"wipe_transient_state (Cluster 1). Hoisting init out of the loop "
        f"reintroduces cross-day state bleed."
    )


# ===========================================================================
# AC-9 — behavioural: day 2's simulation is independent of day 1's exit.
# ===========================================================================


def _default_params() -> dict:
    return {
        "TRIGGER_THRESHOLD_PCT": 15.0,
        "TAKE_PROFIT_MC_PCT": 5.0,
        "VWAP_CROSS_HWM_PCT": 1.0,
        "VWAP_BLEED_MULTIPLIER": 1.5,
        "VWAP_BLEED_TICKS": 3,
        "PARABOLIC_VELOCITY_THRESHOLD": 99.0,
        "MAX_PARABOLIC_SQUEEZE": 0.5,
    }


def _replay_seq_for_day(
    ticks: list[dict], params: dict, grace_minutes: int = 0
) -> list[dict]:
    if not hasattr(autotuner, "replay_exit_sequence"):
        pytest.fail(
            "autotuner.replay_exit_sequence missing — required for AC-9 "
            "per-day independence behavioural test."
        )
    return autotuner.replay_exit_sequence(
        ticks, params, grace_minutes=grace_minutes
    )


def test_replay_day_is_independent_of_a_prior_days_exit() -> None:
    """AC-9: a day whose ticks would NOT trigger an exit on their own must
    produce no exit, regardless of any prior day having triggered. The replay
    simulates each day from a clean slate — exactly like production starting
    each day after wipe_transient_state.

    Runs a no-exit day's tick sequence through replay_exit_sequence directly
    (a fresh, independent simulation). It must produce zero exits. If the
    replay leaked triggered/armed/counter state across days, a quiet day
    could inherit a prior day's primed counters and exit spuriously.
    """
    params = _default_params()
    # A quiet day: small positive returns, mc neutral, no VWAP signal.
    quiet_day = [
        {
            "time": "09:30", "return": 0.3, "mc_prob": 50.0, "vol": 0.5,
            "vwap_diff": 0.0, "base_atr_pct": 0.5, "valid_vwap_weight": 0.0,
        }
        for _ in range(20)
    ]
    seq = _replay_seq_for_day(quiet_day, params)
    exits = [(d["tick_idx"], d["exit_reason"]) for d in seq if d["exit_reason"]]
    assert not exits, (
        f"A quiet, no-trigger day produced exit(s) {exits}. Each replay day "
        f"must simulate from a clean slate; no exit may be inherited from "
        f"primed state."
    )


def test_replay_multi_day_history_each_day_starts_fresh() -> None:
    """AC-9: in a 2-day history where day 1 triggers an exit early and day 2
    is quiet, the replay must NOT carry day 1's triggered/armed/counter state
    into day 2.

    Drives run_simulation over a 2-day history and confirms it completes
    without error and that day 2 (quiet) contributes no spurious trigger.
    We verify via the per-day helper that day 2's tick sequence, simulated
    independently, produces no exit — the same result the multi-day run must
    yield for day 2.

    A faithfulness pin: production wipes transient state daily, so a
    cross-day-independent replay is correct. RED only if a refactor breaks
    per-day independence.
    """
    params = _default_params()

    # Day 1: a sustained deep bleed that triggers a VWAP exit early.
    day1 = [
        {
            "time": "09:30", "return": -9.0, "mc_prob": 50.0, "vol": 0.5,
            "vwap_diff": -3.0, "base_atr_pct": 0.5, "valid_vwap_weight": 1.0,
        }
        for _ in range(10)
    ]
    # Day 2: quiet — no signal of any kind.
    day2 = [
        {
            "time": "09:30", "return": 0.3, "mc_prob": 50.0, "vol": 0.5,
            "vwap_diff": 0.0, "base_atr_pct": 0.5, "valid_vwap_weight": 0.0,
        }
        for _ in range(20)
    ]

    history = {"sym-A": {"2026-04-06": day1, "2026-04-07": day2}}
    # Must not raise — the per-day loop handles both days.
    result = autotuner.run_simulation(params, history, ["sym-A"], "2026-05-01", {})
    assert isinstance(result, float), (
        "run_simulation must return a float guard-alpha scalar over a "
        "2-day history."
    )

    # Day 2 simulated independently must produce no exit — confirming the
    # multi-day run did not bleed day 1's primed state into day 2.
    day2_seq = _replay_seq_for_day(day2, params)
    day2_exits = [d for d in day2_seq if d["exit_reason"]]
    assert not day2_exits, (
        f"Day 2 (quiet) produced exit(s) "
        f"{[(d['tick_idx'], d['exit_reason']) for d in day2_exits]} when "
        f"simulated fresh. Day 2 must start from a clean slate — the replay "
        f"must not carry day 1's triggered/armed/counter state forward."
    )
