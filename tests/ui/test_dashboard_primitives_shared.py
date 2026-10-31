"""The dashboard's primitives have one definition, and ``GET /`` is unchanged by the move.

Subject: ``.hero-section``, ``.legend-as-of``, ``.mini-stats`` and its three parts,
``.section-header``, ``.sym-card`` (and its hover rule), ``.card-name`` and ``.status-pill`` with
every modifier are defined once, in ``static/layout.css``, and not in ``templates/index.html``.
Every other page that links ``layout.css`` can then use them; the dashboard itself must not change.

Method: the real ``GET /`` is rendered over the captured bot state
(``tests/fixtures/prod_droplet/bot_state_positions.json``, verbatim) and compared with the
capture of the same route taken on the branch base before any edit
(``tests/fixtures/dashboard/primitives_baseline/``, produced by the committed
``capture_baseline.py`` and read by ``tests/ui/_dashboard_primitives_capture.py``, the module
both sides share):

* the tag and class sequence of the body equals the base's;
* every selector that names a primitive resolves, over the sheets the page loads in document
  order, to the declarations it had at the base;
* the rules moved byte for byte (same rules, same declarations, same order), as a suffix of
  ``layout.css``, and each is defined exactly once.

A rendered page is judged on a screenshot at the gate, not here.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from tests.ui import _dashboard_primitives_capture as cap

REPO = cap.REPO
BASELINE = json.loads((cap.PRIMITIVES_BASELINE_DIR / "baseline.json").read_text(encoding="utf-8"))
SHELL_BASELINE = json.loads(
    (cap.REPO / "tests" / "fixtures" / "dashboard" / "shell_baseline" / "baseline.json").read_text(
        encoding="utf-8"
    )
)["dashboard"]

_TEMPLATES = REPO / "templates"
_STATIC = REPO / "static"
_ADVISOR_TEMPLATE = "ai_advisor.html"

# What a page may write beside a primitive: layout properties only. Anything that gives the
# primitive a radius, border, padding, font, colour or shadow of the page's own would be a second
# definition. `cursor` is allowed on `.sym-card` alone: it is a button-like card on the dashboard
# and a plain card elsewhere.
_PAGE_LAYOUT_PROPERTIES = (
    "display",
    "flex",
    "gap",
    "grid",
    "margin",
    "min-",
    "max-",
    "width",
    "height",
    "overflow",
    "align-",
    "justify-",
    "position",
    "top",
    "left",
    "right",
    "bottom",
    "z-index",
    "white-space",
    "text-align",
    "text-overflow",
    "word-break",
    "overflow-wrap",
    "box-sizing",
    "order",
    "vertical-align",
)


def _is_layout_property(selector: str, prop: str) -> bool:
    if prop == "cursor":
        return cap.names_primitive(selector) and ".sym-card" in selector
    return prop.startswith(_PAGE_LAYOUT_PROPERTIES)


def _inline_css(template_name: str) -> str:
    import re

    html = (_TEMPLATES / template_name).read_text(encoding="utf-8")
    return "\n".join(
        re.findall(r"<style\b[^>]*>(.*?)</style>", html, flags=re.IGNORECASE | re.DOTALL)
    )


@pytest.fixture(scope="module")
def dashboard_html(tmp_path_factory) -> str:
    import database

    database.init_db()
    database.run_migrations()
    return cap.render_dashboard_html()


# ---------------------------------------------------------------------------
# The baseline is a usable base
# ---------------------------------------------------------------------------


class TestBaselineCanFail:
    def test_the_base_capture_holds_every_primitive_in_the_markup_and_in_the_rules(self):
        structure = BASELINE["structure"]
        for name in cap.PRIMITIVE_CLASSES:
            assert cap.tags_with_class(structure, name) > 0, (
                f"the base capture of GET / renders no element with class {name}, so the "
                "structure comparison would prove nothing about it"
            )
        named = " ".join(BASELINE["resolved"])
        for name in cap.PRIMITIVE_CLASSES:
            assert f".{name}" in named, f"the base capture resolves no rule for .{name}"
        assert BASELINE["primitive_classes_absent_from_structure"] == []

    def test_the_base_capture_has_a_card_for_every_captured_symphony(self):
        # The state seeded is every record of the droplet capture; each renders a card.
        positions = json.loads(
            (REPO / "tests" / "fixtures" / "prod_droplet" / "bot_state_positions.json").read_text(
                encoding="utf-8"
            )
        )
        cards = cap.tags_with_class(BASELINE["structure"], "sym-card")
        assert cards >= len(positions), (cards, len(positions))

    def test_a_changed_declaration_is_seen(self):
        html = (
            "<html><head><link rel='stylesheet' href='/static/layout.css'>"
            "<style>.sym-card { padding: 1px; }</style></head><body></body></html>"
        )
        before = cap.resolved_primitive_rules(html)
        changed = html.replace("1px", "2px")
        assert cap.resolved_primitive_rules(changed) != before

    def test_a_later_sheet_wins_and_an_earlier_important_survives(self):
        html = (
            "<html><head><style>.status-pill { color: red; padding: 1px !important; }</style>"
            "<style>.status-pill { color: blue; padding: 9px; }</style></head><body></body></html>"
        )
        resolved = cap.resolved_primitive_rules(html)["||.status-pill"]
        assert resolved["color"] == "blue"
        assert resolved["padding"] == "1px !important"

    def test_the_enclosing_media_rule_is_part_of_the_key(self):
        html = (
            "<html><head><style>@media (max-width: 700px) { .sym-card { padding: 4px; } }"
            ".sym-card { padding: 8px; }</style></head><body></body></html>"
        )
        keys = set(cap.resolved_primitive_rules(html))
        assert keys == {"@media (max-width: 700px)||.sym-card", "||.sym-card"}

    def test_a_rule_naming_no_primitive_is_not_read(self):
        html = "<html><head><style>.cards-grid { gap: 16px; }</style></head><body></body></html>"
        assert cap.resolved_primitive_rules(html) == {}


# ---------------------------------------------------------------------------
# GET / against the base capture
# ---------------------------------------------------------------------------


def _base_with_the_request_guard_script(base: list) -> list:
    """The base capture plus the one element added on purpose since it was taken.

    The shared header now loads ``request_guard.js`` immediately before ``chrome.js``, so the
    dashboard body gains a second ``<script>`` ahead of ``.page-wrap`` and nothing else. The
    capture stays as it was written; the expectation says exactly what was added, so any other
    change to the body still fails the comparison.
    """
    wrap = base.index(["div", ["page-wrap"]])
    assert base[wrap - 1] == ["script", []], "the base has no chrome script ahead of .page-wrap"
    return [*base[:wrap], ["script", []], *base[wrap:]]


def _base_with_the_review_fix_changes(base: list) -> list:
    """The base plus the body changes the review fixes to the dashboard made on purpose.

    Each is named, so any other change to the body still fails the comparison:

    * the headline's descriptor is a block of its own and its mode word has a span, where it was
      a span followed by a line break;
    * the detail panel holds a loading line ahead of its stats row;
    * the Guard stat's label holds the alpha glyph helper;
    * the headline's label is followed by a hidden notice the window picker fills when the picked
      window's strip could not be loaded.

    A card's alpha badge that has no figure to show loses its sign class; that one is judged by
    ``_without_a_sign_class`` and by the badge test below, since which badges that is depends on
    the state, not on the markup.
    """
    out = _base_with_the_request_guard_script(base)
    at = out.index(["span", ["guard-alpha-descriptor"]])
    assert out[at + 1] == ["br", []], "the base's descriptor is not followed by a line break"
    out[at : at + 2] = [["div", ["guard-alpha-descriptor"]], ["span", []]]
    stats = out.index(["div", ["detail-stats-row"]])
    out.insert(stats, ["div", ["math-row-hint"]])
    labels = [i for i in range(stats + 1, len(out)) if out[i] == ["div", ["detail-stat-label"]]]
    out.insert(labels[3] + 1, ["span", ["alpha-glyph"]])
    label = out.index(["div", ["guard-alpha-label"]])
    assert out[label + 1] == ["span", []], "the base's headline label does not hold the window span"
    out.insert(label + 2, ["div", ["legend-as-of"]])
    return out


def _without_a_sign_class(base_element: list, element: list) -> bool:
    """Whether ``element`` is the base's alpha badge minus its pos or neg class."""
    return (
        base_element[0] == element[0] == "span"
        and base_element[1][:1] == element[1] == ["cfg-alpha-badge"]
        and base_element[1][1:] in (["pos"], ["neg"])
    )


