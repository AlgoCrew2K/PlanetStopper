"""The alignment measures find what they are written to find, and nothing else.

Browser-only (marker ``browser``; run with
``python -m pytest -m browser -n 0 tests/ui/test_alignment_measure_browser.py``). Each case is a
small page built here with a known layout and no network. A measure that cannot report a fault
proves nothing, so every script in ``tests/ui/_alignment.py`` is shown both reporting a defect and
reporting none: ``COLUMN_EDGES_JS``, ``COLUMN_GAPS_JS``, ``FIELD_OFFSETS_JS``, ``LINE_GAPS_JS``,
``ROW_START_JS`` and ``TABLE_FORM_JS``.
"""

from __future__ import annotations

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

SHELL = """<!doctype html><html><head><style>
 body {{ margin: 0; font: 14px/20px sans-serif; }}
 table {{ border-collapse: collapse; }}
 td, th {{ padding: 4px 12px; text-align: left; }}
 .num {{ text-align: right; }}
 {css}
</style></head><body>{body}</body></html>"""


@contextmanager
def _page(firefox, body: str, css: str = "", width: int = 1200):  # noqa: F811
    context = firefox.new_context(viewport=al.viewport(width))
    try:
        page = context.new_page()
        page.set_content(SHELL.format(body=body, css=css))
        yield page
    finally:
        context.close()


def _run(firefox, script: str, spec: dict, body: str, css: str = "", width: int = 1200):  # noqa: F811
    with _page(firefox, body, css, width) as page:
        return al.measure(page, script, spec)


# ---------------------------------------------------------------------------
# The constants the plan sets
# ---------------------------------------------------------------------------


def test_the_widths_are_one_inside_each_layout_band_from_1001_up_and_the_narrow_three():
    assert al.WIDTHS == (1001, 1120, 1290, 1350, 1400, 1420, 1440, 1920, 2560)
    assert al.NARROW == (1000, 390, 320)
    assert min(al.WIDTHS) == 1001, "the narrowest width the column rule covers is the boundary"
    assert all(w < 1001 for w in al.NARROW)
    assert al.viewport(390) == {"width": 390, "height": al.NARROW_VIEWPORT_HEIGHT[390]}
    assert al.viewport(1440)["width"] == 1440


def test_the_tolerances_are_two_pixels_and_the_gaps_forty_eight_and_twenty_four():
    assert (al.TOLERANCE_PX, al.MAX_COLUMN_GAP_PX, al.MAX_NON_CONTROL_GAP_PX) == (2, 48, 24)


# ---------------------------------------------------------------------------
# COLUMN_EDGES_JS
# ---------------------------------------------------------------------------

TABLE = """<table id="t"><thead><tr><th>Symphony</th><th class="num">Count</th></tr></thead>
<tbody>{rows}</tbody></table>"""
ROWS = """<tr><td>Alpha</td><td class="num">4</td></tr>
<tr><td>A much longer name</td><td class="num">12345</td></tr>
<tr><td>Beta</td><td class="num">0</td></tr>"""
SPEC = {"name": "t", "group": "#t", "numeric": ["Count"]}


def _edges(firefox, body: str, css: str = "", spec: dict | None = None, width: int = 1200):  # noqa: F811
    return _run(firefox, al.COLUMN_EDGES_JS, spec or SPEC, body, css, width)


def test_an_aligned_table_has_no_violation_and_names_its_columns_and_edges(firefox):  # noqa: F811
    found = _edges(firefox, TABLE.format(rows=ROWS))
    assert found["found"] == 1 and found["violations"] == []
    columns = found["instances"][0]["columns"]
    assert [(c["name"], c["edge"]) for c in columns] == [("Symphony", "left"), ("Count", "right")]
    assert found["instances"][0]["header_rows"] == 1 and found["instances"][0]["body_rows"] == 3


def test_a_numeric_column_left_aligned_under_a_right_aligned_header_is_reported(firefox):  # noqa: F811
    rows = ROWS.replace('class="num"', 'style="text-align:left"')
    found = _edges(firefox, TABLE.format(rows=rows))
    assert any('column "Count": right edges differ' in v for v in found["violations"]), found


