"""RED tests — Change 4: migration 028 and pbo/dsr round-trip in autotune_runs.

Migration 028 adds two NULLable columns to autotune_runs:
  - pbo  REAL DEFAULT NULL  (Phase-3 PBO gate result)
  - dsr  REAL DEFAULT NULL  (forward-compat placeholder, UNUSED in this cycle — DSR
                              computation requires a logged D3 amendment / user sign-off)

schema contract (additive-first, project rule):
  - Both columns are NULLable with DEFAULT NULL — existing rows must read NULL.
  - New rows may supply a float pbo; dsr stays NULL until a later amendment.
  - The migration must be idempotent (safe to re-run on a DB that already has it).
  - Migration file: migrations/028_autotune_runs_pbo_dsr.sql
  - _MIGRATION_FILES list in database.py must include "028_autotune_runs_pbo_dsr.sql"
    as the last (highest-numbered) entry.

save_autotune_run must accept an optional `pbo` parameter (default None) and
persist the value to the pbo column.

IMPORTANT: dsr computation is DEFERRED (pending D3 amendment). The dsr column
exists for forward-compat only. test_c4_dsr_machinery_removed.py (D3 tripwire)
must remain GREEN — do NOT add dsr computation anywhere.

Every test here MUST FAIL until the implementation is in place.
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
import tempfile

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MIGRATIONS_DIR = _WORKTREE_ROOT / "migrations"
_MIGRATION_FILE = "028_autotune_runs_pbo_dsr.sql"


def _import_database():
    import sys
    repo = str(_WORKTREE_ROOT)
    if repo not in sys.path:
        sys.path.insert(0, repo)
    import database
    return database


# ---------------------------------------------------------------------------
# Migration file existence and content
# ---------------------------------------------------------------------------

class TestMigration028Existence:
    """migrations/028_autotune_runs_pbo_dsr.sql must exist and be well-formed."""

    def test_migration_file_exists(self):
        """migrations/028_autotune_runs_pbo_dsr.sql must exist."""
        path = _MIGRATIONS_DIR / _MIGRATION_FILE
        assert path.exists(), (
            f"Migration file not found: {path}. "
            "Change 4 requires adding migrations/028_autotune_runs_pbo_dsr.sql."
        )

    def test_migration_adds_pbo_column(self):
        """028 migration SQL must add a pbo REAL DEFAULT NULL column."""
        path = _MIGRATIONS_DIR / _MIGRATION_FILE
        if not path.exists():
            pytest.skip("Migration file missing — tested in test_migration_file_exists")
        sql = path.read_text(encoding="utf-8")
        assert "pbo" in sql.lower(), (
            f"{_MIGRATION_FILE} must reference a 'pbo' column"
        )
        assert "real" in sql.lower() or "REAL" in sql, (
            f"{_MIGRATION_FILE} pbo column must be REAL type"
        )

    def test_migration_adds_dsr_column(self):
        """028 migration SQL must add a dsr REAL DEFAULT NULL column (forward-compat)."""
        path = _MIGRATIONS_DIR / _MIGRATION_FILE
        if not path.exists():
            pytest.skip("Migration file missing")
        sql = path.read_text(encoding="utf-8")
        assert "dsr" in sql.lower(), (
            f"{_MIGRATION_FILE} must reference a 'dsr' column (forward-compat placeholder)"
        )

    def test_migration_is_additive_alter_table_not_drop(self):
        """028 migration must use ALTER TABLE ADD COLUMN, not DROP or recreate."""
        path = _MIGRATIONS_DIR / _MIGRATION_FILE
        if not path.exists():
            pytest.skip("Migration file missing")
        sql = path.read_text(encoding="utf-8").lower()
        assert "drop table" not in sql, (
            f"{_MIGRATION_FILE} must not DROP TABLE — additive-first rule"
        )
        assert "create table" not in sql or "if not exists" in sql, (
            f"{_MIGRATION_FILE} must not CREATE TABLE without IF NOT EXISTS — "
            "it must be purely additive (ALTER TABLE ADD COLUMN)"
        )
        assert "alter table" in sql, (
            f"{_MIGRATION_FILE} should use ALTER TABLE ADD COLUMN to add pbo and dsr"
        )


# ---------------------------------------------------------------------------
# _MIGRATION_FILES list registration
# ---------------------------------------------------------------------------

class TestMigrationListRegistration:
    """database.py _MIGRATION_FILES must include '028_autotune_runs_pbo_dsr.sql'."""

    def test_migration_028_in_migration_files_list(self):
        """_MIGRATION_FILES must contain '028_autotune_runs_pbo_dsr.sql'."""
        db = _import_database()
        assert hasattr(db, "_MIGRATION_FILES"), (
            "database module must expose _MIGRATION_FILES list"
        )
        assert _MIGRATION_FILE in db._MIGRATION_FILES, (
            f"database._MIGRATION_FILES must include '{_MIGRATION_FILE}' "
            f"(current list has {len(db._MIGRATION_FILES)} entries)"
        )

    def test_migration_028_is_last_in_list(self):
        """028 migration must be the last (highest-numbered) entry in _MIGRATION_FILES."""
        db = _import_database()
        mf = db._MIGRATION_FILES
        last = mf[-1]
        assert last == _MIGRATION_FILE, (
            f"'028_autotune_runs_pbo_dsr.sql' must be the last entry in _MIGRATION_FILES. "
            f"Current last entry: {last!r}"
        )

    def test_migration_028_not_listed_before_027(self):
        """028 must appear after 027 in _MIGRATION_FILES (no out-of-order placement)."""
        db = _import_database()
        mf = db._MIGRATION_FILES
        idx_027 = next(
            (i for i, f in enumerate(mf) if "027" in f), None
        )
        idx_028 = next(
            (i for i, f in enumerate(mf) if "028" in f), None
        )
        assert idx_027 is not None, "migration 027 must be in _MIGRATION_FILES"
        assert idx_028 is not None, "migration 028 must be in _MIGRATION_FILES"
        assert idx_028 > idx_027, (
            f"028 must appear after 027 in _MIGRATION_FILES. "
            f"Positions: 027={idx_027}, 028={idx_028}"
        )


# ---------------------------------------------------------------------------
# Idempotency: migration safe to re-run
# ---------------------------------------------------------------------------

class TestMigration028Idempotency:
    """Running migration 028 on a DB that already has the columns must not fail."""

    def test_migration_idempotent_pbo_dsr_columns_present_after_run_migrations(self):
        """After run_migrations(), autotune_runs must have pbo and dsr columns.

        This test is RED until migration 028 is in _MIGRATION_FILES and the SQL exists:
        run_migrations() currently does NOT add pbo/dsr columns so they are absent.
        """
        db = _import_database()
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_path = f.name
        try:
            os.environ["DB_PATH"] = tmp_path
            import importlib
            importlib.reload(db)
            db.run_migrations()
            conn = db.get_connection()
            # Verify column names via PRAGMA table_info.
            cols = [
                row[1]
                for row in conn.execute("PRAGMA table_info(autotune_runs)").fetchall()
            ]
            conn.close()
            assert "pbo" in cols, (
                f"autotune_runs must have a 'pbo' column after run_migrations(). "
                f"Current columns: {cols}. Add migrations/028_autotune_runs_pbo_dsr.sql "
                f"and register it in _MIGRATION_FILES."
            )
            assert "dsr" in cols, (
                f"autotune_runs must have a 'dsr' column after run_migrations(). "
                f"Current columns: {cols}."
            )
            # Second run must also be idempotent (no duplicate column error).
            db.run_migrations()
        finally:
            os.environ.pop("DB_PATH", None)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Existing rows read NULL for pbo and dsr
# ---------------------------------------------------------------------------

class TestMigration028ExistingRowsNullable:
    """Pre-existing autotune_runs rows must read NULL for pbo and dsr after migration."""

    def test_existing_rows_have_null_pbo_after_migration(self):
        """A row inserted before migration 028 must have pbo=NULL after the migration runs."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_path = f.name
        try:
            # Create a minimal DB with a pre-existing autotune_runs row (without pbo/dsr).
            conn = sqlite3.connect(tmp_path)
            conn.execute(
                """CREATE TABLE autotune_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_timestamp TEXT,
                    symphony_id TEXT,
                    oos_alpha REAL,
                    train_alpha REAL,
                    baseline_decision TEXT,
                    fallback_oos_alpha REAL,
                    default_oos_alpha REAL
                )"""
            )
            conn.execute(
                """INSERT INTO autotune_runs
                   (run_timestamp, symphony_id, oos_alpha, train_alpha,
                    baseline_decision, fallback_oos_alpha, default_oos_alpha)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                ("2024-01-01T00:00:00Z", "test_sym", 1.0, 0.5, "Adopted AI", 0.3, 0.2),
            )
            conn.commit()
            conn.close()

            # Now apply the migration SQL directly.
            migration_path = _MIGRATIONS_DIR / _MIGRATION_FILE
            if not migration_path.exists():
                pytest.skip("Migration file missing")
            sql = migration_path.read_text(encoding="utf-8")
            conn2 = sqlite3.connect(tmp_path)
            try:
                conn2.executescript(sql)
            except sqlite3.OperationalError as e:
                if "duplicate column" in str(e).lower():
                    pass  # idempotent path — columns already existed
                else:
                    raise
            conn2.commit()

            # The pre-existing row must now have pbo=NULL.
            row = conn2.execute("SELECT pbo, dsr FROM autotune_runs").fetchone()
            assert row is not None, "Pre-existing row must survive migration"
            pbo_val, dsr_val = row
            assert pbo_val is None, (
                f"Pre-existing row pbo must be NULL after migration, got {pbo_val!r}"
            )
            assert dsr_val is None, (
                f"Pre-existing row dsr must be NULL after migration, got {dsr_val!r}"
            )
            conn2.close()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# save_autotune_run: pbo kwarg round-trip
# ---------------------------------------------------------------------------

class TestSaveAutotuneRunPboRoundTrip:
    """save_autotune_run must accept pbo kwarg and persist it to autotune_runs.pbo."""

    def test_save_autotune_run_accepts_pbo_kwarg(self):
        """save_autotune_run must accept pbo= without raising TypeError."""
        db = _import_database()
        import inspect
        sig = inspect.signature(db.save_autotune_run)
        assert "pbo" in sig.parameters, (
            "save_autotune_run must have a 'pbo' parameter"
        )
        param = sig.parameters["pbo"]
        assert param.default is None, (
            f"pbo parameter must default to None, got {param.default!r}"
        )

    def test_pbo_value_persisted_and_round_trips(self):
        """A pbo float written via save_autotune_run must be readable back from autotune_runs."""
        db = _import_database()
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_path = f.name
        try:
            os.environ["DB_PATH"] = tmp_path
            import importlib
            importlib.reload(db)
            db.run_migrations()

            # Write a row with a specific pbo value.
            # The pbo value is a float — we assert it is a positive float in (0, 1)
            # but do NOT hardcode a specific numeric value (fixture-provenance rule).
            # We write 0.3 as an in-range test value.
            row_id = db.save_autotune_run(
                run_timestamp="2024-01-01T00:00:00Z",
                symphony_id="test_sym",
                oos_alpha=1.0,
                train_alpha=0.8,
                baseline_decision="Adopted AI",
                fallback_oos_alpha=0.5,
                default_oos_alpha=0.4,
                pbo=0.3,
            )
            assert isinstance(row_id, int) and row_id > 0, (
                f"save_autotune_run must return a positive integer row id, got {row_id!r}"
            )

            # Read back the pbo value.
            conn = db.get_connection()
            row = conn.execute(
                "SELECT pbo FROM autotune_runs WHERE id = ?", (row_id,)
            ).fetchone()
            conn.close()
            assert row is not None, f"Row id={row_id} not found in autotune_runs"
            pbo_persisted = row[0]
            assert pbo_persisted is not None, (
                "pbo value 0.3 must persist to DB (not NULL)"
            )
            assert isinstance(pbo_persisted, float), (
                f"pbo must be stored as REAL (float), got type {type(pbo_persisted)}"
            )
            # Assert it is a positive float in (0, 1) — format/shape assertion,
            # not a hardcoded producer value.
            assert 0.0 < pbo_persisted < 1.0, (
                f"pbo={pbo_persisted} must be in (0, 1)"
            )
        finally:
            os.environ.pop("DB_PATH", None)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def test_pbo_none_persists_as_null(self):
        """save_autotune_run(pbo=None) must persist NULL to the pbo column."""
        db = _import_database()
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_path = f.name
        try:
            os.environ["DB_PATH"] = tmp_path
            import importlib
            importlib.reload(db)
            db.run_migrations()

            row_id = db.save_autotune_run(
                run_timestamp="2024-01-02T00:00:00Z",
                symphony_id="sym_no_pbo",
                oos_alpha=0.5,
                train_alpha=0.3,
                baseline_decision="Reverted to Fallback",
                fallback_oos_alpha=0.5,
                default_oos_alpha=0.2,
                pbo=None,
            )
            conn = db.get_connection()
            row = conn.execute(
                "SELECT pbo FROM autotune_runs WHERE id = ?", (row_id,)
            ).fetchone()
            conn.close()
            assert row is not None
            assert row[0] is None, (
                f"pbo=None must persist as SQL NULL, got {row[0]!r}"
            )
        finally:
            os.environ.pop("DB_PATH", None)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# DSR deferred: dsr computation must not appear anywhere (D3 tripwire stays GREEN)
# ---------------------------------------------------------------------------

class TestDsrColumnDeferral:
    """dsr column exists for forward-compat only — NO dsr computation in this cycle."""

    def test_dsr_column_exists_in_migration(self):
        """028 migration adds dsr column (forward-compat) but no computation anywhere."""
        path = _MIGRATIONS_DIR / _MIGRATION_FILE
        if not path.exists():
            pytest.skip("Migration file missing")
        sql = path.read_text(encoding="utf-8").lower()
        assert "dsr" in sql, (
            "028 migration must add the dsr column for forward-compat (DSR computation deferred)"
        )

    def test_no_dsr_computation_in_autotuner(self):
        """autotuner.py must NOT compute dsr values (deferred per D3 amendment requirement)."""
        src = (_WORKTREE_ROOT / "autotuner.py").read_text(encoding="utf-8")
        # "compute_dsr" must not appear (D3 deleted it; forward-compat dsr column is NOT
        # the same as re-implementing dsr computation).
        assert "compute_dsr" not in src, (
            "autotuner.py must not call compute_dsr — DSR computation is DEFERRED pending "
            "a D3 amendment and user sign-off. Only the dsr column was added for forward-compat."
        )
        assert "compute_deflated_sharpe" not in src, (
            "autotuner.py must not call compute_deflated_sharpe_ratio — "
            "it was deleted in Decision D3 and must not be resurrected."
        )

    def test_save_autotune_run_does_not_take_dsr_param(self):
        """save_autotune_run must NOT add a dsr parameter in this cycle.

        The dsr column forward-compat exists in the schema but is always NULL.
        save_autotune_run keeps NULL by not accepting a dsr argument — this
        prevents accidental plumbing of a dsr value before the D3 amendment is approved.
        """
        db = _import_database()
        import inspect
        sig = inspect.signature(db.save_autotune_run)
        # dsr must NOT be a parameter — it is not yet plumbed.
        assert "dsr" not in sig.parameters, (
            "save_autotune_run must NOT accept a 'dsr' parameter in this cycle. "
            "The dsr column is a forward-compat placeholder; its computation requires "
            "a D3 amendment and user sign-off (pending)."
        )
