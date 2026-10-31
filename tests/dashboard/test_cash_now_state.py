"""Cash Now is disabled on a card that exits, enabled on one that does not, and never re-armed by a poll
after a sell was sent.

Real call path: ``static/chrome.js`` and ``static/index.js`` run whole under node
(tests/dashboard/_index_js_harness.py) over the page ``GET /`` renders. A press goes through the card's
own button the way a user's click does (nothing on a disabled button, then the markup's inline handler:
``H.press``); the 30 s poll is the page's own interval. Nothing here clicks a live sell: ``/api/sell_account``
is a harness route.

The request is judged against the literal the page has always sent (URL, method, headers, body), because
this suite may not change a trade or liquidation request. Derived bodies change one named field of a real
body: tests/fixtures/refresh_timing/03-c6/PROVENANCE.md.
"""

from __future__ import annotations

import pytest

from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup

WORLD = "default"
UNTRIGGERED = hx.UNTRIGGERED_ID
TRIGGERED = hx.TRIGGERED_TP_ID
SELL_URL = "/api/sell_account"
CSRF_TOKEN = "tok"  # what the harness's /api/csrf-token route serves

PRELUDE = r"""
const D = H.sandbox.document;
const SEL = function (id) { return '.sym-card[data-sym-id="' + id + '"] [data-testid="cash-now-btn"]'; };
const btn = function (id) {
  const e = D.querySelector(SEL(id));
  return e ? { disabled: e.hasAttribute('disabled'), text: e.textContent.trim() } : null;
};
const sells = function () { return H.fetchLog.filter(function (e) { return e.url === '/api/sell_account'; }); };
const poll = async function (body) {
  H.route('/api/state', { body: body });
  await H.tick(); await H.completeAnimations();
};
"""


def _world() -> dict:
    return hx.get_world(WORLD)


def _same() -> dict:
    return sup.with_stamp(_world()["state"], sup.render_stamp(_world()))


def _triggered_body(symphony_id: str) -> dict:
    return sup.with_entry(
        _same(),
        symphony_id,
        triggered=True,
        armed=False,
        status="triggered",
        triggered_reason="Trailing Stop",
    )


def _untriggered_body(symphony_id: str) -> dict:
    return sup.with_entry(_same(), symphony_id, triggered=False, armed=True, status="armed")


def _run(scenario: str, **bodies) -> dict:
    return sup.run(
        PRELUDE + scenario, world=WORLD, scripts=sup.CHROME_AND_INDEX, extra_bodies=bodies
    )


class TestTheButtonFollowsWhetherTheCardHasExited:
    def test_a_card_that_exits_while_the_page_is_open_has_its_button_disabled(self):
        r = _run(
            r"""
        await H.boot({ extras: true });
        const before = btn(__ID__);
        await poll(H.bodies.exited);
        return { before: before, after: btn(__ID__) };
        """.replace("__ID__", repr(UNTRIGGERED)),
            exited=_triggered_body(UNTRIGGERED),
        )
        assert r["before"] == {"disabled": False, "text": "Cash Now"}
        assert r["after"]["disabled"] is True

    def test_a_card_that_stops_reading_triggered_on_the_next_sessions_poll_is_enabled_again(self):
        r = _run(
            r"""
        await H.boot({ extras: true });
        const before = btn(__ID__);
        await poll(H.bodies.fresh);
        return { before: before, after: btn(__ID__) };
        """.replace("__ID__", repr(TRIGGERED)),
            fresh=_untriggered_body(TRIGGERED),
        )
        assert r["before"]["disabled"] is True, (
            "the render must have shown the exited card disabled"
        )
        assert r["after"] == {"disabled": False, "text": "Cash Now"}

    def test_a_poll_that_changes_nothing_leaves_every_button_as_it_was(self):
        r = _run(
            r"""
        await H.boot({ extras: true });
        const ids = [__A__, __B__, __C__];
        const before = ids.map(btn);
        await poll(H.bodies.same); await poll(H.bodies.same);
        return { before: before, after: ids.map(btn) };
        """.replace("__A__", repr(UNTRIGGERED))
            .replace("__B__", repr(TRIGGERED))
            .replace("__C__", repr(hx.TRIGGERED_VWAP_ID)),
            same=_same(),
        )
        assert r["after"] == r["before"]

    def test_a_failed_poll_leaves_the_buttons_alone(self):
        r = _run(
            r"""
        await H.boot({ extras: true });
        const before = btn(__ID__);
        H.route('/api/state', { reject: true });
        await H.tick(); await H.completeAnimations();
        return { before: before, after: btn(__ID__) };
        """.replace("__ID__", repr(UNTRIGGERED)),
        )
        assert r["after"] == r["before"]


