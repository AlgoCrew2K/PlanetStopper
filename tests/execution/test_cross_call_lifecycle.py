"""
Cross-call (lifecycle) invariant regression pins for
``alpha_bot_execution.main()``.

Audit context
-------------
The math-engine invariant audit (``docs/research/math_engine/invariant-audit__2026-05-13.md``)
identified six cross-call invariants (CX-1 through CX-6) that span MULTIPLE
calls to ``main()`` over a position's lifetime. They live in
``alpha_bot_execution.py`` and have NO test coverage in ``tests/math_engine/``
because pure math-function tests are stateless by construction.

This module pins the three highest-value cross-call invariants per the
audit's prioritisation:

  * **CX-2 — `safe_hwm >= current_return` at every decision point.**
    Drive ``main()`` across a sequence of ticks with rising-then-receding
    returns. Assert the HWM is monotonically non-decreasing and is always
    at or above the current tick's return.
    Audit reference: section "CX-2", H7. Cited literature: Fu & Zhang
    (2010) — the "HWM never retraces" property is part of the standard
    trailing-stop contract.

  * **CX-3 — `breakeven_locked=True` survives the state round-trip.**
    Drive ``main()`` to lock breakeven on tick N. Re-enter ``main()`` on
    tick N+1 using the same ``bot_state`` dict. Assert the math engine
    was called with ``currently_breakeven_locked=True`` on tick N+1 —
    i.e. the caller wired the latched value back through correctly.
    Audit reference: section "CX-3". This is the caller-wiring half of
    the latching contract whose math-side half is pinned in
    ``tests/math_engine/test_breakeven_update.py``.

  * **CX-1 — Trigger state freeze is atomic.**
    Once an exit fires, ALL ``triggered_*`` fields (``triggered``,
    ``triggered_reason``, ``triggered_at_return``, ``triggered_at_hwm``,
    ``triggered_at_stop``, ``triggered_at_time``, ``trigger_prices``,
    ``triggered_basket_snapshot``, ``armed=False``, ``tp_armed=False``,
    ``high_water_mark=-999.0``) must appear together in the single
    ``save_state`` call at the end of the cycle — never in a partial
    state where some fields are set and others are not. Pin via
    ``save_state.call_args_list`` introspection.
    Audit reference: section "CX-1", referenced from
    alpha_bot_execution.py:778-805.

Mocking strategy
----------------
Reuses the ``patched_environment`` 17-surface harness pattern from
``tests/execution/test_main_pipeline.py`` (B1). For multi-tick scenarios
this module re-applies the patches PER TICK via a context manager helper
so that ``bot_state`` (the only object that must survive across ticks) is
mutated in-place between calls while every other side-effect surface is
freshly stubbed.

Fixture-derivation rule
-----------------------
Per project memory ``feedback_no_hardcoded_test_values``, every
expected-value assertion in this module derives from the fixture inputs
(returns, prices, HWM levels) — never from literal expectations of math
the engine produced. Magnitudes that flow through the live math layers
(stop_trigger_level, prob_beating, symphony_vol) are asserted on SHAPE
(float, finite, not the -999.0 sentinel) — not on value.
"""

from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

import alpha_bot_execution


# --------------------------------------------------------------------------- #
# Shared constants (mirror test_main_pipeline.py's shape-only identifiers)    #
# --------------------------------------------------------------------------- #
try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
    _FIXED_ET = datetime(2025, 5, 14, 11, 30, 0, tzinfo=_ET)  # Wed 11:30 ET
except Exception:  # pragma: no cover -- tzdata missing
    _FIXED_ET = datetime(2025, 5, 14, 11, 30, 0, tzinfo=timezone(timedelta(hours=-4)))

_SYMPHONY_ID = "sym-lifecycle-test-001"
_ACTUAL_SYMPHONY_ID = "actual-sym-lifecycle-test-001"
_ACCOUNT_ID = "acct-lifecycle-test-001"
_SYMPHONY_NAME = "Lifecycle Test Symphony"
_TICKER = "SPY"


