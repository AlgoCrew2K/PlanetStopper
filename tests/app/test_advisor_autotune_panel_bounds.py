"""The AI Advisor's autotune panel: a bounded card list, older runs behind a closed
``<details>``, and the sparkline drawn from the runs the route returns.

Subject: the real ``static/ai_advisor.js`` under node (``tests/_ai_advisor_autotune_panel_harness.py``)
fed the real ``GET /api/autotune-runs`` body over rows written by ``database.save_autotune_run``,
plus the server render of ``GET /ai-advisor`` for the static markup.

* The cards container holds one card per symphony, each that symphony's newest run, newest
  first, at most 12 of them.
* Every other returned row is an older run, listed in the route's order inside a ``<details>``
  that is closed, hidden while it has no rows, and never recreated by the 15 s poll.
* The ``<details>`` summary states the older-run count and that the list covers the route's 50
  most recent runs.
* The sparkline draws every returned run that has a score. It is hidden when the chart
  library is missing or no run has a score, shown again when one arrives, holds exactly one
  live chart however often the list renders, and replaces a chart the canvas already holds.
* A card prints its time as the New York stamp, with no fraction.

Fixture provenance: ``tests/fixtures/ai_advisor/ux_overhaul_02/PROVENANCE.md`` (autotune
section). Run rows are the output of the real writer; the symphony keys are the normalized
display names of ``tests/fixtures/prod_droplet/bot_state_positions.json``; scores are labelled
constructions and no assertion copies one: each test reads the route's own rows.
"""

from __future__ import annotations

import json
import pathlib
import re
from datetime import datetime, timedelta

import pytest

import analytics
import database
from tests._ai_advisor_autotune_panel_harness import drive
from tests._ux_truth_support import captured_symphonies, load_captured_bot_state
from tests.app import _retirement_tab_world as w
from tests.app import _tenure_review_panel_builders as pb

_ROUTE = "/api/autotune-runs"
_MAX_CARDS = 12
_ROUTE_ROW_LIMIT = 50
_EMPTY_TEXT = "No tuning runs recorded yet."
_TIME_VALUES = (
    pathlib.Path(__file__).resolve().parents[1]
    / "fixtures"
    / "ai_advisor"
    / "ux_overhaul_02"
    / "time_values.json"
)
_FIRST_WEEK = datetime(2026, 9, 1, 4, 0, 0)
_CARD = "autotune-run-row"


# ---------------------------------------------------------------------------
# Rows: the real writer, read back through the real route
# ---------------------------------------------------------------------------


def _symphony_keys(count: int) -> list[str]:
    """Normalized keys of captured symphonies; beyond the 11 captured, plain extra keys that
    no live state holds (their card then prints the stored key)."""
    keys = [database.normalize_name(name) for _, name in captured_symphonies()]
    keys += [f"extra-symphony-{i}" for i in range(max(0, count - len(keys)))]
    return keys[:count]


def _stamp(symphony_index: int, run_index: int) -> str:
    moment = _FIRST_WEEK + timedelta(weeks=run_index, minutes=symphony_index)
    return moment.strftime("%Y-%m-%dT%H:%M:%S")


def _save_runs(
    keys: list[str],
    runs_each: int,
    *,
    score: bool = True,
    decision: str = "Reverted to Fallback",
) -> None:
    """``runs_each`` runs per key, one a week, each with its own score when ``score``."""
    for j in range(runs_each):
        for i, key in enumerate(keys):
            database.save_autotune_run(
                _stamp(i, j),
                key,
                0.5,
                1.0,
                decision,
                0.4,
                0.3,
                selection_tstat=2.0 + i * 0.01 + j * 0.1,
                naive_sharpe=(1.0 + i * 0.01 + j * 0.1) if score else None,
            )


def _route_rows(client) -> list[dict]:
    response = client.get(_ROUTE)
    assert response.status_code == 200
    rows = response.get_json()
    assert isinstance(rows, list) and rows, "the route returned no rows"
    return rows


