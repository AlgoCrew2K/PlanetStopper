"""
RED tests — F-005: minimal unauthenticated GET /health liveness probe.

Source: docs/audit/confidence-program/INSTITUTIONAL-READINESS-REPORT.md (running-register.md
line 42) — "_AUTH_EXEMPT_ENDPOINTS exempts /health but NO such route exists (CLAUDE.md's claim
is currently false; no liveness probe possible)". `_AUTH_EXEMPT_ENDPOINTS` (app.py:103-105)
already lists "health" — confirmed no `def health` / no `/health` route currently exists
(grep returned nothing at plan-time).

AC-2: GET /health returns 200 + honest fields unauthenticated; NOT in
_SETTINGS_WRITE_ALLOWLIST scope; no secrets; CLAUDE.md's existing exempt-list claim becomes true.

Fields required (per feature-plans/fix-ops-cluster.md Summary): {status, daemon_started_at,
last_successful_cycle_at}. Both non-status fields are ALREADY-COMPUTED module/state values —
_DAEMON_STARTED_AT (app.py:463) and state_data["last_successful_cycle_at"] (engine-written,
read the same way app.py:2327 already reads it) — no new computation.

Mock boundary: database.load_state (for last_successful_cycle_at) + database.get_connection
(read-write spy, must never be called — this is a read-only probe, architecture constraint 5).
No live DB, no live network. -n0 only.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import app as app_module

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture()
def auth_enabled_client(monkeypatch):
    """Client with the real auth gate re-enabled (overrides the conftest
    autouse _disable_auth_for_tests fixture) — mirrors the auth_client
    pattern in tests/app/test_dashboard_auth.py so this test proves /health
    is reachable pre-login, not just reachable because auth-checking is
    globally off for the rest of the suite.
    """
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-pass-health-probe")
    monkeypatch.setenv("SECRET_KEY", "test-secret-health-probe")
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture()
def rw_connection_spy():
    """Spy on database.get_connection (read-write) — any call fails the test.

    Mirrors tests/app/test_dashboard_routes_read_only_db.py's rw_connection_spy.
    """
    real_conn = sqlite3.connect(":memory:")
    m = MagicMock(return_value=real_conn)
    with patch.object(app_module.database, "get_connection", m):
        yield m
    real_conn.close()


# ---------------------------------------------------------------------------
# AC-2: route exists, 200, unauthenticated
# ---------------------------------------------------------------------------


def test_health_route_returns_200(client):
    with patch.object(
        app_module.database, "load_state", return_value={"last_successful_cycle_at": None}
    ):
        resp = client.get("/health")
    assert resp.status_code == 200, f"/health must return 200, got {resp.status_code}"


def test_health_reachable_without_authentication(auth_enabled_client):
    """AC-2: /health must be reachable pre-login — the auth gate is ENABLED
    for this test (unlike most of the suite) so a 302-to-/login or 401 here
    is a genuine failure, not an artifact of the global test-suite auth bypass.
    """
    with patch.object(
        app_module.database, "load_state", return_value={"last_successful_cycle_at": None}
    ):
        resp = auth_enabled_client.get("/health", follow_redirects=False)
    assert resp.status_code == 200, (
        f"/health must be exempt from the auth gate (it's already listed in "
        f"_AUTH_EXEMPT_ENDPOINTS) but got {resp.status_code} — "
        "the route's endpoint name must be exactly 'health'."
    )


# ---------------------------------------------------------------------------
# AC-2: honest field shape — assert shape/presence, never a hardcoded value
# ---------------------------------------------------------------------------


def test_health_response_is_json_with_status_field(client):
    with patch.object(
        app_module.database, "load_state", return_value={"last_successful_cycle_at": None}
    ):
        resp = client.get("/health")
    data = resp.get_json()
    assert data is not None, "/health must return a JSON body"
    assert "status" in data, f"/health JSON missing 'status' key: {data}"


def test_health_response_has_daemon_started_at_matching_module_constant(client):
    """daemon_started_at must reflect the real _DAEMON_STARTED_AT module value
    (already computed at import time, app.py:463) — not a fabricated/omitted field.
    """
    with patch.object(
        app_module.database, "load_state", return_value={"last_successful_cycle_at": None}
    ):
        resp = client.get("/health")
    data = resp.get_json()
    assert "daemon_started_at" in data, f"/health JSON missing 'daemon_started_at': {data}"
    assert data["daemon_started_at"] == app_module._DAEMON_STARTED_AT, (
        "daemon_started_at must be sourced from the module-level _DAEMON_STARTED_AT "
        f"constant; got {data['daemon_started_at']!r} vs {app_module._DAEMON_STARTED_AT!r}"
    )


def test_health_response_surfaces_last_successful_cycle_at_from_state(client):
    """last_successful_cycle_at must be read from database.load_state(), the
    same top-level engine-written field app.py:2327 already reads — proven by
    injecting a known fixture value and asserting it round-trips unchanged.
    """
    _fixture_ts = "2026-07-20T14:31:07+00:00"
    with patch.object(
        app_module.database,
        "load_state",
        return_value={"last_successful_cycle_at": _fixture_ts},
    ):
        resp = client.get("/health")
    data = resp.get_json()
    assert "last_successful_cycle_at" in data, (
        f"/health JSON missing 'last_successful_cycle_at': {data}"
    )
    assert data["last_successful_cycle_at"] == _fixture_ts, (
        f"expected the fixture-injected timestamp {_fixture_ts!r} to round-trip "
        f"through /health, got {data['last_successful_cycle_at']!r}"
    )


def test_health_daemon_degraded_returns_honest_none_not_fabricated_ok(client):
    """Edge case (plan): daemon-degraded state still returns honestly — a
    missing/None last_successful_cycle_at must surface as None, never a
    fabricated timestamp or a status that claims full health.
    """
    with patch.object(
        app_module.database, "load_state", return_value={}
    ):  # no last_successful_cycle_at key at all — degraded/cold-start state
        resp = client.get("/health")
    data = resp.get_json()
    assert resp.status_code == 200, "an honestly-degraded daemon must still answer the probe"
    assert data.get("last_successful_cycle_at") is None, (
        "a missing engine cycle timestamp must surface as None, not a fabricated value: "
        f"got {data.get('last_successful_cycle_at')!r}"
    )


# ---------------------------------------------------------------------------
# AC-2: security — no secrets, read-only, not a write-action surface
# ---------------------------------------------------------------------------


def test_health_response_leaks_no_secret_env_values(client, monkeypatch):
    """/health must never echo credential/env values into its response body."""
    _sentinel_secret = "SENTINEL-SECRET-VALUE-DO-NOT-LEAK-9f3a"
    monkeypatch.setenv("COMPOSER_SECRET", _sentinel_secret)
    monkeypatch.setenv("DASHBOARD_PASSWORD", _sentinel_secret)
    monkeypatch.setenv("SECRET_KEY", _sentinel_secret)
    with patch.object(
        app_module.database, "load_state", return_value={"last_successful_cycle_at": None}
    ):
        resp = client.get("/health")
    assert resp.status_code == 200, (
        f"/health must return 200 for this security check to be meaningful, got {resp.status_code}"
    )
    body = resp.get_data(as_text=True)
    assert _sentinel_secret not in body, (
        "/health response body must never contain env/credential values — "
        "found the sentinel secret value in the response"
    )


def test_health_does_not_open_a_read_write_db_connection(client, rw_connection_spy):
    """Architecture constraint 5 (read-only) — /health must never call
    database.get_connection() (the read-write path); it reads via
    database.load_state() only (or an equivalent read-only accessor).
    """
    with patch.object(
        app_module.database, "load_state", return_value={"last_successful_cycle_at": None}
    ):
        resp = client.get("/health")
    assert resp.status_code == 200, (
        f"/health must return 200 for this read-only check to be meaningful, got {resp.status_code}"
    )
    assert rw_connection_spy.call_count == 0, (
        f"/health opened a read-write DB connection {rw_connection_spy.call_count} time(s) — "
        "it must be strictly read-only (no DB write path, AC-2)."
    )


def test_health_only_accepts_get_not_post():
    """/health is a liveness probe, not a write-action surface — POST must not
    be silently accepted as an alias for GET (it should 405, matching Flask's
    default behavior for a route registered without methods=["POST"]).
    """
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        resp = c.post("/health")
    assert resp.status_code == 405, (
        f"POST /health must be rejected (405) — it is a read-only GET-only probe, "
        f"got {resp.status_code}"
    )


def test_health_endpoint_name_not_in_settings_write_allowlist():
    """/health must never be treated as a settings-write path — its endpoint
    name/route string must not appear in _SETTINGS_WRITE_ALLOWLIST (which
    gates the two guarded write paths per architecture constraint 2).
    """
    allowlist = getattr(app_module, "_SETTINGS_WRITE_ALLOWLIST", None)
    assert allowlist is not None, "_SETTINGS_WRITE_ALLOWLIST must exist (pre-existing invariant)"
    assert "health" not in allowlist, (
        "'health' must never appear in _SETTINGS_WRITE_ALLOWLIST — /health is read-only"
    )


# ---------------------------------------------------------------------------
# Scheduler liveness and degradation
#
# /health keeps status, daemon_started_at and last_successful_cycle_at and adds
# scheduler_last_tick_at, engine_running_since, last_cycle ({cycle_id, outcome,
# completed_at} of the newest published record of any outcome, or null) and
# degraded_reasons. The verdict comes from app._health_status() (no database read)
# and the pure app._health_degraded_reasons(now_mono):
#   scheduler_stalled  the last tick (before the first tick: the scheduler's start,
#                      before that the process start) is 120 s old or more
#   engine_overrun     an engine run has lasted MORE than 180 s
#   engine_failing     the last 5 spawned runs (skips excluded) all read "failed"
# Ages use app._monotonic; times shown use app._now_et.
# ---------------------------------------------------------------------------

_LAST_CYCLE_KEYS = {"cycle_id", "outcome", "completed_at"}
_STAMP = "2026-10-22T16:00:01.308786-04:00"


@pytest.fixture()
def world(monkeypatch, monotonic_clock, et_clock, published_cycles):
    """Clocks under test control, the event fan-out captured, a scheduler that runs no job,
    and a daemon that started five seconds ago."""
    monkeypatch.setattr(
        app_module, "schedule", SimpleNamespace(run_pending=lambda: None, every=lambda: None)
    )
    monkeypatch.setattr(
        app_module, "_DAEMON_STARTED_MONO", monotonic_clock.now - 5.0, raising=False
    )
    return SimpleNamespace(mono=monotonic_clock, et=et_clock, published=published_cycles)


def _health(client, last_cycle_at: str | None = None) -> dict:
    with patch.object(
        app_module.database, "load_state", return_value={"last_successful_cycle_at": last_cycle_at}
    ):
        resp = client.get("/health")
    assert resp.status_code == 200
    return resp.get_json()


def _reasons(world) -> list[str]:
    fn = getattr(app_module, "_health_degraded_reasons", None)
    if fn is None:
        pytest.fail("app._health_degraded_reasons(now_mono) is missing")
    return list(fn(world.mono.now))


def _tick() -> None:
    tick = getattr(app_module, "_scheduler_tick", None)
    if tick is None:
        pytest.fail("app._scheduler_tick() is missing")
    tick()


def _publish(record) -> None:
    app_module._publish_cycle(record)


class TestHealthReportsSchedulerAndEngine:
    def test_healthy_daemon_reports_ok_the_newest_run_and_a_fresh_tick(
        self, client, world, stub_engine, join_engine_threads
    ):
        _tick()
        app_module.threaded_trigger()
        assert join_engine_threads()
        (record,) = world.published

        data = _health(client, _STAMP)

        assert data["status"] == "ok"
        assert data["degraded_reasons"] == []
        assert data["engine_running_since"] is None
        assert set(data["last_cycle"]) == _LAST_CYCLE_KEYS
        assert data["last_cycle"] == {
            "cycle_id": record.cycle_id,
            "outcome": record.outcome,
            "completed_at": record.completed_at,
        }
        tick_at = datetime.fromisoformat(data["scheduler_last_tick_at"])
        assert abs(world.et.now - tick_at) < timedelta(seconds=2)
        assert data["daemon_started_at"] == app_module._DAEMON_STARTED_AT
        assert data["last_successful_cycle_at"] == _STAMP

    def test_the_tick_time_is_the_time_of_the_last_tick(self, client, world):
        _tick()
        world.et.now += timedelta(seconds=30)
        data_before = _health(client)
        _tick()
        data_after = _health(client)

        assert datetime.fromisoformat(
            data_after["scheduler_last_tick_at"]
        ) - datetime.fromisoformat(data_before["scheduler_last_tick_at"]) == timedelta(seconds=30)

    def test_a_scheduler_silent_for_130_seconds_is_degraded(self, client, world):
        _tick()
        world.mono.advance(130.0)

        data = _health(client)

        assert data["status"] == "degraded"
        assert data["degraded_reasons"] == ["scheduler_stalled"]

    @pytest.mark.parametrize(
        ("silence_s", "stalled"),
        [(0.0, False), (60.0, False), (119.9, False), (120.0, True), (500.0, True)],
    )
    def test_the_scheduler_stall_threshold_is_120_seconds_or_more(self, world, silence_s, stalled):
        _tick()
        world.mono.advance(silence_s)

        assert ("scheduler_stalled" in _reasons(world)) is stalled

    def test_an_engine_run_longer_than_200_seconds_is_degraded_and_reports_when_it_began(
        self, client, world
    ):
        run = app_module._begin_engine_run(world.et.now)
        _tick()
        world.mono.advance(200.0)
        _tick()

        data = _health(client)

        assert data["status"] == "degraded"
        assert data["degraded_reasons"] == ["engine_overrun"]
        assert datetime.fromisoformat(data["engine_running_since"]) == world.et.now
        assert run is not None

    @pytest.mark.parametrize(
        ("run_s", "overrun"), [(0.0, False), (180.0, False), (180.5, True), (900.0, True)]
    )
    def test_the_engine_overrun_threshold_is_more_than_180_seconds(self, world, run_s, overrun):
        app_module._begin_engine_run(world.et.now)
        world.mono.advance(run_s)
        _tick()

        assert ("engine_overrun" in _reasons(world)) is overrun

    def test_five_failed_runs_in_a_row_are_degraded(self, client, world, make_record):
        _tick()
        for n in range(5):
            _publish(make_record("failed", exit_code=1, cycle_id=f"failed-{n}"))

        data = _health(client)

        assert data["status"] == "degraded"
        assert data["degraded_reasons"] == ["engine_failing"]
        assert data["last_cycle"]["outcome"] == "failed"

    @pytest.mark.parametrize(
        ("outcomes", "failing"),
        [
            pytest.param(["failed"] * 4, False, id="four-failures"),
            pytest.param(["failed"] * 4 + ["cycle"], False, id="a-success-in-the-window"),
            pytest.param(["failed"] * 5 + ["cycle"], False, id="a-success-after-five-failures"),
            pytest.param(["cycle"] + ["failed"] * 5, True, id="five-failures-after-a-success"),
            pytest.param(["failed"] * 4 + ["skipped", "failed"], True, id="skips-are-not-runs"),
            pytest.param(["failed"] * 5 + ["skipped"] * 3, True, id="skips-do-not-clear-failures"),
            pytest.param(["failed"] * 3 + ["closed", "failed", "failed"], False, id="closed-is-ok"),
            pytest.param(["failed"] * 4 + ["blackout"], False, id="blackout-is-ok"),
        ],
    )
    def test_the_failing_window_is_the_last_five_spawned_runs(
        self, world, make_record, outcomes, failing
    ):
        _tick()
        for n, outcome in enumerate(outcomes):
            _publish(
                make_record(
                    outcome,
                    exit_code=1 if outcome == "failed" else (None if outcome == "skipped" else 0),
                    cycle_id=f"{outcome}-{n}",
                )
            )

        assert ("engine_failing" in _reasons(world)) is failing

    def test_a_run_whose_engine_could_not_start_counts_as_failed(self, world, make_record):
        _tick()
        for n in range(5):
            _publish(make_record("failed", exit_code=None, cycle_id=f"no-spawn-{n}"))

        assert "engine_failing" in _reasons(world)

    def test_last_cycle_is_the_newest_record_of_any_outcome_including_a_skip(
        self, client, world, make_record
    ):
        _tick()
        _publish(make_record("cycle", cycle_id="ran"))
        _publish(make_record("skipped", exit_code=None, cycle_id="skipped-minute", changed=False))

        data = _health(client)

        assert data["last_cycle"]["cycle_id"] == "skipped-minute"
        assert data["last_cycle"]["outcome"] == "skipped"
        assert data["status"] == "ok"

    def test_an_unauthenticated_client_gets_the_same_body(self, auth_enabled_client, client, world):
        _tick()
        with_auth_gate = _health(auth_enabled_client)
        without = _health(client)

        assert with_auth_gate == without
        assert {"scheduler_last_tick_at", "engine_running_since", "last_cycle"} <= set(
            with_auth_gate
        )

    def test_the_body_carries_no_credential_account_or_environment_value(
        self, client, world, make_record, monkeypatch
    ):
        sentinel = "SENTINEL-HEALTH-VALUE-DO-NOT-LEAK-71c2"
        for name in (
            "COMPOSER_SECRET",
            "COMPOSER_KEY_ID",
            "ACCOUNT_ROTH",
            "DASHBOARD_PASSWORD",
            "SECRET_KEY",
            "ALPACA_SECRET_KEY",
        ):
            monkeypatch.setenv(name, sentinel)
        app_module._begin_engine_run(world.et.now)
        _tick()
        _publish(make_record("failed", exit_code=1))
        world.mono.advance(200.0)

        resp = client.get("/health")
        body = resp.get_data(as_text=True)

        assert resp.status_code == 200
        assert sentinel not in body
        assert "exit_code" not in body, "an exit code is not part of the public body"
        data = resp.get_json()
        assert set(data["last_cycle"]) == _LAST_CYCLE_KEYS

    def test_a_daemon_that_just_started_with_no_run_is_ok_and_has_no_last_cycle(
        self, client, world
    ):
        data = _health(client)

        assert data["status"] == "ok"
        assert data["degraded_reasons"] == []
        assert data["last_cycle"] is None
        assert data["engine_running_since"] is None

    @pytest.mark.parametrize(
        ("process_age_s", "scheduler_age_s", "stalled"),
        [
            pytest.param(5.0, None, False, id="process-just-started"),
            pytest.param(119.0, None, False, id="process-under-120s"),
            pytest.param(130.0, None, True, id="scheduler-thread-never-started"),
            pytest.param(130.0, 10.0, False, id="scheduler-started-10s-ago"),
            pytest.param(300.0, 125.0, True, id="scheduler-started-but-never-ticked"),
        ],
    )
    def test_before_the_first_tick_age_runs_from_the_scheduler_start_or_the_process_start(
        self, world, monkeypatch, process_age_s, scheduler_age_s, stalled
    ):
        monkeypatch.setattr(
            app_module, "_DAEMON_STARTED_MONO", world.mono.now - process_age_s, raising=False
        )
        monkeypatch.setattr(
            app_module,
            "_scheduler_started_mono",
            None if scheduler_age_s is None else world.mono.now - scheduler_age_s,
            raising=False,
        )

        assert ("scheduler_stalled" in _reasons(world)) is stalled

    def test_a_closed_market_run_is_a_healthy_run(self, client, world, make_record):
        world.et.now = world.et.now.replace(hour=20, minute=0)
        _tick()
        _publish(
            make_record(
                "closed",
                changed=False,
                started_at=world.et.now.isoformat(),
                completed_at=world.et.now.isoformat(),
            )
        )

        data = _health(client, _STAMP)

        assert data["status"] == "ok"
        assert data["last_cycle"]["outcome"] == "closed"
        assert data["last_successful_cycle_at"] == _STAMP

    def test_two_reasons_at_once_are_both_listed_once(self, client, world):
        app_module._begin_engine_run(world.et.now)
        _tick()
        world.mono.advance(200.0)

        data = _health(client)

        assert sorted(data["degraded_reasons"]) == ["engine_overrun", "scheduler_stalled"]
        assert len(data["degraded_reasons"]) == 2

    def test_the_friday_post_close_autotune_reads_as_an_overrun(self, client, world):
        world.et.now = world.et.now.replace(day=23, hour=16, minute=0)  # Friday 2026-10-23
        assert world.et.now.weekday() == 4
        app_module._begin_engine_run(world.et.now)
        world.et.now += timedelta(minutes=5)
        world.mono.advance(200.0)
        _tick()

        data = _health(client)

        assert data["status"] == "degraded"
        assert "engine_overrun" in data["degraded_reasons"]

    def test_the_verdict_is_assembled_by_health_status_with_no_database_read(
        self, client, world, monkeypatch
    ):
        status_fn = getattr(app_module, "_health_status", None)
        if status_fn is None:
            pytest.fail("app._health_status() is missing")

        def _no_database(*_a, **_k):
            raise AssertionError("_health_status must not read the database")

        monkeypatch.setattr(app_module.database, "load_state", _no_database)
        monkeypatch.setattr(app_module.database, "get_ro_connection", _no_database)
        monkeypatch.setattr(app_module.database, "get_connection", _no_database)
        _tick()

        status = status_fn()

        assert set(status) == {
            "status",
            "degraded_reasons",
            "scheduler_last_tick_at",
            "engine_running_since",
            "last_cycle",
        }

    def test_the_route_returns_the_helper_plus_the_two_state_fields(
        self, client, world, monkeypatch
    ):
        helper = {
            "status": "degraded",
            "degraded_reasons": ["engine_failing"],
            "scheduler_last_tick_at": "2026-10-22T10:43:00-04:00",
            "engine_running_since": None,
            "last_cycle": {"cycle_id": "x#1", "outcome": "failed", "completed_at": "t"},
        }
        monkeypatch.setattr(app_module, "_health_status", lambda: dict(helper), raising=False)

        data = _health(client, _STAMP)

        assert data == {
            **helper,
            "daemon_started_at": app_module._DAEMON_STARTED_AT,
            "last_successful_cycle_at": _STAMP,
        }

    def test_the_named_thresholds(self):
        assert getattr(app_module, "_HEALTH_SCHEDULER_STALL_S", None) == 120
        assert getattr(app_module, "_HEALTH_ENGINE_OVERRUN_S", None) == 180
        assert getattr(app_module, "_HEALTH_FAILING_RUNS", None) == 5


# ---------------------------------------------------------------------------
# Caller classes
#
# A caller is "on the box" only when the TCP peer is loopback AND the request carries neither an
# X-Forwarded-For nor a Forwarded header (Caddy adds X-Forwarded-For to every proxied request, so
# an internet caller reaches the daemon as a loopback peer WITH a forwarding header). Header
# presence decides, never its value, so a client-supplied value can only lower the detail it gets.
# On-box callers get the seven keys; every other caller gets exactly the three base keys, status
# computed from the same full view so both classes agree. Bodies come from the route itself.
# ---------------------------------------------------------------------------

_BASE_KEYS = {"status", "daemon_started_at", "last_successful_cycle_at"}
_DETAIL_KEYS = {"degraded_reasons", "scheduler_last_tick_at", "engine_running_since", "last_cycle"}
_FULL_KEYS = _BASE_KEYS | _DETAIL_KEYS

_LOOPBACK_PEER = "127.0.0.1"
_OFF_BOX_PEER = "10.0.0.5"
# RFC 5737 documentation address: the internet caller Caddy forwards.
_INTERNET_CALLER = "203.0.113.7"

_TEST_PASSWORD = "test-pass-health-callers"
_TEST_SECRET = "test-secret-health-callers"


def _health_from(
    client,
    *,
    peer: str = _LOOPBACK_PEER,
    headers: dict | None = None,
    state: dict | None = None,
):
    """GET /health as `peer` with `headers`, the stored state patched; returns the response."""
    state = {"last_successful_cycle_at": _STAMP} if state is None else state
    with patch.object(app_module.database, "load_state", return_value=state):
        return client.get("/health", headers=headers or {}, environ_base={"REMOTE_ADDR": peer})


def _expected_full_body(state: dict) -> dict:
    """The on-box body: today's route output, assembled from the producer functions."""
    return {
        **app_module._health_view(state, app_module._now_et()),
        "daemon_started_at": app_module._DAEMON_STARTED_AT,
        "last_successful_cycle_at": state.get("last_successful_cycle_at"),
    }


