"""RED tests -- advisors/retirement_explainer.py (AC-1, AC-9).

feature-plans/retirement-approval-lifecycle.md AC-1: explain_recommendation(
recommendation: dict) -> str | None. Uses ai_advisor._build_client() and sends
one plain text (NOT tool-use) request through llm_request.send on the
suggestion knob. D-1 never-raises: any failure, and any reply the request
helper classifies with a reason (model_unavailable, refusal, max_tokens, an
override rejection), returns None (honest empty); the log carries the reason
text or type(exc).__name__, never str(exc) and never reply content. Read-only
-- no DB write, no trade/exec primitive.

Two harnesses, both patching "ai_advisor._build_client" and neither reaching
the network. The first classes drive a MagicMock client with SimpleNamespace
content blocks (type "text" and a .text attribute: the shape the request
helper reads), the convention of tests/ai_advisor/test_chat_engine.py. The
last classes drive a real anthropic client over the in-process HTTP fake
(tests/_anthropic_http_fake.py), so the request is asserted as sent on the
wire and the SDK parses captured replies; model IDs come from the captured
Models page, never typed.
"""

from __future__ import annotations

import logging
import pathlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests._anthropic_http_fake import AnthropicHttpFake
from tests._anthropic_site_harness import (
    FALLBACK_BETA,
    FORBIDDEN_PARAMETERS,
    message_body,
    newest_id,
    older_id,
    refusal_reason_text,
    served_by,
    text_of,
)
from tests.llm_request.conftest import error_outcome, fake  # noqa: F401  (the shared fixture)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MODULE_REL_PATH = "advisors/retirement_explainer.py"

# A representative Cycle-2a raw_response dict (the authoritative schema --
# see advisors/retirement_recommender.py's build_recommendations
# docstring/persist shape). Test fixture data, not a producer-computed value
# -- safe to assert this exact dict's fields are echoed into the prompt.
_SAMPLE_RECOMMENDATION = {
    "candidate_id": "symphony-weaker-1",
    "sibling_id": "symphony-stronger-1",
    "correlation": 0.81,
    "ci_lower": 0.72,
    "ci_upper": 0.88,
    "n_obs": 180,
    "candidate_composite": 0.31,
    "sibling_composite": 0.67,
    "candidate_metrics": {"annualized_return": 0.04, "sharpe": 0.5},
    "sibling_metrics": {"annualized_return": 0.11, "sharpe": 1.2},
    "uncertainty_gate_passed": True,
    "structural_redundancy_gate_passed": True,
    "stressed_correlation": 0.79,
    "holdings_overlap": 0.62,
    "basis_label": "actual-traded (bot) daily returns",
}


def _make_fake_llm_response(
    text: str = "A concise explanation of why this candidate is redundant.",
):
    content_block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(content=[content_block], model="a-served-model", stop_reason="end_turn")


def _make_fake_client(text: str | None = None):
    # Bug fix (found by ret2-explainer during GREEN, 2026-08-26): `if text` treats
    # an explicit text="" (used by test_empty_llm_response_returns_none) as falsy,
    # silently substituting the default non-empty text instead of producing a
    # genuinely empty response. `is not None` distinguishes "caller omitted text
    # (use the default)" from "caller explicitly asked for an empty string".
    fake_response = (
        _make_fake_llm_response(text=text) if text is not None else _make_fake_llm_response()
    )
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response
    return fake_client


def _read_source() -> str:
    path = REPO_ROOT / _MODULE_REL_PATH
    if not path.exists():
        pytest.fail(f"expected module source not found: {_MODULE_REL_PATH}")
    return path.read_text(encoding="utf-8")


# ===========================================================================
# Module existence + public API contract
# ===========================================================================


def test_module_is_importable():
    import advisors.retirement_explainer as re_mod  # noqa: F401


def test_module_exposes_explain_recommendation_callable():
    import advisors.retirement_explainer as re_mod

    assert callable(getattr(re_mod, "explain_recommendation", None)), (
        "advisors.retirement_explainer must expose explain_recommendation as a callable."
    )


# ===========================================================================
# Success path
# ===========================================================================


def test_returns_the_llm_text_on_success():
    import advisors.retirement_explainer as re_mod

    fixture_text = "This candidate is 81% correlated with its sibling and has weaker Sharpe/CAGR."
    with patch("ai_advisor._build_client", return_value=_make_fake_client(text=fixture_text)):
        result = re_mod.explain_recommendation(_SAMPLE_RECOMMENDATION)

    assert result == fixture_text, (
        f"explain_recommendation must return the LLM's own text verbatim "
        f"(stripped), got {result!r}."
    )


