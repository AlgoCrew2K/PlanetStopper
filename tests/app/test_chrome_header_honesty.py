"""The header's Force run now and Emergency Liquidate tell the operator the server's real answer.

Subject: ``static/chrome.js`` ``forceRun`` and ``submitPanicLiquidation``, and the markup they
drive in ``templates/_chrome.html``. The whole shipped script runs under ``node`` against a stub
document (``tests/app/_chrome_node_harness.py``); every answer the script is given is the answer
the real Flask app gives to the same request, taken at test time
(``tests/app/_chrome_route_answers.py``), never a typed body.

The money-sensitive half: Emergency Liquidate reaches ``POST /api/sell_account``, which can
liquidate real positions in live mode. What must NOT change is the request (URL, method, headers,
body string and key order), the confirmation steps (account choice, the exact LIQUIDATE phrase),
the modal's open and close behaviour and the route's checks. The request is compared with the
record produced by running this same harness over ``static/chrome.js`` before any edit
(``tests/fixtures/chrome/panic_request_at_base.json``, script ``capture_panic_request.py``).
What changes is only what the operator is shown when a request is refused or cannot be sent.

Texts the operator reads are asserted by the facts the plan names (the route's own 400 message
verbatim; for a 403 that the session check refused the request; for 401, 503 and 500 the HTTP
status; always that nothing was liquidated), not by an exact sentence, so the implementer may
word them.

Protected surfaces are pinned by content hash of the file bytes with CRLF normalized to LF (the
pattern of ``tests/test_mdd_window_truth_zero_diff_guard.py``). A failing pin means a protected
surface changed: STOP and ask before re-pinning.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import pathlib
import re
from unittest.mock import patch

import pytest

import app as app_module
from tests.app import _chrome_node_harness as harness
from tests.app import _chrome_route_answers as answers

REPO = pathlib.Path(__file__).resolve().parents[2]
BASE = json.loads(
    (REPO / "tests" / "fixtures" / "chrome" / "panic_request_at_base.json").read_text(
        encoding="utf-8"
    )
)
BASE_ACCOUNT = BASE["inputs"]["account_id"]
OTHER_ACCOUNT = "0b8d7c1e-35a4-4f0e-9d7a-6c1f2e8b4a90"

NOTHING_LIQUIDATED = "nothing was liquidated"
FORCE_LABEL = "Force run now"
CONFIRM_PHRASE = "LIQUIDATE"


def _sha256_lf(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def _chrome_text() -> str:
    raw = (REPO / "templates" / "_chrome.html").read_bytes().decode("utf-8")
    return raw.replace("\r\n", "\n")


def _file_sha256_lf(relative: str) -> str:
    return _sha256_lf((REPO / relative).read_bytes().decode("utf-8"))


@pytest.fixture(scope="module")
def gate() -> str:
    return answers.chrome_oninput_gate()


@pytest.fixture(scope="module")
def csrf_answer() -> dict:
    return answers.csrf_token_answer()


@pytest.fixture(scope="module")
def accounts_answer() -> dict:
    return answers.accounts_answer([BASE_ACCOUNT, OTHER_ACCOUNT])


def _panic_run(
    gate,
    csrf_answer,
    accounts_answer,
    sell,
    *,
    steps=None,
    accounts=None,
):
    """Open the modal, choose the first account, type the phrase, click Confirm; return the output.

    ``sell`` is the list of scripted answers for ``/api/sell_account`` (a scripted rejection is
    ``{"reject": ...}``). ``steps`` replaces the default sequence.
    """
    default = [
        {"op": "dom_ready"},
        {"op": "open_panic"},
        {"op": "type_phrase", "value": CONFIRM_PHRASE},
        {"op": "confirm"},
        {"op": "flush"},
    ]
    return harness.run(
        steps if steps is not None else default,
        responses={
            "/api/csrf-token": [csrf_answer],
            "/api/accounts": [accounts if accounts is not None else accounts_answer],
            "/api/sell_account": sell,
        },
        oninput=gate,
    )


def _final(out: dict) -> dict:
    return out["snapshots"][-1]


def _sell_calls(out: dict) -> list[dict]:
    return harness.calls_to(out, "/api/sell_account")


def _expected_base_request(live_token: str) -> dict:
    """The recorded request with the recorded token replaced by this run's real token."""
    rec = BASE["single_confirm_request"]
    token = BASE["inputs"]["csrf_token"]
    return {
        "url": rec["url"],
        "method": rec["method"],
        "headers": {k: v.replace(token, live_token) for k, v in rec["headers"].items()},
        "body": rec["body"],
    }


