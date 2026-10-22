"""The fleet-correlation banner shows when an alert is active, and Dismiss is recorded.

Subject: ``GET /`` renders the banner (sentence, counts, reason, trip time) when the
engine has raised an alert that nobody dismissed; each ``/api/state`` poll shows or
hides it; the Dismiss button sends the CSRF header, hides the banner only when the
server accepted the dismissal, and keeps the first dismissal time on a repeat.  The
market-state banner stays hidden throughout (the header's engine status carries the
market state).

Method: the alert is written by the real engine writer
(``alpha_bot_execution.set_fleet_correlation_alert`` through
``database.write_fleet_alert``) and cleared by the real ``database.clear_fleet_alert``;
the page is the real ``GET /`` markup; the poll bodies are the real ``/api/state``
bodies; the real inline script and ``static/index.js`` run over that markup under node
(``_first_paint_dom``).  The sentence rule restated in ``_expected_sentence`` is the
client's own formula, evaluated by node, so the server text is checked against the
browser's output and not against Python's rounding.

Fixture provenance: ``tests/fixtures/dashboard/card_truth/PROVENANCE.md`` and the
schema-derived ``tests/fixtures/engine/fleet_correlation/alert_state_shape.json``,
validated at runtime by ``database.write_fleet_alert``.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock

import pytest
import requests

import alpha_bot_execution
import app as app_module
import database
from tests.dashboard._first_paint_dom import js_eval, parse_markup, run_page

_SHAPE = json.loads(
    (
        pathlib.Path(__file__).parent.parent
        / "fixtures"
        / "engine"
        / "fleet_correlation"
        / "alert_state_shape.json"
    ).read_text(encoding="utf-8")
)
_EXAMPLE = _SHAPE["example"]

_TOKEN = "csrf-fixture-token"
_DISMISS_URL = "/api/fleet-alert/dismiss"
_CSRF_ROUTE = {"/api/csrf-token": {"body": {"csrf_token": _TOKEN}}}


def _alert(**overrides) -> dict:
    base = {
        "tripped_at_et": _EXAMPLE["tripped_at_et"],
        "triggered_reason": _EXAMPLE["triggered_reason"],
        "tripped_count": _EXAMPLE["tripped_count"],
        "active_count": _EXAMPLE["active_count"],
        "tripped_symphonies": ["Fixture Ridge 01", "Fixture Ridge 02"],
    }
    base.update(overrides)
    return base


# Alert shapes the sentence must render identically on both paths: the shared-rule
# ties of the fleet percentage (1 of 8 is 12.5, 5 of 8 is 62.5, 3 of 8 is 37.5), thirds,
# a reason with a non-ASCII letter and an apostrophe (word boundaries), no percentage
# when nothing tripped or nothing is active (no division by zero), a trip time carrying
# a zone offset.
_ALERT_SHAPES = {
    "base": _alert(),
    "one_of_eight": _alert(tripped_count=1, active_count=8),
    "five_of_eight": _alert(tripped_count=5, active_count=8, triggered_reason="take_profit"),
    "three_of_eight": _alert(tripped_count=3, active_count=8),
    "one_of_three": _alert(tripped_count=1, active_count=3, triggered_reason="VWAP Breakdown"),
    "two_of_three": _alert(tripped_count=2, active_count=3),
    "non_ascii_reason": _alert(triggered_reason="ünder_stop"),
    "apostrophe_reason": _alert(triggered_reason="stop's_hit"),
    "none_tripped": _alert(tripped_count=0, active_count=8),
    "none_active": _alert(tripped_count=3, active_count=0),
    "trip_time_with_offset": _alert(tripped_at_et="2026-05-16T10:33:00-04:00"),
}


def _expected_sentence(alert: dict) -> str:
    text = js_eval(
        "(function(){ const a = p;"
        " const reason = (a.triggered_reason || 'exit').replace(/_/g, ' ')"
        ".replace(/\\b\\w/g, function(c){ return c.toUpperCase(); });"
        " const tripped = a.tripped_count != null ? a.tripped_count : '?';"
        " const active = a.active_count != null ? a.active_count : '?';"
        " const pct = (active && tripped) ? Math.round((tripped / active) * 100) + '%' : '';"
        " const t = a.tripped_at_et ? a.tripped_at_et.slice(11, 16) + ' ET' : '';"
        " return 'Concentrated risk \\u2014 ' + tripped + ' of ' + active +"
        " ' symphonies exited on ' + reason + ' within minutes' +"
        " (pct ? ' (' + pct + ' of the fleet)' : '') + (t ? '. Tripped ' + t + '.' : '.'); })()",
        alert,
    )
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# Helpers over the real app
# ---------------------------------------------------------------------------


@pytest.fixture
def app_env(monkeypatch, tmp_path):
    def refuse(self, method, url, *a, **k):
        raise requests.ConnectionError(f"network refused in test: {method} {url}")

    monkeypatch.setattr(requests.sessions.Session, "request", refuse)
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", str(tmp_path / "absent.env"))
    monkeypatch.setattr(app_module, "get_market_state", lambda *_a, **_k: "open")
    app_module.app.config["TESTING"] = True
    return {"client": app_module.app.test_client(), "tmp": tmp_path, "monkeypatch": monkeypatch}


def _raise_alert(alert: dict) -> dict:
    """The engine's own writer; returns the row as the dashboard will read it."""
    alpha_bot_execution.set_fleet_correlation_alert(
        {},
        alert["tripped_at_et"],
        alert["triggered_reason"],
        alert["tripped_count"],
        alert["active_count"],
        alert.get("tripped_symphonies"),
    )
    row = database.read_fleet_alert()
    assert row is not None, "the engine writer left no alert row"
    return row


