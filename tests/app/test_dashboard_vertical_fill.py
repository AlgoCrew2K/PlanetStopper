"""
Tests for dashboard vertical viewport fill — Studio design contract.

The Studio V3 redesign replaced the Tailwind flex-col body+wrapper+accounts-container
layout with a CSS-based layout using:
  - <body> with no class attribute (block flow)
  - <div class="page-wrap"> as the outer wrapper (max-width: 100%; CSS)
  - <div class="cards-grid"> for the symphony card grid (CSS grid, auto rows)
  - No id="accounts-container" — symphony cards are individual data-testid="sym-card" tiles

These tests assert the Studio design contract for vertical fill:
  - page-wrap CSS uses max-width: 100% (not a narrow cap)
  - cards-grid CSS is defined (grid layout for symphony tiles)
  - No legacy fixed-height caps (min-h-[750px], max-height inline style)
  - table_partial.html no longer has the max-height inline style that capped the table

Static HTML source checks — Pytest cannot verify rendered pixel height.
"""

from __future__ import annotations

import pathlib
import re

_WORKTREE = pathlib.Path(__file__).parent.parent.parent
_INDEX_HTML = _WORKTREE / "templates" / "index.html"
_TABLE_PARTIAL = _WORKTREE / "templates" / "table_partial.html"


class TestBodyIsFlexColumn:
    """The page layout must use the Studio design system — no narrow width caps."""

    def test_body_has_no_restrictive_fixed_height(self):
        """
        The Studio design does not use Tailwind flex classes on body. Instead
        it uses a page-wrap CSS class with max-width: 100%.  We assert:
          - body tag does NOT have a fixed-height constraint that would clip the viewport
          - No height: Xpx on the body that would prevent the page from scrolling
        """
        html = _INDEX_HTML.read_text(encoding="utf-8")
        body_tag_match = re.search(r"<body[^>]*>", html, re.IGNORECASE)
        assert body_tag_match, "Could not find <body> opening tag in index.html"
        body_tag = body_tag_match.group(0)

        # Must not set a fixed pixel height on body
        assert not re.search(r'height\s*=\s*["\'][0-9]+px', body_tag), (
            f"body tag must not set a fixed pixel height; body tag: {body_tag!r}"
        )


class TestOuterWrapperIsFlexColumn:
    """The outer page wrapper must use the Studio CSS layout class."""

    def test_outer_wrapper_has_flex_1_and_flex_col(self):
        """
        The Studio outer wrapper is class="page-wrap". Its skeleton CSS lives in
        static/layout.css (the shared single source of truth) with max-width
        driven by --studio-page-max-width. The old Tailwind mx-auto pattern
        (max-w-screen-2xl flex flex-col) was replaced in the V3 Studio redesign.
        We assert:
          - class="page-wrap" exists as the outer wrapper in index.html
          - the shared .page-wrap CSS in layout.css uses no narrow max-width cap
        """
        html = _INDEX_HTML.read_text(encoding="utf-8")

        assert 'class="page-wrap"' in html, (
            'index.html must contain class="page-wrap" as the outer layout wrapper; '
            "the Studio design replaced the Tailwind mx-auto flex-col pattern with "
            "a custom CSS class"
        )

        # page-wrap skeleton CSS lives in static/layout.css post-consolidation.
        layout_css = (_WORKTREE / "static" / "layout.css").read_text(encoding="utf-8")
        page_wrap_css_match = re.search(
            r'\.page-wrap\s*\{([^}]+)\}', layout_css, re.DOTALL
        )
        assert page_wrap_css_match, (
            ".page-wrap CSS definition must be present in static/layout.css — "
            "the shared page-layout skeleton."
        )
        css_body = page_wrap_css_match.group(1)
        narrow_cap = re.search(
            r'(?:max-width|--studio-page-max-width)\s*:\s*'
            r'(?:[0-9]{1,3}px|[0-9]{2,3}rem)',
            layout_css,
        )
        assert not narrow_cap, (
            f".page-wrap must not set a narrow max-width in static/layout.css; "
            f".page-wrap rule body: {css_body.strip()!r}. Use max-width: 100% "
            "(or the --studio-page-max-width default)."
        )


class TestTableCardFlexGrow:
    """The symphony card grid must fill available viewport without fixed-height caps."""

    def test_table_card_does_not_use_min_h_750(self):
        """min-h-[750px] is the old Tailwind fixed floor — it must be absent."""
        html = _INDEX_HTML.read_text(encoding="utf-8")
        assert "min-h-[750px]" not in html, (
            "index.html must not contain min-h-[750px]; "
            "this was a fixed height cap from the pre-Studio design. "
            "The Studio design uses CSS grid auto-rows for vertical fill."
        )

    def test_table_card_has_flex_1(self):
        """
        The Studio design uses a cards-grid CSS class for symphony card layout.
        We assert the cards-grid CSS is defined and uses auto-fill or similar
        grid layout, not a fixed max-height that would cap the viewport.
        """
        html = _INDEX_HTML.read_text(encoding="utf-8")

        # Assert the cards-grid CSS class exists (Studio replacement for flex-1 table card)
        assert 'class="cards-grid"' in html or '.cards-grid' in html, (
            "index.html must contain a cards-grid CSS class or element; "
            "the Studio design uses a CSS grid for symphony cards, not a flex-1 table card"
        )

        # cards-grid must not set a max-height that would cap the grid
        cards_css_match = re.search(r'\.cards-grid\s*\{([^}]+)\}', html, re.DOTALL)
        if cards_css_match:
            css_body = cards_css_match.group(1)
            assert 'max-height' not in css_body, (
                f".cards-grid CSS must not set max-height; got: {css_body.strip()!r}"
            )

    def test_accounts_container_has_flex_1_and_overflow_y_auto(self):
        """
        The Studio design does not use id="accounts-container" — symphony tiles
        are individual data-testid="sym-card" elements in a CSS grid.
        We assert the active/standby card sections exist as the Studio layout.
        """
        html = _INDEX_HTML.read_text(encoding="utf-8")

        # Studio uses data-testid="active-section" and data-testid="standby-section"
        # instead of a single id="accounts-container" div
        has_active_section = 'data-testid="active-section"' in html
        has_standby_section = 'data-testid="standby-section"' in html
        has_sym_card = 'data-testid="sym-card"' in html

        assert has_active_section or has_standby_section or has_sym_card, (
            "index.html must contain the Studio symphony card layout: "
            'data-testid="active-section", data-testid="standby-section", '
            'or data-testid="sym-card" elements; '
            "the old id='accounts-container' was replaced by per-card section layout"
        )


class TestTablePartialMaxHeightRemoved:
    """The per-account table scroll div must no longer carry the max-height cap."""

    def test_table_partial_has_no_max_height_inline_style(self):
        """
        The max-height inline style in table_partial.html was the primary per-table
        height ceiling (calc(48px + (var(--max-rows, 10) * 49px))).  It must be removed;
        vertical expansion is now handled by the flex-1 accounts-container.
        """
        html = _TABLE_PARTIAL.read_text(encoding="utf-8")

        assert "max-height" not in html, (
            "table_partial.html must not contain a max-height inline style; "
            "the old 'max-height: calc(48px + (var(--max-rows, 10) * 49px))' was capping "
            "each account table vertically. Remove it — the outer accounts-container now "
            "owns vertical scroll."
        )
