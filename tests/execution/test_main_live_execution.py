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
  3. ``execute_sell_to_cash`` RAISES          → main() catches it, logs it,
                                                releases the lock, AND still
                                                executes the final
                                                ``save_state`` so any PRIOR
                                                successful iterations are
                                                persisted (was: re-raised /
                                                final save_state skipped).

  4. Multi-iteration loss-prevention (NEW):  two symphonies in the same tick,
                                              one succeeds and one raises —
                                              the success MUST be persisted
                                              and the exception MUST NOT
                                              propagate.

B1-FU1.1 — RED: wrap execute_sell_to_cash
------------------------------------------
B1-FU1 surfaced a HIGH-RISK real-money gap: ``execute_sell_to_cash`` is NOT
wrapped in try/except inside ``main()``. A ``requests.RequestException``
(HTTP timeout, connection reset, non-JSON response from a partial body, etc.)
raised mid-batch would:

  * Propagate up through the for-loop, abandoning later items in the chunk.
  * Skip the outer ``database.save_state(bot_state)`` at line 823 — meaning
    in-memory mutations from EARLIER successful iterations of the same loop
    (lines 779-805: ``triggered=True``, ``triggered_at_*``, ``trigger_prices``,
    snapshot, HWM reset to -999.0) are LOST.
  * Skip ``database.save_chart_history(chart_history)`` at line 824.
  * Hit the ``finally:`` and release the lock cleanly.
  * Re-raise out of ``main()``.

The real-money failure mode: Composer-confirmed SELL (the order DID land on
the wire) + lost ``triggered=True`` flag (state DB rolled back to pre-loop
because ``save_state`` never ran) → engine re-fires the same trigger on the
next minute-cadence tick → DOUBLE-CHARGE.

The correct behaviour (binding contract for the implementer):

  * ``main()`` MUST NOT propagate the exception (catch it at the call site).
  * State for the CURRENT-iteration symphony: ``triggered`` / ``armed`` left
    in pre-call state (the exception happened before the freeze block, so
    nothing should have committed).
  * State for PRIOR successful iterations in the same loop: MUST be
    preserved — the final ``save_state`` at line 823 MUST still execute.
  * Lock MUST be released (already correct via finally).
  * Exception MUST be logged (so an operator can investigate).

