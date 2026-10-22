"""The Run Advisor call names the model its family resolves to, or the operator's override.

Every suggestion-producing call used to name a pinned model through a named accessor with a
literal default. The suggestions site now asks the Models API for the newest active model of the
Fable line (``model_config``, through ``llm_request.send``) unless ``ADVISOR_SUGGESTION_MODEL``
holds a value, which is sent verbatim and never replaced when the API rejects it.

The suggestions half of this file drives the real route over a real ``anthropic.Anthropic`` on the
in-process HTTP fake (``tests/_anthropic_http_fake.py``): the model is read off the outgoing HTTP
request. IDs come from the captured Models page. The build-plan half (below) still pins the
build-plan site's own accessor and moves onto the request helper in its own cycle.

Patch targets and forms production must keep:
- ``ai_advisor._build_client()`` stays the client seam for ``request_suggestions``.
- ``advisors.build_plan_generator._build_client`` stays the seam for the build-plan half.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from model_config import Knob
from tests._anthropic_http_fake import AnthropicHttpFake
from tests._suggest_and_chat_routes import (
    anthropic_fake,  # noqa: F401  (fixture)
    assert_names_no_model_or_exception_text,
    captured_id_outside,
    error_outcome,
    ids_newest_first,
    message_body,
    models_named,
    only_message_request,
    post_suggest,
    use_client,
)

# ---------------------------------------------------------------------------
# (a) ai_advisor.request_suggestions, through POST /ai-advisor/suggest
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _site_client(monkeypatch, anthropic_fake):
    use_client(monkeypatch, anthropic_fake)


@pytest.fixture
def flask_client():
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        yield client


def _minimal_symphony_context() -> dict:
    """A minimal context dict for request_suggestions; only the model named on the wire matters."""
    return {}


class TestSuggestionModelResolution:
    def test_run_advisor_names_the_newest_fable_line_model_the_listing_returned(
        self, anthropic_fake, flask_client, unresolved_models
    ):
        anthropic_fake.queue_message(message_body("thinking_structured.json"))

        response = post_suggest(flask_client)

        assert response.status_code == 200 and "error" not in response.get_json()
        assert models_named(anthropic_fake) == [ids_newest_first("fable")[0]]
        assert anthropic_fake.listing_sequences == 1
        assert anthropic_fake.misuse == []

    def test_run_advisor_never_names_a_model_of_the_other_line(
        self, anthropic_fake, flask_client, unresolved_models
    ):
        anthropic_fake.queue_message(message_body("thinking_structured.json"))

        post_suggest(flask_client)

        assert only_message_request(anthropic_fake).body["model"] not in ids_newest_first("opus")


class TestSuggestionOverride:
    @pytest.mark.parametrize(
        "override_of",
        [
            pytest.param(lambda: ids_newest_first("fable")[1], id="older-fable-line-model"),
            pytest.param(
                lambda: captured_id_outside(*ids_newest_first("fable")),
                id="served-model-of-another-line",
            ),
        ],
    )
    def test_run_advisor_names_exactly_the_override_with_no_listing(
        self, anthropic_fake, flask_client, monkeypatch, unresolved_models, override_of
    ):
        override = override_of()
        monkeypatch.setenv(Knob.SUGGESTION.value, override)
        anthropic_fake.queue_listing_outcome(error_outcome("503_unavailable.json"))
        anthropic_fake.queue_message(message_body("thinking_structured.json"))

        response = post_suggest(flask_client)

        assert "error" not in response.get_json()
        assert models_named(anthropic_fake) == [override]
        assert anthropic_fake.listing_requests == []

    def test_a_request_to_an_override_carries_the_same_settings_as_any_other(
        self, anthropic_fake, flask_client, monkeypatch
    ):
        monkeypatch.setenv(Knob.SUGGESTION.value, ids_newest_first("fable")[1])
        anthropic_fake.queue_message(message_body("thinking_structured.json"))

        post_suggest(flask_client)

        body = only_message_request(anthropic_fake).body
        assert body["output_config"]["effort"] == "high"
        assert body["output_config"]["format"]["type"] == "json_schema"
        assert body["fallbacks"] == "default"

    def test_the_synthesis_override_never_reaches_the_suggestion_request(
        self, anthropic_fake, flask_client, monkeypatch
    ):
        monkeypatch.setenv(Knob.SYNTHESIS.value, captured_id_outside(*ids_newest_first("fable")))
        anthropic_fake.queue_message(message_body("thinking_structured.json"))

        post_suggest(flask_client)

        assert models_named(anthropic_fake) == [ids_newest_first("fable")[0]]

    def test_an_override_the_api_does_not_find_is_reported_on_the_page_and_not_replaced(
        self, anthropic_fake, flask_client, monkeypatch
    ):
        override = captured_id_outside(*ids_newest_first("fable"))
        monkeypatch.setenv(Knob.SUGGESTION.value, override)
        anthropic_fake.queue_message(error_outcome("404_not_found.json"))

        data = post_suggest(flask_client).get_json()

        assert "suggestions" not in data
        assert Knob.SUGGESTION.value in data["error"]
        assert "override_model_not_found" in data["error"]
        assert_names_no_model_or_exception_text(data["error"])
        assert models_named(anthropic_fake) == [override]


# ---------------------------------------------------------------------------
# (b) advisors.build_plan_generator.generate_build_plans
# ---------------------------------------------------------------------------


def _client_returning_empty_plans():
    response = MagicMock()
    response.stop_reason = "tool_use"
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.input = {"plans": []}
    tool_block.name = "emit_build_plans"
    response.content = [tool_block]
    client = MagicMock()
    client.messages.create.return_value = response
    return client


def test_ac16_build_plan_generator_defaults_to_fable_not_opus(monkeypatch):
    """MUST FAIL pre-fix: today the model is a bare 'claude-opus-4-8' literal
    with NO env var read at all."""
    import advisors.build_plan_generator as bpg

    monkeypatch.delenv("ADVISOR_SUGGESTION_MODEL", raising=False)
    fake_client = _client_returning_empty_plans()

    with patch.object(bpg, "_build_client", return_value=fake_client):
        bpg.generate_build_plans(
            bpg.Objective.diversify, n_plans=1, membership_set=frozenset({"AAA"})
        )

    assert fake_client.messages.create.called, (
        "generate_build_plans must call client.messages.create"
    )
    resolved_model = fake_client.messages.create.call_args.kwargs.get("model")
    assert resolved_model == "claude-fable-5", (
        f"AC-16 GAP: build_plan_generator's default suggestion model is "
        f"{resolved_model!r}, expected 'claude-fable-5'."
    )


def test_ac16_build_plan_generator_honors_suggestion_model_env_override(monkeypatch):
    import advisors.build_plan_generator as bpg

    monkeypatch.setenv("ADVISOR_SUGGESTION_MODEL", "test-marker-suggestion-model")
    fake_client = _client_returning_empty_plans()

    with patch.object(bpg, "_build_client", return_value=fake_client):
        bpg.generate_build_plans(
            bpg.Objective.diversify, n_plans=1, membership_set=frozenset({"AAA"})
        )

    resolved_model = fake_client.messages.create.call_args.kwargs.get("model")
    assert resolved_model == "test-marker-suggestion-model", (
        f"AC-16 GAP: setting ADVISOR_SUGGESTION_MODEL did not reach "
        f"build_plan_generator's SDK call — got model={resolved_model!r}."
    )


def test_ac16_both_suggestion_call_sites_share_the_same_accessor_default(monkeypatch):
    """Both call sites must resolve to the IDENTICAL override behavior: a fix that wires one
    site to a genuine shared knob and the other to an independent hardcoded string would pass
    every test above individually but silently diverge the moment the operator overrides the
    env var on only one path in production. This test proves both paths respond to the SAME env
    var. The suggestions side goes over the HTTP fake; the build-plan side keeps its client
    double until its own site moves onto the request helper."""
    import advisors.build_plan_generator as bpg
    import ai_advisor

    monkeypatch.setenv("ADVISOR_SUGGESTION_MODEL", "test-marker-shared")

    anthropic_fake = AnthropicHttpFake.with_captured_page()
    anthropic_fake.queue_message(message_body("thinking_structured.json"))
    use_client(monkeypatch, anthropic_fake)
    ai_advisor.request_suggestions(_minimal_symphony_context())
    request_suggestions_model = only_message_request(anthropic_fake).body["model"]

    fake_create_client = _client_returning_empty_plans()
    with patch.object(bpg, "_build_client", return_value=fake_create_client):
        bpg.generate_build_plans(
            bpg.Objective.diversify, n_plans=1, membership_set=frozenset({"AAA"})
        )
    build_plan_generator_model = fake_create_client.messages.create.call_args.kwargs.get("model")

    assert request_suggestions_model == "test-marker-shared", (
        f"ai_advisor.request_suggestions did not honor the shared override; got {request_suggestions_model!r}"
    )
    assert build_plan_generator_model == "test-marker-shared", (
        f"build_plan_generator.generate_build_plans did not honor the shared override; got {build_plan_generator_model!r}"
    )
