"""The per-symphony sell control is gone from the dashboard; the account-wide route it could reach is not.

The control sent only a symphony id to ``POST /api/sell_account``, a route that liquidates the whole
account and refuses that body. Removing the control must leave no button, no handler, no per-poll
state and no style behind, while the header's Emergency Liquidate and the route stay as they were.

Real call path: the page is what ``GET /`` renders (``tests/dashboard/_index_js_harness.py``), the poll
logic is ``static/chrome.js`` and ``static/index.js`` run whole under node, and the route answers are the
real app's through its test client. Nothing here reads or writes the developer ``.env``: the route's
reader is patched to a dict set here, and a liquidation thread or a broker request fails the test.

Fixture provenance: the worlds are rendered by the real producers over the captured droplet records
(``tests/dashboard/_index_js_harness.py``); the old control's body is the literal it sent,
``{"symphony_id": <id>}``, with an id of the real Composer format taken from that world.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import app as app_module
from tests.app import _chrome_route_answers as answers
from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup

REPO = Path(__file__).resolve().parents[2]
WORLD = "review-fixes"
POLL_WORLD = "default"
SELL_ROUTE = "/api/sell_account"
CONFIRMATION_REFUSAL = "confirm_account_id is required"
PLACEHOLDER_ENV = {
    "COMPOSER_KEY_ID": "placeholder-key-id",
    "COMPOSER_SECRET": "placeholder-secret",
    "DISCORD_WEBHOOK_URL": "",
}
# The words and attribute names the control and its per-poll state went by, compared lower-cased.
REMOVED_TOKENS = ["cashnow", "cash-now", "cash_now", "cash now", "data-cash-state"]
SCANNED_SUFFIXES = {".js", ".css", ".html"}


def _rendered_page() -> str:
    return hx.get_world(WORLD)["html"]


def _cards(tree: dict) -> list[dict]:
    return hx.find_all(tree, lambda n: n["a"].get("data-testid") == "sym-card")


def _scanned_files() -> list[Path]:
    files = [
        p
        for folder in ("static", "templates")
        for p in (REPO / folder).rglob("*")
        if p.is_file() and p.suffix in SCANNED_SUFFIXES
    ]
    assert files, "premise: the scan found no script, stylesheet or template"
    return sorted(files)


class TestThePageRendersNoCashNowControl:
    def test_the_page_names_the_control_nowhere(self):
        page = _rendered_page().lower()
        found = [token for token in REMOVED_TOKENS if token in page]
        assert found == [], f"the rendered dashboard still carries: {found}"

    def test_no_card_holds_a_cash_now_button_or_note(self):
        tree = hx.parse_html(_rendered_page())
        hits = hx.find_all(
            tree,
            lambda n: n["a"].get("data-testid") in ("cash-now-btn", "cash-now-note"),
        )
        assert hits == []

    def test_the_card_actions_hold_the_settings_button_and_nothing_else(self):
        tree = hx.parse_html(_rendered_page())
        containers = hx.find_all(tree, lambda n: "card-actions" in n["a"].get("class", "").split())
        assert containers, "premise: the cards render an actions container"
        for container in containers:
            buttons = hx.find_all(container, lambda n: n["t"] == "button")
            assert [b["a"].get("data-testid") for b in buttons] == ["sym-settings-btn"]

    def test_every_active_and_standby_card_still_has_its_settings_button(self):
        tree = hx.parse_html(_rendered_page())
        cards = _cards(tree)
        standby = hx.find_all(tree, lambda n: n["a"].get("data-testid") == "standby-section")
        assert cards and standby, "premise: the world renders active and standby cards"
        assert hx.find_all(standby[0], lambda n: n["a"].get("data-testid") == "sym-card"), (
            "premise: a standby card is rendered"
        )
        for card in cards:
            settings = hx.find_all(card, lambda n: n["a"].get("data-testid") == "sym-settings-btn")
            assert len(settings) == 1, f"card {card['a'].get('data-sym-id')} lost its settings"


POLL_PRELUDE = r"""
const D = H.sandbox.document;
const poll = async function (body) {
  H.route('/api/state', { body: body });
  await H.tick(); await H.completeAnimations();
};
"""


def _body_with(symphony_id: str, **fields) -> dict:
    world = hx.get_world(POLL_WORLD)
    same = sup.with_stamp(world["state"], sup.render_stamp(world))
    return sup.with_entry(same, symphony_id, **fields)


class TestThePollNeitherReadsNorWritesCashNowState:
    def test_a_card_turning_triggered_and_back_leaves_a_stray_cash_now_element_untouched(self):
        # The page no longer renders the control (any still rendered is removed first, so the probe is
        # the only match): one is planted in a card, and any poll code still keyed on it would disable
        # it, re-label it or stamp state on it.
        target = hx.UNTRIGGERED_ID
        result = sup.run(
            POLL_PRELUDE
            + r"""
            await H.boot({ extras: true });
            D.querySelectorAll('[data-testid="cash-now-btn"]').forEach(function (b) {
              b.parentNode.removeChild(b);
            });
            const card = D.querySelector('.sym-card[data-sym-id="' + __ID__ + '"]');
            const probe = D.createElement('button');
            probe.setAttribute('data-testid', 'cash-now-btn');
            probe.textContent = 'probe';
            card.appendChild(probe);
            const read = function () {
              return { disabled: probe.hasAttribute('disabled'), text: probe.textContent,
                       state: probe.getAttribute('data-cash-state') };
            };
            const before = read();
            await poll(H.bodies.exited);
            const whileExited = read();
            await poll(H.bodies.fresh);
            return { before: before, whileExited: whileExited, after: read(),
                     handler: typeof H.sandbox.cashNow };
            """.replace("__ID__", repr(target)),
            world=POLL_WORLD,
            scripts=sup.CHROME_AND_INDEX,
            extra_bodies={
                "exited": _body_with(
                    target,
                    triggered=True,
                    armed=False,
                    status="triggered",
                    triggered_reason="Trailing Stop",
                ),
                "fresh": _body_with(target, triggered=False, armed=True, status="armed"),
            },
        )
        untouched = {"disabled": False, "text": "probe", "state": None}
        assert result["before"] == untouched
        assert result["whileExited"] == untouched
        assert result["after"] == untouched

    def test_no_click_handler_is_left_on_the_window(self):
        result = sup.run(
            "await H.boot({ extras: true }); return { handler: typeof H.sandbox.cashNow };",
            world=POLL_WORLD,
            scripts=sup.CHROME_AND_INDEX,
        )
        assert result["handler"] == "undefined"


class TestNoShippedFileKeepsTheControlOrItsPlumbing:
    @pytest.mark.parametrize("token", REMOVED_TOKENS)
    def test_no_script_stylesheet_or_template_carries_the_token(self, token):
        offenders = [
            str(p.relative_to(REPO)).replace("\\", "/")
            for p in _scanned_files()
            if token in p.read_text(encoding="utf-8", errors="replace").lower()
        ]
        assert offenders == [], f"{token!r} is still named in {offenders}"

    def test_the_dashboard_script_sends_no_request_to_the_sell_route(self):
        text = (REPO / "static" / "index.js").read_text(encoding="utf-8")
        assert SELL_ROUTE not in text

    def test_the_header_script_still_owns_the_sell_route_request(self):
        # Emergency Liquidate is the one control that reaches the route; it must stay where it is.
        text = (REPO / "static" / "chrome.js").read_text(encoding="utf-8")
        assert SELL_ROUTE in text


def _old_control_body() -> dict:
    return {"symphony_id": hx.UNTRIGGERED_ID}


class TestAPageLoadedBeforeTheChangeCannotLiquidateAnything:
    @pytest.mark.parametrize("live_execution", ["False", "True"])
    def test_the_old_controls_body_is_still_refused_and_nothing_is_liquidated(self, live_execution):
        # sell_answer fails the test if a liquidation thread starts.
        answer = answers.sell_answer(
            _old_control_body(), env=dict(PLACEHOLDER_ENV, LIVE_EXECUTION=live_execution)
        )
        assert answer["status"] == 400
        assert answers.message_of(answer) == CONFIRMATION_REFUSAL


@pytest.fixture
def signed_in_with_token(monkeypatch):
    """A client holding a real login session and the page's CSRF token, both hooks on."""
    monkeypatch.setenv("DASHBOARD_PASSWORD", "placeholder-password")
    monkeypatch.setenv("SECRET_KEY", "placeholder-secret-key")
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    monkeypatch.setattr(app_module, "_csrf_check_enabled", False)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        login = client.post("/login", data={"password": "placeholder-password"})
        assert login.status_code in (302, 303), "premise: the login answered a redirect"
        monkeypatch.setattr(app_module, "_csrf_check_enabled", True)
        token = client.get("/api/csrf-token").get_json()["csrf_token"]
        yield client, token


