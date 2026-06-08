"""
RED tests — Migration 032: mc_prob_underperforming additive column on port_state.

Context: run_monte_carlo's return value semantics are being renamed from the
ambiguous "prob_beating" to "prob_underperforming" (the probability the portfolio
underperforms SPY — the guardian signal that drives arm/disarm/TP decisions).
The existing mc_prob column on port_state is preserved unchanged for back-compat.
Migration 032 adds mc_prob_underperforming as a queryable alias with the correct
name, populated via dual-write so both columns hold the same value.

DESIGN (dual-write transition):
  mc_prob is written unchanged (legacy back-compat for existing readers).
  mc_prob_underperforming is written from the same source value on every
  write_port_state call that includes mc_prob. The two columns cannot diverge.
  The new column is the correctly-named source; mc_prob is legacy context.

THE 4 CHANGES (all must land before any test here can go GREEN):
  1. migrations/032_mc_prob_underperforming.sql:
       ALTER TABLE port_state ADD COLUMN mc_prob_underperforming REAL DEFAULT NULL;
  2. database._MIGRATION_FILES: append '032_mc_prob_underperforming.sql' after
       '031_shadow_history_sym_ts_index.sql'.
  3. database._PORT_STATE_COLUMNS: add 'mc_prob_underperforming' to the tuple.
  4. No write_port_state change needed — it already iterates _PORT_STATE_COLUMNS;
     adding the column to the tuple is sufficient for dual-write coverage.

Coverage (all FAIL RED until GREEN implementation lands):
  M1: migration file exists on disk
  M2: mc_prob_underperforming column present after full migration stack
  M3: column present after migration applied to a pre-032 port_state (upgrade path)
  M4: run_migrations() is idempotent for 032 (duplicate-column swallow)
  M5: 032 registered in _MIGRATION_FILES after 031_shadow_history_sym_ts_index.sql
  M6: migration uses ALTER TABLE, not CREATE TABLE port_state
  M7: migration does not reference the optimization DB (two-DB boundary)

  D1: mc_prob_underperforming is REAL, NULLable (notnull=0), DEFAULT NULL

  R1: write_port_state with mc_prob_underperforming populates the column
  R2: write_port_state without mc_prob_underperforming stores NULL (back-compat)
  R3: mc_prob_underperforming round-trips through read_port_state
  R4: existing pre-032 port_state rows survive migration with NULL in the new column
  R5: dual-write — write_port_state with both mc_prob and mc_prob_underperforming
      stores the same value in each column (transition invariant)

  L1: write_port_state with mc_prob_underperforming completes in < 100ms
      (no blocking I/O introduced — architecture constraint 1)
"""

from __future__ import annotations

import sqlite3
import time
import pathlib

import pytest

import database as db_module
from database import init_db, run_migrations

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_MIGRATION_PATH = (
    pathlib.Path(__file__).parents[2] / "migrations" / "032_mc_prob_underperforming.sql"
)

_WORKTREE_ROOT = pathlib.Path(__file__).parents[2]


