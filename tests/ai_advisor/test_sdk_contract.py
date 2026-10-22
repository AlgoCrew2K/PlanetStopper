"""The Run Advisor call: what goes on the wire and how every reply is read.

``request_suggestions`` calls the Messages API through the shared request helper, asks for JSON in
the suggestions schema (``output_config.format``) and validates the reply text itself, because the
SDK's ``messages.parse`` validates every text block inside the SDK and raises on a refusal's or a
truncation's partial JSON before the stop reason can be read.

Every test hands the site a real ``anthropic.Anthropic`` over the in-process HTTP fake
(``tests/_anthropic_http_fake.py``): request facts are read off the outgoing HTTP request and the
SDK's own reply parsing runs. Replies are the captured bodies, or derived from them by one named
edit and re-checked against the public API references (``tests/_suggest_and_chat_routes.py``).
Model IDs and expected texts come from the fixtures, never from literals.

Patch targets and forms production must keep:
- ``ai_advisor._build_client()`` stays the client seam, called as a module global by
  ``request_suggestions``.
- ``request_suggestions(context) -> (ConfigSuggestionsResponse | None, str | None)`` never raises;
  the error string carries the reply's reason text (``model_unavailable``, ``refusal: <category>``,
  ``max_tokens``, ``<code>: <ENV_VAR>``) and never a model ID, reply text or exception message.
"""

from __future__ import annotations

import anthropic
import httpx
import pytest

import ai_advisor
from model_config import Knob
from tests._anthropic_http_fake import AnthropicHttpFake
from tests._suggest_and_chat_routes import (
    FALLBACK_BETA,
    FORBIDDEN_PARAMETERS,
    SUGGEST_CONTEXT,
    SUGGESTION_ITEM,
    anthropic_fake,  # noqa: F401  (fixture)
    assert_names_no_model_or_exception_text,
    captured_id_outside,
    derive_reply,
    error_outcome,
    fallback_reply,
    ids_newest_first,
    message_body,
    only_message_request,
    post_suggest,
    structured_reply,
    text_reply,
    use_client,
)

# Per-site request settings for the suggestions site (high effort, plus an output budget and a
# per-attempt timeout sized for a model that always thinks): a model that always thinks
# spends part of ``max_tokens`` on thinking, so the old 2048 budget and 30 s timeout cannot hold a
# reply at this effort.
SUGGESTION_EFFORT = "high"
SUGGESTION_MAX_TOKENS = 16000
SUGGESTION_TIMEOUT_SECONDS = 120.0


@pytest.fixture(autouse=True)
def _site_client(monkeypatch, anthropic_fake):
    use_client(monkeypatch, anthropic_fake)


@pytest.fixture
def flask_client():
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        yield client


def run_request_suggestions(anthropic_fake, reply=None):
    anthropic_fake.queue_message(
        message_body("thinking_structured.json") if reply is None else reply
    )
    result = ai_advisor.request_suggestions(SUGGEST_CONTEXT)
    assert anthropic_fake.misuse == []
    return result


# ---------------------------------------------------------------------------
# The request: JSON output in the suggestions schema, explicit effort, a budget that fits thinking
# ---------------------------------------------------------------------------


class TestRequestOnTheWire:
    def test_the_request_carries_the_suggestions_schema_as_the_json_output_format(
        self, anthropic_fake
    ):
        run_request_suggestions(anthropic_fake)

        output_config = only_message_request(anthropic_fake).body["output_config"]
        assert output_config["format"] == {
            "type": "json_schema",
            "schema": anthropic.transform_schema(ai_advisor.ConfigSuggestionsResponse),
        }

    def test_the_request_sets_its_effort_explicitly(self, anthropic_fake):
        run_request_suggestions(anthropic_fake)

        assert (
            only_message_request(anthropic_fake).body["output_config"]["effort"]
            == SUGGESTION_EFFORT
        )

    def test_the_output_budget_and_timeout_hold_a_reply_that_thinks(self, anthropic_fake):
        run_request_suggestions(anthropic_fake)

        request = only_message_request(anthropic_fake)
        assert request.body["max_tokens"] == SUGGESTION_MAX_TOKENS
        assert set(request.timeout.values()) == {SUGGESTION_TIMEOUT_SECONDS}

    def test_the_request_names_the_newest_fable_line_model(self, anthropic_fake):
        run_request_suggestions(anthropic_fake)

        assert only_message_request(anthropic_fake).body["model"] == ids_newest_first("fable")[0]

    def test_the_site_messages_go_out_unchanged_and_end_on_the_user_turn(self, anthropic_fake):
        run_request_suggestions(anthropic_fake)

        messages = only_message_request(anthropic_fake).body["messages"]
        assert messages == ai_advisor._build_messages(SUGGEST_CONTEXT)
        assert messages[-1]["role"] == "user"

    def test_the_request_sends_no_sampling_thinking_budget_or_tool_parameter(self, anthropic_fake):
        run_request_suggestions(anthropic_fake)

        body = only_message_request(anthropic_fake).body
        assert FORBIDDEN_PARAMETERS.isdisjoint(body)
        assert "tools" not in body and "tool_choice" not in body

    def test_the_request_opts_in_to_the_server_side_fallback(self, anthropic_fake):
        run_request_suggestions(anthropic_fake)

        request = only_message_request(anthropic_fake)
        assert request.body["fallbacks"] == "default"
        assert request.headers["anthropic-beta"] == FALLBACK_BETA

    def test_the_example_suggestion_has_every_field_the_schema_requires(self):
        schema = anthropic.transform_schema(ai_advisor.ConfigSuggestionsResponse)
        item = schema["$defs"]["ConfigSuggestion"]

        assert set(item["required"]) <= set(SUGGESTION_ITEM)
        assert set(SUGGESTION_ITEM) <= set(item["properties"])


