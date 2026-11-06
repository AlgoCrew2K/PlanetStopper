"""Migration 041: the dashboard session revocation store gains the logout count.

Contract pinned:

* ``migrations/041_dashboard_session_generation.sql`` is additive and idempotent: every statement
  adds to the existing ``dashboard_session_revocation`` store (an ``ALTER TABLE ... ADD COLUMN``, or
  a ``CREATE TABLE IF NOT EXISTS``), and nothing is dropped, renamed, deleted, updated or inserted. It
  touches the state database only. It is applied by ``run_migrations`` (``init_db`` runs it too) and
  is listed in ``database._MIGRATION_FILES`` directly after ``040_dashboard_session_revocation.sql``,
  by position relative to that neighbour and never as the last entry.
* The two accessors migration 040 introduced keep their contract on a fresh database and on a
  database that stopped at 040: the stored floor survives the upgrade, a recorded instant reads back
  without loss, and an earlier instant never moves the floor back.
* A second ``run_migrations`` changes nothing.

Fixtures: none. Databases are made by the real ``init_db`` / ``run_migrations``; the 040 database is
made by running the committed 040 file; every expected value is derived from what the test wrote.
"""

from __future__ import annotations

import pathlib
import re
import sqlite3

import pytest

import database as db_module
from database import init_db, run_migrations

_NAME = "041_dashboard_session_generation.sql"
_PREVIOUS = "040_dashboard_session_revocation.sql"
_TABLE = "dashboard_session_revocation"
_MIGRATIONS = pathlib.Path(__file__).parents[2] / "migrations"
_MIGRATION_PATH = _MIGRATIONS / _NAME
_REAL_INSTANT_NS = 1_793_800_000_123_456_789


@pytest.fixture()
def migrated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_041.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    return db_path


@pytest.fixture()
def database_stopped_at_040(tmp_path, monkeypatch):
    """A database whose revocation store came from the committed 040 file, holding a floor, with
    every migration but 041 recorded as applied."""
    db_path = str(tmp_path / "stopped_at_040.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  migration_name TEXT PRIMARY KEY,"
        "  applied_at     TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    conn.executescript((_MIGRATIONS / _PREVIOUS).read_text(encoding="utf-8"))
    conn.execute(
        f"INSERT INTO {_TABLE} (id, revoked_before_ns, updated_at) "  # noqa: S608
        "VALUES (1, ?, datetime('now'))",
        (_REAL_INSTANT_NS,),
    )
    for name in db_module._MIGRATION_FILES:
        if name != _NAME:
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (migration_name) VALUES (?)", (name,)
            )
    conn.commit()
    conn.close()
    return db_path


def _applied_count(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE migration_name = ?", (_NAME,)
        ).fetchone()[0]
    finally:
        conn.close()


