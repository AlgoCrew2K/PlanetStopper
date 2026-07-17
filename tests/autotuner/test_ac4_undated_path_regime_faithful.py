"""
AC-4 (R2 residual clearance) -- regime-conditional exit_confirm_ticks wired
into the UNDATED search-objective path: _collect_sim_returns, run_simulation,
and (transitively, since it delegates to _collect_sim_returns)
run_simulation_crra_eu.

RULED CONTRACT (this cycle's target, per PM ADDENDUM 6 @ af266a63 closing R1's
AC-4 sufficiency review, and math-r2.md AC-4): R1 wired regime-conditional
exit_confirm_ticks into the DATED path only (_collect_sim_returns_dated --
see test_ac4_regime_conditional_exit_ticks.py, GREEN since R1). The UNDATED
path -- _collect_sim_returns (feeding Optuna's actual per-trial search
objective via the objective() closure's path_scores, and the OOS
cascade's validation_returns/frozen_eval_returns) and run_simulation (the
legacy Sortino objective feeding oos_alpha/fallback_oos_alpha/default_oos_alpha)
-- retained hardcoded math_engine.EXIT_CONFIRM_TICKS(=3) semantics regardless
of regime. This file mirrors test_ac4_regime_conditional_exit_ticks.py's
established pattern (AST pin + real-fixture-derived behavioral pin +
no-lookahead spy + insufficient-history default) for these two undated-path
functions, and closes test_ac4_r2_residual_tripwire.py's xfail tripwire.

Confirmed by direct read (pre-fix): _collect_sim_returns (autotuner.py,
~line 1490) and run_simulation (~line 1839) both call _replay_exit_tick with
NO exit_confirm_ticks kwarg -- unlike _collect_sim_returns_dated (~line 1585),
which explicitly resolves and passes _regime_exit_ticks. Zero occurrences of
"_replay_resolve_regime_exit_ticks" inside either function pre-fix.

No assertions hardcode a producer-computed value: expected tick counts are
derived by calling the REAL regime_classifier.classify_regime +
math_engine.apply_regime_exit_adjustment over the existing
tests/fixtures/math/regime_classifier/*.json series (same fixtures R1 used).
"""

from __future__ import annotations

import ast
import inspect
import json
import pathlib
import unittest.mock as mock

import pytest

import autotuner
import math_engine
import regime_classifier

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_AUTOTUNER_PATH = _REPO_ROOT / "autotuner.py"
_REGIME_FIXTURE_DIR = _REPO_ROOT / "tests" / "fixtures" / "math" / "regime_classifier"

_SYM_ID = "sym-ac4-r2-001"
_CURRENT_DATE = "2026-05-05"

_PARAMS = {
    "TRIGGER_THRESHOLD_PCT": 15.0,
    "TAKE_PROFIT_MC_PCT": 5.0,
    "VWAP_CROSS_HWM_PCT": 99.0,
    "VWAP_BLEED_MULTIPLIER": 1.5,
    "VWAP_BLEED_TICKS": 999,
    "PARABOLIC_VELOCITY_THRESHOLD": 99.0,
    "MAX_PARABOLIC_SQUEEZE": 0.5,
}


