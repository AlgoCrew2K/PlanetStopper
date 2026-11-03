"""The settings modal's AI advisor section renders real text from the rows the route returns.

Real call path: ``static/settings-modal.js`` runs under node (tests/ui/_iife_node_harness.py) and
opens a symphony; the answer it reads is the real ``GET /api/symphony-settings/<id>`` over
``advisor_observations`` rows written through ``database.insert_advisor_observation``. The route
returns the raw rows (``advisor_role``, ``verdict``, ``raw_response``, ``created_at``); until this fix
the card renderer read fields of the old suggestion payload (``variable``, ``rationale``,
``confidence``), so every card was an empty bar.

Rows (the producers are real, the inputs are minimal):
* a CLEAR row from ``advisors.overfitting_conscience.run_overfitting_conscience`` (its output carries
  a prose ``note``);
* a BREACH row from the same producer (its output carries counts and facet names, no ``note``);
* a row with no verdict and an empty body, written through the real writer: synthetic, labelled so; it
  stands for a stored row with nothing to say.

Contract pinned
* Every advisor card that is drawn holds visible text; none is an empty bar.
* A row's verdict appears as text on its card, and a row's own prose (``note``) appears on its card.
* A row with nothing to say draws no card (and when no row has anything, the section says so, once,
  in words, instead of drawing bars).
* Text is escaped: a hostile note shows literally and writes no markup.
* The section never prints ``undefined``, ``null`` or ``[object Object]``.
* The empty case (no rows at all) keeps its honest empty line.
"""

from __future__ import annotations

import re
import urllib.parse
from unittest.mock import patch

import pytest

import app as app_module
import database
from advisors.overfitting_conscience import run_overfitting_conscience
from tests.ui._iife_node_harness import parse_markup, run_modal_scenario

SYMPHONY = "sym-test"  # the id the ui conftest's bot_state stub resolves to a symphony name
NOTE_KEY = "note"
HOSTILE = "<img src=x onerror=alert(1)>"


def _clear_run() -> dict:
    return {
        "id": 701,
        "symphony_id": SYMPHONY,
        "run_timestamp": "2026-10-29T04:00:00Z",
        "spec_bundle_id": "bundle-clear",
        "n_effective": 100,
        "s_count": 0,
    }


def _breach_run() -> dict:
    return {**_clear_run(), "id": 702, "spec_bundle_id": "bundle-breach", "s_count": 80}


@pytest.fixture(autouse=True)
def _no_developer_env(tmp_path):
    with patch.object(app_module, "ENV_FILE_PATH", str(tmp_path / "no.env")):
        yield


def _route(symphony: str = SYMPHONY) -> str:
    return f"GET /api/symphony-settings/{urllib.parse.quote(symphony, safe='')}"


def _answer(client, symphony: str = SYMPHONY) -> dict:
    response = client.get("/api/symphony-settings/" + urllib.parse.quote(symphony, safe=""))
    assert response.status_code == 200
    return response.get_json()


def _render(body: dict) -> str:
    out = run_modal_scenario(
        {_route(): {"json": body}},
        [
            {"op": "open", "id": SYMPHONY},
            {"op": "settle"},
            {"op": "snapshot", "label": "loaded"},
        ],
    )
    return out["snapshots"]["loaded"]


def _cards(html: str) -> list[str]:
    """The visible text of each advisor card, in order."""
    pieces = re.split(r'(?=<div data-testid="sym-settings-advisor-card")', html)[1:]
    texts = []
    for piece in pieces:
        depth = 0
        end = len(piece)
        for match in re.finditer(r"<(/?)div\b", piece):
            depth += -1 if match.group(1) else 1
            if depth == 0:
                end = match.end()
                break
        # the card closes at the matching </div>; read through its closing tag
        close = piece.find(">", end - 1) + 1
        chunk = piece[:close]
        texts.append(" ".join(re.sub(r"<[^>]+>", " ", chunk).split()))
    return texts


def _section_text(html: str) -> str:
    return " ".join(" ".join(parse_markup(html).texts).split())


