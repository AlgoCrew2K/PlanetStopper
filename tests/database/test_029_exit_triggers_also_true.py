"""
RED tests — Migration 029_exit_triggers_also_true (promote also_true to a dedicated
queryable column on exit_triggers).

Context: `also_true` (which other exit layers co-fired when a protective exit wins) is
already computed by math_engine.resolve_trigger_priority, carried through the execution
queue, and serialised inside the gate_state_json TEXT blob.  Migration 029 promotes it
to a first-class column so exit-attribution analysis is clean SQL, not blob-scanning.

DESIGN (Option A — dual-storage, ruling 2026-06-01):
  also_true stays in gate_state_json blob AND is written to the new dedicated
  also_true_json column.  The two values are populated from the same source; they
  cannot diverge.  The dedicated column is the queryable source of truth for
  exit-attribution analysis; the blob copy is legacy context.  The 4 existing GREEN
  tests (test_trigger_priority_dispatch.py) assert "also_true" in gate_state and
  stay GREEN unchanged.  Do NOT pop also_true from gate_state before json.dumps.

THE 5 CHANGES (all must land before any test here can go GREEN):
  1. migrations/029_exit_triggers_also_true.sql:
       ALTER TABLE exit_triggers ADD COLUMN also_true_json TEXT NULL DEFAULT NULL;
  2. database._MIGRATION_FILES: append '029_exit_triggers_also_true.sql' after 028.
  3. database.init_db() CREATE TABLE exit_triggers: add also_true_json column inline
       (H1 dual-write mirror — fresh DBs must have the column without run_migrations).
  4. database.record_exit_trigger: add kwarg also_true: list[str] | None = None;
       INSERT writes also_true_json = json.dumps(also_true) if also_true is not None
       else None.  gate_state dict is NOT modified — blob retains its also_true key.
  5. alpha_bot_execution.py ~:1783-1797: pass also_true=item["also_true"] to
       record_exit_trigger.
  Also: get_triggers and get_recent_exit_triggers must SELECT also_true_json so
       callers get the column in the returned dicts.

Coverage (all tests FAIL RED until GREEN implementation lands):
  M1: migration file exists on disk
  M2: exit_triggers column is present in a fresh DB (init_db only)
  M3: exit_triggers column is present after migration applied to a pre-029 DB
  M4: dual-write idempotency — applying 029 twice does not raise
  M5: 029 registered in _MIGRATION_FILES after 028_autotune_runs_pbo.sql
  M6: 029 SQL uses ALTER TABLE, not CREATE TABLE
  M7: migration does not reference the optimization DB (two-DB boundary)

  R1: record_exit_trigger accepts also_true kwarg (signature check)
  R2: record_exit_trigger(also_true=["VWAP Bleed Cut","Trailing Stop"]) → column
       round-trips to '["VWAP Bleed Cut", "Trailing Stop"]' (json.loads back)
  R3: record_exit_trigger(also_true=[]) → column stores '[]'
  R4: record_exit_trigger(also_true=None) → column stores NULL
  R5: record_exit_trigger called without also_true kwarg → column stores NULL
       (backward-compat: existing callers need no change)
  R6: existing pre-029 rows (NULL also_true_json) survive the migration unharmed

  D1: also_true_json column is TEXT, NULLable, DEFAULT NULL
  D2: also_true_json column is queryable — written value survives round-trip through
       both get_triggers and get_recent_exit_triggers (the two public read paths);
       also_true key is still present in gate_state_json blob (Option A — no pop)

  RP1: get_triggers returns also_true_json in result dicts (read-path coverage)
  RP2: get_recent_exit_triggers returns also_true_json in result dicts (read-path coverage)

  L1: no blocking I/O — record_exit_trigger call with also_true on a tiny list
       completes without sleeping (json.dumps of a small list is trivial)

Fixture: tests/fixtures/database/exit_triggers_also_true/exit_triggers_also_true_schema.json
Provenance: schema-derived from mission brief + database.py:2190-2251 (record_exit_trigger).
"""

from __future__ import annotations

