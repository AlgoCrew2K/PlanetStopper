"""
RED tests for R7 — MED-1 (WAL) + MED-2 (RO connection helper).

These tests are the regression spec for two connection-layer invariants:

MED-1: init_db() must enable WAL journal mode. Without this, a fresh deploy
       on a new host silently degrades to DELETE mode, breaking concurrent
       reads from Flask while the engine holds a write lock.

MED-2: A get_ro_connection() helper must exist in database.py, opening via
       the SQLite URI interface with ?mode=ro. Dashboard read handlers must
       use it so SQLite itself enforces the read-only contract — not just
       convention.

All tests use function-scoped tmp_path databases. `database.DB_FILE` is
patched so no test touches alphabot_state.db.

Fixture provenance: schema-derived (PA-18). No producer-computed values
are asserted; we assert structural/behavioural properties only.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import sqlite3
from unittest.mock import patch

import pytest

import database as db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_isolated_db(tmp_path: pathlib.Path) -> str:
    """Return path to an isolated temp DB patched into the database module."""
    return str(tmp_path / "test_alphabot_state.db")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_db_path(tmp_path):
    """
    Function-scoped isolated SQLite path. Patches DB_FILE but does NOT patch
    get_connection — tests call the real get_connection() so WAL PRAGMA is
    tested end-to-end.
    """
    test_db = _make_isolated_db(tmp_path)
    with patch.object(db, "DB_FILE", test_db):
        yield test_db


@pytest.fixture
def initialised_db(isolated_db_path):
    """Isolated DB that has been through init_db()."""
    db.init_db()
    return isolated_db_path


# ---------------------------------------------------------------------------
# MED-1: WAL journal mode
# ---------------------------------------------------------------------------


def test_init_db_sets_journal_mode_wal_on_fresh_db(isolated_db_path):
    """AC-1: init_db() on a fresh DB results in journal_mode = 'wal'."""
    db.init_db()
    conn = sqlite3.connect(isolated_db_path)
    try:
        row = conn.execute("PRAGMA journal_mode").fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0].lower() == "wal", (
        f"Expected journal_mode='wal' after init_db(), got '{row[0]}'. "
        "MED-1: init_db() must issue PRAGMA journal_mode=WAL."
    )


def test_init_db_idempotent_wal_already_set(isolated_db_path):
    """AC-6: Calling init_db() twice on a WAL DB keeps WAL, raises no error."""
    db.init_db()
    # Second call — must not raise and must still be WAL.
    db.init_db()
    conn = sqlite3.connect(isolated_db_path)
    try:
        row = conn.execute("PRAGMA journal_mode").fetchone()
    finally:
        conn.close()
    assert row[0].lower() == "wal", (
        "journal_mode degraded after second init_db() call. Must be idempotent."
    )


def test_init_db_wal_pragma_source_commented():
    """AC-1 (source): init_db body contains a comment referencing WAL or journal_mode.

    This is a documentation invariant: the PRAGMA must be explained in-line
    so a reader knows it is intentional (not a stray leftover).
    """
    source = inspect.getsource(db.init_db)
    has_comment = any(
        keyword in source for keyword in ("WAL", "journal_mode", "write-ahead", "Write-Ahead")
    )
    assert has_comment, (
        "init_db() must contain a source comment explaining the WAL PRAGMA. "
        "Missing in current implementation — MED-1 requirement."
    )


def test_wal_shm_files_created_after_first_connect(initialised_db):
    """AC-11: WAL side-files (-wal, -shm) are present during an active write, no crash.

    SQLite creates -wal and -shm lazily on first write in WAL mode. On Windows,
    SQLite auto-checkpoints (merges WAL back into main DB) when the last connection
    closes, removing the side-files. The connection must be kept open during the
    file-existence check to avoid racing the checkpoint.
    """
    base = pathlib.Path(initialised_db)
    wal_path = base.parent / (base.name + "-wal")
    shm_path = base.parent / (base.name + "-shm")

    # Keep connection open during the assert — closing it triggers auto-checkpoint
    # on Windows which removes the side-files before we can observe them.
    conn = sqlite3.connect(initialised_db)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO execution_lock (id, is_locked, timestamp) VALUES (1, 0, 0)"
        )
        conn.commit()
        assert wal_path.exists() or shm_path.exists(), (
            "Neither -wal nor -shm file was created after first write. "
            "WAL mode may not be active — check init_db() PRAGMA."
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# MED-2: get_ro_connection() helper
# ---------------------------------------------------------------------------


def test_get_ro_connection_exists():
    """AC-2: database module exposes get_ro_connection()."""
    assert hasattr(db, "get_ro_connection"), (
        "database.get_ro_connection() does not exist. MED-2 requires this helper."
    )
    assert callable(db.get_ro_connection), "database.get_ro_connection is not callable."


def test_get_ro_connection_returns_sqlite_connection(initialised_db):
    """AC-2: get_ro_connection() returns an sqlite3.Connection."""
    conn = db.get_ro_connection()
    try:
        assert isinstance(conn, sqlite3.Connection)
    finally:
        conn.close()


def test_get_ro_connection_read_succeeds(initialised_db):
    """AC-4: Read query on RO connection succeeds (no exception)."""
    conn = db.get_ro_connection()
    try:
        row = conn.execute("SELECT COUNT(*) FROM execution_lock").fetchone()
        assert row is not None
        assert isinstance(row[0], int)
    finally:
        conn.close()


def test_get_ro_connection_write_raises_operational_error(initialised_db):
    """AC-3: Write attempt on RO connection raises OperationalError.

    SQLite URI ?mode=ro enforces this at the driver level — no application
    code guard is needed. This test verifies enforcement is real.
    """
    conn = db.get_ro_connection()
    try:
        with pytest.raises(sqlite3.OperationalError, match="attempt to write a readonly database"):
            conn.execute("INSERT INTO execution_lock (id, is_locked, timestamp) VALUES (99, 0, 0)")
            conn.commit()
    finally:
        conn.close()


def test_get_ro_connection_source_commented():
    """AC-2 (source): get_ro_connection() body contains a comment explaining the URI mode."""
    assert hasattr(db, "get_ro_connection"), (
        "get_ro_connection() missing — see test_get_ro_connection_exists"
    )
    source = inspect.getsource(db.get_ro_connection)
    has_comment = any(
        keyword in source for keyword in ("mode=ro", "read-only", "read_only", "readonly", "URI")
    )
    assert has_comment, (
        "get_ro_connection() must contain a source comment explaining the ?mode=ro URI. "
        "MED-2 documentation requirement."
    )


# ---------------------------------------------------------------------------
# Dashboard read handlers use get_ro_connection (AST inspection)
# ---------------------------------------------------------------------------

# Read-only route handler names that must call get_ro_connection (not get_connection)
# after the migration. These are GET-only handlers with no state mutation.
_READ_ONLY_HANDLERS = [
    "get_state",
    "api_symphony_logs",
    "get_chart_data",
    "api_triggers",
    "api_autotune_runs",
]

# Write handlers that must retain get_connection() capability.
_WRITE_HANDLERS = [
    "fleet_alert_dismiss",
]


def _parse_app_ast() -> ast.Module:
    app_path = pathlib.Path(__file__).parents[2] / "app.py"
    return ast.parse(app_path.read_text(encoding="utf-8"))


def _get_calls_in_function(tree: ast.Module, func_name: str) -> list[str]:
    """Return list of all called names (simple + attribute) in func_name's body."""
    calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    # database.get_connection() → attr call
                    if isinstance(child.func, ast.Attribute):
                        calls.append(child.func.attr)
                    # bare get_connection() → name call
                    elif isinstance(child.func, ast.Name):
                        calls.append(child.func.id)
    return calls


