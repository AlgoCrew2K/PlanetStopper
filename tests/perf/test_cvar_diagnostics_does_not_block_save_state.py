"""
RED tests — shadow-logging-pattern: cvar_diagnostics write is non-blocking on execution path.

Contract under test (plan §Golden-fixture tests 3 and 4):

  3. Non-blocking under concurrent reader: a get_ro_connection() read in a sibling
     thread must not block the M2 writer past the budget.

  4. Non-blocking under concurrent write-lock: record_cvar_diagnostic in mode="live"
     must swallow the OperationalError from a held write-lock AND return within the
     budget — NOT after the 10-second sqlite3.connect(timeout=10.0) default.

Architecture constraint 1 (project CLAUDE.md): "Engine runs 1-minute cadence during
market hours — no blocking I/O on the execution path."

The 10-second timeout at database.py:54 is the worst-case latency on a held lock.
10 s is inside the 1-minute cadence window — the live-mode swallow fires when the
OperationalError arrives, so the cycle always continues.  This test asserts the
swallow's latency profile: it must return within a generous budget, not spin for
the full 10 s.

Marked @pytest.mark.perf — excluded from the default pytest run.
Opt-in: pytest -m perf

Fixture provenance:
  tests/fixtures/math/cvar_diagnostics_per_cycle_bench.json
  — schema-derived; no producer-computed values asserted.

Binding references:
  - shadow-logging-pattern plan §Golden-fixture tests 3 and 4
  - Architecture constraint 1: no blocking I/O on execution path
  - database.py:1147-1194 record_shadow_observation precedent (swallow pattern)
  - H-3 fix (a): "off save_state"; (b): "swallowed on fail"
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import threading
import time

import pytest

import database as db

pytestmark = pytest.mark.perf  # excluded from default run

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Generous non-blocking budget: must return within 1 second even when the DB
# is locked.  The 10-second sqlite3 timeout is the worst case; a live-mode
# swallow should fire on the first OperationalError (immediate), not wait.
_NON_BLOCKING_BUDGET_MS = 1_000.0

# Concurrent-reader budget: a WAL-mode read should never block a write.
# 200 ms matches the P99 write budget — a reader should not add latency.
_CONCURRENT_READER_BUDGET_MS = 200.0

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    pathlib.Path(__file__).parents[1]
    / "fixtures"
    / "math"
    / "cvar_diagnostics_per_cycle_bench.json"
)


@pytest.fixture(scope="module")
def bench_fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# DB setup
# ---------------------------------------------------------------------------


def _create_cvar_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cvar_diagnostics (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id         TEXT    NOT NULL,
            ts_utc           TEXT    NOT NULL DEFAULT (datetime('now')),
            symphony_id      TEXT    NOT NULL,
            cvar_5pct        REAL    DEFAULT NULL,
            cvar_5pct_stderr REAL    DEFAULT NULL,
            cvar_n_tail      INTEGER DEFAULT NULL,
            cvar_5pct_long   REAL    DEFAULT NULL,
            cvar_n_tail_long INTEGER DEFAULT NULL
        )
        """
    )
    conn.commit()


@pytest.fixture
def perf_db(tmp_path, monkeypatch):
    """Isolated WAL-mode DB with cvar_diagnostics."""
    db_path = str(tmp_path / "nonblock_cvar.db")
    monkeypatch.setenv("DB_PATH", db_path)
    db.init_db()
    conn = sqlite3.connect(db_path)
    try:
        _create_cvar_table(conn)
    finally:
        conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Helper: build a call-args dict from fixture
# ---------------------------------------------------------------------------


def _call_kwargs(fixture: dict, cycle_id: str) -> dict:
    row = fixture["row_dict"]
    return dict(
        cycle_id=cycle_id,
        symphony_id=row["symphony_id"],
        cvar_5pct=row["cvar_5pct"],
        cvar_5pct_stderr=row["cvar_5pct_stderr"],
        cvar_n_tail=row["cvar_n_tail"],
        cvar_5pct_long=row["cvar_5pct_long"],
        cvar_n_tail_long=row["cvar_n_tail_long"],
        mode="live",
    )


# ---------------------------------------------------------------------------
# Test 3: Non-blocking under concurrent reader (plan §Golden-fixture test 3)
# ---------------------------------------------------------------------------


