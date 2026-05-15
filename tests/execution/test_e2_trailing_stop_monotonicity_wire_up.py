"""
RED tests — E2: trailing-stop monotonicity ratchet wire-up in the live consumer path.

Context
-------
The math layer (compute_breakeven_update in math_engine.py) already enforces the
Fu & Zhang 2010 trailing-stop monotonicity ratchet when the caller passes
previously_persisted_stop_level. alpha_bot_execution.py:700-707 does NOT pass
that kwarg today. Result: trailing stops can move backwards tick-to-tick — a
real-money exposure live on main right now.

This module tests the LIVE CONSUMER path in alpha_bot_execution.py. It is
distinct from tests/math_engine/test_stop_monotonicity.py which tests the
math layer in isolation.

Five mandatory RED tests (will fail until implementer adds the kwarg at
alpha_bot_execution.py:700-707 and handles the position-open reset):

  1. test_kwarg_passed_to_compute_breakeven_update
     Wire-up pin: spy compute_breakeven_update, assert it receives
     previously_persisted_stop_level= the value stored in
     bot_state[symphony_id]['stop_trigger'] on the prior cycle.

  2. test_trailing_stop_cannot_decrease_tick_to_tick_live_consumer
     Fixture replay: a stop sequence where naive math would lower the stop on
     tick N+1. Assert the live consumer path retains tick N's persisted level.

  3. test_existing_7_monotonicity_scenarios_all_green
     All 7 scenarios in tests/math_engine/test_stop_monotonicity.py must pass
     when replayed through the live consumer path (not just the math layer in
     isolation).

  4. test_new_position_open_resets_persisted_stop
     When bot_state[symphony_id] has no prior stop_trigger key (first cycle of
     a new position), compute_breakeven_update must receive
     previously_persisted_stop_level=None — the new position must NOT inherit
     a stale stop floor from a prior run.

  5. test_breakeven_lock_semantics_preserved
     A position that advances into profit-locked territory: verify that
     breakeven_locked=True semantics hold and the ratchet does not break the
     zero-loss floor or the one-way latch.

Fixture provenance (BC-7 resolution)
--------------------------------------
All named input sequences are defined in
tests/fixtures/math_engine/stop_sequence_*.json.
No bare literals appear in assertions that derive from producer-computed values.
Expected stop levels in assertions are derived by hand in docstrings; where
the assertion checks relational properties (non-decreasing, >= prior) there is
no concrete literal.

Tolerance policy
----------------
pytest.approx with rel=1e-9, abs=1e-12 for float comparisons that pass through
the max() chain (no transcendentals; ~1 ulp realistic noise). The monotonicity
check uses strict `<` (exact comparison) — even an epsilon decrease is a
trailing-stop violation and must never be masked.

Mocking strategy
----------------
Reuses the _patched_main_env context-manager harness from test_cross_call_lifecycle.py.
compute_breakeven_update is NOT mocked (we test it directly — see quant-test-writer rules);
only network and time surfaces are mocked. The spy on compute_breakeven_update uses
unittest.mock.patch and wraps the real function so both the spy assertion AND the
real math run.
"""

from __future__ import annotations

import json
import pathlib
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, call, patch

import pytest

import alpha_bot_execution
import math_engine


# ---------------------------------------------------------------------------
# Tolerance constants
# ---------------------------------------------------------------------------

APPROX_REL = 1e-9
APPROX_ABS = 1e-12


# ---------------------------------------------------------------------------
# Fixture loading helpers
# ---------------------------------------------------------------------------

_FIXTURES_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "math_engine" / "stop_sequence"