@pytest.mark.parametrize("handler", _READ_ONLY_HANDLERS)
def test_read_handler_uses_get_ro_connection(handler):
    """AC-7: Dashboard read handlers call get_ro_connection, not get_connection.

    Inspects the AST of app.py. Fails RED until the migration is complete.
    """
    tree = _parse_app_ast()
    calls = _get_calls_in_function(tree, handler)
    assert "get_ro_connection" in calls, (
        f"Handler '{handler}' does not call get_ro_connection(). "
        f"Found calls: {calls}. MED-2: read handlers must use RO connection."
    )
    assert "get_connection" not in calls, (
        f"Handler '{handler}' still calls get_connection() after RO migration. "
        "Must be replaced with get_ro_connection()."
    )


@pytest.mark.parametrize("handler", _WRITE_HANDLERS)
def test_write_handler_retains_get_connection(handler):
    """AC-8: Write handlers (e.g., dismiss) retain get_connection(), not RO.

    Verifies that the RO migration doesn't break write capability on handlers
    that legitimately mutate state.
    """
    tree = _parse_app_ast()
    calls = _get_calls_in_function(tree, handler)
    # Write handlers may call get_connection (directly or via database.* functions)
    # They must NOT be forced onto get_ro_connection.
    assert "get_ro_connection" not in calls, (
        f"Write handler '{handler}' was incorrectly migrated to get_ro_connection(). "
        "Writes must use get_connection()."
    )


# ---------------------------------------------------------------------------
# No live test regression
# ---------------------------------------------------------------------------


def test_no_test_live_files_added():
    """AC-9: No test_live_*.py files introduced by this R7 cycle.

    Scans tests/ recursively. Any new live-tier file would bypass the default
    run exclusion and could hit production APIs.
    """
    tests_root = pathlib.Path(__file__).parents[1]
    live_files = list(tests_root.rglob("test_live_*.py"))
    # Allow pre-existing files — just ensure this module itself is not one.
    assert not any("wal" in str(f) or "ro_connection" in str(f) for f in live_files), (
        f"A test_live_* file referencing WAL/RO was found: {live_files}. "
        "R7 must not add live-tier tests."
    )


# ---------------------------------------------------------------------------
# Schema reversibility: no migration files needed
# ---------------------------------------------------------------------------


def test_no_new_migration_files_for_r7():
    """AC-10: WAL + RO helper require zero schema migrations.

    Inspects _MIGRATION_FILES to assert no R7-specific migration was added.
    WAL is a PRAGMA (not a schema change) and get_ro_connection is a Python
    helper — neither requires a .sql migration file.
    """
    r7_migrations = [
        m
        for m in db._MIGRATION_FILES
        if "wal" in m.lower()
        or m.lower().startswith("ro_")
        or "_ro_" in m.lower()
        or m.lower().endswith("_ro.sql")
    ]
    assert not r7_migrations, (
        f"Unexpected R7 migration files found: {r7_migrations}. "
        "WAL + RO connection require NO schema migrations (PRAGMA + helper only)."
    )
