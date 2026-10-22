"""The request helper: what goes on the wire and how a failed request is answered.

Every test drives ``llm_request.send`` with a real ``anthropic.Anthropic`` over
the in-process HTTP fake, so request facts are read off the outgoing HTTP
request and the SDK's own reply parsing runs. Model IDs are read from the
captured Models page, never typed.

Patch targets and forms production must keep:
- ``llm_request.send(client, knob, *, effort, max_tokens, messages, system=None,
  tools=None, output_schema=None, timeout=None)`` returns a ``Reply`` and calls
  ``client.messages.create`` for the request and ``model_config.resolve`` /
  ``model_config.invalidate`` (module attribute access) for the model.
- ``anthropic`` is imported lazily inside ``send``; the module imports no
  anthropic code at load time.
- ``model_config.time`` is the clock; tests replace it.
"""

from __future__ import annotations

import copy
import json
import threading
import time

import anthropic
import httpx
import pytest

import llm_request
import model_config
from advisors.asset_swap_engine import _EMIT_SWAP_CANDIDATES_TOOL
from advisors.build_plan_generator import _EMIT_BUILD_PLANS_TOOL
from advisors.frontrunner_builder import _EMIT_OVERLAY_TOOL
from advisors.logic_change_engine import _EMIT_LOGIC_EDITS_TOOL
from model_config import Knob
from tests._anthropic_http_fake import load_fixture
from tests.llm_request.conftest import (
    CAPTURED,
    USER_MESSAGES,
    error_outcome,
    message_body,
    newest_id,
)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
LINE = {Knob.SUGGESTION: "fable", Knob.SYNTHESIS: "opus"}
ENV_VAR = {Knob.SUGGESTION: "ADVISOR_SUGGESTION_MODEL", Knob.SYNTHESIS: "ADVISOR_SYNTHESIS_MODEL"}
KNOBS = [
    pytest.param(Knob.SUGGESTION, id="suggestion"),
    pytest.param(Knob.SYNTHESIS, id="synthesis"),
]
RELISTED_PAGE = {
    Knob.SUGGESTION: "models/relisted_newer_fable.json",
    Knob.SYNTHESIS: "models/relisted_newer_opus.json",
}
OK_REPLY = {Knob.SUGGESTION: "thinking_structured.json", Knob.SYNTHESIS: "thinking_text.json"}
REAL_TOOLS = {
    "asset_swap": _EMIT_SWAP_CANDIDATES_TOOL,
    "logic_change": _EMIT_LOGIC_EDITS_TOOL,
    "build_plans": _EMIT_BUILD_PLANS_TOOL,
    "overlay": _EMIT_OVERLAY_TOOL,
}
TOOL_REPLY = {
    "asset_swap": "thinking_tool_emit_swap_candidates.json",
    "logic_change": "thinking_tool_emit_logic_edits.json",
    "build_plans": "thinking_tool_emit_build_plans.json",
    "overlay": "thinking_tool_emit_frontrunner_overlay.json",
}
FORBIDDEN_PARAMETERS = {"temperature", "top_p", "top_k", "thinking"}
SCHEMA = {
    "type": "object",
    "properties": {"suggestions": {"type": "array", "items": {"type": "string"}}},
    "required": ["suggestions"],
    "additionalProperties": False,
}


def send(fake, knob=Knob.SYNTHESIS, **overrides):
    kwargs = {"effort": "high", "max_tokens": 1000, "messages": copy.deepcopy(USER_MESSAGES)}
    kwargs.update(overrides)
    return llm_request.send(fake.client, knob, **kwargs)


def queue_ok(fake, knob=Knob.SYNTHESIS):
    fake.queue_message(message_body(OK_REPLY[knob]))


def models_named(fake) -> list[str]:
    return [r.body["model"] for r in fake.message_requests]


def respond_404_for(fake, retired_id, knob):
    """Answer 404 for the retired model and a success for any other."""

    def responder(recorded):
        if recorded.body["model"] == retired_id:
            return error_outcome("404_not_found.json")
        return message_body(OK_REPLY[knob])

    fake.set_message_responder(responder)


