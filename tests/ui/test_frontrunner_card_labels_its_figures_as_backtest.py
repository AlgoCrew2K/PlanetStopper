"""A Frontrunner approval card labels its figures as backtest figures, with their window where one is stored.

Real call path: the real ``GET /ai-advisor`` over a pending proposal written through
``database.insert_frontrunner_proposal`` (the captured incumbent tree, synthetic metrics labelled so).
The card is read from the server render as a tree (tests/dashboard/_index_js_harness.parse_html).

Why: the table shows Calmar, CAGR and Max DD for the incumbent and the candidate (a CAGR of 152% under
the name of a live symphony), and Approve creates a real Composer symphony. Nothing said the figures
come from a backtest or over what period, so they read as what the symphony earned.

Contract pinned
* Each proposal card carries a label that says its figures are backtest figures, beside its metric
  table: inside the table (caption, header or footer) or in the element directly before or after the
  table. A banner elsewhere on the tab, or the word in a place far from the table, does not count.
* The label is drawn for both proposal sources the tab lists (the Frontrunner build and the Strategy
  Builder retrofit), because both tables hold backtest figures.
* Where the stored metrics carry the backtest window (``backtest_start`` and ``backtest_end``, ISO
  dates), the label names both dates.
* Where they carry none (every proposal stored today), the label still says backtest and prints no
  date and no span of years or days: no window is invented.
* Stored text is escaped: a window date that is markup writes no markup.
"""

from __future__ import annotations

import json
import re

import pytest

import database
from tests.dashboard._index_js_harness import find_all, parse_html, tree_text
from tests.ui._page_js_harness import render_page
from tests.ui._shell_support import (
    _FR_INCUMBENT_HASH,
    _FR_METRICS,
    _REAL_INCUMBENT_TREE,
    seed_dashboard_state,
)

# Synthetic: the shape strategy_builder_engine persists for a retrofit candidate (no incumbent keys).
RETROFIT_METRICS = {"cagr": 0.19, "sharpe": 1.55, "calmar": 1.9, "max_drawdown": -0.10}
WINDOW = {"backtest_start": "2019-03-04", "backtest_end": "2026-10-23"}
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
SPAN = re.compile(r"\b\d+(\.\d+)?\s*(years?|yrs?|months?|days?|trading days)\b", re.I)

SOURCES = {
    "frontrunner_builder": _FR_METRICS,
    "strategy_builder_retrofit": RETROFIT_METRICS,
}


def _seed(source: str, extra: dict | None = None) -> int:
    seed_dashboard_state()
    return database.insert_frontrunner_proposal(
        symphony_id=_FR_INCUMBENT_HASH,
        proposal_source=source,
        candidate_tree=json.loads(_REAL_INCUMBENT_TREE.read_text(encoding="utf-8")),
        metrics_json={**SOURCES[source], **(extra or {})},
    )


def _card_tree(proposal_id: int):
    tree = parse_html(render_page("/ai-advisor"))
    cards = find_all(tree, lambda n: n["a"].get("id") == f"fr-card-{proposal_id}")
    assert cards, f"the render holds no card for proposal {proposal_id}"
    return cards[0]


def _parent_of(root, target):
    for node in find_all(root, lambda n: target in n["c"]):
        return node
    return None


def _text(node) -> str:
    return " ".join(tree_text(node).split())


def _labels_near_the_table(card) -> list[str]:
    """The texts of the elements that are in, or directly before or after, the metric table."""
    tables = find_all(card, lambda n: n["a"].get("data-testid") == "stats-table")
    assert tables, "the card holds no metric table"
    table = tables[0]
    near = [_text(table)]
    parent = _parent_of(card, table)
    assert parent is not None
    elements = [c for c in parent["c"] if isinstance(c, dict)]
    at = elements.index(table)
    for neighbour in (at - 1, at + 1):
        if 0 <= neighbour < len(elements):
            near.append(_text(elements[neighbour]))
    return near


def _says_backtest(text: str) -> bool:
    return bool(re.search(r"back-?test", text, re.I))


@pytest.mark.parametrize("source", list(SOURCES))
def test_each_card_says_its_figures_are_backtest_figures_beside_its_table(source):
    card = _card_tree(_seed(source))
    near = _labels_near_the_table(card)
    assert any(_says_backtest(t) for t in near), (
        f"{source}: nothing in or beside the metric table says the figures are a backtest: {near}"
    )


@pytest.mark.parametrize("source", list(SOURCES))
def test_a_stored_window_is_named_in_the_label(source):
    card = _card_tree(_seed(source, WINDOW))
    near = " | ".join(_labels_near_the_table(card))
    assert _says_backtest(near)
    for key, date in WINDOW.items():
        assert date in near, f"{source}: the stored {key} {date} is not shown beside the table: {near}"


@pytest.mark.parametrize("source", list(SOURCES))
def test_with_no_stored_window_the_label_invents_none(source):
    card = _card_tree(_seed(source))
    near = [t for t in _labels_near_the_table(card) if _says_backtest(t)]
    assert near, "precondition: the label exists"
    for text in near:
        assert not ISO_DATE.search(text), f"{source}: a date appears with no stored window: {text}"
        assert not SPAN.search(text), f"{source}: a span appears with no stored window: {text}"


def test_a_window_that_is_markup_writes_no_markup():
    hostile = {"backtest_start": "<img src=x onerror=alert(1)>", "backtest_end": "2026-10-23"}
    _seed("frontrunner_builder", hostile)
    assert "<img src=x onerror=alert(1)>" not in render_page("/ai-advisor")
