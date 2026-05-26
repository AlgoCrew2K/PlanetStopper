"""
Tests for migration 024_spec_facets_unique_constraint.sql.

Migration 024 enforces UNIQUE(bundle_hash, facet_name) on spec_facets using the
additive-first approach (project CLAUDE.md mandate: never destructive in one step):

  1. DELETE duplicate rows, keeping the lowest-id row per (bundle_hash, facet_name).
  2. CREATE UNIQUE INDEX IF NOT EXISTS idx_spec_facets_bundle_facet ON spec_facets.

No table rebuild, no DROP, no RENAME.

Coverage (all three bullets from rev-theory Option B adapted for Option A):

  T1 — Migration file exists on disk and is registered in _MIGRATION_FILES
       after migration 023_autotune_runs_s_count.sql.
  T2 — Existing rows survive the migration with content intact (no data loss).
  T3 — Duplicate rows are deduplicated to the lowest-id row per
       (bundle_hash, facet_name) pair before the index is created.
  T4 — After the migration, the UNIQUE index exists on spec_facets.
  T5 — After the migration, inserting a duplicate (bundle_hash, facet_name)
       pair via plain INSERT raises IntegrityError (schema enforcement works).
  T6 — insert_spec_bundle_facet with INSERT OR IGNORE is a no-op on a duplicate
       (bundle_hash, facet_name) pair — returns the existing row's id, does not
       add a row, and does not raise.
  T7 — Migration is idempotent: run_migrations() twice does not raise, and the
       index still exists after the second call.
  T8 — 024 SQL does not contain DROP TABLE or RENAME (additive-only guard).

DB isolation: each test uses a fresh per-test temp DB with full migration stack.
The pre-duplicate scenario (T2, T3) manually inserts duplicate rows into a DB
that has migrations 001-023 applied but NOT 024, then runs 024.
"""

from __future__ import annotations

import pathlib
import sqlite3

import pytest

