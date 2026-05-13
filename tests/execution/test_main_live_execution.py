"""
End-to-end regression pins for ``alpha_bot_execution.main()`` with
``LIVE_EXECUTION=True`` — the highest-stakes production path (real-money
sell orders to Composer.trade).

Audit context
-------------
Cycle B1 (``test_main_pipeline.py``) pinned the DRY-RUN trigger path:
``LIVE_EXECUTION=False`` skips ``execute_sell_to_cash`` entirely (success
defaults to ``True``) and the post-trigger state freeze fires unconditionally.

This module pins the LIVE branch (``alpha_bot_execution.py`` lines 771-821):

  * Line 771: ``if LIVE_EXECUTION:`` → call ``execute_sell_to_cash``
  * Line 773: ``success = execute_sell_to_cash(actual_id, account)``
  * Line 778: ``if success:`` → state freeze + Discord alert
  * Line 820: ``else:`` → ``print(...)`` only; NO state mutation, NO Discord

Three scenarios cover the LIVE branch end-to-end:

  1. ``execute_sell_to_cash`` returns True   → state freeze + alert fire
  2. ``execute_sell_to_cash`` returns False  → NO state mutation, NO alert
  3. ``execute_sell_to_cash`` raises          → exception PROPAGATES (no try/except
                                                wraps the call in main()) but the
                                                outer ``finally:`` still releases
                                                the lock

Mocking philosophy
------------------
Identical to ``test_main_pipeline.py`` — we reuse its ``patched_environment``
fixture verbatim (imported from this module's sibling). Only difference: each
scenario flips ``LIVE_EXECUTION=True`` via a nested ``patch.object`` and
configures ``execute_sell_to_cash`` to the scenario's outcome.

Fixture-derivation rule
-----------------------
Per project memory ``feedback_no_hardcoded_test_values``, every assertion
touching producer-computed values (returns, prices, hwm) is derived from the
fixture this test seeds — never from literal expectations of math the engine
produced.

Real-money gap surfaced
-----------------------
``execute_sell_to_cash`` is NOT wrapped in try/except inside ``main()``. A
``requests.RequestException`` (HTTP timeout, connection reset, etc.) raised
mid-batch will:
  * Propagate up through the for-loop, abandoning any later items in the chunk.
  * Skip the outer ``database.save_state(bot_state)`` at line 823 — meaning
    in-memory mutations from the LOOP-LOCAL state freeze (lines 779-805) are
    LOST.
  * Skip ``database.save_chart_history(chart_history)`` at line 824.
  * Hit the ``finally:`` and release the lock cleanly.
  * Re-raise out of ``main()``.

This means a partial-chunk failure on a multi-symphony tick can leave the
state DB inconsistent (earlier loop iterations' mutations dropped because
``save_state`` never ran). This is a real-money concern: a Composer SELL
that DID execute on the wire but raised on the response read would lose
its ``triggered=True`` flag and re-fire on the next cycle. Flagged for PM.
Scenario 3 below pins the CURRENT behaviour so any future fix is a
deliberate, reviewed change rather than a silent regression.
"""

from unittest.mock import patch

import pytest
import requests

import alpha_bot_execution

# Reuse B1's harness. The fixture, helpers, and sentinel IDs are stable
# building blocks — duplicating them here would violate DRY and would mean
# the two test modules drift apart over time.
from tests.execution.test_main_pipeline import (
    patched_environment,           # noqa: F401  (re-exported pytest fixture)
    _seed_state,
    _make_symphony_payload,
    _make_vwap_payload,
    _SYMPHONY_ID,
    _ACTUAL_SYMPHONY_ID,
    _ACCOUNT_ID,
    _TICKER,
    _FIXED_ET,
)


# Common scenario seed values — identical to B1 scenario 1 (trigger-fires
# scenario) so any divergence between dry-run and live-run behaviour shows
# up as a test-level diff and not a hidden fixture difference.
_FIXTURE_PCT_CHANGE = 0.12      # → current_return == 12.0
_SEED_HWM = 20.0                # above current_return → HWM-rise branch skipped
_LIVE_PRICE = 505.25            # SPY last_price for the freeze snapshot


def _configure_trigger_scenario(env):
    """Inject the trigger-fires fixture into the shared harness.

    Mirrors B1 scenario 1's setup. Returns nothing — mutates env in place.
    """
    env["fetch_symphony_stats"].return_value = [
        _make_symphony_payload(last_percent_change=_FIXTURE_PCT_CHANGE)
    ]
    env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(_LIVE_PRICE)
    env["db"].load_state.return_value = _seed_state(armed=True, hwm=_SEED_HWM)