def test_a_text_column_whose_cells_start_at_different_x_is_reported(firefox):  # noqa: F811
    rows = ROWS.replace("<td>Beta</td>", '<td><span style="margin-left:15px">Beta</span></td>')
    found = _edges(firefox, TABLE.format(rows=rows))
    assert any('column "Symphony": left edges differ' in v for v in found["violations"]), found


def test_the_edge_is_the_text_not_the_cell_padding(firefox):  # noqa: F811
    """Cells with different padding but text that starts at one x are aligned; the reverse is not."""
    aligned = TABLE.format(
        rows="""<tr><td style="padding-left:0"><span style="margin-left:12px">A</span></td>
        <td class="num">1</td></tr>
        <tr><td style="padding-left:12px">B</td><td class="num">2</td></tr>"""
    )
    assert _edges(firefox, aligned)["violations"] == []
    offset = TABLE.format(
        rows="""<tr><td>A</td><td class="num">1</td></tr>
        <tr><td><span style="margin-left:9px">B</span></td><td class="num">2</td></tr>"""
    )
    assert _edges(firefox, offset)["violations"] != []


def test_two_pixels_pass_and_three_fail(firefox):  # noqa: F811
    for shift, offends in ((2, False), (3, True)):
        rows = f"""<tr><td>A</td><td class="num">1</td></tr>
        <tr><td><span style="margin-left:{shift}px">B</span></td><td class="num">2</td></tr>"""
        found = _edges(firefox, TABLE.format(rows=rows))
        assert (found["violations"] != []) is offends, f"{shift}px: {found['violations']}"


def test_a_header_that_repeats_is_reported(firefox):  # noqa: F811
    rows = (
        ROWS
        + '<tr><th>Symphony</th><th class="num">Count</th></tr>'
        + '<tr><td>Gamma</td><td class="num">7</td></tr>'
    )
    found = _edges(firefox, TABLE.format(rows=rows))
    assert any("2 header rows, not one" in v for v in found["violations"]), found


def test_body_rows_with_no_header_are_reported(firefox):  # noqa: F811
    body = '<table id="t"><tbody>' + ROWS + "</tbody></table>"
    found = _edges(firefox, body)
    assert any("no header row above 3 body rows" in v for v in found["violations"]), found


def test_rows_flowed_into_side_by_side_groups_are_reported(firefox):  # noqa: F811
    """The earlier layout: a table whose rows flow into two columns of rows under one header."""
    body = (
        '<div id="t" role="table" style="display:grid;grid-template-columns:1fr 1fr;width:600px">'
        '<div role="row" style="grid-column:1/-1;display:flex;gap:40px">'
        '<span role="columnheader">Metric</span><span role="columnheader">Value</span></div>'
        + "".join(
            f'<div role="row" style="display:flex;gap:40px"><span role="cell">M{i}</span>'
            f'<span role="cell">{i}</span></div>'
            for i in range(4)
        )
        + "</div>"
    )
    found = _edges(firefox, body, spec={"name": "t", "group": "#t"})
    assert any("sit side by side" in v for v in found["violations"]), found


def test_a_table_of_div_rows_with_roles_is_measured_like_a_table_element(firefox):  # noqa: F811
    css = """.r {display:grid;grid-template-columns:12rem 6rem;column-gap:24px}
    .r > :nth-child(2) {text-align:right}"""
    body = (
        '<div id="t" role="table"><div class="r" role="row"><span role="columnheader">Name</span>'
        '<span role="columnheader">Runs</span></div>'
        '<div class="r" role="row"><span role="cell">a</span><span role="cell">1</span></div>'
        '<div class="r" role="row"><span role="cell">longer</span><span role="cell">22</span></div>'
        "</div>"
    )
    spec = {"name": "t", "group": "#t", "numeric": ["Runs"]}
    assert _edges(firefox, body, css, spec)["violations"] == []
    # the same table with the count column left in the body but right-aligned in the header
    css_fault = css + ".r:not(:first-child) > :nth-child(2) {text-align:left}"
    assert _edges(firefox, body, css_fault, spec)["violations"] != []


def test_a_name_that_wraps_inside_its_own_cell_leaves_the_columns_aligned(firefox):  # noqa: F811
    rows = (
        '<tr><td style="max-width:90px">Example Northwind Rotation Long Name</td>'
        '<td class="num">4</td></tr><tr><td>Beta</td><td class="num">12</td></tr>'
    )
    found = _edges(firefox, TABLE.format(rows=rows))
    assert found["violations"] == [], found


