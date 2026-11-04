"""
App test conftest.

Patches get_api_state_dict for every test in tests/app/ so that the additive
portmode-dev fields (port_state, exit_authority, daemon_started_at) are
JSON-serialisable. Historically (predates this cycle) get_api_state_dict
reached engine.exit_authority.get_exit_authority(), which returned a
MagicMock with no live DB/engine, causing Flask's jsonify to raise a 500 on
serialisation -- app.py has SINCE been refactored to compute exit_authority
via a plain `os.getenv("EXIT_AUTHORITY", "per_symphony")` (app.py:2229, no
engine dependency), but this stub is still needed so /api/state-touching
tests in tests/app/ don't reach real DB/engine state via the OTHER fields
this stub replaces.

Also stubs load_community_strategies for every test in tests/app/ so that the
HF-1 route wiring (app.py: lazy `from advisors.community_strats import
load_community_strategies`) never reaches real Atlas (MongoDB) during the
offline suite.  Without this stub every POST to /ai-advisor/strategy-builder/run
blocks on a live network connection and the suite hangs.  The stub returns the
honest-unavailable shape the route already degrades on (AC-4 / AC-6).

[Fixed, DE-PERF-WINDOW-TRUTH-001, 2026-09-04, team-lead root-cause via CI
diagnostic]: `exit_authority` below was `{}` -- a TYPE mismatch (dict where
the real get_api_state_dict always returns a str, "per_symphony" or
"port_level"). This stub is pre-existing/autouse for ALL of tests/app/, and
was never itself reachable outside that directory under normal pytest
conftest directory-scoping -- but a same-CI-worker sys.modules identity-leak
(a tests/app/ file's autouse patch.object + test_dismiss_executor_atexit.py's
sys.modules["app"] pop+reload, co-located with a tests/portmode/ victim
under `-n2 --dist loadfile`'s full-tree grouping) let this MagicMock-wrapped
stub reach a /api/state consumer OUTSIDE tests/app/, which then rendered the
type-mismatched `{}` instead of a valid exit_authority string. Fixed
robust-by-construction: `exit_authority` is now a REALISTIC value
("per_symphony", matching the real function's own default at app.py:2229)
so that even if this stub ever leaks again via the same or a similar
identity-leak mechanism, ANY consumer sees a genuinely valid value instead
of a type-mismatched placeholder -- the leak-TIMING dependency is removed as
a failure mode, not just this one occurrence.
"""

from __future__ import annotations

import copy
import json
import pathlib
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

_API_STATE_STUB = {
    "bot_state": {},
    "is_locked": False,
    "port_state": {},
    "exit_authority": "per_symphony",
    "daemon_started_at": None,
}

# Offline community-strats stub: mimics the "Atlas unavailable" branch so the
# route degrades to a template-only run without touching the network.
_COMMUNITY_STRATS_OFFLINE_STUB = {
    "available": False,
    "candidates": [],
    "stats": {},
    "source": "test-stub",
}


@pytest.fixture(autouse=True)
def _stub_get_api_state_dict():
    import app as app_module

    with patch.object(app_module, "get_api_state_dict", return_value=_API_STATE_STUB):
        yield


@pytest.fixture(autouse=True)
def _stub_community_strats_offline():
    """Prevent load_community_strategies from reaching real Atlas in any tests/app/ test.

    Patched at the source module because the route handler uses a lazy
    `from advisors.community_strats import load_community_strategies` inside
    its function body — that re-fetches the name from the source module
    namespace on each call, so patching there is the correct seam.
    """
    with patch(
        "advisors.community_strats.load_community_strategies",
        return_value=_COMMUNITY_STRATS_OFFLINE_STUB,
    ) as mock_load:
        yield mock_load