def _load_fixture(filename: str) -> dict:
    return json.loads((_FIXTURES_DIR / filename).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Shared test constants (mirror test_cross_call_lifecycle.py naming)
# ---------------------------------------------------------------------------

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
    _FIXED_ET = datetime(2025, 5, 14, 11, 30, 0, tzinfo=_ET)
except Exception:  # pragma: no cover
    _FIXED_ET = datetime(2025, 5, 14, 11, 30, 0, tzinfo=timezone(timedelta(hours=-4)))

_SYMPHONY_ID = "sym-e2-monotonicity-test-001"
_ACTUAL_SYMPHONY_ID = "actual-sym-e2-monotonicity-test-001"
_ACCOUNT_ID = "acct-e2-monotonicity-test-001"
_SYMPHONY_NAME = "E2 Monotonicity Test Symphony"
_TICKER = "SPY"


# ---------------------------------------------------------------------------
# Harness builders (replicate the pattern from test_cross_call_lifecycle.py
# so the harness is self-contained and does not create a cross-test import)
# ---------------------------------------------------------------------------

def _make_symphony_payload(last_percent_change: float) -> dict:
    """Composer-shaped symphony dict. last_percent_change * 100 = current_return."""
    return {
        "id": _SYMPHONY_ID,
        "symphony_id": _ACTUAL_SYMPHONY_ID,
        "name": _SYMPHONY_NAME,
        "last_percent_change": last_percent_change,
        "current_value": 10000.0,
        "holdings": [
            {"ticker": _TICKER, "allocation": 1.0},
        ],
    }


def _make_minimal_history(current_date_str: str) -> dict:
    return {
        current_date_str: {
            "SPY": {
                "c": 500.0,
                "daily_ret": 0.001,
                "high": 501.0,
                "low": 499.0,
                "close": 500.0,
            }
        }
    }


def _make_vwap_payload(last_price: float = 500.0) -> dict:
    return {_TICKER: {"vwap": last_price, "last_price": last_price}}


def _seed_state(
    armed: bool = False,
    hwm: float = 0.0,
    stop_trigger: float | None = None,
    breakeven_locked: bool = False,
    hwm_hold_ticks: int = 0,
    **extras,
) -> dict:
    """Build a bot_state dict shaped like main()'s first-tick write.

    stop_trigger=None means the key is absent (new position — no prior stop).
    stop_trigger=<float> means the key is present with that value (prior cycle
    persisted it).
    """
    sym_state: dict = {
        "high_water_mark": hwm,
        "shadow_hwm": hwm,
        "prev_return": 0.0,
        "armed": armed,
        "tp_armed": False,
        "para_armed": False,
        "triggered": False,
        "mc_history": [],
        "below_stop_count": 0,
        "above_tp_count": 0,
        "vwap_ticks": 0,
        "vwap_bleed_ticks": 0,
        "breakeven_locked": breakeven_locked,
        "hwm_hold_ticks": hwm_hold_ticks,
    }
    if stop_trigger is not None:
        sym_state["stop_trigger"] = stop_trigger
    sym_state.update(extras)

    return {
        "date": _FIXED_ET.strftime("%Y-%m-%d"),
        "last_execution_mode": False,
        _SYMPHONY_ID: sym_state,
    }


@contextmanager
def _patched_main_env(initial_bot_state: dict, pct_change: float, live_price: float = 500.0):
    """Per-tick patch bundle for main(). Returns same bot_state dict reference
    across multiple ticks so state accumulates correctly."""
    with patch.object(alpha_bot_execution, "database") as mock_db, \
         patch.object(alpha_bot_execution, "reporting") as mock_reporting, \
         patch.object(alpha_bot_execution, "fetch_symphony_stats") as mock_fetch_sym, \
         patch.object(alpha_bot_execution, "fetch_alpaca_history") as mock_fetch_hist, \
         patch.object(alpha_bot_execution, "fetch_intraday_vwaps") as mock_fetch_vwap, \
         patch.object(alpha_bot_execution, "get_current_et", return_value=_FIXED_ET), \
         patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", [_ACCOUNT_ID]), \
         patch.object(alpha_bot_execution, "COMPOSER_KEY_ID", "test-composer-key"), \
         patch.object(alpha_bot_execution, "ALPACA_KEY", "test-alpaca-key"), \
         patch.object(alpha_bot_execution, "LIVE_EXECUTION", False), \
         patch.object(alpha_bot_execution.time, "sleep"), \
         patch.object(alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py"]):

        mock_db.acquire_lock.return_value = True
        mock_db.load_state.return_value = initial_bot_state
        mock_db.load_chart_history.return_value = {
            "date": _FIXED_ET.strftime("%Y-%m-%d"), "symphonies": {}
        }
        mock_db.get_symphony_strategy.return_value = {"params": {}, "locked_vars": {}}
        mock_db.normalize_name.side_effect = lambda n: n.strip().lower()
        mock_db.wipe_transient_state.side_effect = lambda s: s

        mock_fetch_sym.return_value = [_make_symphony_payload(last_percent_change=pct_change)]
        mock_fetch_hist.return_value = _make_minimal_history(_FIXED_ET.strftime("%Y-%m-%d"))
        mock_fetch_vwap.return_value = _make_vwap_payload(live_price)

        yield {
            "db": mock_db,
            "reporting": mock_reporting,
            "fetch_symphony_stats": mock_fetch_sym,
            "fetch_alpaca_history": mock_fetch_hist,
            "fetch_intraday_vwaps": mock_fetch_vwap,
        }


