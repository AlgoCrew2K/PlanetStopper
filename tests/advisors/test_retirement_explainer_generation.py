"""RED tests -- advisors/retirement_explainer.py (AC-1, AC-9).

feature-plans/retirement-approval-lifecycle.md AC-1: explain_recommendation(
recommendation: dict) -> str | None. Uses ai_advisor._build_client() +
model_config.get_advisor_suggestion_model() + client.messages.create(...)
plain text (NOT tool-use), mirroring advisor_chat.explain_artifact. D-1
never-raises: any failure returns None (honest empty), log only
type(exc).__name__, never str(exc). Read-only -- no DB write, no trade/exec
primitive.

Mocking idiom copied verbatim from tests/ai_advisor/test_chat_engine.py's own
_make_fake_client/_make_fake_llm_response pattern (SimpleNamespace content
blocks with a .text attribute, patch "ai_advisor._build_client") -- the
established convention for this exact LLM seam in this codebase. NO live
Anthropic call anywhere in this file.

Expected state: RED until advisors/retirement_explainer.py exists.
"""

from __future__ import annotations

import logging
import pathlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

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
    content_block = SimpleNamespace(text=text)
    return SimpleNamespace(content=[content_block], model="claude-fable-5", stop_reason="end_turn")


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


def test_uses_the_advisor_suggestion_model_accessor():
    """AC-1: model_config.get_advisor_suggestion_model() (fable-5, the cheap
    suggestion-family knob) -- never a hardcoded model string, never the
    opus synthesis knob."""
    import advisors.retirement_explainer as re_mod

    fake_client = _make_fake_client()
    sentinel_model = "sentinel-explainer-model-xyz"
    with (
        patch("ai_advisor._build_client", return_value=fake_client),
        patch("model_config.get_advisor_suggestion_model", return_value=sentinel_model),
    ):
        re_mod.explain_recommendation(_SAMPLE_RECOMMENDATION)

    _, call_kwargs = fake_client.messages.create.call_args
    assert call_kwargs.get("model") == sentinel_model, (
        f"explain_recommendation must source its model from "
        f"model_config.get_advisor_suggestion_model(); expected {sentinel_model!r}, "
        f"got {call_kwargs.get('model')!r}."
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