import inspect
import json
import pathlib
import sqlite3
import time

import pytest

import database as db_module
from database import init_db, run_migrations

# ---------------------------------------------------------------------------
# Fixture path
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    pathlib.Path(__file__).parents[1]
    / "fixtures"
    / "database"
    / "exit_triggers_also_true"
    / "exit_triggers_also_true_schema.json"
)

_MIGRATION_PATH = (
    pathlib.Path(__file__).parents[2] / "migrations" / "029_exit_triggers_also_true.sql"
)

_WORKTREE_ROOT = pathlib.Path(__file__).parents[2]


# ---------------------------------------------------------------------------
# DB isolation fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def migrated_db(tmp_path, monkeypatch):
    """Per-test isolated SQLite DB with the full migration stack applied (function-scoped)."""
    db_path = str(tmp_path / "test_029.db")
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


def _exit_triggers_columns(db_path: str) -> set[str]:
    return set(_table_columns(db_path, "exit_triggers").keys())


# ---------------------------------------------------------------------------
# M1: migration file exists
# ---------------------------------------------------------------------------


def test_migration_029_file_exists():
    """M1: migrations/029_exit_triggers_also_true.sql must exist on disk."""
    assert _MIGRATION_PATH.is_file(), (
        f"Migration file not found: {_MIGRATION_PATH}. "
        "Create migrations/029_exit_triggers_also_true.sql with: "
        "ALTER TABLE exit_triggers ADD COLUMN also_true_json TEXT NULL DEFAULT NULL;"
    )


# ---------------------------------------------------------------------------
# M2: column present after init_db() only (fresh DB, no run_migrations)
# ---------------------------------------------------------------------------