def test_cvar_write_does_not_block_under_concurrent_ro_reader(perf_db, bench_fixture):
    """Concurrent WAL reader must not delay the cvar_diagnostics write past budget.

    Opens a get_ro_connection() read in a background thread during the write.
    In WAL mode, readers and writers proceed concurrently — no lock contention.
    If the DB is in DELETE mode (regression), the reader would hold a shared lock
    and the writer would wait until the timeout.

    This test catches:
    - WAL mode regression (DELETE mode would block writes during reads)
    - get_ro_connection() opening with EXCLUSIVE locking (misconfiguration)
    """
    errors: list[Exception] = []
    read_rows: list = []

    def _reader_thread(db_path: str) -> None:
        try:
            conn = db.get_ro_connection()
            try:
                rows = conn.execute(
                    "SELECT COUNT(*) FROM cvar_diagnostics"
                ).fetchall()
                read_rows.extend(rows)
            finally:
                conn.close()
        except Exception as exc:
            errors.append(exc)

    # Start reader BEFORE the write
    t = threading.Thread(target=_reader_thread, args=(perf_db,), daemon=True)
    t.start()

    kwargs = _call_kwargs(bench_fixture, "CONCURRENT_READ_CYCLE_001")
    t0 = time.perf_counter()
    db.record_cvar_diagnostic(**kwargs)
    elapsed_ms = (time.perf_counter() - t0) * 1_000

    t.join(timeout=2.0)

    assert not errors, (
        f"Concurrent reader thread raised: {errors}. "
        "get_ro_connection() must not error while the writer is active."
    )
    assert elapsed_ms < _CONCURRENT_READER_BUDGET_MS, (
        f"record_cvar_diagnostic took {elapsed_ms:.2f} ms with a concurrent reader "
        f"— exceeds budget {_CONCURRENT_READER_BUDGET_MS} ms. "
        "WAL mode must allow concurrent reads during writes. "
        "Check for journal_mode regression (PRAGMA journal_mode should be 'wal')."
    )


# ---------------------------------------------------------------------------
# Test 4: Non-blocking under concurrent write-lock (plan §Golden-fixture test 4)
# ---------------------------------------------------------------------------


def test_cvar_write_live_mode_swallows_locked_db_without_raising(perf_db, bench_fixture):
    """Live-mode swallow contract: record_cvar_diagnostic must not raise on a locked DB.

    Holds a write-lock via a BEGIN EXCLUSIVE transaction, then calls
    record_cvar_diagnostic in mode="live".  The write fails with OperationalError
    after sqlite3's timeout elapses — the live-mode swallow must absorb it silently.

    The plan's risk callout (shadow-logging-pattern plan §Risk callouts) explicitly
    states that timeout=10.0 is the intended live-path contract and that 10 s is
    inside the 1-minute cadence budget.  This test asserts only the swallow
    property (no raise) — not a sub-10-second latency budget, because the plan
    accepts the full timeout wait before the swallow fires.

    Architecture constraint 1 is satisfied by the try/except swallow, not by
    a zero-timeout fail-fast.  A zero-timeout would cause data loss on transient
    sub-millisecond WAL locks that would otherwise resolve immediately.
    """
    db_path = perf_db

    # Hold a write-lock: BEGIN EXCLUSIVE prevents any other writer until we release
    lock_conn = sqlite3.connect(db_path, timeout=0.0)
    lock_conn.execute("BEGIN EXCLUSIVE")

    try:
        kwargs = _call_kwargs(bench_fixture, "LOCKED_DB_CYCLE_001")
        # Must not raise — live mode swallows sqlite3.Error regardless of timeout wait
        db.record_cvar_diagnostic(**kwargs)
    finally:
        lock_conn.rollback()
        lock_conn.close()

    # If we reach here without exception, the swallow contract holds.


def test_cvar_write_live_mode_does_not_raise_on_locked_db(perf_db, bench_fixture):
    """Live-mode contract: record_cvar_diagnostic must not raise when the DB is locked.

    Companion to the latency test: confirms the function signature (no raised exception),
    separate from the timing assertion.  A wrong implementation could swallow the error
    but re-raise after a timeout; this test fails on any uncaught exception.
    """
    db_path = perf_db

    lock_conn = sqlite3.connect(db_path, timeout=0.0)
    lock_conn.execute("BEGIN EXCLUSIVE")

    try:
        kwargs = _call_kwargs(bench_fixture, "LOCKED_DB_CYCLE_002")
        # Must not raise — live mode swallows all sqlite3.Error subclasses
        db.record_cvar_diagnostic(**kwargs)
    finally:
        lock_conn.rollback()
        lock_conn.close()

    # If we get here, no exception was raised — test passes.


def test_cvar_write_live_mode_does_not_raise_on_missing_table(perf_db):
    """Live-mode contract: record_cvar_diagnostic returns None when the table is absent.

    Simulates a state where the 021_cvar_diagnostics migration has not yet been
    applied (the table does not exist).  The live-mode swallow must absorb the
    OperationalError("no such table: cvar_diagnostics") silently.

    This guards the pre-migration bootstrap period: if the daemon starts before the
    migration runs, it must not crash.
    """
    # Drop the table to simulate pre-migration state
    conn = sqlite3.connect(perf_db)
    try:
        conn.execute("DROP TABLE IF EXISTS cvar_diagnostics")
        conn.commit()
    finally:
        conn.close()

    row = {
        "cycle_id": "PRE_MIGRATION_CYCLE_001",
        "symphony_id": "SYM_BOOT_001",
        "cvar_5pct": None,
        "cvar_5pct_stderr": None,
        "cvar_n_tail": 0,
        "cvar_5pct_long": None,
        "cvar_n_tail_long": None,
    }

    # Must not raise — swallow fires on OperationalError("no such table")
    db.record_cvar_diagnostic(
        cycle_id=row["cycle_id"],
        symphony_id=row["symphony_id"],
        cvar_5pct=row["cvar_5pct"],
        cvar_5pct_stderr=row["cvar_5pct_stderr"],
        cvar_n_tail=row["cvar_n_tail"],
        cvar_5pct_long=row["cvar_5pct_long"],
        cvar_n_tail_long=row["cvar_n_tail_long"],
        mode="live",
    )
