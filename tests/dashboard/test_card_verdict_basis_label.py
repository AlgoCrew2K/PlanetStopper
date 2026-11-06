"""The label beside a card's verdict figure says whether the figure moves.

The figure is the latest exit's return minus the symphony's current (if-held) return. While
the market is open that return moves every engine cycle, so the figure moves too; the card used
to call it "at exit", which says it is fixed. The label now reads "vs held now" while the
market is open and "at close" once the close is frozen, on the server render and on every poll,
and never "at exit". The figure's formula is unchanged.

Method: the real worlds of `_refresh_timing_card_world` (captured records, real temp database),
the real `GET /` markup and `/api/state` bodies for the same state, then the real client code run
over that markup under node. The rounding rule of the figure is the browser's `toFixed`:
expected strings come from node, never from Python's formatting. A page is judged visible only
on a live render; this file proves text, attributes and hidden state.
"""

from __future__ import annotations

import pathlib

import pytest

from tests.app._refresh_timing_card_world import (
    CLOSED_INSTANT,
    DASH,
    LABEL_CLOSED,
    LABEL_OPEN,
    PRE_MARKET_INSTANT,
    closed_cards_world,
    live_entry,
    open_cards_world,
    rendered_cards,
    seed_exit_row,
    set_live_return,
)
from tests.app._refresh_timing_state_world import TRIGGERED_ID
from tests.dashboard._exit_verdict_copy import verdict_lead, verdict_sentence
from tests.dashboard._first_paint_dom import js_to_fixed, run_page

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_TEMPLATE = _ROOT / "templates" / "index.html"
_SCRIPT = _ROOT / "static" / "index.js"

_CSRF = {"/api/csrf-token": {"body": {"csrf_token": "csrf-fixture-token"}}}


def _verdict(guard_alpha: float, label: str) -> str:
    """The text a card shows for a guard alpha: figure sentence and label."""
    magnitude = js_to_fixed([abs(guard_alpha)], 1)[0]
    return f"{verdict_sentence(guard_alpha, magnitude)} {label}"


def _run(tmp_path, page: str, first_body: dict, later_bodies: list[dict]) -> list[tuple[str, dict]]:
    """Run the page's client code: first paint, the load poll, then one poll per later body.

    Returns the triggered card as observed at each point, with a name for the point.
    """
    run = run_page(
        page,
        tmp_path,
        routes={**_CSRF, "/api/state": {"body": first_body}},
        steps=[
            {"op": "poll", "label": f"poll {n}", "route": {"/api/state": {"body": body}}}
            for n, body in enumerate(later_bodies, start=1)
        ],
    )
    assert not [e for e in run["errors"] if e.startswith(("index.js:", "inline.js:"))], run[
        "errors"
    ][:2]
    seen = [("first paint", run["initial"]["cards"][TRIGGERED_ID])]
    seen.append(("load poll", run["loaded"]["cards"][TRIGGERED_ID]))
    seen += [(step["label"], step["obs"]["cards"][TRIGGERED_ID]) for step in run["steps"]]
    return seen


# ---------------------------------------------------------------------------
# Open market: the figure moves with the held return, and says so
# ---------------------------------------------------------------------------


