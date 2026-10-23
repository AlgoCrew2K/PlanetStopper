"""Conftest for tests/realtime_push/.

Provides:
  - `client`: authenticated Flask test client (auth gate DISABLED, same as suite default)
  - `client_no_auth`: test client with auth gate ENABLED and no session cookie — used
    to verify /api/events returns 401 for unauthenticated requests (AC-2).
  - _stub_get_api_state_dict: autouse stub so get_api_state_dict() never reaches the
    live DB or engine (consistent with tests/app/conftest.py and tests/dashboard/conftest.py).
  - _reset_account_totals_cache: autouse fixture that drains in-flight "cycle-refresh"
    daemon threads and resets _account_totals_cache to a clean state before every test.

Auth pattern follows tests/app/test_dashboard_auth.py: the suite-level autouse
`_disable_auth_for_tests` in tests/conftest.py sets _auth_check_enabled=False.
`client_no_auth` re-enables the gate via monkeypatch for its own scope.

_account_totals_cache isolation note:
  _notify_cycle_complete() spawns threading.Thread(target=_refresh_account_totals,
  daemon=True, name="cycle-refresh").  The thread target is bound to the module-level
  name at the time the Thread object is created — patch("app._refresh_account_totals")
  replaces only the module ATTRIBUTE, not the thread's already-bound target function.
  Consequently the thread ALWAYS executes the real _refresh_account_totals, regardless
  of any mock.  Without joining these threads before each test the cache can be left in
  an indeterminate state (stale flag set or wrong values written) by a thread from a
  prior test, causing non-deterministic failures.  The _reset_account_totals_cache
  fixture joins all live "cycle-refresh" threads (up to a short timeout) and then
  hard-resets the cache so every test starts clean.
"""

from __future__ import annotations

import json
import pathlib
import queue
import subprocess
import threading
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_API_STATE_STUB = {
    "bot_state": {},
    "is_locked": False,
    "port_state": {},
    "exit_authority": {},
    "daemon_started_at": None,
}

_TEST_PASSWORD = "rt-push-test-pass-abc123"
_TEST_SECRET_KEY = "rt-push-test-secret-key-xyz789"

# Timeout (seconds) to join a lingering "cycle-refresh" daemon thread before
# giving up and hard-resetting the cache.  Short enough not to slow the suite.
_CYCLE_REFRESH_JOIN_TIMEOUT = 0.5


@pytest.fixture(autouse=True)
def _reset_account_totals_cache():
    """Join in-flight cycle-refresh threads and hard-reset _account_totals_cache.

    Root cause: _notify_cycle_complete() spawns threading.Thread(target=
    _refresh_account_totals, name="cycle-refresh", daemon=True).  The thread's
    target is bound to the real _refresh_account_totals at thread-creation time —
    patch("app._refresh_account_totals") replaces the module attribute but NOT
    the already-bound thread target.  A thread from a previous test may still be
    running when the next test starts, and can:
      - Set the stale flag (via mark_stale() — only called by _notify_cycle_complete,
        not by _refresh_account_totals directly), or
      - Fail to call refresh_written() (e.g. Composer unreachable → stale flag stays
        set from the previous _notify_cycle_complete call).

    This fixture joins all live "cycle-refresh" threads (up to _CYCLE_REFRESH_JOIN_TIMEOUT
    each) before the test runs, then hard-resets the cache by calling both clear()
    (which resets dict contents + stale flag per _StaleFlagDict.clear) and
    refresh_written() (belt-and-suspenders: ensures stale=False even if a thread
    squeezed in a mark_stale() between the join and the clear).

    The same reset runs in teardown (yield) so a test cannot leak state to the next.
    """
    import app as app_module

    def _drain_and_reset():
        # Join any live "cycle-refresh" threads from prior tests.
        for t in threading.enumerate():
            if t.name == "cycle-refresh" and t.is_alive():
                t.join(timeout=_CYCLE_REFRESH_JOIN_TIMEOUT)
        # Hard-reset: clear contents + stale flag, then confirm stale=False.
        app_module._account_totals_cache.clear()  # also resets _stale per _StaleFlagDict
        app_module._account_totals_cache.refresh_written()  # belt-and-suspenders

    _drain_and_reset()
    yield
    _drain_and_reset()


@pytest.fixture(autouse=True)
def _stub_get_api_state_dict():
    """Prevent get_api_state_dict from reaching live DB or engine for all tests here."""
    import app as app_module

    with patch.object(app_module, "get_api_state_dict", return_value=_API_STATE_STUB):
        yield


@pytest.fixture()
def client():
    """Authenticated Flask test client with auth gate DISABLED (suite default)."""
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture()
def client_no_auth(monkeypatch):
    """Flask test client with auth gate ENABLED and no session — produces 401 on /api/*."""
    import app as app_module

    monkeypatch.setenv("DASHBOARD_PASSWORD", _TEST_PASSWORD)
    monkeypatch.setenv("SECRET_KEY", _TEST_SECRET_KEY)
    # Override the suite-level _disable_auth_for_tests to enable the real gate.
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Cycle-event fixtures (refresh-timing cycle record, stub engine, captured markers)
#
# Provenance: every value read here comes from the tracked 2026-07-09 droplet
# captures under tests/fixtures/prod_droplet/ (see
# tests/fixtures/refresh_timing/02-c1/PROVENANCE.md). No record is derived: a
# marker "advance" is two real consecutive captured values.
# ---------------------------------------------------------------------------

