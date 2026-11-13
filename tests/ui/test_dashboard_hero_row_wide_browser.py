"""The hero's headline row keeps its wide-desktop arrangement and clears the picker at the
widths between (marker ``browser``).

Run with ``python -m pytest -m browser -n 0 tests/ui/test_dashboard_hero_row_wide_browser.py``.
Firefox headless through Playwright; the page and its API answers are the harness world's
(tests/ui/_dashboard_refresh_browser_support.py). Never skips.

Contract pinned: at 1440, 1920 and 2560 px the guard headline and the window picker share one row
of the hero's top block. The headline starts at the block's left edge, the picker is a single line
of its seven segments that ends at the block's right edge and sits within the headline's vertical
span, the headline's box ends before the picker's begins, and the descriptor starts below both.
This is the look the operator approved; the fix that keeps the headline whole at narrower widths
(tests/ui/test_dashboard_headline_one_line_browser.py) must leave these arrangements as they are.
At 1001 to 1350 px, either side of the 1240 px breakpoint where the picker leaves the headline's
row, the headline's box holds its figure and the picker clears the headline and the chart column.
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


# The widths the headline module does not sample, either side of the desktop breakpoint (1240 px)
# where the picker leaves the headline's row.
BAND_WIDTHS = [1001, 1120, 1239, 1240, 1290, 1350]

_BAND_FACTS = """
(written) => {
  const el = document.getElementById('guard-alpha-headline');
  el.textContent = written;
  const range = document.createRange();
  range.selectNodeContents(el);
  const text = range.getBoundingClientRect();
  const headline = el.getBoundingClientRect();
  const picker = document.querySelector('[data-testid="window-selector"]');
  const p = picker.getBoundingClientRect();
  const chart = document.querySelector('[data-testid="hero-right"]').getBoundingClientRect();
  const clipped = [...picker.querySelectorAll('button')].filter((b) => {
    const r = b.getBoundingClientRect();
    return r.left < p.left - 1 || r.right > p.right + 1 || r.right > document.documentElement.clientWidth;
  }).length;
  const apart = (a, b) => a.right <= b.left + 1 || a.left >= b.right - 1 || a.bottom <= b.top + 1 || a.top >= b.bottom - 1;
  return {
    holds: text.right <= headline.right + 1 && text.left >= headline.left - 1,
    clipped,
    pickerApartFromHeadline: apart(p, headline),
    pickerApartFromChart: apart(p, chart),
  };
}
"""


@pytest.mark.parametrize("width", BAND_WIDTHS)
@pytest.mark.parametrize("face", ["default", "wide"])
def test_the_headline_holds_its_figure_and_the_picker_clears_it_around_the_desktop_breakpoint(
    probe, width, face
):
    probe.page.set_viewport_size({"width": width, "height": HEIGHT})
    probe.page.goto(probe.server.base_url + "/")
    wait_refresh(probe, 1)
    if face == "wide":
        probe.page.evaluate(
            "() => document.documentElement.style.setProperty('--studio-sans', 'Verdana, sans-serif')"
        )
    facts = probe.page.evaluate(_BAND_FACTS, "-12.34%")
    where = f"{width}px {face} face"
    assert facts["holds"], f"{where}: the headline box does not hold its text"
    assert facts["clipped"] == 0, f"{where}: {facts['clipped']} picker segments overflow its box"
    assert facts["pickerApartFromHeadline"], f"{where}: the picker overlaps the headline's box"
    assert facts["pickerApartFromChart"], f"{where}: the picker overlaps the chart column"
    assert probe.page_errors == []


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
