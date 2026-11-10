"""
Pytest configuration and shared fixtures for Planet Stopper test suite.
"""

import os
import pathlib
import tempfile
import warnings
from unittest.mock import patch

import pytest

from tests._function_leak_guard import FunctionLeakGuard

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
#
# PER-WORKER PATH FIX (ledger UPDATE-18 flake class; exit-friction-realized-
# savings cycle, 2026-07-25, 2/2 reproduction on PR #113 CI same night):
# under xdist (this project's standing addopts: "-n 2 --dist loadfile"),
# EVERY worker subprocess (gw0, gw1, ...) — and the controller's own
# collection pass — INHERITS DB_PATH from the controller's environment via
# standard OS subprocess inheritance. The `"DB_PATH" not in os.environ`
# guard below is therefore False for every worker, so before this fix every
# worker silently reused the controller's ONE session DB file. Any test
# file whose import chain transitively reaches database.py (e.g. via
# acceptance_gate -> advisors.overfitting_conscience/spec_critic ->
# database) then triggers database.py's unconditional module-level
# init_db() — full schema DDL, including the WAL-mode PRAGMA — against that
# SAME shared SQLite file from multiple OS processes concurrently during
# collection, occasionally exceeding the busy-timeout
# ("sqlite3.OperationalError: database is locked", worse on Windows'
# distinct file-locking behavior vs POSIX). --dist loadfile's hash-based
# file-to-worker distribution shifts with every new test file added to the
# suite, so a previously-rare interleaving can start reproducing reliably
# without any new file doing anything wrong on its own — exactly what
# happened here (2/2 on this cycle's PR, zero new import-time work added).
#
# FIX: branch on PYTEST_XDIST_WORKER (an env var xdist sets fresh inside
# EVERY worker subprocess specifically for this per-worker-resource
# pattern) so each worker gets its own unique temp DB path regardless of
# what it inherited. Applies to both DB_PATH and its ATLAS_CACHE_DB_PATH
# sibling below (identical latent shared-file exposure). database.py's
# module-level init_db() itself is intentionally untouched — out of scope;
# this conftest guard is the complete fix for the CI race.
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


# Loopback discard port: nothing listens, so a connection attempt is refused at once.
_UNROUTABLE_ANTHROPIC_URL = "http://127.0.0.1:9"


def _live_anthropic_selected(markexpr: str) -> bool:
    """True when the -m expression selects a test that carries only the ``live`` marker.

    The default run deselects live tests, and then no test may reach the real
    Anthropic API; an opt-in live run keeps its own endpoint. An empty
    expression selects everything, live tests included. An expression pytest
    cannot evaluate is treated as not selecting live, so the guard fails closed.
    """
    if not markexpr:
        return True
    try:
        from _pytest.mark.expression import Expression

        return bool(Expression.compile(markexpr).evaluate(lambda name, **_kwargs: name == "live"))
    except Exception:
        return False


def pytest_configure(config):
    """Set DB_PATH before any test module is imported (pre-collection hook).

    This is the earliest possible hook — it runs before collection begins,
    ensuring that database.py's module-level init_db() call never resolves
    to the production alphabot_state.db when running under pytest.

    PYTEST_XDIST_WORKER (set by xdist inside every worker subprocess, e.g.
    "gw0"/"gw1"; absent in the controller / no-xdist runs) forces a
    worker-unique path even when DB_PATH/ATLAS_CACHE_DB_PATH were already
    inherited from the controller's environment — see the module-level
    comment above for the full race this closes.
    """
    global _SESSION_TEMP_DIR
    worker_id = os.environ.get("PYTEST_XDIST_WORKER")
    suffix = f"_{worker_id}" if worker_id else ""

    if worker_id is not None or "DB_PATH" not in os.environ:
        if _SESSION_TEMP_DIR is None:
            _SESSION_TEMP_DIR = tempfile.TemporaryDirectory(prefix="pytest_session_db_")
        session_db = os.path.join(_SESSION_TEMP_DIR.name, f"session_alphabot_state{suffix}.db")
        os.environ["DB_PATH"] = session_db

    # Route the Atlas cache DB to a session-temp path so tests never read a
    # stale production cache (alphabot_atlas_cache.db in the project root).
    # This ensures tests that mock MongoClient see a cold cache, not a live
    # cached result from a previous operator run with real credentials.
    if worker_id is not None or "ATLAS_CACHE_DB_PATH" not in os.environ:
        if _SESSION_TEMP_DIR is None:
            _SESSION_TEMP_DIR = tempfile.TemporaryDirectory(prefix="pytest_session_db_")
        atlas_cache_db = os.path.join(_SESSION_TEMP_DIR.name, f"session_atlas_cache{suffix}.db")
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

    # Live-Anthropic guard, mirroring the session Mongo guard below: unless the
    # run opted into live tests, point the SDK's default endpoint at an address
    # nothing serves, so a test that forgets the HTTP fake fails loudly instead
    # of listing models or sending a metered request to the real API.
    if not _live_anthropic_selected(getattr(config.option, "markexpr", "")):
        os.environ["ANTHROPIC_BASE_URL"] = _UNROUTABLE_ANTHROPIC_URL

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