# --------------------------------------------------------------------------- #
# Fixture builders                                                            #
# --------------------------------------------------------------------------- #
def _make_symphony_payload(last_percent_change: float):
    """Composer-shaped symphony dict. ``last_percent_change`` is multiplied
    by 100 inside ``main()`` to yield ``current_return``."""
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
    """Minimal Alpaca-shaped history dict so math layers don't blow up."""
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


def _make_vwap_payload(last_price: float):
    """``fetch_intraday_vwaps`` payload shape."""
    return {_TICKER: {"vwap": last_price, "last_price": last_price}}


def _seed_state(armed: bool = False, hwm: float = 0.0, **extras):
    """Build a bot_state dict shaped like main()'s first-tick write."""
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


@contextmanager
def _patched_main_env(initial_bot_state, pct_change, live_price=500.0):
    """Per-tick patch bundle for ``main()``.

    Differs from test_main_pipeline.py's fixture in that ``load_state`` is
    wired to return the SAME dict reference each call so multi-tick tests
    can mutate ``bot_state`` between ``main()`` invocations and observe
    the next-tick read. Yields the MagicMock bundle.
    """
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
        # CRITICAL for multi-tick: return the same dict reference so the
        # tests can mutate bot_state between calls and observe the next
        # tick's read of the mutated state.
        mock_db.load_state.return_value = initial_bot_state
        mock_db.load_chart_history.return_value = {
            "date": _FIXED_ET.strftime("%Y-%m-%d"), "symphonies": {}
        }
        mock_db.get_symphony_strategy.return_value = {"params": {}, "locked_vars": {}}
        mock_db.normalize_name.side_effect = lambda n: n.strip().lower()
        mock_db.wipe_transient_state.side_effect = lambda s: s

        mock_fetch_sym.return_value = [
            _make_symphony_payload(last_percent_change=pct_change)
        ]
        mock_fetch_hist.return_value = _make_minimal_history(
            _FIXED_ET.strftime("%Y-%m-%d")
        )
        mock_fetch_vwap.return_value = _make_vwap_payload(live_price)

        yield {
            "db": mock_db,
            "reporting": mock_reporting,
            "fetch_symphony_stats": mock_fetch_sym,
            "fetch_alpaca_history": mock_fetch_hist,
            "fetch_intraday_vwaps": mock_fetch_vwap,
        }


