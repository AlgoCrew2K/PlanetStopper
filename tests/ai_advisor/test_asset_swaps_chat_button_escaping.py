"""
RED test — Asset Swaps "Chat about this" button onclick escaping (AC-3).

Feature plan: feature-plans/advisor-suite-fixes.md.

THE BUG: static/ai_advisor_asset_swaps.js:256-257 builds the chatBtn's
onclick="..." attribute (a double-quoted HTML attribute) out of JS string
literals that embed RAW double-quotes:
    sessionStorage.setItem("pendingChatArtifact",d);window.location.href="/ai-advisor/chat";
Those embedded `"` characters terminate the attribute value early — a browser
parses onclick="...pendingChatArtifact" as the WHOLE attribute value the
instant it hits the first unescaped `"`, silently dropping the rest of the
handler (including the catch fallback). The button visually renders but does
nothing when clicked.

The correctly-escaped sibling pattern already exists at ai_advisor.js:298-301
(same "Chat about this" button, used by the Suggestion Cards feature) — it
uses single-quoted JS string literals for the embedded string values instead
of double-quoted ones, so nothing inside the double-quoted HTML attribute is
ever a raw `"`.

Test strategy: reconstruct the actual HTML string a browser would receive by
concatenating the source's single-quoted JS string-literal fragments (the
codebase's own string-building idiom — see the `+`-chained literals in both
files), then simulate a browser's naive attribute scan (stop at the first `"`
after `onclick="`). On the buggy file this truncates before reaching
`openChatPanel`/`catch`; on the correct sibling it does not. This proves the
actual truncation defect, not merely "some quote appears somewhere."

Assert structure/contract (attribute is not truncated), never a hardcoded
literal value.
"""

from __future__ import annotations

import pathlib
import re

_ASSET_SWAPS_JS = (
    pathlib.Path(__file__).parent.parent.parent / "static" / "ai_advisor_asset_swaps.js"
)
_AI_ADVISOR_JS = pathlib.Path(__file__).parent.parent.parent / "static" / "ai_advisor.js"

# Shared anchor — both files render a "Chat about this" button carrying this
# exact data-testid, immediately followed by the onclick attribute under test.
_ANCHOR = 'data-testid="chat-about-this-btn"'

# The chatBtn onclick block is ~10-11 lines (~900-1000 chars) after the anchor
# in both files. 1500 gives comfortable margin without spilling into the next
# unrelated statement (which may contain its own onclick=...).
_RECONSTRUCT_WINDOW = 1500

# Matches a single-quoted JS string literal, honoring \x escape pairs so an
# embedded \' does not terminate the match early.
_STRING_LITERAL_RE = re.compile(r"'((?:[^'\\]|\\.)*)'")


def _asset_swaps_js() -> str:
    return _ASSET_SWAPS_JS.read_text(encoding="utf-8")


def _ai_advisor_js() -> str:
    return _AI_ADVISOR_JS.read_text(encoding="utf-8")


def _reconstruct_string_concat(
    js_source: str, anchor: str, window: int = _RECONSTRUCT_WINDOW
) -> str:
    """Reconstruct the actual concatenated string a `'...' + '...' + ...` chain
    produces at runtime, starting from `anchor`. Skips non-literal segments
    (variable interpolations) — irrelevant here since the onclick attribute
    under test is built entirely from literals.
    """
    idx = js_source.find(anchor)
    assert idx != -1, f"anchor {anchor!r} not found in source"
    chunk = js_source[idx : idx + window]
    literals = _STRING_LITERAL_RE.findall(chunk)
    assert literals, f"No single-quoted string literals found near anchor {anchor!r}"
    return "".join(lit.replace("\\'", "'") for lit in literals)