# ---------------------------------------------------------------------------
# Stand-in functions never outlive the test that left them.
#
# A suite conftest stubs a module function for every test under it (tests/ui/conftest.py:
# database.load_state, app.get_api_state_dict), and a test that patches the same name with
# monkeypatch saves the stub as the value to restore. Whether the stub survives depends on
# which of the two undos runs last, so one test can leave it on the module for every later
# test in the worker, and the victims are unrelated files. A protocol wrapper runs after
# every fixture of the test is finalized, whatever order they were created in, so it sees
# the final binding. It puts the real function back and reports the test that left the
# stand-in as a pytest warning (forwarded to the run's warning summary under xdist), because
# a silent restore would hide the bug class.
# ---------------------------------------------------------------------------

_FUNCTION_LEAK_GUARD = FunctionLeakGuard()


def pytest_collection_finish(session):
    # No test has run yet, so nothing recorded here can be a stand-in. Modules a test would
    # import lazily are imported now, or its first import could already hold one.
    _FUNCTION_LEAK_GUARD.snapshot(import_modules=True)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    _FUNCTION_LEAK_GUARD.snapshot()
    yield
    for leak in _FUNCTION_LEAK_GUARD.restore():
        message = (
            f"{item.nodeid} left a stand-in on the module: {leak.describe()}. "
            "The recorded value was put back; patch a function with a context manager that is "
            "undone before the suite's own stub, or request the stub fixture so it is; a test "
            "that reloads a module restores its DB_PATH-derived paths before it deletes the file."
        )
        item.config.hook.pytest_warning_recorded.call_historic(
            kwargs={
                "warning_message": warnings.WarningMessage(
                    UserWarning(message), UserWarning, __file__, 0
                ),
                "nodeid": item.nodeid,
                "when": "runtest",
                "location": None,
            }
        )


@pytest.fixture
def fixtures_dir() -> pathlib.Path:
    """Return the absolute path to the tests/fixtures directory."""
    return pathlib.Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Session-level live-Mongo/Atlas guard — mirrors database._db_file()'s
# pytest-sentinel pattern (database.py:55-74), applied to the Atlas/Mongo
# read path instead of the state DB.
#
# Operator escalation (real-money-critical, 2026-07-11): a test in this suite
# (test_fable_calls_per_symphony_are_bounded_by_a_budget_cap) forgot to mock
# advisors.community_strats.load_community_strategies, and the code path it
# exercised (advisors.frontrunner_builder._gather_atlas_frontrunner_patterns,
# wired in during AC-3, 1b3e9fb) fell through to community_strats.py's
# _fetch_fn, which calls the REAL pymongo.MongoClient(os.environ["MONGO_URI"])
# — a genuine, BILLED Atlas connection attempt, 45 times in that one test.
# database._db_file()'s guard makes an equivalent state-DB leak structurally
# impossible (loud RuntimeError under pytest, gated on "pytest" in
# sys.modules so production is never affected); this fixture is the analog
# for the Atlas/Mongo read path.
#
# pymongo.MongoClient is the SINGLE real network choke point across the whole
# Atlas surface — advisors/atlas_cache.py's own docstring states it "imports
# no database.py, autotuner, or pymongo code" (enforced by an AST test,
# tests/advisors/test_atlas_cache.py:627), and advisors/community_strats.py's
# _fetch_fn is the only call site anywhere in the codebase that constructs
# one (grep-verified). Patching it here at the pymongo module level, once,
# session-wide, covers every current and future Atlas/Mongo caller — nobody
# has to remember to mock it per-test.
#
# COMPOSES CORRECTLY with existing tests/advisors/test_community_strats*.py /
# test_atlas_cache_populate.py tests that ALREADY do their own scoped
# patch("pymongo.MongoClient", ...): unittest.mock.patch nests as a stack —
# a test's own `with patch("pymongo.MongoClient", ...)` block temporarily
# overrides this session-level sentinel for its duration and correctly
# reverts to the sentinel (never to the true unmocked pymongo.MongoClient)
# when the test's own patch exits. No existing test needed to change.
#
# A test that legitimately wants to exercise the Atlas path mocks
# community_strats.load_community_strategies (the preferred, higher-level
# seam — see tests/advisors/test_frontrunner_atlas_patterns.py) or
# pymongo.MongoClient directly (the lower-level seam used by
# test_community_strats*.py / test_atlas_cache_populate.py) — either way,
# this sentinel is simply never reached.
# ---------------------------------------------------------------------------


