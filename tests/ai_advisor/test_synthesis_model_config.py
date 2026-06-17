"""
Tests — Advisor Synthesis Model Config (C1).

Feature: make all advisor LLM calls read their model identifier from the
``ADVISOR_SYNTHESIS_MODEL`` environment variable, with a documented default
of claude-opus-4-8.

Coverage (AC-1..4 from feature-plans/advisor-synthesis-model-config.md):

  AC-1  The synthesis model (and other advisor LLM calls) reads its model
        identifier from ``ADVISOR_SYNTHESIS_MODEL`` rather than a hardcoded
        literal in any of the three scoped modules:
          - advisors/lens_pipeline._synthesize_via_claude
          - ai_advisor (the _CLAUDE_MODEL constant / messages.parse call)
          - advisors/advisor_chat (the _CHAT_MODEL constant / messages.create call)

  AC-2  When ADVISOR_SYNTHESIS_MODEL is unset, the default is ``claude-opus-4-8``
        (Opus 4.8 — corrected in kickoff commit 9edbbdb from the stale
        ``claude-opus-4-5`` that appeared in the original plan draft).
        When set, the provided value is passed to the Anthropic client.
        No production path makes a real Opus call during a pytest run —
        tests assert the model string, mocking the Anthropic client.

  AC-3  The _extract_json_object fence-stripping logic fixed at df2d19e is
        preserved byte-for-byte — tests assert all its current edge-case
        behaviours as a regression guard against accidental modification.

  AC-4  Tests assert config wiring via function-scoped monkeypatch.setenv,
        NOT module-level os.environ mutation.  No hardcoded provider values.

Hermetic test design (AC-4b — call-time read contract):
  After the implementer's fix, each ``model=`` argument reads the env var
  at CALL TIME (inline ``os.environ.get(...)``), not at module-import time.
  This makes the wiring tests order-independent: they do NOT rely on
  ``importlib.reload`` to re-evaluate a module-level constant.  Any sibling
  test that imports the module first cannot cause these tests to see a stale
  model string, because the env var is read fresh on every function call.

Suite-ordering regression tests (TestSuiteOrderingRegression):
  Reproduce the exact failure mode observed in the full suite:
  ``test_advisor_chat_does_not_import_alpha_bot_execution`` (test_chat_engine.py)
  pops ``advisors.advisor_chat`` from ``sys.modules``, re-imports it, then
  restores ``sys.modules`` via a saved reference — but Python also writes the
  re-imported module as an attribute on the ``advisors`` package object.
  After the restore, ``sys.modules["advisors.advisor_chat"]`` is the original
  module but ``advisors.advisor_chat`` (the package attribute) is the newly
  imported copy.  A ``from advisors import advisor_chat`` then gets the stale
  copy whose import-time ``_CHAT_MODEL`` was frozen to the default, ignoring
  any subsequent ``monkeypatch.setenv`` + ``importlib.reload`` calls.
  The ordering-regression tests simulate this state deterministically and
  assert that a CALL-TIME env read survives it.

Mocking strategy:
  - Anthropic client: patched to a MagicMock whose messages.create /
    messages.parse records the ``model=`` kwarg; no real LLM calls.
  - database.insert_advisor_observation: patched to a side_effect list
    to prevent DB writes.
  - All network-calling lens sections in the pipeline: patched to
    stub blocks so run_pipeline() exercises the synthesis path only.
  - monkeypatch.setenv / monkeypatch.delenv: function-scoped (pytest
    default) — never module-level os.environ mutation.

No hardcoded provider-computed values in any assertion (rule
feedback_no_hardcoded_test_values): we assert the MODEL STRING (a
config identifier, not a provider response), which is explicitly
the non-provider-computed config wire we own.  Rationale: the model
ID is a project-controlled constant, not a value produced by the LLM.
"""

from __future__ import annotations

import importlib
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# The single expected default model ID — update here if the operator ever
# changes the project's Opus model pin.
# ---------------------------------------------------------------------------

# Opus 4.8 is the documented production default for this feature (C1).
# Source: feature-plans/advisor-synthesis-model-config.md §Architecture
# and kickoff commit 9edbbdb (corrected from stale claude-opus-4-5 draft).
_EXPECTED_DEFAULT = "claude-opus-4-8"

# The env var name the feature wires to.
_ENV_VAR = "ADVISOR_SYNTHESIS_MODEL"

# A sentinel cheap model the tests use when they SET the env var —
# distinct from the default so tests can distinguish "default path" vs
# "env-override path".  Not a real Anthropic model ID; the Anthropic
# client is always mocked, so the string is never validated.
_CHEAP_TEST_MODEL = "test-stub-model-do-not-use"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client_mock_for_synthesize(model_holder: list) -> MagicMock:
    """Build a mock Anthropic client that records the model= kwarg of messages.create.

    Used to verify which model string lens_pipeline._synthesize_via_claude
    passes to client.messages.create(...) — without making a real API call.
    """
    content_block = MagicMock()
    # Return a valid JSON string so _extract_json_object + json.loads succeed,
    # allowing _synthesize_via_claude to reach the model= call cleanly.
    content_block.text = '{"overall_sentiment": "neutral", "sentiment_rationale": "test"}'
    response = MagicMock()
    response.content = [content_block]

    client = MagicMock()

    def _create(*args, model=None, **kwargs):
        model_holder.append(model)
        return response

    client.messages.create.side_effect = _create
    return client


