"""BL-9 (audit #118 M4, docs/audit/TWO-WEEK-REVIEW-2026-08-04.md §4/§6):
basket-reconstruction footgun hardening — retroactive regression test.

IMPLEMENTATION ALREADY SHIPPED (commit eca71013, DE-AUDIT-BL9-001) — this
test was written AFTER the fact rather than before, per the team's actual
sequencing on this item: BL-9's mechanism (AC-12) required bl5risk's
ratification before any RED test could meaningfully commit to a marker name,
and bl5risk implemented directly once the team lead approved the mechanism
(the test-writer's proposed `current_return_is_reconstructed` key, verified
identical in the shipped commit) rather than waiting on a RED test to land
first. This file is the sufficiency-review-pass regression guard that locks
the shipped behavior in and would have caught a regression either way — it
is genuinely non-vacuous (RED against a hypothetical revert of the shipped
diff, GREEN against current code, verified below).

THE FIX (AC-12): ``bot_state[symphony_id]["current_return_is_reconstructed"]``
is stamped ``True`` at the TRUE SHADOW RETURN OVERRIDE's main write site
(alpha_bot_execution.py:~1658, reusing the already-computed
``is_triggered_now`` — the SAME ``bot_state[symphony_id]["triggered"]`` read
the DE-MATH-F7-001 MC-suppression guard uses) and ``False`` at the two other
clean per-tick write sites (~:889, the data-phase loop; ~:1048, the EOD
post-mortem pass) — refined from the plan's single-site proposal to all
three after the implementer traced that the EOD pass also writes a clean
current_return even for a still-triggered symphony, which would have left a
single-site marker stale/dishonest after that write.

AC-13 (zero behavioral/decision-path diff): covered by the existing
tests/execution suite staying green — independently re-verified in this
cycle (423 passed, tests/execution -n0), not merely re-quoting the
implementer's own commit-message claim.

AC-14 (read-path regression guard): already covered by pre-existing
coverage, independently confirmed green before this cycle started —
tests/reporting/test_postmortem_if_held_shadow_history_source.py::
TestSavedDollarsSourcedFromShadowHistoryTable::
test_saved_dollars_uses_latest_shadow_row_not_clobbered_bot_state
(12/12 passed). No new test needed for AC-14; not duplicated here.

Fixture pattern mirrored from tests/execution/test_m4_triggered_mc_history_
guard.py (no import coupling, per this project's established test-file
convention — small fixture helpers are intentionally duplicated across test
files rather than cross-imported).
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

import alpha_bot_execution
import math_engine

_ET = ZoneInfo("America/New_York")
_FIXED_ET = datetime(2025, 5, 14, 11, 30, 0, tzinfo=_ET)  # Wed 11:30 ET

_SYMPHONY_ID = "sym-bl9-marker-001"
_ACTUAL_SYMPHONY_ID = "actual-sym-bl9-marker-001"
_ACCOUNT_ID = "acct-bl9-marker-001"
_SYMPHONY_NAME = "BL-9 Marker Symphony"
_TICKER = "SPY"

_SUFFICIENT_HISTORY_DAYS = (
    math_engine.MC_MIN_HISTORY_DAYS + (math_engine.MC_VOL_WINDOW_DAYS - 1) + 1
)


def _make_sufficient_history(latest_date_str: str, n_days: int) -> dict:
    from datetime import timedelta

    base = datetime.strptime(latest_date_str, "%Y-%m-%d")
    history: dict = {}
    for i in range(n_days):
        day = base - timedelta(days=(n_days - 1 - i))
        day_str = day.strftime("%Y-%m-%d")
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


def _seed_state(*, triggered: bool, **extras) -> dict:
    sym = {
        "high_water_mark": -999.0 if triggered else 0.0,
        "shadow_hwm": 0.0,
        "prev_return": 0.0,
        "armed": False,
        "tp_armed": False,
        "para_armed": False,
        "triggered": triggered,
        "mc_history": [42.0, 58.0],
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
    save_calls = env["db"].save_state.call_args_list
    assert save_calls, "save_state was never called — main() aborted early."
    return save_calls[-1].args[0][_SYMPHONY_ID]


class TestReconstructedMarkerStampedOnTriggeredCycle:
    def test_triggered_cycle_marks_current_return_as_reconstructed(self, patched_environment):
        """AC-12: a TRIGGERED symphony's main write site must stamp
        current_return_is_reconstructed=True — the TRUE SHADOW RETURN
        OVERRIDE reconstructs current_return from frozen trigger_prices +
        live VWAPs for a triggered symphony, and this hardening makes that
        fact structurally discoverable to a future bot_state reader."""
        env = patched_environment
        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=-0.04)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(495.0)
        env["db"].load_state.return_value = _seed_state(triggered=True)

        alpha_bot_execution.main()

        sym_state = _final_symphony_state(env)
        assert sym_state.get("current_return_is_reconstructed") is True, (
            f"a triggered symphony's current_return write must be marked "
            f"reconstructed=True; got "
            f"{sym_state.get('current_return_is_reconstructed')!r} "
            f"(full state: {sym_state})"
        )

    def test_untriggered_cycle_marks_current_return_as_not_reconstructed(self, patched_environment):
        """Regression: an UNTRIGGERED symphony's current_return is a live
        per-tick figure, never a basket reconstruction — the marker must be
        False, never fabricated True (which would falsely flag a clean value
        as reconstructed)."""
        env = patched_environment
        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=-0.04)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(495.0)
        env["db"].load_state.return_value = _seed_state(triggered=False, high_water_mark=0.0)

        alpha_bot_execution.main()

        sym_state = _final_symphony_state(env)
        assert sym_state.get("current_return_is_reconstructed") is False, (
            f"an untriggered symphony's current_return write must be marked "
            f"reconstructed=False, never True/absent; got "
            f"{sym_state.get('current_return_is_reconstructed')!r}"
        )


class TestEODPassClearsStaleReconstructedFlag:
    def test_eod_pass_overwrites_stale_true_on_still_triggered_symphony(
        self, patched_environment, tmp_path, monkeypatch
    ):
        """THE REFINEMENT's reason (team-lead ratification, message
        6bba550e): the single-site design (stamp only at the action-phase
        reconstruction write, ~:1658) would leave a STALE True flag after
        the SEPARATE EOD post-mortem pass (~:1048) later overwrites
        current_return with the clean per-tick figure for the SAME
        still-triggered symphony — that write was always clean (pre-existing
        behavior, unchanged), but pre-refinement nothing corrected the
        marker to match. This test seeds a symphony as if an EARLIER
        intraday cycle already reconstructed it (current_return_is_
        reconstructed=True in bot_state) and STILL triggered, then runs
        main() at a frozen clock inside the EOD post-mortem window
        (market_close <= current_time <= post_mortem_cutoff, 16:00-16:05 ET)
        — a code path that returns (alpha_bot_execution.py:1213) BEFORE ever
        reaching the action-phase reconstruction block again this cycle, so
        only the EOD-pass write site can be responsible for any change to
        the flag.
        """
        import analytics

        # The EOD pass writes real post-mortem files to analytics.
        # _POST_MORTEMS_DIR (analytics.py:70) — redirect to tmp_path so this
        # test never touches the real project's post_mortems/ directory.
        monkeypatch.setattr(analytics, "_POST_MORTEMS_DIR", str(tmp_path))

        env = patched_environment
        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=0.02)
        ]
        seeded_state = _seed_state(triggered=True)
        # Simulate the leftover state from an earlier intraday action-phase
        # cycle that DID reconstruct current_return (already ran once today,
        # before this EOD cycle).
        seeded_state[_SYMPHONY_ID]["current_return_is_reconstructed"] = True
        env["db"].load_state.return_value = seeded_state

        # _FIXED_ET is a Wednesday 11:30 ET; shift to 16:02 ET the SAME day —
        # inside the EOD post-mortem window (session close 16:00 + 5min
        # cutoff on a regular trading day), still weekday()==2 (Wed), so the
        # Friday/weekend-only autotuner branch (:1180) is skipped and no
        # extra autotuner mocking is needed.
        eod_et = _FIXED_ET.replace(hour=16, minute=2, second=0, microsecond=0)
        with patch.object(alpha_bot_execution, "get_current_et", return_value=eod_et):
            alpha_bot_execution.main()

        sym_state = _final_symphony_state(env)
        assert sym_state.get("current_return_is_reconstructed") is False, (
            f"the EOD post-mortem pass must clear a stale True flag left over "
            f"from an earlier intraday reconstruction — the EOD write always "
            f"produces a clean per-tick current_return, even for a still-"
            f"triggered symphony, so the marker must read False afterward, "
            f"not the stale True; got "
            f"{sym_state.get('current_return_is_reconstructed')!r}"
        )