class TestOnBoxCallerGetsTheFullBody:
    @pytest.mark.parametrize(
        "peer",
        [
            pytest.param("127.0.0.1", id="ipv4-loopback"),
            pytest.param("::1", id="ipv6-loopback"),
            pytest.param("::ffff:127.0.0.1", id="ipv4-mapped-loopback"),
            pytest.param("127.0.0.2", id="elsewhere-in-127-slash-8"),
            pytest.param("127.255.255.254", id="top-of-127-slash-8"),
        ],
    )
    def test_a_loopback_peer_with_no_forwarding_header_gets_the_seven_keys(
        self, client, world, peer
    ):
        _tick()
        state = {"last_successful_cycle_at": _STAMP}

        resp = _health_from(client, peer=peer, state=state)

        assert resp.status_code == 200
        data = resp.get_json()
        assert set(data) == _FULL_KEYS
        assert data == _expected_full_body(state)

    def test_the_on_box_values_are_the_producers_not_placeholders(self, client, world, make_record):
        _tick()
        _publish(make_record("failed", exit_code=1, cycle_id="failed-on-box"))
        world.mono.advance(130.0)

        data = _health_from(client).get_json()

        assert data["status"] == "degraded"
        assert "scheduler_stalled" in data["degraded_reasons"]
        assert data["last_cycle"]["cycle_id"] == "failed-on-box"
        assert data["last_successful_cycle_at"] == _STAMP