def _first_difference(structure: list, expected: list) -> int | None:
    """Index of the first element that differs from the expectation, None when none does."""
    for index, (now, want) in enumerate(zip(structure, expected, strict=False)):
        if now != want and not _without_a_sign_class(want, now):
            return index
    return None if len(structure) == len(expected) else min(len(structure), len(expected))


class TestDashboardIsUnchanged:
    def test_the_body_has_the_base_tag_and_class_sequence(self, dashboard_html):
        # The shared header is excluded: it changes by design (it wraps at narrow widths); the
        # base is the capture taken before the shell work began.
        from tests.ui import _shell_capture as shell

        structure = shell.body_structure_outside_top_bar(dashboard_html)
        base = _base_with_the_review_fix_changes(SHELL_BASELINE["structure_outside_top_bar"])
        first = _first_difference(structure, base)
        if first is not None:
            pytest.fail(
                f"GET / changed outside the header: {len(structure)} elements against the base's "
                f"{len(base)}; first difference at index {first}: "
                f"{structure[first : first + 1]} against {base[first : first + 1]}"
            )

    def test_a_cards_alpha_badge_has_a_sign_class_exactly_when_it_shows_a_figure(
        self, dashboard_html
    ):
        """The one body change that depends on the state: a badge with no figure reads the dash
        and carries no sign class, and a badge with a figure carries the class of its sign."""
        import html as html_module
        import re

        badges = re.findall(
            r'<span data-field="(?:tc|cr|mdd)-alpha-badge" class="([^"]*)"[^>]*>(.*?)</span>',
            dashboard_html,
            flags=re.DOTALL,
        )
        assert badges, "the capture renders no alpha badge"
        with_figure = 0
        for classes, text in badges:
            tokens = classes.split()
            shown = html_module.unescape(text).strip()
            figure = re.fullmatch(r"\u03b1 ([+-])\d+(?:\.\d+)?", shown)
            if figure:
                with_figure += 1
                signs = [t for t in tokens if t in ("pos", "neg")]
                assert signs == ["pos" if figure.group(1) == "+" else "neg"], (classes, shown)
            else:
                assert shown == "\u03b1 --", f"a badge with no figure reads {shown!r}"
                assert not {"pos", "neg"} & set(tokens), f"a dash badge is coloured: {classes!r}"
        assert with_figure, "no badge shows a figure, so the class of a figure is not exercised"

    def test_every_primitive_selector_resolves_to_its_base_declarations(self, dashboard_html):
        resolved = cap.resolved_primitive_rules(dashboard_html)
        base = BASELINE["resolved"]
        missing = sorted(set(base) - set(resolved))
        extra = sorted(set(resolved) - set(base))
        changed = {
            key: {"base": base[key], "now": resolved[key]}
            for key in sorted(set(base) & set(resolved))
            if base[key] != resolved[key]
        }
        assert not missing, f"selectors that no longer resolve on GET /: {missing}"
        assert not extra, f"selectors that newly resolve on GET /: {extra}"
        assert not changed, f"selectors whose resolved declarations changed: {changed}"

    def test_the_page_loads_the_shared_sheet(self, dashboard_html):
        assert "/static/layout.css" in cap.linked_static_sheets(dashboard_html)

    def test_the_shared_sheet_loads_before_the_inline_block_it_used_to_sit_in(self, dashboard_html):
        # The moved rules must come first so the page's own rules can still refine them.
        order = [
            "layout" if kind == "link" else kind
            for kind, payload in cap.document_sheets(dashboard_html)
            if kind == "style" or "layout.css" in payload
        ]
        assert order and order[0] == "layout", order
        assert "style" in order[1:], "GET / is expected to keep its inline block after the link"


