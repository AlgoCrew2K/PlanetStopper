"""
RED -- AC-2v2/AC-4 (exit-friction-realized-savings): friction subtraction
applied uniformly at all three replay exit-accounting sites.

AC-2v2 (PM ruling 2026-07-24, folding ga2-opt's independent recon): the
``triggered_return = tick['return'] + penalty`` pattern is duplicated in
THREE functions -- _collect_sim_returns (autotuner.py:1523-1526),
_collect_sim_returns_dated (autotuner.py:1717-1720), and run_simulation
(autotuner.py:1984-1987). SIM_EXIT_FRICTION_PCT must be subtracted as a
DISTINCT term at all three, only when a trigger fires, exactly once per
triggered day -- never folded into the deviation-dict `penalty`.

Test strategy: patch the shared per-tick core `_replay_exit_tick` to fire a
deterministic trigger on a chosen tick_idx (isolating the friction-
accounting arithmetic from the exit-DECISION math, which ~80 other tests in
this suite already cover). The patch replicates ONLY the trivial,
documented, unconditional HWM-tracking side effect
(`if ret > state["hwm"]: state["hwm"] = ret`) that `_replay_exit_tick`
performs before any decision logic -- required because `run_simulation`
reads `day_state["hwm"]` after the tick loop for its missed-upside/drawdown
penalties. This is not "mocking the math engine" (math_engine.py is never
touched) -- it isolates the NEW friction line from the pre-existing,
separately-tested exit-decision cascade.

Expected values are hand-derived in each test from the fixture's raw inputs
plus the real named constants (SORTINO_OBJ_*, SIM_EXIT_FRICTION_PCT) --
never hardcoded producer literals (house test rule).
"""

from __future__ import annotations

import contextlib
import json
import pathlib
from unittest.mock import patch

import pytest

_FIXTURE = json.loads(
    (
        pathlib.Path(__file__).parent.parent
        / "fixtures"
        / "math"
        / "exit_friction_replay_three_sites.json"
    ).read_text(encoding="utf-8")
)


def _import_autotuner():
    import autotuner

    return autotuner


@contextlib.contextmanager
def _patched_replay_core(trigger_tick_idx: int, reason: str):
    """Patch the shared per-tick core to fire `reason` deterministically on
    `trigger_tick_idx`, replicating only the unconditional HWM-tracking side
    effect the real function performs before any decision logic runs.
    """
    autotuner = _import_autotuner()

    def _mock_replay_exit_tick(
        state,
        tick,
        tick_idx,
        n_ticks,
        p,
        grace_minutes,
        execution_start_hhmm="09:30",
        exit_confirm_ticks=3,
    ):
        ret = tick.get("return", 0.0)
        if ret > state["hwm"]:
            state["hwm"] = ret
        if tick_idx == trigger_tick_idx:
            return reason
        return None

    with (
        patch("autotuner._replay_exit_tick", side_effect=_mock_replay_exit_tick),
        patch("autotuner._replay_grace_minutes", return_value=0),
        patch("autotuner._replay_execution_start_time", return_value="09:30"),
    ):
        yield autotuner


def _history_for(ticks: list[dict], sym_id: str, date: str) -> dict:
    return {sym_id: {date: ticks}}


# ---------------------------------------------------------------------------
# Shared single-tick scenario: pins the core friction delta at all 3 sites.
# ---------------------------------------------------------------------------

_SYM = _FIXTURE["symphony_id"]
_DATE = _FIXTURE["trading_date"]
_REASON = _FIXTURE["deviation_reason"]
_PENALTY = _FIXTURE["deviation_penalty_pct"]
_SINGLE_TICK_DAY = _FIXTURE["single_tick_day"]