class TestRowsWithText:
    def test_a_cleared_row_and_a_breached_row_each_draw_a_card_with_their_verdict_and_prose(
        self, client
    ):
        run_overfitting_conscience(_clear_run(), ledger_rows=[])
        run_overfitting_conscience(_breach_run(), ledger_rows=[])
        body = _answer(client)
        rows = body["advisor_observations"]
        assert [r["verdict"] for r in rows] == ["CLEAR", "BREACH"], "precondition: the route's rows"
        note = rows[0]["raw_response"][NOTE_KEY]
        html = _render(body)
        cards = _cards(html)
        assert len(cards) == 2, (
            f"expected one card per row with something to say, drew {len(cards)}"
        )
        assert "CLEAR" in cards[0].upper() and note in cards[0], cards[0]
        assert "BREACH" in cards[1].upper(), cards[1]

    def test_no_card_is_an_empty_bar(self, client):
        run_overfitting_conscience(_clear_run(), ledger_rows=[])
        run_overfitting_conscience(_breach_run(), ledger_rows=[])
        cards = _cards(_render(_answer(client)))
        assert cards, "the section drew no card at all"
        empty = [i for i, text in enumerate(cards) if not text.strip()]
        assert not empty, f"cards {empty} hold no text"

    def test_the_section_never_prints_a_placeholder_word(self, client):
        run_overfitting_conscience(_clear_run(), ledger_rows=[])
        run_overfitting_conscience(_breach_run(), ledger_rows=[])
        text = _section_text(_render(_answer(client)))
        for bad in ("undefined", "null", "[object Object]", "NaN"):
            assert bad not in text, f"the modal prints {bad!r}"

    def test_a_hostile_note_shows_literally_and_writes_no_markup(self, client):
        database.insert_advisor_observation(
            advisor_role="OVERFITTING_CONSCIENCE",
            subject_type="autotune_run",
            subject_id="703",
            verdict="WATCH",
            raw_response={NOTE_KEY: HOSTILE},
            symphony_id=SYMPHONY,
        )
        html = _render(_answer(client))
        assert "<img" not in html, "the note was written as markup"
        assert HOSTILE in _section_text(html), "the note does not show as text"


class TestRowsWithNothingToSay:
    def _empty_row(self):
        # Synthetic: the real writer, a row with no verdict and an empty body.
        database.insert_advisor_observation(
            advisor_role="SPEC_CRITIC",
            subject_type="spec_bundle",
            subject_id="bundle-empty",
            verdict=None,
            raw_response=None,
            symphony_id=SYMPHONY,
        )

    def test_a_row_with_no_verdict_and_no_prose_draws_no_card(self, client):
        self._empty_row()
        body = _answer(client)
        assert len(body["advisor_observations"]) == 1, "precondition: the route returns the row"
        assert _cards(_render(body)) == []

    def test_when_no_row_has_anything_to_say_the_section_says_so_in_words(self, client):
        self._empty_row()
        text = _section_text(_render(_answer(client)))
        assert re.search(
            r"no (advisor )?(suggestions|observations|notes|findings|detail)", text, re.I
        ), "the section drew nothing and said nothing"

    def test_an_empty_card_never_sits_beside_a_real_one(self, client):
        run_overfitting_conscience(_clear_run(), ledger_rows=[])
        self._empty_row()
        cards = _cards(_render(_answer(client)))
        assert len(cards) == 1 and "CLEAR" in cards[0].upper(), cards


class TestRowsThatSayTheSameThingAreOneRowWithACount:
    """Weekly runs store the same verdict and note again and again; the modal states it once.

    A card is one distinct (role, verdict, note) with how many stored observations say it, so a
    symphony with many runs does not draw the same sentence a dozen times.
    """

    def test_identical_observations_draw_one_card_with_their_count_and_the_note_once(self, client):
        for _ in range(5):
            run_overfitting_conscience(_clear_run(), ledger_rows=[])
        body = _answer(client)
        rows = body["advisor_observations"]
        assert len(rows) == 5, "precondition: five stored observations"
        note = rows[0]["raw_response"][NOTE_KEY]
        html = _render(body)
        cards = _cards(html)
        assert len(cards) == 1, f"five identical observations drew {len(cards)} cards"
        assert "5 observations" in cards[0], cards[0]
        assert _section_text(html).count(note) == 1, "the note is stated once"

    def test_a_different_verdict_is_its_own_card_in_first_seen_order(self, client):
        run_overfitting_conscience(_clear_run(), ledger_rows=[])
        run_overfitting_conscience(_breach_run(), ledger_rows=[])
        run_overfitting_conscience(_clear_run(), ledger_rows=[])
        cards = _cards(_render(_answer(client)))
        assert len(cards) == 2
        assert "CLEAR" in cards[0].upper() and "2 observations" in cards[0], cards[0]
        assert "BREACH" in cards[1].upper() and "observations" not in cards[1], cards[1]

    def test_a_card_for_one_observation_shows_no_count(self, client):
        run_overfitting_conscience(_clear_run(), ledger_rows=[])
        cards = _cards(_render(_answer(client)))
        assert len(cards) == 1 and "observations" not in cards[0], cards


class TestNoRowsAtAll:
    def test_the_empty_line_is_kept_and_no_card_is_drawn(self, client):
        body = _answer(client)
        assert body["advisor_observations"] == []
        html = _render(body)
        assert _cards(html) == []
        assert "No advisor suggestions for this symphony yet." in _section_text(html)
