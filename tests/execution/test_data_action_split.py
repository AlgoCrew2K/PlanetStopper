"""
RED-phase tests for the data-vs-action phase split in alpha_bot_execution.main().

Background
----------
The operator set EXECUTION_START_TIME=10:30 to gate EXIT ACTIONS (trailing-stop
evaluation, MC gating, trade placement) away from opening-volatility noise. This
is CORRECT and must be preserved.

But the current engine conflates data-gathering with action-taking: both are gated
at EXECUTION_START_TIME. This creates two bugs:

  1. Dashboard shows stale accounts_map={} and yesterday's current_return from
     09:30 to 10:30 every day.

  2. When the action phase fires at 10:30, the math engine has cold state: HWM
     not tracked from 09:30, no volatility data points accumulated, no warm
     current_return history. Exit decisions at 10:30 are based on stub data.

The fix restructures main() into two phases per cycle:

  DATA PHASE (runs every cycle from 09:30 ET onward, regardless of
  EXECUTION_START_TIME):
    - Composer symphony-stats-meta fetch
    - Per-symphony current_return calculation (last_percent_change * 100)
    - HWM update (current_return > high_water_mark → advance HWM)
    - Volatility data-point accumulation (symphony_vol appended to history)
    - accounts_map build / bot_state[sym_id] population
    - State write to bot_state + state DB

  ACTION PHASE (gated by EXECUTION_START_TIME — only fires at/after 10:30):
    - Monte Carlo gating (run_monte_carlo)
    - Exit-decision generation (trailing stop, TP, VWAP checks)
    - Execution queue build and dispatch
    - Alpaca trade placement (already gated by LIVE_EXECUTION=False)

RED CRITERIA
------------
These tests FAIL on the current code because:
  - Pre-gate cycles (09:45) do not write current_return to bot_state per symphony
  - Pre-gate cycles do not update HWM
  - Pre-gate cycles do not accumulate volatility data
  - There is no separation between data and action: the entire intraday loop
    (including run_monte_carlo and exit decisions) is gated as one block at 10:30

They will turn GREEN when the implementer restructures main() to run the data
phase every cycle from 09:30 and gate only the action phase at EXECUTION_START_TIME.

OPERATOR INVARIANT — DO NOT CHANGE
-----------------------------------
EXECUTION_START_TIME=10:30 must remain the gate for the action phase.
Tests assert that NO exit decisions fire before that gate.

Mocking philosophy (mirrors test_main_pipeline.py)
---------------------------------------------------
- No live Composer or Alpaca calls.
- fetch_symphony_stats patched to return fixture-derived data.
- database functions patched to inject/capture bot_state.
- get_current_et patched per scenario.
- math_engine.run_monte_carlo patched — must NOT be called pre-gate;
  tests assert call_count == 0 for pre-gate cycles.
- math_engine.calculate_20d_vol patched — called in data phase for vol
  accumulation; the result is stored in bot_state[sym_id]["symphony_vol"].
- time.sleep patched to no-op.
- NEVER mock math_engine functions that the data phase itself uses
  (e.g., compute_vwap_signals) unless they require network I/O.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

import alpha_bot_execution

# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")

    def _et(hour: int, minute: int, weekday_date: str = "2025-05-14") -> datetime:
        """Return an ET datetime for a given weekday (Wed 2025-05-14 by default)."""
        y, mo, d = map(int, weekday_date.split("-"))
        return datetime(y, mo, d, hour, minute, 0, tzinfo=_ET)

except Exception:  # pragma: no cover
    def _et(hour: int, minute: int, weekday_date: str = "2025-05-14") -> datetime:
        y, mo, d = map(int, weekday_date.split("-"))
        return datetime(y, mo, d, hour, minute, 0,
                        tzinfo=timezone(timedelta(hours=-4)))


# Fixed scenario times — all are regular weekday (Wed 2025-05-14)
_PRE_GATE_09_45 = _et(9, 45)     # 09:45 ET — after market open, before EXECUTION_START_TIME
_PRE_GATE_10_15 = _et(10, 15)    # 10:15 ET — still before EXECUTION_START_TIME
_POST_GATE_10_31 = _et(10, 31)   # 10:31 ET — just past EXECUTION_START_TIME
_POST_GATE_10_45 = _et(10, 45)   # 10:45 ET — solidly in action phase

# Canonical date string matching the above times
_DATE_STR = "2025-05-14"


# ---------------------------------------------------------------------------
# Test fixture / symphony data
# ---------------------------------------------------------------------------

_ACCOUNT_ID = "acct-datasplit-test-001"
_SYM_ID = "sym-datasplit-test-001"
_ACTUAL_SYM_ID = "actual-sym-datasplit-test-001"
_SYM_NAME = "DataSplit Test Symphony"
_TICKER = "SPY"

# last_percent_change=0.015 → current_return = 1.5%
_COMPOSER_RETURN_FRAC = 0.015
_EXPECTED_CURRENT_RETURN = _COMPOSER_RETURN_FRAC * 100.0  # 1.5

# A second return higher than the first — drives HWM advance in cycle 2
_COMPOSER_RETURN_FRAC_HIGH = 0.025
_EXPECTED_CURRENT_RETURN_HIGH = _COMPOSER_RETURN_FRAC_HIGH * 100.0  # 2.5


def _make_sym_payload(last_percent_change: float = _COMPOSER_RETURN_FRAC) -> dict:
    """Composer-shaped symphony dict matching what fetch_symphony_stats returns."""
    return {
        "id": _SYM_ID,
        "symphony_id": _ACTUAL_SYM_ID,
        "name": _SYM_NAME,
        "last_percent_change": last_percent_change,
        "current_value": 10_000.0,
        "simple_return": 0.12345,
        "net_deposits": 9_500.0,
        "time_weighted_return": 0.13000,
        "max_drawdown": 0.08000,
        "holdings": [{"ticker": _TICKER, "allocation": 1.0}],
    }


def _make_minimal_history(date_str: str) -> dict:
    return {
        date_str: {
            _TICKER: {
                "c": 500.0, "daily_ret": 0.001,
                "high": 501.0, "low": 499.0, "close": 500.0,
            }
        }
    }


def _seed_state(
    hwm: float = 0.0,
    current_return: float = 0.0,
    symphony_vol: float | None = None,
    mc_history: list | None = None,
    **extras,
) -> dict:
    """Build a minimal bot_state with a pre-seeded symphony entry."""
    sym_entry: dict = {
        "high_water_mark": hwm,
        "shadow_hwm": hwm,
        "prev_return": current_return,
        "armed": False,
        "tp_armed": False,
        "para_armed": False,
        "triggered": False,
        "mc_history": mc_history if mc_history is not None else [],
        "below_stop_count": 0,
        "above_tp_count": 0,
        "vwap_ticks": 0,
        "vwap_bleed_ticks": 0,
        "breakeven_locked": False,
        "hwm_hold_ticks": 0,
        "current_return": current_return,
        "name": _SYM_NAME,
        "account": _ACCOUNT_ID,
    }
    if symphony_vol is not None:
        sym_entry["symphony_vol"] = symphony_vol
    sym_entry.update(extras)

    return {
        "date": _DATE_STR,
        "last_execution_mode": False,
        _SYM_ID: sym_entry,
    }


# ---------------------------------------------------------------------------
# Core cycle runner
#
# Runs one call to alpha_bot_execution.main() with all network/DB/clock
# patched. Returns the final bot_state captured from the last save_state call,
# plus a dict of the mock objects so callers can assert call counts.
# ---------------------------------------------------------------------------

def _run_one_cycle(
    current_et: datetime,
    initial_bot_state: dict,
    sym_payload: dict | None = None,
    execution_start_time: str = "10:30",
) -> tuple[dict, dict]:
    """
    Run one cycle of alpha_bot_execution.main().

    Returns:
      (final_bot_state, mocks)

    final_bot_state: the last save_state argument (or initial_bot_state if
    save_state was never called).

    mocks: dict with keys:
      "mock_db"          — the patched database module
      "mock_math"        — the patched math_engine module
      "mock_reporting"   — the patched reporting module
    """
    if sym_payload is None:
        sym_payload = _make_sym_payload()

    date_str = current_et.strftime("%Y-%m-%d")
    captured_states: list[dict] = []

    def capture_save_state(state: dict) -> None:
        captured_states.append(copy.deepcopy(state))

    with patch.object(alpha_bot_execution, "database") as mock_db, \
         patch.object(alpha_bot_execution, "reporting") as mock_reporting, \
         patch.object(alpha_bot_execution, "fetch_symphony_stats",
                      return_value=[sym_payload]), \
         patch.object(alpha_bot_execution, "fetch_alpaca_history",
                      return_value=_make_minimal_history(date_str)), \
         patch.object(alpha_bot_execution, "fetch_intraday_vwaps",
                      return_value={_TICKER: {"vwap": 500.0, "last_price": 500.0}}), \
         patch.object(alpha_bot_execution, "get_current_et",
                      return_value=current_et), \
         patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", [_ACCOUNT_ID]), \
         patch.object(alpha_bot_execution, "COMPOSER_KEY_ID", "test-key-id"), \
         patch.object(alpha_bot_execution, "ALPACA_KEY", "test-alpaca-key"), \
         patch.object(alpha_bot_execution, "LIVE_EXECUTION", False), \
         patch.object(alpha_bot_execution, "EXECUTION_START_TIME", execution_start_time), \
         patch.object(alpha_bot_execution.time, "sleep"), \
         patch.object(alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py"]), \
         patch.object(alpha_bot_execution, "autotuner") as _mock_autotuner, \
         patch.object(alpha_bot_execution, "math_engine") as mock_math:

        # DB stubs
        mock_db.acquire_lock.return_value = True
        mock_db.load_state.return_value = copy.deepcopy(initial_bot_state)
        mock_db.load_chart_history.return_value = {"date": date_str, "symphonies": {}}
        mock_db.get_symphony_strategy.return_value = {"params": {}, "locked_vars": {}}
        mock_db.normalize_name.side_effect = lambda n: n.strip().lower()
        mock_db.wipe_transient_state.side_effect = lambda s: s
        mock_db.save_state.side_effect = capture_save_state
        _mock_autotuner.run_autotuner.return_value = None

        # Math stubs — safe defaults that won't trigger exits
        # run_monte_carlo returns (history_list, prob_float) in some callers;
        # the current engine uses the float directly as prob_beating.
        # Use a high probability so no exit arms under normal conditions.
        mock_math.run_monte_carlo.return_value = 80.0  # 80% — well above trigger threshold
        mock_math.calculate_20d_vol.return_value = 0.15
        mock_math.compute_vwap_signals.return_value = (0.0, 0.0)
        mock_math.compute_para_arm_decision.return_value = (0.0, False)
        mock_math.compute_time_squeeze_decay.return_value = (1.0, 0.5)
        mock_math.compute_active_trailing_stop.return_value = 2.0
        mock_math.compute_breakeven_update.return_value = (0, False, -2.0)
        mock_math.compute_exit_confirmation.return_value = (0, False)
        mock_math.compute_vwap_breakdown_update.return_value = (0, 0, False, False)
        mock_math.compute_vwap_bleed_arm_threshold.return_value = 0.5

        alpha_bot_execution.main()

    final_state = captured_states[-1] if captured_states else copy.deepcopy(
        initial_bot_state
    )
    mocks = {
        "mock_db": mock_db,
        "mock_math": mock_math,
        "mock_reporting": mock_reporting,
    }
    return final_state, mocks


# ---------------------------------------------------------------------------
# Test 1: Pre-gate cycle populates current_return (the accounts_map / today_change
# proxy in bot_state)
#
# RED: currently the pre-gate path only runs _persist_composer_fields_to_bot_state
# (4 inception fields). It does NOT write current_return from last_percent_change.
# After the split, the data phase must write current_return every cycle from 09:30.
# ---------------------------------------------------------------------------

class TestPreGateCyclePopulatesAccountsMap:
    """Data phase must write current_return (the symphony's today_change) every cycle."""

    def test_pre_execution_start_time_cycle_populates_current_return(self):
        """
        A cycle at 09:45 ET (before EXECUTION_START_TIME=10:30) must write
        current_return to bot_state[sym_id] derived from Composer's
        last_percent_change.

        RED: the current market-open gate at :310 returns early after only
        persisting the 4 inception Composer fields; current_return is NOT
        written on the pre-gate path.
        """
        initial = _seed_state(hwm=0.0, current_return=0.0)
        final, _ = _run_one_cycle(
            current_et=_PRE_GATE_09_45,
            initial_bot_state=initial,
        )

        assert _SYM_ID in final, (
            f"bot_state must contain symphony {_SYM_ID} after pre-gate data cycle"
        )
        assert "current_return" in final[_SYM_ID], (
            f"bot_state[{_SYM_ID}] must contain 'current_return' after pre-gate cycle; "
            f"the data phase must write current_return = last_percent_change * 100 "
            f"from the Composer fetch, even before EXECUTION_START_TIME"
        )
        # Value must be a real float derived from the fixture, not the stale seed value
        actual = final[_SYM_ID]["current_return"]
        assert isinstance(actual, float), (
            f"current_return must be a float; got {type(actual).__name__}"
        )
        assert actual == pytest.approx(_EXPECTED_CURRENT_RETURN, abs=1e-9), (
            # Tolerance: float multiplication of last_percent_change * 100.
            # 1e-9 is appropriate because we are comparing two Python float
            # multiplications of the same operands — no lossy rounding involved.
            f"current_return must equal last_percent_change*100 = "
            f"{_EXPECTED_CURRENT_RETURN}; got {actual}"
        )

    def test_pre_execution_start_time_cycle_populates_today_change_as_nonzero(self):
        """
        A pre-gate cycle with a positive Composer return must write a non-zero,
        non-None current_return. The dashboard 'today_change' is derived from
        this field.

        RED: current code leaves current_return at the stale seeded value
        during pre-gate cycles.
        """
        initial = _seed_state(hwm=0.0, current_return=0.0)  # stale seed
        final, _ = _run_one_cycle(
            current_et=_PRE_GATE_09_45,
            initial_bot_state=initial,
            sym_payload=_make_sym_payload(last_percent_change=0.018),
        )

        actual = final.get(_SYM_ID, {}).get("current_return")
        assert actual is not None, (
            "current_return must not be None after pre-gate data cycle; "
            "data phase must write it from the Composer fetch"
        )
        assert actual != 0.0, (
            f"current_return must be non-zero when Composer reports last_percent_change=0.018; "
            f"got {actual!r}. The data phase is not running pre-gate."
        )
        assert actual == pytest.approx(1.8, abs=1e-9), (
            # 0.018 * 100 = 1.8; tolerance 1e-9 as above
            f"current_return must equal 1.8 (= 0.018 * 100); got {actual!r}"
        )

    def test_pre_gate_cycle_save_state_is_called(self):
        """
        A pre-gate cycle must call save_state (the data phase writes to DB every cycle).

        RED: the current pre-gate path calls save_state only for the 4 inception
        Composer fields. After the split, save_state must be called with the full
        per-symphony data update (including current_return, current_value, etc.).
        """
        initial = _seed_state()
        _, mocks = _run_one_cycle(
            current_et=_PRE_GATE_09_45,
            initial_bot_state=initial,
        )

        assert mocks["mock_db"].save_state.call_count >= 1, (
            "save_state must be called at least once during a pre-gate data cycle; "
            "the data phase must persist its updates to the DB"
        )


# ---------------------------------------------------------------------------
# Test 2: Pre-gate cycle does NOT fire action-phase logic
#
# The exit-decision machinery (MC gating, trailing-stop evaluation, Alpaca trade
# placement) must be completely absent from pre-gate cycles.
# ---------------------------------------------------------------------------

class TestPreGateCycleDoesNotFireActionPhase:
    """Action phase must be entirely absent from pre-gate cycles."""

    def test_pre_execution_start_time_cycle_does_not_call_run_monte_carlo(self):
        """
        run_monte_carlo must NOT be called during a pre-gate cycle (09:45 ET).

        MC gating is an action-phase concern: it consumes accumulated history to
        decide whether to arm an exit. Running it on cold data gives garbage
        probabilities and is the core warm-state bug. Pre-gate cycles must skip it.

        RED: the current engine has no action/data split — the entire intraday loop
        (including run_monte_carlo) is gated at EXECUTION_START_TIME together.
        After the split, run_monte_carlo must not be called on the data-only path.
        """
        initial = _seed_state()
        _, mocks = _run_one_cycle(
            current_et=_PRE_GATE_09_45,
            initial_bot_state=initial,
        )

        assert mocks["mock_math"].run_monte_carlo.call_count == 0, (
            f"run_monte_carlo was called {mocks['mock_math'].run_monte_carlo.call_count} "
            f"times during a pre-gate cycle (09:45 ET, EXECUTION_START_TIME=10:30). "
            f"MC gating is an action-phase concern and must not run before the gate. "
            f"The data phase must compute current_return and update HWM without MC."
        )

    def test_pre_execution_start_time_cycle_does_not_generate_exit_decisions(self):
        """
        No entry must be added to the execution queue, and execute_sell_to_cash
        must NOT be called, during a pre-gate cycle at 09:45 ET.

        The action phase is what generates exit decisions. The data phase only
        gathers and persists state. Pre-gate cycles must be purely observational.

        RED: with the current code there is no pre-gate data path, so this
        scenario is untestable (the intraday loop never runs pre-gate at all).
        After the split, this becomes the primary regression guard: the data
        path must be provably free of exit-triggering logic.
        """
        initial = _seed_state()
        _, mocks = _run_one_cycle(
            current_et=_PRE_GATE_09_45,
            initial_bot_state=initial,
        )

        # No exit action via Composer sell-to-cash
        # reporting.send_discord_alert is the canary: it fires ONLY when an exit
        # is triggered (trailing stop, TP, or VWAP). Pre-gate → never.
        assert mocks["mock_reporting"].send_discord_alert.call_count == 0, (
            f"send_discord_alert was called {mocks['mock_reporting'].send_discord_alert.call_count} "
            f"times during a pre-gate cycle; it must never be called before EXECUTION_START_TIME "
            f"because no exit decision can be generated in the data phase."
        )

    def test_pre_gate_cycle_does_not_arm_symphony(self):
        """
        A symphony must NOT be armed (armed=True in bot_state) after a pre-gate cycle,
        even if the Composer return would otherwise satisfy the arming condition.

        Arming is an action-phase state change. A data-only cycle updates metrics but
        must not advance the exit-decision state machine.

        RED: the action-phase state machine (arming, TP-arming, MC gating) is
        currently bundled with data gathering; after the split it must be absent
        pre-gate.
        """
        # Seed with armed=False
        initial = _seed_state(hwm=0.0, current_return=0.0, armed=False)
        final, _ = _run_one_cycle(
            current_et=_PRE_GATE_09_45,
            initial_bot_state=initial,
        )

        sym_state = final.get(_SYM_ID, {})
        assert not sym_state.get("armed", False), (
            f"symphony {_SYM_ID} was armed=True after a pre-gate cycle at 09:45 ET; "
            f"arming is action-phase logic and must not run before EXECUTION_START_TIME"
        )


# ---------------------------------------------------------------------------
# Test 3: HWM is updated in the data phase (pre-gate)
#
# The high_water_mark must advance when current_return exceeds it, even before
# EXECUTION_START_TIME. This is a data-phase concern: it tracks the intraday peak
# so the action phase at 10:30 starts with a warm HWM.
# ---------------------------------------------------------------------------

class TestDataPhaseUpdatesHwm:
    """HWM must be advanced in the data phase, not gated at EXECUTION_START_TIME."""

    def test_pre_gate_cycle_advances_hwm_when_return_rises(self):
        """
        When current_return rises above the seeded high_water_mark, the HWM must
        be updated in the pre-gate data cycle.

        This is the warm-state fix: without it, the first action-phase cycle at 10:30
        starts with HWM from the previous day, and the trailing stop fires against
        a stale reference point.

        RED: current code does not run the intraday loop pre-gate, so HWM never
        advances between 09:30 and 10:30. After the split, the data phase must
        call the HWM-advance logic unconditionally.
        """
        # Seed with HWM lower than what the Composer fetch will report
        initial = _seed_state(hwm=0.0, current_return=0.0)
        final, _ = _run_one_cycle(
            current_et=_PRE_GATE_09_45,
            initial_bot_state=initial,
            sym_payload=_make_sym_payload(last_percent_change=_COMPOSER_RETURN_FRAC),
        )

        sym_state = final.get(_SYM_ID, {})
        actual_hwm = sym_state.get("high_water_mark", 0.0)
        assert actual_hwm == pytest.approx(_EXPECTED_CURRENT_RETURN, abs=1e-9), (
            # Tolerance: comparing HWM (a stored float) to current_return (float multiply).
            # 1e-9 is tight enough to catch HWM-not-updated (stays 0.0) while
            # tolerating any legitimate floating-point chaining in the data path.
            f"high_water_mark must advance to {_EXPECTED_CURRENT_RETURN} when "
            f"current_return rises above seeded HWM (0.0); got {actual_hwm}. "
            f"HWM update is a data-phase concern and must run pre-gate."
        )

    def test_pre_gate_cycle_does_not_advance_hwm_when_return_below_prior(self):
        """
        When current_return is BELOW the seeded HWM, the HWM must NOT be reduced.
        The data phase may only advance the HWM, never lower it.

        This is an invariant of the trailing-stop math: the HWM only moves up.
        """
        high_prior_hwm = 5.0  # seeded above the Composer return of 1.5%
        initial = _seed_state(hwm=high_prior_hwm, current_return=1.5)
        final, _ = _run_one_cycle(
            current_et=_PRE_GATE_09_45,
            initial_bot_state=initial,
            sym_payload=_make_sym_payload(last_percent_change=_COMPOSER_RETURN_FRAC),
        )

        actual_hwm = final.get(_SYM_ID, {}).get("high_water_mark", high_prior_hwm)
        assert actual_hwm >= high_prior_hwm, (
            f"high_water_mark must not decrease below {high_prior_hwm}; "
            f"got {actual_hwm}. HWM is a monotonic high — it never goes down."
        )


# ---------------------------------------------------------------------------
# Test 4: Warm state at gate boundary
#
# The warm-state invariant: after two pre-gate data cycles, when the action
# phase fires at 10:31, the math engine has warm state — HWM reflects the
# intraday peak, symphony_vol is populated, current_return has been tracked.
# ---------------------------------------------------------------------------

class TestWarmStateAtGateBoundary:
    """After pre-gate data cycles, the action phase at 10:31 starts with warm state."""

    def test_hwm_warm_from_prior_data_cycles_when_action_phase_fires(self):
        """
        Run two consecutive pre-gate data cycles (09:45, 10:15), then one
        action-phase cycle (10:31). Assert that when the 10:31 cycle's action
        phase evaluates, high_water_mark reflects the peak established by the
        prior data cycles.

        This is the core regression guard for real-money correctness: trailing-stop
        distance is computed as current_return - high_water_mark. A cold HWM (0.0
        from yesterday) means the stop fires too late or not at all.

        RED: the current code never runs the data phase pre-gate, so HWM stays at
        the seeded/yesterday value until the first action cycle at 10:30. After
        the split, two pre-gate cycles must warm the HWM so the action cycle at
        10:31 uses the real intraday peak.
        """
        # --- Cycle 1: 09:45, return = 1.5% ---
        initial = _seed_state(hwm=0.0, current_return=0.0)
        state_after_cycle_1, _ = _run_one_cycle(
            current_et=_PRE_GATE_09_45,
            initial_bot_state=initial,
            sym_payload=_make_sym_payload(last_percent_change=_COMPOSER_RETURN_FRAC),
        )

        # HWM must reflect cycle-1 return after the data phase
        hwm_after_cycle_1 = state_after_cycle_1.get(_SYM_ID, {}).get("high_water_mark", 0.0)
        assert hwm_after_cycle_1 == pytest.approx(_EXPECTED_CURRENT_RETURN, abs=1e-9), (
            # 1e-9: float-multiply result comparison; see tolerance rationale in class above
            f"After cycle 1 (09:45), HWM must be {_EXPECTED_CURRENT_RETURN}; "
            f"got {hwm_after_cycle_1}. Data phase at 09:45 must update HWM."
        )

        # --- Cycle 2: 10:15, return rises to 2.5% (new peak) ---
        state_after_cycle_2, _ = _run_one_cycle(
            current_et=_PRE_GATE_10_15,
            initial_bot_state=state_after_cycle_1,
            sym_payload=_make_sym_payload(last_percent_change=_COMPOSER_RETURN_FRAC_HIGH),
        )

        hwm_after_cycle_2 = state_after_cycle_2.get(_SYM_ID, {}).get("high_water_mark", 0.0)
        assert hwm_after_cycle_2 == pytest.approx(_EXPECTED_CURRENT_RETURN_HIGH, abs=1e-9), (
            # 1e-9: same as above
            f"After cycle 2 (10:15), HWM must advance to {_EXPECTED_CURRENT_RETURN_HIGH} "
            f"(new peak); got {hwm_after_cycle_2}. Data phase must always advance HWM."
        )

        # --- Cycle 3: 10:31 — action phase fires with warm HWM ---
        state_after_cycle_3, mocks_3 = _run_one_cycle(
            current_et=_POST_GATE_10_31,
            initial_bot_state=state_after_cycle_2,
            sym_payload=_make_sym_payload(last_percent_change=_COMPOSER_RETURN_FRAC_HIGH),
        )

        # The action phase should have fired (run_monte_carlo called)
        assert mocks_3["mock_math"].run_monte_carlo.call_count >= 1, (
            "run_monte_carlo must be called in the action phase at 10:31; "
            "if it wasn't called, the action phase did not fire."
        )

        # HWM in the post-cycle state must still be the warm value from prior cycles,
        # not reset to 0 or yesterday's value
        final_hwm = state_after_cycle_3.get(_SYM_ID, {}).get("high_water_mark", 0.0)
        assert final_hwm >= _EXPECTED_CURRENT_RETURN_HIGH, (
            # >= because the action cycle may advance HWM further; but it must never
            # REGRESS the HWM below the warm pre-gate value
            f"HWM after the 10:31 action cycle must be >= {_EXPECTED_CURRENT_RETURN_HIGH} "
            f"(the warm pre-gate peak); got {final_hwm}. "
            f"The action cycle must NOT reset HWM to a cold value."
        )

    def test_symphony_vol_populated_before_action_phase(self):
        """
        After a pre-gate data cycle (09:45), symphony_vol must be populated in
        bot_state[sym_id]. The action phase at 10:31 uses symphony_vol for
        trailing-stop distance and parabolic-ratchet thresholds.

        If symphony_vol is absent pre-gate, the action phase at 10:30 starts
        with cold vol (0.0 or the default), which can cause miscalibrated stops.

        RED: the current code computes symphony_vol only inside the intraday loop
        (line ~547), which is gated at EXECUTION_START_TIME. The data phase must
        expose symphony_vol from calculate_20d_vol pre-gate.
        """
        initial = _seed_state()
        final, _ = _run_one_cycle(
            current_et=_PRE_GATE_09_45,
            initial_bot_state=initial,
        )

        assert "symphony_vol" in final.get(_SYM_ID, {}), (
            f"bot_state[{_SYM_ID}] must contain 'symphony_vol' after a pre-gate "
            f"data cycle; calculate_20d_vol must run in the data phase so the "
            f"action phase at 10:30 starts with warm volatility."
        )
        vol = final[_SYM_ID]["symphony_vol"]
        assert isinstance(vol, float), (
            f"symphony_vol must be a float; got {type(vol).__name__}"
        )
        assert vol >= 0.0, (
            f"symphony_vol must be non-negative (volatility is always >= 0); got {vol}"
        )


# ---------------------------------------------------------------------------
# Test 5: Post-gate cycle runs full action phase AND data phase
#
# After EXECUTION_START_TIME, both phases run: the data phase populates the
# accounts_map (current_return, current_value, holdings), AND the action phase
# runs MC gating, exit decisions, etc.
# ---------------------------------------------------------------------------

class TestPostGateCycleRunsBothPhases:
    """Post-gate cycles must run the full pipeline (data phase + action phase)."""

    def test_post_gate_cycle_populates_current_return(self):
        """
        A post-gate cycle (10:45 ET) must write current_return to bot_state.
        Data phase still runs inside the action-phase window.
        """
        initial = _seed_state(hwm=0.0, current_return=0.0)
        final, _ = _run_one_cycle(
            current_et=_POST_GATE_10_45,
            initial_bot_state=initial,
        )

        assert _SYM_ID in final, (
            f"bot_state must contain symphony {_SYM_ID} after post-gate cycle"
        )
        actual = final[_SYM_ID].get("current_return")
        assert actual is not None, (
            "current_return must not be None after post-gate cycle"
        )
        assert actual == pytest.approx(_EXPECTED_CURRENT_RETURN, abs=1e-9), (
            # Tolerance: same float multiply as pre-gate tests
            f"current_return must equal {_EXPECTED_CURRENT_RETURN} after post-gate cycle; "
            f"got {actual}"
        )

    def test_post_gate_cycle_runs_monte_carlo_gating(self):
        """
        The action phase must run during a post-gate cycle (10:45 ET).
        run_monte_carlo is the canary: if it was called, the action phase ran.
        """
        initial = _seed_state()
        _, mocks = _run_one_cycle(
            current_et=_POST_GATE_10_45,
            initial_bot_state=initial,
        )

        assert mocks["mock_math"].run_monte_carlo.call_count >= 1, (
            f"run_monte_carlo must be called during a post-gate cycle (10:45 ET); "
            f"the action phase must run fully after EXECUTION_START_TIME. "
            f"Call count: {mocks['mock_math'].run_monte_carlo.call_count}"
        )

    def test_post_gate_cycle_populates_mc_prob_in_bot_state(self):
        """
        After a post-gate cycle, bot_state[sym_id] must contain mc_prob (the result
        of run_monte_carlo). This is used by the dashboard to show arming probability.
        """
        initial = _seed_state()
        final, _ = _run_one_cycle(
            current_et=_POST_GATE_10_45,
            initial_bot_state=initial,
        )

        assert "mc_prob" in final.get(_SYM_ID, {}), (
            f"bot_state[{_SYM_ID}] must contain 'mc_prob' after post-gate cycle; "
            f"the action phase must write the MC probability to bot_state."
        )


# ---------------------------------------------------------------------------
# Test 6: No double state write within a single pre-gate cycle
#
# The data phase must write state exactly once per cycle. A double-write would
# cause the second save to clobber any state changes made between the two writes
# (e.g., if the first save writes the warm HWM and the second save reads stale
# state and overwrites with the old HWM).
# ---------------------------------------------------------------------------

class TestNoPersistDoubleSavePreGate:
    """save_state must be called exactly once per pre-gate data cycle."""

    def test_pre_gate_cycle_calls_save_state_exactly_once(self):
        """
        During a pre-gate data cycle (09:45 ET), save_state must be called
        exactly once. A double-write is a sign that the data phase has two
        separate code paths both calling save_state, which risks state races.

        This pins the 'no double-writes within a single cycle' acceptance criterion.
        """
        initial = _seed_state()
        _, mocks = _run_one_cycle(
            current_et=_PRE_GATE_09_45,
            initial_bot_state=initial,
        )

        save_count = mocks["mock_db"].save_state.call_count
        assert save_count == 1, (
            f"save_state must be called exactly once per pre-gate data cycle; "
            f"got {save_count} calls. A double-write risks state corruption."
        )


# ---------------------------------------------------------------------------
# Test 7: Fetch failure in data phase does NOT clobber bot_state with None
#
# If the Composer fetch fails (returns []) during a pre-gate data cycle,
# bot_state must retain its prior values. The data phase must never write
# None over a previously valid current_return or HWM.
# ---------------------------------------------------------------------------

class TestDataPhaseFetchFailureRetainsPriorState:
    """Composer fetch failure in data phase must not overwrite prior bot_state values."""

    def test_fetch_failure_in_pre_gate_data_phase_retains_prior_current_return(self):
        """
        When fetch_symphony_stats returns [] (network failure) during a pre-gate
        data cycle, bot_state[sym_id]['current_return'] must retain its prior value.

        The data phase must guard against overwriting valid prior data with absent/None
        on fetch failure. This is the real-money safety net: a transient Composer outage
        must not poison the warm state accumulated by prior successful cycles.
        """
        prior_return = 1.5
        prior_hwm = 1.5
        initial = _seed_state(hwm=prior_hwm, current_return=prior_return)

        date_str = _PRE_GATE_09_45.strftime("%Y-%m-%d")
        captured_states: list[dict] = []

        def capture(s: dict) -> None:
            captured_states.append(copy.deepcopy(s))

        with patch.object(alpha_bot_execution, "database") as mock_db, \
             patch.object(alpha_bot_execution, "reporting"), \
             patch.object(alpha_bot_execution, "fetch_symphony_stats",
                          return_value=[]), \
             patch.object(alpha_bot_execution, "fetch_alpaca_history",
                          return_value=_make_minimal_history(date_str)), \
             patch.object(alpha_bot_execution, "fetch_intraday_vwaps",
                          return_value={}), \
             patch.object(alpha_bot_execution, "get_current_et",
                          return_value=_PRE_GATE_09_45), \
             patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", [_ACCOUNT_ID]), \
             patch.object(alpha_bot_execution, "COMPOSER_KEY_ID", "test-key-id"), \
             patch.object(alpha_bot_execution, "ALPACA_KEY", "test-alpaca-key"), \
             patch.object(alpha_bot_execution, "LIVE_EXECUTION", False), \
             patch.object(alpha_bot_execution, "EXECUTION_START_TIME", "10:30"), \
             patch.object(alpha_bot_execution.time, "sleep"), \
             patch.object(alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py"]):

            mock_db.acquire_lock.return_value = True
            mock_db.load_state.return_value = copy.deepcopy(initial)
            mock_db.load_chart_history.return_value = {"date": date_str, "symphonies": {}}
            mock_db.get_symphony_strategy.return_value = {"params": {}, "locked_vars": {}}
            mock_db.normalize_name.side_effect = lambda n: n.strip().lower()
            mock_db.wipe_transient_state.side_effect = lambda s: s
            mock_db.save_state.side_effect = capture

            alpha_bot_execution.main()

        if not captured_states:
            # save_state was never called — the prior state was never overwritten,
            # so prior values are still intact (this is acceptable behavior on
            # fetch failure: do nothing rather than write partial state)
            return

        final = captured_states[-1]
        if _SYM_ID not in final:
            # Symphony entry absent from saved state on fetch failure is acceptable
            # (the data phase skipped it because no Composer data arrived). What
            # is NOT acceptable: the entry being present with current_return=None.
            return

        actual_return = final[_SYM_ID].get("current_return")
        assert actual_return is not None, (
            f"bot_state[{_SYM_ID}]['current_return'] must not be overwritten with "
            f"None when fetch_symphony_stats returns []; prior value was {prior_return}. "
            f"The data phase must only write current_return when it has fresh data."
        )
        if actual_return != 0.0:  # 0.0 is only invalid if the prior was non-zero
            assert actual_return == pytest.approx(prior_return, abs=1e-9), (
                # Tolerance: prior_return is a Python float literal, comparison is exact
                f"current_return must equal prior value {prior_return} after fetch failure; "
                f"got {actual_return!r}"
            )

    def test_fetch_failure_in_pre_gate_data_phase_retains_prior_hwm(self):
        """
        When fetch_symphony_stats returns [], the data phase must not reset
        high_water_mark to 0.0 or to any value below the prior HWM.

        Resetting HWM on fetch failure would cause the trailing stop to fire
        incorrectly when the action phase next runs.
        """
        prior_hwm = 2.5
        initial = _seed_state(hwm=prior_hwm, current_return=2.5)

        date_str = _PRE_GATE_09_45.strftime("%Y-%m-%d")
        captured_states: list[dict] = []

        def capture(s: dict) -> None:
            captured_states.append(copy.deepcopy(s))

        with patch.object(alpha_bot_execution, "database") as mock_db, \
             patch.object(alpha_bot_execution, "reporting"), \
             patch.object(alpha_bot_execution, "fetch_symphony_stats",
                          return_value=[]), \
             patch.object(alpha_bot_execution, "fetch_alpaca_history",
                          return_value=_make_minimal_history(date_str)), \
             patch.object(alpha_bot_execution, "fetch_intraday_vwaps",
                          return_value={}), \
             patch.object(alpha_bot_execution, "get_current_et",
                          return_value=_PRE_GATE_09_45), \
             patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", [_ACCOUNT_ID]), \
             patch.object(alpha_bot_execution, "COMPOSER_KEY_ID", "test-key-id"), \
             patch.object(alpha_bot_execution, "ALPACA_KEY", "test-alpaca-key"), \
             patch.object(alpha_bot_execution, "LIVE_EXECUTION", False), \
             patch.object(alpha_bot_execution, "EXECUTION_START_TIME", "10:30"), \
             patch.object(alpha_bot_execution.time, "sleep"), \
             patch.object(alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py"]):

            mock_db.acquire_lock.return_value = True
            mock_db.load_state.return_value = copy.deepcopy(initial)
            mock_db.load_chart_history.return_value = {"date": date_str, "symphonies": {}}
            mock_db.get_symphony_strategy.return_value = {"params": {}, "locked_vars": {}}
            mock_db.normalize_name.side_effect = lambda n: n.strip().lower()
            mock_db.wipe_transient_state.side_effect = lambda s: s
            mock_db.save_state.side_effect = capture

            alpha_bot_execution.main()

        if not captured_states:
            return  # acceptable: no state written on fetch failure

        final = captured_states[-1]
        if _SYM_ID not in final:
            return  # symphony entry absent is acceptable on fetch failure

        actual_hwm = final[_SYM_ID].get("high_water_mark", prior_hwm)
        assert actual_hwm >= prior_hwm, (
            f"high_water_mark must not decrease below {prior_hwm} after a "
            f"fetch-failure pre-gate cycle; got {actual_hwm}. "
            f"The data phase must not reset HWM on fetch failure."
        )


