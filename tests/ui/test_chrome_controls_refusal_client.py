"""RED: the header's controls survive a daemon restart and never send anything twice.

Force run now, Emergency Liquidate and the candidate-alert mark-viewed POST are driven through the
real ``static/chrome.js`` (with ``request_guard.js``) on the rendered ``/history`` page. The answers
are the real app's: the CSRF hook's 403 page with the check re-enabled, the rotated token from
``GET /api/csrf-token``, the dry-run 200 from ``POST /api/sell_account`` over a patched ``.env``
reader. The harness fails any test in which a POST is sent without an operator click or twice for
one click, so "never re-sends" is checked on every scenario here, not only where it is asserted.

CONTRACT PINNED
* A 403 on any of these POSTs refreshes the token (one ``GET /api/csrf-token``) and sends nothing.
* Force run: the button reads ``Not sent: click again`` for the existing 2000 ms, then returns to
  ``Force run now`` and is enabled.
* Emergency Liquidate: the alert keeps the sentence "The session check refused the request (HTTP 403),
  so nothing was liquidated." and ends with "Click Confirm again." (no "Reload the page"). The next
  Confirm click sends one request byte-identical to the base record's, with the new token.
* Mark-viewed: the badge keeps its count; the next click sends one POST with the new token.
* A token fetch that fails after the 403 is said: the text names "not sent" and the connection.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tests.ui import _refusal_answers as answers
from tests.ui._page_js_harness import DEFAULT_TOKEN, render_page, route, run_page

BASE_RECORD = json.loads(
    (
        Path(__file__).resolve().parents[1] / "fixtures" / "chrome" / "panic_request_at_base.json"
    ).read_text(encoding="utf-8")
)
ACCOUNT = BASE_RECORD["inputs"]["account_id"]
FORCE = '[data-testid="force-run-btn"]'
TOKEN_URL = r"/api/csrf-token$"
NOT_SENT = re.compile(r"not sent", re.I)
CONNECTION = re.compile(r"connection", re.I)

OPEN_AND_CONFIRM = """
  await H.act('open', async () => { window.openPanicModal(); });
  await H.set('#panic-phrase', 'LIQUIDATE');
  await H.click('#panic-confirm-btn');