Scenarios 3 and 4 below pin this behaviour as RED tests against the current
(buggy) implementation. Both should FAIL until ``main()`` wraps the executor
call.

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
# Scenario 3 — LIVE_EXECUTION=True, execute_sell_to_cash RAISES.
#
# B1-FU1.1 RED: contract was "exception propagates + final save_state skipped".
# It is now: "exception is caught + final save_state still runs + earlier
# successful iterations are preserved + lock released + exception logged."
# =============================================================================
class TestLiveExecutionExceptionIsCaughtAndStateIsPreserved:
    """Real-money path, unhandled-in-executor exception.

    Setup: B1 scenario-1 fixture, ``LIVE_EXECUTION=True``,
    ``execute_sell_to_cash`` raises ``requests.RequestException`` (the same
    family ``execute_sell_to_cash`` itself catches internally — but for the
    sake of this test we simulate a defensive failure where the executor
    DOES leak an exception out, since e.g. a JSON decode error or a
    KeyError from a malformed response would NOT be caught by the
    executor's own narrow ``except`` clause).

    Required correct behaviour (binding contract — B1-FU1.1):

      * ``main()`` MUST NOT propagate the exception. The call site at
        ``alpha_bot_execution.py:773`` must be wrapped (try/except, journal
        pattern, or equivalent) so a single-symphony executor failure does
        NOT abandon the rest of the cycle.

      * The state for the symphony whose executor raised: ``triggered``
        remains False, ``armed`` left in pre-call state. The freeze block
        at lines 779-805 must NOT have run for it (the exception happened
        BEFORE commitment, so nothing should be persisted as "triggered").

      * The final ``database.save_state(bot_state)`` at line 823 MUST
        still execute. This is the load-bearing invariant: without it, any
        in-memory mutations from EARLIER successful iterations of the same
        chunk are lost.

      * The lock MUST be released (already correct via the ``finally:``).

      * The exception MUST be logged in some operator-visible way (so a
        Composer outage is investigable). We accept any of: a Discord
        error alert, a ``database.log_symphony_event`` event, a ``print``
        call to stdout/stderr, or a ``logging`` record. We don't pin the
        channel — only that SOMETHING was emitted.
    """

    def test_live_exception_does_not_propagate_lock_released_save_state_runs(
        self, patched_environment, capsys
    ):
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
            # The exception MUST NOT escape main(). Any escape is a regression
            # to the pre-FU1.1 behaviour and would re-introduce the
            # double-charge real-money risk.
            try:
                alpha_bot_execution.main()
            except BaseException as exc:  # pragma: no cover — only fires on regression
                pytest.fail(
                    f"main() must not propagate exceptions raised by "
                    f"execute_sell_to_cash; got {type(exc).__name__}: {exc}. "
                    f"Wrap the call site at alpha_bot_execution.py:~773 so a "
                    f"single-symphony executor failure cannot abandon the rest "
                    f"of the cycle (real-money: lost triggered=True → "
                    f"double-charge on next tick)."
                )

        # ----- 1. The attempt WAS made -----
        mock_execute.assert_called_once()
        assert mock_execute.call_args.args == (_ACTUAL_SYMPHONY_ID, _ACCOUNT_ID)

        # ----- 2. Lock acquired AND released (still must hold) -----
        env["db"].acquire_lock.assert_called_once()
        env["db"].release_lock.assert_called_once()

        # ----- 3. Final save_state at line 823 MUST have run -----
        # Before B1-FU1.1, the exception bypassed line 823 entirely and only
        # earlier in-cycle save_state calls (e.g. the LIVE_EXECUTION-toggle
        # wipe at line ~357) would have fired. Pin that the LAST save_state
        # call carries the post-loop bot_state, by asserting it contains the
        # fixture's symphony key (the toggle-wipe save would too, but the
        # invariant is "save_state ran AT LEAST once after the exception
        # site"). The simplest robust pin: save_state was called and the
        # final call's state dict contains our symphony key.
        save_calls = env["db"].save_state.call_args_list
        assert save_calls, (
            "save_state was never called — the final save_state at line 823 "
            "is unreachable because the exception bypassed it. This is the "
            "B1-FU1 bug: in-memory mutations from prior successful iterations "
            "would be lost."
        )
        last_persisted_state = save_calls[-1].args[0]
        assert _SYMPHONY_ID in last_persisted_state, (
            "Final save_state must persist the symphony's state. The fact "
            "that the symphony key is missing means the executor exception "
            "bypassed the post-loop save_state call."
        )

        # ----- 4. The failed-iteration symphony was NOT committed as triggered -----
        # The exception happened BEFORE the freeze block, so triggered must
        # remain False and no freeze-block keys may be set.
        last_sym_state = last_persisted_state[_SYMPHONY_ID]
        assert last_sym_state.get("triggered") is not True, (
            "An exception during execute_sell_to_cash MUST NOT result "
            "in a persisted triggered=True for the failed-iteration "
            "symphony — the freeze block at lines 779-805 is downstream of "
            "the executor call and must not have run."
        )
        for freeze_key in (
            "triggered_reason",
            "triggered_at_return",
            "trigger_prices",
            "triggered_basket_snapshot",
        ):
            assert not last_sym_state.get(freeze_key), (
                f"Freeze-block key {freeze_key!r} MUST NOT be present in "
                f"persisted state for the symphony whose executor raised; "
                f"got {last_sym_state.get(freeze_key)!r}"
            )

        # ----- 5. Discord exit alert NOT sent (the success-only alert) -----
        env["reporting"].send_discord_alert.assert_not_called()

        # ----- 6. The exception MUST be logged (operator visibility) -----
        # We accept ANY of: stdout/stderr text mentioning the failure,
        # a database.log_symphony_event call, or a logging record. We don't
        # pin the channel — just that the operator can find out something
        # went wrong. The captured stdout from capsys is the simplest path.
        captured = capsys.readouterr()
        combined_text = (captured.out or "") + (captured.err or "")
        log_event_calls = env["db"].log_symphony_event.call_args_list
        logged_via_event = any(
            "simulated HTTP failure" in repr(c)
            or "RequestException" in repr(c)
            or "exception" in repr(c).lower()
            or "fail" in repr(c).lower()
            for c in log_event_calls
        )
        logged_via_stdio = (
            "simulated HTTP failure" in combined_text
            or "RequestException" in combined_text
            or "Exception" in combined_text
            or "EXECUTION FAILED" in combined_text
        )
        assert logged_via_event or logged_via_stdio, (
            "Executor exceptions MUST be logged in an operator-visible way "
            "(Discord error alert, database.log_symphony_event, print, or "
            "logging). No record of the simulated failure was found in "
            "stdout/stderr or in log_symphony_event call args. Without a "
            "log, a Composer outage is silently swallowed."
        )


# =============================================================================
# Scenario 4 — Multi-iteration loss prevention (NEW for B1-FU1.1).
#
# Two symphonies in the same tick, BOTH trigger. Symphony A's executor
# succeeds; Symphony B's executor raises. The required behaviour is:
#
#   * Symphony A's success is committed (its triggered=True is in the FINAL
#     persisted state).
#   * Symphony B remains un-triggered (the exception happened before its
#     freeze block).
#   * No exception propagates out of main().
#   * The final save_state at line 823 runs (this is how A's success becomes
#     durable; without the wrap, A's mutation would be in-memory only and
#     lost when the exception unwound through line 823).
# =============================================================================

