"""RED: the dashboard's money controls survive a restart, say why a click failed and never re-send.

``static/index.js`` (with ``request_guard.js`` and ``chrome.js``) runs unmodified over the page the real app
renders, in the whole-file harness of ``tests/dashboard/_index_js_harness.py``. The answers are the real
routes': the 400 of ``POST /api/sell_account`` for Cash Now's body, the CSRF hook's 403 page, the
rotated token, the poll's ``/api/state`` body. Controls are called as the page's own buttons call
them; the scenarios assert on the requests the page recorded, so a POST sent twice for one click is
visible.

CONTRACT PINNED
* Cash Now: the server's refusal message is shown (``Not sold: confirm_account_id is required`` for
  the route's 400), the button is enabled again, exactly one POST was sent; a 403 refreshes the token
  once and the text says the sale was not sent and needs a new click; a 200 reads ``In cash``.
* Fleet Dismiss: a 403 keeps the banner, refreshes the token once, shows ``Not dismissed: click Dismiss
  again`` in ``#fleet-dismiss-notice`` and re-enables the button; any other failure keeps the notice's
  ``Dismissal not saved``.
* Managed Sleeves arm-to-paper, disarm and delete: a 403 says the action was not sent and needs a new
  click, does not reload the page and sends one POST.
* ``loadState`` notes ``daemon_started_at``: a poll that shows a new value fetches one new token.
* None of these requests changes: URL, method, headers other than the token value, and body.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.app import _chrome_route_answers as real
from tests.dashboard._index_js_harness import get_world, run_scenario
from tests.ui import _refusal_answers as answers

STATIC = Path(__file__).resolve().parents[2] / "static"
SCRIPTS = tuple(
    f"static/{name}"
    for name in ("request_guard.js", "chrome.js", "index.js")
    if (STATIC / name).exists()
)
NOT_SENT = re.compile(r"not sent", re.I)

COMMON = """
  if (!H.sandbox.PSRequest) throw new Error('window.PSRequest is not defined');
  const seq = (list) => { let n = 0; return () => list[Math.min(n++, list.length - 1)]; };
  const alerts = [];
  H.sandbox.alert = (m) => { alerts.push(String(m)); };
  const post = (re) => H.fetchLog.filter((e) => e.method === 'POST' && re.test(e.url));
  const surfaced = () => alerts.join(' | ') + ' | ' + H.sandbox.document.body.textContent;
  const tokenGets = () => H.fetchLog.filter((e) => e.url === '/api/csrf-token').length;
  const refused = H.bodies.refused, tokenB = H.bodies.tokenB;
  const newButton = () => {
    const b = H.sandbox.document.createElement('button');
    H.sandbox.document.body.appendChild(b);
    return b;
  };
"""


def refused_spec():
    return answers.csrf_refused()


def cash_now_400():
    answer = real.sell_answer({"symphony_id": "abc123"})
    assert answer["status"] == 400, (
        f"the sell route answered {answer['status']} for Cash Now's body"
    )
    assert "confirm_account_id is required" in answer["body"]
    return {"status": 400, "body": json.loads(answer["body"])}


def spec_for_js(spec):
    """A harness-route spec in the index harness's shape (``text`` or ``body``)."""
    if "text" in spec:
        return {"status": spec["status"], "text": spec["text"]}
    return {"status": spec["status"], "body": spec["json"]}


def run(scenario, **extra):
    world = get_world("cards-open")
    bodies = {
        "refused": spec_for_js(refused_spec()),
        "tokenB": {"body": answers.token_answer()["json"]},
        "sell400": cash_now_400(),
        **extra,
    }
    out = run_scenario(
        world,
        COMMON + "\nawait H.boot({extras: true});\n" + scenario,
        extra_bodies=bodies,
        scripts=SCRIPTS,
    )
    return out["result"]


CASH = """
  const btn = H.sandbox.document.querySelectorAll('[data-testid="cash-now-btn"]').find((b) => !b.hasAttribute('disabled'));
  if (!btn) throw new Error('no enabled Cash Now button in the page');
  const card = btn.closest('[data-testid="sym-card"]');
  const symId = card.getAttribute('data-sym-id') || 'sym-from-card';
  const click = async () => { H.sandbox.cashNow({stopPropagation() {}, currentTarget: btn}, symId); await H.settle(); };
"""


