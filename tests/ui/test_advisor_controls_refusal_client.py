"""RED: every /ai-advisor POST control survives a restart and never re-sends.

Each control is driven through the real page scripts (``ai_advisor.js``, ``ai_advisor_asset_swaps.js``,
``ai_advisor_logic_changes.js``, ``ai_advisor_chat.js`` with ``request_guard.js``) over the page the real app
renders. The 403 is the page the real CSRF hook gives (``tests/ui/_refusal_answers``); the rotated token
is the real ``GET /api/csrf-token``'s. The harness fails any test in which a POST is sent without a
click or twice for one click.

CONTRACT PINNED
* A control refused for its token (403) sends exactly one POST, fetches exactly one new token, and
  the page then says the action was not sent and that a new click is needed (text matching
  ``not sent`` and ``click``, in the card, form or an alert; the place is the control's own).
* The control works again on the next click, with the new token in ``X-CSRF-Token``.
* Chat: the bubble reads not sent with a ``Send again`` button, the input keeps the typed text and the
  history is unchanged; the Send again click sends one POST with the new token.
* Accept: the request body is ``{symphony_id, suggestion}`` as before; the card says the accept was
  not sent and needs a new click.
"""

from __future__ import annotations

import json
import re

import pytest

from tests.ui import _refusal_answers as answers
from tests.ui._page_js_harness import DEFAULT_TOKEN, render_page, route, run_page

TOKEN_URL = r"/api/csrf-token$"
NOT_SENT = re.compile(r"not sent", re.I)
CLICK = re.compile(r"click", re.I)

CASES = {
    "suggest": {
        "url": r"/ai-advisor/suggest$",
        "setup": "H.$('#symphony-id-input').innerHTML = '<option value=\"sym-x\" selected>sym-x</option>';"
        " H.$('#get-suggestions-btn').removeAttribute('disabled');",
        "invoke": "await H.click('#get-suggestions-btn');",
    },
    "reject_suggestion": {
        "url": r"/ai-advisor/reject$",
        "setup": "const box = H.$('#suggestions-container'); box._suggestions = [{field: 'X'}];"
        " box.innerHTML = '<div id=\"card-0\"></div>';",
        "invoke": "await H.act('reject', async () => { window.rejectSuggestion(0, 'sym-x'); });",
    },
    "accept_suggestion": {
        "url": r"/ai-advisor/accept$",
        "setup": "const box = H.$('#suggestions-container'); box._suggestions = [{field: 'X'}];"
        " box.innerHTML = '<div id=\"card-0\"></div>';",
        "invoke": "await H.act('accept', async () => { window.acceptSuggestion(0, 'sym-x'); });",
    },
    "frontrunner_run": {
        "url": r"/ai-advisor/frontrunner-builder/run$",
        "setup": "",
        "invoke": "await H.click('#fr-run-btn');",
    },
    "strategy_builder_run": {
        "url": r"/ai-advisor/strategy-builder/run$",
        "setup": "",
        "invoke": "await H.click('#sb-run-btn');",
    },
    "proposal_approve": {
        "url": r"/ai-advisor/proposal/approve$",
        "setup": 'document.body.insertAdjacentHTML(\'beforeend\', \'<div id="fr-card-5"><button data-testid="fr-approve-btn"></button>'
        '<button data-testid="fr-reject-btn"></button></div>\');',
        "invoke": "await H.act('approve', async () => { window.frApprove(5); });",
    },
    "proposal_reject": {
        "url": r"/ai-advisor/proposal/reject$",
        "setup": 'document.body.insertAdjacentHTML(\'beforeend\', \'<div id="fr-card-5"><button data-testid="fr-approve-btn"></button>'
        '<button data-testid="fr-reject-btn"></button></div>\');',
        "invoke": "await H.act('reject', async () => { window.frReject(5); });",
    },
    "retirement_approve": {
        "url": r"/ai-advisor/retirement/approve$",
        "setup": "document.body.insertAdjacentHTML('beforeend', '<div data-testid=\"retirement-recommendation-card\">"
        '<button id="ret-a" data-testid="retirement-approve-btn" data-candidate-id="cand-1"></button>'
        '<button data-testid="retirement-reject-btn" data-candidate-id="cand-1"></button></div>\');',
        "invoke": "await H.act('approve', async () => { window.retDispatchDecision('approve', H.$('#ret-a')); });",
    },
    "retirement_reject": {
        "url": r"/ai-advisor/retirement/reject$",
        "setup": "document.body.insertAdjacentHTML('beforeend', '<div data-testid=\"retirement-recommendation-card\">"
        '<button data-testid="retirement-approve-btn" data-candidate-id="cand-1"></button>'
        '<button id="ret-r" data-testid="retirement-reject-btn" data-candidate-id="cand-1"></button></div>\');',
        "invoke": "await H.act('reject', async () => { window.retDispatchDecision('reject', H.$('#ret-r')); });",
    },
    "retirement_review_run": {
        "url": r"/ai-advisor/retirement/review/run$",
        "setup": "document.body.insertAdjacentHTML('beforeend', '<button id=\"ret-run\"></button>"
        '<div data-testid="tenure-review-run-message"></div>\');',
        "invoke": "await H.act('run', async () => { window.retRunReview(H.$('#ret-run')); });",
    },
    "asset_swap_evaluate": {
        "url": r"/ai-advisor/asset-swaps/evaluate$",
        "setup": "H.$('#swap-symphony-select').innerHTML = '<option value=\"sym-x\" selected>x</option>';"
        " H.$('#swap-evaluate-btn').removeAttribute('disabled');",
        "invoke": "await H.click('#swap-evaluate-btn');",
    },
    "logic_change_evaluate": {
        "url": r"/ai-advisor/logic-changes/evaluate$",
        "setup": "H.$('#tweak-symphony-select').innerHTML = '<option value=\"sym-x\" selected>x</option>';"
        " H.$('#tweak-description').value = 'raise the threshold'; H.$('#tweak-evaluate-btn').removeAttribute('disabled');",
        "invoke": "await H.click('#tweak-evaluate-btn');",
    },
}