def _first_paint(env, state: str = "open") -> str:
    env["monkeypatch"].setattr(app_module, "get_market_state", lambda *_a, **_k: state)
    resp = env["client"].get("/")
    assert resp.status_code == 200
    return resp.data.decode("utf-8")


def _state_body(env, market: str = "open") -> dict:
    env["monkeypatch"].setattr(app_module, "get_market_state", lambda *_a, **_k: market)
    resp = env["client"].get("/api/state")
    assert resp.status_code == 200
    return resp.get_json()


def _drain_dismiss_executor() -> None:
    app_module._DISMISS_EXECUTOR.submit(lambda: None).result(timeout=10)


def _run(env, html: str, *, steps=None, routes=None) -> dict:
    run = run_page(html, env["tmp"], steps=steps or [], routes=routes or {})
    errors = [e for e in run["errors"] if e.startswith(("index.js:", "inline.js:", "step "))]
    assert not errors, f"client code failed: {errors[:2]}"
    return run


def _header(request: dict, name: str):
    for key, value in request["headers"].items():
        if key.lower() == name.lower():
            return value
    return None


def _dismiss_posts(run_or_step: dict) -> list[dict]:
    return [
        r for r in run_or_step["requests"] if r["url"] == _DISMISS_URL and r["method"] == "POST"
    ]


def _walk(node: dict):
    yield node
    for child in node.get("children", []):
        if "tag" in child:
            yield from _walk(child)


# ---------------------------------------------------------------------------
# First paint
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape", sorted(_ALERT_SHAPES))
def test_first_paint_shows_the_banner_with_the_alert_sentence(app_env, shape):
    _raise_alert(_ALERT_SHAPES[shape])
    run = _run(app_env, _first_paint(app_env))
    fleet = run["initial"]["fleet"]
    assert fleet["hidden"] is False, "an undismissed alert must be shown at first paint"
    assert fleet["text"] == _expected_sentence(_ALERT_SHAPES[shape])


def test_first_paint_banner_is_hidden_when_no_alert_exists(app_env):
    run = _run(app_env, _first_paint(app_env))
    assert run["initial"]["fleet"]["present"] is True
    assert run["initial"]["fleet"]["hidden"] is True


def test_first_paint_banner_is_hidden_when_the_alert_was_dismissed(app_env):
    row = _raise_alert(_alert())
    database.write_fleet_alert({**row, "dismissed_at_et": "2026-01-02T03:04:05-05:00"})
    assert _run(app_env, _first_paint(app_env))["initial"]["fleet"]["hidden"] is True