class TestRequestBody:
    @pytest.mark.parametrize("knob", KNOBS)
    def test_the_request_names_the_resolved_model_of_the_knobs_own_line(self, fake, knob):
        queue_ok(fake, knob)

        reply = send(fake, knob, max_tokens=777)

        (request,) = fake.message_requests
        assert reply.reason is None
        assert request.body["model"] == newest_id(LINE[knob])
        assert request.body["max_tokens"] == 777
        assert request.body["messages"] == USER_MESSAGES
        assert fake.misuse == []

    @pytest.mark.parametrize("effort", ["high", "medium"])
    def test_the_requested_effort_is_sent_and_no_output_format_without_a_schema(self, fake, effort):
        queue_ok(fake)

        send(fake, effort=effort)

        assert fake.message_requests[0].body["output_config"] == {"effort": effort}

    def test_an_output_schema_goes_on_the_wire_as_a_json_schema_format(self, fake):
        queue_ok(fake, Knob.SUGGESTION)

        send(fake, Knob.SUGGESTION, effort="high", output_schema=SCHEMA)

        assert fake.message_requests[0].body["output_config"] == {
            "effort": "high",
            "format": {"type": "json_schema", "schema": SCHEMA},
        }

    @pytest.mark.parametrize("knob", KNOBS)
    @pytest.mark.parametrize("with_tools", [False, True], ids=["no-tools", "tools"])
    def test_every_request_carries_the_fallback_setting_and_the_beta_header(
        self, fake, knob, with_tools
    ):
        queue_ok(fake, knob)
        tools = [REAL_TOOLS["asset_swap"]] if with_tools else None

        send(fake, knob, tools=tools)

        (request,) = fake.message_requests
        assert request.body["fallbacks"] == "default"
        assert request.headers["anthropic-beta"] == FALLBACK_BETA

    @pytest.mark.parametrize(
        "extra",
        [
            {},
            {"tools": [REAL_TOOLS["logic_change"]]},
            {"output_schema": SCHEMA},
            {"system": "You are an advisor."},
        ],
        ids=["plain", "tools", "schema", "system"],
    )
    def test_no_sampling_thinking_or_prefill_parameter_is_ever_sent(self, fake, extra):
        queue_ok(fake, Knob.SUGGESTION)

        send(fake, Knob.SUGGESTION, **extra)

        body = fake.message_requests[0].body
        assert FORBIDDEN_PARAMETERS.isdisjoint(body)
        assert "budget_tokens" not in json.dumps(body)
        assert body["messages"][-1]["role"] != "assistant"

    def test_tool_choice_is_auto_when_tools_are_given_and_absent_otherwise(self, fake):
        queue_ok(fake)
        queue_ok(fake)

        send(fake, tools=[REAL_TOOLS["overlay"]])
        send(fake)

        with_tools, without_tools = fake.message_requests
        assert with_tools.body["tool_choice"] == {"type": "auto"}
        assert "tool_choice" not in without_tools.body
        assert "tools" not in without_tools.body

    @pytest.mark.parametrize("name", list(REAL_TOOLS))
    def test_tools_go_out_exactly_as_the_site_passes_them_never_made_strict_by_the_helper(
        self, fake, name
    ):
        tool = REAL_TOOLS[name]
        original = copy.deepcopy(tool)
        fake.queue_message(message_body(TOOL_REPLY[name]))

        send(fake, Knob.SUGGESTION, tools=[tool])

        assert fake.message_requests[0].body["tools"] == [original]
        assert "strict" not in fake.message_requests[0].body["tools"][0]
        assert tool == original

    @pytest.mark.parametrize("name", ["asset_swap", "logic_change"])
    def test_a_tool_the_site_made_strict_goes_out_strict(self, fake, name):
        strict = llm_request.strict_tool(REAL_TOOLS[name])
        fake.queue_message(message_body(TOOL_REPLY[name]))

        send(fake, Knob.SUGGESTION, tools=[strict])

        sent = fake.message_requests[0].body["tools"][0]
        assert sent == strict
        assert sent["strict"] is True
        assert sent["input_schema"]["additionalProperties"] is False

    def test_system_text_cache_breakpoints_and_messages_pass_through_unchanged(self, fake):
        system = [
            {
                "type": "text",
                "text": "Stable instructions.",
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": "Volatile context."},
        ]
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Base prompt.",
                        "cache_control": {"type": "ephemeral"},
                    },
                    {"type": "text", "text": "Trailing block."},
                ],
            }
        ]
        queue_ok(fake)

        send(fake, system=copy.deepcopy(system), messages=copy.deepcopy(messages))

        body = fake.message_requests[0].body
        assert body["system"] == system
        assert body["messages"] == messages

    def test_system_is_omitted_when_the_site_gives_none(self, fake):
        queue_ok(fake)

        send(fake)

        assert "system" not in fake.message_requests[0].body

    def test_the_callers_arguments_are_not_mutated(self, fake):
        messages = copy.deepcopy(USER_MESSAGES)
        system = [{"type": "text", "text": "S", "cache_control": {"type": "ephemeral"}}]
        tools = [copy.deepcopy(REAL_TOOLS["build_plans"])]
        before = copy.deepcopy((messages, system, tools, SCHEMA))
        queue_ok(fake)

        send(fake, messages=messages, system=system, tools=tools, output_schema=SCHEMA)

        assert (messages, system, tools, SCHEMA) == before

    def test_a_timeout_the_site_gives_reaches_the_connection(self, fake):
        queue_ok(fake)

        send(fake, timeout=120.0)

        assert set(fake.message_requests[0].timeout.values()) == {120.0}

    def test_no_timeout_leaves_the_clients_default_in_force_not_an_unbounded_wait(self, fake):
        queue_ok(fake)

        send(fake)

        read = fake.message_requests[0].timeout["read"]
        assert read is not None
        assert read == fake.client.timeout.read

    def test_a_large_output_budget_without_a_timeout_is_sent(self, fake):
        queue_ok(fake, Knob.SUGGESTION)

        reply = send(fake, Knob.SUGGESTION, max_tokens=21000)

        assert reply.reason is None
        assert fake.message_requests[0].body["max_tokens"] == 21000

    def test_a_reply_without_a_tool_call_is_one_request_the_helper_never_retries(self, fake):
        fake.queue_message(message_body("text_only_with_tools.json"))

        reply = send(fake, Knob.SUGGESTION, tools=[REAL_TOOLS["asset_swap"]])

        assert reply.reason == "no_tool_call"
        assert len(fake.message_requests) == 1

    def test_the_attempt_bound_for_a_missing_tool_call_is_three(self):
        assert llm_request.NO_TOOL_CALL_ATTEMPTS == 3


