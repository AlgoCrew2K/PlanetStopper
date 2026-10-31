"""Performance page: window buttons, risk table signs, and the preconditions and exit-turnover panels.

Every test loads the real `/performance` markup from the Flask client, runs the real
`static/performance.js` over it under node, and answers the page's fetches with the body
the real route returned: `/api/performance` and `/api/performance/symphonies` over the
captured production shadow rows and bot state, `/api/guard-alpha-preconditions` through the
route's own sample accessors, and `/api/exit-turnover` over the captured exit triggers
(tests/fixtures/prod_droplet, see tests/fixtures/ux_data_truth/PROVENANCE.md). Expected text
is derived from those bodies; the browser's own rounding rule is `analytics.js_to_fixed`.
"""

from __future__ import annotations

import functools
import math
import os
import re
import sqlite3
from datetime import timedelta

import pytest

import analytics
import app as app_module
import database
from tests.app.test_calendar_window_routes import _freeze, _seed_flat_history, _weekdays
from tests.ui.test_static_js_page_harness import (
    captured,
    captured_json,
    page_html,
    route,
    run_page,
    seed_shadow_rows,
)

DASH = ("—", "--")
EXIT_TRIGGER_INSERT = (
    "INSERT INTO exit_triggers (ts_utc, ts_et, symphony_id, account_id, triggered_reason, "
    "at_return, cycle_id) VALUES (?, ?, ?, ?, ?, ?, ?)"
)

_READ = """
function cellInfo(tr) {
  const td = tr.children[0];
  const inner = td.querySelector('[title]');
  return { text: td.textContent,
           title: td.getAttribute('title') || (inner && inner.getAttribute('title')) || tr.getAttribute('title') || null };
}
function panelRows(sel) {
  return H.$$(sel + ' tr').map(function (tr) {
    return { cells: tr.children.map(function (td) { return td.textContent; }), cls: tr.className, first: cellInfo(tr) };
  });
}
function noteState() {
  const notes = H.$$('[data-testid="guard-alpha-preconditions-note"]');
  return { count: notes.length, visible: notes.length > 0 && H.visible(notes[0]), text: notes.length ? notes[0].textContent : null };
}
"""


@pytest.fixture(autouse=True)
def _analytics_shadow_db_seam(_isolate_db, monkeypatch):
    monkeypatch.setattr(analytics, "DB_FILE", os.environ["DB_PATH"])


@pytest.fixture(autouse=True)
def _stub_database_load_state_for_advisor_routes():
    """Replace tests/ui/conftest.py's load_state stub with nothing: these tests exercise
    routes over the captured bot_state they write with database.save_state, so the real
    accessor stays in place.

    Overriding the fixture leaves nothing to restore. Re-patching the attribute from a
    monkeypatch fixture instead is torn down after the conftest patch and puts the stub
    back on the module for every later test in the same worker.
    """


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _seed_captured_state_and_shadow_rows() -> dict:
    state = captured_json("bot_state_positions.json")
    seed_shadow_rows(os.environ["DB_PATH"], captured_json("shadow_last_rows.json"))
    database.save_state(state)
    return state


def _seed_captured_exit_triggers() -> None:
    conn = sqlite3.connect(os.environ["DB_PATH"], timeout=10.0)
    try:
        conn.executemany(
            EXIT_TRIGGER_INSERT,
            [
                (
                    r["ts_utc"],
                    r["ts_et"],
                    r["symphony_id"],
                    r["account_id"],
                    r["triggered_reason"],
                    r["at_return"],
                    r["cycle_id"],
                )
                for r in captured_json("exit_triggers.json")
            ],
        )
        conn.commit()
    finally:
        conn.close()


@functools.lru_cache(maxsize=1)
def _captured_metrics() -> dict:
    """{symphony_id: (live_metrics, shadow_metrics)} over the captured rows, for choosing a
    symphony by the property a scenario needs. Selection only: the page is always fed the
    route's own answer for the chosen symphony."""
    by_symphony: dict[str, list[dict]] = {}
    for row in captured_json("shadow_last_rows.json"):
        by_symphony.setdefault(row["symphony_id"], []).append(row)
    out = {}
    for sid, rows in by_symphony.items():
        rows.sort(key=lambda r: r["trading_day"])
        out[sid] = (
            analytics.compute_quantstats_metrics([r["current_return"] for r in rows]),
            analytics.compute_quantstats_metrics([r["shadow_return"] for r in rows]),
        )
    return out


