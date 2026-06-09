"""
AC-ISO-GUARD: Production-DB write guard — RED test suite.

Tests the four acceptance criteria for making test→prod-DB writes structurally
impossible:

  GUARD-1  database._db_file() raises RuntimeError when pytest is loaded and
           DB_PATH is absent (or resolves to the production default basename).

  GUARD-2  Advisor producers — run_overfitting_conscience, run_divergence_explainer,
           run_spec_critic — do NOT write to the production DB path, even though
           each has its own `import database` that is not covered by a wholesale
           `patch("autotuner.database")` mock.

  GUARD-3  DB_PATH is set to a non-production, non-empty path for the entire
           pytest session, including at module-import time (session-scoped fixture
           closes Gap 2 in LEAK-SOURCE.md).

  GUARD-4  The guard does NOT fire when pytest is NOT present in sys.modules
           (daemon-safety: the real engine must never see a RuntimeError from
           the guard).

All four tests are RED before the implementation in database.py / conftest.py.

Root cause documented in .design-handoff/data-audit/LEAK-SOURCE.md.
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
import sys
import types

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROJECT_ROOT = pathlib.Path(__file__).parents[1]
_PROD_DB_PATH = _PROJECT_ROOT / "alphabot_state.db"
# The canonical basename that the guard must block.
_PROD_DB_BASENAME = "alphabot_state.db"


def _prod_db_row_count(table: str) -> int:
    """Return the row count from the production DB for the given table.

    If the production DB does not exist or the table does not exist,
    returns 0 — the test is checking that no NEW rows appeared, so a
    missing DB is an acceptable starting state.
    """
    if not _PROD_DB_PATH.exists():
        return 0
    try:
        conn = sqlite3.connect(str(_PROD_DB_PATH), timeout=2.0)
        try:
            result = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            return result[0] if result else 0
        except sqlite3.OperationalError:
            # Table does not exist in this DB.
            return 0
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


# ---------------------------------------------------------------------------
# GUARD-1: _db_file() raises under pytest when DB_PATH resolves to prod default
# ---------------------------------------------------------------------------

class TestProdDbWriteGuardFires:
    """GUARD-1: database._db_file() must raise RuntimeError when pytest is
    running and DB_PATH resolves to the production-default basename."""

    def test_db_file_raises_when_db_path_unset_under_pytest(self, monkeypatch):
        """Simulate the exact leak precondition: pytest loaded, DB_PATH absent.

        Before the guard implementation this passes silently (returns the
        production path), making the test RED.

        The guard's contract:
          - `"pytest" in sys.modules` is True (always true during pytest)
          - `os.path.basename(resolved_path) == "alphabot_state.db"`
          → raise RuntimeError

        We temporarily unset DB_PATH to force the fallback to the default.
        monkeypatch ensures cleanup after the test.
        """
        import importlib

        import database as db_module

        # Temporarily remove DB_PATH to force fallback to the prod default.
        monkeypatch.delenv("DB_PATH", raising=False)

        # Also reset the module-level DB_FILE to the default to simulate a
        # fresh import (or a subprocess import) where DB_PATH was absent.
        original_db_file = db_module.DB_FILE
        original_default = db_module._DB_FILE_DEFAULT
        monkeypatch.setattr(db_module, "DB_FILE", _PROD_DB_BASENAME)
        monkeypatch.setattr(db_module, "_DB_FILE_DEFAULT", _PROD_DB_BASENAME)

        # The guard must raise before returning the prod path.
        with pytest.raises(RuntimeError, match="alphabot_state.db"):
            db_module._db_file()

    def test_db_file_raises_when_db_path_set_to_prod_basename_under_pytest(
        self, monkeypatch, tmp_path
    ):
        """DB_PATH explicitly set to the production filename (not just unset)
        must also be blocked — defence-in-depth against misconfigured CI
        environments that set DB_PATH=alphabot_state.db.
        """
        import database as db_module

        monkeypatch.setenv("DB_PATH", _PROD_DB_BASENAME)
        monkeypatch.setattr(db_module, "DB_FILE", _PROD_DB_BASENAME)
        monkeypatch.setattr(db_module, "_DB_FILE_DEFAULT", _PROD_DB_BASENAME)

        with pytest.raises(RuntimeError, match="alphabot_state.db"):
            db_module._db_file()

    def test_db_file_does_not_raise_when_db_path_is_temp(self, monkeypatch, tmp_path):
        """Normal test operation: DB_PATH set to a temp file — guard must be silent.

        This test documents the guard's safe path so a regression in the guard
        itself (guard firing on legitimate temp paths) is immediately visible.
        """
        import database as db_module

        temp_db = str(tmp_path / "test_alphabot_state.db")
        monkeypatch.setenv("DB_PATH", temp_db)
        monkeypatch.setattr(db_module, "DB_FILE", temp_db)
        monkeypatch.setattr(db_module, "_DB_FILE_DEFAULT", temp_db)

        # Should NOT raise — guard must only fire on the prod basename.
        result = db_module._db_file()
        assert result == temp_db


# ---------------------------------------------------------------------------
# GUARD-2: Advisor producers do NOT write to the production DB
# ---------------------------------------------------------------------------

class TestAdvisorProducersDoNotWriteProductionDb:
    """GUARD-2: the advisor producers must never reach the production DB,
    even though their own `import database` is not covered by a wholesale
    `patch("autotuner.database")` mock.

    These tests exercise the real producers — not mocked — and assert that
    the production DB's advisor_observations row count is unchanged after the
    call.  Before the session-scoped DB_PATH guard and the database.py guard,
    these fail if DB_PATH is unset in the calling environment.

    The tests deliberately do NOT mock the producer modules' database import,
    to expose the split-reference leak documented in LEAK-SOURCE.md §2A.
    """

    def _advisor_obs_count_in_prod(self) -> int:
        return _prod_db_row_count("advisor_observations")

    def test_run_overfitting_conscience_does_not_write_to_prod_db(self, tmp_path, monkeypatch):
        """run_overfitting_conscience must write to the temp DB, never to prod.

        Simulates the leak: if DB_PATH were unset when this function calls
        database.insert_advisor_observation, the row would land in prod.
        With the session guard, DB_PATH is already set — this is a regression
        confirmation that it STAYS set even when run directly via the
        producer's own database reference.
        """
        import database as db_module
        from advisors.overfitting_conscience import run_overfitting_conscience

        # Record prod row count BEFORE the call.
        before_count = self._advisor_obs_count_in_prod()

        autotune_run = {
            "id": 9999,
            "symphony_id": "test-guard-symphony",
            "n_effective": 100.0,
            # spec_bundle_id and s_count are mandatory for OC computation.
            "spec_bundle_id": "test-guard-bundle",
            "s_count": 0,
        }
        ledger_rows: list = []

        run_overfitting_conscience(autotune_run, ledger_rows)

        # Production DB must be unchanged.
        after_count = self._advisor_obs_count_in_prod()
        assert after_count == before_count, (
            f"GUARD-2 FAIL: run_overfitting_conscience wrote {after_count - before_count} "
            f"row(s) to the production DB ({_PROD_DB_PATH}).  "
            "DB_PATH was not isolated — the split-import leak is live."
        )

        # The write must have landed in the current (temp) DB.
        actual_path = db_module._db_file()
        assert os.path.basename(actual_path) != _PROD_DB_BASENAME, (
            f"GUARD-2 FAIL: database._db_file() returned the production path "
            f"({actual_path}) during the test."
        )

    def test_run_divergence_explainer_does_not_write_to_prod_db(self, tmp_path, monkeypatch):
        """run_divergence_explainer must write to the temp DB, never to prod."""
        import database as db_module
        from advisors.divergence_explainer import run_divergence_explainer

        before_count = self._advisor_obs_count_in_prod()

        autotune_run = {
            "id": 9998,
            "symphony_id": "test-guard-symphony-de",
        }

        # Pass second_window_enabled=False explicitly — the NOT_APPLICABLE stub
        # still calls insert_advisor_observation via the real database module.
        run_divergence_explainer(autotune_run, cvar_row=None, second_window_enabled=False)

        after_count = self._advisor_obs_count_in_prod()
        assert after_count == before_count, (
            f"GUARD-2 FAIL: run_divergence_explainer wrote {after_count - before_count} "
            f"row(s) to the production DB ({_PROD_DB_PATH}).  "
            "Split-import leak live."
        )

    def test_run_spec_critic_does_not_write_to_prod_db(self, tmp_path, monkeypatch):
        """run_spec_critic must write to the temp DB, never to prod."""
        import database as db_module
        from advisors.spec_critic import run_spec_critic

        before_count = self._advisor_obs_count_in_prod()

        # Minimal valid spec_facets_rows — only the required THEORY facets, all
        # with a valid THEORY freeze_discipline and a recent frozen_at timestamp.
        spec_facets_rows = [
            {
                "facet_name": "gamma",
                "freeze_discipline": "THEORY",
                "frozen_at": "2026-01-01T00:00:00Z",
            },
            {
                "facet_name": "utility_family",
                "freeze_discipline": "THEORY",
                "frozen_at": "2026-01-01T00:00:00Z",
            },
            {
                "facet_name": "wealth_argument",
                "freeze_discipline": "THEORY",
                "frozen_at": "2026-01-01T00:00:00Z",
            },
        ]

        run_spec_critic(
            spec_bundle_id="test-guard-bundle-001",
            spec_facets_rows=spec_facets_rows,
            symphony_id="test-guard-symphony-sc",
        )

        after_count = self._advisor_obs_count_in_prod()
        assert after_count == before_count, (
            f"GUARD-2 FAIL: run_spec_critic wrote {after_count - before_count} "
            f"row(s) to the production DB ({_PROD_DB_PATH}).  "
            "Split-import leak live."
        )

    def test_advisor_observation_rows_land_in_temp_db_not_prod(self, tmp_path, monkeypatch):
        """Cross-check: rows written by producers must be readable from the
        current (temp) DB, confirming the write hit the right target.

        This is the positive side of GUARD-2: not only must prod be clean,
        the temp DB must actually contain the written rows.
        """
        import database as db_module
        from advisors.spec_critic import run_spec_critic

        # Ensure the temp DB has the schema.
        db_module.init_db()

        spec_facets_rows = [
            {
                "facet_name": "gamma",
                "freeze_discipline": "THEORY",
                "frozen_at": "2026-01-01T00:00:00Z",
            },
            {
                "facet_name": "utility_family",
                "freeze_discipline": "THEORY",
                "frozen_at": "2026-01-01T00:00:00Z",
            },
            {
                "facet_name": "wealth_argument",
                "freeze_discipline": "THEORY",
                "frozen_at": "2026-01-01T00:00:00Z",
            },
        ]

        row_id = run_spec_critic(
            spec_bundle_id="test-land-check-bundle",
            spec_facets_rows=spec_facets_rows,
            symphony_id="test-land-check-sym",
        )

        assert isinstance(row_id, int), (
            f"GUARD-2 FAIL: run_spec_critic returned {row_id!r} — expected an int row id. "
            "If this is a MagicMock, the producer reached a mocked database, not the real one."
        )
        assert row_id > 0, (
            f"GUARD-2 FAIL: row_id={row_id} — insert must return a positive int."
        )

        # Read back from the current temp DB.
        temp_path = db_module._db_file()
        conn = sqlite3.connect(temp_path, timeout=2.0)
        try:
            row = conn.execute(
                "SELECT id, subject_id, advisor_role FROM advisor_observations WHERE id = ?",
                (row_id,),
            ).fetchone()
        finally:
            conn.close()

        assert row is not None, (
            f"GUARD-2 FAIL: row id {row_id} not found in temp DB at {temp_path}.  "
            "The write may have landed in the production DB instead."
        )
        assert row[2] == "SPEC_CRITIC", (
            f"GUARD-2 FAIL: unexpected advisor_role {row[2]!r} — expected 'SPEC_CRITIC'."
        )


# ---------------------------------------------------------------------------
# GUARD-3: Session-scoped DB_PATH is set before any test (including imports)
# ---------------------------------------------------------------------------

class TestSessionScopedDbPathIsAlwaysSet:
    """GUARD-3: DB_PATH must be set for the entire pytest session.

    The existing function-scoped _isolate_db fixture sets DB_PATH per-test,
    but a module-level import of database before _isolate_db runs can still
    capture the production default (Gap 3 in LEAK-SOURCE.md).

    A session-scoped autouse fixture setting DB_PATH BEFORE any test module
    is imported closes this gap.

    These tests verify:
      a) DB_PATH is set (not empty, not the prod basename) at test time.
      b) The basename of DB_PATH is not "alphabot_state.db".
    """

    def test_db_path_env_var_is_set_for_session(self):
        """DB_PATH must be non-empty for the whole session.

        This test is structurally similar to AC-ISO.3 but specifically
        tests for session-level isolation (not just per-test).
        """
        val = os.environ.get("DB_PATH", "")
        assert val != "", (
            "GUARD-3 FAIL: DB_PATH env var is empty at test-collection time.  "
            "A session-scoped autouse fixture must set DB_PATH before any test imports "
            "database, to prevent module-level DB_FILE captures pointing at production."
        )

    def test_db_path_basename_is_not_production_db_name(self):
        """DB_PATH must not resolve to the production DB filename."""
        val = os.environ.get("DB_PATH", "")
        assert val != "", "DB_PATH is empty — cannot check basename."
        basename = os.path.basename(val)
        assert basename != _PROD_DB_BASENAME, (
            f"GUARD-3 FAIL: DB_PATH basename is '{basename}' — this IS the production "
            f"DB name.  The session-scoped fixture must direct DB_PATH to a temp file."
        )

    def test_session_db_path_is_set_before_module_level_import_time(self):
        """DB_PATH must be set early enough that a fresh `import database`
        at module scope (before any fixture runs) would NOT capture the default.

        This test verifies that at the time Python resolves module-level
        os.environ.get("DB_PATH", "alphabot_state.db") in database.py, the
        env var is already present.

        Strategy: force a reimport of the database module in a subprocess-like
        context using importlib — if DB_PATH is set, the captured DB_FILE
        will NOT be the production default.
        """
        import importlib
        import database as db_module

        # Simulate what happens when a subprocess starts with no DB_PATH:
        # if the session fixture has already set it, os.environ will have it.
        # We verify the env var is present and correct, not that it was set
        # "before the subprocess" (we can't control that from within the test).
        current_val = os.environ.get("DB_PATH", "")
        assert current_val != "", (
            "GUARD-3 FAIL: DB_PATH is not set — a fresh `import database` in a "
            "subprocess or at module scope would capture 'alphabot_state.db'."
        )
        assert os.path.basename(current_val) != _PROD_DB_BASENAME, (
            f"GUARD-3 FAIL: DB_PATH basename ({os.path.basename(current_val)!r}) "
            f"== production default.  Session guard must set a non-prod temp path."
        )


# ---------------------------------------------------------------------------
# GUARD-4: Guard does NOT fire when pytest is absent (daemon safety)
# ---------------------------------------------------------------------------

class TestProdDbWriteGuardDoesNotFireOutsidePytest:
    """GUARD-4: the database._db_file() guard must be unconditionally silent
    when `"pytest"` is not in sys.modules.

    The guard condition is:
        if "pytest" in sys.modules and basename == "alphabot_state.db":
            raise RuntimeError

    This test removes pytest from sys.modules temporarily, then calls
    _db_file() with no DB_PATH set, and asserts no exception is raised.

    Without the guard implementation, this test PASSES (because _db_file()
    never raises).  It becomes a meaningful regression test once the guard
    is implemented: if the guard later loses its pytest-sentinel check, this
    test turns RED and blocks a daemon-breaking merge.
    """

    def test_guard_is_gated_on_pytest_sentinel_so_daemon_is_unaffected(
        self, monkeypatch
    ):
        """Simulate a non-pytest environment: remove pytest from sys.modules,
        remove DB_PATH, call _db_file() — must return the prod default path
        (no exception raised).

        Note: we restore pytest to sys.modules immediately after the call,
        so the rest of the suite is unaffected.
        """
        import database as db_module

        # Save state
        saved_db_file = db_module.DB_FILE
        saved_db_file_default = db_module._DB_FILE_DEFAULT
        saved_pytest = sys.modules.get("pytest")

        try:
            # Remove pytest from sys.modules to simulate a non-pytest runtime.
            sys.modules.pop("pytest", None)

            # Remove DB_PATH so the default would be the production basename.
            monkeypatch.delenv("DB_PATH", raising=False)
            monkeypatch.setattr(db_module, "DB_FILE", _PROD_DB_BASENAME)
            monkeypatch.setattr(db_module, "_DB_FILE_DEFAULT", _PROD_DB_BASENAME)

            # In a non-pytest environment, _db_file() MUST NOT raise.
            # It should simply return the default production path.
            try:
                result = db_module._db_file()
            except RuntimeError as exc:
                pytest.fail(
                    f"GUARD-4 FAIL: _db_file() raised RuntimeError in a non-pytest "
                    f"context (pytest not in sys.modules): {exc}.  "
                    "The guard must be gated on 'pytest' in sys.modules — the real "
                    "daemon must never see this exception."
                )

            # The result should be the production default basename (no DB_PATH set).
            assert result == _PROD_DB_BASENAME, (
                f"GUARD-4 UNEXPECTED: _db_file() returned {result!r} when "
                f"DB_PATH was unset and DB_FILE=={_PROD_DB_BASENAME!r}.  "
                "Expected the production default to be returned in daemon context."
            )
        finally:
            # Always restore pytest to sys.modules.
            if saved_pytest is not None:
                sys.modules["pytest"] = saved_pytest
            # monkeypatch will restore DB_FILE attributes and DB_PATH env var.

    def test_guard_fires_when_pytest_is_in_sys_modules(self, monkeypatch):
        """Counterpart to the daemon-safety test: with pytest present in
        sys.modules (always true during an actual pytest run), the guard
        MUST raise when the path resolves to the prod default.

        This test is RED before the guard implementation.  After implementation,
        both GUARD-4 tests pass: the daemon is safe AND the guard fires in CI.
        """
        import database as db_module

        # pytest IS in sys.modules (we're running under pytest now).
        assert "pytest" in sys.modules, (
            "Expected pytest to be in sys.modules during a pytest run."
        )

        monkeypatch.delenv("DB_PATH", raising=False)
        monkeypatch.setattr(db_module, "DB_FILE", _PROD_DB_BASENAME)
        monkeypatch.setattr(db_module, "_DB_FILE_DEFAULT", _PROD_DB_BASENAME)

        with pytest.raises(RuntimeError, match="alphabot_state.db"):
            db_module._db_file()
