"""
Regression tests for symphony log functions in database.py:
  - log_symphony_event
  - get_symphony_logs
  - clear_symphony_logs

These functions maintain a JSON file of per-symphony operational events that
are surfaced in the Flask dashboard.  A regression in isolation or clearing
would cause log bleed across symphonies, or stale events persisting across
sessions.

File isolation: every test redirects database.SYMPHONY_LOGS_FILE to a
per-test temp path using monkeypatch.setattr(db_module, "SYMPHONY_LOGS_FILE", ...)
analogous to the DB_FILE pattern in test_symphony_strategy.py.  No real
symphony_logs.json is ever touched by the test suite.
"""

from __future__ import annotations

import json
from unittest.mock import patch, mock_open, MagicMock

import pytest

import database as db_module
from database import (
    clear_symphony_logs,
    get_symphony_logs,
    log_symphony_event,
)


# ---------------------------------------------------------------------------
# Fixture: isolated log file path per test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_log_file(tmp_path, monkeypatch):
    """
    Redirect database.SYMPHONY_LOGS_FILE to a per-test temp path.

    The file is NOT pre-created — log_symphony_event must handle
    FileNotFoundError on its initial read (which it does via the
    (FileNotFoundError, json.JSONDecodeError) except clause).

    Monkeypatch is restored automatically after each test.
    """
    log_path = str(tmp_path / "test_symphony_logs.json")
    monkeypatch.setattr(db_module, "SYMPHONY_LOGS_FILE", log_path)
    yield log_path


# ---------------------------------------------------------------------------
# Test 1: log_symphony_event writes JSON with correct structure
# ---------------------------------------------------------------------------

def test_log_symphony_event_writes_three_entries(isolated_log_file):
    """
    Calling log_symphony_event 3 times for the same symphony must produce a
    JSON file where that symphony's list contains exactly 3 entries.

    Each entry shape is asserted (has 'timestamp', 'event_type', 'message')
    without asserting specific values — timestamps are production-computed.
    """
    symphony_id = "sym-log-three"
    messages = ["engine started", "stop triggered", "exit executed"]

    for msg in messages:
        log_symphony_event(symphony_id, msg, event_type="info")

    with open(isolated_log_file, "r", encoding="utf-8") as f:
        logs = json.load(f)

    assert symphony_id in logs, (
        f"log file must contain key '{symphony_id}'; keys found: {list(logs.keys())}"
    )

    entries = logs[symphony_id]
    assert len(entries) == 3, (
        f"Expected 3 log entries for '{symphony_id}'; got {len(entries)}"
    )

    for i, entry in enumerate(entries):
        assert "timestamp" in entry, (
            f"Entry {i} missing 'timestamp' key; keys: {list(entry.keys())}"
        )
        assert "event_type" in entry, (
            f"Entry {i} missing 'event_type' key; keys: {list(entry.keys())}"
        )
        assert "message" in entry, (
            f"Entry {i} missing 'message' key; keys: {list(entry.keys())}"
        )
        # Timestamp must be a non-empty string ending in 'Z' (UTC ISO-8601)
        assert isinstance(entry["timestamp"], str) and entry["timestamp"].endswith("Z"), (
            f"Entry {i} 'timestamp' must be a UTC ISO-8601 string ending in 'Z'; "
            f"got {entry['timestamp']!r}"
        )
        assert entry["message"] == messages[i], (
            f"Entry {i} message mismatch: expected {messages[i]!r}, "
            f"got {entry['message']!r}"
        )


def test_log_symphony_event_preserves_event_type(isolated_log_file):
    """
    The event_type parameter passed to log_symphony_event must appear
    in the stored entry.  Default is 'info'; non-default types must also persist.
    """
    log_symphony_event("sym-etype", "stop hit", event_type="warning")
    log_symphony_event("sym-etype", "exit confirmed", event_type="critical")

    with open(isolated_log_file, "r", encoding="utf-8") as f:
        logs = json.load(f)

    entries = logs["sym-etype"]
    assert entries[0]["event_type"] == "warning", (
        f"First entry event_type must be 'warning'; got {entries[0]['event_type']!r}"
    )
    assert entries[1]["event_type"] == "critical", (
        f"Second entry event_type must be 'critical'; got {entries[1]['event_type']!r}"
    )


# ---------------------------------------------------------------------------
# Test 2: get_symphony_logs returns logs for the right symphony only
# ---------------------------------------------------------------------------

def test_get_symphony_logs_returns_only_matching_symphony(isolated_log_file):
    """
    After logging events for sym1 and sym2, get_symphony_logs(sym1) must
    return only sym1's events — not sym2's.

    This pins the isolation contract: symphony log entries must never bleed
    across symphony IDs.
    """
    log_symphony_event("sym1", "event-for-sym1-a", event_type="info")
    log_symphony_event("sym2", "event-for-sym2", event_type="info")
    log_symphony_event("sym1", "event-for-sym1-b", event_type="info")

    result = get_symphony_logs("sym1")

    assert isinstance(result, list), (
        f"get_symphony_logs must return a list; got {type(result)}"
    )
    assert len(result) == 2, (
        f"Expected 2 entries for sym1; got {len(result)}"
    )

    messages = [e["message"] for e in result]
    assert "event-for-sym1-a" in messages
    assert "event-for-sym1-b" in messages
    assert "event-for-sym2" not in messages, (
        "sym2's event must not appear in sym1's log results — cross-symphony bleed detected"
    )


