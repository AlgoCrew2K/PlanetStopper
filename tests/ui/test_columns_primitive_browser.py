"""The shared columns primitive lines a table up under its header, from 1001 px up, and only then.

Browser-only (marker ``browser``; run with
``python -m pytest -m browser -n 0 tests/ui/test_columns_primitive_browser.py``).

Each case is a small page built here that inlines the real ``static/tokens.css`` and
``static/layout.css`` (no network) and holds a ``.cols-table`` of ``.cols-row`` rows. The
measures are the ones every host is measured with (``tests/ui/_alignment.py``), so a primitive
that passes here passes the rule the hosts are held to:

* every column's header sits above its cells, every text column shares a left edge and every
  figure column a right edge, whatever the width of a row's content;
* the table sits at the width its content needs and never past its panel, and a long name wraps
  inside its own cell instead of widening the table;
* rows stay one under another;
* at 1000 px and narrower the primitive does nothing: the page keeps its stacked form.
"""

from __future__ import annotations

import pathlib
from contextlib import contextmanager

import pytest

from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _alignment as al
from tests.ui._shell_support import (  # noqa: F401  (fixtures: overrides, empty directory, browser)
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    firefox,
)

pytestmark = pytest.mark.browser

STATIC = pathlib.Path(__file__).resolve().parents[2] / "static"
TOKENS = (STATIC / "tokens.css").read_text(encoding="utf-8")
LAYOUT = (STATIC / "layout.css").read_text(encoding="utf-8")

HEAD = (
    '<div class="cols-row" role="row">'
    '<span role="columnheader">Symphony</span>'
    '<span role="columnheader" class="cols-num">Runs without adopting a tuned config</span>'
    '<span role="columnheader">Outcome</span></div>'
)
ROWS = [
    ("Example Northwind Rotation", "4", "Fallback"),
    ("Short", "12345", "Adopted AI"),
    ("A mid-length symphony name", "0", "Reset to Global Default"),
]
SPEC = {
    "name": "cols",
    "group": ".cols-table",
    "numeric": ["Runs without adopting a tuned config"],
}


def _table(rows=ROWS, cols: int = 3, head: str = HEAD, style: str = "") -> str:
    body = "".join(
        '<div class="cols-row" role="row">'
        f'<span role="cell">{a}</span><span role="cell" class="cols-num">{b}</span>'
        f'<span role="cell">{c}</span></div>'
        for a, b, c in rows
    )
    return (
        f'<div class="panel" style="width:1100px;border:1px solid #999">'
        f'<div class="cols-table" role="table" style="--cols:{cols};{style}">{head}{body}</div></div>'
    )


@contextmanager
def _open(firefox, body: str, width: int):  # noqa: F811
    context = firefox.new_context(viewport=al.viewport(width))
    try:
        page = context.new_page()
        page.set_content(
            "<!doctype html><html><head><style>"
            f"{TOKENS}\n{LAYOUT}\nbody {{ margin: 0; font: 14px/20px sans-serif; }}"
            f"</style></head><body>{body}</body></html>"
        )
        yield page
    finally:
        context.close()


@pytest.mark.parametrize("width", al.WIDTHS)
def test_every_column_lines_up_under_its_header_at_every_measured_width(firefox, width):  # noqa: F811
    with _open(firefox, _table(), width) as page:
        found = al.measure(page, al.COLUMN_EDGES_JS, SPEC)
    assert found["found"] == 1
    assert found["violations"] == [], found["violations"]
    assert found["instances"][0]["body_rows"] == 3 and found["instances"][0]["header_rows"] == 1


@pytest.mark.parametrize("width", [1001, 1440, 2560])
def test_the_columns_sit_at_the_width_their_content_needs(firefox, width):  # noqa: F811
    spec = {"name": "cols", "kind": "table", "group": ".cols-table"}
    with _open(firefox, _table(), width) as page:
        gaps = al.measure(page, al.COLUMN_GAPS_JS, spec)
        widths = page.evaluate(
            "() => [document.querySelector('.cols-table').getBoundingClientRect().width,"
            " document.querySelector('.panel').getBoundingClientRect().width]"
        )
    assert gaps["violations"] == [], gaps["violations"]
    assert widths[0] < widths[1] - 200, f"the table ({widths[0]}) fills its panel ({widths[1]})"