# =============================================================================
# CX-2 — safe_hwm >= current_return at every decision point.
# =============================================================================
class TestHwmMonotonicityAcrossTicks:
    """Pin ``high_water_mark`` monotonic non-decrease across consecutive
    ``main()`` invocations sharing the same ``bot_state``.

    Drives 5 sequential ticks with fixture-derived ``last_percent_change``
    values ``[0.01, 0.03, 0.025, 0.04, 0.035]`` → ``current_return``
    sequence ``[1.0, 3.0, 2.5, 4.0, 3.5]``.

    Expected HWM evolution (per alpha_bot_execution.py:519 — HWM rises iff
    ``current_return > high_water_mark AND not triggered``):

      Tick | current_return | HWM_in | HWM_out | invariant: HWM_out >= cr
        1  |      1.0       |  0.0   |   1.0   |       1.0 >= 1.0  ✓
        2  |      3.0       |  1.0   |   3.0   |       3.0 >= 3.0  ✓
        3  |      2.5       |  3.0   |   3.0   |       3.0 >= 2.5  ✓
        4  |      4.0       |  3.0   |   4.0   |       4.0 >= 4.0  ✓
        5  |      3.5       |  4.0   |   4.0   |       4.0 >= 3.5  ✓

    Audit reference: CX-2 (HIGH risk). Production gap surfaced if any
    tick's HWM_out < HWM_in (i.e. HWM retraces).
    """

    # Fixture-derived input sequence. Each row IS the test parameterisation.
    TICK_SEQUENCE = [
        # (last_percent_change, expected_current_return, expected_hwm_after_tick)
        (0.01, 1.0, 1.0),
        (0.03, 3.0, 3.0),
        (0.025, 2.5, 3.0),  # HWM holds because cr < HWM
        (0.04, 4.0, 4.0),
        (0.035, 3.5, 4.0),  # HWM holds again
    ]

    def test_hwm_is_non_decreasing_across_consecutive_ticks(self):
        # bot_state is THE object that survives across ticks.
        bot_state = _seed_state(armed=False, hwm=0.0)

        observed_hwms = []
        previous_hwm = bot_state[_SYMPHONY_ID]["high_water_mark"]

        for tick_idx, (pct_change, expected_cr, expected_hwm) in enumerate(
            self.TICK_SEQUENCE, start=1
        ):
            # Each tick re-applies patches but keeps the SAME bot_state dict.
            with _patched_main_env(bot_state, pct_change) as env:
                # Suppress every exit pathway so the only state change is
                # HWM (and the routine prev_return / vwap_ticks bookkeeping).
                with patch.object(
                    alpha_bot_execution.math_engine,
                    "compute_exit_confirmation",
                    return_value=(0, False),
                ), patch.object(
                    alpha_bot_execution.math_engine,
                    "compute_vwap_breakdown_update",
                    return_value=(0, 0, False, False),
                ):
                    alpha_bot_execution.main()

                save_calls = env["db"].save_state.call_args_list
                assert save_calls, (
                    f"tick {tick_idx}: save_state never called; "
                    "pipeline aborted early"
                )
                final_state = save_calls[-1].args[0]
                sym_state = final_state[_SYMPHONY_ID]

                current_hwm = sym_state["high_water_mark"]
                observed_hwms.append(current_hwm)

                # --- CX-2 core invariant: HWM >= current_return ---
                assert current_hwm >= expected_cr - 1e-9, (
                    f"tick {tick_idx}: CX-2 violation — HWM ({current_hwm}) "
                    f"< current_return ({expected_cr}). The high-water-mark "
                    "must by definition equal or exceed any return seen so "
                    "far."
                )

                # --- HWM monotonic non-decrease across ticks ---
                assert current_hwm >= previous_hwm - 1e-9, (
                    f"tick {tick_idx}: HWM retraced from {previous_hwm} "
                    f"to {current_hwm}. Fu & Zhang (2010) trailing-stop "
                    "contract: HWM is monotone non-decreasing while "
                    "not triggered."
                )

                # --- Fixture-derived expected HWM at this tick ---
                # expected_hwm is derived from the fixture sequence above
                # (max of all prior current_returns up to and including
                # this tick); not a hardcoded producer value.
                assert current_hwm == pytest.approx(expected_hwm, rel=1e-9), (
                    f"tick {tick_idx}: expected HWM = max(prior returns) "
                    f"= {expected_hwm}, observed {current_hwm}"
                )

                # Position should never have triggered in this scenario.
                assert sym_state["triggered"] is False, (
                    f"tick {tick_idx}: unexpected trigger; this scenario "
                    "should never fire an exit (exit_confirmation patched "
                    "to (0, False))."
                )

                previous_hwm = current_hwm

        # Sanity: we observed exactly TICK_SEQUENCE-many HWMs and the
        # final HWM equals max(all current_returns).
        assert len(observed_hwms) == len(self.TICK_SEQUENCE)
        expected_final_hwm = max(cr for (_, cr, _) in self.TICK_SEQUENCE)
        assert observed_hwms[-1] == pytest.approx(expected_final_hwm, rel=1e-9), (
            f"final HWM should equal max(current_returns) = "
            f"{expected_final_hwm}; observed {observed_hwms[-1]}"
        )


