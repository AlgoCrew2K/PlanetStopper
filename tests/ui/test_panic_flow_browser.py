"""Emergency Liquidate in a real browser: it opens and cancels as before on every page and width,
and a refused request is answered with the server's real words.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_panic_flow_browser.py``). Firefox headless
through Playwright, imported inside a fixture; a missing Playwright or Firefox fails with a
message naming it, never a skip.

MONEY-SENSITIVE. The route behind Confirm can liquidate real positions in live mode. This module
never reaches that path:

* the route's own ``.env`` reader (``app.dotenv_values``) is replaced by a dict holding NO
  Composer credentials and ``LIVE_EXECUTION=False``, so a request that passes the four
  confirmation checks stops at the ``Missing credentials or account ID.`` 400, before any Discord
  call, log line or liquidation branch (the launcher's second mode has the same property);
* ``app.perform_account_liquidation`` is replaced by a function that fails the test if called;
* every request to anything but the in-process server is refused, and the developer ``.env`` is
  never read (``app.ENV_FILE_PATH`` is a temporary file holding only the account ids below).

The accepted (200) answer is therefore not reachable here and is covered by the node harness over
the route's real body (``tests/app/test_chrome_header_honesty.py``). The request the page sends is
captured by ``page.route`` and fulfilled in the browser where the module only needs to read it;
nothing is written anywhere.

The refusal rows run with the auth and CSRF hooks on and sign in through the page's own form, as
the launcher's second mode does: the CSRF 403 is provoked with a stale page token (the token route
answers a wrong value once, to the page's token holder), the 401 by
dropping the session cookie, the empty-account 400 with no account configured.
"""

from __future__ import annotations

import json
import re

import pytest

import app as app_module
from tests.app import _chrome_route_answers as answers
from tests.ui._shell_support import (  # noqa: F401
    NARROW,
    PAGE_IDS,
    PAGES,
    REPO,
    WIDE,
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    assert_reachable,
    fail,
    firefox,
    open_page,
    open_probe,
    seed_dashboard_state,
    set_account_in_env,
    wait_until,
)

pytestmark = pytest.mark.browser

BASE = json.loads(
    (REPO / "tests" / "fixtures" / "chrome" / "panic_request_at_base.json").read_text(
        encoding="utf-8"
    )
)
ACCOUNT = BASE["inputs"]["account_id"]
OTHER_ACCOUNT = "0b8d7c1e-35a4-4f0e-9d7a-6c1f2e8b4a90"
PASSWORD = "shell-panic-password-71d3"
NOTHING_LIQUIDATED = "nothing was liquidated"
MODAL_OPEN = "#panic-modal:not([hidden])"
MODAL_CLOSED = "#panic-modal[hidden]"

_BLANK_CREDENTIALS = {
    "LIVE_EXECUTION": "False",
    "COMPOSER_KEY_ID": "",
    "COMPOSER_SECRET": "",
    "DISCORD_WEBHOOK_URL": "",
}


@pytest.fixture
def inert_liquidation(monkeypatch):
    """The route reads blank credentials, and the liquidation thread body fails if it runs."""
    started: list[tuple] = []

    def must_not_run(*args, **kwargs):
        started.append(args)
        raise AssertionError("a liquidation was started from a browser test")

    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: dict(_BLANK_CREDENTIALS))
    monkeypatch.setattr(app_module, "perform_account_liquidation", must_not_run)
    yield started
    assert started == [], "a liquidation was started"


@pytest.fixture
def panic_server(advisor_server, inert_liquidation):  # noqa: F811
    """The server with two accounts configured (the account select lists them)."""
    set_account_in_env(app_module.ENV_FILE_PATH, [ACCOUNT, OTHER_ACCOUNT])
    return advisor_server


@pytest.fixture
def secure_server(advisor_server, inert_liquidation, monkeypatch):  # noqa: F811
    """Auth and CSRF hooks on, a launcher-only password, one account configured."""
    set_account_in_env(app_module.ENV_FILE_PATH, [ACCOUNT])
    monkeypatch.setenv("DASHBOARD_PASSWORD", PASSWORD)
    monkeypatch.setenv("SECRET_KEY", "shell-panic-secret-key-5b0e")
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    monkeypatch.setattr(app_module, "_csrf_check_enabled", True)
    app_module._AUTH_FAILED_ATTEMPTS.clear()
    yield advisor_server
    app_module._AUTH_FAILED_ATTEMPTS.clear()


def _sign_in(probe) -> None:
    probe.page.goto(probe.server.base_url + "/login")
    probe.page.fill("#password", PASSWORD)
    probe.page.click('button[type="submit"]')
    probe.page.wait_for_url(probe.server.base_url + "/")


def _open_modal(probe) -> None:
    probe.page.click("#emergency-liquidate")
    probe.page.wait_for_selector(MODAL_OPEN)


def _choose_first_account_and_type_phrase(probe, phrase: str = "LIQUIDATE") -> None:
    wait_until(
        probe,
        "() => document.querySelectorAll('#panic-account option').length > 0"
        " && !/Loading/.test(document.querySelector('#panic-account').textContent)",
        "the account list never loaded",
    )
    probe.page.fill("#panic-phrase", phrase)