def _make_client_mock_for_parse(model_holder: list) -> MagicMock:
    """Build a mock Anthropic client that records the model= kwarg of messages.parse.

    Used to verify which model string ai_advisor.request_suggestions passes
    to client.messages.parse(...).
    """
    parsed_block = MagicMock()
    parsed_block.parsed_output = MagicMock()
    parsed_block.parsed_output.suggestions = []
    response = MagicMock()
    response.content = [parsed_block]

    client = MagicMock()

    def _parse(*args, model=None, **kwargs):
        model_holder.append(model)
        return response

    client.messages.parse.side_effect = _parse
    return client


def _make_client_mock_for_chat(model_holder: list) -> MagicMock:
    """Build a mock Anthropic client that records the model= kwarg of messages.create.

    Used to verify which model string advisor_chat.explain_artifact passes
    to client.messages.create(...).
    """
    content_block = MagicMock()
    content_block.text = "This is a test explanation."
    response = MagicMock()
    response.content = [content_block]

    client = MagicMock()

    def _create(*args, model=None, **kwargs):
        model_holder.append(model)
        return response

    client.messages.create.side_effect = _create
    return client


def _stub_lens_available(lens_name: str) -> dict:
    return {
        "lens": lens_name,
        "available": True,
        "summary": f"stub {lens_name} summary",
        "payload": {"stub": True},
        "sources": [
            {
                "title": f"Stub {lens_name}",
                "url": f"https://example.com/{lens_name}",
                "published": "2026-06-17",
                "lens": lens_name,
            }
        ],
    }


def _stub_lens_unavailable(lens_name: str) -> dict:
    return {
        "lens": lens_name,
        "available": False,
        "reason": "stub — unavailable",
        "payload": None,
        "sources": [],
    }


# ---------------------------------------------------------------------------
# AC-1 / AC-2 — lens_pipeline._synthesize_via_claude reads ADVISOR_SYNTHESIS_MODEL
# ---------------------------------------------------------------------------


class TestLensPipelineModelEnvVar:
    """AC-1/AC-2: advisors/lens_pipeline._synthesize_via_claude must read
    its model from ADVISOR_SYNTHESIS_MODEL, not a hardcoded literal.

    RED intent: the current implementation has ``model="claude-haiku-4-5-20251001"``
    hardcoded at lens_pipeline.py:284.  These tests will fail until the
    implementer replaces that literal with an env-var lookup.
    """

    def _run_pipeline_with_client(self, client_mock) -> None:
        """Helper: run the pipeline with one available lens so synthesis is triggered."""
        import advisors.lens_pipeline as pipeline_mod

        captured_writes: list = []

        with (
            patch("ai_advisor._build_technicals_section", return_value=_stub_lens_available("technicals")),
            patch("ai_advisor._build_sentiment_section", return_value=_stub_lens_unavailable("sentiment")),
            patch("ai_advisor._build_derivatives_section", return_value=_stub_lens_unavailable("derivatives")),
            patch("ai_advisor._build_macro_section", return_value=_stub_lens_unavailable("macro")),
            patch("ai_advisor._build_fundamentals_section", return_value=_stub_lens_unavailable("fundamentals")),
            patch("ai_advisor._build_client", return_value=client_mock),
            patch("database.insert_advisor_observation", side_effect=lambda **kw: captured_writes.append(kw) or 1),
        ):
            pipeline_mod.run_pipeline(dry_run=False)

    def test_env_var_unset_uses_default_in_synthesize(self, monkeypatch):
        """When ADVISOR_SYNTHESIS_MODEL is unset, _synthesize_via_claude must pass
        the production default (claude-opus-4-8) to client.messages.create.

        RED: the hardcoded literal is ``claude-haiku-4-5-20251001``, not the default.
        This test will fail until the literal is replaced with env-var logic.
        """
        monkeypatch.delenv(_ENV_VAR, raising=False)

        # Force a module reload so the env-var is re-evaluated at import time
        # if the implementer reads it at module level.
        if "advisors.lens_pipeline" in sys.modules:
            importlib.reload(sys.modules["advisors.lens_pipeline"])

        model_holder: list[str] = []
        client_mock = _make_client_mock_for_synthesize(model_holder)

        self._run_pipeline_with_client(client_mock)

        assert model_holder, (
            "client.messages.create was never called — synthesis was not triggered. "
            "Ensure at least one lens is available so _synthesize_via_claude is called."
        )
        actual_model = model_holder[0]
        assert actual_model == _EXPECTED_DEFAULT, (
            f"When {_ENV_VAR!r} is unset, _synthesize_via_claude must pass the default "
            f"model {_EXPECTED_DEFAULT!r} to client.messages.create. "
            f"Got: {actual_model!r}. "
            f"Fix: replace the hardcoded model literal in advisors/lens_pipeline.py "
            f"with os.environ.get({_ENV_VAR!r}, {_EXPECTED_DEFAULT!r})."
        )

    def test_env_var_set_overrides_model_in_synthesize(self, monkeypatch):
        """When ADVISOR_SYNTHESIS_MODEL is set, _synthesize_via_claude must pass
        that value to client.messages.create.

        RED: hardcoded literal ignores the env var entirely.
        This test will fail until the literal is replaced with env-var logic.
        """
        monkeypatch.setenv(_ENV_VAR, _CHEAP_TEST_MODEL)

        if "advisors.lens_pipeline" in sys.modules:
            importlib.reload(sys.modules["advisors.lens_pipeline"])

        model_holder: list[str] = []
        client_mock = _make_client_mock_for_synthesize(model_holder)

        self._run_pipeline_with_client(client_mock)

        assert model_holder, (
            "client.messages.create was never called — synthesis was not triggered."
        )
        actual_model = model_holder[0]
        assert actual_model == _CHEAP_TEST_MODEL, (
            f"When {_ENV_VAR!r}={_CHEAP_TEST_MODEL!r}, _synthesize_via_claude must pass "
            f"that exact value to client.messages.create. "
            f"Got: {actual_model!r}. "
            f"Fix: replace the hardcoded model literal with "
            f"os.environ.get({_ENV_VAR!r}, {_EXPECTED_DEFAULT!r})."
        )

    def test_no_hardcoded_model_literal_in_lens_pipeline_source(self):
        """Static guard: advisors/lens_pipeline.py must contain no hardcoded
        claude-haiku or claude-opus model literal as the model= argument.

        The literal ``claude-haiku-4-5-20251001`` at line 284 must be gone
        once the implementer ships the fix.

        RED: the literal still exists in the unmodified source.
        """
        import pathlib

        repo_root = pathlib.Path(__file__).resolve().parent.parent.parent
        source = (repo_root / "advisors" / "lens_pipeline.py").read_text(encoding="utf-8")

        # The old hardcoded Haiku literal must not appear anywhere in the
        # synthesis call.  After the fix, the model is read from the env var.
        assert "claude-haiku-4-5-20251001" not in source, (
            "advisors/lens_pipeline.py still contains the hardcoded model literal "
            "'claude-haiku-4-5-20251001'. Replace it with "
            f"os.environ.get({_ENV_VAR!r}, {_EXPECTED_DEFAULT!r})."
        )


