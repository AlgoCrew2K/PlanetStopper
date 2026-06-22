"""
Regression tests for config-suggestion card fixes — overflow + chat button.

These tests verify the three changes applied to the config-suggestion surface
(rendered by static/ai_advisor.js renderSuggestions), which were previously
missing the fixes already present on asset-swap and logic-change cards:

  1. OVERFLOW (grid floor)  — templates/ai_advisor.html .suggestions-col uses
     minmax(22rem, 1fr) so 3 cards fit without overflow.

  2. OVERFLOW (button wrap) — ai_advisor.js card inner flex row has flex-wrap:wrap
     and the left content div has min-width:0, mirroring ai_advisor_asset_swaps.js.

  3. CHAT BUTTON            — ai_advisor.js renderSuggestions appends a .card-actions
     div containing a chat-about-btn (data-testid="chat-about-this-btn") that calls
     openChatPanel or falls back to /ai-advisor/chat.

All tests are source-level (static analysis) — no live daemon or browser required.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_WORKTREE_ROOT = Path(__file__).resolve().parents[2]
_AI_ADVISOR_JS = _WORKTREE_ROOT / "static" / "ai_advisor.js"
_AI_ADVISOR_HTML = _WORKTREE_ROOT / "templates" / "ai_advisor.html"


def _read_js() -> str:
    return _AI_ADVISOR_JS.read_text(encoding="utf-8")


def _read_html() -> str:
    return _AI_ADVISOR_HTML.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Fix 1 — grid floor: minmax(22rem, 1fr)
# ---------------------------------------------------------------------------


class TestGridFloor:
    """templates/ai_advisor.html .suggestions-col must use minmax(22rem, 1fr)."""

    def test_suggestions_col_uses_22rem_minmax(self):
        """The .suggestions-col grid-template-columns must use minmax(22rem, 1fr).

        The old value (28rem) prevented 3 cards from fitting the column width
        at typical viewport widths, causing overflow.  22rem fits 3 cards.
        """
        html = _read_html()
        assert "minmax(22rem, 1fr)" in html, (
            "templates/ai_advisor.html .suggestions-col is missing "
            "'minmax(22rem, 1fr)'. "
            "The grid-template-columns value must use 22rem (not 28rem) so "
            "3 config-suggestion cards fit the column without overflow."
        )

    def test_suggestions_col_does_not_use_28rem(self):
        """The .suggestions-col must not use the old minmax(28rem, 1fr) value."""
        html = _read_html()
        assert "minmax(28rem, 1fr)" not in html, (
            "templates/ai_advisor.html .suggestions-col still uses "
            "minmax(28rem, 1fr). Update to minmax(22rem, 1fr)."
        )


# ---------------------------------------------------------------------------
# Fix 2a — card flex row has flex-wrap:wrap
# ---------------------------------------------------------------------------


class TestCardFlexWrap:
    """ai_advisor.js renderSuggestions card inner flex row must have flex-wrap:wrap.

    The right-hand button column anchors right and overflows the card at narrow
    widths without flex-wrap.  This mirrors the fix in ai_advisor_asset_swaps.js.
    """

    def test_card_row_has_flex_wrap_wrap(self):
        """The card's inner flex container must declare flex-wrap:wrap."""
        src = _read_js()
        # The flex row that wraps content + button column.
        # After the fix it reads: display:flex;...flex-wrap:wrap;
        assert re.search(
            r"display\s*:\s*flex.*?flex-wrap\s*:\s*wrap",
            src,
            re.DOTALL,
        ), (
            "ai_advisor.js renderSuggestions card row is missing flex-wrap:wrap. "
            "Add flex-wrap:wrap to the inner flex container so the button column "
            "wraps below the content column at narrow card widths."
        )

    def test_card_content_div_has_min_width_0(self):
        """The card left content <div> must carry min-width:0.

        Without min-width:0, a flex child with flex:1 cannot shrink below its
        intrinsic content width, so the text column overflows the card.
        """
        src = _read_js()
        # After the fix: 'flex:1;min-width:0;'
        assert (
            re.search(
                r"flex\s*:\s*1[^;]*;[^'\"]*min-width\s*:\s*0",
                src,
            )
            or "flex:1;min-width:0;" in src
            or "flex:1; min-width:0;" in src
        ), (
            "ai_advisor.js renderSuggestions left content div is missing min-width:0. "
            "Without it the text column resists shrinking and overflows the card. "
            "Add min-width:0 to the div with flex:1."
        )


# ---------------------------------------------------------------------------
# Fix 2b — ai_advisor.js chat button presence and structure
# ---------------------------------------------------------------------------