def _request_of(call: dict) -> dict:
    init = call["init"]
    return {
        "url": call["url"],
        "method": init["method"],
        "headers": init["headers"],
        "body": init["body"],
    }


# ---------------------------------------------------------------------------
# The inputs the answers are built from are sound
# ---------------------------------------------------------------------------


class TestInputsCanFail:
    def test_the_base_record_holds_a_post_with_the_confirmation_fields_in_a_fixed_order(self):
        rec = BASE["single_confirm_request"]
        assert rec["method"] == "POST" and rec["url"] == "/api/sell_account"
        assert list(json.loads(rec["body"])) == [
            "account_id",
            "confirm_account_id",
            "confirm_phrase",
        ]
        assert BASE["two_confirm_clicks_request_count"] >= 1

    def test_the_scripted_answers_are_the_real_apps(self, csrf_answer):
        assert json.loads(csrf_answer["body"])["csrf_token"] == app_module._CSRF_TOKEN

    def test_the_harness_sends_the_request_the_record_holds_for_the_same_inputs(
        self, gate, csrf_answer, accounts_answer
    ):
        accepted = answers.sell_answer(answers.sell_payload(BASE_ACCOUNT))
        out = _panic_run(gate, csrf_answer, accounts_answer, [accepted])
        calls = _sell_calls(out)
        assert len(calls) == 1, "premise: one Confirm click sends one request"
        assert _request_of(calls[0]) == _expected_base_request(app_module._CSRF_TOKEN)


# ---------------------------------------------------------------------------
# Force run now reports the route's answer
# ---------------------------------------------------------------------------


def _force_run(trigger: dict, *, steps=None):
    return harness.run(
        steps
        or [
            {"op": "dom_ready"},
            {"op": "click_force"},
            {"op": "flush"},
        ],
        responses={"/api/csrf-token": [answers.csrf_token_answer()], "/api/trigger": [trigger]},
        oninput="",
    )


class TestForceRunReportsTheRoutesAnswer:
    def test_the_button_shows_the_routes_message_not_triggered(self):
        """the route is disabled by design; the button says so."""
        trigger = answers.trigger_answer()
        message = answers.message_of(trigger)
        out = _force_run(trigger)
        shown = out["snapshots"][-1]["force_text"]
        assert shown == message
        assert shown != "Triggered!"
        assert "scheduler" in shown.lower(), "the route's message says runs come from the scheduler"

    def test_the_message_stays_a_few_seconds_then_the_label_returns(self):
        """for a few seconds, then the button returns to its label and works again."""
        trigger = answers.trigger_answer()
        out = _force_run(
            trigger,
            steps=[
                {"op": "dom_ready"},
                {"op": "click_force"},
                {"op": "flush"},
                {"op": "timers"},
            ],
        )
        shown, restored = out["snapshots"][-2], out["snapshots"][-1]
        assert shown["force_text"] == answers.message_of(trigger)
        assert restored["force_text"] == FORCE_LABEL
        assert restored["force_disabled"] is False
        assert out["ran_timer_delays"], "the label must come back on a timer"
        assert all(1000 <= ms <= 10000 for ms in out["ran_timer_delays"]), out["ran_timer_delays"]

    def test_a_403_html_refusal_says_it_was_not_sent_and_to_click_again(self):
        """no CSRF token: the real 403 HTML page. The request is not sent again; the next click is."""
        refused = answers.trigger_answer_csrf_refused()
        assert refused["status"] == 403 and "json" not in refused["content_type"], "premise"
        out = _force_run(refused)
        assert out["snapshots"][-1]["force_text"] == "Not sent: click again"
        assert len(harness.calls_to(out, "/api/trigger")) == 1, (
            "the refused request was sent a second time"
        )

    def test_a_failed_network_shows_error(self):
        out = _force_run({"reject": "NetworkError when attempting to fetch resource."})
        assert out["snapshots"][-1]["force_text"] == "Error"

    def test_an_unauthenticated_json_refusal_shows_error(self):
        """The auth hook's 401 JSON carries `error`, no `message`: still an error, never success."""
        refused = answers.trigger_answer_unauthenticated()
        assert refused["status"] == 401, "premise"
        out = _force_run(refused)
        assert out["snapshots"][-1]["force_text"] == "Error"

    def test_a_2xx_answer_that_is_not_json_shows_error(self):
        trigger = {"status": 200, "content_type": "text/html", "body": "<html>proxy page</html>"}
        out = _force_run(trigger)
        assert out["snapshots"][-1]["force_text"] == "Error"

    def test_a_2xx_json_answer_with_no_message_keeps_the_existing_text(self):
        """Derived at test time from the real body by deleting `message` (PROVENANCE: derived)."""
        real = answers.trigger_answer()
        body = json.loads(real["body"])
        del body["message"]
        out = _force_run(dict(real, body=json.dumps(body)))
        assert out["snapshots"][-1]["force_text"] == "Triggered!"

    def test_two_clicks_send_one_request_while_the_button_is_disabled(self):
        out = _force_run(
            answers.trigger_answer(),
            steps=[
                {"op": "dom_ready"},
                {"op": "click_force"},
                {"op": "click_force"},
                {"op": "flush"},
            ],
        )
        assert len(harness.calls_to(out, "/api/trigger")) == 1

    def test_the_request_is_a_post_with_the_page_token(self):
        out = _force_run(answers.trigger_answer())
        (call,) = harness.calls_to(out, "/api/trigger")
        assert call["init"]["method"] == "POST"
        assert call["init"]["headers"] == {"X-CSRF-Token": app_module._CSRF_TOKEN}


