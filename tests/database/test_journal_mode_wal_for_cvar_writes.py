"""
RED tests — shadow-logging-pattern: WAL journal mode guard for cvar_diagnostics writes.

Contract under test (plan §Golden-fixture test 5):
  - `PRAGMA journal_mode` on the state DB returns 'wal'.
  - This protects against an inadvertent regression that switches to DELETE-mode
    journaling, which would lock dashboard readers during the M2 write.

Why separate from test_wal_ro_connection.py:
  That file tests WAL + get_ro_connection().  This file is the shadow-logging-
  pattern plan's binding guard: if the DB is not in WAL mode, a concurrent
  dashboard reader (get_ro_connection) would block for the duration of every
  cvar_diagnostics INSERT — up to 50 ms per symphony per cycle, visible as a
  periodic dashboard freeze.

Binding references:
  - shadow-logging-pattern plan §Deliverable 3 + §Golden-fixture test 5
  - Architecture constraint 1: no blocking I/O on the execution path
  - Architecture constraint 5: templates open SQLite read-only (the dashboard
    reader that WAL protects)
  - database.py:71-75 (init_db WAL PRAGMA)

Fixture provenance: schema-derived; no producer-computed values.
"""

from __future__ import annotations

import inspect
import pathlib
import sqlite3
from unittest.mock import patch

import pytest

import database as db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WORKTREE_ROOT = pathlib.Path(__file__).parents[2]


# ---------------------------------------------------------------------------
# Test 5a: WAL mode is active after init_db (binding for cvar writes)
# ---------------------------------------------------------------------------


def test_journal_mode_is_wal_after_init_db(tmp_path):
    """cvar_diagnostics writes require WAL mode: init_db must set journal_mode=wal.

    WAL mode allows the dashboard reader (get_ro_connection) to proceed
    concurrently with the M2 cvar_diagnostics INSERT.  DELETE mode would
    serialise reads and writes, causing a ~50 ms dashboard freeze per
    INSERT cycle.

    This is a regression guard: a future schema change must not inadvertently
    replace the WAL PRAGMA.
    """
    db_path = str(tmp_path / "wal_cvar.db")
    with patch.object(db, "DB_FILE", db_path):
        db.init_db()

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("PRAGMA journal_mode").fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row[0].lower() == "wal", (
        f"State DB journal_mode is '{row[0]}', expected 'wal'. "
        "cvar_diagnostics writes require WAL mode so the dashboard reader "
        "(get_ro_connection) can proceed concurrently. "
        "shadow-logging-pattern plan §Deliverable 3."
    )


def test_journal_mode_wal_pragma_present_in_init_db_source():
    """Source guard: init_db() must contain a PRAGMA journal_mode=WAL statement.

    Prevents a silent regression where the PRAGMA is removed without failing
    the DB-level test on an already-WAL file (SQLite preserves the mode).
    An AST/source check fails on the next deploy to a fresh DB.
    """
    source = inspect.getsource(db.init_db)
    assert "journal_mode" in source.lower() or "WAL" in source, (
        "init_db() source does not contain a PRAGMA journal_mode/WAL statement. "
        "The WAL PRAGMA must be explicit in init_db() — not assumed from a pre-existing DB file. "
        "shadow-logging-pattern plan §Deliverable 3."
    )


# ---------------------------------------------------------------------------
# Test 5b: WAL mode is not silently downgraded after cvar_diagnostics writes
# ---------------------------------------------------------------------------


