"""
RED tests for H2 — Trigger Priority Resolution at Side-Effect Level.

Covers AC-H2.1 through AC-H2.4:

  AC-H2.1: When multiple triggers fire in the same cycle, priority is resolved
            BEFORE side effects (triggered=True, counter, sell-to-cash, event log).
  AC-H2.2: Priority order is VWAP Breakdown > Take-Profit > VWAP Bleed Cut >
            Trailing Stop — explicit in the if/elif ladder at the dispatch site.
  AC-H2.3: H1 telemetry records the resolved trigger, candidates that also
            fired, and gate-state at priority resolution.
  AC-H2.4: Fixture with TP + Trailing Stop + VWAP Breakdown all True → only
            VWAP Breakdown side-effects fire; telemetry records "VWAP Breakdown
            (with TP, Trailing Stop also True)".

Mocking philosophy (inherits from test_main_pipeline.py):
  - No real network — fetch_symphony_stats / fetch_alpaca_history /
    fetch_intraday_vwaps patched.
  - No real DB — database.* mocked; record_exit_trigger captured for inspection.
  - Math engine NOT mocked wholesale; only the four trigger-evaluation functions
    are surgically patched to return deterministic True/False values so the
    priority-resolution logic is exercised without needing a hand-crafted
    gate-state fixture that reverse-engineers MC / vol math.
  - execute_sell_to_cash mocked to return True (success) so the full side-effect
    block runs and call_count is measurable.

PA-18: fixtures under tests/fixtures/engine/trigger_priority/ — schema-derived,
       zero inline construction, zero hardcoded producer-computed values.
PA-19: explicit APPROVE from quant-code-reviewer + risk-engine-specialist required.

Fixture provenance: captured-from-source — field names derived directly from
  alpha_bot_execution.py:938-950 trigger detection block and
  database.record_exit_trigger() call at alpha_bot_execution.py:1064-1077.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, call, patch

import pytest

import alpha_bot_execution

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

FIXTURES_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "engine" / "trigger_priority"


def _load(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


# ---------------------------------------------------------------------------
# Shared clock + minimal state helpers
# ---------------------------------------------------------------------------

try:
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
    _FIXED_ET = datetime(2026, 5, 14, 11, 30, 0, tzinfo=_ET)  # Wed 11:30 ET — market hours
except Exception:
    _FIXED_ET = datetime(2026, 5, 14, 11, 30, 0, tzinfo=timezone(timedelta(hours=-4)))

_SYMPHONY_ID = "sym-h2-priority-test"
_ACTUAL_SYMPHONY_ID = "actual-sym-h2-priority-test"
_ACCOUNT_ID = "acct-h2-priority-test"
_SYMPHONY_NAME = "H2 Priority Test Symphony"
_TICKER = "SPY"


def _make_symphony_payload(last_percent_change: float = 0.05) -> dict:
    return {
        "id": _SYMPHONY_ID,
        "symphony_id": _ACTUAL_SYMPHONY_ID,
        "name": _SYMPHONY_NAME,
        "last_percent_change": last_percent_change,
        "current_value": 10000.0,
        "holdings": [{"ticker": _TICKER, "allocation": 1.0}],
    }


def _make_minimal_history(date_str: str) -> dict:
    return {
        date_str: {
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


def _seed_state(**extras) -> dict:
    base = {
        "date": _FIXED_ET.strftime("%Y-%m-%d"),
        "last_execution_mode": False,
        _SYMPHONY_ID: {
            "high_water_mark": 5.0,
            "shadow_hwm": 5.0,
            "prev_return": 4.5,
            "armed": True,
            "tp_armed": True,
            "para_armed": False,
            "triggered": False,
            "mc_history": [],
            "below_stop_count": 3,
            "above_tp_count": 2,
            "vwap_ticks": 3,
            "vwap_bleed_ticks": 2,
            "breakeven_locked": False,
            "hwm_hold_ticks": 0,
        },
    }
    base[_SYMPHONY_ID].update(extras)
    return base


# ---------------------------------------------------------------------------
# Core environment fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def patched_env():
    """
    Bundle all patches needed for trigger-priority dispatch tests.

    Key surgical patches:
      - compute_exit_confirmation → controls is_trailing_stop_hit
      - compute_vwap_breakdown_update → controls is_vwap_broken + is_vwap_bleed_broken
      - tp_triggered_now is set by the TP arm/fire logic; we force it by seeding
        above_tp_count >= 2, tp_armed=True, and current_return > 0 (positive return
        in payload), plus prob_underperforming >= acc_TAKE_PROFIT_MC_PCT (so TP fires).
        We control tp NOT firing by patching mc_prob so prob < threshold
        never reached, or by pre-seeding tp_armed=False.
      - execute_sell_to_cash → returns True so state mutations run
    """
    date_str = _FIXED_ET.strftime("%Y-%m-%d")

    with (
        patch.object(alpha_bot_execution, "database") as mock_db,
        patch.object(alpha_bot_execution, "reporting") as mock_reporting,
        patch.object(alpha_bot_execution, "fetch_symphony_stats") as mock_fetch_sym,
        patch.object(alpha_bot_execution, "fetch_alpaca_history") as mock_fetch_hist,
        patch.object(alpha_bot_execution, "fetch_intraday_vwaps") as mock_fetch_vwap,
        patch.object(alpha_bot_execution, "execute_sell_to_cash", return_value=True) as mock_sell,
        patch.object(alpha_bot_execution, "get_current_et", return_value=_FIXED_ET),
        patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", [_ACCOUNT_ID]),
        patch.object(alpha_bot_execution, "COMPOSER_KEY_ID", "test-key"),
        patch.object(alpha_bot_execution, "ALPACA_KEY", "test-alpaca"),
        patch.object(alpha_bot_execution, "LIVE_EXECUTION", True),
        patch.object(alpha_bot_execution.time, "sleep"),
        patch.object(alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py"]),
        # These priority-dispatch tests isolate the if/elif winner-resolution
        # ladder from the underlying math. They already surgically patch
        # compute_exit_confirmation and compute_vwap_breakdown_update to control
        # the Trailing-Stop / VWAP trigger booleans; run_monte_carlo is patched
        # here for the same reason — to give a real, in-band prob_underperforming that
        # the TP-confirm gate can act on. The single-day _make_minimal_history
        # fixture is below MC_MIN_HISTORY_DAYS, so the real run_monte_carlo would
        # return the insufficient sentinel (None) and the TP path could never
        # fire (Cluster 2 AC-2 fail-safe). A fixed 50.0 is >= TAKE_PROFIT_MC_PCT
        # (TP-confirm fires for a pre-armed symphony) and >= TRIGGER_THRESHOLD_PCT
        # (so it does not also re-trip the arm gate). This is NOT a math test —
        # the MC math itself is covered by tests/math_engine/.
        patch.object(alpha_bot_execution.math_engine, "run_monte_carlo", return_value=50.0),
    ):
        mock_db.acquire_lock.return_value = True
        mock_db.load_state.return_value = _seed_state()
        mock_db.load_chart_history.return_value = {
            "date": _FIXED_ET.strftime("%Y-%m-%d"),
            "symphonies": {},
        }
        # live_mode=True: these tests exercise live execution paths; the master-switch
        # gate (LIVE_EXECUTION and item["live_mode"]) requires both flags set.
        mock_db.get_symphony_strategy.return_value = {
            "params": {},
            "locked_vars": {},
            "live_mode": True,
        }
        mock_db.normalize_name.side_effect = lambda x: x
        mock_db.wipe_transient_state.side_effect = lambda s: s

        mock_fetch_sym.return_value = [_make_symphony_payload(last_percent_change=0.05)]
        mock_fetch_hist.return_value = _make_minimal_history(date_str)
        mock_fetch_vwap.return_value = _make_vwap_payload()

        yield {
            "mock_db": mock_db,
            "mock_reporting": mock_reporting,
            "mock_sell": mock_sell,
            "mock_fetch_sym": mock_fetch_sym,
            "date_str": date_str,
        }


# ---------------------------------------------------------------------------
# Helper: extract the triggered_reason written to bot_state via save_state
# ---------------------------------------------------------------------------


def _capture_triggered_reason(mock_db: MagicMock) -> str | None:
    """
    Scan save_state call_args to find the triggered_reason set in bot_state.
    Returns None if no triggered state was saved (zero-trigger cycle).
    """
    for c in mock_db.save_state.call_args_list:
        saved_state = c[0][0] if c[0] else c[1].get("state", {})
        sym = saved_state.get(_SYMPHONY_ID, {})
        if sym.get("triggered") and sym.get("triggered_reason"):
            return sym["triggered_reason"]
    return None


def _capture_record_exit_trigger_calls(mock_db: MagicMock) -> list[dict]:
    """Return list of kwargs from all record_exit_trigger calls."""
    return [c[1] for c in mock_db.record_exit_trigger.call_args_list]


# ---------------------------------------------------------------------------
# H2.4 / AC-H2.2 primary: all three (TP + Trailing Stop + VWAP Breakdown) True
# VWAP Breakdown must win — this is the canonical failing test for the bug.
# ---------------------------------------------------------------------------


def test_vwap_breakdown_wins_when_tp_and_trailing_stop_also_true(patched_env):
    """
    AC-H2.4: TP + Trailing Stop + VWAP Breakdown all evaluate True.
    Only VWAP Breakdown side-effects may run.

    CURRENT BUG: the if/elif at alpha_bot_execution.py:939 checks tp_triggered_now
    BEFORE is_vwap_broken, so TP wins today. H2 must invert the order.

    This test will be RED until the implementer reorders the if/elif to put
    VWAP Breakdown first.
    """
    fixture = _load("multi_trigger_all_three.json")
    assert fixture["expected_winner"] == "VWAP Breakdown"

    mock_db = patched_env["mock_db"]
    mock_sell = patched_env["mock_sell"]

    # Force VWAP Breakdown True, Trailing Stop True via surgical patches.
    # TP is forced True by seeding: tp_armed=True, above_tp_count=2, current_return>0,
    # and prob_underperforming >= TAKE_PROFIT_MC_PCT (TP-fire condition: above_tp_count>=2
    # AND current_return > 0 per alpha_bot_execution.py:860-862).
    mock_db.load_state.return_value = _seed_state(
        tp_armed=True, above_tp_count=2, below_stop_count=3
    )

    with (
        patch.object(
            alpha_bot_execution.math_engine,
            "compute_exit_confirmation",
            return_value=(3, True),  # (new_count, is_hit) — Trailing Stop fires
        ),
        patch.object(
            alpha_bot_execution.math_engine,
            "compute_vwap_breakdown_update",
            return_value=(
                3,
                2,
                True,
                False,
            ),  # (vwap_ticks, bleed_ticks, is_vwap_broken, is_vwap_bleed_broken)
        ),
        # prob_underperforming >= TAKE_PROFIT_MC_PCT so TP arm fires (above_tp_count increment)
        # but we also need current_return > 0 (payload sets last_percent_change=0.05>0)
        # above_tp_count starts at 2 → >=2 → tp_triggered_now = True
        # So TP is also True.
    ):
        alpha_bot_execution.main()

    # Assert: triggered_reason in final saved state is VWAP Breakdown
    reason = _capture_triggered_reason(mock_db)
    assert reason == fixture["expected_winner"], (
        f"Priority bug: expected VWAP Breakdown to win when all three triggers fire, "
        f"got {reason!r}. The if/elif ladder must check is_vwap_broken BEFORE tp_triggered_now."
    )

    # Assert: exactly one sell_to_cash call (one side-effect dispatch, not three)
    assert mock_sell.call_count == fixture["side_effect_call_counts"]["sell_to_cash_total_calls"], (
        f"Expected exactly 1 sell_to_cash call; got {mock_sell.call_count}. "
        f"Duplicate side-effect dispatch detected."
    )

    # Assert: exactly one record_exit_trigger call
    trigger_calls = _capture_record_exit_trigger_calls(mock_db)
    assert len(trigger_calls) == fixture["side_effect_call_counts"]["record_exit_trigger_calls"], (
        f"Expected 1 record_exit_trigger call; got {len(trigger_calls)}."
    )

    # Assert: triggered_reason in telemetry row is VWAP Breakdown
    if trigger_calls:
        assert trigger_calls[0].get("triggered_reason") == fixture["expected_winner"], (
            f"Telemetry triggered_reason must be {fixture['expected_winner']!r}; "
            f"got {trigger_calls[0].get('triggered_reason')!r}"
        )


def test_vwap_breakdown_win_telemetry_records_also_true_candidates(patched_env):
    """
    AC-H2.3: when VWAP Breakdown wins over TP and Trailing Stop, the telemetry
    gate_state_json must contain an 'also_true' field listing the non-winning
    triggers that also fired.

    This will be RED until the implementer extends gate_state_json with also_true.
    """
    fixture = _load("multi_trigger_all_three.json")
    mock_db = patched_env["mock_db"]
    mock_db.load_state.return_value = _seed_state(
        tp_armed=True, above_tp_count=2, below_stop_count=3
    )

    with (
        patch.object(
            alpha_bot_execution.math_engine,
            "compute_exit_confirmation",
            return_value=(3, True),
        ),
        patch.object(
            alpha_bot_execution.math_engine,
            "compute_vwap_breakdown_update",
            return_value=(3, 2, True, False),
        ),
    ):
        alpha_bot_execution.main()

    trigger_calls = _capture_record_exit_trigger_calls(mock_db)
    assert len(trigger_calls) >= 1, "record_exit_trigger must have been called"

    gate_state_raw = trigger_calls[0].get("gate_state")
    assert gate_state_raw is not None, "gate_state must be passed to record_exit_trigger"

    # gate_state may arrive as dict (direct) or JSON string
    gate_state = json.loads(gate_state_raw) if isinstance(gate_state_raw, str) else gate_state_raw

    assert "also_true" in gate_state, (
        "AC-H2.3: gate_state must contain 'also_true' key listing non-winning "
        "triggers that also fired. Key absent — implementer must extend gate_state_json."
    )
    also_true = gate_state["also_true"]
    assert isinstance(also_true, list), f"also_true must be a list; got {type(also_true)}"

    expected_candidates = set(fixture["also_true_candidates"])
    actual_candidates = set(also_true)
    assert expected_candidates == actual_candidates, (
        f"also_true must record {expected_candidates}; got {actual_candidates}"
    )


def test_vwap_breakdown_win_gate_state_captures_moment_of_resolution(patched_env):
    """
    AC-H2.3: gate_state recorded in telemetry must contain the HWM, vwap_ticks,
    vwap_bleed_ticks, mc_prob, and symphony_vol at the priority-resolution moment.

    Shape test only — no hardcoded producer-computed values.
    """
    fixture = _load("multi_trigger_all_three.json")
    mock_db = patched_env["mock_db"]
    mock_db.load_state.return_value = _seed_state(
        tp_armed=True, above_tp_count=2, below_stop_count=3
    )

    with (
        patch.object(
            alpha_bot_execution.math_engine,
            "compute_exit_confirmation",
            return_value=(3, True),
        ),
        patch.object(
            alpha_bot_execution.math_engine,
            "compute_vwap_breakdown_update",
            return_value=(3, 2, True, False),
        ),
    ):
        alpha_bot_execution.main()

    trigger_calls = _capture_record_exit_trigger_calls(mock_db)
    assert trigger_calls, "record_exit_trigger must be called"
    gate_state_raw = trigger_calls[0].get("gate_state")
    gate_state = json.loads(gate_state_raw) if isinstance(gate_state_raw, str) else gate_state_raw
    assert gate_state is not None

    shape = fixture["gate_state_shape"]
    for field, spec in shape.items():
        assert field in gate_state, (
            f"gate_state must contain '{field}'; missing. "
            f"H1 gate_state shape: {list(gate_state.keys())}"
        )
        val = gate_state[field]
        if spec["type"] == "float":
            assert isinstance(val, (int, float)), f"{field} must be numeric; got {type(val)}"
            if "min" in spec:
                # vwap_ticks / vwap_bleed_ticks may be 0 (non-negative floor)
                assert val >= spec["min"], f"{field} must be >= {spec['min']}; got {val}"
        if spec["type"] == "int":
            assert isinstance(val, int), f"{field} must be int; got {type(val)}"
            if "min" in spec:
                assert val >= spec["min"], f"{field} must be >= {spec['min']}; got {val}"


# ---------------------------------------------------------------------------
# Pairwise: TP + Trailing Stop — TP must win
# ---------------------------------------------------------------------------


def test_take_profit_wins_over_trailing_stop(patched_env):
    """
    AC-H2.2: TP (priority 1) beats Trailing Stop (priority 3) when both fire.
    No regression — TP already wins in the current code for this pair.
    This test pins the no-regression contract on the single-trigger path.
    """
    fixture = _load("pairwise_tp_and_trailing_stop.json")
    assert fixture["expected_winner"] == "Take-Profit"

    mock_db = patched_env["mock_db"]
    mock_sell = patched_env["mock_sell"]
    mock_db.load_state.return_value = _seed_state(
        tp_armed=True, above_tp_count=2, below_stop_count=3
    )

    with (
        patch.object(
            alpha_bot_execution.math_engine,
            "compute_exit_confirmation",
            return_value=(3, True),
        ),
        patch.object(
            alpha_bot_execution.math_engine,
            "compute_vwap_breakdown_update",
            return_value=(0, 0, False, False),  # VWAP Breakdown NOT firing
        ),
    ):
        alpha_bot_execution.main()

    reason = _capture_triggered_reason(mock_db)
    assert reason == fixture["expected_winner"], (
        f"Expected Take-Profit to win over Trailing Stop; got {reason!r}"
    )
    assert mock_sell.call_count == fixture["side_effect_call_counts"]["sell_to_cash_total_calls"]


def test_take_profit_wins_telemetry_records_trailing_stop_as_also_true(patched_env):
    """
    AC-H2.3: when TP wins over Trailing Stop, telemetry also_true must contain
    'Trailing Stop'.
    """
    fixture = _load("pairwise_tp_and_trailing_stop.json")
    mock_db = patched_env["mock_db"]
    mock_db.load_state.return_value = _seed_state(
        tp_armed=True, above_tp_count=2, below_stop_count=3
    )

    with (
        patch.object(
            alpha_bot_execution.math_engine,
            "compute_exit_confirmation",
            return_value=(3, True),
        ),
        patch.object(
            alpha_bot_execution.math_engine,
            "compute_vwap_breakdown_update",
            return_value=(0, 0, False, False),
        ),
    ):
        alpha_bot_execution.main()

    trigger_calls = _capture_record_exit_trigger_calls(mock_db)
    assert trigger_calls
    gate_state_raw = trigger_calls[0].get("gate_state")
    gate_state = json.loads(gate_state_raw) if isinstance(gate_state_raw, str) else gate_state_raw
    assert "also_true" in gate_state, "also_true must be present in gate_state"
    assert "Trailing Stop" in gate_state["also_true"], (
        f"Trailing Stop must appear in also_true when TP wins over it; got {gate_state['also_true']}"
    )


# ---------------------------------------------------------------------------
# Pairwise: VWAP Bleed Cut + Trailing Stop — VWAP Bleed Cut must win
# ---------------------------------------------------------------------------


def test_vwap_bleed_cut_wins_over_trailing_stop(patched_env):
    """
    AC-H2.2: VWAP Bleed Cut (priority 2) beats Trailing Stop (priority 3).
    """
    fixture = _load("pairwise_vwap_bleed_and_trailing_stop.json")
    assert fixture["expected_winner"] == "VWAP Bleed Cut"

    mock_db = patched_env["mock_db"]
    mock_sell = patched_env["mock_sell"]
    mock_db.load_state.return_value = _seed_state(
        tp_armed=False, above_tp_count=0, below_stop_count=3
    )

    with (
        patch.object(
            alpha_bot_execution.math_engine,
            "compute_exit_confirmation",
            return_value=(3, True),
        ),
        patch.object(
            alpha_bot_execution.math_engine,
            "compute_vwap_breakdown_update",
            return_value=(0, 3, False, True),  # bleed fires, breakdown does NOT
        ),
    ):
        alpha_bot_execution.main()

    reason = _capture_triggered_reason(mock_db)
    assert reason == fixture["expected_winner"], (
        f"Expected VWAP Bleed Cut to win over Trailing Stop; got {reason!r}"
    )
    assert mock_sell.call_count == fixture["side_effect_call_counts"]["sell_to_cash_total_calls"]


# ---------------------------------------------------------------------------
# Pairwise: VWAP Breakdown + VWAP Bleed Cut — VWAP Breakdown must win
# ---------------------------------------------------------------------------


def test_vwap_breakdown_wins_over_vwap_bleed_cut(patched_env):
    """
    AC-H2.2: VWAP Breakdown (priority 0) beats VWAP Bleed Cut (priority 2).
    Current code: is_vwap_bleed_broken is checked BEFORE is_vwap_broken in the
    elif ladder, so this pair also has a priority inversion bug.

    This test will be RED until the implementer reorders the if/elif.
    """
    fixture = _load("pairwise_vwap_breakdown_and_bleed.json")
    assert fixture["expected_winner"] == "VWAP Breakdown"

    mock_db = patched_env["mock_db"]
    mock_sell = patched_env["mock_sell"]
    mock_db.load_state.return_value = _seed_state(
        tp_armed=False, above_tp_count=0, below_stop_count=0
    )

    with (
        patch.object(
            alpha_bot_execution.math_engine,
            "compute_exit_confirmation",
            return_value=(0, False),
        ),
        patch.object(
            alpha_bot_execution.math_engine,
            "compute_vwap_breakdown_update",
            return_value=(3, 3, True, True),  # BOTH breakdown and bleed fire
        ),
    ):
        alpha_bot_execution.main()

    reason = _capture_triggered_reason(mock_db)
    assert reason == fixture["expected_winner"], (
        f"Priority bug: expected VWAP Breakdown to win over VWAP Bleed Cut; got {reason!r}. "
        f"The elif ladder checks is_vwap_bleed_broken before is_vwap_broken — must be reversed."
    )
    assert mock_sell.call_count == fixture["side_effect_call_counts"]["sell_to_cash_total_calls"]


def test_vwap_breakdown_wins_over_vwap_bleed_cut_also_true_recorded(patched_env):
    """
    AC-H2.3: when VWAP Breakdown beats VWAP Bleed Cut, also_true contains
    'VWAP Bleed Cut'.
    """
    fixture = _load("pairwise_vwap_breakdown_and_bleed.json")
    mock_db = patched_env["mock_db"]
    mock_db.load_state.return_value = _seed_state(
        tp_armed=False, above_tp_count=0, below_stop_count=0
    )

    with (
        patch.object(
            alpha_bot_execution.math_engine,
            "compute_exit_confirmation",
            return_value=(0, False),
        ),
        patch.object(
            alpha_bot_execution.math_engine,
            "compute_vwap_breakdown_update",
            return_value=(3, 3, True, True),
        ),
    ):
        alpha_bot_execution.main()

    trigger_calls = _capture_record_exit_trigger_calls(mock_db)
    assert trigger_calls
    gate_state_raw = trigger_calls[0].get("gate_state")
    gate_state = json.loads(gate_state_raw) if isinstance(gate_state_raw, str) else gate_state_raw
    assert "also_true" in gate_state
    assert "VWAP Bleed Cut" in gate_state["also_true"], (
        f"VWAP Bleed Cut must appear in also_true; got {gate_state['also_true']}"
    )


# ---------------------------------------------------------------------------
# Single-trigger regression: each trigger alone fires only its own side-effect
# ---------------------------------------------------------------------------


def test_single_trailing_stop_fires_one_side_effect(patched_env):
    """
    Single-trigger regression: Trailing Stop alone → exactly one sell, one telemetry
    row, triggered_reason = 'Trailing Stop', also_true = [].
    """
    fixture = _load("single_trigger_trailing_stop.json")
    assert fixture["expected_winner"] == "Trailing Stop"

    mock_db = patched_env["mock_db"]
    mock_sell = patched_env["mock_sell"]
    mock_db.load_state.return_value = _seed_state(
        tp_armed=False, above_tp_count=0, below_stop_count=3
    )

    with (
        patch.object(
            alpha_bot_execution.math_engine,
            "compute_exit_confirmation",
            return_value=(3, True),
        ),
        patch.object(
            alpha_bot_execution.math_engine,
            "compute_vwap_breakdown_update",
            return_value=(0, 0, False, False),
        ),
    ):
        alpha_bot_execution.main()

    reason = _capture_triggered_reason(mock_db)
    assert reason == fixture["expected_winner"], (
        f"Single Trailing Stop: expected 'Trailing Stop'; got {reason!r}"
    )
    assert mock_sell.call_count == fixture["side_effect_call_counts"]["sell_to_cash_total_calls"]

    trigger_calls = _capture_record_exit_trigger_calls(mock_db)
    assert len(trigger_calls) == fixture["side_effect_call_counts"]["record_exit_trigger_calls"]

    # also_true must be present and empty for a single-trigger cycle
    if trigger_calls:
        gate_state_raw = trigger_calls[0].get("gate_state")
        gate_state = (
            json.loads(gate_state_raw) if isinstance(gate_state_raw, str) else gate_state_raw
        )
        assert "also_true" in gate_state, (
            "also_true key must always be present in gate_state (even when empty)"
        )
        assert gate_state["also_true"] == [], (
            f"Single-trigger cycle: also_true must be []; got {gate_state['also_true']}"
        )


# ---------------------------------------------------------------------------
# Zero-trigger cycle: no side effects
# ---------------------------------------------------------------------------


def test_zero_trigger_cycle_fires_no_side_effects(patched_env):
    """
    Sanity: when no trigger conditions evaluate True, no side effects run.
    """
    fixture = _load("zero_trigger_cycle.json")
    assert fixture["expected_winner"] is None

    mock_db = patched_env["mock_db"]
    mock_sell = patched_env["mock_sell"]
    mock_db.load_state.return_value = _seed_state(
        tp_armed=False, above_tp_count=0, below_stop_count=0
    )

    with (
        patch.object(
            alpha_bot_execution.math_engine,
            "compute_exit_confirmation",
            return_value=(0, False),
        ),
        patch.object(
            alpha_bot_execution.math_engine,
            "compute_vwap_breakdown_update",
            return_value=(0, 0, False, False),
        ),
    ):
        alpha_bot_execution.main()

    assert mock_sell.call_count == fixture["side_effect_call_counts"]["sell_to_cash_total_calls"], (
        "Zero-trigger cycle must not call execute_sell_to_cash"
    )
    trigger_calls = _capture_record_exit_trigger_calls(mock_db)
    assert len(trigger_calls) == fixture["side_effect_call_counts"]["record_exit_trigger_calls"], (
        "Zero-trigger cycle must not call record_exit_trigger"
    )
    reason = _capture_triggered_reason(mock_db)
    assert reason is None, f"Zero-trigger cycle must not set triggered_reason; got {reason!r}"


# ---------------------------------------------------------------------------
# Determinism: same gate-state → same resolved trigger (no randomness)
# ---------------------------------------------------------------------------


def test_priority_resolution_is_deterministic(patched_env):
    """
    AC-H2.2 invariant: same trigger flags → same resolved trigger on repeated calls.
    Priority logic must be a pure function with no randomness.
    """
    mock_db = patched_env["mock_db"]

    results = []
    for _ in range(3):
        mock_db.load_state.return_value = _seed_state(
            tp_armed=True, above_tp_count=2, below_stop_count=3
        )
        mock_db.record_exit_trigger.reset_mock()
        mock_db.save_state.reset_mock()

        with (
            patch.object(
                alpha_bot_execution.math_engine,
                "compute_exit_confirmation",
                return_value=(3, True),
            ),
            patch.object(
                alpha_bot_execution.math_engine,
                "compute_vwap_breakdown_update",
                return_value=(3, 2, True, False),
            ),
        ):
            alpha_bot_execution.main()

        reason = _capture_triggered_reason(mock_db)
        results.append(reason)

    assert len(set(results)) == 1, (
        f"Priority resolution is non-deterministic: got different reasons across runs: {results}"
    )
    assert results[0] == "VWAP Breakdown", (
        f"Deterministic result must be VWAP Breakdown; got {results[0]!r}"
    )


# ---------------------------------------------------------------------------
# Side-effect count invariant: exactly one sell per symphony per cycle
# ---------------------------------------------------------------------------


def test_multi_trigger_dispatches_exactly_one_sell_to_cash_call(patched_env):
    """
    AC-H2.1: regardless of how many triggers fire simultaneously, only one
    execute_sell_to_cash call may be made per symphony per cycle.

    This pins the "side-effects execute regardless" bug described in AC-H2.1.
    The current code already only appends ONE item to execution_queue per symphony
    (the if/elif resolves one label). The test pins this invariant explicitly so
    any future change that adds multiple queue entries is caught.
    """
    mock_db = patched_env["mock_db"]
    mock_sell = patched_env["mock_sell"]
    mock_db.load_state.return_value = _seed_state(
        tp_armed=True, above_tp_count=2, below_stop_count=3
    )

    with (
        patch.object(
            alpha_bot_execution.math_engine,
            "compute_exit_confirmation",
            return_value=(3, True),
        ),
        patch.object(
            alpha_bot_execution.math_engine,
            "compute_vwap_breakdown_update",
            return_value=(3, 2, True, True),  # ALL four paths True
        ),
    ):
        alpha_bot_execution.main()

    assert mock_sell.call_count == 1, (
        f"Multi-trigger concurrent fire must dispatch exactly 1 sell_to_cash call; "
        f"got {mock_sell.call_count}. Duplicate side-effect dispatch detected."
    )
