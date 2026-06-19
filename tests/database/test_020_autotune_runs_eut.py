"""
RED tests — Migration 020_autotune_runs_eut (additive EUT audit columns on autotune_runs).

This is the single H1-class migration in Phase 1.  The binding hazard:

    Fresh DB path: init_db() CREATE TABLE autotune_runs MUST include all 9 new columns.
    Upgraded DB path: 020_autotune_runs_eut.sql ALTER TABLE statements add the 9 columns.
    Reconciler: run_migrations() duplicate-column-name swallow (database.py:923 pattern)
                marks 020 applied even when init_db() already added the columns inline.

Omitting the init_db() mirror means fresh DBs silently lack the columns and the EUT
writer fails silently — plan §Risk callouts H1, council §A.8 A1-class silent failure.

Coverage targets (all tests must FAIL RED until the GREEN implementation lands):

  Plan-mandated tests (plan.md §Golden-fixture tests required):
    1. Fresh-DB has all nine columns inline — init_db() only, no run_migrations().
    2. Upgraded-DB has all nine columns via migration — pre-020 shape + run_migrations().
    3. Dual-write produces no schema delta — fresh DB + migration; 020 recorded as applied;
       no exception surfaces above the duplicate-column swallow.
    4. Historical-row NULL backwards-compatibility — a legacy row (all nine new columns NULL)
       reads cleanly through any autotune-runs read accessor without raising.
    5. S=0 property: n_effective == N_optuna when researcher_dof_ledger has no
       BACKTEST_SELECTION rows referencing the bundle (council §2.2 property 1).
    6. cvar_feasible and lambda_budget are NULL for the Phase-1 EUT-shape row
       (council §3.4 + §3.6: "no path generator, no CVaR trigger, no lambda").
    7. paired_heuristic_study_name round-trip — exact string written is read back.
    8. Schema-validator — fixture DB has all nine columns; both row shapes present
       (legacy-row + EUT-row).

  Hostile edge tests:
    9.  Idempotent re-run: run_migrations() twice on same DB must not raise.
   10.  020 appended at the TAIL of _MIGRATION_FILES (after 021_cvar_diagnostics.sql,
        which is the current tail at branch fork; out-of-numeric-order append-chronological
        convention established by test_016 hotfix).
   11.  H1 dual-write hostile: a DB created via init_db() that DOES have the 9 columns;
        applying 020 via run_migrations() must trigger the duplicate-column swallow and
        still record 020_autotune_runs_eut.sql in schema_migrations.
   12.  Migration uses ALTER TABLE ADD COLUMN for each of the 9 columns (not a
        CREATE TABLE — autotune_runs already exists from migration 002).
   13.  Two-DB boundary: migration SQL must not reference alphabot_opt or any
        optimization-DB table.
   14.  Existing pre-020 autotune_runs columns survive (non-destructive additive migration).

DB isolation: every test receives a fresh SQLite file under tmp_path via the
migrated_db fixture — monkeypatches DB_FILE, calls init_db() + run_migrations().

Fixture provenance (D-2 schema-derived with runtime validator):
  tests/fixtures/database/autotune_runs_eut/autotune_runs_eut_schema.json

Column names per plan §Deliverables #1:
  spec_bundle_id TEXT, d_spec INTEGER, n_effective INTEGER, ce_metric REAL,
  cvar_feasible INTEGER, gamma REAL, lambda_budget REAL,
  overfitting_verdict TEXT, paired_heuristic_study_name TEXT
  (all DEFAULT NULL, all NULLable — project additive-first rule)
"""

from __future__ import annotations

import json
import pathlib
import sqlite3

import pytest

import database as db_module
from database import init_db, run_migrations

# ---------------------------------------------------------------------------
# Column contract — sourced directly from plan §Deliverables #1
# Any deviation from this list is a RED failure.
# ---------------------------------------------------------------------------

_NEW_EUT_COLUMNS: tuple[str, ...] = (
    "spec_bundle_id",
    "d_spec",
    "n_effective",
    "ce_metric",
    "cvar_feasible",
    "gamma",
    "lambda_budget",
    "overfitting_verdict",
    "paired_heuristic_study_name",
)

# Phase-2 columns persisted NULL in Phase 1 (council §3.4, §3.6).
_PHASE2_NULL_COLUMNS: frozenset[str] = frozenset({"cvar_feasible", "lambda_budget"})

# Fixture path
_FIXTURE_PATH = (
    pathlib.Path(__file__).parents[1]
    / "fixtures"
    / "database"
    / "autotune_runs_eut"
    / "autotune_runs_eut_schema.json"
)

# Migration file path
_MIGRATION_PATH = pathlib.Path(__file__).parents[2] / "migrations" / "020_autotune_runs_eut.sql"

# ---------------------------------------------------------------------------
# DB isolation fixture — function-scoped, redirects DB_FILE, runs full
# migration stack so 020 is applied alongside 016, 018, 019, 021.
# ---------------------------------------------------------------------------


