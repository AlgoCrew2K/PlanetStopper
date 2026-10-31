"""RED: a login form rendered before a daemon restart is answered with a fresh form, not a 403 page.

CONTRACT PINNED
* ``POST /login`` carrying a CSRF token from an earlier process (``app._CSRF_TOKEN`` has since been
  replaced) answers status 403 whose body is the login page: the hidden ``csrf_token`` field holds the
  CURRENT process token, and the error line reads exactly
  ``This form expired after a restart; sign in again``. It is never Werkzeug's raw 403 page.
* Nothing is submitted for the operator: the session stays unauthenticated, the failed-attempt
  throttle count is unchanged, and the credential is not compared (the right password in an expired
  form does not log in).
* Only the ``login`` endpoint changes: every other POST refused for its token still gets the hook's
  403 unchanged, and a login POST with the current token behaves as before (right password signs in,
  wrong password re-renders "Incorrect password.").

The auth gate and the CSRF check are re-enabled here: ``tests/conftest.py`` turns both off for every
test. The token is rotated between the page GET and the POST, which is what a restart does.
"""

from __future__ import annotations

import re

import pytest

import app as app_module

PASSWORD = "expired-form-test-password"
SECRET = "expired-form-test-secret-key"
EXPIRED_LINE = "This form expired after a restart; sign in again"
OLD_TOKEN = "old-process-token-0000000000000000000000000000"
NEW_TOKEN = "new-process-token-1111111111111111111111111111"
HIDDEN_FIELD = re.compile(r'name="csrf_token"\s+value="([^"]*)"')


@pytest.fixture(autouse=True)
def _clean_throttle():
    app_module._AUTH_FAILED_ATTEMPTS.clear()
    yield
    app_module._AUTH_FAILED_ATTEMPTS.clear()


@pytest.fixture
def guarded(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", PASSWORD)
    monkeypatch.setenv("SECRET_KEY", SECRET)
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    monkeypatch.setattr(app_module, "_csrf_check_enabled", True)
    app_module.app.config["TESTING"] = True
    monkeypatch.setattr(app_module, "_CSRF_TOKEN", OLD_TOKEN)
    with app_module.app.test_client() as client:
        yield client


def restart(monkeypatch):
    monkeypatch.setattr(app_module, "_CSRF_TOKEN", NEW_TOKEN)


def submit_old_form(client, password=PASSWORD):
    page = client.get("/login")
    assert HIDDEN_FIELD.search(page.get_data(as_text=True)).group(1) == OLD_TOKEN
    return page


class TestExpiredLoginForm:
    def test_answers_with_a_fresh_form_and_the_expired_line(self, guarded, monkeypatch):
        submit_old_form(guarded)
        restart(monkeypatch)
        response = guarded.post("/login", data={"password": PASSWORD, "csrf_token": OLD_TOKEN})
        body = response.get_data(as_text=True)
        assert response.status_code == 403, "the route's refusal status is unchanged"
        assert 'name="password"' in body, (
            "a 403 here must be the login page, not Werkzeug's raw page"
        )
        assert EXPIRED_LINE in body
        assert HIDDEN_FIELD.search(body).group(1) == NEW_TOKEN, (
            "the form must carry the current token"
        )
        assert "Forbidden" not in re.sub(r"<[^>]+>", " ", body).split(EXPIRED_LINE)[0][:200]

    def test_the_fresh_form_signs_in_with_no_other_step(self, guarded, monkeypatch):
        submit_old_form(guarded)
        restart(monkeypatch)
        refused = guarded.post("/login", data={"password": PASSWORD, "csrf_token": OLD_TOKEN})
        token = HIDDEN_FIELD.search(refused.get_data(as_text=True)).group(1)
        accepted = guarded.post("/login", data={"password": PASSWORD, "csrf_token": token})
        assert accepted.status_code == 302, "the re-rendered form must work as submitted"

    def test_nothing_is_submitted_for_the_operator(self, guarded, monkeypatch):
        submit_old_form(guarded)
        restart(monkeypatch)
        guarded.post("/login", data={"password": PASSWORD, "csrf_token": OLD_TOKEN})
        with guarded.session_transaction() as session:
            assert not session.get("authenticated"), (
                "an expired form must never sign the operator in"
            )
        assert guarded.get("/api/state").status_code == 401

    def test_a_wrong_password_in_an_expired_form_is_not_counted_as_a_failed_attempt(
        self, guarded, monkeypatch
    ):
        submit_old_form(guarded)
        restart(monkeypatch)
        for _ in range(app_module._AUTH_MAX_ATTEMPTS + 3):
            response = guarded.post("/login", data={"password": "wrong", "csrf_token": OLD_TOKEN})
            assert response.status_code == 403 and EXPIRED_LINE in response.get_data(as_text=True)
        assert not app_module._AUTH_FAILED_ATTEMPTS, (
            "an expired form must not count as a failed password"
        )

    def test_a_missing_token_is_the_same_refusal(self, guarded):
        response = guarded.post("/login", data={"password": PASSWORD})
        assert response.status_code == 403
        assert 'name="password"' in response.get_data(as_text=True)

    def test_a_current_token_login_is_unchanged(self, guarded):
        wrong = guarded.post("/login", data={"password": "wrong", "csrf_token": OLD_TOKEN})
        assert wrong.status_code == 200 and "Incorrect password." in wrong.get_data(as_text=True)
        right = guarded.post("/login", data={"password": PASSWORD, "csrf_token": OLD_TOKEN})
        assert right.status_code == 302


class TestOtherRefusalsAreUntouched:
    def test_another_post_refused_for_its_token_keeps_the_hooks_403_page(self, guarded):
        response = guarded.post("/api/trigger", headers={"X-CSRF-Token": "stale"})
        body = response.get_data(as_text=True)
        assert response.status_code == 403
        assert EXPIRED_LINE not in body and 'name="password"' not in body

    def test_the_throttle_still_locks_out_wrong_passwords_with_a_valid_token(self, guarded):
        for _ in range(app_module._AUTH_MAX_ATTEMPTS):
            guarded.post("/login", data={"password": "wrong", "csrf_token": OLD_TOKEN})
        locked = guarded.post("/login", data={"password": "wrong", "csrf_token": OLD_TOKEN})
        assert locked.status_code == 429
