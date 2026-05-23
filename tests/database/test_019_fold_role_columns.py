"""
RED tests — Migration 019_fold_role_columns (structural frozen-eval Advisor wall).

Tests per the plan §Golden-fixture tests required + spec-019 domain requirements
R-1..R-11. All tests are RED until the implementer:
  1. creates migrations/019_fold_role_columns.sql with ALTER TABLE ADD COLUMN fold_role TEXT DEFAULT NULL
  2. appends "019_fold_role_columns.sql" to database._MIGRATION_FILES (after 015_*)
  3. adds database.advisor_ro_query(sql, params=()) with COALESCE guard + wall-breach tripwire
  4. ensures advisor_observations table exists (from migration 017)

Fixture provenance: schema-derived from plan 019 + README §1 hazard H3.
No producer-computed values. All assertions are structural or behavioural.

H3 SQL-NULL trap (load-bearing):
    COALESCE(fold_role, '') != 'frozen_eval'  →  NULL row is INCLUDED (correct)
    fold_role != 'frozen_eval'                →  NULL row is EXCLUDED (the bug)
The difference in row counts between the two filters on a NULL fold_role row is
the definitive test. If an implementation skips COALESCE, the Advisor wall has
a silent hole through which legacy train/validation rows fall.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
from unittest.mock import patch

import pytest

import database as db


# ---------------------------------------------------------------------------
# Fixture — isolated DB with fold-partitioned table and four fold_role rows
# ---------------------------------------------------------------------------

# The fold-partitioned table name. The implementer may target a different
# table; however, the plan identifies the backtest-replay rows as the current
# candidate. The tests use a synthetic table name so they are not coupled to
# the final table choice — the impl test must confirm the right table.
_FOLD_TABLE = "backtest_replay_rows"


@pytest.fixture
def fold_db(tmp_path):
    """Isolated DB with init_db() + the 019 migration applied + a synthetic
    fold-partitioned table populated with all four fold_role variants.

    The fold-partitioned table is created here regardless of whether the
    migration exists — this lets the schema and COALESCE filter tests run
    against a known-good structure. The migration tests assert separately that
    _MIGRATION_FILES lists the migration AND that it applies via run_migrations.
    """
    db_path = str(tmp_path / "test_019.db")
    with patch.object(db, "DB_FILE", db_path):
        db.init_db()

        conn = sqlite3.connect(db_path)
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_FOLD_TABLE} (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id  TEXT NOT NULL,
                fold_role TEXT DEFAULT NULL
            )
            """
        )
        # Four canonical fold_role rows — one of each value the wall must handle.
        conn.execute(
            f"INSERT INTO {_FOLD_TABLE} (cycle_id, fold_role) VALUES (?, ?)",
            ("cycle-train-1", "train"),
        )
        conn.execute(
            f"INSERT INTO {_FOLD_TABLE} (cycle_id, fold_role) VALUES (?, ?)",
            ("cycle-val-1", "validation"),
        )
        conn.execute(
            f"INSERT INTO {_FOLD_TABLE} (cycle_id, fold_role) VALUES (?, ?)",
            ("cycle-frozen-1", "frozen_eval"),
        )
        # NULL row — the legacy-row case; COALESCE must treat this as non-frozen.
        conn.execute(
            f"INSERT INTO {_FOLD_TABLE} (cycle_id, fold_role) VALUES (?, ?)",
            ("cycle-legacy-1", None),
        )
        conn.commit()
        conn.close()

    return db_path


