"""Text that carries a figure, a basis, a stamp or a failure cue clears 4.5:1 in both themes.

The ratio is computed from ``static/tokens.css`` (tests/_contrast_support.py): the text token composited
over each surface it sits on (page, card, raised card, stat tile), light and dark, with any element
opacity applied. A future change of a token changes the number, and a raw colour literal in place of a
token fails because it is not a token the helper can resolve.

Contract pinned, per element
* ``.legend-as-of`` (the data stamp, the connection-lost cue, the basis lines of Correlations and
  Retirement, the Blocked tile's per-gate figures) and ``.mini-stat-note`` (the Performance basis line
  and the "not a portfolio return" caveat): every rule that colours them uses a token that clears the
  bar on every surface in both themes, and none uses a colour that is not a token.
* The elements the Advisor page names by test id use only classes whose rules clear the bar, with no
  inline colour that does not.
* The sparkline's session caption is built with classes (and an inline colour, if any) that clear it.
* The per-card Lifetime MDD line, the hero Lifetime MDD line and the figure inside them clear it with no
  element opacity on the figure.
* The Settings "No longer held" heading and rows are dimmed but readable: clear it with their opacity.

``--studio-ink-faint`` is the token this replaces; the first test shows it fails the bar on every
surface in both themes, so the bar tells a fix from the status quo.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pytest

from tests import _contrast_support as contrast
from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"
BAR = contrast.MIN_BODY_TEXT_RATIO
FAINT = "--studio-ink-faint"


def _describe(token: str, opacity: float = 1.0) -> str:
    table = contrast.ratios_over_surfaces(token, opacity=opacity)
    return ", ".join(f"{k} {v:.2f}" for k, v in table.items())


def assert_clears_bar(token_or_value: str, where: str, *, opacity: float = 1.0) -> None:
    token = contrast.token_of(token_or_value)
    assert token is not None, (
        f"{where}: colour {token_or_value!r} is not a design token, so no ratio can be read from it"
    )
    surface, ratio = contrast.weakest(token, opacity=opacity)
    assert ratio >= BAR, (
        f"{where}: {token}{'' if opacity == 1.0 else f' at opacity {opacity}'} is {ratio:.2f}:1 "
        f"on {surface}, below {BAR}:1. All surfaces: {_describe(token, opacity)}"
    )


def _opacity(value: str | None) -> float:
    if value is None:
        return 1.0
    return float(value)


def test_precondition_the_faint_token_fails_the_bar_on_every_surface_in_both_themes():
    table = contrast.ratios_over_surfaces(FAINT)
    assert all(v < BAR for v in table.values()), table


def test_precondition_the_body_ink_token_clears_it_so_a_token_exists_that_does():
    assert contrast.weakest("--studio-ink")[1] >= BAR
    assert contrast.weakest("--studio-ink-dim")[1] >= BAR


# ---------------------------------------------------------------------------
# The two shared classes
# ---------------------------------------------------------------------------

SHARED_CLASSES = ("legend-as-of", "mini-stat-note")


@pytest.mark.parametrize("class_name", SHARED_CLASSES)
def test_every_rule_that_colours_the_shared_class_uses_a_token_that_clears_the_bar(class_name):
    rules = contrast.class_rules(class_name)
    colouring = [r for r in rules if "color" in r[2]]
    assert colouring, f".{class_name} has no colour rule; the test cannot read what it resolves to"
    for source, selector, props in colouring:
        assert_clears_bar(props["color"], f"{source}: {selector}", opacity=_opacity(props.get("opacity")))


@pytest.mark.parametrize("class_name", SHARED_CLASSES)
def test_the_shared_class_adds_no_opacity_below_one(class_name):
    for source, selector, props in contrast.class_rules(class_name):
        assert _opacity(props.get("opacity")) == 1.0, (
            f"{source}: {selector} dims the text with opacity {props.get('opacity')}"
        )


# ---------------------------------------------------------------------------
# Advisor page elements, by test id, read from the template that renders them
# ---------------------------------------------------------------------------


def _tag_attributes(source: str, locator: str) -> list[dict[str, str]]:
    tags = []
    for match in re.finditer(locator, source, flags=re.S):
        tags.append(dict(re.findall(r'([\w:-]+)="([^"]*)"', match.group(0))))
    assert tags, f"no element in the template matches {locator!r}"
    return tags


ADVISOR_ELEMENTS = {
    "Retirement last-run stamp": r'<[a-z]+[^>]*data-testid="retirement-last-run-time"[^>]*>',
    "Retirement series-through line": r'<[a-z]+[^>]*data-testid="retirement-last-run-series-through"[^>]*>',
    "Retirement tile detail (the Blocked per-gate figures)": r"<div[^>]*>\s*\{\{\s*_tile\.detail",
    "Correlations basis": r'<[a-z]+[^>]*data-testid="correlations-basis"[^>]*>',
    "Correlations as-of": r'<[a-z]+[^>]*data-testid="correlations-as-of"[^>]*>',
    "Correlations earlier-run note": r'<[a-z]+[^>]*data-testid="correlations-earlier-run-note"[^>]*>',
}


@pytest.mark.parametrize("name", list(ADVISOR_ELEMENTS))
def test_the_advisor_basis_and_stamp_lines_use_only_classes_and_inline_colours_that_clear_the_bar(name):
    source = (TEMPLATES / "ai_advisor.html").read_text(encoding="utf-8")
    for attrs in _tag_attributes(source, ADVISOR_ELEMENTS[name]):
        inline = contrast.style_properties(attrs.get("style"))
        if "color" in inline:
            assert_clears_bar(inline["color"], f"{name}: inline colour", opacity=_opacity(inline.get("opacity")))
        for class_name in attrs.get("class", "").split():
            for source_file, selector, props in contrast.class_rules(class_name):
                if "color" in props:
                    assert_clears_bar(
                        props["color"],
                        f"{name}: {source_file} {selector}",
                        opacity=_opacity(props.get("opacity")),
                    )


# ---------------------------------------------------------------------------
# The sparkline session caption: the element the page script builds
# ---------------------------------------------------------------------------

CAPTION_SCENARIO = r"""
await H.boot({ extras: true, charts: H.bodies.charts, state: H.bodies.same });
const canvas = H.sandbox.document.querySelector('[data-testid="card-spark"][data-sym-id="__ID__"]');
const el = canvas && canvas.parentNode ? canvas.parentNode.querySelector('.spark-session') : null;
return el ? { text: el.textContent.trim(), classes: el.className, color: el.style.color || null,
              opacity: el.style.opacity || null, tokens: H.tokens } : null;
