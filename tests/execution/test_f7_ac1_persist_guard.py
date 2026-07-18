"""
RED tests -- F7 AC-1: post-trigger MC persist guard (both persist sites) +
the ArmProb console-print leak.

Cycle context (feature-plans/math-f7.md + ADDENDUM 2, ratified 2026-07-18)
---------------------------------------------------------------------------
After a guard exit fires, ``alpha_bot_execution.main()`` keeps computing and
persisting a Monte Carlo probability every cycle -- even for a TRIGGERED
symphony, whose holdings have been swapped for the frozen post-trigger
snapshot ("TRUE SHADOW RETURN OVERRIDE", :1189-1204). That frozen snapshot
lacks ``last_percent_change``, so ``run_monte_carlo``'s
``current_symphony_return`` collapses to a fictional 0% baseline -- the
resulting probability is real math, computed against a meaningless number.
Two sites persist that fabricated value unconditionally:

  * ``alpha_bot_execution.py:1549`` -- ``bot_state[sid]["mc_prob"]``
  * ``alpha_bot_execution.py:1598`` -- the per-cycle ``chart_history`` append

ADDENDUM 2 timing (PM ruling, commit a904374d): ``bot_state[sid]["triggered"]``
only flips True at ``:1808``, inside the LATER execution-queue processing
block -- AFTER both persist sites already ran this cycle. So on the
TRIGGERING cycle itself the flag is still False and the persisted value is
the engine's honest, real, pre-trigger MC reading; the guard must not touch
it. From the NEXT cycle onward the flag is already True when the loop
re-enters, the shadow-override applies, and the guard must suppress the
resulting fabricated value to an honest ``None`` sentinel at BOTH sites.

A third surface was ruled in during plan approval: the ArmProb console print
at ``alpha_bot_execution.py:1541-1544`` formats the LOCAL
``prob_underperforming`` variable directly, BEFORE the :1549 persist site
runs. A guard applied only to the bot_state assignment's right-hand side
would not touch this print -- it must independently show an honest marker
for a post-trigger cycle.

PROVENANCE / no-mock-the-math-engine
-------------------------------------
``run_monte_carlo`` is never mocked. The fixture history below (260 days,
NEIGHBOR_K resolution) was chosen empirically (see cycle notes) so specific
``last_percent_change`` fixture inputs land in known, non-degenerate
probability zones under the REAL production seed derivation
(``math_engine.derive_cycle_mc_seed(current_et.strftime("%Y%m%d_%H%M"))``,
not a bare literal seed). Only network / clock / DB / Discord are mocked.
No producer-computed probability VALUE is hardcoded in any assertion --
only its realness (non-None / float) or a self-consistent recomputation from
the value actually observed at runtime.

RED STATUS (HEAD a904374d, no F7 code yet)
-------------------------------------------
* test_triggering_cycle_persists_real_mc_at_both_sites -- GREEN already
  (paired regression half of AC-1; nothing here is broken pre-fix).
* test_post_trigger_cycle_persists_sentinel_at_both_sites -- RED (both sites
  currently persist the fabricated real float; no sentinel exists yet).
* test_untriggered_low_mc_does_not_arm_and_is_not_leaked_sentinel -- GREEN
  already (paired regression; proves the guard has no scope to leak into
  this path in the first place).
* test_triggering_cycle_armprob_print_keeps_real_number -- GREEN already.
* test_post_trigger_armprob_print_omits_fabricated_number -- RED (the print
  is unconditional today).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import alpha_bot_execution
import math_engine

# ---------------------------------------------------------------------------
# Fixed clock -- a regular-hours weekday so time / blackout gates are bypassed.
# ---------------------------------------------------------------------------
try:
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
    _FIXED_ET = datetime(2025, 5, 14, 11, 30, 0, tzinfo=_ET)  # Wed 11:30 ET
except Exception:  # pragma: no cover -- tzdata-less fallback
    _FIXED_ET = datetime(2025, 5, 14, 11, 30, 0, tzinfo=timezone(timedelta(hours=-4)))

# Shape-only identifiers -- the engine only dict-key-matches these.
_SYMPHONY_ID = "sym-f7-ac1-001"
_ACTUAL_SYMPHONY_ID = "actual-sym-f7-ac1-001"
_ACCOUNT_ID = "acct-f7-ac1-001"
_SYMPHONY_NAME = "F7 AC1 Persist Guard Symphony"
_TICKER = "SPY"

# History shape tuned empirically (math_engine.run_monte_carlo probed directly
# against the REAL production seed derivation -- derive_cycle_mc_seed, not a
# bare literal seed) so specific last_percent_change fixture inputs land in
# known, non-degenerate probability zones. 260 days gives NEIGHBOR_K=150
# (the production default) enough candidate days to resolve into fine-grained
# buckets -- a short history (as in the M4 file) collapses too coarsely for
# this file's purposes.
_HISTORY_MODULUS = 23
_HISTORY_SPREAD = 300.0
_HISTORY_DAYS = 260


def _make_history(latest_date_str: str, n_days: int = _HISTORY_DAYS) -> dict:
    """Alpaca-shaped SPY history: a repeating varied-return ladder ending at
    ``latest_date_str``. Values are arbitrary fixture inputs, not producer
    outputs."""
    base = datetime.strptime(latest_date_str, "%Y-%m-%d")
    history: dict = {}
    for i in range(n_days):
        day = base - timedelta(days=(n_days - 1 - i))
        day_str = day.strftime("%Y-%m-%d")
        ret = ((i % _HISTORY_MODULUS) - _HISTORY_MODULUS // 2) / _HISTORY_SPREAD
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
    """Composer-shaped symphony dict for the fan-out loop.

    ``last_percent_change`` is set BOTH at the symphony level (drives
    ``current_return``/stop-level bookkeeping, alpha_bot_execution.py:1187)
    AND on the single holding entry (drives run_monte_carlo's
    ``current_symphony_return``, math_engine.py:1162-1166 -- it sums only
    holdings carrying their OWN ``last_percent_change`` key, ignoring the
    symphony-level field entirely). A 100%-allocation single-holding
    symphony makes these numerically identical, matching a real Composer
    payload for a single-asset symphony.
    """
    return {
        "id": _SYMPHONY_ID,
        "symphony_id": _ACTUAL_SYMPHONY_ID,
        "name": _SYMPHONY_NAME,
        "last_percent_change": last_percent_change,
        "current_value": 10000.0,
        "holdings": [
            {"ticker": _TICKER, "allocation": 1.0, "last_percent_change": last_percent_change}
        ],
    }


def _make_vwap_payload(last_price: float) -> dict:
    return {_TICKER: {"vwap": last_price, "last_price": last_price}}


def _seed_state(
    *,
    triggered: bool,
    armed: bool = False,
    below_stop_count: int = 0,
    high_water_mark: float = 0.0,
    **extras,
) -> dict:
    """Minimal pre-seeded bot_state for the fixture symphony.

    When ``triggered=True`` the state also carries the post-trigger fields the
    True-Shadow-Return override reads (alpha_bot_execution.py:1191-1204):
    ``current_holdings`` (no last_percent_change), ``triggered_at_return`` and
    ``trigger_prices``.
    """
    sym = {
        "high_water_mark": -999.0 if triggered else high_water_mark,
        "shadow_hwm": high_water_mark,
        "prev_return": high_water_mark,
        "armed": armed,
        "tp_armed": False,
        "para_armed": False,
        "triggered": triggered,
        "mc_history": [],
        "below_stop_count": below_stop_count,
        "above_tp_count": 0,
        "vwap_ticks": 0,
        "vwap_bleed_ticks": 0,
        "breakeven_locked": False,
        "hwm_hold_ticks": 0,
        "symphony_vol": 0.10,
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
    """Bundle every patch main()'s pipeline needs -- network, clock, DB,
    Discord -- leaving the math engine REAL. Mirrors
    tests/execution/test_m4_triggered_mc_history_guard.py's harness."""
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
        mock_fetch_hist.return_value = _make_history(_FIXED_ET.strftime("%Y-%m-%d"))
        mock_fetch_vwap.return_value = {}

        yield {
            "db": mock_db,
            "reporting": mock_reporting,
            "fetch_symphony_stats": mock_fetch_sym,
            "fetch_alpaca_history": mock_fetch_hist,
            "fetch_intraday_vwaps": mock_fetch_vwap,
        }


