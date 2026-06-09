"""
M5 site-1 (math-audit MEDIUM, CONFIRMED) — RED tests for the UTC-4 hardcode in
the ``alpha_bot_execution.main()`` shadow-observation caller.

Audit context
-------------
The data-phase shadow-telemetry write computed ``ts_et`` from a hardcoded
EDT offset (the pre-fix form at alpha_bot_execution.py:924):

    ts_et_str = (now_utc - timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%S")

That ``-4h`` is correct only during EDT (roughly Mar–Nov). During EST
(roughly Nov–Mar) Eastern is UTC-5, so the stored ts_et is one hour AHEAD of
the real wall-clock ET. The fix routes the timestamp through the already-in-
scope ZoneInfo-correct ``current_et`` (from ``get_current_et()``):

    ts_et_str = current_et.strftime("%Y-%m-%dT%H:%M:%S")

This is the LIVE-CALLER half of M5 (the DB-default half is pinned in
``tests/database/test_m5_record_exit_trigger_ts_et_dst.py``). The team-lead
briefing requires BOTH sites covered.

WHAT THESE TESTS ASSERT
-----------------------
1. DST-1 (the M5 RED): on a cycle whose ET clock is in EST, the ts_et passed to
   ``database.record_shadow_observation`` equals the EST wall-clock string —
   NOT the one-hour-ahead EDT approximation.
2. DST-2 (regression): on a summer (EDT) cycle the ts_et is unchanged — the fix
   must not shift correct summer timestamps.

Provenance / mock strategy
--------------------------
The clock is the test input: ``get_current_et`` is frozen to a known ET instant
and ``datetime.now(UTC)`` is the matching UTC instant. The expected ts_et is
DERIVED from the frozen ET instant (``current_et.strftime(...)``), not a
hardcoded producer value — so the assertion checks the wiring (which clock the
caller uses), not a magic string. Only network / clock / DB / Discord are
mocked; the math engine is left REAL. The shadow write is captured by
inspecting the ``record_shadow_observation`` call kwargs on the mocked DB.

Timing: the shadow write lives in the DATA phase (alpha_bot_execution.py:904-
939), reached when ``REAL_MARKET_OPEN <= current_time < EXECUTION_START_TIME``.
EXECUTION_START_TIME is patched to 10:30 and the clock frozen to ~09:45 ET so
the data phase runs and ``main()`` returns before the action phase.

RED-VERIFICATION
----------------
DST-1 is RED against the pre-fix hardcode (it would store the +1h EDT string in
EST) and GREEN against the ZoneInfo-correct ``current_et`` form at HEAD. DST-2
is GREEN in both. Because the fix is already committed at HEAD, both are GREEN
here and serve as the regression pins for site-1; RED-capability is demonstrated
in the cycle handoff by reverting the caller to the ``-timedelta(hours=4)`` form.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from unittest.mock import patch

import pytest

import alpha_bot_execution


try:
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
    _HAVE_TZDATA = True
except Exception:  # pragma: no cover -- tzdata missing
    _ET = None
    _HAVE_TZDATA = False


pytestmark = pytest.mark.skipif(
    not _HAVE_TZDATA,
    reason="ZoneInfo('America/New_York') unavailable — DST offset cannot be resolved",
)


# Shape-only identifiers.
_SYMPHONY_ID = "sym-m5-shadow-ts-001"
_ACTUAL_SYMPHONY_ID = "actual-sym-m5-shadow-ts-001"
_ACCOUNT_ID = "acct-m5-shadow-ts-001"
_SYMPHONY_NAME = "M5 Shadow ts_et Symphony"
_TICKER = "SPY"

# Data-only window: EXECUTION_START_TIME pushed to 10:30 so a ~09:45 clock lands
# in the data phase (>= REAL_MARKET_OPEN 09:30, < action gate 10:30).
_EXECUTION_START = "10:30"


def _make_symphony_payload(last_percent_change: float) -> dict:
    return {
        "id": _SYMPHONY_ID,
        "symphony_id": _ACTUAL_SYMPHONY_ID,
        "name": _SYMPHONY_NAME,
        "last_percent_change": last_percent_change,
        "current_value": 10000.0,
        "holdings": [{"ticker": _TICKER, "allocation": 1.0}],
    }


def _make_minimal_history(current_date_str: str) -> dict:
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


def _make_vwap_payload(last_price: float) -> dict:
    return {_TICKER: {"vwap": last_price, "last_price": last_price}}


def _seed_state(date_str: str) -> dict:
    return {
        "date": date_str,
        "last_execution_mode": False,
        _SYMPHONY_ID: {
            "high_water_mark": 0.0,
            "shadow_hwm": 0.0,
            "prev_return": 0.0,
            "armed": False,
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


def _run_cycle_and_capture_ts_et(frozen_et: datetime, utc_instant: datetime) -> str:
    """Drive main() once with a frozen ET clock; return the ts_et kwarg passed
    to record_shadow_observation.

    ``get_current_et`` is frozen to ``frozen_et`` (the ZoneInfo-correct ET the
    fixed caller must use). ``datetime.now(UTC)`` is patched to ``utc_instant``
    so the pre-fix ``now_utc - timedelta(hours=4)`` form would produce the
    one-hour-ahead EDT string — that divergence is what DST-1 detects.
    """
    date_str = frozen_et.strftime("%Y-%m-%d")

    # Patch the module-level datetime so `datetime.now(UTC)` inside main()
    # returns the pinned UTC instant, while everything else proxies through.
    real_datetime = datetime

    class _FrozenDatetime:
        @staticmethod
        def now(tz=None):
            if tz is not None:
                return utc_instant.astimezone(tz)
            return utc_instant.replace(tzinfo=None)

        def __getattr__(self, name):  # pragma: no cover - attribute proxy
            return getattr(real_datetime, name)

    with patch.object(alpha_bot_execution, "database") as mock_db, patch.object(
        alpha_bot_execution, "reporting"
    ), patch.object(
        alpha_bot_execution, "fetch_symphony_stats"
    ) as mock_fetch_sym, patch.object(
        alpha_bot_execution, "fetch_alpaca_history"
    ) as mock_fetch_hist, patch.object(
        alpha_bot_execution, "fetch_intraday_vwaps"
    ) as mock_fetch_vwap, patch.object(
        alpha_bot_execution, "get_current_et", return_value=frozen_et
    ), patch.object(
        alpha_bot_execution, "datetime", _FrozenDatetime()
    ), patch.object(
        alpha_bot_execution, "EXECUTION_START_TIME", _EXECUTION_START
    ), patch.object(
        alpha_bot_execution, "ACCOUNT_UUIDS", [_ACCOUNT_ID]
    ), patch.object(
        alpha_bot_execution, "COMPOSER_KEY_ID", "test-composer-key"
    ), patch.object(
        alpha_bot_execution, "ALPACA_KEY", "test-alpaca-key"
    ), patch.object(
        alpha_bot_execution, "LIVE_EXECUTION", False
    ), patch.object(
        alpha_bot_execution.time, "sleep"
    ), patch.object(
        alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py"]
    ):
        mock_db.acquire_lock.return_value = True
        mock_db.load_state.return_value = _seed_state(date_str)
        mock_db.load_chart_history.return_value = {"date": date_str, "symphonies": {}}
        mock_db.get_symphony_strategy.return_value = {"params": {}, "locked_vars": {}}
        mock_db.normalize_name.side_effect = lambda n: n.strip().lower()
        mock_db.wipe_transient_state.side_effect = lambda s: s
        mock_db.mint_position_epoch.return_value = "epoch-test-001"

        mock_fetch_sym.return_value = [_make_symphony_payload(0.01)]
        mock_fetch_hist.return_value = _make_minimal_history(date_str)
        mock_fetch_vwap.return_value = _make_vwap_payload(500.0)

        alpha_bot_execution.main()

        calls = mock_db.record_shadow_observation.call_args_list
        assert calls, (
            "record_shadow_observation was never called — the data-phase "
            "shadow write did not run. Check the timing gate (clock must be "
            "in [REAL_MARKET_OPEN, EXECUTION_START_TIME))."
        )
        return calls[-1].kwargs["ts_et"]


# ---------------------------------------------------------------------------
# DST-1 — EST cycle: ts_et must be the EST wall-clock, not +1h EDT
# ---------------------------------------------------------------------------
def test_shadow_ts_et_uses_est_wall_clock_during_est() -> None:
    """M5 site-1 / DST-1. On an EST cycle (UTC-5), the ts_et passed to
    record_shadow_observation must equal the ZoneInfo-correct ET wall clock —
    NOT the pre-fix ``now_utc - 4h`` EDT string (which would be one hour ahead).

    Frozen ET instant: 2026-01-15 09:45 EST (a January weekday in the data
    window). The matching UTC instant is 14:45 UTC (EST = UTC-5).

    Pre-fix the caller computes ``14:45 UTC - 4h = 10:45`` (one hour AHEAD of
    the real 09:45 ET). Post-fix it uses ``current_et`` directly → 09:45.
    """
    frozen_et = datetime(2026, 1, 15, 9, 45, 0, tzinfo=_ET)  # EST, data window
    utc_instant = datetime(2026, 1, 15, 14, 45, 0, tzinfo=timezone.utc)  # = 09:45 EST

    # Expected ts_et is DERIVED from the frozen ET (the clock the fix must use),
    # not a hardcoded literal.
    expected_ts_et = frozen_et.strftime("%Y-%m-%dT%H:%M:%S")
    # The buggy EDT string the pre-fix caller would store (UTC - 4h).
    buggy_ts_et = (utc_instant - timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%S")
    # Sanity on the fixture: the two must differ by exactly the EST/EDT hour.
    assert expected_ts_et != buggy_ts_et, (
        "fixture error: EST and EDT renderings coincide; pick an instant where "
        "they differ so the test can distinguish the fix from the bug"
    )

    stored = _run_cycle_and_capture_ts_et(frozen_et, utc_instant)

    assert stored != buggy_ts_et, (
        f"ts_et stored {stored!r} matches the BUGGY EDT (-4h) approximation "
        f"{buggy_ts_et!r}. The hardcoded UTC-4 offset is wrong during EST. The "
        f"caller must use the ZoneInfo-correct current_et "
        f"(alpha_bot_execution.py:925)."
    )
    assert stored == expected_ts_et, (
        f"Expected ts_et={expected_ts_et!r} (EST wall clock from current_et) for "
        f"a 09:45 EST cycle; got {stored!r}. The shadow write must derive ts_et "
        f"from current_et, not a hardcoded -4h offset."
    )


# ---------------------------------------------------------------------------
# DST-2 — EDT (summer) cycle: ts_et unchanged (regression)
# ---------------------------------------------------------------------------
def test_shadow_ts_et_unchanged_during_edt_summer() -> None:
    """M5 site-1 / DST-2 (regression). On a summer (EDT, UTC-4) cycle the ts_et
    must equal the ET wall clock — which, in summer, coincides with the old
    ``now_utc - 4h`` value. The ZoneInfo fix must not shift correct summer
    timestamps.

    Frozen ET instant: 2026-07-10 09:45 EDT. Matching UTC instant: 13:45 UTC
    (EDT = UTC-4). Here current_et and ``UTC - 4h`` agree at 09:45.
    """
    frozen_et = datetime(2026, 7, 10, 9, 45, 0, tzinfo=_ET)  # EDT, data window
    utc_instant = datetime(2026, 7, 10, 13, 45, 0, tzinfo=timezone.utc)  # = 09:45 EDT

    expected_ts_et = frozen_et.strftime("%Y-%m-%dT%H:%M:%S")
    # In summer the old -4h form coincides with current_et.
    edt_offset_ts_et = (utc_instant - timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%S")
    assert expected_ts_et == edt_offset_ts_et, (
        "fixture error: in EDT the ZoneInfo render and the -4h render must "
        "coincide; choose a summer instant"
    )

    stored = _run_cycle_and_capture_ts_et(frozen_et, utc_instant)

    assert stored == expected_ts_et, (
        f"Expected ts_et={expected_ts_et!r} (EDT wall clock) for a summer cycle; "
        f"got {stored!r}. The ZoneInfo fix must leave correct summer timestamps "
        f"unchanged."
    )