def test_prompt_is_grounded_in_the_recommendations_own_evidence():
    """AC-1: the prompt must be built from the rec's OWN raw_response fields
    (candidate_id/sibling_id/correlation/ci_lower/composites/gate verdicts/
    basis_label) -- never a fabricated or hardcoded evidence set. Verified by
    checking the actual call args passed to client.messages.create carry
    these exact values from the fixture recommendation."""
    import advisors.retirement_explainer as re_mod

    fake_client = _make_fake_client()
    with patch("ai_advisor._build_client", return_value=fake_client):
        re_mod.explain_recommendation(_SAMPLE_RECOMMENDATION)

    assert fake_client.messages.create.called, (
        "explain_recommendation never called messages.create."
    )
    _, call_kwargs = fake_client.messages.create.call_args
    prompt_blob = str(call_kwargs)

    for expected_value in (
        _SAMPLE_RECOMMENDATION["candidate_id"],
        _SAMPLE_RECOMMENDATION["sibling_id"],
        str(_SAMPLE_RECOMMENDATION["correlation"]),
    ):
        assert expected_value in prompt_blob, (
            f"Expected {expected_value!r} (from the rec's own raw_response) to "
            f"appear in the prompt sent to the LLM; call kwargs were: {call_kwargs!r}"
        )


def test_uses_plain_text_messages_create_not_tool_use():
    """AC-1: 'client.messages.create(...) plain text (NOT tool-use)' -- no
    `tools=` kwarg on the call."""
    import advisors.retirement_explainer as re_mod

    fake_client = _make_fake_client()
    with patch("ai_advisor._build_client", return_value=fake_client):
        re_mod.explain_recommendation(_SAMPLE_RECOMMENDATION)

    _, call_kwargs = fake_client.messages.create.call_args
    assert "tools" not in call_kwargs, (
        "explain_recommendation must use plain-text generation (messages.create "
        "with no tools= kwarg), not tool-use structured output."
    )


# ===========================================================================
# D-1 never-raises contract
# ===========================================================================


def test_client_construction_failure_returns_none():
    import advisors.retirement_explainer as re_mod

    with patch("ai_advisor._build_client", side_effect=RuntimeError("no ANTHROPIC_API_KEY")):
        result = re_mod.explain_recommendation(_SAMPLE_RECOMMENDATION)

    assert result is None, "A client-construction failure must degrade to None, never raise."


def test_messages_create_failure_returns_none():
    import advisors.retirement_explainer as re_mod

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = Exception("connection timeout")
    with patch("ai_advisor._build_client", return_value=fake_client):
        result = re_mod.explain_recommendation(_SAMPLE_RECOMMENDATION)

    assert result is None, "An LLM call failure must degrade to None, never raise."


def test_error_log_never_contains_the_raw_exception_message(caplog):
    """D-1 security contract: log only type(exc).__name__, never str(exc) --
    an exception message could carry a secret/path/internal detail."""
    import advisors.retirement_explainer as re_mod

    secret_bearing_message = "auth failed: api_key=sk-ant-abc123-should-never-be-logged"
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = RuntimeError(secret_bearing_message)

    with (
        caplog.at_level(logging.WARNING),
        patch("ai_advisor._build_client", return_value=fake_client),
    ):
        result = re_mod.explain_recommendation(_SAMPLE_RECOMMENDATION)

    assert result is None
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "sk-ant-abc123-should-never-be-logged" not in joined, (
        f"The raw exception message leaked into logs -- D-1 requires "
        f"type(exc).__name__ only. Log records: {joined!r}"
    )


def test_empty_llm_response_returns_none():
    import advisors.retirement_explainer as re_mod

    fake_client = _make_fake_client(text="")
    with patch("ai_advisor._build_client", return_value=fake_client):
        result = re_mod.explain_recommendation(_SAMPLE_RECOMMENDATION)

    assert result is None, "An empty LLM text response must degrade to None, never an empty string."


def test_malformed_recommendation_missing_optional_keys_never_raises():
    """D-1 defensive parsing: a recommendation dict missing some of the
    documented raw_response keys must not raise a KeyError -- degrade to
    either a best-effort string or None, never crash."""
    import advisors.retirement_explainer as re_mod

    sparse_rec = {"candidate_id": "sym-x"}
    fake_client = _make_fake_client()
    with patch("ai_advisor._build_client", return_value=fake_client):
        try:
            re_mod.explain_recommendation(sparse_rec)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"explain_recommendation raised on a sparse recommendation dict "
                f"missing optional keys: {type(exc).__name__}: {exc}"
            )


# ===========================================================================
# Read-only / no-write-path guards (redundant with the AC-8 security file --
# kept here as a fast local guard co-located with the module's own tests)
# ===========================================================================


