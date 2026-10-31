"""RED: an on-demand Frontrunner build says how it ended (``FRONTRUNNER_BUILD_SUMMARY``).

CONTRACT PINNED
* ``POST /ai-advisor/frontrunner-builder/run`` answers 202 ``{"status": "started", "run_id": <text>}``.
* The route's background wrapper writes exactly one append-only row per run into the existing
  ``advisor_observations`` table (no migration), ``advisor_role`` ``FRONTRUNNER_BUILD_SUMMARY``,
  ``is_advisory_only`` 1, whose ``raw_response`` is a dict:
  ``{run_id, status, proposals, symphonies, failed_symphonies, error_code}``.
  ``run_id`` equals the 202's. ``status`` is ``completed`` (``proposals`` is how many proposal rows
  the build inserted, 0 included, ``error_code`` None) or ``failed`` with an ``error_code``:
  ``roster_unavailable`` (the live roster could not be read), ``all_symphonies_failed`` (every
  symphony's build raised) or, for an exception that escaped the whole build, the exception's TYPE
  NAME only, never its message.
* ``advisors.frontrunner_builder._run_build_for_symphony`` returns how many proposals it inserted and
  ``run_frontrunner_build`` returns a dict (it returned ``None``); the weekly scheduler's call writes
  no summary row (only the route's wrapper does).
* ``GET /api/frontrunner-build/last-run`` answers ``{"last_run": <newest row's raw_response>}`` or
  ``{"last_run": null}``; it is read-only, behind the auth gate, and the role stays out of
  ``app._ADVISOR_ROLES``.

The build's leaves are patched at their origin (the roster, the per-symphony build, the build itself);
the summary rows are written and read through the real ``database`` functions.
"""

from __future__ import annotations

import concurrent.futures
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import app as app_module
import database
from advisors import frontrunner_builder

ROLE = "FRONTRUNNER_BUILD_SUMMARY"
RUN = "/ai-advisor/frontrunner-builder/run"
LAST = "/api/frontrunner-build/last-run"
BASE_MIGRATION_FILE_COUNT = 39  # migrations/*.sql at the plan's base: this work adds none


class InlineExecutor:
    """Runs a submitted closure at once, on the calling thread, so the test sees its effects."""

    def submit(self, fn, *args, **kwargs):
        future = concurrent.futures.Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001 - mirrors a real executor holding the exception
            future.set_exception(exc)
        return future


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder-key-not-used")
    monkeypatch.setattr(app_module, "_FRONTRUNNER_BUILD_EXECUTOR", InlineExecutor())
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def summaries():
    return database.get_advisor_observations_for_role(ROLE, limit=50)


def start(client, *, roster=("sym-a", "sym-b"), built=(2, 1), raises=None):
    """POST the run with the build's leaves patched; returns the 202 body.

    ``built`` is what each symphony's build inserts; an exception instance in it is raised instead.
    """
    results = list(built)

    def per_symphony(symphony_id, *a, **k):
        value = results.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    with (
        patch.object(frontrunner_builder, "_resolve_live_symphony_roster", lambda: list(roster)),
        patch.object(frontrunner_builder, "_run_build_for_symphony", per_symphony),
    ):
        if raises is not None:
            with patch.object(frontrunner_builder, "run_frontrunner_build", side_effect=raises):
                response = client.post(RUN, json={})
        else:
            response = client.post(RUN, json={})
    assert response.status_code == 202, response.get_data(as_text=True)
    return response.get_json()


class TestRunIdentity:
    def test_the_202_names_the_run(self, client):
        body = start(client)
        assert body["status"] == "started"
        assert isinstance(body.get("run_id"), str) and body["run_id"], "the 202 carries no run_id"

    def test_two_runs_have_two_run_ids(self, client):
        first, second = start(client), start(client, built=(0, 0))
        assert first["run_id"] != second["run_id"]


class TestCompleted:
    def test_a_build_with_proposals_writes_one_completed_row(self, client):
        body = start(client, built=(2, 1))
        (row,) = summaries()
        raw = row["raw_response"]
        assert row["advisor_role"] == ROLE and row["is_advisory_only"] == 1
        assert raw["run_id"] == body["run_id"]
        assert raw["status"] == "completed" and raw["proposals"] == 3
        assert raw["error_code"] in (None, "")
        assert raw["symphonies"] == 2

    def test_a_build_that_inserts_nothing_is_completed_with_zero(self, client):
        start(client, built=(0, 0))
        (row,) = summaries()
        assert (
            row["raw_response"]["status"] == "completed" and row["raw_response"]["proposals"] == 0
        )

    def test_one_symphony_failing_does_not_fail_the_build(self, client):
        start(client, built=(RuntimeError("secret detail /private/path"), 2))
        (row,) = summaries()
        raw = row["raw_response"]
        assert raw["status"] == "completed" and raw["proposals"] == 2
        assert raw["failed_symphonies"], "the failed symphony must be counted or listed"
        assert "secret detail" not in str(raw) and "/private/path" not in str(raw)