def test_visually_hidden_text_in_a_cell_is_not_content(firefox):  # noqa: F811
    css = ".sr {position:absolute;left:0;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}"
    rows = (
        '<tr><td><span class="sr">hidden words</span>Alpha</td><td class="num">4</td></tr>'
        '<tr><td>Beta</td><td class="num">0</td></tr>'
    )
    assert _edges(firefox, TABLE.format(rows=rows), css)["violations"] == []


def test_a_row_with_fewer_cells_than_the_header_is_reported(firefox):  # noqa: F811
    rows = ROWS + "<tr><td>Short row</td></tr>"
    found = _edges(firefox, TABLE.format(rows=rows))
    assert any("has 1 cells under 2 headers" in v for v in found["violations"]), found


def test_a_header_with_no_body_rows_and_a_missing_group_report_no_violation(firefox):  # noqa: F811
    only_header = _edges(firefox, TABLE.format(rows=""))
    assert only_header["violations"] == [] and only_header["instances"][0]["body_rows"] == 0
    missing = _edges(firefox, "<p>no table</p>")
    assert missing["found"] == 0 and missing["violations"] == []


def test_a_control_in_a_cell_is_measured_by_its_box(firefox):  # noqa: F811
    rows = (
        '<tr><td><button>go</button></td><td class="num">1</td></tr>'
        '<tr><td><span style="margin-left:20px"><button>go</button></span></td>'
        '<td class="num">2</td></tr>'
    )
    found = _edges(firefox, TABLE.format(rows=rows))
    assert any('column "Symphony": left edges differ' in v for v in found["violations"]), found


def test_a_closed_disclosure_holds_no_measured_row(firefox):  # noqa: F811
    body = "<details>" + TABLE.format(rows=ROWS) + "</details>"
    assert _edges(firefox, body)["found"] == 0
    body_open = "<details open>" + TABLE.format(rows=ROWS) + "</details>"
    assert _edges(firefox, body_open)["found"] == 1


# ---------------------------------------------------------------------------
# COLUMN_GAPS_JS
# ---------------------------------------------------------------------------


def test_a_table_stretched_across_its_panel_has_gaps_the_measure_reports(firefox):  # noqa: F811
    css = "#t {width:1100px}"
    found = _run(
        firefox,
        al.COLUMN_GAPS_JS,
        {"name": "t", "kind": "table", "group": "#t"},
        TABLE.format(rows=ROWS),
        css,
    )
    assert any("gap between" in v and "more than 48" in v for v in found["violations"]), found


def test_a_table_at_its_content_width_has_no_gap_over_forty_eight(firefox):  # noqa: F811
    found = _run(
        firefox,
        al.COLUMN_GAPS_JS,
        {"name": "t", "kind": "table", "group": "#t"},
        TABLE.format(rows=ROWS),
        "#t {width:max-content}",
    )
    assert found["violations"] == []
    assert found["instances"][0]["gaps"][0]["gap"] <= al.MAX_COLUMN_GAP_PX


def test_the_gap_is_between_the_widest_text_and_the_nearest_text_of_the_next_column(firefox):  # noqa: F811
    """A short name beside a long one leaves a wide cell, not a wide gap: the widest counts."""
    css = "#t {width:max-content} td:first-child {width:300px}"
    found = _run(
        firefox,
        al.COLUMN_GAPS_JS,
        {"name": "t", "kind": "table", "group": "#t"},
        TABLE.format(rows=ROWS),
        css,
    )
    # the first column is 300 px wide and its widest text is far narrower, so the gap runs from
    # that text's right edge to the nearest text of the next column
    assert found["instances"][0]["gaps"][0]["gap"] > 100
    assert found["violations"] != []


def test_fields_spread_by_space_between_are_reported_and_packed_fields_are_not(firefox):  # noqa: F811
    cards = "".join(
        f'<div class="card"><span class="a">name {i}</span><span class="b">{i}</span></div>'
        for i in range(3)
    )
    spec = {
        "name": "cards",
        "kind": "fields",
        "rows": ".card",
        "fields": [{"name": "a", "selector": ".a"}, {"name": "b", "selector": ".b"}],
    }
    spread = ".card {display:flex;justify-content:space-between;width:900px}"
    packed = ".card {display:flex;gap:24px;width:900px}"
    assert _run(firefox, al.COLUMN_GAPS_JS, spec, cards, spread)["violations"] != []
    assert _run(firefox, al.COLUMN_GAPS_JS, spec, cards, packed)["violations"] == []