# ---------------------------------------------------------------------------
# Helper: suppress exit trigger so main() does not fire an execution queue
# on any tick (we are testing stop bookkeeping, not trigger firing).
# ---------------------------------------------------------------------------

@contextmanager
def _suppress_exits():
    """Suppress VWAP and exit confirmation so the test suite controls
    whether a trigger fires. compute_breakeven_update is NOT suppressed —
    we need the real math to run."""
    with patch.object(
        alpha_bot_execution.math_engine,
        "compute_exit_confirmation",
        return_value=(0, False),
    ), patch.object(
        alpha_bot_execution.math_engine,
        "compute_vwap_breakdown_update",
        return_value=(0, 0, False, False),
    ):
        yield


# ===========================================================================
# TEST 1 — Wire-up pin: compute_breakeven_update receives the kwarg.
#
# Fixture: stop_sequence_kwarg_wire_up.json
# Scenario: bot_state[symphony_id]['stop_trigger'] = 1.5 (from prior cycle).
# Expected: compute_breakeven_update is called with
#           previously_persisted_stop_level=1.5.
#
# RED: will fail today because alpha_bot_execution.py:700-707 does not pass
# previously_persisted_stop_level at all, so it defaults to None regardless
# of what's in bot_state.
# ===========================================================================


def test_kwarg_passed_to_compute_breakeven_update() -> None:
    """
    Wire-up pin (RED test): the live consumer path passes
    previously_persisted_stop_level into compute_breakeven_update.

    Scenario (fixture: stop_sequence_kwarg_wire_up.json):
      bot_state[symphony_id]['stop_trigger'] = 1.5 (persisted ratchet floor
      from the prior cycle). On the next call to main(), compute_breakeven_update
      must receive previously_persisted_stop_level=1.5 so the math layer can
      enforce the trailing-stop ratchet.

    Spy strategy: wrap compute_breakeven_update with a side-effect that
    records keyword arguments, then call the real function. This lets us
    assert the kwarg AND still exercise the math (mock only network/time).

    Fails today because alpha_bot_execution.py:700-707 calls
    compute_breakeven_update without previously_persisted_stop_level —
    the kwarg defaults to None regardless of bot_state content.
    """
    fixture = _load_fixture("stop_sequence_kwarg_wire_up.json")
    persisted_stop = fixture["bot_state_stop_trigger"]
    expected_kwarg = fixture["expected_kwarg_value"]

    bot_state = _seed_state(
        armed=True,
        hwm=2.0,
        stop_trigger=persisted_stop,
        breakeven_locked=True,
        hwm_hold_ticks=6,
    )

    captured_calls: list[dict] = []
    real_compute_breakeven_update = math_engine.compute_breakeven_update

    def _spy_compute_breakeven_update(*args, **kwargs):
        captured_calls.append(kwargs)
        return real_compute_breakeven_update(*args, **kwargs)

    # current_return = 0.02 * 100 = 2.0%; armed=True; breakeven_locked=True so
    # compute_breakeven_update fires on the live path.
    pct_change = 0.02

    with _patched_main_env(bot_state, pct_change) as env:
        with patch.object(
            alpha_bot_execution.math_engine,
            "compute_breakeven_update",
            side_effect=_spy_compute_breakeven_update,
        ), _suppress_exits():
            alpha_bot_execution.main()

    assert captured_calls, (
        "compute_breakeven_update was never called. The live consumer path did "
        "not reach the compute_breakeven_update call site. Verify that armed=True "
        "and the action phase ran."
    )

    # Assert the kwarg was passed (not merely that the call happened).
    call_kwargs = captured_calls[0]
    assert "previously_persisted_stop_level" in call_kwargs, (
        f"WIRE-UP MISSING: compute_breakeven_update was called but "
        f"previously_persisted_stop_level was NOT passed as a keyword argument. "
        f"Call kwargs received: {call_kwargs}. "
        f"alpha_bot_execution.py:700-707 must add "
        f"previously_persisted_stop_level=bot_state[symphony_id].get('stop_trigger') "
        f"to the compute_breakeven_update call."
    )

    assert call_kwargs["previously_persisted_stop_level"] == pytest.approx(
        expected_kwarg, rel=APPROX_REL, abs=APPROX_ABS
    ), (
        f"WIRE-UP VALUE MISMATCH: previously_persisted_stop_level passed was "
        f"{call_kwargs['previously_persisted_stop_level']!r}, expected "
        f"{expected_kwarg!r} (from bot_state[symphony_id]['stop_trigger']). "
        f"The live consumer must thread the persisted stop level — not a "
        f"constant — into the call."
    )