def _expected_guard_alpha_single_tick(friction: float) -> float:
    """Hand-derived guard_alpha for the single-tick scenario:
    triggered_return = tick_return + penalty - friction; eod_return = tick_return
    (single-tick day: trigger tick IS the eod tick).
    """
    tick_return = _SINGLE_TICK_DAY["ticks"][0]["return"]
    triggered_return = tick_return + _PENALTY - friction
    eod_return = tick_return
    return triggered_return - eod_return


class TestCollectSimReturnsFrictionApplication:
    """Site 1: _collect_sim_returns -- feeds the CRRA-EU trial objective."""

    def test_friction_off_reproduces_pre_feature_guard_alpha(self):
        autotuner = _import_autotuner()
        history = _history_for(_SINGLE_TICK_DAY["ticks"], _SYM, _DATE)
        expected = _expected_guard_alpha_single_tick(friction=0.0)

        with (
            _patched_replay_core(_SINGLE_TICK_DAY["trigger_tick_idx"], _REASON),
            patch.object(autotuner, "SIM_EXIT_FRICTION_PCT", 0.0),
        ):
            result = autotuner._collect_sim_returns(
                {}, history, [_SYM], "2026-05-10", {_REASON: _PENALTY}
            )

        assert result == pytest.approx([expected], abs=1e-9), (
            f"_collect_sim_returns with friction=0.0 must reproduce the "
            f"pre-feature guard_alpha exactly (AC-4 backward-compat). "
            f"expected={[expected]}, got={result}"
        )

    def test_friction_on_subtracts_distinctly_from_deviation_penalty(self):
        autotuner = _import_autotuner()
        history = _history_for(_SINGLE_TICK_DAY["ticks"], _SYM, _DATE)
        friction = autotuner.SIM_EXIT_FRICTION_PCT
        expected_on = _expected_guard_alpha_single_tick(friction=friction)
        expected_off = _expected_guard_alpha_single_tick(friction=0.0)

        with _patched_replay_core(_SINGLE_TICK_DAY["trigger_tick_idx"], _REASON):
            result_on = autotuner._collect_sim_returns(
                {}, history, [_SYM], "2026-05-10", {_REASON: _PENALTY}
            )
        with (
            _patched_replay_core(_SINGLE_TICK_DAY["trigger_tick_idx"], _REASON),
            patch.object(autotuner, "SIM_EXIT_FRICTION_PCT", 0.0),
        ):
            result_off = autotuner._collect_sim_returns(
                {}, history, [_SYM], "2026-05-10", {_REASON: _PENALTY}
            )

        assert result_on == pytest.approx([expected_on], abs=1e-9), (
            f"_collect_sim_returns friction-on result mismatch. "
            f"expected={[expected_on]}, got={result_on}"
        )
        delta = result_off[0] - result_on[0]
        assert delta == pytest.approx(friction, abs=1e-9), (
            f"friction-off minus friction-on delta must equal exactly "
            f"SIM_EXIT_FRICTION_PCT ({friction}) -- the friction subtraction "
            f"must be a distinct term, not folded into the -0.20 deviation "
            f"penalty. Delta observed: {delta}"
        )


