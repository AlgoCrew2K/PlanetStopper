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

_TEMPLATES = REPO / "templates"
_STATIC = REPO / "static"

# The one page-local copy that outlives the move, until the pages are brought onto the shared
# rules: the Advisor page's own .section-header.
_ADVISOR_TEMPLATE = "ai_advisor.html"
_ADVISOR_KEEPS_SELECTOR = ".section-header"


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


class TestDashboardIsUnchanged:
    def test_the_body_has_the_base_tag_and_class_sequence(self, dashboard_html):
        structure = cap.body_structure(dashboard_html)
        base = BASELINE["structure"]
        if structure != base:
            first = next(
                (i for i, (a, b) in enumerate(zip(structure, base, strict=False)) if a != b),
                min(len(structure), len(base)),
            )
            pytest.fail(
                f"GET / changed its element structure: {len(structure)} elements against the "
                f"base's {len(base)}; first difference at index {first}: "
                f"{structure[first : first + 1]} against {base[first : first + 1]}"
            )

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
                    if path.name == _ADVISOR_TEMPLATE and selector == _ADVISOR_KEEPS_SELECTOR:
                        continue
                    offenders.append(f"templates/{path.name}: {selector}")
        assert offenders == []

    def test_the_advisor_pages_own_section_header_keeps_winning_on_every_property(self):
        # Until the Advisor page's copy is deleted, the shared rule must not add a property
        # the page-local one does not set, or the shared rule would leak into that page.
        shared = dict(
            next(
                r["declarations"]
                for r in BASELINE["ordered"]
                if r["selectors"] == [".section-header"]
            )
        )
        local_rules = [
            dict(r["declarations"])
            for r in cap.ordered_primitive_rules(_inline_css(_ADVISOR_TEMPLATE))
            if r["selectors"] == [_ADVISOR_KEEPS_SELECTOR]
        ]
        assert len(local_rules) == 1, "the Advisor page keeps exactly one .section-header rule"
        assert set(shared) <= set(local_rules[0]), sorted(set(shared) - set(local_rules[0]))

    def test_the_advisor_page_still_links_the_shared_sheet(self):
        html = (_TEMPLATES / _ADVISOR_TEMPLATE).read_text(encoding="utf-8")
        assert "layout.css" in html
