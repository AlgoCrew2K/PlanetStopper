"""
RED tests for migration 022_spec_bundles_add_id.sql — additive id column.

Context (rev-nn1 blocker, cycle/sprint2-nn1-spec-freeze):

  Migration 016_spec_bundles.sql was already applied to existing databases.
  The schema_migrations tracker prevents re-execution; CREATE TABLE IF NOT EXISTS
  is a no-op on live databases. The cycle's change that appended
  "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT" to migration 016 silently
  fails to materialise the id column on any non-fresh database.

  validate_nn1_compliance (autotuner.py) queries `WHERE id = ?` and
  database._SPEC_BUNDLE_COLUMNS includes "id". Both fail with:
      OperationalError: no such column: id
  against any database that had migration 016 applied before this cycle.

  Fix: a new additive migration 022_spec_bundles_add_id.sql that uses
  `ALTER TABLE spec_bundles ADD COLUMN id INTEGER` (nullable — SQLite ADD COLUMN
  cannot add NOT NULL PRIMARY KEY AUTOINCREMENT) plus a UNIQUE constraint added
  via a new index, with application-level backfill of id from rowid for existing rows.

These tests verify the additive migration contract:

  T20 — 022_spec_bundles_add_id.sql exists on disk
  T21 — 022 appears in _MIGRATION_FILES after 016_spec_bundles.sql
  T24 — After the full migration stack, inserting a new spec_bundles row produces a non-NULL id
  T26 — 022 is idempotent (run_migrations twice does not raise)
  T27 — 022 SQL does not use ALTER TABLE … ADD COLUMN … NOT NULL (SQLite limitation)

No hardcoded producer-computed values. All assertions are shape/presence/
structural properties.
"""

from __future__ import annotations

import pathlib
import sqlite3

import pytest

import database as db_module
from database import init_db, run_migrations

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_MIGRATION_022_PATH = (
    pathlib.Path(__file__).parents[2] / "migrations" / "022_spec_bundles_add_id.sql"
)

_MIGRATION_016_PATH = (
    pathlib.Path(__file__).parents[2] / "migrations" / "016_spec_bundles.sql"
)

# ---------------------------------------------------------------------------
# Shared DB fixture (full migration stack)
# ---------------------------------------------------------------------------


