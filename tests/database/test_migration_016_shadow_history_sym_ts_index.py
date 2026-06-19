"""
AC-10 (migration 031, formerly orphaned as 016) — additive composite index
on shadow_history.

The post-audit performance scenario C ("shadow trajectory") showed warm-cache
queries 245x-9700x slower than the pre-remediation baseline. Root cause:
``ORDER BY ts_utc DESC LIMIT 1`` runs BEFORE the cache check and
``idx_shadow_history_sym_day`` cannot serve the sort (the index keys on
trading_day, not ts_utc).

Fix (plan AC-10, BUG-001): the migration was originally written as
016_shadow_history_sym_ts_index.sql but that numeric slot was already occupied
by 016_spec_bundles.sql in database._MIGRATION_FILES. The file existed on disk
but was never run. BUG-001 renames it to 031 and registers it in
_MIGRATION_FILES after 030_per_symphony_live_mode.sql.

This test module validates the SQL file content in isolation (idempotency,
target table, column order). The end-to-end registration + runtime creation
is covered by tests/database/test_bug001_shadow_history_sym_ts_index.py.

Pin:
  1. migrations/031_shadow_history_sym_ts_index.sql exists.
  2. Its content creates the named composite index with IF NOT EXISTS
     against shadow_history(symphony_id, ts_utc).
  3. After manually applying the SQL to a fresh in-memory DB, the index
     is present with columns (symphony_id, ts_utc) in that order.

Provenance: SQL syntax and column ordering are read directly from the
migration file at test time, not from a producer cache.
"""

from __future__ import annotations

import pathlib
import re
import sqlite3

import pytest

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "migrations"
MIGRATION_FILE = MIGRATIONS_DIR / "031_shadow_history_sym_ts_index.sql"
EXPECTED_INDEX_NAME = "idx_shadow_history_sym_ts"


class TestMigration031FileExists:
    """The BUG-001 fix renames the orphan 016 file to 031."""

    def test_migration_031_file_exists(self):
        assert MIGRATION_FILE.exists(), (
            f"AC-10 / BUG-001: expected file {MIGRATION_FILE!s} not "
            "found. The fix renames the orphan "
            "'016_shadow_history_sym_ts_index.sql' to "
            "'031_shadow_history_sym_ts_index.sql'."
        )

    def test_migration_031_contents_create_composite_index(self):
        """The file must contain a CREATE INDEX statement (idempotent via
        IF NOT EXISTS) on shadow_history(symphony_id, ts_utc)."""
        if not MIGRATION_FILE.exists():
            pytest.fail("031 migration not present; see preceding test.")
        sql = MIGRATION_FILE.read_text(encoding="utf-8").lower()

        assert "create index" in sql, (
            "Migration 031 must use CREATE INDEX, not a different DDL verb."
        )
        assert "if not exists" in sql, "Migration 031 must be idempotent — use 'IF NOT EXISTS'."
        assert EXPECTED_INDEX_NAME.lower() in sql, (
            f"Migration 031 must name the index {EXPECTED_INDEX_NAME!r}."
        )
        assert "shadow_history" in sql, "Migration 031 must target the shadow_history table."
        # The composite columns must appear together, in the canonical
        # order — column ORDER matters for index selection.
        pattern = re.compile(
            r"shadow_history\s*\(\s*symphony_id\s*,\s*ts_utc\s*\)",
            re.IGNORECASE,
        )
        assert pattern.search(sql), (
            "Migration 031 must index "
            "shadow_history(symphony_id, ts_utc) in that exact column "
            f"order. Current SQL: {sql!r}"
        )


class TestMigration031AppliedCreatesIndex:
    """Apply the migration to an in-memory SQLite DB seeded with the
    shadow_history table; assert the index is present and has the
    correct column ordering."""

    def test_applying_031_creates_named_composite_index(self):
        if not MIGRATION_FILE.exists():
            pytest.fail("031 migration not present.")
        sql = MIGRATION_FILE.read_text(encoding="utf-8")

        conn = sqlite3.connect(":memory:")
        # Minimal shadow_history schema — just enough columns for the
        # index. The production schema (migrations/008) is larger but the
        # index references only symphony_id and ts_utc.
        conn.executescript(
            """
            CREATE TABLE shadow_history (
                symphony_id TEXT NOT NULL,
                ts_utc      TEXT NOT NULL,
                trading_day TEXT NOT NULL
            );
            """
        )
        conn.executescript(sql)

        cur = conn.cursor()
        cur.execute(
            f"SELECT name FROM sqlite_master WHERE type='index' AND name='{EXPECTED_INDEX_NAME}';"
        )
        assert cur.fetchone() is not None, (
            f"AC-10 / BUG-001 migration 031 VIOLATED: index "
            f"{EXPECTED_INDEX_NAME!r} not present after applying the "
            "migration."
        )

        cur.execute(f"PRAGMA index_info('{EXPECTED_INDEX_NAME}');")
        info = cur.fetchall()
        # PRAGMA index_info rows: (seqno, cid, name)
        column_order = [row[2] for row in info]
        assert column_order == ["symphony_id", "ts_utc"], (
            f"AC-10 / BUG-001 migration 031 VIOLATED: composite index columns "
            f"are {column_order!r}; expected ['symphony_id', 'ts_utc'] "
            "in that order. Column ordering determines which queries the "
            "index can serve."
        )

        # Idempotency: re-applying must not raise.
        conn.executescript(sql)
        conn.close()
