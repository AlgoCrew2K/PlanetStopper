"""The History exits table fits a phone without splitting a word, in a real browser.

Browser-only (marker ``browser``; run with ``python -m pytest -m browser -n 0
tests/ui/test_history_exits_table_fit_browser.py``). Firefox headless through Playwright; the app in
this process on the per-test database, seeded through the real writers from captured post-mortem
days (tests/ui/_shell_support.seed_history_exits). Never skips.

The table is drilled to one day's exits, as an operator does with a tap on the daily chart. Two data
sets are shown: the captured exits as the writers stored them, and the same exits with the names and
reasons a real account holds (long symphony names, a reason of several words), set on the
``/api/history`` answer by the browser route, since the captured days hold shorter ones.

Layout facts, asserted on the live render at 375 and 320 px:
* the page does not scroll sideways;
* all four columns are on screen (each header cell lies inside the viewport);
* no cell's content is wider than the cell;
* no word is split across lines. A word is a run of characters between spaces and hyphens: a line
  may end at a space or after a hyphen, never inside a run. Checked by measuring each run with a
  range: a run on two lines has client rectangles at two heights.
"""

from __future__ import annotations

import pytest

from tests.ui._shell_support import (  # noqa: F401  (fixtures)
    NARROW,
    VERY_NARROW,
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    firefox,
    open_page,
    open_probe,
    seed_dashboard_state,
    seed_history_exits,
    wait_until,
)

pytestmark = pytest.mark.browser

REAL_LENGTH_NAMES = [
    "Planet of the Reasonabilists: Era of the",
    "Land of Feaver'd Allocations: Intelligent Novae",
    "(INVEST) Planet of Hunted Cascades - Land of Plenty",
    "Breakdown Watch",
]
REAL_LENGTH_REASONS = ["Breakdown", "VWAP Breakdown", "Trailing Stop", "Take-Profit"]
OVERFLOW_TOLERANCE_PX = 1

_TABLE_FACTS = """
() => {
  const table = document.querySelector('table.triggers-tbl');
  const cells = [...table.querySelectorAll('th, td')];
  const overflowing = cells
    .filter((c) => c.scrollWidth > c.clientWidth + 1)
    .map((c) => [c.textContent.trim().slice(0, 40), c.scrollWidth, c.clientWidth]);
  const split = [];
  for (const cell of cells) {
    const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const text = node.nodeValue;
      const run = /[^\\s-]+/g;
      for (let m = run.exec(text); m; m = run.exec(text)) {
        const range = document.createRange();
        range.setStart(node, m.index);
        range.setEnd(node, m.index + m[0].length);
        const tops = new Set([...range.getClientRects()].map((r) => Math.round(r.top)));
        if (tops.size > 1) split.push(m[0]);
      }
    }
  }
  const headers = [...table.querySelectorAll('thead th')].map((th) => {
    const r = th.getBoundingClientRect();
    return { text: th.textContent.trim(), left: r.left, right: r.right, width: r.width };
  });
  return {
    pageWidth: document.documentElement.scrollWidth,
    viewport: innerWidth,
    rows: table.querySelectorAll('tbody tr').length,
    overflowing,
    split,
    headers,
  };
}
"""


def _rewrite_exits(route) -> None:
    """Answer ``/api/history`` with the app's own answer, the exits renamed to real-length values."""
    response = route.fetch()
    body = response.json()
    lists = [body.get("todays_exits") or []] + list((body.get("daily_exits") or {}).values())
    for exits in lists:
        for index, record in enumerate(exits):
            record["symphony_name"] = REAL_LENGTH_NAMES[index % len(REAL_LENGTH_NAMES)]
            record["reason"] = REAL_LENGTH_REASONS[index % len(REAL_LENGTH_REASONS)]
    route.fulfill(response=response, json=body)


def _drilled_table_facts(
    firefox, advisor_server, tmp_path, monkeypatch, size, *, real_length: bool
):  # noqa: F811
    seed_dashboard_state()
    seed_history_exits(tmp_path, monkeypatch.setattr)
    with open_probe(firefox, advisor_server, width=size[0], height=size[1]) as probe:
        if real_length:
            probe.page.route("**/api/history/*", _rewrite_exits)
        open_page(probe, "/history")
        probe.page.click('#window-picker button[data-window="1825"]')
        days = probe.page.evaluate(
            "() => fetch('/api/history/1825').then((r) => r.json())"
            ".then((p) => Object.keys(p.daily_exits || {}).filter((d) => p.daily_exits[d].length))"
        )
        assert days, "premise: the seeded post-mortems hold at least one day with exits"
        wait_until(
            probe,
            "(d) => !!document.querySelector('#daily-chart-svg rect[data-date=\"' + d + '\"]')",
            "the daily chart never drew the day with exits",
            arg=days[0],
        )
        probe.page.locator(f'#daily-chart-svg rect[data-date="{days[0]}"]').dispatch_event("click")
        wait_until(
            probe,
            "() => document.querySelectorAll('#triggers-tbody tr').length > 1",
            "the day's exits never filled the table",
        )
        if real_length:
            wait_until(
                probe,
                "(names) => names.some((n) => document.querySelector('#triggers-tbody').textContent.includes(n))",
                "the table never showed the real-length names",
                arg=REAL_LENGTH_NAMES,
            )
        return probe.page.evaluate(_TABLE_FACTS)


@pytest.mark.parametrize("real_length", [False, True], ids=["captured-exits", "real-length-exits"])
@pytest.mark.parametrize("size", [NARROW, VERY_NARROW], ids=["375", "320"])
def test_the_drilled_exits_table_fits_the_screen_and_splits_no_word(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    tmp_path,
    monkeypatch,
    size,
    real_length,
):
    facts = _drilled_table_facts(
        firefox, advisor_server, tmp_path, monkeypatch, size, real_length=real_length
    )
    where = f"{size[0]} px, {'real-length' if real_length else 'captured'} exits"
    assert facts["rows"] > 1, f"{where}: premise, the table holds the day's exits"
    assert facts["pageWidth"] <= facts["viewport"] + OVERFLOW_TOLERANCE_PX, (
        f"{where}: the page scrolls sideways ({facts['pageWidth']} > {facts['viewport']})"
    )
    assert [h["text"] for h in facts["headers"]] == ["Time", "Symphony", "Reason", "Detail"]
    for header in facts["headers"]:
        assert header["left"] >= -0.5 and header["right"] <= facts["viewport"] + 0.5, (
            f"{where}: the {header['text']} column is not on screen "
            f"({header['left']:.0f} to {header['right']:.0f} of {facts['viewport']})"
        )
        assert header["width"] > 0, f"{where}: the {header['text']} column has no width"
    assert facts["overflowing"] == [], (
        f"{where}: cell content wider than its cell: {facts['overflowing']}"
    )
    assert facts["split"] == [], f"{where}: words split across lines: {facts['split']}"
