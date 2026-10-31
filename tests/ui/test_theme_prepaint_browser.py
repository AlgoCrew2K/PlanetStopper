"""The stored theme is on the root before the first frame, and a theme change reaches the other open tab
(marker ``browser``).

Run with ``python -m pytest -m browser -n 0 tests/ui/test_theme_prepaint_browser.py``. Firefox headless
through Playwright, imported inside a fixture so collection works on a host without it; never skips. The pages
are the real ones the app in this process serves (tests/ui/_browser_server.py) over its per-test database;
outbound requests from a page are refused, so no chart library loads.

"Before the first frame" is sampled by the first animation frame a page gets once its ``<body>`` exists. The
tweaks script is held back for 400 ms in the browser's request handler, so a page that waits for it with its
body already built has time to draw a frame first (the light flash), while a page that waits for it in the head
has no body to draw. Whether the theme looks right is judged on a live render by the capturer; this proves
the attribute is in place when a frame can be drawn.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.ui._browser_server import running_server
from tests.ui.test_dashboard_charts_browser import firefox  # noqa: F401  (module-scoped fixture)

pytestmark = pytest.mark.browser

PAGES = ["/", "/history", "/performance", "/ai-advisor", "/settings"]
PAGE_TIMEOUT_MS = 20_000
HOLD_TWEAKS_SECONDS = 0.4
TWEAKS_JS = Path(__file__).resolve().parents[2] / "static" / "tweaks.js"

STORED_DARK = json.dumps(
    {
        "theme": "dark",
        "density": "balanced",
        "accent": "#1f7a4d",
        "typeface": "Manrope",
        "mathOverlays": True,
        "numFormat": "full",
    }
)

SAMPLE_FIRST_FRAME = """
(() => {
  try { localStorage.setItem('studioTweaks', %s); } catch (e) {}
  window.__themeAtFirstFrame = null;
  const sample = () => {
    if (document.body) {
      window.__themeAtFirstFrame = document.documentElement.getAttribute('data-theme') || '';
    } else {
      requestAnimationFrame(sample);
    }
  };
  requestAnimationFrame(sample);
})();
""" % json.dumps(STORED_DARK)


def _refuse_outside(server):
    def handler(route):
        if not route.request.url.startswith(server.base_url):
            route.abort()
        else:
            route.continue_()

    return handler


def _hold_tweaks(route):
    time.sleep(HOLD_TWEAKS_SECONDS)
    route.fulfill(
        status=200,
        content_type="application/javascript",
        body=TWEAKS_JS.read_text(encoding="utf-8"),
    )


@pytest.mark.parametrize("path", PAGES)
def test_a_stored_dark_theme_is_on_the_root_when_the_first_frame_can_draw(firefox, path):  # noqa: F811
    with running_server() as server:
        context = firefox.new_context(viewport={"width": 1440, "height": 900})
        try:
            context.add_init_script(SAMPLE_FIRST_FRAME)
            page = context.new_page()
            page.set_default_timeout(PAGE_TIMEOUT_MS)
            page.route("**/*", _refuse_outside(server))
            page.route("**/static/tweaks.js", _hold_tweaks)
            page.goto(server.base_url + path)
            page.wait_for_function("() => window.__themeAtFirstFrame !== null")
            assert page.evaluate("() => window.__themeAtFirstFrame") == "dark", (
                f"{path}: a frame could be drawn before the stored theme was on the root"
            )
        finally:
            context.close()


def test_a_theme_set_in_one_tab_reaches_the_other_without_a_reload(firefox):  # noqa: F811
    with running_server() as server:
        context = firefox.new_context(viewport={"width": 1440, "height": 900})
        try:
            first = context.new_page()
            second = context.new_page()
            for page, path in ((first, "/"), (second, "/history")):
                page.set_default_timeout(PAGE_TIMEOUT_MS)
                page.route("**/*", _refuse_outside(server))
                page.goto(server.base_url + path)
                page.wait_for_function("() => !!window.studioTweaks")
            second.evaluate(
                "() => { window.__sameDocument = true; window.__tweakchanges = 0;"
                " window.addEventListener('tweakchange', () => { window.__tweakchanges += 1; }); }"
            )
            assert (
                second.evaluate("() => document.documentElement.getAttribute('data-theme')")
                == "light"
            )

            first.evaluate("() => window.studioTweaks.set('theme', 'dark')")

            from playwright.sync_api import TimeoutError as PlaywrightTimeout

            try:
                second.wait_for_function(
                    "() => document.documentElement.getAttribute('data-theme') === 'dark'",
                    timeout=5_000,
                )
            except PlaywrightTimeout:
                raise AssertionError(
                    "the other tab never applied the theme set in the first"
                ) from None
            assert second.evaluate("() => window.__sameDocument") is True, "the other tab reloaded"
            assert second.evaluate("() => window.__tweakchanges") == 1, (
                "the other tab's charts recolour on one tweakchange"
            )
        finally:
            context.close()