class TestForwardedCallerGetsOnlyTheBaseFields:
    @pytest.mark.parametrize(
        "headers",
        [
            pytest.param({"X-Forwarded-For": _INTERNET_CALLER}, id="x-forwarded-for"),
            pytest.param({"X-Forwarded-For": ""}, id="x-forwarded-for-empty-value"),
            pytest.param({"Forwarded": f"for={_INTERNET_CALLER}"}, id="forwarded-only"),
            pytest.param({"Forwarded": ""}, id="forwarded-empty-value"),
            pytest.param(
                {"X-Forwarded-For": _INTERNET_CALLER, "Forwarded": f"for={_INTERNET_CALLER}"},
                id="both-headers",
            ),
            pytest.param({"X-Forwarded-For": "127.0.0.1"}, id="forged-loopback-value"),
            pytest.param({"X-Forwarded-For": "::1"}, id="forged-ipv6-loopback-value"),
            pytest.param({"Forwarded": "for=127.0.0.1"}, id="forged-forwarded-loopback-value"),
            pytest.param({"X-Forwarded-For": "not-an-address"}, id="unparsable-value"),
        ],
    )
    def test_a_loopback_peer_with_a_forwarding_header_gets_exactly_three_keys(
        self, client, world, headers
    ):
        _tick()

        resp = _health_from(client, peer=_LOOPBACK_PEER, headers=headers)

        assert resp.status_code == 200
        data = resp.get_json()
        assert set(data) == _BASE_KEYS
        assert _DETAIL_KEYS.isdisjoint(data), "the detail keys are absent, not null"

    @pytest.mark.parametrize(
        "peer",
        [
            pytest.param("10.0.0.5", id="private-ipv4"),
            pytest.param("203.0.113.7", id="public-ipv4"),
            pytest.param("2001:db8::1", id="public-ipv6"),
            pytest.param("::ffff:10.0.0.5", id="ipv4-mapped-non-loopback"),
            pytest.param("0.0.0.0", id="unspecified"),
        ],
    )
    def test_a_non_loopback_peer_with_no_header_gets_exactly_three_keys(self, client, world, peer):
        _tick()

        resp = _health_from(client, peer=peer)

        assert resp.status_code == 200
        assert set(resp.get_json()) == _BASE_KEYS

    def test_the_base_values_equal_the_on_box_values_for_the_same_state(self, client, world):
        _tick()
        state = {"last_successful_cycle_at": _STAMP}

        full = _health_from(client, state=state).get_json()
        base = _health_from(client, headers={"X-Forwarded-For": _INTERNET_CALLER}, state=state)

        assert base.get_json() == {k: full[k] for k in _BASE_KEYS}
        assert base.get_json()["daemon_started_at"] == app_module._DAEMON_STARTED_AT

    def test_a_missing_cycle_stamp_is_present_with_null_for_an_off_box_caller(self, client, world):
        resp = _health_from(client, peer=_OFF_BOX_PEER, state={})

        data = resp.get_json()
        assert "last_successful_cycle_at" in data
        assert data["last_successful_cycle_at"] is None
        assert set(data) == _BASE_KEYS

    def test_a_degraded_daemon_reads_degraded_for_both_classes_and_lists_reasons_on_box_only(
        self, client, world
    ):
        _tick()
        world.mono.advance(130.0)

        on_box = _health_from(client).get_json()
        off_box = _health_from(client, headers={"X-Forwarded-For": _INTERNET_CALLER}).get_json()

        assert on_box["status"] == "degraded"
        assert off_box["status"] == "degraded"
        assert on_box["degraded_reasons"] == ["scheduler_stalled"]
        assert "degraded_reasons" not in off_box
        assert "scheduler_stalled" not in str(off_box)

    def test_a_healthy_daemon_reads_ok_for_both_classes(self, client, world):
        _tick()

        on_box = _health_from(client).get_json()
        off_box = _health_from(client, peer=_OFF_BOX_PEER).get_json()

        assert on_box["status"] == "ok"
        assert off_box["status"] == "ok"

    def test_a_failing_state_read_still_answers_200_with_a_null_stamp_for_both_classes(
        self, client, world
    ):
        _tick()
        with patch.object(app_module.database, "load_state", side_effect=RuntimeError("db down")):
            on_box = client.get("/health", environ_base={"REMOTE_ADDR": _LOOPBACK_PEER})
            off_box = client.get(
                "/health",
                headers={"X-Forwarded-For": _INTERNET_CALLER},
                environ_base={"REMOTE_ADDR": _LOOPBACK_PEER},
            )

        assert on_box.status_code == 200
        assert off_box.status_code == 200
        assert set(on_box.get_json()) == _FULL_KEYS
        assert on_box.get_json()["last_successful_cycle_at"] is None
        assert set(off_box.get_json()) == _BASE_KEYS
        assert off_box.get_json()["last_successful_cycle_at"] is None
        assert "db down" not in on_box.get_data(as_text=True)
        assert "db down" not in off_box.get_data(as_text=True)

    def test_an_off_box_body_carries_no_credential_environment_value_or_caller_address(
        self, client, world, monkeypatch
    ):
        sentinel = "SENTINEL-HEALTH-CALLER-VALUE-DO-NOT-LEAK-5e0b"
        for name in ("COMPOSER_SECRET", "ACCOUNT_ROTH", "DASHBOARD_PASSWORD", "SECRET_KEY"):
            monkeypatch.setenv(name, sentinel)
        _tick()

        resp = _health_from(client, headers={"X-Forwarded-For": _INTERNET_CALLER})

        body = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert sentinel not in body
        assert _INTERNET_CALLER not in body


