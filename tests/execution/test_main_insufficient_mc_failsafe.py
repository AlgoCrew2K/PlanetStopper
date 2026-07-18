"""
AC-2 (math-audit HIGH) — RED tests for the insufficient-MC-history fail-safe
across the ``alpha_bot_execution.main()`` consumer gates.

SCOPE (team-lead Ruling 1 — Option A)
-------------------------------------
``run_monte_carlo`` now returns the out-of-band sentinel
``MC_INSUFFICIENT_HISTORY_SENTINEL`` (None) when MC history is insufficient.
``alpha_bot_execution.main()`` consumes ``prob_underperforming`` at six decision sites
that do unguarded numeric comparisons / format-spec formatting:

  * :1132 arm gate        — ``acc_TAKE_PROFIT_MC_PCT <= prob_underperforming < ...``
  * :1148 disarm gate     — ``prob_underperforming > (acc_TRIGGER_THRESHOLD_PCT * 2)``
  * :1249 TP-arm gate     — ``prob_underperforming < acc_TAKE_PROFIT_MC_PCT``
  * :1265 TP-confirm gate — ``prob_underperforming >= acc_TAKE_PROFIT_MC_PCT``
  * :1360 mc_sanity gate  — ``prob_underperforming >= acc_TRIGGER_THRESHOLD_PCT``
  * :1134/:1257/:1261/:1319 — ``f"...{prob_underperforming:.1f}%"`` format specs

Each raises ``TypeError`` on a ``None`` ``prob_underperforming``. A ``TypeError``
thrown mid-cycle aborts the execution cycle — on a below-stop position the
protective exit never runs. That is the exact fail-DANGEROUS path AC-2 exists
to eliminate.

THE FIX (team-lead Ruling 1)
----------------------------
Every consumer branches on ``prob_underperforming is None`` first. Insufficient MC =
the MC second opinion is absent: no arm, no disarm, no TP-arm, no TP-confirm,
no MC veto of the trailing stop. The trailing stop fires on its ticks-below-
stop condition alone. Format strings render None safely. ``mc_history`` is not
polluted with None.

WHAT THESE TESTS ASSERT
-----------------------
1. ``main()`` does NOT propagate a TypeError when MC history is insufficient.
2. No spurious ARM under the insufficient sentinel.
3. No spurious DISARM of an already-armed symphony under the sentinel.
4. No spurious TP-ARM under the sentinel.
5. THE FAIL-SAFE PROOF — an armed, below-stop symphony still triggers its
   trailing-stop exit when MC history is insufficient.
6. ``mc_history`` is not polluted with the None sentinel.

PROVENANCE / no-mock-the-math-engine
------------------------------------
``run_monte_carlo`` is NOT mocked. The harness feeds the engine a one-day
Alpaca history (the existing ``_make_minimal_history`` shape) which is genuinely
below MC_MIN_HISTORY_DAYS, so the REAL ``run_monte_carlo`` returns the
insufficient sentinel. Only network / clock / DB / Discord are mocked, per the
project test policy. No producer-computed value is hardcoded — assertions are
on state transitions and exception behaviour.

RED-VERIFICATION
----------------
All tests here are RED against the current GREEN-math/un-fixed-consumers code
(7ff61a4): main() raises TypeError at alpha_bot_execution.py:1132. They go
GREEN once the six consumer gates branch on ``is None``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import alpha_bot_execution
import math_engine

# ---------------------------------------------------------------------------
# Fixed clock — a regular-hours weekday so the time / blackout gates are bypassed.
# ---------------------------------------------------------------------------

try:
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
    _FIXED_ET = datetime(2025, 5, 14, 11, 30, 0, tzinfo=_ET)
except Exception:  # pragma: no cover - tzdata-less fallback
    _FIXED_ET = datetime(2025, 5, 14, 11, 30, 0, tzinfo=timezone(timedelta(hours=-4)))


# Shape-only identifiers — the engine only dict-key-matches these.
_SYMPHONY_ID = "sym-insufficient-mc-001"
_ACTUAL_SYMPHONY_ID = "actual-sym-insufficient-mc-001"
_ACCOUNT_ID = "acct-insufficient-mc-001"
_SYMPHONY_NAME = "Insufficient MC Failsafe Symphony"
_TICKER = "SPY"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _make_one_day_history(current_date_str: str) -> dict:
    """
    A one-day Alpaca-shaped history. One day is far below MC_MIN_HISTORY_DAYS,
    so the REAL run_monte_carlo returns the insufficient sentinel — the
    insufficient-MC path is exercised genuinely, not by mocking the math engine.
    """
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


def _make_symphony_payload(last_percent_change: float) -> dict:
    """Composer-shaped symphony dict for the fan-out loop."""
    return {
        "id": _SYMPHONY_ID,
        "symphony_id": _ACTUAL_SYMPHONY_ID,
        "name": _SYMPHONY_NAME,
        "last_percent_change": last_percent_change,
        "current_value": 10000.0,
        "holdings": [{"ticker": _TICKER, "allocation": 1.0}],
    }


def _make_vwap_payload(last_price: float) -> dict:
    return {_TICKER: {"vwap": last_price, "last_price": last_price}}


def _seed_state(armed: bool = False, hwm: float = 0.0, **extras) -> dict:
    """Minimal pre-seeded bot_state for the fixture symphony."""
    base = {
        "date": _FIXED_ET.strftime("%Y-%m-%d"),
        "last_execution_mode": False,
        _SYMPHONY_ID: {
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
            "breakeven_locked": False,
            "hwm_hold_ticks": 0,
        },
    }
    base[_SYMPHONY_ID].update(extras)
    return base


@pytest.fixture
def patched_environment():
    """
    Bundle every patch main()'s pipeline needs — network, clock, DB, Discord —
    leaving the math engine REAL. Yields a dict of mocks for per-test config.
    """
    with (
        patch.object(alpha_bot_execution, "database") as mock_db,
        patch.object(alpha_bot_execution, "reporting") as mock_reporting,
        patch.object(alpha_bot_execution, "fetch_symphony_stats") as mock_fetch_sym,
        patch.object(alpha_bot_execution, "fetch_alpaca_history") as mock_fetch_hist,
        patch.object(alpha_bot_execution, "fetch_intraday_vwaps") as mock_fetch_vwap,
        patch.object(alpha_bot_execution, "get_current_et", return_value=_FIXED_ET),
        patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", [_ACCOUNT_ID]),
        patch.object(alpha_bot_execution, "COMPOSER_KEY_ID", "test-composer-key"),
        patch.object(alpha_bot_execution, "ALPACA_KEY", "test-alpaca-key"),
        patch.object(alpha_bot_execution, "LIVE_EXECUTION", False),
        patch.object(alpha_bot_execution.time, "sleep"),
        patch.object(alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py"]),
    ):
        mock_db.acquire_lock.return_value = True
        mock_db.load_state.return_value = {}
        mock_db.load_chart_history.return_value = {
            "date": _FIXED_ET.strftime("%Y-%m-%d"),
            "symphonies": {},
        }
        mock_db.get_symphony_strategy.return_value = {"params": {}, "locked_vars": {}}
        mock_db.normalize_name.side_effect = lambda n: n.strip().lower()
        mock_db.wipe_transient_state.side_effect = lambda s: s

        mock_fetch_sym.return_value = []
        mock_fetch_hist.return_value = _make_one_day_history(_FIXED_ET.strftime("%Y-%m-%d"))
        mock_fetch_vwap.return_value = {}

        yield {
            "db": mock_db,
            "reporting": mock_reporting,
            "fetch_symphony_stats": mock_fetch_sym,
            "fetch_alpaca_history": mock_fetch_hist,
            "fetch_intraday_vwaps": mock_fetch_vwap,
        }


def _final_symphony_state(env) -> dict:
    """The symphony's state in the last save_state write."""
    save_calls = env["db"].save_state.call_args_list
    assert save_calls, "save_state was never called — main() aborted early."
    return save_calls[-1].args[0][_SYMPHONY_ID]