_PROD_DROPLET = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "prod_droplet"
_SYMPHONY_ID = "iaSOOUsmnCJHiZvbrWfs"
_BEFORE_TRIGGER_ROW_ID = 86
_AFTER_TRIGGER_ROW_ID = 87


def _read_capture(name: str):
    return json.loads((_PROD_DROPLET / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def captured_markers() -> SimpleNamespace:
    """Real marker values the engine wrote to bot_state, from the droplet capture.

    exit_triggers.cycle_id is the engine's own last_successful_cycle_at at the
    time of the trigger, so two consecutive rows are a real advance of that marker.
    trading_day values are the distinct ET trading days of the shadow_history capture.
    """
    triggers = {row["id"]: row for row in _read_capture("exit_triggers.json")}
    days = sorted({row["trading_day"] for row in _read_capture("shadow_last_rows.json")})
    return SimpleNamespace(
        cycle_at_before=triggers[_BEFORE_TRIGGER_ROW_ID]["cycle_id"],
        cycle_at_after=triggers[_AFTER_TRIGGER_ROW_ID]["cycle_id"],
        day_before=days[-2],
        day_after=days[-1],
        symphony_id=_SYMPHONY_ID,
        symphony_entry=_read_capture("bot_state_positions.json")[_SYMPHONY_ID],
        post_mortem_dir=_PROD_DROPLET / "post_mortems",
    )


@pytest.fixture()
def seed_bot_state(captured_markers):
    """Write bot_state as one captured symphony entry plus the two marker fields."""
    import database

    def _seed(cycle_at: str | None, trading_day: str | None) -> dict:
        state = {captured_markers.symphony_id: dict(captured_markers.symphony_entry)}
        if cycle_at is not None:
            state["last_successful_cycle_at"] = cycle_at
        if trading_day is not None:
            state["last_market_close_snapshot"] = {"trading_day": trading_day}
        database.save_state(state)
        return state

    return _seed


@pytest.fixture()
def frozen_clock(monkeypatch):
    """Replace app._now_et (the wall clock the cycle record reads) with a fixed ET instant."""
    import app as app_module

    def _freeze(instant: datetime) -> datetime:
        assert instant.tzinfo is not None, "freeze an aware ET instant"
        monkeypatch.setattr(app_module, "_now_et", lambda: instant)
        return instant

    return _freeze


@pytest.fixture()
def stub_engine(monkeypatch, tmp_path):
    """Stand in for the engine subprocess: run `writes()` (the engine's state writes), then
    exit as subprocess.run(check=True) would (return, or raise CalledProcessError)."""
    import app as app_module

    monkeypatch.setattr(app_module, "LOG_FILE", str(tmp_path / "daemon.log"))

    def _install(exit_code: int = 0, writes=None, spawn_error: Exception | None = None):
        stub = SimpleNamespace(calls=[], returned_at_mono=None)

        def _fake_run(cmd, check=False, env=None, stdout=None, stderr=None, **_kw):
            stub.calls.append(cmd)
            if spawn_error is not None:
                raise spawn_error
            if writes is not None:
                writes()
            stub.returned_at_mono = time.monotonic()
            if exit_code != 0 and check:
                raise subprocess.CalledProcessError(exit_code, cmd)
            return SimpleNamespace(returncode=exit_code)

        monkeypatch.setattr(app_module.subprocess, "run", _fake_run)
        return stub

    return _install


@pytest.fixture()
def captured_records(monkeypatch):
    """Capture each CycleRecord trigger_alpha_bot hands to _publish_cycle."""
    import app as app_module

    records: list = []
    monkeypatch.setattr(app_module, "_publish_cycle", records.append)
    return records


@pytest.fixture()
def make_cycle_record():
    """Build a real CycleRecord with all eight fields explicit."""
    import itertools

    import app as app_module

    counter = itertools.count(1)

    def _make(outcome: str = "cycle", changed: bool = True, **overrides):
        n = next(counter)
        fields = {
            "cycle_id": f"2026-07-09T10:14:00-04:00#{n}",
            "started_at": "2026-07-09T10:14:00-04:00",
            "completed_at": "2026-07-09T10:14:04-04:00",
            "outcome": outcome,
            "changed": changed,
            "exit_code": None if outcome == "skipped" else 0,
            "running_cycle_id": None,
            "running_since": None,
        }
        fields.update(overrides)
        return app_module.CycleRecord(**fields)

    return _make


@pytest.fixture()
def sse_queue():
    """Register a SimpleQueue as an SSE client; remove every one registered at teardown."""
    import app as app_module

    created: list = []

    def _register():
        q: queue.SimpleQueue = queue.SimpleQueue()
        with app_module._sse_clients_lock:
            app_module._sse_clients.append(q)
        created.append(q)
        return q

    yield _register
    with app_module._sse_clients_lock:
        for q in created:
            try:
                app_module._sse_clients.remove(q)
            except ValueError:
                pass


@pytest.fixture()
def account_refresh_spy(monkeypatch):
    """Replace _refresh_account_totals with a recorder that never touches Composer.

    `release` lets a test hold the refresh open to prove the fan-out does not wait on it."""
    import app as app_module

    gate = threading.Event()
    gate.set()
    spy = SimpleNamespace(calls=[], gate=gate)

    def _fake_refresh():
        spy.calls.append(threading.current_thread().name)
        gate.wait(timeout=5)

    monkeypatch.setattr(app_module, "_refresh_account_totals", _fake_refresh)
    yield spy
    gate.set()