def _expected_partition(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Cards: each symphony's first (newest) row, the first 12 of those. Older: every other row."""
    seen: set = set()
    newest = []
    for row in rows:
        if row["symphony_id"] not in seen:
            seen.add(row["symphony_id"])
            newest.append(row)
    cards = newest[:_MAX_CARDS]
    card_ids = {row["id"] for row in cards}
    return cards, [row for row in rows if row["id"] not in card_ids]


def _routes(rows) -> dict:
    return {_ROUTE: rows}


# ---------------------------------------------------------------------------
# Reading the snapshots
# ---------------------------------------------------------------------------


def _cards(markup: str) -> list[pb.Node]:
    return pb.parse_html(f"<div>{markup}</div>").find_all(_CARD)


def _card_key(card: pb.Node) -> str:
    named = [n for n in card.walk() if "autotune-run-name" in n.classes()]
    assert named, "a card has no .autotune-run-name"
    return named[0].attrs.get("title", "")


def _score_text(row: dict) -> str:
    return f"{row['naive_sharpe']:.4f}"


def _loaded(client, *, chart: bool = True, extra_steps=()) -> tuple[list[dict], list[dict]]:
    """Rows from the route and the snapshots of load followed by ``extra_steps``."""
    rows = _route_rows(client)
    return rows, drive([{"op": "load"}, *extra_steps], _routes(rows), chart=chart)


# ---------------------------------------------------------------------------
# One card per symphony, older runs behind the details
# ---------------------------------------------------------------------------


class TestCardsAndOlderRuns:
    def test_each_symphony_gets_one_card_showing_its_newest_run(self, client):
        _save_runs(_symphony_keys(11), 3)
        rows, (loaded,) = _loaded(client)
        expected, _ = _expected_partition(rows)
        cards = _cards(loaded["list"])
        assert [_card_key(c) for c in cards] == [r["symphony_id"] for r in expected]
        assert len(cards) == 11
        by_key: dict[str, list[dict]] = {}
        for row in rows:
            by_key.setdefault(row["symphony_id"], []).append(row)
        for card, newest in zip(cards, expected, strict=True):
            text = card.text()
            assert _score_text(newest) in text
            for older in by_key[newest["symphony_id"]][1:]:
                assert _score_text(older) not in text, "a card shows a run that is not the newest"

    def test_the_other_rows_are_older_runs_in_the_routes_order(self, client):
        _save_runs(_symphony_keys(11), 3)
        rows, (loaded,) = _loaded(client)
        _, older = _expected_partition(rows)
        listed = _cards(loaded["older"]["html"])
        assert len(older) == len(rows) - 11 > 0
        assert [_card_key(c) for c in listed] == [r["symphony_id"] for r in older]
        for card, row in zip(listed, older, strict=True):
            assert _score_text(row) in card.text()

    def test_the_older_runs_summary_states_the_older_count_and_the_fifty_run_cap(self, client):
        _save_runs(_symphony_keys(11), 3)
        rows, (loaded,) = _loaded(client)
        older_count = len(rows) - 11
        text = loaded["older"]["summary"]
        assert set(re.findall(r"\d+", text)) == {str(older_count), str(_ROUTE_ROW_LIMIT)}, text
        assert "recent" in text.lower(), text
        assert str(len(rows)) not in re.findall(r"\d+", text), "the total, not the older count"

    def test_the_details_is_shown_and_closed_when_older_runs_exist(self, client):
        _save_runs(_symphony_keys(11), 3)
        _, (loaded,) = _loaded(client)
        assert loaded["older"]["displayed"] is True
        assert loaded["older"]["open"] is False

    def test_fifteen_symphonies_give_twelve_cards_and_the_rest_go_to_older_runs(self, client):
        _save_runs(_symphony_keys(15), 2)
        rows, (loaded,) = _loaded(client)
        expected_cards, expected_older = _expected_partition(rows)
        cards = _cards(loaded["list"])
        assert len(cards) == _MAX_CARDS
        assert [_card_key(c) for c in cards] == [r["symphony_id"] for r in expected_cards]
        newest_all = []
        for row in rows:
            if row["symphony_id"] not in {r["symphony_id"] for r in newest_all}:
                newest_all.append(row)
        left_out = [r["symphony_id"] for r in newest_all[_MAX_CARDS:]]
        assert len(left_out) == 3
        older_keys = [_card_key(c) for c in _cards(loaded["older"]["html"])]
        assert older_keys == [r["symphony_id"] for r in expected_older]
        for key in left_out:
            assert key in older_keys, "a symphony without a card lost its latest run"

    def test_an_empty_route_shows_the_empty_text_and_no_older_runs(self, client):
        (loaded,) = drive([{"op": "load"}], _routes([]))
        assert _EMPTY_TEXT in loaded["list"]
        assert _cards(loaded["list"]) == []
        assert loaded["older"]["displayed"] is False
        # Before any script runs the page already holds the older-runs details, hidden:
        # with no runs there is nothing for the script to reveal.
        details = pb.parse_html(w.get_page(client)).find("autotune-older-runs")
        assert details is not None and "hidden" in details.attrs

    def test_one_run_per_symphony_leaves_the_details_hidden(self, client):
        _save_runs(_symphony_keys(11), 1)
        rows, (loaded,) = _loaded(client)
        assert len(_cards(loaded["list"])) == 11 == len(rows)
        assert loaded["older"]["displayed"] is False
        assert _cards(loaded["older"]["html"]) == []

    def test_the_details_follows_whether_older_runs_exist(self, client):
        _save_runs(_symphony_keys(11), 3)
        many = _route_rows(client)
        newest_ids = {row["id"] for row in _expected_partition(many)[0]}
        few = [row for row in many if row["id"] in newest_ids]
        snapshots = drive(
            [
                {"op": "load"},
                {"op": "routes", "routes": _routes(few)},
                {"op": "poll"},
                {"op": "routes", "routes": _routes(many)},
                {"op": "poll"},
            ],
            _routes(many),
        )
        loaded, shrunk, regrown = snapshots[0], snapshots[2], snapshots[4]
        assert loaded["older"]["displayed"] is True
        assert shrunk["older"]["displayed"] is False
        assert regrown["older"]["displayed"] is True

    def test_a_poll_leaves_the_cards_and_the_opened_details_alone(self, client):
        _save_runs(_symphony_keys(11), 3)
        rows = _route_rows(client)
        steps = [{"op": "load"}, {"op": "open"}, {"op": "poll"}, {"op": "poll"}]
        snapshots = drive(steps, _routes(rows))
        _, opened, first_poll, second_poll = snapshots
        assert opened["older"]["open"] is True
        for after in (first_poll, second_poll):
            assert after["older"]["open"] is True, "a poll closed the details the operator opened"
            assert after["older"]["displayed"] is True
            assert len(_cards(after["list"])) == 11
            assert len(_cards(after["older"]["html"])) == len(rows) - 11
        for snapshot in snapshots:
            assert snapshot["older"]["detailsRecreated"] == 0, "the details element was recreated"
            assert snapshot["older"]["summaryRecreated"] == 0
            assert snapshot["older"]["listRecreated"] == 0

    def test_a_rejected_request_shows_the_refresh_notice_and_keeps_the_details_hidden(self):
        (loaded,) = drive([{"op": "load"}], {_ROUTE: {"__reject__": "network down"}})
        assert loaded["notice"] == "Could not refresh runs"
        assert "network down" not in loaded["list"], (
            "a transport error's text is never written into the page"
        )
        assert loaded["older"]["displayed"] is False


class TestCardContent:
    def test_every_card_and_older_row_carries_the_pill_score_frozen_eval_and_streak(self, client):
        database.save_state(load_captured_bot_state())
        keys = _symphony_keys(11)
        _save_runs(keys[:1], 2)  # two runs, neither adopted: a streak of two
        _save_runs(keys[1:], 1)
        rows, (loaded,) = _loaded(client)
        streak_key = keys[0]
        streak_rows = [r for r in rows if r["symphony_id"] == streak_key]
        assert len(streak_rows) == 2
        assert streak_rows[0]["never_adopted_streak"]["streak_weeks"] == 2
        newest_card = _cards(loaded["list"])[
            [r["symphony_id"] for r in _expected_partition(rows)[0]].index(streak_key)
        ]
        older_rows = _cards(loaded["older"]["html"])
        assert len(older_rows) == 1, "the streak symphony's older run is not listed"
        for card in (newest_card, older_rows[0]):
            classes = {c for n in card.walk() for c in n.classes()}
            assert "decision-pill" in classes
            assert "Fallback" in card.text()
            assert "Best objective score" in card.text()
            assert "Frozen-eval" in card.text()
            assert "2 consecutive runs without adopting a tuned config" in card.text()
        other = _cards(loaded["list"])[1].text()
        assert "consecutive runs without adopting" not in other

    def test_a_card_prints_the_new_york_stamp_with_no_fraction(self, client):
        stamped = json.loads(_TIME_VALUES.read_text(encoding="utf-8"))["autotune_run_timestamp"]
        key = _symphony_keys(1)[0]
        database.save_autotune_run(
            stamped, key, 0.5, 1.0, "Reverted to Fallback", 0.4, 0.3, naive_sharpe=1.5
        )
        database.save_autotune_run(
            "2026-10-10T01:08:12.048917Z", key, 0.5, 1.0, "Reverted to Fallback", 0.4, 0.3
        )
        rows, (loaded,) = _loaded(client)
        assert [r["run_timestamp"] for r in rows][0] == stamped
        older_rows = _cards(loaded["older"]["html"])
        assert len(older_rows) == 1, "the older run is not listed"
        for card, row in ((_cards(loaded["list"])[0], rows[0]), (older_rows[0], rows[1])):
            expected = analytics.format_et_stamp(row["run_timestamp"])
            assert expected.endswith(" ET") and expected != analytics.ET_STAMP_MISSING
            assert expected in card.text()
            assert ".048917" not in card.text()
            assert row["run_timestamp"] not in card.text()


# ---------------------------------------------------------------------------
# The sparkline
# ---------------------------------------------------------------------------


class TestSparkline:
    def test_it_draws_every_returned_run_that_has_a_score_and_the_canvas_is_shown(self, client):
        keys = _symphony_keys(11)
        _save_runs(keys, 3)
        _save_runs(keys[:4], 1, score=False, decision="Applied")  # newest runs of four, unscored
        rows, (loaded,) = _loaded(client)
        scored = [r for r in rows if r["naive_sharpe"] is not None]
        assert 0 < len(scored) < len(rows), "premise: some returned runs have no score"
        chart = loaded["chart"]
        assert chart["liveCount"] == 1
        assert len(chart["livePoints"]) == len(scored)
        # Oldest run first, as the series reads left to right.
        assert chart["livePoints"] == [float(r["naive_sharpe"]) for r in reversed(scored)]
        assert loaded["canvas"]["displayed"] is True

    def test_runs_that_are_only_in_the_older_list_still_draw_points(self, client):
        _save_runs(_symphony_keys(11), 3)
        rows, (loaded,) = _loaded(client)
        assert len(rows) > 11
        assert len(loaded["chart"]["livePoints"]) == len(rows)

    def test_runs_without_scores_hide_the_canvas_and_build_no_chart(self, client):
        _save_runs(_symphony_keys(11), 2, score=False, decision="Applied")
        rows, (loaded,) = _loaded(client)
        assert all(r["naive_sharpe"] is None for r in rows)
        assert loaded["canvas"]["displayed"] is False
        assert loaded["chart"]["constructed"] == []
        assert loaded["chart"]["liveCount"] == 0
        assert len(_cards(loaded["list"])) == 11, "the cards still render"

    def test_an_empty_route_hides_the_canvas(self):
        (loaded,) = drive([{"op": "load"}], _routes([]))
        assert loaded["canvas"]["displayed"] is False
        assert loaded["chart"]["constructed"] == []

    def test_a_missing_chart_library_hides_the_canvas_and_the_cards_still_render(self, client):
        _save_runs(_symphony_keys(11), 2)
        _, (loaded,) = _loaded(client, chart=False)
        assert loaded["canvas"]["displayed"] is False
        assert len(_cards(loaded["list"])) == 11
        assert "Failed to load runs" not in loaded["list"]

    def test_the_canvas_is_shown_again_when_a_score_arrives(self, client):
        keys = _symphony_keys(11)
        _save_runs(keys, 2, score=False, decision="Applied")
        unscored = _route_rows(client)
        _save_runs(keys[:3], 1)
        scored = _route_rows(client)
        assert any(r["naive_sharpe"] is not None for r in scored)
        snapshots = drive(
            [{"op": "load"}, {"op": "routes", "routes": _routes(scored)}, {"op": "poll"}],
            _routes(unscored),
        )
        first, after_poll = snapshots[0], snapshots[2]
        assert first["canvas"]["displayed"] is False
        assert after_poll["canvas"]["displayed"] is True
        assert after_poll["chart"]["liveCount"] == 1

    def test_repeated_renders_leave_exactly_one_live_chart(self, client):
        _save_runs(_symphony_keys(11), 3)
        rows = _route_rows(client)
        snapshots = drive([{"op": "load"}, {"op": "poll"}, {"op": "poll"}], _routes(rows))
        for snapshot in snapshots:
            assert snapshot["chart"]["liveCount"] == 1
            assert snapshot["chart"]["thrown"] == []
            assert snapshot["canvas"]["displayed"] is True
            assert len(snapshot["chart"]["livePoints"]) == len(rows)

    def test_a_chart_the_canvas_already_holds_is_replaced_and_nothing_throws(self, client):
        _save_runs(_symphony_keys(11), 2)
        rows = _route_rows(client)
        _, loaded = drive([{"op": "foreign_chart"}, {"op": "load"}], _routes(rows))
        assert loaded["chart"]["thrown"] == [], loaded["chart"]["thrown"]
        assert "Failed to load runs" not in loaded["list"]
        assert len(_cards(loaded["list"])) == 11
        assert loaded["chart"]["liveCount"] == 1
        assert loaded["chart"]["liveIsForeign"] is False
        assert len(loaded["chart"]["livePoints"]) == len(rows)

    def test_one_scored_run_draws_one_point(self, client):
        _save_runs(_symphony_keys(1), 1)
        rows, (loaded,) = _loaded(client)
        assert len(rows) == 1
        assert loaded["chart"]["livePoints"] == [float(rows[0]["naive_sharpe"])]
        assert loaded["canvas"]["displayed"] is True


# ---------------------------------------------------------------------------
# The page's static markup
# ---------------------------------------------------------------------------

_PAGE_ROOT = pathlib.Path(__file__).resolve().parents[2]


_STATIC_URL_FOR = re.compile(
    r"""^\{\{\s*url_for\(\s*'static'\s*,\s*filename\s*=\s*'([^']+)'\s*\)\s*\}\}$"""
)


def _chart_src(markup: str) -> list[str]:
    """The chart library script URLs of a page, a template's static ``url_for`` tag resolved to
    the URL it renders to, so a template and a rendered page compare on what the browser loads."""
    found = re.findall(r'<script[^>]+src="([^"]*chart[^"]*)"', markup, flags=re.IGNORECASE)
    return [
        f"/static/{match.group(1)}" if (match := _STATIC_URL_FOR.match(src)) else src
        for src in found
    ]


class TestPageMarkup:
    def test_the_older_runs_details_is_static_hidden_closed_and_inside_the_panel(self, client):
        root = pb.parse_html(w.get_page(client))
        details = root.find("autotune-older-runs")
        assert details is not None, "the autotune panel has no older-runs details"
        assert details.tag == "details" and details.attrs.get("id") == "autotune-older-runs"
        assert "hidden" in details.attrs, "it starts hidden"
        assert "open" not in details.attrs, "it starts closed"
        assert details.is_inside(root.find("autotune-panel"))
        assert details.is_inside(root.find("tab-panel-overview"))
        summary = details.find("autotune-older-summary")
        rows = details.find("autotune-older-list")
        assert summary is not None and summary.tag == "summary"
        assert summary.attrs.get("id") == "autotune-older-summary"
        assert rows is not None and rows.attrs.get("id") == "autotune-older-list"
        assert summary.is_inside(details) and rows.is_inside(details)

    def test_the_cards_container_and_canvas_keep_their_hooks(self, client):
        root = pb.parse_html(w.get_page(client))
        assert root.find("autotune-runs-list").attrs.get("id") == "autotune-runs-list"
        canvas = root.find("autotune-sparkline")
        assert canvas.tag == "canvas" and canvas.attrs.get("id") == "autotune-sparkline-canvas"

    def test_the_chart_library_tag_is_the_dashboards_and_precedes_the_page_script(self, client):
        page = w.get_page(client)
        dashboard = (_PAGE_ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        dashboard_src = _chart_src(dashboard)
        assert len(dashboard_src) == 1, "premise: the dashboard loads the library once"
        assert _chart_src(page) == dashboard_src
        head = page.split("</head>", 1)[0]
        assert _chart_src(head) == dashboard_src, "the tag is in the head"
        assert head.index(dashboard_src[0]) < page.index("ai_advisor.js")