# ---------------------------------------------------------------------------
# Test 8: Math-engine inputs are populated by data phase before action phase
#
# For each math-engine input that the action phase consumes at 10:31, assert
# that the field is populated in bot_state by the time the action phase fires.
# ---------------------------------------------------------------------------

class TestMathEngineInputsPopulatedByDataPhase:
    """Key math-engine inputs must be in bot_state before the action phase runs."""

    def test_current_return_present_in_bot_state_before_action_phase(self):
        """
        current_return must be in bot_state[sym_id] when run_monte_carlo is called
        in the action phase at 10:31. The math engine uses current_return to assess
        whether the symphony's return trajectory warrants an exit.
        """
        # Prime state from a pre-gate data cycle
        initial = _seed_state(hwm=0.0, current_return=0.0)
        state_after_data_cycle, _ = _run_one_cycle(
            current_et=_PRE_GATE_09_45,
            initial_bot_state=initial,
        )

        # Now run the action cycle — current_return must be present from data phase
        final, mocks = _run_one_cycle(
            current_et=_POST_GATE_10_31,
            initial_bot_state=state_after_data_cycle,
        )

        assert "current_return" in final.get(_SYM_ID, {}), (
            f"current_return must be in bot_state[{_SYM_ID}] after the action cycle "
            f"at 10:31; it must have been populated by the data phase at 09:45."
        )

    def test_symphony_vol_present_before_action_phase(self):
        """
        symphony_vol must be in bot_state[sym_id] before/when the action phase runs.
        compute_active_trailing_stop and compute_breakeven_update both consume it.

        After pre-gate data cycles, the action cycle must not start with a cold vol.
        """
        initial = _seed_state()
        state_after_data_cycle, _ = _run_one_cycle(
            current_et=_PRE_GATE_09_45,
            initial_bot_state=initial,
        )

        # symphony_vol must be written by the data cycle
        vol_after_data = state_after_data_cycle.get(_SYM_ID, {}).get("symphony_vol")
        assert vol_after_data is not None, (
            f"symphony_vol must be in bot_state[{_SYM_ID}] after data cycle at 09:45; "
            f"it must be populated before the action phase fires at 10:31."
        )
        assert isinstance(vol_after_data, float) and vol_after_data >= 0.0, (
            f"symphony_vol must be a non-negative float; got {vol_after_data!r}"
        )