class TestModelUnavailable:
    pytestmark = pytest.mark.usefixtures("unresolved_models")

    @pytest.mark.parametrize("knob", KNOBS)
    def test_a_failed_listing_sends_no_request_and_names_no_model(self, fake, knob):
        fake.queue_listing_outcome(error_outcome("503_unavailable.json"))

        reply = send(fake, knob)

        assert reply.reason == "model_unavailable"
        assert reply.reason_text() == "model_unavailable"
        assert reply.model == ""
        assert fake.message_requests == []

    @pytest.mark.parametrize("knob", KNOBS)
    def test_a_transport_failure_listing_sends_no_request(self, fake, knob):
        fake.queue_listing_outcome(httpx.ConnectError("no route"))

        reply = send(fake, knob)

        assert reply.reason == "model_unavailable"
        assert fake.message_requests == []

    def test_a_family_absent_from_a_successful_listing_is_unavailable(self, fake):
        fake.set_pages([load_fixture("models/no_fable.json")])

        reply = send(fake, Knob.SUGGESTION)

        assert reply.reason == "model_unavailable"
        assert fake.message_requests == []
        assert fake.misuse == []

    def test_an_absent_family_is_not_relisted_by_each_call_until_the_daily_refresh(
        self, fake, clock
    ):
        fake.set_pages([load_fixture("models/no_fable.json")])

        first = send(fake, Knob.SUGGESTION)
        second = send(fake, Knob.SUGGESTION)

        assert (first.reason, second.reason) == ("model_unavailable", "model_unavailable")
        assert fake.listing_sequences == 1
        clock.now += 25 * 60 * 60
        send(fake, Knob.SUGGESTION)
        assert fake.listing_sequences == 2
        assert fake.message_requests == []

    def test_the_family_that_is_listed_still_sends_while_the_other_is_absent(self, fake):
        fake.set_pages([load_fixture("models/no_fable.json")])
        queue_ok(fake, Knob.SYNTHESIS)

        unavailable = send(fake, Knob.SUGGESTION)
        sent = send(fake, Knob.SYNTHESIS)

        assert unavailable.reason == "model_unavailable"
        assert sent.reason is None
        assert models_named(fake) == [newest_id("opus")]


