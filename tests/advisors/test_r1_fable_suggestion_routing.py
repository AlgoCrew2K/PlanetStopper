"""RED tests — AC-16 (operator directive 2026-07-13: "anything it suggests
should be using fable"): every suggestion-producing LLM call routes through
Fable (claude-fable-5) via a single named, env-overridable accessor, not a
hardcoded claude-opus-4-8 literal.

Two call sites named by the plan, both confirmed hardcoded pre-fix:
  (a) ai_advisor.request_suggestions -> client.messages.parse(model=
      os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-8"), ...)
      (ai_advisor.py:1760) — note this reads ADVISOR_SYNTHESIS_MODEL, a
      DIFFERENT env var already used for the Market Prism synthesis model
      (resolve_advisor_model(), ai_advisor.py:73-79) — AC-16 wants a
      SEPARATE suggestion-model accessor so flipping the nightly Prism
      synthesis model can never accidentally also flip config-suggestion
      routing, and vice versa.
  (b) advisors.build_plan_generator.generate_build_plans -> client.messages.
      create(model="claude-opus-4-8", ...) (build_plan_generator.py:1079) —
      a bare literal, no env var read at all today.

TEST TECHNIQUE (per the plan's own AC-16 text: "accessor-monkeypatch pattern
... assert the resolved model reaches the SDK call"): monkeypatch the
ADVISOR_SUGGESTION_MODEL env var (the plan's own example name — see
Questions-for-User in tdd-handoff.md: the exact accessor name/location is an
assumption pending r1-engine/review confirmation) and assert the model
actually reaching the mocked SDK call reflects it — env-var-level, not
accessor-function-level, so these tests don't lock a specific accessor
function name/location the plan didn't pin.

WHAT IS MOCKED: both modules' `_build_client()` factory seam only (the
established pattern in both test suites — tests/ai_advisor/test_ai_advisor.py
and tests/advisors/test_build_plan_generator.py already patch this exact
seam). The real request_suggestions / generate_build_plans logic runs.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# (a) ai_advisor.request_suggestions
# ---------------------------------------------------------------------------


def _make_fake_parse_client():
    """Minimal fake anthropic client for request_suggestions — mirrors
    tests/ai_advisor/test_ai_advisor.py's _make_fake_anthropic_client, trimmed
    to only what AC-16's model-routing assertion needs (we never inspect the
    parsed content, only the model= kwarg reaching .messages.parse)."""
    import ai_advisor

    client = MagicMock()
    empty_response = ai_advisor.ConfigSuggestionsResponse(suggestions=[])
    block = MagicMock()
    block.parsed_output = empty_response
    sdk_response = MagicMock()
    sdk_response.content = [block]
    client.messages.parse.return_value = sdk_response
    return client


def _minimal_symphony_context() -> dict:
    """A minimal context dict for request_suggestions. The exact shape does
    not matter for AC-16 (which targets the model= kwarg only) as long as
    _build_messages(context) does not raise — request_suggestions' D-1
    contract means even a malformed context degrades to (None, error) rather
    than crashing, so a minimal dict is safe regardless."""
    return {}


def test_ac16_request_suggestions_defaults_to_fable_not_opus(monkeypatch):
    """MUST FAIL pre-fix: today the model is always claude-opus-4-8 regardless
    of any env var (ADVISOR_SYNTHESIS_MODEL only affects Market Prism
    synthesis, a different call site) — request_suggestions has no
    Fable-routing accessor at all yet."""
    import ai_advisor

    monkeypatch.delenv("ADVISOR_SUGGESTION_MODEL", raising=False)
    fake_client = _make_fake_parse_client()

    with patch.object(ai_advisor, "_build_client", return_value=fake_client, create=True):
        ai_advisor.request_suggestions(_minimal_symphony_context())

    assert fake_client.messages.parse.called, "request_suggestions must call client.messages.parse"
    resolved_model = fake_client.messages.parse.call_args.kwargs.get("model")
    assert resolved_model == "claude-fable-5", (
        f"AC-16 GAP: request_suggestions's default suggestion model is "
        f"{resolved_model!r}, expected 'claude-fable-5' (the operator "
        f"directive default) when no override env var is set."
    )


def test_ac16_request_suggestions_honors_suggestion_model_env_override(monkeypatch):
    """Env-var-level proof the model is resolved at CALL TIME from a named
    accessor, not hardcoded — monkeypatching a distinctive marker value and
    observing it reach the SDK call proves real wiring, not a coincidental
    default match."""
    import ai_advisor

    monkeypatch.setenv("ADVISOR_SUGGESTION_MODEL", "test-marker-suggestion-model")
    fake_client = _make_fake_parse_client()

    with patch.object(ai_advisor, "_build_client", return_value=fake_client, create=True):
        ai_advisor.request_suggestions(_minimal_symphony_context())

    resolved_model = fake_client.messages.parse.call_args.kwargs.get("model")
    assert resolved_model == "test-marker-suggestion-model", (
        f"AC-16 GAP: setting ADVISOR_SUGGESTION_MODEL did not reach the SDK "
        f"call — got model={resolved_model!r}. The accessor must read the env "
        f"var at call time (mirrors resolve_advisor_model()'s pattern)."
    )


def test_ac16_request_suggestions_suggestion_model_independent_of_synthesis_model(monkeypatch):
    """The two accessors must be independently overridable — setting
    ADVISOR_SYNTHESIS_MODEL (the pre-existing Market Prism accessor) must NOT
    change request_suggestions' resolved model, and vice versa is covered by
    the override test above. This guards against a lazy fix that reuses
    ADVISOR_SYNTHESIS_MODEL for both purposes, which would silently couple
    nightly Prism model changes to config-suggestion routing."""
    import ai_advisor

    monkeypatch.delenv("ADVISOR_SUGGESTION_MODEL", raising=False)
    monkeypatch.setenv("ADVISOR_SYNTHESIS_MODEL", "test-marker-synthesis-only")
    fake_client = _make_fake_parse_client()

    with patch.object(ai_advisor, "_build_client", return_value=fake_client, create=True):
        ai_advisor.request_suggestions(_minimal_symphony_context())

    resolved_model = fake_client.messages.parse.call_args.kwargs.get("model")
    assert resolved_model != "test-marker-synthesis-only", (
        "AC-16 GAP: request_suggestions' resolved model was driven by "
        "ADVISOR_SYNTHESIS_MODEL — the suggestion-model accessor must be "
        "independent of the Market Prism synthesis-model accessor."
    )


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
        bpg.generate_build_plans(bpg.Objective.diversify, n_plans=1, membership_set=frozenset({"AAA"}))

    assert fake_client.messages.create.called, "generate_build_plans must call client.messages.create"
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
        bpg.generate_build_plans(bpg.Objective.diversify, n_plans=1, membership_set=frozenset({"AAA"}))

    resolved_model = fake_client.messages.create.call_args.kwargs.get("model")
    assert resolved_model == "test-marker-suggestion-model", (
        f"AC-16 GAP: setting ADVISOR_SUGGESTION_MODEL did not reach "
        f"build_plan_generator's SDK call — got model={resolved_model!r}."
    )


def test_ac16_both_suggestion_call_sites_share_the_same_accessor_default(monkeypatch):
    """Both call sites must resolve to the IDENTICAL default (claude-fable-5)
    and the IDENTICAL override behavior — a fix that wires one site to a
    genuine shared accessor and the other to an independent hardcoded
    'claude-fable-5' string would pass every test above individually but
    silently diverge the moment the operator overrides the env var on only
    one path in production. This test proves both paths respond to the SAME
    env var, not just the same default value."""
    import advisors.build_plan_generator as bpg
    import ai_advisor

    monkeypatch.setenv("ADVISOR_SUGGESTION_MODEL", "test-marker-shared")

    fake_parse_client = _make_fake_parse_client()
    with patch.object(ai_advisor, "_build_client", return_value=fake_parse_client, create=True):
        ai_advisor.request_suggestions(_minimal_symphony_context())
    request_suggestions_model = fake_parse_client.messages.parse.call_args.kwargs.get("model")

    fake_create_client = _client_returning_empty_plans()
    with patch.object(bpg, "_build_client", return_value=fake_create_client):
        bpg.generate_build_plans(bpg.Objective.diversify, n_plans=1, membership_set=frozenset({"AAA"}))
    build_plan_generator_model = fake_create_client.messages.create.call_args.kwargs.get("model")

    assert request_suggestions_model == "test-marker-shared", (
        f"ai_advisor.request_suggestions did not honor the shared override; got {request_suggestions_model!r}"
    )
    assert build_plan_generator_model == "test-marker-shared", (
        f"build_plan_generator.generate_build_plans did not honor the shared override; got {build_plan_generator_model!r}"
    )