# ---------------------------------------------------------------------------
# The reply: the structured text block, wherever it sits, validated by the site
# ---------------------------------------------------------------------------


class TestReplyRead:
    def test_an_empty_suggestion_list_is_a_valid_answer_not_an_error(self, anthropic_fake):
        response, error = run_request_suggestions(anthropic_fake)

        assert error is None
        assert isinstance(response, ai_advisor.ConfigSuggestionsResponse)
        assert response.suggestions == []

    def test_a_populated_reply_round_trips_every_field(self, anthropic_fake):
        response, error = run_request_suggestions(
            anthropic_fake, structured_reply([SUGGESTION_ITEM])
        )

        assert error is None
        (suggestion,) = response.suggestions
        assert suggestion.model_dump(include=set(SUGGESTION_ITEM)) == SUGGESTION_ITEM

    @pytest.mark.parametrize(
        "reply",
        [
            pytest.param(lambda: message_body("thinking_text.json"), id="prose-not-json"),
            pytest.param(
                lambda: text_reply('{"suggestions": [{"config_key": "MAX_SQUEEZE_FLOOR"}]}'),
                id="suggestion-missing-fields",
            ),
            pytest.param(lambda: text_reply('{"items": []}'), id="wrong-top-level-key"),
            pytest.param(
                lambda: derive_reply(
                    "thinking_structured.json",
                    lambda body: body.update(
                        content=[b for b in body["content"] if b["type"] != "text"]
                    ),
                ),
                id="no-text-block",
            ),
        ],
    )
    def test_a_reply_that_is_not_the_suggestions_schema_degrades_and_shows_no_reply_text(
        self, anthropic_fake, reply
    ):
        body = reply()

        response, error = run_request_suggestions(anthropic_fake, body)

        assert response is None
        assert isinstance(error, str) and error.strip()
        for block in body["content"]:
            if block["type"] == "text":
                assert block["text"] not in error

    @pytest.mark.parametrize(
        "outcome",
        [
            pytest.param(error_outcome("401_authentication.json"), id="401"),
            pytest.param(error_outcome("403_permission.json"), id="403"),
            pytest.param(error_outcome("429_rate_limit.json"), id="429"),
            pytest.param(error_outcome("500_api_error.json"), id="500"),
            pytest.param(error_outcome("529_overloaded.json"), id="529"),
            pytest.param(httpx.ConnectError("example connection failure"), id="connect"),
            pytest.param(httpx.ReadTimeout("example read timeout"), id="timeout"),
        ],
    )
    def test_a_failed_request_degrades_with_the_exception_type_only(self, anthropic_fake, outcome):
        anthropic_fake.queue_message(outcome)

        response, error = ai_advisor.request_suggestions(SUGGEST_CONTEXT)

        assert response is None
        assert isinstance(error, str) and error.strip()
        assert len(anthropic_fake.message_requests) == 1
        # What the SDK puts in an exception's text: the status line and the request ID. (The
        # API's own message can be a word the exception's class name also contains.)
        exception_texts = (
            ["Error code", outcome["body"]["request_id"]]
            if isinstance(outcome, dict)
            else [str(outcome)]
        )
        assert_names_no_model_or_exception_text(error, *exception_texts)

    def test_a_400_for_the_resolved_model_degrades_with_the_exception_type_only(
        self, anthropic_fake
    ):
        outcome = error_outcome("400_invalid_request.json")
        anthropic_fake.queue_message(outcome)

        response, error = ai_advisor.request_suggestions(SUGGEST_CONTEXT)

        assert response is None
        assert "BadRequestError" in error
        assert_names_no_model_or_exception_text(error, outcome["body"]["error"]["message"])
        assert len(anthropic_fake.message_requests) == 1


# ---------------------------------------------------------------------------
# POST /ai-advisor/suggest: reply shapes that reach the operator
# ---------------------------------------------------------------------------


