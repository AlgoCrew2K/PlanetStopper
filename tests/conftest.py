"""
Pytest configuration and shared fixtures for Planet Stopper test suite.
"""

import os
import pathlib
import tempfile

import pytest

# ---------------------------------------------------------------------------
# Session-level DB_PATH guard — must fire BEFORE any module is imported.
#
# database.py calls init_db() at module level (line 3052), which fires during
# collection — before any fixture, including session-scoped ones.  A fixture
# is too late; the guard in database._db_file() would raise during collection.
#
# pytest_configure() is the earliest hook in the pytest lifecycle, called
# before any collection or import.  Setting DB_PATH here ensures that when
# pytest workers import database (triggering init_db → get_connection →
# _db_file), DB_PATH is already set to a temp path.
#
# The per-test _isolate_db fixture (below) provides per-test isolation for
# read-back assertions.  Both mechanisms coexist: pytest_configure sets a
# safe session-wide default, _isolate_db overrides per-test.
# ---------------------------------------------------------------------------

_SESSION_TEMP_DIR: tempfile.TemporaryDirectory | None = None


def pytest_configure(config):
    """Set DB_PATH before any test module is imported (pre-collection hook).

    This is the earliest possible hook — it runs before collection begins,
    ensuring that database.py's module-level init_db() call never resolves
    to the production alphabot_state.db when running under pytest.
    """
    global _SESSION_TEMP_DIR
    if "DB_PATH" not in os.environ:
        _SESSION_TEMP_DIR = tempfile.TemporaryDirectory(prefix="pytest_session_db_")
        session_db = os.path.join(_SESSION_TEMP_DIR.name, "session_alphabot_state.db")
        os.environ["DB_PATH"] = session_db

    # Route the Atlas cache DB to a session-temp path so tests never read a
    # stale production cache (alphabot_atlas_cache.db in the project root).
    # This ensures tests that mock MongoClient see a cold cache, not a live
    # cached result from a previous operator run with real credentials.
    if "ATLAS_CACHE_DB_PATH" not in os.environ:
        if _SESSION_TEMP_DIR is None:
            _SESSION_TEMP_DIR = tempfile.TemporaryDirectory(prefix="pytest_session_db_")
        atlas_cache_db = os.path.join(_SESSION_TEMP_DIR.name, "session_atlas_cache.db")
        os.environ["ATLAS_CACHE_DB_PATH"] = atlas_cache_db

    # ---------------------------------------------------------------------
    # Bound nested parallelism in the TEST ENVIRONMENT ONLY (memory-blowup
    # mitigation; companion to the "-n 2" xdist cap in pyproject.toml addopts).
    #
    # Two production code paths spawn all-core child processes:
    #   * synthetic_history.generate_synthetic_history -> joblib.Parallel(n_jobs=-1)
    #   * autotuner study.optimize(n_jobs=OPTUNA_N_JOBS) (already defaults to 1)
    # Nested inside N pytest-xdist workers, the fan-out is multiplicative
    # (workers x cores) and committed >90 GB of memory, causing two
    # CRITICAL_PROCESS_DIED host bugchecks (2026-06-11, 2026-06-12).
    #
    # Forcing both degrees to 1 here is reproducibility-NEUTRAL:
    #   - the joblib replay is content-keyed/deterministic (per-(symphony,day)
    #     SHA-256 MC seed), order-independent of worker scheduling;
    #   - OPTUNA_N_JOBS=1 is already the production default and is the
    #     SQLite-writer-lock-safe setting Optuna determinism relies on.
    # Production leaves both unset, so live behavior is unchanged.
    #
    # setdefault preserves an explicit operator override (e.g. a perf harness
    # that deliberately wants more parallelism in a controlled environment).
    os.environ.setdefault("ALPHABOT_MAX_JOBS", "1")
    os.environ.setdefault("OPTUNA_N_JOBS", "1")


def pytest_unconfigure(config):
    """Clean up the session temp directory after the test run."""
    global _SESSION_TEMP_DIR
    if _SESSION_TEMP_DIR is not None:
        try:
            _SESSION_TEMP_DIR.cleanup()
        except OSError:
            pass
        _SESSION_TEMP_DIR = None


@pytest.fixture(autouse=True, scope="session")
def _session_db_guard():
    """Session-scoped fixture confirming DB_PATH is set (belt-and-suspenders).

    The real work is done by pytest_configure above.  This fixture exists so
    tests can detect whether the session guard is active (e.g. GUARD-3 tests),
    and as an explicit documentation anchor for the isolation contract.
    """
    assert os.environ.get("DB_PATH", ""), (
        "_session_db_guard: DB_PATH is not set — pytest_configure failed to "
        "initialise the session temp DB.  Check conftest.py."
    )
    yield


@pytest.fixture
def fixtures_dir() -> pathlib.Path:
    """Return the absolute path to the tests/fixtures directory."""
    return pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _disable_csrf_for_tests(monkeypatch):
    """Disable the CSRF check for the entire test suite (A-3).

    The CSRF token mechanism (app._csrf_check_enabled) is designed to be
    bypassable in test contexts — the SOP protection CSRF provides has no
    meaning inside pytest's in-process test client.  Setting this flag ensures
    every existing POST test still passes without injecting a token header.

    Security tests that want to verify the CSRF enforcement itself should
    *not* rely on this fixture; they should set _csrf_check_enabled=True
    explicitly and supply/omit the X-CSRF-Token header under test.
    """
    import app as _app_module

    monkeypatch.setattr(_app_module, "_csrf_check_enabled", False)


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    """Redirect DB_PATH to a per-test temp file for every test in the suite.

    Uses monkeypatch.setenv so database._db_file() always returns the temp
    path regardless of DB_FILE module-attribute state.  Monkeypatch restores
    the original env after each test, so the live alphabot_state.db is never
    touched by any test run.

    Calls init_db() after setting the path so any code that opens the DB
    directly (e.g. engine sub-modules importing database) finds a fully-
    initialised schema without needing a separate fixture call.
    """
    import database as _db

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test_alphabot_state.db"))
    _db.init_db()
