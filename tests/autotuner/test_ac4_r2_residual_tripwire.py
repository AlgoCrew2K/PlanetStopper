"""
AC-4/F5 residual tripwire — R2 owns wiring regime-conditional exit_confirm_ticks
into the UNDATED search-objective path (_collect_sim_returns / run_simulation /
run_simulation_crra_eu).

RULED (PM ADDENDUM 6 @ af266a63, closing the AC-4 sufficiency-review finding):
R1 wires regime-conditional exit_confirm_ticks into the DATED path only
(_collect_sim_returns_dated -- the CSCV/PBO selection/diagnostic gate; see
test_ac4_regime_conditional_exit_ticks.py, GREEN). The UNDATED path
(_collect_sim_returns, feeding Optuna's actual per-trial search objective via
run_simulation / run_simulation_crra_eu's `path_scores` ->
`sum(path_scores) / len(path_scores)`, the value Optuna's TPE sampler directly
optimizes) retains hardcoded math_engine.EXIT_CONFIRM_TICKS(=3) semantics
regardless of regime. The residual's home is R2 (not R3): R2's charter is
exactly the objective-computation redesign (real CPCV consumption / honest
purged folds) that legitimately rebuilds how trial scores are computed --
wiring regime ticks into _collect_sim_returns now would be churned by that
redesign weeks later, stacked on top of r1-tuner's correctly-identified blast
radius (MEAN_REVERTING_EXIT_TICKS=5 != EXIT_CONFIRM_TICKS default=3 could
silently shift numeric assertions in pre-existing >=20-day-history tests
outside R1's RED scope).

SEVERITY (exact framing -- do not compress into "AC-4 done" or omit): post-R1,
Optuna's TPE explores guided by a 3-tick score while SELECTION and the
CSCV/PBO/BHY gating -- the layer that actually decides SURVIVING params -- run
regime-faithful. This is a search-efficiency/consistency wart, NOT a
shipped-decision correctness cliff. It DOES mean the plan-Summary's "faithful
to production exit-decision semantics" claim is true for the selection/
diagnostic layer only, not the search layer.

THIS FILE'S PURPOSE: a cheap, permanent tripwire -- NOT a new RED test for
this cycle, and NOT something r1-tuner or r1-engine needs to make pass. It
asserts the DESIRED FUTURE state (the undated path IS regime-faithful) and is
marked xfail(strict=False) because that assertion is CURRENTLY false (verified
live below, not assumed). The day R2 wires _replay_resolve_regime_exit_ticks
(or an equivalent) into _collect_sim_returns, this test will unexpectedly PASS
(XPASS) -- visible in the test report, never build-breaking with
strict=False -- so the residual structurally cannot be silently forgotten.

DO NOT DELETE OR SILENCE THIS TEST without either (a) R2 wiring the fix and
this test flipping to a real passing test (remove the xfail marker at that
point -- do not just delete the file), or (b) an explicit, reviewed PM
decision that the residual is permanently out of scope.

BINDING RIDER (R3 pre-retune checklist, ADDENDUM 6): the retune must NEVER
run on the mismatched optimizer -- R3 begins only after this test's xfail
marker has been removed (i.e. R2's undated-path wiring has landed and this
test passes for real).
"""

from __future__ import annotations

import pytest

import autotuner

_SYM_ID = "sym-tripwire-001"
_DATE = "2026-04-06"

_PARAMS = {
    "TRIGGER_THRESHOLD_PCT": 15.0,
    "TAKE_PROFIT_MC_PCT": 5.0,
    "VWAP_CROSS_HWM_PCT": 99.0,
    "VWAP_BLEED_MULTIPLIER": 1.5,
    "VWAP_BLEED_TICKS": 999,
    "PARABOLIC_VELOCITY_THRESHOLD": 99.0,
    "MAX_PARABOLIC_SQUEEZE": 0.5,
}


def _tick(ret: float, mc: float) -> dict:
    return {
        "time": "tick",
        "return": ret,
        "mc_prob": mc,
        "vol": 1.0,
        "vwap_diff": 0.0,
        "base_atr_pct": 0.5,
        "valid_vwap_weight": 0.0,
    }