def _symphony_where(predicate) -> str:
    for sid, (live, shadow) in _captured_metrics().items():
        if predicate(live["max_drawdown"], shadow["max_drawdown"]):
            return sid
    pytest.fail("the captured rows hold no symphony with the drawdown relation this scenario needs")


def _bot_shallower(live_mdd, shadow_mdd):
    return abs(live_mdd) > abs(shadow_mdd)


def _bot_deeper(live_mdd, shadow_mdd):
    return abs(live_mdd) < abs(shadow_mdd)


def _equal(live_mdd, shadow_mdd):
    return live_mdd == shadow_mdd


def _symphony_payload(client, sid: str, days: int = 125) -> dict:
    resp = client.get(f"/api/performance?scope=symphony&symphony_id={sid}&days={days}")
    assert resp.status_code == 200
    return resp.get_json()


def _perf_page(client, routes, scenario):
    return run_page(
        page_html(client, "/performance"),
        ["request_guard.js", "performance.js"],
        routes,
        _READ + scenario,
    )


ANY_PERFORMANCE = r"^/api/performance\?"


def _routes(client, payload, *, with_panels=True):
    routes = [route(ANY_PERFORMANCE, payload, exact=False)]
    if with_panels:
        routes += [
            captured(client, "/api/performance/symphonies"),
            captured(client, "/api/guard-alpha-preconditions"),
        ]
    return routes


def _metric_rows(page_result) -> dict[str, list[str]]:
    return {r[0]: r for r in page_result["metrics"]}


_METRICS_SCENARIO = """
await H.boot();
return { metrics: H.rows('#metrics-tbody'), errors: H.errors, unrouted: H.unrouted };
"""


def _render_metrics(client, payload):
    return _perf_page(client, _routes(client, payload, with_panels=False), _METRICS_SCENARIO)


# ---------------------------------------------------------------------------
# Window buttons
# ---------------------------------------------------------------------------

TOKENS = ["30d", "60d", "90d", "125d", "ytd", "1y", "5y"]


def test_the_page_opens_on_60d_with_calendar_tokens_and_asks_for_it(client):
    _seed_captured_state_and_shadow_rows()
    payload = _symphony_payload(client, next(iter(captured_json("bot_state_positions.json"))))

    result = _perf_page(
        client,
        _routes(client, payload, with_panels=False),
        """
        await H.boot();
        return { values: H.$$('#days-picker button').map(function (b) { return b.getAttribute('data-value'); }),
                 active: H.$$('#days-picker button.active').map(function (b) { return b.getAttribute('data-value'); }),
                 first: H.fetchLog.find(function (u) { return u.indexOf('/api/performance?') === 0; }) };
        """,
    )

    assert result["values"] == TOKENS
    assert result["active"] == ["60d"]
    assert "days=60d" in result["first"]


def _year_page_world(client, monkeypatch):
    today = _freeze(monkeypatch, "2026-08-20T12:00:00")
    all_days = _weekdays(today - timedelta(days=730), today)
    _seed_flat_history(all_days)
    return today


def _token_payload(client, token):
    resp = client.get(f"/api/performance?scope=aggregate&days={token}")
    assert resp.status_code == 200, f"/api/performance?days={token} answered {resp.status_code}"
    return resp.get_json()


def _clicks(token_testid: str, times: int) -> str:
    return f"for (let i = 0; i < {times}; i++) {{ await H.click('[data-testid=\"{token_testid}\"]'); }}"


