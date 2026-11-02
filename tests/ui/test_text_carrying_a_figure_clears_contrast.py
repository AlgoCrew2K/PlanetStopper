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
* The text of a figure's basis, a stamp, a data cue or a threshold clears it too: the retirement basis
  line, the hero legend note, the card's "at close" and "insufficient history" lines, the account
  stamp, the detail panel's stat sub-labels and the tile labels, each by the token its rule or inline
  style resolves to. A sweep of every ``--studio-ink-faint`` text colour in ``templates/`` and
  ``static/`` fails on any left that is not on the named allowlist of text that carries none of those.
* The card headline's If held figure carries no opacity below 1 and clears the bar in both themes.
* The account-basis chip on the Today and Cumulative rows clears it in both themes and keeps a visible
  warning cue of its own (a border, a background tint or a leading marker in the warn family), so moving
  its text off the warn token does not leave it reading as plain text.
* Headings, labels and footnotes: the dashboard's section headings (CVaR Diagnostic, Managed Sleeves,
  ``.section-label``), the page ``.section-header``, the tweaks ``.twk-sect``, the settings nav
  footnote ``.sn-footnote`` and the settings modal's kicker and caption clear it too. The enabled sleeve Delete
  button's label too. The sweep's allowlist keeps only icons and disabled controls.

``--studio-ink-faint`` is the token this replaces; the first test shows it fails the bar on every
surface in both themes, so the bar tells a fix from the status quo.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import NamedTuple

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


_LEADING_VAR = re.compile(r"^var\(\s*(--[\w-]+)")


def primary_token(value: str) -> str | None:
    """The token a colour value names first: ``var(--x)`` and ``var(--x, fallback)`` are both ``--x``."""
    named = contrast.token_of(value)
    if named:
        return named
    leading = _LEADING_VAR.match(value.strip())
    return leading.group(1) if leading else None


def assert_clears_bar(token_or_value: str, where: str, *, opacity: float = 1.0) -> None:
    token = primary_token(token_or_value)
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

SHARED_CLASSES = (
    "legend-as-of",
    "mini-stat-note",
    "mini-stat-label",
    "detail-stat-sub",
    "retirement-rec-basis",
    "section-label",
    "section-header",
    "twk-sect",
    "sn-footnote",
)


@pytest.mark.parametrize("class_name", SHARED_CLASSES)
def test_every_rule_that_colours_the_shared_class_uses_a_token_that_clears_the_bar(class_name):
    rules = contrast.class_rules(class_name)
    colouring = [r for r in rules if "color" in r[2]]
    assert colouring, f".{class_name} has no colour rule; the test cannot read what it resolves to"
    for source, selector, props in colouring:
        assert_clears_bar(
            props["color"], f"{source}: {selector}", opacity=_opacity(props.get("opacity"))
        )


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
def test_the_advisor_basis_and_stamp_lines_use_only_classes_and_inline_colours_that_clear_the_bar(
    name,
):
    source = (TEMPLATES / "ai_advisor.html").read_text(encoding="utf-8")
    for attrs in _tag_attributes(source, ADVISOR_ELEMENTS[name]):
        inline = contrast.style_properties(attrs.get("style"))
        if "color" in inline:
            assert_clears_bar(
                inline["color"], f"{name}: inline colour", opacity=_opacity(inline.get("opacity"))
            )
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
let card = canvas;
while (card && card.getAttribute && card.getAttribute('data-testid') !== 'sym-card') card = card.parentNode;
const el = card ? card.querySelector('.spark-session') : null;
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
        assert any(contrast.weakest(t, opacity=inline_opacity)[1] >= BAR for t in candidates), (
            f"the caption's inline colour is one of {candidates}; none clears {BAR}:1"
        )
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
    strong = re.search(r'data-testid="mdd-lifetime-scalar".*?<strong([^>]*)>', source, flags=re.S)
    assert strong, "the hero Lifetime MDD line has no figure element"
    inline = contrast.style_properties(
        dict(re.findall(r'([\w-]+)="([^"]*)"', strong.group(1))).get("style")
    )
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