class TestAScriptedSymphonyOnlyRequestIsStillRefused:
    @pytest.mark.parametrize(
        "payload",
        [
            {"symphony_id": hx.UNTRIGGERED_ID},
            {"symphony_id": hx.TRIGGERED_TP_ID, "account_id": "placeholder-account"},
            {"symphony_id": hx.UNTRIGGERED_ID, "confirm_phrase": "LIQUIDATE"},
        ],
        ids=["symphony_only", "with_account_id", "with_phrase_but_no_account_confirmation"],
    )
    @pytest.mark.parametrize("live_execution", ["False", "True"])
    def test_the_route_answers_its_confirmation_refusal(
        self, signed_in_with_token, payload, live_execution
    ):
        client, token = signed_in_with_token
        env = dict(PLACEHOLDER_ENV, LIVE_EXECUTION=live_execution)
        with (
            patch.object(app_module, "dotenv_values", lambda *_a, **_k: dict(env)),
            patch.object(app_module.requests, "post") as broker,
            patch.object(app_module.threading, "Thread") as thread,
        ):
            refused = client.post(SELL_ROUTE, json=payload, headers={"X-CSRF-Token": token})
        assert refused.status_code != 404, "premise: the route exists"
        assert refused.status_code == 400
        assert refused.get_json() == {"status": "error", "message": CONFIRMATION_REFUSAL}
        assert not thread.called, "a liquidation thread was started"
        assert not broker.called, "a request left the process"

    def test_the_hooks_are_live_in_this_session_so_the_400_is_the_routes_own(
        self, signed_in_with_token
    ):
        client, _token = signed_in_with_token
        with patch.object(app_module.threading, "Thread") as thread:
            tokenless = client.post(SELL_ROUTE, json=_old_control_body())
        assert tokenless.status_code == 403
        assert not thread.called