class TestForceRunStillStartsNoEngineRun:
    def test_posting_the_route_spawns_nothing(self):
        """the dashboard never spawns the engine; the answer is a message."""
        with (
            patch.object(app_module, "trigger_alpha_bot") as trigger,
            patch.object(app_module.subprocess, "Popen") as popen,
        ):
            answer = answers.trigger_answer()
        assert answer["status"] == 200
        trigger.assert_not_called()
        popen.assert_not_called()
        assert "disabled" in answers.message_of(answer).lower()


# ---------------------------------------------------------------------------
# Emergency Liquidate works exactly as before
# ---------------------------------------------------------------------------


class TestEmergencyLiquidateIsUnchanged:
    def test_opening_the_modal_lists_the_accounts_and_keeps_confirm_disabled(
        self, gate, csrf_answer, accounts_answer
    ):
        """Harness half: the real /api/accounts body fills the select."""
        out = _panic_run(
            gate,
            csrf_answer,
            accounts_answer,
            [],
            steps=[{"op": "dom_ready"}, {"op": "open_panic"}, {"op": "flush"}],
        )
        final = _final(out)
        assert final["modal_hidden"] is False
        assert final["confirm_disabled"] is True
        assert BASE_ACCOUNT in final["account_html"] and OTHER_ACCOUNT in final["account_html"]
        assert final["account_value"] == BASE_ACCOUNT, "the first account is chosen"
        assert _sell_calls(out) == []

    def test_the_confirm_gate_in_the_markup_is_the_one_recorded_at_the_base(self, gate):
        """Render half: the phrase input's oninput gate is byte-identical."""
        assert gate == BASE["confirm_gate_oninput"]

    @pytest.mark.parametrize(
        "typed, disabled",
        [
            ("liquidate", True),
            ("Liquidate", True),
            ("LIQUIDATE ", True),
            (" LIQUIDATE", True),
            ("LIQUIDAT", True),
            ("", True),
            ("LIQUIDATE", False),
        ],
    )
    def test_only_the_exact_phrase_enables_confirm(
        self, gate, csrf_answer, accounts_answer, typed, disabled
    ):
        """lower case or any variant keeps Confirm disabled."""
        out = _panic_run(
            gate,
            csrf_answer,
            accounts_answer,
            [],
            steps=[
                {"op": "dom_ready"},
                {"op": "open_panic"},
                {"op": "type_phrase", "value": typed},
            ],
        )
        assert _final(out)["confirm_disabled"] is disabled

    def test_clicking_confirm_with_a_lower_case_phrase_sends_nothing(
        self, gate, csrf_answer, accounts_answer
    ):
        out = _panic_run(
            gate,
            csrf_answer,
            accounts_answer,
            [],
            steps=[
                {"op": "dom_ready"},
                {"op": "open_panic"},
                {"op": "type_phrase", "value": "liquidate"},
                {"op": "confirm"},
                {"op": "flush"},
            ],
        )
        assert _sell_calls(out) == []
        assert _final(out)["alerts"] == []

    def test_cancel_after_the_phrase_closes_the_modal_and_sends_nothing(
        self, gate, csrf_answer, accounts_answer
    ):
        out = _panic_run(
            gate,
            csrf_answer,
            accounts_answer,
            [],
            steps=[
                {"op": "dom_ready"},
                {"op": "open_panic"},
                {"op": "type_phrase", "value": CONFIRM_PHRASE},
                {"op": "cancel"},
                {"op": "flush"},
            ],
        )
        assert _final(out)["modal_hidden"] is True
        assert _sell_calls(out) == []

    def test_escape_closes_the_modal_and_sends_nothing(self, gate, csrf_answer, accounts_answer):
        out = _panic_run(
            gate,
            csrf_answer,
            accounts_answer,
            [],
            steps=[{"op": "dom_ready"}, {"op": "open_panic"}, {"op": "escape"}],
        )
        assert _final(out)["modal_hidden"] is True
        assert _sell_calls(out) == []

    def test_confirm_sends_the_recorded_request_byte_for_byte(
        self, gate, csrf_answer, accounts_answer
    ):
        """URL, method, headers and the body string with its key order."""
        accepted = answers.sell_answer(answers.sell_payload(BASE_ACCOUNT))
        out = _panic_run(gate, csrf_answer, accounts_answer, [accepted])
        calls = _sell_calls(out)
        assert len(calls) == 1
        assert _request_of(calls[0]) == _expected_base_request(app_module._CSRF_TOKEN)

    def test_the_second_account_is_sent_in_the_same_shape(self, gate, csrf_answer, accounts_answer):
        accepted = answers.sell_answer(answers.sell_payload(OTHER_ACCOUNT))
        out = _panic_run(
            gate,
            csrf_answer,
            accounts_answer,
            [accepted],
            steps=[
                {"op": "dom_ready"},
                {"op": "open_panic"},
                {"op": "select_account", "value": OTHER_ACCOUNT},
                {"op": "type_phrase", "value": CONFIRM_PHRASE},
                {"op": "confirm"},
                {"op": "flush"},
            ],
        )
        (call,) = _sell_calls(out)
        body = call["init"]["body"]
        assert body == json.dumps(
            {
                "account_id": OTHER_ACCOUNT,
                "confirm_account_id": OTHER_ACCOUNT,
                "confirm_phrase": CONFIRM_PHRASE,
            },
            separators=(",", ":"),
        )

    def test_a_refusal_is_shown_and_nothing_else_changes(self, gate, csrf_answer, accounts_answer):
        """the route's own refusal is shown; Force run's button is untouched."""
        refused = answers.sell_answer(answers.sell_payload(BASE_ACCOUNT, confirm_phrase=""))
        assert refused["status"] == 400, "premise"
        out = _panic_run(gate, csrf_answer, accounts_answer, [refused])
        final = _final(out)
        assert final["alerts"] == [answers.message_of(refused)]
        assert final["force_text"] == FORCE_LABEL and final["force_disabled"] is False

    def test_two_confirm_clicks_send_as_many_requests_as_the_base_did(
        self, gate, csrf_answer, accounts_answer
    ):
        """no in-flight guard is added to Confirm."""
        accepted = answers.sell_answer(answers.sell_payload(BASE_ACCOUNT))
        out = _panic_run(
            gate,
            csrf_answer,
            accounts_answer,
            [accepted],
            steps=[
                {"op": "dom_ready"},
                {"op": "open_panic"},
                {"op": "type_phrase", "value": CONFIRM_PHRASE},
                {"op": "confirm"},
                {"op": "confirm"},
                {"op": "flush"},
            ],
        )
        assert len(_sell_calls(out)) == BASE["two_confirm_clicks_request_count"]

    def test_a_failed_account_list_shows_its_failure_option_and_confirm_stays_disabled(
        self, gate, csrf_answer
    ):
        out = harness.run(
            [
                {"op": "dom_ready"},
                {"op": "open_panic"},
                {"op": "type_phrase", "value": "LIQUIDAT"},
                {"op": "flush"},
            ],
            responses={
                "/api/csrf-token": [csrf_answer],
                "/api/accounts": [{"reject": "NetworkError"}],
            },
            oninput=gate,
        )
        final = _final(out)
        assert "Failed to load accounts" in final["account_html"]
        assert final["account_value"] == ""
        assert final["confirm_disabled"] is True