class TestLabelWhileTheMarketIsOpen:
    def test_figure_changes_between_cycles_and_every_path_reads_vs_held_now(
        self, monkeypatch, tmp_path
    ):
        world = open_cards_world(monkeypatch, tmp_path)
        exit_return = seed_exit_row()
        first_return = live_entry(TRIGGERED_ID)["current_return"]
        second_return = first_return + 0.5
        page = world.page()
        first_body = world.poll()
        set_live_return(TRIGGERED_ID, second_return)
        second_body = world.poll()

        seen = _run(tmp_path, page, first_body, [second_body])

        first = _verdict(exit_return - first_return, LABEL_OPEN)
        second = _verdict(exit_return - second_return, LABEL_OPEN)
        assert first != second, "fixture: the two cycles must show different figures"
        assert [(where, card["verdict"]["text"]) for where, card in seen] == [
            ("first paint", first),
            ("load poll", first),
            ("poll 1", second),
        ]

    def test_the_label_never_reads_at_exit(self, monkeypatch, tmp_path):
        world = open_cards_world(monkeypatch, tmp_path)
        seed_exit_row()
        page = world.page()
        body = world.poll()

        for where, card in _run(tmp_path, page, body, [body]):
            assert card["atExit"] is None, f"{where}: an element still labels the figure at exit"
            assert "at exit" not in card["verdict"]["text"], where

    def test_figure_crossing_zero_switches_the_sentence_and_keeps_the_label(
        self, monkeypatch, tmp_path
    ):
        world = open_cards_world(monkeypatch, tmp_path)
        exit_return = seed_exit_row()
        below = exit_return - 0.3
        above = exit_return + 0.3
        set_live_return(TRIGGERED_ID, below)
        page = world.page()
        first_body = world.poll()
        set_live_return(TRIGGERED_ID, above)
        second_body = world.poll()

        seen = _run(tmp_path, page, first_body, [second_body])

        saved = _verdict(exit_return - below, LABEL_OPEN)
        gave_up = _verdict(exit_return - above, LABEL_OPEN)
        assert saved.startswith(verdict_lead("beat")), "fixture"
        assert gave_up.startswith(verdict_lead("trailed")), "fixture"
        assert [card["verdict"]["text"] for _, card in seen] == [saved, saved, gave_up]

    def test_three_polls_with_no_cycle_between_them_leave_the_verdict_unchanged(
        self, monkeypatch, tmp_path
    ):
        world = open_cards_world(monkeypatch, tmp_path)
        exit_return = seed_exit_row()
        page = world.page()
        body = world.poll()

        seen = _run(tmp_path, page, body, [body, body, body])

        expected = _verdict(exit_return - live_entry(TRIGGERED_ID)["current_return"], LABEL_OPEN)
        assert {card["verdict"]["text"] for _, card in seen} == {expected}
        assert len(seen) == 5

    def test_figure_is_the_exit_return_minus_the_current_return(self, monkeypatch, tmp_path):
        """The formula is untouched: only the label changed."""
        world = open_cards_world(monkeypatch, tmp_path)
        exit_return = seed_exit_row()

        body = world.poll()

        current = live_entry(TRIGGERED_ID)["current_return"]
        assert body["bot_state"][TRIGGERED_ID]["guard_alpha"] == round(exit_return - current, 6)

    def test_a_figure_of_exactly_zero_reads_as_a_match_with_the_label(
        self, monkeypatch, tmp_path
    ):
        world = open_cards_world(monkeypatch, tmp_path)
        exit_return = seed_exit_row(at_return=live_entry(TRIGGERED_ID)["current_return"])
        assert exit_return - live_entry(TRIGGERED_ID)["current_return"] == 0, "fixture"
        page = world.page()
        body = world.poll()

        seen = _run(tmp_path, page, body, [body])

        assert {card["verdict"]["text"] for _, card in seen} == {_verdict(0.0, LABEL_OPEN)}
        assert _verdict(0.0, LABEL_OPEN) == f"{verdict_lead('matched')} {LABEL_OPEN}"


# ---------------------------------------------------------------------------
# After the close: the figure is fixed on the snapshot, and says so
# ---------------------------------------------------------------------------


class TestLabelAfterTheClose:
    def test_page_and_three_polls_read_at_close_with_one_figure(self, monkeypatch, tmp_path):
        world = closed_cards_world(monkeypatch, tmp_path)
        seed_exit_row()
        page = world.page()
        bodies = [world.poll(), world.poll(), world.poll()]
        figure = bodies[0]["bot_state"][TRIGGERED_ID].get("guard_alpha")
        assert figure is not None, "the frozen poll must carry the figure the label qualifies"

        seen = _run(tmp_path, page, bodies[0], bodies[1:] + [bodies[0]])

        expected = _verdict(figure, LABEL_CLOSED)
        assert {card["verdict"]["text"] for _, card in seen} == {expected}

    def test_label_is_at_close_before_the_open_too(self, monkeypatch, tmp_path):
        world = closed_cards_world(monkeypatch, tmp_path, instant=PRE_MARKET_INSTANT)
        seed_exit_row()

        shown = rendered_cards(world.page())[TRIGGERED_ID]

        assert shown["basis_label"] == LABEL_CLOSED
        assert shown["basis_hidden"] is False