class TestResolvedModelNotFound:
    @pytest.mark.parametrize("knob", KNOBS)
    def test_a_404_relists_once_and_resends_once_on_the_newly_listed_model(self, fake, knob):
        retired = newest_id(LINE[knob])
        relisted = load_fixture(RELISTED_PAGE[knob])
        replacement = newest_id(LINE[knob], relisted["data"])
        assert replacement != retired
        fake.set_pages([relisted])
        respond_404_for(fake, retired, knob)

        reply = send(fake, knob)

        assert reply.reason is None
        assert models_named(fake) == [retired, replacement]
        assert fake.listing_sequences == 1
        assert model_config.peek(knob).model_id == replacement
        assert fake.misuse == []

    @pytest.mark.parametrize("knob", KNOBS)
    def test_the_resend_keeps_every_other_setting_of_the_request(self, fake, knob):
        retired = newest_id(LINE[knob])
        fake.set_pages([load_fixture(RELISTED_PAGE[knob])])
        respond_404_for(fake, retired, knob)

        send(fake, knob, effort="medium", max_tokens=4321, timeout=90.0, system="Keep me.")

        first, second = fake.message_requests
        assert {k: v for k, v in first.body.items() if k != "model"} == {
            k: v for k, v in second.body.items() if k != "model"
        }
        assert second.headers["anthropic-beta"] == FALLBACK_BETA
        assert second.timeout == first.timeout

    def test_a_relisting_that_names_the_same_model_ends_unavailable_without_a_resend(self, fake):
        retired = newest_id("opus")
        fake.queue_message(error_outcome("404_not_found.json"))

        reply = send(fake, Knob.SYNTHESIS)

        assert reply.reason == "model_unavailable"
        assert models_named(fake) == [retired]
        assert fake.listing_sequences == 1

    def test_a_relisting_that_fails_ends_unavailable_without_a_resend(self, fake):
        fake.queue_message(error_outcome("404_not_found.json"))
        fake.queue_listing_outcome(error_outcome("503_unavailable.json"))

        reply = send(fake, Knob.SYNTHESIS)

        assert reply.reason == "model_unavailable"
        assert len(fake.message_requests) == 1
        assert fake.listing_sequences == 1

    def test_a_relisting_that_no_longer_holds_the_family_ends_unavailable_without_a_resend(
        self, fake
    ):
        fake.set_pages([load_fixture("models/no_fable.json")])
        fake.queue_message(error_outcome("404_not_found.json"))

        reply = send(fake, Knob.SUGGESTION)

        assert reply.reason == "model_unavailable"
        assert len(fake.message_requests) == 1
        assert fake.listing_sequences == 1

    def test_a_second_404_after_the_resend_ends_unavailable_with_no_second_relisting(self, fake):
        fake.set_pages([load_fixture(RELISTED_PAGE[Knob.SYNTHESIS])])
        fake.queue_message(error_outcome("404_not_found.json"))
        fake.queue_message(error_outcome("404_not_found.json"))

        reply = send(fake, Knob.SYNTHESIS)

        assert reply.reason == "model_unavailable"
        assert len(fake.message_requests) == 2
        assert fake.listing_sequences == 1

    def test_a_pinned_run_follows_the_replacement_for_its_later_calls(self, fake):
        retired = newest_id("opus")
        relisted = load_fixture(RELISTED_PAGE[Knob.SYNTHESIS])
        replacement = newest_id("opus", relisted["data"])
        fake.set_pages([relisted])
        respond_404_for(fake, retired, Knob.SYNTHESIS)

        with model_config.pinned_run():
            first = send(fake)
            second = send(fake)

        assert (first.reason, second.reason) == (None, None)
        assert models_named(fake) == [retired, replacement, replacement]
        assert fake.listing_sequences == 1

    def test_a_late_404_for_a_model_another_caller_already_replaced_keeps_the_newer_one(self, fake):
        retired = newest_id("opus")
        relisted = load_fixture(RELISTED_PAGE[Knob.SYNTHESIS])
        replacement = newest_id("opus", relisted["data"])
        fake.set_pages([relisted])
        answered_late = []

        def responder(recorded):
            if recorded.body["model"] != retired:
                return message_body(OK_REPLY[Knob.SYNTHESIS])
            if not answered_late:
                # another caller's own 404 recovery lands while this request is in flight
                answered_late.append(model_config.invalidate(Knob.SYNTHESIS, retired, fake.client))
            return error_outcome("404_not_found.json")

        fake.set_message_responder(responder)

        reply = send(fake)

        assert answered_late[0].model_id == replacement
        assert reply.reason is None
        assert models_named(fake) == [retired, replacement]
        assert fake.listing_sequences == 1
        assert model_config.peek(Knob.SYNTHESIS).model_id == replacement

    def test_two_requests_that_both_get_a_404_for_the_old_model_cause_one_relisting(self, fake):
        retired = newest_id("opus")
        relisted = load_fixture(RELISTED_PAGE[Knob.SYNTHESIS])
        replacement = newest_id("opus", relisted["data"])
        fake.set_pages([relisted])
        respond_404_for(fake, retired, Knob.SYNTHESIS)
        fake.hold_messages()
        replies = []

        def call():
            replies.append(send(fake))

        threads = [threading.Thread(target=call) for _ in range(2)]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 5.0
        while len(fake.message_requests) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        fake.release_messages()
        for thread in threads:
            thread.join(timeout=10.0)

        assert [r.reason for r in replies] == [None, None]
        assert sorted(models_named(fake)) == sorted([retired, retired, replacement, replacement])
        assert fake.listing_sequences == 1
        assert model_config.peek(Knob.SYNTHESIS).model_id == replacement
        assert fake.misuse == []