class TestFailed:
    def test_every_symphony_failing_is_all_symphonies_failed(self, client):
        start(client, built=(RuntimeError("a"), ValueError("b")))
        (row,) = summaries()
        assert row["raw_response"]["status"] == "failed"
        assert row["raw_response"]["error_code"] == "all_symphonies_failed"

    def test_an_unreadable_roster_is_roster_unavailable(self, client):
        def boom():
            raise RuntimeError("bot_state unreadable at /private/db")

        with patch.object(frontrunner_builder, "_resolve_live_symphony_roster", boom):
            assert client.post(RUN, json={}).status_code == 202
        (row,) = summaries()
        assert row["raw_response"]["status"] == "failed"
        assert row["raw_response"]["error_code"] == "roster_unavailable"
        assert "/private/db" not in str(row["raw_response"])

    def test_an_escaped_exception_is_its_type_name_only(self, client):
        start(client, raises=KeyError("secret detail /private/path"))
        (row,) = summaries()
        raw = row["raw_response"]
        assert raw["status"] == "failed" and raw["error_code"] == "KeyError"
        assert "secret detail" not in str(raw) and "/private/path" not in str(raw)


class TestAppendOnly:
    def test_each_run_adds_one_row_and_changes_no_earlier_one(self, client):
        start(client, built=(1, 1))
        (first,) = summaries()
        start(client, built=(0, 0))
        rows = summaries()
        assert len(rows) == 2
        older = next(r for r in rows if r["id"] == first["id"])
        assert older == first, "an earlier summary row was changed"

    def test_the_weekly_path_writes_no_summary(self):
        with (
            patch.object(frontrunner_builder, "_resolve_live_symphony_roster", lambda: ["sym-a"]),
            patch.object(frontrunner_builder, "_run_build_for_symphony", lambda *_a, **_k: 4),
        ):
            result = frontrunner_builder.run_frontrunner_build()
        assert isinstance(result, dict), "run_frontrunner_build must return its outcome, not None"
        assert summaries() == []

    def test_no_schema_change(self):
        migrations = Path(app_module.__file__).parent / "migrations"
        assert len(list(migrations.glob("*.sql"))) == BASE_MIGRATION_FILE_COUNT
        assert not any(ROLE in p.read_text(encoding="utf-8") for p in migrations.glob("*.sql"))

    def test_the_role_stays_out_of_the_overview_list(self):
        assert ROLE not in app_module._ADVISOR_ROLES


def last_run_body(client):
    response = client.get(LAST)
    assert response.status_code != 404, f"GET {LAST} is not registered"
    assert response.status_code == 200
    return response.get_json()


class TestLastRunRead:
    def test_none_before_any_run(self, client):
        response = client.get(LAST)
        assert response.status_code != 404, f"GET {LAST} is not registered"
        assert response.status_code == 200
        assert response.get_json() == {"last_run": None}

    def test_the_newest_runs_summary(self, client):
        start(client, built=(1, 1))
        newest = start(client, built=(0, 0))
        body = last_run_body(client)
        assert body["last_run"]["run_id"] == newest["run_id"]
        assert body["last_run"]["status"] == "completed" and body["last_run"]["proposals"] == 0

    def test_a_failed_run_is_read_with_its_code(self, client):
        start(client, built=(RuntimeError("x"), RuntimeError("y")))
        assert last_run_body(client)["last_run"]["error_code"] == "all_symphonies_failed"

    def test_behind_the_auth_gate_and_read_only(self, client, monkeypatch):
        assert "frontrunner_build_last_run" not in app_module._AUTH_EXEMPT_ENDPOINTS
        spy = MagicMock(side_effect=AssertionError("the read opened a read-write connection"))
        monkeypatch.setattr(database, "get_connection", spy)
        last_run_body(client)
        spy.assert_not_called()
        monkeypatch.setenv("DASHBOARD_PASSWORD", "placeholder-password")
        monkeypatch.setenv("SECRET_KEY", "placeholder-secret-key")
        monkeypatch.setattr(app_module, "_auth_check_enabled", True)
        with app_module.app.test_client() as anonymous:
            assert anonymous.get(LAST).status_code == 401

    def test_post_is_not_allowed(self, client):
        assert client.post(LAST).status_code in (404, 405)
