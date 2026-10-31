"""Emergency Liquidate's Confirm sends one request while one is unanswered.

A double click on Confirm sent two POSTs to ``/api/sell_account`` and started two liquidation threads.
The route and the request stay exactly as they are (this is client-side only); Confirm is ignored from the
moment a request is sent until its answer arrives, an answer of any kind (accepted, refused, the
connection lost), after which the next Confirm sends a new request.

Real call path: ``static/chrome.js`` (with ``request_guard.js``) runs unmodified on the rendered
``/history`` page (tests/ui/_page_js_harness.py). The answer is the real route's dry-run 200 over a
patched ``.env`` reader (tests/ui/_refusal_answers.py), held until the scenario releases it, so a click
made while the request is in flight is a click made while it is unanswered. ``/api/sell_account`` is
never called for real: every request goes to the harness's recorded fetch.

Contract pinned
* A second Confirm while the first request is unanswered sends nothing (one POST in all).
* The one request is byte-identical to the recorded base request: URL, method, headers (the token is
  the page's own) and body.
* Once the request is answered, whatever the answer, the next Confirm sends one new request.
"""

from __future__ import annotations

from tests.ui import _refusal_answers as answers
from tests.ui._page_js_harness import DEFAULT_TOKEN, render_page, route, run_page
from tests.ui.test_chrome_controls_refusal_client import (
    ACCOUNT,
    BASE_RECORD,
    OPEN_AND_CONFIRM,
)

SELL = r"/api/sell_account$"
SELL_PATH = "/api/sell_account"
CONFIRM = "#panic-confirm-btn"


def _on_history(scenario: str, *routes_):
    return run_page(render_page("/history"), "await H.boot();\n" + scenario, routes=list(routes_))


def _accounts():
    return route(r"/api/accounts$", answers.accounts([ACCOUNT]), method="GET")


def _held(answer: dict) -> dict:
    return {**answer, "hold": "sell"}


class TestOneRequestWhileOneIsUnanswered:
    def test_a_second_confirm_before_the_answer_sends_nothing(self):
        out = _on_history(
            OPEN_AND_CONFIRM
            + f"""
            await H.click('{CONFIRM}');
            const waiting = H.posts('{SELL}').length;
            await H.release('sell');
            return {{ waiting, answered: H.posts('{SELL}').length }};
            """,
            _accounts(),
            route(SELL, _held(answers.accepted_sell(ACCOUNT)), method="POST"),
        )
        assert out.result == {"waiting": 1, "answered": 1}, out.result
        assert len(out.posts(SELL_PATH)) == 1, "a double click started two liquidations"

    def test_the_one_request_is_byte_identical_to_the_recorded_base_request(self):
        out = _on_history(
            OPEN_AND_CONFIRM + "await H.release('sell'); return 1;",
            _accounts(),
            route(SELL, _held(answers.accepted_sell(ACCOUNT)), method="POST"),
        )
        (post,) = out.posts(SELL_PATH)
        base = BASE_RECORD["single_confirm_request"]
        assert post["url"] == base["url"] and post["method"] == base["method"]
        expected_headers = {k.lower(): v for k, v in base["headers"].items()}
        expected_headers["x-csrf-token"] = DEFAULT_TOKEN
        assert post["headers"] == expected_headers
        assert post["body"] == base["body"], "the body must stay byte-identical to the base record"


class TestTheGuardLetsGoOfEveryAnswer:
    def test_after_an_accepted_answer_the_next_confirm_sends_a_new_request(self):
        out = _on_history(
            OPEN_AND_CONFIRM
            + f"""
            await H.release('sell');
            const afterFirst = H.posts('{SELL}').length;
            """
            + OPEN_AND_CONFIRM
            + "return { afterFirst };",
            _accounts(),
            route(
                SELL,
                _held(answers.accepted_sell(ACCOUNT)),
                answers.accepted_sell(ACCOUNT),
                method="POST",
            ),
        )
        assert out.result["afterFirst"] == 1
        assert len(out.posts(SELL_PATH)) == 2, "the guard was never released after the answer"

    def test_after_a_refusal_the_next_confirm_sends_a_new_request(self):
        out = _on_history(
            OPEN_AND_CONFIRM
            + """
            await H.release('sell');
            """
            + OPEN_AND_CONFIRM
            + "return 1;",
            _accounts(),
            route(
                SELL,
                _held(answers.csrf_refused()),
                answers.accepted_sell(ACCOUNT),
                method="POST",
            ),
        )
        assert len(out.posts(SELL_PATH)) == 2, "the guard stayed on after a refused answer"

    def test_after_the_connection_was_lost_the_next_confirm_sends_a_new_request(self):
        out = _on_history(
            OPEN_AND_CONFIRM
            + """
            await H.release('sell');
            """
            + OPEN_AND_CONFIRM
            + "return 1;",
            _accounts(),
            route(
                SELL,
                {"hold": "sell", "reject": True},
                answers.accepted_sell(ACCOUNT),
                method="POST",
            ),
        )
        assert len(out.posts(SELL_PATH)) == 2, "the guard stayed on after a lost connection"
