"""RED tests — Change 4: migration 028 — pbo column only on autotune_runs.

DSR is DROPPED ENTIRELY from this cycle (team-lead ruling 2026-06-01): DSR is
Sharpe-based, mismatched to the CRRA-EU objective, and redundant given PBO +
the existing BHY/n_effective gate. No dsr column, no dsr param, no dsr tests.
test_c4_dsr_machinery_removed.py (D3 tripwire) stays completely untouched and GREEN.

Migration 028 adds ONE NULLable column to autotune_runs:
  - pbo  REAL DEFAULT NULL  (Phase-3 PBO gate result)

Schema contract (additive-first, project rule):
  - Column is NULLable with DEFAULT NULL — existing rows must read NULL.
  - New rows may supply a float pbo.
  - The migration must be idempotent (safe to re-run on a DB that already has it).
  - Migration file: migrations/028_autotune_runs_pbo.sql
  - _MIGRATION_FILES list in database.py must include "028_autotune_runs_pbo.sql"
    as the last (highest-numbered) entry.

save_autotune_run must accept an optional `pbo` parameter (default None) and
persist the value to the pbo column.

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
_MIGRATION_FILE = "028_autotune_runs_pbo.sql"


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
    """migrations/028_autotune_runs_pbo.sql must exist and be well-formed."""

    def test_migration_file_exists(self):
        """migrations/028_autotune_runs_pbo.sql must exist."""
        path = _MIGRATIONS_DIR / _MIGRATION_FILE
        assert path.exists(), (
            f"Migration file not found: {path}. "
            "Change 4 requires adding migrations/028_autotune_runs_pbo.sql."
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

    def test_migration_does_not_add_dsr_column(self):
        """028 migration must NOT define a dsr SQL column — DSR is dropped from this cycle.

        The guard matches the column-definition pattern only (ADD COLUMN ... dsr or
        dsr ... REAL/INTEGER), not any mention of the three-letter string.  This
        allows comments that explain WHY the metric was dropped without false-positiving.
        """
        import re
        path = _MIGRATIONS_DIR / _MIGRATION_FILE
        if not path.exists():
            pytest.skip("Migration file missing")
        sql = path.read_text(encoding="utf-8")
        # Match an actual column definition: ADD COLUMN dsr ... or a column named dsr
        # followed by a SQL type keyword.  Case-insensitive.
        col_def_pattern = re.compile(
            r"add\s+column\s+dsr\b"          # ADD COLUMN dsr ...
            r"|"
            r"\bdsr\s+(?:real|integer|text|blob|numeric)\b",  # dsr REAL / dsr INTEGER etc.
            re.IGNORECASE,
        )
        match = col_def_pattern.search(sql)
        assert match is None, (
            f"{_MIGRATION_FILE} must NOT define a 'dsr' column — "
            "DSR was dropped entirely from this cycle (team-lead ruling 2026-06-01). "
            f"Matched pattern at: {match.group()!r}"
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
        assert "alter table" in sql, (
            f"{_MIGRATION_FILE} should use ALTER TABLE ADD COLUMN to add pbo"
        )


# ---------------------------------------------------------------------------
# _MIGRATION_FILES list registration
# ---------------------------------------------------------------------------

class TestMigrationListRegistration:
    """database.py _MIGRATION_FILES must include '028_autotune_runs_pbo.sql'."""

    def test_migration_028_in_migration_files_list(self):
        """_MIGRATION_FILES must contain '028_autotune_runs_pbo.sql'."""
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
            f"'028_autotune_runs_pbo.sql' must be the last entry in _MIGRATION_FILES. "
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
# Idempotency + pbo column present after run_migrations
# ---------------------------------------------------------------------------

class TestMigration028Idempotency:
    """run_migrations() must add the pbo column; running it twice must not raise."""

    def test_migration_idempotent_pbo_column_present_after_run_migrations(self):
        """After run_migrations(), autotune_runs must have a pbo column.

        RED until migration 028 is in _MIGRATION_FILES and the SQL file exists:
        run_migrations() currently does NOT add a pbo column.
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
            cols = [
                row[1]
                for row in conn.execute("PRAGMA table_info(autotune_runs)").fetchall()
            ]
            conn.close()
            assert "pbo" in cols, (
                f"autotune_runs must have a 'pbo' column after run_migrations(). "
                f"Current columns: {cols}. Add migrations/028_autotune_runs_pbo.sql "
                "and register it in _MIGRATION_FILES."
            )
            # dsr must NOT be present — dropped from this cycle.
            assert "dsr" not in cols, (
                f"autotune_runs must NOT have a 'dsr' column — "
                f"DSR was dropped entirely. Found columns: {cols}"
            )
            # Second run must be idempotent (no duplicate-column error).
            db.run_migrations()
        finally:
            os.environ.pop("DB_PATH", None)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Existing rows read NULL for pbo