# ---------------------------------------------------------------------------
# a refused or unreachable liquidation request tells the operator the truth
# ---------------------------------------------------------------------------


def _refusal_400(case: str) -> dict:
    payload = answers.sell_payload(BASE_ACCOUNT)
    if case == "confirm_account_id is required":
        payload.pop("confirm_account_id")
    elif case == "confirm_account_id does not match account_id":
        payload["confirm_account_id"] = OTHER_ACCOUNT
    elif case == "confirm_phrase is required":
        payload.pop("confirm_phrase")
    elif case == "confirm_phrase must be exactly LIQUIDATE":
        payload["confirm_phrase"] = "liquidate"
    elif case == "Missing credentials or account ID.":
        return answers.sell_answer_without_credentials(payload)
    else:
        raise AssertionError(case)
    return answers.sell_answer(payload)


FIVE_REFUSALS = [
    "confirm_account_id is required",
    "confirm_account_id does not match account_id",
    "confirm_phrase is required",
    "confirm_phrase must be exactly LIQUIDATE",
    "Missing credentials or account ID.",
]


def _assert_closed_clean_and_not_claiming(out: dict) -> str:
    """Modal closed, exactly one alert, nothing unhandled, no claim of a liquidation."""
    final = _final(out)
    assert final["modal_hidden"] is True, "the modal must close"
    assert final["unhandled"] == [], f"an unhandled error was raised: {final['unhandled']}"
    assert len(final["alerts"]) == 1, final["alerts"]
    text = final["alerts"][0]
    assert text.strip(), "the alert must say something"
    assert "liquidation initiated" not in text.lower()
    assert "dry-run" not in text.lower()
    return text


