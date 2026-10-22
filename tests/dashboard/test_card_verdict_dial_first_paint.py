"""The card verdict line and the MC dial read the same on first paint and after a refresh.

Subject: ``GET /`` renders each active card's verdict and dial on the server;
``static/index.js`` rewrites both on every ``/api/state`` poll.  The two writers must
agree on text, on rounding of exact binary ties, and on the dial's colour reference,
and a poll that carries no guard alpha (the closed-market body) or no answer at all
must never replace a real verdict with the empty glyph.

Method: seed the real database (bot_state through ``database.save_state``, exit rows
through ``database.record_exit_trigger``), take the real ``GET /`` markup and the real
``/api/state`` bodies for the same state, then run the real client code over that
markup under node (``_first_paint_dom``).  The rounding rule is the browser's
``Number.prototype.toFixed``; expected strings come from node, never from Python's
formatting.  A page is judged visible only on a live render; this file proves text,
attributes and hidden state.

Fixture provenance: ``tests/fixtures/dashboard/card_truth/PROVENANCE.md``.
"""

from __future__ import annotations

import copy
import json
import pathlib

import pytest
import requests

import app as app_module
import database
from tests.dashboard._first_paint_dom import js_eval, js_to_fixed, run_page

_DROPLET = pathlib.Path(__file__).parent.parent / "fixtures" / "prod_droplet"

_DASH = "—"
_INVENTED_ACCOUNT = "7e5b2c1a-4d3f-4a86-9b0e-2f6c8d1a3e57"

# Guard-alpha values the server and client must round identically.  The exact binary
# ties (0.25, 0.75, 1.25, -0.25, -0.75, -1.75) are where Python's own formatting and
# toFixed part ways; 0.04 and -0.04 sit beside zero; 0.0 is the zero case.
_GUARD_ALPHA_FAMILY = [0.25, 0.75, 1.25, 2.5, -0.25, -0.75, -1.75, 0.0, 0.04, -0.04]

_ABSENT = object()
# MC readings (0-100 scale): ties at the label's zero places (0.5, 12.5, 62.5, 99.5),
# a tie at the arc offset's two places (50 gives 47.125), the colour thresholds
# (14.99, 15, 80, 80.5), clamps (100, 150, -5) and zero.
_DIAL_FAMILY = [
    ("zero", 0),
    ("half", 0.5),
    ("p12_5", 12.5),
    ("p14_99", 14.99),
    ("p15", 15),
    ("p50", 50),
    ("p62_5", 62.5),
    ("p80", 80),
    ("p80_5", 80.5),
    ("p99_5", 99.5),
    ("p100", 100),
    ("p150", 150),
    ("negative", -5),
    ("null", None),
    ("missing", _ABSENT),
]
_SENTINEL_FAMILY = [("sentinel_high", 999.0), ("sentinel_low", -999.0)]

_CACHE: dict = {}


def _load_json(name: str):
    return json.loads((_DROPLET / name).read_text(encoding="utf-8"))


def _base_records() -> tuple[dict, dict, dict]:
    positions = _load_json("bot_state_positions.json")
    triggered = next(v for v in positions.values() if v.get("triggered"))
    untriggered = next(v for v in positions.values() if not v.get("triggered"))
    exit_row = next(r for r in _load_json("exit_triggers.json") if r["triggered_reason"])
    return copy.deepcopy(triggered), copy.deepcopy(untriggered), copy.deepcopy(exit_row)


def _invented_id(n: int) -> str:
    return f"ct{n:018d}"


def _triggered_state(n: int) -> dict:
    record, _, _ = _base_records()
    # current_return is edited to 1.0 so the injected difference (exit return minus
    # current return) is exact in binary for the dyadic family values.
    record.update(name=f"Fixture Ridge {n:02d}", account=_INVENTED_ACCOUNT, current_return=1.0)
    return record


def _untriggered_state(n: int, mc) -> dict:
    _, record, _ = _base_records()
    record.update(
        name=f"Fixture Meadow {n:02d}", account=_INVENTED_ACCOUNT, armed=True, current_return=1.0
    )
    if mc is _ABSENT:
        record.pop("mc_prob", None)
    else:
        record["mc_prob"] = mc
    return record