def _final_symphony_state(env, symphony_id: str = _SYMPHONY_ID) -> dict:
    """The symphony's state in the last save_state write."""
    save_calls = env["db"].save_state.call_args_list
    assert save_calls, "save_state was never called -- main() aborted early."
    return save_calls[-1].args[0][symphony_id]


def _final_chart_row(env, symphony_id: str = _SYMPHONY_ID) -> dict:
    """The last chart_history row appended for this symphony in the final
    save_chart_history call."""
    save_calls = env["db"].save_chart_history.call_args_list
    assert save_calls, "save_chart_history was never called -- main() aborted early."
    chart_history = save_calls[-1].args[0]
    rows = chart_history.get("symphonies", {}).get(symphony_id, [])
    assert rows, f"No chart_history rows were appended for {symphony_id!r}."
    return rows[-1]


_ARMPROB_PATTERN = re.compile(r"ArmProb:\s*\d+\.\d%")


def _armprob_lines(captured_out: str) -> list[str]:
    return [
        line
        for line in captured_out.splitlines()
        if _SYMPHONY_NAME[:35] in line and "ArmProb" in line
    ]


# ---------------------------------------------------------------------------
# Provenance guard -- the frozen post-trigger holdings shape genuinely drives
# the REAL run_monte_carlo to a non-None probability against this fixture.
# ---------------------------------------------------------------------------
def test_provenance_frozen_holdings_yield_real_mc_probability() -> None:
    """Guard: if this ever goes RED, the post-trigger sentinel test below is
    not exercising suppression of a genuinely fabricated value -- it would be
    trivially "passing" because MC was already unavailable for an unrelated
    reason (e.g. insufficient history), not because of the AC-1 guard."""
    latest = _FIXED_ET.strftime("%Y-%m-%d")
    history = _make_history(latest)
    spy_today = history[latest]["SPY"]["daily_ret"] * 100.0
    seed = math_engine.derive_cycle_mc_seed(_FIXED_ET.strftime("%Y%m%d_%H%M"))
    # Frozen current_holdings shape (alpha_bot_execution.py:1557-1561): ticker +
    # allocation only, NO last_percent_change key.
    frozen_holdings = [{"ticker": _TICKER, "allocation": 1.0}]
    result = math_engine.run_monte_carlo(
        frozen_holdings,
        history,
        spy_today,
        simulation_paths=alpha_bot_execution.SIMULATION_PATHS,
        neighbor_k=alpha_bot_execution.NEIGHBOR_K,
        seed=seed,
    )
    assert result is not math_engine.MC_INSUFFICIENT_HISTORY_SENTINEL, (
        f"The frozen-holdings fixture must drive run_monte_carlo to a real "
        f"probability (not the insufficient sentinel); got {result!r}."
    )
    assert isinstance(result, float), (
        f"run_monte_carlo should return a float probability on this fixture; "
        f"got {type(result).__name__}"
    )