def _load_regime_fixture(name: str) -> dict:
    with (_REGIME_FIXTURE_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


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
    """Arms at tick0 (mc=10.0, in [5,15)); ticks1-3 are 3 confirming ticks
    (deep decline, mc=70.0 clears MC_BREAKDOWN_THRESHOLD=60). Fires under the
    hardcoded default (3 ticks); must NOT fire under a mean-reverting
    (5-tick) regime-faithful reading -- same fixture as
    test_ac4_r2_residual_tripwire.py so both files agree on the scenario."""
    return [_tick(0.0, 10.0), _tick(-20.0, 70.0), _tick(-21.0, 70.0), _tick(-22.0, 70.0)]


def _two_confirming_tick_sequence() -> list[dict]:
    """Arms at tick0; ticks1-2 are exactly 2 confirming ticks. Does NOT fire
    under the hardcoded default (needs 3) but MUST fire under a trending
    (2-tick) regime-faithful reading -- the under-fire direction, symmetric
    to the mean-reverting over-fire direction above."""
    return [_tick(0.0, 10.0), _tick(-20.0, 70.0), _tick(-21.0, 70.0)]


def _trailing_days(returns_fraction: list[float], start_date: str, target_date: str) -> dict:
    """Build MIN_LABEL_SERIES_LENGTH trailing days (one EOD tick each, in
    PERCENT units per synthetic_history's tick['return'] convention) ending
    strictly before target_date, keyed under one symphony, using
    trading-day-spaced synthetic dates so date ordering is deterministic and
    sorts correctly as ISO strings.
    """
    import datetime as _dt

    n = len(returns_fraction)
    base = _dt.date.fromisoformat(start_date)
    days: dict[str, list[dict]] = {}
    d = base
    added = 0
    while added < n:
        if d.weekday() < 5:  # skip weekends -- trading-day cadence, matches production
            ret_pct = returns_fraction[added] * autotuner.RETURN_PCT_TO_FRACTION
            days[d.isoformat()] = [_tick(ret_pct, 50.0)]
            added += 1
        d += _dt.timedelta(days=1)
    assert max(days.keys()) < target_date, (
        "Trailing day construction overran target_date -- fixture bug, tighten start_date."
    )
    return days


# ===========================================================================
# (a) Structural: _collect_sim_returns / run_simulation pass an explicit
#     exit_confirm_ticks= kwarg to _replay_exit_tick (AST pin, mirrors R1's
#     established TestInLoopGateUsesHelper pattern).
# ===========================================================================


def _function_node(name: str) -> ast.FunctionDef:
    src = _AUTOTUNER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(_AUTOTUNER_PATH))
    node = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name),
        None,
    )
    assert node is not None, f"{name} function not found in autotuner.py"
    return node


@pytest.mark.parametrize("func_name", ["_collect_sim_returns", "run_simulation"])
def test_undated_path_function_passes_explicit_exit_confirm_ticks_kwarg(func_name: str) -> None:
    """RED (pre-fix): neither _collect_sim_returns nor run_simulation passes
    an exit_confirm_ticks= kwarg on its _replay_exit_tick call -- both rely
    silently on the module-level hardcoded default, exactly the F5-class gap
    R1 already closed on the dated path (_collect_sim_returns_dated)."""
    target_func = _function_node(func_name)
    replay_calls = [
        node
        for node in ast.walk(target_func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_replay_exit_tick"
    ]
    assert replay_calls, f"{func_name} has no _replay_exit_tick call to inspect."
    for call in replay_calls:
        kwarg_names = {kw.arg for kw in call.keywords}
        assert "exit_confirm_ticks" in kwarg_names, (
            f"{func_name}'s _replay_exit_tick call at line {call.lineno} does not pass "
            "an explicit exit_confirm_ticks= kwarg -- it silently uses the hardcoded "
            "module default, never regime-conditioned. AC-4 (R2 residual) requires the "
            "regime-resolved count to reach this call site explicitly, the same way "
            "_collect_sim_returns_dated already does."
        )


@pytest.mark.parametrize("func_name", ["_collect_sim_returns", "run_simulation"])
def test_undated_path_function_resolves_regime_ticks_via_the_shared_r1_helper(
    func_name: str,
) -> None:
    """The undated path must REUSE R1's _replay_resolve_regime_exit_ticks (no
    lookahead, recompute-fresh contract) rather than inventing a second
    resolver -- one source of truth for the no-lookahead guarantee."""
    src = _AUTOTUNER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(_AUTOTUNER_PATH))
    target_func = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func_name),
        None,
    )
    assert target_func is not None
    calls_helper = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_replay_resolve_regime_exit_ticks"
        for node in ast.walk(target_func)
    )
    assert calls_helper, (
        f"{func_name} does not call _replay_resolve_regime_exit_ticks -- AC-4 (R2 "
        "residual) requires reusing R1's shared no-lookahead resolver, not a new one."
    )


