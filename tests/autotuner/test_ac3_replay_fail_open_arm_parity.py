"""
AC-3 (MA-10 HIGH) — the replay's exit tick must gain production's fail-open
arming on MC-absent ticks.

Bug (audit finding MA-10, alpha_bot_execution.py:1324-1326 vs
autotuner.py's arm/disarm block): production treats an ABSENT MC opinion
(prob_underperforming is None — insufficient MC history, or the
regime-match-quality guard suppressed it) as a reason to ARM the protective
stop (fail-open — an absent second opinion must never silently disable the
protective stop; the EXIT_CONFIRM_TICKS ladder still gates the actual
liquidation, so a transient one-tick gap cannot trigger a sale):

    if mc_available and take_profit_mc <= prob_underperforming < trigger_threshold:
        should_arm = True
    elif not mc_available:
        should_arm = True                      # <-- fail-open branch
    if should_arm and not armed and not triggered:
        armed = True

The replay's arm/disarm block (autotuner.py, inside _replay_exit_tick) is
missing the `elif not mc_available: should_arm = True` branch entirely:

    if mc_available and take_profit_mc <= mc < trigger_threshold:
        if not state["armed"]:
            state["armed"] = True
    elif state["armed"]:
        if mc_available and mc > (trigger_threshold * 2) and ret > 0.0:
            state["armed"] = False
            ...

On an MC-absent tick while UNARMED, the replay's `if` is False (mc_available
is False) and the `elif state["armed"]` branch is also False (not yet
armed) -- nothing happens, the position stays unarmed. Production would have
armed it. This is the exact MA-10 divergence: the autotuner optimizes a
system where MC-absent gaps can leave the protective stop dark, while the
live engine always arms through them.

The replay's own comment on this block currently asserts the OPPOSITE of
production's actual behavior ("An absent MC opinion drives neither an arm
nor a disarm") -- the plan requires that misleading comment corrected in the
same diff as the behavioral fix.

Testing seam: `_replay_exit_tick(state, tick, tick_idx, n_ticks, p,
grace_minutes, execution_start_hhmm=...)` mutates `state` in place and is the
single per-tick exit core (autotuner.py:1119, shared by run_simulation,
_collect_sim_returns and replay_exit_sequence). `_fresh_replay_state()`
constructs the state dict. Both are already the established testing seam for
this file's internals (see test_c3_replay_internal_lockstep.py).
"""

from __future__ import annotations

import pathlib

import pytest

import autotuner

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_AUTOTUNER_PATH = _REPO_ROOT / "autotuner.py"

_DEFAULT_PARAMS = {
    "TRIGGER_THRESHOLD_PCT": 15.0,
    "TAKE_PROFIT_MC_PCT": 5.0,
    "VWAP_CROSS_HWM_PCT": 99.0,  # effectively disable VWAP for this file's tests
    "VWAP_BLEED_MULTIPLIER": 1.5,
    "VWAP_BLEED_TICKS": 999,
    "PARABOLIC_VELOCITY_THRESHOLD": 99.0,  # effectively disable parabolic squeeze
    "MAX_PARABOLIC_SQUEEZE": 0.5,
}


def _mc_absent_tick(ret: float = 1.0) -> dict:
    """A tick with no MC opinion available -- mirrors run_monte_carlo's
    MC_INSUFFICIENT_HISTORY_SENTINEL (None) or a regime-match-quality
    suppression, both of which production's mc_available = prob_underperforming
    is not None treats identically."""
    return {
        "time": "09:31",
        "return": ret,
        "mc_prob": None,
        "vol": 0.5,
        "vwap_diff": 0.0,
        "base_atr_pct": 0.5,
        "valid_vwap_weight": 0.0,
    }


def _mc_inband_tick(mc: float = 10.0, ret: float = 1.0) -> dict:
    """A tick with an available MC opinion inside the arm band
    [TAKE_PROFIT_MC_PCT, TRIGGER_THRESHOLD_PCT)."""
    return {
        "time": "09:31",
        "return": ret,
        "mc_prob": mc,
        "vol": 0.5,
        "vwap_diff": 0.0,
        "base_atr_pct": 0.5,
        "valid_vwap_weight": 0.0,
    }


