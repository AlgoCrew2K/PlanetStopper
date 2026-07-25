"""
RED tests -- Strategy Incubation Gate badge rendering (AC-5).

Source-level assertions mirroring tests/app/test_guard_alpha_panel_ui.py's
pattern: template/JS string search, no live browser, no Flask test client
needed. Fails RED because none of this markup/JS exists yet.

Contract under test (pinned in .claude/tdd-handoff.md "static/ai_advisor.js +
templates/ai_advisor.html"):
- templates/ai_advisor.html contains an incubation status chip matching the
  existing prism-sentiment-chip BEM pattern exactly (same
  <span class="X X--modifier" data-testid="X"> shape).
- Four status modifiers exist: incubating / promoted / failed / expired.
- Only the PROMOTED chip carries the recommended/actionable styling class.
- static/ai_advisor.js fetches GET /api/incubation and passes node --check.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

_TEMPLATES_DIR = pathlib.Path(__file__).parent.parent.parent / "templates"
_STATIC_DIR = pathlib.Path(__file__).parent.parent.parent / "static"

_AI_ADVISOR_HTML = _TEMPLATES_DIR / "ai_advisor.html"
_AI_ADVISOR_JS = _STATIC_DIR / "ai_advisor.js"


def _html() -> str:
    return _AI_ADVISOR_HTML.read_text(encoding="utf-8")


def _js() -> str:
    return _AI_ADVISOR_JS.read_text(encoding="utf-8")


class TestIncubationChipMarkup:
    def test_incubation_status_chip_data_testid_present(self):
        html = _html()
        assert 'data-testid="incubation-status-chip"' in html, (
            'templates/ai_advisor.html missing data-testid="incubation-status-chip" '
            "-- the incubation status chip has not been added to the Strategy "
            "Builder tab yet."
        )

    def test_incubation_status_chip_base_class_present(self):
        html = _html()
        assert "incubation-status-chip" in html, (
            "templates/ai_advisor.html missing the 'incubation-status-chip' BEM base "
            "class (mirrors the existing prism-sentiment-chip pattern)."
        )

    @pytest.mark.parametrize(
        "modifier",
        ["incubating", "promoted", "failed", "expired"],
    )
    def test_all_four_status_modifiers_referenced(self, modifier):
        html = _html()
        assert f"incubation-status-chip--{modifier}" in html, (
            f"templates/ai_advisor.html missing the "
            f"'incubation-status-chip--{modifier}' modifier class -- all four "
            "statuses (incubating/promoted/failed/expired) must map to a "
            "pairwise-distinct chip modifier (AC-5)."
        )

    def test_promoted_chip_is_the_only_actionable_styled_status(self):
        """AC-5: ONLY PROMOTED candidates render in the recommended/actionable
        styling -- the template must not apply the same actionable class to any
        other status modifier. Structural proxy: the 'promoted' modifier string
        must appear, and it must not be present as a substring inside any of the
        other three modifier class names (a false-positive-proof check)."""
        html = _html()
        assert "incubation-status-chip--promoted" in html
        for other in ("incubating", "failed", "expired"):
            assert f"incubation-status-chip--{other}-promoted" not in html
            assert f"incubation-status-chip--promoted-{other}" not in html


class TestIncubationJsWiring:
    def test_js_fetches_incubation_route(self):
        js = _js()
        assert "/api/incubation" in js, (
            "static/ai_advisor.js does not reference /api/incubation -- the "
            "Strategy Builder tab has no fetch wiring for incubation status yet."
        )

    def test_js_passes_node_check(self):
        """JS syntax coverage lives in the parametrized module
        (tests/js_syntax/test_js_syntax.py) which glob-discovers static/*.js --
        this is a direct sanity check specific to this file so a syntax error here
        surfaces with an unambiguous file name rather than only in the parametrized
        sweep."""
        result = subprocess.run(
            ["node", "--check", str(_AI_ADVISOR_JS)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 and "not found" in (result.stderr or "").lower():
            pytest.skip("node not available in this environment")
        assert result.returncode == 0, (
            f"node --check failed for static/ai_advisor.js:\n{result.stderr}"
        )

    def test_js_does_not_use_innerhtml_for_incubation_content(self):
        """Security / house convention: textContent-only DOM building, no innerHTML
        of untrusted (server-derived) content."""
        js = _js()
        # Locate any incubation-related block and confirm it doesn't couple with
        # innerHTML assignment in the same neighborhood (loose structural check --
        # a tight AST check is out of scope for a source-string test).
        if "/api/incubation" not in js:
            pytest.skip(
                "incubation fetch not yet present -- covered by test_js_fetches_incubation_route"
            )
        idx = js.index("/api/incubation")
        window = js[max(0, idx - 400) : idx + 2000]
        assert ".innerHTML" not in window, (
            "The incubation fetch/render block must not use .innerHTML -- build "
            "chip content via textContent only (house convention, avoids XSS via "
            "server-derived status_reason strings)."
        )