# ===========================================================================
# TEST 2 — Live consumer stop cannot decrease tick-to-tick.
#
# Fixture: stop_sequence_retrace_monotonicity_violation.json
# Scenario: 7-tick replay — 5 pre-lock ticks, stop rises to 2.0 on tick 6,
# then base retraces to 1.0 on tick 7. Assert persisted stop_trigger never
# drops below 2.0 on tick 7.
#
# RED: without the kwarg, tick-7 naive math returns max(1.0,0.0)=1.0 < 2.0.
# The bot_state['stop_trigger'] would be written as 1.0 — a backward step.
# ===========================================================================


def test_trailing_stop_cannot_decrease_tick_to_tick_live_consumer() -> None:
    """
    Live-consumer monotonicity regression (RED test): the persisted
    bot_state[symphony_id]['stop_trigger'] must be non-decreasing across
    consecutive main() invocations for a single position's lifetime.

    Scenario (fixture: stop_sequence_retrace_monotonicity_violation.json):
      7-tick sequence. symphony_vol=1.0 throughout; dynamic_activation=1.0;
      qualifying_threshold=0.8. 5 pre-lock ticks with current_return=1.5
      (qualifying) and base_stop=-1.0. Lock fires on tick 5.
      Tick 6: base_stop=2.0 -> stop=max(2.0,0.0)=2.0.
      Tick 7: base_stop=1.0 -> naive max(1.0,0.0)=1.0 < 2.0 (violation).
      With the ratchet kwarg: max(2.0, max(1.0,0.0)) = 2.0 (correct).

    This test drives main() through all 7 ticks sharing the same bot_state.
    After each tick it reads bot_state[symphony_id]['stop_trigger'] from the
    save_state call and asserts it never dropped below the previous tick's value.

    Fails today because the missing kwarg means tick-7 lowers stop from 2.0
    to 1.0.
    """
    fixture = _load_fixture("stop_sequence_retrace_monotonicity_violation.json")
    ticks = fixture["ticks"]
    ratchet_critical_tick = fixture["ratchet_critical_tick"]    # 7 (1-indexed)
    ratchet_expected_floor = fixture["ratchet_expected_floor"]  # 2.0

    # symphony_vol=1.0 -> compute_active_trailing_stop uses vol=1.0.
    # We need to override so that math_engine.calculate_20d_vol returns 1.0
    # (matching the fixture's assumed vol) — otherwise the harness's sparse
    # history would produce an unpredictable vol.
    bot_state = _seed_state(armed=False, hwm=0.0)

    persisted_stops: list[float] = []
    current_date_str = _FIXED_ET.strftime("%Y-%m-%d")

    # symphony_vol is at the top level of the fixture (constant across all ticks).
    fixture_vol = fixture["symphony_vol"]

    for tick_info in ticks:
        tick_num = tick_info["tick"]
        pct_change = tick_info["current_return"] / 100.0  # main() multiplies by 100

        with _patched_main_env(bot_state, pct_change) as env, \
             patch.object(
                 alpha_bot_execution.math_engine,
                 "calculate_20d_vol",
                 return_value=fixture_vol,
             ), \
             patch.object(
                 alpha_bot_execution.math_engine,
                 "compute_active_trailing_stop",
                 # base_stop_level = hwm - active_stop; we set active_stop=HWM - base
                 # so base_stop_level matches the fixture. Returns the stop distance.
                 return_value=max(0.0, bot_state[_SYMPHONY_ID]["high_water_mark"] - tick_info["base_stop_level"]),
             ), \
             _suppress_exits():
            alpha_bot_execution.main()

        save_calls = env["db"].save_state.call_args_list
        assert save_calls, f"tick {tick_num}: save_state not called — pipeline aborted"
        final_state = save_calls[-1].args[0]
        written_stop = final_state[_SYMPHONY_ID].get("stop_trigger")

        assert written_stop is not None, (
            f"tick {tick_num}: bot_state[symphony_id]['stop_trigger'] was not "
            f"written by main(). The live consumer must write stop_trigger_level "
            f"at alpha_bot_execution.py:783."
        )
        persisted_stops.append(written_stop)

    # Non-decreasing assertion: each persisted stop >= previous.
    violations: list[tuple[int, float, float]] = []
    for i in range(1, len(persisted_stops)):
        if persisted_stops[i] < persisted_stops[i - 1]:
            violations.append((i + 1, persisted_stops[i - 1], persisted_stops[i]))

    assert not violations, (
        f"LIVE-CONSUMER TRAILING-STOP MONOTONICITY VIOLATED. "
        f"Persisted stop_trigger sequence across {len(ticks)} ticks: "
        f"{persisted_stops}. "
        f"Tick(s) where stop DROPPED (tick_num, prev, new): {violations}. "
        f"Fixture: stop_sequence_retrace_monotonicity_violation.json. "
        f"Fix: alpha_bot_execution.py:700-707 must pass "
        f"previously_persisted_stop_level=bot_state[symphony_id].get('stop_trigger')."
    )

    # Spot-check the critical tick (tick 7) specifically.
    # >= comparison against a derived constant; no approx needed on the floor
    # itself because ratchet_expected_floor (2.0) is a fixture-defined sentinel,
    # and any drop below it is a definitive violation. APPROX_ABS tolerance covers
    # float representation noise at the boundary.
    stop_at_critical_tick = persisted_stops[ratchet_critical_tick - 1]
    assert stop_at_critical_tick >= ratchet_expected_floor - APPROX_ABS, (
        f"RATCHET FLOOR VIOLATED at tick {ratchet_critical_tick}: "
        f"stop={stop_at_critical_tick!r} < ratchet_expected_floor="
        f"{ratchet_expected_floor!r}. "
        f"The stop advanced to {ratchet_expected_floor} on tick 6 and "
        f"must not retreat on tick 7 when base retraces."
    )


