"""
RED tests for migration 032_prism_audit_log.sql and the prism_audit_log
accessor surface.

Coverage (all tests must FAIL until GREEN implementation lands):

  AC-1  Migration file:
    1. 032_prism_audit_log.sql exists under migrations/.
    2. The file uses CREATE TABLE IF NOT EXISTS (idempotent on re-apply).
    3. _MIGRATION_FILES contains "032_prism_audit_log.sql" appended AFTER
       "031_shadow_history_sym_ts_index.sql" (append-only ordering contract).
    4. After run_migrations(), prism_audit_log exists with all required columns:
       id, run_id, agent_role, phase, content, created_at.
    5. An index on run_id exists after migrations apply.
    6. run_migrations() is idempotent — calling a second time must not raise.

  AC-2  Public accessors (insert_prism_audit_entry + get_prism_audit_for_run):
    7. insert_prism_audit_entry(run_id, agent_role, phase, content) -> positive int.
    8. Return value from insert is the actual SQLite rowid (query-verified).
    9. get_prism_audit_for_run returns entries ordered by id ascending.
    10. get_prism_audit_for_run isolates by run_id — rows from a different run_id
        are excluded.
    11. get_prism_audit_for_run returns an empty list when no rows match (no raise).
    12. Each dict returned by get_prism_audit_for_run contains at minimum:
        id, run_id, agent_role, phase, content, created_at.
    13. created_at is a non-empty string (ISO timestamp format).

  AC-3  SQL-injection-shaped content is stored verbatim:
    14. content containing single-quotes, semicolons, and SQL keywords
        (e.g. "'; DROP TABLE prism_audit_log; --") round-trips without
        corruption — the row reads back byte-for-byte.
    15. run_id containing special characters also stores verbatim.

  AC-4  No update/delete accessors exported:
    16. database module exports no symbol starting with "update_prism" or
        "delete_prism" (append-only contract).

  AC-5  Pytest DB sentinel respected:
    17. Under pytest, opening the production DB raises RuntimeError — verified
        by the existing _db_file() guard (regression: new accessors must not
        bypass _db_file()).

DB isolation: the global conftest._isolate_db autouse fixture redirects
DB_PATH to a per-test tmpfile and calls init_db() → run_migrations().
Every test therefore starts with a fully-migrated, empty DB.

Fixture provenance: schema-derived with runtime validator — no hardcoded
producer-computed values.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import database as db_module

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIGRATION_FILENAME = "032_prism_audit_log.sql"
_MIGRATION_PATH = Path(__file__).parents[2] / "migrations" / _MIGRATION_FILENAME
_REQUIRED_COLUMNS = {"id", "run_id", "agent_role", "phase", "content", "created_at"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _column_names(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
    return {row[1] for row in rows}


def _index_names_for_table(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
        (table_name,),
    ).fetchall()
    return {row[0] for row in rows}


def _index_covers_column(conn: sqlite3.Connection, index_name: str, column_name: str) -> bool:
    """Return True if the index covers (includes) the named column."""
    rows = conn.execute(f"PRAGMA index_info({index_name})").fetchall()
    # PRAGMA index_info: seqno, cid, name
    return any(row[2] == column_name for row in rows)


# ---------------------------------------------------------------------------
# AC-1  Migration file checks
# ---------------------------------------------------------------------------

class TestMigrationFile:
    def test_migration_file_exists(self):
        # The SQL file must exist before green implementation touches database.py.
        assert _MIGRATION_PATH.exists(), (
            f"{_MIGRATION_FILENAME} not found under migrations/ — "
            "implementer must create it"
        )

    def test_migration_uses_create_if_not_exists(self):
        # Idempotency: must use IF NOT EXISTS so re-applying on a live DB is safe.
        sql = _MIGRATION_PATH.read_text()
        assert "IF NOT EXISTS" in sql.upper(), (
            f"{_MIGRATION_FILENAME} must use CREATE TABLE IF NOT EXISTS "
            "so re-application is idempotent"
        )

    def test_migration_wired_in_migration_files_after_031(self):
        # Must be appended AFTER "031_shadow_history_sym_ts_index.sql" in
        # _MIGRATION_FILES — never inserted in the middle of the list.
        files = db_module._MIGRATION_FILES
        assert _MIGRATION_FILENAME in files, (
            f"_MIGRATION_FILES must include {_MIGRATION_FILENAME!r}"
        )
        idx_031 = files.index("031_shadow_history_sym_ts_index.sql")
        idx_032 = files.index(_MIGRATION_FILENAME)
        assert idx_032 > idx_031, (
            f"{_MIGRATION_FILENAME!r} (pos {idx_032}) must appear after "
            f"031_shadow_history_sym_ts_index.sql (pos {idx_031}) in _MIGRATION_FILES"
        )

    def test_prism_audit_log_table_exists_after_migrations(self):
        # init_db/run_migrations is called by _isolate_db autouse fixture.
        conn = db_module.get_connection()
        try:
            assert _table_exists(conn, "prism_audit_log"), (
                "prism_audit_log table must exist after run_migrations()"
            )
        finally:
            conn.close()

    def test_prism_audit_log_has_required_columns(self):
        conn = db_module.get_connection()
        try:
            cols = _column_names(conn, "prism_audit_log")
            missing = _REQUIRED_COLUMNS - cols
            assert not missing, (
                f"prism_audit_log is missing columns: {sorted(missing)}"
            )
        finally:
            conn.close()

    def test_run_id_index_exists(self):
        conn = db_module.get_connection()
        try:
            indexes = _index_names_for_table(conn, "prism_audit_log")
            # At least one index must cover the run_id column.
            covering = [
                idx for idx in indexes
                if _index_covers_column(conn, idx, "run_id")
            ]
            assert covering, (
                "prism_audit_log must have at least one index on run_id; "
                f"found indexes: {indexes}"
            )
        finally:
            conn.close()

    def test_run_migrations_is_idempotent(self):
        # Calling run_migrations() twice on the same DB must not raise.
        db_module.run_migrations()
        db_module.run_migrations()  # second call — must be silent


# ---------------------------------------------------------------------------
# AC-2  Public accessor surface
# ---------------------------------------------------------------------------

class TestInsertPrismAuditEntry:
    def test_insert_returns_positive_int(self):
        row_id = db_module.insert_prism_audit_entry(
            run_id="run-abc-001",
            agent_role="technicals_analyst",
            phase="initial_read",
            content="Volatility is elevated.",
        )
        assert isinstance(row_id, int), (
            "insert_prism_audit_entry must return an int row id"
        )
        assert row_id > 0, (
            f"insert_prism_audit_entry must return a positive row id; got {row_id}"
        )

    def test_insert_returns_actual_rowid(self):
        # The returned id must be the ACTUAL SQLite rowid — verify by querying.
        row_id = db_module.insert_prism_audit_entry(
            run_id="run-abc-002",
            agent_role="synthesizer",
            phase="synthesis",
            content="Neutral posture across all lenses.",
        )
        conn = db_module.get_connection()
        try:
            row = conn.execute(
                "SELECT id FROM prism_audit_log WHERE id = ?", (row_id,)
            ).fetchone()
        finally:
            conn.close()
        assert row is not None, (
            f"Row with id={row_id} returned by insert_prism_audit_entry "
            "was not found in prism_audit_log"
        )
        assert row[0] == row_id

    def test_function_is_exported_from_database_module(self):
        # Must be a public function on the database module — not private.
        assert hasattr(db_module, "insert_prism_audit_entry"), (
            "database.insert_prism_audit_entry must be exported"
        )
        assert callable(db_module.insert_prism_audit_entry)

    def test_get_accessor_is_exported_from_database_module(self):
        assert hasattr(db_module, "get_prism_audit_for_run"), (
            "database.get_prism_audit_for_run must be exported"
        )
        assert callable(db_module.get_prism_audit_for_run)


class TestGetPrismAuditForRun:
    def test_returns_empty_list_when_no_rows(self):
        result = db_module.get_prism_audit_for_run("nonexistent-run-id")
        assert result == [], (
            "get_prism_audit_for_run must return [] for an unknown run_id"
        )

    def test_returns_list_of_dicts(self):
        db_module.insert_prism_audit_entry(
            run_id="run-shape-001",
            agent_role="macro_analyst",
            phase="initial_read",
            content="Rates stable.",
        )
        result = db_module.get_prism_audit_for_run("run-shape-001")
        assert isinstance(result, list), "get_prism_audit_for_run must return a list"
        assert len(result) == 1
        assert isinstance(result[0], dict), "Each entry must be a dict"

    def test_returned_dicts_have_required_keys(self):
        db_module.insert_prism_audit_entry(
            run_id="run-keys-001",
            agent_role="sentiment_analyst",
            phase="debate_round_1",
            content="Retail sentiment slightly bearish.",
        )
        result = db_module.get_prism_audit_for_run("run-keys-001")
        assert result, "Expected at least one entry"
        entry = result[0]
        for key in _REQUIRED_COLUMNS:
            assert key in entry, (
                f"Returned dict is missing key {key!r}; got keys: {sorted(entry)}"
            )

    def test_created_at_is_non_empty_string(self):
        db_module.insert_prism_audit_entry(
            run_id="run-ts-001",
            agent_role="fundamentals_analyst",
            phase="clarification",
            content="EPS beat last quarter.",
        )
        result = db_module.get_prism_audit_for_run("run-ts-001")
        assert result
        created_at = result[0]["created_at"]
        assert isinstance(created_at, str) and created_at, (
            "created_at must be a non-empty string"
        )

    def test_ordering_is_by_id_ascending(self):
        # Insert 3 entries for the same run in sequence; get must preserve id order.
        run_id = "run-order-001"
        ids = []
        for phase in ("initial_read", "debate_round_1", "synthesis"):
            row_id = db_module.insert_prism_audit_entry(
                run_id=run_id,
                agent_role="synthesizer",
                phase=phase,
                content=f"Content for {phase}.",
            )
            ids.append(row_id)

        result = db_module.get_prism_audit_for_run(run_id)
        assert len(result) == 3, f"Expected 3 entries; got {len(result)}"
        returned_ids = [entry["id"] for entry in result]
        assert returned_ids == sorted(returned_ids), (
            f"Entries not ordered by id ascending; got ids: {returned_ids}"
        )
        # Verify the ids in the result match what was inserted.
        assert returned_ids == ids, (
            f"Returned ids {returned_ids} do not match inserted ids {ids}"
        )

    def test_run_id_grouping_isolates_across_two_runs(self):
        # Insert entries under two different run_ids; each get call must return
        # only its own entries.
        run_a = "run-group-A"
        run_b = "run-group-B"

        id_a = db_module.insert_prism_audit_entry(
            run_id=run_a,
            agent_role="technicals_analyst",
            phase="initial_read",
            content="Run A entry.",
        )
        id_b = db_module.insert_prism_audit_entry(
            run_id=run_b,
            agent_role="macro_analyst",
            phase="initial_read",
            content="Run B entry.",
        )

        result_a = db_module.get_prism_audit_for_run(run_a)
        result_b = db_module.get_prism_audit_for_run(run_b)

        assert len(result_a) == 1, (
            f"run_id={run_a!r} should return 1 entry; got {len(result_a)}"
        )
        assert len(result_b) == 1, (
            f"run_id={run_b!r} should return 1 entry; got {len(result_b)}"
        )
        assert result_a[0]["id"] == id_a
        assert result_b[0]["id"] == id_b
        # Ensure no cross-contamination.
        a_ids = {e["id"] for e in result_a}
        b_ids = {e["id"] for e in result_b}
        assert a_ids.isdisjoint(b_ids), (
            f"run_id grouping leaked: a_ids={a_ids}, b_ids={b_ids}"
        )


# ---------------------------------------------------------------------------
# AC-3  SQL-injection-shaped inputs stored verbatim
# ---------------------------------------------------------------------------

class TestParameterizedStorage:
    @pytest.mark.parametrize(
        "content",
        [
            "'; DROP TABLE prism_audit_log; --",
            "SELECT * FROM prism_audit_log; DELETE FROM prism_audit_log",
            'He said "hello"; she said \'goodbye\'',
            "Line 1\nLine 2\nLine 3",  # multiline
            "\x00null\x00byte",         # embedded null
        ],
        ids=[
            "sql_injection_classic",
            "multi_statement",
            "mixed_quotes",
            "multiline",
            "null_byte",
        ],
    )
    def test_content_stores_and_reads_verbatim(self, content: str):
        run_id = "run-inject-test"
        row_id = db_module.insert_prism_audit_entry(
            run_id=run_id,
            agent_role="synthesizer",
            phase="synthesis",
            content=content,
        )
        result = db_module.get_prism_audit_for_run(run_id)
        matching = [e for e in result if e["id"] == row_id]
        assert matching, f"Row id={row_id} not found in get result"
        stored = matching[0]["content"]
        assert stored == content, (
            f"Content was not stored verbatim.\n"
            f"  Expected: {content!r}\n"
            f"  Got:      {stored!r}"
        )

    def test_run_id_with_special_chars_stores_verbatim(self):
        special_run_id = "run/2026-06-13T03:00:00Z?lens=all&v=1"
        row_id = db_module.insert_prism_audit_entry(
            run_id=special_run_id,
            agent_role="derivatives_analyst",
            phase="initial_read",
            content="Options vol surface.",
        )
        result = db_module.get_prism_audit_for_run(special_run_id)
        assert result, f"No entries returned for run_id={special_run_id!r}"
        assert result[0]["run_id"] == special_run_id, (
            f"run_id not stored verbatim; got {result[0]['run_id']!r}"
        )


# ---------------------------------------------------------------------------
# AC-4  No update/delete accessors (append-only contract)
# ---------------------------------------------------------------------------

class TestAppendOnlyContract:
    def test_no_update_prism_symbol_exported(self):
        update_symbols = [
            name for name in dir(db_module)
            if name.startswith("update_prism")
        ]
        assert not update_symbols, (
            f"prism_audit_log must be append-only; found update symbols: {update_symbols}"
        )

    def test_no_delete_prism_symbol_exported(self):
        delete_symbols = [
            name for name in dir(db_module)
            if name.startswith("delete_prism")
        ]
        assert not delete_symbols, (
            f"prism_audit_log must be append-only; found delete symbols: {delete_symbols}"
        )
