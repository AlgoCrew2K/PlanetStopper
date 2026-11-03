"""The shared header in a real browser: every control reachable at 375 px, one row at 1440 px,
and the login page in the app's font and stored theme.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_shell_header_browser.py``). Firefox headless
through Playwright, imported inside a fixture; a missing Playwright or Firefox fails with a
message naming it, never a skip.

The server is the app in this process on the per-test database (``tests/ui/_shell_support``),
seeded through the real writers from captured records; every request to anything but that server
is refused, so the font stylesheet's host is never contacted and the browser falls back to the
face the stack names. The login rows enable the auth and CSRF hooks and sign in through the
page's own form (a launcher-only password set in this test's environment), as the launcher's
second mode does; nothing is written to the developer ``.env``.

Layout facts (a control inside the viewport, rows of the header, scroll width) are asserted on a
live render; colours and sizes are never asserted. The 1440 px header height is compared with
the capture of the same pages taken on the plan's base
(``tests/fixtures/dashboard/shell_baseline/baseline.json``).
"""

from __future__ import annotations

import json
import re

import pytest

import app as app_module
from tests.ui._shell_support import (  # noqa: F401
    ALERT_INDICATOR,
    HEADER_CONTROLS,
    NARROW,
    PAGE_IDS,
    PAGES,
    REPO,
    WIDE,
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    assert_reachable,
    document_scroll_width,
    fail,
    firefox,
    nav_height,
    nav_rows,
    open_page,
    open_probe,
    seed_candidate_alert_survivor,
    seed_dashboard_state,
    wait_until,
)

pytestmark = pytest.mark.browser

BASELINE = json.loads(
    (REPO / "tests" / "fixtures" / "dashboard" / "shell_baseline" / "baseline.json").read_text(
        encoding="utf-8"
    )
)
LOGIN_PASSWORD = "shell-browser-password-4f7a"
HEIGHT_TOLERANCE_PX = 1.0


@pytest.fixture
def login_server(advisor_server, monkeypatch):  # noqa: F811
    """The server with the auth and CSRF hooks on and a launcher-only password."""
    monkeypatch.setenv("DASHBOARD_PASSWORD", LOGIN_PASSWORD)
    monkeypatch.setenv("SECRET_KEY", "shell-browser-secret-key-91c2")
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    monkeypatch.setattr(app_module, "_csrf_check_enabled", True)
    app_module._AUTH_FAILED_ATTEMPTS.clear()
    yield advisor_server
    app_module._AUTH_FAILED_ATTEMPTS.clear()


# ---------------------------------------------------------------------------
# at 375 px every control stays reachable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", PAGES, ids=PAGE_IDS)
def test_settings_the_mode_pill_force_run_and_liquidate_are_reachable_at_375(
    firefox, advisor_server, path
):
    """each is inside the viewport and not covered, with no sideways scroll to reach it."""
    seed_dashboard_state()
    with open_probe(firefox, advisor_server, width=NARROW[0], height=NARROW[1]) as probe:
        open_page(probe, path)
        for label, selector in HEADER_CONTROLS.items():
            assert_reachable(probe, label, selector, f"{path} at {NARROW[0]} px")
        assert probe.page_errors == [], probe.page_errors


@pytest.mark.parametrize("path", PAGES, ids=PAGE_IDS)
def test_every_nav_link_is_reachable_at_375(firefox, advisor_server, path):
    """the five nav links, not only Settings."""
    seed_dashboard_state()
    with open_probe(firefox, advisor_server, width=NARROW[0], height=NARROW[1]) as probe:
        open_page(probe, path)
        hrefs = probe.page.evaluate(
            "() => [...document.querySelectorAll('[data-testid=\"studio-nav\"] a')]"
            ".map((a) => a.getAttribute('href'))"
        )
        assert {"/", "/performance", "/ai-advisor", "/history", "/settings"} <= set(hrefs)
        for href in ("/", "/performance", "/history", "/settings"):
            assert_reachable(
                probe, f"nav link {href}", f'[data-testid="studio-nav"] a[href="{href}"]', path
            )
        assert_reachable(
            probe,
            "nav link Advisor",
            '[data-testid="studio-nav"] > div a[href="/ai-advisor"]',
            path,
        )


@pytest.mark.parametrize("path", PAGES, ids=PAGE_IDS)
def test_the_engine_status_is_reachable_at_375(firefox, advisor_server, path):
    seed_dashboard_state()
    with open_probe(firefox, advisor_server, width=NARROW[0], height=NARROW[1]) as probe:
        open_page(probe, path)
        assert_reachable(probe, "engine status", '[data-testid="engine-status"]', path)


