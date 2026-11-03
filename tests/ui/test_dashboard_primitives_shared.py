"""The dashboard's primitives have one shared definition; the dashboard only refines them.

Subject: ``.hero-section``, ``.legend-as-of``, ``.mini-stats`` and its three parts,
``.section-header``, ``.sym-card`` (and its hover rule), ``.card-name`` and ``.status-pill`` with
every modifier are defined once, in ``static/layout.css``. Every other page that links
``layout.css`` uses them. The dashboard's own inline block may refine them, and only scoped under
``.page-dashboard`` (the body class), so a refinement can never reach another page.

Method: the real ``GET /`` is rendered over the captured bot state
(``tests/fixtures/prod_droplet/bot_state_positions.json``, verbatim) and compared with the
capture of the same route taken on the branch base before any edit
(``tests/fixtures/dashboard/primitives_baseline/``, produced by the committed
``capture_baseline.py`` and read by ``tests/ui/_dashboard_primitives_capture.py``, the module
both sides share):

* the shared rules are byte for byte what the base moved (same rules, same declarations, same
  order), as a suffix of ``layout.css``, and each is defined exactly once;
* the dashboard markup still renders every primitive class;
* a dashboard rule that names a primitive is scoped under ``.page-dashboard``.

The dashboard's own markup and styling were redesigned on purpose (the unslop pass), so the base's
tag-and-class sequence and resolved declarations are no longer the dashboard's contract. A rendered
page is judged on a screenshot at the gate, not here.
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

# The one change admitted since the base capture: three labels moved from the faint ink token to the
# dim one for contrast. Only these three colours move; every other declaration stays as captured.
_ADMITTED_COLOUR_CHANGES = {
    ".mini-stat-label": ("var(--studio-ink-faint)", "var(--studio-ink-dim)"),
    ".section-header": ("var(--studio-ink-faint)", "var(--studio-ink-dim)"),
    ".status-pill.sleeve-stale": ("var(--studio-ink-faint)", "var(--studio-ink-dim)"),
}


def _admit_colour_changes(baseline_resolved, baseline_ordered=None):
    """The base capture with the admitted colour changes applied, asserting each was in the base."""
    for selector, (was, now) in _ADMITTED_COLOUR_CHANGES.items():
        declarations = baseline_resolved[f"||{selector}"]
        assert declarations["color"] == was, f"{selector}: the base colour is not {was}"
        declarations["color"] = now
    if baseline_ordered is not None:
        for rule in baseline_ordered:
            for selector in rule["selectors"]:
                if selector in _ADMITTED_COLOUR_CHANGES:
                    was, now = _ADMITTED_COLOUR_CHANGES[selector]
                    rule["declarations"] = [
                        [prop, now if prop == "color" and value == was else value]
                        for prop, value in rule["declarations"]
                    ]


_admit_colour_changes(BASELINE["resolved"], BASELINE["ordered"])
_admit_colour_changes(SHELL_BASELINE["resolved"])

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
# GET / against the shared primitives
# ---------------------------------------------------------------------------


class TestDashboardUsesTheSharedPrimitives:
    def test_the_body_still_renders_every_shared_primitive(self, dashboard_html):
        from tests.ui import _shell_capture as shell

        structure = shell.body_structure_outside_top_bar(dashboard_html)
        for name in cap.PRIMITIVE_CLASSES:
            assert cap.tags_with_class(structure, name) > 0, (
                f"GET / renders no element with class {name}"
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

    def test_every_shared_primitive_selector_still_resolves_on_the_dashboard(self, dashboard_html):
        """The shared rules still reach the page: a base selector that stops resolving is a loss."""
        resolved = cap.resolved_primitive_rules(dashboard_html)
        missing = sorted(set(BASELINE["resolved"]) - set(resolved))
        assert not missing, f"selectors that no longer resolve on GET /: {missing}"

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

    def test_the_dashboard_template_refines_them_only_under_the_page_class(self):
        """A dashboard rule that names a primitive is scoped under .page-dashboard."""
        offenders = [
            selector
            for rule in cap.ordered_primitive_rules(_inline_css("index.html"))
            for selector in rule["selectors"]
            if cap.names_primitive(selector) and not selector.startswith(".page-dashboard ")
        ]
        assert offenders == [], offenders

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
