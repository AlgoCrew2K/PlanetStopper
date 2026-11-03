"""The shared chrome's look: one quiet system, the same on every page.

Subject: the shared stylesheet ``static/layout.css`` (the page primitives), ``static/tweaks.css``
(the floating tweaks trigger and panel), ``templates/_chrome.html`` (the header), ``static/chrome.js``
and ``templates/login.html``.

Contract pinned, read from the files (no browser):

* A status is a dot and one word: ``.status-pill`` has no fill, border or rounding of its own, and
  every variant colours only its ``::before`` dot.
* The section header, the stat label, the stat figure and the card name are sentence case, with no
  tracking, in weights 400 to 600 (no uppercase, no 700 or 800).
* A row of stat figures has no box: ``.mini-stat`` has no fill and no radius, only a hairline above.
* A card has a hairline and no shadow; its hover moves the border, not a shadow.
* Text on a phone is never below 11 px in the shared primitives (the stamp that is 10 px on a
  desktop steps up to 11 px at the phone breakpoint).
* The header names no italic, no uppercase label and no weight above 600 in its own classes; the
  countdown reads ``Next mm:ss``; the mode tag, Force run now and Emergency Liquidate keep their
  recorded markup and are restyled from the style block.
* The tweaks panel and the login page carry no glass, no uppercase label, no italic, no shadow of
  the card and no colour that is not a token.

A rendered page is judged on a screenshot (and by ``test_chrome_look_browser.py``), not here.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from tests.ui import _dashboard_primitives_capture as cap

REPO = pathlib.Path(__file__).resolve().parents[2]
LAYOUT = (REPO / "static" / "layout.css").read_text(encoding="utf-8")
TWEAKS = (REPO / "static" / "tweaks.css").read_text(encoding="utf-8")
CHROME = (REPO / "templates" / "_chrome.html").read_text(encoding="utf-8")
CHROME_JS = (REPO / "static" / "chrome.js").read_text(encoding="utf-8")
LOGIN = (REPO / "templates" / "login.html").read_text(encoding="utf-8")

ALLOWED_WEIGHTS = {"400", "500", "600"}
FOUR_PX_NOT_REQUIRED_PROPS = {"font-size", "line-height"}


def _decls(rules, selector, context=""):
    """The declarations of the rule(s) whose selector list holds ``selector`` exactly."""
    merged: dict[str, str] = {}
    for ctx, selectors, declarations in rules:
        if ctx == context and selector in selectors:
            merged.update(dict(declarations))
    return merged


LAYOUT_RULES = list(cap.iter_rules(LAYOUT))
TWEAKS_RULES = list(cap.iter_rules(TWEAKS))


def _primitive_rules():
    return [
        (ctx, selectors, declarations)
        for ctx, selectors, declarations in LAYOUT_RULES
        if any(cap.names_primitive(s) for s in selectors)
    ]


class TestPrimitivesAreQuiet:
    @pytest.mark.parametrize("ctx,selectors,declarations", _primitive_rules())
    def test_no_primitive_is_uppercase_tracked_or_heavier_than_600(
        self, ctx, selectors, declarations
    ):
        props = dict(declarations)
        assert props.get("text-transform") != "uppercase", selectors
        assert "letter-spacing" not in props, selectors
        if "font-weight" in props:
            assert props["font-weight"] in ALLOWED_WEIGHTS, (selectors, props["font-weight"])
        assert props.get("font-style") != "italic", selectors

    def test_the_status_is_a_dot_and_one_word_not_a_pill(self):
        base = _decls(LAYOUT_RULES, ".status-pill")
        for forbidden in ("background", "border-radius", "padding", "border"):
            assert forbidden not in base, f".status-pill still sets {forbidden}"
        dot = _decls(LAYOUT_RULES, ".status-pill::before")
        assert dot["width"] == dot["height"] == "8px"
        assert dot["border-radius"] == "50%"
        variants = [
            s
            for _c, selectors, _d in LAYOUT_RULES
            for s in selectors
            if s.startswith(".status-pill.") and "::before" not in s
        ]
        # the only variant that sets anything other than a dot is the heavier word of a paused sleeve
        assert variants == [".status-pill.sleeve-paused"], variants
        assert _decls(LAYOUT_RULES, ".status-pill.sleeve-paused") == {"font-weight": "600"}

    def test_every_status_variant_colours_its_dot_only(self):
        for _ctx, selectors, declarations in LAYOUT_RULES:
            for selector in selectors:
                if selector.startswith(".status-pill.") and selector.endswith("::before"):
                    props = dict(declarations)
                    assert set(props) <= {"background", "border"}, (selector, props)

    def test_a_row_of_stat_figures_has_no_box(self):
        stat = _decls(LAYOUT_RULES, ".mini-stat")
        assert "background" not in stat and "border-radius" not in stat
        assert stat["border-top"] == "1px solid var(--studio-rule)"
        assert stat["text-align"] == "left"
        value = _decls(LAYOUT_RULES, ".mini-stat-value")
        assert value["font-weight"] == "500"
        assert value["font-variant-numeric"] == "tabular-nums"

    def test_a_card_has_a_hairline_and_no_shadow(self):
        card = _decls(LAYOUT_RULES, ".sym-card")
        assert "box-shadow" not in card
        assert card["border"] == "1px solid var(--studio-rule)"
        assert card["transition"] == "border-color 0.15s"
        hover = _decls(LAYOUT_RULES, ".sym-card:hover")
        assert hover == {"border-color": "var(--studio-rule-hi)"}

    def test_the_section_header_is_a_heading_not_a_label(self):
        header = _decls(LAYOUT_RULES, ".section-header")
        assert header["font-size"] == "14px"
        assert header["font-weight"] == "500"
        assert header["color"] == "var(--studio-ink)"

    def test_the_stat_stamp_and_note_never_fall_below_11px_on_a_phone(self):
        assert _decls(LAYOUT_RULES, ".mini-stat-note")["font-size"] == "11px"
        assert _decls(LAYOUT_RULES, ".mini-stat-label")["font-size"] == "11px"
        phone = "@media (max-width: 700px)"
        assert _decls(LAYOUT_RULES, ".legend-as-of", phone)["font-size"] == "11px"

    def test_a_keyboard_user_sees_focus_on_every_control(self):
        focus = [
            declarations
            for _c, selectors, declarations in LAYOUT_RULES
            if "button:focus-visible" in selectors and "a:focus-visible" in selectors
        ]
        assert focus, "layout.css has no shared :focus-visible rule"
        assert dict(focus[0])["outline"] == "2px solid var(--studio-accent)"

    def test_the_moved_primitives_still_close_the_sheet(self):
        flags = [
            any(cap.names_primitive(s) for s in selectors) for _c, selectors, _d in LAYOUT_RULES
        ]
        first = flags.index(True)
        assert all(flags[first:]), "a rule that names no primitive follows the primitives"


class TestTweaksIsQuiet:
    def test_the_panel_has_no_glass_no_uppercase_label_and_a_hairline(self):
        panel = _decls(TWEAKS_RULES, ".twk-panel")
        assert "backdrop-filter" not in panel and "-webkit-backdrop-filter" not in panel
        assert panel["border"] == "1px solid var(--studio-rule)"
        sect = _decls(TWEAKS_RULES, ".twk-sect")
        assert "text-transform" not in sect and "letter-spacing" not in sect
        assert sect["font-weight"] == "500" and sect["font-size"] == "11px"

    def test_no_inset_highlight_survives(self):
        assert "inset" not in TWEAKS

    def test_every_weight_is_400_to_600(self):
        for _ctx, selectors, declarations in TWEAKS_RULES:
            weight = dict(declarations).get("font-weight")
            if weight:
                assert weight in ALLOWED_WEIGHTS, (selectors, weight)

    def test_the_trigger_and_close_icons_are_masks_in_the_text_colour(self):
        for selector in ('[data-testid="tweaks-btn"]::before', ".twk-x::before"):
            assert "mask-image" in _decls(TWEAKS_RULES, selector), selector
        both = _decls(TWEAKS_RULES, '[data-testid="tweaks-btn"]::before')
        assert "stroke-width='1.5'" in both["mask-image"]
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", TWEAKS)

    def test_the_trigger_carries_no_glyph_and_no_inline_style(self):
        start = CHROME.index('<button data-testid="tweaks-btn"')
        button = CHROME[start : CHROME.index("</button>", start) + len("</button>")]
        assert "&#x2699;" not in button and "style=" not in button
        close = CHROME[CHROME.index('<button class="twk-x"') :]
        close = close[: close.index("</button>") + len("</button>")]
        assert "&#x2715;" not in close

    def test_the_swatches_are_one_row_and_the_selects_show_they_open(self):
        row = [d for _c, s, d in TWEAKS_RULES if any("accent-swatch" in x for x in s)]
        assert row and dict(row[0])["flex-direction"] == "row"
        assert "linear-gradient" in _decls(TWEAKS_RULES, ".twk-field")["background-image"]


class TestHeaderIsQuiet:
    STYLE = re.search(r"<style>(.*?)</style>", CHROME, re.DOTALL).group(1)

    def test_the_header_classes_carry_no_italic_uppercase_or_tracking(self):
        assert "font-style" not in self.STYLE
        # the one upper-case rule is the first letter of the sentence-cased mode word
        for _ctx, selectors, declarations in cap.iter_rules(self.STYLE):
            if dict(declarations).get("text-transform") == "uppercase":
                assert all(s.endswith("::first-letter") for s in selectors), selectors
        # the one tracking the style block writes is the reset that undoes the recorded inline value
        for match in re.finditer(r"letter-spacing\s*:\s*([^;]+);", self.STYLE):
            assert match.group(1).strip() == "0 !important", match.group(0)

    def test_the_header_weights_are_400_to_600(self):
        for weight in re.findall(r"font-weight\s*:\s*(\d+)", self.STYLE):
            assert weight in ALLOWED_WEIGHTS, weight

    def test_the_active_link_has_the_weight_of_the_others(self):
        link = re.search(r"\.shell-link \{([^}]*)\}", self.STYLE).group(1)
        assert "font-weight:500" in link.replace(" ", "")
        assert "aria-current" in self.STYLE
        # no per-link inline style is left in the markup
        links = CHROME[CHROME.index('<div class="shell-nav-links">') :]
        links = links[: links.index("</div>")]
        assert links.count('class="shell-link"') == 5
        assert "style=" not in links

    def test_the_wordmark_is_plain(self):
        assert re.search(
            r'<div class="shell-brand">Planet Stopper <span class="shell-brand-sub">guard</span></div>',
            CHROME,
        )
        assert re.search(r"\.shell-brand \{[^}]*font-weight:500", self.STYLE)
        assert re.search(r"\.shell-brand-sub \{[^}]*font-weight:400", self.STYLE)

    def test_the_countdown_reads_next_in_sentence_case(self):
        assert "'Next '" in CHROME_JS and "'NEXT '" not in CHROME_JS
        assert "Next {{ meta.next_run }}" in CHROME and "NEXT {{" not in CHROME

    def test_the_bell_is_a_16px_line_icon_with_a_plain_count(self):
        start = CHROME.index('data-testid="candidate-alert-indicator"')
        window = CHROME[start : start + 900]
        assert 'width="16" height="16"' in window and 'stroke-width="1.5"' in window
        assert re.search(r"\.shell-bell-count \{[^}]*color:var\(--studio-neg\)", self.STYLE)
        assert "border-radius:8px;background:var(--studio-neg)" not in CHROME

    def test_the_mode_is_a_dot_and_a_word_drawn_from_the_style_block(self):
        rule = re.search(
            r'\.shell-nav > \[data-testid="mode-pill"\] \{([^}]*)\}', self.STYLE
        ).group(1)
        for needle in (
            "border-radius:0 !important",
            "background-color:transparent !important",
            "radial-gradient",
            "text-transform:lowercase",
        ):
            assert needle in rule, needle
        assert re.search(
            r'\[data-live="true"\] \{[^}]*color:var\(--studio-neg\) !important', self.STYLE
        )

    def test_force_run_and_liquidate_are_one_size(self):
        rule = re.search(
            r'\.shell-nav > \[data-testid="force-run-btn"\], \.shell-nav > #emergency-liquidate \{([^}]*)\}',
            self.STYLE,
        ).group(1)
        assert "min-height:32px" in rule and "padding:8px 16px !important" in rule
        assert "border-radius:8px !important" in rule and "font-weight:500 !important" in rule

    def test_the_confirm_button_reads_as_disabled_until_the_phrase_is_typed(self):
        assert re.search(r"#panic-confirm-btn:disabled \{[^}]*opacity:0\.4 !important", self.STYLE)

    def test_the_header_adds_no_breakpoint_beyond_the_two_it_had(self):
        widths = set(re.findall(r"@media\s*\(max-width:\s*(\d+)px\)", self.STYLE))
        assert widths <= {"1400", "700"}, widths


class TestLoginIsQuiet:
    def test_no_italic_no_card_shadow_no_literal_colour(self):
        assert "font-style" not in LOGIN and "box-shadow" not in LOGIN
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", LOGIN)
        assert "rgba(" not in LOGIN

    def test_weights_are_400_to_600_and_labels_are_sentence_case(self):
        for weight in re.findall(r"font-weight\s*:\s*(\d+)", LOGIN):
            assert weight in ALLOWED_WEIGHTS, weight
        assert "text-transform" not in LOGIN and "letter-spacing" not in LOGIN

    def test_the_error_uses_the_danger_tokens(self):
        block = re.search(r"\.login-error \{([^}]*)\}", LOGIN).group(1)
        assert "var(--studio-neg-tint)" in block and "var(--studio-neg-border)" in block
        assert "color: var(--studio-neg)" in block

    def test_the_theme_script_is_still_the_first_child_of_the_body(self):
        assert re.search(
            r"<body[^>]*>\s*<script src=\"\{\{ url_for\('static', filename='tweaks\.js'\) \}\}\"></script>",
            LOGIN,
        )
