"""
Pytest configuration and shared fixtures for AlphaBot test suite.
"""

import pathlib

import pytest


@pytest.fixture
def fixtures_dir() -> pathlib.Path:
    """Return the absolute path to the tests/fixtures directory."""
    return pathlib.Path(__file__).parent / "fixtures"


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