# ---------------------------------------------------------------------------
# Basis, stamp, data-cue and threshold text on the dashboard: the elements, by test id
# ---------------------------------------------------------------------------

DASHBOARD_ELEMENTS = {
    "hero legend basis note": r'<span[^>]*class="legend-basis-note"[^>]*>',
    "card 'at close' basis": r'<span[^>]*data-testid="guard-alpha-basis"[^>]*>',
    "hero Max DD 'insufficient history'": r'<span[^>]*data-testid="mdd-insufficient-label"[^>]*>',
    "card Max DD 'insufficient history'": r'<span[^>]*data-testid="card-mdd-insufficient-label"[^>]*>',
    "account basis stamp": r'<div[^>]*id="account-basis-as-of"[^>]*>',
    "CVaR Diagnostic heading": r"<div[^>]*>\s*CVaR Diagnostic",
    "Managed Sleeves heading": r"<div[^>]*>\s*Managed Sleeves",
    "Sleeve Delete button label": r'<button[^>]*data-testid="sleeve-delete-control"[^>]*>',
}


@pytest.mark.parametrize("name", list(DASHBOARD_ELEMENTS))
def test_the_dashboards_basis_stamp_and_cue_lines_name_a_colour_that_clears_the_bar(name):
    source = _index_source()
    for attrs in _tag_attributes(source, DASHBOARD_ELEMENTS[name]):
        inline = contrast.style_properties(attrs.get("style"))
        named = []
        if "color" in inline:
            named.append((f"{name}: inline colour", inline["color"], inline.get("opacity")))
        for class_name in attrs.get("class", "").split():
            for source_file, selector, props in contrast.class_rules(class_name):
                if "color" in props:
                    named.append(
                        (f"{name}: {source_file} {selector}", props["color"], props.get("opacity"))
                    )
        assert named, (
            f"{name}: no inline colour and no class rule names its colour, so the test cannot read "
            "the token it resolves to"
        )
        for where, value, opacity in named:
            assert_clears_bar(value, where, opacity=_opacity(opacity))


# ---------------------------------------------------------------------------
# The card headline's If held figure
# ---------------------------------------------------------------------------

IF_HELD_FIGURE = re.compile(r'data-field="tc-held"\s+class="dv-value[^"]*"\s*style="([^"]*)"', re.S)


def test_the_card_headline_if_held_figure_is_found_in_both_card_templates():
    assert len(IF_HELD_FIGURE.findall(_index_source())) == 2


@pytest.mark.parametrize("tone", ["pos", "neg"])
@pytest.mark.parametrize("theme", list(contrast.THEMES))
def test_the_card_headline_if_held_figure_has_no_opacity_and_clears_the_bar_on_the_card(
    theme, tone
):
    rules = [r for r in contrast.class_rules("dv-value") if re.search(rf"\.{tone}(?![\w-])", r[1])]
    assert rules, f"no rule colours .dv-value.{tone}"
    for style in IF_HELD_FIGURE.findall(_index_source()):
        inline = contrast.style_properties(style)
        opacity = _opacity(inline.get("opacity"))
        assert opacity == 1.0, f"the If held figure is dimmed to opacity {opacity}: {style!r}"
        for source_file, selector, props in rules:
            value = inline.get("color") or props["color"]
            token = primary_token(value)
            assert token is not None, f"{selector}: colour {value!r} is not a design token"
            ratio = contrast.text_ratio(
                token,
                contrast.SURFACES["card"],
                theme,
                opacity=opacity * _opacity(props.get("opacity")),
            )
            assert ratio >= BAR, (
                f"{source_file} {selector}: {token} is {ratio:.2f}:1 on the card in the {theme} "
                f"theme, below {BAR}:1"
            )


# ---------------------------------------------------------------------------
# The account-basis chip: readable text, and still a warning
# ---------------------------------------------------------------------------

CHIP_IDS = ("comp-today-basis-chip", "comp-cumulative-basis-chip")
WARN_FAMILY = "--studio-warn"
_DECLARATION_BLOCK = re.compile(r"([^{}]+)\{([^{}]*)\}")