# ---------------------------------------------------------------------------
# Account-totals refresh harness (shared by the account-basis grace and the
# startup-refresh specs).  Mocks sit at the network boundary (requests.get),
# never at a local name binding, so the real _refresh_account_totals runs.
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_TOTAL_STATS_FIXTURE = _REPO_ROOT / "tests/fixtures/composer/v3-audit/total_stats.json"
_CAPTURED_ENTRY_FIXTURE = (
    _REPO_ROOT / "tests/fixtures/refresh_timing/02-c3/bot_state_entry_iaSOOUsmnCJHiZvbrWfs.json"
)
CAPTURED_SYMPHONY_ID = "iaSOOUsmnCJHiZvbrWfs"
# Longest a held Composer call waits for the test to release it.  A request
# that is wrongly serialized behind the held refresh therefore returns only
# after this long, by which point the held thread has already finished.
HOLD_TIMEOUT_S = 6.0
_ACCOUNT_REFRESH_THREAD_PREFIX = "account-refresh-"


@pytest.fixture(autouse=True)
def _drain_account_refresh_threads():
    """Join daemon threads named account-refresh-* before and after each test.

    The non-blocking account refresh starts a daemon thread per call; a thread
    left running would write the shared totals cache during the next test.
    """

    def _drain():
        for t in threading.enumerate():
            if t.name.startswith(_ACCOUNT_REFRESH_THREAD_PREFIX) and t.is_alive():
                t.join(timeout=2.0)

    _drain()
    yield
    _drain()


