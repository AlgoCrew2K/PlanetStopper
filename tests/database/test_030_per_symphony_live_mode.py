"""
RED tests — Migration 030: per-symphony live_mode + config_audit_log.

Context: today live/dry-run is a single global flag LIVE_EXECUTION.  Migration 030
adds per-symphony live_mode under a MASTER-SWITCH model:
  is_live = LIVE_EXECUTION AND symphony_live_mode

Design constraints enforced by these tests:
  - live_mode is a SEPARATE INTEGER column on symphony_strategies (NOT inside
    the parameters JSON blob), so it is invisible to the autotuner.
  - Default is 0 (dry-run): unconfigured symphonies are always dry (arch rule 4).
  - config_audit_log records every set_symphony_live_mode call with before→after,
    operator, ts_utc — no update/delete accessor (append-only).

THE 3 IMPLEMENTATION DELIVERABLES (all must land for these tests to go GREEN):
  1. migrations/030_per_symphony_live_mode.sql:
       ALTER TABLE symphony_strategies ADD COLUMN live_mode INTEGER DEFAULT 0;
       CREATE TABLE IF NOT EXISTS config_audit_log (
         id          INTEGER PRIMARY KEY AUTOINCREMENT,
         symphony    TEXT    NOT NULL,
         field       TEXT    NOT NULL,
         before_value TEXT,
         after_value TEXT    NOT NULL,
         operator    TEXT    NOT NULL,
         ts_utc      TEXT    NOT NULL
       );
  2. database._MIGRATION_FILES: append '030_per_symphony_live_mode.sql' after
       '029_exit_triggers_also_true.sql'.
  3. database.py new functions:
       get_symphony_live_mode(symphony_name: str) -> int
           — returns live_mode (0 or 1); 0 when row absent.
       set_symphony_live_mode(symphony_name: str, live: int, operator: str) -> None
           — UPDATEs live_mode on symphony_strategies (INSERT OR IGNORE first if
             the row doesn't exist) AND writes a config_audit_log row with
             before_value (the prior live_mode as str), after_value=str(live),
             operator, and ts_utc=datetime.utcnow().isoformat().
     Also: init_db() CREATE TABLE symphony_strategies must include live_mode
       inline (H1 dual-write mirror — fresh DBs must have the column without
       run_migrations).

Coverage (all FAIL RED until GREEN implementation lands):
  M1: migration file exists on disk
  M2: live_mode column present after init_db() only (fresh DB, H1 dual-write)
  M3: live_mode column present after migration on pre-030 DB (upgrade path)
  M4: run_migrations() is idempotent for 030 (duplicate-column swallow)
  M5: 030 registered in _MIGRATION_FILES after 029
  M6: migration file uses ALTER TABLE for symphony_strategies (not CREATE TABLE)
  M7: config_audit_log created by migration (CREATE TABLE IF NOT EXISTS)
  M8: migration does not reference the optimization DB (two-DB boundary)

  C1: live_mode column is INTEGER, NULLable (additive-first), DEFAULT 0
  C2: config_audit_log has the required columns (symphony, field, before_value,
      after_value, operator, ts_utc)

  G1: get_symphony_live_mode exists and has symphony_name parameter
  G2: get_symphony_live_mode returns 0 when no row exists (default-dry safety)
  G3: get_symphony_live_mode returns 0 when live_mode column is 0
  G4: get_symphony_live_mode returns 1 when live_mode column is 1
  G5: get_symphony_live_mode normalizes the symphony_name (matches save_symphony_strategy)

  S1: set_symphony_live_mode exists with symphony_name, live, operator parameters
  S2: set_symphony_live_mode updates live_mode from 0 → 1; read-back confirms
  S3: set_symphony_live_mode updates live_mode from 1 → 0; read-back confirms
  S4: set_symphony_live_mode writes a config_audit_log row with correct before/after
  S5: set_symphony_live_mode on a symphony with no existing row creates it and audits
  S6: set_symphony_live_mode writes ts_utc as an ISO-formatted string (not NULL)

  A1: live_mode does NOT appear in the parameters JSON blob written by
      save_symphony_strategy (operator-only column — autotuner cannot reach it)
  A2: save_symphony_strategy round-trip leaves live_mode unchanged (does NOT reset
      live_mode to 0 on an INSERT OR REPLACE that re-writes the row)

  L1: get_symphony_live_mode does not block — completes in < 50ms

Fixture: tests/fixtures/database/per_symphony_live_mode/per_symphony_live_mode_schema.json
Provenance: schema-derived from mission brief + database.py:107-113 (symphony_strategies).
"""

from __future__ import annotations

import inspect
import json
import pathlib
import sqlite3
import time

import pytest

import database as db_module
from database import init_db, run_migrations, save_symphony_strategy

# ---------------------------------------------------------------------------
# Fixture path
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    pathlib.Path(__file__).parents[1]
    / "fixtures"
    / "database"
    / "per_symphony_live_mode"
    / "per_symphony_live_mode_schema.json"
)

_MIGRATION_PATH = (
    pathlib.Path(__file__).parents[2] / "migrations" / "030_per_symphony_live_mode.sql"
)


