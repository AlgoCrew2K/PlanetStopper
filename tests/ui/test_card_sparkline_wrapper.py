"""The box around a card's sparkline is a fixed-height box, and the session caption is not in it.

Chart.js sizes a responsive canvas to its parent. A parent that has only a minimum height, and that
also holds the session caption, grows with the caption on every resize pass. These pins are the
default-run counterpart of tests/ui/test_card_sparkline_height_browser.py (browser modules do not run
in CI).

* Served HTML: the real ``GET /`` render of a world with both an active card and a standby card; the
  parent of every ``card-spark`` canvas states a height and no minimum height, in both card sections.
* Page script: ``static/index.js`` runs unmodified under node over that page, with a tape that names a
  session other than the ET day; the caption span is in the card and is not a descendant of the
  canvas's parent.
"""

from __future__ import annotations

import copy
import re

import pytest

from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup

WORLD = "review-fixes"
SPARK_HEIGHT_PX = 64
PREVIOUS_SESSION = "2000-01-03"


def _wrappers_in(tree: dict, section_testid: str) -> list[dict]:
    section = hx.find_all(tree, lambda n: n["a"].get("data-testid") == section_testid)
    assert section, f"the served page has no {section_testid}"
    canvases = hx.find_all(
        section[0], lambda n: n["t"] == "canvas" and n["a"].get("data-testid") == "card-spark"
    )
    assert canvases, f"{section_testid} holds no sparkline canvas"
    parents = hx.find_all(
        section[0], lambda n: any(c in canvases for c in n["c"] if not isinstance(c, str))
    )
    assert len(parents) == len(canvases), "each canvas has its own parent"
    return parents


def _style(node: dict) -> str:
    return re.sub(r"\s+", "", node["a"].get("style", "")).lower()


@pytest.mark.parametrize("section", ["active-section", "standby-section"])
def test_the_box_around_each_sparkline_has_a_fixed_height_and_no_minimum_height(section):
    tree = hx.parse_html(hx.get_world(WORLD)["html"])
    for wrapper in _wrappers_in(tree, section):
        style = _style(wrapper)
        assert "min-height" not in style, (
            f"{section}: the sparkline's parent is only a minimum: {style}"
        )
        assert re.search(rf"(^|;)height:{SPARK_HEIGHT_PX}px(;|$)", style), (
            f"{section}: the sparkline's parent does not state a {SPARK_HEIGHT_PX}px height: {style}"
        )


CAPTION_SCENARIO = r"""
await H.boot({ extras: true, charts: H.bodies.charts, state: H.bodies.same });
const D = H.sandbox.document;
const canvas = D.querySelector('[data-testid="card-spark"][data-sym-id="' + __ID__ + '"]');
let card = canvas;
while (card && card.getAttribute('data-testid') !== 'sym-card') card = card.parentNode;
const wrapper = canvas.parentNode;
const caption = card ? card.querySelector('.spark-session') : null;
return {
  hasCard: !!card,
  captionInCard: !!caption,
  captionText: caption ? caption.textContent.trim() : null,
  captionInWrapper: !!wrapper.querySelector('.spark-session'),
  captionParentIsWrapper: !!caption && caption.parentNode === wrapper,
};
"""


def test_the_session_caption_is_in_the_card_and_not_in_the_canvas_parent():
    world = hx.get_world("default")
    today = world["seeded_today"]
    chart = copy.deepcopy(world["charts"][hx.UNTRIGGERED_ID])
    chart["session_date"], chart["et_date"] = PREVIOUS_SESSION, today
    result = sup.run(
        CAPTION_SCENARIO.replace("__ID__", repr(hx.UNTRIGGERED_ID)),
        world="default",
        scripts=sup.INDEX_ONLY,
        extra_bodies={
            "charts": {**world["charts"], hx.UNTRIGGERED_ID: chart},
            "same": sup.with_stamp(world["state"], sup.render_stamp(world)),
        },
    )
    assert result["hasCard"], "premise: the canvas is inside a sym-card"
    assert result["captionInCard"], "premise: the other session's tape is captioned in its card"
    assert result["captionText"] == f"{PREVIOUS_SESSION} session"
    assert not result["captionInWrapper"], "the caption is inside the box the chart sizes itself to"
    assert not result["captionParentIsWrapper"]