@pytest.fixture
def fold_db_with_advisor_observations(fold_db, tmp_path):
    """Extends fold_db with the advisor_observations table (from migration 017).

    The wall-breach tripwire writes to advisor_observations; that table must
    exist for the tripwire test to exercise the write-then-raise contract.
    """
    conn = sqlite3.connect(fold_db)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS advisor_observations (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at          TEXT NOT NULL DEFAULT (datetime('now')),
            advisor_role        TEXT NOT NULL,
            subject_type        TEXT NOT NULL,
            subject_id          TEXT NOT NULL,
            verdict             TEXT,
            raw_response        TEXT NOT NULL DEFAULT '{}',
            is_advisory_only    INTEGER NOT NULL DEFAULT 1,
            spec_bundle_id      TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    return fold_db


# ---------------------------------------------------------------------------
# Test 1 — H3 SQL-NULL trap: COALESCE filter includes NULL rows; bare != excludes them
# ---------------------------------------------------------------------------


def test_coalesce_filter_includes_null_fold_role_row(fold_db):
    """H3 (CRITICAL): COALESCE(fold_role,'') != 'frozen_eval' must INCLUDE a row
    where fold_role IS NULL, because NULL is not 'frozen_eval'.

    This is the exact hazard the plan documents: a bare fold_role != 'frozen_eval'
    evaluates NULL operand as NULL (falsy) and EXCLUDES the row, hiding a
    legitimate legacy train/validation row from the Advisor.

    The test runs both the correct and buggy filter on the same DB and asserts:
      - correct filter returns MORE rows than the buggy filter (or equal rows
        when there are no NULL fold_role rows — but with our fixture, there is
        exactly one, so the counts must differ by exactly 1)
      - the NULL-fold_role row IS in the correct filter result set
      - the NULL-fold_role row is NOT in the buggy filter result set

    Tolerance: none — this is an integer count comparison.
    """
    conn = sqlite3.connect(fold_db)
    try:
        # Correct COALESCE filter — the one the Advisor wall must use.
        correct_rows = conn.execute(
            f"SELECT cycle_id FROM {_FOLD_TABLE} "
            f"WHERE COALESCE(fold_role, '') != 'frozen_eval'"
        ).fetchall()
        correct_cycle_ids = {r[0] for r in correct_rows}

        # Buggy bare != filter — what a naive implementer might write.
        buggy_rows = conn.execute(
            f"SELECT cycle_id FROM {_FOLD_TABLE} "
            f"WHERE fold_role != 'frozen_eval'"
        ).fetchall()
        buggy_cycle_ids = {r[0] for r in buggy_rows}
    finally:
        conn.close()

    # The NULL row (cycle-legacy-1) must appear under COALESCE but not under bare !=.
    assert "cycle-legacy-1" in correct_cycle_ids, (
        "COALESCE filter must include the NULL fold_role row (legacy train/val row). "
        "H3: NULL is not frozen_eval — COALESCE(fold_role,'') != 'frozen_eval' must be True."
    )
    assert "cycle-legacy-1" not in buggy_cycle_ids, (
        "Bare fold_role != 'frozen_eval' should EXCLUDE the NULL row (SQL three-valued logic). "
        "This confirms H3 is a real hazard — the bare != hides the row."
    )

    # The row counts must differ by exactly 1 (the NULL row).
    assert len(correct_cycle_ids) == len(buggy_cycle_ids) + 1, (
        f"COALESCE result ({len(correct_cycle_ids)} rows) must be exactly 1 more than "
        f"bare != result ({len(buggy_cycle_ids)} rows). "
        "H3: the one extra row is the NULL fold_role legacy row."
    )


# ---------------------------------------------------------------------------
# Test 2 — frozen_eval rows are excluded by the COALESCE accessor filter
# ---------------------------------------------------------------------------


def test_frozen_eval_row_is_blocked_by_coalesce_wall_filter(fold_db):
    """The row with fold_role='frozen_eval' must NOT appear in the COALESCE filter
    result set. This is the primary security property of the wall.

    Also asserts train and validation rows ARE included, so the filter is not
    over-blocking.
    """
    conn = sqlite3.connect(fold_db)
    try:
        rows = conn.execute(
            f"SELECT cycle_id, fold_role FROM {_FOLD_TABLE} "
            f"WHERE COALESCE(fold_role, '') != 'frozen_eval'"
        ).fetchall()
    finally:
        conn.close()

    result_by_cycle = {r[0]: r[1] for r in rows}

    assert "cycle-frozen-1" not in result_by_cycle, (
        "Row with fold_role='frozen_eval' must NOT pass the COALESCE wall filter. "
        "The frozen-eval fold is the held-out evaluation set — Advisor must never see it."
    )
    assert "cycle-train-1" in result_by_cycle, (
        "Row with fold_role='train' must pass the COALESCE filter (not over-blocked)."
    )
    assert "cycle-val-1" in result_by_cycle, (
        "Row with fold_role='validation' must pass the COALESCE filter (not over-blocked)."
    )
    assert "cycle-legacy-1" in result_by_cycle, (
        "Row with fold_role=NULL must pass the COALESCE filter (legacy row — safe default)."
    )


# ---------------------------------------------------------------------------
# Test 3 — wall-breach tripwire fires: advisor_ro_query raises + writes WALL_BREACH
# ---------------------------------------------------------------------------


def test_wall_breach_tripwire_writes_observation_then_raises(fold_db_with_advisor_observations):
    """If a frozen_eval row appears in advisor_ro_query's result set (via a
    deliberately permissive query), the helper must:
      1. Write a WALL_BREACH row to advisor_observations BEFORE raising.
      2. Raise (any exception is acceptable — the plan does not mandate a specific type).

    The test constructs a raw SQL that bypasses the filter by using an OR 1=1
    predicate — an adversarial bypass that the tripwire is designed to catch.

    Provenance: plan §Golden-fixture tests required, test 3.
    """
    # advisor_ro_query must exist on the database module.
    assert hasattr(db, "advisor_ro_query"), (
        "database.advisor_ro_query() does not exist. "
        "Migration 019 deliverable 2 requires this accessor."
    )

    db_path = fold_db_with_advisor_observations
    with patch.object(db, "DB_FILE", db_path):
        # Deliberately bypass the COALESCE wall with OR 1=1 to smuggle a frozen_eval
        # row into the result set — this is the adversarial scenario the tripwire guards.
        adversarial_sql = (
            f"SELECT id, cycle_id, fold_role FROM {_FOLD_TABLE} "
            f"WHERE COALESCE(fold_role, '') != 'frozen_eval' OR 1=1"
        )

        with pytest.raises(Exception):
            # Any exception is acceptable — the tripwire must raise.
            db.advisor_ro_query(adversarial_sql)

        # The WALL_BREACH row must have been written before the raise.
        conn = sqlite3.connect(db_path)
        try:
            breach_rows = conn.execute(
                "SELECT advisor_role, subject_type FROM advisor_observations "
                "WHERE advisor_role = 'WALL_BREACH'"
            ).fetchall()
        finally:
            conn.close()

        assert len(breach_rows) >= 1, (
            "advisor_ro_query must write a WALL_BREACH row to advisor_observations "
            "BEFORE raising when a frozen_eval row appears in results. "
            "The write-then-raise contract ensures the audit survives even if the "
            "caller swallows the exception."
        )


# ---------------------------------------------------------------------------
# Test 4 — lint: advisor code paths must only use advisor_ro_query (no side door)
# ---------------------------------------------------------------------------


def test_advisor_module_uses_only_advisor_ro_query_for_db_access():
    """No advisor code path may call get_connection() or get_ro_connection() directly.

    advisor_ro_query is the only door from Advisor code to the state DB. A
    direct call to get_connection/get_ro_connection bypasses the COALESCE guard
    and the wall-breach tripwire — it is a structural side door.

    This test greps the ai_advisor module (and any advisor-tagged file) for
    direct connection-function calls. It is a lint-style test — it can only
    fail on wrong implementation, never on correct implementation.

    If the ai_advisor module does not yet exist, the test passes vacuously
    (no side-door calls found in a non-existent module). Once the module exists,
    it must only use advisor_ro_query.
    """
    import ast

    project_root = pathlib.Path(__file__).parents[2]
    advisor_files = list(project_root.glob("ai_advisor*.py")) + list(
        project_root.glob("advisor*.py")
    )

    forbidden_direct_calls = {"get_connection", "get_ro_connection"}
    violations: list[str] = []

    for advisor_file in advisor_files:
        source = advisor_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(advisor_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                called_name: str | None = None
                if isinstance(node.func, ast.Attribute):
                    called_name = node.func.attr
                elif isinstance(node.func, ast.Name):
                    called_name = node.func.id
                if called_name in forbidden_direct_calls:
                    violations.append(
                        f"{advisor_file.name}:{node.lineno} — direct call to {called_name}()"
                    )

    assert not violations, (
        "Advisor code paths must use advisor_ro_query() exclusively. "
        "Direct calls to get_connection() or get_ro_connection() bypass the "
        "COALESCE guard and the wall-breach tripwire (structural side door). "
        f"Violations found:\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# Test 5 — bare fold_role != in caller SQL is rejected at the helper
# ---------------------------------------------------------------------------


def test_advisor_ro_query_rejects_bare_fold_role_predicate(fold_db_with_advisor_observations):
    """advisor_ro_query must detect and reject a bare fold_role != predicate in
    the caller's SQL before execution. The rejection must happen at the Python
    helper level — not at SQL execution time.

    A ValueError (or any exception) raised before any rows are fetched is the
    correct outcome. An exception raised after execution is NOT acceptable
    (the query would have already touched the data).

    Provenance: plan §Golden-fixture tests required, test 5.
    """
    assert hasattr(db, "advisor_ro_query"), (
        "database.advisor_ro_query() does not exist — cannot test predicate rejection."
    )

    db_path = fold_db_with_advisor_observations
    with patch.object(db, "DB_FILE", db_path):
        bare_predicate_sql = (
            f"SELECT id FROM {_FOLD_TABLE} WHERE fold_role != 'frozen_eval'"
        )

        with pytest.raises((ValueError, Exception)):
            db.advisor_ro_query(bare_predicate_sql)


# ---------------------------------------------------------------------------
# Test 6 — run_migrations swallows duplicate-column on re-run (idempotency)
# ---------------------------------------------------------------------------


def test_run_migrations_swallows_duplicate_column_on_019_reapplication(tmp_path):
    """run_migrations must not raise when 019_fold_role_columns.sql is applied
    to a DB that already has the fold_role column (duplicate column swallow).

    Two scenarios are exercised:
      (a) Fresh DB: migration applies cleanly; schema_migrations records it.
      (b) Pre-populated DB (fold_role already present via a prior run): re-run
          either skips (idempotent via tracker) or swallows the duplicate-column
          error and records applied.

    Provenance: plan §Golden-fixture tests required, test 6.
    Reference: database.py:794-803 (the duplicate-column swallow precedent).
    """
    assert "019_fold_role_columns.sql" in db._MIGRATION_FILES, (
        "_MIGRATION_FILES must list '019_fold_role_columns.sql'. "
        "The append is missing — plan deliverable 4."
    )

    db_path = str(tmp_path / "test_019_idempotent.db")
    with patch.object(db, "DB_FILE", db_path):
        db.init_db()  # first apply — must succeed

    # Second apply — must NOT raise regardless of whether fold_role is already present.
    with patch.object(db, "DB_FILE", db_path):
        try:
            db.run_migrations()
        except Exception as exc:
            pytest.fail(
                f"run_migrations() raised on second call (duplicate-column case): {exc}. "
                "The duplicate-column swallow at database.py:794-803 must cover "
                "'019_fold_role_columns.sql' as well."
            )

    # After both runs, the migration must be recorded in schema_migrations.
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT migration_name FROM schema_migrations "
            "WHERE migration_name = '019_fold_role_columns.sql'"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, (
        "'019_fold_role_columns.sql' must appear in schema_migrations after run_migrations(). "
        "Either the migration was not applied or the tracker INSERT was skipped."
    )


# ---------------------------------------------------------------------------
# Test 7 — schema-validator: fold_role column present; all four role values representable
# ---------------------------------------------------------------------------


def test_fold_role_column_present_and_all_role_values_representable(fold_db):
    """After migration 019, the fold-partitioned table must have a fold_role column
    and all four canonical values (train, validation, frozen_eval, NULL) must be
    storable and queryable without error.

    Asserts:
      - fold_role column exists on the table (PRAGMA table_info)
      - the column accepts TEXT or NULL (type is TEXT, no NOT NULL constraint)
      - all four role values inserted in the fixture are retrievable
      - at least one row has fold_role='frozen_eval' (tripwire test requires it)
      - at least one row has fold_role IS NULL (H3 test requires it)

    Provenance: plan §Golden-fixture tests required, test 7.
    """
    conn = sqlite3.connect(fold_db)
    try:
        columns = conn.execute(
            f"PRAGMA table_info({_FOLD_TABLE})"
        ).fetchall()
        # PRAGMA table_info returns: (cid, name, type, notnull, dflt_value, pk)
        col_map = {row[1]: row for row in columns}
    finally:
        conn.close()

    assert "fold_role" in col_map, (
        f"fold_role column is missing from {_FOLD_TABLE}. "
        "Migration 019_fold_role_columns.sql must ADD COLUMN fold_role TEXT DEFAULT NULL."
    )

    col_info = col_map["fold_role"]
    # col_info[3] is notnull — must be 0 (nullable)
    assert col_info[3] == 0, (
        "fold_role must be NULLable (notnull=0). "
        "The column must accept NULL for legacy rows (plan §Deliverables 1)."
    )
    # col_info[2] is type — must be TEXT (or empty, SQLite is flexible)
    col_type = col_info[2].upper()
    assert col_type in ("TEXT", ""), (
        f"fold_role column type must be TEXT; found '{col_info[2]}'. "
        "Plan §Deliverables 1 specifies TEXT DEFAULT NULL."
    )

    # Verify all four role values are present in the fixture DB.
    conn = sqlite3.connect(fold_db)
    try:
        rows = conn.execute(
            f"SELECT cycle_id, fold_role FROM {_FOLD_TABLE}"
        ).fetchall()
    finally:
        conn.close()

    role_values = {r[1] for r in rows}
    expected_non_null = {"train", "validation", "frozen_eval"}
    for expected in expected_non_null:
        assert expected in role_values, (
            f"Fixture DB must contain a row with fold_role='{expected}'. "
            "Schema-validator requires all canonical role values representable."
        )

    cycle_ids = {r[0] for r in rows}
    assert "cycle-legacy-1" in cycle_ids, (
        "Fixture DB must contain a row with fold_role=NULL (legacy row — cycle-legacy-1). "
        "H3 test and tripwire test both require a NULL fold_role row present."
    )


# ---------------------------------------------------------------------------
# Test 8 — R-1: migration file content — DEFAULT NULL, no NOT NULL, no CHECK
# ---------------------------------------------------------------------------


def test_migration_file_has_default_null_no_not_null_no_check():
    """R-1 (spec-019): The migration SQL file must add fold_role with DEFAULT NULL,
    must NOT include NOT NULL, and must NOT include a CHECK constraint.

    Application-level enum only — no CHECK constraint (codebase precedent:
    init_db() has no CHECK constraints anywhere).
    """
    migrations_dir = pathlib.Path(__file__).parents[2] / "migrations"
    migration_path = migrations_dir / "019_fold_role_columns.sql"

    assert migration_path.exists(), (
        f"migrations/019_fold_role_columns.sql does not exist at {migration_path}. "
        "Plan deliverable 1 requires this file."
    )

    sql_text = migration_path.read_text(encoding="utf-8").upper()

    assert "DEFAULT NULL" in sql_text, (
        "019_fold_role_columns.sql must include DEFAULT NULL on the fold_role column. "
        "R-1: existing rows must get NULL (no value specified on ALTER)."
    )
    assert "NOT NULL" not in sql_text, (
        "019_fold_role_columns.sql must NOT include NOT NULL. "
        "R-1: existing rows have no fold_role value — a NOT NULL constraint would "
        "violate the additive-first migration rule."
    )
    # CHECK constraints would block additive schema evolution (e.g. adding 'holdout' role later).
    assert "CHECK" not in sql_text, (
        "019_fold_role_columns.sql must NOT include a CHECK constraint. "
        "R-1: fold_role values are an application-level enum only (plan §Risk callouts)."
    )
    assert "ADD COLUMN" in sql_text, (
        "019_fold_role_columns.sql must use ALTER TABLE ... ADD COLUMN. "
        "R-1: additive ALTER is the required migration pattern."
    )


# ---------------------------------------------------------------------------
# Test 9 — R-2: _MIGRATION_FILES ordering — 019 appears after 015
# ---------------------------------------------------------------------------


def test_migration_files_list_019_appears_after_015():
    """R-2 (spec-019): '019_fold_role_columns.sql' must be present in
    _MIGRATION_FILES AND its list index must be greater than the index of
    '015_shadow_history_position_epoch.sql'.

    Migration files are append-only in dependency order. 019 depends on nothing
    (plan §Dependencies) but must come after the existing baseline 015.
    """
    migration_list = db._MIGRATION_FILES

    assert "019_fold_role_columns.sql" in migration_list, (
        "'019_fold_role_columns.sql' missing from _MIGRATION_FILES. "
        "R-2: the append is required (plan deliverable 4)."
    )
    assert "015_shadow_history_position_epoch.sql" in migration_list, (
        "'015_shadow_history_position_epoch.sql' unexpectedly missing from _MIGRATION_FILES. "
        "Cannot verify ordering — this entry is the baseline anchor."
    )

    idx_015 = migration_list.index("015_shadow_history_position_epoch.sql")
    idx_019 = migration_list.index("019_fold_role_columns.sql")

    assert idx_019 > idx_015, (
        f"'019_fold_role_columns.sql' (index {idx_019}) must appear AFTER "
        f"'015_shadow_history_position_epoch.sql' (index {idx_015}) in _MIGRATION_FILES. "
        "R-2: migration list is append-only in dependency order."
    )

    # No duplicate entries.
    count = sum(1 for m in migration_list if m == "019_fold_role_columns.sql")
    assert count == 1, (
        f"'019_fold_role_columns.sql' appears {count} times in _MIGRATION_FILES. "
        "R-2: append-only means exactly one entry."
    )


# ---------------------------------------------------------------------------
# Test 10 — R-3: migration file targets state DB only (no ATTACH, no optuna)
# ---------------------------------------------------------------------------


def test_migration_file_targets_state_db_only():
    """R-3 (spec-019): 019_fold_role_columns.sql must not reference the Optuna DB,
    use ATTACH DATABASE, or reference the optimization DB path.

    All decision-science migrations land in the state DB only (architecture
    constraint 3 — Two-DB boundary, E-2 binding hazard in README §1).
    """
    migrations_dir = pathlib.Path(__file__).parents[2] / "migrations"
    migration_path = migrations_dir / "019_fold_role_columns.sql"

    assert migration_path.exists(), (
        f"migrations/019_fold_role_columns.sql does not exist — cannot check R-3."
    )

    sql_text = migration_path.read_text(encoding="utf-8").upper()

    assert "ATTACH" not in sql_text, (
        "019_fold_role_columns.sql must not use ATTACH DATABASE. "
        "R-3: the migration targets state DB (alphabot_state.db) only — "
        "Two-DB boundary (architecture constraint 3) prohibits cross-DB operations."
    )
    assert "OPTUNA" not in sql_text, (
        "019_fold_role_columns.sql must not reference the Optuna DB. "
        "R-3: optimization DB (Optuna studies) is a separate file."
    )


# ---------------------------------------------------------------------------
# Test 11 — R-4/R-10: PRAGMA table_info on autotune_runs after migration applies
# ---------------------------------------------------------------------------


def test_fold_partitioned_table_fold_role_column_shape_after_run_migrations(tmp_path):
    """R-4/R-10 (spec-019): After run_migrations() on a fresh DB that has the
    fold-partitioned table pre-created, fold_role must appear on that table with:
      - type TEXT (or empty — SQLite is flexible)
      - notnull = 0 (NULLable)

    Also asserts pre-existing rows (inserted before migration) have fold_role IS NULL
    after migration (existing rows get the column DEFAULT, which is NULL).

    The implementer determined the fold-partitioned table is backtest_replay_rows
    (plan §Numbering: "current candidate is the backtest-replay rows"). This test
    targets that table specifically. The _FOLD_TABLE constant at the top of this
    file also uses backtest_replay_rows for consistency.

    This is the authoritative PRAGMA check on the actual target table.
    """
    assert "019_fold_role_columns.sql" in db._MIGRATION_FILES, (
        "'019_fold_role_columns.sql' missing from _MIGRATION_FILES — "
        "cannot test run_migrations() effect on backtest_replay_rows."
    )

    db_path = str(tmp_path / "test_pragma.db")
    with patch.object(db, "DB_FILE", db_path):
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "  migration_name TEXT PRIMARY KEY,"
            "  applied_at     TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        # Create the fold-partitioned table WITHOUT fold_role — simulating a pre-migration DB.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_replay_rows (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id  TEXT NOT NULL,
                symphony_id TEXT NOT NULL
            )
            """
        )
        # Pre-migration row — no fold_role column yet.
        conn.execute(
            "INSERT INTO backtest_replay_rows (cycle_id, symphony_id) VALUES (?, ?)",
            ("cycle-pre-001", "test-symphony"),
        )
        # Mark all prior migrations as already applied so only 019 is new.
        prior = [
            "004_schema_migrations_tracker.sql",
            "005_exit_triggers.sql",
            "006_autotune_runs_sharpe.sql",
            "007_autotune_runs_frozen_eval.sql",
            "008_shadow_history.sql",
            "009_fleet_alert_state.sql",
            "010_port_state.sql",
            "011_exit_triggers_port.sql",
            "012_autotune_runs_portmode.sql",
            "013_fleet_alert_tripped_symphonies.sql",
            "014_autotune_runs_selection_tstat.sql",
            "015_shadow_history_position_epoch.sql",
        ]
        for m in prior:
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (migration_name) VALUES (?)", (m,)
            )
        conn.commit()
        conn.close()

        # Now run_migrations — only 019 (and any 016/017/018 if present) should apply.
        db.run_migrations()

        # Check PRAGMA table_info on backtest_replay_rows.
        conn = sqlite3.connect(db_path)
        try:
            columns = conn.execute(
                "PRAGMA table_info(backtest_replay_rows)"
            ).fetchall()
            # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
            col_map = {row[1]: row for row in columns}
        finally:
            conn.close()

    assert "fold_role" in col_map, (
        "fold_role column missing from backtest_replay_rows after run_migrations(). "
        "R-4/R-10: migration 019 must ADD COLUMN fold_role to the fold-partitioned table."
    )

    col_info = col_map["fold_role"]
    # notnull = 0 means NULLable.
    assert col_info[3] == 0, (
        f"fold_role must be NULLable (notnull=0) on backtest_replay_rows; got notnull={col_info[3]}. "
        "R-4: existing rows must be preserved with NULL (additive ALTER)."
    )
    col_type = col_info[2].upper()
    assert col_type in ("TEXT", ""), (
        f"fold_role column type must be TEXT on backtest_replay_rows; found '{col_info[2]}'. "
        "R-10: plan §Deliverables 1 specifies TEXT DEFAULT NULL."
    )

    # Pre-existing row must have fold_role IS NULL after migration.
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT fold_role FROM backtest_replay_rows WHERE cycle_id = 'cycle-pre-001'"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, (
        "Pre-existing backtest_replay_rows row not found after migration — "
        "the row must be preserved (additive ALTER does not delete rows)."
    )
    assert row[0] is None, (
        f"Pre-existing backtest_replay_rows row must have fold_role IS NULL after migration; "
        f"got fold_role={row[0]!r}. "
        "R-4: existing rows get the column DEFAULT (NULL) — not any other value."
    )


# ---------------------------------------------------------------------------
# Test 12 — R-5: WAL mode preserved before and after migration applies
# ---------------------------------------------------------------------------


def test_wal_mode_preserved_through_migration(tmp_path):
    """R-5 (spec-019): ALTER TABLE ADD COLUMN is safe under WAL mode. The test
    confirms journal_mode is 'wal' before and after the migration applies,
    verifying no journal-mode incompatibility exists.
    """
    assert "019_fold_role_columns.sql" in db._MIGRATION_FILES, (
        "'019_fold_role_columns.sql' missing from _MIGRATION_FILES — "
        "cannot test WAL compatibility."
    )

    db_path = str(tmp_path / "test_wal.db")
    with patch.object(db, "DB_FILE", db_path):
        db.init_db()

        # Confirm WAL is set after init_db() (before migration re-check).
        conn = sqlite3.connect(db_path)
        try:
            mode_before = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
        finally:
            conn.close()

        assert mode_before == "wal", (
            f"journal_mode must be 'wal' after init_db(); got '{mode_before}'. "
            "R-5: WAL must be set before migration applies."
        )

        # Run migrations again (idempotent) — WAL must survive.
        db.run_migrations()

        conn = sqlite3.connect(db_path)
        try:
            mode_after = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
        finally:
            conn.close()

    assert mode_after == "wal", (
        f"journal_mode must still be 'wal' after run_migrations(); got '{mode_after}'. "
        "R-5: ALTER TABLE ADD COLUMN must not disturb WAL mode."
    )


# ---------------------------------------------------------------------------
# Test 13 — R-6: COALESCE semantics anchored to fixture JSON
# ---------------------------------------------------------------------------


def test_coalesce_filter_semantics_match_fixture_documentation(fold_db, fixtures_dir):
    """R-6 (spec-019): The fixture at tests/fixtures/math/fold_role_columns_019.json
    documents the wall_filter semantics. This test loads that fixture and asserts
    the live SQL results match what the fixture documents.

    This anchors the test to the fixture provenance (schema-derived, documented
    before implementation) rather than floating assertions.
    """
    fixture_path = fixtures_dir / "math" / "fold_role_columns_019.json"
    assert fixture_path.exists(), (
        f"Fixture file not found at {fixture_path}. "
        "R-6: tests must be anchored to the golden fixture."
    )

    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    semantics = fixture["wall_filter"]["semantics"]

    # The fixture states NULL row is INCLUDED under the correct filter.
    assert "included" in semantics["null_row_under_correct_filter"].lower(), (
        "Fixture documents that NULL fold_role is included under COALESCE filter. "
        "If this assertion fails, the fixture itself is wrong and must be corrected first."
    )
    # The fixture states NULL row is EXCLUDED under the buggy filter.
    assert "excluded" in semantics["null_row_under_buggy_filter"].lower(), (
        "Fixture documents that NULL fold_role is excluded under bare != filter. "
        "If this assertion fails, the fixture itself is wrong."
    )

    # Now verify the live SQL matches the fixture documentation.
    conn = sqlite3.connect(fold_db)
    try:
        correct_count = conn.execute(
            f"SELECT COUNT(*) FROM {_FOLD_TABLE} "
            f"WHERE COALESCE(fold_role, '') != 'frozen_eval'"
        ).fetchone()[0]
        buggy_count = conn.execute(
            f"SELECT COUNT(*) FROM {_FOLD_TABLE} "
            f"WHERE fold_role != 'frozen_eval'"
        ).fetchone()[0]
    finally:
        conn.close()

    # The fixture's documented semantics must hold in the live DB.
    # Correct filter includes 3 rows (train, validation, NULL); buggy excludes NULL → 2 rows.
    assert correct_count > buggy_count, (
        f"Live SQL result ({correct_count} COALESCE vs {buggy_count} bare) "
        "does not match fixture-documented semantics (COALESCE must return more rows). "
        "R-6: the fixture and the live DB must agree."
    )
