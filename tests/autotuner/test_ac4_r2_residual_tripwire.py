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

THIS FILE'S ORIGINAL PURPOSE (R1, historical): a cheap, permanent tripwire --
NOT a RED test for R1, and NOT something r1-tuner or r1-engine needed to make
pass. It asserted the DESIRED FUTURE state (the undated path IS
regime-faithful) and was marked xfail(strict=False) because that assertion
was false at R1 ship time (verified live, not assumed).

R2 CONVERSION (this cycle, math-r2.md AC-4): R2 is the cycle that owns
resolving this residual (see test_ac4_undated_path_regime_faithful.py for the
full RED battery -- AST pins, real-fixture-derived tick counts, no-lookahead
spies, insufficient-history default -- covering _collect_sim_returns,
run_simulation, and run_simulation_crra_eu). This file's xfail marker is
REMOVED as of this cycle: the assertion below is now a genuine RED test,
expected to FAIL until R2's implementer wires
_replay_resolve_regime_exit_ticks into _collect_sim_returns, then PASS for
real once that lands. Do not silence, skip, or re-add an xfail marker to this
test without an explicit, reviewed PM decision that the residual is
permanently out of scope -- the historical xfail reasoning above the test
function is retained for provenance but no longer governs this test's
execution.

BINDING RIDER (R3 pre-retune checklist, ADDENDUM 6): the retune must NEVER
run on the mismatched optimizer -- R3 begins only after this test passes for
real (i.e. R2's undated-path wiring has landed).
"""

from __future__ import annotations

import datetime as _dt
import json
import pathlib

import autotuner
import regime_classifier

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

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


def test_undated_path_is_regime_faithful_not_hardcoded_three_ticks() -> None:
    """Desired state (R2): _collect_sim_returns resolves a regime-conditional
    exit_confirm_ticks per day, the same way _collect_sim_returns_dated
    already does (test_ac4_regime_conditional_exit_ticks.py).

    Scenario: a day with exactly 3 magnitude-and-breakdown-qualifying
    confirming ticks after arming -- enough to fire under the hardcoded
    default (3) but NOT enough under MEAN_REVERTING_EXIT_TICKS(=5)
    regime-faithful semantics. A genuinely regime-faithful undated path
    evaluating this day under a mean-reverting regime must produce NO
    triggered return (empty result) -- the position needs 2 more confirming
    ticks before the regime-adjusted ladder is satisfied.

    CORRECTION (R2, this cycle): the original module-docstring claim that
    "this test does not need to construct a genuine 20-day trailing history
    ... regardless of which mechanism R2 uses to resolve the label, as long
    as it reaches this path at all" is WRONG given R1's ruled no-lookahead
    safe-default contract (ADDENDUM 2 @ a46be889): insufficient trailing
    history (< regime_classifier.MIN_LABEL_SERIES_LENGTH=20) MUST resolve to
    the UNCHANGED base_ticks default, never an invented fallback -- see
    test_ac4_undated_path_regime_faithful.py's
    test_insufficient_trailing_history_keeps_hardcoded_default, which pins
    exactly this as a hard, correct contract. A history_data dict containing
    ONLY the target day (as originally written here) supplies ZERO trailing
    days, so a CORRECT regime-faithful implementation can never resolve
    "mean-reverting" for it -- it would always fall through to the safe
    default (3 ticks, unchanged) and this test could never legitimately pass.
    Fixed here by adding 20 real trailing days reproducing
    tests/fixtures/math/regime_classifier/mean_reverting_basic.json's return
    series (the same fixture-derivation discipline as the AC-4 R2 battery),
    so the scenario genuinely discriminates a correct fix from the bug.
    """
    fx = json.loads(
        (
            _REPO_ROOT
            / "tests"
            / "fixtures"
            / "math"
            / "regime_classifier"
            / "mean_reverting_basic.json"
        ).read_text(encoding="utf-8")
    )
    label = regime_classifier.classify_regime(fx["inputs"]["returns"])
    assert label == "mean-reverting", "Fixture self-check failed."

    trailing_days: dict[str, list[dict]] = {}
    d = _dt.date.fromisoformat("2026-03-01")
    added = 0
    for fraction in fx["inputs"]["returns"]:
        while d.weekday() >= 5:
            d += _dt.timedelta(days=1)
        ret_pct = fraction * autotuner.RETURN_PCT_TO_FRACTION
        trailing_days[d.isoformat()] = [_tick(ret_pct, 50.0)]
        d += _dt.timedelta(days=1)
        added += 1
    assert added == len(fx["inputs"]["returns"])
    assert max(trailing_days.keys()) < _DATE, (
        "Trailing day construction overran the target date -- adjust the start date."
    )

    ticks = _three_confirming_tick_sequence()
    history_data = {_SYM_ID: {**trailing_days, _DATE: ticks}}

    daily_returns = autotuner._collect_sim_returns(_PARAMS, history_data, [_SYM_ID], _DATE, {})

    assert daily_returns == [], (
        "R2 residual RESOLVED: _collect_sim_returns no longer fires on a "
        "3-confirming-tick sequence that requires 5 ticks under "
        "MEAN_REVERTING_EXIT_TICKS-faithful semantics -- the undated "
        f"search-objective path now appears regime-faithful. Got: {daily_returns!r}"
    )