def _wait_for_alert(probe, count: int = 1) -> str:
    for _ in range(100):
        if len(probe.dialogs) >= count:
            return probe.dialogs[count - 1]
        probe.page.wait_for_timeout(50)
    fail(probe, f"no alert number {count} was shown (shown: {probe.dialogs})")
    return ""


def _capture_sell_requests(probe, answer: dict | None = None) -> list:
    """Record every request to the liquidation route; answer it in the browser, or abort it.

    ``answer`` is a real answer of the app (``tests/app/_chrome_route_answers``); without one the
    request fails before any answer, as a dropped network does. Either way it never reaches the
    server.
    """
    seen: list = []

    def handler(route):
        seen.append(route.request)
        if answer is None:
            route.abort()
        else:
            route.fulfill(
                status=answer["status"],
                content_type=answer["content_type"],
                body=answer["body"],
            )

    probe.page.route("**/api/sell_account", handler)
    return seen


# ---------------------------------------------------------------------------
# the modal opens and cancels exactly as before, on every page and width
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width, height", [WIDE, NARROW], ids=["1440", "375"])
@pytest.mark.parametrize("path", PAGES, ids=PAGE_IDS)
def test_emergency_liquidate_opens_the_modal_with_the_accounts_and_confirm_disabled(
    firefox, panic_server, path, width, height
):
    seed_dashboard_state()
    with open_probe(firefox, panic_server, width=width, height=height) as probe:
        open_page(probe, path)
        sent = _capture_sell_requests(probe)
        _open_modal(probe)
        wait_until(
            probe,
            "() => document.querySelectorAll('#panic-account option').length === 2",
            "the account list never listed both configured accounts",
        )
        values = probe.page.eval_on_selector_all(
            "#panic-account option", "(opts) => opts.map((o) => o.value)"
        )
        assert values == [ACCOUNT, OTHER_ACCOUNT]
        assert probe.page.is_disabled("#panic-confirm-btn")
        assert_reachable(probe, "Confirm Liquidate", "#panic-confirm-btn", f"{path} at {width}")
        assert_reachable(
            probe,
            "Cancel",
            '#panic-modal button[onclick="closePanicModal()"]',
            f"{path} at {width}",
        )
        assert sent == [], "opening the modal must send nothing"


def test_only_the_exact_phrase_enables_confirm(firefox, panic_server):
    """lower case keeps it disabled; the exact phrase enables it; clearing disables."""
    with open_probe(firefox, panic_server, width=NARROW[0], height=NARROW[1]) as probe:
        open_page(probe, "/history")
        _open_modal(probe)
        for typed, disabled in [
            ("liquidate", True),
            ("Liquidate", True),
            ("LIQUIDATE ", True),
            ("LIQUIDATE", False),
            ("", True),
        ]:
            probe.page.fill("#panic-phrase", typed)
            assert probe.page.is_disabled("#panic-confirm-btn") is disabled, typed


@pytest.mark.parametrize("width, height", [WIDE, NARROW], ids=["1440", "375"])
def test_cancel_closes_the_modal_and_sends_nothing(firefox, panic_server, width, height):
    with open_probe(firefox, panic_server, width=width, height=height) as probe:
        open_page(probe, "/history")
        sent = _capture_sell_requests(probe)
        _open_modal(probe)
        _choose_first_account_and_type_phrase(probe)
        probe.page.click('#panic-modal button[onclick="closePanicModal()"]')
        probe.page.wait_for_selector(MODAL_CLOSED, state="attached")
        probe.page.wait_for_timeout(300)
        assert sent == []
        assert probe.dialogs == []


def test_confirm_sends_one_request_with_the_recorded_body_and_headers(firefox, panic_server):
    """the browser's request equals the base record's, for this account."""
    # Taken before the page loads: building the answer swaps threading.Thread for a moment, and
    # the in-process server starts a thread per request.
    accepted = answers.sell_answer(answers.sell_payload(ACCOUNT))
    with open_probe(firefox, panic_server, width=NARROW[0], height=NARROW[1]) as probe:
        open_page(probe, "/history")
        token = probe.page.evaluate(
            "() => fetch('/api/csrf-token').then((r) => r.json()).then((b) => b.csrf_token)"
        )
        sent = _capture_sell_requests(probe, accepted)
        _open_modal(probe)
        _choose_first_account_and_type_phrase(probe)
        probe.page.click("#panic-confirm-btn")
        assert _wait_for_alert(probe) == answers.message_of(accepted)
        assert len(sent) == 1
        request = sent[0]
        assert request.method == "POST"
        assert request.url == panic_server.base_url + BASE["single_confirm_request"]["url"]
        assert request.post_data == BASE["single_confirm_request"]["body"]
        headers = {k.lower(): v for k, v in request.headers.items()}
        assert headers["content-type"].startswith("application/json")
        assert headers["x-csrf-token"] == token