class TestARequestInFlightOrSentKeepsTheButtonShut:
    def test_a_poll_that_lands_while_the_sell_is_pending_leaves_selling_disabled(self):
        r = _run(
            r"""
        await H.boot({ extras: true });
        H.route('/api/sell_account', { pending: true });
        await H.press(SEL(__ID__));
        const sent = btn(__ID__);
        await poll(H.bodies.untriggered);
        const during = btn(__ID__);
        await poll(H.bodies.exited);
        return { sent: sent, during: during, stillDuring: btn(__ID__), requests: sells().length };
        """.replace("__ID__", repr(UNTRIGGERED)),
            untriggered=_untriggered_body(UNTRIGGERED),
            exited=_triggered_body(UNTRIGGERED),
        )
        assert r["sent"] == {"disabled": True, "text": "Selling…"}
        assert r["during"] == {"disabled": True, "text": "Selling…"}
        assert r["stillDuring"] == {"disabled": True, "text": "Selling…"}
        assert r["requests"] == 1

    def test_a_refused_sell_gives_the_button_back_and_a_later_poll_keeps_it_enabled(self):
        r = _run(
            r"""
        await H.boot({ extras: true });
        H.route('/api/sell_account', { pending: true });
        await H.press(SEL(__ID__));
        await H.releasePending('/api/sell_account', { status: 400, body: { message: 'refused' } });
        const refused = btn(__ID__);
        await poll(H.bodies.untriggered);
        return { refused: refused, after: btn(__ID__) };
        """.replace("__ID__", repr(UNTRIGGERED)),
            untriggered=_untriggered_body(UNTRIGGERED),
        )
        assert r["refused"] == {"disabled": False, "text": "Cash Now"}
        assert r["after"] == {"disabled": False, "text": "Cash Now"}

    def test_after_a_successful_sell_no_poll_makes_a_second_sell_one_click_away(self):
        r = _run(
            r"""
        await H.boot({ extras: true });
        H.route('/api/sell_account', { status: 200, body: { status: 'success' } });
        await H.press(SEL(__ID__));
        const sold = btn(__ID__);
        await poll(H.bodies.untriggered);
        const afterUntriggered = btn(__ID__);
        await poll(H.bodies.exited);
        await poll(H.bodies.untriggered);
        const pressed = await H.press(SEL(__ID__));
        return { sold: sold, afterUntriggered: afterUntriggered, last: btn(__ID__), pressed: pressed,
                 requests: sells().length };
        """.replace("__ID__", repr(UNTRIGGERED)),
            untriggered=_untriggered_body(UNTRIGGERED),
            exited=_triggered_body(UNTRIGGERED),
        )
        assert r["sold"] == {"disabled": True, "text": "In cash"}
        assert r["afterUntriggered"] == {"disabled": True, "text": "In cash"}
        assert r["last"] == {"disabled": True, "text": "In cash"}
        assert r["pressed"] is False and r["requests"] == 1, "a second sell was one press away"

    def test_a_double_press_sends_one_request(self):
        r = _run(
            r"""
        await H.boot({ extras: true });
        H.route('/api/sell_account', { pending: true });
        await H.press(SEL(__ID__));
        await H.press(SEL(__ID__));
        return { requests: sells().length };
        """.replace("__ID__", repr(UNTRIGGERED)),
        )
        assert r["requests"] == 1

    def test_a_declined_confirmation_sends_nothing_and_leaves_the_button_enabled(self):
        r = _run(
            r"""
        await H.boot({ extras: true });
        H.sandbox.confirm = function () { return false; };
        await H.press(SEL(__ID__));
        await poll(H.bodies.untriggered);
        return { requests: sells().length, after: btn(__ID__) };
        """.replace("__ID__", repr(UNTRIGGERED)),
            untriggered=_untriggered_body(UNTRIGGERED),
        )
        assert r["requests"] == 0
        assert r["after"] == {"disabled": False, "text": "Cash Now"}

    def test_one_cards_sell_does_not_shut_another_cards_button(self):
        r = _run(
            r"""
        await H.boot({ extras: true });
        H.route('/api/sell_account', { status: 200, body: {} });
        await H.press(SEL(__A__));
        const other = btn(__B__);
        await poll(H.bodies.both);
        return { other: other, after: btn(__B__), shut: btn(__A__) };
        """.replace("__A__", repr(UNTRIGGERED)).replace("__B__", repr(TRIGGERED)),
            both=sup.with_entry(
                _untriggered_body(TRIGGERED),
                UNTRIGGERED,
                triggered=False,
                armed=True,
                status="armed",
            ),
        )
        assert r["shut"] == {"disabled": True, "text": "In cash"}
        assert r["after"] == {"disabled": False, "text": "Cash Now"}, (
            "the second card, no longer triggered, must be enabled whatever the first one sold"
        )


class TestTheRequestIsTheOneThePageAlwaysSent:
    def test_url_method_headers_and_body_are_unchanged(self):
        r = _run(
            r"""
        await H.boot({ extras: true });
        H.route('/api/sell_account', { status: 200, body: {} });
        await H.press(SEL(__ID__));
        const e = sells()[0];
        return { n: sells().length, url: e.url, method: e.method, headers: e.headers, body: e.body };
        """.replace("__ID__", repr(UNTRIGGERED)),
        )
        assert r["n"] == 1
        assert r["url"] == SELL_URL and r["method"] == "POST"
        assert r["headers"] == {"Content-Type": "application/json", "X-CSRF-Token": CSRF_TOKEN}
        assert r["body"] == '{"symphony_id":"%s"}' % UNTRIGGERED, (
            "the request body is the symphony id alone; the confirmation fields are another change"
        )

    @pytest.mark.parametrize("outcome", ["pending", "refused", "sold"])
    def test_no_poll_ever_sends_a_sell_by_itself(self, outcome):
        spec = {
            "pending": "{ pending: true }",
            "refused": "{ status: 400, body: {} }",
            "sold": "{ status: 200, body: {} }",
        }[outcome]
        r = _run(
            r"""
        await H.boot({ extras: true });
        H.route('/api/sell_account', __SPEC__);
        await H.press(SEL(__ID__));
        const n = sells().length;
        await poll(H.bodies.untriggered); await poll(H.bodies.exited); await poll(H.bodies.untriggered);
        return { before: n, after: sells().length };
        """.replace("__SPEC__", spec).replace("__ID__", repr(UNTRIGGERED)),
            untriggered=_untriggered_body(UNTRIGGERED),
            exited=_triggered_body(UNTRIGGERED),
        )
        assert r["before"] == r["after"] == 1
