"""
RED/regression tests — AC-3 window-picker client wiring (the picker drives EVERY metric).

The /api/strip/<window> route is server-tested in tests/app/test_hero_chart_and_windowed_strip.py.
But a green server suite does NOT prove the picker actually CALLS it and re-renders the
headline value + label on click (per feedback_visual_gate_catches_js_break — client wiring
that never runs still passes every server test). These source-level tests lock the wiring:

  - the template exposes all 7 picker buttons incl the NEW "window-all" (All Time);
  - index.js maps every picker token (30d/60d/90d/125d/ytd/1y/all);
  - the picker click handler fetches /api/strip/<token> AND re-renders the headline
    via renderGuardAlpha (so the VALUE re-windows, not just the chart);
  - the click handler updates the guard-alpha window label (so the label matches the value);
  - index.js still parses (node --check).

Established dashboard-test pattern: read_text source assertions + node --check (this harness
has no jsdom). The PM owns the LIVE picker gate — these are necessary, not sufficient.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

_JS_PATH = pathlib.Path(__file__).parent.parent.parent / "static" / "index.js"
_INDEX_HTML_PATH = pathlib.Path(__file__).parent.parent.parent / "templates" / "index.html"

# Lowercase URL tokens the picker must drive — includes the NEW All-Time token.
_WINDOW_TOKENS = ["30d", "60d", "90d", "125d", "ytd", "1y", "all"]


def _js() -> str:
    return _JS_PATH.read_text(encoding="utf-8")


def _html() -> str:
    return _INDEX_HTML_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Template — the picker buttons, incl the new All-Time option
# ---------------------------------------------------------------------------

class TestPickerButtonsPresent:
    def test_template_has_window_all_button(self):
        html = _html()
        assert 'data-testid="window-all"' in html or 'data-window="all"' in html, (
            "AC-3 FAIL: templates/index.html has no All-Time picker button "
            "(data-testid=window-all / data-window=all). The picker must offer a NEW "
            "'All Time' window covering the lifetime cross-epoch guard alpha."
        )

    @pytest.mark.parametrize("token", _WINDOW_TOKENS)
    def test_template_picker_offers_token(self, token):
        html = _html()
        assert f'data-window="{token}"' in html, (
            f"AC-3 FAIL: templates/index.html picker is missing the '{token}' window option "
            f"(data-window=\"{token}\"). The picker must expose 30d/60d/90d/125d/YTD/1Y/All-Time."
        )


# ---------------------------------------------------------------------------
# index.js — the picker maps every token and re-windows the VALUE on click
# ---------------------------------------------------------------------------

class TestPickerJsReWindowsValue:
    @pytest.mark.parametrize("token", _WINDOW_TOKENS)
    def test_js_references_each_window_token(self, token):
        js = _js()
        # The token must appear as a quoted URL/window token in index.js.
        assert (f"'{token}'" in js) or (f'"{token}"' in js), (
            f"AC-3 FAIL: static/index.js never references the window token '{token}'. "
            "The picker map must cover every window including the All-Time 'all' token."
        )

    def test_js_picker_fetches_windowed_strip_route(self):
        js = _js()
        assert "/api/strip/" in js, (
            "AC-3 FAIL: static/index.js never fetches '/api/strip/<token>'. The picker must "
            "re-window the headline guard-alpha VALUE (not only the chart) — the F1 bug was "
            "exactly that the picker re-windowed the chart but left the value all-time."
        )

    def test_js_picker_rerenders_headline_value_after_strip_fetch(self):
        """The windowed-strip fetch must feed renderGuardAlpha so the headline value
        actually changes — fetching the route but not re-rendering would still be F1."""
        js = _js()
        # The strip fetch and a renderGuardAlpha re-render must both be present; pin that
        # the strip handler path references renderGuardAlpha (the headline renderer).
        assert "/api/strip/" in js and "renderGuardAlpha" in js, (
            "AC-3 FAIL: the /api/strip fetch must re-render the headline via renderGuardAlpha "
            "so the displayed guard-alpha VALUE re-windows on picker click."
        )

    def test_js_picker_updates_window_label(self):
        """The picker must update the guard-alpha window label so it matches the value
        (the dishonest static '30d' label was the F1 symptom)."""
        js = _js()
        assert "guard-alpha-window-label" in js, (
            "AC-3 FAIL: static/index.js does not update 'guard-alpha-window-label' on picker "
            "click. The label must reflect the selected window (incl 'All Time')."
        )


# ---------------------------------------------------------------------------
# Parse guard — the picker wiring must actually be runnable
# ---------------------------------------------------------------------------

class TestIndexJsParses:
    def test_node_check_passes(self):
        node = shutil.which("node")
        if node is None:
            pytest.skip("node not available — JS parse guard requires node")
        result = subprocess.run(
            [node, "--check", str(_JS_PATH)], capture_output=True, text=True
        )
        assert result.returncode == 0, (
            "static/index.js failed `node --check`. A parse error makes the picker wiring "
            f"never run while every server test still passes. stderr:\n{result.stderr}"
        )