def _chip_attributes(test_id: str) -> dict[str, str]:
    (attrs,) = _tag_attributes(_index_source(), rf'<span[^>]*data-testid="{test_id}"[^>]*>')
    return attrs


def _class_declarations(class_name: str) -> list[dict[str, str]]:
    """Every declaration list of a rule whose last compound selector is ``.class_name`` (the resting
    state and its pseudo-elements), every property kept, unlike ``contrast.class_rules``."""
    subject = re.compile(r"\." + re.escape(class_name) + r"(?![\w-])")
    found = []
    for _source, css in contrast.stylesheet_texts():
        for group, body in _DECLARATION_BLOCK.findall(css):
            for selector in group.split(","):
                last = re.split(r"[\s>+~]+", selector.strip())[-1]
                if subject.search(last) and not re.search(r":(hover|focus|active|disabled)", last):
                    props = contrast.style_properties(body)
                    props["__selector__"] = selector.strip()
                    found.append(props)
    return found


def _chip_declarations(test_id: str) -> list[dict[str, str]]:
    """The inline style of the chip, then every rule of each class it carries."""
    attrs = _chip_attributes(test_id)
    found = [contrast.style_properties(attrs.get("style"))]
    for class_name in attrs.get("class", "").split():
        found += _class_declarations(class_name)
    return found


def _chip_text_color(test_id: str) -> tuple[str, float]:
    """(colour value, opacity) the chip's text resolves to: the last declaration that names one,
    inline style winning over a class rule."""
    declared = [d for d in _chip_declarations(test_id) if "color" in d and "__selector__" not in d]
    declared += [d for d in _chip_declarations(test_id) if "color" in d and "__selector__" in d]
    assert declared, f"{test_id}: no inline colour and no class rule gives the chip a text colour"
    return declared[0]["color"], _opacity(declared[0].get("opacity"))


def _chip_background_tokens(test_id: str) -> list[str]:
    tokens = []
    for props in _chip_declarations(test_id):
        if "__selector__" in props and "::" in props["__selector__"]:
            continue
        for key in ("background", "background-color"):
            value = props.get(key, "").strip()
            if value and value not in ("none", "transparent"):
                token = primary_token(value)
                assert token is not None, (
                    f"{test_id}: background {value!r} is not a design token, so the text's ratio "
                    "on it cannot be read; name a token (for example --studio-warn-tint)"
                )
                tokens.append(token)
    return tokens


@pytest.mark.parametrize("test_id", CHIP_IDS)
def test_precondition_the_chips_warn_token_text_fails_the_bar_on_the_light_card(test_id):
    """The chip is 9px text, so the bar is 4.5:1; the warn token on the light card is below it."""
    assert contrast.text_ratio(WARN_FAMILY, contrast.SURFACES["card"], "light") < BAR


@pytest.mark.parametrize("test_id", CHIP_IDS)
@pytest.mark.parametrize("theme", list(contrast.THEMES))
def test_the_account_basis_chip_text_clears_the_bar_in_both_themes_on_every_surface_it_can_sit_on(
    test_id, theme
):
    value, opacity = _chip_text_color(test_id)
    token = primary_token(value)
    assert token is not None, f"{test_id}: text colour {value!r} is not a design token"
    backgrounds = _chip_background_tokens(test_id)
    for name, layers in contrast.SURFACES.items():
        ratio = contrast.text_ratio(token, (*layers, *backgrounds), theme, opacity=opacity)
        assert ratio >= BAR, (
            f"{test_id}: {token} is {ratio:.2f}:1 on {name}"
            f"{' with ' + ', '.join(backgrounds) if backgrounds else ''} in the {theme} theme, "
            f"below {BAR}:1"
        )


def _chip_text_assignments() -> list[str]:
    """The string literals static/index.js assigns to the chip's text."""
    source = _blank_comments("static/index.js", (STATIC / "index.js").read_text(encoding="utf-8"))
    body = re.search(r"function renderAccountBasisChip\(.*?\n    }\n", source, flags=re.S)
    assert body, "renderAccountBasisChip is gone from static/index.js"
    return re.findall(r"chip\.textContent\s*=\s*'([^']*)'", body.group(0))