# ---------------------------------------------------------------------------
# No figure, no label
# ---------------------------------------------------------------------------


class TestNoFigureNoLabel:
    @pytest.mark.parametrize("branch", ["open", "closed"])
    def test_card_without_a_guard_alpha_reads_the_dash_and_hides_the_label(
        self, monkeypatch, tmp_path, branch
    ):
        world = (
            open_cards_world(monkeypatch, tmp_path)
            if branch == "open"
            else closed_cards_world(monkeypatch, tmp_path)
        )
        page = world.page()
        body = world.poll()
        assert body["bot_state"][TRIGGERED_ID].get("guard_alpha") is None, "fixture: no exit row"

        seen = _run(tmp_path, page, body, [body])

        for where, card in seen:
            assert card["verdict"]["text"] == DASH, where
            assert card["basis"] is not None, f"{where}: the basis label element is missing"
            assert card["basis"]["hidden"] is True, f"{where}: a label is shown beside no figure"

    def test_card_painted_without_a_figure_gets_its_label_when_the_exit_arrives(
        self, monkeypatch, tmp_path
    ):
        world = open_cards_world(monkeypatch, tmp_path)
        page = world.page()
        exit_return = seed_exit_row()
        body = world.poll()

        seen = _run(tmp_path, page, body, [])

        assert seen[0][1]["basis"]["hidden"] is True
        loaded = seen[1][1]
        assert loaded["basis"]["hidden"] is False
        assert loaded["verdict"]["text"] == _verdict(
            exit_return - live_entry(TRIGGERED_ID)["current_return"], LABEL_OPEN
        )


# ---------------------------------------------------------------------------
# The server chooses the label from the market state
# ---------------------------------------------------------------------------


class TestServerRenderChoosesTheLabelFromTheMarketState:
    def test_open_page_labels_the_figure_vs_held_now(self, monkeypatch, tmp_path):
        world = open_cards_world(monkeypatch, tmp_path)
        seed_exit_row()

        shown = rendered_cards(world.page())[TRIGGERED_ID]

        assert shown["basis_testid"] == "guard-alpha-basis"
        assert shown["basis_label"] == LABEL_OPEN
        assert shown["basis_hidden"] is False

    @pytest.mark.parametrize("instant", [CLOSED_INSTANT, PRE_MARKET_INSTANT])
    def test_frozen_pages_label_the_figure_at_close(self, monkeypatch, tmp_path, instant):
        world = closed_cards_world(monkeypatch, tmp_path, instant=instant)
        seed_exit_row()

        shown = rendered_cards(world.page())[TRIGGERED_ID]

        assert shown["basis_testid"] == "guard-alpha-basis"
        assert shown["basis_label"] == LABEL_CLOSED
        assert shown["basis_hidden"] is False


# ---------------------------------------------------------------------------
# Sources: no reader of the old label is left
# ---------------------------------------------------------------------------


class TestNoReaderOfTheOldLabelIsLeft:
    def test_template_carries_the_basis_label_and_not_the_old_one(self):
        source = _TEMPLATE.read_text(encoding="utf-8")

        assert 'data-testid="guard-alpha-basis"' in source
        assert "verdict_basis_label" in source, "the label text must come from the render context"
        assert "guard-alpha-at-exit" not in source
        assert 'Label it "at exit"' not in source, "the comment still describes the old label"

    def test_script_writes_both_labels_and_never_the_old_testid(self):
        source = _SCRIPT.read_text(encoding="utf-8")

        assert LABEL_OPEN in source and LABEL_CLOSED in source
        assert "guard-alpha-at-exit" not in source
        assert "guard-alpha-basis" in source