class TestCashNow:
    def test_the_routes_400_is_shown_with_its_message_and_the_button_comes_back(self):
        result = run(
            CASH
            + """
            H.route('/api/sell_account', {status: H.bodies.sell400.status, body: H.bodies.sell400.body});
            await click();
            return {text: card.textContent, btn: btn.textContent, disabled: btn.hasAttribute('disabled'),
                    state: btn.getAttribute('data-cash-state'), posts: post(/sell_account/).length,
                    body: post(/sell_account/)[0].body};
            """
        )
        assert "Not sold: confirm_account_id is required" in result["text"]
        assert (
            result["btn"] == "Cash Now" and result["disabled"] is False and result["state"] is None
        )
        assert result["posts"] == 1
        assert json.loads(result["body"]) == {
            "symphony_id": json.loads(result["body"])["symphony_id"]
        }

    def test_a_refusal_for_the_token_says_not_sent_and_refreshes_once(self):
        result = run(
            CASH
            + """
            H.route('/api/sell_account', {fn: seq([{status: 403, text: H.bodies.refused.text}])});
            H.route('/api/csrf-token', {fn: seq([{body: {csrf_token: 'tok'}}, H.bodies.tokenB])});
            await click();
            await H.runTimers(60000);
            return {text: card.textContent, disabled: btn.hasAttribute('disabled'),
                    posts: post(/sell_account/).length, gets: tokenGets()};
            """
        )
        assert NOT_SENT.search(result["text"]) and "click" in result["text"].lower()
        assert result["disabled"] is False
        assert result["posts"] == 1, "the refused sale was sent again"
        assert result["gets"] == 2

    def test_a_network_failure_re_enables_the_button_and_says_something(self):
        result = run(
            CASH
            + """
            H.route('/api/sell_account', {reject: true});
            await click();
            return {disabled: btn.hasAttribute('disabled'), text: card.textContent, posts: post(/sell_account/).length};
            """
        )
        assert result["disabled"] is False and result["posts"] == 1
        assert re.search(r"not sold|not sent|failed|connection", result["text"], re.I)

    def test_an_accepted_sale_reads_in_cash(self):
        result = run(
            CASH
            + """
            H.route('/api/sell_account', {status: 200, body: {status: 'success'}});
            await click();
            return {btn: btn.textContent, state: btn.getAttribute('data-cash-state')};
            """
        )
        assert result == {"btn": "In cash", "state": "sold"}

    def test_the_request_is_the_one_it_always_was(self):
        result = run(
            CASH
            + """
            H.route('/api/sell_account', {status: 200, body: {status: 'success'}});
            await click();
            const p = post(/sell_account/)[0];
            return {url: p.url, headers: p.headers, body: p.body, symId};
            """
        )
        assert result["url"] == "/api/sell_account"
        assert result["headers"] == {"Content-Type": "application/json", "X-CSRF-Token": "tok"}
        assert json.loads(result["body"]) == {"symphony_id": result["symId"]}