class TestARefusedRequestShowsTheServersRealAnswer:
    @pytest.mark.parametrize("case", FIVE_REFUSALS)
    def test_each_of_the_five_400s_shows_its_own_message(
        self, gate, csrf_answer, accounts_answer, case
    ):
        """the modal closes and the alert is the route's message, as at the ref."""
        refused = _refusal_400(case)
        assert refused["status"] == 400 and answers.message_of(refused) == case, "premise"
        out = _panic_run(gate, csrf_answer, accounts_answer, [refused])
        final = _final(out)
        assert final["modal_hidden"] is True
        assert final["alerts"] == [case]
        assert final["unhandled"] == []

    def test_a_403_html_refusal_says_the_session_check_refused_it_and_nothing_was_liquidated(
        self, gate, csrf_answer, accounts_answer
    ):
        """no unhandled error, the modal closes, the operator is told."""
        refused = answers.sell_answer_csrf_refused(answers.sell_payload(BASE_ACCOUNT))
        assert refused["status"] == 403 and "json" not in refused["content_type"], "premise"
        out = _panic_run(gate, csrf_answer, accounts_answer, [refused])
        text = _assert_closed_clean_and_not_claiming(out)
        assert "session" in text.lower()
        assert "refused" in text.lower()
        assert NOTHING_LIQUIDATED in text.lower()
        assert "<" not in text, "the HTML error page is never shown"

    def test_an_accepted_dry_run_shows_the_routes_message(self, gate, csrf_answer, accounts_answer):
        """the acceptance reads as at the ref."""
        accepted = answers.sell_answer(answers.sell_payload(BASE_ACCOUNT))
        assert accepted["status"] == 200, "premise"
        out = _panic_run(gate, csrf_answer, accounts_answer, [accepted])
        final = _final(out)
        assert final["modal_hidden"] is True
        assert final["alerts"] == [answers.message_of(accepted)]
        assert final["unhandled"] == []

    def test_an_accepted_live_start_shows_the_routes_message(
        self, gate, csrf_answer, accounts_answer
    ):
        started = answers.sell_answer_live_started(answers.sell_payload(BASE_ACCOUNT))
        out = _panic_run(gate, csrf_answer, accounts_answer, [started])
        assert _final(out)["alerts"] == [answers.message_of(started)]

    def test_a_2xx_without_a_message_keeps_the_existing_fallback_expression(
        self, gate, csrf_answer, accounts_answer
    ):
        """Derived at test time from the real bodies by deleting `message` (PROVENANCE: derived)."""
        dry = answers.sell_answer(answers.sell_payload(BASE_ACCOUNT))
        dry_body = json.loads(dry["body"])
        del dry_body["message"]
        out = _panic_run(gate, csrf_answer, accounts_answer, [dict(dry, body=json.dumps(dry_body))])
        assert _final(out)["alerts"] == ["Dry-run: no action taken."]
        live = answers.sell_answer_live_started(answers.sell_payload(BASE_ACCOUNT))
        live_body = json.loads(live["body"])
        del live_body["message"]
        out = _panic_run(
            gate, csrf_answer, accounts_answer, [dict(live, body=json.dumps(live_body))]
        )
        assert _final(out)["alerts"] == ["Liquidation initiated."]

    def test_a_failed_network_says_the_request_could_not_be_sent_and_nothing_was_liquidated(
        self, gate, csrf_answer, accounts_answer
    ):
        out = _panic_run(
            gate,
            csrf_answer,
            accounts_answer,
            [{"reject": "NetworkError when attempting to fetch"}],
        )
        text = _assert_closed_clean_and_not_claiming(out)
        assert "could not be sent" in text.lower()
        assert NOTHING_LIQUIDATED in text.lower()

    def test_after_a_refusal_reopening_and_confirming_sends_one_new_request_with_its_own_answer(
        self, gate, csrf_answer, accounts_answer
    ):
        first = _refusal_400("confirm_phrase is required")
        second = _refusal_400("confirm_account_id does not match account_id")
        steps = [
            {"op": "dom_ready"},
            {"op": "open_panic"},
            {"op": "type_phrase", "value": CONFIRM_PHRASE},
            {"op": "confirm"},
            {"op": "flush"},
            {"op": "open_panic"},
            {"op": "type_phrase", "value": CONFIRM_PHRASE},
            {"op": "confirm"},
            {"op": "flush"},
        ]
        out = _panic_run(gate, csrf_answer, accounts_answer, [first, second], steps=steps)
        calls = _sell_calls(out)
        assert len(calls) == 2, "exactly one new request after the reopen"
        assert _request_of(calls[0]) == _request_of(calls[1])
        after_first = out["snapshots"][4]
        assert after_first["alerts"] == [answers.message_of(first)]
        assert _final(out)["alerts"] == [answers.message_of(first), answers.message_of(second)]
        assert _final(out)["modal_hidden"] is True

    def test_an_expired_session_401_shows_the_status_and_never_the_dry_run_text(
        self, gate, csrf_answer, accounts_answer
    ):
        """the auth hook's JSON carries `error`, no `message`."""
        refused = answers.sell_answer_unauthenticated(answers.sell_payload(BASE_ACCOUNT))
        assert refused["status"] == 401 and "message" not in json.loads(refused["body"]), "premise"
        out = _panic_run(gate, csrf_answer, accounts_answer, [refused])
        text = _assert_closed_clean_and_not_claiming(out)
        assert "401" in text
        assert NOTHING_LIQUIDATED in text.lower()

    def test_a_misconfigured_deployment_503_shows_the_status(
        self, gate, csrf_answer, accounts_answer
    ):
        refused = answers.sell_answer_misconfigured(answers.sell_payload(BASE_ACCOUNT))
        assert refused["status"] == 503 and "message" not in json.loads(refused["body"]), "premise"
        out = _panic_run(gate, csrf_answer, accounts_answer, [refused])
        text = _assert_closed_clean_and_not_claiming(out)
        assert "503" in text
        assert NOTHING_LIQUIDATED in text.lower()

    def test_a_server_error_500_shows_the_status(self, gate, csrf_answer, accounts_answer):
        """Flask's own error page, not JSON."""
        refused = answers.sell_answer_server_error(answers.sell_payload(BASE_ACCOUNT))
        assert refused["status"] == 500, "premise"
        out = _panic_run(gate, csrf_answer, accounts_answer, [refused])
        text = _assert_closed_clean_and_not_claiming(out)
        assert "500" in text
        assert NOTHING_LIQUIDATED in text.lower()
        assert "<" not in text, "the HTML error page is never shown"

    def test_a_non_json_answer_that_is_neither_a_403_nor_a_5xx_still_says_nothing_was_liquidated(
        self, gate, csrf_answer, accounts_answer
    ):
        """A refusal with no message of any kind (a proxy's 502 page, a 404) is never silent."""
        for status in (404, 429, 502):
            page = {"status": status, "content_type": "text/html", "body": "<html>no</html>"}
            out = _panic_run(gate, csrf_answer, accounts_answer, [page])
            text = _assert_closed_clean_and_not_claiming(out)
            assert str(status) in text
            assert NOTHING_LIQUIDATED in text.lower()