@pytest.mark.parametrize("path", PAGES, ids=PAGE_IDS)
def test_the_header_wraps_onto_more_rows_at_375_instead_of_running_sideways(
    firefox, advisor_server, path
):
    """it wraps; it does not collapse and does not overflow."""
    seed_dashboard_state()
    with open_probe(firefox, advisor_server, width=NARROW[0], height=NARROW[1]) as probe:
        open_page(probe, path)
        assert nav_rows(probe) > 1, "the header must wrap at 375 px"
        nav_right = probe.page.evaluate(
            "() => document.querySelector('[data-testid=\"studio-nav\"]')"
            ".getBoundingClientRect().right"
        )
        assert nav_right <= NARROW[0] + 0.5


@pytest.mark.parametrize("path", PAGES, ids=PAGE_IDS)
def test_the_candidate_alert_indicator_and_its_badge_stay_visible_at_375(
    firefox, advisor_server, path
):
    """with a survivor waiting, the badge shows the route's count, inside the viewport."""
    seed_dashboard_state()
    seed_candidate_alert_survivor()
    with open_probe(firefox, advisor_server, width=NARROW[0], height=NARROW[1]) as probe:
        open_page(probe, path)
        count = probe.page.evaluate(
            "() => fetch('/api/candidate-alert').then((r) => r.json()).then((b) => b.new_valid_count)"
        )
        assert count >= 1, "premise: the survivor row counts as new"
        wait_until(
            probe,
            "() => { const b = document.querySelector('[data-testid=\"candidate-alert-badge\"]');"
            " return !!b && getComputedStyle(b).display !== 'none' && b.textContent.trim() !== ''; }",
            "the candidate-alert badge never became visible",
        )
        assert_reachable(probe, "candidate-alert indicator", ALERT_INDICATOR, path)
        assert_reachable(
            probe, "candidate-alert badge", '[data-testid="candidate-alert-badge"]', path
        )
        badge = probe.page.locator('[data-testid="candidate-alert-badge"]').inner_text().strip()
        assert badge == str(count)


# ---------------------------------------------------------------------------
# 1440 px is unchanged, and resizing wraps and unwraps
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", PAGES, ids=PAGE_IDS)
def test_at_1440_the_header_is_one_row_no_higher_than_it_was_at_the_base(
    firefox, advisor_server, path
):
    """one row, and no higher than the capture of the base (the unslop pass made it slimmer)."""
    seed_dashboard_state()
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        open_page(probe, path)
        assert nav_rows(probe) == 1
        base = BASELINE["pages_1440"][path]["nav_height"]
        assert nav_height(probe) <= base + HEIGHT_TOLERANCE_PX, (
            f"{path}: the header is {nav_height(probe)} px high, the base was {base} px"
        )
        for label, selector in HEADER_CONTROLS.items():
            assert_reachable(probe, label, selector, f"{path} at {WIDE[0]} px")


@pytest.mark.parametrize("path", PAGES, ids=PAGE_IDS)
def test_resizing_from_1440_to_375_and_back_wraps_and_unwraps_without_losing_a_control(
    firefox, advisor_server, path
):
    seed_dashboard_state()
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        open_page(probe, path)
        before_height = nav_height(probe)
        assert nav_rows(probe) == 1

        probe.page.set_viewport_size({"width": NARROW[0], "height": NARROW[1]})
        wait_until(
            probe,
            "() => innerWidth === 375",
            "the viewport never narrowed",
        )
        assert nav_rows(probe) > 1, "the header must wrap when narrowed"
        for label, selector in HEADER_CONTROLS.items():
            assert_reachable(probe, label, selector, f"{path} narrowed")

        probe.page.set_viewport_size({"width": WIDE[0], "height": WIDE[1]})
        wait_until(probe, "() => innerWidth === 1440", "the viewport never widened")
        assert nav_rows(probe) == 1, "the header must unwrap when widened"
        assert abs(nav_height(probe) - before_height) <= HEIGHT_TOLERANCE_PX
        for label, selector in HEADER_CONTROLS.items():
            assert_reachable(probe, label, selector, f"{path} widened again")


# ---------------------------------------------------------------------------
#: the login page
# ---------------------------------------------------------------------------


def _open_login(probe) -> None:
    probe.page.goto(probe.server.base_url + "/login")
    wait_until(
        probe, "() => document.querySelector('#password') !== null", "the login form never rendered"
    )