def test_clicking_one_year_twice_sends_one_request_per_click_and_shows_the_one_year_result(
    client, monkeypatch
):
    _year_page_world(client, monkeypatch)
    payload = _token_payload(client, "1y")

    result = _perf_page(
        client,
        _routes(client, payload, with_panels=False),
        """
        await H.boot();
        const before = H.fetchLog.filter(function (u) { return u.indexOf('/api/performance?') === 0; }).length;
        %s
        const calls = H.fetchLog.filter(function (u) { return u.indexOf('/api/performance?') === 0; }).slice(before);
        return { calls: calls, active: H.$$('#days-picker button.active').map(function (b) { return b.textContent.trim(); }),
                 caption: H.text('#obs-caption') };
        """
        % _clicks("window-btn-1y", 2),
    )

    assert len(result["calls"]) == 2
    assert all("days=1y" in c for c in result["calls"])
    assert result["active"] == ["1Y"]
    assert "1Y" in result["caption"] and payload["date_range"]["start"] in result["caption"]


def test_a_failing_request_after_a_one_year_click_keeps_the_button_active_and_the_page_as_it_was(
    client, monkeypatch
):
    _year_page_world(client, monkeypatch)
    payload = _token_payload(client, "1y")
    routes = [
        {
            "pattern": ANY_PERFORMANCE,
            "responses": [{"status": 200, "json": payload}, {"status": 500, "json": None}],
        }
    ]

    result = _perf_page(
        client,
        routes,
        """
        await H.boot();
        const before = H.text('#obs-caption');
        await H.click('[data-testid="window-btn-1y"]');
        return { before: before, after: H.text('#obs-caption'),
                 active: H.$$('#days-picker button.active').map(function (b) { return b.textContent.trim(); }),
                 last: H.fetchLog[H.fetchLog.length - 1], errors: H.errors };
        """,
    )

    assert "days=1y" in result["last"]
    assert result["active"] == ["1Y"]
    assert result["after"] == result["before"]
    assert any("performance refresh failed" in e for e in result["errors"])


def test_five_years_over_a_short_history_states_the_span_actually_held(client, monkeypatch):
    today = _freeze(monkeypatch, "2026-08-20T12:00:00")
    _seed_flat_history(_weekdays(today - timedelta(days=200), today))
    payload = _token_payload(client, "5y")

    result = _perf_page(
        client,
        _routes(client, payload, with_panels=False),
        """
        await H.boot();
        await H.click('[data-testid="window-btn-5y"]');
        return { caption: H.text('#obs-caption') };
        """,
    )

    caption = result["caption"]
    assert payload["date_range"]["start"] in caption, (
        f"the span actually held must be stated: {caption!r}"
    )
    assert "5Y" in caption
    assert str(payload["observation_count"]) in caption


def test_a_window_with_fewer_rows_than_the_minimum_shows_the_insufficient_history_banner(
    client, monkeypatch
):
    _freeze(monkeypatch, "2026-07-10T12:00:00")
    _seed_captured_state_and_shadow_rows()
    payload = _token_payload(client, "60d")
    assert payload["insufficient_history"] is True

    result = _perf_page(
        client,
        _routes(client, payload, with_panels=False),
        """
        await H.boot();
        return { shown: H.isVisible('#insufficient-banner'), caption: H.text('#obs-caption') };
        """,
    )

    assert result["shown"] is True
    assert str(payload["observation_count"]) in result["caption"]


# ---------------------------------------------------------------------------
# Risk table: one sign per delta, Max DD as a magnitude, no rows without a producer
# ---------------------------------------------------------------------------


def _reduction_pp(payload) -> float:
    return (
        abs(payload["live_metrics"]["max_drawdown"])
        - abs(payload["shadow_metrics"]["max_drawdown"])
    ) * 100


def _pp(value: float) -> str:
    return analytics.js_to_fixed(value, 2) + "pp"


def test_a_positive_reduction_reads_up_arrow_one_plus_and_the_magnitude(client):
    _seed_captured_state_and_shadow_rows()
    payload = _symphony_payload(client, _symphony_where(_bot_shallower))
    reduction = _reduction_pp(payload)
    assert reduction > 0

    cell = _metric_rows(_render_metrics(client, payload))["Max DD reduction"][3]

    assert cell == f"↑ +{_pp(reduction)}"
    assert cell.count("+") == 1


