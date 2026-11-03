"""History page: dollar result wording, default window and day drilldown, run under node.

Each test loads the real `/history` markup from the Flask client, runs the real
`static/history.js` over it, and answers the page's fetches with the body the real
`/api/history/<window>` route returned over captured production post-mortem days
(tests/fixtures/prod_droplet/post_mortems, stamped as recorded in
tests/fixtures/ux_data_truth/PROVENANCE.md). Expected figures are derived from that body or
from the producer (`analytics.format_dollar_saved`), never typed in.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest

import analytics
import app as app_module
from tests.ui._history_answers import day_label
from tests.ui.test_static_js_page_harness import (
    page_html,
    pm_capture,
    pm_dir_factory,  # noqa: F401  (fixture)
    route,
    run_page,
    stamped,
)

TILE = '[data-testid="stat-total-saved"]'
VALUE = "#val-total-saved"


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _ago(days: int) -> str:
    return (datetime.now(UTC).date() - timedelta(days=days)).isoformat()


def _stamped_day(capture_day: str) -> list[dict]:
    return [stamped(t) for t in pm_capture(capture_day)["triggers"]]


def _history(client, days) -> dict:
    resp = client.get(f"/api/history/{days}")
    assert resp.status_code == 200, f"/api/history/{days} answered {resp.status_code}"
    return resp.get_json()


def _page(client) -> str:
    return page_html(client, "/history")


def _pick(window: str) -> str:
    return f'#window-picker button[data-window="{window}"]'


def _run(client, routes, scenario):
    return run_page(_page(client), ["request_guard.js", "history.js"], routes, scenario)


# ---------------------------------------------------------------------------
# The dollar tile says saved or lost, once, under the label "Dollar result"
# ---------------------------------------------------------------------------

_TILE_SCENARIO = """
await H.boot();
await H.click(%r);
return { value: H.text(%r), label: H.text(%r + ' .hp-fig-label'), tile: H.text(%r) };
"""


def _tile(client, payload, window="30"):
    return _run(
        client,
        [route(f"/api/history/{window}", payload)],
        _TILE_SCENARIO % (_pick(window), VALUE, TILE, TILE),
    )


def test_a_positive_window_reads_the_producers_text_under_dollar_result(client, pm_dir_factory):
    pm_dir_factory({_ago(5): _stamped_day("2026-06-22")})
    payload = _history(client, 30)
    assert payload["total_saved"] > 0, "fixture integrity: the chosen day must save money"

    tile = _tile(client, payload)

    assert tile["value"] == analytics.format_dollar_saved(payload["total_saved"])
    assert tile["label"].strip() == "Dollar result"


def test_a_negative_window_reads_magnitude_and_lost_with_no_minus_and_no_saved(
    client, pm_dir_factory
):
    pm_dir_factory({_ago(5): _stamped_day("2026-07-07")})
    payload = _history(client, 30)
    assert payload["total_saved"] < 0, "fixture integrity: the chosen day must lose money"

    tile = _tile(client, payload)

    assert tile["value"] == analytics.format_dollar_saved(payload["total_saved"])
    assert tile["value"].endswith(" lost")
    assert "-" not in tile["value"]
    assert "saved" not in tile["tile"].lower(), (
        f"the tile must not say saved over a loss: {tile['tile']!r}"
    )


def test_a_zero_total_with_a_trigger_reads_zero_saved(client, pm_dir_factory):
    zero_trigger = next(
        t for t in pm_capture("2026-06-23")["triggers"] if t["saved_dollars"] == 0.0
    )
    pm_dir_factory({_ago(5): [stamped(zero_trigger)]})
    payload = _history(client, 30)
    assert payload["total_saved"] == 0 and payload["trigger_count"] >= 1

    tile = _tile(client, payload)

    assert tile["value"] == "$0.00 saved"
    assert tile["label"].strip() == "Dollar result"


def test_a_missing_total_reads_a_dash_with_no_word_under_dollar_result(client, pm_dir_factory):
    pm_dir_factory({_ago(5): _stamped_day("2026-06-22")})
    payload = _history(client, 30)
    assert payload["trigger_count"] >= 1
    del payload["total_saved"]

    tile = _tile(client, payload)

    assert tile["value"] == "--"
    assert tile["label"].strip() == "Dollar result"
    assert "saved" not in tile["tile"].lower() and "lost" not in tile["tile"].lower()


def test_alpha_win_rate_and_reason_cards_render_from_the_payload(client, pm_dir_factory):
    """Behaviour the page already has and must keep (a guard, not a new behaviour)."""
    pm_dir_factory({_ago(5): _stamped_day("2026-07-07")})
    payload = _history(client, 30)

    result = _run(
        client,
        [route("/api/history/30", payload)],
        """
        await H.boot();
        await H.click(%r);
        return { alpha: H.text('#val-total-alpha'), win: H.text('#val-win-rate'),
                 cards: H.$$('[data-testid="reason-card"]').map(function (c) { return c.textContent; }) };
        """
        % _pick("30"),
    )

    # A sum of per-exit alpha is in points, never a percent.
    assert result["alpha"] == f"{payload['total_alpha']:+.2f} pts"
    assert result["win"] == f"{payload['win_rate']:.1f}%"
    assert len(result["cards"]) == len(payload["by_reason"])
    for reason, stats in payload["by_reason"].items():
        card = next(c for c in result["cards"] if reason in c)
        assert analytics.format_dollar_saved(stats["dollars"]) in card
        assert f"{stats['alpha']:+.2f} pts" in card


def test_an_empty_window_shows_the_empty_state_and_hides_the_tiles(client, pm_dir_factory):
    pm_dir_factory({})
    payload = _history(client, 30)
    assert payload["trigger_count"] == 0

    result = _run(
        client,
        [route("/api/history/30", payload)],
        """
        await H.boot();
        await H.click(%r);
        return { hero: H.isVisible('[data-testid="history-hero"]'),
                 empty: H.isVisible('#history-empty-state'), value: H.text(%r) };
        """
        % (_pick("30"), VALUE),
    )

    assert result == {"hero": False, "empty": True, "value": "--"}


def test_switching_from_90_to_30_shows_the_30_day_result_with_one_word(client, pm_dir_factory):
    pm_dir_factory({_ago(10): _stamped_day("2026-07-07"), _ago(60): _stamped_day("2026-06-22")})
    p30, p90 = _history(client, 30), _history(client, 90)
    assert p30["total_saved"] != p90["total_saved"], "fixture integrity: the windows must differ"

    result = _run(
        client,
        [route("/api/history/30", p30), route("/api/history/90", p90)],
        """
        await H.boot();
        await H.click(%r);
        const at90 = H.text(%r);
        await H.click(%r);
        return { at90: at90, at30: H.text(%r) };
        """
        % (_pick("90"), VALUE, _pick("30"), VALUE),
    )

    assert result["at90"] == analytics.format_dollar_saved(p90["total_saved"])
    assert result["at30"] == analytics.format_dollar_saved(p30["total_saved"])
    assert len(re.findall(r"saved|lost", result["at30"])) == 1


def test_a_failed_refresh_keeps_the_prior_tile_without_a_second_word(client, pm_dir_factory):
    pm_dir_factory({_ago(5): _stamped_day("2026-07-07")})
    payload = _history(client, 30)
    routes = [
        {
            "pattern": "^/api/history/30$",
            "responses": [{"status": 200, "json": payload}, {"reject": True}],
        }
    ]

    result = _run(
        client,
        routes,
        """
        await H.boot();
        const before = H.text(%r);
        await H.tick();
        return { before: before, after: H.text(%r), calls: H.fetchLog.filter(function (u) { return u === '/api/history/30'; }).length };
        """
        % (VALUE, VALUE),
    )

    assert result["calls"] == 2, "the poll must have re-requested the 30-day window"
    assert result["before"] == analytics.format_dollar_saved(payload["total_saved"])
    assert result["after"] == result["before"]


# SC-2: the page and the producer round the same exact-binary ties the same way.
_TIE_VALUES = [0.125, -0.125, 0.625, 0.004, -0.004, 1234.5, 0.0]


@pytest.mark.parametrize("value", _TIE_VALUES)
def test_hero_and_reason_card_round_ties_as_the_producer_does(client, pm_dir_factory, value):
    pm_dir_factory({_ago(5): _stamped_day("2026-06-22")})
    payload = _history(client, 30)
    payload["total_saved"] = value
    first_reason = next(iter(payload["by_reason"]))
    payload["by_reason"][first_reason]["dollars"] = value

    result = _run(
        client,
        [route("/api/history/30", payload)],
        """
        await H.boot();
        await H.click(%r);
        return { hero: H.text(%r),
                 cards: H.$$('[data-testid="reason-card"]').map(function (c) { return c.textContent; }) };
        """
        % (_pick("30"), VALUE),
    )

    expected = analytics.format_dollar_saved(value)
    assert result["hero"] == expected
    assert any(expected in c for c in result["cards"]), f"no reason card reads {expected!r}"


# ---------------------------------------------------------------------------
# Default window and the YTD token
# ---------------------------------------------------------------------------


def test_history_opens_on_the_30_day_window_and_asks_for_it(client, pm_dir_factory):
    pm_dir_factory({_ago(5): _stamped_day("2026-06-22")})
    payload = _history(client, 30)

    # The token holder reads the CSRF token at load, so the first fetch is not always the data read.
    result = _run(
        client,
        [route("/api/history/30", payload), route("/api/history/90", payload)],
        """
        await H.boot();
        return { active: H.$$('#window-picker button.active').map(function (b) { return b.textContent.trim(); }),
                 first: H.fetchLog.find(function (u) { return u !== '/api/csrf-token'; }) };
        """,
    )

    assert result["active"] == ["30d"]
    assert result["first"] == "/api/history/30"


def test_the_ytd_button_requests_the_ytd_token_not_a_day_count(client, pm_dir_factory):
    pm_dir_factory({_ago(5): _stamped_day("2026-06-22")})
    payload = _history(client, 30)

    result = _run(
        client,
        [route(r"^/api/history/\w+$", payload, exact=False)],
        """
        await H.boot();
        await H.click(%r);
        return { last: H.fetchLog[H.fetchLog.length - 1] };
        """
        % _pick("ytd"),
    )

    assert result["last"] == "/api/history/ytd"


# ---------------------------------------------------------------------------
# The day drilldown names each symphony and lists exits in time order
# ---------------------------------------------------------------------------

_DRILL = """
await H.boot();
await H.click(%r);
%s
await H.click('rect[data-date="%s"]');
return { heading: H.text('#todays-exits-heading'), rows: H.rows('#triggers-tbody') };
"""


def _drill(client, triggers_by_day: dict[str, list[dict]], pm_dir_factory, open_day: str):
    pm_dir_factory(triggers_by_day)
    payload = _history(client, 30)
    assert open_day in payload["daily_dates"], "fixture integrity: the day must have a bar"
    return payload, _run(
        client,
        [route("/api/history/30", payload)],
        _DRILL % (_pick("30"), "", open_day),
    )


def _by_time(items: list[dict]) -> list[dict]:
    """Expected order: ascending time_triggered, untimed last, ties in file order."""
    keyed = [
        (0 if t.get("time_triggered") else 1, t.get("time_triggered") or "", i, t)
        for i, t in enumerate(items)
    ]
    return [k[3] for k in sorted(keyed, key=lambda k: k[:3])]


def test_three_exits_written_out_of_order_list_by_time_with_their_names(client, pm_dir_factory):
    day = _ago(4)
    base = pm_capture("2026-06-22")["triggers"]
    distinct = []
    for t in base:
        if t["time_triggered"] not in {d["time_triggered"] for d in distinct}:
            distinct.append(t)
    first, second, third = distinct[0], distinct[1], distinct[2]
    in_file_order = [stamped(second), stamped(third), stamped(first)]
    assert [t["time_triggered"] for t in in_file_order] != sorted(
        t["time_triggered"] for t in in_file_order
    )

    _, result = _drill(client, {day: in_file_order}, pm_dir_factory, day)

    expected = _by_time(in_file_order)
    assert [r[0] for r in result["rows"]] == [t["time_triggered"] for t in expected]
    assert [r[1] for r in result["rows"]] == [t["symphony_name"] for t in expected]
    assert result["heading"] == f"{day_label(day)} exits (3)"


def test_an_exit_with_no_symphony_name_reads_name_not_recorded(client, pm_dir_factory):
    day = _ago(4)
    named, unnamed = pm_capture("2026-06-22")["triggers"][:2]
    unnamed = {k: v for k, v in unnamed.items() if k != "symphony_name"}

    _, result = _drill(client, {day: [stamped(unnamed), stamped(named)]}, pm_dir_factory, day)

    by_time = {r[0]: r[1] for r in result["rows"]}
    assert by_time[unnamed["time_triggered"]] == "name not recorded"
    assert by_time[named["time_triggered"]] == named["symphony_name"]


def test_an_exit_with_no_trigger_time_is_listed_after_every_timed_exit(client, pm_dir_factory):
    day = _ago(4)
    timed = pm_capture("2026-06-22")["triggers"][:3]
    untimed = {
        k: v for k, v in pm_capture("2026-06-22")["triggers"][3].items() if k != "time_triggered"
    }
    in_file_order = [stamped(untimed)] + [stamped(t) for t in timed]

    _, result = _drill(client, {day: in_file_order}, pm_dir_factory, day)

    expected = _by_time(in_file_order)
    assert [r[1] for r in result["rows"]] == [t["symphony_name"] for t in expected]
    assert result["rows"][-1][1] == untimed["symphony_name"]


def test_two_exits_with_the_same_trigger_time_are_both_listed_in_file_order(client, pm_dir_factory):
    day = _ago(4)
    same_time = [t for t in pm_capture("2026-06-23")["triggers"] if t["time_triggered"] == "09:50"]
    assert len({t["symphony_name"] for t in same_time}) >= 2, (
        "fixture integrity: two names share 09:50"
    )
    # File order is the reverse of name order, so any name tiebreak would reorder the pair.
    pair = sorted(same_time, key=lambda t: t["symphony_name"], reverse=True)[:2]
    earlier = next(
        t for t in pm_capture("2026-06-23")["triggers"] if t["time_triggered"] != "09:50"
    )

    _, result = _drill(client, {day: [stamped(t) for t in [*pair, earlier]]}, pm_dir_factory, day)

    tied_rows = [r for r in result["rows"] if r[0] == "09:50"]
    assert [r[1] for r in tied_rows] == [t["symphony_name"] for t in pair]


def test_a_day_with_a_bar_but_no_valid_exit_shows_zero_and_the_placeholder_row(
    client, pm_dir_factory
):
    valid_day, unstamped_day = _ago(4), _ago(5)
    unstamped = pm_capture("2026-07-07")["triggers"]  # verbatim: no if_held_source stamp
    payload, result = _drill(
        client,
        {valid_day: _stamped_day("2026-06-22"), unstamped_day: unstamped},
        pm_dir_factory,
        unstamped_day,
    )

    assert payload["daily_exits"].get(unstamped_day) in (None, [])
    assert result["heading"] == f"{day_label(unstamped_day)} exits (0)"
    assert result["rows"] == [["No exit records for this window."]]


def test_opening_a_second_day_shows_only_that_days_exits(client, pm_dir_factory):
    first_day, second_day = _ago(4), _ago(5)
    second_triggers = _stamped_day("2026-07-07")
    pm_dir_factory({first_day: _stamped_day("2026-06-22"), second_day: second_triggers})
    payload = _history(client, 30)

    result = _run(
        client,
        [route("/api/history/30", payload)],
        """
        await H.boot();
        await H.click(%r);
        await H.click('rect[data-date="%s"]');
        await H.click('rect[data-date="%s"]');
        return { heading: H.text('#todays-exits-heading'), rows: H.rows('#triggers-tbody') };
        """
        % (_pick("30"), first_day, second_day),
    )

    expected = _by_time(second_triggers)
    assert result["heading"] == f"{day_label(second_day)} exits ({len(expected)})"
    assert [(r[0], r[1]) for r in result["rows"]] == [
        (t["time_triggered"], t["symphony_name"]) for t in expected
    ]
