"""
Regression pins for two deferred ``main()`` branches in
``alpha_bot_execution.py``:

  * **FU2 — True Shadow Return override** (lines ~463-478): when a symphony's
    persisted state already has ``triggered=True``, the per-tick
    ``current_return`` is REPLACED by a frozen baseline
    (``triggered_at_return``) plus a post-trigger move computed from
    ``trigger_prices`` vs current ``live_vwaps`` last prices. This pins that
    the override:
        (a) uses the FROZEN baseline, NOT the symphony's live
            ``last_percent_change``,
        (b) uses FROZEN holdings (``current_holdings``), NOT live ``holdings``,
        (c) does NOT mutate the frozen ``triggered_at_hwm`` (HWM is also
            frozen post-trigger),
        (d) does NOT re-fire ``reporting.send_discord_alert`` on subsequent
            ticks.

  * **FU3 — EOD post-mortem branch** (lines ~389-424): when wall-clock time is
    between ``market_close`` (16:00 ET) and ``post_mortem_cutoff`` (16:05 ET)
    on a weekday, ``main()`` enters the post-mortem branch. This pins that:
        (a) ``reporting.generate_eod_snapshot`` is invoked exactly once,
        (b) ``autotuner.run_autotuner`` is invoked on Fridays (weekday==4) but
            NOT on Mon-Thu (0..3),
        (c) the post-mortem is idempotent within the same day (a second tick
            same-day short-circuits BEFORE re-invoking
            ``generate_eod_snapshot``).

Audit context
-------------
Cycle B1 pinned the dry-run pipeline core. Cycle B1-FU1 pinned the live-money
executor branch. Cycle B1-FU2+FU3 (this module) pins the two deferred control
branches identified in the B1 worker's coverage notes:

  * The True Shadow Return override is the engine's "tracking" mode for
    already-exited symphonies — required for Planet Stopper-vs-live comparison
    stats. A regression where the override falls through to live values would
    silently corrupt the historical comparison and the Discord post-mortem.

  * The EOD post-mortem branch is where weekly autotuning kicks off. A
    regression that fires autotuner mid-week or fails to fire it on Friday
    silently degrades strategy quality over weeks.

Mocking philosophy
------------------
Identical to ``test_main_pipeline.py`` and ``test_main_live_execution.py`` —
we reuse the shared ``patched_environment`` fixture verbatim. Per-scenario
overrides:

  * FU2 tests: pre-seed ``bot_state`` with ``triggered=True`` and frozen
    fields; control ``live_vwaps`` per scenario.
  * FU3 tests: patch ``get_current_et`` to return a fixed time AFTER
    ``market_close`` and switch the weekday between Fri/Mon.

Fixture-derivation rule
-----------------------
Per project memory ``feedback_no_hardcoded_test_values``, every assertion
touching producer-computed values (returns, prices, HWM) is derived from the
fixture this test seeds — never from literal expectations of math the engine
produced. Exception: the override's expected post-trigger move is an algebraic
combination of fixture-controlled inputs (``triggered_at_return``,
``trigger_prices``, ``live_vwaps``), so we recompute it in the test from those
fixture values rather than hardcoding the resulting number.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import alpha_bot_execution

# Reuse B1's harness verbatim. Duplicating the fixture / helpers would drift.
from tests.execution.test_main_pipeline import (
    _SYMPHONY_ID,
    _TICKER,
    _make_symphony_payload,
    _make_vwap_payload,
    _seed_state,
    patched_environment,  # noqa: F401  (re-exported pytest fixture)
)

# ZoneInfo for the EOD fixed clocks. Mirrors test_main_pipeline.py's fallback.
try:
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")

    def _et(year, month, day, hour, minute):
        return datetime(year, month, day, hour, minute, 0, tzinfo=_ET)
except Exception:  # pragma: no cover -- tzdata missing

    def _et(year, month, day, hour, minute):
        return datetime(year, month, day, hour, minute, 0, tzinfo=timezone(timedelta(hours=-4)))


# Wednesday 2025-05-14 11:30 ET — re-used from the harness baseline (intraday).
# Friday 2025-05-16 is the natural Friday next to that Wednesday.
# Monday 2025-05-12 is the natural Monday before.
_FRIDAY_EOD = _et(2025, 5, 16, 16, 2)  # Fri 16:02 ET — within EOD window
_MONDAY_EOD = _et(2025, 5, 12, 16, 2)  # Mon 16:02 ET — within EOD window


# =============================================================================
# FU2 — True Shadow Return override
# =============================================================================

# Per ``alpha_bot_execution.py`` lines 463-478, the override path activates iff
# ``bot_state[symphony_id]["triggered"]`` is truthy at the top of the per-symphony
# loop. The override:
#   * Replaces ``holdings`` with the FROZEN ``current_holdings`` from bot_state.
#   * Computes ``post_trigger_move`` as a sum of ``alloc * (p_now - p_start) / p_start``
#     across the FROZEN basket using ``trigger_prices[t]`` (frozen) and
#     ``live_vwaps[t]["last_price"]`` (live).
#   * Overrides ``current_return`` to ``triggered_at_return + post_trigger_move * 100``.
#
# The frozen baseline values are pre-seeded via ``_seed_state(..., triggered=True, ...)``.

_FROZEN_TRIGGERED_AT_RETURN = 2.5  # %, pre-trigger HWM-relative return at freeze
_FROZEN_TRIGGERED_AT_HWM = 3.0  # %, frozen HWM at the trigger instant
_FROZEN_TRIGGER_PRICE = 500.00  # SPY price at trigger instant
_FROZEN_BASKET_ALLOC = 1.0  # whole-portfolio SPY at trigger

# These are sentinel values the LIVE feed reports — distinct from frozen ones
# so divergence is observable.
_LIVE_PERCENT_CHANGE = 0.50  # symphony's live last_percent_change (=50%)
_LIVE_LAST_PRICE = 510.00  # SPY's CURRENT VWAP price (2% above frozen)


def _seed_triggered_state():
    """Seed bot_state with a symphony that already triggered on a prior tick.

    Shape mirrors what ``main()`` writes during the post-trigger state freeze
    block (alpha_bot_execution.py:829-856): ``armed=False``, ``triggered=True``,
    ``triggered_at_return``, ``triggered_at_hwm``, ``triggered_at_stop``,
    ``trigger_prices``, ``triggered_basket_snapshot``, ``current_holdings``,
    ``high_water_mark = -999.0`` (sentinel post-trigger).
    """
    state = _seed_state(
        armed=False,
        hwm=-999.0,  # post-trigger sentinel
        triggered=True,
        triggered_reason="Trailing Stop",
        triggered_at_return=_FROZEN_TRIGGERED_AT_RETURN,
        triggered_at_hwm=_FROZEN_TRIGGERED_AT_HWM,
        triggered_at_stop=1.5,  # arbitrary stop level, not under test
        triggered_at_time="11:25",
        current_holdings=[{"ticker": _TICKER, "allocation": _FROZEN_BASKET_ALLOC}],
        trigger_prices={_TICKER: _FROZEN_TRIGGER_PRICE},
        triggered_basket_snapshot=[
            {
                "ticker": _TICKER,
                "allocation": _FROZEN_BASKET_ALLOC,
                "price": _FROZEN_TRIGGER_PRICE,
            }
        ],
    )
    return state


def _expected_override_return(
    p_start: float,
    p_now: float,
    frozen_ret: float = _FROZEN_TRIGGERED_AT_RETURN,
    alloc: float = _FROZEN_BASKET_ALLOC,
) -> float:
    """Algebraic recompute of the override formula at line 477.

    ``current_return = triggered_at_return + (alloc * (p_now - p_start) / p_start) * 100.0``

    Derived from fixture inputs only — NOT from running the engine.
    """
    post_trigger_move = alloc * (p_now - p_start) / p_start
    return frozen_ret + post_trigger_move * 100.0


class TestShadowOverrideUsesFrozenBaseline:
    """The override MUST use the frozen ``triggered_at_return`` baseline, NOT
    the live ``last_percent_change`` * 100.

    If a future refactor accidentally drops the override (e.g. the
    ``if ... triggered`` guard is broken), ``current_return`` would fall
    through to ``sym["last_percent_change"] * 100`` — which is the LIVE feed's
    own (now-stale) tracking number. The test detects that regression by
    making the live and frozen values DELIBERATELY different.
    """

    def test_current_return_in_saved_state_uses_frozen_baseline_not_live_feed(
        self, patched_environment
    ):
        env = patched_environment

        # LIVE feed reports +50.0%. Frozen baseline is +2.5%. If override
        # fires, current_return ≈ 2.5 + (1.0 * (510 - 500) / 500) * 100 = 4.5.
        # If override DOESN'T fire, current_return = 50.0. The two are
        # diametrically distinguishable.
        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=_LIVE_PERCENT_CHANGE)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(_LIVE_LAST_PRICE)
        env["db"].load_state.return_value = _seed_triggered_state()

        # Suppress exit pathways — we only care about the saved current_return.
        # The compute_exit_confirmation arms-guard already protects against
        # re-fire (is_triggered=True, armed=False), but we pin it to no-op for
        # determinism.
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

        # The saved ``current_return`` (line 675) must match the override
        # formula — derived from fixture inputs, NOT from live percent_change.
        expected = _expected_override_return(
            p_start=_FROZEN_TRIGGER_PRICE,
            p_now=_LIVE_LAST_PRICE,
        )
        assert sym_state["current_return"] == pytest.approx(expected, rel=1e-9), (
            "saved current_return must equal frozen baseline + post-trigger move "
            "computed from FROZEN trigger_prices and live VWAP last_price — "
            "NOT the live feed's last_percent_change * 100"
        )

        # Sanity floor: if the override silently fell through, current_return
        # would equal the live feed value (50.0). Pin that it does NOT.
        live_feed_value = _LIVE_PERCENT_CHANGE * 100.0
        assert sym_state["current_return"] != pytest.approx(live_feed_value), (
            "current_return must NOT equal the live feed's last_percent_change * 100; "
            "if it does, the True Shadow Return override has been bypassed"
        )


class TestShadowOverrideHwmRemainsFrozenPostTrigger:
    """Post-trigger, HWM is frozen — ``triggered_at_hwm`` is the reference
    value for Guard Alpha calculations. Two invariants pinned here:

      1. ``triggered_at_hwm`` is UNCHANGED across a tick that runs the override.
      2. ``high_water_mark`` stays at the sentinel ``-999.0`` (it is NOT
         advanced) — line 519's guard ``not bot_state[symphony_id]["triggered"]``
         protects this; we pin that the guard is in place.
    """

    def test_hwm_fields_are_not_mutated_by_override_tick(self, patched_environment):
        env = patched_environment

        # Seed live values that, absent the HWM-frozen guard, WOULD push the
        # HWM upward (live percent_change=50% is far above the frozen 3% HWM).
        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=_LIVE_PERCENT_CHANGE)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(_LIVE_LAST_PRICE)
        env["db"].load_state.return_value = _seed_triggered_state()

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

        # Frozen HWM unchanged — derived from seed fixture.
        assert sym_state["triggered_at_hwm"] == pytest.approx(_FROZEN_TRIGGERED_AT_HWM, rel=1e-9), (
            "triggered_at_hwm must NOT mutate on subsequent ticks post-trigger"
        )

        # Live HWM stays at the sentinel -999.0 (post-trigger reset value).
        # If line 519's ``not triggered`` guard breaks, high_water_mark would
        # advance to the override's current_return (~4.5%).
        assert sym_state["high_water_mark"] == pytest.approx(-999.0, rel=1e-9), (
            "high_water_mark must stay at -999.0 sentinel post-trigger; "
            "rising-HWM branch must be guarded by ``not triggered``"
        )

        # Frozen baseline return ALSO unchanged.
        assert sym_state["triggered_at_return"] == pytest.approx(
            _FROZEN_TRIGGERED_AT_RETURN, rel=1e-9
        )


class TestShadowOverrideDoesNotRefireDiscordAlert:
    """A symphony that is already ``triggered=True`` MUST NOT fire another
    Discord exit alert on subsequent ticks. The alert (line 862) is inside
    the ``if success`` branch of the execution queue — which itself is only
    reached if the per-symphony loop appends to ``execution_queue``, which
    requires one of {is_trailing_stop_hit, tp_triggered_now, is_vwap_broken,
    is_vwap_bleed_broken}.

    Post-trigger, ``armed=False`` and ``is_triggered=True`` are passed into
    ``compute_exit_confirmation``, so a properly-implemented engine will not
    re-fire the trailing stop. We let the REAL math engine run to pin this
    end-to-end (no mocking of compute_exit_confirmation).
    """

    def test_already_triggered_symphony_does_not_re_fire_discord_alert(self, patched_environment):
        env = patched_environment

        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=_LIVE_PERCENT_CHANGE)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(_LIVE_LAST_PRICE)
        env["db"].load_state.return_value = _seed_triggered_state()

        # NO patching of compute_exit_confirmation — real math runs.
        alpha_bot_execution.main()

        # The exit-alert call site (line 862) must not have been hit.
        env["reporting"].send_discord_alert.assert_not_called()

        # Also pin that the symphony is STILL triggered (the override branch
        # ran and did not mutate the trigger flag back to False).
        save_calls = env["db"].save_state.call_args_list
        assert save_calls
        final_state = save_calls[-1].args[0]
        assert final_state[_SYMPHONY_ID]["triggered"] is True


class TestShadowOverrideUsesFrozenHoldingsNotLiveHoldings:
    """The override iterates ``bot_state[symphony_id]["current_holdings"]``
    (FROZEN at trigger), NOT the live ``sym["holdings"]`` from the Composer
    fetch. Pin this by making the LIVE holdings list differ from the frozen
    one — if the override accidentally uses live holdings, post_trigger_move
    would be computed against a different basket and the saved current_return
    would diverge from the frozen-basket recompute.
    """

    def test_override_iterates_frozen_basket_not_live_basket(self, patched_environment):
        env = patched_environment

        # LIVE symphony has a DIFFERENT holding (e.g. Composer rebalanced into
        # a different ticker after the trigger — exactly the scenario the
        # override exists to handle). The frozen basket is SPY @ 1.0; live
        # reports QQQ @ 1.0.
        live_payload = _make_symphony_payload(last_percent_change=_LIVE_PERCENT_CHANGE)
        live_payload["holdings"] = [{"ticker": "QQQ", "allocation": 1.0}]
        env["fetch_symphony_stats"].return_value = [live_payload]

        # live_vwaps must include BOTH so the override can find _TICKER (frozen
        # basket member) AND QQQ doesn't crash a fallthrough. Use distinguishable
        # prices: SPY moves +2%, QQQ moves +10%. If the override mistakenly used
        # the live basket, post_trigger_move would reflect QQQ's +10%.
        env["fetch_intraday_vwaps"].return_value = {
            _TICKER: {"vwap": _LIVE_LAST_PRICE, "last_price": _LIVE_LAST_PRICE},
            "QQQ": {"vwap": 550.0, "last_price": 550.0},
        }

        env["db"].load_state.return_value = _seed_triggered_state()

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
        assert save_calls
        final_state = save_calls[-1].args[0]
        sym_state = final_state[_SYMPHONY_ID]

        # Recompute the EXPECTED value using the FROZEN basket (SPY).
        expected_frozen_basket = _expected_override_return(
            p_start=_FROZEN_TRIGGER_PRICE,  # frozen
            p_now=_LIVE_LAST_PRICE,  # live SPY
        )
        # The DECOY value: what we'd see if override used the LIVE basket (QQQ).
        # QQQ has no entry in trigger_prices, so the inner ``if t in trigger_prices``
        # guard would skip every iteration → post_trigger_move == 0 →
        # current_return == triggered_at_return (frozen baseline alone).
        # Either way, the expected value comes from the FROZEN basket — and it
        # is NOT a function of QQQ's live price. Pin via the frozen-basket
        # recompute.
        assert sym_state["current_return"] == pytest.approx(expected_frozen_basket, rel=1e-9), (
            "override must compute post_trigger_move against the FROZEN basket "
            "(current_holdings + trigger_prices), NOT the live Composer holdings"
        )


# =============================================================================
# FU3 — EOD post-mortem branch
# =============================================================================

# The EOD branch fires when:
#   * weekday AND  market_close (16:00) <= current_time <= post_mortem_cutoff (16:05)
#   OR
#   * force_run AND weekday >= 5  (Sat/Sun manual run)
#
# Within the branch:
#   * If post_mortem_run == current_date_str → early return (idempotency).
#   * Otherwise: generate_eod_snapshot, set the flag, conditionally call
#     autotuner.run_autotuner if weekday >= 4 (Fri/Sat/Sun) or force_run.


def _configure_eod_environment(env, fixed_dt):
    """Re-patch ``get_current_et`` on the existing harness so the EOD time gate
    fires. The harness already patches ``get_current_et`` with a fixed Wed
    intraday clock; we override that within the test's ``with`` block.

    Returns nothing — caller wraps the test body in the patch context.
    """
    # No-op helper; the actual patch is applied per-test via patch.object.
    return None


class TestEodPostMortemInvokesSnapshotAfterMarketClose:
    """On a weekday between 16:00 and 16:05 ET, ``main()`` MUST call
    ``reporting.generate_eod_snapshot`` exactly once with the bot_state and
    the current date string.
    """

    def test_eod_window_triggers_generate_eod_snapshot(self, patched_environment):
        env = patched_environment

        # Empty symphonies cache is fine — the EOD branch loops over it but
        # the snapshot call itself does not depend on having symphonies.
        env["fetch_symphony_stats"].return_value = []
        # post_mortem_run is unset → branch should fire.
        env["db"].load_state.return_value = {"date": _FRIDAY_EOD.strftime("%Y-%m-%d")}

        with (
            patch.object(alpha_bot_execution, "get_current_et", return_value=_FRIDAY_EOD),
            patch.object(alpha_bot_execution.autotuner, "run_autotuner", return_value=None),
        ):
            alpha_bot_execution.main()

        # generate_eod_snapshot called exactly once with is_post_rebalance=True.
        env["reporting"].generate_eod_snapshot.assert_called_once()
        snap_kwargs = env["reporting"].generate_eod_snapshot.call_args.kwargs
        assert snap_kwargs.get("is_post_rebalance") is True, (
            "EOD post-mortem must invoke generate_eod_snapshot with "
            "is_post_rebalance=True; False is reserved for the 15:53 blackout"
        )

        # The post_mortem_run flag must have been written to the saved state
        # — this is what guards idempotency on subsequent ticks (line 407).
        save_calls = env["db"].save_state.call_args_list
        assert save_calls, "save_state was never called"
        final_state = save_calls[-1].args[0]
        expected_date = _FRIDAY_EOD.strftime("%Y-%m-%d")
        assert final_state.get("post_mortem_run") == expected_date, (
            "post_mortem_run must be set to the current date string to guard "
            "against re-running within the same day"
        )


class TestEodPostMortemFridayTriggersAutotuner:
    """On Friday after market close, ``autotuner.run_autotuner`` MUST be
    invoked. Line 415: ``if current_et.weekday() >= 4 or force_run``.
    Friday is weekday 4 (passes the gate).
    """

    def test_friday_eod_invokes_autotuner_run(self, patched_environment):
        env = patched_environment

        env["fetch_symphony_stats"].return_value = []
        env["db"].load_state.return_value = {"date": _FRIDAY_EOD.strftime("%Y-%m-%d")}

        with (
            patch.object(alpha_bot_execution, "get_current_et", return_value=_FRIDAY_EOD),
            patch.object(
                alpha_bot_execution.autotuner, "run_autotuner", return_value={}
            ) as mock_autotuner,
        ):
            alpha_bot_execution.main()

        # autotuner.run_autotuner was called exactly once.
        mock_autotuner.assert_called_once()

        # Pin the call signature — the engine MUST pass the bot_state, the
        # date string, and ACCOUNT_UUIDS positionally, with is_forced kwarg.
        call = mock_autotuner.call_args
        # Date string is the second positional arg (after bot_state).
        assert call.args[1] == _FRIDAY_EOD.strftime("%Y-%m-%d"), (
            "autotuner.run_autotuner must receive the current_date_str as its "
            "second positional argument — pin prevents an arg-order regression"
        )
        # is_forced kwarg must be False on natural Friday close (no --force).
        assert call.kwargs.get("is_forced") is False, (
            "is_forced must be False on a natural Friday EOD (no --force flag)"
        )


class TestEodPostMortemMidWeekSkipsAutotuner:
    """On Monday through Thursday after market close, the EOD snapshot runs
    but ``autotuner.run_autotuner`` MUST NOT be invoked. Line 415's gate
    ``weekday() >= 4`` excludes weekdays 0..3.
    """

    def test_monday_eod_runs_snapshot_but_skips_autotuner(self, patched_environment):
        env = patched_environment

        env["fetch_symphony_stats"].return_value = []
        env["db"].load_state.return_value = {"date": _MONDAY_EOD.strftime("%Y-%m-%d")}

        with (
            patch.object(alpha_bot_execution, "get_current_et", return_value=_MONDAY_EOD),
            patch.object(alpha_bot_execution.autotuner, "run_autotuner") as mock_autotuner,
        ):
            alpha_bot_execution.main()

        # Snapshot fires (Monday is still a weekday inside the EOD window).
        env["reporting"].generate_eod_snapshot.assert_called_once()

        # But the weekly autotuner does NOT.
        mock_autotuner.assert_not_called()


class TestEodPostMortemIdempotentWithinSameDay:
    """Once the EOD post-mortem has run on a given calendar date, subsequent
    ticks within the same date MUST short-circuit BEFORE re-invoking
    ``generate_eod_snapshot``. Line 390-393: if
    ``bot_state["post_mortem_run"] == current_date_str``, return.

    Sim: pre-seed ``post_mortem_run`` to today's date and confirm the snapshot
    is NOT regenerated.
    """

    def test_second_tick_within_same_day_does_not_regenerate_snapshot(self, patched_environment):
        env = patched_environment
        date_str = _FRIDAY_EOD.strftime("%Y-%m-%d")

        env["fetch_symphony_stats"].return_value = []
        # State already records today's post-mortem complete.
        env["db"].load_state.return_value = {
            "date": date_str,
            "post_mortem_run": date_str,
        }

        with (
            patch.object(alpha_bot_execution, "get_current_et", return_value=_FRIDAY_EOD),
            patch.object(alpha_bot_execution.autotuner, "run_autotuner") as mock_autotuner,
        ):
            alpha_bot_execution.main()

        # generate_eod_snapshot MUST NOT have been called — the idempotency
        # guard at line 390-393 returns early.
        env["reporting"].generate_eod_snapshot.assert_not_called()

        # autotuner also skipped (same early return path).
        mock_autotuner.assert_not_called()

        # Lock cycle still well-formed (acquire + release).
        env["db"].acquire_lock.assert_called_once()
        env["db"].release_lock.assert_called_once()