def _live_mongo_client_blocked(*_args, **_kwargs):
    raise RuntimeError(
        "test attempted to construct a REAL pymongo.MongoClient under pytest — "
        "this would make a genuine, BILLED Atlas connection attempt "
        "(real-money-critical guard, operator escalation 2026-07-11). "
        "Mock advisors.community_strats.load_community_strategies (preferred) "
        "or patch('pymongo.MongoClient', ...) directly if this test genuinely "
        "needs to exercise the Atlas fetch path."
    )


@pytest.fixture(autouse=True, scope="session")
def _no_live_mongo_atlas_connections():
    """Session-autouse: make any REAL pymongo.MongoClient construction under
    pytest fail loud instead of silently making a billed live Atlas call.

    See the module comment above this fixture for the full rationale and why
    this composes safely with tests that already patch pymongo.MongoClient
    themselves. Gated only on the pytest session existing (this fixture only
    runs under pytest at all) — no "pytest in sys.modules" check needed the
    way database._db_file() requires one, since conftest.py fixtures are
    never loaded outside a pytest session.
    """
    try:
        import pymongo
    except ImportError:
        # pymongo not installed in this environment — no live connection is
        # possible anyway; nothing to guard.
        yield
        return

    with patch("pymongo.MongoClient", side_effect=_live_mongo_client_blocked):
        yield


@pytest.fixture(autouse=True, scope="session")
def _advisor_modules_bind_the_real_correlation_diagnostic():
    """Session-autouse: import the two advisor modules that copy constants from
    ``advisors.correlation_diagnostic`` before any test can fake that module.

    Many tests fake it with ``patch.dict("sys.modules", ...)`` while a page renders, and a page
    render imports ``tenure_review`` and ``retirement_recommender`` lazily. When the fake is in
    place at the first import, the module binds fakes for ``MIN_OBS_FLOOR`` and the rest, and
    ``patch.dict`` drops the module from ``sys.modules`` on exit but not from the ``advisors``
    package attribute, so every later ``from advisors import tenure_review`` keeps getting the
    poisoned module (an ``int < MagicMock`` TypeError in whichever test renders a retirement or
    tenure panel next). Importing them first makes the order of the test files irrelevant.
    """
    import advisors.retirement_recommender  # noqa: F401
    import advisors.tenure_review  # noqa: F401

    yield


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
    # The process-wide session revocation instant is read from the database once and cached:
    # forget it so no test inherits another test's logout or database.
    monkeypatch.setattr(_app_module, "_session_revoked_before_ns", None, raising=False)


@pytest.fixture(autouse=True)
def _committed_shadow_conn_for_stubbed_analytics(monkeypatch):
    """Give a request a usable shadow connection when a test replaces `app.analytics`.

    `app._committed_shadow_conn` opens `analytics._get_shadow_db_file()` read-only and a
    failure to open answers 500 (the contract a route test pins by patching the function
    itself). A test that swaps the whole `analytics` module for a mock makes that path
    a mock, so the open fails before any stubbed reader runs. For that case only, hand
    the request an empty in-memory connection: every reader is a stub and ignores it.
    A real analytics module, or a mock that returns a real path, takes the real function.
    """
    import sqlite3

    import app as _app_module

    real = _app_module._committed_shadow_conn

    def _connection_for_request(state):
        path = _app_module.analytics._get_shadow_db_file()
        # A MagicMock passes an os.PathLike check, so name the real path types.
        if isinstance(path, (str, pathlib.PurePath)):
            return real(state)
        return sqlite3.connect(":memory:")

    monkeypatch.setattr(_app_module, "_committed_shadow_conn", _connection_for_request)


@pytest.fixture(scope="session")
def _registry_seed_client():
    """One client over the captured Models API page, shared by every test's registry seed."""
    from tests._anthropic_http_fake import AnthropicHttpFake

    return AnthropicHttpFake.with_captured_page().client