def _network_refused(monkeypatch) -> None:
    def refuse(self, method, url, *a, **k):
        raise requests.ConnectionError(f"network refused in test: {method} {url}")

    monkeypatch.setattr(requests.sessions.Session, "request", refuse)


def _seed_exit_row(symphony_id: str, at_return: float) -> None:
    _, _, row = _base_records()
    row_id = database.record_exit_trigger(
        symphony_id=symphony_id,
        account_id=_INVENTED_ACCOUNT,
        triggered_reason=row["triggered_reason"],
        at_return=at_return,
        cycle_id=row["cycle_id"],
        ts_utc=row["ts_utc"],
        ts_et=row["ts_et"],
    )
    assert row_id is not None, "the real exit-trigger writer refused the fixture row"


def _client():
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def _market(monkeypatch, state: str) -> None:
    monkeypatch.setattr(app_module, "get_market_state", lambda *_a, **_k: state)


def _page_world(monkeypatch, tmp_path) -> dict:
    """One seeded world, rendered once: the server markup, the open and closed
    ``/api/state`` bodies, and two node runs over the markup."""
    if "world" in _CACHE:
        return _CACHE["world"]

    _network_refused(monkeypatch)
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", str(tmp_path / "absent.env"))

    state: dict = {}
    ids: dict = {"guard": {}, "dial": {}, "sentinel": {}}
    n = 0
    for ga in _GUARD_ALPHA_FAMILY:
        n += 1
        sid = _invented_id(n)
        state[sid] = _triggered_state(n)
        ids["guard"][ga] = sid
    n += 1
    ids["no_exit_row"] = _invented_id(n)
    state[ids["no_exit_row"]] = _triggered_state(n)
    for label, mc in _DIAL_FAMILY + _SENTINEL_FAMILY:
        n += 1
        sid = _invented_id(n)
        state[sid] = _untriggered_state(n, mc)
        ids["sentinel" if label.startswith("sentinel") else "dial"][label] = sid

    database.save_state(state)
    for ga, sid in ids["guard"].items():
        _seed_exit_row(sid, at_return=1.0 + ga)

    client = _client()
    first = client.get("/")
    assert first.status_code == 200
    html = first.data.decode("utf-8")

    _market(monkeypatch, "open")
    open_resp = client.get("/api/state")
    assert open_resp.status_code == 200
    open_payload = open_resp.get_json()

    snapshot = {
        "trading_day": "2026-10-21",
        "captured_at_et": "16:00:00 ET",
        "data_as_of": "16:00 ET",
        "portfolio_strip": {"today_change": None, "cumulative_return": None, "max_drawdown": None},
        "shadow_divergence": {"by_symphony": {}, "portfolio_today": None},
        "accounts_map": {_INVENTED_ACCOUNT: [dict(v, id=k) for k, v in state.items()]},
    }
    database.save_state({**state, "last_market_close_snapshot": snapshot})
    _market(monkeypatch, "closed_frozen")
    closed_resp = client.get("/api/state")
    assert closed_resp.status_code == 200
    closed_payload = closed_resp.get_json()

    routes = {
        "/api/csrf-token": {"body": {"csrf_token": "csrf-fixture-token"}},
        "/api/state": {"body": open_payload},
    }
    steps = [
        {"op": "poll", "label": "closed", "route": {"/api/state": {"body": closed_payload}}},
        {"op": "poll", "label": "reopened", "route": {"/api/state": {"body": open_payload}}},
    ]
    polled = run_page(html, tmp_path, routes=routes, steps=steps)
    refused = run_page(
        html, tmp_path, routes={"/api/csrf-token": routes["/api/csrf-token"]}, steps=[]
    )
    world = {
        "ids": ids,
        "html": html,
        "open_payload": open_payload,
        "closed_payload": closed_payload,
        "polled": polled,
        "refused": refused,
    }
    _CACHE["world"] = world
    return world


@pytest.fixture
def world(monkeypatch, tmp_path):
    return _page_world(monkeypatch, tmp_path)


def _mags() -> dict:
    if "mags" not in _CACHE:
        values = sorted({abs(g) for g in _GUARD_ALPHA_FAMILY})
        _CACHE["mags"] = dict(zip(values, js_to_fixed(values, 1), strict=True))
    return _CACHE["mags"]