def test_a_negative_reduction_reads_down_arrow_one_minus_and_no_plus(client):
    _seed_captured_state_and_shadow_rows()
    payload = _symphony_payload(client, _symphony_where(_bot_deeper))
    reduction = _reduction_pp(payload)
    assert reduction < 0

    cell = _metric_rows(_render_metrics(client, payload))["Max DD reduction"][3]

    assert cell == f"↓ {_pp(reduction)}"
    assert cell.count("-") == 1 and "+" not in cell


def test_a_zero_delta_has_no_arrow_and_at_most_one_sign_on_both_drawdown_rows(client):
    _seed_captured_state_and_shadow_rows()
    payload = _symphony_payload(client, _symphony_where(_equal))
    assert payload["live_metrics"]["max_drawdown"] == payload["shadow_metrics"]["max_drawdown"]

    rows = _metric_rows(_render_metrics(client, payload))

    for label in ("Max DD", "Max DD reduction"):
        cell = rows[label][3]
        assert "↑" not in cell and "↓" not in cell, (
            f"{label}: a zero delta carries no arrow: {cell!r}"
        )
        assert cell.count("+") + cell.count("-") <= 1, f"{label}: {cell!r}"


def test_no_delta_cell_in_the_table_carries_two_sign_characters(client):
    _seed_captured_state_and_shadow_rows()
    for pick in (_bot_shallower, _bot_deeper):
        payload = _symphony_payload(client, _symphony_where(pick))
        for label, cells in _metric_rows(_render_metrics(client, payload)).items():
            assert cells[3].count("+") + cells[3].count("-") <= 1, f"{label}: {cells[3]!r}"


def test_max_dd_row_shows_each_leg_as_a_positive_magnitude_with_two_decimals(client):
    _seed_captured_state_and_shadow_rows()
    payload = _symphony_payload(client, _symphony_where(_bot_shallower))

    row = _metric_rows(_render_metrics(client, payload))["Max DD"]

    for cell, leg in ((row[1], "live_metrics"), (row[2], "shadow_metrics")):
        assert cell == analytics.js_to_fixed(abs(payload[leg]["max_drawdown"]) * 100, 2) + "%"
        assert "-" not in cell


def test_the_server_markup_labels_the_drawdown_row_max_dd(client):
    html = page_html(client, "/performance")
    result = run_page(
        html,
        [],
        [],
        "return H.rows('#metrics-tbody').map(function (r) { return r[0]; });",
    )
    assert "Max DD" in result
    assert "Max drawdown" not in result


def test_a_null_leg_reads_a_dash_and_the_other_leg_still_renders(client):
    _seed_captured_state_and_shadow_rows()
    payload = _symphony_payload(client, _symphony_where(_bot_shallower))
    payload["live_metrics"]["max_drawdown"] = None

    row = _metric_rows(_render_metrics(client, payload))["Max DD"]

    assert row[1] in DASH
    assert (
        row[2]
        == analytics.js_to_fixed(abs(payload["shadow_metrics"]["max_drawdown"]) * 100, 2) + "%"
    )


def test_a_zero_drawdown_reads_zero_percent(client):
    _seed_captured_state_and_shadow_rows()

    def leg_mdd(sid, key):
        return analytics.compute_quantstats_metrics([r[key] for r in _tail(sid, 2)])["max_drawdown"]

    sid = next(
        s
        for s in _captured_metrics()
        if leg_mdd(s, "current_return") == 0 or leg_mdd(s, "shadow_return") == 0
    )
    payload = _symphony_payload(client, sid, days=2)
    zero_leg = "live_metrics" if payload["live_metrics"]["max_drawdown"] == 0 else "shadow_metrics"
    assert payload[zero_leg]["max_drawdown"] == 0

    row = _metric_rows(_render_metrics(client, payload))["Max DD"]

    assert row[1 if zero_leg == "live_metrics" else 2] == "0.00%"


def _tail(sid: str, n: int) -> list[dict]:
    rows = sorted(
        (r for r in captured_json("shadow_last_rows.json") if r["symphony_id"] == sid),
        key=lambda r: r["trading_day"],
    )
    return rows[-n:]