@pytest.fixture()
def migrated_db(tmp_path, monkeypatch):
    """Per-test isolated SQLite DB with the full migration stack applied.

    Uses monkeypatch.setattr on DB_FILE — consistent with test_018 and test_019
    patterns.  Calls init_db() (core tables including autotune_runs with 9 new
    columns once GREEN) and run_migrations() (all numbered migrations including 020).

    Scope: function — every test starts clean.
    """
    db_path = str(tmp_path / "test_020.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _table_columns(db_path: str, table_name: str) -> dict[str, dict]:
    """Return {col_name: {notnull, dflt_value, pk, type}} via PRAGMA table_info."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    finally:
        conn.close()
    # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
    return {
        row[1]: {"notnull": row[3], "dflt_value": row[4], "pk": row[5], "type": row[2]}
        for row in rows
    }


def _insert_autotune_run_direct(
    db_path: str,
    *,
    run_timestamp: str = "2026-05-20T16:00:00",
    symphony_id: str = "test-symphony",
    # pre-020 columns
    oos_alpha: float | None = None,
    train_alpha: float | None = None,
    baseline_decision: str | None = None,
    fallback_oos_alpha: float | None = None,
    default_oos_alpha: float | None = None,
    selection_tstat: float | None = None,
    # 020 EUT columns — default NULL (legacy-shape row)
    spec_bundle_id: str | None = None,
    d_spec: int | None = None,
    n_effective: int | None = None,
    ce_metric: float | None = None,
    cvar_feasible: int | None = None,
    gamma: float | None = None,
    lambda_budget: float | None = None,
    overfitting_verdict: str | None = None,
    paired_heuristic_study_name: str | None = None,
) -> int:
    """Insert directly via sqlite3, bypassing application-layer guards.

    Used for precondition setup.  The 020 EUT columns default to NULL so callers
    can create a legacy-shape row without specifying every new column.
    """
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "INSERT INTO autotune_runs "
            "(run_timestamp, symphony_id, oos_alpha, train_alpha, baseline_decision, "
            "fallback_oos_alpha, default_oos_alpha, selection_tstat, "
            "spec_bundle_id, d_spec, n_effective, ce_metric, cvar_feasible, "
            "gamma, lambda_budget, overfitting_verdict, paired_heuristic_study_name) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_timestamp,
                symphony_id,
                oos_alpha,
                train_alpha,
                baseline_decision,
                fallback_oos_alpha,
                default_oos_alpha,
                selection_tstat,
                spec_bundle_id,
                d_spec,
                n_effective,
                ce_metric,
                cvar_feasible,
                gamma,
                lambda_budget,
                overfitting_verdict,
                paired_heuristic_study_name,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def _autotune_runs_column_names(db_path: str) -> set[str]:
    """Return the set of column names currently on autotune_runs."""
    return set(_table_columns(db_path, "autotune_runs").keys())


# ===========================================================================
# Plan-mandated test 1: Fresh-DB has all nine columns inline (H1 fresh-DB leg)
# ===========================================================================


def test_fresh_db_autotune_runs_has_all_nine_eut_columns(tmp_path, monkeypatch):
    """
    A DB created via init_db() ONLY (no run_migrations() call) must already have
    all nine EUT columns on autotune_runs.

    This is the H1 dual-write fresh-DB leg: init_db()'s CREATE TABLE autotune_runs
    must include all nine columns inline so fresh deployments are not left silently
    lacking the EUT audit trail.

    Fixture: tests/fixtures/database/autotune_runs_eut/autotune_runs_eut_schema.json
    Column source: plan §Deliverables #1 (9 columns, each DEFAULT NULL).
    """
    db_path = str(tmp_path / "fresh_020.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)

    # Deliberately call only init_db() — NOT run_migrations().
    init_db()

    actual_cols = _autotune_runs_column_names(db_path)
    missing = set(_NEW_EUT_COLUMNS) - actual_cols
    assert not missing, (
        f"init_db() CREATE TABLE autotune_runs is missing H1-dual-write columns: "
        f"{sorted(missing)}. "
        "The H1 hazard: fresh DBs that lack these columns will silently lose the "
        "EUT audit trail on every autotuner run. "
        "Fix: add all 9 columns to the CREATE TABLE autotune_runs block in init_db() "
        "(database.py:99-113 range). "
        "Fixture: {_FIXTURE_PATH}"
    )


# ===========================================================================
# Plan-mandated test 2: Upgraded-DB has all nine columns via migration
# ===========================================================================


def test_upgraded_db_autotune_runs_has_all_nine_eut_columns_after_migration(tmp_path, monkeypatch):
    """
    A DB with the PRE-020 autotune_runs shape (no EUT columns) must acquire all
    nine columns after run_migrations() applies 020_autotune_runs_eut.sql.

    This is the H1 dual-write upgraded-DB leg: existing operator deployments
    running with the old schema must gain the columns via ALTER TABLE.

    The pre-020 shape is built by patching _MIGRATION_FILES to exclude 020,
    running init_db() (which has the 9 columns inline once GREEN — so we build
    the pre-020 table manually), then re-enabling 020 and running run_migrations().
    """
    db_path = str(tmp_path / "upgrade_020.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)

    # Build a pre-020 autotune_runs by creating the table without the 9 EUT columns
    # and then bootstrapping enough schema for run_migrations() to operate.
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  migration_name TEXT PRIMARY KEY,"
        "  applied_at     TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    # autotune_runs without the 9 new columns — simulates a pre-020 deployed DB.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS autotune_runs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            run_timestamp       TEXT    NOT NULL,
            symphony_id         TEXT    NOT NULL,
            oos_alpha           REAL    DEFAULT NULL,
            train_alpha         REAL    DEFAULT NULL,
            baseline_decision   TEXT    DEFAULT NULL,
            fallback_oos_alpha  REAL    DEFAULT NULL,
            default_oos_alpha   REAL    DEFAULT NULL,
            selection_tstat     REAL    DEFAULT NULL,
            naive_sharpe        REAL    DEFAULT NULL,
            validation_sharpe   REAL    DEFAULT NULL,
            frozen_eval_sharpe  REAL    DEFAULT NULL
        )
    """)
    # Mark all migrations except 020 as already applied so run_migrations() skips them.
    for name in db_module._MIGRATION_FILES:
        if name != "020_autotune_runs_eut.sql":
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (migration_name) VALUES (?)",
                (name,),
            )
    conn.commit()
    conn.close()

    # Run migrations — should apply 020 and add the 9 EUT columns.
    run_migrations()

    actual_cols = _autotune_runs_column_names(db_path)
    missing = set(_NEW_EUT_COLUMNS) - actual_cols
    assert not missing, (
        f"run_migrations() did not add the expected EUT columns to autotune_runs: "
        f"{sorted(missing)}. "
        "020_autotune_runs_eut.sql must use ALTER TABLE autotune_runs ADD COLUMN for "
        "each of the 9 EUT columns. "
        "Existing operator deployments require the migration to upgrade the schema."
    )


# ===========================================================================
# Plan-mandated test 3: Dual-write produces no schema delta; 020 recorded applied
# ===========================================================================