def _expected_verdict(ga: float) -> str:
    lead = "Good call · saved +" if ga > 0 else "Early exit · gave up "
    return f"{lead}{_mags()[abs(ga)]}%α at exit"


def _snapshots(world: dict) -> dict:
    polled = world["polled"]
    return {
        "first paint": polled["initial"],
        "open poll": polled["loaded"],
        "closed poll": polled["steps"][0]["obs"],
        "reopened poll": polled["steps"][1]["obs"],
    }


def _written(dial: dict) -> dict:
    """The part of a dial both writers own: arc offset and label.  The arc colour a
    refresh writes is the chart cycle's concern; only the server's first-paint colour
    reference is judged here."""
    return {"offset": dial["offset"], "text": dial["text"]}


def _expected_dial(mc) -> dict:
    return js_eval(
        "(function(){ const m = p; const pct = Math.max(0, Math.min(100, m)) / 100;"
        " return { offset: (94.25 * (1 - pct)).toFixed(2),"
        " text: (pct * 100).toFixed(0) + '%',"
        " token: m < 15 ? '--studio-warn' : (m > 80 ? '--studio-ink-dim' : '--studio-accent') }; })()",
        mc,
    )


# ---------------------------------------------------------------------------
# The harness itself ran the real client code to completion
# ---------------------------------------------------------------------------


def test_client_script_loads_and_handles_page_load_without_a_top_level_error(world):
    for run in (world["polled"], world["refused"]):
        top_level = [
            e
            for e in run["errors"]
            if e.startswith(("index.js:", "inline.js:", "DOMContentLoaded"))
        ]
        assert not top_level, f"client code failed at load: {top_level[:2]}"


def test_page_load_asks_for_state_so_the_first_paint_comparison_is_meaningful(world):
    urls = [r["url"] for r in world["polled"]["requests"]]
    assert "/api/state" in urls, "the page never requested /api/state at load"
    assert any(r["url"] == "/api/state" for r in world["refused"]["requests"])


# ---------------------------------------------------------------------------
# Verdict text: server first paint
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("guard_alpha", _GUARD_ALPHA_FAMILY, ids=lambda g: f"ga{g}")
def test_first_paint_verdict_follows_the_browsers_rounding_with_the_at_exit_qualifier(
    world, guard_alpha
):
    sid = world["ids"]["guard"][guard_alpha]
    verdict = world["polled"]["initial"]["cards"][sid]["verdict"]
    assert verdict is not None, "triggered card has no verdict element"
    assert verdict["hidden"] is False, "a triggered card's verdict must be shown"
    assert verdict["text"] == _expected_verdict(guard_alpha)


def test_server_guard_alpha_is_the_same_number_the_poll_carries(world):
    """The first paint and the poll read one source: the value the route injects for
    each triggered card equals the value the verdict was seeded from."""
    for ga, sid in world["ids"]["guard"].items():
        entry = world["open_payload"]["bot_state"][sid]
        assert entry["guard_alpha"] == pytest.approx(ga, abs=1e-9), (sid, ga, entry)


def test_every_triggered_card_marks_its_verdict_text_for_the_client_to_rewrite(world):
    for sid in list(world["ids"]["guard"].values()) + [world["ids"]["no_exit_row"]]:
        verdict = world["polled"]["initial"]["cards"][sid]["verdict"]
        assert verdict["hasVerdictTextEl"] is True, (
            "the verdict text needs its own element so the client rewrites the text "
            "and leaves the 'at exit' qualifier in place"
        )


# ---------------------------------------------------------------------------
# Verdict text: refresh equals first paint, through every poll shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("guard_alpha", _GUARD_ALPHA_FAMILY, ids=lambda g: f"ga{g}")
def test_refreshed_verdict_text_is_identical_to_first_paint_text(world, guard_alpha):
    sid = world["ids"]["guard"][guard_alpha]
    first = world["polled"]["initial"]["cards"][sid]["verdict"]["text"]
    after = world["polled"]["loaded"]["cards"][sid]["verdict"]
    assert after["hidden"] is False
    assert after["text"] == first == _expected_verdict(guard_alpha)


