"""
BUG-001 — shadow_history composite index orphaned from _MIGRATION_FILES.

Root cause: migrations/016_shadow_history_sym_ts_index.sql existed on disk but
was never registered in database._MIGRATION_FILES (which already consumed the 016
slot for spec_bundles).  As a result, idx_shadow_history_sym_ts was never created
on deployed databases, causing 245x-9700x warm-cache regressions on the
shadow-trajectory query (ORDER BY ts_utc DESC LIMIT 1).

Fix: rename the orphan 016 → 031 and register it in _MIGRATION_FILES immediately
after "030_per_symphony_live_mode.sql".

These tests are RED until the fix lands.  They assert observable runtime behavior
(index present after run_migrations, column ordering, registration position) — not
just file existence — so a wrong implementation can still fail them.

Test inventory:
  I1 — After run_migrations() on a fresh temp DB, idx_shadow_history_sym_ts
       EXISTS in sqlite_master (type='index', tbl_name='shadow_history').
  I2 — The index covers columns (symphony_id, ts_utc) IN THAT ORDER via
       PRAGMA index_info / index_xinfo.
  R1 — "031_shadow_history_sym_ts_index.sql" is present in database._MIGRATION_FILES
       AND positioned AFTER "030_per_symphony_live_mode.sql" (relative-neighbor
       ordering; NOT asserted to be the last element — a future 032 would break that).
  R2 — Guard: "016_shadow_history_sym_ts_index.sql" is NOT in database._MIGRATION_FILES
       (no leftover duplicate registration of the original orphan filename).

Fixture provenance: all assertions derive from sqlite_master and PRAGMA introspection
at runtime, not from hardcoded producer-computed values.
"""

from __future__ import annotations

import sqlite3

import pytest

import database as db_module
from database import init_db, run_migrations

_EXPECTED_INDEX_NAME = "idx_shadow_history_sym_ts"
_EXPECTED_TABLE = "shadow_history"
_NEW_MIGRATION_NAME = "031_shadow_history_sym_ts_index.sql"
_OLD_ORPHAN_NAME = "016_shadow_history_sym_ts_index.sql"
_PREDECESSOR_NAME = "030_per_symphony_live_mode.sql"


# ---------------------------------------------------------------------------
# DB isolation fixture — per-test fresh DB with the full migration stack
# ---------------------------------------------------------------------------


