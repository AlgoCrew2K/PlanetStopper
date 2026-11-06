"""Migration 040 and the two accessors that store the dashboard session revocation.

Contract pinned:

* ``migrations/040_dashboard_session_revocation.sql`` creates exactly one table,
  ``dashboard_session_revocation(id INTEGER PRIMARY KEY CHECK (id = 1), revoked_before_ns INTEGER
  NOT NULL DEFAULT 0, updated_at TEXT)``: additive, idempotent, state database only, applied by
  ``run_migrations`` (``init_db`` runs it too) and listed in ``database._MIGRATION_FILES`` directly
  after ``039_close_account_totals.sql``.
* ``record_session_revocation(floor_ns)`` stores ``MAX(stored, floor_ns)`` in the one row and returns
  the stored value: two logouts can never move the revocation instant back. It raises on failure.
* ``get_session_revocation_floor_ns()`` returns the stored instant, 0 when no row exists, and RAISES on
  any other failure (a swallowed read failure would let revoked sessions through after a restart).

Fixtures: none. Rows are written by the real accessor into a temporary database made by the real
``init_db`` / ``run_migrations``; every expected value is derived from what the test wrote.
"""

from __future__ import annotations

import pathlib
import re
import sqlite3

import pytest

import database as db_module
from database import init_db, run_migrations

_NAME = "040_dashboard_session_revocation.sql"
_TABLE = "dashboard_session_revocation"
_MIGRATION_PATH = pathlib.Path(__file__).parents[2] / "migrations" / _NAME
_NEIGHBOUR = "039_close_account_totals.sql"
# A real nanosecond wall-clock instant is 19 digits: the column must hold one without loss.
_REAL_INSTANT_NS = 1_793_800_000_123_456_789


@pytest.fixture()
def migrated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_040.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    return db_path


def _table_exists(db_path: str, name: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        return (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()
            is not None
        )
    finally:
        conn.close()


def _rows(db_path: str) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            f"SELECT id, revoked_before_ns, updated_at FROM {_TABLE}"  # noqa: S608
        ).fetchall()
    finally:
        conn.close()


class TestTheMigrationFile:
    def test_the_file_exists(self):
        assert _MIGRATION_PATH.is_file(), f"missing {_MIGRATION_PATH}"

    def test_it_is_listed_directly_after_039(self):
        files = db_module._MIGRATION_FILES
        assert _NAME in files, f"{_NAME} is not in database._MIGRATION_FILES"
        assert _NEIGHBOUR in files
        assert files.index(_NAME) == files.index(_NEIGHBOUR) + 1, (
            "040 must be registered right after 039 (not merely somewhere later)"
        )
        assert files.count(_NAME) == 1

    def test_it_creates_exactly_one_table_and_changes_nothing_else(self):
        sql = _MIGRATION_PATH.read_text(encoding="utf-8")
        code = re.sub(r"--[^\n]*", "", sql)
        statements = [s.strip() for s in code.split(";") if s.strip()]
        assert len(statements) == 1, f"expected one statement, found {len(statements)}"
        statement = re.sub(r"\s+", " ", statements[0]).upper()
        assert statement.startswith(f"CREATE TABLE IF NOT EXISTS {_TABLE.upper()}"), statement
        for forbidden in ("DROP ", "ALTER ", "DELETE ", "UPDATE ", "INSERT "):
            assert forbidden not in statement, f"the migration is not additive: {forbidden!r}"

    def test_it_does_not_reference_the_optimization_database(self):
        sql = _MIGRATION_PATH.read_text(encoding="utf-8")
        assert not re.search(r"alphabot_opt|\.opt\.db", sql, re.IGNORECASE)