# ---------------------------------------------------------------------------
# Pre-condition: the harness genuinely exercises the insufficient-MC path
# ---------------------------------------------------------------------------


def test_harness_one_day_history_yields_insufficient_mc_sentinel() -> None:
    """
    Guard: the one-day history this module feeds main() must make the REAL
    run_monte_carlo return the insufficient sentinel. If this fails the rest of
    the module is not exercising the insufficient-MC path at all.
    """
    history = _make_one_day_history(_FIXED_ET.strftime("%Y-%m-%d"))
    holdings = [{"ticker": _TICKER, "allocation": 1.0, "last_percent_change": 0.0}]
    result = math_engine.run_monte_carlo(
        holdings, history, 0.1, simulation_paths=5000, neighbor_k=150, seed=42
    )
    assert result is math_engine.MC_INSUFFICIENT_HISTORY_SENTINEL, (
        f"The one-day harness history must drive run_monte_carlo to the "
        f"insufficient sentinel; got {result!r}. The AC-2 consumer-gate tests "
        f"depend on this."
    )


# ---------------------------------------------------------------------------
# 1. main() must not propagate a TypeError on the insufficient sentinel
# ---------------------------------------------------------------------------


def test_main_does_not_raise_typeerror_when_mc_history_insufficient(
    patched_environment,
) -> None:
    """
    AC-2 / Ruling 1. With MC history insufficient (run_monte_carlo -> None),
    main() must complete the cycle without an unhandled TypeError. Pre-fix the
    arm gate at alpha_bot_execution.py:1132 does ``acc_TAKE_PROFIT_MC_PCT <=
    prob_underperforming`` with prob_underperforming None and raises TypeError, aborting the
    cycle.

    RED against the un-fixed consumers (7ff61a4).
    """
    env = patched_environment
    env["fetch_symphony_stats"].return_value = [_make_symphony_payload(last_percent_change=0.03)]
    env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(500.0)
    env["db"].load_state.return_value = _seed_state(armed=False, hwm=0.0)

    try:
        alpha_bot_execution.main()
    except TypeError as exc:
        pytest.fail(
            f"main() propagated a TypeError when MC history was insufficient: "
            f"{exc}. The insufficient-MC sentinel (None) must be handled at "
            f"every prob_underperforming consumer — an unhandled TypeError aborts the "
            f"live cycle and, on a below-stop position, the protective exit "
            f"never runs (the fail-dangerous path AC-2 must eliminate)."
        )

    # The cycle must actually have run to completion (state written).
    save_calls = env["db"].save_state.call_args_list
    assert save_calls, (
        "save_state was never called — main() aborted before completing the "
        "cycle even though no TypeError surfaced."
    )