# ===========================================================================
# (b) Behavioral: real-fixture-derived tick counts actually change firing.
# ===========================================================================


class TestCollectSimReturnsRegimeFaithful:
    def test_mean_reverting_regime_suppresses_the_hardcoded_three_tick_fire(self) -> None:
        """20 trailing days reproducing the mean-reverting fixture's return
        series, then a target day with exactly 3 confirming ticks (fires
        under the hardcoded default=3). Once regime-wired, mean-reverting
        resolves to MEAN_REVERTING_EXIT_TICKS=5 -- 3 confirming ticks must
        NOT be enough, so _collect_sim_returns must return an EMPTY list for
        this symphony/date (no triggered return)."""
        fx = _load_regime_fixture("mean_reverting_basic.json")
        label = regime_classifier.classify_regime(fx["inputs"]["returns"])
        assert label == "mean-reverting", "Fixture self-check failed."
        expected_ticks = math_engine.apply_regime_exit_adjustment(
            regime_label=label, base_ticks=math_engine.EXIT_CONFIRM_TICKS
        )
        assert expected_ticks == math_engine.MEAN_REVERTING_EXIT_TICKS

        history_data = {
            _SYM_ID: {
                **_trailing_days(fx["inputs"]["returns"], "2026-04-01", _CURRENT_DATE),
                _CURRENT_DATE: _three_confirming_tick_sequence(),
            }
        }

        daily_returns = autotuner._collect_sim_returns(
            _PARAMS, history_data, [_SYM_ID], _CURRENT_DATE, {}
        )
        assert daily_returns == [], (
            "_collect_sim_returns fired on 3 confirming ticks under a mean-reverting "
            f"regime (requires {expected_ticks} ticks per apply_regime_exit_adjustment) "
            f"-- got {daily_returns!r}. The undated path is not regime-faithful."
        )

    def test_trending_regime_fires_on_two_confirming_ticks_hardcoded_default_would_miss(
        self,
    ) -> None:
        """Symmetric under-fire direction: a trending regime resolves to
        TRENDING_EXIT_TICKS=2 (fewer than the hardcoded default=3). A
        2-confirming-tick day that would NOT fire under the hardcoded
        default MUST fire once regime-wired."""
        fx = _load_regime_fixture("trending_basic.json")
        label = regime_classifier.classify_regime(fx["inputs"]["returns"])
        assert label == "trending", "Fixture self-check failed."
        expected_ticks = math_engine.apply_regime_exit_adjustment(
            regime_label=label, base_ticks=math_engine.EXIT_CONFIRM_TICKS
        )
        assert expected_ticks == math_engine.TRENDING_EXIT_TICKS

        history_data = {
            _SYM_ID: {
                **_trailing_days(fx["inputs"]["returns"], "2026-04-01", _CURRENT_DATE),
                _CURRENT_DATE: _two_confirming_tick_sequence(),
            }
        }

        # Pre-fix control: the hardcoded-default 3-tick behavior must NOT fire
        # on 2 confirming ticks -- confirms the fixture is discriminating, not
        # trivially true for any tick count.
        no_regime_history = {_SYM_ID: {_CURRENT_DATE: _two_confirming_tick_sequence()}}
        control = autotuner._collect_sim_returns(
            _PARAMS, no_regime_history, [_SYM_ID], _CURRENT_DATE, {}
        )
        assert control == [], (
            "Fixture control failed: 2 confirming ticks alone (no trailing history) "
            f"already fires under the hardcoded default -- got {control!r}. This "
            "scenario cannot discriminate trending-regime-faithful behavior."
        )

        daily_returns = autotuner._collect_sim_returns(
            _PARAMS, history_data, [_SYM_ID], _CURRENT_DATE, {}
        )
        assert daily_returns != [], (
            "_collect_sim_returns did not fire on 2 confirming ticks under a trending "
            f"regime (requires only {expected_ticks} ticks per apply_regime_exit_adjustment) "
            "-- the undated path is not regime-faithful."
        )

    def test_insufficient_trailing_history_keeps_hardcoded_default(self) -> None:
        """Fewer than regime_classifier.MIN_LABEL_SERIES_LENGTH trailing days
        -> classify_regime returns None -> apply_regime_exit_adjustment's own
        safe default fires (base_ticks unchanged) -- 3 confirming ticks still
        fires exactly as the pre-R2 hardcoded behavior did."""
        short_trailing = {
            f"2026-03-{d:02d}": [_tick(0.1, 50.0)] for d in range(1, 6)  # only 5 days
        }
        history_data = {
            _SYM_ID: {
                **short_trailing,
                _CURRENT_DATE: _three_confirming_tick_sequence(),
            }
        }
        daily_returns = autotuner._collect_sim_returns(
            _PARAMS, history_data, [_SYM_ID], _CURRENT_DATE, {}
        )
        assert daily_returns != [], (
            "With insufficient trailing history (<20 days), _collect_sim_returns must "
            "keep the hardcoded EXIT_CONFIRM_TICKS=3 default (safe-default contract) "
            f"and still fire on 3 confirming ticks -- got {daily_returns!r}."
        )