# Distinct sentinel IDs for symphony B. Same shape as test_main_pipeline's
# constants, deliberately suffixed so the two symphonies fan out into TWO
# separate execution-queue items inside main().
_SYMPHONY_ID_B = "sym-pipeline-test-002"
_ACTUAL_SYMPHONY_ID_B = "actual-sym-pipeline-test-002"
_SYMPHONY_NAME_B = "Pipeline Test Symphony B"


def _make_symphony_payload_b(last_percent_change: float):
    """Composer-shaped payload for symphony B (distinct id from the default).

    Shape mirrors ``_make_symphony_payload`` from ``test_main_pipeline``; only
    the ids/name differ so the engine sees two distinct symphonies.
    """
    return {
        "id": _SYMPHONY_ID_B,
        "symphony_id": _ACTUAL_SYMPHONY_ID_B,
        "name": _SYMPHONY_NAME_B,
        "last_percent_change": last_percent_change,
        "current_value": 10000.0,
        "holdings": [
            {"ticker": _TICKER, "allocation": 1.0},
        ],
    }


def _seed_two_symphonies(armed: bool, hwm: float):
    """Build a bot_state seeded with two distinct symphonies, both armed.

    Mirrors ``_seed_state`` from ``test_main_pipeline`` but adds the second
    symphony key. Returned state has BOTH _SYMPHONY_ID and _SYMPHONY_ID_B at
    the top level.
    """
    def _per_sym(armed, hwm):
        return {
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
        }

    return {
        "date": _FIXED_ET.strftime("%Y-%m-%d"),
        "last_execution_mode": False,  # avoid the toggle-wipe branch
        _SYMPHONY_ID: _per_sym(armed, hwm),
        _SYMPHONY_ID_B: _per_sym(armed, hwm),
    }