def test_dismiss_button_calls_the_client_handler_and_carries_no_inline_request(app_env):
    _raise_alert(_alert())
    fleet = _run(app_env, _first_paint(app_env))["initial"]["fleet"]
    assert fleet["btnPresent"] is True
    assert "dismissFleetAlert(this)" in fleet["btnOnclick"]
    assert "fetch(" not in fleet["btnOnclick"], "an inline fetch sends no CSRF header"


def test_dismissal_notice_exists_hidden_so_a_refusal_has_somewhere_to_speak(app_env):
    _raise_alert(_alert())
    fleet = _run(app_env, _first_paint(app_env))["initial"]["fleet"]
    assert fleet["noticePresent"] is True
    assert fleet["noticeHidden"] is True


def test_alert_object_rides_the_banner_as_json_for_the_more_info_modal(app_env):
    alert = _alert(triggered_reason="Stop's & <b>go</b>", tripped_symphonies=["A & B", "C's <i>"])
    row = _raise_alert(alert)
    fleet = _run(app_env, _first_paint(app_env))["initial"]["fleet"]
    assert fleet["alertAttr"] is not None, "banner carries no data-fleet-alert"
    assert json.loads(fleet["alertAttr"]) == row


def test_alert_text_is_escaped_never_rendered_as_markup(app_env):
    _raise_alert(_alert(triggered_reason="<b>go</b>"))
    html = _first_paint(app_env)
    tree = parse_markup(html)
    banner = next(
        n for n in _walk(tree) if n.get("attrs", {}).get("id") == "fleet-correlation-banner"
    )
    injected = [n["tag"] for n in _walk(banner) if n.get("tag") in {"b", "i", "img", "script"}]
    assert not injected, f"alert text became markup inside the banner: {injected}"
    run = _run(app_env, html)
    assert "<B>Go</B>" in run["initial"]["fleet"]["text"]


def test_more_info_modal_reads_the_alert_seeded_at_first_paint(app_env):
    _raise_alert(_alert())
    run = _run(app_env, _first_paint(app_env), steps=[{"op": "open_modal"}])
    modal = run["steps"][0]["obs"]["modal"]
    assert modal["hidden"] is False
    assert "Shared exit reason" in modal["specifics"]
    assert "No alert data available" not in modal["specifics"]


# ---------------------------------------------------------------------------
# Polls
# ---------------------------------------------------------------------------


def test_a_poll_carrying_an_alert_shows_the_banner_with_the_first_paint_sentence(app_env):
    html = _first_paint(app_env)
    row = _raise_alert(_ALERT_SHAPES["one_of_eight"])
    body = _state_body(app_env)
    assert body["fleet_correlation_alert"] == row, "premise: the real poll body carries the alert"
    run = _run(
        app_env,
        html,
        routes={**_CSRF_ROUTE, "/api/state": {"body": body}},
        steps=[],
    )
    assert run["initial"]["fleet"]["hidden"] is True
    fleet = run["loaded"]["fleet"]
    assert fleet["hidden"] is False
    server_text = _run(app_env, _first_paint(app_env))["initial"]["fleet"]["text"]
    assert fleet["text"] == server_text == _expected_sentence(_ALERT_SHAPES["one_of_eight"])


def test_a_poll_without_an_alert_hides_the_banner(app_env):
    _raise_alert(_alert())
    html = _first_paint(app_env)
    database.clear_fleet_alert()
    body = _state_body(app_env)
    assert body["fleet_correlation_alert"] is None
    run = _run(app_env, html, routes={**_CSRF_ROUTE, "/api/state": {"body": body}})
    assert run["initial"]["fleet"]["hidden"] is False
    assert run["loaded"]["fleet"]["hidden"] is True


def test_a_poll_carrying_the_legacy_string_alert_shows_the_string(app_env):
    html = _first_paint(app_env)
    body = _state_body(app_env)
    body["fleet_correlation_alert"] = "Fleet-correlated trigger event detected"
    run = _run(app_env, html, routes={**_CSRF_ROUTE, "/api/state": {"body": body}})
    fleet = run["loaded"]["fleet"]
    assert fleet["hidden"] is False
    assert fleet["text"] == "Fleet-correlated trigger event detected"