@pytest.fixture(autouse=True)
def _seed_model_registry(monkeypatch, _registry_seed_client):
    """Give every test a resolved model for both knobs, through the real resolver.

    Existing tests build their LLM clients as MagicMocks, whose model listing is
    empty, so without this seed a call site would find both families unresolved
    and send nothing. The seed resolves against the captured page, never a
    literal ID. Override variables are deleted so the seed is the family
    resolution; a test that wants an override sets it itself. A test that needs
    the unresolved state requests ``unresolved_models``.
    """
    import model_config

    for env_var in ("ADVISOR_SUGGESTION_MODEL", "ADVISOR_SYNTHESIS_MODEL"):
        monkeypatch.delenv(env_var, raising=False)
    model_config._reset_registry()
    for knob in (model_config.Knob.SUGGESTION, model_config.Knob.SYNTHESIS):
        model_config.resolve(knob, _registry_seed_client)
    yield
    model_config._reset_registry()


@pytest.fixture
def unresolved_models(_seed_model_registry):
    """Opt out of the registry seed: no family is resolved when the test body starts."""
    import model_config

    model_config._reset_registry()
    yield
    model_config._reset_registry()


class _EndedEngine:
    """An engine process that has already ended: what a `subprocess.run` double leaves behind.

    The daemon starts the engine with `subprocess.Popen` (through `app._start_engine_process`) so
    it can end a run that hangs. A test that replaced `subprocess.run` with a stand-in has already
    run its whole call by the time the handle is returned, so every wait answers at once."""

    pid = 0
    stdout = None
    stderr = None

    def __init__(self, args, returncode: int) -> None:
        self.args = args
        self.returncode = returncode

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def communicate(self, input=None, timeout=None):
        return (None, None)

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None

    def send_signal(self, _sig) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None


@pytest.fixture(autouse=True)
def _engine_spawn_never_starts_the_real_engine(monkeypatch):
    """No test ever starts the real `alpha_bot_execution.py`.

    The daemon's one engine spawn is `app._start_engine_process`. Where a test installed a
    `subprocess.Popen` stand-in, the spawn calls it. Where a test replaced `subprocess.run` (every
    engine stand-in written before the engine run bound did), the spawn runs that stand-in with the
    arguments `subprocess.run` used to get and hands back the process it ended as. With neither,
    the spawn would start the real engine, so it fails the test instead."""
    import subprocess

    import app as _app_module

    real_popen, real_run = subprocess.Popen, subprocess.run

    def start_engine_process(cmd, env, log_fh):
        if subprocess.Popen is not real_popen:
            return subprocess.Popen(cmd, env=env, stdout=log_fh, stderr=log_fh)
        if subprocess.run is not real_run:
            try:
                subprocess.run(cmd, check=True, env=env, stdout=log_fh, stderr=log_fh)
            except subprocess.CalledProcessError as exc:
                return _EndedEngine(cmd, exc.returncode)
            return _EndedEngine(cmd, 0)
        raise AssertionError(
            "a test reached the real engine spawn: install an engine stand-in "
            "(StubEngine / FakeEngines) before it runs the daemon"
        )

    monkeypatch.setattr(_app_module, "_start_engine_process", start_engine_process)


@pytest.fixture(autouse=True)
def _reset_closed_market_holds(monkeypatch):
    """Start every test with no held closed-market result and no warm-up in flight.

    A closed market's results are held per snapshot identity (app._CLOSED_HOLD), and the worlds the
    tests build re-create different rows under the SAME identity (the same snapshot day and capture
    time), so a result held by one test would be served to the next as its own. The warm-up thread
    (app._warm_closed_results) reads the test database, so any still running is joined before the
    next test replaces that database.
    """
    import threading

    import app as _app_module

    def _drain_warmups():
        for t in threading.enumerate():
            if t.name == "closed-warmup" and t.is_alive():
                t.join(timeout=10.0)

    _drain_warmups()
    _app_module._CLOSED_HOLD.clear()
    monkeypatch.setattr(_app_module, "_closed_warmup_checked_mono", None)
    monkeypatch.setattr(_app_module, "_CLOSED_WARMUP_FLIGHT", threading.Lock())
    yield
    _drain_warmups()
    _app_module._CLOSED_HOLD.clear()


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

    Also resets app._account_totals_last_good (DE-EOD-BASIS-001 /review revise).
    Production intentionally NEVER clears this plain dict — it is a Tier-1
    last-good snapshot that must survive mark_stale() calls so a transient
    Composer timeout still has a fallback. But that means a REAL
    _refresh_account_totals() call in one test (e.g.
    tests/app/test_account_totals_cache.py) writes real values into it, and
    without a reset those values leak into every later render test on the same
    xdist worker — pushing them onto the Tier-1 last-good branch instead of the
    cold-cache path they intended to exercise. Mirrors the _account_totals_cache
    handling above: clear() before and after every test.
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
        last_good = getattr(_app_module, "_account_totals_last_good", None)
        if last_good is not None:
            last_good.clear()
        # The monotonic success stamp survives across tests exactly like the
        # last-good snapshot: a real refresh in one test would otherwise put a
        # later test that seeds last-good by hand inside the account-basis grace.
        _app_module._account_totals_last_success_mono = None
        # The close-totals latch is process state of the daemon like the last-good snapshot:
        # a real refresh in one test must not hand its totals to a later test's close snapshot.
        if hasattr(_app_module, "_frozen_account_totals"):
            _app_module._frozen_account_totals = None

    _drain_and_reset()
    yield
    _drain_and_reset()


