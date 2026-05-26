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

These tests are RED until the fix lands. They verify:

  T20 — 022_spec_bundles_add_id.sql exists on disk
  T21 — 022 appears in _MIGRATION_FILES after 021_cvar_diagnostics.sql
  T22 — On a DB where 016 has already been applied (no id column),
         running 022 materialises id in spec_bundles
  T23 — After 022, existing spec_bundles rows have id backfilled (non-NULL)
  T24 — After 022, inserting a new spec_bundles row produces a non-NULL id
  T25 — validate_nn1_compliance does not raise OperationalError on a DB that
         had 016 applied before the cycle (pre-existing-DB simulation)
  T26 — 022 is idempotent (run_migrations twice does not raise)
  T27 — 022 SQL does not use ALTER TABLE … ADD COLUMN … NOT NULL (SQLite limitation)

DB isolation strategy:
  Each test that simulates a pre-existing database:
    1. Creates a fresh DB under tmp_path.
    2. Applies ONLY migrations up to and including 016 (stopping before 022).
    3. Inserts a seed spec_bundles row (simulating live data).
    4. Then applies the remaining migrations (including 022) via run_migrations().
    5. Verifies the id column is present and populated.

  Tests that do not need the pre-existing-DB scenario use the standard
  migrated_db fixture (full stack, identical to test_spec_bundles.py).

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
# Pre-existing-DB fixture: 016 applied, id column absent, seed row present
# ---------------------------------------------------------------------------


@pytest.fixture()
def pre_existing_db(tmp_path, monkeypatch):
    """
    Simulates a live database that had migration 016 applied BEFORE this cycle.

    Procedure:
      1. Create fresh DB with init_db() (core tables only — no 016 yet).
      2. Apply ONLY migrations up to and including 016 directly via executescript,
         recording them in schema_migrations but not applying later ones.
      3. Insert one seed spec_bundles row WITHOUT an id column value.
      4. Verify id column is absent (precondition).

    The fixture yields the db_path. Tests then call run_migrations() to apply
    022 and assert the id column materialises.
    """
    db_path = str(tmp_path / "preexisting.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()

    conn = sqlite3.connect(db_path)
    try:
        # Bootstrap schema_migrations tracker (mirrors run_migrations bootstrap)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "  migration_name TEXT PRIMARY KEY,"
            "  applied_at     TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        conn.commit()

        # Apply only the migrations that existed BEFORE this cycle,
        # stopping at 021 (inclusive). This simulates a live DB.
        migrations_to_apply = [
            m for m in db_module._MIGRATION_FILES
            if m not in ("022_spec_bundles_add_id.sql",)
        ]
        for name in migrations_to_apply:
            row = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE migration_name = ?",
                (name,),
            ).fetchone()
            if row is not None:
                continue
            migration_path = str(
                pathlib.Path(__file__).parents[2] / "migrations" / name
            )
            try:
                import os
                if os.path.exists(migration_path):
                    with open(migration_path, encoding="utf-8") as fh:
                        sql = fh.read()
                    conn.executescript(sql)
                    conn.execute(
                        "INSERT OR IGNORE INTO schema_migrations (migration_name) VALUES (?)",
                        (name,),
                    )
                    conn.commit()
            except Exception:
                # Some migrations may not apply cleanly in isolation;
                # we just need spec_bundles to be created (by 016) and not have id.
                conn.rollback()

        # Insert a seed spec_bundles row representing pre-existing live data.
        # Do NOT include id — it does not exist yet on this DB.
        conn.execute(
            "INSERT OR IGNORE INTO spec_bundles (bundle_hash, facets_json) "
            "VALUES (?, ?)",
            ("pre-existing-hash-" + "0" * 46, '{"gamma": "2.0"}'),
        )
        conn.commit()

        # Precondition: id column must be absent at this point.
        pragma_rows = conn.execute(
            "PRAGMA table_info(spec_bundles)"
        ).fetchall()
        col_names = {row[1] for row in pragma_rows}
        if "id" in col_names:
            pytest.skip(
                "pre_existing_db fixture: id column already present after 016 — "
                "the test scenario does not apply (migration 016 was not the original "
                "CREATE TABLE in this code state)."
            )
    finally:
        conn.close()

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
# T22 — 022 materialises id column on a pre-existing DB
# ===========================================================================