# ---------------------------------------------------------------------------
# FIELD_OFFSETS_JS
# ---------------------------------------------------------------------------

CARDS_CSS = ".card {width:300px;margin-bottom:8px;display:flex;gap:16px} .name {width:120px}"
CARD = '<div class="card"><span class="name">{n}</span><span class="fig">{v}</span></div>'
FIELDS = {
    "name": "cards",
    "rows": ".card",
    "fields": [
        {"name": "name", "selector": ".name"},
        {"name": "figure", "selector": ".fig", "numeric": True},
    ],
}


def test_cards_whose_fields_share_an_offset_have_no_violation(firefox):  # noqa: F811
    body = "".join(CARD.format(n=f"Name {i}", v=i * 11) for i in range(1, 5))
    found = _run(firefox, al.FIELD_OFFSETS_JS, FIELDS, body, CARDS_CSS)
    assert found["found"] == 4 and found["violations"] == []
    assert set(found["fields"]) == {"name", "figure"}


def test_one_card_whose_field_starts_elsewhere_is_reported_with_its_row(firefox):  # noqa: F811
    body = (
        CARD.format(n="A", v=1)
        + '<div class="card"><span class="name" style="margin-left:30px">B</span>'
        '<span class="fig">2</span></div>' + CARD.format(n="C", v=3)
    )
    found = _run(firefox, al.FIELD_OFFSETS_JS, FIELDS, body, CARDS_CSS)
    assert any('field "name"' in v and "row 2" in v for v in found["violations"]), found


def test_a_numeric_field_is_compared_by_its_right_edge(firefox):  # noqa: F811
    css = CARDS_CSS + " .fig {width:60px;text-align:right}"
    body = CARD.format(n="A", v=1) + CARD.format(n="B", v=1000)
    assert _run(firefox, al.FIELD_OFFSETS_JS, FIELDS, body, css)["violations"] == []
    left = CARDS_CSS + " .fig {width:60px;text-align:left}"
    found = _run(firefox, al.FIELD_OFFSETS_JS, FIELDS, body, left)
    assert any('field "figure": right offset differs' in v for v in found["violations"]), found


def test_a_missing_field_is_reported_unless_the_field_is_optional(firefox):  # noqa: F811
    body = CARD.format(n="A", v=1) + '<div class="card"><span class="name">B</span></div>'
    assert any(
        "missing or empty on row 2" in v
        for v in _run(firefox, al.FIELD_OFFSETS_JS, FIELDS, body, CARDS_CSS)["violations"]
    )
    optional = {
        **FIELDS,
        "fields": [FIELDS["fields"][0], {**FIELDS["fields"][1], "optional": True}],
    }
    assert _run(firefox, al.FIELD_OFFSETS_JS, optional, body, CARDS_CSS)["violations"] == []


def test_moving_every_card_together_is_not_a_violation(firefox):  # noqa: F811
    css = CARDS_CSS + " .shift {margin-left:77px}"
    body = '<div class="shift">' + "".join(CARD.format(n=f"N{i}", v=i) for i in range(3)) + "</div>"
    assert _run(firefox, al.FIELD_OFFSETS_JS, FIELDS, body, css)["violations"] == []


# ---------------------------------------------------------------------------
# LINE_GAPS_JS
# ---------------------------------------------------------------------------

LINE = {"name": "line", "line": "#l", "items": ".i", "no_wrap": True}


def _items(n: int, text: str = "item") -> str:
    return "".join(f'<span class="i">{text} {k}</span>' for k in range(n))


def test_items_with_equal_gaps_on_one_line_have_no_violation(firefox):  # noqa: F811
    found = _run(
        firefox,
        al.LINE_GAPS_JS,
        LINE,
        f'<div id="l" style="display:flex;gap:32px">{_items(4)}</div>',
    )
    assert found["found"] == 1 and found["violations"] == []
    assert found["lines"][0]["items"] == 4