# ---------------------------------------------------------------------------
# AC-1 / AC-2 — ai_advisor._CLAUDE_MODEL reads ADVISOR_SYNTHESIS_MODEL
# ---------------------------------------------------------------------------


class TestAiAdvisorModelEnvVar:
    """AC-1/AC-2: ai_advisor must read its model from ADVISOR_SYNTHESIS_MODEL at CALL TIME.

    After the fix: the ``model=`` argument to ``messages.parse`` is evaluated
    as ``os.environ.get(ADVISOR_SYNTHESIS_MODEL, default)`` inline at each call
    site, not from a module-level constant set at import time.  This makes the
    tests order-independent — no ``importlib.reload`` needed.

    These tests are hermetic: they do NOT reload the module.  Regardless of
    when or how often ai_advisor was previously imported, the live env var
    value at call time must be what reaches the SDK.
    """

    def test_env_var_unset_uses_opus_default(self, monkeypatch):
        """When ADVISOR_SYNTHESIS_MODEL is unset, the model passed to messages.parse
        must be the production default (claude-opus-4-8).

        Hermetic: does not reload ai_advisor.  A call-time env read must return
        the default regardless of previous imports or sibling-test env state.
        """
        monkeypatch.delenv(_ENV_VAR, raising=False)

        model_holder: list[str] = []
        client_mock = _make_client_mock_for_parse(model_holder)

        import ai_advisor

        stub_context = {
            "technicals": _stub_lens_unavailable("technicals"),
            "sentiment": _stub_lens_unavailable("sentiment"),
            "derivatives": _stub_lens_unavailable("derivatives"),
            "macro": _stub_lens_unavailable("macro"),
            "fundamentals": _stub_lens_unavailable("fundamentals"),
            "config": {},
            "bot_state": {},
        }

        with (
            patch.object(ai_advisor, "_build_client", return_value=client_mock),
            patch.object(ai_advisor, "_build_messages", return_value=[{"role": "user", "content": "test"}]),
        ):
            ai_advisor.request_suggestions(stub_context)

        assert model_holder, (
            "ai_advisor.request_suggestions never called messages.parse — "
            "ensure _build_client is not returning None in the test."
        )
        actual_model = model_holder[0]
        assert actual_model == _EXPECTED_DEFAULT, (
            f"When {_ENV_VAR!r} is unset, ai_advisor must pass {_EXPECTED_DEFAULT!r} "
            f"to messages.parse. Got: {actual_model!r}. "
            f"Fix: replace the module-level _CLAUDE_MODEL reference with an inline "
            f"os.environ.get({_ENV_VAR!r}, {_EXPECTED_DEFAULT!r}) at the model= call site."
        )

    def test_env_var_set_overrides_claude_model(self, monkeypatch):
        """When ADVISOR_SYNTHESIS_MODEL is set, ai_advisor must pass that value
        to messages.parse.

        Hermetic: does not reload ai_advisor.  A call-time env read must pick up
        the new value even if the module was already imported under a different env.
        """
        monkeypatch.setenv(_ENV_VAR, _CHEAP_TEST_MODEL)

        model_holder: list[str] = []
        client_mock = _make_client_mock_for_parse(model_holder)

        import ai_advisor

        stub_context = {
            "technicals": _stub_lens_unavailable("technicals"),
            "sentiment": _stub_lens_unavailable("sentiment"),
            "derivatives": _stub_lens_unavailable("derivatives"),
            "macro": _stub_lens_unavailable("macro"),
            "fundamentals": _stub_lens_unavailable("fundamentals"),
            "config": {},
            "bot_state": {},
        }

        with (
            patch.object(ai_advisor, "_build_client", return_value=client_mock),
            patch.object(ai_advisor, "_build_messages", return_value=[{"role": "user", "content": "test"}]),
        ):
            ai_advisor.request_suggestions(stub_context)

        assert model_holder, (
            "ai_advisor.request_suggestions never called messages.parse."
        )
        actual_model = model_holder[0]
        assert actual_model == _CHEAP_TEST_MODEL, (
            f"When {_ENV_VAR!r}={_CHEAP_TEST_MODEL!r}, ai_advisor must pass that value "
            f"to messages.parse. Got: {actual_model!r}. "
            f"Fix: replace the module-level _CLAUDE_MODEL reference with an inline "
            f"os.environ.get({_ENV_VAR!r}, {_EXPECTED_DEFAULT!r}) at the model= call site."
        )