# ---------------------------------------------------------------------------
# 2. Fail-open ARM under the insufficient sentinel (audit H-3)
# ---------------------------------------------------------------------------


def test_insufficient_mc_arms_symphony_failopen(
    patched_environment,
) -> None:
    """
    Audit H-3 / FAIL-OPEN contract. When MC history is insufficient
    (prob_underperforming=None, mc_available=False), the protective trailing stop
    MUST arm so its ticks-below-stop confirmation ladder can fire.

    Pre-fix (broken): the arming gate required mc_available=True, so a
    permanently absent MC second opinion silently disabled the protective
    stop — contradicting the code's own comment that "the protective stop
    still fires on its ticks-below-stop condition alone."

    Post-fix (correct): mc_available=False triggers the fail-open arming
    path (`elif not mc_available: should_arm = True`). The symphony is
    armed with arm_reason="MC Absent (fail-open)". The EXIT_CONFIRM_TICKS
    ladder still gates actual liquidation, so a single absent tick cannot
    trigger a sale.
    """
    env = patched_environment
    # A modest positive return — the fail-open arm path does not require
    # the return to be below the stop level; arming is unconditional on
    # MC absence so the ticks-below-stop condition can later confirm.
    env["fetch_symphony_stats"].return_value = [_make_symphony_payload(last_percent_change=0.03)]
    env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(500.0)
    env["db"].load_state.return_value = _seed_state(armed=False, hwm=0.0)

    alpha_bot_execution.main()

    sym_state = _final_symphony_state(env)
    assert sym_state["armed"] is True, (
        "Symphony was NOT armed when MC history was insufficient. "
        "H-3 fail-open contract: absent MC must trigger arming so the "
        "protective stop can fire on its ticks-below-stop condition alone."
    )


# ---------------------------------------------------------------------------
# 3. No spurious DISARM of an already-armed symphony
# ---------------------------------------------------------------------------


def test_insufficient_mc_does_not_spuriously_disarm_symphony(
    patched_environment,
) -> None:
    """
    AC-2 / Ruling 1 + MA-4 (R3-b). With MC unavailable (prob_underperforming is None)
    an already-armed symphony must NOT be disarmed — the absent opinion cannot
    manufacture a recovery signal, and post-MA-4 the recovery-disarm
    (math_engine.compute_arm_disarm_decision) requires mc_available AND genuine
    recovery (prob < TAKE_PROFIT_MC_PCT). The symphony stays armed so its protective
    stop remains live. This drives the REAL main(), so it also guards that the
    extracted seam is wired into production for the MC-absent case.

    GREEN on base (MC-absent already could not trip the inverted disarm) and GREEN
    post-extraction (the seam's disarm requires mc_available) — a behavioral
    survival guard, not a RED test.
    """
    env = patched_environment
    # A positive return: under the OLD inverted disarm this satisfied its (now
    # deleted) ``current_return > 0`` half; post-MA-4 the return sign is irrelevant —
    # MC-absent alone keeps the stop armed (the disarm requires an available reading).
    env["fetch_symphony_stats"].return_value = [_make_symphony_payload(last_percent_change=0.05)]
    env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(500.0)
    env["db"].load_state.return_value = _seed_state(armed=True, hwm=10.0)

    alpha_bot_execution.main()

    sym_state = _final_symphony_state(env)
    assert sym_state["armed"] is True, (
        "An armed symphony was spuriously DISARMED while MC history was "
        "insufficient. The insufficient sentinel must not trip the disarm gate "
        "— disarming on absent data would silently drop the protective stop."
    )