"""


def _tokens_with_value(value: str, tokens: dict) -> list[str]:
    """The tokens a colour string written by the page's own token reader could have come from."""
    reference = contrast.token_of(value)
    if reference:
        return [reference]
    return [name for name, raw in tokens.items() if str(raw).strip() == value.strip()]


def test_the_sessions_caption_over_a_standby_tape_is_built_with_text_that_clears_the_bar():
    world = hx.get_world("default")
    symphony = hx.UNTRIGGERED_ID
    today = world["seeded_today"]
    previous = (dt.date.fromisoformat(today) - dt.timedelta(days=3)).isoformat()
    chart = dict(world["charts"][symphony], session_date=previous, et_date=today)
    result = sup.run(
        CAPTION_SCENARIO.replace("__ID__", symphony),
        world="default",
        extra_bodies={
            "charts": {**world["charts"], symphony: chart},
            "same": sup.with_stamp(world["state"], sup.render_stamp(world)),
        },
    )
    assert result is not None and result["text"], "the previous session's tape has no caption"
    inline_opacity = _opacity(result["opacity"])
    if result["color"]:
        candidates = _tokens_with_value(result["color"], result["tokens"])
        assert candidates, (
            f"the caption's inline colour {result['color']!r} is not any design token's value"
        )
        assert any(
            contrast.weakest(t, opacity=inline_opacity)[1] >= BAR for t in candidates
        ), f"the caption's inline colour is one of {candidates}; none clears {BAR}:1"
    for class_name in result["classes"].split():
        for source_file, selector, props in contrast.class_rules(class_name):
            if "color" in props:
                assert_clears_bar(
                    props["color"],
                    f"the caption's class {class_name}: {source_file} {selector}",
                    opacity=inline_opacity * _opacity(props.get("opacity")),
                )


# ---------------------------------------------------------------------------
# Lifetime MDD: the card line, the hero line
# ---------------------------------------------------------------------------


def _index_source() -> str:
    return (TEMPLATES / "index.html").read_text(encoding="utf-8")


def test_the_card_lifetime_mdd_line_and_its_figure_clear_the_bar_with_no_opacity_on_the_figure():
    source = _index_source()
    cells = _tag_attributes(source, r'<div[^>]*data-field="mdd-lifetime-cell"[^>]*>')
    figures = _tag_attributes(source, r'<span[^>]*data-field="mdd-lifetime"[^>]*>')
    assert len(cells) == len(figures) >= 1
    for attrs in cells:
        inline = contrast.style_properties(attrs.get("style"))
        assert "color" in inline, "the Lifetime MDD line carries no colour of its own"
        assert_clears_bar(inline["color"], "the card Lifetime MDD line")
    for attrs in figures:
        inline = contrast.style_properties(attrs.get("style"))
        assert _opacity(inline.get("opacity")) == 1.0, (
            f"the Lifetime MDD figure is dimmed with opacity {inline.get('opacity')}"
        )
        if "color" in inline:
            assert_clears_bar(inline["color"], "the card Lifetime MDD figure")


def test_the_hero_lifetime_mdd_line_and_its_figure_clear_the_bar():
    source = _index_source()
    for attrs in _tag_attributes(source, r'<div[^>]*data-testid="mdd-lifetime-scalar"[^>]*>'):
        inline = contrast.style_properties(attrs.get("style"))
        assert "color" in inline, "the hero Lifetime MDD line carries no colour of its own"
        assert_clears_bar(inline["color"], "the hero Lifetime MDD line")
    strong = re.search(
        r'data-testid="mdd-lifetime-scalar".*?<strong([^>]*)>', source, flags=re.S
    )
    assert strong, "the hero Lifetime MDD line has no figure element"
    inline = contrast.style_properties(dict(re.findall(r'([\w-]+)="([^"]*)"', strong.group(1))).get("style"))
    if "color" in inline:
        assert_clears_bar(inline["color"], "the hero Lifetime MDD figure")


# ---------------------------------------------------------------------------
# Settings: "No longer held" is dimmed, not unreadable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("class_name", ["sym-gone-heading", "sym-gone-row"])
def test_the_settings_no_longer_held_heading_and_rows_clear_the_bar_with_their_opacity(class_name):
    rules = [r for r in contrast.class_rules(class_name) if "color" in r[2]]
    assert rules, f".{class_name} has no colour rule"
    for source, selector, props in rules:
        assert_clears_bar(
            props["color"], f"{source}: {selector}", opacity=_opacity(props.get("opacity"))
        )