class TestSessionGrantsNoExtraDetail:
    @pytest.fixture()
    def logged_in_client(self, monkeypatch):
        monkeypatch.setenv("DASHBOARD_PASSWORD", _TEST_PASSWORD)
        monkeypatch.setenv("SECRET_KEY", _TEST_SECRET)
        monkeypatch.setattr(app_module, "_auth_check_enabled", True)
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as c:
            login = c.post("/login", data={"password": _TEST_PASSWORD}, follow_redirects=False)
            assert login.status_code in (302, 303), "a real login must succeed for this check"
            yield c

    def test_an_authenticated_browser_behind_the_proxy_gets_three_keys(
        self, logged_in_client, world
    ):
        _tick()

        resp = _health_from(logged_in_client, headers={"X-Forwarded-For": _INTERNET_CALLER})

        assert resp.status_code == 200
        assert set(resp.get_json()) == _BASE_KEYS

    def test_the_same_session_from_the_box_with_no_header_still_gets_seven(
        self, logged_in_client, world
    ):
        _tick()

        resp = _health_from(logged_in_client)

        assert set(resp.get_json()) == _FULL_KEYS

    def test_an_anonymous_caller_behind_the_proxy_with_the_auth_gate_on_gets_200_and_three_keys(
        self, auth_enabled_client, world
    ):
        _tick()

        resp = _health_from(auth_enabled_client, headers={"X-Forwarded-For": _INTERNET_CALLER})

        assert resp.status_code == 200, "/health stays exempt from the auth gate"
        assert set(resp.get_json()) == _BASE_KEYS

    def test_an_anonymous_on_box_caller_with_the_auth_gate_on_gets_the_seven_keys(
        self, auth_enabled_client, world
    ):
        _tick()

        resp = _health_from(auth_enabled_client)

        assert resp.status_code == 200
        assert set(resp.get_json()) == _FULL_KEYS