# ---------------------------------------------------------------------------
# 4. No spurious TP-ARM under the insufficient sentinel
# ---------------------------------------------------------------------------


def test_insufficient_mc_does_not_spuriously_tp_arm_symphony(
    patched_environment,
) -> None:
    """
    AC-2 / Ruling 1. The take-profit arm gate (alpha_bot_execution.py:1249)
    fires when a real prob_underperforming is very low. With MC unavailable it must NOT
    TP-arm — an absent opinion is not an "exceptional gain" signal.

    RED against un-fixed consumers; GREEN once the TP-arm gate branches on
    ``is None``.
    """
    env = patched_environment
    env["fetch_symphony_stats"].return_value = [_make_symphony_payload(last_percent_change=0.08)]
    env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(500.0)
    env["db"].load_state.return_value = _seed_state(armed=False, hwm=0.0)

    alpha_bot_execution.main()

    sym_state = _final_symphony_state(env)
    assert sym_state["tp_armed"] is False, (
        "A symphony was spuriously TP-ARMED while MC history was insufficient. "
        "The insufficient sentinel must not trip the take-profit arm gate."
    )


# ---------------------------------------------------------------------------
# 5. THE FAIL-SAFE PROOF — armed, below-stop symphony still exits
# ---------------------------------------------------------------------------


def test_armed_below_stop_symphony_still_triggers_when_mc_insufficient(
    patched_environment,
) -> None:
    """
    AC-2 / Ruling 1 — THE fail-safe proof for the live engine.

    An armed, not-triggered symphony whose return is below its trailing stop
    must still fire the protective exit when MC history is insufficient. The
    insufficient sentinel must NOT veto the stop; the stop confirms on its
    ticks-below-stop condition alone.

    The symphony is seeded with below_stop_count = EXIT_CONFIRM_TICKS - 1 so a
    single qualifying tick this cycle reaches the confirmation threshold. A
    deeply negative return puts it unambiguously below any plausible stop
    level. compute_exit_confirmation is NOT mocked — the real fail-safe path
    (None prob_underperforming -> MC gate treated as passing) must carry the exit.

    Pre-fix, main() raises TypeError at the arm gate before exit-confirmation
    is even reached — RED. Post-fix the cycle completes and the symphony is
    triggered.
    """
    env = patched_environment
    # Deeply negative return — unambiguously below the trailing stop.
    env["fetch_symphony_stats"].return_value = [_make_symphony_payload(last_percent_change=-0.15)]
    env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(495.0)
    # Armed, one tick short of confirmation, HWM above the current return so the
    # stop sits above the current return.
    env["db"].load_state.return_value = _seed_state(
        armed=True,
        hwm=10.0,
        below_stop_count=math_engine.EXIT_CONFIRM_TICKS - 1,
    )

    alpha_bot_execution.main()

    sym_state = _final_symphony_state(env)
    assert sym_state["triggered"] is True, (
        "An armed, below-stop symphony did NOT trigger its protective exit "
        "when MC history was insufficient. AC-2: insufficient MC data must "
        "never disable the protective stop — the exit must fire on the "
        "ticks-below-stop condition alone."
    )


# ---------------------------------------------------------------------------
# 6. mc_history is not polluted with the None sentinel
# ---------------------------------------------------------------------------


def test_insufficient_mc_does_not_pollute_mc_history_with_none(
    patched_environment,
) -> None:
    """
    AC-2 / Ruling 1. ``bot_state[...]["mc_history"]`` is a rolling list of
    recent MC probabilities. The insufficient sentinel (None) must not be
    appended into it — a None entry there would break any later consumer that
    averages or compares the history.

    RED against un-fixed consumers (TypeError before this point); GREEN once
    the mc_history append branches on ``is None``.
    """
    env = patched_environment
    env["fetch_symphony_stats"].return_value = [_make_symphony_payload(last_percent_change=0.03)]
    env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(500.0)
    env["db"].load_state.return_value = _seed_state(armed=False, hwm=0.0)

    alpha_bot_execution.main()

    sym_state = _final_symphony_state(env)
    mc_history = sym_state.get("mc_history", [])
    assert None not in mc_history, (
        f"mc_history was polluted with the None insufficient sentinel: "
        f"{mc_history!r}. The insufficient sentinel must not be appended into "
        f"the rolling MC-probability history."
    )
