"""
AC-ISO: Test DB isolation contract.

These tests verify that no test in this suite can accidentally write to the
live alphabot_state.db in the project root.

AC-ISO.1 : database.save_autotune_run(...) inside pytest writes to a temp DB,
           never to alphabot_state.db.
AC-ISO.2 : Each test gets its own isolated DB path (per-test tmp_path).
AC-ISO.3 : Production engine code (alpha_bot_execution / autotuner) writes to
           the real DB when run outside pytest — the isolation mechanism must
           not bake paths into source code.

All three tests are RED until the autouse top-level conftest fixture is
implemented in tests/conftest.py.
"""

from __future__ import annotations

import os
import pathlib
import sqlite3

import pytest

# Absolute path of the live production DB — must never be touched by tests.
_PROJECT_ROOT = pathlib.Path(__file__).parents[1]
_LIVE_DB = _PROJECT_ROOT / "alphabot_state.db"


# ---------------------------------------------------------------------------
# AC-ISO.1 — save_autotune_run must NOT write to alphabot_state.db
# ---------------------------------------------------------------------------

class TestSaveAutotuneRunDoesNotWriteLiveDb:
    """
    AC-ISO.1: calling database.save_autotune_run inside pytest must never
    touch the live alphabot_state.db in the project root.
    """

    def test_save_autotune_run_writes_to_tmp_not_live_db(self, tmp_path):
        """
        After save_autotune_run, the row must land in the temp DB (not the live
        alphabot_state.db).  The conftest autouse fixture must redirect DB_PATH
        before this test runs.
        """
        import database as db_module

        # Confirm which DB the module will write to
        actual_db = db_module._db_file()
        assert actual_db != str(_LIVE_DB), (
            f"AC-ISO.1 FAIL: database._db_file() returned the live production DB path "
            f"({_LIVE_DB}).  The top-level conftest autouse fixture must set "
            "DB_PATH env var to a tmp_path before any test runs."
        )

        # Ensure the test DB is initialised and write a marker row
        db_module.init_db()
        db_module.save_autotune_run(
            run_timestamp="2026-01-01T00:00:00",
            symphony_id="iso-test-symphony",
            oos_alpha=1.0,
            train_alpha=1.0,
            baseline_decision="Adopted AI",
            fallback_oos_alpha=0.5,
            default_oos_alpha=0.5,
        )

        # The row must be in the temp DB
        temp_conn = sqlite3.connect(actual_db)
        try:
            count = temp_conn.execute(
                "SELECT COUNT(*) FROM autotune_runs WHERE symphony_id = 'iso-test-symphony'"
            ).fetchone()[0]
            assert count == 1, (
                f"AC-ISO.1 FAIL: row not found in temp DB ({actual_db}). "
                "save_autotune_run may have written nowhere or to wrong path."
            )
        finally:
            temp_conn.close()

        # The live DB must NOT be the path we wrote to
        assert actual_db != str(_LIVE_DB), (
            "AC-ISO.1 FAIL: DB_PATH was not redirected to a temp path."
        )


# ---------------------------------------------------------------------------
# AC-ISO.2 — each test gets a distinct DB path
# ---------------------------------------------------------------------------

class TestPerTestDbIsolation:
    """
    AC-ISO.2: every test that requests DB access gets its own DB file so state
    cannot bleed between tests.
    """

    def _current_db_path(self) -> str:
        import database as db_module
        return db_module._db_file()

    def test_first_test_gets_isolated_path(self):
        """DB path must not be the live alphabot_state.db."""
        assert self._current_db_path() != str(_LIVE_DB), (
            "AC-ISO.2 FAIL: DB_PATH points at the live DB inside test."
        )

    def test_second_test_gets_isolated_path(self):
        """Separate test call also gets an isolated (non-live) path."""
        assert self._current_db_path() != str(_LIVE_DB), (
            "AC-ISO.2 FAIL: DB_PATH points at the live DB inside test."
        )

    def test_db_path_is_not_empty_string(self):
        """Isolated path must not be empty (would default to in-cwd alphabot_state.db)."""
        path = self._current_db_path()
        assert path.strip() != "", (
            "AC-ISO.2 FAIL: DB_PATH is empty — SQLite would create alphabot_state.db in cwd."
        )

    def test_db_path_does_not_end_with_alphabot_state_db_in_project_root(self):
        """Path must not resolve to the project-root alphabot_state.db."""
        import database as db_module
        resolved = str(pathlib.Path(db_module._db_file()).resolve())
        live_resolved = str(_LIVE_DB.resolve())
        assert resolved != live_resolved, (
            f"AC-ISO.2 FAIL: resolved DB path ({resolved}) matches live DB ({live_resolved})."
        )


# ---------------------------------------------------------------------------
# AC-ISO.3 — isolation is env-var based, not baked into source code
# ---------------------------------------------------------------------------

class TestIsolationMechanismIsEnvBased:
    """
    AC-ISO.3: the isolation must be implemented via the DB_PATH environment
    variable so that production engine code (run outside pytest) uses the real
    DB path ('alphabot_state.db') by default.
    """

    def test_db_path_env_var_is_set_during_tests(self):
        """DB_PATH env var must be set by the autouse fixture during test runs."""
        val = os.environ.get("DB_PATH", "")
        assert val != "", (
            "AC-ISO.3 FAIL: DB_PATH env var is not set.  The conftest autouse fixture "
            "must use monkeypatch.setenv('DB_PATH', ...) so production code is unaffected "
            "when run outside pytest."
        )

    def test_db_path_env_var_points_outside_project_root(self):
        """DB_PATH must resolve outside the project root (or at least not to alphabot_state.db)."""
        val = os.environ.get("DB_PATH", "")
        assert val != "", "DB_PATH not set — see AC-ISO.3."
        resolved = str(pathlib.Path(val).resolve())
        live_resolved = str(_LIVE_DB.resolve())
        assert resolved != live_resolved, (
            f"AC-ISO.3 FAIL: DB_PATH resolves to the live DB ({live_resolved})."
        )
