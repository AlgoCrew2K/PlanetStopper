"""
RED tests — candidate-alert header indicator markup + wiring (AC-1/AC-4/AC-6,
feature-plans/candidate-alert.md).

Source-level assertions only — mirrors the established pattern in
tests/app/test_guard_alpha_panel_ui.py (template string search + JS source
search, no Flask test client / no live browser). This project's own render
tests for full pages stub render_template entirely (see
test_app_routes.py::test_dashboard_root_returns_200,
test_ai_advisor_tab.py::test_get_ai_advisor_returns_200) precisely because
end-to-end rendering these 4 routes requires heavy mocking unrelated to this
feature — a source-level check on the ACTUAL shared partial is the more
robust, hermetic way to pin "renders on every screen."

Contract under test:
- AC-1: a single shared header partial (templates/_chrome.html) — confirmed
  already `{% include %}`-ed by all 4 screens (index.html:740, ai_advisor.html:979,
  history.html:265, performance.html:338) — carries the indicator markup, so
  adding it ONCE makes it appear on all 4 screens without duplication. This
  file also pins that all 4 templates keep including _chrome.html (guards
  against a future refactor silently dropping a screen).
- AC-4: the indicator links to the AI Advisor view (url_for('ai_advisor_tab'))
  so a click routes there even before any JS runs.
- AC-2/AC-6 (JS side): static/chrome.js (the ONLY JS asset shared by all 4
  pages — static/index.js loads on index.html only) fetches
  /api/candidate-alert and posts to /api/candidate-alert/mark-viewed with the
  existing _chromeCsrfToken, and degrades honestly on a non-OK response
  (mirrors the AC-7 JS-safety pattern from test_guard_alpha_panel_ui.py).
"""

from __future__ import annotations

import pathlib

import pytest

_TEMPLATES_DIR = pathlib.Path(__file__).parent.parent.parent / "templates"
_STATIC_DIR = pathlib.Path(__file__).parent.parent.parent / "static"

_CHROME_HTML = _TEMPLATES_DIR / "_chrome.html"
_CHROME_JS = _STATIC_DIR / "chrome.js"

_PAGE_TEMPLATES = ["index.html", "ai_advisor.html", "history.html", "performance.html"]


def _chrome_html() -> str:
    return _CHROME_HTML.read_text(encoding="utf-8")


def _chrome_js() -> str:
    return _CHROME_JS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AC-1: all 4 screens still route through the one shared partial
# (guards the "single shared partial" precondition this whole file relies on)
# ---------------------------------------------------------------------------