def test_get_symphony_logs_returns_empty_list_for_unknown_symphony(isolated_log_file):
    """
    Requesting logs for a symphony that has never logged anything must return
    an empty list — not raise, not return None.
    """
    log_symphony_event("other-sym", "some event", event_type="info")

    result = get_symphony_logs("nonexistent-sym")

    assert result == [], (
        "get_symphony_logs for an unknown symphony must return []; "
        f"got {result!r}"
    )


def test_get_symphony_logs_returns_empty_list_when_file_absent():
    """
    When SYMPHONY_LOGS_FILE does not exist at all, get_symphony_logs must
    return an empty list — the FileNotFoundError handler must fire.

    Uses a monkeypatch pointing to a path that was never written.
    """
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        nonexistent = os.path.join(td, "does_not_exist.json")
        original = db_module.SYMPHONY_LOGS_FILE
        db_module.SYMPHONY_LOGS_FILE = nonexistent
        try:
            result = get_symphony_logs("any-sym")
        finally:
            db_module.SYMPHONY_LOGS_FILE = original

    assert result == [], (
        "get_symphony_logs must return [] when log file does not exist; "
        f"got {result!r}"
    )


# ---------------------------------------------------------------------------
# Test 3: clear_symphony_logs removes all entries
# ---------------------------------------------------------------------------

def test_clear_symphony_logs_empties_all_entries(isolated_log_file):
    """
    After logging events for multiple symphonies and then calling
    clear_symphony_logs, a subsequent get_symphony_logs for each symphony
    must return an empty list.

    The log file must still be valid JSON (empty object {}), not deleted.
    """
    for sym in ("sym-clear-a", "sym-clear-b"):
        log_symphony_event(sym, "before clear", event_type="info")

    clear_symphony_logs()

    assert get_symphony_logs("sym-clear-a") == [], (
        "sym-clear-a logs must be empty after clear_symphony_logs"
    )
    assert get_symphony_logs("sym-clear-b") == [], (
        "sym-clear-b logs must be empty after clear_symphony_logs"
    )

    # File must still be valid JSON (not deleted, not corrupted)
    with open(isolated_log_file, "r", encoding="utf-8") as f:
        content = json.load(f)
    assert content == {}, (
        f"Log file content after clear must be {{}}; got {content!r}"
    )


def test_clear_symphony_logs_idempotent(isolated_log_file):
    """
    Calling clear_symphony_logs twice in a row must not raise.
    The second clear operates on an already-empty file.
    """
    log_symphony_event("sym", "event", event_type="info")
    clear_symphony_logs()
    # Second clear — file is already empty
    clear_symphony_logs()

    assert get_symphony_logs("sym") == [], (
        "Logs must still be empty after double clear"
    )


# ---------------------------------------------------------------------------
# Test 4: log_symphony_event file-write failure is swallowed silently
# ---------------------------------------------------------------------------

def test_log_symphony_event_swallows_oserror_on_write(isolated_log_file):
    """
    If the file write raises OSError, log_symphony_event must not propagate
    the exception.  The production code catches 'except Exception' on the
    write side (database.py ~line 224), which covers OSError.

    The test patches the built-in open() to raise OSError only on the write
    call (mode='w'), so the initial read still works normally (avoiding a
    cascade that would make the assertion vacuous).
    """
    # Pre-seed the log file so the read path works correctly
    log_symphony_event("sym-oserr", "seed event", event_type="info")

    original_open = open  # noqa: builtin reference

    def _open_raiser(path, mode="r", **kwargs):
        if str(path) == isolated_log_file and "w" in mode:
            raise OSError("simulated disk full")
        return original_open(path, mode, **kwargs)

    with patch("builtins.open", side_effect=_open_raiser):
        # Must not raise — OSError on write must be swallowed
        log_symphony_event("sym-oserr", "event during write failure", event_type="info")


def test_log_symphony_event_does_not_swallow_keyboard_interrupt(isolated_log_file):
    """
    KeyboardInterrupt inherits from BaseException, not Exception.  The
    production 'except Exception' clause must NOT catch it — the interrupt
    must propagate.

    This test guards against a future broadening of the except clause to
    'except BaseException' which would make the engine un-interruptible.
    """
    original_open = open  # noqa: builtin reference

    def _open_ki_raiser(path, mode="r", **kwargs):
        if str(path) == isolated_log_file and "w" in mode:
            raise KeyboardInterrupt("simulated ctrl-c during write")
        return original_open(path, mode, **kwargs)

    with pytest.raises(KeyboardInterrupt):
        with patch("builtins.open", side_effect=_open_ki_raiser):
            log_symphony_event("sym-ki", "should propagate", event_type="info")


# ---------------------------------------------------------------------------
# Test 5: log entries accumulate across separate calls (append semantics)
# ---------------------------------------------------------------------------

def test_log_symphony_event_appends_across_separate_calls(isolated_log_file):
    """
    Each call to log_symphony_event must append to the existing list, not
    overwrite it.  After N calls the list must have exactly N entries.
    """
    symphony_id = "sym-append"
    n = 10

    for i in range(n):
        log_symphony_event(symphony_id, f"msg-{i}", event_type="info")

    result = get_symphony_logs(symphony_id)
    assert len(result) == n, (
        f"After {n} log_symphony_event calls, expected {n} entries; got {len(result)}"
    )

    # Order must be preserved (append semantics, not prepend)
    for i, entry in enumerate(result):
        assert entry["message"] == f"msg-{i}", (
            f"Entry {i} message must be 'msg-{i}'; got {entry['message']!r} — "
            "log entries must be appended in call order"
        )