class TestSuggestRouteReplies:
    def test_a_structured_reply_that_follows_a_thinking_block_is_parsed(
        self, anthropic_fake, flask_client
    ):
        anthropic_fake.queue_message(structured_reply([SUGGESTION_ITEM]))

        response = post_suggest(flask_client)

        data = response.get_json()
        assert response.status_code == 200 and "error" not in data
        assert [s["config_key"] for s in data["suggestions"]] == [SUGGESTION_ITEM["config_key"]]
        assert anthropic_fake.misuse == []

    def test_a_reply_served_by_the_fallback_model_gives_its_suggestions(
        self, anthropic_fake, flask_client
    ):
        declined = ids_newest_first("fable")[0]
        served = ids_newest_first("opus")[0]
        reply = fallback_reply(
            structured_reply([SUGGESTION_ITEM])["content"][-1]["text"],
            declined_id=declined,
            served_id=served,
        )
        anthropic_fake.queue_message(reply)

        response = post_suggest(flask_client)

        data = response.get_json()
        assert "error" not in data
        assert [s["config_key"] for s in data["suggestions"]] == [SUGGESTION_ITEM["config_key"]]
        request = only_message_request(anthropic_fake)
        assert request.body["fallbacks"] == "default"
        assert request.headers["anthropic-beta"] == FALLBACK_BETA

    def test_a_refusal_from_every_model_reports_refusal_and_the_last_category(
        self, anthropic_fake, flask_client
    ):
        body = message_body("refusal_reasoning_extraction.json")
        category = body["stop_details"]["category"]
        anthropic_fake.queue_message(body)

        response = post_suggest(flask_client)

        data = response.get_json()
        assert response.status_code == 200 and "suggestions" not in data
        assert "refusal" in data["error"] and category in data["error"]
        assert_names_no_model_or_exception_text(data["error"])

    def test_a_refusal_without_a_category_names_no_category(self, anthropic_fake, flask_client):
        anthropic_fake.queue_message(message_body("refusal_null_category.json"))
        other = message_body("refusal_reasoning_extraction.json")["stop_details"]["category"]

        response = post_suggest(flask_client)

        error = response.get_json()["error"]
        assert "refusal" in error
        assert other not in error and "None" not in error and "null" not in error

    def test_a_refusal_after_partial_output_shows_none_of_it(self, anthropic_fake, flask_client):
        body = message_body("refusal_after_partial_text.json")
        anthropic_fake.queue_message(body)

        response = post_suggest(flask_client)

        data = response.get_json()
        assert "suggestions" not in data and "refusal" in data["error"]
        partial = next(b["text"] for b in body["content"] if b["type"] == "text")
        assert partial not in response.get_data(as_text=True)

    def test_a_refusal_whose_text_is_a_complete_suggestion_list_still_returns_none(
        self, anthropic_fake, flask_client
    ):
        body = derive_reply(
            "thinking_structured.json",
            lambda b: b.update(
                content=structured_reply([SUGGESTION_ITEM])["content"],
                stop_reason="refusal",
                stop_details={"type": "refusal", "category": None, "explanation": None},
            ),
        )
        anthropic_fake.queue_message(body)

        data = post_suggest(flask_client).get_json()

        assert "suggestions" not in data and "refusal" in data["error"]

    def test_a_reply_cut_off_at_max_tokens_reports_max_tokens_and_no_suggestion(
        self, anthropic_fake, flask_client
    ):
        anthropic_fake.queue_message(message_body("max_tokens.json"))

        data = post_suggest(flask_client).get_json()

        assert "suggestions" not in data and "max_tokens" in data["error"]
        assert len(anthropic_fake.message_requests) == 1

    def test_a_reply_cut_off_at_max_tokens_is_not_used_even_when_its_text_parses(
        self, anthropic_fake, flask_client
    ):
        anthropic_fake.queue_message(structured_reply([SUGGESTION_ITEM], stop_reason="max_tokens"))

        data = post_suggest(flask_client).get_json()

        assert "suggestions" not in data and "max_tokens" in data["error"]


# ---------------------------------------------------------------------------
# An override the API rejects is reported by variable name and never replaced
# ---------------------------------------------------------------------------


class TestOverrideRejected:
    @pytest.fixture
    def override(self, monkeypatch):
        served = captured_id_outside(*ids_newest_first("fable"))
        monkeypatch.setenv(Knob.SUGGESTION.value, served)
        return served

    def test_an_override_the_api_does_not_find_names_its_variable_and_is_not_replaced(
        self, anthropic_fake, override
    ):
        anthropic_fake.queue_message(error_outcome("404_not_found.json"))

        response, error = ai_advisor.request_suggestions(SUGGEST_CONTEXT)

        assert response is None
        assert Knob.SUGGESTION.value in error and "override_model_not_found" in error
        assert_names_no_model_or_exception_text(error)
        assert [r.body["model"] for r in anthropic_fake.message_requests] == [override]
        assert anthropic_fake.listing_requests == []

    def test_an_override_whose_request_the_api_rejects_names_its_variable(
        self, anthropic_fake, override
    ):
        anthropic_fake.queue_message(error_outcome("400_invalid_request.json"))

        response, error = ai_advisor.request_suggestions(SUGGEST_CONTEXT)

        assert response is None
        assert Knob.SUGGESTION.value in error and "override_request_rejected" in error
        assert len(anthropic_fake.message_requests) == 1
        assert anthropic_fake.listing_requests == []