class TestCollectSimReturnsDatedFrictionApplication:
    """Site 2: _collect_sim_returns_dated -- feeds cscv_date_returns -> compute_pbo
    (the Phase-3 PBO overfitting veto). Same accounting line, dated output shape.
    """

    def test_friction_off_reproduces_pre_feature_guard_alpha(self):
        autotuner = _import_autotuner()
        history = _history_for(_SINGLE_TICK_DAY["ticks"], _SYM, _DATE)
        expected = _expected_guard_alpha_single_tick(friction=0.0)

        with (
            _patched_replay_core(_SINGLE_TICK_DAY["trigger_tick_idx"], _REASON),
            patch.object(autotuner, "SIM_EXIT_FRICTION_PCT", 0.0),
        ):
            result = autotuner._collect_sim_returns_dated(
                {}, history, [_SYM], "2026-05-10", {_REASON: _PENALTY}
            )

        assert len(result) == 1
        got_date, got_alpha = result[0]
        assert got_date == _DATE
        assert got_alpha == pytest.approx(expected, abs=1e-9)

    def test_friction_on_subtracts_distinctly_and_matches_undated_sibling(self):
        """The dated and undated functions must apply friction IDENTICALLY --
        a divergence here would mean the PBO gate (fed by the dated variant)
        sees a different friction basis than the trial objective (fed by the
        undated variant), reintroducing exactly the gross-vs-net split AC-2v2
        exists to close.
        """
        autotuner = _import_autotuner()
        history = _history_for(_SINGLE_TICK_DAY["ticks"], _SYM, _DATE)
        friction = autotuner.SIM_EXIT_FRICTION_PCT
        expected_on = _expected_guard_alpha_single_tick(friction=friction)

        with _patched_replay_core(_SINGLE_TICK_DAY["trigger_tick_idx"], _REASON):
            dated_result = autotuner._collect_sim_returns_dated(
                {}, history, [_SYM], "2026-05-10", {_REASON: _PENALTY}
            )
        with _patched_replay_core(_SINGLE_TICK_DAY["trigger_tick_idx"], _REASON):
            undated_result = autotuner._collect_sim_returns(
                {}, history, [_SYM], "2026-05-10", {_REASON: _PENALTY}
            )

        assert len(dated_result) == 1
        _got_date, got_alpha = dated_result[0]
        assert got_alpha == pytest.approx(expected_on, abs=1e-9)
        assert got_alpha == pytest.approx(undated_result[0], abs=1e-9), (
            "_collect_sim_returns_dated and _collect_sim_returns must produce "
            "IDENTICAL friction-adjusted guard_alpha for the same scenario -- "
            "a divergence would leave the PBO veto on a different friction "
            "basis than the trial objective (AC-2v2's core rationale)."
        )


class TestRunSimulationFrictionApplication:
    """Site 3: run_simulation -- feeds oos_alpha/fallback_oos_alpha/default_oos_alpha,
    the acceptance-gate accept/reject cascade. Includes the missed-upside/
    drawdown penalty block, so the single-tick scenario (both thresholds
    un-crossed) isolates the friction delta cleanly.
    """

    def _expected_run_simulation_return(self, autotuner, friction: float) -> float:
        tick_return = _SINGLE_TICK_DAY["ticks"][0]["return"]
        triggered_return = tick_return + _PENALTY - friction
        eod_return = tick_return
        day_max_return = tick_return
        safe_hwm = tick_return  # single tick: hwm == the only return seen

        guard_alpha = triggered_return - eod_return
        missed_upside = day_max_return - triggered_return
        drawdown_from_peak = safe_hwm - triggered_return

        total = 0.0
        if missed_upside > autotuner.SORTINO_OBJ_MISSED_UPSIDE_THRESHOLD:
            total -= missed_upside * autotuner.SORTINO_OBJ_MISSED_UPSIDE_MULT
        if safe_hwm > 1.0 and drawdown_from_peak > autotuner.SORTINO_OBJ_DRAWDOWN_THRESHOLD:
            total -= drawdown_from_peak * autotuner.SORTINO_OBJ_DRAWDOWN_MULT
        if guard_alpha < 0:
            total += guard_alpha * autotuner.SORTINO_OBJ_NEGATIVE_GUARD_ALPHA_MULT
        else:
            total += guard_alpha
        # run_simulation returns -total_guard_alpha (autotuner.py:2021).
        return -total

    def test_friction_off_reproduces_pre_feature_value(self):
        autotuner = _import_autotuner()
        history = _history_for(_SINGLE_TICK_DAY["ticks"], _SYM, _DATE)
        expected = self._expected_run_simulation_return(autotuner, friction=0.0)

        with (
            _patched_replay_core(_SINGLE_TICK_DAY["trigger_tick_idx"], _REASON),
            patch.object(autotuner, "SIM_EXIT_FRICTION_PCT", 0.0),
        ):
            result = autotuner.run_simulation({}, history, [_SYM], "2026-05-10", {_REASON: _PENALTY})

        assert result == pytest.approx(expected, abs=1e-9), (
            f"run_simulation with friction=0.0 must reproduce the pre-feature "
            f"value exactly (AC-4 backward-compat). expected={expected}, got={result}"
        )

    def test_friction_on_subtracts_distinctly(self):
        autotuner = _import_autotuner()
        history = _history_for(_SINGLE_TICK_DAY["ticks"], _SYM, _DATE)
        friction = autotuner.SIM_EXIT_FRICTION_PCT
        expected_on = self._expected_run_simulation_return(autotuner, friction=friction)
        expected_off = self._expected_run_simulation_return(autotuner, friction=0.0)

        with _patched_replay_core(_SINGLE_TICK_DAY["trigger_tick_idx"], _REASON):
            result_on = autotuner.run_simulation({}, history, [_SYM], "2026-05-10", {_REASON: _PENALTY})

        assert result_on == pytest.approx(expected_on, abs=1e-9), (
            f"run_simulation friction-on result mismatch. "
            f"expected={expected_on}, got={result_on}"
        )
        # Both scenarios stay below the missed-upside/drawdown thresholds, so
        # the delta reduces to friction * NEGATIVE_GUARD_ALPHA_MULT (the
        # guard_alpha stays negative in both cases -- see the fixture math).
        assert (expected_off - expected_on) == pytest.approx(
            friction * autotuner.SORTINO_OBJ_NEGATIVE_GUARD_ALPHA_MULT, abs=1e-9
        ), "Hand-derivation sanity check failed -- fixture scenario assumption violated."