# =============================================================================
# CX-3 — breakeven_locked persists through state round-trip.
# =============================================================================
class TestBreakevenLockedPersistsAcrossTicks:
    """Pin that ``breakeven_locked=True`` written by tick N's math call
    survives the ``bot_state`` round-trip and is passed back to the math
    engine on tick N+1 as ``currently_breakeven_locked=True``.

    Setup
    -----
    Tick 1: patch ``compute_breakeven_update`` to return ``(5, True, ...)``.
    The caller writes ``new_breakeven_locked`` back to
    ``bot_state[sym]["breakeven_locked"]`` (alpha_bot_execution.py:603).

    Tick 2: re-enter ``main()`` with the SAME ``bot_state`` dict. The math
    engine is mocked again, and its call_args is introspected to verify
    that ``currently_breakeven_locked=True`` was passed in.

    Audit reference: CX-3 (HIGH risk). This pins the caller wiring half
    of the latching contract; the math-side half (locked-stays-locked
    when called with currently_locked=True) is pinned in
    ``tests/math_engine/test_breakeven_update.py``.
    """

    def test_locked_flag_round_trips_into_next_tick_math_call(self):
        bot_state = _seed_state(armed=True, hwm=2.0)

        # Use any fixture-derived return; the exact value is irrelevant
        # because we are mocking the breakeven update entirely.
        pct_change = 0.05  # → current_return == 5.0

        # ---------------- Tick 1: math returns locked=True ----------------
        with _patched_main_env(bot_state, pct_change) as env1:
            mock_breakeven_t1 = MagicMock(
                return_value=(5, True, 4.0)  # (hold_ticks, locked=True, stop_level)
            )
            with patch.object(
                alpha_bot_execution.math_engine,
                "compute_breakeven_update",
                mock_breakeven_t1,
            ), patch.object(
                alpha_bot_execution.math_engine,
                "compute_exit_confirmation",
                return_value=(0, False),
            ), patch.object(
                alpha_bot_execution.math_engine,
                "compute_vwap_breakdown_update",
                return_value=(0, 0, False, False),
            ):
                alpha_bot_execution.main()

            # Tick 1's math call must have received locked=False (initial).
            t1_kwargs = mock_breakeven_t1.call_args.kwargs
            assert t1_kwargs.get("currently_breakeven_locked") is False, (
                "tick 1: math engine should be invoked with "
                "currently_breakeven_locked=False because the seeded state "
                "had it False; observed "
                f"{t1_kwargs.get('currently_breakeven_locked')}"
            )

            # After tick 1, bot_state must reflect the math return value
            # (caller wired new_breakeven_locked back at line 603).
            assert bot_state[_SYMPHONY_ID]["breakeven_locked"] is True, (
                "tick 1: caller did NOT write the math engine's "
                "new_breakeven_locked=True back into bot_state. This is "
                "the CX-3 wiring contract — a write failure here means "
                "the lock silently un-locks on the next tick."
            )

        # ---------------- Tick 2: math should see locked=True -------------
        with _patched_main_env(bot_state, pct_change) as env2:
            mock_breakeven_t2 = MagicMock(
                return_value=(6, True, 4.5)  # math returns locked still True
            )
            with patch.object(
                alpha_bot_execution.math_engine,
                "compute_breakeven_update",
                mock_breakeven_t2,
            ), patch.object(
                alpha_bot_execution.math_engine,
                "compute_exit_confirmation",
                return_value=(0, False),
            ), patch.object(
                alpha_bot_execution.math_engine,
                "compute_vwap_breakdown_update",
                return_value=(0, 0, False, False),
            ):
                alpha_bot_execution.main()

            # --- CX-3 core invariant: tick 2 saw locked=True ---
            t2_kwargs = mock_breakeven_t2.call_args.kwargs
            assert t2_kwargs.get("currently_breakeven_locked") is True, (
                "CX-3 violation: tick 2's math call received "
                f"currently_breakeven_locked={t2_kwargs.get('currently_breakeven_locked')}; "
                "expected True because tick 1's math engine returned "
                "locked=True and the caller is contractually obligated "
                "to propagate that into the next tick's call. A bug here "
                "silently re-allows looser stops after breakeven."
            )

            # And the persisted state STILL says locked=True.
            assert bot_state[_SYMPHONY_ID]["breakeven_locked"] is True, (
                "tick 2: post-cycle bot_state should still reflect "
                "locked=True (math returned locked=True, caller wires "
                "it back)"
            )