# ---------------------------------------------------------------------------
# One definition, in layout.css
# ---------------------------------------------------------------------------


class TestOneDefinition:
    def test_layout_css_holds_the_base_rules_byte_for_byte_in_order(self):
        css = (_STATIC / "layout.css").read_text(encoding="utf-8")
        assert cap.ordered_primitive_rules(css) == BASELINE["ordered"]

    def test_the_moved_rules_are_a_suffix_of_layout_css(self):
        css = (_STATIC / "layout.css").read_text(encoding="utf-8")
        rules = list(cap.iter_rules(css))
        flags = [any(cap.names_primitive(s) for s in selectors) for _c, selectors, _d in rules]
        assert any(flags), "layout.css defines no primitive"
        first = flags.index(True)
        assert all(flags[first:]), (
            "a rule that names no primitive follows the moved rules; they are appended at the end"
        )
        assert first > 0, "layout.css keeps the page skeleton rules it had"

    def test_each_primitive_selector_is_defined_once_in_layout_css(self):
        css = (_STATIC / "layout.css").read_text(encoding="utf-8")
        counts: dict[str, int] = {}
        for context, selectors, _decls in cap.iter_rules(css):
            for selector in selectors:
                if cap.names_primitive(selector):
                    key = f"{context}||{selector}"
                    counts[key] = counts.get(key, 0) + 1
        assert set(counts) == set(BASELINE["resolved"])
        assert {k: n for k, n in counts.items() if n != 1} == {}

    def test_the_dashboard_template_defines_none_of_them(self):
        assert cap.ordered_primitive_rules(_inline_css("index.html")) == []

    def test_no_other_stylesheet_or_template_defines_one(self):
        """A page rule that names a primitive sets layout properties only."""
        offenders: list[str] = []
        for path in sorted(_STATIC.glob("*.css")):
            if path.name == "layout.css":
                continue
            for rule in cap.ordered_primitive_rules(path.read_text(encoding="utf-8")):
                offenders.append(f"static/{path.name}: {rule['selectors']}")
        for path in sorted(_TEMPLATES.glob("*.html")):
            if path.name == "index.html":
                continue
            for rule in cap.ordered_primitive_rules(_inline_css(path.name)):
                for selector in rule["selectors"]:
                    if not cap.names_primitive(selector):
                        continue
                    bad = [
                        prop
                        for prop, _value in rule["declarations"]
                        if not _is_layout_property(selector, prop)
                    ]
                    if bad:
                        offenders.append(f"templates/{path.name}: {selector} sets {bad}")
        assert offenders == []

    def test_the_advisor_page_defines_no_section_header_of_its_own(self):
        """The Advisor's own `.section-header` is deleted; the shared one serves it."""
        rules = [
            r
            for r in cap.ordered_primitive_rules(_inline_css(_ADVISOR_TEMPLATE))
            if ".section-header" in r["selectors"]
        ]
        assert rules == []

    def test_the_advisor_page_still_links_the_shared_sheet(self):
        html = (_TEMPLATES / _ADVISOR_TEMPLATE).read_text(encoding="utf-8")
        assert "layout.css" in html