@pytest.fixture()
def migrated_db(tmp_path, monkeypatch):
    """Per-test isolated DB with all migrations applied (including 022)."""
    db_path = str(tmp_path / "test_alphabot_state.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _table_columns(db_path: str, table_name: str) -> dict[str, dict]:
    """Return {col_name: {notnull, dflt_value, pk, type}} via PRAGMA table_info."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    finally:
        conn.close()
    return {
        row[1]: {"notnull": row[3], "dflt_value": row[4], "pk": row[5], "type": row[2]}
        for row in rows
    }


# ===========================================================================
# T20 — migration file exists on disk
# ===========================================================================


def test_022_migration_file_exists():
    """
    migrations/022_spec_bundles_add_id.sql must exist on disk.

    The additive migration is required to materialise the id column on existing
    databases where migration 016 was already applied (and is now a no-op).
    """
    assert _MIGRATION_022_PATH.is_file(), (
        f"Migration file not found: {_MIGRATION_022_PATH}. "
        "Create migrations/022_spec_bundles_add_id.sql to backfill "
        "spec_bundles.id on pre-existing databases."
    )


# ===========================================================================
# T21 — 022 appears in _MIGRATION_FILES after its logical predecessor (016)
# ===========================================================================


def test_022_migration_present_and_after_016():
    """
    '022_spec_bundles_add_id.sql' must appear in database._MIGRATION_FILES
    after '016_spec_bundles.sql'.

    Append-only contract: 022 is an additive migration on spec_bundles (created
    by 016), so it must come after its logical predecessor. The contiguous-position
    assertion ("comes immediately after 021") that this replaced was a pinned-tail
    assertion — the same pattern that broke test_016 when cycle 019 landed (fixed
    in commit 4c3f3cb). Cycle n-effective appended 020 between 021 and 022, which
    broke the old pinned assertion because out-of-numeric-order appends are a
    deliberate convention in this codebase (append-chronological, not numeric order).

    Correct contract: 022 is present AND its index comes after 016 (its schema
    dependency). No mid-list-insertion claim — append-only means 022 was appended
    at some time after 016, not necessarily immediately after any given migration.
    """
    migrations = db_module._MIGRATION_FILES
    assert "022_spec_bundles_add_id.sql" in migrations, (
        "'022_spec_bundles_add_id.sql' not found in database._MIGRATION_FILES. "
        "Append it to the migration list (after '016_spec_bundles.sql')."
    )
    assert "016_spec_bundles.sql" in migrations, (
        "'016_spec_bundles.sql' not in _MIGRATION_FILES — required logical predecessor "
        "for the 022 order check (022 adds a column to the spec_bundles table "
        "created by 016)."
    )
    idx_016 = migrations.index("016_spec_bundles.sql")
    idx_022 = migrations.index("022_spec_bundles_add_id.sql")
    assert idx_022 > idx_016, (
        f"'022_spec_bundles_add_id.sql' (index {idx_022}) must appear after "
        f"'016_spec_bundles.sql' (index {idx_016}). "
        "022 depends on spec_bundles existing — it must come after its creator."
    )


# ===========================================================================
# T24 — new rows inserted after 022 get a non-NULL id
# ===========================================================================


def test_022_new_rows_get_non_null_id(migrated_db):
    """
    After the full migration stack (including 022), inserting a new spec_bundles
    row must produce a non-NULL, positive integer id.

    On a fresh DB (where migration 016 created spec_bundles with the id column
    from the start), this confirms the id column is present and auto-populated.

    Uses migrated_db (full stack) — not the pre_existing_db scenario.
    """
    conn = sqlite3.connect(migrated_db)
    try:
        conn.execute(
            "INSERT INTO spec_bundles (bundle_hash, facets_json) VALUES (?, ?)",
            ("new-row-hash-" + "f" * 51, '{"horizon": 63}'),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM spec_bundles WHERE bundle_hash = ?",
            ("new-row-hash-" + "f" * 51,),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "Inserted row must be retrievable."
    id_val = row[0]
    assert id_val is not None, (
        "spec_bundles.id is NULL for a newly inserted row after full migration stack. "
        "Either 022 did not materialise the column or the DEFAULT/AUTOINCREMENT is absent."
    )
    assert isinstance(id_val, int) and id_val > 0, (
        f"spec_bundles.id must be a positive integer; got {id_val!r}."
    )


# ===========================================================================
# T26 — migration 022 is idempotent
# ===========================================================================


def test_022_idempotent_run_migrations_twice_does_not_raise(migrated_db):
    """
    Calling run_migrations() a second time on a fully migrated DB (including 022)
    must not raise.

    SQLite ADD COLUMN on an already-present column raises OperationalError;
    the migration must guard against this (e.g., check PRAGMA table_info before
    ALTER, or rely on the schema_migrations tracker to skip re-application).
    """
    # Already applied once by the migrated_db fixture.
    run_migrations()  # second call
    run_migrations()  # third call — belt-and-suspenders

    # Table must still be intact.
    cols = _table_columns(migrated_db, "spec_bundles")
    assert "id" in cols, (
        "spec_bundles.id must still be present after repeated run_migrations() calls."
    )


# ===========================================================================
# T27 — 022 SQL does not use NOT NULL on the added column
# ===========================================================================


def test_022_sql_does_not_add_not_null_column():
    """
    SQLite's ALTER TABLE … ADD COLUMN does not support NOT NULL columns without
    a DEFAULT. A NOT NULL ADD COLUMN statement raises:
        OperationalError: Cannot add a NOT NULL column with default value NULL

    Migration 022 must add id as a nullable column (INTEGER without NOT NULL),
    then backfill existing rows to avoid NULL values at the application layer.

    This guards against a subtle SQLite limitation that would cause 022 to fail
    on deployment if the SQL is written naively.
    """
    assert _MIGRATION_022_PATH.is_file(), (
        f"Migration file not found: {_MIGRATION_022_PATH}. "
        "Create it before this test can pass."
    )
    sql_upper = _MIGRATION_022_PATH.read_text(encoding="utf-8").upper()

    # The ADD COLUMN clause must NOT include NOT NULL (without a DEFAULT that
    # satisfies all existing rows — which AUTOINCREMENT cannot be).
    # We look for the dangerous pattern: ADD COLUMN ... NOT NULL
    # A safe pattern is: ADD COLUMN id INTEGER (nullable, backfilled separately).
    import re  # noqa: PLC0415
    add_column_match = re.search(r"ADD\s+COLUMN\s+ID\s+\S+\s+NOT\s+NULL", sql_upper)
    assert add_column_match is None, (
        "022_spec_bundles_add_id.sql adds id as NOT NULL, which SQLite rejects "
        "on ALTER TABLE when existing rows are present and no DEFAULT covers them. "
        "Use: ALTER TABLE spec_bundles ADD COLUMN id INTEGER; "
        "then: UPDATE spec_bundles SET id = rowid WHERE id IS NULL;"
    )