# ---------------------------------------------------------------------------
# AC-1 -- triggering cycle: real value persisted at BOTH sites (untouched)
# ---------------------------------------------------------------------------
def test_triggering_cycle_persists_real_mc_at_both_sites(patched_environment) -> None:
    """ADDENDUM 2 timing: on the cycle where the trailing stop ACTUALLY FIRES,
    ``bot_state[sid]["triggered"]`` is still False when the two persist sites
    (:1549 bot_state, :1598 chart_history) run -- the flag only flips True
    later, at :1808, once the execution queue is processed. THIS cycle's
    prob_underperforming is computed from real (untouched) holdings, so the
    persisted value at BOTH sites must remain a real float, exactly as
    before the guard existed.

    Fixture: a large, real drawdown (-30%) drives prob_underperforming to a
    real value confirmed empirically >= MC_BREAKDOWN_THRESHOLD (60) under
    the production seed derivation, so compute_exit_confirmation's
    mc_breakdown_ok gate ALLOWS the exit. armed=True with below_stop_count
    already one tick short of EXIT_CONFIRM_TICKS so THIS cycle's below-stop
    tick confirms the exit within the SAME main() call (dry-run,
    success=True).
    """
    env = patched_environment
    env["fetch_symphony_stats"].return_value = [_make_symphony_payload(last_percent_change=-0.30)]
    env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(350.0)
    env["db"].load_state.return_value = _seed_state(
        triggered=False,
        armed=True,
        below_stop_count=math_engine.EXIT_CONFIRM_TICKS - 1,
        high_water_mark=5.0,
    )

    alpha_bot_execution.main()

    sym_state = _final_symphony_state(env)
    assert sym_state["triggered"] is True, (
        f"Fixture did not actually trigger this cycle -- test setup invalid; "
        f"got triggered={sym_state.get('triggered')!r}. The below-stop-count "
        f"ladder or the -30% drawdown may need adjustment."
    )

    mc_prob = sym_state.get("mc_prob")
    assert mc_prob is not None and isinstance(mc_prob, float), (
        f"bot_state mc_prob on the TRIGGERING cycle must be a real float (the "
        f"engine's honest pre-trigger MC reading) -- got {mc_prob!r} "
        f"({type(mc_prob).__name__}). Per ADDENDUM 2, the triggered flag is "
        f"still False when :1549 persists this cycle; the guard must not "
        f"suppress it."
    )

    chart_row = _final_chart_row(env)
    chart_mc = chart_row.get("mc_prob")
    assert chart_mc is not None and isinstance(chart_mc, float), (
        f"chart_history mc_prob on the TRIGGERING cycle must also be a real "
        f"float -- got {chart_mc!r} ({type(chart_mc).__name__}). The same "
        f"timing argument applies to the :1598 persist site."
    )


