"""
RED tests -- Guard-Alpha Stop-Justification Preconditions panel UI (AC-7).

Source-level assertions only (template string search + node --check via the
existing parametrized tests/js_syntax module -- no new per-file JS syntax
test needed there). No live browser; no Flask test client needed for these
structure checks. Mirrors tests/app/test_guard_alpha_panel_ui.py's convention
(the established pattern for this project, which has no JS e2e/Playwright
framework -- behavioral verification is a PM-driven live-droplet screenshot
gate per the plan's Testing Strategy, not an automated e2e spec).

Contract under test (AC-7):
- templates/performance.html contains the per-symphony verdict table panel
  with distinct data-testid elements, and an honest empty state shown when no
  symphony has sufficient data.
- Verdict chip CSS uses the design system's --modifier BEM pattern (mirrors
  templates/ai_advisor.html's .prism-sentiment-chip--risk-on convention) --
  no raw inline colors, no bare unstyled markup.
- static/performance.js fetches /api/guard-alpha-preconditions, 401-guarded
  (following the fetchGuardAlphaSummary() pattern in static/index.js: check
  response.ok before parsing, never assume success).
"""

from __future__ import annotations

import pathlib

_TEMPLATES_DIR = pathlib.Path(__file__).parent.parent.parent / "templates"
_STATIC_DIR = pathlib.Path(__file__).parent.parent.parent / "static"

_PERFORMANCE_HTML = _TEMPLATES_DIR / "performance.html"
_PERFORMANCE_JS = _STATIC_DIR / "performance.js"


def _html() -> str:
    return _PERFORMANCE_HTML.read_text(encoding="utf-8")


def _js() -> str:
    return _PERFORMANCE_JS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AC-7: panel markup exists
# ---------------------------------------------------------------------------


class TestPreconditionsPanelMarkup:
    def test_panel_container_present(self):
        html = _html()
        assert 'data-testid="guard-alpha-preconditions-panel"' in html, (
            "AC-7 FAIL: templates/performance.html missing "
            'data-testid="guard-alpha-preconditions-panel". The preconditions '
            "panel has not been added to the Performance tab yet."
        )

    def test_verdict_table_present(self):
        html = _html()
        assert 'data-testid="guard-alpha-preconditions-table"' in html, (
            'AC-7 FAIL: missing data-testid="guard-alpha-preconditions-table" '
            "-- the per-symphony rho-vs-SR verdict table."
        )

    def test_honest_empty_state_element_present(self):
        """AC-7: 'the honest empty state when no symphony has sufficient
        data' -- panel-level empty state, not an empty 200 that renders blank
        (Edge Cases: 'All symphonies insufficient -> panel-level empty
        state')."""
        html = _html()
        assert 'data-testid="guard-alpha-preconditions-empty-state"' in html, (
            'AC-7 FAIL: missing data-testid="guard-alpha-preconditions-empty-state".'
        )


# ---------------------------------------------------------------------------
# Design-System Mapping: verdict chip CSS follows the --modifier BEM pattern
# ---------------------------------------------------------------------------


class TestVerdictChipDesignSystemContract:
    """Per the plan's Design-System Mapping: 'reuse the chip modifier pattern
    from templates/ai_advisor.html -- canonical class names, no inline
    styles, no raw colors in JS'. Asserts PRIMITIVE/TOKEN USAGE (class names
    exist and follow the established --modifier convention), never a specific
    computed color value (feedback_tests_assert_design_contract_not_values)."""

    _EXPECTED_MODIFIERS = (
        "precond-verdict-chip--sufficient-met",
        "precond-verdict-chip--sufficient-likely",
        "precond-verdict-chip--not-met",
        "precond-verdict-chip--negative-edge",
        "precond-verdict-chip--insufficient-data",
    )

    def test_base_chip_class_defined_in_stylesheet(self):
        html = _html()
        assert "precond-verdict-chip" in html, (
            "Expected a base '.precond-verdict-chip' class (BEM base, mirrors "
            "templates/ai_advisor.html's .prism-sentiment-chip pattern) "
            "somewhere in templates/performance.html's <style> block or "
            "rendered markup."
        )

    def test_all_five_verdict_modifiers_defined(self):
        html = _html()
        missing = [m for m in self._EXPECTED_MODIFIERS if m not in html]
        assert not missing, (
            f"Missing verdict chip modifier classes: {missing!r}. Each of the "
            "5 verdict classes (SUFFICIENT_MET, SUFFICIENT_LIKELY, NOT_MET, "
            "NEGATIVE_EDGE, INSUFFICIENT_DATA) needs its own semantic CSS "
            "modifier, mirroring templates/ai_advisor.html's "
            ".prism-sentiment-chip--risk-on / --risk-off / --neutral pattern."
        )

    def test_no_raw_hardcoded_hex_colors_in_new_panel_markup(self):
        """Never raw colors in JS/inline styles for the new panel -- must use
        CSS variables (var(--studio-...)) per house convention, matching
        static/index.js's cs('--studio-pos') idiom for the sibling
        dollar-saved panel."""
        js = _js()
        assert "fetchGuardAlphaPreconditions" not in js or "#" not in _extract_function_body(
            js, "fetchGuardAlphaPreconditions"
        ), (
            "fetchGuardAlphaPreconditions() must not set raw hex colors "
            "inline -- use CSS variables / semantic classes instead, "
            "matching static/index.js's cs('--studio-pos') pattern."
        )


def _extract_function_body(js: str, fn_name: str) -> str:
    """Crude but adequate for a source-level lint check: slice from the
    function declaration to the next top-level '    }' close at the same
    indentation, or to end of file."""
    start = js.find(f"function {fn_name}")
    if start == -1:
        return ""
    return js[start : start + 2000]


# ---------------------------------------------------------------------------
# AC-7: JS fetch, 401-guarded, mirrors fetchGuardAlphaSummary()
# ---------------------------------------------------------------------------


class TestPreconditionsFetchJs:
    def test_fetch_function_present(self):
        js = _js()
        assert "fetchGuardAlphaPreconditions" in js, (
            "AC-7 FAIL: static/performance.js missing a fetchGuardAlphaPreconditions "
            "function to load GET /api/guard-alpha-preconditions."
        )

    def test_fetch_targets_correct_route(self):
        js = _js()
        assert "/api/guard-alpha-preconditions" in js, (
            "static/performance.js must fetch '/api/guard-alpha-preconditions'."
        )

    def test_fetch_is_401_guarded(self):
        """Mirrors fetchGuardAlphaSummary()'s idiom: 'if (!response.ok)
        return;' before parsing JSON -- an unguarded fetch would throw on a
        401 JSON body or silently render 'undefined'."""
        body = _extract_function_body(_js(), "fetchGuardAlphaPreconditions")
        assert "response.ok" in body or ".ok" in body, (
            "fetchGuardAlphaPreconditions() must guard on response.ok before "
            "parsing JSON (401-guarded fetch, AC-7), mirroring "
            "fetchGuardAlphaSummary()'s 'if (!response.ok) return;' pattern. "
            f"Function body found: {body[:300]!r}"
        )
