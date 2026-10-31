"""What the shell work compares with its base: the dashboard's markup outside the header, and
the 1440 px header height and table scroll bars of every page.

TEST-ONLY. Used by ``tests/fixtures/dashboard/shell_baseline/capture_shell_baseline.py`` (which
writes the base capture before any edit of the plan) and by the tests that read it back
(``tests/ui/test_dashboard_primitives_shared.py``, ``tests/ui/test_shell_header_browser.py`` and
``tests/ui/test_narrow_close_browser.py``), so both sides go through one implementation.

* ``body_structure_outside_top_bar(html)``: ``[[tag, [class, ...]], ...]`` for every body element
  of the dashboard's markup except the shared header subtree (``data-testid="top-bar"``): the
  header changes by design (it wraps), everything else on ``/`` must not.
* ``TABLE_OVERFLOW_JS``: for each ``<table>`` on the page, in document order, whether the nearest
  scrolling ancestor (or the table itself when none scrolls) is wider inside than its box, so
  "a table gained a scroll bar it did not have" is a per-table comparison.
"""

from __future__ import annotations

from html.parser import HTMLParser

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source"}
TOP_BAR_TESTID = "top-bar"


class _OutsideTopBar(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_body = False
        self.skip_depth = 0
        self.skip_tag = ""
        self.structure: list[list] = []

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag == "body":
            self.in_body = True
            return
        if not self.in_body:
            return
        if self.skip_depth:
            if tag == self.skip_tag and tag not in _VOID:
                self.skip_depth += 1
            return
        if attr.get("data-testid") == TOP_BAR_TESTID:
            self.skip_depth, self.skip_tag = 1, tag
            return
        self.structure.append([tag, sorted((attr.get("class") or "").split())])

    def handle_endtag(self, tag):
        if self.skip_depth and tag == self.skip_tag:
            self.skip_depth -= 1


def body_structure_outside_top_bar(html: str) -> list[list]:
    parser = _OutsideTopBar()
    parser.feed(html)
    parser.close()
    return parser.structure


TABLE_OVERFLOW_JS = """
() => [...document.querySelectorAll('table')].map((table, index) => {
  let box = table;
  for (let el = table; el && el !== document.body; el = el.parentElement) {
    const ox = getComputedStyle(el).overflowX;
    if (ox === 'auto' || ox === 'scroll' || ox === 'hidden') { box = el; break; }
  }
  return {
    index,
    testid: table.getAttribute('data-testid') || '',
    cls: table.className || '',
    box_scrolls: box.scrollWidth > box.clientWidth + 1,
    table_wider_than_viewport: table.getBoundingClientRect().right > innerWidth + 1,
  };
})
"""

NAV_HEIGHT_JS = (
    "() => document.querySelector('[data-testid=\"studio-nav\"]').getBoundingClientRect().height"
)