@pytest.fixture()
def migrated_db(tmp_path, monkeypatch):
    """Per-test isolated SQLite DB with the full migration stack applied.

    Uses monkeypatch.setattr (not setenv) so _db_file() picks up the override
    in the DB_FILE != _DB_FILE_DEFAULT branch (database.py:57-58) — the same
    pattern used by tests/database/test_030_per_symphony_live_mode.py.
    """
    db_path = str(tmp_path / "test_bug001.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path


# ---------------------------------------------------------------------------
# I1 — idx_shadow_history_sym_ts exists in sqlite_master after run_migrations
# ---------------------------------------------------------------------------


def test_run_migrations_creates_shadow_history_composite_index(migrated_db):
    """I1: run_migrations() must create idx_shadow_history_sym_ts on shadow_history.

    This is the primary correctness check.  Prior to the BUG-001 fix, the index
    migration file existed on disk but was never registered in _MIGRATION_FILES,
    so the index was never created on deployed databases.  A wrong implementation
    (e.g. file renamed but not registered) will still fail this test.
    """
    conn = sqlite3.connect(migrated_db)
    try:
        row = conn.execute(
            "SELECT name, tbl_name FROM sqlite_master WHERE type='index' AND name=?",
            (_EXPECTED_INDEX_NAME,),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, (
        f"BUG-001 VIOLATED: index {_EXPECTED_INDEX_NAME!r} not found in "
        "sqlite_master after run_migrations().  "
        "Fix: rename migrations/016_shadow_history_sym_ts_index.sql → "
        "migrations/031_shadow_history_sym_ts_index.sql and register "
        "'031_shadow_history_sym_ts_index.sql' in database._MIGRATION_FILES "
        "after '030_per_symphony_live_mode.sql'."
    )

    _, tbl_name = row
    assert tbl_name == _EXPECTED_TABLE, (
        f"Index {_EXPECTED_INDEX_NAME!r} was found but is on table "
        f"{tbl_name!r}; expected {_EXPECTED_TABLE!r}.  "
        "The migration must create the index on shadow_history."
    )


# ---------------------------------------------------------------------------
# I2 — column ordering: (symphony_id, ts_utc) in that exact sequence
# ---------------------------------------------------------------------------


def test_shadow_history_composite_index_covers_symphony_id_then_ts_utc(migrated_db):
    """I2: The composite index must cover (symphony_id, ts_utc) IN THAT ORDER.

    Column ordering determines whether the index can serve a query.  The
    shadow-trajectory query filters on symphony_id and sorts on ts_utc DESC — the
    index is only useful if symphony_id is the leading key.  An index built as
    (ts_utc, symphony_id) would be useless for this workload.

    PRAGMA index_info returns rows (seqno, cid, name); seqno=0 is the leading column.
    Tolerance: exact column-name string comparison — 'symphony_id' and 'ts_utc' are
    canonical names defined by migrations/008_shadow_history.sql and must not drift.
    """
    conn = sqlite3.connect(migrated_db)
    try:
        # Confirm the index exists before checking column order (avoids a confusing
        # "empty list" failure that would be less diagnostic than the I1 failure).
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
            (_EXPECTED_INDEX_NAME,),
        ).fetchone()
        if exists is None:
            pytest.skip(
                f"Index {_EXPECTED_INDEX_NAME!r} absent — I1 already covers this; "
                "skip I2 to avoid duplicate noise."
            )

        info_rows = conn.execute(f"PRAGMA index_info('{_EXPECTED_INDEX_NAME}');").fetchall()
    finally:
        conn.close()

    # PRAGMA index_info row layout: (seqno INTEGER, cid INTEGER, name TEXT)
    column_order = [row[2] for row in info_rows]
    assert column_order == ["symphony_id", "ts_utc"], (
        f"BUG-001 / column-order VIOLATED: index {_EXPECTED_INDEX_NAME!r} covers "
        f"columns {column_order!r}; expected ['symphony_id', 'ts_utc'] in that exact "
        "order.  The leading column must be symphony_id so the index serves the "
        "per-symphony ORDER BY ts_utc DESC LIMIT 1 query.  "
        "Check the CREATE INDEX statement in the migration file — column order matters."
    )


# ---------------------------------------------------------------------------
# R1 — "031_shadow_history_sym_ts_index.sql" registered AFTER 030 in _MIGRATION_FILES
# ---------------------------------------------------------------------------


def test_031_migration_registered_in_migration_files_after_030():
    """R1: '031_shadow_history_sym_ts_index.sql' must be in database._MIGRATION_FILES
    and must appear AFTER '030_per_symphony_live_mode.sql'.

    Asserts relative ordering against the 030 neighbor — does NOT assert
    that 031 is the last element, because a future migration 032 would
    invalidate that assertion (known brittle-pin trap: project memory
    feedback_no_is_last_migration_pins).
    """
    assert _NEW_MIGRATION_NAME in db_module._MIGRATION_FILES, (
        f"'{_NEW_MIGRATION_NAME}' not found in database._MIGRATION_FILES.  "
        "Fix: append '031_shadow_history_sym_ts_index.sql' to _MIGRATION_FILES "
        "after '030_per_symphony_live_mode.sql' in database.py."
    )

    assert _PREDECESSOR_NAME in db_module._MIGRATION_FILES, (
        f"'{_PREDECESSOR_NAME}' must be present in _MIGRATION_FILES as the "
        "predecessor entry for the relative-ordering assertion."
    )

    idx_030 = db_module._MIGRATION_FILES.index(_PREDECESSOR_NAME)
    idx_031 = db_module._MIGRATION_FILES.index(_NEW_MIGRATION_NAME)

    assert idx_031 > idx_030, (
        f"'{_NEW_MIGRATION_NAME}' (index {idx_031}) must appear AFTER "
        f"'{_PREDECESSOR_NAME}' (index {idx_030}) in database._MIGRATION_FILES.  "
        "Append-chronological convention: new migrations go at the tail after 030."
    )


# ---------------------------------------------------------------------------
# R2 — Guard: old orphan filename "016_shadow_history_sym_ts_index.sql" NOT in list
# ---------------------------------------------------------------------------


def test_old_016_orphan_filename_absent_from_migration_files():
    """R2: '016_shadow_history_sym_ts_index.sql' must NOT appear in _MIGRATION_FILES.

    If the implementer both renames the file AND registers a new entry without
    removing any stale 016 reference, the idempotency tracker would try to apply
    a file that no longer exists under that name, raising FileNotFoundError at
    startup.  Additionally, migration slot 016 is already occupied by
    '016_spec_bundles.sql' — a duplicate slot would be a silent ordering hazard.
    """
    assert _OLD_ORPHAN_NAME not in db_module._MIGRATION_FILES, (
        f"'{_OLD_ORPHAN_NAME}' found in database._MIGRATION_FILES.  "
        "The fix renames the file to 031_shadow_history_sym_ts_index.sql; "
        "there must be NO registration of the old '016_shadow_history_sym_ts_index.sql' "
        "name.  Remove it if it was inadvertently left in the list."
    )