class TestTheRequestIsTheSameForEveryAnswer:
    """the answer never changes what is sent."""

    @staticmethod
    def _answers() -> dict[str, dict]:
        payload = answers.sell_payload(BASE_ACCOUNT)
        out = {case: _refusal_400(case) for case in FIVE_REFUSALS}
        out["403"] = answers.sell_answer_csrf_refused(payload)
        out["200-dry-run"] = answers.sell_answer(payload)
        out["200-live"] = answers.sell_answer_live_started(payload)
        out["401"] = answers.sell_answer_unauthenticated(payload)
        out["503"] = answers.sell_answer_misconfigured(payload)
        out["500"] = answers.sell_answer_server_error(payload)
        out["network"] = {"reject": "NetworkError"}
        return out

    def test_url_method_headers_and_body_equal_the_record_for_every_answer(
        self, gate, csrf_answer, accounts_answer
    ):
        expected = _expected_base_request(app_module._CSRF_TOKEN)
        for name, answer in self._answers().items():
            out = _panic_run(gate, csrf_answer, accounts_answer, [answer])
            calls = _sell_calls(out)
            assert len(calls) == 1, f"{name}: one Confirm click sends one request"
            assert _request_of(calls[0]) == expected, name

    def test_the_only_calls_the_script_makes_while_confirming_are_the_known_ones(
        self, gate, csrf_answer, accounts_answer
    ):
        accepted = answers.sell_answer(answers.sell_payload(BASE_ACCOUNT))
        out = _panic_run(gate, csrf_answer, accounts_answer, [accepted])
        urls = {c["url"] for c in out["fetch_calls"]}
        known = {
            "/api/csrf-token",
            "/api/accounts",
            "/api/sell_account",
            "/api/candidate-alert",
            "/api/candidate-alert/mark-viewed",
        }
        assert urls <= known, f"unexpected request(s): {sorted(urls - known)}"