def test_never_writes_advisor_observations():
    import advisors.retirement_explainer as re_mod

    fake_client = _make_fake_client()
    with (
        patch("ai_advisor._build_client", return_value=fake_client),
        patch("database.insert_advisor_observation") as mock_insert,
    ):
        re_mod.explain_recommendation(_SAMPLE_RECOMMENDATION)

    mock_insert.assert_not_called()


def test_never_writes_retirement_decisions():
    """The explainer must never write approval status itself -- that is
    exclusively the approve/reject routes' job (AC-5)."""
    import advisors.retirement_explainer as re_mod

    fake_client = _make_fake_client()
    with (
        patch("ai_advisor._build_client", return_value=fake_client),
        patch("database.upsert_retirement_decision") as mock_upsert,
    ):
        re_mod.explain_recommendation(_SAMPLE_RECOMMENDATION)

    mock_upsert.assert_not_called()


def test_never_writes_bot_state():
    import advisors.retirement_explainer as re_mod

    fake_client = _make_fake_client()
    with (
        patch("ai_advisor._build_client", return_value=fake_client),
        patch("database.save_state") as mock_save,
    ):
        re_mod.explain_recommendation(_SAMPLE_RECOMMENDATION)

    mock_save.assert_not_called()


def test_source_does_not_reference_composer_draft_client_or_alpha_bot_execution():
    source = _read_source()
    assert "composer_draft_client" not in source
    assert "alpha_bot_execution" not in source


# ===========================================================================
# Real client over the HTTP fake: the request as sent, and the reply as parsed
# ===========================================================================

EXPLAINER_LOGGER = "advisors.retirement_explainer"
# The old budget (512) held no thinking: the request must leave room for thinking plus a
# 2-4 sentence answer. Request settings are the per-site table of the approach.
MIN_EXPLAINER_MAX_TOKENS = 4000
MIN_EXPLAINER_TIMEOUT_SECONDS = 120.0
THINKING_THEN_TEXT = "thinking_text.json"


def _explain(fake, recommendation=None):
    import advisors.retirement_explainer as re_mod

    with served_by(fake):
        return re_mod.explain_recommendation(recommendation or _SAMPLE_RECOMMENDATION)


def _warning_texts(caplog, logger_name=EXPLAINER_LOGGER) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == logger_name and r.levelno >= logging.WARNING
    ]


class TestExplainerRequestOnTheWire:
    def test_request_names_the_newest_fable_line_model(self, fake):
        fake.queue_message(message_body(THINKING_THEN_TEXT))

        _explain(fake)

        (request,) = fake.message_requests
        assert request.body["model"] == newest_id("fable"), (
            "the explainer must resolve the suggestion family's newest active model"
        )
        assert fake.misuse == []

    def test_override_request_names_exactly_that_id_through_the_request_helper(
        self, fake, monkeypatch
    ):
        override = older_id("fable")
        assert override != newest_id("fable")
        monkeypatch.setenv("ADVISOR_SUGGESTION_MODEL", override)
        monkeypatch.setenv("ADVISOR_SYNTHESIS_MODEL", older_id("opus"))
        fake.queue_message(message_body(THINKING_THEN_TEXT))

        result = _explain(fake)

        (request,) = fake.message_requests
        assert request.body["model"] == override, "the suggestion override is sent verbatim"
        assert fake.listing_requests == [], "an override is never validated against the Models API"
        assert request.body["output_config"] == {"effort": "high"}, (
            "an override request is still the request helper's request (explicit effort)"
        )
        assert result == text_of(message_body(THINKING_THEN_TEXT)).strip()

    def test_request_body_is_the_sites_prompt_with_thinking_model_settings(self, fake):
        import advisors.retirement_explainer as re_mod

        fake.queue_message(message_body(THINKING_THEN_TEXT))

        _explain(fake)

        (request,) = fake.message_requests
        body = request.body
        assert body["output_config"] == {"effort": "high"}
        assert body["max_tokens"] >= MIN_EXPLAINER_MAX_TOKENS
        assert body["fallbacks"] == "default"
        assert request.headers["anthropic-beta"] == FALLBACK_BETA
        assert request.timeout["read"] >= MIN_EXPLAINER_TIMEOUT_SECONDS
        assert FORBIDDEN_PARAMETERS.isdisjoint(body)
        assert "tools" not in body and "tool_choice" not in body, "plain text, not tool use"
        assert body["system"] == re_mod._EXPLAIN_SYSTEM_PROMPT, "prompt text is unchanged"
        assert body["messages"] == re_mod._build_explain_messages(_SAMPLE_RECOMMENDATION), (
            "the evidence message is unchanged and ends on the user turn (no assistant prefill)"
        )


