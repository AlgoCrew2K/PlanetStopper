"""The Preconditions panel's note states the sample its Shadow verdicts are computed over.

``analytics.get_shadow_current_return_daily_series`` concatenates every position epoch of a symphony's
shadow history (the series is not scoped to the position currently open), and the table's N on the
live data is the whole series since the shadow history began. The note said Shadow is the if-held
series "since the current position was opened", a basis the verdicts do not have.

Real call path: the real ``GET /performance``. The note is the caption of the preconditions panel.

Contract pinned
* The note says the Shadow sample covers all position epochs.
* The note does not say the Shadow sample starts at the current position.
* The note still defines Replay (the simulated if-held series) so the two samples stay told apart.
* The analytics function the note describes says the same thing in its docstring, so the sentence and
  the code cannot drift apart unseen.
"""

from __future__ import annotations

import re

import analytics
from tests.dashboard._index_js_harness import find_all, parse_html, tree_text
from tests.ui._page_js_harness import render_page


def _note() -> str:
    tree = parse_html(render_page("/performance"))
    panels = find_all(
        tree, lambda n: n["a"].get("data-testid") == "guard-alpha-preconditions-panel"
    )
    assert panels, "the render holds no preconditions panel"
    captions = find_all(panels[0], lambda n: "metrics-caption" in n["a"].get("class", "").split())
    assert captions, "the preconditions panel has no caption"
    return " ".join(tree_text(captions[0]).split())


def test_the_note_says_the_shadow_sample_covers_all_position_epochs():
    note = _note()
    assert re.search(r"all\s+position\s+epochs", note, re.I), note


def test_the_note_does_not_say_the_sample_starts_at_the_current_position():
    note = _note()
    assert not re.search(r"current\s+position", note, re.I), note
    assert not re.search(r"since\s+the\s+(current\s+)?position", note, re.I), note


def test_the_note_still_tells_replay_from_shadow():
    note = _note()
    assert re.search(r"replay", note, re.I) and re.search(r"shadow", note, re.I), note
    assert re.search(r"simulated", note, re.I), "the note no longer says Replay is simulated"


def test_the_function_the_note_describes_says_the_same_in_its_docstring():
    doc = analytics.get_shadow_current_return_daily_series.__doc__ or ""
    assert re.search(r"all\s+position\s+epochs|every\s+position\s+epoch", doc, re.I), doc[:400]