def test_a_metric_with_no_value_on_either_leg_renders_its_row_with_dashes(client):
    _seed_captured_state_and_shadow_rows()
    sid = next(iter(captured_json("bot_state_positions.json")))
    payload = _symphony_payload(client, sid, days=1)
    assert (
        payload["live_metrics"]["sortino"] is None and payload["shadow_metrics"]["sortino"] is None
    )

    row = _metric_rows(_render_metrics(client, payload))["Sortino"]

    assert row[1] in DASH and row[2] in DASH


def test_no_capture_ratio_row_exists_in_the_server_markup_or_the_rendered_table(client):
    _seed_captured_state_and_shadow_rows()
    payload = _symphony_payload(client, _symphony_where(_bot_shallower))
    static = run_page(
        page_html(client, "/performance"),
        [],
        [],
        "return { labels: H.rows('#metrics-tbody').map(function (r) { return r[0]; }), unavail: H.$$('[data-unavail]').length };",
    )

    rendered = [r[0] for r in _render_metrics(client, payload)["metrics"]]

    for labels in (static["labels"], rendered):
        assert not any("capture vs SPY" in label for label in labels), labels
    assert static["unavail"] == 0


def _ref_fmt(value, kind) -> str:
    """The existing cell text for the kinds this change leaves alone."""
    if (
        value is None
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or abs(value) > 1000
    ):
        return "—"
    if kind in ("pct_frac", "frac"):
        return analytics.js_to_fixed(value * 100, 2) + "%"
    return analytics.js_to_fixed(value, 3)


def test_every_other_metric_cell_and_headline_keeps_its_existing_text(client):
    _seed_captured_state_and_shadow_rows()
    payload = _symphony_payload(client, _symphony_where(_bot_shallower))
    live, shadow = payload["live_metrics"], payload["shadow_metrics"]

    result = _perf_page(
        client,
        _routes(client, payload, with_panels=False),
        """
        await H.boot();
        return { metrics: H.rows('#metrics-tbody'),
                 guard: H.text('#guard-alpha-value'), sharpe: H.text('#sharpe-delta-value'),
                 sortino: H.text('#sortino-delta-value'), mdd: H.text('#mdd-reduction-value') };
        """,
    )

    kinds = {
        "Sharpe": ("sharpe", "num"),
        "Sortino": ("sortino", "num"),
        "Calmar": ("calmar", "num"),
        "Annualized volatility": ("volatility", "pct_frac"),
        "Total return": ("total_return", "pct_frac"),
        "Annualized return (CAGR)": ("annualized_return", "pct_frac"),
        "Win rate": ("win_rate", "frac"),
    }
    rows = _metric_rows(result)
    for label, (key, kind) in kinds.items():
        assert rows[label][1] == _ref_fmt(live[key], kind), label
        assert rows[label][2] == _ref_fmt(shadow[key], kind), label
    guard = (shadow["total_return"] - live["total_return"]) * 100
    assert result["guard"] == f"{'+' if guard >= 0 else ''}{analytics.js_to_fixed(guard, 2)}%"
    assert result["mdd"] == f"+{_pp(_reduction_pp(payload))}"


# ---------------------------------------------------------------------------
# Stop-justification preconditions panel
# ---------------------------------------------------------------------------


def _patch_samples(monkeypatch, *, replay: dict[str, list[float]], shadow: dict[str, list[float]]):
    import autotuner

    monkeypatch.setattr(
        autotuner, "build_if_held_replay_series", lambda sid, *a, **k: replay.get(sid)
    )
    monkeypatch.setattr(
        analytics, "get_shadow_current_return_daily_series", lambda sid, *a, **k: shadow.get(sid)
    )


