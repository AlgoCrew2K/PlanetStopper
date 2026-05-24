"""
Contract tests for POST /api/settings/flush-resync.

AC-FR.1 — Phase 1: synthetic post-mortem files in analytics._POST_MORTEMS_DIR are deleted;
           real dates (in _REAL_POST_MORTEM_DATES) are kept.
AC-FR.2 — Phase 2: per-symphony bot_state entries are reset; only "name" and "account"
           are preserved; non-symphony metadata keys are untouched.
AC-FR.3 — Response shape includes symphonies_reset (list of display names) and
           symphonies_reset_count (int).
AC-FR.4 — Analytics _HISTORY_CACHE is invalidated after the flush.
AC-FR.5 — Post-mortem path round-trip: generate_eod_snapshot writes into
           analytics._POST_MORTEMS_DIR; analytics.load_post_mortem_history with
           base_dir=analytics._POST_MORTEMS_DIR reads the file back.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

import analytics
import app as app_module
import reporting


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _make_pm_file(directory: str, date_str: str, triggers: list | None = None) -> str:
    """Write a minimal post-mortem JSON file, return the file path."""
    payload = {
        "date": date_str,
        "summary": {"total_monitored": 1, "total_triggered": 0, "positive_guard_alpha_count": 0},
        "tomorrow_target_holdings": {},
        "triggers": triggers or [],
    }
    os.makedirs(directory, exist_ok=True)
    fpath = os.path.join(directory, f"post_mortem_{date_str}.json")
    with open(fpath, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return fpath


# ---------------------------------------------------------------------------
# AC-FR.5 — Post-mortem path round-trip
# ---------------------------------------------------------------------------


def test_post_mortem_path_round_trip(tmp_path):
    """generate_eod_snapshot writes to the patched _POST_MORTEMS_DIR;
    analytics.load_post_mortem_history reads it back via the same directory.

    This pins the writer → directory → reader contract end-to-end without
    touching the real project post_mortems/ directory.
    """
    pm_dir = str(tmp_path / "pm")

    bot_state = {
        "SYM_RT": {
            "triggered": True,
            "triggered_at_return": -2.5,
            "triggered_at_stop": -3.5,
            "triggered_reason": "Trailing Stop",
            "current_return": -4.0,
            "current_value": 20_000.0,
            "triggered_basket_snapshot": [],
            "name": "Round-Trip Symphony",
            "account": "acct-rt",
            "shadow_hwm": 1.0,
            "triggered_at_hwm": 1.0,
            "triggered_at_time": "15:30:00",
            "symphony_vol": 0.01,
        }
    }
    date_str = "2026-03-01"

    with (
        patch("reporting.database.normalize_name", side_effect=lambda n: n),
        patch(
            "reporting.database.get_symphony_strategy",
            return_value={"params": {}, "locked_vars": {}},
        ),
        patch.object(reporting, "_POST_MORTEMS_DIR", pm_dir),
    ):
        reporting.generate_eod_snapshot(
            bot_state,
            date_str,
            is_post_rebalance=False,
            discord_webhook_url=None,
            live_prices=None,
        )

    # File must exist in patched pm_dir, not a CWD-relative path.
    expected_file = os.path.join(pm_dir, f"post_mortem_{date_str}.json")
    assert os.path.exists(expected_file), (
        f"Snapshot not found at {expected_file}. "
        "reporting.generate_eod_snapshot must write into _POST_MORTEMS_DIR, not CWD."
    )

    # Reader must find it when given the same base_dir.
    history = analytics.load_post_mortem_history(days=7, base_dir=pm_dir)

    assert date_str in history, (
        f"analytics.load_post_mortem_history did not load date {date_str}. "
        f"Keys returned: {list(history.keys())}"
    )
    # analytics keyed by symphony_name (display name), not the bot_state dict key.
    assert "Round-Trip Symphony" in history[date_str], (
        f"Expected 'Round-Trip Symphony' in history[{date_str}]. "
        f"Keys present: {list(history[date_str].keys())}"
    )


# ---------------------------------------------------------------------------
# AC-FR.1 — Synthetic files are deleted; real dates are kept
# ---------------------------------------------------------------------------


def test_flush_resync_deletes_synthetic_and_keeps_real(client, tmp_path, monkeypatch):
    """POST /api/settings/flush-resync removes synthetic-date files and keeps real ones."""
    pm_dir = str(tmp_path / "pm")

    # One real date, one synthetic date
    real_date = next(iter(app_module._REAL_POST_MORTEM_DATES))
    synthetic_date = "2020-01-01"

    _make_pm_file(pm_dir, real_date)
    _make_pm_file(pm_dir, synthetic_date)

    monkeypatch.setattr(analytics, "_POST_MORTEMS_DIR", pm_dir)

    with (
        patch.object(app_module, "database") as db_mock,
        patch.object(app_module, "_refresh_account_totals"),
        patch.object(app_module.threading, "Thread", _SyncThread),
    ):
        db_mock.load_state.return_value = {}
        db_mock.save_state.return_value = None
        db_mock.DB_FILE = str(tmp_path / "fake.db")

        resp = client.post("/api/settings/flush-resync")

    assert resp.status_code == 200
    body = resp.get_json()

    assert f"post_mortem_{synthetic_date}.json" in body["deleted"], (
        "Synthetic file must appear in deleted list."
    )
    assert f"post_mortem_{real_date}.json" in body["kept"], (
        "Real file must appear in kept list."
    )
    assert not os.path.exists(os.path.join(pm_dir, f"post_mortem_{synthetic_date}.json")), (
        "Synthetic file must be removed from disk."
    )
    assert os.path.exists(os.path.join(pm_dir, f"post_mortem_{real_date}.json")), (
        "Real file must remain on disk."
    )


# ---------------------------------------------------------------------------
# AC-FR.2/FR.3 — Per-symphony bot_state entries are reset; response reports names
# ---------------------------------------------------------------------------


def test_flush_resync_resets_symphony_state_and_reports_names(client, tmp_path, monkeypatch):
    """Phase 2: per-symphony entries are stripped to {name, account};
    response includes symphonies_reset list and symphonies_reset_count.

    NEW CONTRACT (side-effect ban): database.save_state must NOT be called
    directly on the Flask request thread.  The state-reset is dispatched to a
    background thread; the route response reports the names it WILL reset
    (derived from reading current state) without blocking on the write.
    """
    pm_dir = str(tmp_path / "pm")
    monkeypatch.setattr(analytics, "_POST_MORTEMS_DIR", pm_dir)

    pre_flush_state = {
        "sym_aaa": {
            "name": "Alpha",
            "account": "acct-1",
            "triggered": True,
            "current_return": -3.5,
            "high_water_mark": 2.1,
            "some_tracking_field": 99,
        },
        "sym_bbb": {
            "name": "Beta",
            "account": "acct-2",
            "triggered": False,
            "current_return": 0.5,
        },
        # Non-symphony metadata key — must NOT be reset
        "last_execution_mode": "live",
    }

    with (
        patch.object(app_module, "database") as db_mock,
        patch.object(app_module, "_refresh_account_totals"),
        patch.object(app_module.threading, "Thread", _SyncThread),
    ):
        db_mock.load_state.return_value = pre_flush_state
        db_mock.DB_FILE = str(tmp_path / "fake.db")

        resp = client.post("/api/settings/flush-resync")

    assert resp.status_code == 200
    body = resp.get_json()

    # Response shape — route must still report which symphonies will be reset
    assert "symphonies_reset" in body, "Response must include symphonies_reset key."
    assert "symphonies_reset_count" in body, "Response must include symphonies_reset_count key."
    assert body["symphonies_reset_count"] == 2
    assert set(body["symphonies_reset"]) == {"Alpha", "Beta"}

    # Side-effect ban: save_state must NOT be called directly from the route thread.
    # The state-reset is dispatched to a background worker; the route does not block on it.
    db_mock.save_state.assert_not_called(), (
        "database.save_state must NOT be called directly from the flush_resync route. "
        "The state-reset must be dispatched to a background thread (side-effect ban)."
    )


def test_flush_resync_symphony_without_account_resets_cleanly(client, tmp_path, monkeypatch):
    """A symphony entry with no 'account' key is reported in symphonies_reset — no KeyError.

    NEW CONTRACT (side-effect ban): save_state is not called on the request thread.
    The test verifies the route reads the no-account entry without crashing and
    reports it in the response without calling save_state directly.
    """
    pm_dir = str(tmp_path / "pm")
    monkeypatch.setattr(analytics, "_POST_MORTEMS_DIR", pm_dir)

    pre_flush_state = {
        "sym_no_acct": {
            "name": "NoAccount Symphony",
            "triggered": False,
            "current_return": 1.0,
        },
    }

    with (
        patch.object(app_module, "database") as db_mock,
        patch.object(app_module, "_refresh_account_totals"),
        patch.object(app_module.threading, "Thread", _SyncThread),
    ):
        db_mock.load_state.return_value = pre_flush_state
        db_mock.DB_FILE = str(tmp_path / "fake.db")

        resp = client.post("/api/settings/flush-resync")

    assert resp.status_code == 200
    body = resp.get_json()
    # No-account symphony must appear in the reset list without causing a KeyError
    assert "NoAccount Symphony" in body.get("symphonies_reset", []), (
        "Symphony without 'account' must appear in symphonies_reset — no KeyError."
    )
    # Side-effect ban: save_state must not be called on the route thread
    db_mock.save_state.assert_not_called(), (
        "database.save_state must NOT be called directly from the flush_resync route."
    )


# ---------------------------------------------------------------------------
# AC-FR.4 — Analytics history cache is invalidated
# ---------------------------------------------------------------------------


def test_flush_resync_invalidates_analytics_cache(client, tmp_path, monkeypatch):
    """After flush, analytics._HISTORY_CACHE is reset to the sentinel blank state."""
    pm_dir = str(tmp_path / "pm")
    monkeypatch.setattr(analytics, "_POST_MORTEMS_DIR", pm_dir)

    # Pre-populate the cache with stale data
    analytics._HISTORY_CACHE = {"key": "stale-key", "data": {"2026-01-01": {}}}

    with (
        patch.object(app_module, "database") as db_mock,
        patch.object(app_module, "_refresh_account_totals"),
        patch.object(app_module.threading, "Thread", _SyncThread),
    ):
        db_mock.load_state.return_value = {}
        db_mock.save_state.return_value = None
        db_mock.DB_FILE = str(tmp_path / "fake.db")

        resp = client.post("/api/settings/flush-resync")

    assert resp.status_code == 200
    assert analytics._HISTORY_CACHE == {"key": None, "data": None}, (
        "Analytics _HISTORY_CACHE must be reset to {key: None, data: None} after flush."
    )


# ---------------------------------------------------------------------------
# Shared SyncThread helper (avoids real threading in tests)
# ---------------------------------------------------------------------------


class _SyncThread:
    """Synchronous drop-in for threading.Thread — runs target inline."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}

    def start(self):
        if self.target:
            self.target(*self.args, **self.kwargs)

    def join(self, timeout=None):
        pass
