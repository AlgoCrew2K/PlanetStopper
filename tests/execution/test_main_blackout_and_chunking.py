"""
Regression pins for two deferred ``main()`` branches in
``alpha_bot_execution.py``:

  * **FU4 — Rebalance-blackout snapshot** (lines ~308-325): when wall-clock ET
    is within the Composer rebalance window
    (``rebalance_blackout`` 15:53 <= current_time < ``market_close`` 16:00) on
    a weekday, ``main()`` MUST:
        (a) load bot_state,
        (b) collect every ticker mentioned in the ``triggered_basket_snapshot``
            of every symphony already in ``triggered=True`` state,
        (c) fetch intraday VWAPs for those tickers,
        (d) call ``reporting.generate_eod_snapshot`` exactly once with
            ``is_post_rebalance=False`` and the live_prices it just fetched,
        (e) return early (no execution-queue processing, no Discord exit
            alerts, no autotuner kick-off),
        (f) ``--force`` bypasses the early return so the engine can run a
            normal execution cycle inside the blackout window.

  * **FU5 — Execution-queue chunking** (lines ~752-761): when the execution
    queue exceeds 25 items, ``main()`` MUST process them in chunks of 25 with
    a ``time.sleep(60)`` between each chunk (Composer rate-limit guard). All
    triggered symphonies, regardless of which chunk they fall into, must
    appear in the final persisted ``bot_state`` with ``triggered=True``.

Audit context
-------------
Cycle B1 pinned the dry-run pipeline core (3 scenarios).
Cycle B1-FU1 + FU1.1 pinned the live-money executor branch (5 scenarios).
Cycle B1-FU2 + FU3 pinned the True Shadow Return override + EOD post-mortem
branch (8 scenarios).

This module (B1-FU4 + FU5) closes the two remaining deferred main() branches:

  * The rebalance-blackout snapshot is the only path that runs during the
    Composer rebalance window — it produces the pre-rebalance Discord snapshot
    operators use to verify Composer accepted the right basket. A regression
    that fails to record the snapshot silently breaks the daily audit trail.

  * Execution-queue chunking is a real-money rate-limit guard. The 60-second
    sleep between batches of 25 is the only thing preventing a 50-symphony
    fan-out from hitting Composer's rate limit and getting partially rejected.
    A regression that drops the chunking (e.g. someone "simplifies" the loop)
    silently re-introduces the rate-limit risk.

Production code observed at task-b1-fu4-fu5-blackout-chunking @ d36da9f:
  * rebalance_blackout = dt_time(15, 53)   -- alpha_bot_execution.py:299
  * market_close       = dt_time(16, 0)    -- alpha_bot_execution.py:298
  * chunk size         = 25                -- alpha_bot_execution.py:756
  * sleep value        = 60                -- alpha_bot_execution.py:761
  * is_post_rebalance  = False             -- alpha_bot_execution.py:320

Mocking philosophy
------------------
Identical to ``test_main_pipeline.py`` — we reuse its ``patched_environment``
fixture verbatim. Per-scenario overrides:

  * FU4 tests: patch ``get_current_et`` to a fixed weekday clock at 15:55 ET
    (within the blackout window).
  * FU5 tests: build a fan-out fixture with 27 distinct symphonies, all armed,
    all returning a fixture-derived percent_change that — together with the
    forced ``compute_exit_confirmation`` return — guarantees every one of them
    appends to ``execution_queue``. Patch ``time.sleep`` to a tracking no-op so
    we can assert it was invoked between chunks.

Fixture-derivation rule
-----------------------
Per project memory ``feedback_no_hardcoded_test_values``, every assertion
touching producer-computed values (returns, prices, HWM) is derived from the
fixture this test seeds — never from literal expectations of math the engine
produced. The two production constants we DO assert literally (chunk size 25
and sleep value 60) are the exact values we are pinning against regression;
the test docstring above quotes their source lines and the assertions name them
explicitly.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import alpha_bot_execution

# Reuse the B1 harness verbatim. Duplicating the fixture / helpers would drift.
from tests.execution.test_main_pipeline import (
    _FIXED_ET,
    _SYMPHONY_ID,
    _TICKER,
    _make_vwap_payload,
    patched_environment,  # noqa: F401  (re-exported pytest fixture)
)

# Production constants pinned by this module. These are the literal values
# being protected against regression; the docstring at the top of this file
# cites the source-of-truth lines in alpha_bot_execution.py.
_PROD_CHUNK_SIZE = 25  # alpha_bot_execution.py:756 (`range(0, len, 25)`)
_PROD_CHUNK_SLEEP_SECS = 60  # alpha_bot_execution.py:761 (`time.sleep(60)`)


# ZoneInfo for the blackout fixed clock. Mirrors test_main_pipeline.py's fallback.
try:
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")

    def _et(year, month, day, hour, minute):
        return datetime(year, month, day, hour, minute, 0, tzinfo=_ET)
except Exception:  # pragma: no cover -- tzdata missing

    def _et(year, month, day, hour, minute):
        return datetime(year, month, day, hour, minute, 0, tzinfo=timezone(timedelta(hours=-4)))


# Wednesday 2025-05-14 15:55 ET — inside the [15:53, 16:00) blackout window.
# Wednesday is weekday() == 2 so the is_weekday gate at line 289 passes.
_BLACKOUT_ET = _et(2025, 5, 14, 15, 55)


# =============================================================================
# FU4 — Rebalance-blackout snapshot
# =============================================================================
#
# Per alpha_bot_execution.py:308-325, the blackout branch fires iff:
#     rebalance_blackout (15:53) <= current_time < market_close (16:00)
# It then:
#   1. Loads bot_state.
#   2. Collects tickers from `triggered_basket_snapshot` across all
#      triggered=True symphonies (`all_snapshotted_tickers`).
#   3. Calls fetch_intraday_vwaps(list(all_snapshotted_tickers), ..., et).
#   4. Calls reporting.generate_eod_snapshot(bot_state, date_str,
#         is_post_rebalance=False, discord_webhook_url=..., live_prices=...).
#   5. Returns early (unless --force is set).
#
# Per-symphony state ("blackout") is NOT a thing in the current production
# code — the blackout is a WALL-CLOCK gate that pauses the WHOLE engine.
# The pin therefore: blackout window => no execution-queue processing for ANY
# symphony, including ones that would otherwise trigger.


def _seed_blackout_state(triggered_symphonies):
    """Build a bot_state with N pre-triggered symphonies whose
    ``triggered_basket_snapshot`` lists tickers the engine should pick up
    during the blackout snapshot scan.

    ``triggered_symphonies`` is a list of (sym_id, tickers) tuples.
    """
    state = {
        "date": _BLACKOUT_ET.strftime("%Y-%m-%d"),
        "last_execution_mode": False,  # avoid the toggle-wipe branch
    }
    for sym_id, tickers in triggered_symphonies:
        state[sym_id] = {
            "armed": False,
            "tp_armed": False,
            "para_armed": False,
            "triggered": True,
            "triggered_reason": "Trailing Stop",
            "triggered_at_return": 5.0,
            "triggered_at_hwm": 7.0,
            "triggered_at_stop": 4.5,
            "high_water_mark": -999.0,
            "shadow_hwm": -999.0,
            "prev_return": 0.0,
            "mc_history": [],
            "below_stop_count": 0,
            "above_tp_count": 0,
            "vwap_ticks": 0,
            "vwap_bleed_ticks": 0,
            "breakeven_locked": False,
            "hwm_hold_ticks": 0,
            "current_holdings": [{"ticker": t, "allocation": 1.0 / len(tickers)} for t in tickers],
            "trigger_prices": {t: 500.0 for t in tickers},
            "triggered_basket_snapshot": [
                {"ticker": t, "allocation": 1.0 / len(tickers), "price": 500.0} for t in tickers
            ],
        }
    return state


class TestBlackoutWindowGeneratesPreRebalanceSnapshot:
    """When the wall clock is inside [15:53, 16:00) ET on a weekday, the
    blackout branch MUST call ``reporting.generate_eod_snapshot`` exactly
    once with ``is_post_rebalance=False`` and return BEFORE any execution
    queue processing runs.
    """

    def test_blackout_window_invokes_generate_eod_snapshot_with_pre_rebalance_flag(
        self, patched_environment
    ):
        env = patched_environment

        # One triggered symphony seeded into state. The blackout snapshot scan
        # must find SPY in its triggered_basket_snapshot.
        env["db"].load_state.return_value = _seed_blackout_state([(_SYMPHONY_ID, [_TICKER])])
        # Composer fan-out result for the blackout path is unused (the branch
        # returns BEFORE fetch_symphony_stats runs), but set it to empty so a
        # regression that runs the fan-out crashes here loudly.
        env["fetch_symphony_stats"].return_value = []
        # Live VWAPs that the snapshot scan should fetch and pass along.
        snapshot_vwap_payload = {
            _TICKER: {"vwap": 502.0, "last_price": 503.5},
        }
        env["fetch_intraday_vwaps"].return_value = snapshot_vwap_payload

        with patch.object(alpha_bot_execution, "get_current_et", return_value=_BLACKOUT_ET):
            alpha_bot_execution.main()

        # ----- 1. generate_eod_snapshot called exactly once -----
        env["reporting"].generate_eod_snapshot.assert_called_once()

        # ----- 2. is_post_rebalance=False — pin the blackout-vs-postmortem
        #         distinction. 15:53 blackout uses False; 16:00+ post-mortem
        #         uses True (see test_main_shadow_and_eod.py FU3). -----
        call_obj = env["reporting"].generate_eod_snapshot.call_args
        assert call_obj.kwargs.get("is_post_rebalance") is False, (
            "Rebalance-blackout snapshot must invoke generate_eod_snapshot "
            "with is_post_rebalance=False; True is reserved for the 16:00+ "
            "post-mortem branch. Pin protects against a flag-swap regression "
            "that would mis-label the daily Discord snapshot."
        )

        # ----- 3. live_prices kwarg propagated from fetch_intraday_vwaps -----
        live_prices = call_obj.kwargs.get("live_prices")
        assert live_prices is not None, (
            "generate_eod_snapshot must receive a live_prices kwarg — the "
            "snapshot is meaningless without current VWAPs for the frozen "
            "basket."
        )
        # Fixture-derived: the live_prices kwarg must equal the dict we returned
        # from fetch_intraday_vwaps. Direct identity / equality.
        assert live_prices == snapshot_vwap_payload, (
            "live_prices kwarg must equal the dict returned by "
            "fetch_intraday_vwaps — the engine must NOT silently drop entries."
        )

        # ----- 4. fetch_intraday_vwaps was called with the snapshotted ticker -----
        env["fetch_intraday_vwaps"].assert_called_once()
        vwap_call = env["fetch_intraday_vwaps"].call_args
        # First positional arg is the ticker list.
        ticker_arg = vwap_call.args[0]
        assert _TICKER in ticker_arg, (
            f"fetch_intraday_vwaps must be invoked with the snapshotted "
            f"ticker(s); got {ticker_arg!r}. The engine must scan "
            f"triggered_basket_snapshot of every triggered=True symphony."
        )


class TestBlackoutWindowDoesNotRunExecutionPipeline:
    """The blackout branch returns BEFORE the execution-queue loop. Pin that
    no Composer fan-out (``fetch_symphony_stats``), no Alpaca history fetch,
    no ``send_discord_alert``, and no ``execute_sell_to_cash`` happens during
    the blackout window.

    Critical for real-money: if a regression accidentally lets execution run
    during the rebalance window, the engine could fire sell-to-cash against
    a portfolio Composer is actively rebalancing — a race condition with
    undefined real-money behaviour.
    """

    def test_blackout_short_circuits_before_execution_pipeline(self, patched_environment):
        env = patched_environment
        env["db"].load_state.return_value = _seed_blackout_state([(_SYMPHONY_ID, [_TICKER])])
        env["fetch_symphony_stats"].return_value = []
        env["fetch_intraday_vwaps"].return_value = {
            _TICKER: {"vwap": 502.0, "last_price": 503.5},
        }

        with (
            patch.object(alpha_bot_execution, "get_current_et", return_value=_BLACKOUT_ET),
            patch.object(alpha_bot_execution, "execute_sell_to_cash") as mock_execute,
        ):
            alpha_bot_execution.main()

        # ----- 1. Composer symphony fan-out did NOT run -----
        env["fetch_symphony_stats"].assert_not_called()

        # ----- 2. Alpaca historical fetch did NOT run -----
        env["fetch_alpaca_history"].assert_not_called()

        # ----- 3. No Discord exit alert -----
        env["reporting"].send_discord_alert.assert_not_called()

        # ----- 4. execute_sell_to_cash NEVER called -----
        mock_execute.assert_not_called()

        # ----- 5. Lock cycle still well-formed (acquire + release) -----
        env["db"].acquire_lock.assert_called_once()
        env["db"].release_lock.assert_called_once()


class TestBlackoutCollectsTickersAcrossMultipleTriggeredSymphonies:
    """The snapshot scan iterates EVERY symphony in bot_state with
    ``triggered=True``, collecting tickers from each
    ``triggered_basket_snapshot`` into a set. Pin that with multiple triggered
    symphonies, EVERY ticker mentioned across all of them ends up in the
    fetch_intraday_vwaps call.

    Regression sensitivity: if someone refactors the loop and breaks the union
    semantics (e.g. only reads the FIRST triggered symphony, or skips the
    snapshot for cash-only baskets), the blackout snapshot would silently
    omit tickers and the operator's pre-rebalance audit would be incomplete.
    """

    def test_all_triggered_symphony_tickers_passed_to_vwap_fetch(self, patched_environment):
        env = patched_environment

        # Three triggered symphonies, each with a distinct ticker basket.
        # Sym A: [SPY], Sym B: [QQQ, IWM], Sym C: [SPY, GLD] (note SPY overlap).
        seeded = _seed_blackout_state(
            [
                ("sym-blackout-A", ["SPY"]),
                ("sym-blackout-B", ["QQQ", "IWM"]),
                ("sym-blackout-C", ["SPY", "GLD"]),  # SPY also in A
            ]
        )
        env["db"].load_state.return_value = seeded
        env["fetch_symphony_stats"].return_value = []
        env["fetch_intraday_vwaps"].return_value = {
            "SPY": {"vwap": 500.0, "last_price": 501.0},
            "QQQ": {"vwap": 400.0, "last_price": 401.0},
            "IWM": {"vwap": 200.0, "last_price": 201.0},
            "GLD": {"vwap": 180.0, "last_price": 181.0},
        }

        with patch.object(alpha_bot_execution, "get_current_et", return_value=_BLACKOUT_ET):
            alpha_bot_execution.main()

        # ----- 1. fetch_intraday_vwaps was called exactly once -----
        env["fetch_intraday_vwaps"].assert_called_once()
        ticker_arg = env["fetch_intraday_vwaps"].call_args.args[0]

        # ----- 2. Fixture-derived: derive expected set from the SEEDED state,
        #         NOT a hardcoded list. If the seed helper changes, the
        #         expected set re-derives automatically. -----
        expected_tickers = set()
        for s_id, s_data in seeded.items():
            if isinstance(s_data, dict) and s_data.get("triggered"):
                for h in s_data.get("triggered_basket_snapshot", []):
                    if h.get("ticker"):
                        expected_tickers.add(h["ticker"])

        # The engine passes a list (line 318: `list(all_snapshotted_tickers)`),
        # but the production code constructs it from a set so order is not
        # guaranteed — compare as sets.
        assert set(ticker_arg) == expected_tickers, (
            f"fetch_intraday_vwaps must receive the UNION of every ticker in "
            f"every triggered symphony's triggered_basket_snapshot. Expected "
            f"(fixture-derived): {expected_tickers!r}; got: {set(ticker_arg)!r}."
        )


class TestBlackoutForceFlagBypassesEarlyReturn:
    """When ``--force`` is in ``sys.argv``, the blackout branch logs the
    snapshot AND CONTINUES into the normal pipeline. Production code at
    alpha_bot_execution.py:322-325 does the early return only when
    ``not force_run``.

    Regression sensitivity: the --force escape hatch is what operators use to
    manually re-run the engine during the blackout window (e.g. to investigate
    a mid-day rebalance issue). If --force stops bypassing, manual runs become
    impossible.
    """

    def test_force_flag_continues_pipeline_past_blackout_branch(self, patched_environment):
        env = patched_environment
        env["db"].load_state.return_value = _seed_blackout_state([(_SYMPHONY_ID, [_TICKER])])
        # Empty symphony list — the test asserts the engine ATTEMPTED the
        # Composer fan-out (proving it ran past the blackout's early return),
        # not that anything was processed.
        env["fetch_symphony_stats"].return_value = []
        env["fetch_intraday_vwaps"].return_value = {
            _TICKER: {"vwap": 500.0, "last_price": 500.0},
        }

        with (
            patch.object(alpha_bot_execution, "get_current_et", return_value=_BLACKOUT_ET),
            patch.object(
                alpha_bot_execution.sys,
                "argv",
                ["alpha_bot_execution.py", "--force"],
            ),
        ):
            alpha_bot_execution.main()

        # ----- 1. Snapshot still ran (it's BEFORE the force-gated early return) -----
        env["reporting"].generate_eod_snapshot.assert_called_once()

        # ----- 2. Critical: the Composer fan-out RAN — proving the engine
        #         continued past the blackout's early return. -----
        (
            env["fetch_symphony_stats"].assert_called(),
            (
                "--force MUST bypass the blackout's early return so the normal "
                "execution pipeline runs. fetch_symphony_stats not being called "
                "means the engine returned at the blackout guard despite --force."
            ),
        )


# =============================================================================
# FU5 — Execution-queue chunking
# =============================================================================
#
# Per alpha_bot_execution.py:752-761:
#
#     if execution_queue:
#         print(f"\nProcessing Execution Queue ({len(execution_queue)} items)...")
#         for i in range(0, len(execution_queue), 25):
#             chunk = execution_queue[i:i+25]
#             if i > 0:
#                 print("  -> ⏳ Rate limit chunking: ...")
#                 time.sleep(60)
#             for item in chunk:
#                 ...
#
# Invariants pinned below:
#   1. Chunk size is 25.
#   2. Between chunks, time.sleep(60) is invoked exactly once per chunk
#      boundary.
#   3. ALL items in the queue (across all chunks) are processed — the
#      final save_state reflects triggered=True for every queued symphony.


_FU5_QUEUE_DEPTH = 27  # one full chunk + 2 stragglers
_FU5_FIXTURE_PCT_CHANGE = 0.12  # → current_return == 12.0
_FU5_SEED_HWM = 20.0  # above current_return → HWM-rise branch skipped
_FU5_LIVE_PRICE = 505.25


def _make_fu5_symphony_payload(idx: int):
    """Composer-shaped payload for the Nth fan-out symphony.

    Each symphony has a unique id ``sym-fu5-<idx>`` and a unique
    actual_symphony_id ``actual-sym-fu5-<idx>``. All share the same single-
    ticker holding so the fan-out keeps the math fixture simple.
    """
    return {
        "id": f"sym-fu5-{idx:03d}",
        "symphony_id": f"actual-sym-fu5-{idx:03d}",
        "name": f"FU5 Test Symphony {idx:03d}",
        "last_percent_change": _FU5_FIXTURE_PCT_CHANGE,
        "current_value": 10000.0,
        "holdings": [{"ticker": _TICKER, "allocation": 1.0}],
    }


def _seed_fu5_state(n: int, armed: bool = True, hwm: float = _FU5_SEED_HWM):
    """Build a bot_state with N pre-armed symphonies, all keyed under
    ``sym-fu5-<idx>``."""
    base = {
        "date": _FIXED_ET.strftime("%Y-%m-%d"),
        "last_execution_mode": False,
    }
    for i in range(n):
        base[f"sym-fu5-{i:03d}"] = {
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
    return base


class TestExecutionQueueChunksAtTwentyFive:
    """27 triggered symphonies → 2 chunks (25 + 2) → exactly ONE
    ``time.sleep(60)`` between them.

    Pre-cond: every symphony is pre-armed in seeded state AND
    ``compute_exit_confirmation`` is patched to return ``(3, True)`` so the
    trailing-stop branch fires unconditionally for every symphony — this
    guarantees ``execution_queue`` receives all 27 items.
    """

    def test_twenty_seven_triggers_produce_one_sleep_of_sixty_seconds(self, patched_environment):
        env = patched_environment

        env["fetch_symphony_stats"].return_value = [
            _make_fu5_symphony_payload(i) for i in range(_FU5_QUEUE_DEPTH)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(_FU5_LIVE_PRICE)
        env["db"].load_state.return_value = _seed_fu5_state(_FU5_QUEUE_DEPTH)

        # The harness already patches alpha_bot_execution.time.sleep as a
        # no-op MagicMock — re-grab it from the module so we can inspect
        # call args. The harness patch is via patch.object(... time, "sleep")
        # without a return-value override, which makes it a MagicMock attribute.
        with (
            patch.object(
                alpha_bot_execution.math_engine,
                "compute_exit_confirmation",
                return_value=(3, True),  # force trailing-stop hit for every symphony
            ),
            patch.object(
                alpha_bot_execution, "execute_sell_to_cash", return_value=True
            ) as mock_execute,
        ):
            alpha_bot_execution.main()

        # ----- 1. All 27 executor calls happened -----
        # LIVE_EXECUTION is False (harness default), so execute_sell_to_cash
        # is NOT called in this scenario — but the chunking loop still runs
        # because the queue is non-empty. Pin via the freeze-block side effects
        # instead: every symphony must end as triggered=True.
        assert mock_execute.call_count == 0, (
            "Sanity: harness defaults LIVE_EXECUTION=False, so "
            "execute_sell_to_cash should NOT be invoked in this scenario."
        )

        # ----- 2. time.sleep(60) was invoked exactly once -----
        # 27 items / 25 chunk size = 2 chunks → 1 inter-chunk sleep.
        sleep_mock = alpha_bot_execution.time.sleep
        sleep_calls = [c for c in sleep_mock.call_args_list if c.args == (_PROD_CHUNK_SLEEP_SECS,)]
        # Calculated from production constants (chunk size 25, queue depth 27):
        # expected inter-chunk pauses = ceil(27/25) - 1 = 1.
        import math as _math

        expected_sleeps = _math.ceil(_FU5_QUEUE_DEPTH / _PROD_CHUNK_SIZE) - 1
        assert len(sleep_calls) == expected_sleeps, (
            f"Expected {expected_sleeps} call(s) to "
            f"time.sleep({_PROD_CHUNK_SLEEP_SECS}) "
            f"for {_FU5_QUEUE_DEPTH} items at chunk size {_PROD_CHUNK_SIZE}; "
            f"got {len(sleep_calls)}. Production rate-limit guard requires "
            f"a {_PROD_CHUNK_SLEEP_SECS}s pause between Composer batches."
        )


class TestExecutionQueueChunkingProcessesEveryItem:
    """Across 27 triggered symphonies (one full chunk + 2 stragglers), every
    single one MUST end the cycle as ``triggered=True`` in the persisted
    bot_state. Drop-on-chunk-boundary regressions would leak armed-and-not-
    triggered symphonies into the next minute tick, causing a re-fire and
    a real-money double-charge.
    """

    def test_all_twenty_seven_symphonies_committed_as_triggered(self, patched_environment):
        env = patched_environment

        env["fetch_symphony_stats"].return_value = [
            _make_fu5_symphony_payload(i) for i in range(_FU5_QUEUE_DEPTH)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(_FU5_LIVE_PRICE)
        env["db"].load_state.return_value = _seed_fu5_state(_FU5_QUEUE_DEPTH)

        with patch.object(
            alpha_bot_execution.math_engine,
            "compute_exit_confirmation",
            return_value=(3, True),
        ):
            alpha_bot_execution.main()

        # Final save_state must carry triggered=True for EVERY queued symphony.
        save_calls = env["db"].save_state.call_args_list
        assert save_calls, "save_state was never called — pipeline aborted early"
        final_state = save_calls[-1].args[0]

        # Fixture-derived expected set of triggered symphony ids.
        expected_ids = {f"sym-fu5-{i:03d}" for i in range(_FU5_QUEUE_DEPTH)}
        actual_triggered_ids = {
            k for k, v in final_state.items() if isinstance(v, dict) and v.get("triggered") is True
        }
        # Subset assertion — final_state may also contain top-level non-symphony
        # keys (date, post_mortem_run, etc.). We want every fixture symphony
        # present AND triggered.
        missing = expected_ids - actual_triggered_ids
        assert not missing, (
            f"All {_FU5_QUEUE_DEPTH} queued symphonies must end with "
            f"triggered=True after the chunked execution loop. Missing: "
            f"{sorted(missing)!r}. A miss here indicates an item was dropped "
            f"at the chunk boundary — real-money: lost triggered flag → "
            f"next-tick re-fire → double-charge."
        )


class TestExecutionQueueChunkingPersistsCumulativeStateOnce:
    """The final ``database.save_state`` at line 874 must fire AFTER the entire
    chunked loop completes — so EVERY chunk's freeze-block mutations land in
    ONE durable write.

    Pin: the LAST save_state call is the post-loop one, and it contains
    triggered=True for symphonies from BOTH the first chunk (indices 0..24)
    AND the second chunk (indices 25..26).
    """

    def test_final_save_state_contains_symphonies_from_both_chunks(self, patched_environment):
        env = patched_environment

        env["fetch_symphony_stats"].return_value = [
            _make_fu5_symphony_payload(i) for i in range(_FU5_QUEUE_DEPTH)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(_FU5_LIVE_PRICE)
        env["db"].load_state.return_value = _seed_fu5_state(_FU5_QUEUE_DEPTH)

        with patch.object(
            alpha_bot_execution.math_engine,
            "compute_exit_confirmation",
            return_value=(3, True),
        ):
            alpha_bot_execution.main()

        save_calls = env["db"].save_state.call_args_list
        assert save_calls, "save_state was never called — pipeline aborted early"
        last_state = save_calls[-1].args[0]

        # First-chunk representative (index 0) — must be triggered.
        first_chunk_id = "sym-fu5-000"
        assert last_state.get(first_chunk_id, {}).get("triggered") is True, (
            f"First-chunk symphony {first_chunk_id!r} must be triggered in the "
            f"final persisted state — its commit must survive past the "
            f"inter-chunk sleep and into the post-loop save_state call."
        )

        # Last-chunk representative (index 26 — the second of two stragglers).
        last_chunk_id = f"sym-fu5-{_FU5_QUEUE_DEPTH - 1:03d}"
        assert last_state.get(last_chunk_id, {}).get("triggered") is True, (
            f"Final-chunk symphony {last_chunk_id!r} must be triggered in the "
            f"final persisted state — proves the second chunk ran AFTER the "
            f"60-second sleep and its mutations reached the post-loop "
            f"save_state."
        )

        # Lock cycle still well-formed.
        env["db"].acquire_lock.assert_called_once()
        env["db"].release_lock.assert_called_once()


class TestExecutionQueueNoSleepUnderChunkBoundary:
    """When the queue has FEWER than 25 items (e.g. one symphony triggers),
    NO inter-chunk sleep occurs. Production code at
    alpha_bot_execution.py:759 guards with ``if i > 0:`` — the first chunk
    never sleeps.

    Regression sensitivity: a "simplification" that puts the sleep before
    the chunk-iteration check would introduce a spurious 60s pause on every
    single-symphony trigger — silently degrading the engine's reaction time
    on light days.
    """

    def test_single_item_queue_does_not_invoke_sixty_second_sleep(self, patched_environment):
        env = patched_environment

        # One symphony only.
        env["fetch_symphony_stats"].return_value = [_make_fu5_symphony_payload(0)]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(_FU5_LIVE_PRICE)
        env["db"].load_state.return_value = _seed_fu5_state(1)

        with patch.object(
            alpha_bot_execution.math_engine,
            "compute_exit_confirmation",
            return_value=(3, True),
        ):
            alpha_bot_execution.main()

        # The 60-second sleep must NOT have been invoked.
        sleep_mock = alpha_bot_execution.time.sleep
        sleep_60_calls = [
            c for c in sleep_mock.call_args_list if c.args == (_PROD_CHUNK_SLEEP_SECS,)
        ]
        assert len(sleep_60_calls) == 0, (
            f"A single-item execution queue must NOT trigger "
            f"time.sleep({_PROD_CHUNK_SLEEP_SECS}); the inter-chunk pause is "
            f"gated on `i > 0`. Got {len(sleep_60_calls)} calls. A spurious "
            f"sleep here silently degrades engine reactivity on light days."
        )

        # Sanity: the symphony WAS still committed as triggered.
        save_calls = env["db"].save_state.call_args_list
        assert save_calls
        last_state = save_calls[-1].args[0]
        assert last_state.get("sym-fu5-000", {}).get("triggered") is True, (
            "Sanity: single-symphony trigger must still freeze state."
        )