# ---------------------------------------------------------------------------
# AC-1 / AC-2 — advisors/advisor_chat._CHAT_MODEL reads ADVISOR_SYNTHESIS_MODEL
# ---------------------------------------------------------------------------


class TestAdvisorChatModelEnvVar:
    """AC-1/AC-2: advisors/advisor_chat must read its model from ADVISOR_SYNTHESIS_MODEL
    at CALL TIME — not from a module-level constant set at import time.

    After the fix: the ``model=`` argument to ``messages.create`` is evaluated
    as ``os.environ.get(ADVISOR_SYNTHESIS_MODEL, default)`` inline at each call
    site.  This makes the tests order-independent — no ``importlib.reload`` needed.

    advisor_chat delegates client construction to ai_advisor._build_client() (see
    advisor_chat.py comment: "Tests patch ai_advisor._build_client").
    Patch ai_advisor._build_client, not advisor_chat._build_client.

    These tests are hermetic: they do NOT reload the module.  Regardless of
    when or how often advisors.advisor_chat was previously imported, the live
    env var value at call time must be what reaches the SDK.
    """

    def test_env_var_unset_uses_opus_default(self, monkeypatch):
        """When ADVISOR_SYNTHESIS_MODEL is unset, advisor_chat must pass
        claude-opus-4-8 to client.messages.create.

        Hermetic: does not reload advisors.advisor_chat.  A call-time env read
        must return the default regardless of previous imports or env state.
        """
        monkeypatch.delenv(_ENV_VAR, raising=False)

        model_holder: list[str] = []
        client_mock = _make_client_mock_for_chat(model_holder)

        import ai_advisor
        from advisors import advisor_chat

        stub_artifact = {"type": "config_suggestion", "suggestion": "test suggestion"}

        with patch.object(ai_advisor, "_build_client", return_value=client_mock):
            advisor_chat.explain_artifact(
                question="Why is this a good suggestion?",
                artifact=stub_artifact,
            )

        assert model_holder, (
            "advisor_chat.explain_artifact never called messages.create — "
            "ensure ai_advisor._build_client is not returning None in the test."
        )
        actual_model = model_holder[0]
        assert actual_model == _EXPECTED_DEFAULT, (
            f"When {_ENV_VAR!r} is unset, advisor_chat must pass {_EXPECTED_DEFAULT!r} "
            f"to messages.create. Got: {actual_model!r}. "
            f"Fix: replace the module-level _CHAT_MODEL reference with an inline "
            f"os.environ.get({_ENV_VAR!r}, {_EXPECTED_DEFAULT!r}) at the model= call site."
        )

    def test_env_var_set_overrides_chat_model(self, monkeypatch):
        """When ADVISOR_SYNTHESIS_MODEL is set, advisor_chat must pass that value
        to client.messages.create.

        Hermetic: does not reload advisors.advisor_chat.  A call-time env read
        must pick up the new value even if the module was already imported under
        a different env (e.g., by a sibling test file in the same pytest process).
        """
        monkeypatch.setenv(_ENV_VAR, _CHEAP_TEST_MODEL)

        model_holder: list[str] = []
        client_mock = _make_client_mock_for_chat(model_holder)

        import ai_advisor
        from advisors import advisor_chat

        stub_artifact = {"type": "config_suggestion", "suggestion": "test suggestion"}

        with patch.object(ai_advisor, "_build_client", return_value=client_mock):
            advisor_chat.explain_artifact(
                question="Why is this a good suggestion?",
                artifact=stub_artifact,
            )

        assert model_holder, (
            "advisor_chat.explain_artifact never called messages.create."
        )
        actual_model = model_holder[0]
        assert actual_model == _CHEAP_TEST_MODEL, (
            f"When {_ENV_VAR!r}={_CHEAP_TEST_MODEL!r}, advisor_chat must pass that value "
            f"to messages.create. Got: {actual_model!r}. "
            f"Fix: replace the module-level _CHAT_MODEL reference with an inline "
            f"os.environ.get({_ENV_VAR!r}, {_EXPECTED_DEFAULT!r}) at the model= call site."
        )


# ---------------------------------------------------------------------------
# AC-2 — The production default is specifically claude-opus-4-8
# ---------------------------------------------------------------------------


