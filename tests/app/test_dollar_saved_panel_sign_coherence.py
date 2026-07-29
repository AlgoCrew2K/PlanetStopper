"""
RED tests — dashboard $-saved panel sign coherence (static/index.js + templates/index.html).

THE BUG (corrected from the original kickoff brief -- this surface is NOT
already-compliant): fetchGuardAlphaSummary's snapshot headline
(static/index.js:1432-1435) and its realized-basis sibling (:1454-1457) both
do `(value < 0 ? '-$' : '$') + Math.abs(value).toFixed(2)` -- a NAKED MINUS --
paired with a STATIC caption that always reads "saved"/"realized"
(templates/index.html:1060, :1072) regardless of sign. A losing window
renders "-$50.00 saved across 3 exits" -- exactly the "naked minus under a
saved label" pattern the operator ruling forbids.

THE FIX (operator-locked convention): render the ABS magnitude with NO sign
character; a dedicated verb element (new ids introduced by this fix,
`dollar-saved-verb` / `dollar-saved-realized-verb`) carries "saved"/"lost"
driven by sign -- mirrors analytics.format_dollar_saved's contract on the
Python side, reimplemented locally in JS (no shared module between
index.js/history.js exists in this codebase).

AC-2's windowing wiring is ALSO pinned here: the existing hero-strip window
picker (data-testid="window-30d" etc, index.html:838-844) already tracks
`_heroWindow` (index.js:7) and re-fetches /api/hero-chart + /api/strip on
click (index.js:1503-1512) -- fetchGuardAlphaSummary must join that same
click handler and read the SAME active window token, so the $-saved panel
re-windows in lockstep with the strip/chart instead of always showing
all-time.

Follows the established source-window regex-extraction pattern from
tests/app/test_dollar_saved_display_contract.py (`_js_block`) -- this
codebase asserts JS behavior via targeted source-text assertions, not a
jsdom/execution harness.
"""

from __future__ import annotations

import pathlib
import re

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent
_INDEX_HTML = _PROJECT_ROOT / "templates" / "index.html"
_INDEX_JS = _PROJECT_ROOT / "static" / "index.js"


def _html() -> str:
    return _INDEX_HTML.read_text(encoding="utf-8")


def _js() -> str:
    return _INDEX_JS.read_text(encoding="utf-8")


def _js_block(source: str, anchor: str, span: int = 2500) -> str:
    """A source window following `anchor` -- wide enough to hold the function
    or listener body without needing a JS parser. Same helper shape as
    test_dollar_saved_display_contract.py's `_js_block`."""
    idx = source.find(anchor)
    assert idx != -1, f"static/index.js no longer contains {anchor!r}"
    return source[idx : idx + span]


# ---------------------------------------------------------------------------
# Template: new verb-carrying ids exist (JS needs a hook to swap the word)
# ---------------------------------------------------------------------------


class TestTemplateVerbElementsExist:
    def test_snapshot_headline_has_a_verb_element(self):
        html = _html()
        assert re.search(r'id="dollar-saved-verb"', html), (
            "index.html must add a dedicated element (id=dollar-saved-verb) so "
            "fetchGuardAlphaSummary can swap the word 'saved'/'lost' independently "
            "of the static 'across'/'exits' text -- today the caption is one static "
            "span that never changes"
        )

    def test_realized_headline_has_a_verb_element(self):
        html = _html()
        assert re.search(r'id="dollar-saved-realized-verb"', html), (
            "index.html must add a dedicated element (id=dollar-saved-realized-verb) "
            "for the realized-basis headline's sign-driven word -- same requirement "
            "as the snapshot headline, currently missing entirely (today's caption "
            "is the static word 'realized', which carries no direction at all)"
        )


# ---------------------------------------------------------------------------
# JS: snapshot headline -- no naked minus, verb element is set by sign
# ---------------------------------------------------------------------------