def test_verdict_text_never_changes_across_open_closed_and_open_polls(world):
    """The closed-market body carries no guard alpha; the verdict must survive it."""
    for entry in (
        world["closed_payload"].get("bot_state", world["closed_payload"].get("state", {})).values()
    ):
        if isinstance(entry, dict) and "name" in entry:
            assert "guard_alpha" not in entry, (
                "premise changed: the closed body now carries guard alpha, so this "
                "test no longer exercises the missing-value poll"
            )
    for ga, sid in world["ids"]["guard"].items():
        texts = {
            label: snap["cards"][sid]["verdict"]["text"]
            for label, snap in _snapshots(world).items()
        }
        assert set(texts.values()) == {_expected_verdict(ga)}, texts


def test_verdict_is_never_replaced_by_the_empty_glyph_by_any_poll(world):
    for sid in world["ids"]["guard"].values():
        for label, snap in _snapshots(world).items():
            assert _DASH not in snap["cards"][sid]["verdict"]["text"].replace("·", ""), (
                label,
                snap["cards"][sid]["verdict"],
            )


def test_failed_state_request_leaves_the_server_rendered_verdict_in_place(world):
    refused = world["refused"]
    for ga, sid in world["ids"]["guard"].items():
        assert refused["loaded"]["cards"][sid]["verdict"]["text"] == _expected_verdict(ga)
        assert refused["loaded"]["cards"][sid]["verdict"]["hidden"] is False


def test_failed_state_request_leaves_the_server_rendered_dials_in_place(world):
    refused = world["refused"]
    for sid, card in refused["initial"]["cards"].items():
        assert refused["loaded"]["cards"][sid]["dial"] == card["dial"], sid


# ---------------------------------------------------------------------------
# Triggered card with no exit row: an honest empty verdict on both paths
# ---------------------------------------------------------------------------


def test_triggered_card_without_an_exit_row_shows_the_empty_glyph_without_the_qualifier(world):
    sid = world["ids"]["no_exit_row"]
    for label, snap in _snapshots(world).items():
        card = snap["cards"][sid]
        assert card["verdict"]["hidden"] is False, label
        assert card["verdict"]["text"] == _DASH, (label, card["verdict"])
        assert card["atExit"]["hidden"] is True, f"{label}: 'at exit' shown beside no value"


# ---------------------------------------------------------------------------
# Untriggered cards keep their verdict hidden
# ---------------------------------------------------------------------------


def test_untriggered_cards_keep_the_verdict_hidden_on_every_path(world):
    untriggered = list(world["ids"]["dial"].values()) + list(world["ids"]["sentinel"].values())
    for label, snap in _snapshots(world).items():
        for sid in untriggered:
            assert snap["cards"][sid]["verdict"]["hidden"] is True, (label, sid)


# ---------------------------------------------------------------------------
# MC dial
# ---------------------------------------------------------------------------


def test_triggered_cards_show_the_dash_dial_with_an_empty_arc_on_every_path(world):
    triggered = list(world["ids"]["guard"].values()) + [world["ids"]["no_exit_row"]]
    for sid in triggered:
        assert world["polled"]["initial"]["cards"][sid]["dial"] == {
            "offset": "94.25",
            "stroke": "var(--studio-ink-faint)",
            "text": _DASH,
        }, sid
    for label, snap in _snapshots(world).items():
        for sid in triggered:
            assert _written(snap["cards"][sid]["dial"]) == {"offset": "94.25", "text": _DASH}, (
                label,
                sid,
            )


@pytest.mark.parametrize(
    "label,mc", [(lbl, v) for lbl, v in _DIAL_FAMILY if v is not None and v is not _ABSENT]
)
def test_first_paint_dial_follows_the_clients_formulas_and_token_thresholds(world, label, mc):
    sid = world["ids"]["dial"][label]
    expected = _expected_dial(mc)
    dial = world["polled"]["initial"]["cards"][sid]["dial"]
    assert dial == {
        "offset": expected["offset"],
        "stroke": f"var({expected['token']})",
        "text": expected["text"],
    }