import database as db_module
from database import run_migrations

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_WORKTREE_ROOT = pathlib.Path(__file__).parents[2]
_MIGRATION_024_PATH = _WORKTREE_ROOT / "migrations" / "024_spec_facets_unique_constraint.sql"
_MIGRATION_023_PATH = _WORKTREE_ROOT / "migrations" / "023_autotune_runs_s_count.sql"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def migrated_db(tmp_path, monkeypatch):
    """Per-test isolated DB with all migrations applied (including 024)."""
    db_path = str(tmp_path / "test_alphabot_state.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    db_module.init_db()
    run_migrations()
    yield db_path


@pytest.fixture()
def pre_024_db(tmp_path, monkeypatch):
    """DB with migrations up to and including 023, but NOT 024.

    Used to test the deduplication + index creation path when duplicate rows
    may already exist from the pre-024 concurrent-insert window.
    """
    db_path = str(tmp_path / "pre_024.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)

    # Apply all migrations except 024 by temporarily removing it from the list.
    original_files = list(db_module._MIGRATION_FILES)
    without_024 = [f for f in original_files if "024" not in f]
    monkeypatch.setattr(db_module, "_MIGRATION_FILES", without_024)
    db_module.init_db()  # runs run_migrations() internally with the patched list

    # Restore so callers can apply 024 explicitly.
    monkeypatch.setattr(db_module, "_MIGRATION_FILES", original_files)
    yield db_path


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _get_index_names(db_path: str) -> set[str]:
    """Return the set of index names on spec_facets."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='spec_facets'"
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def _count_facets(db_path: str, bundle_hash: str, facet_name: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM spec_facets WHERE bundle_hash=? AND facet_name=?",
            (bundle_hash, facet_name),
        ).fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# T1 — File exists and is registered after 023
# ---------------------------------------------------------------------------


def test_migration_024_file_exists_on_disk():
    """024_spec_facets_unique_constraint.sql must exist in the migrations directory."""
    assert _MIGRATION_024_PATH.is_file(), (
        f"024_spec_facets_unique_constraint.sql not found at {_MIGRATION_024_PATH}. "
        "The migration file must be committed alongside the application change."
    )


def test_migration_024_present_and_after_023():
    """024 must appear in _MIGRATION_FILES after 023_autotune_runs_s_count.sql.

    Mirrors the append-pattern test in test_016 and test_022 hotfixes.
    """
    files = db_module._MIGRATION_FILES
    assert any("024" in f for f in files), (
        "'024_spec_facets_unique_constraint.sql' not found in database._MIGRATION_FILES. "
        "Add it to the list."
    )
    idx_023 = next((i for i, f in enumerate(files) if "023" in f), None)
    idx_024 = next((i for i, f in enumerate(files) if "024" in f), None)
    assert idx_023 is not None, "023_autotune_runs_s_count.sql missing from _MIGRATION_FILES"
    assert idx_024 is not None, "024_spec_facets_unique_constraint.sql missing from _MIGRATION_FILES"
    assert idx_024 > idx_023, (
        f"024 (position {idx_024}) must come after 023 (position {idx_023}) "
        "in _MIGRATION_FILES."
    )


# ---------------------------------------------------------------------------
# T2 — Existing rows survive with content intact
# ---------------------------------------------------------------------------


def test_existing_facet_rows_survive_migration_024(pre_024_db):
    """Rows inserted before migration 024 runs must be present with unchanged
    content after the migration completes.

    No data loss on a clean DB (no duplicates).
    """
    # Insert a bundle and two clean (non-duplicate) facet rows.
    canonical_json = db_module.canonicalize_facets_json({"test": "survive"})
    bundle_hash = db_module.hash_facets_json(canonical_json)
    db_module.insert_spec_bundle(bundle_hash=bundle_hash, facets_json=canonical_json)

    conn = sqlite3.connect(pre_024_db)
    try:
        conn.execute(
            "INSERT INTO spec_facets (bundle_hash, facet_name, facet_value, freeze_discipline) "
            "VALUES (?, 'alpha', '\"A\"', 'THEORY')",
            (bundle_hash,),
        )
        conn.execute(
            "INSERT INTO spec_facets (bundle_hash, facet_name, facet_value, freeze_discipline) "
            "VALUES (?, 'beta', '\"B\"', 'MANDATE')",
            (bundle_hash,),
        )
        conn.commit()
    finally:
        conn.close()

    # Apply 024.
    run_migrations()

    # Both rows must still be present.
    facets = db_module.get_spec_facets_for_bundle(bundle_hash)
    names = {f["facet_name"] for f in facets}
    assert "alpha" in names, "Facet 'alpha' was lost during migration 024."
    assert "beta" in names, "Facet 'beta' was lost during migration 024."

    values = {f["facet_name"]: f["facet_value"] for f in facets}
    assert values["alpha"] == '"A"', (
        f"Facet 'alpha' value changed during migration 024: got {values['alpha']!r}"
    )
    assert values["beta"] == '"B"', (
        f"Facet 'beta' value changed during migration 024: got {values['beta']!r}"
    )


# ---------------------------------------------------------------------------
# T3 — Duplicate rows deduplicated to lowest-id row
# ---------------------------------------------------------------------------


def test_duplicate_facet_rows_deduplicated_to_lowest_id(pre_024_db):
    """When duplicate (bundle_hash, facet_name) rows exist before 024 runs,
    the migration must remove all but the lowest-id row per pair.

    The kept row's content (facet_value, freeze_discipline) must match the
    lowest-id row — "first write wins."
    """
    canonical_json = db_module.canonicalize_facets_json({"test": "dedup"})
    bundle_hash = db_module.hash_facets_json(canonical_json)
    db_module.insert_spec_bundle(bundle_hash=bundle_hash, facets_json=canonical_json)

    # Manually insert three duplicate rows (simulating the pre-024 concurrent-insert race).
    conn = sqlite3.connect(pre_024_db)
    try:
        conn.execute(
            "INSERT INTO spec_facets (bundle_hash, facet_name, facet_value, freeze_discipline) "
            "VALUES (?, 'gamma', '\"first\"', 'THEORY')",
            (bundle_hash,),
        )
        conn.execute(
            "INSERT INTO spec_facets (bundle_hash, facet_name, facet_value, freeze_discipline) "
            "VALUES (?, 'gamma', '\"second\"', 'THEORY')",
            (bundle_hash,),
        )
        conn.execute(
            "INSERT INTO spec_facets (bundle_hash, facet_name, facet_value, freeze_discipline) "
            "VALUES (?, 'gamma', '\"third\"', 'THEORY')",
            (bundle_hash,),
        )
        conn.commit()
    finally:
        conn.close()

    # Precondition: 3 rows for (bundle_hash, 'gamma').
    assert _count_facets(pre_024_db, bundle_hash, "gamma") == 3, (
        "Precondition failed: expected 3 duplicate rows before migration 024."
    )

    # Apply 024.
    run_migrations()

    # After migration: exactly 1 row remains.
    assert _count_facets(pre_024_db, bundle_hash, "gamma") == 1, (
        "Migration 024 must deduplicate to exactly 1 row per (bundle_hash, facet_name)."
    )

    # The surviving row must be the first-inserted one ("first write wins").
    facets = db_module.get_spec_facets_for_bundle(bundle_hash)
    gamma = next(f for f in facets if f["facet_name"] == "gamma")
    assert gamma["facet_value"] == '"first"', (
        f"Migration 024 kept facet_value={gamma['facet_value']!r}; "
        "expected the lowest-id (first-written) row to survive."
    )


# ---------------------------------------------------------------------------
# T4 — UNIQUE index exists after migration
# ---------------------------------------------------------------------------


def test_unique_index_exists_after_migration_024(migrated_db):
    """idx_spec_facets_bundle_facet must exist in sqlite_master after migration 024."""
    index_names = _get_index_names(migrated_db)
    assert "idx_spec_facets_bundle_facet" in index_names, (
        f"UNIQUE index 'idx_spec_facets_bundle_facet' not found in spec_facets indexes. "
        f"Present indexes: {sorted(index_names)}. "
        "Migration 024 must create this index."
    )


# ---------------------------------------------------------------------------
# T5 — Plain INSERT of duplicate raises IntegrityError (schema enforcement)
# ---------------------------------------------------------------------------


def test_direct_duplicate_insert_raises_integrity_error(migrated_db):
    """After migration 024, a raw INSERT with a duplicate (bundle_hash, facet_name)
    pair must raise sqlite3.IntegrityError — the schema constraint is enforced.
    """
    canonical_json = db_module.canonicalize_facets_json({"test": "integrity"})
    bundle_hash = db_module.hash_facets_json(canonical_json)
    db_module.insert_spec_bundle(bundle_hash=bundle_hash, facets_json=canonical_json)

    conn = sqlite3.connect(migrated_db)
    try:
        conn.execute(
            "INSERT INTO spec_facets (bundle_hash, facet_name, facet_value, freeze_discipline) "
            "VALUES (?, 'dup_facet', '\"v1\"', 'THEORY')",
            (bundle_hash,),
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO spec_facets (bundle_hash, facet_name, facet_value, freeze_discipline) "
                "VALUES (?, 'dup_facet', '\"v2\"', 'THEORY')",
                (bundle_hash,),
            )
            conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# T6 — insert_spec_bundle_facet INSERT OR IGNORE is a no-op on duplicate
# ---------------------------------------------------------------------------


def test_insert_spec_bundle_facet_insert_or_ignore_is_noop_on_duplicate(migrated_db):
    """insert_spec_bundle_facet must not raise and must not add a row when the
    (bundle_hash, facet_name) pair already exists (INSERT OR IGNORE behaviour).

    Returns the existing row's id (not 0 or None).
    """
    canonical_json = db_module.canonicalize_facets_json({"test": "noop"})
    bundle_hash = db_module.hash_facets_json(canonical_json)
    db_module.insert_spec_bundle(bundle_hash=bundle_hash, facets_json=canonical_json)

    id_first = db_module.insert_spec_bundle_facet(
        bundle_hash=bundle_hash,
        facet_name="noop_facet",
        facet_value='"original"',
        freeze_discipline="THEORY",
    )
    assert isinstance(id_first, int) and id_first > 0, (
        f"First insert must return a positive int id; got {id_first!r}"
    )

    # Second insert of the same (bundle_hash, facet_name) — must be a no-op.
    id_second = db_module.insert_spec_bundle_facet(
        bundle_hash=bundle_hash,
        facet_name="noop_facet",
        facet_value='"should_be_ignored"',
        freeze_discipline="THEORY",
    )
    assert isinstance(id_second, int) and id_second > 0, (
        f"Duplicate insert must return a positive int id; got {id_second!r}"
    )
    assert id_second == id_first, (
        f"Duplicate insert returned id={id_second}; expected the existing row id={id_first}. "
        "INSERT OR IGNORE must return the existing row's id, not a new id."
    )

    # Only 1 row must exist.
    assert _count_facets(migrated_db, bundle_hash, "noop_facet") == 1, (
        "Duplicate insert_spec_bundle_facet call must not add a second row."
    )

    # Original value must be preserved (INSERT OR IGNORE does not overwrite).
    facets = db_module.get_spec_facets_for_bundle(bundle_hash)
    noop = next(f for f in facets if f["facet_name"] == "noop_facet")
    assert noop["facet_value"] == '"original"', (
        f"INSERT OR IGNORE must not overwrite the existing value; "
        f"got {noop['facet_value']!r}, expected '\"original\"'."
    )


# ---------------------------------------------------------------------------
# T7 — Migration is idempotent
# ---------------------------------------------------------------------------


def test_migration_024_is_idempotent(migrated_db):
    """run_migrations() called a second time must not raise.

    The schema_migrations tracker prevents re-applying 024, and
    CREATE UNIQUE INDEX IF NOT EXISTS is a no-op when the index already exists.
    """
    try:
        run_migrations()
    except Exception as exc:
        pytest.fail(
            f"Second call to run_migrations() raised {type(exc).__name__}: {exc}. "
            "Migration 024 must be idempotent."
        )

    # Index must still be present.
    index_names = _get_index_names(migrated_db)
    assert "idx_spec_facets_bundle_facet" in index_names, (
        "UNIQUE index was lost after second run_migrations() call."
    )


# ---------------------------------------------------------------------------
# T8 — Migration SQL is additive-only (no DROP TABLE or RENAME)
# ---------------------------------------------------------------------------


def test_migration_024_sql_contains_no_destructive_statements():
    """024_spec_facets_unique_constraint.sql must not contain DROP TABLE or
    RENAME TABLE statements — additive-first mandate.
    """
    sql = _MIGRATION_024_PATH.read_text(encoding="utf-8").upper()

    assert "DROP TABLE" not in sql, (
        "024_spec_facets_unique_constraint.sql contains 'DROP TABLE'. "
        "Project mandate: schema migrations must be additive-first — never destructive. "
        "Use CREATE UNIQUE INDEX instead of table rebuild."
    )
    assert "RENAME TO" not in sql, (
        "024_spec_facets_unique_constraint.sql contains 'RENAME TO'. "
        "Table renames are part of the destructive rebuild pattern — forbidden."
    )
    assert "CREATE TABLE" not in sql, (
        "024_spec_facets_unique_constraint.sql contains 'CREATE TABLE'. "
        "Creating a shadow table is the destructive rebuild pattern — forbidden. "
        "Use CREATE UNIQUE INDEX to add the constraint additively."
    )
