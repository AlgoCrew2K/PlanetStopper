"""The guard headline's number and its unit stay on one line at every viewport (marker ``browser``).

Run with ``python -m pytest -m browser -n 0 tests/ui/test_dashboard_headline_one_line_browser.py``.
Firefox headless through Playwright; the page and its API answers are the harness world's
(tests/ui/_dashboard_refresh_browser_support.py). Never skips.

Contract pinned: in the hero block the headline ``#guard-alpha-headline`` (a signed number
then ``%``) is laid out as one line at desktop widths, at widths between the page's breakpoints and
on a phone, for a negative and a positive figure, both for the figure the poll writes and for any
figure written into the element. Keeping the headline whole must not push the window picker onto the
chart column, so the picker's box never overlaps the chart column's box at any of those widths.
Only layout facts are asserted (how many lines the text occupies, that the box holds its text, that
the headline stays inside the viewport, that two boxes do not overlap); no size or colour is.
"""

from __future__ import annotations

import pytest

from tests.dashboard import _refresh_timing_support as sup
from tests.ui._browser_server import running_server
from tests.ui._dashboard_refresh_browser_support import (  # noqa: F401  (fixtures)
    firefox,
    new_probe,
    text,
    wait_for,
    wait_refresh,
    world,
)

pytestmark = pytest.mark.browser

HEIGHT = 900
# 1440, 1366 and 1280 are laptop widths (the picker is squeezed at the narrower ones); 1000 and 701
# sit between the layout's breakpoints (701 is the narrowest two-column hero); 375 and 320 are phones.
WIDTHS = [1440, 1366, 1280, 1000, 701, 375, 320]
# A small negative, a small positive and a figure with more digits (the widest the headline prints).
FIGURES = [-0.73, 0.73, -12.34]

# One task: the text (when given) is written and measured with no page script able to run between.
_LINE_FACTS = """
(written) => {
  const el = document.getElementById('guard-alpha-headline');
  if (written !== null) el.textContent = written;
  const range = document.createRange();
  range.selectNodeContents(el);
  const tops = new Set([...range.getClientRects()].map((r) => Math.round(r.top)));
  const box = el.getBoundingClientRect();
  const content = range.getBoundingClientRect();
  const picker = document.querySelector('[data-testid="window-selector"]').getBoundingClientRect();
  const chart = document.querySelector('[data-testid="hero-right"]').getBoundingClientRect();
  const overlaps = picker.left < chart.right && picker.right > chart.left &&
    picker.top < chart.bottom && picker.bottom > chart.top;
  return {
    lines: tops.size,
    text: el.textContent.replace(/\\s+/g, ' ').trim(),
    boxHoldsText: content.right <= box.right + 1 && content.left >= box.left - 1,
    insideViewport: content.right <= document.documentElement.clientWidth + 1 && content.left >= -1,
    pickerOverlapsChartColumn: overlaps,
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


def _assert_one_line(probe, expected: str, label: str, *, written: str | None = None) -> None:
    facts = probe.page.evaluate(_LINE_FACTS, written)
    assert facts["text"] == expected, f"{label}: the headline reads {facts['text']!r}"
    assert facts["lines"] == 1, (
        f"{label}: the headline {expected!r} is laid out on {facts['lines']} lines"
    )
    assert facts["boxHoldsText"], f"{label}: the headline box does not hold its text"
    assert facts["insideViewport"], f"{label}: the headline runs outside the viewport"
    assert not facts["pickerOverlapsChartColumn"], (
        f"{label}: the window picker overlaps the chart column"
    )


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("figure", FIGURES)
def test_the_headline_the_poll_writes_is_one_line(probe, world, width, figure):
    stamp = sup.render_stamp(world)
    probe.state = sup.with_stamp(world["state"], stamp)
    strip = dict(probe.strips["1y"])
    strip["guard_alpha"] = figure
    probe.strips["1y"] = strip
    probe.page.set_viewport_size({"width": width, "height": HEIGHT})
    probe.page.goto(probe.server.base_url + "/")
    wait_refresh(probe, 1)
    # A DOM click: at the narrow two-column widths the picker's own position is not under test here.
    probe.page.evaluate("document.querySelector('[data-testid=\"window-1y\"]').click()")
    want = sup.fmt_pp(figure)
    wait_for(
        probe,
        "(want) => document.getElementById('guard-alpha-headline').textContent.trim() === want",
        f"the headline never read {want}",
        arg=want,
    )
    assert text(probe, "#guard-alpha-headline") == want
    _assert_one_line(probe, want, f"{width}px poll {figure}")
    assert probe.page_errors == []


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("figure", FIGURES)
def test_the_headline_stays_one_line_for_any_figure_written_into_it(probe, width, figure):
    """The rule is the element's own: the same text, written in place, is one line at every width."""
    probe.page.set_viewport_size({"width": width, "height": HEIGHT})
    probe.page.goto(probe.server.base_url + "/")
    wait_refresh(probe, 1)
    want = sup.fmt_pp(figure)
    _assert_one_line(probe, want, f"{width}px written {figure}", written=want)
    assert probe.page_errors == []


@pytest.mark.parametrize("width", [1440, 1366, 1280])
@pytest.mark.parametrize("figure", FIGURES)
def test_the_headline_stays_one_line_in_a_wide_typeface(probe, width, figure):
    """A wider face makes the headline and the picker take more room, as the live page's does at
    laptop widths. Verdana is wide where it is installed; where it is not the face falls back and
    this case is the plain one."""
    probe.page.set_viewport_size({"width": width, "height": HEIGHT})
    probe.page.goto(probe.server.base_url + "/")
    wait_refresh(probe, 1)
    probe.page.evaluate(
        "() => document.documentElement.style.setProperty('--studio-sans', 'Verdana, sans-serif')"
    )
    want = sup.fmt_pp(figure)
    _assert_one_line(probe, want, f"{width}px wide face {figure}", written=want)
    assert probe.page_errors == []
