"""The login form left open across a restart, in a real browser (S-2.9).

Browser-only (marker ``browser``; ``python -m pytest -m browser -n 0
tests/ui/test_login_expired_browser.py``). The auth and CSRF hooks are on and a launcher-only
password is set in this test's environment; the token is rotated between the page GET and the submit,
as a restart does. Asserted on the live render: the page after the submit is the login form again
with the expired line visible, never a bare "Forbidden" page, the hidden field holds the new token,
and submitting that form signs in. The screenshot goes to ``REFRESH_TIMING_SHOTS`` for the ux-expert.
"""

from __future__ import annotations

import os
import pathlib

import pytest

import app as app_module
from tests.ui._shell_support import (  # noqa: F401
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    fail,
    firefox,
    open_probe,
    seed_dashboard_state,
)

pytestmark = pytest.mark.browser

PASSWORD = "expired-form-browser-password-7c1e"
EXPIRED_LINE = "This form expired after a restart; sign in again"
NEW_TOKEN = "new-process-token-browser-22222222222222222222"


@pytest.fixture
def login_server(advisor_server, monkeypatch):  # noqa: F811
    monkeypatch.setenv("DASHBOARD_PASSWORD", PASSWORD)
    monkeypatch.setenv("SECRET_KEY", "expired-form-browser-secret-4d9a")
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    monkeypatch.setattr(app_module, "_csrf_check_enabled", True)
    app_module._AUTH_FAILED_ATTEMPTS.clear()
    yield advisor_server
    app_module._AUTH_FAILED_ATTEMPTS.clear()


def test_an_expired_form_is_answered_with_a_fresh_form_that_signs_in(
    firefox, login_server, monkeypatch, tmp_path
):  # noqa: F811
    seed_dashboard_state()
    with open_probe(firefox, login_server, width=1440, height=900) as probe:
        page = probe.page
        page.goto(login_server.base_url + "/login")
        page.wait_for_selector('input[name="password"]')
        monkeypatch.setattr(app_module, "_CSRF_TOKEN", NEW_TOKEN)
        page.fill('input[name="password"]', PASSWORD)
        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_selector('[role="alert"]')
        text = page.inner_text("body")
        assert EXPIRED_LINE in text, f"the page after the refusal reads {text[:200]!r}"
        assert page.query_selector('input[name="password"]') is not None, "the form is gone"
        assert page.get_attribute('input[name="csrf_token"]', "value") == NEW_TOKEN
        shots = pathlib.Path(os.environ.get("REFRESH_TIMING_SHOTS", str(tmp_path)))
        shots.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(shots / "login-expired.png"))
        page.fill('input[name="password"]', PASSWORD)
        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_url(lambda url: not url.endswith("/login"))
        assert probe.page_errors == [], probe.page_errors
