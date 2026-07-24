"""
RED tests -- realized-basis headline + exit-turnover panel UI (AC-6/AC-8
visible surfaces).

Mirrors the established pattern in test_guard_alpha_panel_ui.py: source-level
assertions (template string search + presence of the JS fetch wiring), no
live browser. Behavioral/visual verification is the PM's live droplet gate
per the plan's Testing Strategy -- these tests only pin the STRUCTURAL
contract an implementer must satisfy.

Two DISTINCT surfaces, two DISTINCT file pairs (confirmed by ga2-doc's
survey + PM ruling 2026-07-24):
- Dual-basis $-saved headline: templates/index.html + static/index.js
  (the MAIN dashboard -- NOT static/performance.js).
- Exit-turnover panel: templates/performance.html + static/performance.js
  (the Performance tab, beside the existing guard-alpha-preconditions-panel).
"""

from __future__ import annotations

import pathlib

_TEMPLATES_DIR = pathlib.Path(__file__).parent.parent.parent / "templates"
_STATIC_DIR = pathlib.Path(__file__).parent.parent.parent / "static"

_INDEX_HTML = _TEMPLATES_DIR / "index.html"
_INDEX_JS = _STATIC_DIR / "index.js"
_PERFORMANCE_HTML = _TEMPLATES_DIR / "performance.html"
_PERFORMANCE_JS = _STATIC_DIR / "performance.js"


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AC-6: dual-basis $-saved headline (index.html / index.js)
# ---------------------------------------------------------------------------


class TestDualBasisHeadlineMarkup:
    def test_realized_headline_element_present_in_index_html(self):
        html = _read(_INDEX_HTML)
        assert 'data-testid="dollar-saved-realized-headline"' in html, (
            'templates/index.html missing data-testid="dollar-saved-realized-headline" -- '
            "the second, realized-basis figure has not been added to the dollar-saved-panel."
        )

    def test_realized_headline_is_inside_the_existing_panel_not_a_stray_element(self):
        """The new element must be a sibling within the SAME dollar-saved-panel
        block as the existing snapshot headline, not an unrelated stray div
        elsewhere in the template.
        """
        html = _read(_INDEX_HTML)
        panel_start = html.find('data-testid="dollar-saved-panel"')
        assert panel_start != -1, "dollar-saved-panel element not found at all."
        # The panel block is bounded by the next top-level <section>/<div> close;
        # a generous 3000-char window is enough to contain a panel's markup
        # without false-negatives on reasonable indentation.
        panel_window = html[panel_start : panel_start + 3000]
        assert 'data-testid="dollar-saved-realized-headline"' in panel_window, (
            "dollar-saved-realized-headline must live inside the dollar-saved-panel "
            "block (within ~3000 chars of the panel's data-testid), not elsewhere "
            "in the template."
        )

    def test_marks_basis_qualifier_text_present_near_realized_headline(self):
        """AC-5's honesty addendum is contractually mandatory: the 'marks
        basis' qualifier must appear in the template near the realized
        headline, not be silently dropped."""
        html = _read(_INDEX_HTML)
        panel_start = html.find('data-testid="dollar-saved-panel"')
        assert panel_start != -1
        panel_window = html[panel_start : panel_start + 3000]
        assert "marks" in panel_window.lower(), (
            "The 'marks basis' qualifier (AC-5 honesty addendum) must appear "
            "in the dollar-saved-panel markup near the realized-basis headline."
        )


class TestDualBasisHeadlineJsWiring:
    def test_index_js_references_saved_dollars_realized_field(self):
        js = _read(_INDEX_JS)
        assert "saved_dollars_realized" in js, (
            "static/index.js must read 'saved_dollars_realized' from the "
            "/api/guard-alpha-summary response to populate the realized headline."
        )

    def test_index_js_references_realized_coverage_field(self):
        js = _read(_INDEX_JS)
        assert "realized_coverage" in js, (
            "static/index.js must read 'realized_coverage' to render the "
            "'N of M exits' coverage annotation and the honest no-data state."
        )

    def test_index_js_never_hardcodes_zero_dollar_string_for_no_coverage(self):
        """AC-7/plan Edge Case: realized_coverage.with_data == 0 must render
        'no realized data yet', never '$0.00'. This is a structural proxy
        check -- the literal 'no realized data yet' string (or equivalent
        honest-empty-state phrase) must appear in the JS source near the
        realized-basis rendering logic.
        """
        js = _read(_INDEX_JS)
        assert "no realized data yet" in js.lower() or "no realized data" in js.lower(), (
            "static/index.js must render an honest 'no realized data yet' "
            "message when realized_coverage.with_data is 0 -- never a bare "
            "'$0.00' that looks like a real (zero) realized figure."
        )


# ---------------------------------------------------------------------------
# AC-8: exit-turnover panel (performance.html / performance.js)
# ---------------------------------------------------------------------------


class TestExitTurnoverPanelMarkup:
    def test_turnover_panel_element_present_in_performance_html(self):
        html = _read(_PERFORMANCE_HTML)
        assert 'data-testid="exit-turnover-panel"' in html, (
            'templates/performance.html missing data-testid="exit-turnover-panel".'
        )

    def test_turnover_table_element_present(self):
        html = _read(_PERFORMANCE_HTML)
        assert 'data-testid="exit-turnover-table"' in html, (
            'templates/performance.html missing data-testid="exit-turnover-table".'
        )

    def test_turnover_panel_placed_beside_existing_preconditions_panel(self):
        """Plan Architecture: rendered 'beside the preconditions/turnover
        area' -- must be structurally near the existing Kaminski-Lo
        preconditions panel (PR #112), not on an unrelated part of the page.
        """
        html = _read(_PERFORMANCE_HTML)
        preconditions_idx = html.find('data-testid="guard-alpha-preconditions-panel"')
        assert preconditions_idx != -1, (
            "Reference anchor guard-alpha-preconditions-panel not found -- "
            "template may have changed since this test was written; verify "
            "the panel still exists before treating this as a real failure."
        )
        turnover_idx = html.find('data-testid="exit-turnover-panel"')
        assert turnover_idx != -1, "exit-turnover-panel not found."
        # "Beside" = within a generous 6000-char window either direction,
        # loose enough to tolerate reasonable markup between the two panels.
        assert abs(turnover_idx - preconditions_idx) < 6000, (
            "exit-turnover-panel must be placed near the existing "
            "guard-alpha-preconditions-panel per the plan's Architecture "
            f"section. Distance in template: {abs(turnover_idx - preconditions_idx)} chars."
        )


class TestExitTurnoverJsWiring:
    def test_performance_js_fetches_exit_turnover_route(self):
        js = _read(_PERFORMANCE_JS)
        assert "/api/exit-turnover" in js, (
            "static/performance.js must fetch GET /api/exit-turnover to "
            "populate the turnover panel."
        )

    def test_performance_js_is_401_guarded(self):
        """Mirrors the established 401-guarded fetch pattern (e.g.
        fetchGuardAlphaSummary in index.js) -- every authenticated fetch in
        this codebase checks for a 401 response.
        """
        js = _read(_PERFORMANCE_JS)
        assert "401" in js, (
            "static/performance.js must handle a 401 response from the new "
            "fetch (401-guarded fetch pattern, house convention)."
        )