def test_fresh_db_exit_triggers_has_also_true_json_column(tmp_path, monkeypatch):
    """
    M2: A DB created via init_db() ONLY must have also_true_json on exit_triggers.

    H1 dual-write: the init_db() CREATE TABLE exit_triggers must include also_true_json
    inline so fresh deployments do not silently lack the column.

    Will be RED until init_db()'s CREATE TABLE exit_triggers block is updated.
    """
    db_path = str(tmp_path / "fresh_029.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()

    cols = _exit_triggers_columns(db_path)
    assert "also_true_json" in cols, (
        "init_db() CREATE TABLE exit_triggers is missing 'also_true_json'. "
        "Fix: add also_true_json TEXT NULL DEFAULT NULL to the CREATE TABLE block in "
        "init_db() (database.py). "
        "H1 hazard: fresh deployments will silently lack the column, causing record_exit_trigger "
        "to fail every call on a new DB."
    )


# ---------------------------------------------------------------------------
# M3: column present after migration applied to a pre-029 DB
# ---------------------------------------------------------------------------


def test_upgraded_db_gains_also_true_json_column_via_migration(tmp_path, monkeypatch):
    """
    M3: A pre-029 exit_triggers table must gain also_true_json after run_migrations().

    Builds a DB with exit_triggers lacking also_true_json, marks 029 as unapplied,
    then verifies run_migrations() adds the column.
    """
    db_path = str(tmp_path / "upgrade_029.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  migration_name TEXT PRIMARY KEY,"
        "  applied_at     TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    # exit_triggers WITHOUT also_true_json — simulates a pre-029 deployed DB.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS exit_triggers (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc           TEXT    NOT NULL,
            ts_et            TEXT    NOT NULL,
            symphony_id      TEXT    NOT NULL,
            account_id       TEXT    DEFAULT NULL,
            triggered_reason TEXT    NOT NULL,
            at_return        REAL    DEFAULT NULL,
            gate_state_json  TEXT    DEFAULT NULL,
            cycle_id         TEXT    DEFAULT NULL,
            math_mode        TEXT    DEFAULT NULL,
            port_trigger_id  TEXT    DEFAULT NULL
        )
        """
    )
    # Mark all migrations except 029 as applied.
    for name in db_module._MIGRATION_FILES:
        if name != "029_exit_triggers_also_true.sql":
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (migration_name) VALUES (?)",
                (name,),
            )
    conn.commit()
    conn.close()

    run_migrations()

    cols = _exit_triggers_columns(db_path)
    assert "also_true_json" in cols, (
        "run_migrations() did not add 'also_true_json' to exit_triggers. "
        "029_exit_triggers_also_true.sql must use: "
        "ALTER TABLE exit_triggers ADD COLUMN also_true_json TEXT NULL DEFAULT NULL;"
    )


# ---------------------------------------------------------------------------
# M4: dual-write idempotency
# ---------------------------------------------------------------------------


def test_run_migrations_is_idempotent_for_029(migrated_db):
    """
    M4: Calling run_migrations() again on a DB that already has 029 applied must not raise.
    """
    try:
        run_migrations()
        run_migrations()
    except Exception as exc:
        pytest.fail(
            f"run_migrations() raised on second/third call after 029 already applied: {exc!r}. "
            "The duplicate-column-name swallow must absorb repeated applies."
        )

    cols = _exit_triggers_columns(migrated_db)
    assert "also_true_json" in cols, (
        "also_true_json missing after repeated run_migrations() — idempotency broke the column."
    )


# ---------------------------------------------------------------------------
# M5: 029 registered in _MIGRATION_FILES after 028
# ---------------------------------------------------------------------------


def test_029_in_migration_files_after_028():
    """
    M5: '029_exit_triggers_also_true.sql' must appear in database._MIGRATION_FILES
    and must come after '028_autotune_runs_pbo.sql' (append-chronological convention).
    """
    assert "029_exit_triggers_also_true.sql" in db_module._MIGRATION_FILES, (
        "'029_exit_triggers_also_true.sql' not found in database._MIGRATION_FILES. "
        "Append it after '028_autotune_runs_pbo.sql' in the _MIGRATION_FILES list."
    )
    assert "028_autotune_runs_pbo.sql" in db_module._MIGRATION_FILES, (
        "'028_autotune_runs_pbo.sql' must be present as the predecessor entry."
    )

    idx_028 = db_module._MIGRATION_FILES.index("028_autotune_runs_pbo.sql")
    idx_029 = db_module._MIGRATION_FILES.index("029_exit_triggers_also_true.sql")
    assert idx_029 > idx_028, (
        f"'029_exit_triggers_also_true.sql' (index {idx_029}) must appear AFTER "
        f"'028_autotune_runs_pbo.sql' (index {idx_028}) in _MIGRATION_FILES. "
        "Append-chronological convention: new migrations go at the tail."
    )


# ---------------------------------------------------------------------------
# M6: migration uses ALTER TABLE, not CREATE TABLE
# ---------------------------------------------------------------------------


def test_029_migration_uses_alter_table_not_create_table():
    """
    M6: 029_exit_triggers_also_true.sql must use ALTER TABLE ADD COLUMN, not CREATE TABLE.

    exit_triggers already exists from migration 005.  A CREATE TABLE would conflict.
    """
    assert _MIGRATION_PATH.is_file(), f"Migration file missing: {_MIGRATION_PATH}"
    sql = _MIGRATION_PATH.read_text(encoding="utf-8")
    sql_upper = sql.upper()

    assert "ALTER TABLE" in sql_upper, (
        "029_exit_triggers_also_true.sql must use ALTER TABLE ADD COLUMN. "
        "exit_triggers already exists from migration 005."
    )

    import re

    create_exit_triggers = re.search(
        r"CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?exit_triggers",
        sql_upper,
    )
    assert create_exit_triggers is None, (
        "029_exit_triggers_also_true.sql must NOT use CREATE TABLE [IF NOT EXISTS] exit_triggers. "
        "Use ALTER TABLE ADD COLUMN only."
    )


# ---------------------------------------------------------------------------
# M7: migration does not reference the optimization DB (two-DB boundary)
# ---------------------------------------------------------------------------


def test_029_migration_does_not_reference_opt_db():
    """
    M7: 029_exit_triggers_also_true.sql must not reference alphabot_opt or .opt.db.

    Architecture constraint 3: exit_triggers lives in the state DB only.
    """
    assert _MIGRATION_PATH.is_file(), f"Migration file missing: {_MIGRATION_PATH}"
    sql_lower = _MIGRATION_PATH.read_text(encoding="utf-8").lower()

    assert "alphabot_opt" not in sql_lower, (
        "029 references 'alphabot_opt' — exit_triggers is state-DB only (two-DB boundary)."
    )
    assert "opt.db" not in sql_lower, "029 references 'opt.db' — state DB only."


# ---------------------------------------------------------------------------
# D1: also_true_json column is TEXT, NULLable, DEFAULT NULL
# ---------------------------------------------------------------------------


def test_also_true_json_column_schema_is_text_nullable_default_null(migrated_db):
    """
    D1: also_true_json must be TEXT, NULLable (notnull=0), and DEFAULT NULL.

    Project additive-first rule: additive columns must be NULLable + DEFAULT NULL —
    a non-NULL default would implicitly alter pre-existing exit_triggers rows.
    """
    col_info = _table_columns(migrated_db, "exit_triggers")
    assert "also_true_json" in col_info, (
        "also_true_json column not found on exit_triggers. Migration 029 must add it."
    )
    info = col_info["also_true_json"]

    assert info["notnull"] == 0, (
        f"also_true_json must be NULLable (notnull=0); got notnull={info['notnull']}. "
        "Project additive-first rule: new columns on existing tables must be NULLable."
    )

    dflt = info["dflt_value"]
    assert dflt is None or dflt == "NULL", (
        f"also_true_json must have DEFAULT NULL; got dflt_value={dflt!r}. "
        "A non-NULL default would silently modify pre-existing exit_triggers rows."
    )

    # SQLite TEXT type — allow upper or lower case as SQLite is flexible here
    assert "TEXT" in info["type"].upper(), (
        f"also_true_json must have affinity TEXT; got type={info['type']!r}."
    )


# ---------------------------------------------------------------------------
# R1: record_exit_trigger accepts also_true kwarg (signature check)
# ---------------------------------------------------------------------------


def test_record_exit_trigger_accepts_also_true_kwarg():
    """
    R1: database.record_exit_trigger must accept an also_true keyword argument.

    inspect.signature is used rather than calling the function so the test does
    not require a live DB connection for the signature check alone.
    """
    assert hasattr(db_module, "record_exit_trigger"), "database.record_exit_trigger does not exist."
    sig = inspect.signature(db_module.record_exit_trigger)
    assert "also_true" in sig.parameters, (
        "database.record_exit_trigger does not have an 'also_true' parameter. "
        "Add: also_true: list[str] | None = None"
    )

    # Default must be None (backward-compat: callers that omit also_true get NULL).
    param = sig.parameters["also_true"]
    assert param.default is None, (
        f"also_true parameter must default to None; got {param.default!r}. "
        "Existing callers must not need updating."
    )


# ---------------------------------------------------------------------------
# R2: round-trip with two co-fires
# ---------------------------------------------------------------------------


def test_record_exit_trigger_also_true_multi_cofire_round_trips(tmp_path, monkeypatch):
    """
    R2: record_exit_trigger(also_true=["VWAP Bleed Cut","Trailing Stop"]) must store
    a JSON array that round-trips back to the original list via json.loads.

    Fixture: fixture["round_trip_cases"][0]
    """
    assert _FIXTURE_PATH.is_file(), f"Fixture missing: {_FIXTURE_PATH}"
    fx = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    case = next(c for c in fx["round_trip_cases"] if c["id"] == "multi_cofires")

    db_path = str(tmp_path / "r2_also_true.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()

    db_module.record_exit_trigger(
        symphony_id="sym-r2-test",
        triggered_reason="VWAP Breakdown",
        also_true=case["also_true_input"],
        ts_utc="2026-06-01T15:00:00Z",
        ts_et="2026-06-01T11:00:00",
    )

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT also_true_json FROM exit_triggers WHERE symphony_id = 'sym-r2-test'"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "record_exit_trigger did not write a row to exit_triggers."
    raw = row[0]
    assert raw is not None, (
        "also_true_json must not be NULL when also_true=['VWAP Bleed Cut','Trailing Stop'] "
        "is passed. Expected a non-NULL JSON array."
    )

    parsed = json.loads(raw)
    assert isinstance(parsed, list), f"also_true_json must parse to a list; got {type(parsed)}"
    assert set(parsed) == set(case["also_true_input"]), (
        f"also_true round-trip mismatch: wrote {case['also_true_input']!r}, "
        f"read back {parsed!r}. "
        "record_exit_trigger must store json.dumps(also_true) in also_true_json."
    )


# ---------------------------------------------------------------------------
# R3: empty co-fire list stores '[]'
# ---------------------------------------------------------------------------


def test_record_exit_trigger_also_true_empty_list_stores_empty_json(tmp_path, monkeypatch):
    """
    R3: record_exit_trigger(also_true=[]) must store '[]', not NULL.

    An empty list means a trigger fired cleanly with no co-fires.  Storing NULL
    would conflate "no co-fires" with "also_true not passed" — these are distinct.

    Fixture: fixture["round_trip_cases"][1] (id="empty_cofires")
    """
    assert _FIXTURE_PATH.is_file(), f"Fixture missing: {_FIXTURE_PATH}"
    fx = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    case = next(c for c in fx["round_trip_cases"] if c["id"] == "empty_cofires")

    db_path = str(tmp_path / "r3_also_true.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()

    db_module.record_exit_trigger(
        symphony_id="sym-r3-empty",
        triggered_reason="Trailing Stop",
        also_true=case["also_true_input"],  # []
        ts_utc="2026-06-01T15:01:00Z",
        ts_et="2026-06-01T11:01:00",
    )

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT also_true_json FROM exit_triggers WHERE symphony_id = 'sym-r3-empty'"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "record_exit_trigger did not write a row."
    raw = row[0]
    expected_json = case["expected_json"]  # "[]"
    assert raw == expected_json, (
        f"also_true_json must be {expected_json!r} when also_true=[]; got {raw!r}. "
        "An empty list and None are semantically distinct: "
        "[] = single winner, no co-fires; NULL = also_true not provided (legacy)."
    )


# ---------------------------------------------------------------------------
# R4: None stores NULL
# ---------------------------------------------------------------------------


def test_record_exit_trigger_also_true_none_stores_null(tmp_path, monkeypatch):
    """
    R4: record_exit_trigger(also_true=None) must store NULL in also_true_json.

    Fixture: fixture["round_trip_cases"][2] (id="null_cofires")
    """
    assert _FIXTURE_PATH.is_file(), f"Fixture missing: {_FIXTURE_PATH}"
    fx = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    case = next(c for c in fx["round_trip_cases"] if c["id"] == "null_cofires")

    db_path = str(tmp_path / "r4_also_true.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()

    db_module.record_exit_trigger(
        symphony_id="sym-r4-null",
        triggered_reason="Take-Profit",
        also_true=case["also_true_input"],  # None
        ts_utc="2026-06-01T15:02:00Z",
        ts_et="2026-06-01T11:02:00",
    )

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT also_true_json FROM exit_triggers WHERE symphony_id = 'sym-r4-null'"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "record_exit_trigger did not write a row."
    assert row[0] is None, (
        f"also_true_json must be NULL when also_true=None; got {row[0]!r}. "
        "None means no co-fire information provided (maps to SQL NULL, not '[]')."
    )


# ---------------------------------------------------------------------------
# R5: omitting also_true kwarg stores NULL (backward-compat)
# ---------------------------------------------------------------------------


def test_record_exit_trigger_omit_also_true_stores_null(tmp_path, monkeypatch):
    """
    R5: Calling record_exit_trigger WITHOUT also_true must store NULL (backward-compat).

    All existing callers omit also_true — they must not break after the signature change.
    The default value of None must produce NULL in the column.
    """
    db_path = str(tmp_path / "r5_also_true.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()

    # No also_true kwarg — this is the pre-029 call pattern
    db_module.record_exit_trigger(
        symphony_id="sym-r5-omit",
        triggered_reason="VWAP Bleed Cut",
        ts_utc="2026-06-01T15:03:00Z",
        ts_et="2026-06-01T11:03:00",
    )

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT also_true_json FROM exit_triggers WHERE symphony_id = 'sym-r5-omit'"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "record_exit_trigger did not write a row."
    assert row[0] is None, (
        f"also_true_json must be NULL when also_true kwarg is omitted; got {row[0]!r}. "
        "Backward-compat: existing callers that omit also_true must not break."
    )


# ---------------------------------------------------------------------------
# R6: pre-029 rows survive the migration with NULL also_true_json
# ---------------------------------------------------------------------------


def test_pre_029_rows_survive_migration_with_null_also_true_json(tmp_path, monkeypatch):
    """
    R6: Rows written to exit_triggers BEFORE migration 029 is applied must survive
    with also_true_json = NULL after the migration runs.

    Verifies the migration is purely additive — no data loss, no coercion.
    """
    db_path = str(tmp_path / "r6_survival.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)

    # Build a pre-029 exit_triggers without also_true_json.
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  migration_name TEXT PRIMARY KEY,"
        "  applied_at     TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS exit_triggers (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_utc           TEXT    NOT NULL,
            ts_et            TEXT    NOT NULL,
            symphony_id      TEXT    NOT NULL,
            account_id       TEXT    DEFAULT NULL,
            triggered_reason TEXT    NOT NULL,
            at_return        REAL    DEFAULT NULL,
            gate_state_json  TEXT    DEFAULT NULL,
            cycle_id         TEXT    DEFAULT NULL,
            math_mode        TEXT    DEFAULT NULL,
            port_trigger_id  TEXT    DEFAULT NULL
        )
        """
    )
    # Insert a pre-029 row.
    conn.execute(
        "INSERT INTO exit_triggers (ts_utc, ts_et, symphony_id, triggered_reason) "
        "VALUES (?, ?, ?, ?)",
        ("2026-01-01T15:00:00Z", "2026-01-01T11:00:00", "sym-legacy", "Trailing Stop"),
    )
    conn.commit()
    # Mark all migrations except 029 as applied.
    for name in db_module._MIGRATION_FILES:
        if name != "029_exit_triggers_also_true.sql":
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
            "SELECT triggered_reason, also_true_json FROM exit_triggers "
            "WHERE symphony_id = 'sym-legacy'"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "Pre-029 row not found after migration — data loss."
    triggered_reason, also_true_json = row

    assert triggered_reason == "Trailing Stop", (
        f"Pre-029 row triggered_reason corrupted after migration: {triggered_reason!r}"
    )
    assert also_true_json is None, (
        f"Pre-029 row also_true_json must be NULL (DEFAULT NULL applies); got {also_true_json!r}."
    )