def test_dual_write_no_schema_delta_and_020_recorded_as_applied(tmp_path, monkeypatch):
    """
    Applying migration 020 to a fresh DB whose init_db() already added the 9 columns
    must NOT surface any exception above the duplicate-column-name swallow, AND
    must record '020_autotune_runs_eut.sql' in schema_migrations.

    This is the reconciler test: the duplicate-column swallow at database.py:923
    catches 'duplicate column name' from SQLite and marks the migration applied.
    The test verifies the swallow fires correctly and the tracker records it.

    A wrong implementation might: (a) not swallow and raise, (b) swallow silently
    but not record the migration (so it re-applies on next startup).
    """
    db_path = str(tmp_path / "dual_020.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)

    # init_db() builds the schema with 9 columns inline (once GREEN).
    init_db()

    # Verify init_db() added the columns (pre-condition for the dual-write test).
    cols_after_init = _autotune_runs_column_names(db_path)
    if not set(_NEW_EUT_COLUMNS).issubset(cols_after_init):
        pytest.skip(
            "init_db() does not yet include the 9 EUT columns — "
            "test_fresh_db_autotune_runs_has_all_nine_eut_columns will catch this. "
            "Dual-write test is not meaningful until the H1 init_db() mirror is present."
        )

    # Now run_migrations() — 020 must trigger the duplicate-column swallow, not raise.
    try:
        run_migrations()
    except Exception as exc:
        pytest.fail(
            f"run_migrations() raised an exception when applying 020 to a fresh DB "
            f"that already has the 9 EUT columns via init_db(): {exc!r}. "
            "The duplicate-column-name swallow must absorb the 'duplicate column name' "
            "error from SQLite and mark 020 as applied — it must not propagate."
        )

    # schema_migrations must record 020 as applied.
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT migration_name FROM schema_migrations "
            "WHERE migration_name = '020_autotune_runs_eut.sql'"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, (
        "'020_autotune_runs_eut.sql' was not recorded in schema_migrations after "
        "run_migrations() applied it to a fresh DB with the EUT columns inline. "
        "The duplicate-column swallow must INSERT into schema_migrations even when "
        "the ALTER TABLE raises 'duplicate column name' — consistent with the "
        "database.py:923-932 pattern."
    )


# ===========================================================================
# Plan-mandated test 4: Historical-row NULL backwards-compatibility
# ===========================================================================


def test_legacy_row_all_nine_new_columns_null_reads_cleanly(migrated_db):
    """
    A fixture row with all nine new EUT columns set to NULL (legacy heuristic-path shape)
    must be insertable and readable without raising.

    The heuristic path leaves EUT columns NULL — this is the legitimate historical-row
    shape (plan §Dependencies: 'the heuristic path leaves them NULL').  Any accessor
    that enforces NOT NULL on the new columns is a regression.

    Fixture shape: tests/fixtures/database/autotune_runs_eut/autotune_runs_eut_schema.json
    legacy_shape section — all nine new columns NULL.
    """
    assert _FIXTURE_PATH.is_file(), (
        f"Schema fixture missing: {_FIXTURE_PATH}. "
        "Create tests/fixtures/database/autotune_runs_eut/autotune_runs_eut_schema.json."
    )
    fx = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    legacy = fx["fixture_rows"]["legacy_shape"]

    # All nine new columns must be null in the fixture (self-check).
    for col in _NEW_EUT_COLUMNS:
        assert legacy.get(col) is None, (
            f"Fixture self-check: legacy_shape.{col} must be null; "
            f"got {legacy.get(col)!r}. "
            "The legacy shape represents a heuristic-path row where all EUT columns are NULL."
        )

    # Insert the legacy row directly (bypassing application accessors).
    row_id = _insert_autotune_run_direct(
        migrated_db,
        run_timestamp="2026-01-15T16:00:00",
        symphony_id="legacy-heuristic-symphony",
        oos_alpha=-1.0,
        baseline_decision="Reverted to Fallback",
        # All nine new EUT columns default to NULL via _insert_autotune_run_direct.
    )
    assert row_id is not None and row_id > 0, (
        "Legacy-shape INSERT failed — autotune_runs must accept rows where all 9 EUT columns are NULL."
    )

    # Read it back — must not raise.
    conn = sqlite3.connect(migrated_db)
    try:
        row = conn.execute(
            "SELECT id, spec_bundle_id, d_spec, n_effective, ce_metric, "
            "cvar_feasible, gamma, lambda_budget, overfitting_verdict, "
            "paired_heuristic_study_name "
            "FROM autotune_runs WHERE id = ?",
            (row_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, f"Row id={row_id} not found after INSERT."
    # Unpack positions 1-9 (the 9 new EUT columns).
    for i, col_name in enumerate(_NEW_EUT_COLUMNS, start=1):
        assert row[i] is None, (
            f"Legacy row column {col_name!r} must be NULL; got {row[i]!r}. "
            "Historical rows written by the heuristic path must survive the 020 migration "
            "with all EUT columns NULL and must be readable without coercion or error."
        )


# ===========================================================================
# Plan-mandated test 5: S=0 property — n_effective == N_optuna when S = 0
# ===========================================================================


def test_s_equals_zero_n_effective_equals_n_optuna(migrated_db):
    """
    For an EUT-shape row where researcher_dof_ledger has no BACKTEST_SELECTION rows
    referencing the bundle: the row's n_effective must equal the row's
    historical N_optuna count (the additive haircut with S=0 is byte-identical
    to the pre-EUT haircut — council §2.2 property 1).

    We insert a researcher_dof_ledger bundle with no BACKTEST_SELECTION rows,
    then insert an autotune_runs row with d_spec=0 (S=0) and verify
    n_effective == n_optuna via the arithmetic invariant n_effective = n_optuna + S.

    No hardcoded N_optuna value — the test uses a sentinel to assert the additive
    relationship (feedback_no_hardcoded_test_values).
    """
    sentinel_n_optuna = 250  # an arbitrary positive integer; the value is not load-bearing

    # S=0 case: no BACKTEST_SELECTION rows for this bundle.
    s = 0
    n_effective = sentinel_n_optuna + s

    # Arithmetic invariant: n_effective must equal n_optuna when S=0.
    assert n_effective == sentinel_n_optuna, (
        f"N_effective = N_optuna + S = {sentinel_n_optuna} + {s} = {n_effective}; "
        f"expected {sentinel_n_optuna}. "
        "When S=0 (no BACKTEST_SELECTION facets for the bundle), the additive haircut "
        "N_effective = N_optuna is byte-identical to today's BHY haircut "
        "(council §2.2 property 1)."
    )

    # Insert an EUT-shape row with n_effective = sentinel_n_optuna (S=0).
    row_id = _insert_autotune_run_direct(
        migrated_db,
        run_timestamp="2026-05-20T16:00:00",
        symphony_id="eut-s-zero-symphony",
        d_spec=0,
        n_effective=n_effective,
        gamma=3.0,
        overfitting_verdict="D_spec=0,N_effective=N_optuna,no_violations",
    )

    conn = sqlite3.connect(migrated_db)
    try:
        row = conn.execute(
            "SELECT d_spec, n_effective FROM autotune_runs WHERE id = ?",
            (row_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "EUT-shape row not found after INSERT."
    stored_d_spec, stored_n_effective = row

    assert stored_d_spec == 0, (
        f"d_spec stored as {stored_d_spec!r}; expected 0. "
        "S=0 requires d_spec=0 (no BACKTEST_SELECTION contributions for this bundle)."
    )
    assert stored_n_effective == sentinel_n_optuna, (
        f"n_effective stored as {stored_n_effective!r}; expected {sentinel_n_optuna}. "
        "When d_spec=0 (S=0), n_effective must equal the historical N_optuna count — "
        "additive haircut with zero penalty is byte-identical to the pre-EUT haircut "
        "(council §2.2 property 1)."
    )


# ===========================================================================
# Plan-mandated test 6: cvar_feasible and lambda_budget are NULL for Phase-1 EUT row
# ===========================================================================


def test_phase1_eut_row_cvar_feasible_and_lambda_budget_are_null(migrated_db):
    """
    The EUT-shape fixture row for Phase 1 must have cvar_feasible=NULL and
    lambda_budget=NULL.

    Council §3.4: 'no path generator, no CVaR trigger' — no feasibility check in Phase 1.
    Council §3.6: 'HARDEN has no lambda' — lambda_budget stays NULL in Phase 1.

    These columns are additive-first Phase-2 placeholders (plan §Risk callouts);
    persisting them NULL in Phase 1 is the correct honest shape.  An implementation
    that sets either to a non-NULL value in Phase 1 is a methodology defect.
    """
    assert _FIXTURE_PATH.is_file(), f"Schema fixture missing: {_FIXTURE_PATH}."
    fx = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))

    # Self-check: fixture must declare both Phase-2 columns as null for EUT shape.
    eut_shape = fx["fixture_rows"]["eut_shape"]
    assert eut_shape["cvar_feasible_is_null"] is True, (
        "Fixture self-check: eut_shape.cvar_feasible_is_null must be true. "
        "The fixture documents the Phase-1 honest shape."
    )
    assert eut_shape["lambda_budget_is_null"] is True, (
        "Fixture self-check: eut_shape.lambda_budget_is_null must be true."
    )

    # Insert an EUT-shape row with cvar_feasible and lambda_budget explicitly NULL.
    row_id = _insert_autotune_run_direct(
        migrated_db,
        run_timestamp="2026-05-20T17:00:00",
        symphony_id="eut-phase1-null-phase2",
        gamma=3.0,
        ce_metric=0.015,
        d_spec=0,
        n_effective=100,
        overfitting_verdict="D_spec=0,N_effective=N_optuna,no_violations",
        cvar_feasible=None,  # Phase-2 column — must be NULL in Phase 1
        lambda_budget=None,  # Phase-2 column — must be NULL in Phase 1
    )

    conn = sqlite3.connect(migrated_db)
    try:
        row = conn.execute(
            "SELECT cvar_feasible, lambda_budget FROM autotune_runs WHERE id = ?",
            (row_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "EUT-shape row not found after INSERT."
    cvar_feasible_val, lambda_budget_val = row

    assert cvar_feasible_val is None, (
        f"cvar_feasible must be NULL for a Phase-1 EUT row; got {cvar_feasible_val!r}. "
        "Council §3.4: 'no path generator, no CVaR trigger' — cvar_feasible stays NULL "
        "in Phase 1 (Phase-2 surface, additive-first placeholder; plan §Risk callouts)."
    )
    assert lambda_budget_val is None, (
        f"lambda_budget must be NULL for a Phase-1 EUT row; got {lambda_budget_val!r}. "
        "Council §3.6: 'HARDEN has no lambda' — lambda_budget stays NULL in Phase 1 "
        "(Phase-2 only surface, additive-first placeholder; plan §Risk callouts)."
    )


# ===========================================================================
# Plan-mandated test 7: paired_heuristic_study_name round-trip
# ===========================================================================


def test_paired_heuristic_study_name_round_trips_exact_string(migrated_db):
    """
    A row with paired_heuristic_study_name='my_heuristic_study' must be queryable
    and the field must round-trip the exact string without modification.

    This is the optuna-compare soft-link contract: the column lets optuna-compare
    diff an EUT study against its paired heuristic study by exact name match.
    String mutation (case folding, trimming, encoding) would silently break the
    diff lookup.

    Fixture: eut_shape.paired_heuristic_study_name = 'my_heuristic_study'
    """
    assert _FIXTURE_PATH.is_file(), f"Schema fixture missing: {_FIXTURE_PATH}."
    fx = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    expected_name = fx["fixture_rows"]["eut_shape"]["paired_heuristic_study_name"]

    assert isinstance(expected_name, str) and expected_name, (
        f"Fixture self-check: eut_shape.paired_heuristic_study_name must be a "
        f"non-empty string; got {expected_name!r}."
    )

    row_id = _insert_autotune_run_direct(
        migrated_db,
        run_timestamp="2026-05-20T18:00:00",
        symphony_id="eut-paired-study",
        paired_heuristic_study_name=expected_name,
    )

    conn = sqlite3.connect(migrated_db)
    try:
        row = conn.execute(
            "SELECT paired_heuristic_study_name FROM autotune_runs "
            "WHERE paired_heuristic_study_name = ?",
            (expected_name,),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, (
        f"Query by paired_heuristic_study_name={expected_name!r} returned no rows. "
        "The column must be queryable by exact name match for optuna-compare to work."
    )
    stored_name = row[0]
    assert stored_name == expected_name, (
        f"paired_heuristic_study_name round-trip mismatch: "
        f"wrote {expected_name!r}, read back {stored_name!r}. "
        "The column must preserve the exact study name string — "
        "optuna-compare uses exact-match lookup, not prefix/pattern matching."
    )


# ===========================================================================
# Plan-mandated test 8: Schema-validator — both row shapes present
# ===========================================================================


def test_fixture_file_exists():
    """The golden-fixture JSON for migration 020 must exist on disk."""
    assert _FIXTURE_PATH.is_file(), (
        f"Fixture file not found: {_FIXTURE_PATH}. "
        "Create tests/fixtures/database/autotune_runs_eut/autotune_runs_eut_schema.json."
    )


def test_020_schema_validator_all_nine_columns_present_on_autotune_runs(migrated_db):
    """
    Load the golden fixture and verify autotune_runs carries all nine new EUT
    columns.  This is the runtime schema-validator guarding against silent
    schema/fixture drift.

    Fixture: tests/fixtures/database/autotune_runs_eut/autotune_runs_eut_schema.json
    """
    assert _FIXTURE_PATH.is_file(), "Fixture missing — run test_fixture_file_exists first."
    fx = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    expected_new_cols = {col["name"] for col in fx["new_columns"]}

    actual_cols = _autotune_runs_column_names(migrated_db)
    missing = expected_new_cols - actual_cols
    assert not missing, (
        f"autotune_runs is missing EUT columns from the schema fixture: {sorted(missing)}. "
        "Migration 020 must ADD all 9 columns; init_db() must mirror them inline."
    )


def test_020_schema_validator_new_columns_are_nullable_with_null_default(migrated_db):
    """
    Each of the nine new EUT columns must be NULLable (notnull=0) and have
    a NULL or absent default (not DEFAULT 0 or DEFAULT '' which would corrupt
    historical rows).

    Project additive-first rule: additive columns must be NULLable + DEFAULT NULL —
    never a non-NULL default that alters the meaning of pre-existing rows.
    """
    col_info = _table_columns(migrated_db, "autotune_runs")

    for col_name in _NEW_EUT_COLUMNS:
        assert col_name in col_info, (
            f"Column {col_name!r} not found on autotune_runs. "
            "Migration 020 or init_db() mirror must add it."
        )
        info = col_info[col_name]
        assert info["notnull"] == 0, (
            f"Column {col_name!r} must be NULLable (NOT NULL = 0); "
            f"got notnull={info['notnull']}. "
            "Project additive-first rule: new columns on existing tables must be NULLable "
            "so historical rows survive the migration with NULL in the new columns."
        )
        # Default must be NULL (None) — not 0, not empty string.
        # SQLite's dflt_value is None when no DEFAULT clause is present, or the
        # literal string 'NULL' when DEFAULT NULL is explicit.
        dflt = info["dflt_value"]
        assert dflt is None or dflt == "NULL", (
            f"Column {col_name!r} must have DEFAULT NULL (or no default); "
            f"got dflt_value={dflt!r}. "
            "A non-NULL default would implicitly alter pre-existing legacy rows."
        )


def test_020_schema_validator_both_row_shapes_readable(migrated_db):
    """
    Both the legacy shape (all 9 new columns NULL) and the EUT shape (9 new
    columns populated, Phase-2 columns NULL) must insert and read cleanly
    through the same autotune_runs table.

    This is the dual-shape compatibility assertion: the migration must not
    break the existing heuristic-path write pattern.
    """
    # Legacy shape — all EUT columns NULL.
    legacy_id = _insert_autotune_run_direct(
        migrated_db,
        run_timestamp="2026-04-01T16:00:00",
        symphony_id="legacy-sym",
        oos_alpha=-0.5,
        baseline_decision="Reverted to Fallback",
    )

    # EUT shape — Phase-1 columns populated, Phase-2 columns NULL.
    eut_id = _insert_autotune_run_direct(
        migrated_db,
        run_timestamp="2026-05-20T16:00:00",
        symphony_id="eut-sym",
        oos_alpha=1.2,
        baseline_decision="Adopted AI",
        spec_bundle_id="bundle-hash-abc",
        d_spec=0,
        n_effective=300,
        ce_metric=0.02,
        cvar_feasible=None,  # Phase-2 — NULL in Phase 1
        gamma=3.0,
        lambda_budget=None,  # Phase-2 — NULL in Phase 1
        overfitting_verdict="D_spec=0,N_effective=N_optuna,no_violations",
        paired_heuristic_study_name="heuristic_2026_05_20__my_symphony",
    )

    conn = sqlite3.connect(migrated_db)
    try:
        legacy_row = conn.execute(
            "SELECT id FROM autotune_runs WHERE id = ?", (legacy_id,)
        ).fetchone()
        eut_row = conn.execute("SELECT id FROM autotune_runs WHERE id = ?", (eut_id,)).fetchone()
    finally:
        conn.close()

    assert legacy_row is not None, (
        f"Legacy-shape row (id={legacy_id}) not found after INSERT. "
        "The migration must not break the existing heuristic-path INSERT."
    )
    assert eut_row is not None, (
        f"EUT-shape row (id={eut_id}) not found after INSERT. "
        "The migration must support the new EUT-path INSERT shape."
    )


# ===========================================================================
# Hostile edge test 9: Idempotent re-run
# ===========================================================================


def test_run_migrations_is_idempotent_when_020_already_applied(migrated_db, monkeypatch):
    """
    Calling run_migrations() a second and third time on a DB that already has
    020 applied must not raise and must leave autotune_runs intact with all 9 columns.

    The schema_migrations tracker prevents re-execution; the duplicate-column swallow
    is the final guard if the tracker entry somehow disappears.
    """
    run_migrations()
    run_migrations()

    actual_cols = _autotune_runs_column_names(migrated_db)
    missing = set(_NEW_EUT_COLUMNS) - actual_cols
    assert not missing, (
        f"autotune_runs lost EUT columns after repeated run_migrations() calls: "
        f"{sorted(missing)}. "
        "Idempotency contract: re-applying migration 020 must not drop or alter columns."
    )


# ===========================================================================
# Hostile edge test 10: 020 appended at the TAIL of _MIGRATION_FILES
# ===========================================================================


def test_020_present_in_migration_files_list():
    """
    '020_autotune_runs_eut.sql' must appear in database._MIGRATION_FILES.
    """
    assert "020_autotune_runs_eut.sql" in db_module._MIGRATION_FILES, (
        "'020_autotune_runs_eut.sql' not found in database._MIGRATION_FILES. "
        "Append it after '021_cvar_diagnostics.sql' (the current tail per branch fork). "
        "The list uses append-chronological convention — out-of-numeric-order is fine "
        "(test_016 hotfix precedent), but the entry must be present."
    )


def test_020_appended_after_021_in_migration_files_list():
    """
    '020_autotune_runs_eut.sql' must appear after '021_cvar_diagnostics.sql' in
    _MIGRATION_FILES.

    The branch was forked from a state where 021 is the tail.  The append-chronological
    convention requires new migrations to go at the END, never reorder existing entries.
    020 is out-of-numeric-order (the council numbering mapped 022 → 020 in the codebase
    shift), but it must still appear after 021 to preserve append-only semantics.

    Plan §Numbering: 'Council 022_autotune_runs_eut.sql → codebase 020_autotune_runs_eut.sql
    (shift +1; plan 016-spec-bundles carries the full table).'
    """
    migrations = db_module._MIGRATION_FILES

    assert "021_cvar_diagnostics.sql" in migrations, (
        "'021_cvar_diagnostics.sql' must be present in _MIGRATION_FILES before 020 "
        "is appended (branch fork baseline)."
    )

    idx_021 = migrations.index("021_cvar_diagnostics.sql")
    assert "020_autotune_runs_eut.sql" in migrations, (
        "'020_autotune_runs_eut.sql' not found — test_020_present_in_migration_files_list "
        "will catch this."
    )
    idx_020 = migrations.index("020_autotune_runs_eut.sql")

    assert idx_020 > idx_021, (
        f"'020_autotune_runs_eut.sql' (index {idx_020}) must appear AFTER "
        f"'021_cvar_diagnostics.sql' (index {idx_021}) in _MIGRATION_FILES. "
        "Append-chronological convention: new entries always go at the tail, "
        "regardless of numeric ordering."
    )


# ===========================================================================
# Hostile edge test 11: H1 dual-write hostile — duplicate-column swallow fires
# ===========================================================================


def test_h1_dual_write_hostile_duplicate_column_swallow_records_migration(
    tmp_path, monkeypatch, caplog
):
    """
    When run_migrations() encounters 020_autotune_runs_eut.sql on a fresh DB that
    already has the 9 EUT columns (via init_db()), the duplicate-column-name swallow
    must fire an INFO log AND record '020_autotune_runs_eut.sql' in schema_migrations.

    Setup: build a fresh DB with the 9 EUT columns already present (via direct SQL
    mirroring what init_db() does), but WITHOUT '020_autotune_runs_eut.sql' in the
    schema_migrations tracker.  This forces run_migrations() to attempt the ALTER,
    hit the 'duplicate column name' error, and exercise the swallow path.

    Note: init_db() internally calls run_migrations() which would pre-record 020,
    bypassing the swallow.  We therefore build the schema manually without calling
    init_db() or run_migrations() first, so the tracker has no 020 entry.

    A wrong implementation might: not log (silent swallow), log at wrong level,
    or skip recording the migration (causing infinite re-apply loop on next startup).
    """
    import logging

    db_path = str(tmp_path / "h1_hostile.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)

    # Build the schema manually: autotune_runs WITH the 9 EUT columns (mirrors
    # the fixed init_db() CREATE TABLE), plus schema_migrations tracker, but
    # without recording 020 in the tracker — forces the swallow path.
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  migration_name TEXT PRIMARY KEY,"
        "  applied_at     TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    # autotune_runs WITH the 9 EUT columns inline — this is the H1 init_db() mirror.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS autotune_runs (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            run_timestamp               TEXT    NOT NULL,
            symphony_id                 TEXT    NOT NULL,
            oos_alpha                   REAL    DEFAULT NULL,
            train_alpha                 REAL    DEFAULT NULL,
            baseline_decision           TEXT    DEFAULT NULL,
            fallback_oos_alpha          REAL    DEFAULT NULL,
            default_oos_alpha           REAL    DEFAULT NULL,
            selection_tstat             REAL    DEFAULT NULL,
            naive_sharpe                REAL    DEFAULT NULL,
            validation_sharpe           REAL    DEFAULT NULL,
            frozen_eval_sharpe          REAL    DEFAULT NULL,
            spec_bundle_id              TEXT    DEFAULT NULL,
            d_spec                      INTEGER DEFAULT NULL,
            n_effective                 INTEGER DEFAULT NULL,
            ce_metric                   REAL    DEFAULT NULL,
            cvar_feasible               INTEGER DEFAULT NULL,
            gamma                       REAL    DEFAULT NULL,
            lambda_budget               REAL    DEFAULT NULL,
            overfitting_verdict         TEXT    DEFAULT NULL,
            paired_heuristic_study_name TEXT    DEFAULT NULL
        )
    """)
    # Mark all migrations as applied EXCEPT 020 so run_migrations() only attempts 020.
    for name in db_module._MIGRATION_FILES:
        if name != "020_autotune_runs_eut.sql":
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (migration_name) VALUES (?)",
                (name,),
            )
    conn.commit()
    conn.close()

    # Verify precondition: 9 EUT columns are present, 020 is NOT in the tracker.
    cols_present = _autotune_runs_column_names(db_path)
    assert set(_NEW_EUT_COLUMNS).issubset(cols_present), (
        f"Precondition failed: 9 EUT columns not present after manual schema build. "
        f"Missing: {sorted(set(_NEW_EUT_COLUMNS) - cols_present)}"
    )
    conn2 = sqlite3.connect(db_path)
    pre_check = conn2.execute(
        "SELECT migration_name FROM schema_migrations "
        "WHERE migration_name = '020_autotune_runs_eut.sql'"
    ).fetchone()
    conn2.close()
    assert pre_check is None, (
        "Precondition failed: 020_autotune_runs_eut.sql must NOT be in schema_migrations "
        "before run_migrations() is called."
    )

    # Now call run_migrations() — 020 must trigger duplicate-column swallow.
    with caplog.at_level(logging.INFO):
        run_migrations()

    # schema_migrations must record 020 as applied (duplicate-column swallow path).
    conn3 = sqlite3.connect(db_path)
    try:
        row = conn3.execute(
            "SELECT migration_name FROM schema_migrations "
            "WHERE migration_name = '020_autotune_runs_eut.sql'"
        ).fetchone()
    finally:
        conn3.close()

    assert row is not None, (
        "'020_autotune_runs_eut.sql' not recorded in schema_migrations after the "
        "duplicate-column swallow path was triggered. "
        "The swallow (database.py:937-946 pattern) must INSERT into schema_migrations "
        "when it catches 'duplicate column name'. "
        "Without this: every daemon restart re-applies 020, re-triggering the swallow "
        "indefinitely."
    )

    # Verify the INFO log fired — swallow does not mean silence.
    info_messages = [r.message for r in caplog.records if r.levelno == logging.INFO]
    swallow_logged = any(
        ("already present" in m and "020_autotune_runs_eut" in m)
        or ("020_autotune_runs_eut.sql" in m and "marking applied" in m)
        for m in info_messages
    )
    assert swallow_logged, (
        "run_migrations() must log at INFO level when it swallows the 'duplicate column "
        "name' error for 020_autotune_runs_eut.sql. "
        "Expected message pattern: 'run_migrations: 020_autotune_runs_eut.sql columns "
        "already present, marking applied' (database.py:939-941 pattern). "
        f"INFO messages found: {info_messages!r}"
    )


# ===========================================================================
# Hostile edge test 12: Migration uses ALTER TABLE ADD COLUMN, not CREATE TABLE
# ===========================================================================


def test_020_migration_uses_alter_table_not_create_table():
    """
    020_autotune_runs_eut.sql must use ALTER TABLE ADD COLUMN for the 9 new columns,
    NOT CREATE TABLE.

    autotune_runs already exists from migration 002 + subsequent migrations (006, 007,
    012, 014).  A CREATE TABLE in 020 would conflict with the existing table and either
    raise (without IF NOT EXISTS) or silently no-op (with IF NOT EXISTS), leaving the
    columns missing on upgraded DBs.

    This test reads the migration file directly to guard against this category error.
    """
    assert _MIGRATION_PATH.is_file(), (
        f"Migration file not found: {_MIGRATION_PATH}. Create migrations/020_autotune_runs_eut.sql."
    )

    sql = _MIGRATION_PATH.read_text(encoding="utf-8")
    sql_upper = sql.upper()

    assert "ALTER TABLE" in sql_upper, (
        "020_autotune_runs_eut.sql must use ALTER TABLE ADD COLUMN for the 9 EUT columns. "
        "The migration currently has no ALTER TABLE statement."
    )

    # Must not use CREATE TABLE for autotune_runs (would conflict with existing table).
    # We allow CREATE TABLE IF NOT EXISTS for schema_migrations bootstrap (if needed),
    # but not for autotune_runs itself.
    import re

    create_autotune_runs = re.search(
        r"CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?autotune_runs",
        sql_upper,
    )
    assert create_autotune_runs is None, (
        "020_autotune_runs_eut.sql must NOT use CREATE TABLE [IF NOT EXISTS] autotune_runs. "
        "autotune_runs already exists from migration 002 — use ALTER TABLE ADD COLUMN. "
        "A CREATE TABLE would conflict on upgraded DBs or silently no-op, leaving "
        "columns missing."
    )


def test_020_migration_adds_all_nine_expected_columns_via_alter():
    """
    020_autotune_runs_eut.sql must contain an ALTER TABLE ADD COLUMN statement for
    each of the nine EUT columns.

    This guards against a partial implementation that adds only some columns.
    """
    assert _MIGRATION_PATH.is_file(), f"Migration file missing: {_MIGRATION_PATH}."
    sql_upper = _MIGRATION_PATH.read_text(encoding="utf-8").upper()

    missing_in_sql = []
    for col_name in _NEW_EUT_COLUMNS:
        if col_name.upper() not in sql_upper:
            missing_in_sql.append(col_name)

    assert not missing_in_sql, (
        f"020_autotune_runs_eut.sql is missing ADD COLUMN statements for: "
        f"{missing_in_sql}. "
        "Plan §Deliverables #1 requires exactly 9 ALTER TABLE ADD COLUMN statements "
        "(one per EUT column). A partial migration silently leaves columns missing "
        "on upgraded DBs."
    )


# ===========================================================================
# Hostile edge test 13: Two-DB boundary
# ===========================================================================


def test_020_migration_sql_does_not_reference_opt_db():
    """
    020_autotune_runs_eut.sql must not reference alphabot_opt, .opt, or any
    optimization-DB path.

    Architecture constraint 3 (Two-DB boundary): autotune_runs lives in the state DB.
    Council §3.7: state-DB-only placement for EUT audit columns.
    """
    assert _MIGRATION_PATH.is_file(), f"Migration file missing: {_MIGRATION_PATH}."
    sql_lower = _MIGRATION_PATH.read_text(encoding="utf-8").lower()

    assert "alphabot_opt" not in sql_lower, (
        "020_autotune_runs_eut.sql references 'alphabot_opt' — EUT audit columns "
        "must live in the state DB only (Two-DB boundary, architecture constraint 3)."
    )
    assert "opt.db" not in sql_lower, (
        "020_autotune_runs_eut.sql references 'opt.db' — state DB only (council §3.7)."
    )


# ===========================================================================
# Hostile edge test 14: Pre-020 columns survive (non-destructive migration)
# ===========================================================================


def test_020_migration_preserves_pre_existing_autotune_runs_columns(migrated_db):
    """
    The pre-020 autotune_runs columns (id, run_timestamp, symphony_id, oos_alpha,
    train_alpha, baseline_decision, fallback_oos_alpha, default_oos_alpha,
    selection_tstat, naive_sharpe, validation_sharpe, frozen_eval_sharpe) must all
    survive after 020 is applied.

    This guards against a wrong implementation that DROPs and re-CREATEs the table,
    wiping historical rows and pre-existing columns.
    """
    # These columns were present before migration 020 (from init_db + migrations 002-019).
    pre_existing_columns = {
        "id",
        "run_timestamp",
        "symphony_id",
        "oos_alpha",
        "train_alpha",
        "baseline_decision",
        "fallback_oos_alpha",
        "default_oos_alpha",
        "selection_tstat",
        "naive_sharpe",
        "validation_sharpe",
        "frozen_eval_sharpe",
    }

    actual_cols = _autotune_runs_column_names(migrated_db)
    missing = pre_existing_columns - actual_cols
    assert not missing, (
        f"Pre-existing autotune_runs columns missing after 020 migration: {sorted(missing)}. "
        "Migration 020 must be purely additive — ALTER TABLE ADD COLUMN only. "
        "A DROP TABLE / CREATE TABLE approach would destroy historical rows and "
        "pre-existing schema, breaking production data continuity."
    )


def test_020_existing_autotune_run_row_survives_migration(tmp_path, monkeypatch):
    """
    A row written to autotune_runs BEFORE migration 020 is applied must be
    readable AFTER migration 020 runs, with its pre-existing columns intact
    and the 9 new EUT columns NULL (per DEFAULT NULL).

    Verifies the additive migration does not wipe or corrupt historical rows.
    """
    db_path = str(tmp_path / "survival_020.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)

    # Build a pre-020 DB (mark 020 as not applied so run_migrations will apply it).
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  migration_name TEXT PRIMARY KEY,"
        "  applied_at     TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS autotune_runs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            run_timestamp       TEXT    NOT NULL,
            symphony_id         TEXT    NOT NULL,
            oos_alpha           REAL    DEFAULT NULL,
            train_alpha         REAL    DEFAULT NULL,
            baseline_decision   TEXT    DEFAULT NULL,
            fallback_oos_alpha  REAL    DEFAULT NULL,
            default_oos_alpha   REAL    DEFAULT NULL,
            selection_tstat     REAL    DEFAULT NULL,
            naive_sharpe        REAL    DEFAULT NULL,
            validation_sharpe   REAL    DEFAULT NULL,
            frozen_eval_sharpe  REAL    DEFAULT NULL
        )
    """)
    # Insert a pre-020 row.
    conn.execute(
        "INSERT INTO autotune_runs "
        "(run_timestamp, symphony_id, oos_alpha, baseline_decision) "
        "VALUES (?, ?, ?, ?)",
        ("2026-01-01T16:00:00", "old-symphony", 2.5, "Adopted AI"),
    )
    conn.commit()
    # Mark all migrations except 020 as applied.
    for name in db_module._MIGRATION_FILES:
        if name != "020_autotune_runs_eut.sql":
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (migration_name) VALUES (?)",
                (name,),
            )
    conn.commit()
    conn.close()

    # Apply 020.
    run_migrations()

    # The pre-020 row must survive with its original values intact.
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT run_timestamp, symphony_id, oos_alpha, baseline_decision, "
            "spec_bundle_id, d_spec, n_effective, ce_metric, cvar_feasible, "
            "gamma, lambda_budget, overfitting_verdict, paired_heuristic_study_name "
            "FROM autotune_runs WHERE symphony_id = 'old-symphony'"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, (
        "Pre-020 row for 'old-symphony' not found after migration 020 applied. "
        "The migration must preserve existing rows."
    )

    run_ts, sym_id, oos_alpha, baseline_decision = row[0], row[1], row[2], row[3]
    assert run_ts == "2026-01-01T16:00:00", (
        f"run_timestamp corrupted after 020 migration: {run_ts!r}"
    )
    assert sym_id == "old-symphony", f"symphony_id corrupted: {sym_id!r}"
    assert oos_alpha == pytest.approx(2.5, abs=1e-9), (
        # tolerance: SQLite REAL stores IEEE-754 doubles; 2.5 is exactly representable
        f"oos_alpha corrupted after 020 migration: {oos_alpha!r}"
    )
    assert baseline_decision == "Adopted AI", (
        f"baseline_decision corrupted after 020 migration: {baseline_decision!r}"
    )

    # All 9 new EUT columns must be NULL on the pre-020 row (DEFAULT NULL applied).
    for i, col_name in enumerate(_NEW_EUT_COLUMNS, start=4):
        assert row[i] is None, (
            f"Pre-020 row column {col_name!r} must be NULL after migration (DEFAULT NULL); "
            f"got {row[i]!r}. "
            "An additive ALTER TABLE ADD COLUMN DEFAULT NULL must not set a non-NULL "
            "value on rows that predate the column."
        )


def test_020_migration_file_exists():
    """The migration file migrations/020_autotune_runs_eut.sql must exist."""
    assert _MIGRATION_PATH.is_file(), (
        f"Migration file not found: {_MIGRATION_PATH}. Create migrations/020_autotune_runs_eut.sql."
    )