@pytest.mark.parametrize(
    "label,mc", [(lbl, v) for lbl, v in _DIAL_FAMILY if v is not None and v is not _ABSENT]
)
def test_refreshed_dial_is_identical_to_first_paint_dial(world, label, mc):
    sid = world["ids"]["dial"][label]
    first = _written(world["polled"]["initial"]["cards"][sid]["dial"])
    for name, snap in _snapshots(world).items():
        assert _written(snap["cards"][sid]["dial"]) == first, (label, name)


@pytest.mark.parametrize("label", ["null", "missing"])
def test_unknown_mc_reading_shows_the_dash_not_zero_percent_on_every_path(world, label):
    sid = world["ids"]["dial"][label]
    assert world["polled"]["initial"]["cards"][sid]["dial"] == {
        "offset": "94.25",
        "stroke": "var(--studio-ink-faint)",
        "text": _DASH,
    }
    for name, snap in _snapshots(world).items():
        assert _written(snap["cards"][sid]["dial"]) == {"offset": "94.25", "text": _DASH}, (
            label,
            name,
        )


def test_a_genuine_zero_reading_shows_zero_percent_not_the_dash(world):
    sid = world["ids"]["dial"]["zero"]
    for name, snap in _snapshots(world).items():
        dial = snap["cards"][sid]["dial"]
        assert dial["text"] == "0%", (name, dial)
        assert dial["offset"] == "94.25", (name, dial)
    assert world["polled"]["initial"]["cards"][sid]["dial"]["stroke"] == "var(--studio-warn)"


@pytest.mark.parametrize("label", [lbl for lbl, _ in _SENTINEL_FAMILY])
def test_engine_sentinel_mc_reading_shows_the_dash_on_first_paint(world, label):
    """The engine writes +-999 for 'not set'; on first paint it is no reading."""
    sid = world["ids"]["sentinel"][label]
    assert world["polled"]["initial"]["cards"][sid]["dial"] == {
        "offset": "94.25",
        "stroke": "var(--studio-ink-faint)",
        "text": _DASH,
    }


# ---------------------------------------------------------------------------
# Route robustness: the producers behind the first paint fail soft
# ---------------------------------------------------------------------------


def _single_triggered_page(monkeypatch, tmp_path) -> str:
    _network_refused(monkeypatch)
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", str(tmp_path / "absent.env"))
    sid = _invented_id(1)
    database.save_state({sid: _triggered_state(1)})
    _seed_exit_row(sid, at_return=1.25)
    return sid


@pytest.mark.parametrize("failure", ["raises", "non_numeric_value"])
def test_guard_alpha_read_failure_degrades_the_verdict_and_never_the_page(
    monkeypatch, tmp_path, failure
):
    sid = _single_triggered_page(monkeypatch, tmp_path)

    def broken(_ids=None):
        if failure == "raises":
            raise RuntimeError("exit_triggers unreadable")
        return {sid: "not-a-number"}

    monkeypatch.setattr(database, "get_guard_alpha_by_symphony", broken)
    resp = _client().get("/")
    assert resp.status_code == 200, "a failed guard-alpha read must not fail the page"
    run = run_page(resp.data.decode("utf-8"), tmp_path, routes={}, steps=[])
    card = run["initial"]["cards"][sid]
    assert card["verdict"]["text"] == _DASH
    assert card["atExit"]["hidden"] is True


def test_first_paint_reads_the_real_exit_row_without_any_state_poll(monkeypatch, tmp_path):
    """Entry-point check: the verdict is on the page `GET /` returns, before the page
    script has asked for anything."""
    sid = _single_triggered_page(monkeypatch, tmp_path)
    resp = _client().get("/")
    assert resp.status_code == 200
    run = run_page(resp.data.decode("utf-8"), tmp_path, routes={}, steps=[])
    assert run["requests"] == [] or all(r["url"] != "/" for r in run["requests"])
    assert run["initial"]["cards"][sid]["verdict"]["text"] == _expected_verdict_for(1.25 - 1.0)


def _expected_verdict_for(ga: float) -> str:
    mag = js_to_fixed([abs(ga)], 1)[0]
    lead = "Good call · saved +" if ga > 0 else "Early exit · gave up "
    return f"{lead}{mag}%α at exit"