def test_journal_mode_remains_wal_after_cvar_diagnostics_writes(tmp_path):
    """WAL mode must persist across cvar_diagnostics writes.

    Some configurations (read-only filesystems, Docker bind mounts) prevent
    WAL mode from taking effect.  A write sequence should not silently
    downgrade the mode.

    Creates the cvar_diagnostics table and executes 3 INSERTs via
    record_cvar_diagnostic, then re-checks journal_mode.
    """
    db_path = str(tmp_path / "wal_after_writes.db")
    with patch.object(db, "DB_FILE", db_path):
        db.init_db()

        conn = sqlite3.connect(db_path)
        try:
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
        finally:
            conn.close()

        # Execute 3 writes via the production call chain
        for i in range(3):
            db.record_cvar_diagnostic(
                cycle_id=f"WAL_GUARD_CYCLE_{i:03d}",
                symphony_id="SYM_WAL_TEST",
                cvar_5pct=None,
                cvar_5pct_stderr=None,
                cvar_n_tail=0,
                cvar_5pct_long=None,
                cvar_n_tail_long=None,
                mode="live",
            )

        # Re-check journal_mode after writes
        check_conn = sqlite3.connect(db_path)
        try:
            row = check_conn.execute("PRAGMA journal_mode").fetchone()
        finally:
            check_conn.close()

    assert row is not None
    assert row[0].lower() == "wal", (
        f"journal_mode degraded to '{row[0]}' after cvar_diagnostics writes. "
        "WAL mode must persist across writes. "
        "Check the init_db() PRAGMA sequence and the write path in write_telemetry_row."
    )


# ---------------------------------------------------------------------------
# Test 5c: No migration introduces journal_mode=DELETE
# ---------------------------------------------------------------------------


def test_no_migration_sets_journal_mode_to_delete():
    """No .sql migration file must issue PRAGMA journal_mode=delete or journal_mode=off.

    A migration that resets the journal mode would silently break the non-blocking
    dashboard-read guarantee for all subsequent cvar_diagnostics writes.
    This is a source-level guard.
    """
    migrations_dir = pathlib.Path(__file__).parents[2] / "migrations"
    offending_migrations: list[str] = []

    if not migrations_dir.exists():
        pytest.skip("migrations/ directory not found — skip source guard")

    for sql_file in migrations_dir.glob("*.sql"):
        content = sql_file.read_text(encoding="utf-8").lower()
        # journal_mode=delete, journal_mode=off, or journal_mode=memory would all break WAL
        if (
            "journal_mode=delete" in content
            or "journal_mode=off" in content
            or "journal_mode=memory" in content
        ):
            offending_migrations.append(sql_file.name)

    assert not offending_migrations, (
        f"Migration(s) set journal_mode to a non-WAL value: {offending_migrations}. "
        "Any migration that changes journal_mode overrides the WAL PRAGMA in init_db() "
        "and breaks the non-blocking cvar_diagnostics write contract."
    )


# ---------------------------------------------------------------------------
# Test: cvar_diagnostics migration is registered in _MIGRATION_FILES
# ---------------------------------------------------------------------------


def test_cvar_diagnostics_migration_registered_in_migration_files():
    """021_cvar_diagnostics.sql must be in database._MIGRATION_FILES.

    RED until the implementer adds it.  Without this entry, run_migrations()
    never creates the table — every record_cvar_diagnostic call fails with
    OperationalError("no such table: cvar_diagnostics") and the live-mode
    swallow discards all CVaR data silently.
    """
    assert hasattr(db, "_MIGRATION_FILES"), (
        "database._MIGRATION_FILES not found. It must be the ordered list of migration filenames."
    )
    migration_names = list(db._MIGRATION_FILES)
    cvar_migrations = [m for m in migration_names if "cvar_diagnostics" in m.lower()]

    assert len(cvar_migrations) >= 1, (
        "No cvar_diagnostics migration found in database._MIGRATION_FILES. "
        "Expected an entry like '021_cvar_diagnostics.sql'. "
        "shadow-logging-pattern plan §Dependencies: hard-depends on 021_cvar_diagnostics.sql."
    )


def test_cvar_diagnostics_migration_file_exists():
    """The cvar_diagnostics migration SQL file must exist on disk.

    RED until the sqlite-specialist creates migrations/021_cvar_diagnostics.sql.
    If the file is missing, run_migrations() will raise FileNotFoundError at
    daemon startup — blocking all migrations including those after 021.
    """
    migrations_dir = pathlib.Path(__file__).parents[2] / "migrations"
    cvar_sql_files = list(migrations_dir.glob("*cvar_diagnostics*.sql"))

    assert len(cvar_sql_files) >= 1, (
        f"No cvar_diagnostics migration SQL file found under {migrations_dir}. "
        "Expected migrations/021_cvar_diagnostics.sql (or a similarly named file). "
        "shadow-logging-pattern plan §Dependencies (1)."
    )