def test_a_long_name_wraps_inside_its_own_cell_and_the_table_never_passes_its_panel(firefox):  # noqa: F811
    rows = [
        ("An extremely long symphony display name that has to wrap " * 4, "7", "Fallback")
    ] + ROWS
    body = _table(rows).replace("width:1100px", "width:520px")
    with _open(firefox, body, 1440) as page:
        found = al.measure(page, al.COLUMN_EDGES_JS, SPEC)
        box = page.evaluate(
            "() => [document.querySelector('.cols-table').getBoundingClientRect().right,"
            " document.querySelector('.panel').getBoundingClientRect().right,"
            " document.documentElement.scrollWidth, innerWidth]"
        )
    assert found["violations"] == [], found["violations"]
    assert box[0] <= box[1] + 1, "the table is wider than its panel"
    assert box[2] <= box[3], "the page scrolls sideways"


@pytest.mark.parametrize("cols", [2, 3, 5])
def test_the_column_count_comes_from_the_custom_property(firefox, cols):  # noqa: F811
    head = (
        '<div class="cols-row" role="row">'
        + "".join(f'<span role="columnheader">H{k}</span>' for k in range(cols))
        + "</div>"
    )
    rows = "".join(
        '<div class="cols-row" role="row">'
        + "".join(f'<span role="cell">{"x" * (1 + (r * 3 + k) % 7)}</span>' for k in range(cols))
        + "</div>"
        for r in range(4)
    )
    body = f'<div class="cols-table" role="table" style="--cols:{cols}">{head}{rows}</div>'
    with _open(firefox, body, 1440) as page:
        found = al.measure(page, al.COLUMN_EDGES_JS, {"name": "n", "group": ".cols-table"})
    assert found["violations"] == [], found["violations"]
    assert len(found["instances"][0]["columns"]) == cols


def test_rows_stay_one_under_another(firefox):  # noqa: F811
    with _open(firefox, _table(), 1440) as page:
        tops = page.evaluate(
            "() => [...document.querySelectorAll('.cols-row')].map(r => r.getBoundingClientRect().top)"
        )
    assert tops == sorted(tops) and len(set(round(t) for t in tops)) == len(tops)


def test_a_figure_cell_ends_at_its_columns_right_edge(firefox):  # noqa: F811
    with _open(firefox, _table(), 1440) as page:
        rights = page.evaluate(
            "() => [...document.querySelectorAll('.cols-num')].map(e => {"
            " const r = document.createRange(); r.selectNodeContents(e);"
            " return Math.round(r.getBoundingClientRect().right); })"
        )
    assert max(rights) - min(rights) <= al.TOLERANCE_PX, rights


def test_a_row_that_is_not_aligned_is_found_by_the_same_measure(firefox):  # noqa: F811
    """The measure can fail on this markup: a cell moved by 10 px is reported."""
    moved = _table().replace(
        '<span role="cell">Short</span>', '<span role="cell" style="margin-left:10px">Short</span>'
    )
    with _open(firefox, moved, 1440) as page:
        found = al.measure(page, al.COLUMN_EDGES_JS, SPEC)
    assert any('column "Symphony": left edges differ' in v for v in found["violations"])


@pytest.mark.parametrize("width", al.NARROW)
def test_below_1001_px_the_primitive_does_nothing(firefox, width):  # noqa: F811
    with _open(firefox, _table(), width) as page:
        facts = page.evaluate(
            "() => { const cs = (sel) => getComputedStyle(document.querySelector(sel));"
            " return { table: cs('.cols-table').display, tableCols: cs('.cols-table').gridTemplateColumns,"
            " row: cs('.cols-row').display, rowCols: cs('.cols-row').gridTemplateColumns,"
            " num: cs('.cols-num').textAlign, justify: cs('.cols-num').justifySelf }; }"
        )
    assert facts["table"] != "grid" and facts["row"] != "grid", facts
    assert facts["tableCols"] == "none" and facts["rowCols"] == "none", facts
    assert facts["num"] != "right" and facts["justify"] in {"normal", "auto"}, facts


def test_the_boundary_is_1001_px_exactly(firefox):  # noqa: F811
    displays = {}
    for width in (1000, 1001):
        with _open(firefox, _table(), width) as page:
            displays[width] = page.evaluate(
                "() => getComputedStyle(document.querySelector('.cols-table')).display"
            )
    assert displays[1000] != "grid" and displays[1001] == "grid", displays
