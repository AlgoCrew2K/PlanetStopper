"""The Performance page's exit-turnover note says what limits the history in words, not by table name.

The note under "Exit Turnover & Estimated Friction Drag" explained the retained-history days with an
internal table name in code style, which an operator cannot act on or look up.

Real call path: the real ``GET /performance``; the note is the caption of the exit-turnover panel.

Contract pinned
* Neither the note nor any other text of the panel names an internal table or column (a snake_case
  identifier).
* The note still tells the reader that the history behind each count is limited and that each window
  names the days it really covers, and still says the drag is an estimate from a simulated exit cost,
  so the fix removes a name and not the explanation.
"""

from __future__ import annotations

import re

from tests.dashboard._index_js_harness import find_all, parse_html, tree_text
from tests.ui._page_js_harness import render_page
from tests.ui.test_frontrunner_card_labels_its_figures_as_backtest import _text

SNAKE_CASE = re.compile(r"\b[A-Za-z]+(?:_[A-Za-z0-9]+)+\b")
LIMIT_WORDS = re.compile(r"retain|kept|keep|stored|pruned|deleted|removed|older|limited", re.I)


def _panel():
    tree = parse_html(render_page("/performance"))
    panels = find_all(tree, lambda n: n["a"].get("data-testid") == "exit-turnover-panel")
    assert panels, "the render holds no exit-turnover panel"
    return panels[0]


def _note() -> str:
    captions = find_all(_panel(), lambda n: "metrics-caption" in n["a"].get("class", "").split())
    assert captions, "the exit-turnover panel has no caption"
    return _text(captions[0])


def test_the_note_names_no_internal_table():
    note = _note()
    assert not SNAKE_CASE.findall(note), f"the note shows an internal name: {note}"


def test_no_text_of_the_panel_shows_a_snake_case_identifier():
    text = _text(_panel())
    assert not SNAKE_CASE.findall(text), f"the panel shows an internal name: {text}"


def test_the_note_still_says_the_history_is_limited_and_each_window_names_its_days():
    note = _note()
    assert "history" in note.lower() and LIMIT_WORDS.search(note), (
        f"the note no longer says what limits the history: {note}"
    )
    assert re.search(r"actual|days", note, re.I), f"the note no longer ties counts to days: {note}"


def test_the_note_still_says_the_drag_is_an_estimate_from_a_simulated_cost():
    note = _note()
    assert re.search(r"estimat", note, re.I) and re.search(r"simulated", note, re.I), note
