"""
End-to-end regression pins for ``alpha_bot_execution.main()``.

Audit context
-------------
``main()`` (~lines 276-830 in ``alpha_bot_execution.py``) is the highest-stakes
untested function in the engine. It owns:

  * Lock acquisition / release (single-writer invariant for live trading)
  * Per-cycle state load / save
  * High-Water-Mark (HWM) advancement on rising returns
  * True Shadow Return override for post-trigger symphonies
  * Exit-decision dispatch (trailing stop, TP, VWAP)
  * Execution queue construction
  * Post-trigger state freeze (``triggered_at_*`` + ``trigger_prices`` +
    ``triggered_basket_snapshot``)
  * Discord alert dispatch on trigger

These tests pin the **three most-critical pipeline scenarios** (Tier-A subset
per the audit's recommendation) against the CURRENT in-code behaviour as of
``task-b1-main-pipeline`` (forked from main @ e1391b3).

Out of scope for cycle B1 (deferred to B2+): EOD post-mortem branch, rebalance
blackout branch, autotuner kick-off, execution-mode toggle wipe, chart-history
chronological merge, multi-account / multi-symphony fan-out, LIVE_EXECUTION=True
end-to-end with ``execute_sell_to_cash`` success-then-state-freeze (covered
indirectly by ``test_execute_sell_to_cash.py`` for the inner call).

Mocking philosophy
------------------
* No real network — ``alpha_bot_execution.requests`` is fully stubbed via
  patches on ``fetch_symphony_stats``, ``fetch_alpaca_history``,
  ``fetch_intraday_vwaps``.
* No real sleeps — ``alpha_bot_execution.time.sleep`` is patched to no-op.
* No real I/O on the DB — ``alpha_bot_execution.database`` functions are
  mocked at the namespace level so we can both inject the seed bot_state
  AND assert that ``save_state`` / ``release_lock`` were/were not called.
* No real Discord — ``alpha_bot_execution.reporting`` is mocked.
* No real clock — ``alpha_bot_execution.get_current_et`` is patched to return
  a fixed weekday datetime during regular market hours so the time-gate /
  rebalance-blackout / post-mortem branches are bypassed.
* The math engine is NOT mocked wholesale: we let real math compute most
  values from the fixture, then surgically patch ``compute_exit_confirmation``
  in scenario #1 to force the trailing-stop-hit branch deterministically
  (otherwise we would have to author a fixture that reverse-engineers the
  exact MC / volatility / stop-distance math, and the test would be brittle).

Fixture-derivation rule
-----------------------
Per project memory ``feedback_no_hardcoded_test_values``, assertions touching
producer-computed values (returns, prices, HWM levels) are derived from the
fixture this test seeds — NEVER from literal expectations of math the engine
produced.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import alpha_bot_execution

# Try ZoneInfo for the fixed clock; fall back to a naive UTC-offset construction
# on systems that lack tzdata. The bot's time gates only care about wall-clock
# ET hours/minutes/weekday, so a naive datetime with the right values works.
try:
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
    _FIXED_ET = datetime(2025, 5, 14, 11, 30, 0, tzinfo=_ET)  # Wed 11:30 ET
except Exception:  # pragma: no cover -- tzdata missing
    _FIXED_ET = datetime(2025, 5, 14, 11, 30, 0, tzinfo=timezone(timedelta(hours=-4)))


# Sentinel test inputs. These are SHAPE-ONLY identifiers; the engine never
# inspects their literal content beyond dict-key matching.
_SYMPHONY_ID = "sym-pipeline-test-001"
_ACTUAL_SYMPHONY_ID = "actual-sym-pipeline-test-001"
_ACCOUNT_ID = "acct-pipeline-test-001"
_SYMPHONY_NAME = "Pipeline Test Symphony"
_TICKER = "SPY"


def _make_symphony_payload(last_percent_change: float):
    """Build a Composer-shaped symphony dict for the fan-out loop.

    Shape mirrors what ``fetch_symphony_stats`` returns in production: a list
    of dicts with ``id``, ``symphony_id``, ``name``, ``holdings``,
    ``last_percent_change``, ``current_value``. The fixture's
    ``last_percent_change`` is what ``main()`` reads into ``current_return``
    via ``sym.get("last_percent_change", 0.0) * 100``, so callers control the
    return-value scenario by tweaking this number alone.
    """
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


def _make_minimal_history(current_date_str: str):
    """Minimal Alpaca-shaped history dict so ``run_monte_carlo`` and
    ``calculate_20d_vol`` don't blow up. Two days of SPY bars + a current-day
    SPY entry for the macro print."""
    return {
        current_date_str: {
            "SPY": {"c": 500.0, "daily_ret": 0.001, "high": 501.0, "low": 499.0, "close": 500.0}
        }
    }


def _make_vwap_payload(last_price: float):
    """``fetch_intraday_vwaps`` returns ``{ticker: {"vwap": x, "last_price": y}}``."""
    return {_TICKER: {"vwap": last_price, "last_price": last_price}}


def _seed_state(armed: bool = False, hwm: float = 0.0, **extras):
    """Build a minimal pre-seeded bot_state dict for the fixture symphony.

    The shape matches what ``main()`` writes back to the symphony key on a
    fresh-bot first-tick (line ~488). Callers override individual keys via
    ``extras``.
    """
    base = {
        "date": _FIXED_ET.strftime("%Y-%m-%d"),
        "last_execution_mode": False,  # avoid the toggle-wipe branch
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
    """One-shot context manager bundling every patch the pipeline needs.

    Yields a dict of MagicMocks so individual tests can configure return
    values and assert call counts.

    Patches applied (in order they appear in ``main()`` execution path):
      * ``database.acquire_lock``               — gate at top
      * ``database.release_lock``               — finally-clause cleanup
      * ``database.load_state``                 — initial state read
      * ``database.save_state``                 — write-back (called multiple times)
      * ``database.load_chart_history``         — chart memory read
      * ``database.save_chart_history``         — chart write-back
      * ``database.get_symphony_strategy``      — per-symphony param lookup
      * ``database.wipe_transient_state``       — toggle / date-roll wipe
      * ``database.clear_symphony_logs``        — date-roll log wipe
      * ``database.log_symphony_event``         — event log writes
      * ``database.normalize_name``             — name-normalisation hop
      * ``reporting.send_discord_alert``        — trigger-time alert
      * ``reporting.generate_eod_snapshot``     — EOD report (rebalance/post-mortem only)
      * ``reporting.send_eod_discord_post``     — EOD discord post
      * ``fetch_symphony_stats``                — Composer fan-out
      * ``fetch_alpaca_history``                — Alpaca daily bars
      * ``fetch_intraday_vwaps``                — Alpaca minute VWAPs
      * ``get_current_et``                      — fixed clock
      * ``ACCOUNT_UUIDS``                       — non-empty so the loop runs
      * ``COMPOSER_KEY_ID`` / ``ALPACA_KEY``    — non-empty to pass the credentials gate
      * ``LIVE_EXECUTION``                      — False (default; tests override per-scenario)
      * ``time.sleep``                          — no-op (no real waits)
      * ``sys.argv``                            — bare argv so ``--force`` is absent
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
        # Defaults that downstream code expects to exist. Tests override as needed.
        mock_db.acquire_lock.return_value = True
        mock_db.load_state.return_value = {}
        mock_db.load_chart_history.return_value = {
            "date": _FIXED_ET.strftime("%Y-%m-%d"),
            "symphonies": {},
        }
        # live_mode=True: the patched_environment is shared by dry-run (pipeline)
        # and live-execution tests alike.  The master-switch gate
        # (LIVE_EXECUTION and item["live_mode"]) requires both flags set for real
        # trades to fire.  Dry-run tests override LIVE_EXECUTION=False, so
        # live_mode=True here has no effect on them; live-execution tests set
        # LIVE_EXECUTION=True and rely on live_mode=True to pass the gate.
        mock_db.get_symphony_strategy.return_value = {
            "params": {},
            "locked_vars": {},
            "live_mode": True,
        }
        mock_db.normalize_name.side_effect = lambda n: n.strip().lower()
        mock_db.wipe_transient_state.side_effect = lambda s: s  # identity

        mock_fetch_sym.return_value = []
        mock_fetch_hist.return_value = _make_minimal_history(_FIXED_ET.strftime("%Y-%m-%d"))
        mock_fetch_vwap.return_value = {}

        yield {
            "db": mock_db,
            "reporting": mock_reporting,
            "fetch_symphony_stats": mock_fetch_sym,
            "fetch_alpaca_history": mock_fetch_hist,
            "fetch_intraday_vwaps": mock_fetch_vwap,
        }