class TestRunSimulationRegimeFaithful:
    def test_mean_reverting_regime_suppresses_the_hardcoded_three_tick_fire(self) -> None:
        """Same scenario as _collect_sim_returns, driven through run_simulation
        (the legacy Sortino objective backing oos_alpha/fallback_oos_alpha/
        default_oos_alpha). No triggered day -> total_guard_alpha stays 0.0
        -> run_simulation returns 0.0 (== -total_guard_alpha)."""
        fx = _load_regime_fixture("mean_reverting_basic.json")
        label = regime_classifier.classify_regime(fx["inputs"]["returns"])
        assert label == "mean-reverting"
        history_data = {
            _SYM_ID: {
                **_trailing_days(fx["inputs"]["returns"], "2026-04-01", _CURRENT_DATE),
                _CURRENT_DATE: _three_confirming_tick_sequence(),
            }
        }
        result = autotuner.run_simulation(_PARAMS, history_data, [_SYM_ID], _CURRENT_DATE, {})
        assert result == pytest.approx(0.0), (
            f"run_simulation returned {result!r}, expected 0.0 (no triggered day) under "
            "a mean-reverting regime that requires 5 confirming ticks -- the undated "
            "legacy-Sortino path is not regime-faithful."
        )


def test_run_simulation_crra_eu_inherits_regime_faithfulness_via_collect_sim_returns() -> None:
    """run_simulation_crra_eu delegates entirely to _collect_sim_returns
    (autotuner.py ~line 1939) -- once that function is regime-wired,
    run_simulation_crra_eu must produce the CRRA-EU no-signal value (0.0,
    per its own empty-series contract) for the same mean-reverting
    3-confirming-tick scenario that must not fire."""
    fx = _load_regime_fixture("mean_reverting_basic.json")
    label = regime_classifier.classify_regime(fx["inputs"]["returns"])
    assert label == "mean-reverting"
    history_data = {
        _SYM_ID: {
            **_trailing_days(fx["inputs"]["returns"], "2026-04-01", _CURRENT_DATE),
            _CURRENT_DATE: _three_confirming_tick_sequence(),
        }
    }
    result = autotuner.run_simulation_crra_eu(
        _PARAMS, history_data, [_SYM_ID], _CURRENT_DATE, {}, gamma=2.0
    )
    assert result == 0.0, (
        f"run_simulation_crra_eu returned {result!r}, expected 0.0 (empty-series "
        "no-signal contract) under a mean-reverting regime that suppresses the "
        "3-confirming-tick fire -- CRRA-EU objective is not regime-faithful."
    )


