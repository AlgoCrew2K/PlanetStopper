"""A Strategy Builder card labels its figures as backtest figures, with no window invented.

A survivor card printed "Sharpe 0.042" beside "Annual Return 6.56%" with nothing saying that both come
from a backtest, or on what basis the Sharpe is stated; the only mention sat at the card foot, as the
caveat "Selected on backtest". Frontrunner cards got a label beside their metric table
(tests/ui/test_frontrunner_card_labels_its_figures_as_backtest.py); the Strategy Builder tab's persisted
cards did not.

Real call path: the real ``GET /ai-advisor`` over observations written through
``database.insert_advisor_observation`` (the shape ``strategy_builder_engine`` persists; the figures are
synthetic and labelled so). Each card is read from the server render as a tree.

Contract pinned
* Each card, survivor and withheld, carries a label that says its figures are backtest figures, beside its
  metric table: inside the table (caption, header or footer) or in the element directly before or after it.
  A banner elsewhere on the tab, or the word at the card foot, does not count.
* The label does not invent a window: no date and no span of years or days appears in it, since the
  stored row holds none.
* The Sharpe cell still prints the stored figure to three decimals, so the label is not bought by
  re-scaling the number (an unlabelled annualisation).
* Stored text is escaped: a candidate id that is markup writes no markup.
"""

from __future__ import annotations

import re

import pytest

import database
from tests.dashboard._index_js_harness import find_all, parse_html, tree_text
from tests.ui._page_js_harness import render_page
from tests.ui._shell_support import seed_dashboard_state

# Synthetic: the keys strategy_builder_engine persists on a survivor and on a withheld candidate.
SHARPE = 0.0421
FIGURES = {
    "sharpe": SHARPE,
    "cagr": 0.0656,
    "calmar": 0.31,
    "max_drawdown": -0.2121,
    "template_id": "built-new",
}
KINDS = {
    "survivor": ("ADOPT_CANDIDATE", {**FIGURES, "candidate_hash": "sbcardlabel00001"}),
    "withheld": (
        "REJECT_VETO_FAILED",
        {**FIGURES, "candidate_hash": "sbcardlabel00002", "rejection_reason": "pbo_veto"},
    ),
}
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
SPAN = re.compile(r"\b\d+(\.\d+)?\s*(years?|yrs?|months?|days?|trading days)\b", re.I)


def _seed(kind: str, subject_id: str = "sb-card-label-subject") -> None:
    seed_dashboard_state()
    verdict, raw_response = KINDS[kind]
    database.insert_advisor_observation(
        advisor_role="STRATEGY_BUILDER",
        subject_type="strategy_builder",
        subject_id=subject_id,
        verdict=verdict,
        raw_response=raw_response,
        symphony_id="",
    )


def _tables(html: str | None = None) -> tuple[dict, list[dict]]:
    tree = parse_html(html or render_page("/ai-advisor"))
    panels = find_all(tree, lambda n: n["a"].get("id") == "tab-panel-strategy-builder")
    assert panels, "the render holds no Strategy Builder tab"
    tables = find_all(panels[0], lambda n: n["a"].get("data-testid") == "stats-table")
    assert tables, "the Strategy Builder tab holds no metric table for the seeded observation"
    return panels[0], tables


def _parent_of(root, target):
    for node in find_all(root, lambda n: target in n["c"]):
        return node
    return None


def _text(node) -> str:
    return " ".join(tree_text(node).split())


def _labels_near(panel: dict, table: dict) -> list[str]:
    near = [_text(table)]
    parent = _parent_of(panel, table)
    assert parent is not None
    elements = [c for c in parent["c"] if isinstance(c, dict)]
    at = elements.index(table)
    for neighbour in (at - 1, at + 1):
        if 0 <= neighbour < len(elements):
            near.append(_text(elements[neighbour]))
    return near


def _says_backtest(text: str) -> bool:
    return bool(re.search(r"back-?test", text, re.I))


@pytest.mark.parametrize("kind", list(KINDS))
def test_each_card_says_its_figures_are_backtest_figures_beside_its_table(kind):
    _seed(kind)
    panel, tables = _tables()
    for table in tables:
        near = _labels_near(panel, table)
        assert any(_says_backtest(t) for t in near), (
            f"{kind}: nothing in or beside the metric table says the figures are a backtest: {near}"
        )


@pytest.mark.parametrize("kind", list(KINDS))
def test_the_label_invents_no_window(kind):
    _seed(kind)
    panel, tables = _tables()
    for table in tables:
        labels = [t for t in _labels_near(panel, table) if _says_backtest(t)]
        assert labels, "precondition: the label exists"
        for text in labels:
            assert not ISO_DATE.search(text), (
                f"{kind}: a date appears with no stored window: {text}"
            )
            assert not SPAN.search(text), f"{kind}: a span appears with no stored window: {text}"


@pytest.mark.parametrize("kind", list(KINDS))
def test_the_sharpe_cell_still_prints_the_stored_figure_to_three_decimals(kind):
    _seed(kind)
    _panel, tables = _tables()
    rows = find_all(tables[0], lambda n: n["t"] == "tr")
    sharpe_rows = [r for r in rows if _text(r).lower().startswith("sharpe")]
    assert sharpe_rows, "the table has no Sharpe row"
    assert f"{SHARPE:.3f}" in _text(sharpe_rows[0]), _text(sharpe_rows[0])


def test_a_candidate_id_that_is_markup_writes_no_markup():
    hostile = "<img src=x onerror=alert(1)>"
    _seed("survivor", subject_id=hostile)
    assert hostile not in render_page("/ai-advisor")