# ---------------------------------------------------------------------------
# D2: also_true_json column is the queryable source of truth AND gate_state_json
#     blob STILL contains the also_true key (Option A — dual-storage, no pop)
# ---------------------------------------------------------------------------


def test_also_true_column_populated_and_blob_retains_also_true_key(tmp_path, monkeypatch):
    """
    D2: Option A dual-storage contract — two assertions in one scenario:

    (a) also_true_json column is populated from the also_true kwarg.
    (b) gate_state_json blob still contains an "also_true" key — do NOT pop.

    Rationale: the 4 existing GREEN tests in test_trigger_priority_dispatch.py
    (lines 345/484/604/658) assert "also_true" in gate_state; they must stay GREEN
    without modification.  The dedicated column adds queryability without removing
    the blob copy.  The two values are populated from the same source and cannot
    diverge.

    Fixture: fixture["gate_state_blob_design"]["also_true_popped_before_json_dumps"] == false
    """
    assert _FIXTURE_PATH.is_file(), f"Fixture missing: {_FIXTURE_PATH}"
    fx = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    design = fx["gate_state_blob_design"]
    assert design["also_true_popped_before_json_dumps"] is False, (
        "Fixture design mismatch: also_true_popped_before_json_dumps must be false "
        "(Option A — dual-storage, no pop)."
    )
    assert design["column_is_queryable_source"] is True

    db_path = str(tmp_path / "d2_dual_storage.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()

    # Pass gate_state with also_true key — as alpha_bot_execution does at line 1794.
    gate_state_with_also_true = {
        "high_water_mark": 5.0,
        "vwap_ticks": 3,
        "vwap_bleed_ticks": 2,
        "mc_prob": 50.0,
        "symphony_vol": 0.012,
        "also_true": ["VWAP Bleed Cut"],
    }

    db_module.record_exit_trigger(
        symphony_id="sym-d2-dual",
        triggered_reason="VWAP Breakdown",
        also_true=["VWAP Bleed Cut"],
        gate_state=gate_state_with_also_true,
        ts_utc="2026-06-01T15:04:00Z",
        ts_et="2026-06-01T11:04:00",
    )

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT gate_state_json, also_true_json FROM exit_triggers "
            "WHERE symphony_id = 'sym-d2-dual'"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "record_exit_trigger did not write a row."
    gate_state_raw, also_true_raw = row

    # (a) Dedicated column carries the co-fire data.
    assert also_true_raw is not None, (
        "also_true_json must be non-NULL when also_true=['VWAP Bleed Cut'] is passed."
    )
    parsed_col = json.loads(also_true_raw)
    assert "VWAP Bleed Cut" in parsed_col, (
        f"also_true_json column must contain 'VWAP Bleed Cut'; got {parsed_col!r}"
    )

    # (b) Blob STILL contains "also_true" key — Option A: do NOT pop.
    assert gate_state_raw is not None, (
        "gate_state_json blob must be populated when gate_state dict is passed."
    )
    blob = json.loads(gate_state_raw)
    assert "also_true" in blob, (
        f"gate_state_json blob must still contain 'also_true' key (Option A dual-storage — "
        f"do NOT pop before json.dumps). "
        f"The 4 existing GREEN tests in test_trigger_priority_dispatch.py assert "
        f"'also_true' in gate_state and must stay GREEN. "
        f"Blob keys found: {list(blob.keys())}"
    )
    assert blob["also_true"] == ["VWAP Bleed Cut"], (
        f"Blob also_true value must match input; got {blob['also_true']!r}"
    )


# ---------------------------------------------------------------------------
# RP1: get_triggers returns also_true_json in result dicts
# ---------------------------------------------------------------------------


def test_get_triggers_returns_also_true_json_in_result(tmp_path, monkeypatch):
    """
    RP1: database.get_triggers must include also_true_json in the returned dicts.

    get_triggers is the primary read path for exit-attribution analysis.  If it
    does not SELECT also_true_json, callers get dicts without the column and the
    entire promotion is invisible to consumers.

    Implementation requirement: the SELECT list in get_triggers must include
    also_true_json alongside the existing columns.
    """
    db_path = str(tmp_path / "rp1_get_triggers.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()

    db_module.record_exit_trigger(
        symphony_id="sym-rp1",
        triggered_reason="Trailing Stop",
        also_true=["VWAP Bleed Cut"],
        ts_utc="2026-06-01T15:10:00Z",
        ts_et="2026-06-01T11:10:00",
    )

    results = db_module.get_triggers(symphony_id="sym-rp1")

    assert len(results) == 1, f"Expected 1 result from get_triggers; got {len(results)}."
    row = results[0]

    assert "also_true_json" in row, (
        f"get_triggers result dict must include 'also_true_json' key. "
        f"Keys present: {list(row.keys())}. "
        "Fix: add also_true_json to the SELECT list in database.get_triggers "
        "(database.py:2783-2786)."
    )

    raw = row["also_true_json"]
    assert raw is not None, (
        "also_true_json must be non-NULL in get_triggers result when "
        "also_true=['VWAP Bleed Cut'] was written."
    )
    parsed = json.loads(raw)
    assert "VWAP Bleed Cut" in parsed, (
        f"also_true_json from get_triggers must contain 'VWAP Bleed Cut'; got {parsed!r}"
    )


# ---------------------------------------------------------------------------
# RP2: get_recent_exit_triggers returns also_true_json in result dicts
# ---------------------------------------------------------------------------


def test_get_recent_exit_triggers_returns_also_true_json_in_result(tmp_path, monkeypatch):
    """
    RP2: database.get_recent_exit_triggers must include also_true_json in the
    returned dicts.

    get_recent_exit_triggers is used by the /api/triggers dashboard route.
    If it does not SELECT also_true_json, the dashboard (and any future
    SIP re-decision tooling) cannot access co-fire attribution without a
    separate query.

    Implementation requirement: the SELECT list in get_recent_exit_triggers must
    include also_true_json alongside the existing columns.
    """
    db_path = str(tmp_path / "rp2_get_recent.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()

    db_module.record_exit_trigger(
        symphony_id="sym-rp2",
        triggered_reason="VWAP Breakdown",
        also_true=["Take-Profit", "Trailing Stop"],
        ts_utc="2026-06-01T15:11:00Z",
        ts_et="2026-06-01T11:11:00",
    )

    results = db_module.get_recent_exit_triggers(limit=10)

    matching = [r for r in results if r.get("symphony_id") == "sym-rp2"]
    assert len(matching) == 1, (
        f"Expected 1 matching row from get_recent_exit_triggers; got {len(matching)}."
    )
    row = matching[0]

    assert "also_true_json" in row, (
        f"get_recent_exit_triggers result dict must include 'also_true_json' key. "
        f"Keys present: {list(row.keys())}. "
        "Fix: add also_true_json to the SELECT list in database.get_recent_exit_triggers "
        "(database.py:2262-2265)."
    )

    raw = row["also_true_json"]
    assert raw is not None, (
        "also_true_json must be non-NULL in get_recent_exit_triggers result when "
        "also_true=['Take-Profit','Trailing Stop'] was written."
    )
    parsed = json.loads(raw)
    assert set(parsed) == {"Take-Profit", "Trailing Stop"}, (
        f"also_true_json from get_recent_exit_triggers must contain the written co-fires; "
        f"got {parsed!r}"
    )


# ---------------------------------------------------------------------------
# L1: no blocking I/O — record_exit_trigger with also_true completes quickly
# ---------------------------------------------------------------------------


def test_record_exit_trigger_with_also_true_does_not_block(tmp_path, monkeypatch):
    """
    L1: record_exit_trigger with a small also_true list must complete in well under
    100ms (json.dumps of a list of 3 strings is ~microseconds; anything longer
    implies unexpected I/O or serialisation overhead).

    Architecture constraint 1: no blocking I/O on the 1-minute execution path.
    This test pins the non-regression contract: if also_true handling introduces
    blocking (e.g. a round-trip DB read, a sleep, or O(n) scan), the budget fails.

    Tolerance: 100ms — generous enough for disk I/O on the SQLite write itself,
    strict enough to catch accidental blocking work in the also_true path.
    """
    db_path = str(tmp_path / "l1_latency.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()

    also_true_payload = ["VWAP Bleed Cut", "Trailing Stop"]

    start = time.perf_counter()
    db_module.record_exit_trigger(
        symphony_id="sym-l1-latency",
        triggered_reason="VWAP Breakdown",
        also_true=also_true_payload,
        ts_utc="2026-06-01T15:05:00Z",
        ts_et="2026-06-01T11:05:00",
    )
    elapsed = time.perf_counter() - start

    # 100ms is generous; json.dumps of 3 strings should be sub-millisecond.
    # If this trips, the also_true path is doing unexpected blocking work.
    assert elapsed < 0.1, (
        f"record_exit_trigger with also_true took {elapsed:.3f}s — exceeds 100ms budget. "
        "Architecture constraint 1: no blocking I/O on the 1-minute execution path. "
        "json.dumps of a small list must not introduce blocking or O(n) DB work."
    )