class TestLiveExecutionMultiSymphonyPartialFailurePreservesEarlierSuccess:
    """Two-symphony tick: A succeeds, B raises. A's commit must survive.

    This is the load-bearing real-money invariant of B1-FU1.1. Pre-fix, B's
    ``RequestException`` propagated out of the executor for-loop, skipped the
    final ``save_state`` at line 823, and rolled the in-memory ``triggered=True``
    flag for A out of existence. A was actually sold to cash by Composer (the
    HTTP call returned 200 from A's perspective), but the engine "forgot"
    that — and on the next minute tick, A would be re-evaluated as still
    armed-and-not-triggered, and the engine would issue a SECOND sell-to-cash
    for A → double-charge.

    Post-fix:
      * main() catches B's exception, logs it, continues past the failed
        iteration (or breaks the loop cleanly), and reaches the final
        save_state at line 823 with A's freeze block already applied.
      * A is durably persisted as triggered.
      * B is persisted in its pre-call state (triggered=False).
    """

    def test_a_succeeds_b_raises_a_is_preserved_no_exception_propagates(
        self, patched_environment
    ):
        env = patched_environment

        # Both symphonies report the same fixture-derived current_return so
        # both will be evaluated identically downstream. compute_exit_confirmation
        # is patched globally below to force the trailing-stop branch for both.
        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=_FIXTURE_PCT_CHANGE),
            _make_symphony_payload_b(last_percent_change=_FIXTURE_PCT_CHANGE),
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(_LIVE_PRICE)
        env["db"].load_state.return_value = _seed_two_symphonies(
            armed=True, hwm=_SEED_HWM
        )

        # Per-call dispatch: A's actual_id returns True; B's actual_id raises.
        # We don't care about call order in the chunk loop — the production
        # code preserves insertion order from ``execution_queue.append`` and
        # the for-loop iterates in that order, so A is invoked first because
        # ``fetch_symphony_stats`` returned A then B. Either order is fine
        # for this assertion set, but A-before-B is the more interesting
        # real-money scenario (A commits, then B blows up — A must survive).
        def _executor_side_effect(actual_id, account):
            if actual_id == _ACTUAL_SYMPHONY_ID:
                return True
            if actual_id == _ACTUAL_SYMPHONY_ID_B:
                raise requests.RequestException("simulated HTTP failure for B")
            # Anything else is a fixture wiring bug.
            raise AssertionError(
                f"executor invoked with unexpected actual_id={actual_id!r}; "
                f"fixture expected {_ACTUAL_SYMPHONY_ID!r} or "
                f"{_ACTUAL_SYMPHONY_ID_B!r}"
            )

        with patch.object(alpha_bot_execution, "LIVE_EXECUTION", True), \
             patch.object(
                 alpha_bot_execution.math_engine,
                 "compute_exit_confirmation",
                 return_value=(3, True),
             ), \
             patch.object(
                 alpha_bot_execution,
                 "execute_sell_to_cash",
                 side_effect=_executor_side_effect,
             ) as mock_execute:
            try:
                alpha_bot_execution.main()
            except BaseException as exc:  # pragma: no cover — only fires on regression
                pytest.fail(
                    f"main() must not propagate B's executor exception; "
                    f"got {type(exc).__name__}: {exc}. The pre-FU1.1 bug was "
                    f"exactly this: B's exception unwound through the chunk "
                    f"loop and rolled A's in-memory triggered=True out of "
                    f"existence (next-tick double-charge for A)."
                )

        # ----- 1. Both executors were attempted -----
        # (A first then B per the fan-out order; if B raises before A is even
        # called, that's a different bug, but the assertion below catches it.)
        called_actual_ids = [c.args[0] for c in mock_execute.call_args_list]
        assert _ACTUAL_SYMPHONY_ID in called_actual_ids, (
            "Symphony A's executor was never called — the chunk loop must "
            "iterate both queued items."
        )
        assert _ACTUAL_SYMPHONY_ID_B in called_actual_ids, (
            "Symphony B's executor was never called — A's success must not "
            "short-circuit the rest of the chunk."
        )

        # ----- 2. Lock acquired AND released -----
        env["db"].acquire_lock.assert_called_once()
        env["db"].release_lock.assert_called_once()

        # ----- 3. Final save_state ran AND contains A's freeze -----
        save_calls = env["db"].save_state.call_args_list
        assert save_calls, (
            "save_state was never called — the final save_state at line 823 "
            "is unreachable because B's exception bypassed it. A's "
            "triggered=True is lost; next-tick double-charge for A."
        )
        last_persisted_state = save_calls[-1].args[0]
        assert _SYMPHONY_ID in last_persisted_state, (
            "Final save_state must persist symphony A's state."
        )
        assert _SYMPHONY_ID_B in last_persisted_state, (
            "Final save_state must persist symphony B's state too."
        )

        # ----- 4. Symphony A's success is DURABLE -----
        sym_a_state = last_persisted_state[_SYMPHONY_ID]
        assert sym_a_state.get("triggered") is True, (
            "Symphony A's triggered=True MUST be in the final persisted "
            "state. The pre-FU1.1 bug: B's exception bypassed line 823, "
            "rolling A's in-memory mutation out of existence — Composer had "
            "already executed the SELL but the engine forgot, and re-fired "
            "next minute → DOUBLE-CHARGE on A."
        )
        assert sym_a_state.get("armed") is False, (
            "Symphony A must end un-armed (armed flipped to False inside "
            "the success branch at line 779)."
        )
        # Fixture-derived: A's triggered_at_return echoes current_return.
        expected_a_return = _FIXTURE_PCT_CHANGE * 100.0
        assert sym_a_state.get("triggered_at_return") == pytest.approx(
            expected_a_return, rel=1e-9
        ), "A's triggered_at_return must be derived from the fixture."
        assert sym_a_state.get("triggered_reason"), (
            "A's triggered_reason must be set by the freeze block."
        )
        # A's trigger_prices snapshot uses the live_vwap last_price.
        assert sym_a_state.get("trigger_prices", {}).get(_TICKER) == pytest.approx(
            _LIVE_PRICE, rel=1e-9
        ), "A's trigger_prices must be populated from live_vwaps."

        # ----- 5. Symphony B was NOT committed as triggered -----
        sym_b_state = last_persisted_state[_SYMPHONY_ID_B]
        assert sym_b_state.get("triggered") is not True, (
            "Symphony B's executor raised BEFORE the freeze block — "
            "triggered MUST remain False in the persisted state."
        )
        for freeze_key in (
            "triggered_reason",
            "triggered_at_return",
            "trigger_prices",
            "triggered_basket_snapshot",
        ):
            assert not sym_b_state.get(freeze_key), (
                f"Symphony B's freeze-block key {freeze_key!r} MUST NOT be "
                f"present in the persisted state; got "
                f"{sym_b_state.get(freeze_key)!r}"
            )

        # ----- 6. Discord alert fired exactly once — for A only -----
        alert_calls = env["reporting"].send_discord_alert.call_args_list
        assert len(alert_calls) == 1, (
            f"Expected exactly one Discord exit alert (for A's success); "
            f"got {len(alert_calls)}. B's failure must NOT produce a "
            f"success-channel exit alert."
        )
        alert_args = alert_calls[0].args
        # The first positional arg to send_discord_alert is the symphony_name.
        # Pin that it's A's name, not B's — proves A's freeze block (which
        # immediately precedes the Discord call at line 811) actually ran.
        assert alert_args and alert_args[0] != _SYMPHONY_NAME_B, (
            "The single Discord exit alert must NOT be for symphony B "
            "(B's executor raised before its freeze/alert block)."
        )