class TestPostHealthKeepsItsAnswers:
    @pytest.fixture()
    def gated(self, monkeypatch):
        """Auth and CSRF both on, as production runs."""
        monkeypatch.setenv("DASHBOARD_PASSWORD", _TEST_PASSWORD)
        monkeypatch.setenv("SECRET_KEY", _TEST_SECRET)
        monkeypatch.setattr(app_module, "_auth_check_enabled", True)
        monkeypatch.setattr(app_module, "_csrf_check_enabled", True)
        app_module.app.config["TESTING"] = True
        return SimpleNamespace(token=app_module._CSRF_TOKEN)

    def _session_client(self, gated):
        c = app_module.app.test_client()
        resp = c.post(
            "/login",
            data={"password": _TEST_PASSWORD, "csrf_token": gated.token},
            follow_redirects=False,
        )
        assert resp.status_code in (302, 303), "a real login must succeed for this check"
        return c

    @pytest.mark.parametrize("peer", [_LOOPBACK_PEER, _OFF_BOX_PEER])
    def test_the_four_post_answers_are_302_401_403_405_and_carry_no_health_field(
        self, gated, rw_connection_spy, peer
    ):
        environ = {"REMOTE_ADDR": peer}
        anonymous = app_module.app.test_client()
        anonymous_xhr = app_module.app.test_client()
        no_token = self._session_client(gated)
        with_token = self._session_client(gated)

        answers = {
            "anonymous": anonymous.post("/health", environ_base=environ),
            "anonymous-xhr": anonymous_xhr.post(
                "/health", headers={"X-Requested-With": "XMLHttpRequest"}, environ_base=environ
            ),
            "session-without-token": no_token.post("/health", environ_base=environ),
            "session-with-token": with_token.post(
                "/health", headers={"X-CSRF-Token": gated.token}, environ_base=environ
            ),
        }

        assert {k: r.status_code for k, r in answers.items()} == {
            "anonymous": 302,
            "anonymous-xhr": 401,
            "session-without-token": 403,
            "session-with-token": 405,
        }
        assert answers["anonymous"].headers["Location"].endswith("/login")
        for name, resp in answers.items():
            text = resp.get_data(as_text=True)
            assert "daemon_started_at" not in text, f"{name}: a refused POST carries no body"
            assert "last_successful_cycle_at" not in text, f"{name}: no /health field"
        assert rw_connection_spy.call_count == 0, "a refused POST writes nothing"