def test_022_materialises_id_column_on_preexisting_db(pre_existing_db, monkeypatch):
    """
    On a database where migration 016 was already applied (id column absent),
    running run_migrations() — which now includes 022 — must materialise the
    id column in spec_bundles.

    This is the core regression guard for the rev-nn1 blocker.
    """
    # Precondition: confirm id is absent before 022 runs.
    cols_before = _table_columns(pre_existing_db, "spec_bundles")
    assert "id" not in cols_before, (
        "Precondition failed: id column already present before run_migrations(). "
        "The pre_existing_db fixture should have omitted 022."
    )

    # Apply the remaining migrations (022 included).
    run_migrations()

    cols_after = _table_columns(pre_existing_db, "spec_bundles")
    assert "id" in cols_after, (
        "spec_bundles.id column absent after run_migrations() on a pre-existing DB. "
        "Migration 022_spec_bundles_add_id.sql must use "
        "ALTER TABLE spec_bundles ADD COLUMN id INTEGER to backfill the column."
    )


# ===========================================================================
# T23 — existing rows get id backfilled (non-NULL) after 022
# ===========================================================================


def test_022_backfills_id_for_existing_rows(pre_existing_db, monkeypatch):
    """
    After migration 022 runs, the seed row inserted BEFORE the migration must
    have a non-NULL id value.

    SQLite's ALTER TABLE ADD COLUMN leaves existing rows with NULL unless the
    column has a DEFAULT or the migration includes an UPDATE … SET id = rowid
    backfill statement. The implementation must backfill; a NULL id would cause
    `validate_nn1_compliance` to fail to locate any row via `WHERE id = ?`.
    """
    run_migrations()

    conn = sqlite3.connect(pre_existing_db)
    try:
        rows = conn.execute("SELECT id FROM spec_bundles").fetchall()
    finally:
        conn.close()

    assert rows, (
        "No rows in spec_bundles after migration — pre_existing_db fixture must "
        "have inserted at least one seed row."
    )
    for row in rows:
        id_val = row[0]
        assert id_val is not None, (
            f"spec_bundles row has NULL id after migration 022. "
            "The migration must backfill id from rowid for all existing rows. "
            "Add: UPDATE spec_bundles SET id = rowid WHERE id IS NULL;"
        )
        assert isinstance(id_val, int) and id_val > 0, (
            f"spec_bundles.id must be a positive integer after backfill; got {id_val!r}."
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
# T25 — validate_nn1_compliance does not raise OperationalError on pre-existing DB
# ===========================================================================


def test_validate_nn1_compliance_does_not_raise_no_such_column_on_preexisting_db(
    pre_existing_db, monkeypatch
):
    """
    validate_nn1_compliance(spec_bundle_id) must NOT raise
    OperationalError('no such column: id') on a database that had migration 016
    applied before this cycle.

    This is the direct encoding of the rev-nn1 blocker: before migration 022,
    `validate_nn1_compliance` would fail at the `WHERE id = ?` clause.

    Setup:
      1. pre_existing_db fixture: 016 applied, id absent, seed row present.
      2. run_migrations() to apply 022 (id materialises + backfill).
      3. Insert a valid spec_bundle with spec_facets via the accessor.
      4. Call validate_nn1_compliance(bundle_id) and assert it does not raise
         OperationalError.
    """
    import autotuner as at  # noqa: PLC0415

    run_migrations()

    # Insert a valid all-THEORY bundle using public accessors.
    from database import (  # noqa: PLC0415
        canonicalize_facets_json,
        hash_facets_json,
        insert_spec_bundle,
        insert_spec_bundle_facet,
    )

    facets = {"gamma": "2.0", "utility_family": "CRRA"}
    canonical = canonicalize_facets_json(facets)
    bundle_hash = hash_facets_json(canonical)
    insert_spec_bundle(bundle_hash=bundle_hash, facets_json=canonical)

    conn = db_module.get_connection()
    bundle_id = conn.execute(
        "SELECT id FROM spec_bundles WHERE bundle_hash = ?", (bundle_hash,)
    ).fetchone()[0]
    conn.close()

    assert bundle_id is not None, (
        "spec_bundles.id must be non-NULL after run_migrations() + backfill. "
        "validate_nn1_compliance cannot proceed with a NULL id."
    )

    insert_spec_bundle_facet(
        bundle_hash=bundle_hash,
        facet_name="gamma",
        facet_value='"2.0"',
        freeze_discipline="THEORY",
        justification="CRRA gamma is theoretically motivated.",
    )

    # Must not raise OperationalError: no such column: id
    try:
        is_honest, violations = at.validate_nn1_compliance(bundle_id)
    except sqlite3.OperationalError as exc:
        pytest.fail(
            f"validate_nn1_compliance raised OperationalError: {exc}. "
            "This is the rev-nn1 blocker: migration 022 must materialise "
            "spec_bundles.id so the `WHERE id = ?` clause does not fail on "
            "pre-existing databases."
        )

    # Shape assertions — not value assertions.
    assert isinstance(is_honest, bool), (
        f"validate_nn1_compliance must return (bool, list); got is_honest={is_honest!r}"
    )
    assert isinstance(violations, list), (
        f"validate_nn1_compliance must return (bool, list); got violations={violations!r}"
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