# ---------------------------------------------------------------------------
# Protected surfaces: the route, the modal markup and the existing panic tests
# ---------------------------------------------------------------------------

# Recorded at the plan base with: python -c "import hashlib,inspect,app; ..." over
# inspect.getsource(app.sell_account) with CRLF normalized to LF.
SELL_ACCOUNT_SOURCE_SHA256 = "c73b05f5273dade83023ff3034de21f10d5946ff24307a1337a0a4c0195b277c"
MANUAL_TRIGGER_SOURCE_SHA256 = "38bf64b4e577831c1b2dfbda2bcfa3a5ee8fcbfdd9228b147e410126ed2e271c"
# templates/_chrome.html from "<!-- Right-rail: mode pill -->" up to (not including) "</nav></div>":
# the mode pill, Force run now and Emergency Liquidate. The panic modal's own <style> block sits
# between the buttons and the modal and is NOT pinned: the header's layout classes may be added
# to it (the plan's reference to that block overlaps the lines it also calls unchanged).
CHROME_PILL_THROUGH_LIQUIDATE_SHA256 = (
    "0923102c4188a3970f1c00769e6b75d2b5416c6acce610c95f03ec6c10efacf0"
)
# From '<div id="panic-modal"' up to (not including) "<!-- Tweaks floating trigger": the modal.
CHROME_PANIC_MODAL_SHA256 = "b7db924d8cace8dd90fb391ac788de7becbe12e4d43facaa90ed317dd094c1ff"
PANIC_TEST_FILE_SHA256 = {
    "tests/app/test_sell_account_panic_confirm.py": (
        "103cd269e199a2be364c147ab30fc87d96e1f3f31dbf1a9cc91913036ed2872e"
    ),
    "tests/app/test_f003_liquidation_confirmation.py": (
        "15c6d6b32648664d806778985243d188fa8151b4e09618494bce8cef14c60aec"
    ),
}