def test_gaps_that_differ_are_reported(firefox):  # noqa: F811
    body = (
        '<div id="l" style="display:flex"><span class="i">one</span>'
        '<span class="i" style="margin-left:20px">two</span>'
        '<span class="i" style="margin-left:40px">three</span></div>'
    )
    found = _run(firefox, al.LINE_GAPS_JS, LINE, body)
    assert any("gaps are not equal" in v for v in found["violations"]), found


def test_a_gap_over_forty_eight_is_reported_even_when_all_gaps_are_equal(firefox):  # noqa: F811
    found = _run(
        firefox,
        al.LINE_GAPS_JS,
        LINE,
        f'<div id="l" style="display:flex;gap:90px">{_items(3)}</div>',
    )
    assert any("more than 48" in v for v in found["violations"]), found


def test_items_spread_to_the_two_ends_of_a_row_are_reported(firefox):  # noqa: F811
    found = _run(
        firefox,
        al.LINE_GAPS_JS,
        LINE,
        f'<div id="l" style="display:flex;justify-content:space-between;width:1000px">{_items(3)}</div>',
    )
    assert any("more than 48" in v for v in found["violations"]), found


def test_items_that_wrap_onto_a_second_line_are_reported(firefox):  # noqa: F811
    found = _run(
        firefox,
        al.LINE_GAPS_JS,
        LINE,
        f'<div id="l" style="display:flex;flex-wrap:wrap;width:150px;gap:8px">{_items(4)}</div>',
    )
    assert any("not on one line" in v for v in found["violations"]), found


def test_a_label_that_breaks_across_lines_is_reported_only_when_no_wrap_is_asked(firefox):  # noqa: F811
    body = (
        '<div id="l" style="display:flex;gap:24px"><span class="i" style="width:60px">'
        'a label that breaks</span><span class="i">7</span></div>'
    )
    assert any(
        "breaks across lines" in v for v in _run(firefox, al.LINE_GAPS_JS, LINE, body)["violations"]
    )
    relaxed = {**LINE, "no_wrap": False}
    assert not any(
        "breaks across lines" in v
        for v in _run(firefox, al.LINE_GAPS_JS, relaxed, body)["violations"]
    )


def test_a_figure_with_its_label_under_its_value_is_one_item(firefox):  # noqa: F811
    tile = '<div class="i"><div>12</div><div>Tracked</div></div>'
    found = _run(
        firefox,
        al.LINE_GAPS_JS,
        LINE,
        f'<div id="l" style="display:flex;gap:32px">{tile * 3}</div>',
    )
    assert found["violations"] == [] and found["lines"][0]["items"] == 3


# ---------------------------------------------------------------------------
# ROW_START_JS
# ---------------------------------------------------------------------------

ROW = {"name": "row", "row": "#r"}


def test_text_within_twenty_four_pixels_of_the_element_before_it_has_no_violation(firefox):  # noqa: F811
    body = (
        '<div id="r" style="display:flex;gap:16px;align-items:center">'
        "<button>30d</button><span>caption</span><span>stamp</span></div>"
    )
    found = _run(firefox, al.ROW_START_JS, ROW, body)
    assert found["found"] == 1 and found["violations"] == []


def test_a_stamp_pushed_to_the_far_end_of_the_row_is_reported(firefox):  # noqa: F811
    body = (
        '<div id="r" style="display:flex;gap:16px;width:1000px"><button>30d</button>'
        '<span>caption</span><span style="margin-left:auto">stamp</span></div>'
    )
    found = _run(firefox, al.ROW_START_JS, ROW, body)
    assert any('"stamp"' in v and "more than 24" in v for v in found["violations"]), found


def test_a_control_at_the_end_of_the_row_is_not_a_violation(firefox):  # noqa: F811
    body = (
        '<div id="r" style="display:flex;gap:16px;width:1000px"><span>caption</span>'
        '<button style="margin-left:auto">Save</button></div>'
    )
    assert _run(firefox, al.ROW_START_JS, ROW, body)["violations"] == []


def test_a_wrapper_that_holds_only_controls_counts_as_a_control(firefox):  # noqa: F811
    body = (
        '<div id="r" style="display:flex;gap:16px;width:1000px"><span>caption</span>'
        '<div style="margin-left:auto"><button>Discard</button><button>Save</button></div></div>'
    )
    assert _run(firefox, al.ROW_START_JS, ROW, body)["violations"] == []
    labelled = (
        '<div id="r" style="display:flex;gap:16px;width:1000px"><span>caption</span>'
        '<div style="margin-left:auto">note <button>Save</button></div></div>'
    )
    assert _run(firefox, al.ROW_START_JS, ROW, labelled)["violations"] != []


