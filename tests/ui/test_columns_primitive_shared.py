"""The shared columns primitive sits in the right place in ``static/layout.css`` and sets layout only.

A table whose rows are not ``<tr>`` elements (the autotune runs, the observation lines, the cards'
figure rows) shares one column template across its rows through a shared primitive, defined once
in the stylesheet every page links. Three rules make it: ``.cols-table`` (the grid, its column count
from ``--cols``), ``.cols-row`` (a row that takes the grid's columns through a subgrid) and
``.cols-num`` (a figure that ends at its column's right edge).

Placement and scope are part of the contract, read from the sheet's text so this module runs on CI
where the browser modules do not:

* the rules follow the ``.hp-more`` block and precede the first shared primitive rule, because
  ``tests/ui/test_hp_more_shared_rule.py`` and ``tests/ui/test_dashboard_primitives_shared.py`` pin
  that order (the primitives must stay the sheet's closing run);
* every rule applies from 1001 px up and none below, so every narrower width keeps the stacked
  forms the pages have;
* the rules set layout and never the look (no colour, background, border, shadow or font), and name
  no raw colour.
"""

from __future__ import annotations

import pathlib
import re

from tests.ui import _dashboard_primitives_capture as cap

REPO = pathlib.Path(__file__).resolve().parents[2]
LAYOUT = (REPO / "static" / "layout.css").read_text(encoding="utf-8")

COLS_CLASS = re.compile(r"\.cols-(table|row|num)(?![\w-])")
WIDE_ONLY = "@media (min-width: 1001px)"
LOOK_PROPERTIES = re.compile(
    r"^(color|background.*|border.*|box-shadow|outline.*|font.*|opacity|text-shadow)$"
)


def _rules() -> list[tuple[str, list[str], list[tuple[str, str]]]]:
    return list(cap.iter_rules(LAYOUT))


def _cols_rules() -> list[tuple[int, str, list[str], list[tuple[str, str]]]]:
    return [
        (i, context, selectors, declarations)
        for i, (context, selectors, declarations) in enumerate(_rules())
        if any(COLS_CLASS.search(s) for s in selectors)
    ]


def _declared(rule_class: str) -> dict[str, str]:
    """The declarations of the rules whose subject is exactly ``.cols-<rule_class>``."""
    merged: dict[str, str] = {}
    for _i, _context, selectors, declarations in _cols_rules():
        if f".cols-{rule_class}" in [s.strip() for s in selectors]:
            merged.update(dict(declarations))
    return merged


def test_layout_css_defines_the_table_row_and_figure_rules():
    named = {
        m.group(1)
        for _i, _c, sels, _d in _cols_rules()
        for s in sels
        for m in COLS_CLASS.finditer(s)
    }
    assert named == {"table", "row", "num"}, f"layout.css names {sorted(named)}"
    for rule_class in ("table", "row", "num"):
        assert _declared(rule_class), f".cols-{rule_class} has no rule of its own"


def test_the_table_takes_its_column_count_from_a_custom_property_and_never_stretches_past_its_panel():
    declared = _declared("table")
    assert declared, "layout.css has no .cols-table rule"
    assert declared.get("display") == "grid"
    assert "var(--cols)" in declared.get("grid-template-columns", ""), declared
    assert declared.get("max-width") == "100%", "a table must never be wider than its panel"
    assert declared.get("width") in {"max-content", "fit-content"}, (
        "a table sits at the width its content needs, not the panel's"
    )


def test_a_row_takes_the_tables_columns_through_a_subgrid_across_every_column():
    declared = _declared("row")
    assert declared, "layout.css has no .cols-row rule"
    assert declared.get("display") == "grid"
    assert declared.get("grid-template-columns") == "subgrid", declared
    assert declared.get("grid-column") == "1 / -1", declared


def test_a_figure_cell_ends_at_its_columns_right_edge():
    declared = _declared("num")
    assert declared, "layout.css has no .cols-num rule"
    assert declared.get("justify-self") == "end"
    assert declared.get("text-align") == "right"


def test_every_rule_applies_from_1001_px_up_and_none_below():
    rules = _cols_rules()
    assert rules, "layout.css holds no columns rule"
    offenders = [
        (i, context or "<top level>") for i, context, _s, _d in rules if context != WIDE_ONLY
    ]
    assert not offenders, f"rules outside {WIDE_ONLY}: {offenders}"


def test_the_rules_follow_the_hp_more_block_and_precede_the_first_primitive_rule():
    rules = _rules()
    hp_more = [i for i, (_c, sels, _d) in enumerate(rules) if any(".hp-more" in s for s in sels)]
    first_primitive = next(
        i for i, (_c, sels, _d) in enumerate(rules) if any(cap.names_primitive(s) for s in sels)
    )
    indexes = [i for i, *_ in _cols_rules()]
    assert hp_more and indexes
    assert all(max(hp_more) < i < first_primitive for i in indexes), (
        f".hp-more ends at {max(hp_more)}, first primitive at {first_primitive}, columns at {indexes}"
    )


def test_the_rules_are_contiguous_so_no_other_rule_hides_between_them():
    indexes = [i for i, *_ in _cols_rules()]
    assert indexes, "layout.css holds no columns rule"
    assert indexes == list(range(indexes[0], indexes[0] + len(indexes)))


def test_the_rules_set_layout_only_and_name_no_raw_colour():
    for _i, _context, selectors, declarations in _cols_rules():
        for prop, value in declarations:
            assert not LOOK_PROPERTIES.match(prop), f"{selectors}: {prop} sets the look"
            assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", value), (
                f"{selectors}: a raw colour in {prop}: {value}"
            )


def test_the_columns_names_are_not_primitives_of_the_dashboard_set():
    """They are not in the set whose rules must be the sheet's closing run."""
    for name in (".cols-table", ".cols-row", ".cols-num"):
        assert not cap.names_primitive(name)