class TestProtectedSurfacesAreByteIdentical:
    def test_the_sell_account_route_is_unchanged(self):
        """the route and every server-side check stay as they were."""
        source = inspect.getsource(app_module.sell_account)
        assert _sha256_lf(source) == SELL_ACCOUNT_SOURCE_SHA256, (
            "app.sell_account changed. STOP: the liquidation route and its checks are protected; "
            "ask before re-pinning."
        )

    def test_the_manual_trigger_route_is_unchanged(self):
        source = inspect.getsource(app_module.manual_trigger)
        assert _sha256_lf(source) == MANUAL_TRIGGER_SOURCE_SHA256

    def test_the_mode_pill_force_run_and_liquidate_buttons_are_unchanged(self):
        """the three controls read and behave exactly as at the base, at every width."""
        text = _chrome_text()
        start = text.index("<!-- Right-rail: mode pill -->")
        end = text.index("</nav></div>")
        segment = text[start:end]
        # The Force run label colour is the one admitted change (white to paper, for contrast).
        # It is put back to the pinned colour inside the Force run button alone, so the mode pill
        # and Emergency Liquidate stay byte for byte and any other Force run change still fails.
        button = segment.index('<button data-testid="force-run-btn"')
        button_end = segment.index("</button>", button)
        force_run = segment[button:button_end]
        assert force_run.count("color: var(--studio-paper);") == 1, (
            "Force run label colour is no longer the admitted contrast colour"
        )
        force_run = force_run.replace("color: var(--studio-paper);", "color:var(--studio-white);")
        segment = segment[:button] + force_run + segment[button_end:]
        assert _sha256_lf(segment) == CHROME_PILL_THROUGH_LIQUIDATE_SHA256, (
            "the mode pill / Force run / Emergency Liquidate markup changed. STOP and ask before "
            "re-pinning."
        )

    def test_the_panic_modal_markup_is_unchanged(self):
        """the account list, the phrase gate and the Confirm button keep their markup."""
        text = _chrome_text()
        start = text.index('<div id="panic-modal"')
        end = text.index("<!-- Tweaks floating trigger")
        assert _sha256_lf(text[start:end]) == CHROME_PANIC_MODAL_SHA256, (
            "the panic modal markup changed. STOP and ask before re-pinning."
        )

    @pytest.mark.parametrize("relative", sorted(PANIC_TEST_FILE_SHA256))
    def test_the_existing_panic_test_files_are_unchanged(self, relative):
        """they pass unchanged, so they must not be edited to make them pass."""
        assert _file_sha256_lf(relative) == PANIC_TEST_FILE_SHA256[relative]

    def test_the_request_construction_in_chrome_js_is_the_recorded_text(self):
        """The fetch call that sends the liquidation request keeps its text."""
        source = (REPO / "static" / "chrome.js").read_text(encoding="utf-8")
        assert re.search(
            r"PSRequest\.postAction\('/api/sell_account', \{\s*"
            r"method: 'POST',\s*"
            r"headers: \{ 'Content-Type': 'application/json', 'X-CSRF-Token': PSRequest\.csrfToken\(\) \|\| '' \},\s*"
            r"body: JSON\.stringify\(\{ account_id: accountId, confirm_account_id: accountId, "
            r"confirm_phrase: phrase \}\),\s*\}\)",
            source,
        ), "the /api/sell_account fetch call changed"
        assert "if (phrase !== 'LIQUIDATE') return;" in source

    def test_chrome_js_reads_responses_without_innerhtml(self):
        """Security: the answer is shown through textContent or alert, never markup."""
        source = (REPO / "static" / "chrome.js").read_text(encoding="utf-8")
        trigger = source[source.index("function forceRun") : source.index("// ── Workspace")]
        submit = source[source.index("function submitPanicLiquidation") :]
        submit = submit[: submit.index("window.submitPanicLiquidation")]
        assert "innerHTML" not in trigger
        assert "innerHTML" not in submit