@pytest.fixture(autouse=True)
def _pin_execution_start_time_to_code_default(monkeypatch):
    """Pin alpha_bot_execution.EXECUTION_START_TIME to the code-default "09:30"
    for the entire test suite (math-r1 ADDENDUM 5 — latent test-isolation gap).

    Root cause (r1-tuner, 2026-07-17): the local worktree's .env carries
    EXECUTION_START_TIME='9:35' (the droplet-real operator value). Before
    AC-5, no replay code path consulted this env var for exit-decision
    purposes, so no test noticed the local-env drift. AC-5 (correctly)
    wired autotuner._replay_execution_start_time() -> the real
    alpha_bot_execution.EXECUTION_START_TIME into the replay's action-phase
    gate (_replay_exit_tick, via replay_exit_sequence / run_simulation /
    _collect_sim_returns / _collect_sim_returns_dated). Once that gate went
    live, every pre-existing short hand-specified-tick test (tick_idx 0-4,
    5 ticks or fewer) that never pinned this value found its entire tick
    sequence sitting BEFORE the '9:35' action-phase offset (5) — the whole
    action phase collapsed to a no-op regardless of the scenario under test.
    26 failures across 10 files in tests/autotuner/, all one root cause.

    This is exactly the pattern test_c3_replay_exit_parity.py's own
    `_pin_execution_start_time_to_session_open` autouse fixture already
    solved locally (same monkeypatch, same target, same "09:30" value) —
    promoted here to suite-wide so it protects every OTHER file with the
    same latent exposure, not just the one that happened to get it first.
    The C3 file's local fixture is now redundant but harmless (same value,
    same target — it simply re-pins what this fixture already pinned).

    Hermetic side effect (intended): this also makes the suite immune to
    future .env drift on any developer/CI machine — CI itself already runs
    credential-less with no .env (default 09:30), so the 26 failures were
    LOCAL-ONLY; this fixture makes local runs match CI's isolation instead
    of the other way around.

    Opt-out: any test that deliberately wants a NON-default
    EXECUTION_START_TIME (e.g. tests/autotuner/test_ac5_replay_action_phase_gated_by_execution_start_time.py,
    which exercises the action-phase gate's response to non-default values)
    simply monkeypatches the attribute again within its own test body/fixture
    AFTER this one has run — the later call wins for that test's duration and
    is correctly reverted by monkeypatch's teardown stack. No test needed to
    change: test_ac5_*.py drives _replay_exit_tick directly with an explicit
    execution_start_hhmm= keyword argument (never reads this module attribute
    at all) and test_n3_*.py's tests exercise the pure grace-window helpers
    with explicit parameters — neither file depends on this attribute's
    ambient value. The one test in the AC-6 bar-level battery that DOES need
    a non-default value (the execution_start_time_pre_action_gate scenario)
    sets it explicitly in a try/finally block around the call that needs it.
    """
    monkeypatch.setattr("alpha_bot_execution.EXECUTION_START_TIME", "09:30", raising=False)


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


# ---------------------------------------------------------------------------
# Timing log harness (observability and transport), shared by tests/app and
# tests/advisors: the daemon's timing lines are captured from the
# planetstopper.timing logger, and every duration comes from a clock the test
# advances (timing_log.clock), never from wall time.
# ---------------------------------------------------------------------------


@pytest.fixture()
def timing_records():
    """Capture every line the timing logger receives during the test."""
    from tests._timing_capture import capture_timing_records

    with capture_timing_records() as capture:
        yield capture


@pytest.fixture()
def timing_clock(monkeypatch):
    """Install a controllable timing_log.clock (read at call time by every emitter)."""
    import timing_log
    from tests._timing_capture import FakeClock

    clock = FakeClock()
    monkeypatch.setattr(timing_log, "clock", clock)
    return clock