# =============================================================================
# CX-1 — Trigger state freeze is atomic.
# =============================================================================
class TestTriggerFreezeIsAtomic:
    """Pin that once an exit fires, the post-trigger state freeze writes
    ALL ``triggered_*`` fields in the SAME ``save_state`` call — never in
    a partial state.

    Setup mirrors B1's "trigger fires" scenario: one armed symphony, HWM
    seeded ABOVE current return so the HWM-rise branch is skipped,
    ``compute_exit_confirmation`` patched to ``(3, True)`` so the
    trailing-stop branch fires unconditionally.

    Atomicity check: the single ``save_state`` call after the execution
    queue (alpha_bot_execution.py:823) must contain ALL of the following
    keys with their freeze values, in one dict — no intermediate
    ``save_state`` call may contain a SUBSET of them.

    Audit reference: CX-1 (HIGH risk). A partial-write window would mean
    the bot could restart mid-freeze and observe a state where
    ``triggered=True`` but ``trigger_prices={}`` — i.e. an exit with no
    audit trail. That is a money-loss audit defect even when execution
    succeeds.
    """

    # The full set of fields written by the freeze block
    # (alpha_bot_execution.py:778-805).
    REQUIRED_FREEZE_FIELDS = {
        "armed",                       # set to False
        "tp_armed",                    # set to False
        "triggered",                   # set to True
        "triggered_reason",            # string
        "triggered_at_return",         # float
        "triggered_at_hwm",            # float
        "triggered_at_stop",           # float
        "triggered_at_time",           # string
        "high_water_mark",             # set to sentinel -999.0
        "trigger_prices",              # dict
        "triggered_basket_snapshot",   # list
    }

    def test_all_triggered_fields_written_in_single_save_state_call(self):
        # Fixture-derived inputs
        fixture_pct_change = 0.12  # → current_return == 12.0
        seed_hwm = 20.0            # above current_return so HWM doesn't rise
        live_price = 505.25
        bot_state = _seed_state(armed=True, hwm=seed_hwm)

        with _patched_main_env(bot_state, fixture_pct_change, live_price=live_price) as env:
            with patch.object(
                alpha_bot_execution.math_engine,
                "compute_exit_confirmation",
                return_value=(3, True),  # force trailing-stop hit
            ), patch.object(
                alpha_bot_execution, "execute_sell_to_cash"
            ):
                alpha_bot_execution.main()

            save_calls = env["db"].save_state.call_args_list
            assert save_calls, "save_state never called; pipeline aborted early"

            # Per the audit's atomicity statement: there is exactly ONE
            # save_state call per cycle (line 823). If a future regression
            # introduced a mid-cycle save_state call, that would surface
            # here.
            assert len(save_calls) == 1, (
                f"CX-1 atomicity violation: save_state was called "
                f"{len(save_calls)} times in one cycle; expected exactly "
                "1 (the post-execution-queue write). Multiple save_state "
                "calls open a partial-freeze window where the engine "
                "could be killed between writes and resume with an "
                "inconsistent triggered_* field set."
            )

            final_state = save_calls[0].args[0]
            sym_state = final_state[_SYMPHONY_ID]

            # --- CX-1 atomicity: ALL freeze fields present together ---
            missing = self.REQUIRED_FREEZE_FIELDS - set(sym_state.keys())
            assert not missing, (
                f"CX-1 partial-freeze violation: trigger fired but "
                f"these freeze fields are missing from the save_state "
                f"payload: {sorted(missing)}. All triggered_* fields "
                "must be written atomically per "
                "alpha_bot_execution.py:778-805."
            )

            # --- Field-level value invariants (fixture-derived) ---
            assert sym_state["triggered"] is True
            assert sym_state["armed"] is False
            assert sym_state["tp_armed"] is False
            assert sym_state["triggered_reason"] == "Trailing Stop", (
                "freeze.reason mismatch — patched exit-confirmation "
                "branch corresponds to the Trailing Stop reason"
            )

            # Fixture-derived: current_return = fixture_pct_change * 100.
            expected_cr = fixture_pct_change * 100.0
            assert sym_state["triggered_at_return"] == pytest.approx(
                expected_cr, rel=1e-9
            ), (
                "triggered_at_return must echo current_return at freeze "
                "instant; fixture-derived expected value"
            )

            # Fixture-derived: HWM was seeded above current_return so the
            # rise branch was skipped → safe_hwm == seed_hwm.
            assert sym_state["triggered_at_hwm"] == pytest.approx(
                seed_hwm, rel=1e-9
            )

            # high_water_mark is reset to the -999.0 sentinel as part of
            # the freeze (line 787). Named-sentinel assertion (not a
            # producer-computed value).
            assert sym_state["high_water_mark"] == -999.0, (
                "post-freeze high_water_mark must equal the -999.0 "
                "sentinel so the True Shadow Return override path "
                "engages on subsequent ticks"
            )

            # Shape-only assertions on the math-derived attempted_level.
            assert isinstance(sym_state["triggered_at_stop"], float)
            assert sym_state["triggered_at_stop"] != -999.0, (
                "triggered_at_stop must be the live math layer's "
                "stop_trigger_level, not the default sentinel"
            )

            # --- Audit-trail completeness ---
            assert isinstance(sym_state["trigger_prices"], dict)
            assert _TICKER in sym_state["trigger_prices"], (
                "trigger_prices must contain an entry per ticker in "
                "current_holdings, derived from live_vwaps"
            )
            assert sym_state["trigger_prices"][_TICKER] == pytest.approx(
                live_price, rel=1e-9
            ), "trigger_price for ticker must equal live_vwap.last_price"

            snapshot = sym_state["triggered_basket_snapshot"]
            assert isinstance(snapshot, list) and len(snapshot) == 1, (
                "triggered_basket_snapshot must be a list with one entry "
                "per holding"
            )
            snap_row = snapshot[0]
            assert snap_row["ticker"] == _TICKER
            assert snap_row["allocation"] == pytest.approx(1.0, rel=1e-9)
            assert snap_row["price"] == pytest.approx(live_price, rel=1e-9)

            # --- triggered_at_time is a non-empty string ---
            assert isinstance(sym_state["triggered_at_time"], str)
            assert sym_state["triggered_at_time"], (
                "triggered_at_time must be a non-empty timestamp string"
            )

            # --- Discord alert fired exactly once (part of the atomic
            # freeze action) ---
            env["reporting"].send_discord_alert.assert_called_once()