class TestFleetDismiss:
    DISMISS = """
      H.sandbox._lastFleetAlert = {tripped_at_et: '2026-10-22T10:00:00-04:00'};
      const dismissBtn = newButton();
      const banner = H.sandbox.document.getElementById('fleet-correlation-banner');
      const notice = H.sandbox.document.getElementById('fleet-dismiss-notice');
      banner.removeAttribute('hidden');
      const dismiss = async () => { H.sandbox.dismissFleetAlert(dismissBtn); await H.settle(); };
    """

    def test_a_refusal_for_the_token_keeps_the_banner_and_says_click_again(self):
        result = run(
            self.DISMISS
            + """
            H.route('/api/fleet-alert/dismiss', {fn: seq([{status: 403, text: H.bodies.refused.text}])});
            H.route('/api/csrf-token', {fn: seq([{body: {csrf_token: 'tok'}}, H.bodies.tokenB])});
            await dismiss();
            await H.runTimers(60000);
            return {banner: banner.hasAttribute('hidden'), notice: notice.textContent,
                    noticeHidden: notice.hasAttribute('hidden'), disabled: dismissBtn.disabled,
                    posts: post(/fleet-alert\\/dismiss/).length, gets: tokenGets()};
            """
        )
        assert result["banner"] is False, "a refused dismissal hid the banner"
        assert result["notice"].strip() == "Not dismissed: click Dismiss again"
        assert result["noticeHidden"] is False and not result["disabled"]
        assert result["posts"] == 1 and result["gets"] == 2

    def test_a_503_keeps_the_dismissal_not_saved_notice(self):
        result = run(
            self.DISMISS
            + """
            H.route('/api/fleet-alert/dismiss', {status: 503, body: {status: 'error', message: 'Dismissal not saved'}});
            await dismiss();
            return {banner: banner.hasAttribute('hidden'), notice: notice.textContent, posts: post(/fleet-alert\\/dismiss/).length};
            """
        )
        assert result["banner"] is False
        assert result["notice"].strip() == "Dismissal not saved"
        assert result["posts"] == 1

    def test_an_accepted_dismissal_hides_the_banner(self):
        result = run(
            self.DISMISS
            + """
            H.route('/api/fleet-alert/dismiss', {status: 200, body: {status: 'ok'}});
            await dismiss();
            return {banner: banner.hasAttribute('hidden'), p: post(/fleet-alert\\/dismiss/)[0]};
            """
        )
        assert result["banner"] is True
        assert result["p"]["headers"] == {"X-CSRF-Token": "tok"} and result["p"]["body"] is None


class TestManagedSleeves:
    CALLS = {
        "arm": "H.sandbox.armSleeveRuleToPaper('s1', 'r1', btn)",
        "disarm": "H.sandbox.disarmSleeve('s1', btn)",
        "delete": "H.sandbox.deleteSleeve('s1', btn)",
    }

    @pytest.mark.parametrize("which", list(CALLS))
    def test_a_refusal_for_the_token_says_not_sent_does_not_reload_and_sends_once(self, which):
        result = run(
            f"""
            H.route(/\\/api\\/sleeves\\//, {{fn: seq([{{status: 403, text: H.bodies.refused.text}}])}});
            H.route('/api/csrf-token', {{fn: seq([{{body: {{csrf_token: 'tok'}}}}, H.bodies.tokenB])}});
            const btn = newButton();
            {self.CALLS[which]};
            await H.settle();
            await H.runTimers(60000);
            return {{shown: surfaced(), reloads: H.reloads, posts: post(/api\\/sleeves/).length, gets: tokenGets(),
                     disabled: btn.disabled}};
            """
        )
        assert NOT_SENT.search(result["shown"]) and "click" in result["shown"].lower()
        assert result["reloads"] == 0, "a refused action reloaded the page"
        assert result["posts"] == 1 and result["gets"] == 2
        assert not result["disabled"], "the control must be usable for the new click"


class TestRestartSeenOnTheStatePoll:
    def test_a_poll_with_a_new_start_stamp_fetches_one_new_token(self):
        result = run(
            """
            const first = Object.assign({}, H.bodies.state, {daemon_started_at: '2026-10-22T09:00:00Z'});
            const second = Object.assign({}, H.bodies.state, {daemon_started_at: '2026-10-22T10:43:30Z'});
            H.route('/api/state', {fn: seq([{body: first}, {body: second}])});
            H.route('/api/csrf-token', {fn: seq([{body: {csrf_token: 'tok'}}, H.bodies.tokenB])});
            await H.sandbox.loadState();
            await H.settle();
            const afterFirst = tokenGets();
            await H.sandbox.loadState();
            await H.settle();
            const afterSecond = tokenGets();
            await H.runTimers(10 * 60 * 1000, {skipBelow: 600000});
            return {afterFirst, afterSecond, token: H.sandbox.PSRequest.csrfToken(), posts: post(/./).length};
            """
        )
        assert result["afterSecond"] == result["afterFirst"] + 1, (
            "a restart must fetch one new token"
        )
        assert result["token"] == answers.TOKEN_B
        assert result["posts"] == 0