# =============================================================================
# Scenario 1 — Trigger fires → post-trigger state freeze is applied.
# =============================================================================
class TestTriggerFiresStateFreeze:
    """Pin the post-trigger state freeze.

    Setup: one armed symphony with a non-zero HWM. ``compute_exit_confirmation``
    is patched to return ``(3, True)`` so the trailing-stop-hit branch fires
    unconditionally. ``LIVE_EXECUTION=False`` means ``execute_sell_to_cash``
    is NOT invoked but the post-trigger state freeze IS — that is the
    high-value invariant (dry-run must still produce the audit trail).

    Expected end-state under freeze (alpha_bot_execution.py:778-805):
      * armed → False
      * triggered → True
      * triggered_reason → "Trailing Stop"
      * triggered_at_return populated from the fixture's last_percent_change
      * triggered_at_hwm / triggered_at_stop populated
      * trigger_prices populated from live_vwaps last_price
      * triggered_basket_snapshot populated with ticker+allocation+price
      * Discord exit alert sent with non-empty exit_reason
      * execute_sell_to_cash NOT called (dry-run gate)
    """

    def test_trigger_freezes_state_and_sends_alert(self, patched_environment):
        env = patched_environment

        # Fixture: symphony returns 12.0% (i.e. last_percent_change=0.12, scaled by 100
        # inside main → current_return == 12.0). Seed HWM ABOVE current return so
        # the HWM-rise branch (line 519) is skipped — keeps safe_hwm pinned to the
        # seeded value and decouples the trigger-freeze assertions from the HWM
        # update path covered separately in Scenario 2.
        fixture_pct_change = 0.12
        seed_hwm = 20.0  # above 12.0 so the rising-HWM branch does not fire
        live_price = 505.25  # SPY last price in the freeze snapshot
        date_str = _FIXED_ET.strftime("%Y-%m-%d")

        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=fixture_pct_change)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(live_price)
        env["db"].load_state.return_value = _seed_state(armed=True, hwm=seed_hwm)

        # Force the trailing-stop branch. We patch the math layer at the
        # ``alpha_bot_execution.math_engine`` import-site (which IS the same
        # module object as ``math_engine``); restore on context exit.
        with (
            patch.object(
                alpha_bot_execution.math_engine,
                "compute_exit_confirmation",
                return_value=(3, True),  # (new_below_stop_count, is_trailing_stop_hit)
            ),
            patch.object(alpha_bot_execution, "execute_sell_to_cash") as mock_execute,
        ):
            alpha_bot_execution.main()

        # ----- 1. State freeze invariants -----
        # The save_state call right after the execution-queue loop is the
        # POST-FREEZE write. Grab its argument.
        save_calls = env["db"].save_state.call_args_list
        assert save_calls, "save_state was never called — pipeline aborted early"
        final_state = save_calls[-1].args[0]
        sym_state = final_state[_SYMPHONY_ID]

        assert sym_state["triggered"] is True
        assert sym_state["armed"] is False
        assert sym_state["triggered_reason"] == "Trailing Stop"

        # Derived from fixture — current_return == last_percent_change * 100.
        expected_current_return = fixture_pct_change * 100.0
        assert sym_state["triggered_at_return"] == pytest.approx(
            expected_current_return,
            rel=1e-9,  # exact-float arithmetic; producer just multiplies by 100
        ), (
            "triggered_at_return must echo current_return at freeze instant; "
            "fixture-derived expected value"
        )

        # safe_hwm equals the seeded HWM (current_return < HWM here).
        assert sym_state["triggered_at_hwm"] == pytest.approx(seed_hwm, rel=1e-9)

        # triggered_at_stop is the "attempted_level" — for Trailing Stop that's
        # ``stop_trigger_level``. We don't pin the exact number (it comes from
        # the live math layers we let run); we pin its shape: a float that is
        # NOT the sentinel -999.0 default.
        assert isinstance(sym_state["triggered_at_stop"], float)
        assert sym_state["triggered_at_stop"] != -999.0

        # ----- 2. trigger_prices populated from live_vwaps -----
        assert _TICKER in sym_state["trigger_prices"]
        assert sym_state["trigger_prices"][_TICKER] == pytest.approx(live_price, rel=1e-9), (
            "trigger_price must equal the live_vwap last_price at freeze instant"
        )

        # ----- 3. triggered_basket_snapshot populated -----
        snapshot = sym_state["triggered_basket_snapshot"]
        assert isinstance(snapshot, list) and len(snapshot) == 1
        snap_row = snapshot[0]
        assert snap_row["ticker"] == _TICKER
        assert snap_row["allocation"] == pytest.approx(1.0, rel=1e-9)
        assert snap_row["price"] == pytest.approx(live_price, rel=1e-9)

        # ----- 4. Discord exit alert fired with non-empty reason -----
        env["reporting"].send_discord_alert.assert_called_once()
        alert_kwargs = env["reporting"].send_discord_alert.call_args.kwargs
        assert alert_kwargs.get("exit_reason"), "exit_reason kwarg must be non-empty"
        assert alert_kwargs["exit_reason"] == sym_state["triggered_reason"], (
            "Discord alert reason must match the state's triggered_reason"
        )

        # ----- 5. Dry-run mode: live executor NOT called -----
        mock_execute.assert_not_called()

        # ----- 6. Lock cycle: acquired AND released -----
        env["db"].acquire_lock.assert_called_once()
        env["db"].release_lock.assert_called_once()