# ---------------------------------------------------------------------------
# AC-1 -- post-trigger cycle: honest sentinel at BOTH sites (the fix)
# ---------------------------------------------------------------------------
def test_post_trigger_cycle_persists_sentinel_at_both_sites(patched_environment) -> None:
    """From the cycle AFTER the trigger onward, ``triggered`` is already True
    when the loop re-enters -- the True-Shadow-Return override swaps in the
    frozen ``current_holdings`` (no ``last_percent_change``), collapsing
    run_monte_carlo's baseline to a fictional 0%. Both persist sites must
    suppress that fabricated value to the honest sentinel (None)."""
    env = patched_environment
    env["fetch_symphony_stats"].return_value = [_make_symphony_payload(last_percent_change=-0.04)]
    env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(500.0)
    env["db"].load_state.return_value = _seed_state(triggered=True, high_water_mark=5.0)

    alpha_bot_execution.main()

    sym_state = _final_symphony_state(env)
    assert sym_state["triggered"] is True, "Fixture must stay triggered across this cycle."

    mc_prob = sym_state.get("mc_prob")
    assert mc_prob is None, (
        f"AC-1 FAIL: bot_state mc_prob on a POST-TRIGGER cycle must be the "
        f"honest sentinel (None) -- got {mc_prob!r} ({type(mc_prob).__name__}). "
        f"The :1549 persist site is not guarded on bot_state[sid]['triggered']."
    )

    chart_row = _final_chart_row(env)
    chart_mc = chart_row.get("mc_prob")
    assert chart_mc is None, (
        f"AC-1 FAIL: chart_history mc_prob on a POST-TRIGGER cycle must also "
        f"be the honest sentinel (None) -- got {chart_mc!r} "
        f"({type(chart_mc).__name__}). The :1598 persist site is not guarded."
    )


