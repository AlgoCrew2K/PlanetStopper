"""
M4 (math-audit MEDIUM, CONFIRMED-REVISED) — RED tests for the triggered-path
Monte-Carlo ``mc_history`` pollution.

Audit context
-------------
On a TRIGGERED symphony cycle ``alpha_bot_execution.main()`` swaps the live
holdings for ``current_holdings`` (alpha_bot_execution.py:1191-1192):

    holdings = bot_state[symphony_id].get("current_holdings", [])

``current_holdings`` rows carry only ``ticker`` + ``allocation`` — they are
written WITHOUT ``last_percent_change`` (alpha_bot_execution.py:1558-1561).
``run_monte_carlo`` then computes ``current_symphony_return`` by summing only
holdings that HAVE ``last_percent_change`` (math_engine.py:1163-1167), so a
triggered basket collapses to ``current_symphony_return == 0.0``. The MC engine
returns a real ``prob_underperforming`` computed against that FICTIONAL 0% — and
the unguarded append at alpha_bot_execution.py:1349-1352:

    if mc_available:
        bot_state[symphony_id]["mc_history"].append(prob_underperforming)

pushes that fictional-baseline probability into the rolling history.

REVISED scope (synthesis VERDICT, M4 row)
-----------------------------------------
The arm gate (alpha_bot_execution.py:1326-1331) and disarm gate (:1337) are
ALREADY ``not triggered``-guarded, so the finding's "arm/disarm corruption" is
overstated. ONLY the ``mc_history`` append is unguarded. The minimal fix is a
triggered-guard on the append — nothing else.

WHAT THESE TESTS ASSERT
-----------------------
1. A TRIGGERED symphony cycle does NOT grow ``mc_history`` — the fictional-0%
   probability must never enter the rolling buffer. (THE M4 fix.)
2. An UNTRIGGERED symphony cycle WITH sufficient MC history STILL appends a
   real probability — the fix must not break the healthy path (regression).
3. (Provenance guard) The 40-day history fixture genuinely drives the REAL
   ``run_monte_carlo`` to a non-None probability, so test #1 is exercising the
   pollution path (append reached) rather than the insufficient-MC short-circuit.

PROVENANCE / no-mock-the-math-engine
------------------------------------
``run_monte_carlo`` is NOT mocked — it runs on a real 40-day SPY history
(>= MC_MIN_HISTORY_DAYS + MC_VOL_WINDOW_DAYS - 1 = 39, so the eligible pool is
non-degenerate and the engine returns a real probability, not the sentinel).
Only network / clock / DB / Discord are mocked. No producer-computed value is
hardcoded — assertions are on ``mc_history`` LENGTH transitions and membership,
never on the probability value itself.

RED-VERIFICATION
----------------
Test #1 is RED against the current code (8d81568): the triggered cycle reaches
the unguarded append at alpha_bot_execution.py:1350 and ``mc_history`` grows by
one. It goes GREEN once the append is guarded with ``and not triggered``.
Tests #2 and #3 are GREEN now and must STAY green after the fix (regression).
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
    _FIXED_ET = datetime(2025, 5, 14, 11, 30, 0, tzinfo=_ET)  # Wed 11:30 ET
except Exception:  # pragma: no cover -- tzdata-less fallback
    _FIXED_ET = datetime(2025, 5, 14, 11, 30, 0, tzinfo=timezone(timedelta(hours=-4)))


# Shape-only identifiers — the engine only dict-key-matches these.
_SYMPHONY_ID = "sym-m4-triggered-mc-001"
_ACTUAL_SYMPHONY_ID = "actual-sym-m4-triggered-mc-001"
_ACCOUNT_ID = "acct-m4-triggered-mc-001"
_SYMPHONY_NAME = "M4 Triggered MC History Symphony"
_TICKER = "SPY"

# Raw-day floor for a non-degenerate MC eligible pool. Derived from the engine's
# own constants (NOT hardcoded): the first MC_VOL_WINDOW_DAYS - 1 days are
# excluded from the kNN pool, so a sufficient history needs at least
# MC_MIN_HISTORY_DAYS + (MC_VOL_WINDOW_DAYS - 1) raw days. We use floor + 1.
_SUFFICIENT_HISTORY_DAYS = (
    math_engine.MC_MIN_HISTORY_DAYS + (math_engine.MC_VOL_WINDOW_DAYS - 1) + 1
)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------
def _make_sufficient_history(latest_date_str: str, n_days: int) -> dict:
    """A multi-day Alpaca-shaped SPY history with >= the MC raw-day floor.

    Returns a dict keyed by ascending date strings ending at ``latest_date_str``.
    Each day carries a small varied ``daily_ret`` so the kNN pool has spread
    (a zero-variance pool would standardise to a degenerate distance). Values
    are arbitrary fixture inputs, not producer outputs.
    """
    base = datetime.strptime(latest_date_str, "%Y-%m-%d")
    history: dict = {}
    for i in range(n_days):
        # Oldest first; day 0 is (n_days-1) days before latest.
        day = base - timedelta(days=(n_days - 1 - i))
        day_str = day.strftime("%Y-%m-%d")
        # Deterministic small return ladder in [-0.01, +0.01], varied per day.
        ret = ((i % 7) - 3) / 300.0
        close = 500.0 * (1.0 + ret)
        history[day_str] = {
            "SPY": {
                "c": close,
                "daily_ret": ret,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
            }
        }
    return history


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


def _seed_state(*, triggered: bool, mc_history: list, **extras) -> dict:
    """Minimal pre-seeded bot_state for the fixture symphony.

    When ``triggered=True`` the state also carries the post-trigger fields the
    True-Shadow-Return override reads (alpha_bot_execution.py:1191-1204):
    ``current_holdings`` (no last_percent_change), ``triggered_at_return`` and
    ``trigger_prices``.
    """
    sym = {
        "high_water_mark": -999.0 if triggered else 0.0,
        "shadow_hwm": 0.0,
        "prev_return": 0.0,
        "armed": False,
        "tp_armed": False,
        "para_armed": False,
        "triggered": triggered,
        "mc_history": list(mc_history),
        "below_stop_count": 0,
        "above_tp_count": 0,
        "vwap_ticks": 0,
        "vwap_bleed_ticks": 0,
        "breakeven_locked": False,
        "hwm_hold_ticks": 0,
    }
    if triggered:
        sym["current_holdings"] = [{"ticker": _TICKER, "allocation": 1.0}]
        sym["triggered_at_return"] = 5.0
        sym["trigger_prices"] = {_TICKER: 500.0}
        sym["triggered_reason"] = "Trailing Stop"
    sym.update(extras)
    return {
        "date": _FIXED_ET.strftime("%Y-%m-%d"),
        "last_execution_mode": False,
        _SYMPHONY_ID: sym,
    }


@pytest.fixture
def patched_environment():
    """Bundle every patch main()'s pipeline needs — network, clock, DB, Discord —
    leaving the math engine REAL. Yields a dict of mocks for per-test config."""
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
        mock_fetch_hist.return_value = _make_sufficient_history(
            _FIXED_ET.strftime("%Y-%m-%d"), _SUFFICIENT_HISTORY_DAYS
        )
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
# Provenance guard — the fixture genuinely reaches the append (non-None MC)
# ---------------------------------------------------------------------------
def test_sufficient_history_fixture_yields_real_mc_probability() -> None:
    """Guard: the 40-day fixture history must drive the REAL run_monte_carlo to
    a non-None probability so the M4 tests exercise the mc_history APPEND path
    (rather than the insufficient-MC short-circuit, which never reaches it).

    Uses untriggered-shaped holdings (with last_percent_change) so this checks
    only that the history is long enough — independent of the triggered defect.
    """
    history = _make_sufficient_history(_FIXED_ET.strftime("%Y-%m-%d"), _SUFFICIENT_HISTORY_DAYS)
    holdings = [{"ticker": _TICKER, "allocation": 1.0, "last_percent_change": 0.0}]
    result = math_engine.run_monte_carlo(
        holdings,
        history,
        0.1,
        simulation_paths=alpha_bot_execution.SIMULATION_PATHS,
        neighbor_k=alpha_bot_execution.NEIGHBOR_K,
        seed=42,
    )
    assert result is not math_engine.MC_INSUFFICIENT_HISTORY_SENTINEL, (
        "The sufficient-history fixture must drive run_monte_carlo to a real "
        f"probability (not the insufficient sentinel); got {result!r}. The M4 "
        "append-pollution tests depend on the append being reachable."
    )
    assert isinstance(result, float), (
        f"run_monte_carlo should return a float probability on sufficient "
        f"history; got {type(result).__name__}"
    )


# ---------------------------------------------------------------------------
# THE M4 fix — triggered cycle must NOT pollute mc_history
# ---------------------------------------------------------------------------
def test_triggered_cycle_does_not_append_to_mc_history(
    patched_environment,
) -> None:
    """M4. On a TRIGGERED cycle the holdings are ``current_holdings`` (no
    ``last_percent_change``) → run_monte_carlo's ``current_symphony_return``
    collapses to 0.0 → the resulting probability is computed against a FICTIONAL
    0% baseline. That probability must NOT be appended to ``mc_history``.

    The state is seeded with a non-empty ``mc_history`` of two real prior
    probabilities; after a triggered cycle the buffer LENGTH must be unchanged
    (no fictional-baseline entry added). Length, not value, is asserted —
    no producer-computed value is hardcoded.

    RED against the current code (8d81568): the append at
    alpha_bot_execution.py:1349-1352 is guarded only by ``if mc_available`` —
    NOT by ``not triggered`` — so the buffer grows by one. GREEN once the
    append also requires the symphony is not triggered.
    """
    env = patched_environment
    # Two real prior probabilities already in the rolling buffer. These are
    # arbitrary fixture inputs (valid 0-100 probabilities), not producer values.
    seed_history = [42.0, 58.0]
    env["fetch_symphony_stats"].return_value = [_make_symphony_payload(last_percent_change=-0.04)]
    env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(495.0)
    env["db"].load_state.return_value = _seed_state(triggered=True, mc_history=seed_history)

    alpha_bot_execution.main()

    sym_state = _final_symphony_state(env)
    mc_history = sym_state.get("mc_history", [])
    assert len(mc_history) == len(seed_history), (
        f"mc_history grew on a TRIGGERED cycle: {seed_history!r} -> "
        f"{mc_history!r}. On a triggered cycle the holdings lack "
        f"last_percent_change, so run_monte_carlo's current_symphony_return "
        f"collapses to 0.0 and the probability is computed against a fictional "
        f"0% baseline. That value must NOT enter the rolling MC history "
        f"(alpha_bot_execution.py:1349-1352 append needs a `not triggered` "
        f"guard)."
    )
    assert mc_history == seed_history, (
        f"mc_history contents changed on a triggered cycle: expected the seeded "
        f"{seed_history!r} untouched, got {mc_history!r}."
    )


# ---------------------------------------------------------------------------
# Regression — untriggered cycle WITH sufficient MC still appends
# ---------------------------------------------------------------------------
def test_untriggered_cycle_still_appends_real_mc_probability(
    patched_environment,
) -> None:
    """Regression. An UNTRIGGERED symphony with a sufficient MC history must
    STILL append a real probability to ``mc_history`` — the M4 triggered-guard
    must not suppress the healthy path.

    Holdings here carry a genuine ``last_percent_change`` (the live Composer
    payload), so run_monte_carlo computes a real ``current_symphony_return`` and
    returns a real probability that is appended. The buffer LENGTH must grow by
    exactly one. Length + None-absence are asserted, not the value.

    GREEN now; must STAY green after the M4 fix.
    """
    env = patched_environment
    seed_history = [40.0]
    env["fetch_symphony_stats"].return_value = [_make_symphony_payload(last_percent_change=-0.04)]
    env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(495.0)
    env["db"].load_state.return_value = _seed_state(
        triggered=False, mc_history=seed_history, high_water_mark=0.0
    )

    alpha_bot_execution.main()

    sym_state = _final_symphony_state(env)
    mc_history = sym_state.get("mc_history", [])
    assert len(mc_history) == len(seed_history) + 1, (
        f"mc_history did NOT grow on an untriggered, sufficient-MC cycle: "
        f"{seed_history!r} -> {mc_history!r}. The healthy path must still append "
        f"the real per-cycle probability; the M4 triggered-guard must only "
        f"suppress the TRIGGERED path."
    )
    assert None not in mc_history, (
        f"mc_history contains the None sentinel on a sufficient-MC cycle: "
        f"{mc_history!r}. A real probability was expected."
    )
    # The seeded prior entry must be preserved (rolling buffer, not replaced).
    assert mc_history[: len(seed_history)] == seed_history, (
        f"the seeded prior history was not preserved: expected a prefix of "
        f"{seed_history!r}, got {mc_history!r}."
    )