# =============================================================================
# Scenario 2 — HWM advances before exit check; no false trigger.
# =============================================================================
class TestHwmUpdateAdvancesOnRisingReturn:
    """Pin that the HWM moves upward on a rising-return tick BEFORE any exit
    check runs. The exit-confirmation layer is patched to a no-op
    (``(0, False)``) so the only state change observed in this scenario is
    HWM advancement.

    Pre-condition (per alpha_bot_execution.py:519): HWM only advances if
    ``current_return > high_water_mark`` AND ``not triggered``. Seed HWM at
    1.0%; have the symphony report a 2.0% current return (via
    ``last_percent_change=0.02`` → ``current_return == 2.0``).

    Expected end-state:
      * bot_state[sym]["high_water_mark"] >= fixture-derived current_return
      * bot_state[sym]["triggered"] remains False
      * Discord exit alert NOT sent
    """

    def test_hwm_advances_to_at_least_current_return(self, patched_environment):
        env = patched_environment

        fixture_pct_change = 0.02
        seed_hwm = 1.0
        date_str = _FIXED_ET.strftime("%Y-%m-%d")

        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=fixture_pct_change)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(500.0)
        env["db"].load_state.return_value = _seed_state(armed=False, hwm=seed_hwm)

        # Suppress every exit pathway so we observe ONLY the HWM update.
        # compute_exit_confirmation → no trigger.
        # compute_vwap_breakdown_update → no VWAP break / bleed.
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

        save_calls = env["db"].save_state.call_args_list
        assert save_calls, "save_state was never called — pipeline aborted early"
        final_state = save_calls[-1].args[0]
        sym_state = final_state[_SYMPHONY_ID]

        expected_current_return = fixture_pct_change * 100.0  # fixture-derived

        # The HWM must have been pushed up to AT LEAST the current return
        # (line 519-520 sets it to current_return verbatim on the rise).
        assert sym_state["high_water_mark"] == pytest.approx(expected_current_return, rel=1e-9), (
            "HWM must advance to fixture-derived current_return; seeded HWM "
            f"({seed_hwm}) must have been overwritten by current_return "
            f"({expected_current_return})"
        )

        # No exit pathway should have fired.
        assert sym_state["triggered"] is False
        env["reporting"].send_discord_alert.assert_not_called()


