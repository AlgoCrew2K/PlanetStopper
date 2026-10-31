"""The CSRF gate: the form field belongs to the login form alone, a cross-site POST is refused,
and a token that is not ASCII is a clean 403.

The process-wide token is public (``GET /api/csrf-token`` serves it, and so does the login page), and
the site sits on a host whose registrable domain is shared with strangers, so a page on a neighbouring
host is same-site and its form POSTs carry the session cookie. The token in the header is what stops
that page; a ``csrf_token`` form field accepted on every route would let it through for any route
that reads no JSON body.

Contract pinned
* A ``csrf_token`` form field (urlencoded or multipart) is accepted on the login endpoint only. On any
  other route it is a 403, whatever the value.
* The ``X-CSRF-Token`` header stays accepted on every route.
* A POST whose ``Sec-Fetch-Site`` is ``same-site`` or ``cross-site`` is a 403 even with a valid token;
  ``same-origin``, ``none`` and an absent header are not refused by this rule (a script client sends
  none).
* A token that is not ASCII (in the header or the login form's field) is a 403: no 500, no traceback.

Safety of the routes used: ``/ai-advisor/retirement/reject`` answers 400 to a body that names no
recommendation and writes nothing, so a request that gets past the gate cannot do anything here.
``/api/sell_account`` and the other money routes are never called.
"""

from __future__ import annotations

import logging

import pytest

import app as app_module

HARMLESS = "/ai-advisor/retirement/reject"
OTHER_HARMLESS = "/api/candidate-alert/mark-viewed"


@pytest.fixture(autouse=True)
def _gate_on(monkeypatch):
    """The root conftest switches the CSRF check off for every test; these tests are about it."""
    monkeypatch.setattr(app_module, "_csrf_check_enabled", True)
    app_module.app.config["TESTING"] = True


@pytest.fixture
def client():
    with app_module.app.test_client() as c:
        yield c


def _token() -> str:
    return app_module._CSRF_TOKEN


class TestTheFormFieldIsForTheLoginFormOnly:
    @pytest.mark.parametrize("route", [HARMLESS, OTHER_HARMLESS])
    def test_a_valid_token_in_a_urlencoded_form_field_is_refused_off_the_login_route(
        self, client, route
    ):
        response = client.post(route, data={"csrf_token": _token()})
        assert response.status_code == 403, (
            f"POST {route} with the token in a form field answered {response.status_code}"
        )

    @pytest.mark.parametrize("route", [HARMLESS, OTHER_HARMLESS])
    def test_a_valid_token_in_a_multipart_form_field_is_refused_off_the_login_route(
        self, client, route
    ):
        response = client.post(
            route, data={"csrf_token": _token()}, content_type="multipart/form-data"
        )
        assert response.status_code == 403, (
            f"POST {route} with the token in a multipart field answered {response.status_code}"
        )

    def test_the_header_token_is_accepted_off_the_login_route(self, client):
        response = client.post(HARMLESS, json={}, headers={"X-CSRF-Token": _token()})
        assert response.status_code != 403, "the header token was refused"

    def test_the_header_token_is_accepted_beside_a_form_body(self, client):
        response = client.post(
            HARMLESS, data={"unrelated": "x"}, headers={"X-CSRF-Token": _token()}
        )
        assert response.status_code != 403, "the header token was refused for a form body"

    def test_the_login_form_still_signs_in_with_the_token_in_its_field(self, client):
        accepted = client.post(
            "/login", data={"password": "not-the-password", "csrf_token": _token()}
        )
        assert accepted.status_code != 403, "the login form's own token field was refused"

    def test_the_login_form_with_a_wrong_field_token_is_refused(self, client):
        refused = client.post("/login", data={"password": "x", "csrf_token": _token()[::-1]})
        assert refused.status_code == 403


class TestACrossSitePostIsRefused:
    @pytest.mark.parametrize("site", ["same-site", "cross-site"])
    def test_a_post_from_a_neighbouring_site_is_refused_even_with_the_header_token(
        self, client, site
    ):
        response = client.post(
            HARMLESS, json={}, headers={"X-CSRF-Token": _token(), "Sec-Fetch-Site": site}
        )
        assert response.status_code == 403, (
            f"Sec-Fetch-Site: {site} answered {response.status_code}"
        )

    @pytest.mark.parametrize("site", ["same-site", "cross-site"])
    def test_the_login_post_from_a_neighbouring_site_is_refused_too(self, client, site):
        response = client.post(
            "/login",
            data={"password": "x", "csrf_token": _token()},
            headers={"Sec-Fetch-Site": site},
        )
        assert response.status_code == 403

    @pytest.mark.parametrize(
        "headers", [{"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"}, {}]
    )
    def test_a_same_origin_post_a_user_initiated_one_and_a_script_client_are_not_refused(
        self, client, headers
    ):
        response = client.post(HARMLESS, json={}, headers={"X-CSRF-Token": _token(), **headers})
        assert response.status_code != 403, f"{headers} was refused"

    def test_a_get_with_a_cross_site_header_is_not_a_post_and_is_not_refused(self, client):
        response = client.get("/api/csrf-token", headers={"Sec-Fetch-Site": "cross-site"})
        assert response.status_code == 200


class TestANonAsciiTokenIsACleanRefusal:
    @pytest.fixture(autouse=True)
    def _answer_errors_as_a_server_does(self, monkeypatch):
        monkeypatch.setitem(app_module.app.config, "PROPAGATE_EXCEPTIONS", False)

    def _no_traceback_was_logged(self, caplog) -> None:
        with_traceback = [r for r in caplog.records if r.exc_info]
        assert with_traceback == [], [r.getMessage() for r in with_traceback]

    def test_a_non_ascii_header_token_is_a_403(self, client, caplog):
        with caplog.at_level(logging.DEBUG):
            response = client.post(HARMLESS, json={}, headers={"X-CSRF-Token": "tökén"})
        assert response.status_code == 403, f"answered {response.status_code}"
        self._no_traceback_was_logged(caplog)

    def test_a_non_ascii_login_form_token_is_a_403(self, client, caplog):
        with caplog.at_level(logging.DEBUG):
            response = client.post("/login", data={"password": "x", "csrf_token": "tökén"})
        assert response.status_code == 403, f"answered {response.status_code}"
        self._no_traceback_was_logged(caplog)

    @pytest.mark.parametrize("token", ["", " ", "\u0000", "a" * 10_000])
    def test_an_empty_blank_or_oversized_header_token_is_a_403(self, client, token):
        response = client.post(HARMLESS, json={}, headers={"X-CSRF-Token": token})
        assert response.status_code == 403