# =============================================================================
# CX-1 bonus — Post-trigger: subsequent ticks do NOT re-fire the trigger.
# =============================================================================
class TestPostTriggerNoRefire:
    """Adversarial extension of CX-1: once the freeze has been applied,
    the next tick must NOT produce a second trigger (no double-counting,
    no second Discord alert, no second freeze write that would clobber
    the first).

    This pins the interaction between CX-1 (atomic freeze) and CX-4
    (triggered=True flows downstream to short-circuit the next exit
    check). It's also the canonical regression sensor for the
    minute-cadence cron's single-trigger-per-position contract.
    """

    def test_second_tick_after_trigger_does_not_re_fire(self):
        fixture_pct_change = 0.12
        seed_hwm = 20.0
        live_price = 505.25
        bot_state = _seed_state(armed=True, hwm=seed_hwm)

        # ---- Tick 1: trigger fires ----
        with _patched_main_env(bot_state, fixture_pct_change, live_price=live_price) as env_t1:
            with patch.object(
                alpha_bot_execution.math_engine,
                "compute_exit_confirmation",
                return_value=(3, True),
            ), patch.object(
                alpha_bot_execution, "execute_sell_to_cash"
            ):
                alpha_bot_execution.main()

            # Sanity: tick 1 fired.
            assert bot_state[_SYMPHONY_ID]["triggered"] is True

        # ---- Tick 2: no new trigger. compute_exit_confirmation should
        # short-circuit on is_triggered=True (math guard) but even if it
        # didn't, the caller short-circuits on triggered=True (line 715
        # only adds to execution_queue if a NEW hit flag fires this
        # cycle). ----
        with _patched_main_env(bot_state, fixture_pct_change, live_price=live_price) as env_t2:
            # Do NOT force the trailing-stop branch this tick — leave
            # the real math engine to short-circuit on is_triggered.
            with patch.object(
                alpha_bot_execution, "execute_sell_to_cash"
            ) as mock_execute_t2:
                alpha_bot_execution.main()

            # No second Discord alert.
            env_t2["reporting"].send_discord_alert.assert_not_called()

            # No second execute_sell_to_cash even if LIVE_EXECUTION were
            # True (which it isn't here — but pin the queue-empty path).
            mock_execute_t2.assert_not_called()

            # State still says triggered=True; freeze fields persist.
            assert bot_state[_SYMPHONY_ID]["triggered"] is True
            assert bot_state[_SYMPHONY_ID]["armed"] is False
            assert bot_state[_SYMPHONY_ID]["triggered_reason"] == "Trailing Stop"