class TestConcurrentCallersEachGetTheirOwnShape:
    def test_on_box_and_off_box_requests_arriving_together_never_swap_shapes(self, world):
        import threading

        _tick()
        rounds = 30
        barrier = threading.Barrier(2)
        seen: dict[str, list[frozenset]] = {"on": [], "off": []}
        errors: list[BaseException] = []

        def caller(kind: str, headers: dict) -> None:
            try:
                with app_module.app.test_client() as c:
                    barrier.wait(timeout=10)
                    for _ in range(rounds):
                        resp = c.get(
                            "/health",
                            headers=headers,
                            environ_base={"REMOTE_ADDR": _LOOPBACK_PEER},
                        )
                        seen[kind].append(frozenset(resp.get_json()))
            except BaseException as exc:  # noqa: BLE001 - reported to the main thread
                errors.append(exc)

        app_module.app.config["TESTING"] = True
        with patch.object(
            app_module.database, "load_state", return_value={"last_successful_cycle_at": _STAMP}
        ):
            threads = [
                threading.Thread(target=caller, args=("on", {})),
                threading.Thread(
                    target=caller, args=("off", {"X-Forwarded-For": _INTERNET_CALLER})
                ),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)

        assert not errors, errors
        assert seen["on"] == [frozenset(_FULL_KEYS)] * rounds
        assert seen["off"] == [frozenset(_BASE_KEYS)] * rounds


