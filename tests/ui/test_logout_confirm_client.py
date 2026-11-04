"""The logout confirm page signs out with a header-token POST and never re-sends.

The real page scripts (``request_guard.js`` then ``logout_confirm.js``, as the rendered ``GET /logout``
names them) run unmodified in ``tests/ui/_page_js_harness``. The 403 is the page the real CSRF hook
gives (``tests/ui/_refusal_answers``) and the rotated token is the real token route's.

CONTRACT PINNED
* Choosing "Sign out" (the button's click, or the form's submit) sends exactly one ``POST /logout``
  carrying the page's current token in ``X-CSRF-Token``, and the browser's own form submission is
  prevented (a native submit would post without the header and be refused).
* When the POST succeeds (the followed redirect lands on the 200 login page) the page goes to ``/login``.
* A 403 after a restart: the page reads "This page expired after a restart; choose Sign out again",
  one new token is fetched, nothing is re-sent on its own, the button works again and the next click
  sends one POST with the new token.
* Any other failure leaves the page where it is, says "Choose Sign out again." (a network failure or a
  403 whose token could not be refreshed) or "Not signed out: the server answered HTTP <status>. Choose
  Sign out again.", and enables the button again.
* Two clicks (or a click and a submit) while the first POST is in flight send one POST.
"""

from __future__ import annotations

import re

import pytest

from tests.ui import _refusal_answers as answers
from tests.ui._page_js_harness import DEFAULT_TOKEN, render_page, route, run_page

LOGOUT = r"/logout$"
TOKEN_URL = r"/api/csrf-token$"
BUTTON = '[data-testid="logout-btn"]'
MESSAGE = '[data-testid="logout-message"]'
EXPIRED_TEXT = "This page expired after a restart; choose Sign out again"
RESTART = "H.setToken('" + answers.TOKEN_B + "');\n"
CHOOSE_AGAIN = re.compile(r"Choose Sign out again\.")


@pytest.fixture(scope="module")
def confirm_html() -> str:
    return render_page("/logout")


@pytest.fixture(scope="module")
def login_answer() -> dict:
    """What the followed redirect lands on: the real login page, answered 200."""
    return {"status": 200, "text": render_page("/login")}


def on_confirm_page(html, scenario, *routes_):
    return run_page(html, "await H.boot();\n" + scenario, routes=list(routes_), pathname="/logout")


class TestChoosingSignOut:
    def test_the_click_sends_one_post_with_the_current_token_and_goes_to_login(
        self, confirm_html, login_answer
    ):
        out = on_confirm_page(
            confirm_html,
            f"await H.click('{BUTTON}'); await H.advance(1000);",
            route(LOGOUT, login_answer, method="POST"),
        )

        posts = out.posts(LOGOUT.removesuffix("$"))
        assert len(posts) == 1, f"{len(posts)} POSTs to /logout"
        assert posts[0]["headers"].get("x-csrf-token") == DEFAULT_TOKEN
        assert out.navigations == ["assign:/login"]

    def test_the_form_submit_sends_one_post_with_the_token_and_is_default_prevented(
        self, confirm_html, login_answer
    ):
        out = on_confirm_page(
            confirm_html,
            """
            const form = H.$('#logout-form');
            const ev = { type: 'submit', target: form, bubbles: true, defaultPrevented: false,
                         preventDefault() { this.defaultPrevented = true; }, stopPropagation() {} };
            await H.act('submit', async () => { form.dispatchEvent(ev); });
            await H.advance(1000);
            return ev.defaultPrevented;
            """,
            route(LOGOUT, login_answer, method="POST"),
        )

        assert out.result is True, "the submit must be prevented: a native submit has no header"
        posts = out.posts(LOGOUT.removesuffix("$"))
        assert len(posts) == 1
        assert posts[0]["headers"].get("x-csrf-token") == DEFAULT_TOKEN
        assert out.navigations == ["assign:/login"]

    def test_the_button_click_is_default_prevented(self, confirm_html, login_answer):
        out = on_confirm_page(
            confirm_html,
            """
            const btn = H.$('[data-testid="logout-btn"]');
            const ev = { type: 'click', target: btn, bubbles: true, defaultPrevented: false,
                         preventDefault() { this.defaultPrevented = true; }, stopPropagation() {} };
            await H.act('click', async () => { btn.dispatchEvent(ev); });
            await H.advance(1000);
            return ev.defaultPrevented;
            """,
            route(LOGOUT, login_answer, method="POST"),
        )

        assert out.result is True
        assert len(out.posts(LOGOUT.removesuffix("$"))) == 1

    def test_nothing_is_sent_before_the_operator_chooses(self, confirm_html):
        out = on_confirm_page(confirm_html, "await H.advance(120000);")
        assert out.posts() == []
        assert out.navigations == []