# ===========================================================================
# TEST 3 — Existing 7 monotonicity scenarios all GREEN through live consumer.
#
# The 7 scenarios in test_stop_monotonicity.py test the math layer directly.
# This test re-runs the same conceptual scenarios through the live consumer
# path (main()) to verify the wire-up does not break the math layer's
# behavior.
#
# Strategy: use the math-layer helper _simulate_position_lifetime logic
# adapted to drive main() for each tick. This confirms the live consumer
# path + kwarg thread produces the same non-decreasing sequence as the math
# layer's direct test.
#
# RED: without the kwarg wire-up, 4 of the 7 scenarios produce monotonicity
# violations in the live consumer path (same root cause as test 2).
# ===========================================================================


_POSTLOCK_SCENARIOS = [
    # (scenario_id, postlock_base_sequence) — 5 pre-lock ticks always prepended
    ("strictly_increasing",          [0.5, 1.0, 1.5, 2.0, 2.5]),
    ("flat_then_increasing",         [1.0, 1.0, 1.0, 2.0, 2.0]),
    ("retrace_above_zero",           [2.0, 1.5, 1.0, 0.5, 0.0]),   # VIOLATES without kwarg
    ("retrace_dip_recover",          [1.0, 2.0, 1.5, 2.5, 3.0]),   # VIOLATES without kwarg
    ("retrace_below_zero",           [2.0, 1.0, -1.0, -2.0, -3.0]),# VIOLATES without kwarg
    ("oscillating_above_zero",       [1.0, 2.0, 1.0, 2.0, 1.0]),   # VIOLATES without kwarg
    ("always_negative_floored_zero", [-1.0, -2.0, -3.0, -4.0, -5.0]),
]