@pytest.mark.parametrize("market", ["open", "closed_frozen", "pre_market"])
def test_market_state_banner_stays_hidden_on_first_paint_and_after_a_poll(app_env, market):
    html = _first_paint(app_env, state=market)
    body = _state_body(app_env, market=market)
    run = _run(app_env, html, routes={**_CSRF_ROUTE, "/api/state": {"body": body}})
    assert run["initial"]["market"]["hidden"] is True
    assert run["loaded"]["market"]["hidden"] is True
    assert run["loaded"]["market"]["text"] == run["initial"]["market"]["text"]


# ---------------------------------------------------------------------------
# Dismiss in the browser
# ---------------------------------------------------------------------------


def _dismiss_world(env) -> dict:
    row = _raise_alert(_alert())
    html = _first_paint(env)
    same_body = _state_body(env)
    newer = _raise_alert(_alert(tripped_at_et="2026-05-16T11:05:00", tripped_count=7))
    new_body = _state_body(env)
    assert same_body["fleet_correlation_alert"] == row
    assert new_body["fleet_correlation_alert"] == newer
    return {"html": html, "same_body": same_body, "new_body": new_body, "row": row}


def test_accepted_dismissal_posts_with_the_csrf_header_and_hides_the_banner(app_env):
    w = _dismiss_world(app_env)
    run = _run(
        app_env,
        w["html"],
        routes={**_CSRF_ROUTE},
        steps=[
            {
                "op": "dismiss",
                "label": "dismissed",
                "route": {_DISMISS_URL: {"body": {"status": "ok"}}},
            }
        ],
    )
    step = run["steps"][0]
    posts = _dismiss_posts(step)
    assert len(posts) >= 1, "Dismiss sent no POST"
    assert all(_header(p, "X-CSRF-Token") == _TOKEN for p in posts), posts
    assert step["obs"]["fleet"]["hidden"] is True
    assert step["obs"]["fleet"]["noticeHidden"] is True


def test_dismissed_alert_stays_hidden_when_an_earlier_state_response_lands_but_a_new_alert_shows(
    app_env,
):
    w = _dismiss_world(app_env)
    run = _run(
        app_env,
        w["html"],
        routes={**_CSRF_ROUTE},
        steps=[
            {"op": "dismiss", "route": {_DISMISS_URL: {"body": {"status": "ok"}}}},
            {
                "op": "poll",
                "label": "stale response",
                "route": {"/api/state": {"body": w["same_body"]}},
            },
            {"op": "poll", "label": "new alert", "route": {"/api/state": {"body": w["new_body"]}}},
        ],
    )
    stale, fresh = run["steps"][1]["obs"]["fleet"], run["steps"][2]["obs"]["fleet"]
    assert stale["hidden"] is True, "a poll for the dismissed alert re-showed the banner"
    assert _shows_new_alert(fresh)


def _shows_new_alert(fleet: dict) -> bool:
    return fleet["hidden"] is False and "7 of" in fleet["text"]


@pytest.mark.parametrize(
    "answer",
    [
        {"status": 403, "body": {"error": "csrf"}},
        {"status": 500, "body": {"error": "boom"}},
        {"reject": True},
    ],
    ids=["refused_403", "server_error_500", "network_failure"],
)
def test_unsaved_dismissal_keeps_the_banner_and_says_so(app_env, answer):
    w = _dismiss_world(app_env)
    run = _run(
        app_env,
        w["html"],
        routes={**_CSRF_ROUTE},
        steps=[{"op": "dismiss", "route": {_DISMISS_URL: answer}}],
    )
    fleet = run["steps"][0]["obs"]["fleet"]
    assert fleet["hidden"] is False, "the banner hid although the dismissal was not saved"
    assert fleet["noticeHidden"] is False
    assert "not saved" in fleet["noticeText"].lower()
    assert fleet["btnDisabled"] is False, "the button must be usable again to retry"


def test_dismiss_button_is_disabled_while_the_request_is_pending(app_env):
    w = _dismiss_world(app_env)
    run = _run(
        app_env,
        w["html"],
        routes={**_CSRF_ROUTE},
        steps=[
            {
                "op": "dismiss",
                "label": "pending",
                "route": {_DISMISS_URL: {"deferred": True, "body": {}}},
            },
            {"op": "resolve", "label": "resolved"},
        ],
    )
    pending, resolved = run["steps"][0]["obs"]["fleet"], run["steps"][1]["obs"]["fleet"]
    assert pending["btnDisabled"] is True
    assert pending["hidden"] is False, "banner hid before the server answered"
    assert resolved["hidden"] is True


