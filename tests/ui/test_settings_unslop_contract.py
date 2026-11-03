"""The Settings page and the per-symphony modal keep the quiet-form contract of the unslop pass.

Source scans of ``templates/settings.html``, ``static/settings.js`` and ``static/settings-modal.js``
(the three files the pass owns). A rule that came back would put the old look back:

* no upper-case labels, no weight above 500, no side accent bar, no glyph used as an icon;
* a status is a dot and one word: the page's mode word and the modal's badges are sentence case;
* a credential field says nothing until something is typed in it (the note above the fields says
  blank fields are ignored), so nine rows do not each repeat "unchanged";
* text on a filled control is the paper token (white is below 4.5:1 on the dark theme's accents);
* the modal's look is one stylesheet, so its controls have hover and focus states.

The rendered shapes are pinned in ``tests/ui/test_settings_controls_browser.py`` (browser) and
``tests/ui/test_settings_modal_advisor_section_shows_the_rows_text.py`` (the grouped advisor rows).
"""

from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE = (REPO / "templates" / "settings.html").read_text(encoding="utf-8")
PAGE_JS = (REPO / "static" / "settings.js").read_text(encoding="utf-8")
MODAL_JS = (REPO / "static" / "settings-modal.js").read_text(encoding="utf-8")

# U+26A0 (warning sign), U+2713 is the modal's check mark and is not an emoji, so it is not listed.
GLYPHS_USED_AS_ICONS = ("⚠", "◆", "●", "↑", "✕")


def _style_block() -> str:
    match = re.search(r"<style>(.*?)</style>", TEMPLATE, flags=re.S)
    assert match, "settings.html has no inline style block"
    return match.group(1)


def _modal_css() -> str:
    match = re.search(r"var MODAL_CSS = \[(.*?)\]\.join", MODAL_JS, flags=re.S)
    assert match, "settings-modal.js has no MODAL_CSS"
    return match.group(1)


class TestQuietType:
    def test_the_page_sets_no_upper_case_label(self):
        assert "uppercase" not in _style_block()

    def test_the_modal_sets_no_upper_case_label(self):
        assert "uppercase" not in _modal_css()

    def test_no_weight_above_500_on_the_page_or_in_the_modal(self):
        for name, css in (("settings.html", _style_block()), ("settings-modal.js", _modal_css())):
            heavy = re.findall(r"font-weight:\s*([6-9]00)", css)
            assert not heavy, f"{name} sets weights {heavy}; the pass uses 400 and 500"

    def test_no_text_is_tracked(self):
        for name, css in (("settings.html", _style_block()), ("settings-modal.js", _modal_css())):
            values = [v.strip() for v in re.findall(r"letter-spacing:\s*([^;}]+)", css)]
            tracked = [v for v in values if v not in ("0", "0px")]
            assert not tracked, f"{name} tracks text: {tracked}"

    def test_no_text_is_below_11_px(self):
        for name, css in (("settings.html", _style_block()), ("settings-modal.js", _modal_css())):
            small = [
                size for size in re.findall(r"font-size:\s*([\d.]+)px", css) if float(size) < 11
            ]
            assert not small, f"{name} sets text below 11 px: {small}"


class TestNoSideBarsOrGlyphIcons:
    def test_no_rule_draws_a_side_accent_bar(self):
        assert "border-left" not in _style_block()
        assert "border-left" not in _modal_css()

    def test_no_glyph_stands_in_for_an_icon(self):
        for name, source in (
            ("settings.html", TEMPLATE),
            ("settings.js", PAGE_JS),
            ("settings-modal.js", MODAL_JS),
        ):
            for glyph in GLYPHS_USED_AS_ICONS:
                assert glyph not in source, f"{name} uses {glyph!r} as an icon"
            assert "&#9670;" not in source and "&#9679;" not in source, name

    def test_the_modal_draws_no_stripe_pattern(self):
        assert "repeating-linear-gradient" not in _modal_css()


class TestStatusIsADotAndOneWord:
    def test_the_page_names_the_mode_in_sentence_case(self):
        assert "'Live' : 'Dry run'" in PAGE_JS
        assert "'LIVE'" not in PAGE_JS and "'DRY RUN'" not in PAGE_JS

    def test_the_mode_dot_shows_only_once_the_server_has_answered(self):
        assert re.search(r'\.live-pill\[data-known="true"\]::before', _style_block())
        assert "pill.dataset.known = 'true'" in PAGE_JS
        assert "pill.dataset.known = 'false'" in PAGE_JS

    def test_the_modal_badges_are_a_dot_and_a_word(self):
        assert "function _status(" in MODAL_JS
        assert "function _badge(" not in MODAL_JS
        assert "Dry-run (global off)" in MODAL_JS


class TestCredentialsSayNothingUntilTyped:
    def test_no_credential_row_ships_a_status_word(self):
        statuses = re.findall(r'<span class="cred-status"[^>]*>(.*?)</span>', TEMPLATE)
        assert len(statuses) == 9
        assert all(text == "" for text in statuses), statuses

    def test_the_status_reads_only_ready_to_save_once_typed(self):
        assert "'Ready to save' : ''" in PAGE_JS
        assert "unchanged" not in PAGE_JS

    def test_an_empty_status_takes_no_room(self):
        assert re.search(r"\.cred-status:empty\s*\{\s*display:\s*none", _style_block())

    def test_the_note_that_blank_fields_are_ignored_is_still_stated_once(self):
        assert TEMPLATE.count("Blank fields are ignored") == 1


class TestTextOnAFilledControlClearsTheBar:
    def test_no_white_text_on_a_filled_control(self):
        for name, css in (("settings.html", _style_block()), ("settings-modal.js", _modal_css())):
            assert not re.search(r"color:\s*var\(--studio-white", css), name
            assert not re.search(r"color:\s*#", css), name

    def test_the_modal_uses_no_bare_hex_colour(self):
        assert not re.search(
            r"#[0-9a-fA-F]{3,8}\b(?![-\w])", _modal_css().replace("#sym-settings-modal", "")
        )


class TestStatesExist:
    def test_the_modal_controls_have_hover_and_focus_states(self):
        css = _modal_css()
        assert ":hover" in css and ":focus-visible" in css

    def test_the_page_controls_have_a_focus_ring(self):
        assert ":focus-visible" in _style_block()

    def test_the_modal_stylesheet_is_added_once(self):
        assert MODAL_JS.count("document.getElementById(MODAL_STYLE_ID)") == 1