class TestChatAboutThisButton:
    """ai_advisor.js renderSuggestions must produce a 'Chat about this' button.

    The config-suggestion cards previously rendered only Apply/Blocked + Dismiss.
    This fix adds a .card-actions div with a chat-about-btn on every card,
    matching the pattern in ai_advisor_asset_swaps.js lines 246-260.
    """

    def test_render_suggestions_contains_chat_about_this_text(self):
        """ai_advisor.js renderSuggestions must emit 'Chat about this' text."""
        src = _read_js()
        assert re.search(r"[Cc]hat about this", src), (
            "ai_advisor.js renderSuggestions does not contain 'Chat about this'. "
            "Add a chat-about-btn to the config-suggestion cards."
        )

    def test_chat_button_has_data_testid_chat_about_this_btn(self):
        """The chat button must carry data-testid='chat-about-this-btn'."""
        src = _read_js()
        assert 'data-testid="chat-about-this-btn"' in src or "chat-about-this-btn" in src, (
            "ai_advisor.js renderSuggestions chat button is missing "
            'data-testid="chat-about-this-btn". '
            "Add data-testid='chat-about-this-btn' to the button element."
        )

    def test_chat_button_has_chat_about_btn_class(self):
        """The chat button must carry the class 'chat-about-btn'."""
        src = _read_js()
        assert "chat-about-btn" in src, (
            "ai_advisor.js renderSuggestions chat button is missing class 'chat-about-btn'. "
            "Add class='chat-about-btn' to the button element."
        )

    def test_chat_button_links_to_ai_advisor_chat_route(self):
        """The chat button fallback must navigate to /ai-advisor/chat."""
        src = _read_js()
        assert "/ai-advisor/chat" in src, (
            "ai_advisor.js does not contain '/ai-advisor/chat'. "
            "The chat-about-btn fallback must navigate to /ai-advisor/chat."
        )

    def test_chat_button_calls_open_chat_panel(self):
        """The chat button onclick must call openChatPanel."""
        src = _read_js()
        assert "openChatPanel" in src, (
            "ai_advisor.js does not call openChatPanel. "
            "The chat-about-btn onclick must call openChatPanel(JSON.parse(d)) "
            "with the artifact JSON."
        )

    def test_chat_button_has_card_actions_container(self):
        """The chat button must be wrapped in a .card-actions container."""
        src = _read_js()
        assert "card-actions" in src, (
            "ai_advisor.js renderSuggestions is missing a 'card-actions' container. "
            "Wrap the chat button in a div with class 'card-actions' and "
            "flex-wrap:wrap styling."
        )

    def test_card_actions_has_flex_wrap(self):
        """The .card-actions container must declare flex-wrap:wrap."""
        src = _read_js()
        # Locate the card-actions block and verify flex-wrap:wrap is present.
        card_actions_match = re.search(
            r"card-actions.*?flex-wrap\s*:\s*wrap",
            src,
            re.DOTALL,
        )
        assert card_actions_match, (
            "ai_advisor.js .card-actions container is missing flex-wrap:wrap. "
            "The card-actions div must use display:flex;flex-wrap:wrap;gap to "
            "keep the chat button within card bounds at narrow widths."
        )

    def test_chat_button_carries_artifact_json(self):
        """The chat button must carry data-artifact-json scoping data."""
        src = _read_js()
        chat_section = re.search(
            r"[Cc]hat about this.*?(?:</div>|</button>)",
            src,
            re.DOTALL,
        )
        assert chat_section is not None, (
            "Could not locate 'Chat about this' block in ai_advisor.js."
        )
        block = chat_section.group(0)
        assert "artifact" in block.lower() or "artifactJson" in block or "data-artifact" in block, (
            "The 'Chat about this' button in ai_advisor.js is missing artifact "
            "scoping context (data-artifact-json or equivalent). "
            "The chat panel needs the artifact JSON to pre-fill the context."
        )

    def test_chat_button_scopes_config_key_in_artifact(self):
        """The artifact JSON must include the config_key field for context scoping."""
        src = _read_js()
        assert "config_key" in src, (
            "ai_advisor.js chat button artifact JSON does not reference config_key. "
            "Scope the artifact to the suggestion using config_key, current_value, "
            "suggested_value, rationale, and oos_status."
        )

    def test_chat_button_has_visible_button_styling(self):
        """The 'Chat about this' element must use a <button> with prominent styling."""
        src = _read_js()
        chat_section = re.search(
            r"[Cc]hat about this.*?(?:</button>|</div>)",
            src,
            re.DOTALL,
        )
        assert chat_section is not None, (
            "Could not locate 'Chat about this' block in ai_advisor.js."
        )
        block = chat_section.group(0)
        has_button = "<button" in block
        has_prominent = re.search(
            r"(background|font-weight\s*:\s*[5-9]\d\d|border\s*:)",
            block,
            re.IGNORECASE,
        )
        assert has_button or has_prominent, (
            "The 'Chat about this' element in ai_advisor.js lacks prominent styling. "
            "Use a <button> element with background-color and font-weight."
        )