def test_a_retry_after_a_refused_dismissal_succeeds_and_clears_the_notice(app_env):
    w = _dismiss_world(app_env)
    run = _run(
        app_env,
        w["html"],
        routes={**_CSRF_ROUTE},
        steps=[
            {"op": "dismiss", "label": "refused", "route": {_DISMISS_URL: {"status": 403}}},
            {
                "op": "dismiss",
                "label": "accepted",
                "route": {_DISMISS_URL: {"body": {"status": "ok"}}},
            },
        ],
    )
    assert run["steps"][0]["obs"]["fleet"]["noticeHidden"] is False
    after = run["steps"][1]["obs"]["fleet"]
    assert after["hidden"] is True
    assert after["noticeHidden"] is True


def test_dismissing_twice_ends_hidden_with_no_notice(app_env):
    w = _dismiss_world(app_env)
    run = _run(
        app_env,
        w["html"],
        routes={**_CSRF_ROUTE},
        steps=[
            {"op": "dismiss", "route": {_DISMISS_URL: {"body": {"status": "ok"}}}},
            {"op": "dismiss"},
        ],
    )
    final = run["steps"][1]["obs"]["fleet"]
    assert final["hidden"] is True
    assert final["noticeHidden"] is True


def test_dismiss_without_a_token_still_sends_the_header_and_the_refusal_is_shown(app_env):
    """The token fetch failed: the request goes out with an empty header, the server
    refuses it, and the banner says the dismissal was not saved."""
    w = _dismiss_world(app_env)
    run = _run(
        app_env,
        w["html"],
        routes={"/api/csrf-token": {"reject": True}},
        steps=[{"op": "dismiss", "route": {_DISMISS_URL: {"status": 403}}}],
    )
    step = run["steps"][0]
    posts = _dismiss_posts(step)
    assert posts and all(_header(p, "X-CSRF-Token") == "" for p in posts), posts
    assert step["obs"]["fleet"]["hidden"] is False
    assert step["obs"]["fleet"]["noticeHidden"] is False


# ---------------------------------------------------------------------------
# Dismiss route, with the real CSRF hook on
# ---------------------------------------------------------------------------


@pytest.fixture
def csrf_on(monkeypatch):
    monkeypatch.setattr(app_module, "_csrf_check_enabled", True)


def _token(client) -> str:
    resp = client.get("/api/csrf-token")
    assert resp.status_code == 200
    return resp.get_json()["csrf_token"]


def test_dismiss_with_a_valid_token_is_recorded_and_the_banner_stays_hidden_on_reload(
    app_env, csrf_on
):
    row = _raise_alert(_alert())
    client = app_env["client"]
    resp = client.post(_DISMISS_URL, headers={"X-CSRF-Token": _token(client)})
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}
    _drain_dismiss_executor()
    stored = database.read_fleet_alert()
    assert stored["dismissed_at_et"] is not None
    assert {
        k: stored[k] for k in ("tripped_at_et", "triggered_reason", "tripped_count", "active_count")
    } == {
        k: row[k] for k in ("tripped_at_et", "triggered_reason", "tripped_count", "active_count")
    }, "dismissal must not alter the alert itself"
    assert _run(app_env, _first_paint(app_env))["initial"]["fleet"]["hidden"] is True


@pytest.mark.parametrize(
    "headers", [{}, {"X-CSRF-Token": "not-the-token"}], ids=["absent", "wrong"]
)
def test_dismiss_without_a_valid_token_is_refused_and_records_nothing(app_env, csrf_on, headers):
    _raise_alert(_alert())
    resp = app_env["client"].post(_DISMISS_URL, headers=headers)
    assert resp.status_code == 403
    _drain_dismiss_executor()
    assert database.read_fleet_alert()["dismissed_at_et"] is None
    assert _run(app_env, _first_paint(app_env))["initial"]["fleet"]["hidden"] is False


