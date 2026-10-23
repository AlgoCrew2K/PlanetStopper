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
    connection_error.  `hold` blocks on `release` (up to HOLD_TIMEOUT_S) and
    then serves the captured 200 body.  The 200 body is the captured producer
    record, deep-copied per call so a caller cannot mutate the fixture.
    """

    hold_timeout_s = HOLD_TIMEOUT_S

    def __init__(self, payload: dict, account_id: str) -> None:
        self.payload = payload
        self.account_id = account_id
        self.mode = "ok"
        self.urls: list[str] = []
        self.started = threading.Event()
        self.release = threading.Event()
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
            self.release.wait(timeout=HOLD_TIMEOUT_S)
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