# =============================================================================
# Scenario 3 — Lock not acquired: main() exits immediately, no side effects.
# =============================================================================
class TestLockNotAcquiredEarlyExit:
    """Pin the single-writer invariant.

    When ``acquire_lock()`` returns ``False`` (a concurrent invocation holds
    the lock), ``main()`` MUST:

      * Return cleanly (no exception)
      * NOT call ``release_lock()``  (early-return is BEFORE the try-block)
      * NOT call ``fetch_symphony_stats``  (nothing downstream runs)
      * NOT call ``load_state`` / ``save_state``  (no state mutation)
      * NOT send any Discord alert

    Regression sensitivity: this is the only thing standing between a
    minute-cadence cron and a double-trigger of ``execute_sell_to_cash`` in
    live mode. Breakage here ships duplicate sell orders to Composer.
    """

    def test_lock_denied_short_circuits_pipeline(self, patched_environment):
        env = patched_environment
        env["db"].acquire_lock.return_value = False

        # Must not raise.
        result = alpha_bot_execution.main()
        assert result is None  # function has no return value on this path

        # Lock was attempted exactly once.
        env["db"].acquire_lock.assert_called_once()

        # CRITICAL: release_lock NOT called — the early-return is BEFORE the
        # try/finally block (alpha_bot_execution.py:277-279). Pinning this
        # protects against a regression where someone wraps the lock check
        # inside the try-block and inadvertently releases a lock they don't
        # own.
        env["db"].release_lock.assert_not_called()

        # No downstream side effects.
        env["fetch_symphony_stats"].assert_not_called()
        env["fetch_alpaca_history"].assert_not_called()
        env["fetch_intraday_vwaps"].assert_not_called()
        env["db"].load_state.assert_not_called()
        env["db"].save_state.assert_not_called()
        env["reporting"].send_discord_alert.assert_not_called()