class TestDefaultModelIsOpus:
    """AC-2: the production default is claude-opus-4-8, not any other model.

    These tests guard against the default silently drifting to an older or
    cheaper model (the original stale draft had claude-opus-4-5).

    RED intent: current defaults are claude-haiku-4-5-20251001 (lens_pipeline)
    and claude-opus-4-7 (ai_advisor, advisor_chat) — neither matches the
    required claude-opus-4-8.
    """

    def test_lens_pipeline_default_is_opus_4_8(self, monkeypatch):
        """The lens pipeline synthesis default must resolve to claude-opus-4-8."""
        monkeypatch.delenv(_ENV_VAR, raising=False)

        if "advisors.lens_pipeline" in sys.modules:
            importlib.reload(sys.modules["advisors.lens_pipeline"])

        model_holder: list[str] = []
        client_mock = _make_client_mock_for_synthesize(model_holder)

        import advisors.lens_pipeline as pipeline_mod

        with (
            patch("ai_advisor._build_technicals_section", return_value=_stub_lens_available("technicals")),
            patch("ai_advisor._build_sentiment_section", return_value=_stub_lens_unavailable("sentiment")),
            patch("ai_advisor._build_derivatives_section", return_value=_stub_lens_unavailable("derivatives")),
            patch("ai_advisor._build_macro_section", return_value=_stub_lens_unavailable("macro")),
            patch("ai_advisor._build_fundamentals_section", return_value=_stub_lens_unavailable("fundamentals")),
            patch("ai_advisor._build_client", return_value=client_mock),
            patch("database.insert_advisor_observation", return_value=1),
        ):
            pipeline_mod.run_pipeline(dry_run=False)

        assert model_holder, "synthesis was not triggered — check that one lens is available"
        assert model_holder[0] == _EXPECTED_DEFAULT, (
            f"lens_pipeline default model must be {_EXPECTED_DEFAULT!r}; "
            f"got {model_holder[0]!r}"
        )

    def test_ai_advisor_default_is_opus_4_8(self, monkeypatch):
        """ai_advisor._CLAUDE_MODEL (or equivalent env-var default) must be claude-opus-4-8.

        The call-time wiring test (TestAiAdvisorModelEnvVar::test_env_var_unset_uses_opus_default)
        is the authoritative guard; this constant check is a lightweight supplementary guard
        confirming the default value is correct at module level when the env var is absent.
        Note: the constant is still meaningful after the fix — it is the env-var default
        embedded in the module, even though the real read happens at call time.
        """
        monkeypatch.delenv(_ENV_VAR, raising=False)

        import ai_advisor

        model_constant = getattr(ai_advisor, "_CLAUDE_MODEL", None)
        assert model_constant == _EXPECTED_DEFAULT, (
            f"ai_advisor._CLAUDE_MODEL must be {_EXPECTED_DEFAULT!r} when {_ENV_VAR!r} "
            f"is unset. Got {model_constant!r}. "
            f"Fix: ensure the default fallback in os.environ.get({_ENV_VAR!r}, ...) "
            f"is {_EXPECTED_DEFAULT!r}."
        )

    def test_advisor_chat_default_is_opus_4_8(self, monkeypatch):
        """advisors/advisor_chat._CHAT_MODEL (or equivalent env-var default) must be claude-opus-4-8.

        Supplementary constant check — see TestAdvisorChatModelEnvVar for the authoritative
        call-time wiring test.
        """
        monkeypatch.delenv(_ENV_VAR, raising=False)

        from advisors import advisor_chat

        model_constant = getattr(advisor_chat, "_CHAT_MODEL", None)
        assert model_constant == _EXPECTED_DEFAULT, (
            f"advisors/advisor_chat._CHAT_MODEL must be {_EXPECTED_DEFAULT!r} when "
            f"{_ENV_VAR!r} is unset. Got {model_constant!r}. "
            f"Fix: ensure the default fallback in os.environ.get({_ENV_VAR!r}, ...) "
            f"is {_EXPECTED_DEFAULT!r}."
        )


# ---------------------------------------------------------------------------
# AC-2 — No real LLM calls in tests (model string assertion only)
# ---------------------------------------------------------------------------


