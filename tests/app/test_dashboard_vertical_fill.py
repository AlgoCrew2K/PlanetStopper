"""
Tests for symphony table vertical viewport fill (task: fix(dashboard) vertical sizing).

Asserts the markup contract introduced by the vertical-fill fix:
  - body uses flex flex-col so children can grow
  - outer max-w-screen-2xl wrapper is a flex column
  - table card uses flex-1 (not min-h-[750px]) so it expands to fill available space
  - accounts-container uses flex-1 overflow-y-auto for internal scroll
  - table_partial.html no longer has the max-height inline style that capped the table

These are static HTML source checks — Pytest cannot verify rendered pixel height
(visual verification was done with before/after screenshots at 1920x1080).
"""

from __future__ import annotations

import pathlib
import re

import pytest

_WORKTREE = pathlib.Path(__file__).parent.parent.parent
_INDEX_HTML = _WORKTREE / "templates" / "index.html"
_TABLE_PARTIAL = _WORKTREE / "templates" / "table_partial.html"


class TestBodyIsFlexColumn:
    """The body element must be a flex column for children to grow vertically."""

    def test_body_has_flex_and_flex_col(self):
        """body tag must include both 'flex' and 'flex-col' classes."""
        html = _INDEX_HTML.read_text(encoding="utf-8")
        body_tag_match = re.search(r"<body\s+class=\"([^\"]+)\"", html)
        assert body_tag_match, "Could not find <body class=...> in index.html"
        classes = body_tag_match.group(1)
        assert "flex" in classes.split(), (
            f"body must have 'flex' class for flex layout; got: {classes!r}"
        )
        assert "flex-col" in classes.split(), (
            f"body must have 'flex-col' class to stack children vertically; got: {classes!r}"
        )


class TestOuterWrapperIsFlexColumn:
    """The max-w-screen-2xl wrapper must participate in the flex column."""

    def test_outer_wrapper_has_flex_1_and_flex_col(self):
        """The outer wrapper div (mx-auto) must have flex-1 and flex-col."""
        html = _INDEX_HTML.read_text(encoding="utf-8")
        outer_match = re.search(r"<div\s+class=\"([^\"]*mx-auto[^\"]+)\"", html)
        assert outer_match, "Could not find outer wrapper div with mx-auto in index.html"
        classes = outer_match.group(1)
        class_list = classes.split()
        assert "flex-1" in class_list, (
            f"Outer wrapper must have 'flex-1' to expand within the body column; got: {classes!r}"
        )
        assert "flex" in class_list, (
            f"Outer wrapper must have 'flex' to establish flex context; got: {classes!r}"
        )
        assert "flex-col" in class_list, (
            f"Outer wrapper must have 'flex-col' so children stack vertically; got: {classes!r}"
        )
        assert "min-h-0" in class_list, (
            f"Outer wrapper must have 'min-h-0' to allow shrinking below intrinsic height; got: {classes!r}"
        )


class TestTableCardFlexGrow:
    """The table card must use flex-1 to fill remaining viewport, not a fixed min-height."""

    def test_table_card_does_not_use_min_h_750(self):
        """min-h-[750px] is the old fixed floor — it must be removed."""
        html = _INDEX_HTML.read_text(encoding="utf-8")
        assert "min-h-[750px]" not in html, (
            "index.html must not contain min-h-[750px]; "
            "this was the fixed height ceiling that prevented the table from filling the viewport. "
            "Replace with flex-1 flex flex-col min-h-0."
        )

    def test_table_card_has_flex_1(self):
        """The table card (the bg-slate-800 div wrapping accounts-container) must have flex-1."""
        html = _INDEX_HTML.read_text(encoding="utf-8")

        # Find the table card — it contains the 'Symphony Status by Account' heading
        # and the accounts-container.  We look for flex-1 near that section.
        card_section_start = html.find("Symphony Status by Account")
        assert card_section_start != -1, (
            "index.html must contain 'Symphony Status by Account' heading in the table card"
        )

        # Walk back ~500 chars to find the opening div of the card
        # (the card div is ~280 chars before the heading due to the comment + inner div)
        card_open_region = html[max(0, card_section_start - 500) : card_section_start]

        # The card's opening div must have flex-1
        flex1_match = re.search(r"<div[^>]*flex-1[^>]*>", card_open_region)
        assert flex1_match, (
            "The table card div (wrapping 'Symphony Status by Account') must have 'flex-1' "
            "class so it expands to fill remaining viewport height. "
            f"Region searched (last 500 chars before heading): {card_open_region!r}"
        )

    def test_accounts_container_has_flex_1_and_overflow_y_auto(self):
        """#accounts-container must have flex-1 and overflow-y-auto for internal scroll."""
        html = _INDEX_HTML.read_text(encoding="utf-8")

        container_match = re.search(
            r'<div[^>]*id=["\']accounts-container["\'][^>]*>',
            html,
        )
        assert container_match, "#accounts-container element must exist in index.html"

        tag = container_match.group(0)
        assert "flex-1" in tag, (
            f"#accounts-container must have 'flex-1' to expand within the card; tag: {tag!r}"
        )
        assert "overflow-y-auto" in tag, (
            f"#accounts-container must have 'overflow-y-auto' for internal scroll when rows exceed space; tag: {tag!r}"
        )
        assert "min-h-0" in tag, (
            f"#accounts-container must have 'min-h-0' to allow shrinking; tag: {tag!r}"
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