def _three_confirming_tick_sequence() -> list[dict]:
    """tick0 arms (mc=10.0, in the default band [5,15)); ticks1-3 are exactly
    3 confirming ticks (deep decline, mc=70.0 clears MC_BREAKDOWN_THRESHOLD=60).
    Under the DEFAULT (hardcoded) exit_confirm_ticks=3, this confirms and
    fires "Trailing Stop" at tick_idx=3 -- verified live via
    autotuner._replay_exit_tick tick-by-tick trace before locking this
    fixture in. Under a MEAN_REVERTING_EXIT_TICKS=5-faithful reading (the
    discriminator this tripwire targets), 3 confirming ticks is NOT enough --
    2 more would be required, so a genuinely regime-faithful undated path
    must NOT fire within these 4 ticks."""
    return [_tick(0.0, 10.0), _tick(-20.0, 70.0), _tick(-21.0, 70.0), _tick(-22.0, 70.0)]


@pytest.mark.xfail(
    reason=(
        "R2 residual (PM ADDENDUM 6): _collect_sim_returns (the undated "
        "search-objective path feeding Optuna's actual per-trial score) does "
        "not yet resolve regime-conditional exit_confirm_ticks -- it always "
        "uses _replay_exit_tick's hardcoded default (math_engine."
        "EXIT_CONFIRM_TICKS=3), regardless of the day's regime. This test "
        "asserts the DESIRED regime-faithful future state and is expected to "
        "fail until R2 wires _replay_resolve_regime_exit_ticks (or "
        "equivalent) into _collect_sim_returns / run_simulation. See this "
        "file's module docstring for the full ruling. DO NOT silence or "
        "delete this xfail without either landing the R2 fix (remove the "
        "marker, let the test pass for real) or an explicit PM decision "
        "that the residual is permanently out of scope."
    ),
    strict=False,
)
def test_undated_path_is_regime_faithful_not_hardcoded_three_ticks() -> None:
    """Desired future state (R2): _collect_sim_returns resolves a
    regime-conditional exit_confirm_ticks per day, the same way
    _collect_sim_returns_dated already does (test_ac4_regime_conditional_exit_ticks.py).

    Scenario: a day with exactly 3 magnitude-and-breakdown-qualifying
    confirming ticks after arming -- enough to fire under the hardcoded
    default (3) but NOT enough under MEAN_REVERTING_EXIT_TICKS(=5)
    regime-faithful semantics. A genuinely regime-faithful undated path
    evaluating this day under a mean-reverting regime must produce NO
    triggered return (empty result) -- the position needs 2 more confirming
    ticks before the regime-adjusted ladder is satisfied.

    XFAIL today (verified live, not assumed): _collect_sim_returns currently
    fires on this exact sequence (hardcoded 3-tick default), so
    ``daily_returns`` is non-empty -- the assertion below fails as expected.
    XPASS the day R2 lands the fix and this day is correctly evaluated as
    mean-reverting with a 5-tick ladder that these 4 ticks cannot satisfy.

    NOTE: this test does not need to construct a genuine 20-day trailing
    history that classify_regime would label "mean-reverting" -- it pins the
    CONSEQUENCE regime-conditioning must have (this specific tick count no
    longer fires), which is true regardless of which mechanism R2 uses to
    resolve the label, as long as it reaches this path at all.
    """
    ticks = _three_confirming_tick_sequence()
    history_data = {_SYM_ID: {_DATE: ticks}}

    daily_returns = autotuner._collect_sim_returns(_PARAMS, history_data, [_SYM_ID], _DATE, {})

    assert daily_returns == [], (
        "R2 residual RESOLVED: _collect_sim_returns no longer fires on a "
        "3-confirming-tick sequence that requires 5 ticks under "
        "MEAN_REVERTING_EXIT_TICKS-faithful semantics -- the undated "
        "search-objective path now appears regime-faithful. This is an "
        "XPASS, not a failure: remove this test's xfail marker (do not "
        "delete the test) and update DE-MATH-R1-001 / the R3 pre-retune "
        f"checklist to record that the R2 residual has landed. Got: {daily_returns!r}"
    )