"""


def token_sequence(*, rotated=True):
    second = answers.token_answer() if rotated else {"reject": True}
    return route(TOKEN_URL, {"json": {"csrf_token": DEFAULT_TOKEN}}, second, method="GET")


def on_history(scenario, *routes_):
    return run_page(render_page("/history"), "await H.boot();\n" + scenario, routes=list(routes_))


class TestForceRun:
    def test_refused_for_its_token_says_not_sent_for_two_seconds(self):
        out = on_history(
            f"""
            await H.click('{FORCE}');
            const at = (ms) => ({{text: H.text('{FORCE}'), disabled: H.$('{FORCE}').disabled}});
            const shown = at();
            await H.advance(1900); const early = at();
            await H.advance(200); const late = at();
            return {{shown, early, late, gets: H.to('/api/csrf-token').length}};
            """,
            route(r"/api/trigger$", answers.csrf_refused(), method="POST"),
            token_sequence(),
        )
        assert out.result["shown"]["text"] == "Not sent: click again"
        assert out.result["early"]["text"] == "Not sent: click again"
        assert out.result["late"] == {"text": "Force run now", "disabled": False}
        assert out.result["gets"] == 2, "the refusal refreshes the token once"
        assert len(out.posts("/api/trigger")) == 1

    def test_a_token_fetch_that_fails_says_the_connection_failed(self):
        out = on_history(
            f"await H.click('{FORCE}'); return H.text('{FORCE}');",
            route(r"/api/trigger$", answers.csrf_refused(), method="POST"),
            token_sequence(rotated=False),
        )
        assert NOT_SENT.search(out.result) and CONNECTION.search(out.result), out.result
        assert len(out.posts("/api/trigger")) == 1

    def test_the_request_is_the_one_it_always_was(self):
        out = on_history(
            f"await H.click('{FORCE}'); return 1;",
            route(
                r"/api/trigger$",
                {"json": {"status": "success", "message": "Manual trigger disabled"}},
                method="POST",
            ),
        )
        (post,) = out.posts("/api/trigger")
        assert post["headers"] == {"x-csrf-token": DEFAULT_TOKEN}
        assert post["body"] is None and post["initKeys"] == ["headers", "method"]


class TestEmergencyLiquidate:
    REFUSAL = "The session check refused the request (HTTP 403), so nothing was liquidated."

    def routes(self, sell):
        return [
            route(r"/api/accounts$", answers.accounts([ACCOUNT]), method="GET"),
            route(r"/api/sell_account$", sell, method="POST"),
            token_sequence(),
        ]

    def test_refusal_sends_one_request_and_tells_the_operator_to_confirm_again(self):
        out = on_history(
            OPEN_AND_CONFIRM + "await H.advance(120000); return 1;",
            *self.routes(answers.csrf_refused()),
        )
        assert len(out.posts("/api/sell_account")) == 1, (
            "a refused liquidation request was sent again"
        )
        (alert,) = out.alerts
        assert self.REFUSAL in alert
        assert alert.rstrip().endswith("Click Confirm again.")
        assert "Reload the page" not in alert
        assert len(out.to("/api/csrf-token")) == 2

    def test_the_next_confirm_sends_one_valid_request_equal_to_the_base_record(self):
        accepted = answers.accepted_sell(ACCOUNT)
        out = run_page(
            render_page("/history"),
            "await H.boot();\n" + OPEN_AND_CONFIRM + OPEN_AND_CONFIRM + "return 1;",
            routes=[
                route(r"/api/accounts$", answers.accounts([ACCOUNT]), method="GET"),
                route(
                    r"/api/sell_account$",
                    answers.csrf_refused(),
                    accepted,
                    method="POST",
                    strict=True,
                ),
                token_sequence(),
            ],
        )
        first, second = out.posts("/api/sell_account")
        assert first["headers"]["x-csrf-token"] == DEFAULT_TOKEN, (
            "the first click used the load-time token"
        )
        base = BASE_RECORD["single_confirm_request"]
        assert second["url"] == base["url"] and second["method"] == base["method"]
        expected_headers = {k.lower(): v for k, v in base["headers"].items()}
        expected_headers["x-csrf-token"] = answers.TOKEN_B
        assert second["headers"] == expected_headers, (
            "the retry-free second click carries the new token"
        )
        assert second["body"] == base["body"], (
            "the body must stay byte-identical to the base record"
        )
        assert len(out.alerts) == 2 and "Click Confirm again." not in out.alerts[1]

    def test_a_token_fetch_that_fails_says_the_connection_failed(self):
        out = on_history(
            OPEN_AND_CONFIRM + "return 1;",
            route(r"/api/accounts$", answers.accounts([ACCOUNT]), method="GET"),
            route(r"/api/sell_account$", answers.csrf_refused(), method="POST"),
            token_sequence(rotated=False),
        )
        (alert,) = out.alerts
        assert NOT_SENT.search(alert) or "nothing was liquidated" in alert
        assert CONNECTION.search(alert), alert
        assert len(out.posts("/api/sell_account")) == 1


class TestMarkViewed:
    INDICATOR = "#candidate-alert-indicator"
    BADGE = "#candidate-alert-badge"

    def routes(self, mark):
        return [
            route(
                r"/api/candidate-alert$",
                {"json": {"new_valid_count": 3, "last_run": None}},
                method="GET",
            ),
            route(r"/api/candidate-alert/mark-viewed$", mark, method="POST"),
            token_sequence(),
        ]

    def test_refused_keeps_the_badge_and_the_next_click_sends_one_post_with_the_new_token(self):
        out = on_history(
            f"""
            const badge = () => H.text('{self.BADGE}');
            const before = badge();
            await H.click('{self.INDICATOR}');
            const afterRefusal = badge();
            await H.advance(5000);
            await H.click('{self.INDICATOR}');
            return {{before, afterRefusal}};
            """,
            *self.routes(answers.csrf_refused()),
        )
        assert out.result == {"before": "3", "afterRefusal": "3"}
        first, second = out.posts("/api/candidate-alert/mark-viewed")
        assert first["keepalive"] is True and second["keepalive"] is True
        assert first["headers"]["x-csrf-token"] == DEFAULT_TOKEN
        assert second["headers"]["x-csrf-token"] == answers.TOKEN_B

    def test_one_click_sends_exactly_one_post(self):
        out = on_history(
            f"await H.click('{self.INDICATOR}'); await H.advance(120000); return 1;",
            *self.routes(answers.csrf_refused()),
        )
        assert len(out.posts("/api/candidate-alert/mark-viewed")) == 1
