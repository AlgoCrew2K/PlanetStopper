"""RED: ``GET /api/header-status``, the light status read the four other pages' header polls.

CONTRACT PINNED (every field name is one ``/api/state`` already uses, so the header functions
that read ``/api/state`` read this body unchanged):

* ``daemon_started_at``       the process start stamp (``app._DAEMON_STARTED_AT``)
* ``last_successful_cycle_at`` the engine's last cycle, top level of ``database.load_state()``
* ``market_state``            ``get_market_state(<ET now>)``
* ``engine_phase``            ``app._engine_phase(now_et, market_state, last_cycle_at)``
* ``status``, ``degraded_reasons``, ``scheduler_last_tick_at``, ``engine_running_since``,
  ``last_cycle``             ``app._health_status()`` verbatim
* ``next_run_seconds``        ``app._seconds_to_next_minute(<ET now>)``, the one countdown source
* ``meta``                    ``mode``, ``system_online``, ``account`` and ``next_run`` of
                              ``_build_meta({}, ...)``, the call the page routes render with

The route reads the Eastern clock through ``app._now_et()`` at call time (the daemon's wall-clock
seam), so a test that replaces ``app._now_et`` controls it. Auth, CSRF and the read-only database
rule apply as to every ``/api/`` route: the route is not auth exempt, answers a lapsed session 401
JSON, and never opens a read-write connection.

Server instants are built with ``ZoneInfo``; the state and ``.env`` reads are patched, so nothing of
the developer's ``.env`` or database is read. Expected values are derived from the instant the test
set, never copied from a capture.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

import app as app_module

ET = ZoneInfo("America/New_York")
ROUTE = "/api/header-status"
MAX_BODY_BYTES = 10 * 1024
MAX_SECONDS_TO_ANSWER = 1.0


def et(hour, minute, second=0):
    return datetime(2026, 10, 22, hour, minute, second, tzinfo=ET)


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def at(monkeypatch):
    """Install a settable server clock and an engine whose last cycle was 20 s ago."""
    clock = {"now": et(10, 43, 12)}
    monkeypatch.setattr(app_module, "_now_et", lambda: clock["now"])

    def state():
        return {"last_successful_cycle_at": (clock["now"] - timedelta(seconds=20)).isoformat()}

    monkeypatch.setattr(app_module.database, "load_state", state)
    monkeypatch.setattr(
        app_module._dotenv_module,
        "dotenv_values",
        lambda *_a, **_k: {"LIVE_EXECUTION": "False", "ACCOUNT_INDIVIDUAL": "0123abcd-1"},
    )
    return clock


def get_ok(client):
    response = client.get(ROUTE)
    assert response.status_code != 404, f"GET {ROUTE} is not registered"
    assert response.status_code == 200, f"GET {ROUTE} answered {response.status_code}"
    return response.get_json()


class TestShape:
    def test_body_carries_the_fields_the_header_functions_read(self, client, at):
        body = get_ok(client)
        for key in (
            "daemon_started_at",
            "last_successful_cycle_at",
            "market_state",
            "engine_phase",
            "status",
            "degraded_reasons",
            "scheduler_last_tick_at",
            "engine_running_since",
            "last_cycle",
            "next_run_seconds",
            "meta",
        ):
            assert key in body, f"{key} is missing from the header status body"
        assert body["daemon_started_at"] == app_module._DAEMON_STARTED_AT
        assert isinstance(body["degraded_reasons"], list)
        for key in ("mode", "system_online", "account", "next_run"):
            assert key in body["meta"], f"meta.{key} is missing"

    def test_last_cycle_is_what_the_state_says(self, client, at):
        body = get_ok(client)
        assert body["last_successful_cycle_at"] == (at["now"] - timedelta(seconds=20)).isoformat()

    def test_answers_small_and_fast(self, client, at):
        started = time.monotonic()
        response = client.get(ROUTE)
        elapsed = time.monotonic() - started
        assert response.status_code == 200
        assert len(response.get_data()) < MAX_BODY_BYTES
        assert elapsed < MAX_SECONDS_TO_ANSWER

    def test_carries_nothing_the_state_route_is_slow_for(self, client, at):
        body = get_ok(client)
        for heavy in ("html", "state", "bot_state", "symphonies", "portfolio_strip"):
            assert heavy not in body, f"{heavy} makes this the heavy read it replaces"


class TestMarketAndPhase:
    @pytest.mark.parametrize(
        ("when", "expected_open"),
        [(et(15, 59, 30), True), (et(16, 0, 20), False), (et(9, 25), False), (et(9, 31), True)],
    )
    def test_market_state_follows_the_server_clock(self, client, at, when, expected_open):
        at["now"] = when
        body = get_ok(client)
        assert (body["market_state"] == "open") is expected_open
        assert body["meta"]["system_online"] is expected_open

    def test_rebalance_pause_phase_before_the_close(self, client, at):
        at["now"] = et(15, 56)
        body = get_ok(client)
        assert body["engine_phase"] == app_module._engine_phase(
            et(15, 56), body["market_state"], body["last_successful_cycle_at"]
        )
        assert body["engine_phase"] == "blackout"

    def test_opening_phase_until_the_first_cycle_of_the_session(self, client, monkeypatch):
        now = et(9, 30, 20)
        monkeypatch.setattr(app_module, "_now_et", lambda: now)
        monkeypatch.setattr(app_module._dotenv_module, "dotenv_values", lambda *_a, **_k: {})
        yesterday = (now - timedelta(days=1)).isoformat()
        monkeypatch.setattr(
            app_module.database, "load_state", lambda: {"last_successful_cycle_at": yesterday}
        )
        assert get_ok(client)["engine_phase"] == "opening"
        today = (now - timedelta(seconds=5)).isoformat()
        monkeypatch.setattr(
            app_module.database, "load_state", lambda: {"last_successful_cycle_at": today}
        )
        assert get_ok(client)["engine_phase"] == "running"

    def test_closed_market_phase(self, client, at):
        at["now"] = et(20, 0)
        assert get_ok(client)["engine_phase"] == "closed"


class TestCountdownSource:
    @pytest.mark.parametrize(
        ("second", "micro", "expected"),
        [(0, 0, 60), (12, 500000, 47), (59, 999000, 0), (30, 0, 30)],
    )
    def test_seconds_to_next_minute_is_one_function(self, second, micro, expected):
        assert hasattr(app_module, "_seconds_to_next_minute"), (
            "app._seconds_to_next_minute(now) is the one source of the countdown"
        )
        now = datetime(2026, 10, 22, 10, 43, second, micro, tzinfo=ET)
        assert app_module._seconds_to_next_minute(now) == expected

    def test_route_countdown_comes_from_the_server_clock(self, client, at):
        at["now"] = et(10, 43, 5)
        assert get_ok(client)["next_run_seconds"] == app_module._seconds_to_next_minute(
            et(10, 43, 5)
        )

    def test_state_route_uses_the_same_function(self):
        source = Path(app_module.__file__).read_text(encoding="utf-8")
        assert source.count("_seconds_to_next_minute(") >= 3, (
            "get_state and the header status route must both call _seconds_to_next_minute "
            "(the definition plus two call sites)"
        )


class TestModeAndAccount:
    def test_mode_follows_the_live_flag(self, client, at, monkeypatch):
        assert get_ok(client)["meta"]["mode"] == "DRY RUN"
        monkeypatch.setattr(
            app_module._dotenv_module, "dotenv_values", lambda *_a, **_k: {"LIVE_EXECUTION": "True"}
        )
        assert get_ok(client)["meta"]["mode"] == "LIVE"

    def test_account_label_is_the_pages_own(self, client, at):
        account = get_ok(client)["meta"]["account"]
        assert account["label"] == "Individual"
        assert account["uuid_short"] == "0123abcd"


class TestLiveness:
    def test_degraded_reasons_are_the_health_helpers(self, client, at, monkeypatch):
        health = {
            "status": "degraded",
            "degraded_reasons": ["engine_overrun"],
            "scheduler_last_tick_at": "2026-10-22T10:43:00-04:00",
            "engine_running_since": "2026-10-22T10:39:00-04:00",
            "last_cycle": None,
        }
        monkeypatch.setattr(app_module, "_health_status", lambda: dict(health))
        body = get_ok(client)
        for key, value in health.items():
            assert body[key] == value, f"{key} must be the health helper's value"

    def test_a_healthy_daemon_reports_no_reasons(self, client, at, monkeypatch):
        # A daemon that ticked just now. With no tick stamped the check counts from the process
        # start, and a test process that has run for minutes would read as a stalled scheduler.
        monkeypatch.setattr(app_module, "_scheduler_last_tick_mono", app_module._monotonic())
        body = get_ok(client)
        assert body["degraded_reasons"] == []
        assert body["status"] == "ok"


class TestFailureAndAuth:
    def test_a_failed_read_answers_500_without_exception_text(self, client, at, monkeypatch):
        def boom():
            raise RuntimeError("state read failed at /private/path/state.db")

        monkeypatch.setattr(app_module.database, "load_state", boom)
        monkeypatch.setitem(app_module.app.config, "PROPAGATE_EXCEPTIONS", False)
        response = client.get(ROUTE)
        assert response.status_code != 404, f"GET {ROUTE} is not registered"
        assert response.status_code == 500
        assert response.get_json() == {"status": "error", "message": "An internal error occurred"}
        assert "/private/path" not in response.get_data(as_text=True)

    def test_lapsed_session_gets_401_json_not_a_redirect(self, at, monkeypatch):
        monkeypatch.setenv("DASHBOARD_PASSWORD", "placeholder-password")
        monkeypatch.setenv("SECRET_KEY", "placeholder-secret-key")
        monkeypatch.setattr(app_module, "_auth_check_enabled", True)
        with app_module.app.test_client() as anonymous:
            response = anonymous.get(ROUTE)
        assert response.status_code == 401, (
            "a lapsed session must read 401, which sends the page to /login"
        )
        assert response.get_json() is not None

    def test_route_is_behind_the_auth_gate(self):
        assert "header_status" not in app_module._AUTH_EXEMPT_ENDPOINTS
        assert any(
            rule.rule == ROUTE and "GET" in rule.methods
            for rule in app_module.app.url_map.iter_rules()
        ), f"{ROUTE} is not a registered GET route"

    def test_post_is_not_allowed(self, client):
        assert client.post(ROUTE).status_code in (404, 405)

    def test_route_never_opens_a_read_write_connection(self, client, at):
        spy = MagicMock(side_effect=AssertionError("header status opened a read-write connection"))
        with patch.object(app_module.database, "get_connection", spy):
            get_ok(client)
        spy.assert_not_called()


class TestNoOrderPath:
    def test_handler_reaches_no_order_or_creation_call(self):
        import ast
        import inspect
        import textwrap

        assert hasattr(app_module, "header_status"), "the route handler is named header_status"
        tree = ast.parse(textwrap.dedent(inspect.getsource(app_module.header_status)))
        seen = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                seen.add(node.attr)
            elif isinstance(node, ast.Name):
                seen.add(node.id)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                seen.add(node.value)
        for forbidden in (
            "sell_account",
            "save_symphony",
            "composer_draft_client",
            "LIVE_EXECUTION",
        ):
            assert forbidden not in seen, f"header_status must not reference {forbidden}"