# ===========================================================================
# (c) No-lookahead: the per-date replay loop inside _collect_sim_returns must
#     never hand classify_regime the current or a future date's return.
# ===========================================================================


def test_collect_sim_returns_no_lookahead_trailing_window_excludes_current_and_future_days() -> (
    None
):
    """Mirrors test_ac4_regime_conditional_exit_ticks.py's no-lookahead spy,
    driven through _collect_sim_returns instead of the dated variant.
    RED (pre-fix): classify_regime is never called at all from
    _collect_sim_returns -- zero calls recorded, failing the
    "at least one call" precondition before lookahead can even be assessed.
    """
    day_returns_pct = {
        "2026-04-06": -37.0,
        "2026-04-07": -41.0,
        "2026-04-08": -43.0,
    }
    history_data = {
        _SYM_ID: {
            date: [_tick(ret, 50.0)] for date, ret in day_returns_pct.items()
        }
    }

    calls: list[list[float]] = []
    real_classify = regime_classifier.classify_regime

    def _spy(returns):
        calls.append(list(returns))
        return real_classify(returns)

    with mock.patch.object(regime_classifier, "classify_regime", side_effect=_spy):
        autotuner._collect_sim_returns(_PARAMS, history_data, [_SYM_ID], "2026-04-08", {})

    assert calls, (
        "regime_classifier.classify_regime was never called while replaying 3 days of "
        "history through _collect_sim_returns -- the undated path never resolves a "
        "regime label at all (AC-4 R2 residual's core symptom)."
    )
    for call_returns in calls:
        assert len(call_returns) < len(day_returns_pct), (
            f"A trailing window passed to classify_regime from _collect_sim_returns "
            f"contained {len(call_returns)} of {len(day_returns_pct)} known days -- a "
            "window covering the full history proves at least one day saw its own or "
            f"a future day's return (lookahead). Window: {call_returns!r}."
        )


def test_run_simulation_no_lookahead_trailing_window_excludes_current_and_future_days() -> None:
    """Same no-lookahead spy, driven through run_simulation."""
    day_returns_pct = {
        "2026-04-06": -37.0,
        "2026-04-07": -41.0,
        "2026-04-08": -43.0,
    }
    history_data = {
        _SYM_ID: {
            date: [_tick(ret, 50.0)] for date, ret in day_returns_pct.items()
        }
    }

    calls: list[list[float]] = []
    real_classify = regime_classifier.classify_regime

    def _spy(returns):
        calls.append(list(returns))
        return real_classify(returns)

    with mock.patch.object(regime_classifier, "classify_regime", side_effect=_spy):
        autotuner.run_simulation(_PARAMS, history_data, [_SYM_ID], "2026-04-08", {})

    assert calls, (
        "regime_classifier.classify_regime was never called while replaying 3 days of "
        "history through run_simulation -- the legacy-Sortino undated path never "
        "resolves a regime label at all (AC-4 R2 residual's core symptom)."
    )
    for call_returns in calls:
        assert len(call_returns) < len(day_returns_pct), (
            f"A trailing window passed to classify_regime from run_simulation contained "
            f"{len(call_returns)} of {len(day_returns_pct)} known days -- lookahead. "
            f"Window: {call_returns!r}."
        )


# ===========================================================================
# Sanity: inspect.signature confirms _replay_exit_tick already carries the
# exit_confirm_ticks parameter (R1 landed this on the shared core) -- this
# file's gap is purely about the two undated callers not USING it.
# ===========================================================================


def test_replay_exit_tick_already_has_exit_confirm_ticks_parameter() -> None:
    sig = inspect.signature(autotuner._replay_exit_tick)
    assert "exit_confirm_ticks" in sig.parameters, (
        "_replay_exit_tick lost its exit_confirm_ticks parameter (R1 regression) -- "
        "this file assumes it is present and merely unused by the undated callers."
    )