class MonotonicClock:
    """Replacement for app._monotonic: a clock tests advance explicitly."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def monotonic_clock(monkeypatch):
    """Install a controllable app._monotonic (raising=False: the seam is new)."""
    import app as app_module

    clock = MonotonicClock()
    monkeypatch.setattr(app_module, "_monotonic", clock, raising=False)
    return clock


class ComposerTotalStatsStub:
    """requests.get stand-in for Composer's account total-stats route.

    `mode` selects the reply: ok | hold | status_503 | read_timeout |
    connection_error.  `hold` blocks on `release` (up to `hold_timeout_s`, HOLD_TIMEOUT_S unless
    a test sets its own) and then serves the captured 200 body; `returned` is set once a held
    call has stopped holding, so a test can say whether something finished while Composer was
    still held.  The 200 body is the captured producer record, deep-copied per call so a caller
    cannot mutate the fixture.
    """

    def __init__(self, payload: dict, account_id: str) -> None:
        self.payload = payload
        self.account_id = account_id
        self.mode = "ok"
        self.hold_timeout_s = HOLD_TIMEOUT_S
        self.urls: list[str] = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.returned = threading.Event()
        self._lock = threading.Lock()

    def _ok(self):
        body = copy.deepcopy(self.payload)
        return SimpleNamespace(status_code=200, json=lambda: body)

    def __call__(self, url, headers=None, timeout=None, **_kwargs):
        if not str(url).endswith("/total-stats"):
            raise AssertionError(f"unexpected network call during the test: {url}")
        with self._lock:
            self.urls.append(url)
        if self.mode == "hold":
            self.started.set()
            self.release.wait(timeout=self.hold_timeout_s)
            self.returned.set()
            return self._ok()
        if self.mode == "status_503":
            return SimpleNamespace(status_code=503)
        if self.mode == "read_timeout":
            raise requests.exceptions.ReadTimeout("simulated read timeout")
        if self.mode == "connection_error":
            raise requests.exceptions.ConnectionError("simulated connection error")
        return self._ok()

    @property
    def call_count(self) -> int:
        with self._lock:
            return len(self.urls)


@pytest.fixture()
def captured_symphony_entry():
    """One real per-symphony bot_state entry captured from the droplet (verbatim)."""
    return copy.deepcopy(json.loads(_CAPTURED_ENTRY_FIXTURE.read_text(encoding="utf-8")))


@pytest.fixture()
def composer_total_stats(tmp_path, monkeypatch, captured_symphony_entry):
    """Serve the captured total-stats body through requests.get, credentials isolated.

    The developer .env is real, so ENV_FILE_PATH is redirected to an empty file
    and every credential the refresh reads is set or deleted here.
    """
    import app as app_module

    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", str(env_file))
    account_id = captured_symphony_entry["account"]
    monkeypatch.setenv("COMPOSER_KEY_ID", "composer-key-id-for-tests")
    monkeypatch.setenv("COMPOSER_SECRET", "composer-secret-for-tests")
    monkeypatch.setenv("ACCOUNT_ROTH", account_id)
    monkeypatch.delenv("ACCOUNT_INDIVIDUAL", raising=False)
    monkeypatch.delenv("ACCOUNT_TRAD", raising=False)
    stub = ComposerTotalStatsStub(
        json.loads(_TOTAL_STATS_FIXTURE.read_text(encoding="utf-8")), account_id
    )
    monkeypatch.setattr(app_module.requests, "get", stub)
    try:
        yield stub
    finally:
        stub.release.set()


# ---------------------------------------------------------------------------
# Scheduler, engine run and health harness (shared by the scheduler survival,
# single-flight and /health specs).  The engine is replaced at the process
# boundary (subprocess.run), so the real trigger_alpha_bot / threaded_trigger /
# _publish_cycle run; the wall clock and the monotonic clock are the two seams
# the daemon exposes (app._now_et and app._monotonic).
# ---------------------------------------------------------------------------

_ENGINE_THREAD_NAME = "engine-cycle"


@pytest.fixture(autouse=True)
def _reset_scheduler_run_state(monkeypatch):
    """The daemon keeps its run state in module globals; start every test empty.

    Names are the module state of the scheduler/health design: the engine run in
    flight, the newest published record, the last five spawned runs' outcomes and
    the scheduler's tick and start stamps.  raising=False: the names are new.
    """
    import collections

    import app as app_module

    for name in (
        "_engine_run",
        "_engine_process",
        "_last_cycle_record",
        "_scheduler_last_tick_mono",
        "_scheduler_last_tick_at",
        "_scheduler_started_mono",
    ):
        monkeypatch.setattr(app_module, name, None, raising=False)
    monkeypatch.setattr(
        app_module, "_RECENT_RUN_OUTCOMES", collections.deque(maxlen=5), raising=False
    )
    # The process start the stall verdict falls back on before the scheduler's first tick is the
    # start of THIS test, not of the test process: a verdict read in a worker that has been up for
    # two minutes would otherwise say "scheduler_stalled" for a test that never starts a scheduler.
    monkeypatch.setattr(app_module, "_DAEMON_STARTED_MONO", app_module._monotonic())
    yield
    for thread in threading.enumerate():
        if thread.name == _ENGINE_THREAD_NAME and thread.is_alive():
            thread.join(timeout=2.0)


class EtClock:
    """Replacement for app._now_et: a wall clock tests set explicitly."""

    def __init__(self, start) -> None:
        self.now = start

    def __call__(self):
        return self.now


@pytest.fixture()
def et_clock(monkeypatch):
    """Install a controllable app._now_et, at 10:43:00 ET on a Thursday trading day."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import app as app_module

    clock = EtClock(datetime(2026, 10, 22, 10, 43, 0, tzinfo=ZoneInfo("America/New_York")))
    monkeypatch.setattr(app_module, "_now_et", clock)
    return clock


class StubEngine:
    """app.subprocess.run stand-in for the engine process.

    Every call appends ("start", n) and, when it returns or raises, ("end", n) to
    `events`.  With `hold` set the call blocks on `release` (up to HOLD_S) so a run
    can be kept in flight; `exit_code` non-zero makes it fail the way check=True does.
    """

    HOLD_S = 10.0

    def __init__(self) -> None:
        import subprocess

        self._subprocess = subprocess
        self.events: list[tuple[str, int]] = []
        self.hold = False
        self.exit_code = 0
        self.started = threading.Event()
        self.release = threading.Event()
        self.killed = False
        self._lock = threading.Lock()
        self._count = 0

    @property
    def starts(self) -> int:
        with self._lock:
            return self._count

    def __call__(self, cmd, **_kwargs):
        with self._lock:
            self._count += 1
            n = self._count
            self.events.append(("start", n))
        self.started.set()
        if self.hold:
            self.release.wait(timeout=self.HOLD_S)
        with self._lock:
            self.events.append(("end", n))
        if self.exit_code:
            raise self._subprocess.CalledProcessError(self.exit_code, cmd)
        return self._subprocess.CompletedProcess(cmd, 0)