def test_text_that_begins_a_new_line_must_begin_at_the_rows_left_edge(firefox):  # noqa: F811
    wrapped = (
        '<div id="r" style="display:flex;flex-wrap:wrap;gap:8px 16px;width:200px">'
        '<span style="width:180px">first line</span><span>second line</span></div>'
    )
    assert _run(firefox, al.ROW_START_JS, ROW, wrapped)["violations"] == []
    indented = wrapped.replace(
        "<span>second line</span>", '<span style="margin-left:40px">second line</span>'
    )
    found = _run(firefox, al.ROW_START_JS, ROW, indented)
    assert any("begins a line" in v for v in found["violations"]), found


def test_text_pushed_right_when_it_is_first_on_its_line_is_reported(firefox):  # noqa: F811
    body = '<div id="r" style="display:flex;justify-content:flex-end;width:600px"><span>stamp</span></div>'
    found = _run(firefox, al.ROW_START_JS, ROW, body)
    assert any("begins a line" in v for v in found["violations"]), found


def test_the_row_padding_is_not_part_of_the_rows_left_edge(firefox):  # noqa: F811
    body = '<div id="r" style="display:flex;padding:0 40px;width:600px"><span>text</span></div>'
    assert _run(firefox, al.ROW_START_JS, ROW, body)["violations"] == []


# ---------------------------------------------------------------------------
# TABLE_FORM_JS
# ---------------------------------------------------------------------------

FORM_TABLE = (
    '<table id="t"><thead><tr><th>Name</th><th>Count</th></tr></thead>'
    "<tbody><tr><td>a</td><td>1</td></tr><tr><td>b</td><td>2</td></tr></tbody></table>"
)
FORM_SPEC = {"groups": [{"name": "t", "group": "#t"}, {"name": "absent", "group": "#nothing"}]}


def test_a_columnar_table_and_a_stacked_table_have_different_forms(firefox):  # noqa: F811
    columnar = _run(firefox, al.TABLE_FORM_JS, FORM_SPEC, FORM_TABLE, width=390)
    stacked_css = "#t tr, #t td {display:block} #t thead {display:none}"
    stacked = _run(firefox, al.TABLE_FORM_JS, FORM_SPEC, FORM_TABLE, stacked_css, width=390)
    assert columnar["t"]["shapes"] != stacked["t"]["shapes"]
    assert columnar["t"]["shapes"][0]["stacked"] is False
    assert stacked["t"]["shapes"][0]["stacked"] is True
    assert stacked["t"]["shapes"][0]["header_shown"] is False
    assert columnar["t"]["shapes"][0]["header_shown"] is True


def test_the_form_does_not_change_when_the_rows_say_other_words(firefox):  # noqa: F811
    css = "#t tr, #t td {display:block}"
    one = _run(firefox, al.TABLE_FORM_JS, FORM_SPEC, FORM_TABLE, css, width=390)
    other = _run(
        firefox,
        al.TABLE_FORM_JS,
        FORM_SPEC,
        FORM_TABLE.replace("<td>a</td><td>1</td>", "<td>a much longer name</td><td>123456</td>"),
        css,
        width=390,
    )
    assert one["t"]["shapes"] == other["t"]["shapes"]


def test_a_group_the_page_does_not_hold_reads_zero_and_a_closed_disclosure_is_closed_again(firefox):  # noqa: F811
    with _page(firefox, "<details id='d'>" + FORM_TABLE + "</details>", width=390) as page:
        found = al.measure(page, al.TABLE_FORM_JS, FORM_SPEC)
        assert found["absent"] == {"count": 0, "shapes": []}
        assert found["t"]["count"] == 1, "a group inside a closed disclosure is measured open"
        assert page.evaluate("document.getElementById('d').open") is False


def test_the_stacked_form_groups_are_every_table_entry_with_its_selector():
    groups = al.stacked_form_groups()
    names = {g["name"] for g in groups}
    expected = {n for n, e in al.TABLE_GROUPS.items() if e["kind"] == "table"}
    assert names == expected and names, "no table entry is recorded for the stacked form"
    assert all(g["group"] for g in groups), "a table entry with no selector cannot be recorded"