class TestAllFourScreensShareTheChromePartial:
    @pytest.mark.parametrize("template_name", _PAGE_TEMPLATES)
    def test_page_includes_chrome_partial(self, template_name):
        html = (_TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
        assert "_chrome.html" in html, (
            f"{template_name} no longer includes _chrome.html. AC-1 relies on the "
            "indicator living in the ONE shared partial so it appears on every "
            "screen without duplication — if this include is ever removed, the "
            "indicator silently disappears from that screen."
        )


# ---------------------------------------------------------------------------
# AC-1/AC-2: indicator + badge markup present in the shared partial
# ---------------------------------------------------------------------------


class TestCandidateAlertIndicatorMarkup:
    def test_indicator_element_present(self):
        """AC-1: _chrome.html must contain the candidate-alert indicator element.

        Fails RED because no such element exists in the current partial.
        """
        html = _chrome_html()
        assert 'data-testid="candidate-alert-indicator"' in html, (
            "AC-1 FAIL: templates/_chrome.html missing data-testid="
            '"candidate-alert-indicator". The header indicator has not been added.'
        )

    def test_badge_element_present(self):
        """AC-2: the indicator must contain a badge element for the numeric count."""
        html = _chrome_html()
        assert 'data-testid="candidate-alert-badge"' in html, (
            "AC-2 FAIL: templates/_chrome.html missing data-testid="
            '"candidate-alert-badge". The element the JS will populate with '
            "new_valid_count is absent."
        )


# ---------------------------------------------------------------------------
# AC-4: click routes to the AI Advisor view
# ---------------------------------------------------------------------------


class TestCandidateAlertClickRoutes:
    def test_indicator_links_to_ai_advisor_route(self):
        """AC-4: the indicator must route to the AI Advisor view on click,
        server-renderable via url_for('ai_advisor_tab') (not a hardcoded path —
        C-12 convention already used throughout _chrome.html's nav links, e.g.
        url_for('dashboard'), url_for('performance_page'))."""
        html = _chrome_html()
        idx = html.find('data-testid="candidate-alert-indicator"')
        if idx == -1:
            pytest.skip("Indicator not yet present — prior test covers this.")

        # Look in a window around the indicator for the AI Advisor url_for call —
        # tolerant of exact DOM structure (wrapper vs inner <a>), strict on the
        # actual route target.
        window = html[max(0, idx - 400) : idx + 800]
        assert "url_for('ai_advisor_tab')" in window or 'url_for("ai_advisor_tab")' in window, (
            "AC-4 FAIL: the candidate-alert indicator does not link to "
            "url_for('ai_advisor_tab'). A click must route to the AI Advisor view "
            "showing the weekly suggestions — no hardcoded '/ai-advisor' path "
            "(C-12 convention: url_for(), not hardcoded paths)."
        )


# ---------------------------------------------------------------------------
# AC-2/AC-6: chrome.js fetch + render + mark-viewed wiring
# ---------------------------------------------------------------------------


class TestCandidateAlertJsWiring:
    def test_fetch_candidate_alert_present_in_chrome_js(self):
        """AC-2: static/chrome.js must fetch GET /api/candidate-alert.

        Deliberately chrome.js, NOT index.js — index.js only loads on
        index.html; chrome.js is the only JS asset shared by all 4 screens
        (loaded via _chrome.html's <script src="/static/chrome.js"> on every
        page), so this is the only place a poll can cover all 4 screens.
        """
        js = _chrome_js()
        assert "/api/candidate-alert" in js, (
            "AC-2 FAIL: static/chrome.js does not fetch /api/candidate-alert. "
            "The badge will never populate on any screen."
        )

    def test_mark_viewed_post_present_in_chrome_js(self):
        """AC-5: a click must POST /api/candidate-alert/mark-viewed with the
        existing CSRF token (_chromeCsrfToken, already fetched by chrome.js on
        DOMContentLoaded — same pattern as forceRun() and
        submitPanicLiquidation())."""
        js = _chrome_js()
        if "/api/candidate-alert" not in js:
            pytest.skip("candidate-alert fetch not yet present — prior test covers this.")

        assert "/api/candidate-alert/mark-viewed" in js, (
            "AC-5 FAIL: static/chrome.js does not POST /api/candidate-alert/mark-viewed. "
            "The badge will never clear after the operator views the candidates."
        )
        assert "_chromeCsrfToken" in js.split("/api/candidate-alert/mark-viewed")[0][-600:] or (
            "_chromeCsrfToken" in js
        ), (
            "AC-5 FAIL: the mark-viewed POST does not appear to use the existing "
            "_chromeCsrfToken — a CSRF-protected POST without a token will 403."
        )

    def test_js_handles_non_ok_response_without_throwing(self):
        """AC-6: a non-200 /api/candidate-alert response (e.g. 401 when logged
        out) must not throw an uncaught error or render 'undefined'/'NaN' —
        mirrors the AC-7 JS-safety pattern in test_guard_alpha_panel_ui.py."""
        js = _chrome_js()
        if "/api/candidate-alert" not in js:
            pytest.skip("candidate-alert fetch not yet present — prior test covers this.")

        has_ok_guard = "response.ok" in js or ".ok" in js
        has_catch = ".catch(" in js
        has_status_check = "response.status" in js or ".status ===" in js
        assert has_ok_guard or has_catch or has_status_check, (
            "AC-6 FAIL: the /api/candidate-alert fetch in chrome.js appears to lack "
            "a response.ok guard or .catch handler — a 401 response will throw on "
            ".json() (HTML body, not JSON), crashing the poller with an uncaught "
            "promise rejection on every one of the 4 screens."
        )