def _mc_deterioration_tick(mc: float, ret: float) -> dict:
    """A tick engineered to hit the OLD inverted-disarm condition
    (mc > 2*TRIGGER_THRESHOLD_PCT and ret > 0.0). run_monte_carlo is HIGH when badly
    underperforming, so post-MA-4 this is DETERIORATION, not recovery -- it must NOT
    disarm (see test_replay_no_longer_disarms_on_extreme_mc_deterioration)."""
    return {
        "time": "09:31",
        "return": ret,
        "mc_prob": mc,
        "vol": 0.5,
        "vwap_diff": 0.0,
        "base_atr_pct": 0.5,
        "valid_vwap_weight": 0.0,
    }


# ===========================================================================
# 1 — Fail-open arming (the RED core of AC-3 / MA-10).
# ===========================================================================


def test_replay_arms_on_mc_absent_tick_when_previously_unarmed() -> None:
    """MA-10: an MC-absent tick while unarmed must ARM the stop (fail-open),
    matching production's `elif not mc_available: should_arm = True`
    (alpha_bot_execution.py:1324-1326).

    RED (pre-fix): the replay's arm block has no fail-open branch -- an
    MC-absent tick while unarmed leaves state["armed"] False.
    """
    state = autotuner._fresh_replay_state()
    assert state["armed"] is False, "Fixture self-check: state must start unarmed."

    autotuner._replay_exit_tick(
        state,
        _mc_absent_tick(),
        tick_idx=0,
        n_ticks=5,
        p=_DEFAULT_PARAMS,
        grace_minutes=0,
    )

    assert state["armed"] is True, (
        "MA-10 VIOLATED: an MC-absent tick while unarmed did not arm the replay's "
        "protective stop. Production fail-opens on MC-absent (an absent second "
        "opinion must never silently disable the protective stop); the replay must "
        "mirror this via an `elif not mc_available: should_arm = True`-equivalent "
        "branch."
    )


def test_replay_arms_on_mc_absent_regardless_of_return_sign() -> None:
    """Production's fail-open arm branch does not gate on current_return sign
    or magnitude -- only mc_available. Pin that the replay's fail-open branch
    is unconditional on `ret` too (a wrong fix might accidentally couple it to
    the disarm condition's `ret > 0.0` check)."""
    for ret in (-5.0, 0.0, 5.0):
        state = autotuner._fresh_replay_state()
        autotuner._replay_exit_tick(
            state,
            _mc_absent_tick(ret=ret),
            tick_idx=0,
            n_ticks=5,
            p=_DEFAULT_PARAMS,
            grace_minutes=0,
        )
        assert state["armed"] is True, (
            f"MC-absent tick with return={ret} did not arm -- the fail-open branch "
            "must be unconditional on current_return, matching production."
        )


# ===========================================================================
# 2 — MC-absent must NOT force a disarm (already-armed stays armed).
# ===========================================================================


def test_replay_stays_armed_on_mc_absent_tick_no_forced_disarm() -> None:
    """Production's disarm condition requires mc_available (mc_available and
    mc > 2*TRIGGER_THRESHOLD_PCT and ret > 0.0) -- an MC-absent tick can never
    disarm. Pin that this stays true after the AC-3 fix lands (a naive fix
    that flips should_arm unconditionally on mc_available=False could
    accidentally introduce a forced disarm path)."""
    state = autotuner._fresh_replay_state()
    state["armed"] = True

    autotuner._replay_exit_tick(
        state,
        _mc_absent_tick(ret=5.0),  # positive return -- would satisfy the disarm
        # condition's ret>0.0 leg IF mc were also available and extreme; it is not.
        tick_idx=0,
        n_ticks=5,
        p=_DEFAULT_PARAMS,
        grace_minutes=0,
    )

    assert state["armed"] is True, (
        "An MC-absent tick disarmed an already-armed position. Production never "
        "disarms without an available, extreme MC opinion -- MC-absent must leave "
        "an already-armed stop armed."
    )


# ===========================================================================
# 3 — Regression guards: the EXISTING correct ARM behavior for an AVAILABLE MC
#     opinion must not be disturbed by the AC-3 fix. NOTE: the DISARM regression
#     guard below was REWRITTEN by MA-4 (R3-b) — the OLD inverted disarm it pinned
#     was the bug; disarm now keys on genuine recovery (prob < TAKE_PROFIT_MC_PCT).
# ===========================================================================


def test_replay_still_arms_on_in_band_available_mc() -> None:
    """Regression guard: mc_available AND in [TAKE_PROFIT_MC_PCT,
    TRIGGER_THRESHOLD_PCT) must still arm -- this path is already correct and
    the AC-3 fix must not disturb it."""
    state = autotuner._fresh_replay_state()
    autotuner._replay_exit_tick(
        state,
        _mc_inband_tick(mc=10.0),  # 5.0 <= 10.0 < 15.0
        tick_idx=0,
        n_ticks=5,
        p=_DEFAULT_PARAMS,
        grace_minutes=0,
    )
    assert state["armed"] is True, (
        "Regression: an available, in-band MC opinion must still arm the stop."
    )