# =============================================================================
# Scenario 1 — LIVE_EXECUTION=True, execute_sell_to_cash returns True.
# =============================================================================
class TestLiveExecutionSuccessFreezesState:
    """Real-money path, happy case.

    Setup: B1 scenario-1 fixture, but with ``LIVE_EXECUTION=True`` and
    ``execute_sell_to_cash`` patched to return True (Composer accepted the
    sell-to-cash command).

    Expected end-state (alpha_bot_execution.py:771-819):
      * ``execute_sell_to_cash`` called exactly once with
        ``(actual_symphony_id, account_id)`` positional args
      * ``armed`` → False
      * ``triggered`` → True
      * ``triggered_reason`` populated
      * ``triggered_at_return`` echoes fixture-derived current_return
      * ``triggered_at_hwm`` == seeded HWM (current_return < HWM here)
      * ``trigger_prices`` populated from live_vwaps last_price
      * ``triggered_basket_snapshot`` populated with ticker / allocation / price
      * Discord exit alert sent
    """

    def test_live_success_path_invokes_executor_and_freezes_state(self, patched_environment):
        env = patched_environment
        _configure_trigger_scenario(env)

        with patch.object(alpha_bot_execution, "LIVE_EXECUTION", True), \
             patch.object(
                 alpha_bot_execution.math_engine,
                 "compute_exit_confirmation",
                 return_value=(3, True),  # force the trailing-stop-hit branch
             ), \
             patch.object(
                 alpha_bot_execution, "execute_sell_to_cash", return_value=True
             ) as mock_execute:
            alpha_bot_execution.main()

        # ----- 1. execute_sell_to_cash WAS invoked with the right args -----
        mock_execute.assert_called_once()
        call_args = mock_execute.call_args
        # Production passes (actual_id, account) positionally — pin that.
        assert call_args.args == (_ACTUAL_SYMPHONY_ID, _ACCOUNT_ID), (
            "execute_sell_to_cash must be called with "
            "(actual_symphony_id, account_id) — pinning prevents a regression "
            "where someone swaps args and silently sells the wrong portfolio."
        )

        # ----- 2. State freeze invariants -----
        save_calls = env["db"].save_state.call_args_list
        assert save_calls, "save_state was never called — pipeline aborted early"
        final_state = save_calls[-1].args[0]
        sym_state = final_state[_SYMPHONY_ID]

        assert sym_state["triggered"] is True
        assert sym_state["armed"] is False
        assert sym_state["triggered_reason"] == "Trailing Stop"

        # Derived from fixture — current_return == last_percent_change * 100.
        expected_current_return = _FIXTURE_PCT_CHANGE * 100.0
        assert sym_state["triggered_at_return"] == pytest.approx(
            expected_current_return,
            rel=1e-9,  # exact-float arithmetic; producer just multiplies by 100
        )

        # safe_hwm equals the seeded HWM (current_return < HWM here).
        assert sym_state["triggered_at_hwm"] == pytest.approx(_SEED_HWM, rel=1e-9)

        # ----- 3. trigger_prices populated from live_vwaps -----
        assert _TICKER in sym_state["trigger_prices"]
        assert sym_state["trigger_prices"][_TICKER] == pytest.approx(
            _LIVE_PRICE, rel=1e-9
        )

        # ----- 4. triggered_basket_snapshot populated -----
        snapshot = sym_state["triggered_basket_snapshot"]
        assert isinstance(snapshot, list) and len(snapshot) == 1
        snap_row = snapshot[0]
        assert snap_row["ticker"] == _TICKER
        assert snap_row["allocation"] == pytest.approx(1.0, rel=1e-9)
        assert snap_row["price"] == pytest.approx(_LIVE_PRICE, rel=1e-9)

        # ----- 5. Discord exit alert fired -----
        env["reporting"].send_discord_alert.assert_called_once()
        alert_kwargs = env["reporting"].send_discord_alert.call_args.kwargs
        assert alert_kwargs.get("exit_reason") == sym_state["triggered_reason"]

        # ----- 6. Lock acquired AND released -----
        env["db"].acquire_lock.assert_called_once()
        env["db"].release_lock.assert_called_once()


