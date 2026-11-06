"""Server contract the dashboard refresh relies on: ``degraded_reasons`` on ``/api/state`` and the HEAD probe.

Real call path: the Flask test client against the real ``/api/state`` and ``/api/events`` routes.

* ``/api/state`` carries ``degraded_reasons`` on every branch, read from plan 02's ``_health_status()`` (the
  header turns it into the "Degraded" label); a failing helper gives an empty list and one WARNING that
  names the exception type only.
* ``HEAD /api/events`` answers the auth gate's status (401 without a session, 200 with one) and registers
  no stream client, so the page can ask why its stream closed without opening another.

The live-branch state is the verbatim symphony entry of tests/fixtures/refresh_timing/02-c5/.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import time
from pathlib import Path

import pytest

import analytics as analytics_module
import app as app_module
import database as database_module

_ENTRY = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "refresh_timing"
        / "02-c5"
        / "symphony_state_entry.json"
    ).read_text("utf-8")
)
_SYMPHONY_ID = next(iter(_ENTRY))


def _health(reasons: list[str]):
    def _status() -> dict:
        return {
            "status": "degraded" if reasons else "ok",
            "degraded_reasons": list(reasons),
            "scheduler_last_tick_at": None,
            "engine_running_since": None,
            "last_cycle": None,
        }

    return _status


@pytest.fixture()
def state_client(monkeypatch):
    monkeypatch.setattr(analytics_module, "DB_FILE", os.environ["DB_PATH"])
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _seed_live_state() -> None:
    database_module.save_state({_SYMPHONY_ID: copy.deepcopy(_ENTRY[_SYMPHONY_ID])})


class TestStateCarriesTheDegradedReasons:
    @pytest.mark.parametrize(
        "reasons",
        [[], ["scheduler_stalled"], ["engine_overrun", "engine_failing"]],
        ids=["healthy", "scheduler stalled", "two reasons"],
    )
    @pytest.mark.parametrize("branch", ["waiting", "live"])
    def test_the_body_lists_the_reasons_the_health_helper_reports(
        self, state_client, monkeypatch, branch, reasons
    ):
        monkeypatch.setattr(app_module, "_health_status", _health(reasons))
        monkeypatch.setattr(app_module, "get_market_state", lambda *_a, **_k: "open")
        if branch == "live":
            _seed_live_state()

        response = state_client.get("/api/state")

        assert response.status_code == 200
        body = response.get_json()
        assert "degraded_reasons" in body, "/api/state must carry degraded_reasons on every branch"
        assert body["degraded_reasons"] == reasons

    def test_a_failing_health_helper_gives_an_empty_list_and_one_warning_naming_the_type(
        self, state_client, monkeypatch, caplog
    ):
        def _fails() -> dict:
            raise RuntimeError("scheduler internals the page must never see")

        monkeypatch.setattr(app_module, "_health_status", _fails)
        monkeypatch.setattr(app_module, "get_market_state", lambda *_a, **_k: "open")
        _seed_live_state()

        with caplog.at_level(logging.WARNING, logger="alphabot"):
            response = state_client.get("/api/state")

        assert response.status_code == 200, "a health read error must not fail the state route"
        assert response.get_json().get("degraded_reasons") == [], (
            "the label must never claim Degraded on a read error"
        )
        health_records = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "RuntimeError" in r.getMessage()
        ]
        assert len(health_records) == 1, [r.getMessage() for r in caplog.records]
        assert "scheduler internals" not in health_records[0].getMessage(), (
            "the warning names the exception type only"
        )

    def test_the_helper_is_called_once_per_response(self, state_client, monkeypatch):
        calls: list[int] = []
        status = _health(["scheduler_stalled"])

        def _counted() -> dict:
            calls.append(1)
            return status()

        monkeypatch.setattr(app_module, "_health_status", _counted)
        monkeypatch.setattr(app_module, "get_market_state", lambda *_a, **_k: "open")
        _seed_live_state()

        state_client.get("/api/state")

        assert len(calls) == 1, "one health reading per response keeps the body self-consistent"


@pytest.fixture()
def authed_client(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "refresh-timing-password")
    monkeypatch.setenv("SECRET_KEY", "refresh-timing-secret")
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        signed_in = c.post("/login", data={"password": "refresh-timing-password"})
        assert signed_in.status_code == 302, "premise: the real login signs the operator in"
        yield c


class TestHeadProbeOfTheEventStream:
    def test_head_without_a_session_is_refused_with_401(self, client_no_auth):
        response = client_no_auth.head("/api/events")

        assert response.status_code == 401
        assert "application/json" in (response.content_type or "")

    def test_head_with_a_session_is_200_and_registers_no_client(self, authed_client):
        before = len(app_module._sse_clients)

        response = authed_client.head("/api/events")

        assert response.status_code == 200
        assert len(app_module._sse_clients) == before, (
            "a HEAD probe must not register a stream client: the page asks why a stream closed, "
            "and a registered probe would leak a queue per attempt"
        )