def test_replay_no_longer_disarms_on_extreme_mc_deterioration() -> None:
    """ROOT-CAUSE REWRITE (was test_replay_still_disarms_on_extreme_available_mc_with_positive_return).

    VERDICT: the original test PINNED THE MA-4 BUG as correct — it asserted that an
    extreme MC reading (mc > 2*TRIGGER_THRESHOLD_PCT) with a positive return DISARMS
    the stop. That is the inversion: run_monte_carlo is HIGH (~100) when badly
    underperforming, so mc=31 is DETERIORATION, and disarming there strips the
    protective stop exactly as compute_exit_confirmation's exit gate opens. The
    settled ruling (charter:7,:35) is the arbiter — disarm ONLY on genuine recovery
    (prob < TAKE_PROFIT_MC_PCT). So an extreme-MC/positive-return tick must now KEEP
    the stop armed.

    RED on base 57a8a897: the replay's OLD inverted disarm fires here -> armed=False.
    GREEN once the replay delegates to compute_arm_disarm_decision.

    below_stop_count still ends at 0 — but the ROOT CAUSE changed: it is now zeroed by
    compute_exit_confirmation's MC-breakdown veto (mc=31 < MC_BREAKDOWN_THRESHOLD 60
    -> the exit tick resets the count), NOT by a disarm (which no longer fires here).
    """
    state = autotuner._fresh_replay_state()
    state["armed"] = True
    state["below_stop_count"] = 2

    trigger_threshold = _DEFAULT_PARAMS["TRIGGER_THRESHOLD_PCT"]
    autotuner._replay_exit_tick(
        state,
        _mc_deterioration_tick(mc=(trigger_threshold * 2) + 1.0, ret=1.0),
        tick_idx=0,
        n_ticks=5,
        p=_DEFAULT_PARAMS,
        grace_minutes=0,
    )
    assert state["armed"] is True, (
        "MA-4: an extreme MC reading (deterioration, mc > 2x trigger) must NOT disarm "
        "-- the OLD inverted disarm did, stripping the protective stop at the worst "
        "moment. Disarm keys on genuine recovery (prob < TAKE_PROFIT_MC_PCT) only."
    )
    assert state["below_stop_count"] == 0, (
        "below_stop_count is zeroed by compute_exit_confirmation's MC-breakdown veto "
        "(mc=31 < 60 -> the exit tick resets the count), NOT by a disarm (none fires here)."
    )


def test_replay_does_not_arm_on_in_band_mc_below_take_profit_floor() -> None:
    """Regression guard: an available MC opinion BELOW TAKE_PROFIT_MC_PCT is
    outside the arm band and must not arm (nor is it MC-absent, so the
    fail-open branch must not fire either)."""
    state = autotuner._fresh_replay_state()
    autotuner._replay_exit_tick(
        state,
        _mc_inband_tick(mc=1.0),  # below TAKE_PROFIT_MC_PCT=5.0, still available
        tick_idx=0,
        n_ticks=5,
        p=_DEFAULT_PARAMS,
        grace_minutes=0,
    )
    assert state["armed"] is False, (
        "An available MC opinion below the arm band's floor incorrectly armed the "
        "stop -- the fail-open branch must only fire when MC is genuinely ABSENT "
        "(None), never merely low."
    )


# ===========================================================================
# 4 — Misleading comment correction (plan requirement: fixed in the same diff).
# ===========================================================================


def test_misleading_neither_arm_nor_disarm_comment_is_corrected() -> None:
    """The plan requires the replay's arm-block comment (which currently
    asserts 'An absent MC opinion drives neither an arm nor a disarm' -- false
    once the fail-open branch exists) be corrected in the same diff as the
    behavioral fix.

    RED (pre-fix): the false claim is still present verbatim.
    """
    src = _AUTOTUNER_PATH.read_text(encoding="utf-8")
    assert "drives neither an arm nor a disarm" not in src, (
        "autotuner.py still contains the comment claiming an absent MC opinion "
        "'drives neither an arm nor a disarm' -- this is now FALSE (MC-absent "
        "fail-opens to ARM per AC-3/MA-10). Correct the comment in the same diff "
        "as the behavioral fix so the source no longer documents the bug it just "
        "fixed."
    )