# =============================================================================
# Scenario 2 — LIVE_EXECUTION=True, execute_sell_to_cash returns False.
# =============================================================================
class TestLiveExecutionHttpFailureSkipsStateFreeze:
    """Real-money path, HTTP failure.

    Setup: B1 scenario-1 fixture, ``LIVE_EXECUTION=True``,
    ``execute_sell_to_cash`` returns False (Composer rejected the command;
    e.g. HTTP 4xx/5xx or non-JSON response — ``execute_sell_to_cash`` itself
    swallows ``requests.RequestException`` and returns False per its
    contract in ``alpha_bot_execution.py:98+``).

    Expected end-state (production behaviour at lines 778 ``if success:``
    and 820 ``else:``):
      * ``execute_sell_to_cash`` called exactly once (the attempt was made)
      * State mutations DO NOT apply for the trigger:
          - ``triggered`` remains False (the freeze block at line 781 never runs)
          - No ``triggered_reason``, ``triggered_at_return``,
            ``triggered_at_hwm``, ``triggered_at_stop``, ``trigger_prices``,
            or ``triggered_basket_snapshot`` keys written by the freeze block
          - HWM is NOT reset to the -999.0 sentinel (line 787 is in the
            success branch)
      * NOTE on ``armed``: the symphony's ``armed`` flag is NOT necessarily
        True at end-of-cycle. The arming-evaluation logic earlier in the cycle
        runs INDEPENDENTLY of the executor branch and may have toggled
        ``armed`` based on the current return / fixture. We therefore pin only
        the freeze-block invariants here. Pre-execution arming-state behaviour
        is covered separately.
      * Discord exit alert NOT sent (alert is INSIDE the ``if success:``
        block at line 811)
      * ``save_state`` IS still called at the end (line 823) — preserves
        any non-trigger updates earlier in the cycle (HWM advancement,
        below_stop_count increments, etc.)
      * Lock is released cleanly

    REAL-MONEY GAP (documented for PM): on Composer rejection, the symphony's
    ``armed`` state may have been independently set to False by the
    DISARMED (Conditions Recovered) branch earlier in the cycle — meaning a
    later tick on the SAME problem will NOT re-fire the trailing-stop logic
    unless arming conditions return. This is the current production
    behaviour and is intentional, but it means a transient Composer outage
    coinciding with a recovery move could leave the symphony un-protected
    for the rest of the day. Worth a follow-up cycle.
    """

    def test_live_failure_skips_state_freeze_and_alert(self, patched_environment):
        env = patched_environment
        _configure_trigger_scenario(env)

        with patch.object(alpha_bot_execution, "LIVE_EXECUTION", True), \
             patch.object(
                 alpha_bot_execution.math_engine,
                 "compute_exit_confirmation",
                 return_value=(3, True),  # force the trailing-stop-hit branch
             ), \
             patch.object(
                 alpha_bot_execution, "execute_sell_to_cash", return_value=False
             ) as mock_execute:
            alpha_bot_execution.main()

        # ----- 1. The attempt WAS made -----
        mock_execute.assert_called_once()
        assert mock_execute.call_args.args == (_ACTUAL_SYMPHONY_ID, _ACCOUNT_ID)

        # ----- 2. State freeze did NOT apply for the trigger -----
        save_calls = env["db"].save_state.call_args_list
        assert save_calls, "save_state was never called — pipeline aborted early"
        final_state = save_calls[-1].args[0]
        sym_state = final_state[_SYMPHONY_ID]

        # The freeze block at lines 779-805 is gated on the success branch.
        # On failure, only line 821 print() runs — no state mutation from
        # the executor. So ``triggered`` MUST remain at its seeded value
        # (False). ``armed`` may have been toggled by the upstream arming
        # evaluation independent of the executor outcome; we intentionally
        # do NOT pin armed here (see class docstring real-money gap note).
        assert sym_state["triggered"] is False, (
            "On execute_sell_to_cash failure, triggered MUST remain False — "
            "the state freeze at lines 779-805 is gated on the success branch."
        )

        # Freeze-only keys must NOT have been written. Use .get() (default
        # None) so a missing key reads as "freeze did not run" rather than
        # KeyError'ing.
        for freeze_key in (
            "triggered_reason",
            "triggered_at_return",
            "triggered_at_hwm",
            "triggered_at_stop",
            "triggered_at_time",
            "trigger_prices",
            "triggered_basket_snapshot",
        ):
            assert sym_state.get(freeze_key) in (None, "", {}, []), (
                f"Freeze-block key {freeze_key!r} must NOT be populated "
                f"when execute_sell_to_cash returns False; got "
                f"{sym_state.get(freeze_key)!r}"
            )

        # HWM must NOT have been reset to the -999.0 sentinel (line 787 is
        # inside the success branch). It should remain at the seeded value
        # (current_return < seed_hwm here, so no HWM rise either).
        assert sym_state["high_water_mark"] == pytest.approx(_SEED_HWM, rel=1e-9), (
            "HWM must remain at seeded value on failure — the -999.0 sentinel "
            "reset is INSIDE the success branch at line 787."
        )

        # ----- 3. Discord exit alert NOT sent -----
        env["reporting"].send_discord_alert.assert_not_called()

        # ----- 4. Lock released cleanly -----
        env["db"].acquire_lock.assert_called_once()
        env["db"].release_lock.assert_called_once()