def test_the_login_page_fits_at_375(firefox, login_server):
    """it fits now and keeps fitting."""
    with open_probe(firefox, login_server, width=NARROW[0], height=NARROW[1]) as probe:
        _open_login(probe)
        scroll, viewport = document_scroll_width(probe)
        assert scroll == viewport
        card = probe.page.evaluate(
            "() => { const r = document.querySelector('.login-card').getBoundingClientRect();"
            " return [r.left, r.right]; }"
        )
        assert card[0] >= 0 and card[1] <= NARROW[0]


def test_the_login_page_renders_dark_when_the_dark_theme_is_stored(firefox, login_server):
    """the stored theme is applied, and the app's typeface is the page's face."""
    with open_probe(firefox, login_server, width=WIDE[0], height=WIDE[1], theme="dark") as probe:
        _open_login(probe)
        assert (
            probe.page.evaluate("() => document.documentElement.getAttribute('data-theme')")
            == "dark"
        )
        family = probe.page.evaluate("() => getComputedStyle(document.body).fontFamily")
        assert family.split(",")[0].strip().strip("\"'") == "Manrope", family
        input_family = probe.page.evaluate(
            "() => getComputedStyle(document.querySelector('#password')).fontFamily"
        )
        assert input_family.split(",")[0].strip().strip("\"'") == "Manrope", input_family


def test_the_login_page_links_and_requests_the_font_stylesheet(firefox, login_server):
    """the font stylesheet is linked and the browser asks for it (refused here)."""
    with open_probe(firefox, login_server, width=WIDE[0], height=WIDE[1]) as probe:
        _open_login(probe)
        linked = probe.page.evaluate(
            "() => [...document.querySelectorAll('link[rel=\"stylesheet\"]')].map((l) => l.href)"
            ".filter((h) => h.includes('fonts.googleapis.com'))"
        )
        assert linked, "the login page links no font stylesheet"
        assert any(url in probe.refused for url in linked), (
            "the browser never asked for the font stylesheet the page links"
        )


def test_the_login_page_renders_light_when_no_theme_is_stored(firefox, login_server):
    """no stored value: light, as the other pages do."""
    with open_probe(firefox, login_server, width=WIDE[0], height=WIDE[1]) as probe:
        _open_login(probe)
        assert (
            probe.page.evaluate("() => document.documentElement.getAttribute('data-theme')")
            == "light"
        )


def test_with_the_font_host_unreachable_the_form_falls_back_and_still_signs_in(
    firefox, login_server
):
    """the stylesheet cannot load (every outside host is refused here); the form works."""
    with open_probe(firefox, login_server, width=NARROW[0], height=NARROW[1]) as probe:
        _open_login(probe)
        assert any("fonts.googleapis.com" in url for url in probe.refused), (
            "premise: the font stylesheet was requested and refused"
        )
        stack = probe.page.evaluate("() => getComputedStyle(document.body).fontFamily")
        assert re.search(r"(sans-serif|system-ui|serif)\s*$", stack), (
            f"the face stack must end in a generic family: {stack}"
        )
        probe.page.fill("#password", LOGIN_PASSWORD)
        probe.page.click('button[type="submit"]')
        probe.page.wait_for_url(login_server.base_url + "/")
        assert probe.page_errors == [], probe.page_errors


def test_a_wrong_password_shows_the_generic_error_in_the_stored_theme(firefox, login_server):
    """the route's own error text, the dark theme still applied."""
    with app_module.app.test_client() as client:
        page = client.get("/login")
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True))
        assert token, "premise: the login form carries its CSRF token"
        refused = client.post("/login", data={"password": "wrong", "csrf_token": token.group(1)})
        error_text = re.search(
            r'class="login-error"[^>]*>\s*([^<]+?)\s*<', refused.get_data(as_text=True)
        )
        assert error_text, "premise: the route shows an error block for a wrong password"
    app_module._AUTH_FAILED_ATTEMPTS.clear()

    with open_probe(
        firefox, login_server, width=NARROW[0], height=NARROW[1], theme="dark"
    ) as probe:
        _open_login(probe)
        probe.page.fill("#password", "not-the-password")
        probe.page.click('button[type="submit"]')
        wait_until(
            probe,
            "() => document.querySelector('.login-error') !== null",
            "the wrong-password error never showed",
        )
        assert probe.page.locator(".login-error").inner_text().strip() == error_text.group(1)
        assert (
            probe.page.evaluate("() => document.documentElement.getAttribute('data-theme')")
            == "dark"
        )
        if probe.page_errors:
            fail(probe, "the error page raised a script error")