# ---------------------------------------------------------------------------
# Decision-path purity (ADDENDUM 2) -- the guard never leaks upstream
# ---------------------------------------------------------------------------
def test_untriggered_low_mc_does_not_arm_and_is_not_leaked_sentinel(patched_environment) -> None:
    """'The AC-1 guard lives strictly at the persist site ... no decision
    function ever receives a guard-produced None for an untriggered
    symphony' (ADDENDUM 2).

    Fixture: a fresh (never-armed, never-triggered) symphony whose real
    prob_underperforming lands at a LOW value (empirically verified against
    this fixture/seed -- not hardcoded here, only observed post-run).
    mc_available is True (a real value was computed), so the inline arm
    gate's fail-open branch (``elif not mc_available: should_arm = True``)
    must NOT fire. If the guard ever leaked a None into the LOCAL
    decision-path variable for an untriggered symphony, mc_available would
    flip False and this fail-open branch would incorrectly arm the symphony
    -- this test catches that regression without pinning the exact
    probability value.
    """
    env = patched_environment
    env["fetch_symphony_stats"].return_value = [_make_symphony_payload(last_percent_change=0.05)]
    env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(500.0)
    env["db"].load_state.return_value = _seed_state(
        triggered=False, armed=False, high_water_mark=0.0
    )

    alpha_bot_execution.main()

    sym_state = _final_symphony_state(env)
    mc_prob = sym_state.get("mc_prob")
    assert mc_prob is not None and isinstance(mc_prob, float), (
        f"Expected a real, non-sentinel mc_prob for this untriggered fixture; "
        f"got {mc_prob!r}. If MC is unavailable here the test's premise "
        f"(mc_available=True) is void -- the history fixture would need "
        f"adjustment, independent of the AC-1 guard."
    )
    assert sym_state["armed"] is False, (
        f"Symphony armed={sym_state['armed']!r} on a LOW real mc_prob "
        f"({mc_prob!r}) that does not qualify for the arm band "
        f"[{alpha_bot_execution.TAKE_PROFIT_MC_PCT}, "
        f"{alpha_bot_execution.TRIGGER_THRESHOLD_PCT}). If the guard leaked a "
        f"sentinel (None) into the arm decision instead of this real value, "
        f"mc_available would be False and the fail-open branch would have "
        f"incorrectly armed the symphony."
    )


# ---------------------------------------------------------------------------
# ArmProb console print (ruled in at plan approval) -- third leak surface
# ---------------------------------------------------------------------------
def test_triggering_cycle_armprob_print_keeps_real_number(patched_environment, capsys) -> None:
    """Regression pairing: the TRIGGERING cycle's ArmProb print must KEEP
    showing the real number (the flag is still False when this print runs,
    per ADDENDUM 2 timing) -- proving the fix does not overcorrect into
    suppressing the legitimate case too."""
    env = patched_environment
    env["fetch_symphony_stats"].return_value = [_make_symphony_payload(last_percent_change=-0.30)]
    env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(350.0)
    env["db"].load_state.return_value = _seed_state(
        triggered=False,
        armed=True,
        below_stop_count=math_engine.EXIT_CONFIRM_TICKS - 1,
        high_water_mark=5.0,
    )

    alpha_bot_execution.main()

    captured = capsys.readouterr()
    symphony_lines = _armprob_lines(captured.out)
    assert symphony_lines, (
        "No 'ArmProb:' console line found for this symphony -- main()'s "
        "per-cycle summary print may have changed shape; update this test's "
        "line-matching."
    )
    assert any(_ARMPROB_PATTERN.search(line) for line in symphony_lines), (
        f"Expected the TRIGGERING cycle's ArmProb print to show a real "
        f"numeric percentage (untouched, per ADDENDUM 2 timing); got: "
        f"{symphony_lines!r}"
    )


def test_post_trigger_armprob_print_omits_fabricated_number(patched_environment, capsys) -> None:
    """The ArmProb print at alpha_bot_execution.py:1541-1544 formats the LOCAL
    prob_underperforming variable directly, BEFORE the :1549 persist site
    runs -- a guard applied only to the bot_state assignment's right-hand
    side would not touch this print. It must independently show an honest
    marker (not the fabricated percentage) for a post-trigger cycle."""
    env = patched_environment
    env["fetch_symphony_stats"].return_value = [_make_symphony_payload(last_percent_change=-0.04)]
    env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(500.0)
    env["db"].load_state.return_value = _seed_state(triggered=True, high_water_mark=5.0)

    alpha_bot_execution.main()

    captured = capsys.readouterr()
    symphony_lines = _armprob_lines(captured.out)
    assert symphony_lines, (
        "No 'ArmProb:' console line found for this symphony -- cannot verify "
        "the fabricated-number omission."
    )
    for line in symphony_lines:
        assert not _ARMPROB_PATTERN.search(line), (
            f"AC-1 FAIL: post-trigger ArmProb print still shows a fabricated "
            f"percentage: {line!r}. The :1541-1544 print must use an honest "
            f"marker for a triggered symphony, not the raw local "
            f"prob_underperforming."
        )