class TestARefusalAfterARestart:
    def test_the_page_says_it_expired_fetches_one_token_and_sends_nothing_by_itself(
        self, confirm_html
    ):
        out = on_confirm_page(
            confirm_html,
            RESTART
            + f"""
            await H.click('{BUTTON}');
            await H.advance(60000);
            return {{ text: H.text('{MESSAGE}'), visible: H.isVisible('{MESSAGE}'),
                     disabled: H.$('{BUTTON}').hasAttribute('disabled') }};
            """,
            route(LOGOUT, answers.csrf_refused(), method="POST"),
        )

        assert EXPIRED_TEXT in out.result["text"], out.result
        assert out.result["visible"] is True
        assert out.result["disabled"] is False, "the button must work again"
        assert len(out.posts(LOGOUT.removesuffix("$"))) == 1, "a refused POST must not be re-sent"
        assert len(out.to(TOKEN_URL, "GET")) == 2, "the load's read plus one new token"
        assert out.navigations == []

    def test_the_next_click_sends_one_post_with_the_new_token_and_signs_out(
        self, confirm_html, login_answer
    ):
        out = on_confirm_page(
            confirm_html,
            RESTART
            + f"""
            await H.click('{BUTTON}');
            await H.advance(1000);
            await H.click('{BUTTON}');
            await H.advance(1000);
            """,
            route(LOGOUT, answers.csrf_refused(), login_answer, method="POST"),
        )

        posts = out.posts(LOGOUT.removesuffix("$"))
        assert len(posts) == 2
        assert posts[0]["headers"].get("x-csrf-token") == DEFAULT_TOKEN
        assert posts[1]["headers"].get("x-csrf-token") == answers.TOKEN_B
        assert out.navigations == ["assign:/login"]

    def test_the_expired_message_does_not_stay_once_the_operator_chooses_again(
        self, confirm_html, login_answer
    ):
        out = on_confirm_page(
            confirm_html,
            RESTART
            + f"""
            await H.click('{BUTTON}');
            await H.click('{BUTTON}');
            return {{ text: H.text('{MESSAGE}'), visible: H.isVisible('{MESSAGE}') }};
            """,
            route(
                LOGOUT, answers.csrf_refused(), {**login_answer, "hold": "second"}, method="POST"
            ),
        )

        assert EXPIRED_TEXT not in (out.result["text"] or "") or not out.result["visible"], (
            "the stale message must be hidden while the second POST is in flight"
        )


class TestAnyOtherFailure:
    def test_a_refusal_whose_token_could_not_be_refreshed_says_to_choose_again(self, confirm_html):
        out = on_confirm_page(
            confirm_html,
            f"""
            await H.click('{BUTTON}');
            await H.advance(60000);
            return {{ text: H.surfaced(), disabled: H.$('{BUTTON}').hasAttribute('disabled') }};
            """,
            route(LOGOUT, answers.csrf_refused(), method="POST"),
            route(
                TOKEN_URL, {"json": {"csrf_token": DEFAULT_TOKEN}}, {"reject": True}, method="GET"
            ),
        )

        assert CHOOSE_AGAIN.search(out.result["text"]), out.result["text"]
        assert EXPIRED_TEXT not in out.result["text"], (
            "the restart text claims a refresh that failed"
        )
        assert out.result["disabled"] is False
        assert len(out.posts(LOGOUT.removesuffix("$"))) == 1
        assert out.navigations == []

    def test_a_network_failure_says_to_choose_again_and_enables_the_button(self, confirm_html):
        out = on_confirm_page(
            confirm_html,
            f"""
            await H.click('{BUTTON}');
            await H.advance(60000);
            return {{ text: H.surfaced(), disabled: H.$('{BUTTON}').hasAttribute('disabled') }};
            """,
            route(LOGOUT, {"reject": True}, method="POST"),
        )

        assert CHOOSE_AGAIN.search(out.result["text"]), out.result["text"]
        assert out.result["disabled"] is False
        assert len(out.posts(LOGOUT.removesuffix("$"))) == 1
        assert out.navigations == []

    @pytest.mark.parametrize("status", [400, 429, 500, 503])
    def test_another_status_is_named_in_the_message(self, confirm_html, status):
        out = on_confirm_page(
            confirm_html,
            f"""
            await H.click('{BUTTON}');
            await H.advance(60000);
            return {{ text: H.surfaced(), disabled: H.$('{BUTTON}').hasAttribute('disabled') }};
            """,
            route(LOGOUT, {"status": status, "json": {"error": "x"}}, method="POST"),
        )

        text = out.result["text"]
        assert f"HTTP {status}" in text and CHOOSE_AGAIN.search(text), text
        assert out.result["disabled"] is False
        assert len(out.posts(LOGOUT.removesuffix("$"))) == 1
        assert out.navigations == []


class TestADoubleChoice:
    def test_two_clicks_while_the_first_post_is_in_flight_send_one_post(
        self, confirm_html, login_answer
    ):
        out = on_confirm_page(
            confirm_html,
            f"""
            await H.click('{BUTTON}');
            await H.click('{BUTTON}');
            await H.click('{BUTTON}');
            await H.release('first');
            await H.advance(1000);
            """,
            route(LOGOUT, {**login_answer, "hold": "first"}, method="POST"),
        )

        assert len(out.posts(LOGOUT.removesuffix("$"))) == 1
        assert out.navigations == ["assign:/login"]

    def test_a_submit_while_the_first_post_is_in_flight_sends_nothing_more(
        self, confirm_html, login_answer
    ):
        out = on_confirm_page(
            confirm_html,
            f"""
            await H.click('{BUTTON}');
            const form = H.$('#logout-form');
            const ev = {{ type: 'submit', target: form, bubbles: true, defaultPrevented: false,
                         preventDefault() {{ this.defaultPrevented = true; }}, stopPropagation() {{}} }};
            await H.act('second submit', async () => {{ form.dispatchEvent(ev); }});
            await H.release('first');
            await H.advance(1000);
            return ev.defaultPrevented;
            """,
            route(LOGOUT, {**login_answer, "hold": "first"}, method="POST"),
        )

        assert out.result is True, "even a second submit must not fall through to a native post"
        assert len(out.posts(LOGOUT.removesuffix("$"))) == 1