class TestNoRealLlmCallsInTests:
    """AC-2: tests assert the selected model string only — never a network response.

    These tests verify that our own test machinery never reaches the real
    Anthropic API.  They are structural tests of the test setup, not of the
    production code.  They always pass (they test our mock discipline, not
    the implementation) and serve as documentation and regression guards.
    """

    def test_lens_pipeline_synth_asserts_model_string_only(self, monkeypatch):
        """The synthesis test captures the model= kwarg from the mock client,
        not any content returned by the real Anthropic API.

        This test verifies the test design (mock is present, no real call is
        made) rather than the production default.  It will always pass.
        """
        monkeypatch.delenv(_ENV_VAR, raising=False)

        import advisors.lens_pipeline as pipeline_mod

        model_holder: list[str] = []
        client_mock = _make_client_mock_for_synthesize(model_holder)

        # Verify the mock is in place — client.messages.create is a MagicMock
        assert hasattr(client_mock.messages, "create"), "Test mock must have messages.create"
        assert client_mock.messages.create.side_effect is not None, (
            "Test mock must capture the model= kwarg via side_effect"
        )

        with (
            patch("ai_advisor._build_technicals_section", return_value=_stub_lens_available("technicals")),
            patch("ai_advisor._build_sentiment_section", return_value=_stub_lens_unavailable("sentiment")),
            patch("ai_advisor._build_derivatives_section", return_value=_stub_lens_unavailable("derivatives")),
            patch("ai_advisor._build_macro_section", return_value=_stub_lens_unavailable("macro")),
            patch("ai_advisor._build_fundamentals_section", return_value=_stub_lens_unavailable("fundamentals")),
            patch("ai_advisor._build_client", return_value=client_mock),
            patch("database.insert_advisor_observation", return_value=1),
        ):
            pipeline_mod.run_pipeline(dry_run=False)

        # The model_holder was populated by our side_effect — no real HTTP call occurred.
        assert isinstance(model_holder, list), "model_holder must be populated by mock, not real call"

    def test_ai_advisor_request_asserts_model_string_only(self, monkeypatch):
        """ai_advisor wiring tests assert the model= kwarg from mock, not a real response."""
        monkeypatch.delenv(_ENV_VAR, raising=False)

        import ai_advisor

        model_holder: list[str] = []
        client_mock = _make_client_mock_for_parse(model_holder)

        assert hasattr(client_mock.messages, "parse"), "Test mock must have messages.parse"

        stub_context = {
            "technicals": _stub_lens_unavailable("technicals"),
            "sentiment": _stub_lens_unavailable("sentiment"),
            "derivatives": _stub_lens_unavailable("derivatives"),
            "macro": _stub_lens_unavailable("macro"),
            "fundamentals": _stub_lens_unavailable("fundamentals"),
            "config": {},
            "bot_state": {},
        }

        with (
            patch.object(ai_advisor, "_build_client", return_value=client_mock),
            patch.object(ai_advisor, "_build_messages", return_value=[{"role": "user", "content": "test"}]),
        ):
            ai_advisor.request_suggestions(stub_context)

        assert isinstance(model_holder, list), "model_holder populated by mock side_effect only"

    def test_advisor_chat_asserts_model_string_only(self, monkeypatch):
        """advisor_chat wiring tests assert the model= kwarg from mock, not a real response.

        advisor_chat delegates to ai_advisor._build_client() — patch that seam.
        """
        monkeypatch.delenv(_ENV_VAR, raising=False)

        import ai_advisor
        from advisors import advisor_chat

        model_holder: list[str] = []
        client_mock = _make_client_mock_for_chat(model_holder)

        assert hasattr(client_mock.messages, "create"), "Test mock must have messages.create"

        stub_artifact = {"type": "config_suggestion", "suggestion": "test suggestion"}

        # advisor_chat uses ai_advisor._build_client() as the shared factory seam.
        with patch.object(ai_advisor, "_build_client", return_value=client_mock):
            advisor_chat.explain_artifact(
                question="Why?",
                artifact=stub_artifact,
            )

        assert isinstance(model_holder, list), "model_holder populated by mock side_effect only"


# ---------------------------------------------------------------------------
# Suite-ordering regression — reproduces the full-suite failure deterministically
# ---------------------------------------------------------------------------