class TestSnapshotHeadlineNoNakedMinus:
    def test_fetch_guard_alpha_summary_does_not_prefix_naked_minus(self):
        block = _js_block(_js(), "function fetchGuardAlphaSummary")
        assert "'-$'" not in block, (
            "fetchGuardAlphaSummary must not prepend a literal '-$' for a negative "
            "value -- the magnitude must be ABS with no sign character at all; "
            "direction is conveyed by color + the verb element alone"
        )

    def test_fetch_guard_alpha_summary_sets_the_verb_element_by_sign(self):
        block = _js_block(_js(), "function fetchGuardAlphaSummary")
        assert "dollar-saved-verb" in block, (
            "fetchGuardAlphaSummary must reference the new dollar-saved-verb element "
            "to swap the word by sign"
        )
        assert "'lost'" in block, (
            "fetchGuardAlphaSummary must reference the literal word 'lost' for the "
            "negative branch -- the antonym of the existing 'saved' wording"
        )

    def test_realized_headline_does_not_prefix_naked_minus(self):
        # A wider span than the other assertions in this file -- the realized
        # branch sits near the END of fetchGuardAlphaSummary's ~50-line body,
        # past the default 2500-char window.
        block = _js_block(_js(), "function fetchGuardAlphaSummary", span=5000)
        # Scope to the realized-basis branch specifically -- both branches live
        # in the same function, so re-derive the sub-block via its own anchor.
        realized_idx = block.find("realizedSaved")
        assert realized_idx != -1, "fetchGuardAlphaSummary must still handle realizedSaved"
        realized_block = block[realized_idx : realized_idx + 400]
        assert "'-$'" not in realized_block, (
            "the realized-basis headline must not prepend a literal '-$' either -- "
            "same ABS-magnitude-no-sign contract as the snapshot headline"
        )

    def test_realized_headline_sets_its_own_verb_element_by_sign(self):
        block = _js_block(_js(), "function fetchGuardAlphaSummary", span=5000)
        assert "dollar-saved-realized-verb" in block, (
            "fetchGuardAlphaSummary must reference dollar-saved-realized-verb to "
            "drive the realized headline's word by sign, independent of the "
            "snapshot headline's verb element"
        )


# ---------------------------------------------------------------------------
# JS: window-picker wiring -- $-saved panel re-fetches on the same click
# ---------------------------------------------------------------------------


class TestDollarSavedPanelJoinsWindowPicker:
    def test_hero_window_initial_value_is_a_token_string_not_a_bare_number(self):
        """_heroWindow is read by the window-picker click handler and (per this
        fix) by fetchGuardAlphaSummary -- it must be initialized to the SAME
        token shape ('30d', a string) the click handler assigns
        (`_heroWindow = token` where token is e.g. '30d'), matching the
        already-active 30d button in the markup (index.html's window-30d
        button carries class="active" by default). Today it is initialized as
        the bare number 30, a type mismatch with every post-click value.
        """
        js = _js()
        assert "var _heroWindow = '30d';" in js or 'var _heroWindow = "30d";' in js, (
            "_heroWindow must be initialized as the string token '30d' (matching the "
            "actively-highlighted 30d button), not the bare number 30 -- the "
            "window-picker click handler always assigns a string token, so the "
            "pre-click value should share that shape"
        )

    def test_fetch_guard_alpha_summary_accepts_a_window_token_argument(self):
        # The function signature itself must accept a parameter -- a bare
        # `function fetchGuardAlphaSummary()` with no params cannot receive
        # the active window token from the click handler.
        match = re.search(r"function fetchGuardAlphaSummary\(([^)]*)\)", _js())
        assert match is not None, "fetchGuardAlphaSummary must still be defined as a function"
        assert match.group(1).strip() != "", (
            "fetchGuardAlphaSummary must accept a window-token parameter -- it is "
            "currently a zero-arg function and cannot be windowed by the picker"
        )

    def test_fetch_guard_alpha_summary_includes_window_in_its_request_url(self):
        block = _js_block(_js(), "function fetchGuardAlphaSummary")
        assert "/api/guard-alpha-summary" in block
        assert "window" in block, (
            "fetchGuardAlphaSummary must include a `window=` query parameter in its "
            "fetch URL, sourced from its window-token argument -- today it always "
            "fetches the bare all-time endpoint"
        )

    def test_window_picker_click_handler_also_refetches_dollar_saved_summary(self):
        """The click handler that already re-fetches /api/hero-chart and the
        windowed strip (index.js ~1503-1512) must ALSO call
        fetchGuardAlphaSummary(token) so the $-saved panel re-windows in
        lockstep with the rest of the hero -- today it only updates the chart
        and the vs-rows."""
        source = _js()
        anchor = "var windowTokenMap = {"
        idx = source.find(anchor)
        assert idx != -1, "static/index.js no longer contains the window-picker token map"
        handler_block = source[idx : idx + 4000]
        assert "fetchGuardAlphaSummary(" in handler_block, (
            "the window-picker click handler must call fetchGuardAlphaSummary(token) "
            "alongside its existing /api/hero-chart + windowed-strip fetches -- "
            "otherwise the $-saved panel silently stays on whatever window it loaded "
            "with while every other hero metric re-windows live"
        )