# ---------------------------------------------------------------------------

class TestMigration028ExistingRowsNullable:
    """Pre-existing autotune_runs rows must read NULL for pbo after migration."""

    def test_existing_rows_have_null_pbo_after_migration(self):
        """A row inserted before migration 028 must have pbo=NULL after the migration runs."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_path = f.name
        try:
            # Create a minimal DB with a pre-existing autotune_runs row (without pbo).
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

            migration_path = _MIGRATIONS_DIR / _MIGRATION_FILE
            if not migration_path.exists():
                pytest.skip("Migration file missing")
            sql = migration_path.read_text(encoding="utf-8")
            conn2 = sqlite3.connect(tmp_path)
            try:
                conn2.executescript(sql)
            except sqlite3.OperationalError as e:
                if "duplicate column" in str(e).lower():
                    pass  # idempotent path — column already existed
                else:
                    raise
            conn2.commit()

            row = conn2.execute("SELECT pbo FROM autotune_runs").fetchone()
            assert row is not None, "Pre-existing row must survive migration"
            pbo_val = row[0]
            assert pbo_val is None, (
                f"Pre-existing row pbo must be NULL after migration, got {pbo_val!r}"
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
        """save_autotune_run must have a 'pbo' parameter defaulting to None."""
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
        """A pbo float written via save_autotune_run must be readable back as a positive float."""
        db = _import_database()
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_path = f.name
        try:
            os.environ["DB_PATH"] = tmp_path
            import importlib
            importlib.reload(db)
            db.run_migrations()

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

            conn = db.get_connection()
            row = conn.execute(
                "SELECT pbo FROM autotune_runs WHERE id = ?", (row_id,)
            ).fetchone()
            conn.close()
            assert row is not None, f"Row id={row_id} not found in autotune_runs"
            pbo_persisted = row[0]
            assert pbo_persisted is not None, "pbo value must persist to DB (not NULL)"
            assert isinstance(pbo_persisted, float), (
                f"pbo must be stored as REAL (float), got type {type(pbo_persisted)}"
            )
            # Format/shape assertion — not a hardcoded producer value.
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
# DSR dropped entirely: must not appear in migration, schema, or save_autotune_run
# ---------------------------------------------------------------------------

class TestDsrDroppedEntirely:
    """DSR is dropped from this cycle — no dsr column, no dsr param, no dsr tests."""

    def test_save_autotune_run_does_not_take_dsr_param(self):
        """save_autotune_run must NOT have a 'dsr' parameter.

        DSR was dropped entirely (team-lead ruling 2026-06-01): Sharpe-based,
        mismatched to CRRA-EU objective, redundant with PBO + BHY/n_effective.
        """
        db = _import_database()
        import inspect
        sig = inspect.signature(db.save_autotune_run)
        assert "dsr" not in sig.parameters, (
            "save_autotune_run must NOT accept a 'dsr' parameter — "
            "DSR was dropped entirely from this cycle."
        )

    def test_no_dsr_computation_in_autotuner(self):
        """autotuner.py must NOT compute dsr values (dropped from this cycle)."""
        src = (_WORKTREE_ROOT / "autotuner.py").read_text(encoding="utf-8")
        assert "compute_dsr" not in src, (
            "autotuner.py must not call compute_dsr — "
            "DSR was dropped entirely from this cycle."
        )
        assert "compute_deflated_sharpe" not in src, (
            "autotuner.py must not call compute_deflated_sharpe_ratio — "
            "deleted in Decision D3; must not be resurrected."
        )