class TestPeerIsLoopbackHelper:
    @pytest.mark.parametrize(
        ("addr", "expected"),
        [
            ("127.0.0.1", True),
            ("127.0.0.2", True),
            ("127.255.255.254", True),
            ("::1", True),
            ("::ffff:127.0.0.1", True),
            ("::ffff:127.0.0.9", True),
            ("10.0.0.5", False),
            ("203.0.113.7", False),
            ("2001:db8::1", False),
            ("::ffff:10.0.0.5", False),
            ("0.0.0.0", False),
            ("::", False),
            ("128.0.0.1", False),
            ("", False),
            (None, False),
            ("not-an-address", False),
            ("127.0.0.1.evil", False),
            ("127.0.0", False),
            ("localhost", False),
        ],
    )
    def test_loopback_forms_are_true_and_everything_else_including_garbage_is_false(
        self, addr, expected
    ):
        fn = getattr(app_module, "_peer_is_loopback", None)
        if fn is None:
            pytest.fail("app._peer_is_loopback(addr) is missing")

        assert fn(addr) is expected


class TestCallerIsOnBoxHelper:
    @pytest.mark.parametrize(
        ("peer", "headers", "expected"),
        [
            ("127.0.0.1", {}, True),
            ("::1", {}, True),
            ("127.0.0.1", {"X-Forwarded-For": "203.0.113.7"}, False),
            ("127.0.0.1", {"X-Forwarded-For": ""}, False),
            ("127.0.0.1", {"Forwarded": "for=203.0.113.7"}, False),
            ("127.0.0.1", {"X-Forwarded-For": "127.0.0.1", "Forwarded": "for=127.0.0.1"}, False),
            ("10.0.0.5", {}, False),
            ("10.0.0.5", {"X-Forwarded-For": "127.0.0.1"}, False),
        ],
    )
    def test_on_box_needs_a_loopback_peer_and_no_forwarding_header(self, peer, headers, expected):
        fn = getattr(app_module, "_health_caller_is_on_box", None)
        if fn is None:
            pytest.fail("app._health_caller_is_on_box() is missing")

        with app_module.app.test_request_context(
            "/health", headers=headers, environ_base={"REMOTE_ADDR": peer}
        ):
            assert fn() is expected