def _usable_series(sid: str) -> list[float]:
    import guard_preconditions as gp

    base = [r["current_return"] for r in _tail(sid, 13)]
    need = gp.N_MIN_OBS + 10
    return (base * (need // len(base) + 1))[:need]


def _precondition_world(client, monkeypatch, *, replay_for: list[str], shadow_for: list[str]):
    state = _seed_captured_state_and_shadow_rows()
    ids = list(state)
    _patch_samples(
        monkeypatch,
        replay={s: _usable_series(s) for s in replay_for},
        shadow={s: _usable_series(s) for s in shadow_for},
    )
    body = client.get("/api/guard-alpha-preconditions").get_json()
    return ids, body


def _panel_scenario(extra: str = "") -> str:
    return (
        """
        await H.boot();
        %s
        return { rows: panelRows('#guard-alpha-preconditions-tbody'), note: noteState(),
                 empty: H.isVisible('#guard-alpha-preconditions-empty-state') };
        """
        % extra
    )


def test_all_replay_samples_cold_and_one_shadow_usable_gives_one_panel_note_and_no_row_message(
    client, monkeypatch
):
    _, body = _precondition_world(
        client,
        monkeypatch,
        replay_for=[],
        shadow_for=[next(iter(captured_json("bot_state_positions.json")))],
    )
    usable = next(
        s for s, e in body["symphonies"].items() if e["shadow"]["verdict"] != "INSUFFICIENT_DATA"
    )
    assert all(
        e["replay"]["verdict"] == "INSUFFICIENT_DATA" and e["replay"]["n_obs"] == 0
        for e in body["symphonies"].values()
    )

    result = _perf_page(
        client, _routes(client, {"live_metrics": {}, "shadow_metrics": {}}), _panel_scenario()
    )

    note = result["note"]
    assert note["count"] == 1 and note["visible"]
    lowered = note["text"].lower()
    assert "replay" in lowered and "unavailable" in lowered and "autotune" in lowered
    assert not any("precond-row-unavailable" in r["cls"] for r in result["rows"])
    assert not any("replay sample unavailable" in c for r in result["rows"] for c in r["cells"])
    shadow_rows = [r for r in result["rows"] if r["cells"][1] == "Shadow"]
    assert any(
        r["cells"][-1] != "INSUFFICIENT DATA" and r["cells"][2][0] in "-0123456789"
        for r in shadow_rows
    ), f"the usable shadow sample of {usable} must render as a data row"


def test_mixed_replay_samples_render_the_usable_row_and_the_note_names_how_many_lack_one(
    client, monkeypatch
):
    state = captured_json("bot_state_positions.json")
    usable_id = next(iter(state))
    ids, body = _precondition_world(client, monkeypatch, replay_for=[usable_id], shadow_for=[])
    assert body["symphonies"][usable_id]["replay"]["verdict"] != "INSUFFICIENT_DATA"
    cold = [
        s for s, e in body["symphonies"].items() if e["replay"]["verdict"] == "INSUFFICIENT_DATA"
    ]

    result = _perf_page(
        client, _routes(client, {"live_metrics": {}, "shadow_metrics": {}}), _panel_scenario()
    )

    replay_rows = [r for r in result["rows"] if r["cells"][1] == "Replay"]
    assert any(r["cells"][2] not in DASH and r["cells"][2][0] in "-0123456789" for r in replay_rows)
    assert result["note"]["visible"]
    assert re.search(rf"\b{len(cold)}\b", result["note"]["text"]), result["note"]["text"]
    assert not any("replay sample unavailable" in c for r in result["rows"] for c in r["cells"])


def test_when_no_symphony_has_a_usable_sample_only_the_panel_empty_state_shows(client, monkeypatch):
    _precondition_world(client, monkeypatch, replay_for=[], shadow_for=[])
    state_ids = list(captured_json("bot_state_positions.json"))
    assert state_ids

    result = _perf_page(
        client, _routes(client, {"live_metrics": {}, "shadow_metrics": {}}), _panel_scenario()
    )

    assert result["empty"] is True
    assert result["rows"] == []
    assert result["note"]["count"] == 1 and result["note"]["visible"] is False


def test_three_poll_cycles_leave_one_note_and_one_row_per_metric(client, monkeypatch):
    state = captured_json("bot_state_positions.json")
    _precondition_world(client, monkeypatch, replay_for=[], shadow_for=[next(iter(state))])
    payload = _symphony_payload(client, _symphony_where(_bot_shallower))

    result = _perf_page(
        client,
        _routes(client, payload),
        """
        await H.boot();
        const snap = function () { return { note: noteState(), labels: H.rows('#metrics-tbody').map(function (r) { return r[0]; }),
                                            precond: H.$$('#guard-alpha-preconditions-tbody tr').length }; };
        const first = snap();
        const later = [];
        for (let i = 0; i < 3; i++) { await H.tick(); later.push(snap()); }
        return { first: first, later: later, intervals: H.intervalCount() };
        """,
    )

    assert result["intervals"] == 1, "the page keeps its single 60 second poll"
    assert result["first"]["note"]["count"] == 1 and result["first"]["note"]["visible"]
    for snap in result["later"]:
        assert snap["note"] == result["first"]["note"]
        assert snap["labels"] == result["first"]["labels"]
        assert len(set(snap["labels"])) == len(snap["labels"])
        assert snap["precond"] == result["first"]["precond"]


def test_a_failing_preconditions_poll_keeps_the_last_rendered_panel(client, monkeypatch):
    state = captured_json("bot_state_positions.json")
    _precondition_world(client, monkeypatch, replay_for=[], shadow_for=[next(iter(state))])
    body = client.get("/api/guard-alpha-preconditions").get_json()
    routes = [
        route(ANY_PERFORMANCE, {"live_metrics": {}, "shadow_metrics": {}}, exact=False),
        captured(client, "/api/performance/symphonies"),
        {
            "pattern": "^/api/guard-alpha-preconditions$",
            "responses": [{"status": 200, "json": body}, {"status": 500, "json": None}],
        },
    ]

    result = _perf_page(
        client,
        routes,
        """
        await H.boot();
        const before = { rows: panelRows('#guard-alpha-preconditions-tbody'), note: noteState() };
        await H.tick();
        return { before: before, after: { rows: panelRows('#guard-alpha-preconditions-tbody'), note: noteState() },
                 calls: H.fetchLog.filter(function (u) { return u === '/api/guard-alpha-preconditions'; }).length };
        """,
    )

    assert result["calls"] == 2
    assert result["before"]["note"]["visible"] and result["before"]["rows"]
    assert result["after"] == result["before"]


# ---------------------------------------------------------------------------
# Display names in the preconditions and exit-turnover panels
# ---------------------------------------------------------------------------


def _names_by_id(client) -> dict[str, str]:
    body = client.get("/api/performance/symphonies").get_json()
    return {s["id"]: s["name"] for s in body["symphonies"]}


def test_precondition_rows_read_the_display_name_with_the_identifier_only_in_a_title(
    client, monkeypatch
):
    state = captured_json("bot_state_positions.json")
    _precondition_world(client, monkeypatch, replay_for=[], shadow_for=[next(iter(state))])
    names = _names_by_id(client)

    result = _perf_page(
        client, _routes(client, {"live_metrics": {}, "shadow_metrics": {}}), _panel_scenario()
    )

    assert result["rows"]
    for row in result["rows"]:
        assert row["first"]["title"] in names, (
            f"the identifier belongs in a title: {row['first']!r}"
        )
        assert row["first"]["text"] == names[row["first"]["title"]]


def test_preconditions_are_requested_only_after_the_symphony_names_have_arrived(
    client, monkeypatch
):
    state = captured_json("bot_state_positions.json")
    _precondition_world(client, monkeypatch, replay_for=[], shadow_for=[next(iter(state))])
    names_body = client.get("/api/performance/symphonies").get_json()
    routes = [
        route(ANY_PERFORMANCE, {"live_metrics": {}, "shadow_metrics": {}}, exact=False),
        {
            "pattern": "^/api/performance/symphonies$",
            "responses": [
                {"status": 200, "json": names_body, "hold": "names"},
                {"status": 200, "json": names_body},
            ],
        },
        captured(client, "/api/guard-alpha-preconditions"),
    ]

    result = _perf_page(
        client,
        routes,
        """
        await H.boot();
        const requestedEarly = H.fetchLog.indexOf('/api/guard-alpha-preconditions') >= 0;
        await H.release('names');
        return { requestedEarly: requestedEarly, requestedAfter: H.fetchLog.indexOf('/api/guard-alpha-preconditions') >= 0 };
        """,
    )

    assert result["requestedEarly"] is False
    assert result["requestedAfter"] is True


def test_two_symphonies_sharing_a_long_name_prefix_each_keep_their_own_full_name(
    client, monkeypatch
):
    state = captured_json("bot_state_positions.json")
    names = {sid: e["name"] for sid, e in state.items()}

    def shared(a: str, b: str) -> int:
        n = 0
        while n < min(len(a), len(b)) and a[n] == b[n]:
            n += 1
        return n

    pair = max(
        ((a, b) for a in names for b in names if a < b),
        key=lambda p: shared(names[p[0]], names[p[1]]),
    )
    assert shared(names[pair[0]], names[pair[1]]) >= 8, (
        "fixture integrity: two names share a long prefix"
    )
    _precondition_world(client, monkeypatch, replay_for=[], shadow_for=list(pair))

    result = _perf_page(
        client, _routes(client, {"live_metrics": {}, "shadow_metrics": {}}), _panel_scenario()
    )

    texts = {r["first"]["text"] for r in result["rows"]}
    assert names[pair[0]] in texts and names[pair[1]] in texts


def test_precondition_rows_fall_back_to_the_identifier_when_the_symphony_list_fails(
    client, monkeypatch
):
    state = captured_json("bot_state_positions.json")
    usable = next(iter(state))
    _precondition_world(client, monkeypatch, replay_for=[], shadow_for=[usable])
    routes = [
        route(ANY_PERFORMANCE, {"live_metrics": {}, "shadow_metrics": {}}, exact=False),
        {"pattern": "^/api/performance/symphonies$", "reject": True},
        captured(client, "/api/guard-alpha-preconditions"),
    ]

    result = _perf_page(client, routes, _panel_scenario())

    assert result["empty"] is False
    assert result["rows"], "the panel still renders"
    assert {r["first"]["text"] for r in result["rows"]} == set(state), (
        "every symphony the route returned keeps a row, labelled by the identifier the panel already has"
    )


def _turnover_routes(client):
    names_body = client.get("/api/performance/symphonies").get_json()
    routes = [
        route(ANY_PERFORMANCE, {"live_metrics": {}, "shadow_metrics": {}}, exact=False),
        captured(client, "/api/performance/symphonies"),
        captured(client, "/api/guard-alpha-preconditions"),
    ]
    routes += [
        captured(client, f"/api/exit-turnover?symphony_id={s['id']}")
        for s in names_body["symphonies"]
    ]
    return routes


def test_exit_turnover_rows_read_the_display_name_with_the_identifier_in_a_title(client):
    _seed_captured_state_and_shadow_rows()
    _seed_captured_exit_triggers()
    names = _names_by_id(client)

    result = _perf_page(
        client,
        _turnover_routes(client),
        "await H.boot(); return { rows: panelRows('#exit-turnover-tbody') };",
    )

    assert len(result["rows"]) == len(names)
    for row in result["rows"]:
        assert row["first"]["title"] in names
        assert row["first"]["text"] == names[row["first"]["title"]]


def test_friction_drag_shows_exactly_two_decimals_followed_by_a_percent_sign(client):
    _seed_captured_state_and_shadow_rows()
    _seed_captured_exit_triggers()
    names = _names_by_id(client)
    drags = {
        sid: client.get(f"/api/exit-turnover?symphony_id={sid}").get_json()[
            "est_annual_friction_drag_pct"
        ]
        for sid in names
    }

    result = _perf_page(
        client,
        _turnover_routes(client),
        "await H.boot(); return { rows: panelRows('#exit-turnover-tbody') };",
    )

    for row in result["rows"]:
        drag = drags[row["first"]["title"]]
        assert row["cells"][-1] == analytics.js_to_fixed(drag, 2) + "%"