@pytest.mark.parametrize(
    "scenario_id,postlock_bases",
    _POSTLOCK_SCENARIOS,
    ids=[s[0] for s in _POSTLOCK_SCENARIOS],
)
def test_existing_7_monotonicity_scenarios_all_green(
    scenario_id: str,
    postlock_bases: list[float],
) -> None:
    """
    Existing math-layer monotonicity scenarios verified through the live
    consumer path (RED test for 4 of 7 scenarios).

    The 7 scenarios mirror test_stop_monotonicity.py::
    test_lifetime_monotonicity_across_postlock_base_shapes. The math layer
    passes all 7 directly. This test confirms the live consumer path ALSO
    passes all 7 — requiring the kwarg to be correctly wired.

    Pre-lock: 5 ticks with current_return=1.5, base_stop=-1.0, vol=1.0.
    Lock fires on tick 5 (qualifying_threshold=0.8 < 1.5, 5 consecutive hits).
    Post-lock: 5 ticks varying base_stop_level per scenario.

    Scenarios that fail today (ACTUAL RED — kwarg missing):
      retrace_above_zero, retrace_dip_recover, retrace_below_zero,
      oscillating_above_zero.

    Scenarios that pass today (GREEN already in math layer — stays green):
      strictly_increasing, flat_then_increasing, always_negative_floored_zero.
    """
    symphony_vol = 1.0

    pre_lock_ticks = [
        {"current_return": 1.5, "base_stop_level": -1.0, "is_triggered": False}
        for _ in range(5)
    ]
    post_lock_ticks = [
        {"current_return": 2.0, "base_stop_level": b, "is_triggered": False}
        for b in postlock_bases
    ]
    all_ticks = pre_lock_ticks + post_lock_ticks

    bot_state = _seed_state(armed=False, hwm=0.0)
    persisted_stops: list[float] = []

    for tick_idx, tick_info in enumerate(all_ticks):
        pct_change = tick_info["current_return"] / 100.0
        hwm = bot_state[_SYMPHONY_ID]["high_water_mark"]
        active_stop_distance = max(0.0, hwm - tick_info["base_stop_level"])

        with _patched_main_env(bot_state, pct_change) as env, \
             patch.object(
                 alpha_bot_execution.math_engine,
                 "calculate_20d_vol",
                 return_value=symphony_vol,
             ), \
             patch.object(
                 alpha_bot_execution.math_engine,
                 "compute_active_trailing_stop",
                 return_value=active_stop_distance,
             ), \
             _suppress_exits():
            alpha_bot_execution.main()

        save_calls = env["db"].save_state.call_args_list
        if not save_calls:
            continue
        final_state = save_calls[-1].args[0]
        written_stop = final_state[_SYMPHONY_ID].get("stop_trigger")
        if written_stop is not None:
            persisted_stops.append(written_stop)

    assert len(persisted_stops) == len(all_ticks), (
        f"[{scenario_id}] Expected {len(all_ticks)} persisted stops, got "
        f"{len(persisted_stops)}. Some main() calls aborted early."
    )

    violations: list[tuple[int, float, float]] = []
    for i in range(1, len(persisted_stops)):
        if persisted_stops[i] < persisted_stops[i - 1]:
            violations.append((i + 1, persisted_stops[i - 1], persisted_stops[i]))

    assert not violations, (
        f"[{scenario_id}] LIVE-CONSUMER MONOTONICITY VIOLATED. "
        f"Persisted stop sequence: {persisted_stops}. "
        f"Drop(s) at (tick, prev, new): {violations}. "
        f"Same scenario passes at the math-layer level (test_stop_monotonicity.py) "
        f"but the live consumer path drops the ratchet because "
        f"previously_persisted_stop_level is not threaded through from "
        f"bot_state[symphony_id]['stop_trigger']."
    )


# ===========================================================================
# TEST 4 — New position open resets the persisted stop floor to None.
#
# Fixture: stop_sequence_new_position_open_reset.json
# Scenario: bot_state[symphony_id] has no 'stop_trigger' key (first cycle of
# a new position). compute_breakeven_update must receive
# previously_persisted_stop_level=None.
#
# Subsequent cycles must thread the cycle-1 output forward (not None again).
#
# RED: without the kwarg being passed at all, this test would pass vacuously
# because the kwarg is never passed. The test is written defensively: it
# asserts the kwarg IS present and is None on cycle 1, and is the cycle-1
# output on cycle 2.
# ===========================================================================