@pytest.mark.parametrize("test_id", CHIP_IDS)
def test_the_account_basis_chip_keeps_a_visible_warning_cue_in_the_warn_family(test_id):
    """Moving the text off the warn token must not leave the chip reading as plain text: a border or
    a background in the warn family, or a leading marker glyph in the text it is given."""
    cues = []
    for props in _chip_declarations(test_id):
        for key, value in props.items():
            if key == "__selector__":
                continue
            if (
                key.startswith("border") or key in ("outline", "box-shadow")
            ) and WARN_FAMILY in value:
                cues.append(f"{key}: {value}")
            if key in ("background", "background-color") and WARN_FAMILY in value:
                cues.append(f"{key}: {value}")
    texts = [t for t in _chip_text_assignments() if t]
    marker = bool(texts) and all(not t[0].isalnum() for t in texts)
    assert cues or marker, (
        f"{test_id}: no border, outline, shadow or background in the {WARN_FAMILY} family on the "
        "chip or its classes, and the text it is given does not start with a marker glyph, so a "
        "degraded account fetch no longer reads as a warning"
    )


def test_the_chip_text_assignments_are_found_so_the_marker_alternative_is_read_from_real_code():
    texts = _chip_text_assignments()
    assert len([t for t in texts if t]) == 2, texts


# ---------------------------------------------------------------------------
# Sweep: every text colour left on the faint token
# ---------------------------------------------------------------------------

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "static"
FAINT_TEXT = re.compile(
    r"(?<![\w-])color\s*:\s*var\(\s*--studio-ink-faint\b|\.color\s*=[^;\n]*--studio-ink-faint"
)

# Text the sweep lets stay on the faint token, each with what it is: an icon glyph or a disabled control. Headings, labels and footnotes are not on it (they are readable text). An entry
# that matches no use fails the allowlist test, so it cannot outlive the line it excused. ``exact``
# entries are a CSS selector, compared whole; the others are a fragment of the element's tag and the
# words after it.
SWEEP_ALLOWLIST: tuple[tuple[str, str, bool, str], ...] = (
    ("templates/index.html", "today-held-footnote-marker", False, "footnote marker glyph"),
    ("templates/index.html", "cumulative-cashflow-footnote-marker", False, "footnote marker glyph"),
    ("templates/settings.html", ".btn-discard:disabled", True, "disabled control label"),
    ("templates/settings.html", ".btn-save", True, "control label, replaced when enabled"),
)


def _blank_comments(name: str, text: str) -> str:
    """The text with comments replaced by spaces, so positions and line numbers stay."""

    def blank(match):
        return re.sub(r"[^\n]", " ", match.group(0))

    text = re.sub(r"/\*.*?\*/", blank, text, flags=re.S)
    if name.endswith(".html"):
        text = re.sub(r"<!--.*?-->", blank, text, flags=re.S)
        text = re.sub(r"\{#.*?#\}", blank, text, flags=re.S)
    if name.endswith(".js"):
        text = re.sub(r"(?m)^[ \t]*//.*$", blank, text)
    return text


def source_texts() -> list[tuple[str, str]]:
    """(name, comment-free text) of every template, stylesheet and script the pages load, with the
    vendored libraries and the token file left out."""
    found = [
        (f"templates/{p.relative_to(TEMPLATES).as_posix()}", p) for p in TEMPLATES.rglob("*.html")
    ]
    found += [(f"static/{p.name}", p) for p in STATIC.glob("*.css") if p.name != "tokens.css"]
    found += [(f"static/{p.name}", p) for p in STATIC.glob("*.js")]
    return [
        (name, _blank_comments(name, path.read_text(encoding="utf-8")))
        for name, path in sorted(found)
    ]


def _css_spans(name: str, text: str) -> list[tuple[int, int]]:
    if name.endswith(".css"):
        return [(0, len(text))]
    if name.endswith(".html"):
        return [
            (m.start(1), m.end(1))
            for m in re.finditer(r"<style[^>]*>(.*?)</style>", text, flags=re.S | re.I)
        ]
    return []


