"""
RED tests for AC-4 dashboard template session-close display fix.

The dashboard intraday chart subtitle currently hardcodes "09:30 ET → 16:00 ET"
in templates/index.html. On a half-day (13:00 close) this is factually wrong.

A/C requirement (from Gate-1 AC-4 and flask-dashboard-specialist BLOCK-3):
  The index route must pass a `session_close_display` string (e.g., "13:00 ET"
  or "16:00 ET") into the render_template context, and the template must use it
  to render the intraday chart subtitle dynamically.

These tests are RED on the current codebase:
  - The index route does not yet pass `session_close_display` to render_template.
  - The template still contains the hardcoded "16:00 ET" string.

The template literal test guards that "16:00 ET" is no longer hardcoded once the
implementer replaces it with the template variable.
"""

from __future__ import annotations

import pathlib
from datetime import time as dt_time
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Test 1: Index route passes session_close_display to render_template
# ---------------------------------------------------------------------------


class TestIndexRoutePassesSessionCloseDisplay:
    """
    RED: the index route (GET /) must pass `session_close_display` into the
    render_template context. Currently it does not.

    After GREEN: this string must be "13:00 ET" on a half-day and "16:00 ET"
    on a regular day — ensuring the template can render the correct close time.

    Mocking strategy: patch `market_calendar.session_close` (the function the
    route will call once the implementation adds the session_close_display logic)
    to return a controlled time. This is stable across daily schedule changes and
    does not require controlling the wall clock.
    """

    @pytest.fixture(autouse=True)
    def _client(self, monkeypatch):
        """Set up a minimal Flask test client with heavy deps stubbed."""
        import app as app_module
        import market_calendar as mc_module

        # Capture render_template kwargs
        self._render_kwargs: dict = {}

        def _capture_render(template, **kwargs):
            self._render_kwargs = kwargs
            return "<html>stub</html>"

        monkeypatch.setattr(app_module, "render_template", _capture_render)

        # Stub DB
        monkeypatch.setattr(app_module.database, "load_state", lambda: {})
        monkeypatch.setattr(
            app_module.database, "load_chart_history",
            lambda: {"date": "2025-05-14", "symphonies": {}},
        )
        monkeypatch.setattr(
            app_module.database, "get_advisor_observations_for_symphony",
            lambda *a, **kw: [],
        )

        self._app_module = app_module
        self._mc_module = mc_module
        self._client_obj = app_module.app.test_client()

    def test_index_route_passes_session_close_display_regular_day(
        self, monkeypatch
    ) -> None:
        """
        On a regular trading day (session_close returns 16:00), the index route
        must pass `session_close_display = "16:00 ET"` to render_template.

        RED: the current route does not pass session_close_display at all.
        """
        # Patch session_close to return a regular-day close (16:00)
        monkeypatch.setattr(
            self._mc_module, "session_close", lambda d: dt_time(16, 0)
        )

        self._client_obj.get("/")

        assert "session_close_display" in self._render_kwargs, (
            "render_template must be called with a 'session_close_display' kwarg "
            "from the index route. Currently the route does not pass this variable. "
            "Add: session_close_display = market_calendar.session_close(today).strftime('%H:%M ET') "
            "to the index route and pass it to render_template."
        )
        assert self._render_kwargs["session_close_display"] == "16:00 ET", (
            f"On a regular trading day, session_close_display must be '16:00 ET'; "
            f"got {self._render_kwargs.get('session_close_display')!r}"
        )

    def test_index_route_passes_session_close_display_half_day(
        self, monkeypatch
    ) -> None:
        """
        On a half-day (session_close returns 13:00), the index route must pass
        `session_close_display = "13:00 ET"` to render_template.

        RED: a wrong implementation that hardcodes "16:00 ET" regardless of
        session_close output would fail here.
        """
        # Patch session_close to return half-day close (13:00)
        monkeypatch.setattr(
            self._mc_module, "session_close", lambda d: dt_time(13, 0)
        )

        self._client_obj.get("/")

        assert "session_close_display" in self._render_kwargs, (
            "render_template must be called with a 'session_close_display' kwarg. "
            "Not found in render_template kwargs."
        )
        assert self._render_kwargs["session_close_display"] == "13:00 ET", (
            f"On a half-day (session_close returns 13:00), session_close_display "
            f"must be '13:00 ET'; got {self._render_kwargs.get('session_close_display')!r}. "
            f"The route must query market_calendar.session_close(today) and format it, "
            f"not hardcode '16:00 ET'."
        )


# ---------------------------------------------------------------------------
# Test 2: Template does not hardcode "16:00 ET" in the intraday subtitle
# ---------------------------------------------------------------------------


class TestTemplateDoesNotHardcodeCloseTime:
    """
    RED: templates/index.html currently contains the hardcoded string
    "09:30 ET → 16:00 ET · 1-minute resolution" in the intraday chart subtitle.

    After GREEN: the template must use a variable (e.g., {{ session_close_display }})
    so the correct close time is rendered on half-days.

    This is a textual contract test — it scans the template source for the
    hardcoded pattern and fails until the template is updated.
    """

    def test_intraday_subtitle_does_not_hardcode_1600_et(self) -> None:
        """
        The intraday chart subtitle in templates/index.html must NOT contain
        the literal string "16:00 ET" in the context of the chart subtitle area.

        RED: the template currently has:
            09:30 ET → 16:00 ET · 1-minute resolution

        The "16:00 ET" part must be replaced with a template variable such as
        {{ session_close_display }}. The subtitle region is identified by the
        "1-minute resolution" string that labels the intraday tape.

        Note: "16:00" may appear elsewhere in the template (JS strings, comments,
        other sections). This test only fails if "16:00 ET" appears within 10
        lines of the "1-minute resolution" label — scoped to the subtitle area.
        """
        template_path = (
            pathlib.Path(__file__).parent.parent.parent / "templates" / "index.html"
        )
        assert template_path.exists(), (
            f"templates/index.html not found at {template_path}"
        )

        source = template_path.read_text(encoding="utf-8")
        lines = source.splitlines()

        # Find the subtitle div line containing "1-minute resolution"
        subtitle_region = ""
        for i, line in enumerate(lines):
            if "1-minute resolution" in line:
                start = max(0, i - 5)
                end = min(len(lines), i + 5)
                subtitle_region = "\n".join(lines[start:end])
                break

        assert subtitle_region, (
            "Could not find the '1-minute resolution' intraday subtitle in "
            "templates/index.html. Either the template was restructured (update "
            "this test) or the subtitle was removed."
        )

        assert "16:00 ET" not in subtitle_region, (
            "templates/index.html still hardcodes '16:00 ET' in the intraday chart "
            "subtitle. Replace with a template variable (e.g., {{ session_close_display }}) "
            "so the correct close time (13:00 on half-days) is rendered. "
            f"Subtitle region found:\n{subtitle_region}"
        )