class TestExplainerReadsOnlyTheTextBlock:
    def test_explanation_after_a_thinking_block_is_the_text_block_only(self, fake):
        body = message_body(THINKING_THEN_TEXT)
        assert [b["type"] for b in body["content"]] == ["thinking", "text"]
        fake.queue_message(body)

        result = _explain(fake)

        assert result == text_of(body).strip()
        assert body["content"][0]["signature"] not in result
        assert fake.message_requests[0].body["max_tokens"] >= MIN_EXPLAINER_MAX_TOKENS, (
            "the request must leave room for the thinking block this reply carries"
        )

    def test_a_fallback_served_reply_yields_the_serving_models_text_and_the_request_opted_in(
        self, fake
    ):
        body = message_body("fallback_served_text.json")
        assert [b["type"] for b in body["content"]] == ["fallback", "thinking", "text"]
        fake.queue_message(body)

        result = _explain(fake)

        assert result == text_of(body).strip()
        (request,) = fake.message_requests
        assert request.body["fallbacks"] == "default", (
            "a fallback model can only answer a request that asked for fallback"
        )
        assert request.headers["anthropic-beta"] == FALLBACK_BETA

    @pytest.mark.parametrize("block_type", ["fallback", "thinking", "redacted_thinking"])
    def test_no_other_block_type_contributes_text_even_when_it_carries_a_text_attribute(
        self, block_type
    ):
        import advisors.retirement_explainer as re_mod

        leaked = "TEXT-CARRIED-BY-A-NON-TEXT-BLOCK"
        answer = "The candidate is redundant with its sibling."
        client = MagicMock()
        client.messages.create.return_value = SimpleNamespace(
            content=[
                SimpleNamespace(type=block_type, text=leaked),
                SimpleNamespace(type="text", text=answer),
            ],
            model="a-served-model",
            stop_reason="end_turn",
        )

        with patch("ai_advisor._build_client", return_value=client):
            result = re_mod.explain_recommendation(_SAMPLE_RECOMMENDATION)

        assert result == answer, (
            f"a {block_type} block's text attribute must never enter the explanation"
        )


class TestExplainerReturnsNothingOnAnyReason:
    @pytest.mark.parametrize("name", ["max_tokens.json", "max_tokens_in_thinking.json"])
    def test_a_truncated_reply_stores_no_explanation_and_the_log_names_max_tokens(
        self, fake, caplog, name
    ):
        body = message_body(name)
        fake.queue_message(body)

        with caplog.at_level(logging.WARNING):
            result = _explain(fake)

        assert result is None, "a truncated reply's partial text must not become the explanation"
        logged = _warning_texts(caplog)
        assert any("max_tokens" in text for text in logged), logged
        partial = text_of(body)
        assert not partial or partial[:40] not in " ".join(logged)
        assert len(fake.message_requests) == 1, "the explainer never retries a truncated reply"

    @pytest.mark.parametrize(
        "name",
        [
            "refusal_reasoning_extraction.json",
            "refusal_null_category.json",
            "refusal_after_partial_text.json",
        ],
    )
    def test_a_refused_reply_stores_no_explanation_and_logs_the_reason_text_only(
        self, fake, caplog, name
    ):
        body = message_body(name)
        fake.queue_message(body)
        expected = refusal_reason_text(name)

        with caplog.at_level(logging.WARNING):
            result = _explain(fake)

        assert result is None, "no partial output survives a refusal"
        logged = _warning_texts(caplog)
        assert any(expected in text for text in logged), (expected, logged)
        if expected == "refusal":
            assert not any("None" in text for text in logged), "a null category is not rendered"
        partial = text_of(body)
        assert not partial or partial[:40] not in " ".join(logged)
        assert len(fake.message_requests) == 1

    @pytest.mark.parametrize(
        ("error_fixture", "reason_code"),
        [
            ("404_not_found.json", "override_model_not_found"),
            ("400_invalid_request.json", "override_request_rejected"),
        ],
    )
    def test_a_rejected_override_stores_no_explanation_and_logs_the_reason_with_the_variable(
        self, fake, caplog, monkeypatch, error_fixture, reason_code
    ):
        monkeypatch.setenv("ADVISOR_SUGGESTION_MODEL", older_id("fable"))
        fake.queue_message(error_outcome(error_fixture))

        with caplog.at_level(logging.WARNING):
            result = _explain(fake)

        assert result is None
        logged = _warning_texts(caplog)
        assert any(f"{reason_code}: ADVISOR_SUGGESTION_MODEL" in text for text in logged), logged
        assert len(fake.message_requests) == 1, "an override is never retried"

    def test_an_unresolved_family_sends_nothing_returns_none_and_logs_model_unavailable(
        self, fake, caplog, unresolved_models
    ):
        fake.queue_listing_outcome(error_outcome("503_unavailable.json"))

        with caplog.at_level(logging.WARNING):
            result = _explain(fake)

        assert result is None
        assert fake.message_requests == []
        assert any("model_unavailable" in text for text in _warning_texts(caplog))
