"""The build-plan generator's request on the wire and its handling of every reply class.

Every test runs the real ``generate_build_plans`` with a real ``anthropic.Anthropic``
client over the in-process HTTP fake, handed in through the generator's own client seam,
so request facts are read off the outgoing HTTP request and the SDK parses the reply.
Model IDs come from the captured Models page, never from a literal. Replies are the
captured fixtures, or captured envelopes whose tool input is replaced (see
``tests/_sb_generation_harness.py``); refusal and fallback-served replies are
schema-derived fixtures checked against the public references.

Patch targets and forms production must keep:
- ``advisors.build_plan_generator._build_client`` is called as a module attribute, once
  per ``generate_build_plans`` run, and its client is used for every attempt.
- ``llm_request.send`` and ``model_config`` are reached by module attribute access.
- ``GeneratorResult`` stays a dataclass of ``plans``, ``reason`` and the new ``model``
  (the reply's top-level model once a request returned a reply, else the empty string).
- ``GeneratorResult.reason`` is ``None`` on success; for a failed model call it is the
  reply's ``reason_text()`` (``no_tool_call``, ``max_tokens``, ``refusal: <category>``,
  ``refusal``, ``model_unavailable``, ``override_model_not_found: <ENV_VAR>``); any other
  exception is the exception type name alone.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib

import httpx
import pytest

import advisors.build_plan_generator as bpg
import model_config
from tests._anthropic_http_fake import AnthropicHttpFake
from tests._sb_generation_harness import (
    CAPTURED_MODELS,
    FALLBACK_BETA,
    MEMBERSHIP,
    RELISTED_NEWER_FABLE,
    SUGGESTION_ENV_VAR,
    conforming_diversify_plans,
    error_outcome,
    fake_with_captured_page,
    fake_with_failing_listing,
    fake_with_newer_fable_listing,
    fallback_reply_with_plans,
    install_generator_seam,
    max_tokens_reply,
    newest_active_record,
    refusal_after_partial_plans,
    refusal_reply,
    text_only_reply,
    tool_reply_with_plans,
    walk,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_GOLDEN = json.loads(
    (
        _REPO_ROOT
        / "tests"
        / "fixtures"
        / "strategy_builder"
        / "generation_prompt_from_scratch_baseline.json"
    ).read_text(encoding="utf-8")
)
_FORBIDDEN_PARAMETERS = {"temperature", "top_p", "top_k", "thinking"}
_SDK_NONSTREAMING_TOKEN_CEILING = (
    21333  # anthropic 0.85.0 raises above this when no timeout is passed
)


@pytest.fixture
def fake() -> AnthropicHttpFake:
    return fake_with_captured_page()


def generate(monkeypatch, fake, *, reasoning_context=None, n_plans=12):
    install_generator_seam(monkeypatch, fake)
    return bpg.generate_build_plans(
        bpg.Objective.diversify, MEMBERSHIP, n_plans=n_plans, reasoning_context=reasoning_context
    )


def captured_newest_suggestion_id() -> str:
    return newest_active_record("fable", CAPTURED_MODELS)["id"]


# ===========================================================================
# What goes on the wire
# ===========================================================================


class TestModelNamedOnTheWire:
    def test_request_names_the_resolved_suggestion_family_model(self, monkeypatch, fake):
        fake.queue_message(tool_reply_with_plans(conforming_diversify_plans()))
        generate(monkeypatch, fake)

        assert len(fake.message_requests) == 1
        request = fake.message_requests[0]
        assert request.body["model"] == captured_newest_suggestion_id()
        # Resolution came from the run's cached listing: this request path lists nothing.
        assert fake.listing_requests == []
        assert fake.misuse == []

    def test_request_names_the_later_fable_line_model_after_a_relisting(self, monkeypatch):
        fake = fake_with_newer_fable_listing()
        model_config._reset_registry()
        fake.queue_message(tool_reply_with_plans(conforming_diversify_plans()))
        generate(monkeypatch, fake)

        expected = newest_active_record("fable", RELISTED_NEWER_FABLE)["id"]
        assert expected != captured_newest_suggestion_id(), "the fixtures must differ"
        assert fake.message_requests[0].body["model"] == expected

    def test_override_is_sent_verbatim_without_a_listing(self, monkeypatch, fake):
        override = newest_active_record("opus", CAPTURED_MODELS)["id"]
        monkeypatch.setenv(SUGGESTION_ENV_VAR, f"  {override}  ")
        fake.queue_message(tool_reply_with_plans(conforming_diversify_plans()))
        generate(monkeypatch, fake)

        assert fake.message_requests[0].body["model"] == override
        assert fake.listing_requests == []


class TestRequestShape:
    def test_tool_choice_is_auto_and_the_tool_is_sent_unchanged_and_non_strict(
        self, monkeypatch, fake
    ):
        fake.queue_message(tool_reply_with_plans(conforming_diversify_plans()))
        generate(monkeypatch, fake)

        body = fake.message_requests[0].body
        assert body["tool_choice"] == {"type": "auto"}
        assert body["tools"] == [json.loads(json.dumps(bpg._EMIT_BUILD_PLANS_TOOL))]
        for node in walk(body["tools"]):
            assert "strict" not in node, "the build-plan tool stays non-strict"
            assert node.get("additionalProperties") is not False, (
                "the open rhs and condition objects stay open; the compiler validates them"
            )

    def test_the_prompt_asks_for_the_tool_by_name(self, monkeypatch, fake):
        # With tool_choice auto nothing forces the call, so the instruction must be in the prompt.
        fake.queue_message(tool_reply_with_plans(conforming_diversify_plans()))
        generate(monkeypatch, fake)

        blocks = fake.message_requests[0].body["messages"][0]["content"]
        text = "".join(b["text"] for b in blocks)
        assert "emit_build_plans" in text

    def test_effort_is_explicit_and_no_forbidden_parameter_is_sent(self, monkeypatch, fake):
        fake.queue_message(tool_reply_with_plans(conforming_diversify_plans()))
        generate(monkeypatch, fake)

        body = fake.message_requests[0].body
        assert body["output_config"] == {"effort": "high"}
        assert _FORBIDDEN_PARAMETERS.isdisjoint(body), sorted(_FORBIDDEN_PARAMETERS & set(body))
        assert not [n for n in walk(body) if "budget_tokens" in n]
        assert body["messages"][-1]["role"] == "user", "no trailing assistant message"
        assert "system" not in body, "the prompt stays in the user message as before"

    def test_output_budget_leaves_room_for_thinking_and_clears_the_sdk_guard(
        self, monkeypatch, fake
    ):
        assert 16384 < bpg.MAX_OUTPUT_TOKENS <= _SDK_NONSTREAMING_TOKEN_CEILING
        fake.queue_message(tool_reply_with_plans(conforming_diversify_plans()))
        result = generate(monkeypatch, fake)

        assert fake.message_requests[0].body["max_tokens"] == bpg.MAX_OUTPUT_TOKENS
        # The real SDK raises ValueError for an oversize non-streaming budget with no timeout,
        # which the generator would report as the reason.
        assert result.reason is None

    def test_cache_breakpoint_and_prompt_bytes_are_unchanged_on_the_wire(self, monkeypatch, fake):
        fake.queue_message(tool_reply_with_plans(conforming_diversify_plans()))
        install_generator_seam(monkeypatch, fake)
        args = _GOLDEN["call_args"]
        bpg.generate_build_plans(
            bpg.Objective[args["objective"]], frozenset(args["membership"]), n_plans=args["n_plans"]
        )

        content = fake.message_requests[0].body["messages"][0]["content"]
        assert isinstance(content, list) and len(content) == 1
        assert content[0]["text"] == _GOLDEN["prompt_text"]
        assert content[0]["cache_control"] == {"type": "ephemeral"}

    def test_reasoning_context_is_an_uncached_trailing_block(self, monkeypatch, fake):
        fake.queue_message(tool_reply_with_plans(conforming_diversify_plans()))
        install_generator_seam(monkeypatch, fake)
        args = _GOLDEN["call_args"]
        bpg.generate_build_plans(
            bpg.Objective[args["objective"]],
            frozenset(args["membership"]),
            n_plans=args["n_plans"],
            reasoning_context="HOLD SPY\n",
        )

        content = fake.message_requests[0].body["messages"][0]["content"]
        assert content[0]["text"] == _GOLDEN["prompt_text"]
        assert content[0]["cache_control"] == {"type": "ephemeral"}
        assert len(content) == 2
        assert "## OPERATOR CONTEXT" in content[1]["text"]
        assert "cache_control" not in content[1]

    def test_every_attempt_carries_the_fallback_setting_and_the_same_prompt(
        self, monkeypatch, fake
    ):
        for _ in range(bpg.MAX_GENERATION_ATTEMPTS):
            fake.queue_message(text_only_reply())
        generate(monkeypatch, fake)

        requests = fake.message_requests
        assert len(requests) == bpg.MAX_GENERATION_ATTEMPTS
        for request in requests:
            assert request.body["fallbacks"] == "default"
            assert request.headers["anthropic-beta"] == FALLBACK_BETA
        assert len({json.dumps(r.body["messages"], sort_keys=True) for r in requests}) == 1


# ===========================================================================
# Reading the reply
# ===========================================================================


class TestReplyReading:
    def test_captured_reply_tool_input_is_parsed_not_reported_as_a_payload_failure(
        self, monkeypatch, fake
    ):
        from tests._anthropic_http_fake import load_fixture

        fake.queue_message(load_fixture("messages/thinking_tool_emit_build_plans.json"))
        result = generate(monkeypatch, fake)

        # The captured plan is an equal-weight pair of assets: it parses, then fails the
        # diversify signature. Neither a payload failure nor a missing tool call.
        assert result.plans == []
        assert isinstance(result.reason, str) and "signature" in result.reason
        assert len(fake.message_requests) == 1

    def test_admitted_plans_carry_the_reply_model(self, monkeypatch, fake):
        reply = tool_reply_with_plans(conforming_diversify_plans())
        fake.queue_message(reply)
        result = generate(monkeypatch, fake)

        assert result.reason is None
        assert result.plans, "the conforming plans must be admitted"
        assert all(plan["provenance"] == bpg.PROVENANCE_BUILT_NEW for plan in result.plans)
        assert result.model == reply["model"]

    def test_fallback_served_reply_supplies_the_plans_and_names_the_serving_model(
        self, monkeypatch, fake
    ):
        reply = fallback_reply_with_plans(conforming_diversify_plans())
        fake.queue_message(reply)
        result = generate(monkeypatch, fake)

        requested = fake.message_requests[0].body["model"]
        assert reply["model"] != requested, "the fixture must be served by a different model"
        assert result.reason is None
        assert result.plans
        assert result.model == reply["model"]

    def test_result_model_defaults_to_empty_and_is_a_dataclass_field(self):
        fields = {f.name: f for f in dataclasses.fields(bpg.GeneratorResult)}
        assert fields["model"].default == ""
        assert bpg.GeneratorResult(plans=[]).model == ""


# ===========================================================================
# Attempts: no_tool_call and max_tokens share one bound; a refusal stops the loop
# ===========================================================================


class TestAttempts:
    def test_text_only_replies_use_all_attempts_then_report_no_tool_call(self, monkeypatch, fake):
        for _ in range(bpg.MAX_GENERATION_ATTEMPTS):
            fake.queue_message(text_only_reply())
        result = generate(monkeypatch, fake)

        assert len(fake.message_requests) == bpg.MAX_GENERATION_ATTEMPTS == 3
        assert fake.misuse == []
        assert result.plans == []
        assert result.reason == "no_tool_call"

    def test_a_tool_call_after_a_text_only_reply_recovers(self, monkeypatch, fake):
        fake.queue_message(text_only_reply())
        fake.queue_message(tool_reply_with_plans(conforming_diversify_plans()))
        result = generate(monkeypatch, fake)

        assert len(fake.message_requests) == 2
        assert result.reason is None and result.plans

    def test_the_attempt_bound_is_shared_between_truncation_and_missing_tool_call(
        self, monkeypatch, fake
    ):
        fake.queue_message(max_tokens_reply())
        fake.queue_message(text_only_reply())
        fake.queue_message(text_only_reply())
        generate(monkeypatch, fake)

        assert len(fake.message_requests) == bpg.MAX_GENERATION_ATTEMPTS
        assert fake.misuse == []

    def test_a_tool_call_on_the_last_shared_attempt_still_succeeds(self, monkeypatch, fake):
        fake.queue_message(text_only_reply())
        fake.queue_message(max_tokens_reply())
        fake.queue_message(tool_reply_with_plans(conforming_diversify_plans()))
        result = generate(monkeypatch, fake)

        assert len(fake.message_requests) == 3
        assert result.reason is None and result.plans

    def test_truncation_on_every_attempt_reports_the_max_tokens_code(self, monkeypatch, fake):
        for _ in range(bpg.MAX_GENERATION_ATTEMPTS):
            fake.queue_message(max_tokens_reply())
        result = generate(monkeypatch, fake)

        assert len(fake.message_requests) == bpg.MAX_GENERATION_ATTEMPTS
        assert result.plans == []
        assert result.reason == "max_tokens"

    def test_a_refusal_stops_the_loop_without_a_second_request(self, monkeypatch, fake):
        fake.queue_message(refusal_reply("refusal_reasoning_extraction.json"))
        fake.queue_message(tool_reply_with_plans(conforming_diversify_plans()))
        result = generate(monkeypatch, fake)

        assert len(fake.message_requests) == 1, "the same request is never re-sent after a refusal"
        assert result.plans == []
        assert result.reason == "refusal: reasoning_extraction"

    def test_a_refusal_with_no_category_reports_the_bare_code(self, monkeypatch, fake):
        fake.queue_message(refusal_reply("refusal_null_category.json"))
        result = generate(monkeypatch, fake)

        assert len(fake.message_requests) == 1
        assert result.reason == "refusal"

    def test_a_refusal_after_a_text_only_reply_ends_generation(self, monkeypatch, fake):
        fake.queue_message(text_only_reply())
        fake.queue_message(refusal_reply("refusal_null_category.json"))
        fake.queue_message(tool_reply_with_plans(conforming_diversify_plans()))
        result = generate(monkeypatch, fake)

        assert len(fake.message_requests) == 2
        assert result.plans == []
        assert result.reason == "refusal"

    def test_plans_in_a_tool_block_that_a_refusal_followed_are_discarded(self, monkeypatch, fake):
        fake.queue_message(refusal_after_partial_plans(conforming_diversify_plans()))
        result = generate(monkeypatch, fake)

        assert result.plans == []
        assert result.reason == "refusal"

    def test_a_refusal_reply_still_names_the_model_that_answered(self, monkeypatch, fake):
        reply = refusal_reply("refusal_null_category.json")
        fake.queue_message(reply)
        result = generate(monkeypatch, fake)

        assert result.model == reply["model"]


# ===========================================================================
# Model resolution failures at the site
# ===========================================================================


class TestModelUnavailable:
    def test_unresolved_family_sends_nothing_and_reports_the_code(
        self, monkeypatch, unresolved_models
    ):
        fake = fake_with_failing_listing()
        result = generate(monkeypatch, fake)

        assert fake.message_requests == []
        assert result.plans == []
        assert result.reason == "model_unavailable"
        assert result.model == ""

    def test_override_the_api_does_not_know_reports_the_variable_without_retry(
        self, monkeypatch, fake
    ):
        override = newest_active_record("opus", CAPTURED_MODELS)["id"]
        monkeypatch.setenv(SUGGESTION_ENV_VAR, override)
        fake.queue_message(error_outcome("404_not_found.json"))
        fake.queue_message(tool_reply_with_plans(conforming_diversify_plans()))
        result = generate(monkeypatch, fake)

        assert len(fake.message_requests) == 1
        assert fake.message_requests[0].body["model"] == override
        assert result.plans == []
        assert result.reason == f"override_model_not_found: {SUGGESTION_ENV_VAR}"

    def test_a_rejected_request_on_a_resolved_model_reports_only_the_exception_type(
        self, monkeypatch, fake
    ):
        fake.queue_message(error_outcome("400_invalid_request.json"))
        result = generate(monkeypatch, fake)

        assert result.plans == []
        assert result.reason == "BadRequestError"
        assert "deprecated" not in result.reason and "temperature" not in result.reason

    def test_a_transport_failure_reports_only_the_exception_type(self, monkeypatch, fake):
        fake.queue_message(httpx.ConnectError("secret-host-name-unreachable"))
        result = generate(monkeypatch, fake)

        assert result.plans == []
        assert isinstance(result.reason, str) and result.reason.endswith("Error")
        assert "secret-host-name" not in result.reason