def on_advisor(scenario, *routes_):
    return run_page(
        render_page("/ai-advisor"),
        "await H.boot();\n" + RESTART + scenario,
        routes=list(routes_),
        pathname="/ai-advisor",
    )


# The daemon restarted after the page loaded: from now on the token route answers the new token.
RESTART = "H.setToken('" + answers.TOKEN_B + "');\n"


def token_down():
    return route(TOKEN_URL, {"json": {"csrf_token": DEFAULT_TOKEN}}, {"reject": True}, method="GET")


@pytest.mark.parametrize("name", list(CASES))
class TestEveryControl:
    def test_a_refusal_sends_one_post_refreshes_once_and_says_not_sent(self, name):
        case = CASES[name]
        out = on_advisor(
            f"""
            {{ {case["setup"]} }}
            {case["invoke"]}
            await H.advance(60000);
            return H.surfaced();
            """,
            route(case["url"], answers.csrf_refused(), method="POST"),
        )
        assert len(out.posts(case["url"].removesuffix("$"))) == 1, (
            f"{name}: a refused POST was sent again"
        )
        assert len(out.to("/api/csrf-token")) == 2, (
            f"{name}: the refusal must refresh the token once"
        )
        assert NOT_SENT.search(out.result) and CLICK.search(out.result), (
            f"{name}: nothing on the page says the action was not sent and needs a new click"
        )

    def test_the_next_click_works_with_the_new_token(self, name):
        case = CASES[name]
        ok = {"status": 200, "json": {"status": "ok"}}
        out = on_advisor(
            f"""
            {{ {case["setup"]} }}
            {case["invoke"]}
            {{ {case["setup"]} }}
            {case["invoke"]}
            return 1;
            """,
            route(case["url"], answers.csrf_refused(), ok, method="POST", strict=True),
        )
        first, second = out.posts(case["url"].removesuffix("$"))
        assert first["headers"]["x-csrf-token"] == DEFAULT_TOKEN
        assert second["headers"]["x-csrf-token"] == answers.TOKEN_B


class TestAcceptKeepsItsBody:
    def test_accept_body_is_symphony_and_suggestion_only(self):
        case = CASES["accept_suggestion"]
        out = on_advisor(
            f"{case['setup']} {case['invoke']} return 1;",
            route(case["url"], {"status": 200, "json": {"status": "accepted"}}, method="POST"),
        )
        (post,) = out.posts("/ai-advisor/accept")
        assert json.loads(post["body"]) == {"symphony_id": "sym-x", "suggestion": {"field": "X"}}
        assert post["headers"] == {
            "content-type": "application/json",
            "x-csrf-token": DEFAULT_TOKEN,
        }


class TestChat:
    SETUP = """
      window._chatAvailable = true;
      window.openChatPanel({artifactId: 'a-1', artifactType: 'asset_swap', artifactContext: {}, objective: 'x', gateDecision: 'ADOPT_CANDIDATE', keyStat: ''});
      const typed = 'why was this swap rejected?';
    """
    SEND = "H.$('#chat-input').value = typed; await H.act('send', async () => { window.sendChatMessage(); });"

    def test_a_refused_send_is_shown_as_not_sent_and_keeps_the_text(self):
        out = on_advisor(
            self.SETUP
            + self.SEND
            + """
            return {surfaced: H.surfaced(), input: H.value('#chat-input'), posts: H.posts('/ai-advisor/chat/send').length};
            """,
            route(r"/ai-advisor/chat/send$", answers.csrf_refused(), method="POST"),
        )
        assert out.result["posts"] == 1
        assert NOT_SENT.search(out.result["surfaced"]) and "Send again" in out.result["surfaced"]
        assert out.result["input"] == "why was this swap rejected?", "the typed text was lost"

    def test_send_again_sends_one_post_with_the_new_token_and_the_same_message(self):
        out = on_advisor(
            self.SETUP
            + self.SEND
            + """
            const again = H.$$('button').find((b) => /Send again/.test(b.textContent));
            if (!again) throw new Error('there is no Send again button');
            await H.act('send again', async () => { again.click(); });
            return 1;
            """,
            route(
                r"/ai-advisor/chat/send$",
                answers.csrf_refused(),
                {"json": {"reply": "because"}},
                method="POST",
                strict=True,
            ),
        )
        first, second = out.posts("/ai-advisor/chat/send")
        assert second["headers"]["x-csrf-token"] == answers.TOKEN_B
        assert json.loads(second["body"])["message"] == json.loads(first["body"])["message"]
        assert json.loads(second["body"])["history"] == json.loads(first["body"])["history"], (
            "the refused message must not be in the history twice"
        )