# ---------------------------------------------------------------------------
# what the operator is shown when the request is refused or cannot be sent
# ---------------------------------------------------------------------------


def _confirm_and_read_alert(probe, phrase: str = "LIQUIDATE") -> str:
    _open_modal(probe)
    _choose_first_account_and_type_phrase(probe, phrase)
    probe.page.click("#panic-confirm-btn")
    message = _wait_for_alert(probe)
    probe.page.wait_for_selector(MODAL_CLOSED, state="attached")
    return message


def test_with_no_account_configured_the_empty_account_400_is_shown_verbatim(firefox, secure_server):
    """Live half: the empty account option sends an empty id; the route says so."""
    set_account_in_env(app_module.ENV_FILE_PATH, [])
    with open_probe(firefox, secure_server, width=NARROW[0], height=NARROW[1]) as probe:
        _sign_in(probe)
        open_page(probe, "/history")
        message = _confirm_and_read_alert(probe)
        assert message == "confirm_account_id is required"
        assert probe.page_errors == [], probe.page_errors


def test_with_an_account_chosen_the_missing_credentials_400_is_shown_verbatim(
    firefox, secure_server
):
    """Live half: the four confirmation checks pass and the credential check refuses."""
    with open_probe(firefox, secure_server, width=NARROW[0], height=NARROW[1]) as probe:
        _sign_in(probe)
        open_page(probe, "/history")
        message = _confirm_and_read_alert(probe)
        assert message == "Missing credentials or account ID."
        assert probe.page_errors == [], probe.page_errors


def _make_the_page_token_stale(probe) -> None:
    """The token the page holds (in request_guard.js, out of reach of page scripts) becomes one the
    server has never issued: the token route answers a wrong value to one refresh, then answers the
    real one again, as it does after a daemon restart."""

    def stale(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"csrf_token": "stale-token-from-before-a-restart"}),
        )

    probe.page.route("**/api/csrf-token", stale)
    arrived = probe.page.evaluate("() => window.PSRequest.refreshCsrf()")
    probe.page.unroute("**/api/csrf-token", stale)
    assert arrived is True, "the stale token never reached the page's token holder"


def test_a_blank_page_token_is_answered_with_the_session_check_statement(firefox, secure_server):
    """Live half: the CSRF hook's 403 HTML; the operator is told, nothing is raised."""
    with open_probe(firefox, secure_server, width=NARROW[0], height=NARROW[1]) as probe:
        _sign_in(probe)
        open_page(probe, "/history")
        _make_the_page_token_stale(probe)
        message = _confirm_and_read_alert(probe)
        lowered = message.lower()
        assert "session" in lowered and "refused" in lowered
        assert NOTHING_LIQUIDATED in lowered
        assert "<" not in message
        assert probe.page_errors == [], probe.page_errors


def test_a_dropped_session_is_answered_with_the_401_status_and_never_the_dry_run_text(
    firefox, secure_server
):
    """Live half: the auth hook's JSON has no `message`."""
    with open_probe(firefox, secure_server, width=NARROW[0], height=NARROW[1]) as probe:
        _sign_in(probe)
        open_page(probe, "/history")
        _open_modal(probe)
        _choose_first_account_and_type_phrase(probe)
        probe.context.clear_cookies()
        probe.page.click("#panic-confirm-btn")
        message = _wait_for_alert(probe)
        assert "401" in message
        assert NOTHING_LIQUIDATED in message.lower()
        assert "dry-run" not in message.lower()
        probe.page.wait_for_selector(MODAL_CLOSED, state="attached")


def test_a_network_failure_says_the_request_could_not_be_sent(firefox, panic_server):
    """Live half."""
    with open_probe(firefox, panic_server, width=NARROW[0], height=NARROW[1]) as probe:
        open_page(probe, "/history")
        _capture_sell_requests(probe)  # aborts the request in the browser
        message = _confirm_and_read_alert(probe)
        lowered = message.lower()
        assert "could not be sent" in lowered
        assert NOTHING_LIQUIDATED in lowered
        assert probe.page_errors == [], probe.page_errors


def test_after_a_refusal_the_modal_reopens_and_one_new_request_is_sent(firefox, secure_server):
    """Live half: the second answer is its own."""
    with open_probe(firefox, secure_server, width=NARROW[0], height=NARROW[1]) as probe:
        _sign_in(probe)
        open_page(probe, "/history")
        first = _confirm_and_read_alert(probe)
        assert first == "Missing credentials or account ID."
        _make_the_page_token_stale(probe)
        probe.dialogs.clear()
        second = _confirm_and_read_alert(probe)
        assert "session" in second.lower() and NOTHING_LIQUIDATED in second.lower()
        assert second != first


def test_the_page_never_claims_a_liquidation_for_any_refusal(firefox, secure_server):
    with open_probe(firefox, secure_server, width=NARROW[0], height=NARROW[1]) as probe:
        _sign_in(probe)
        open_page(probe, "/history")
        message = _confirm_and_read_alert(probe)
        assert re.search(r"liquidation initiated", message, re.IGNORECASE) is None