def test_new_position_open_resets_persisted_stop() -> None:
    """
    New-position-open position-reset semantics (RED test).

    When bot_state[symphony_id] has no 'stop_trigger' key (first cycle for a
    new position), compute_breakeven_update must be called with
    previously_persisted_stop_level=None. This is the position-open sentinel:
    no prior ratchet floor exists; the math layer must not clamp against any
    stale floor.

    On cycle 2 (after stop_trigger is written at alpha_bot_execution.py:783),
    previously_persisted_stop_level must equal the cycle-1 output — not None.

    Fixture: stop_sequence_new_position_open_reset.json.

    This test drives two consecutive main() ticks:
      Cycle 1: bot_state has no stop_trigger key -> kwarg must be None.
      Cycle 2: bot_state has stop_trigger from cycle 1 -> kwarg must equal it.
    """
    # Cycle 1: no stop_trigger key in bot_state (new position).
    bot_state = _seed_state(armed=True, hwm=1.0, stop_trigger=None)
    # stop_trigger=None means the key is ABSENT from _seed_state (see impl above).
    assert "stop_trigger" not in bot_state[_SYMPHONY_ID], (
        "Test setup error: _seed_state should not include stop_trigger key when None."
    )

    captured_calls: list[dict] = []
    real_fn = math_engine.compute_breakeven_update

    def _spy(*args, **kwargs):
        captured_calls.append(dict(kwargs))
        return real_fn(*args, **kwargs)

    pct_change = 0.015  # current_return=1.5%

    # --- Cycle 1 ---
    with _patched_main_env(bot_state, pct_change) as env, \
         patch.object(
             alpha_bot_execution.math_engine,
             "compute_breakeven_update",
             side_effect=_spy,
         ), _suppress_exits():
        alpha_bot_execution.main()

    assert captured_calls, "compute_breakeven_update not called on cycle 1"
    cycle_1_kwargs = captured_calls[0]

    assert "previously_persisted_stop_level" in cycle_1_kwargs, (
        "WIRE-UP MISSING on cycle 1: previously_persisted_stop_level not passed. "
        "alpha_bot_execution.py:700-707 must pass "
        "previously_persisted_stop_level=bot_state[symphony_id].get('stop_trigger'). "
        "On the first cycle, .get('stop_trigger') returns None (key absent)."
    )
    assert cycle_1_kwargs["previously_persisted_stop_level"] is None, (
        f"POSITION-OPEN RESET FAILED: cycle 1 (new position, no prior stop_trigger) "
        f"must pass previously_persisted_stop_level=None. "
        f"Got: {cycle_1_kwargs['previously_persisted_stop_level']!r}. "
        f"The new position must NOT inherit a stale stop floor."
    )

    # Read cycle-1 output stop_trigger from save_state.
    save_calls = env["db"].save_state.call_args_list
    assert save_calls, "save_state not called on cycle 1"
    cycle_1_written_stop = save_calls[-1].args[0][_SYMPHONY_ID].get("stop_trigger")
    assert cycle_1_written_stop is not None, (
        "stop_trigger not written after cycle 1 — alpha_bot_execution.py:783 missing."
    )

    # --- Cycle 2: bot_state now has stop_trigger from cycle 1 ---
    captured_calls.clear()

    with _patched_main_env(bot_state, pct_change) as env2, \
         patch.object(
             alpha_bot_execution.math_engine,
             "compute_breakeven_update",
             side_effect=_spy,
         ), _suppress_exits():
        alpha_bot_execution.main()

    assert captured_calls, "compute_breakeven_update not called on cycle 2"
    cycle_2_kwargs = captured_calls[0]

    assert "previously_persisted_stop_level" in cycle_2_kwargs, (
        "WIRE-UP MISSING on cycle 2: previously_persisted_stop_level not passed."
    )
    assert cycle_2_kwargs["previously_persisted_stop_level"] == pytest.approx(
        cycle_1_written_stop, rel=APPROX_REL, abs=APPROX_ABS
    ), (
        f"RATCHET THREADING FAILED: cycle 2 previously_persisted_stop_level="
        f"{cycle_2_kwargs['previously_persisted_stop_level']!r} != "
        f"cycle_1_written_stop={cycle_1_written_stop!r}. "
        f"The cycle-1 output from bot_state[symphony_id]['stop_trigger'] "
        f"must be threaded into cycle 2's call."
    )


# ===========================================================================
# TEST 5 — Breakeven-lock semantics preserved under ratchet.
#
# Fixture: stop_sequence_breakeven_lock_semantics.json
# Scenario: position advances into profit-locked territory (breakeven_locked
# latches True). Then current_return dips below the stop level. Verify:
#   - breakeven_locked remains True (one-way latch).
#   - stop_trigger_level stays >= zero (zero-loss floor).
#   - stop_trigger_level stays >= tick-5's locked level (ratchet).
#
# RED: the ratchet assertion fails today because tick-7 (drawdown tick) would
# recompute max(-0.5, 0.0)=0.0, which is below the tick-5 ratchet floor of
# 0.5. Without the kwarg, the ratchet does not hold.
# ===========================================================================