def test_a_second_dismissal_keeps_the_first_dismissal_time(app_env, csrf_on):
    _raise_alert(_alert())
    client = app_env["client"]
    headers = {"X-CSRF-Token": _token(client)}
    assert client.post(_DISMISS_URL, headers=headers).status_code == 200
    _drain_dismiss_executor()
    first = database.read_fleet_alert()["dismissed_at_et"]
    assert first is not None
    assert client.post(_DISMISS_URL, headers=headers).status_code == 200
    _drain_dismiss_executor()
    assert database.read_fleet_alert()["dismissed_at_et"] == first


def test_a_dismissal_already_on_record_is_never_overwritten(app_env, csrf_on):
    """Deterministic form of the same rule: the stored time is a value no clock read
    during the test could equal."""
    row = _raise_alert(_alert())
    recorded = "2026-01-02T03:04:05-05:00"
    database.write_fleet_alert({**row, "dismissed_at_et": recorded})
    client = app_env["client"]
    assert client.post(_DISMISS_URL, headers={"X-CSRF-Token": _token(client)}).status_code == 200
    _drain_dismiss_executor()
    assert database.read_fleet_alert()["dismissed_at_et"] == recorded


def test_dismiss_with_no_alert_is_accepted_and_creates_no_row(app_env, csrf_on):
    client = app_env["client"]
    resp = client.post(_DISMISS_URL, headers={"X-CSRF-Token": _token(client)})
    assert resp.status_code == 200
    _drain_dismiss_executor()
    assert database.read_fleet_alert() is None


# ---------------------------------------------------------------------------
# The engine's writers around a dismissal
# ---------------------------------------------------------------------------


def test_a_new_engine_alert_after_a_dismissal_is_undismissed_and_shows_on_load(app_env):
    row = _raise_alert(_alert())
    database.write_fleet_alert({**row, "dismissed_at_et": "2026-01-02T03:04:05-05:00"})
    assert _run(app_env, _first_paint(app_env))["initial"]["fleet"]["hidden"] is True
    fresh = _raise_alert(_alert(tripped_at_et="2026-05-16T14:20:00", tripped_count=4))
    assert fresh["dismissed_at_et"] is None, "the engine writer must raise an undismissed alert"
    fleet = _run(app_env, _first_paint(app_env))["initial"]["fleet"]
    assert fleet["hidden"] is False
    assert fleet["text"] == _expected_sentence(
        _alert(tripped_at_et="2026-05-16T14:20:00", tripped_count=4)
    )


def test_engine_auto_clear_removes_the_row_and_the_page_renders_the_banner_hidden(app_env):
    _raise_alert(_alert())
    assert _run(app_env, _first_paint(app_env))["initial"]["fleet"]["hidden"] is False
    database.clear_fleet_alert()
    assert database.read_fleet_alert() is None
    assert _run(app_env, _first_paint(app_env))["initial"]["fleet"]["hidden"] is True


# ---------------------------------------------------------------------------
# The producers behind the banner fail soft; the state route reads the same alert
# ---------------------------------------------------------------------------


def test_a_stand_in_alert_that_is_not_a_dict_never_breaks_the_page_or_shows_the_banner(app_env):
    app_env["monkeypatch"].setattr(database, "read_fleet_alert", lambda: MagicMock())
    resp = app_env["client"].get("/")
    assert resp.status_code == 200
    run = _run(app_env, resp.data.decode("utf-8"))
    assert run["initial"]["fleet"]["hidden"] is True


def test_an_unreadable_alert_table_degrades_to_no_banner_and_a_page(app_env):
    def broken():
        raise RuntimeError("fleet_alert_state unreadable")

    app_env["monkeypatch"].setattr(database, "read_fleet_alert", broken)
    resp = app_env["client"].get("/")
    assert resp.status_code == 200
    run = _run(app_env, resp.data.decode("utf-8"))
    assert run["initial"]["fleet"]["hidden"] is True


def test_state_poll_exposes_the_active_alert_then_none_after_a_dismissal(app_env, csrf_on):
    row = _raise_alert(_alert())
    client = app_env["client"]
    assert _state_body(app_env)["fleet_correlation_alert"] == row
    assert client.post(_DISMISS_URL, headers={"X-CSRF-Token": _token(client)}).status_code == 200
    _drain_dismiss_executor()
    assert _state_body(app_env)["fleet_correlation_alert"] is None