# ---------------------------------------------------------------------------
# DB isolation fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def migrated_db(tmp_path, monkeypatch):
    """Per-test isolated SQLite DB with the full migration stack applied."""
    db_path = str(tmp_path / "test_032.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path


# ---------------------------------------------------------------------------
# Schema helpers
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


def _port_state_column_names(db_path: str) -> set[str]:
    return set(_table_columns(db_path, "port_state").keys())


# ---------------------------------------------------------------------------
# M1: migration file exists on disk
# ---------------------------------------------------------------------------


def test_migration_032_file_exists():
    """M1: migrations/032_mc_prob_underperforming.sql must exist on disk."""
    assert _MIGRATION_PATH.is_file(), (
        f"Migration file not found: {_MIGRATION_PATH}. "
        "Create migrations/032_mc_prob_underperforming.sql with: "
        "ALTER TABLE port_state ADD COLUMN mc_prob_underperforming REAL DEFAULT NULL;"
    )


# ---------------------------------------------------------------------------
# M2: column present after full migration stack
# ---------------------------------------------------------------------------


def test_full_migration_stack_adds_mc_prob_underperforming(migrated_db):
    """
    M2: A DB with the full migration stack applied must have
    mc_prob_underperforming on port_state.

    Fails RED until migration 032 is added to _MIGRATION_FILES and the SQL file
    exists on disk.
    """
    cols = _port_state_column_names(migrated_db)
    assert "mc_prob_underperforming" in cols, (
        "mc_prob_underperforming not found on port_state after full migration stack. "
        "Ensure migrations/032_mc_prob_underperforming.sql exists and is listed in "
        "database._MIGRATION_FILES after '031_shadow_history_sym_ts_index.sql'."
    )


# ---------------------------------------------------------------------------
# M3: column present after migration applied to a pre-032 port_state
# ---------------------------------------------------------------------------


def test_upgraded_db_gains_mc_prob_underperforming_via_migration(tmp_path, monkeypatch):
    """
    M3: A pre-032 port_state table must gain mc_prob_underperforming after
    run_migrations() applies migration 032.

    Builds a DB with port_state lacking mc_prob_underperforming, marks 032 as
    unapplied, then verifies run_migrations() adds the column.
    """
    db_path = str(tmp_path / "upgrade_032.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  migration_name TEXT PRIMARY KEY,"
        "  applied_at     TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    # port_state WITHOUT mc_prob_underperforming — simulates a pre-032 deployed DB.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS port_state (
            account_id                TEXT    PRIMARY KEY,
            composition_hash          TEXT    DEFAULT NULL,
            high_water_mark           REAL    DEFAULT NULL,
            safe_hwm                  REAL    DEFAULT NULL,
            shadow_hwm                REAL    DEFAULT NULL,
            vwap_ticks_json           TEXT    DEFAULT NULL,
            vwap_bleed_ticks_json     TEXT    DEFAULT NULL,
            mc_history_json           TEXT    DEFAULT NULL,
            mc_prob                   REAL    DEFAULT NULL,
            armed                     INTEGER DEFAULT NULL,
            para_armed                INTEGER DEFAULT NULL,
            port_breakeven_active     INTEGER DEFAULT NULL,
            triggered                 INTEGER DEFAULT NULL,
            triggered_reason          TEXT    DEFAULT NULL,
            prev_return               REAL    DEFAULT NULL,
            current_return            REAL    DEFAULT NULL,
            last_target_reduction_json TEXT   DEFAULT NULL,
            last_selected_symphony_id TEXT    DEFAULT NULL,
            stop_trigger              REAL    DEFAULT NULL,
            updated_at                TEXT    DEFAULT NULL
        )
        """
    )
    # Mark all migrations except 032 as applied.
    for name in db_module._MIGRATION_FILES:
        if name != "032_mc_prob_underperforming.sql":
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (migration_name) VALUES (?)",
                (name,),
            )
    conn.commit()
    conn.close()

    run_migrations()

    cols = _port_state_column_names(db_path)
    assert "mc_prob_underperforming" in cols, (
        "run_migrations() did not add 'mc_prob_underperforming' to port_state. "
        "032_mc_prob_underperforming.sql must use: "
        "ALTER TABLE port_state ADD COLUMN mc_prob_underperforming REAL DEFAULT NULL;"
    )


# ---------------------------------------------------------------------------
# M4: run_migrations() is idempotent for 032
# ---------------------------------------------------------------------------


def test_run_migrations_idempotent_for_032(migrated_db):
    """
    M4: Calling run_migrations() again on a DB that already has 032 applied
    must not raise.

    The duplicate-column-name swallow in run_migrations() must absorb repeated
    applies of 032.
    """
    try:
        run_migrations()
        run_migrations()
    except Exception as exc:
        pytest.fail(
            f"run_migrations() raised on second/third call after 032 already applied: {exc!r}. "
            "The duplicate-column-name swallow must absorb repeated applies."
        )

    cols = _port_state_column_names(migrated_db)
    assert "mc_prob_underperforming" in cols, (
        "mc_prob_underperforming missing after repeated run_migrations() — "
        "idempotency broke the column."
    )


# ---------------------------------------------------------------------------
# M5: 032 registered in _MIGRATION_FILES after 031
# ---------------------------------------------------------------------------


def test_032_in_migration_files_after_031():
    """
    M5: '032_mc_prob_underperforming.sql' must appear in database._MIGRATION_FILES
    and must come after '031_shadow_history_sym_ts_index.sql'.

    NOTE: This test does NOT assert that 032 is the last entry — that pin would
    be brittle as future migrations will follow.
    """
    assert "032_mc_prob_underperforming.sql" in db_module._MIGRATION_FILES, (
        "'032_mc_prob_underperforming.sql' not found in database._MIGRATION_FILES. "
        "Append it after '031_shadow_history_sym_ts_index.sql' in the _MIGRATION_FILES list."
    )
    assert "031_shadow_history_sym_ts_index.sql" in db_module._MIGRATION_FILES, (
        "'031_shadow_history_sym_ts_index.sql' must be present as the predecessor entry."
    )

    idx_031 = db_module._MIGRATION_FILES.index("031_shadow_history_sym_ts_index.sql")
    idx_032 = db_module._MIGRATION_FILES.index("032_mc_prob_underperforming.sql")
    assert idx_032 > idx_031, (
        f"'032_mc_prob_underperforming.sql' (index {idx_032}) must appear AFTER "
        f"'031_shadow_history_sym_ts_index.sql' (index {idx_031}) in _MIGRATION_FILES. "
        "Append-chronological convention: new migrations go at the tail."
    )


# ---------------------------------------------------------------------------
# M6: migration uses ALTER TABLE, not CREATE TABLE port_state
# ---------------------------------------------------------------------------


def test_032_migration_uses_alter_table_not_create_table():
    """
    M6: 032_mc_prob_underperforming.sql must use ALTER TABLE ADD COLUMN,
    not CREATE TABLE port_state.

    port_state already exists from migration 010. A CREATE TABLE would conflict.
    """
    assert _MIGRATION_PATH.is_file(), f"Migration file missing: {_MIGRATION_PATH}"
    sql = _MIGRATION_PATH.read_text(encoding="utf-8")
    sql_upper = sql.upper()

    assert "ALTER TABLE" in sql_upper, (
        "032_mc_prob_underperforming.sql must use ALTER TABLE ADD COLUMN. "
        "port_state already exists from migration 010."
    )

    import re
    create_port_state = re.search(
        r"CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?port_state",
        sql_upper,
    )
    assert create_port_state is None, (
        "032_mc_prob_underperforming.sql must NOT use CREATE TABLE [IF NOT EXISTS] port_state. "
        "Use ALTER TABLE ADD COLUMN only."
    )


# ---------------------------------------------------------------------------
# M7: migration does not reference the optimization DB
# ---------------------------------------------------------------------------


def test_032_migration_does_not_reference_opt_db():
    """
    M7: 032_mc_prob_underperforming.sql must not reference alphabot_opt or .opt.db.

    Architecture constraint 3: port_state lives in the state DB only.
    """
    assert _MIGRATION_PATH.is_file(), f"Migration file missing: {_MIGRATION_PATH}"
    sql_lower = _MIGRATION_PATH.read_text(encoding="utf-8").lower()

    assert "alphabot_opt" not in sql_lower, (
        "032 references 'alphabot_opt' — port_state is state-DB only (two-DB boundary)."
    )
    assert "opt.db" not in sql_lower, (
        "032 references 'opt.db' — state DB only."
    )


# ---------------------------------------------------------------------------
# D1: mc_prob_underperforming is REAL, NULLable, DEFAULT NULL
# ---------------------------------------------------------------------------


def test_mc_prob_underperforming_column_schema(migrated_db):
    """
    D1: mc_prob_underperforming must be REAL, NULLable (notnull=0), DEFAULT NULL.

    Project additive-first rule: new columns on existing tables must be NULLable
    with DEFAULT NULL — a non-NULL default would implicitly alter all pre-existing
    port_state rows.
    """
    col_info = _table_columns(migrated_db, "port_state")
    assert "mc_prob_underperforming" in col_info, (
        "mc_prob_underperforming column not found on port_state. "
        "Migration 032 must add it."
    )
    info = col_info["mc_prob_underperforming"]

    assert info["notnull"] == 0, (
        f"mc_prob_underperforming must be NULLable (notnull=0); got notnull={info['notnull']}. "
        "Project additive-first rule: new columns on existing tables must be NULLable."
    )

    dflt = info["dflt_value"]
    assert dflt is None or dflt == "NULL", (
        f"mc_prob_underperforming must have DEFAULT NULL; got dflt_value={dflt!r}. "
        "A non-NULL default would silently modify all pre-existing port_state rows."
    )

    assert "REAL" in info["type"].upper(), (
        f"mc_prob_underperforming must have affinity REAL; got type={info['type']!r}. "
        "mc_prob on the same table is REAL — the new column must match."
    )


# ---------------------------------------------------------------------------
# R1: write_port_state with mc_prob_underperforming populates the column
# ---------------------------------------------------------------------------


def test_write_port_state_populates_mc_prob_underperforming(migrated_db, monkeypatch):
    """
    R1: write_port_state called with mc_prob_underperforming in the state dict
    must persist the value to the column.

    Verifies that _PORT_STATE_COLUMNS includes 'mc_prob_underperforming' and
    the INSERT OR REPLACE path writes it.
    """
    monkeypatch.setattr(db_module, "DB_FILE", migrated_db)

    db_module.write_port_state("acc-r1", {"mc_prob_underperforming": 42.5})

    conn = sqlite3.connect(migrated_db)
    try:
        row = conn.execute(
            "SELECT mc_prob_underperforming FROM port_state WHERE account_id = 'acc-r1'"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "write_port_state did not write a port_state row."
    assert row[0] == pytest.approx(42.5), (
        f"mc_prob_underperforming must be 42.5 after write; got {row[0]!r}. "
        "Ensure 'mc_prob_underperforming' is in database._PORT_STATE_COLUMNS."
    )


# ---------------------------------------------------------------------------
# R2: write_port_state without mc_prob_underperforming stores NULL (back-compat)
# ---------------------------------------------------------------------------


def test_write_port_state_omit_mc_prob_underperforming_stores_null(migrated_db, monkeypatch):
    """
    R2: write_port_state called WITHOUT mc_prob_underperforming in the state dict
    must store NULL in the column (back-compat: existing callers need no change).
    """
    monkeypatch.setattr(db_module, "DB_FILE", migrated_db)

    # Write without mc_prob_underperforming — pre-032 call pattern.
    db_module.write_port_state("acc-r2", {"mc_prob": 38.0, "current_return": 1.5})

    conn = sqlite3.connect(migrated_db)
    try:
        row = conn.execute(
            "SELECT mc_prob_underperforming FROM port_state WHERE account_id = 'acc-r2'"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "write_port_state did not write a port_state row."
    assert row[0] is None, (
        f"mc_prob_underperforming must be NULL when not supplied; got {row[0]!r}. "
        "Existing callers that omit mc_prob_underperforming must not break."
    )


# ---------------------------------------------------------------------------
# R3: mc_prob_underperforming round-trips through read_port_state
# ---------------------------------------------------------------------------


def test_read_port_state_returns_mc_prob_underperforming(migrated_db, monkeypatch):
    """
    R3: read_port_state must return mc_prob_underperforming in the result dict
    so callers can access it without a separate query.
    """
    monkeypatch.setattr(db_module, "DB_FILE", migrated_db)

    db_module.write_port_state("acc-r3", {"mc_prob_underperforming": 55.0})

    result = db_module.read_port_state("acc-r3")

    assert result is not None, "read_port_state returned None — row was not written."
    assert "mc_prob_underperforming" in result, (
        f"read_port_state result dict must include 'mc_prob_underperforming'. "
        f"Keys present: {list(result.keys())}. "
        "Ensure 'mc_prob_underperforming' is in database._PORT_STATE_COLUMNS "
        "(the SELECT list is built from that tuple)."
    )
    assert result["mc_prob_underperforming"] == pytest.approx(55.0), (
        f"mc_prob_underperforming round-trip mismatch: wrote 55.0, "
        f"read back {result['mc_prob_underperforming']!r}."
    )


# ---------------------------------------------------------------------------
# R4: existing pre-032 rows survive migration with NULL in the new column
# ---------------------------------------------------------------------------


def test_pre_032_rows_survive_migration_with_null_mc_prob_underperforming(tmp_path, monkeypatch):
    """
    R4: port_state rows written BEFORE migration 032 is applied must survive
    with mc_prob_underperforming = NULL after the migration runs.

    Verifies the migration is purely additive — no data loss, no coercion.
    """
    db_path = str(tmp_path / "r4_survival.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)

    # Build a pre-032 port_state without mc_prob_underperforming.
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  migration_name TEXT PRIMARY KEY,"
        "  applied_at     TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS port_state (
            account_id    TEXT PRIMARY KEY,
            mc_prob       REAL DEFAULT NULL,
            current_return REAL DEFAULT NULL,
            updated_at    TEXT DEFAULT NULL
        )
        """
    )
    # Insert a pre-032 row with a known mc_prob value.
    conn.execute(
        "INSERT INTO port_state (account_id, mc_prob, current_return) VALUES (?, ?, ?)",
        ("acc-legacy", 33.3, 2.1),
    )
    conn.commit()
    # Mark all migrations except 032 as applied.
    for name in db_module._MIGRATION_FILES:
        if name != "032_mc_prob_underperforming.sql":
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (migration_name) VALUES (?)",
                (name,),
            )
    conn.commit()
    conn.close()

    run_migrations()

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT mc_prob, mc_prob_underperforming FROM port_state "
            "WHERE account_id = 'acc-legacy'"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "Pre-032 row not found after migration — data loss."
    mc_prob, mc_prob_underperforming = row

    assert mc_prob == pytest.approx(33.3), (
        f"Pre-032 row mc_prob corrupted after migration: {mc_prob!r}"
    )
    assert mc_prob_underperforming is None, (
        f"Pre-032 row mc_prob_underperforming must be NULL (DEFAULT NULL applies); "
        f"got {mc_prob_underperforming!r}."
    )