# =============================================================================
# Scenario 3 — LIVE_EXECUTION=True, execute_sell_to_cash raises an exception.
# =============================================================================
class TestLiveExecutionExceptionPropagates:
    """Real-money path, unhandled exception.

    Setup: B1 scenario-1 fixture, ``LIVE_EXECUTION=True``,
    ``execute_sell_to_cash`` raises ``requests.RequestException``.

    Production behaviour (verified against alpha_bot_execution.py:771-827):
    there is NO try/except around the ``execute_sell_to_cash`` call at line
    773. An exception raised there will:
      * Abandon the execution-queue for-loop mid-batch.
      * Skip the outer ``database.save_state(bot_state)`` at line 823.
      * Skip the outer ``database.save_chart_history(...)`` at line 824.
      * Hit the ``finally:`` at line 826 → ``release_lock()`` runs.
      * Re-raise out of ``main()``.

    This pins the CURRENT behaviour. A future fix to catch the exception
    and proceed (so save_state still runs and partial-batch progress is
    persisted) will need to update this test deliberately.

    Real-money concern documented in module docstring above.
    """

    def test_live_exception_propagates_and_releases_lock(self, patched_environment):
        env = patched_environment
        _configure_trigger_scenario(env)

        with patch.object(alpha_bot_execution, "LIVE_EXECUTION", True), \
             patch.object(
                 alpha_bot_execution.math_engine,
                 "compute_exit_confirmation",
                 return_value=(3, True),  # force the trailing-stop-hit branch
             ), \
             patch.object(
                 alpha_bot_execution,
                 "execute_sell_to_cash",
                 side_effect=requests.RequestException("simulated HTTP failure"),
             ) as mock_execute:
            # Production has no try/except around execute_sell_to_cash, so
            # the exception MUST propagate. Pinning this prevents a silent
            # regression where a future swallow-all except hides Composer
            # outages from the operator.
            with pytest.raises(requests.RequestException, match="simulated HTTP failure"):
                alpha_bot_execution.main()

        # The attempt was made before the exception.
        mock_execute.assert_called_once()
        assert mock_execute.call_args.args == (_ACTUAL_SYMPHONY_ID, _ACCOUNT_ID)

        # CRITICAL invariant: the finally-clause MUST have released the lock,
        # otherwise the next minute-cadence tick would dead-lock the engine.
        env["db"].acquire_lock.assert_called_once()
        env["db"].release_lock.assert_called_once()

        # Discord alert is INSIDE the success branch → never fired.
        env["reporting"].send_discord_alert.assert_not_called()

        # save_state at the BOTTOM of the try-block (line 823) is unreachable
        # because the exception bypassed it. ``save_state`` MAY have been
        # called earlier in main() for the LIVE_EXECUTION-toggle wipe
        # (line 357) — that's expected and unrelated to the trigger freeze.
        # The invariant we pin is: whatever state WAS persisted does NOT
        # contain the post-freeze trigger fields (triggered=True, etc.).
        save_calls = env["db"].save_state.call_args_list
        if save_calls:
            last_persisted_state = save_calls[-1].args[0]
            last_sym_state = last_persisted_state.get(_SYMPHONY_ID, {})
            assert last_sym_state.get("triggered") is not True, (
                "An exception during execute_sell_to_cash MUST NOT result "
                "in a persisted triggered=True — the freeze block at lines "
                "779-805 is bypassed by the exception, and the final "
                "save_state at line 823 is unreachable. If a future refactor "
                "wraps the executor call in try/except and proceeds to "
                "freeze, update this test deliberately."
            )
            # And no freeze keys should have been written to a persisted state.
            for freeze_key in (
                "triggered_reason",
                "triggered_at_return",
                "trigger_prices",
                "triggered_basket_snapshot",
            ):
                assert not last_sym_state.get(freeze_key), (
                    f"Freeze-block key {freeze_key!r} MUST NOT be present in "
                    f"any persisted state when execute_sell_to_cash raises; "
                    f"got {last_sym_state.get(freeze_key)!r}"
                )