# ---------------------------------------------------------------------------
# DB isolation fixture (function-scoped; mirrors test_029 pattern)
# ---------------------------------------------------------------------------


@pytest.fixture()
def migrated_db(tmp_path, monkeypatch):
    """Per-test isolated SQLite DB with the full migration stack applied."""
    db_path = str(tmp_path / "test_030.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path


# ---------------------------------------------------------------------------
# Schema introspection helpers
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


def _table_exists(db_path: str, table_name: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
    finally:
        conn.close()
    return row is not None


# ---------------------------------------------------------------------------
# M1: migration file exists
# ---------------------------------------------------------------------------


def test_migration_030_file_exists():
    """M1: migrations/030_per_symphony_live_mode.sql must exist on disk."""
    assert _MIGRATION_PATH.is_file(), (
        f"Migration file not found: {_MIGRATION_PATH}. "
        "Create migrations/030_per_symphony_live_mode.sql with: "
        "ALTER TABLE symphony_strategies ADD COLUMN live_mode INTEGER DEFAULT 0; "
        "CREATE TABLE IF NOT EXISTS config_audit_log (...);"
    )


# ---------------------------------------------------------------------------
# M2: live_mode column present after init_db() only (fresh DB, H1 dual-write)
# ---------------------------------------------------------------------------


def test_fresh_db_symphony_strategies_has_live_mode_column(tmp_path, monkeypatch):
    """
    M2: A DB created via init_db() ONLY must have live_mode on symphony_strategies.

    H1 dual-write: the init_db() CREATE TABLE symphony_strategies must include
    live_mode inline so fresh deployments do not silently lack the column.

    Will be RED until init_db()'s CREATE TABLE symphony_strategies block is updated.
    """
    db_path = str(tmp_path / "fresh_030.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()

    cols = set(_table_columns(db_path, "symphony_strategies").keys())
    assert "live_mode" in cols, (
        "init_db() CREATE TABLE symphony_strategies is missing 'live_mode'. "
        "Fix: add live_mode INTEGER DEFAULT 0 to the CREATE TABLE block in init_db() "
        "(database.py). H1 hazard: fresh deployments will silently lack the column."
    )


# ---------------------------------------------------------------------------
# M3: live_mode column present after migration on a pre-030 DB
# ---------------------------------------------------------------------------


def test_upgraded_db_gains_live_mode_column_via_migration(tmp_path, monkeypatch):
    """
    M3: A pre-030 symphony_strategies table must gain live_mode after run_migrations().

    Builds a DB with symphony_strategies lacking live_mode, marks 030 as unapplied,
    then verifies run_migrations() adds the column.
    """
    db_path = str(tmp_path / "upgrade_030.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  migration_name TEXT PRIMARY KEY,"
        "  applied_at     TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    # symphony_strategies WITHOUT live_mode — simulates a pre-030 deployed DB.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS symphony_strategies (
            symphony_name TEXT PRIMARY KEY,
            parameters    TEXT,
            locked_vars   TEXT
        )
        """
    )
    for name in db_module._MIGRATION_FILES:
        if name != "030_per_symphony_live_mode.sql":
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (migration_name) VALUES (?)",
                (name,),
            )
    conn.commit()
    conn.close()

    run_migrations()

    cols = set(_table_columns(db_path, "symphony_strategies").keys())
    assert "live_mode" in cols, (
        "run_migrations() did not add 'live_mode' to symphony_strategies. "
        "030_per_symphony_live_mode.sql must include: "
        "ALTER TABLE symphony_strategies ADD COLUMN live_mode INTEGER DEFAULT 0;"
    )


# ---------------------------------------------------------------------------
# M4: run_migrations() is idempotent for 030
# ---------------------------------------------------------------------------


def test_run_migrations_idempotent_for_030(migrated_db):
    """
    M4: Calling run_migrations() again after 030 is applied must not raise.

    The duplicate-column-name swallow in run_migrations() (database.py:1196-1207)
    must absorb repeated applies of 030.
    """
    try:
        run_migrations()
        run_migrations()
    except Exception as exc:
        pytest.fail(
            f"run_migrations() raised on second/third call after 030 already applied: {exc!r}. "
            "The duplicate-column-name swallow must absorb repeated applies."
        )

    cols = set(_table_columns(migrated_db, "symphony_strategies").keys())
    assert "live_mode" in cols, (
        "live_mode missing after repeated run_migrations() — idempotency broke the column."
    )


# ---------------------------------------------------------------------------
# M5: 030 registered in _MIGRATION_FILES after 029
# ---------------------------------------------------------------------------


def test_030_in_migration_files_after_029():
    """
    M5: '030_per_symphony_live_mode.sql' must appear in database._MIGRATION_FILES
    and must come after '029_exit_triggers_also_true.sql'.
    """
    assert "030_per_symphony_live_mode.sql" in db_module._MIGRATION_FILES, (
        "'030_per_symphony_live_mode.sql' not found in database._MIGRATION_FILES. "
        "Append it after '029_exit_triggers_also_true.sql' in the _MIGRATION_FILES list."
    )
    assert "029_exit_triggers_also_true.sql" in db_module._MIGRATION_FILES, (
        "'029_exit_triggers_also_true.sql' must be present as the predecessor entry."
    )

    idx_029 = db_module._MIGRATION_FILES.index("029_exit_triggers_also_true.sql")
    idx_030 = db_module._MIGRATION_FILES.index("030_per_symphony_live_mode.sql")
    assert idx_030 > idx_029, (
        f"'030_per_symphony_live_mode.sql' (index {idx_030}) must appear AFTER "
        f"'029_exit_triggers_also_true.sql' (index {idx_029}) in _MIGRATION_FILES. "
        "Append-chronological convention: new migrations go at the tail."
    )


# ---------------------------------------------------------------------------
# M6: migration uses ALTER TABLE for symphony_strategies, not CREATE TABLE
# ---------------------------------------------------------------------------


def test_030_migration_uses_alter_table_for_symphony_strategies():
    """
    M6: 030_per_symphony_live_mode.sql must use ALTER TABLE ADD COLUMN for
    symphony_strategies (which already exists from init_db).
    """
    assert _MIGRATION_PATH.is_file(), f"Migration file missing: {_MIGRATION_PATH}"
    sql = _MIGRATION_PATH.read_text(encoding="utf-8")
    sql_upper = sql.upper()

    assert "ALTER TABLE" in sql_upper, (
        "030_per_symphony_live_mode.sql must use ALTER TABLE ADD COLUMN for "
        "symphony_strategies. The table already exists from init_db()."
    )

    import re
    create_strat = re.search(
        r"CREATE\s+TABLE\s+(?!IF\s+NOT\s+EXISTS\s+config_audit_log)"
        r"symphony_strategies",
        sql_upper,
    )
    assert create_strat is None, (
        "030_per_symphony_live_mode.sql must NOT use CREATE TABLE symphony_strategies. "
        "Use ALTER TABLE ADD COLUMN live_mode INTEGER DEFAULT 0;"
    )


# ---------------------------------------------------------------------------
# M7: config_audit_log table created by migration
# ---------------------------------------------------------------------------


def test_030_migration_creates_config_audit_log(migrated_db):
    """
    M7: After run_migrations(), config_audit_log must exist in the DB.

    Migration 030 must CREATE TABLE IF NOT EXISTS config_audit_log.
    """
    assert _table_exists(migrated_db, "config_audit_log"), (
        "config_audit_log table not found after 030 migration. "
        "030_per_symphony_live_mode.sql must include: "
        "CREATE TABLE IF NOT EXISTS config_audit_log (...);"
    )


# ---------------------------------------------------------------------------
# M8: migration does not reference the optimization DB
# ---------------------------------------------------------------------------


def test_030_migration_does_not_reference_opt_db():
    """
    M8: 030_per_symphony_live_mode.sql must not reference alphabot_opt or .opt.db.

    Architecture constraint 3: symphony_strategies and config_audit_log live in
    the state DB only.
    """
    assert _MIGRATION_PATH.is_file(), f"Migration file missing: {_MIGRATION_PATH}"
    sql_lower = _MIGRATION_PATH.read_text(encoding="utf-8").lower()

    assert "alphabot_opt" not in sql_lower, (
        "030 references 'alphabot_opt' — both tables are state-DB only (two-DB boundary)."
    )
    assert "opt.db" not in sql_lower, (
        "030 references 'opt.db' — state DB only."
    )


# ---------------------------------------------------------------------------
# C1: live_mode column is INTEGER, NULLable, DEFAULT 0
# ---------------------------------------------------------------------------


def test_live_mode_column_schema_integer_nullable_default_zero(migrated_db):
    """
    C1: live_mode must be INTEGER, NULLable (additive-first), and DEFAULT 0.

    Project additive-first rule: new columns on existing tables must be NULLable.
    DEFAULT 0 means every pre-existing row reads as dry-run — arch rule 4.
    """
    col_info = _table_columns(migrated_db, "symphony_strategies")
    assert "live_mode" in col_info, (
        "live_mode column not found on symphony_strategies. Migration 030 must add it."
    )
    info = col_info["live_mode"]

    assert info["notnull"] == 0, (
        f"live_mode must be NULLable (notnull=0); got notnull={info['notnull']}. "
        "Additive-first rule."
    )

    # SQLite stores defaults as strings; '0' or 0 are both valid representations.
    dflt = info["dflt_value"]
    assert dflt == "0" or dflt == 0, (
        f"live_mode must have DEFAULT 0; got dflt_value={dflt!r}. "
        "DEFAULT 0 means unconfigured symphonies default-dry (arch rule 4)."
    )

    assert "INT" in info["type"].upper(), (
        f"live_mode must have affinity INTEGER; got type={info['type']!r}."
    )


# ---------------------------------------------------------------------------
# C2: config_audit_log has the required columns
# ---------------------------------------------------------------------------


def test_config_audit_log_has_required_columns(migrated_db):
    """
    C2: config_audit_log must have: id, symphony, field, before_value,
    after_value, operator, ts_utc.

    These columns are the minimum needed for a complete operator audit trail.
    """
    assert _FIXTURE_PATH.is_file(), f"Fixture missing: {_FIXTURE_PATH}"
    fx = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    required = set(fx["migration_030"]["config_audit_log_table"]["columns"].keys())

    actual = set(_table_columns(migrated_db, "config_audit_log").keys())
    missing = required - actual
    assert not missing, (
        f"config_audit_log is missing required columns: {sorted(missing)}. "
        "Migration 030 CREATE TABLE config_audit_log must include all: "
        f"{sorted(required)}."
    )


# ---------------------------------------------------------------------------
# G1: get_symphony_live_mode function exists with correct signature
# ---------------------------------------------------------------------------


def test_get_symphony_live_mode_function_exists_with_symphony_name_param():
    """
    G1: database.get_symphony_live_mode must exist and accept symphony_name.

    This is the lightweight read the exec path calls once per symphony per cycle.
    """
    assert hasattr(db_module, "get_symphony_live_mode"), (
        "database.get_symphony_live_mode does not exist. "
        "Add: def get_symphony_live_mode(symphony_name: str) -> int"
    )
    sig = inspect.signature(db_module.get_symphony_live_mode)
    assert "symphony_name" in sig.parameters, (
        "get_symphony_live_mode must accept a 'symphony_name' parameter."
    )


# ---------------------------------------------------------------------------
# G2: get_symphony_live_mode returns 0 when no row exists
# ---------------------------------------------------------------------------


def test_get_symphony_live_mode_returns_zero_when_no_row(migrated_db, monkeypatch):
    """
    G2: get_symphony_live_mode must return 0 for a symphony with no
    symphony_strategies row — default-dry safety (arch rule 4).

    A missing row must never be interpreted as live.
    """
    monkeypatch.setattr(db_module, "DB_FILE", migrated_db)
    result = db_module.get_symphony_live_mode("nonexistent-symphony")
    assert result == 0, (
        f"get_symphony_live_mode must return 0 for an unknown symphony; got {result!r}. "
        "Default-dry safety: arch rule 4 — is_live=True is explicit, never by omission."
    )


# ---------------------------------------------------------------------------
# G3: returns 0 when live_mode column is 0
# ---------------------------------------------------------------------------


def test_get_symphony_live_mode_returns_zero_when_column_zero(migrated_db, monkeypatch):
    """
    G3: get_symphony_live_mode must return 0 when the symphony_strategies row
    has live_mode=0 (explicit dry-run).
    """
    monkeypatch.setattr(db_module, "DB_FILE", migrated_db)
    save_symphony_strategy("dry-symphony", db_module.DEFAULT_STRATEGY, db_module.DEFAULT_LOCKED_VARS)

    # live_mode column defaults to 0 — do not call set_symphony_live_mode.
    result = db_module.get_symphony_live_mode("dry-symphony")
    assert result == 0, (
        f"get_symphony_live_mode must return 0 when live_mode column is 0; got {result!r}."
    )


# ---------------------------------------------------------------------------
# G4: returns 1 when live_mode column is 1
# ---------------------------------------------------------------------------


def test_get_symphony_live_mode_returns_one_when_column_one(migrated_db, monkeypatch):
    """
    G4: get_symphony_live_mode must return 1 when the symphony_strategies row
    has live_mode=1 (operator has explicitly enabled live for this symphony).
    """
    monkeypatch.setattr(db_module, "DB_FILE", migrated_db)
    save_symphony_strategy("live-symphony", db_module.DEFAULT_STRATEGY, db_module.DEFAULT_LOCKED_VARS)

    # Directly write live_mode=1 via SQL — set_symphony_live_mode is tested
    # separately; here we test the reader in isolation.
    conn = sqlite3.connect(migrated_db)
    try:
        conn.execute(
            "UPDATE symphony_strategies SET live_mode = 1 WHERE symphony_name = ?",
            ("live-symphony",),
        )
        conn.commit()
    finally:
        conn.close()

    result = db_module.get_symphony_live_mode("live-symphony")
    assert result == 1, (
        f"get_symphony_live_mode must return 1 when live_mode column is 1; got {result!r}."
    )


# ---------------------------------------------------------------------------
# G5: get_symphony_live_mode normalizes symphony_name
# ---------------------------------------------------------------------------


def test_get_symphony_live_mode_normalizes_symphony_name(migrated_db, monkeypatch):
    """
    G5: get_symphony_live_mode must normalize its symphony_name argument the same
    way get_symphony_strategy does — calling with a denormalized name must return
    the same result as calling with the normalized name.

    Rationale: the exec path receives raw API symphony names; normalization must
    be consistent or the reader returns 0 (dry-run) for names that exist in the DB
    under a normalized form.
    """
    monkeypatch.setattr(db_module, "DB_FILE", migrated_db)
    # save_symphony_strategy normalizes the name; use a name that will be altered by
    # database.normalize_name so we can confirm get_symphony_live_mode also normalizes.
    raw_name = "My Test Symphony!!"
    normalized = db_module.normalize_name(raw_name)
    save_symphony_strategy(raw_name, db_module.DEFAULT_STRATEGY, db_module.DEFAULT_LOCKED_VARS)

    conn = sqlite3.connect(migrated_db)
    try:
        conn.execute(
            "UPDATE symphony_strategies SET live_mode = 1 WHERE symphony_name = ?",
            (normalized,),
        )
        conn.commit()
    finally:
        conn.close()

    # Query with the raw (un-normalized) name — must still find live_mode=1.
    result = db_module.get_symphony_live_mode(raw_name)
    assert result == 1, (
        f"get_symphony_live_mode('{raw_name}') returned {result!r} but live_mode=1 "
        f"was set under the normalized name '{normalized}'. "
        "get_symphony_live_mode must normalize its argument before the SELECT."
    )


# ---------------------------------------------------------------------------
# S1: set_symphony_live_mode exists with correct signature
# ---------------------------------------------------------------------------


def test_set_symphony_live_mode_function_exists_with_correct_params():
    """
    S1: database.set_symphony_live_mode must exist with symphony_name, live,
    and operator parameters.

    Phase 2's UI calls this; Phase 1 provides the backend implementation.
    """
    assert hasattr(db_module, "set_symphony_live_mode"), (
        "database.set_symphony_live_mode does not exist. "
        "Add: def set_symphony_live_mode(symphony_name: str, live: int, operator: str) -> None"
    )
    sig = inspect.signature(db_module.set_symphony_live_mode)
    for param in ("symphony_name", "live", "operator"):
        assert param in sig.parameters, (
            f"set_symphony_live_mode must have a '{param}' parameter. "
            f"Parameters present: {list(sig.parameters.keys())}"
        )


# ---------------------------------------------------------------------------
# S2: set_symphony_live_mode updates live_mode 0 → 1
# ---------------------------------------------------------------------------


def test_set_symphony_live_mode_enables_live_mode(migrated_db, monkeypatch):
    """
    S2: set_symphony_live_mode(sym, live=1, operator='admin') must set live_mode=1.

    Read-back via get_symphony_live_mode confirms the write.
    """
    monkeypatch.setattr(db_module, "DB_FILE", migrated_db)
    save_symphony_strategy("switch-sym", db_module.DEFAULT_STRATEGY, db_module.DEFAULT_LOCKED_VARS)

    db_module.set_symphony_live_mode("switch-sym", live=1, operator="admin")

    result = db_module.get_symphony_live_mode("switch-sym")
    assert result == 1, (
        f"After set_symphony_live_mode(live=1), get_symphony_live_mode must return 1; "
        f"got {result!r}."
    )


# ---------------------------------------------------------------------------
# S3: set_symphony_live_mode updates live_mode 1 → 0
# ---------------------------------------------------------------------------


def test_set_symphony_live_mode_disables_live_mode(migrated_db, monkeypatch):
    """
    S3: set_symphony_live_mode(sym, live=0, operator='admin') must set live_mode=0.
    """
    monkeypatch.setattr(db_module, "DB_FILE", migrated_db)
    save_symphony_strategy("disable-sym", db_module.DEFAULT_STRATEGY, db_module.DEFAULT_LOCKED_VARS)

    # Enable first, then disable.
    db_module.set_symphony_live_mode("disable-sym", live=1, operator="admin")
    db_module.set_symphony_live_mode("disable-sym", live=0, operator="operator")

    result = db_module.get_symphony_live_mode("disable-sym")
    assert result == 0, (
        f"After set_symphony_live_mode(live=0), get_symphony_live_mode must return 0; "
        f"got {result!r}."
    )


# ---------------------------------------------------------------------------
# S4: set_symphony_live_mode writes a config_audit_log row with correct fields
# ---------------------------------------------------------------------------


def test_set_symphony_live_mode_writes_audit_log_row(migrated_db, monkeypatch):
    """
    S4: set_symphony_live_mode must write a config_audit_log row capturing
    before_value, after_value, operator, and a non-NULL ts_utc.

    Fixture: audit_log_cases[0] (set_to_live).
    """
    assert _FIXTURE_PATH.is_file(), f"Fixture missing: {_FIXTURE_PATH}"
    fx = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    case = next(c for c in fx["audit_log_cases"] if c["id"] == "set_to_live")

    monkeypatch.setattr(db_module, "DB_FILE", migrated_db)
    save_symphony_strategy(case["symphony"], db_module.DEFAULT_STRATEGY, db_module.DEFAULT_LOCKED_VARS)

    db_module.set_symphony_live_mode(case["symphony"], live=int(case["after_value"]), operator=case["operator"])

    conn = sqlite3.connect(migrated_db)
    try:
        row = conn.execute(
            "SELECT symphony, field, before_value, after_value, operator, ts_utc "
            "FROM config_audit_log WHERE symphony = ? ORDER BY id DESC LIMIT 1",
            (db_module.normalize_name(case["symphony"]),),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, (
        "set_symphony_live_mode did not write a config_audit_log row. "
        "The function must INSERT into config_audit_log after updating live_mode."
    )
    sym, field, before_value, after_value, operator, ts_utc = row

    assert field == "live_mode", (
        f"audit log field must be 'live_mode'; got {field!r}."
    )
    assert before_value == case["before_value"], (
        f"audit log before_value must be {case['before_value']!r} (prior live_mode); "
        f"got {before_value!r}."
    )
    assert after_value == case["after_value"], (
        f"audit log after_value must be {case['after_value']!r}; got {after_value!r}."
    )
    assert operator == case["operator"], (
        f"audit log operator must be {case['operator']!r}; got {operator!r}."
    )
    assert ts_utc is not None and len(ts_utc) >= 10, (
        f"audit log ts_utc must be a non-NULL ISO date string; got {ts_utc!r}."
    )


# ---------------------------------------------------------------------------
# S5: set_symphony_live_mode on a symphony with no existing row
# ---------------------------------------------------------------------------


def test_set_symphony_live_mode_creates_row_when_absent(migrated_db, monkeypatch):
    """
    S5: set_symphony_live_mode on a symphony that has no symphony_strategies row
    must create the row and write an audit log entry.

    This handles the case where an operator enables live_mode before the first
    autotuner run has created the strategy row.
    """
    monkeypatch.setattr(db_module, "DB_FILE", migrated_db)

    # Do NOT call save_symphony_strategy first — row is absent.
    db_module.set_symphony_live_mode("brand-new-sym", live=1, operator="admin")

    live = db_module.get_symphony_live_mode("brand-new-sym")
    assert live == 1, (
        f"set_symphony_live_mode on a new symphony must create the row with live_mode=1; "
        f"got {live!r}."
    )

    conn = sqlite3.connect(migrated_db)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM config_audit_log WHERE symphony = ?",
            (db_module.normalize_name("brand-new-sym"),),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] >= 1, (
        "set_symphony_live_mode on a new symphony must still write a config_audit_log row."
    )


# ---------------------------------------------------------------------------
# S6: set_symphony_live_mode writes a non-NULL ts_utc
# ---------------------------------------------------------------------------


def test_set_symphony_live_mode_audit_ts_utc_is_iso_string(migrated_db, monkeypatch):
    """
    S6: The ts_utc written to config_audit_log must be a non-NULL ISO-formatted
    string so the audit trail is chronologically queryable.
    """
    monkeypatch.setattr(db_module, "DB_FILE", migrated_db)
    db_module.set_symphony_live_mode("ts-test-sym", live=1, operator="tester")

    conn = sqlite3.connect(migrated_db)
    try:
        row = conn.execute(
            "SELECT ts_utc FROM config_audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None and row[0] is not None, (
        "config_audit_log ts_utc must not be NULL."
    )
    ts = row[0]
    # A minimal ISO string must include at least 'YYYY-MM-DD' (10 chars).
    assert len(ts) >= 10 and "-" in ts, (
        f"ts_utc must be an ISO date/datetime string; got {ts!r}."
    )


# ---------------------------------------------------------------------------
# A1: live_mode does NOT appear in the parameters JSON blob
# ---------------------------------------------------------------------------


def test_live_mode_absent_from_parameters_json_blob(migrated_db, monkeypatch):
    """
    A1: live_mode must NOT be a key in the parameters TEXT column (the JSON blob
    the autotuner reads and writes).

    live_mode is a separate column — placing it inside the parameters blob would
    make it visible to Optuna's trial suggestion loop, violating the operator-only
    contract.

    Fixture: autotuner_lock_cases[0] (parameters_json_no_live_mode).
    """
    assert _FIXTURE_PATH.is_file(), f"Fixture missing: {_FIXTURE_PATH}"
    fx = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    case = fx["autotuner_lock_cases"][0]
    forbidden_key = case["parameters_key_must_be_absent"]

    monkeypatch.setattr(db_module, "DB_FILE", migrated_db)
    save_symphony_strategy("autotuner-sym", db_module.DEFAULT_STRATEGY, db_module.DEFAULT_LOCKED_VARS)

    conn = sqlite3.connect(migrated_db)
    try:
        row = conn.execute(
            "SELECT parameters FROM symphony_strategies WHERE symphony_name = ?",
            (db_module.normalize_name("autotuner-sym"),),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "save_symphony_strategy did not write a row."
    params = json.loads(row[0])
    assert forbidden_key not in params, (
        f"save_symphony_strategy wrote '{forbidden_key}' into the parameters JSON blob. "
        "live_mode must be a separate column — never inside the tuning parameter blob. "
        "If it were inside, the autotuner could tune it like any other parameter, "
        "violating the operator-only contract."
    )


# ---------------------------------------------------------------------------
# A2: save_symphony_strategy does NOT reset live_mode
# ---------------------------------------------------------------------------


def test_save_symphony_strategy_does_not_reset_live_mode(migrated_db, monkeypatch):
    """
    A2: save_symphony_strategy (INSERT OR REPLACE) must NOT reset live_mode to 0
    when it overwrites a symphony_strategies row.

    The autotuner calls save_symphony_strategy after every walk-forward run. If
    INSERT OR REPLACE reset live_mode, any symphony that an operator enabled for
    live trading would silently flip back to dry-run after each autotune — a
    silent real-money safety regression.

    Implementation requirement: save_symphony_strategy must use a targeted UPDATE
    of parameters and locked_vars only, OR the INSERT OR REPLACE must preserve
    the existing live_mode via a subselect/COALESCE. The exact approach is the
    implementer's choice; what matters is the observable contract.
    """
    monkeypatch.setattr(db_module, "DB_FILE", migrated_db)
    save_symphony_strategy("persist-sym", db_module.DEFAULT_STRATEGY, db_module.DEFAULT_LOCKED_VARS)

    # Operator enables live_mode.
    db_module.set_symphony_live_mode("persist-sym", live=1, operator="admin")
    assert db_module.get_symphony_live_mode("persist-sym") == 1, "Pre-condition: live_mode must be 1."

    # Autotuner writes new params — save_symphony_strategy called again.
    updated_params = {**db_module.DEFAULT_STRATEGY, "TRIGGER_THRESHOLD_PCT": 12.0}
    save_symphony_strategy("persist-sym", updated_params, db_module.DEFAULT_LOCKED_VARS)

    live_after = db_module.get_symphony_live_mode("persist-sym")
    assert live_after == 1, (
        f"save_symphony_strategy reset live_mode to {live_after!r} after re-writing params. "
        "live_mode must survive a save_symphony_strategy call. "
        "If INSERT OR REPLACE is used, it replaces the entire row — "
        "the live_mode column value will be lost unless explicitly preserved. "
        "Fix: change to INSERT OR IGNORE + UPDATE parameters/locked_vars, "
        "or include live_mode in the INSERT via COALESCE(existing, 0)."
    )


# ---------------------------------------------------------------------------
# L1: get_symphony_live_mode does not block
# ---------------------------------------------------------------------------


def test_get_symphony_live_mode_does_not_block(migrated_db, monkeypatch):
    """
    L1: get_symphony_live_mode must complete in < 50ms.

    Architecture constraint 1: no blocking I/O on the 1-minute execution path.
    The live_mode read piggybacks the existing symphony_strat SELECT — it must
    not introduce any additional round-trips, sleeping, or heavy computation.

    Tolerance: 50ms — generous for a single-row SELECT + SQLite I/O overhead.
    """
    monkeypatch.setattr(db_module, "DB_FILE", migrated_db)
    save_symphony_strategy("latency-sym", db_module.DEFAULT_STRATEGY, db_module.DEFAULT_LOCKED_VARS)

    start = time.perf_counter()
    db_module.get_symphony_live_mode("latency-sym")
    elapsed = time.perf_counter() - start

    assert elapsed < 0.05, (
        f"get_symphony_live_mode took {elapsed:.3f}s — exceeds 50ms budget. "
        "Architecture constraint 1: no blocking I/O on the 1-minute execution path."
    )


# ---------------------------------------------------------------------------
# R1: get_symphony_strategy returns a 'live_mode' key (exec-path reader contract)
# ---------------------------------------------------------------------------


def test_get_symphony_strategy_returns_live_mode_key(migrated_db, monkeypatch):
    """
    R1: database.get_symphony_strategy must include 'live_mode' (bool) in its
    return dict alongside 'params' and 'locked_vars'.

    The exec path at alpha_bot_execution.py:1152 calls get_symphony_strategy once
    per symphony per cycle. Per PM ruling 2026-06-02: the implementer reads
    symphony_live_mode = symphony_strat.get("live_mode", False) — NO additional
    DB query. This removes the need for a separate get_symphony_live_mode call on
    the hot 1-min path.

    Implementation requirement: get_symphony_strategy must SELECT live_mode from
    symphony_strategies alongside parameters and locked_vars, and include it in
    the returned dict as a bool (0/False → dry-run, 1/True → live).
    """
    monkeypatch.setattr(db_module, "DB_FILE", migrated_db)
    save_symphony_strategy("r1-sym", db_module.DEFAULT_STRATEGY, db_module.DEFAULT_LOCKED_VARS)

    result = db_module.get_symphony_strategy("r1-sym")

    assert "live_mode" in result, (
        f"get_symphony_strategy must include 'live_mode' in its return dict. "
        f"Keys present: {list(result.keys())}. "
        "Implementation: SELECT parameters, locked_vars, live_mode FROM symphony_strategies "
        "and include 'live_mode': bool(row[2]) in the returned dict. "
        "The exec path reads symphony_strat.get('live_mode', False) — no second query."
    )


# ---------------------------------------------------------------------------
# R2: get_symphony_strategy returns live_mode=False for a new/unconfigured symphony
# ---------------------------------------------------------------------------


def test_get_symphony_strategy_returns_live_mode_false_for_new_symphony(migrated_db, monkeypatch):
    """
    R2: get_symphony_strategy must return live_mode=False (falsy) for a symphony
    that has no row yet — the auto-insert via DEFAULT_STRATEGY path must also
    default to dry-run (arch rule 4).

    This tests the no-row branch of get_symphony_strategy (lines ~450-456 in
    database.py), which calls save_symphony_strategy and returns a synthetic dict.
    That synthetic dict must also carry live_mode=False.
    """
    monkeypatch.setattr(db_module, "DB_FILE", migrated_db)

    # Call on a symphony that does not yet have a row — triggers auto-insert.
    result = db_module.get_symphony_strategy("brand-new-r2-sym")

    assert "live_mode" in result, (
        "get_symphony_strategy must include 'live_mode' in its return dict even "
        "for the no-row (auto-insert) path."
    )
    assert not result["live_mode"], (
        f"get_symphony_strategy must return live_mode=False for a new symphony; "
        f"got live_mode={result['live_mode']!r}. "
        "Default-dry: unconfigured symphonies are never live (arch rule 4)."
    )


# ---------------------------------------------------------------------------
# R3: get_symphony_strategy returns live_mode=True when DB has live_mode=1
# ---------------------------------------------------------------------------


def test_get_symphony_strategy_returns_live_mode_true_when_column_is_one(migrated_db, monkeypatch):
    """
    R3: get_symphony_strategy must return live_mode=True (truthy) when the
    symphony_strategies row has live_mode=1.

    This is the critical path check: an operator-enabled symphony must appear as
    live_mode=True in the dict the exec path uses to build item["live_mode"].
    """
    monkeypatch.setattr(db_module, "DB_FILE", migrated_db)
    save_symphony_strategy("r3-live-sym", db_module.DEFAULT_STRATEGY, db_module.DEFAULT_LOCKED_VARS)
    db_module.set_symphony_live_mode("r3-live-sym", live=1, operator="admin")

    result = db_module.get_symphony_strategy("r3-live-sym")

    assert "live_mode" in result, (
        "get_symphony_strategy must include 'live_mode' in its return dict."
    )
    assert result["live_mode"], (
        f"get_symphony_strategy must return live_mode=True (truthy) when live_mode=1 "
        f"in the DB; got live_mode={result['live_mode']!r}. "
        "The exec path uses this value as item['live_mode'] in the master-switch gate."
    )


# ---------------------------------------------------------------------------
# R4: get_symphony_strategy returns live_mode=False when DB has live_mode=0
# ---------------------------------------------------------------------------


def test_get_symphony_strategy_returns_live_mode_false_when_column_is_zero(migrated_db, monkeypatch):
    """
    R4: get_symphony_strategy must return live_mode=False (falsy) when the
    symphony_strategies row has live_mode=0.

    Belt-and-suspenders: explicitly tests that 0 → False mapping is correct,
    not just that the key exists.
    """
    monkeypatch.setattr(db_module, "DB_FILE", migrated_db)
    save_symphony_strategy("r4-dry-sym", db_module.DEFAULT_STRATEGY, db_module.DEFAULT_LOCKED_VARS)
    # live_mode defaults to 0; no set_symphony_live_mode call.

    result = db_module.get_symphony_strategy("r4-dry-sym")

    assert "live_mode" in result, (
        "get_symphony_strategy must include 'live_mode' in its return dict."
    )
    assert not result["live_mode"], (
        f"get_symphony_strategy must return live_mode=False (falsy) when live_mode=0 "
        f"in the DB; got live_mode={result['live_mode']!r}."
    )


# ---------------------------------------------------------------------------
# R5: get_symphony_strategy live_mode survives save_symphony_strategy clobber
#     (head-on clobber test — the essential correctness check)
# ---------------------------------------------------------------------------


def test_get_symphony_strategy_live_mode_survives_autotuner_clobber(migrated_db, monkeypatch):
    """
    R5: The clobber-preservation head-on test.

    Steps:
      1. Create a symphony_strategies row.
      2. Operator enables live_mode=1.
      3. Autotuner writes new params via save_symphony_strategy (simulates a tune run).
      4. Assert get_symphony_strategy returns live_mode=True.

    If save_symphony_strategy used INSERT OR REPLACE (DELETE+INSERT), live_mode
    would be reset to DEFAULT (0) silently. That would flip a live symphony to
    dry-run on the next autotune — a silent real-money safety regression (arch rule 4).

    The ON CONFLICT DO UPDATE SET parameters=..., locked_vars=... (deliberately
    omitting live_mode) must preserve the operator-set value.
    """
    monkeypatch.setattr(db_module, "DB_FILE", migrated_db)
    save_symphony_strategy("clobber-sym", db_module.DEFAULT_STRATEGY, db_module.DEFAULT_LOCKED_VARS)

    # Operator enables live for this symphony.
    db_module.set_symphony_live_mode("clobber-sym", live=1, operator="admin")

    # Pre-condition: live_mode is True before the autotune write.
    pre = db_module.get_symphony_strategy("clobber-sym")
    assert pre.get("live_mode"), "Pre-condition: live_mode must be truthy after set_symphony_live_mode(1)."

    # Autotuner writes new tuned params (simulates autotuner.py:2518).
    updated_params = {**db_module.DEFAULT_STRATEGY, "TRIGGER_THRESHOLD_PCT": 12.0}
    save_symphony_strategy("clobber-sym", updated_params, db_module.DEFAULT_LOCKED_VARS)

    # Post-condition: live_mode must be UNCHANGED — still True.
    post = db_module.get_symphony_strategy("clobber-sym")
    assert "live_mode" in post, (
        "get_symphony_strategy must include 'live_mode' in its return dict after a "
        "save_symphony_strategy call."
    )
    assert post["live_mode"], (
        f"CLOBBER DETECTED: get_symphony_strategy returned live_mode={post['live_mode']!r} "
        "after save_symphony_strategy overwrote params. "
        "This is the critical correctness failure: a live symphony was silently flipped "
        "to dry-run by the autotuner's param write. "
        "Fix: save_symphony_strategy must use ON CONFLICT DO UPDATE SET "
        "parameters=excluded.parameters, locked_vars=excluded.locked_vars "
        "(live_mode deliberately omitted — preserved). "
        "INSERT OR REPLACE is DELETE+INSERT and resets live_mode to DEFAULT(0)."
    )
