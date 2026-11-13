"""The hero's headline row keeps its wide-desktop arrangement (marker ``browser``).

Run with ``python -m pytest -m browser -n 0 tests/ui/test_dashboard_hero_row_wide_browser.py``.
Firefox headless through Playwright; the page and its API answers are the harness world's
(tests/ui/_dashboard_refresh_browser_support.py). Never skips.

Contract pinned: at 1440, 1920 and 2560 px the guard headline and the window picker share one row
of the hero's top block. The headline starts at the block's left edge, the picker is a single line
of its seven segments that ends at the block's right edge and sits within the headline's vertical
span, the headline's box ends before the picker's begins, and the descriptor starts below both.
This is the look the operator approved; the fix that keeps the headline whole at narrower widths
(tests/ui/test_dashboard_headline_one_line_browser.py) must leave these arrangements as they are.
Only relations between boxes are asserted (which edge touches which, which box is above which, how
many lines a control occupies), so the guard does not depend on a typeface or a pixel value.
"""

from __future__ import annotations

import pytest

from tests.ui._browser_server import running_server
from tests.ui._dashboard_refresh_browser_support import (  # noqa: F401  (fixtures)
    firefox,
    new_probe,
    wait_refresh,
    world,
)

pytestmark = pytest.mark.browser

HEIGHT = 900
WIDTHS = [1440, 1920, 2560]
TOLERANCE = 2

_FACTS = """
() => {
  const box = (e) => {
    const b = e.getBoundingClientRect();
    return { left: b.left, right: b.right, top: b.top, bottom: b.bottom };
  };
  const q = (s) => document.querySelector(s);
  const picker = q('[data-testid="window-selector"]');
  const segmentTops = [...picker.querySelectorAll('button')].map((b) => Math.round(b.getBoundingClientRect().top));
  return {
    row: box(q('.hero-top')),
    headline: box(q('#guard-alpha-headline')),
    picker: box(picker),
    descriptor: box(q('[data-testid="guard-alpha-descriptor"]')),
    segmentCount: segmentTops.length,
    segmentRows: new Set(segmentTops).size,
  };
}
"""


@pytest.fixture
def probe(firefox, world):  # noqa: F811
    with running_server() as server:
        probe = new_probe(firefox, server, world)
        try:
            yield probe
        finally:
            probe.context.close()


@pytest.mark.parametrize("width", WIDTHS)
def test_the_headline_and_the_window_picker_share_one_row_of_the_hero(probe, width):
    probe.page.set_viewport_size({"width": width, "height": HEIGHT})
    probe.page.goto(probe.server.base_url + "/")
    wait_refresh(probe, 1)
    facts = probe.page.evaluate(_FACTS)
    row, headline, picker, descriptor = (
        facts["row"],
        facts["headline"],
        facts["picker"],
        facts["descriptor"],
    )
    where = f"{width}px"
    assert abs(headline["left"] - row["left"]) <= TOLERANCE, (
        f"{where}: the headline no longer starts at the hero block's left edge"
    )
    assert abs(picker["right"] - row["right"]) <= TOLERANCE, (
        f"{where}: the window picker no longer ends at the hero block's right edge"
    )
    assert headline["right"] <= picker["left"] + TOLERANCE, (
        f"{where}: the headline's box runs under the window picker's"
    )
    assert picker["top"] >= headline["top"] - TOLERANCE, (
        f"{where}: the window picker starts above the headline's row"
    )
    assert picker["bottom"] <= headline["bottom"] + TOLERANCE, (
        f"{where}: the window picker hangs below the headline's row"
    )
    assert facts["segmentCount"] == 7 and facts["segmentRows"] == 1, (
        f"{where}: the picker's {facts['segmentCount']} segments sit on {facts['segmentRows']} lines"
    )
    assert descriptor["top"] >= max(headline["bottom"], picker["bottom"]) - TOLERANCE, (
        f"{where}: the descriptor no longer sits under the headline row"
    )
    assert probe.page_errors == []