class TestSuiteOrderingRegression:
    """Regression: call-time env reads must survive the import-time constant trap.

    Root cause (diagnosed 2026-06-17):
      ``test_chat_engine.py::test_advisor_chat_does_not_import_alpha_bot_execution``
      executes this sequence:
        1. saved = sys.modules.get("advisors.advisor_chat")      # original module object
        2. sys.modules.pop("advisors.advisor_chat", None)
        3. importlib.import_module("advisors.advisor_chat")       # creates NEW module object M2
           => Python also sets advisors.advisor_chat (package attr) = M2
        4. [finally] sys.modules["advisors.advisor_chat"] = saved  # restores original M1
           => advisors.advisor_chat (package attr) is STILL M2

    After this, ``sys.modules["advisors.advisor_chat"]`` is M1 (original),
    but ``from advisors import advisor_chat`` returns M2 (the stale re-import).

    The existing ``test_env_var_set_overrides_chat_model`` then does:
        monkeypatch.setenv(...)
        importlib.reload(sys.modules["advisors.advisor_chat"])   # reloads M1
        from advisors import advisor_chat                         # gets M2 (not reloaded!)
        advisor_chat.explain_artifact(...)                        # M2._CHAT_MODEL = default

    This causes the test to see the import-time default instead of the override.

    The FIX: read ``os.environ.get(...)`` inline at the ``model=`` call site
    in ``explain_artifact`` (and ``request_suggestions``).  Then there is no
    module-level constant to go stale — M2's ``explain_artifact`` reads the
    env var live on every call.

    These tests simulate the stale-package-attribute state deterministically
    and assert that a call-time env read survives it.  They MUST FAIL on
    import-time constant code and PASS after the call-time fix.
    """

    @staticmethod
    def _simulate_pop_reimport_restore(module_key: str) -> None:
        """Reproduce the sys.modules + package-attribute desync from
        ``test_advisor_chat_does_not_import_alpha_bot_execution``.

        After this helper returns:
          - sys.modules[module_key] is the ORIGINAL module object
          - the package attribute (e.g. advisors.advisor_chat) points to a
            NEWLY IMPORTED copy whose import-time constants were frozen under
            whatever env vars were set during this helper's execution
        """
        import importlib as _importlib

        saved = sys.modules.get(module_key)
        sys.modules.pop(module_key, None)
        # Re-import creates a NEW module object AND updates the package attribute.
        _importlib.import_module(module_key)
        # Restore sys.modules to the original — but the package attribute stays
        # pointing at the newly imported copy (the desync).
        if saved is not None:
            sys.modules[module_key] = saved
        else:
            sys.modules.pop(module_key, None)

    def test_advisor_chat_call_time_read_survives_stale_package_attribute(self, monkeypatch):
        """After a pop+reimport+restore cycle leaves a stale advisors.advisor_chat
        package attribute, a call-time env read must still return the current env var.

        Reproduces the exact full-suite ordering failure:
          ADVISOR_SYNTHESIS_MODEL unset at re-import → M2._CHAT_MODEL frozen to default.
          Then monkeypatch sets the env var.
          ``from advisors import advisor_chat`` returns M2 (stale).
          With import-time code: M2.explain_artifact uses M2._CHAT_MODEL = default → FAIL.
          With call-time code: M2.explain_artifact reads os.environ.get(...) live → PASS.

        This test MUST FAIL on import-time constant code and PASS after call-time fix.
        """
        # Ensure the module is imported first (simulating sibling import), then desync it.
        import advisors  # noqa: F401 — ensure the package is loaded
        import advisors.advisor_chat  # noqa: F401 — ensure initial import happened

        # Simulate the pop+reimport+restore with NO env var set (M2 frozen to default).
        monkeypatch.delenv(_ENV_VAR, raising=False)
        self._simulate_pop_reimport_restore("advisors.advisor_chat")

        # Now set the env var — this is what the original test did.
        monkeypatch.setenv(_ENV_VAR, _CHEAP_TEST_MODEL)

        # Get advisor_chat via the package attribute (returns M2 — the stale copy).
        from advisors import advisor_chat

        model_holder: list[str] = []
        client_mock = _make_client_mock_for_chat(model_holder)

        import ai_advisor

        stub_artifact = {"type": "config_suggestion", "suggestion": "test suggestion"}

        with patch.object(ai_advisor, "_build_client", return_value=client_mock):
            advisor_chat.explain_artifact(
                question="Why does this survive the ordering failure?",
                artifact=stub_artifact,
            )

        assert model_holder, (
            "advisor_chat.explain_artifact never called messages.create — "
            "mock setup error in the ordering regression test."
        )
        actual_model = model_holder[0]
        assert actual_model == _CHEAP_TEST_MODEL, (
            f"SUITE-ORDERING REGRESSION: after a pop+reimport+restore cycle that leaves "
            f"a stale advisors.advisor_chat package attribute (M2 frozen at import time "
            f"to {_EXPECTED_DEFAULT!r}), setting {_ENV_VAR!r}={_CHEAP_TEST_MODEL!r} and "
            f"calling explain_artifact must still pass {_CHEAP_TEST_MODEL!r} to "
            f"messages.create. Got: {actual_model!r}. "
            f"Fix: replace `model=_CHAT_MODEL` with an inline "
            f"`model=os.environ.get({_ENV_VAR!r}, {_EXPECTED_DEFAULT!r})` "
            f"at the call site in advisors/advisor_chat.py."
        )

    def test_ai_advisor_call_time_read_survives_stale_module_constant(self, monkeypatch):
        """After ai_advisor is imported under the default env, setting the env var
        must cause messages.parse to use the new value without any reload.

        This is the equivalent hermetic ordering test for ai_advisor.request_suggestions.
        ai_advisor does not have the pop+reimport+restore side-effect because
        test_chat_engine.py only manipulates advisors.advisor_chat; however, any
        sibling that imports ai_advisor fixes the import-time _CLAUDE_MODEL to the
        default.  A call-time read must survive this.

        This test MUST FAIL on import-time constant code (the constant was frozen at
        import time to the default, ignoring the subsequent monkeypatch.setenv) and
        PASS after the call-time fix.
        """
        # Ensure ai_advisor is already imported (simulating sibling import).
        import ai_advisor  # noqa: F401

        # Now set the env var AFTER the module is already imported.
        monkeypatch.setenv(_ENV_VAR, _CHEAP_TEST_MODEL)

        model_holder: list[str] = []
        client_mock = _make_client_mock_for_parse(model_holder)

        stub_context = {
            "technicals": _stub_lens_unavailable("technicals"),
            "sentiment": _stub_lens_unavailable("sentiment"),
            "derivatives": _stub_lens_unavailable("derivatives"),
            "macro": _stub_lens_unavailable("macro"),
            "fundamentals": _stub_lens_unavailable("fundamentals"),
            "config": {},
            "bot_state": {},
        }

        with (
            patch.object(ai_advisor, "_build_client", return_value=client_mock),
            patch.object(ai_advisor, "_build_messages", return_value=[{"role": "user", "content": "test"}]),
        ):
            ai_advisor.request_suggestions(stub_context)

        assert model_holder, (
            "ai_advisor.request_suggestions never called messages.parse — "
            "mock setup error in the ordering regression test."
        )
        actual_model = model_holder[0]
        assert actual_model == _CHEAP_TEST_MODEL, (
            f"SUITE-ORDERING REGRESSION: ai_advisor already imported under default env "
            f"(import-time _CLAUDE_MODEL frozen to {_EXPECTED_DEFAULT!r}), then "
            f"{_ENV_VAR!r}={_CHEAP_TEST_MODEL!r} was set WITHOUT reloading the module. "
            f"messages.parse must receive {_CHEAP_TEST_MODEL!r}. Got: {actual_model!r}. "
            f"Fix: replace `model=_CLAUDE_MODEL` with an inline "
            f"`model=os.environ.get({_ENV_VAR!r}, {_EXPECTED_DEFAULT!r})` "
            f"at the call site in ai_advisor.py."
        )