# ---------------------------------------------------------------------------
# Edge case: friction exceeds the exit's gain -> negative guard-alpha must
# flow through UNCLAMPED (plan Edge Cases section).
# ---------------------------------------------------------------------------


class TestFrictionExceedsGainFlowsThroughUnclamped:
    _SCENARIO = _FIXTURE["friction_exceeds_gain_day"]

    def test_collect_sim_returns_flips_positive_to_negative_unclamped(self):
        autotuner = _import_autotuner()
        history = _history_for(self._SCENARIO["ticks"], _SYM, _DATE)
        friction = autotuner.SIM_EXIT_FRICTION_PCT
        override_penalty = self._SCENARIO["deviation_penalty_pct_override"]

        trigger_ret = self._SCENARIO["ticks"][self._SCENARIO["trigger_tick_idx"]]["return"]
        eod_ret = self._SCENARIO["ticks"][-1]["return"]
        expected_without_friction = (trigger_ret + override_penalty) - eod_ret
        expected_with_friction = (trigger_ret + override_penalty - friction) - eod_ret

        # Sanity: the fixture scenario must actually produce the
        # positive-flips-to-negative shape this test claims to cover.
        assert expected_without_friction > 0, "Fixture scenario must start positive."
        assert expected_with_friction < 0, (
            "Fixture scenario must flip negative once friction is applied -- "
            "if this fails, SIM_EXIT_FRICTION_PCT no longer exceeds the "
            "engineered gain and the fixture needs updating, not the assertion."
        )

        with _patched_replay_core(self._SCENARIO["trigger_tick_idx"], _REASON):
            result = autotuner._collect_sim_returns(
                {}, history, [_SYM], "2026-05-10", {_REASON: override_penalty}
            )

        assert result == pytest.approx([expected_with_friction], abs=1e-9), (
            f"Friction exceeding the exit's gain must flow through as a "
            f"NEGATIVE guard_alpha, never clamped at zero. "
            f"expected={[expected_with_friction]}, got={result}"
        )
        assert result[0] < 0, (
            "Negative guard-alpha from friction-exceeds-gain must NOT be "
            f"clamped to zero. Got {result[0]!r}."
        )
