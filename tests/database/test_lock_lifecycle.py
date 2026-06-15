"""
Regression tests for database.acquire_lock / release_lock lifecycle.

These tests pin the contract of the distributed execution lock that gates
engine-cycle overlap.  A regression in the stale-expiry branch (60 s) would
silently park the engine — exits would never trigger.

Threshold confirmed by reading database.py line 63:
    if row[0] == 1 and (current_time - row[1] < 60):
        return False
Boundary semantics: age < 60 → lock held; age >= 60 → lock expired (acquire
succeeds without release_lock).

All tests use a function-scoped tmp_path SQLite file so state never bleeds
between cases.  `time.time` is patched via unittest.mock.patch to avoid any
dependency on wall-clock timing.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

import database as db

# ---------------------------------------------------------------------------
# The confirmed stale-expiry threshold, read directly from database.py line 63.
# Change this constant if and only if the production threshold changes.
# ---------------------------------------------------------------------------
LOCK_EXPIRY_SECONDS = 60  # age >= 60 s → lock is considered stale


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def db_path(tmp_path):
    """
    Provide an isolated SQLite file for each test.

    Patches `database.DB_FILE` and `database.get_connection` so that all
    database module functions operate on the temp file rather than the
    production 'alphabot_state.db'.  Schema is initialised via init_db()
    using the same patched connection path.
    """
    test_db = str(tmp_path / "test_alphabot_state.db")

    def _get_conn():
        return sqlite3.connect(test_db, timeout=10.0)

    with (
        patch.object(db, "DB_FILE", test_db),
        patch.object(db, "get_connection", side_effect=_get_conn),
    ):
        db.init_db()
        yield test_db


# ---------------------------------------------------------------------------
# Helper: read lock row directly from DB
# ---------------------------------------------------------------------------


def _read_lock_row(db_path_str: str) -> tuple[int, float]:
    """Return (is_locked, timestamp) from execution_lock row id=1."""
    conn = sqlite3.connect(db_path_str, timeout=10.0)
    cursor = conn.cursor()
    cursor.execute("SELECT is_locked, timestamp FROM execution_lock WHERE id = 1")
    row = cursor.fetchone()
    conn.close()
    assert row is not None, "execution_lock row id=1 must exist after init_db()"
    return row  # (is_locked, timestamp)


# ---------------------------------------------------------------------------
# Test 1 — Fresh acquire succeeds
# ---------------------------------------------------------------------------


def test_fresh_acquire_returns_true(db_path):
    """
    On a freshly initialised DB (is_locked=0, timestamp=0), acquire_lock()
    must return True.  This is the happy-path entry to every engine cycle.
    """
    result = db.acquire_lock()
    assert result is True, "acquire_lock() must return True on a fresh (unlocked) DB"


# ---------------------------------------------------------------------------
# Test 2 — Double acquire blocks
# ---------------------------------------------------------------------------


def test_double_acquire_returns_false_immediately(db_path):
    """
    Immediately after a successful acquire, a second acquire_lock() must
    return False.  Concurrent engine cycles must not be allowed to proceed.
    """
    first = db.acquire_lock()
    assert first is True, "precondition: first acquire must succeed"

    second = db.acquire_lock()
    assert second is False, (
        "acquire_lock() must return False when lock is already held and has not yet expired"
    )


# ---------------------------------------------------------------------------
# Test 3 — Release allows re-acquire
# ---------------------------------------------------------------------------


def test_release_then_acquire_succeeds(db_path):
    """
    After release_lock(), the next acquire_lock() must return True.
    This is the normal cycle-complete → re-enter path.
    """
    db.acquire_lock()
    db.release_lock()

    result = db.acquire_lock()
    assert result is True, "acquire_lock() must return True after release_lock()"


# ---------------------------------------------------------------------------
# Test 4 — Stale lock expires at exactly 60 s (boundary inclusive)
# ---------------------------------------------------------------------------


def test_stale_lock_expires_at_60_seconds(db_path):
    """
    A lock acquired at T=0 with is_locked=1 must be treated as expired when
    the current time advances to exactly T+60.

    Boundary from line 63:  age < 60 → held.  age >= 60 → expired.
    At age == 60 the condition (age < 60) is False, so acquire must succeed.

    release_lock() is NOT called — this exercises the stale-expiry path, not
    the normal release path.
    """
    base_time = 1_000_000.0  # arbitrary fixed epoch offset

    # Step 1: acquire at T=base_time
    with patch("database.time") as mock_time:
        mock_time.time.return_value = base_time
        first = db.acquire_lock()
    assert first is True, "precondition: acquire at base_time must succeed"

    # Step 2: attempt re-acquire at T=base_time + LOCK_EXPIRY_SECONDS (exactly)
    # At this age the lock is >= 60 s old, so the stale-expiry branch fires.
    stale_time = base_time + LOCK_EXPIRY_SECONDS
    with patch("database.time") as mock_time:
        mock_time.time.return_value = stale_time
        result = db.acquire_lock()

    assert result is True, (
        f"acquire_lock() must return True when lock age == {LOCK_EXPIRY_SECONDS} s "
        f"(stale-expiry threshold).  A False here means the engine would be "
        f"permanently parked until manual intervention."
    )


# ---------------------------------------------------------------------------
# Test 5 — Pre-expiry lock blocks (threshold - 1 s)
# ---------------------------------------------------------------------------


def test_lock_still_held_one_second_before_expiry(db_path):
    """
    At T + 59 s the lock is still within the 60 s window (59 < 60 is True),
    so a second acquire must return False.

    This pins the off-by-one boundary: the expiry fires at >= 60, not > 59.
    """
    base_time = 1_000_000.0

    with patch("database.time") as mock_time:
        mock_time.time.return_value = base_time
        first = db.acquire_lock()
    assert first is True, "precondition: acquire at base_time must succeed"

    pre_expiry_time = base_time + LOCK_EXPIRY_SECONDS - 1  # 59 s later
    with patch("database.time") as mock_time:
        mock_time.time.return_value = pre_expiry_time
        result = db.acquire_lock()

    assert result is False, (
        f"acquire_lock() must return False at age {LOCK_EXPIRY_SECONDS - 1} s "
        f"(one second before expiry).  A True here means the engine could "
        f"overlap a still-running cycle."
    )


# ---------------------------------------------------------------------------
# Test 6 — Lock state is observable via DB schema at each lifecycle phase
# ---------------------------------------------------------------------------


def test_lock_row_state_transitions_are_correct(db_path):
    """
    Directly query the execution_lock row to verify is_locked transitions
    match the acquire/release contract.

    Phase A — after init:          is_locked == 0
    Phase B — after acquire:       is_locked == 1, timestamp is a positive float
    Phase C — after release:       is_locked == 0
    Phase D — after stale expiry:  is_locked == 1 (re-acquired), timestamp updated
    """
    # Phase A: freshly initialised
    is_locked, timestamp = _read_lock_row(db_path)
    assert is_locked == 0, "Phase A: is_locked must be 0 after init_db()"
    assert timestamp == 0, "Phase A: timestamp must be 0 after init_db()"

    # Phase B: after acquire
    base_time = 1_000_000.0
    with patch("database.time") as mock_time:
        mock_time.time.return_value = base_time
        db.acquire_lock()

    is_locked, timestamp = _read_lock_row(db_path)
    assert is_locked == 1, "Phase B: is_locked must be 1 after acquire_lock()"
    # timestamp must be a positive float matching the patched time
    # (pytest.approx tolerance: floating-point round-trip through SQLite TEXT
    # is exact for this value, so 1e-9 is a generous but meaningful bound)
    assert timestamp == pytest.approx(base_time, abs=1e-9), (
        "Phase B: timestamp must be set to the acquisition time"
    )

    # Phase C: after release
    db.release_lock()
    is_locked, timestamp = _read_lock_row(db_path)
    assert is_locked == 0, "Phase C: is_locked must be 0 after release_lock()"
    # timestamp is not reset by release_lock — only is_locked changes; that is
    # the documented contract (the column is ignored when is_locked == 0)

    # Phase D: acquire again then simulate stale-expiry re-acquire
    with patch("database.time") as mock_time:
        mock_time.time.return_value = base_time
        db.acquire_lock()

    stale_time = base_time + LOCK_EXPIRY_SECONDS
    with patch("database.time") as mock_time:
        mock_time.time.return_value = stale_time
        db.acquire_lock()  # stale-expiry re-acquire

    is_locked, timestamp = _read_lock_row(db_path)
    assert is_locked == 1, "Phase D: is_locked must be 1 after stale-expiry re-acquire"
    assert timestamp == pytest.approx(stale_time, abs=1e-9), (
        "Phase D: timestamp must be updated to the new acquisition time, "
        "not left at the original stale acquisition time"
    )