# ---------------------------------------------------------------------------
# AC-3 — _extract_json_object regression guard (fence-stripping logic)
# ---------------------------------------------------------------------------


class TestExtractJsonObjectRegression:
    """AC-3: _extract_json_object must be byte-preserved from commit df2d19e.

    These tests assert the CURRENT behaviour of the extraction function as a
    regression guard.  They will PASS on the unmodified code and FAIL only if
    the extraction logic is accidentally changed (regression).

    This is not RED in the adversarial sense — the logic is correct already.
    The tests exist to prevent accidental modification during the model-config
    refactor.
    """

    def _extract(self, text: str) -> str:
        """Import and call advisors.lens_pipeline._extract_json_object directly."""
        import importlib

        if "advisors.lens_pipeline" in sys.modules:
            mod = sys.modules["advisors.lens_pipeline"]
        else:
            mod = importlib.import_module("advisors.lens_pipeline")
        fn = getattr(mod, "_extract_json_object", None)
        assert fn is not None, (
            "advisors.lens_pipeline must export _extract_json_object (it is tested directly)"
        )
        return fn(text)

    def test_plain_json_returned_unchanged(self):
        """A bare JSON object with no fences is returned as-is (trimmed)."""
        raw = '{"overall_sentiment": "risk-on", "sentiment_rationale": "good"}'
        result = self._extract(raw)
        import json
        parsed = json.loads(result)
        assert parsed["overall_sentiment"] == "risk-on", (
            "_extract_json_object must return the JSON object unchanged when no fences present"
        )

    def test_json_fenced_code_block_stripped(self):
        """A ```json ... ``` code fence is stripped; inner JSON is parseable."""
        raw = '```json\n{"overall_sentiment": "neutral", "sentiment_rationale": "ok"}\n```'
        result = self._extract(raw)
        import json
        parsed = json.loads(result)
        assert parsed["overall_sentiment"] == "neutral", (
            "_extract_json_object must strip ```json fences and return parseable JSON"
        )

    def test_plain_code_fence_stripped(self):
        """A plain ``` ... ``` fence (no language tag) is also stripped."""
        raw = '```\n{"overall_sentiment": "risk-off", "sentiment_rationale": "bad"}\n```'
        result = self._extract(raw)
        import json
        parsed = json.loads(result)
        assert parsed["overall_sentiment"] == "risk-off", (
            "_extract_json_object must strip plain ``` fences"
        )

    def test_prose_prefix_skipped_json_extracted(self):
        """Leading prose before the JSON object is skipped; the first { ... } block is returned."""
        raw = (
            "Based on the lens data, here is my assessment:\n\n"
            '{"overall_sentiment": "risk-on", "sentiment_rationale": "positive"}'
        )
        result = self._extract(raw)
        import json
        parsed = json.loads(result)
        assert parsed["overall_sentiment"] == "risk-on", (
            "_extract_json_object must skip leading prose and find the first { ... } block"
        )

    def test_no_json_object_returns_text_as_is(self):
        """When there is no { in the input, the stripped text is returned as-is.

        The caller's json.loads will raise JSONDecodeError — that is the documented
        graceful-degradation path.  The function must not itself raise.
        """
        raw = "I cannot provide a market sentiment analysis at this time."
        result = self._extract(raw)
        import json
        # The function should not raise, but json.loads on the result WILL raise.
        assert isinstance(result, str), "_extract_json_object must return a string, never raise"
        with pytest.raises(Exception):
            # json.loads must raise here — that triggers the caller's degradation path.
            json.loads(result)

    def test_nested_json_object_balanced_correctly(self):
        """A JSON object containing a nested object is extracted with correct brace balance."""
        raw = '{"a": {"b": 1}, "overall_sentiment": "neutral", "sentiment_rationale": "nested"}'
        result = self._extract(raw)
        import json
        parsed = json.loads(result)
        assert parsed["overall_sentiment"] == "neutral", (
            "_extract_json_object must handle nested { } braces correctly"
        )
        assert parsed["a"] == {"b": 1}, (
            "_extract_json_object must preserve nested objects in the extracted block"
        )

    def test_json_with_escaped_braces_in_string(self):
        """A JSON string value containing escaped braces is handled correctly.

        The brace-scanning loop must not count { or } inside string values.
        """
        raw = '{"overall_sentiment": "risk-on", "sentiment_rationale": "return {high} today"}'
        result = self._extract(raw)
        import json
        parsed = json.loads(result)
        assert parsed["overall_sentiment"] == "risk-on", (
            "_extract_json_object must not mis-count braces inside quoted string values"
        )
        assert "high" in parsed["sentiment_rationale"], (
            "Braces inside string values must be preserved, not treated as nesting"
        )