class TestTheTable:
    def test_a_fresh_database_has_the_table_with_the_agreed_columns(self, migrated_db):
        assert _table_exists(migrated_db, _TABLE)
        conn = sqlite3.connect(migrated_db)
        try:
            columns = {
                row[1]: row for row in conn.execute(f"PRAGMA table_info({_TABLE})").fetchall()
            }
        finally:
            conn.close()
        # Migration 041 adds a column to this table, so the 040 columns are a subset.
        assert set(columns) >= {"id", "revoked_before_ns", "updated_at"}
        assert columns["id"][2].upper() == "INTEGER" and columns["id"][5] == 1
        assert columns["revoked_before_ns"][2].upper() == "INTEGER"
        assert columns["revoked_before_ns"][3] == 1, "revoked_before_ns must be NOT NULL"
        assert str(columns["revoked_before_ns"][4]) == "0", "revoked_before_ns must default to 0"
        assert columns["updated_at"][2].upper() == "TEXT" and columns["updated_at"][3] == 0

    def test_a_second_row_is_refused_by_the_table_itself(self, migrated_db):
        conn = sqlite3.connect(migrated_db)
        try:
            conn.execute(f"INSERT INTO {_TABLE} (id, revoked_before_ns) VALUES (1, 5)")  # noqa: S608
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(f"INSERT INTO {_TABLE} (id, revoked_before_ns) VALUES (2, 6)")  # noqa: S608
        finally:
            conn.close()

    def test_an_existing_database_gains_it_through_run_migrations(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "upgrade_040.db")
        monkeypatch.setattr(db_module, "DB_FILE", db_path)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "  migration_name TEXT PRIMARY KEY,"
            "  applied_at     TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        for name in db_module._MIGRATION_FILES:
            if name != _NAME:
                conn.execute(
                    "INSERT OR IGNORE INTO schema_migrations (migration_name) VALUES (?)", (name,)
                )
        conn.commit()
        conn.close()
        assert not _table_exists(db_path, _TABLE)

        run_migrations()

        assert _table_exists(db_path, _TABLE)

    def test_running_the_migrations_again_is_a_no_op_that_keeps_the_stored_instant(
        self, migrated_db
    ):
        db_module.record_session_revocation(_REAL_INSTANT_NS)

        run_migrations()
        run_migrations()

        conn = sqlite3.connect(migrated_db)
        try:
            applied = conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE migration_name = ?", (_NAME,)
            ).fetchone()[0]
        finally:
            conn.close()
        assert applied == 1
        assert db_module.get_session_revocation_floor_ns() == _REAL_INSTANT_NS


class TestTheAccessors:
    def test_the_floor_is_zero_before_any_logout(self, migrated_db):
        floor = db_module.get_session_revocation_floor_ns()
        assert floor == 0 and isinstance(floor, int)
        assert _rows(migrated_db) == [], "reading must not create the row"

    def test_a_recorded_instant_reads_back_without_loss(self, migrated_db):
        stored = db_module.record_session_revocation(_REAL_INSTANT_NS)

        assert stored == _REAL_INSTANT_NS
        assert db_module.get_session_revocation_floor_ns() == _REAL_INSTANT_NS
        (row,) = _rows(migrated_db)
        assert row[0] == 1 and row[1] == _REAL_INSTANT_NS and row[2], "updated_at must be set"

    def test_a_later_instant_moves_the_floor_forward(self, migrated_db):
        db_module.record_session_revocation(_REAL_INSTANT_NS)

        stored = db_module.record_session_revocation(_REAL_INSTANT_NS + 1_000)

        assert stored == _REAL_INSTANT_NS + 1_000
        assert db_module.get_session_revocation_floor_ns() == _REAL_INSTANT_NS + 1_000

    def test_an_earlier_instant_never_moves_the_floor_back(self, migrated_db):
        db_module.record_session_revocation(_REAL_INSTANT_NS)

        stored = db_module.record_session_revocation(_REAL_INSTANT_NS - 1_000)

        assert stored == _REAL_INSTANT_NS, "the call returns the stored value, not its argument"
        assert db_module.get_session_revocation_floor_ns() == _REAL_INSTANT_NS

    def test_an_equal_instant_is_accepted(self, migrated_db):
        db_module.record_session_revocation(_REAL_INSTANT_NS)
        assert db_module.record_session_revocation(_REAL_INSTANT_NS) == _REAL_INSTANT_NS

    def test_the_table_only_ever_holds_one_row(self, migrated_db):
        for step in range(5):
            db_module.record_session_revocation(_REAL_INSTANT_NS + step)
        assert len(_rows(migrated_db)) == 1


class TestFailuresAreRaisedNotSwallowed:
    @pytest.fixture()
    def no_table(self, tmp_path, monkeypatch):
        """A database that exists but has no revocation table (a failed or missing migration)."""
        db_path = str(tmp_path / "no_table_040.db")
        monkeypatch.setattr(db_module, "DB_FILE", db_path)
        sqlite3.connect(db_path).close()
        return db_path

    def test_reading_without_the_table_raises_and_does_not_answer_zero(self, no_table):
        with pytest.raises(sqlite3.Error):
            db_module.get_session_revocation_floor_ns()

    def test_recording_without_the_table_raises(self, no_table):
        with pytest.raises(sqlite3.Error):
            db_module.record_session_revocation(_REAL_INSTANT_NS)

    def test_reading_through_a_connection_that_fails_raises(self, migrated_db, monkeypatch):
        def _broken():
            raise sqlite3.OperationalError("unable to open database file")

        monkeypatch.setattr(db_module, "get_ro_connection", _broken)
        with pytest.raises(sqlite3.OperationalError):
            db_module.get_session_revocation_floor_ns()
