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


def _assert_safe_worker_count(numprocesses) -> None:
    """Reject unsafe xdist worker counts before install_from_env() runs (AC-6).

    Raises SystemExit when numprocesses is "auto" or an integer > 4.
    Accepts 0, 1, 2, 3, 4, None (xdist disabled or not active).

    Extracted as a top-level helper so tests can call it directly without
    triggering cap-install or env side-effects (testability seam).

    Background: "-n auto" uses all CPU cores.  On the 24-core dev host this
    fans out to 24 xdist workers × ~1-2 GB each = ~238 GB committed, which
    caused two Kernel-Power 41 hard-reboots (2026-06-21).  Maximum safe = 4.
    This guard runs on Linux CI too so no uncapped run sneaks through pipelines.
    """
    if numprocesses is None:
        return
    if numprocesses == "auto" or (isinstance(numprocesses, int) and numprocesses > 4):
        raise SystemExit(
            f"[mem-cap] Rejecting xdist numprocesses={numprocesses!r}: "
            "exceeds the safe ceiling of 4 on this host (67.8 GB RAM + 4 GB pf). "
            "Two Kernel-Power 41 hard-reboots were caused by -n auto fan-out. "
            "Use -n 0..4.  To opt out, set ALPHABOT_TEST_MEM_CAP_GB=0."
        )


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

    # AC-6: Reject unsafe xdist worker counts EARLY — before install_from_env().
    _assert_safe_worker_count(getattr(config.option, "numprocesses", None))

    # Install a Windows Job Object total-tree memory cap before xdist workers spawn.
    # On Linux/CI this is a no-op.  Cap value comes from ALPHABOT_TEST_MEM_CAP_GB
    # (default 24 GB); set to 0 to disable with a loud warning.
    # See tests/_mem_cap.py and DE-TEST-MEMCAP-001 in DECISIONS.md.
    if os.name == "nt":
        from tests import _mem_cap

        _mem_cap.install_from_env()


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
def _disable_auth_for_tests(monkeypatch):
    """Disable the dashboard auth gate for the entire test suite (AC-1/AC-8).

    The auth gate (app._auth_check_enabled) is bypassable in test contexts so
    the ~7000 existing route tests can hit protected routes without being
    redirected to /login.

    Auth gate tests in tests/app/test_dashboard_auth.py that need the real gate
    re-enable it via monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    in their per-test auth_client / auth_client_authed fixtures.
    """
    import app as _app_module

    monkeypatch.setattr(_app_module, "_auth_check_enabled", False)
    # Reset the in-memory throttle dict between tests so failed-attempt counts
    # from one test do not bleed into the next.
    _app_module._AUTH_FAILED_ATTEMPTS.clear()


@pytest.fixture(autouse=True)
def _reset_account_totals_cache():
    """Reset app._account_totals_cache to a clean state before and after every test.

    Root cause addressed: tests/app/test_app_routes.py and tests/execution/
    test_reliability_expansion.py exercise trigger_alpha_bot(), which calls
    _notify_cycle_complete() in its ``finally`` block.  _notify_cycle_complete()
    does two things that leave the cache dirty:

      1. Calls _account_totals_cache.mark_stale() — sets _stale=True, causing
         every subsequent _StaleFlagDict.get() to return None regardless of what
         is in the dict.
      2. Spawns a daemon "cycle-refresh" thread that runs _refresh_account_totals().
         In CI (no real Composer credentials), the thread fails and never calls
         refresh_written(), so _stale stays True until either clear() or a
         successful refresh.

    Without this fixture, any test on the same xdist worker AFTER those tests
    will see _account_totals_cache.get("portfolio_cr") return None — even after
    monkeypatch.setitem injects a value — because _StaleFlagDict.get() short-
    circuits on _stale=True before reading the underlying dict.

    Fix: before and after every test, join any live "cycle-refresh" threads (up
    to _CYCLE_REFRESH_JOIN_TIMEOUT each) so no lagging thread can race with the
    next test's cache state, then hard-reset the cache via clear() (resets dict
    contents + _stale flag per _StaleFlagDict.clear) and refresh_written()
    (belt-and-suspenders in case a thread squeezed in a mark_stale() between the
    join and the clear).

    This fixture is scoped to the entire suite.  tests/realtime_push/conftest.py
    defines an identically-named fixture whose narrower scope overrides this one
    for realtime_push tests — that is intentional (the local fixture is fully
    compatible and adds realtime_push-specific documentation).
    """
    import threading

    import app as _app_module

    _CYCLE_REFRESH_JOIN_TIMEOUT = 0.5

    def _drain_and_reset():
        for t in threading.enumerate():
            if t.name == "cycle-refresh" and t.is_alive():
                t.join(timeout=_CYCLE_REFRESH_JOIN_TIMEOUT)
        cache = _app_module._account_totals_cache
        cache.clear()  # dict.clear() works on both plain dict and _StaleFlagDict
        # refresh_written() only exists on _StaleFlagDict; guard for tests that
        # monkeypatch.setattr(_account_totals_cache, plain_dict) during the test body
        # — those tests' teardown runs before monkeypatch restores the original.
        _rw = getattr(cache, "refresh_written", None)
        if callable(_rw):
            _rw()  # belt-and-suspenders: ensure _stale=False even if a thread
            # squeezed in a mark_stale() between the join and the clear

    _drain_and_reset()
    yield
    _drain_and_reset()


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