# ---------------------------------------------------------------------------
# App shell: the dashboard outside the shared header is what the plan's base rendered
# ---------------------------------------------------------------------------


class TestShellBaseCanFail:
    def test_the_shell_base_capture_holds_a_structure_without_the_header_and_every_primitive(self):
        from tests.ui import _shell_capture as shell

        structure = SHELL_BASELINE["structure_outside_top_bar"]
        assert structure, "the base capture holds no dashboard markup"
        for name in cap.PRIMITIVE_CLASSES:
            assert cap.tags_with_class(structure, name) > 0, name
        header_markup = '<div data-testid="top-bar"><nav data-testid="studio-nav"><a class="x">'
        page = (
            f"<html><body>{header_markup}</a></nav></div><main class='kept'></main></body></html>"
        )
        assert shell.body_structure_outside_top_bar(page) == [["main", ["kept"]]]


class TestDashboardOutsideTheHeaderIsUnchanged:
    def test_every_primitive_resolves_on_the_dashboard_as_at_the_plan_base(self, dashboard_html):
        """The dashboard uses the same shared definitions, unchanged."""
        resolved = cap.resolved_primitive_rules(dashboard_html)
        base = SHELL_BASELINE["resolved"]
        assert sorted(resolved) == sorted(base)
        changed = {k: {"base": base[k], "now": resolved[k]} for k in base if base[k] != resolved[k]}
        assert not changed, f"selectors whose resolved declarations changed: {changed}"
