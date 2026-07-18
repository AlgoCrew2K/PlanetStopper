"""
RED tests -- F7 AC-4: the MAPERF-15 passive staleness tripwire.

Cycle context (feature-plans/math-f7.md + team-lead's LOCKED contract,
2026-07-18)
---------------------------------------------------------------------------
MAPERF-15 (docs/research/composer/maperf15-post-sale-lpc-semantics.md) found
that Composer's ``last_percent_change`` field keeps "tracking logic" (moving)
even after a symphony is guard-sold to cash -- this is the load-bearing
assumption behind ``DE-GUARD-ALPHA-SAVED-001``'s $-saved math. If Composer
ever silently changed that behavior (the field freezing post-sale instead),
every live-sold symphony would structurally book ~$0 saved with no signal
that anything had gone wrong. AC-4 adds a passive, off-hot-path detector: if
a TRIGGERED symphony's raw ``last_percent_change`` (the data-phase
``current_return`` at alpha_bot_execution.py:769, NOT the action-phase
True-Shadow-Return override at :1189-1204, which is a completely separate,
engine-reconstructed value) is bit-static across N consecutive MARKET-HOURS
cycles, log ONE loud warning naming the dependent feature -- never raise,
never gate anything.

LOCKED CONTRACT (team-lead, 2026-07-18)
----------------------------------------
* Constant: ``alpha_bot_execution.MAPERF15_STATIC_LPC_CYCLES`` (int; value +
  source-comment are the implementer's choice -- these tests read the
  constant dynamically and never hardcode a cycle count).
* Channel: ``logging.warning`` (stdlib), asserted via pytest's ``caplog`` --
  NOT ``database.log_symphony_event`` (rationale: can't-raise by
  construction, no DB write on the data-phase path, no user-facing-feed
  pollution).
* Message: must contain the literal substring ``"DE-GUARD-ALPHA-SAVED-001"``.
* Gate: real market-hours state (checked via the same market-open
  discriminator the engine already uses, not "did the loop execute" -- a
  ``--force`` run over a real weekend still reaches the loop, so the gate
  must actively consult market state) AND TRIGGERED-only (an untriggered
  symphony sitting flat during genuinely quiet market hours is not
  suspicious and must not fire).
* Fires exactly ONCE at cycle N, not at N-1, stays silent (latched) through
  at least N+5, and never raises across the whole run.

PROVENANCE
----------
This file does not need a real, sufficient MC history -- the tripwire
watches the DATA-PHASE raw ``last_percent_change`` read from the mocked
Composer payload, not any Monte Carlo output. ``historical_data`` is a
minimal fixture; MC availability is irrelevant to AC-4.

RED STATUS (HEAD a904374d, no F7 code yet)
-------------------------------------------
Every test in this file references
``alpha_bot_execution.MAPERF15_STATIC_LPC_CYCLES`` directly inside its own
function body (never at collection/fixture-setup time) -- since the
constant does not exist yet, every test currently fails with a clean
``AttributeError`` (a test FAILURE, not a collection ERROR). All tests go
GREEN together once the constant + tripwire are implemented.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import alpha_bot_execution
import math_engine
from tests.execution.test_f7_ac1_persist_guard import (
    _ACCOUNT_ID,
    _SYMPHONY_ID,
    _SYMPHONY_NAME,
    _TICKER,
    _make_history,
    _make_symphony_payload,
    _make_vwap_payload,
    _seed_state,
)

_DE_GUARD_ALPHA_SAVED_MARKER = "DE-GUARD-ALPHA-SAVED-001"

try:
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover -- tzdata-less fallback
    _ET = timezone(timedelta(hours=-4))

# A regular-hours Wednesday, starting just after EXECUTION_START_TIME
# (default 09:30 ET) so every cycle in a long run stays inside market hours
# even for a generous threshold value (well before the 15:53 rebalance
# blackout / 16:00 close).
_MARKET_DAY_START_ET = datetime(2025, 5, 14, 9, 35, 0, tzinfo=_ET)
# A genuinely closed market on the SAME weekday (pre-market, well before the
# 09:30 REAL_MARKET_OPEN gate) -- NOT a Sat/Sun. A real Sat/Sun + force_run
# combination trips a DIFFERENT branch entirely (the EOD-postmortem-then
# -autotune block at alpha_bot_execution.py:958-1105, gated on
# ``force_run and current_et.weekday() >= 5``), which returns before ever
# reaching the per-symphony action-phase loop -- that would make the
# "closed market" regression test vacuous (it wouldn't exercise the
# tripwire's own market-state check at all, only prove an unrelated branch
# returns early). Pre-market on a trading weekday is genuinely closed
# (before 09:30) yet is NOT weekday() >= 5, so force_run bypasses only the
# closed-market gate (:636, :952) and lands in the real action-phase loop --
# a true test of the tripwire's discriminator.
_PRE_MARKET_START_ET = datetime(2025, 5, 14, 4, 0, 0, tzinfo=_ET)


@pytest.fixture
def multi_cycle_environment():
    """Like tests/execution/test_f7_ac1_persist_guard.py's patched_environment,
    but exposes the ``get_current_et`` mock (reconfigurable per test with a
    ``side_effect`` list of advancing timestamps) and defaults to NO
    ``--force`` flag."""
    with (
        patch.object(alpha_bot_execution, "database") as mock_db,
        patch.object(alpha_bot_execution, "reporting") as mock_reporting,
        patch.object(alpha_bot_execution, "autotuner") as mock_autotuner,
        patch.object(alpha_bot_execution, "fetch_symphony_stats") as mock_fetch_sym,
        patch.object(alpha_bot_execution, "fetch_alpaca_history") as mock_fetch_hist,
        patch.object(alpha_bot_execution, "fetch_intraday_vwaps") as mock_fetch_vwap,
        patch.object(alpha_bot_execution, "get_current_et") as mock_get_et,
        patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", [_ACCOUNT_ID]),
        patch.object(alpha_bot_execution, "COMPOSER_KEY_ID", "test-composer-key"),
        patch.object(alpha_bot_execution, "ALPACA_KEY", "test-alpaca-key"),
        patch.object(alpha_bot_execution, "LIVE_EXECUTION", False),
        patch.object(alpha_bot_execution.time, "sleep"),
        patch.object(alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py"]),
    ):
        # The weekend/force-run regression test lands on Sat 2025-05-17, which
        # trips the Friday/weekend "Starting autotune..." branch
        # (alpha_bot_execution.py:1105) when force_run=True. autotuner.run_autotuner
        # is unrelated to AC-4 and would otherwise hit the (mocked) database with
        # non-DB-shaped MagicMock args -- mock it out entirely, like reporting.
        mock_autotuner.run_autotuner.return_value = {}
        mock_db.acquire_lock.return_value = True
        mock_db.get_symphony_strategy.return_value = {"params": {}, "locked_vars": {}}
        mock_db.normalize_name.side_effect = lambda n: n.strip().lower()
        mock_db.wipe_transient_state.side_effect = lambda s: s

        mock_fetch_sym.return_value = []
        mock_fetch_hist.return_value = _make_history(_MARKET_DAY_START_ET.strftime("%Y-%m-%d"))
        mock_fetch_vwap.return_value = {}

        yield {
            "db": mock_db,
            "reporting": mock_reporting,
            "fetch_symphony_stats": mock_fetch_sym,
            "fetch_alpaca_history": mock_fetch_hist,
            "fetch_intraday_vwaps": mock_fetch_vwap,
            "get_current_et": mock_get_et,
        }


def _run_n_cycles(
    env, clock_times, *, symphony_payload, vwap_payload, initial_state
) -> tuple[dict, dict]:
    """Run alpha_bot_execution.main() once per timestamp in ``clock_times``,
    chaining bot_state and chart_history across calls the way the real
    per-minute scheduler does (each cycle's save becomes the next cycle's
    load). Returns the final (bot_state, chart_history)."""
    state = initial_state
    chart = {"date": clock_times[0].strftime("%Y-%m-%d"), "symphonies": {}}
    env["get_current_et"].side_effect = list(clock_times)
    env["fetch_symphony_stats"].return_value = [symphony_payload]
    env["fetch_intraday_vwaps"].return_value = vwap_payload
    for _ in clock_times:
        env["db"].load_state.return_value = state
        env["db"].load_chart_history.return_value = chart
        alpha_bot_execution.main()
        state = env["db"].save_state.call_args_list[-1].args[0]
        chart = env["db"].save_chart_history.call_args_list[-1].args[0]
    return state, chart


def _clock_run(start_et: datetime, n: int) -> list[datetime]:
    return [start_et + timedelta(minutes=i) for i in range(n)]


def _tripwire_warning_records(caplog) -> list:
    return [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and _DE_GUARD_ALPHA_SAVED_MARKER in r.getMessage()
    ]


# ---------------------------------------------------------------------------
# Fires exactly once, at the threshold, latched through N+5
# ---------------------------------------------------------------------------
def test_no_warning_before_threshold_cycles(multi_cycle_environment, caplog) -> None:
    """N-1 consecutive market-hours cycles of bit-static current_return for a
    TRIGGERED symphony must NOT fire the tripwire yet."""
    caplog.set_level(logging.WARNING)
    env = multi_cycle_environment
    threshold = alpha_bot_execution.MAPERF15_STATIC_LPC_CYCLES
    clock = _clock_run(_MARKET_DAY_START_ET, threshold - 1)
    payload = _make_symphony_payload(last_percent_change=0.0123)
    _run_n_cycles(
        env,
        clock,
        symphony_payload=payload,
        vwap_payload=_make_vwap_payload(500.0),
        initial_state=_seed_state(triggered=True, high_water_mark=5.0),
    )
    fires = _tripwire_warning_records(caplog)
    assert not fires, (
        f"AC-4 FAIL: tripwire fired after only {threshold - 1} static cycles "
        f"(< MAPERF15_STATIC_LPC_CYCLES={threshold}); records: "
        f"{[r.getMessage() for r in fires]!r}"
    )


def test_warning_fires_once_at_threshold_and_stays_latched(multi_cycle_environment, caplog) -> None:
    """Exactly at the threshold, ONE warning fires naming
    DE-GUARD-ALPHA-SAVED-001; continuing for 5 more static cycles must NOT
    produce additional warnings (latched, no spam)."""
    caplog.set_level(logging.WARNING)
    env = multi_cycle_environment
    threshold = alpha_bot_execution.MAPERF15_STATIC_LPC_CYCLES
    clock = _clock_run(_MARKET_DAY_START_ET, threshold + 5)
    payload = _make_symphony_payload(last_percent_change=0.0123)
    _run_n_cycles(
        env,
        clock,
        symphony_payload=payload,
        vwap_payload=_make_vwap_payload(500.0),
        initial_state=_seed_state(triggered=True, high_water_mark=5.0),
    )
    fires = _tripwire_warning_records(caplog)
    assert len(fires) == 1, (
        f"AC-4 FAIL: expected exactly ONE tripwire warning across "
        f"{threshold + 5} bit-static cycles (threshold={threshold}); got "
        f"{len(fires)}: {[r.getMessage() for r in fires]!r}"
    )
    assert _SYMPHONY_ID in fires[0].getMessage() or _SYMPHONY_NAME in fires[0].getMessage(), (
        f"AC-4 FAIL: the warning does not identify which symphony is stale: "
        f"{fires[0].getMessage()!r}"
    )


# ---------------------------------------------------------------------------
# Market-hours gate -- a real discriminator, not loop-reachability
# ---------------------------------------------------------------------------
def test_no_warning_when_market_closed_even_under_force_run(
    multi_cycle_environment, caplog
) -> None:
    """A ``--force`` run pre-market (real closed market, well before 09:30)
    still bypasses the closed-market early-return gate entirely
    (alpha_bot_execution.py:636-654, :952) and reaches the per-symphony loop
    -- so this is a genuine test of the tripwire's own market-state check,
    not a vacuous "the loop never ran" pass."""
    caplog.set_level(logging.WARNING)
    env = multi_cycle_environment
    threshold = alpha_bot_execution.MAPERF15_STATIC_LPC_CYCLES
    clock = _clock_run(_PRE_MARKET_START_ET, threshold + 5)
    payload = _make_symphony_payload(last_percent_change=0.0123)
    with patch.object(alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py", "--force"]):
        _run_n_cycles(
            env,
            clock,
            symphony_payload=payload,
            vwap_payload=_make_vwap_payload(500.0),
            initial_state=_seed_state(triggered=True, high_water_mark=5.0),
        )
    fires = _tripwire_warning_records(caplog)
    assert not fires, (
        f"AC-4 FAIL: tripwire fired while the market was genuinely closed "
        f"(weekend, --force bypass) -- the gate must check real market "
        f"state, not merely whether the loop executed. Records: "
        f"{[r.getMessage() for r in fires]!r}"
    )


# ---------------------------------------------------------------------------
# Triggered-only scope
# ---------------------------------------------------------------------------
def test_no_warning_for_untriggered_symphony_bit_static_market_hours(
    multi_cycle_environment, caplog
) -> None:
    """AC-4's own wording is 'a TRIGGERED symphony's current_return' -- an
    UNTRIGGERED symphony sitting flat during genuinely open market hours is
    unremarkable (low volatility happens) and must never fire."""
    caplog.set_level(logging.WARNING)
    env = multi_cycle_environment
    threshold = alpha_bot_execution.MAPERF15_STATIC_LPC_CYCLES
    clock = _clock_run(_MARKET_DAY_START_ET, threshold + 5)
    payload = _make_symphony_payload(last_percent_change=0.0123)
    _run_n_cycles(
        env,
        clock,
        symphony_payload=payload,
        vwap_payload=_make_vwap_payload(500.0),
        initial_state=_seed_state(triggered=False, armed=False, high_water_mark=0.0),
    )
    fires = _tripwire_warning_records(caplog)
    assert not fires, (
        f"AC-4 FAIL: tripwire fired for an UNTRIGGERED symphony -- AC-4 is "
        f"scoped to triggered symphonies only. Records: "
        f"{[r.getMessage() for r in fires]!r}"
    )


# ---------------------------------------------------------------------------
# Never raises
# ---------------------------------------------------------------------------
def test_tripwire_never_raises_across_a_long_run_with_edge_values(
    multi_cycle_environment, caplog
) -> None:
    """Adversarial: exactly-zero static current_return, and a triggered
    symphony whose frozen current_holdings is empty (no tickers at all) --
    the tripwire's own bookkeeping must not raise on either edge, across a
    run well past the threshold."""
    caplog.set_level(logging.WARNING)
    env = multi_cycle_environment
    threshold = alpha_bot_execution.MAPERF15_STATIC_LPC_CYCLES
    clock = _clock_run(_MARKET_DAY_START_ET, threshold + 5)
    payload = _make_symphony_payload(last_percent_change=0.0)
    state = _seed_state(triggered=True, high_water_mark=5.0)
    state[_SYMPHONY_ID]["current_holdings"] = []  # frozen basket with no tickers
    try:
        _run_n_cycles(
            env,
            clock,
            symphony_payload=payload,
            vwap_payload={},
            initial_state=state,
        )
    except Exception as exc:  # pragma: no cover -- the assertion below is the real check
        pytest.fail(
            f"AC-4 FAIL: the tripwire raised {type(exc).__name__}: {exc} on a "
            f"degenerate (empty-holdings, exactly-zero-return) fixture. AC-4 "
            f"requires the tripwire to NEVER raise."
        )