class TestOverrideModel:
    @pytest.fixture
    def override_id(self):
        older = [r["id"] for r in CAPTURED["data"] if r["line"] == "opus"][-1]
        assert older != newest_id("opus")
        return older

    @pytest.mark.parametrize("knob", KNOBS)
    def test_the_override_is_sent_verbatim_with_no_listing(
        self, fake, monkeypatch, override_id, knob
    ):
        monkeypatch.setenv(ENV_VAR[knob], override_id)
        queue_ok(fake, knob)

        reply = send(fake, knob)

        assert reply.reason is None
        assert models_named(fake) == [override_id]
        assert fake.listing_requests == []

    @pytest.mark.parametrize("knob", KNOBS)
    def test_a_404_for_the_override_is_named_with_its_variable_and_is_not_retried(
        self, fake, monkeypatch, override_id, knob
    ):
        monkeypatch.setenv(ENV_VAR[knob], override_id)
        fake.queue_message(error_outcome("404_not_found.json"))

        reply = send(fake, knob)

        assert reply.reason == "override_model_not_found"
        assert reply.reason_text() == f"override_model_not_found: {ENV_VAR[knob]}"
        assert len(fake.message_requests) == 1
        assert fake.listing_requests == []

    @pytest.mark.parametrize("knob", KNOBS)
    def test_a_404_for_the_override_does_not_drop_the_family_resolution(
        self, fake, monkeypatch, override_id, knob
    ):
        resolved = model_config.peek(knob).model_id
        monkeypatch.setenv(ENV_VAR[knob], override_id)
        fake.queue_message(error_outcome("404_not_found.json"))

        send(fake, knob)
        monkeypatch.delenv(ENV_VAR[knob])

        assert model_config.peek(knob).model_id == resolved
        assert fake.listing_requests == []

    @pytest.mark.parametrize("knob", KNOBS)
    def test_a_400_for_the_override_is_named_with_its_variable_and_is_not_retried(
        self, fake, monkeypatch, override_id, knob
    ):
        resolved = model_config.peek(knob).model_id
        monkeypatch.setenv(ENV_VAR[knob], override_id)
        fake.queue_message(error_outcome("400_invalid_request.json"))

        reply = send(fake, knob)

        assert reply.reason == "override_request_rejected"
        assert reply.reason_text() == f"override_request_rejected: {ENV_VAR[knob]}"
        assert len(fake.message_requests) == 1
        assert fake.listing_requests == []
        monkeypatch.delenv(ENV_VAR[knob])
        assert model_config.peek(knob).model_id == resolved

    def test_an_error_other_than_404_or_400_for_the_override_is_raised_unchanged(
        self, fake, monkeypatch, override_id
    ):
        monkeypatch.setenv(ENV_VAR[Knob.SYNTHESIS], override_id)
        fake.queue_message(error_outcome("500_api_error.json"))

        with pytest.raises(anthropic.APIStatusError) as caught:
            send(fake)

        assert caught.value.status_code == 500
        assert len(fake.message_requests) == 1