class Declarations(NamedTuple):
    """Where a declaration list sits. kind ``rule``: label is its selector. kind ``inline``: label is
    the tag (or the script text around the match) and the words after it. body: the declarations."""

    name: str
    line: int
    kind: str
    label: str
    body: str


def _inline_body(text: str, pos: int) -> str:
    low = max(0, pos - 900)
    start_attr = text.rfind('style="', low, pos)
    if start_attr != -1 and '"' not in text[start_attr + len('style="') : pos]:
        start = start_attr + len('style="')
        end = text.find('"', pos)
    else:
        start = max(text.rfind(q, low, pos) for q in ("'", '"', "`")) + 1
        ends = [e for e in (text.find(q, pos) for q in ("'", '"', "`")) if e != -1]
        end = min(ends) if ends else len(text)
    return " ".join(re.sub(r"'\s*\+\s*'", "", text[start:end]).split())


def declarations_matching(pattern: re.Pattern | str) -> list[Declarations]:
    """Every declaration list (a CSS rule body, an inline style) that holds a match of `pattern`."""
    out: list[Declarations] = []
    for name, text in source_texts():
        spans = _css_spans(name, text)
        for match in re.finditer(pattern, text):
            pos = match.start()
            line = text.count("\n", 0, pos) + 1
            if any(a <= pos < b for a, b in spans):
                brace = text.rfind("{", 0, pos)
                close = text.find("}", pos)
                start = max(text.rfind("}", 0, brace), text.rfind("{", 0, brace)) + 1
                selector = " ".join(text[start:brace].split())
                body = " ".join(text[brace + 1 : close].split())
                out.append(Declarations(name, line, "rule", selector, body))
            elif name.endswith(".html"):
                tag_start = max(text.rfind("<", 0, pos), pos - 600)
                tag_end = text.find(">", pos)
                label = " ".join(text[tag_start : tag_end + 1 + 120].split())
                out.append(Declarations(name, line, "inline", label, _inline_body(text, pos)))
            else:
                label = " ".join(text[max(0, pos - 200) : pos + 160].split())
                out.append(Declarations(name, line, "inline", label, _inline_body(text, pos)))
    return out


def _excused_by(use: Declarations, entry: tuple[str, str, bool, str]) -> bool:
    name, key, exact, _reason = entry
    if use.name != name:
        return False
    if exact:
        return use.kind == "rule" and use.label == key
    return use.kind == "inline" and key in use.label


def test_precondition_the_sweep_finds_the_faint_text_colours_the_pages_use():
    uses = declarations_matching(FAINT_TEXT)
    # Once every non-excused use has moved, the allowlisted ones are what is left: the sweep must
    # still see all of them, or its patterns have drifted.
    assert len(uses) >= len(SWEEP_ALLOWLIST), (
        f"the sweep found only {len(uses)} uses for {len(SWEEP_ALLOWLIST)} allowlist entries; "
        "its patterns have drifted"
    )
    assert {u.kind for u in uses} == {"rule", "inline"}


def test_every_allowlist_entry_excuses_a_use_that_still_exists():
    uses = declarations_matching(FAINT_TEXT)
    stale = [e for e in SWEEP_ALLOWLIST if not any(_excused_by(u, e) for u in uses)]
    assert not stale, f"allowlist entries that excuse nothing: {stale}"


def test_no_text_that_carries_a_figure_basis_stamp_cue_or_threshold_is_left_on_the_faint_token():
    left = [
        u
        for u in declarations_matching(FAINT_TEXT)
        if not any(_excused_by(u, e) for e in SWEEP_ALLOWLIST)
    ]
    listing = "\n".join(f"  {u.name}:{u.line} {u.kind} {u.label[:110]}" for u in left)
    assert not left, (
        f"{len(left)} text colours still use {FAINT}, which is below {BAR}:1 on every surface in "
        "both themes. Move each to --studio-ink-dim (or a token that clears the bar), or add it to "
        "SWEEP_ALLOWLIST with a reason if it carries no figure, basis, stamp, cue or threshold:\n"
        + listing
    )