@pytest.fixture()
def stub_engine(monkeypatch, tmp_path):
    """The engine process replaced by a StubEngine, with the daemon's log file in tmp."""
    import app as app_module

    stub = StubEngine()
    monkeypatch.setattr(app_module, "LOG_FILE", str(tmp_path / "engine.log"))
    monkeypatch.setattr(app_module.subprocess, "run", stub)
    try:
        yield stub
    finally:
        stub.release.set()


@pytest.fixture()
def published_cycles(monkeypatch):
    """Every record that reaches the event fan-out, in order.  _publish_cycle itself runs."""
    import app as app_module

    records: list = []
    monkeypatch.setattr(app_module, "_notify_cycle_complete", records.append)
    return records


@pytest.fixture()
def join_engine_threads():
    def _join(timeout_s: float = 5.0) -> bool:
        for thread in threading.enumerate():
            if thread.name == _ENGINE_THREAD_NAME and thread.is_alive():
                thread.join(timeout=timeout_s)
        return not any(
            t.name == _ENGINE_THREAD_NAME and t.is_alive() for t in threading.enumerate()
        )

    return _join


@pytest.fixture()
def make_record():
    """Build an app.CycleRecord; defaults describe a finished data cycle at 10:43 ET."""

    def _make(
        outcome: str = "cycle",
        *,
        exit_code: int | None = 0,
        cycle_id: str = "2026-10-22T10:43:00-04:00#1",
        started_at: str = "2026-10-22T10:43:00-04:00",
        completed_at: str = "2026-10-22T10:43:09-04:00",
        changed: bool = True,
        running_cycle_id: str | None = None,
        running_since: str | None = None,
    ):
        import app as app_module

        return app_module.CycleRecord(
            cycle_id=cycle_id,
            started_at=started_at,
            completed_at=completed_at,
            outcome=outcome,
            changed=changed,
            exit_code=exit_code,
            running_cycle_id=running_cycle_id,
            running_since=running_since,
        )

    return _make


@pytest.fixture()
def client():
    """Flask test client bound to the in-process app (files may define their own)."""
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_review_run_state(monkeypatch):
    """Give every tests/app/ test a fresh, idle review run state with no remembered failed
    attempt.

    The state is one module-level object shared by the nightly tick, the on-demand route
    and the panel, so a test that takes it (or records a failed attempt) would otherwise
    change what the next test's page and API report. A run that is still holding the old
    object finishes against it and cannot touch the new one. Production code must reach the
    state as ``tenure_review.REVIEW_RUN_STATE`` at each use (never a name bound at import),
    which is what makes this replacement take effect everywhere.
    """
    import advisors.tenure_review as tenure_review

    monkeypatch.setattr(tenure_review, "REVIEW_RUN_STATE", type(tenure_review.REVIEW_RUN_STATE)())
    yield


@pytest.fixture()
def no_review_accounts(monkeypatch, tmp_path):
    """Make the nightly tick's tenure-review stage a no-op for a test that runs the tick.

    The 03:45 tick starts the tenure review and then the recommender run. The review reads
    its Composer accounts from the env file (which wins over os.environ) and from the process
    environment; the developer .env is real, so a test that runs the tick without this would
    read real accounts. ENV_FILE_PATH is redirected to an empty file and every account /
    threshold key is removed, which makes the review stop at "no Composer account configured"
    before any request. A test that exercises the review itself sets its own accounts."""
    import app as app_module

    env_file = tmp_path / "no_review_accounts.env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", str(env_file))
    for key in (
        "ACCOUNT_INDIVIDUAL",
        "ACCOUNT_ROTH",
        "ACCOUNT_TRAD",
        "TENURE_REVIEW_MIN_TRADING_DAYS",
    ):
        monkeypatch.delenv(key, raising=False)
