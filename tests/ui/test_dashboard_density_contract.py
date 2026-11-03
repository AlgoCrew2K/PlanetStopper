"""The dashboard's design contract after the unslop pass: a ledger, not a wall of cards.

What the pass built, pinned where it can be pinned without a browser:

* The page's own rules keep to two weights (400 and 500), no upper-case labels, no italics, and no
  text under 11 px; a negative letter spacing is only for display figures.
* A state is a dot and one word: the status pill has no fill, border or radius of its own, and a
  dot is drawn in front of the word.
* Each symphony is one ledger row: the Today figures are shown once (the dual-value headline is the
  first column of the card's ledger), Bot and If held are named in that column alone, and the
  desktop layout puts the card's parts on one grid row.
* No glyph stands in for an icon (the circled i, the multiplication-sign close, the diamond).

The rendered page is judged on a screenshot at the gate and by ``test_dashboard_density_browser.py``;
these pins hold the source those renders come from.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from tests.ui import _dashboard_primitives_capture as cap

REPO = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "templates" / "index.html"
SCRIPT = REPO / "static" / "index.js"

# A figure shown large: the only text a negative letter spacing is allowed on.
DISPLAY_FIGURE_SELECTORS = (".guard-alpha-headline", ".dollar-figure", ".detail-stat-value")


def _template() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _page_css() -> str:
    blocks = re.findall(
        r"<style\b[^>]*>(.*?)</style>", _template(), flags=re.IGNORECASE | re.DOTALL
    )
    assert blocks, "templates/index.html carries no inline style block"
    return re.sub(r"/\*.*?\*/", "", "\n".join(blocks), flags=re.DOTALL)


def _rules(css: str):
    """(selector text, declaration text) for each rule, media wrappers flattened."""
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        yield selectors.strip(), body


def _declarations(body: str) -> dict[str, str]:
    out = {}
    for part in body.split(";"):
        if ":" in part:
            name, value = part.split(":", 1)
            out[name.strip()] = value.strip()
    return out


@pytest.fixture(scope="module")
def dashboard_html() -> str:
    import database

    database.init_db()
    database.run_migrations()
    return cap.render_dashboard_html()


# ---------------------------------------------------------------------------
# Type
# ---------------------------------------------------------------------------


class TestTypeContract:
    def test_the_page_declares_only_the_two_weights(self):
        weights = {
            value
            for _sel, body in _rules(_page_css())
            for name, value in _declarations(body).items()
            if name == "font-weight"
        }
        assert weights <= {"400", "500"}, f"font weights beyond 400/500: {sorted(weights)}"

    def test_no_rule_uppercases_text(self):
        offenders = [
            sel
            for sel, body in _rules(_page_css())
            if _declarations(body).get("text-transform") == "uppercase"
        ]
        assert offenders == [], f"upper-case rules: {offenders}"

    def test_no_rule_italicises_text(self):
        offenders = [
            sel
            for sel, body in _rules(_page_css())
            if _declarations(body).get("font-style") == "italic"
        ]
        assert offenders == []
        assert "font-style:italic" not in re.sub(r"\s+", "", _template())

    def test_no_text_is_set_under_11px(self):
        small = []
        for sel, body in _rules(_page_css()):
            size = _declarations(body).get("font-size", "")
            found = re.fullmatch(r"([\d.]+)px", size)
            if found and float(found.group(1)) < 11:
                small.append((sel, size))
        assert small == [], f"text under 11 px: {small}"

    def test_a_negative_letter_spacing_is_only_on_display_figures(self):
        offenders = []
        for sel, body in _rules(_page_css()):
            spacing = _declarations(body).get("letter-spacing", "")
            if spacing.startswith("-") and not any(
                part.strip() in DISPLAY_FIGURE_SELECTORS for part in sel.split(",")
            ):
                offenders.append((sel, spacing))
        assert offenders == [], offenders


# ---------------------------------------------------------------------------
# A state is a dot and one word
# ---------------------------------------------------------------------------


class TestStatusIsADotAndAWord:
    def _status_rule(self) -> dict[str, str]:
        for sel, body in _rules(_page_css()):
            if sel.strip() == ".page-dashboard .status-pill":
                return _declarations(body)
        pytest.fail("the dashboard refines no .status-pill rule")

    def test_the_status_has_no_fill_border_or_radius_of_its_own(self):
        rule = self._status_rule()
        assert rule.get("background") == "none"
        assert rule.get("border") == "0"
        assert rule.get("border-radius") == "0"
        assert rule.get("text-transform") == "none"

    def test_a_dot_is_drawn_before_the_word(self):
        dots = [
            _declarations(body)
            for sel, body in _rules(_page_css())
            if sel.strip() == ".page-dashboard .status-pill::before"
        ]
        assert len(dots) == 1
        assert dots[0].get("border-radius") == "50%"
        assert dots[0].get("width") == dots[0].get("height") == "8px"

    def test_every_state_class_the_script_writes_has_a_dot_colour(self):
        script = SCRIPT.read_text(encoding="utf-8")
        classes = set(re.findall(r"cls: '([a-z-]+)'", script))
        assert classes, "the script writes no status class"
        css = _page_css()
        for name in sorted(classes):
            assert re.search(
                rf"\.page-dashboard \.status-pill\.{re.escape(name)}\b[^{{]*{{[^}}]*--dot", css
            ), f"status class {name!r} has no --dot colour"

    def test_the_status_words_are_sentence_case(self):
        script = SCRIPT.read_text(encoding="utf-8")
        words = re.findall(r"text: '([^']+)'", script)
        assert words, "the script writes no status words"
        for word in words:
            assert not (word.isupper() and len(word) > 4), f"{word!r} is shouted"
        for shouted in ("TAKE-PROFIT", "TRAILING STOP", "VWAP BLEED"):
            assert shouted not in _template()


# ---------------------------------------------------------------------------
# One ledger row per symphony
# ---------------------------------------------------------------------------


class TestOneRowPerSymphony:
    def test_the_today_figures_are_shown_once_per_card(self, dashboard_html):
        cards = dashboard_html.count('data-testid="sym-card"')
        assert cards >= 2
        assert dashboard_html.count('data-field="tc-bot"') == cards
        assert dashboard_html.count('data-field="tc-held"') == cards
        assert dashboard_html.count('data-testid="dual-value-headline"') == cards

    def test_the_today_column_is_the_first_cell_of_the_ledger(self, dashboard_html):
        grids = re.findall(
            r'<div class="card-footer-grid">\s*<div data-testid="dual-value-headline"',
            dashboard_html,
        )
        assert len(grids) == dashboard_html.count('data-testid="sym-card"')

    def test_bot_and_if_held_are_named_in_the_first_column_only(self):
        css = _page_css()
        hidden = re.search(
            r"\.card-footer-grid > \.cfg-cell ~ \.cfg-cell \.cfg-tag\s*{[^}]*clip", css
        )
        assert hidden, "the other columns still print their row names"

    def test_the_sections_are_one_list_each(self, dashboard_html):
        assert dashboard_html.count('class="cards-grid sym-list"') == 2

    def test_the_desktop_layout_is_one_grid_row_per_card(self):
        css = _page_css()
        media = re.search(r"@media \(min-width: 1240px\)\s*{(.*?)\n    }\n", css, flags=re.DOTALL)
        assert media, "no desktop (min-width: 1240px) layout"
        text = media.group(1)
        assert re.search(r"\.card-top\s*{\s*display:\s*contents;", text)
        for part in (".card-id", ".card-actions", ".card-footer-grid", ".card-spark-col"):
            assert re.search(rf"{re.escape(part)}\s*{{[^}}]*grid-row:\s*1;", text), part

    def test_the_stat_boxes_are_one_row_of_figures(self, dashboard_html):
        assert len(re.findall(r'data-testid="mini-stat"', dashboard_html)) == 3
        css = _page_css()
        stat = next(
            _declarations(body)
            for sel, body in _rules(css)
            if sel.strip() == ".page-dashboard .mini-stat"
        )
        assert stat.get("background") == "none"
        assert stat.get("padding") == "0"


# ---------------------------------------------------------------------------
# No glyph stands in for an icon
# ---------------------------------------------------------------------------


class TestNoGlyphIcons:
    @pytest.mark.parametrize("entity", ["&#9432;", "&#x2715;", "&#9670;", "&#x24D8;"])
    def test_the_template_carries_no_glyph_icon(self, entity):
        assert entity not in _template()

    @pytest.mark.parametrize("glyph", ["◆", "✕", "ⓘ"])
    def test_the_script_writes_no_glyph_icon(self, glyph):
        assert glyph not in SCRIPT.read_text(encoding="utf-8")

    def test_the_info_markers_are_line_icons_with_the_basis_in_their_title(self, dashboard_html):
        for testid in ("today-held-footnote-marker", "cumulative-cashflow-footnote-marker"):
            marker = re.search(
                rf'<span data-testid="{testid}" class="info-mark" title="([^"]+)">\s*<svg',
                dashboard_html,
            )
            assert marker, f"{testid} is not an icon with a title"