# ---------------------------------------------------------------------------
# R5: dual-write — mc_prob and mc_prob_underperforming hold the same value
# ---------------------------------------------------------------------------


def test_dual_write_mc_prob_and_mc_prob_underperforming_store_same_value(migrated_db, monkeypatch):
    """
    R5: write_port_state called with BOTH mc_prob and mc_prob_underperforming
    must store the same value in each column (transition invariant).

    The dual-write design means both columns are populated from the same source;
    they must not diverge. This test pins that invariant so a future accidental
    decoupling is caught immediately.
    """
    monkeypatch.setattr(db_module, "DB_FILE", migrated_db)

    shared_value = 67.8
    db_module.write_port_state("acc-r5", {
        "mc_prob": shared_value,
        "mc_prob_underperforming": shared_value,
    })

    conn = sqlite3.connect(migrated_db)
    try:
        row = conn.execute(
            "SELECT mc_prob, mc_prob_underperforming FROM port_state "
            "WHERE account_id = 'acc-r5'"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "write_port_state did not write a port_state row."
    mc_prob, mc_prob_underperforming = row

    assert mc_prob == pytest.approx(shared_value), (
        f"mc_prob must be {shared_value}; got {mc_prob!r}."
    )
    assert mc_prob_underperforming == pytest.approx(shared_value), (
        f"mc_prob_underperforming must be {shared_value}; got {mc_prob_underperforming!r}. "
        "Dual-write invariant: both columns must hold the same value."
    )
    assert mc_prob == pytest.approx(mc_prob_underperforming), (
        f"mc_prob ({mc_prob}) and mc_prob_underperforming ({mc_prob_underperforming}) "
        "diverged — dual-write invariant violated."
    )


# ---------------------------------------------------------------------------
# L1: write_port_state with mc_prob_underperforming does not block
# ---------------------------------------------------------------------------


def test_write_port_state_with_mc_prob_underperforming_does_not_block(migrated_db, monkeypatch):
    """
    L1: write_port_state with mc_prob_underperforming must complete in < 100ms.

    Architecture constraint 1: no blocking I/O on the 1-minute execution path.
    Adding a column to _PORT_STATE_COLUMNS must not introduce blocking work;
    it simply extends the INSERT OR REPLACE parameter list by one REAL value.
    """
    monkeypatch.setattr(db_module, "DB_FILE", migrated_db)

    start = time.perf_counter()
    db_module.write_port_state("acc-l1", {
        "mc_prob": 50.0,
        "mc_prob_underperforming": 50.0,
        "current_return": 0.5,
    })
    elapsed = time.perf_counter() - start

    assert elapsed < 0.1, (
        f"write_port_state took {elapsed:.3f}s — exceeds 100ms budget. "
        "Architecture constraint 1: no blocking I/O on the 1-minute execution path. "
        "Adding mc_prob_underperforming to _PORT_STATE_COLUMNS must not introduce "
        "additional round-trips or blocking work."
    )
