"""The "more" disclosure look is promoted to the shared stylesheet, once, in the right place.

History's collapsed reason list is the one disclosure with a shared look at the reference, and its
rules sit in ``templates/history.html``. Every page that adds a "What the terms mean" disclosure
needs that look, so the rules move to ``static/layout.css`` (every page links it) and leave the
History template: one definition, the declarations unchanged, the class name kept so History's
element keeps its look.

Placement is part of the contract. ``tests/ui/test_dashboard_primitives_shared.py`` requires the
shared primitive rules to be a suffix of the sheet and ``.hp-more`` is not a primitive, so the
rules go after the shared focus rule and before the first primitive rule; appending them at the
end would break that pin. The declarations below are the ones History held, which is the contract
of a move: nothing about the look changes.
"""

from __future__ import annotations

import pathlib
import re

from tests.ui import _dashboard_primitives_capture as cap

REPO = pathlib.Path(__file__).resolve().parents[2]
LAYOUT = (REPO / "static" / "layout.css").read_text(encoding="utf-8")
TEMPLATES = REPO / "templates"

NARROW_CONTEXT = "@media (max-width: 700px)"
# (context, selector) -> the declarations History held for it.
MOVED_RULES = {
    ("", ".hp-more"): [
        ("margin-top", "12px"),
        ("font-size", "12px"),
        ("color", "var(--studio-ink-dim)"),
    ],
    ("", ".hp-more[hidden]"): [("display", "none")],
    ("", ".hp-more summary"): [
        ("cursor", "pointer"),
        ("width", "fit-content"),
        ("border-radius", "4px"),
    ],
    ("", ".hp-more summary:hover"): [("color", "var(--studio-ink)")],
    ("", ".hp-more dl"): [
        ("margin-top", "8px"),
        ("display", "grid"),
        ("grid-template-columns", "max-content 1fr"),
        ("gap", "4px 16px"),
    ],
    ("", ".hp-more dt"): [("color", "var(--studio-ink)")],
    (NARROW_CONTEXT, ".hp-more dl"): [("grid-template-columns", "1fr"), ("gap", "0")],
    (NARROW_CONTEXT, ".hp-more dd"): [("margin-bottom", "8px")],
}
PAGE_TEMPLATES = ("index", "history", "performance", "settings", "ai_advisor")


def _rules(css: str = LAYOUT) -> list[tuple[str, list[str], list[tuple[str, str]]]]:
    return list(cap.iter_rules(css))


def _hp_more_indexes() -> list[int]:
    return [i for i, (_c, sels, _d) in enumerate(_rules()) if any(".hp-more" in s for s in sels)]


def test_layout_css_defines_each_moved_rule_once_with_the_declarations_history_held():
    found: dict[tuple[str, str], list[list[tuple[str, str]]]] = {}
    for context, selectors, declarations in _rules():
        for selector in selectors:
            if ".hp-more" in selector:
                found.setdefault((context, selector), []).append(declarations)
    assert set(found) == set(MOVED_RULES), (
        f"layout.css holds {sorted(found)}; expected {sorted(MOVED_RULES)}"
    )
    for key, expected in MOVED_RULES.items():
        assert len(found[key]) == 1, f"{key} is defined {len(found[key])} times"
        assert found[key][0] == expected, f"{key}: {found[key][0]} != {expected}"


def test_the_moved_rules_use_tokens_and_no_raw_colour():
    for key, declarations in MOVED_RULES.items():
        for prop, value in declarations:
            if prop == "color":
                assert value.startswith("var(--studio-"), f"{key}: raw colour {value}"
    hp_more_css = "".join(
        f"{sels} {decls}" for _c, sels, decls in _rules() if any(".hp-more" in s for s in sels)
    )
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", hp_more_css)


def test_the_rules_follow_the_shared_focus_rule_and_precede_the_first_primitive_rule():
    rules = _rules()
    focus = next(i for i, (_c, sels, _d) in enumerate(rules) if "summary:focus-visible" in sels)
    first_primitive = next(
        i for i, (_c, sels, _d) in enumerate(rules) if any(cap.names_primitive(s) for s in sels)
    )
    indexes = _hp_more_indexes()
    assert indexes, "layout.css holds no .hp-more rule"
    assert all(focus < i < first_primitive for i in indexes), (
        f"focus rule at {focus}, first primitive at {first_primitive}, .hp-more at {indexes}"
    )


def test_the_rules_are_contiguous_so_no_other_rule_hides_between_them():
    indexes = _hp_more_indexes()
    assert indexes, "layout.css holds no .hp-more rule"
    assert indexes == list(range(indexes[0], indexes[0] + len(indexes)))


def test_hp_more_is_not_a_primitive_class_of_the_shared_set():
    assert not cap.names_primitive(".hp-more")
    assert "hp-more" not in cap.PRIMITIVE_CLASSES


def test_history_defines_no_hp_more_rule_and_holds_no_reasons_disclosure_of_its_own():
    html = (TEMPLATES / "history.html").read_text(encoding="utf-8")
    style = "".join(re.findall(r"<style\b[^>]*>(.*?)</style>", html, re.S | re.I))
    assert ".hp-more" not in style, "History still defines a .hp-more rule of its own"
    assert 'data-testid="reason-descriptions"' not in html, "the reasons disclosure is back"


def test_every_page_links_the_shared_stylesheet_that_now_holds_the_rules():
    for name in PAGE_TEMPLATES:
        html = (TEMPLATES / f"{name}.html").read_text(encoding="utf-8")
        assert re.search(r"layout\.css", html), f"{name}.html does not link layout.css"
