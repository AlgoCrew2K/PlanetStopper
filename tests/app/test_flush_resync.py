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

Mocking strategy:
  - database is patched via patch.object(app_module, "database") for Phase-1/2/3 tests.
  - Phase 2 background write is dispatched to _DISMISS_EXECUTOR (a real
    ThreadPoolExecutor).  Tests that need to observe the write MUST call
    write_completed.wait() INSIDE the patch.object(app_module, "database") context so
    the mock is still active when the executor worker fires.  Same pattern as
    test_fleet_dismiss_background_dispatch.py.
  - threading.Thread is NOT patched — _DISMISS_EXECUTOR manages its own threads and
    patching threading.Thread breaks the executor's internal thread creation.
"""

from __future__ import annotations

import json
import os
import threading
from unittest.mock import patch

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

    write_completed = threading.Event()

    def _capture_save(_state):
        write_completed.set()

    with (
        patch.object(app_module, "database") as db_mock,
        patch.object(app_module, "_refresh_account_totals"),
    ):
        db_mock.load_state.return_value = {}
        db_mock.save_state.side_effect = _capture_save

        resp = client.post("/api/settings/flush-resync")
        # Wait inside patch context so mock is still active when executor worker fires.
        write_completed.wait(timeout=2)

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

    CONTRACT: database.save_state must NOT be called directly on the Flask
    request thread (side-effect ban).  The state-reset is dispatched to a
    background worker via _DISMISS_EXECUTOR; the route response reports the
    names it WILL reset (derived from reading current state).

    We verify both sides:
      (a) handler returns 200 immediately with the correct response shape, and
      (b) save_state IS called by the background worker with correctly reset
          state (only "name" and "account" survive; tracking fields stripped).

    The write_completed.wait() call is INSIDE the patch.object context so the
    mock remains active when the executor worker thread picks up the task.
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

    write_completed = threading.Event()
    saved_state: dict = {}

    def _capture_save(state):
        saved_state.update(state)
        write_completed.set()

    with (
        patch.object(app_module, "database") as db_mock,
        patch.object(app_module, "_refresh_account_totals"),
    ):
        db_mock.load_state.return_value = pre_flush_state
        db_mock.save_state.side_effect = _capture_save

        resp = client.post("/api/settings/flush-resync")
        # Wait inside patch context — executor worker fires after handler returns;
        # mock must be active when it does.
        write_completed.wait(timeout=2)

    assert resp.status_code == 200
    body = resp.get_json()

    # Response shape — route must report which symphonies will be reset
    assert "symphonies_reset" in body, "Response must include symphonies_reset key."
    assert "symphonies_reset_count" in body, "Response must include symphonies_reset_count key."
    assert body["symphonies_reset_count"] == 2
    assert set(body["symphonies_reset"]) == {"Alpha", "Beta"}

    # Background reset must have fired: save_state called within 2 s
    assert db_mock.save_state.called, (
        "database.save_state must be called by the background worker "
        "(dispatched via _DISMISS_EXECUTOR).  It was not called within 2 s."
    )

    # Verify reset content: only name + account survive for symphony entries
    assert "sym_aaa" in saved_state, "sym_aaa must be present in the saved state."
    assert saved_state["sym_aaa"] == {"name": "Alpha", "account": "acct-1"}, (
        "sym_aaa must be stripped to {name, account}; tracking fields must be removed."
    )
    assert saved_state["sym_bbb"] == {"name": "Beta", "account": "acct-2"}, (
        "sym_bbb must be stripped to {name, account}; tracking fields must be removed."
    )
    # Non-symphony metadata key must be preserved untouched
    assert saved_state.get("last_execution_mode") == "live", (
        "Non-symphony metadata key last_execution_mode must be preserved."
    )


def test_flush_resync_symphony_without_account_resets_cleanly(client, tmp_path, monkeypatch):
    """A symphony entry with no 'account' key is reported in symphonies_reset — no KeyError.

    CONTRACT: save_state is not called on the request thread; background worker
    handles the reset.  A no-account entry must reset to {"name": ...} only
    (no "account" key) without raising a KeyError.
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

    write_completed = threading.Event()
    saved_state: dict = {}

    def _capture_save(state):
        saved_state.update(state)
        write_completed.set()

    with (
        patch.object(app_module, "database") as db_mock,
        patch.object(app_module, "_refresh_account_totals"),
    ):
        db_mock.load_state.return_value = pre_flush_state
        db_mock.save_state.side_effect = _capture_save

        resp = client.post("/api/settings/flush-resync")
        # Wait inside patch context — executor worker runs after handler returns.
        write_completed.wait(timeout=2)

    assert resp.status_code == 200
    body = resp.get_json()
    # No-account symphony must appear in the reset list without causing a KeyError
    assert "NoAccount Symphony" in body.get("symphonies_reset", []), (
        "Symphony without 'account' must appear in symphonies_reset — no KeyError."
    )
    # Background write must have fired
    assert db_mock.save_state.called, (
        "database.save_state must be called by the background worker."
    )
    # No-account entry resets to {name} only — no spurious "account" key added
    assert saved_state.get("sym_no_acct") == {"name": "NoAccount Symphony"}, (
        "No-account symphony must reset to {name} only — no account key injected."
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

    write_completed = threading.Event()

    def _capture_save(_state):
        write_completed.set()

    with (
        patch.object(app_module, "database") as db_mock,
        patch.object(app_module, "_refresh_account_totals"),
    ):
        db_mock.load_state.return_value = {}
        db_mock.save_state.side_effect = _capture_save

        resp = client.post("/api/settings/flush-resync")
        # Wait inside patch context for executor worker to complete.
        write_completed.wait(timeout=2)

    assert resp.status_code == 200
    assert analytics._HISTORY_CACHE == {"key": None, "data": None}, (
        "Analytics _HISTORY_CACHE must be reset to {key: None, data: None} after flush."
    )