class TestResolvedModelRejectsTheRequest:
    @pytest.mark.parametrize("knob", KNOBS)
    def test_a_400_is_raised_to_the_site_without_a_relisting_or_a_retry(self, fake, knob):
        resolved = model_config.peek(knob).model_id
        fake.queue_message(error_outcome("400_invalid_request.json"))

        with pytest.raises(anthropic.BadRequestError):
            send(fake, knob)

        assert len(fake.message_requests) == 1
        assert fake.listing_requests == []
        assert model_config.peek(knob).model_id == resolved


class TestOtherFailuresAreRaisedUnchanged:
    @pytest.mark.parametrize(
        "fixture",
        [
            "401_authentication.json",
            "403_permission.json",
            "429_rate_limit.json",
            "500_api_error.json",
            "529_overloaded.json",
        ],
    )
    def test_an_http_error_reaches_the_site_with_its_status_and_no_relisting(self, fake, fixture):
        outcome = error_outcome(fixture)
        resolved = model_config.peek(Knob.SYNTHESIS).model_id
        fake.queue_message(outcome)

        with pytest.raises(anthropic.APIStatusError) as caught:
            send(fake)

        assert caught.value.status_code == outcome["status"]
        assert len(fake.message_requests) == 1
        assert fake.listing_requests == []
        assert model_config.peek(Knob.SYNTHESIS).model_id == resolved

    @pytest.mark.parametrize(
        "failure,expected",
        [
            (httpx.ConnectError("no route"), anthropic.APIConnectionError),
            (httpx.ReadTimeout("slow"), anthropic.APITimeoutError),
        ],
        ids=["connect", "timeout"],
    )
    def test_a_transport_failure_reaches_the_site_as_the_sdk_raises_it(
        self, fake, failure, expected
    ):
        fake.queue_message(failure)

        with pytest.raises(expected):
            send(fake)

        assert fake.listing_requests == []