def _onclick_attr_value_naive(html_fragment: str) -> str:
    """Simulate a browser's naive attribute scan: onclick="..." value ends at
    the first `"` after the opening quote. This is exactly the parsing that
    causes the AC-3 truncation defect.
    """
    marker = 'onclick="'
    idx = html_fragment.find(marker)
    assert idx != -1, f"{marker!r} not found in reconstructed fragment"
    start = idx + len(marker)
    end = html_fragment.find('"', start)
    assert end != -1, "no closing quote found at all for onclick attribute"
    return html_fragment[start:end]


# ---------------------------------------------------------------------------
# AC-3 — onclick attribute must not be truncated by an embedded raw quote
# ---------------------------------------------------------------------------


class TestAssetSwapsChatButtonNotTruncated:
    """AC-3: the asset-swaps chat button's onclick handler must survive a
    naive double-quote-terminated attribute scan intact.

    Note: an earlier draft of this class also asserted "openChatPanel" is
    reachable under the naive scan — removed because 'openChatPanel' appears
    BEFORE the first embedded raw `"` (inside the typeof-check earlier in the
    handler), so that assertion passed even on the buggy source without
    actually detecting the truncation. The two tests below target text that
    is genuinely only reachable AFTER the truncation point.
    """

    def test_onclick_attribute_reaches_catch_fallback(self):
        """FAILS on current: same truncation also drops the catch(...) fallback
        entirely — clicking the button when openChatPanel is undefined would
        silently no-op instead of falling back to the chat route.
        """
        reconstructed = _reconstruct_string_concat(_asset_swaps_js(), _ANCHOR)
        onclick_value = _onclick_attr_value_naive(reconstructed)
        assert "catch" in onclick_value, (
            "AC-3 defect: the onclick attribute is truncated before the "
            "catch(...) fallback. "
            f"Naively-scanned value: {onclick_value!r}"
        )

    def test_onclick_attribute_has_no_bare_double_quote_before_true_end(self):
        """Direct assertion of the plan's own wording: no unescaped double-quote
        may appear inside the double-quoted attribute. Verified by requiring the
        naive-scanned value to extend all the way to the handler's real
        terminator `})(event)` — if a bare `"` cut it short, this substring
        would never appear in the naively-scanned (truncated) value.
        """
        reconstructed = _reconstruct_string_concat(_asset_swaps_js(), _ANCHOR)
        onclick_value = _onclick_attr_value_naive(reconstructed)
        assert "})(event)" in onclick_value, (
            "AC-3 defect: the onclick attribute does not extend to its real "
            "terminator '})(event)' under a naive quote scan — an embedded "
            "raw double-quote cut it off first. "
            f"Naively-scanned value: {onclick_value!r}"
        )


class TestControlSiblingPatternIsAlreadyCorrect:
    """Control group: the KNOWN-CORRECT sibling pattern (ai_advisor.js:298-301)
    must already pass the exact same extraction + naive-scan checks. This
    proves the test harness itself is sound — it is not merely always-failing
    regardless of the source content — and it pins the pattern the fix in
    ai_advisor_asset_swaps.js must match.
    """

    def test_sibling_onclick_attribute_reaches_open_chat_panel_call(self):
        reconstructed = _reconstruct_string_concat(_ai_advisor_js(), _ANCHOR)
        onclick_value = _onclick_attr_value_naive(reconstructed)
        assert "openChatPanel" in onclick_value, (
            "Sanity check failed: the KNOWN-CORRECT sibling pattern in "
            "ai_advisor.js did not survive the naive scan either — the test "
            "harness (not the source) is broken. "
            f"Naively-scanned value: {onclick_value!r}"
        )

    def test_sibling_onclick_attribute_reaches_true_terminator(self):
        reconstructed = _reconstruct_string_concat(_ai_advisor_js(), _ANCHOR)
        onclick_value = _onclick_attr_value_naive(reconstructed)
        assert "})(event)" in onclick_value, (
            "Sanity check failed: the KNOWN-CORRECT sibling pattern in "
            "ai_advisor.js did not reach its terminator under the naive scan "
            "— the test harness (not the source) is broken. "
            f"Naively-scanned value: {onclick_value!r}"
        )
