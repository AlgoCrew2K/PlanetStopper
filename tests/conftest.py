"""
Pytest configuration and shared fixtures for Planet Stopper test suite.
"""

import pathlib

import pytest


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