def test_breakeven_lock_semantics_preserved() -> None:
    """
    Breakeven-lock semantic preservation under the ratchet kwarg (RED test).

    Scenario (fixture: stop_sequence_breakeven_lock_semantics.json):
      6-tick sequence. symphony_vol=1.0; dynamic_activation=1.0;
      qualifying_threshold=0.8. Ticks 1-5: current_return=1.5, base=-1.0.
      Tick 5: lock fires; base=0.5 -> stop=max(0.5,0.0)=0.5. Ratchet floor=0.5.
      Tick 6: current_return=0.3, base=-0.5 -> locked: max(-0.5,0.0)=0.0;
              ratchet (if kwarg wired): max(0.5, 0.0)=0.5.
              Without kwarg: stop=0.0 (drops below 0.5 — ratchet violated).

    Three assertions:
      A) breakeven_locked=True persists from tick 5 onward (one-way latch).
      B) stop_trigger_level >= 0.0 at all times (zero-loss floor).
      C) stop_trigger_level on tick 6 >= stop_trigger_level on tick 5 (ratchet).

    The zero-loss floor (assertion B) is not broken by the current code
    (already honored). The ratchet (assertion C) IS broken — that is the RED.
    """
    fixture = _load_fixture("stop_sequence_breakeven_lock_semantics.json")
    ticks = fixture["ticks"]
    expected_ratchet_floor = fixture["ratchet_floor_at_tick_6"]  # 0.5
    symphony_vol = fixture["symphony_vol"]                        # 1.0

    bot_state = _seed_state(armed=False, hwm=0.0)

    persisted_stops: list[float] = []
    persisted_locks: list[bool] = []

    for tick_info in ticks:
        tick_num = tick_info["tick"]
        pct_change = tick_info["current_return"] / 100.0
        hwm = bot_state[_SYMPHONY_ID]["high_water_mark"]
        active_stop_distance = max(0.0, hwm - tick_info["base_stop_level"])

        with _patched_main_env(bot_state, pct_change) as env, \
             patch.object(
                 alpha_bot_execution.math_engine,
                 "calculate_20d_vol",
                 return_value=symphony_vol,
             ), \
             patch.object(
                 alpha_bot_execution.math_engine,
                 "compute_active_trailing_stop",
                 return_value=active_stop_distance,
             ), \
             _suppress_exits():
            alpha_bot_execution.main()

        save_calls = env["db"].save_state.call_args_list
        assert save_calls, f"tick {tick_num}: save_state not called"
        final_state = save_calls[-1].args[0][_SYMPHONY_ID]
        written_stop = final_state.get("stop_trigger")
        written_lock = final_state.get("breakeven_locked", False)

        if written_stop is not None:
            persisted_stops.append(written_stop)
            persisted_locks.append(bool(written_lock))

    assert len(persisted_stops) == len(ticks), (
        f"Expected {len(ticks)} persisted stops, got {len(persisted_stops)}."
    )

    # Assertion A: breakeven_locked is True from the lock-fire tick (tick 5, index 4) onward.
    for i in range(4, len(persisted_locks)):
        assert persisted_locks[i] is True, (
            f"ONE-WAY LATCH VIOLATED at tick {i + 1}: breakeven_locked="
            f"{persisted_locks[i]!r}, expected True. The lock latched on tick 5 "
            f"and must hold for the lifetime of the position. "
            f"Sequence: {persisted_locks}."
        )

    # Assertion B: zero-loss floor — all stops >= 0.0 from tick 5 (index 4) onward.
    for i in range(4, len(persisted_stops)):
        assert persisted_stops[i] >= 0.0, (
            f"ZERO-LOSS FLOOR VIOLATED at tick {i + 1}: stop_trigger_level="
            f"{persisted_stops[i]!r} < 0.0. Breakeven-locked stop must be "
            f">= 0.0 (max(base, 0.0) semantics). Sequence: {persisted_stops}."
        )

    # Assertion C: ratchet — stop_trigger on tick 6 (index 5) >= tick 5 (index 4).
    stop_tick_5 = persisted_stops[4]
    stop_tick_6 = persisted_stops[5]

    # pytest.approx does not support >= directly; compare against the floor
    # with a tiny tolerance (1e-12) so float representation noise doesn't mask
    # a genuine drop. Any drop larger than 1e-12 below the prior tick is a
    # trailing-stop monotonicity violation.
    assert stop_tick_6 >= stop_tick_5 - APPROX_ABS, (
        f"RATCHET VIOLATED at tick 6: stop dropped from {stop_tick_5!r} (tick 5) "
        f"to {stop_tick_6!r} (tick 6). Fixture: stop_sequence_breakeven_lock_semantics.json. "
        f"Drawdown tick must retain the ratchet floor of {expected_ratchet_floor}. "
        f"Fix: previously_persisted_stop_level kwarg must be wired into "
        f"compute_breakeven_update so the math layer clamps the drawdown output "
        f"against the prior tick's locked-in floor."
    )

    assert stop_tick_6 >= expected_ratchet_floor - APPROX_ABS, (
        f"RATCHET FLOOR MISMATCH: stop_tick_6={stop_tick_6!r} < "
        f"expected_ratchet_floor={expected_ratchet_floor!r} (from fixture). "
        f"The locked-in stop from tick 5 must not retreat on a drawdown tick."
    )