def _columns(db_path: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({_TABLE})").fetchall()]
    finally:
        conn.close()


def _migration_sql() -> str:
    assert _MIGRATION_PATH.is_file(), f"missing {_MIGRATION_PATH}"
    return _MIGRATION_PATH.read_text(encoding="utf-8")


def _statements(sql: str) -> list[str]:
    code = re.sub(r"--[^\n]*", "", sql)
    return [re.sub(r"\s+", " ", s).strip().upper() for s in code.split(";") if s.strip()]


class TestTheMigrationFile:
    def test_the_file_exists(self):
        assert _MIGRATION_PATH.is_file(), f"missing {_MIGRATION_PATH}"

    def test_it_is_listed_directly_after_040(self):
        files = db_module._MIGRATION_FILES
        assert _NAME in files, f"{_NAME} is not in database._MIGRATION_FILES"
        assert _PREVIOUS in files
        assert files.index(_NAME) == files.index(_PREVIOUS) + 1, (
            "041 must be registered right after 040 (not merely somewhere later)"
        )
        assert files.count(_NAME) == 1

    def test_every_statement_adds_to_the_revocation_store_and_changes_nothing_else(self):
        statements = _statements(_migration_sql())
        assert statements, "the migration holds no statement"
        for statement in statements:
            assert re.match(
                rf"ALTER TABLE {_TABLE.upper()} ADD COLUMN |CREATE TABLE IF NOT EXISTS ",
                statement,
            ), f"not an additive statement on the revocation store: {statement}"
            for forbidden in ("DROP ", "DELETE ", "UPDATE ", "INSERT ", "RENAME "):
                assert forbidden not in statement, f"the migration is not additive: {statement}"

    def test_a_column_it_adds_cannot_leave_an_existing_row_without_a_value(self):
        for statement in _statements(_migration_sql()):
            if "ADD COLUMN" in statement:
                assert "DEFAULT" in statement, (
                    f"a column added to a table that may hold a row needs a default: {statement}"
                )

    def test_it_does_not_reference_the_optimization_database(self):
        sql = _migration_sql()
        assert not re.search(r"alphabot_opt|\.opt\.db", sql, re.IGNORECASE)


class TestAFreshDatabase:
    def test_the_migration_is_recorded_once_and_the_store_still_holds_the_040_columns(
        self, migrated_db
    ):
        assert _applied_count(migrated_db) == 1
        assert {"id", "revoked_before_ns", "updated_at"} <= set(_columns(migrated_db))

    def test_the_floor_accessors_keep_their_contract(self, migrated_db):
        assert _applied_count(migrated_db) == 1, "premise: 041 was applied"
        assert db_module.get_session_revocation_floor_ns() == 0

        assert db_module.record_session_revocation(_REAL_INSTANT_NS) == _REAL_INSTANT_NS
        assert db_module.record_session_revocation(_REAL_INSTANT_NS - 1_000) == _REAL_INSTANT_NS
        assert db_module.record_session_revocation(_REAL_INSTANT_NS + 1_000) == (
            _REAL_INSTANT_NS + 1_000
        )
        assert db_module.get_session_revocation_floor_ns() == _REAL_INSTANT_NS + 1_000

    def test_a_second_run_changes_nothing(self, migrated_db):
        db_module.record_session_revocation(_REAL_INSTANT_NS)
        columns_before = _columns(migrated_db)

        run_migrations()
        run_migrations()

        assert _applied_count(migrated_db) == 1
        assert _columns(migrated_db) == columns_before
        assert db_module.get_session_revocation_floor_ns() == _REAL_INSTANT_NS


class TestADatabaseThatStoppedAt040:
    def test_it_upgrades_through_run_migrations_and_keeps_its_stored_floor(
        self, database_stopped_at_040
    ):
        columns_before = _columns(database_stopped_at_040)
        assert _applied_count(database_stopped_at_040) == 0

        run_migrations()

        assert _applied_count(database_stopped_at_040) == 1
        assert set(columns_before) <= set(_columns(database_stopped_at_040)), (
            "an upgrade must not remove a 040 column"
        )
        assert db_module.get_session_revocation_floor_ns() == _REAL_INSTANT_NS

    def test_the_floor_still_only_moves_forward_after_the_upgrade(self, database_stopped_at_040):
        run_migrations()
        assert _applied_count(database_stopped_at_040) == 1, "premise: 041 was applied"

        assert db_module.record_session_revocation(_REAL_INSTANT_NS - 5) == _REAL_INSTANT_NS
        assert db_module.record_session_revocation(_REAL_INSTANT_NS + 5) == _REAL_INSTANT_NS + 5
        assert db_module.get_session_revocation_floor_ns() == _REAL_INSTANT_NS + 5

    def test_running_the_upgrade_twice_is_a_no_op(self, database_stopped_at_040):
        run_migrations()
        run_migrations()

        assert _applied_count(database_stopped_at_040) == 1
        assert db_module.get_session_revocation_floor_ns() == _REAL_INSTANT_NS


class TestTheLogoutStoreKeepsTheHigherValues:
    """Two logouts can reach the store out of order, because the app writes outside its lock.

    An overwrite by the later arrival would let a logged-out session live again after a restart, so
    each column keeps the higher value on its own. Floors are nanosecond instants and generations are
    small counts, so a swapped return pair can never match by coincidence.
    """

    _FLOOR_NS = _REAL_INSTANT_NS
    _GENERATION = 7

    def _stored(self) -> tuple[int, int]:
        return db_module.get_session_revocation_floor_ns(), db_module.get_session_generation()

    def test_the_first_write_returns_the_floor_then_the_generation(self, migrated_db):
        returned = db_module.record_session_logout(self._FLOOR_NS, self._GENERATION)

        assert returned == (self._FLOOR_NS, self._GENERATION)
        assert self._stored() == (self._FLOOR_NS, self._GENERATION)

    def test_a_later_write_with_a_lower_floor_and_a_lower_generation_changes_neither(
        self, migrated_db
    ):
        db_module.record_session_logout(self._FLOOR_NS, self._GENERATION)

        returned = db_module.record_session_logout(self._FLOOR_NS - 5_000_000, self._GENERATION - 3)

        assert returned == (self._FLOOR_NS, self._GENERATION)
        assert self._stored() == (self._FLOOR_NS, self._GENERATION)

    def test_a_higher_floor_with_a_lower_generation_moves_only_the_floor(self, migrated_db):
        db_module.record_session_logout(self._FLOOR_NS, self._GENERATION)

        returned = db_module.record_session_logout(self._FLOOR_NS + 5_000_000, self._GENERATION - 3)

        assert returned == (self._FLOOR_NS + 5_000_000, self._GENERATION)
        assert self._stored() == (self._FLOOR_NS + 5_000_000, self._GENERATION)

    def test_a_higher_generation_with_a_lower_floor_moves_only_the_generation(self, migrated_db):
        db_module.record_session_logout(self._FLOOR_NS, self._GENERATION)

        returned = db_module.record_session_logout(self._FLOOR_NS - 5_000_000, self._GENERATION + 4)

        assert returned == (self._FLOOR_NS, self._GENERATION + 4)
        assert self._stored() == (self._FLOOR_NS, self._GENERATION + 4)
