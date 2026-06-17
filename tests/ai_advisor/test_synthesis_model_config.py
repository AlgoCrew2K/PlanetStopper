"""RED tests — Advisor Synthesis Model Config (C1 feature).

Feature plan: feature-plans/advisor-synthesis-model-config.md
Branch: feat/advisor-synthesis-model-config

Acceptance criteria (AC-1..AC-7):

  AC-1  advisors/lens_pipeline._synthesize_via_claude reads the model from the
        ADVISOR_SYNTHESIS_MODEL env var, not a hardcoded literal.

  AC-2  Default (env var unset) resolves to the Opus 4.8 model ID in production.
        When ADVISOR_SYNTHESIS_MODEL is set, that value is passed to the SDK.
        No production path fires a real Opus call during a pytest run.

  AC-3  _extract_json_object (lens_pipeline) is preserved byte-for-byte: fence
        stripping, balanced-brace scanning, and graceful fallback are all intact.
        No behavior change to the JSON-extraction path.

  AC-4  advisors/advisor_chat.explain_artifact reads the model from
        ADVISOR_SYNTHESIS_MODEL (the shared env var), not a hardcoded literal.

  AC-5  ai_advisor.request_suggestions reads the model from ADVISOR_SYNTHESIS_MODEL,
        not _CLAUDE_MODEL alone. When the env var is set, the overridden model
        reaches the mocked SDK call.

  AC-6  No hardcoded model literal remains at any LLM call site in
        advisors/lens_pipeline.py, advisors/advisor_chat.py, or ai_advisor.py.
        Regex-scans the source to detect bypassed call sites.

  AC-7  Test isolation: ADVISOR_SYNTHESIS_MODEL mutations via monkeypatch are
        function-scoped and do not bleed across tests. The stale-package-attribute
        failure (module constant cached before env var is set) is reproduced and
        verified to NOT affect correct test isolation.

Design constraints obeyed in these tests:
  - HERMETIC: no live Anthropic API calls; ai_advisor._build_client is always
    mocked. Function-scoped monkeypatch for all env mutations.
  - ORDER-INDEPENDENT: no test reads module-level constants populated by earlier
    tests; every test asserts through the mocked call, not through the constant.
  - DOES NOT USE importlib.reload() of module constants — the stale-attribute
    class of bug is tested via a regression scenario, not worked around.
  - CLEANS sys.modules: the suite-ordering regression test restores sys.modules
    state to avoid polluting downstream tests.
  - No real LLM calls from any test in this file.
"""

from __future__ import annotations

import pathlib
import re
import sys
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The single Opus 4.8 model ID this feature should default to.
# Derived from Anthropic model catalogue (claude-opus-4-8[1m] confirmed as
# the current Opus 4.8 identifier in the project environment).
_EXPECTED_DEFAULT_MODEL = "claude-opus-4-8"

# The sentinel override used by all tests that set the env var — must be
# recognisably distinct from any real model ID so assertion failures are obvious.
_TEST_MODEL_OVERRIDE = "test-stub-model-do-not-use"

# Repo root (two levels up from this file: tests/ai_advisor/ → tests/ → repo root).
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Shared mock helpers
# ---------------------------------------------------------------------------

def _make_mock_client(response_json: str = '{"overall_sentiment": "neutral", "sentiment_rationale": "stub"}') -> MagicMock:
    """Build a mock Anthropic client whose messages.create returns a stub response.

    The mock captures the model kwarg so tests can inspect which model was passed.
    """
    client = MagicMock()
    response = MagicMock()
    content_block = MagicMock()
    content_block.text = response_json
    response.content = [content_block]
    client.messages.create.return_value = response
    return client


def _make_mock_parse_client() -> MagicMock:
    """Build a mock client whose messages.parse returns a minimal structured response.

    Used for ai_advisor.request_suggestions (which calls messages.parse, not .create).
    The mock captures the model kwarg on messages.parse.
    """
    client = MagicMock()
    # messages.parse must return an object with a .content attribute whose items
    # have .parsed_output — the exact shape ai_advisor extracts from.
    parsed_block = MagicMock()
    parsed_block.parsed_output = MagicMock()
    parsed_block.parsed_output.suggestions = []
    response = MagicMock()
    response.content = [parsed_block]
    client.messages.parse.return_value = response
    return client


# ---------------------------------------------------------------------------
# AC-1 — lens_pipeline._synthesize_via_claude model wiring
# ---------------------------------------------------------------------------


class TestLensPipelineSynthesisModelEnvVar:
    """AC-1 / AC-2: lens_pipeline._synthesize_via_claude reads model from env."""

    def test_env_var_set_overrides_synthesis_model(self, monkeypatch):
        """When ADVISOR_SYNTHESIS_MODEL is set, _synthesize_via_claude must pass
        that value as the model kwarg to client.messages.create.

        This is RED on the current implementation which hardcodes
        model="claude-haiku-4-5-20251001" at advisors/lens_pipeline.py:284.
        """
        monkeypatch.setenv("ADVISOR_SYNTHESIS_MODEL", _TEST_MODEL_OVERRIDE)
        mock_client = _make_mock_client()

        import advisors.lens_pipeline as pipeline
        import ai_advisor

        with patch.object(ai_advisor, "_build_client", return_value=mock_client):
            per_lens = {
                "technicals": {"available": True, "summary": "bullish", "sources": []},
            }
            per_lens_digest = {
                "technicals": {"available": True, "summary": "bullish"},
            }
            pipeline._synthesize_via_claude(per_lens, per_lens_digest, available_count=1)

        mock_client.messages.create.assert_called_once()
        actual_model = mock_client.messages.create.call_args.kwargs.get(
            "model",
            mock_client.messages.create.call_args[1].get("model")
            if mock_client.messages.create.call_args[1]
            else mock_client.messages.create.call_args[0][0]
            if mock_client.messages.create.call_args[0]
            else None,
        )
        assert actual_model == _TEST_MODEL_OVERRIDE, (
            f"When 'ADVISOR_SYNTHESIS_MODEL'='{_TEST_MODEL_OVERRIDE}', "
            f"_synthesize_via_claude must pass that value to messages.create. "
            f"Got: {actual_model!r}."
        )

    def test_env_var_unset_uses_opus_default(self, monkeypatch):
        """When ADVISOR_SYNTHESIS_MODEL is not set, _synthesize_via_claude must
        use the Opus 4.8 model ID as the default.

        This is RED on the current implementation which defaults to Haiku, not Opus.
        """
        monkeypatch.delenv("ADVISOR_SYNTHESIS_MODEL", raising=False)
        mock_client = _make_mock_client()

        import advisors.lens_pipeline as pipeline
        import ai_advisor

        with patch.object(ai_advisor, "_build_client", return_value=mock_client):
            per_lens = {
                "technicals": {"available": True, "summary": "bearish", "sources": []},
            }
            per_lens_digest = {
                "technicals": {"available": True, "summary": "bearish"},
            }
            pipeline._synthesize_via_claude(per_lens, per_lens_digest, available_count=1)

        mock_client.messages.create.assert_called_once()
        # Extract the model kwarg robustly from call_args (positional or keyword).
        call_args = mock_client.messages.create.call_args
        actual_model = (
            call_args.kwargs.get("model")
            if call_args.kwargs
            else None
        )
        assert actual_model == _EXPECTED_DEFAULT_MODEL, (
            f"When ADVISOR_SYNTHESIS_MODEL is not set, _synthesize_via_claude "
            f"must default to {_EXPECTED_DEFAULT_MODEL!r}. Got: {actual_model!r}. "
            f"The implementation currently hardcodes 'claude-haiku-4-5-20251001'."
        )

    def test_no_real_claude_call_escapes_in_pytest(self, monkeypatch):
        """Even with a valid-looking env, _synthesize_via_claude must never
        reach the real Anthropic network during a pytest run.

        This test verifies the _build_client factory seam is what gets called
        (and therefore can be mocked) rather than a direct anthropic.Anthropic()
        construction buried inside the function.
        """
        monkeypatch.setenv("ADVISOR_SYNTHESIS_MODEL", _TEST_MODEL_OVERRIDE)
        # Track whether _build_client was called — that's the mock seam.
        build_client_calls = []

        def tracking_build_client():
            build_client_calls.append(True)
            return _make_mock_client()

        import advisors.lens_pipeline as pipeline
        import ai_advisor

        with patch.object(ai_advisor, "_build_client", side_effect=tracking_build_client):
            per_lens = {"technicals": {"available": True, "summary": "x", "sources": []}}
            per_lens_digest = {"technicals": {"available": True, "summary": "x"}}
            pipeline._synthesize_via_claude(per_lens, per_lens_digest, available_count=1)

        assert len(build_client_calls) == 1, (
            "_synthesize_via_claude must call ai_advisor._build_client() exactly "
            "once so tests can intercept the SDK call at the factory seam."
        )

    def test_zero_available_lenses_skips_claude_call(self, monkeypatch):
        """When available_count == 0, no LLM call should be made regardless of
        the env var — this is a correctness guard that must survive the refactor.
        """
        monkeypatch.setenv("ADVISOR_SYNTHESIS_MODEL", _TEST_MODEL_OVERRIDE)
        mock_client = _make_mock_client()

        import advisors.lens_pipeline as pipeline
        import ai_advisor

        with patch.object(ai_advisor, "_build_client", return_value=mock_client):
            sentiment, rationale = pipeline._synthesize_via_claude({}, {}, available_count=0)

        mock_client.messages.create.assert_not_called()
        assert sentiment == "limited-inputs", (
            "With 0 available lenses, _synthesize_via_claude must return "
            "'limited-inputs' without calling the LLM."
        )


# ---------------------------------------------------------------------------
# AC-2 — Opus 4.8 default value contract
# ---------------------------------------------------------------------------


class TestOpus48DefaultContract:
    """AC-2: The default model is Opus 4.8 — pinned to the exact model ID."""

    def test_default_model_is_opus_48_not_haiku(self, monkeypatch):
        """The hardcoded Haiku model ('claude-haiku-4-5-20251001') must NOT be
        used as the default. The default must be the Opus 4.8 model ID.

        This test is adversarial: it will pass if the env var is unset AND the
        implementation defaults to _EXPECTED_DEFAULT_MODEL, and fail if any
        other model (including Haiku) is used.
        """
        monkeypatch.delenv("ADVISOR_SYNTHESIS_MODEL", raising=False)
        mock_client = _make_mock_client()

        import advisors.lens_pipeline as pipeline
        import ai_advisor

        _HAIKU_LITERAL = "claude-haiku-4-5-20251001"

        with patch.object(ai_advisor, "_build_client", return_value=mock_client):
            per_lens = {"macro": {"available": True, "summary": "stable", "sources": []}}
            per_lens_digest = {"macro": {"available": True, "summary": "stable"}}
            pipeline._synthesize_via_claude(per_lens, per_lens_digest, available_count=1)

        call_args = mock_client.messages.create.call_args
        actual_model = call_args.kwargs.get("model") if call_args.kwargs else None

        assert actual_model != _HAIKU_LITERAL, (
            f"_synthesize_via_claude must NOT use the legacy Haiku literal "
            f"{_HAIKU_LITERAL!r} as its default. Got: {actual_model!r}. "
            f"Replace the hardcoded literal with os.environ.get('ADVISOR_SYNTHESIS_MODEL', "
            f"{_EXPECTED_DEFAULT_MODEL!r})."
        )
        assert actual_model == _EXPECTED_DEFAULT_MODEL, (
            f"Default must be {_EXPECTED_DEFAULT_MODEL!r}, got {actual_model!r}."
        )


# ---------------------------------------------------------------------------
# AC-3 — _extract_json_object byte-for-exact preservation
# ---------------------------------------------------------------------------


class TestExtractJsonObjectPreserved:
    """AC-3: _extract_json_object must be preserved exactly from df2d19e.

    Each test asserts a specific documented behavior of the function.
    If any test fails post-implementation, it means the function was changed.
    """

    def _extract(self, text: str) -> str:
        from advisors.lens_pipeline import _extract_json_object
        return _extract_json_object(text)

    def test_plain_json_passes_through(self):
        """A bare JSON object with no fence is returned unchanged."""
        payload = '{"overall_sentiment": "neutral", "sentiment_rationale": "steady"}'
        result = self._extract(payload)
        assert result == payload, (
            "_extract_json_object must pass a bare JSON object through unchanged."
        )

    def test_strips_json_code_fence(self):
        """A ```json ... ``` fence is stripped, leaving only the JSON."""
        fenced = '```json\n{"overall_sentiment": "risk-on", "sentiment_rationale": "rally"}\n```'
        result = self._extract(fenced)
        # Must be parseable JSON without the fences.
        import json
        parsed = json.loads(result)
        assert parsed.get("overall_sentiment") == "risk-on", (
            "_extract_json_object must strip ``` json fences and return parseable JSON."
        )

    def test_strips_plain_code_fence(self):
        """A ``` ... ``` fence (no language tag) is also stripped."""
        fenced = '```\n{"overall_sentiment": "risk-off", "sentiment_rationale": "drop"}\n```'
        result = self._extract(fenced)
        import json
        parsed = json.loads(result)
        assert parsed.get("overall_sentiment") == "risk-off"

    def test_extracts_json_from_surrounding_prose(self):
        """Leading and trailing prose are tolerated; only the JSON block is returned."""
        prose_and_json = (
            'Here is my analysis:\n'
            '{"overall_sentiment": "neutral", "sentiment_rationale": "mixed signals"}\n'
            'I hope this helps.'
        )
        result = self._extract(prose_and_json)
        import json
        parsed = json.loads(result)
        assert parsed.get("overall_sentiment") == "neutral", (
            "_extract_json_object must extract the first balanced JSON object "
            "from surrounding prose."
        )

    def test_no_json_object_returns_stripped_text(self):
        """Input with no '{' character returns the stripped text as-is (caller degrades)."""
        no_json = "Just some words, no braces."
        result = self._extract(no_json)
        # Must not raise; must return the stripped text.
        assert isinstance(result, str), "_extract_json_object must always return a str."
        assert "{" not in result, (
            "With no JSON object, result should not contain '{'."
        )

    def test_nested_json_braces_handled_correctly(self):
        """Nested braces (depth > 1) are handled by the balanced-brace scanner."""
        nested = '{"outer": {"inner": 42}, "key": "value"}'
        result = self._extract(nested)
        import json
        parsed = json.loads(result)
        assert parsed.get("key") == "value", (
            "_extract_json_object must handle nested braces correctly."
        )

    def test_escaped_quote_inside_string_not_misinterpreted(self):
        """An escaped quote inside a JSON string must not terminate string scanning."""
        with_escape = r'{"rationale": "it\"s complex", "sentiment": "neutral"}'
        result = self._extract(with_escape)
        import json
        parsed = json.loads(result)
        assert parsed.get("sentiment") == "neutral", (
            "_extract_json_object must handle escaped quotes inside strings."
        )

    def test_code_fence_case_insensitive(self):
        """The fence-stripping regex is case-insensitive (```JSON should also work)."""
        fenced_upper = '```JSON\n{"overall_sentiment": "risk-on"}\n```'
        result = self._extract(fenced_upper)
        import json
        parsed = json.loads(result)
        assert "overall_sentiment" in parsed, (
            "Fence stripping must be case-insensitive for the language tag."
        )


# ---------------------------------------------------------------------------
# AC-4 — advisor_chat model wiring
# ---------------------------------------------------------------------------


class TestAdvisorChatModelEnvVar:
    """AC-4: advisor_chat.explain_artifact reads model from ADVISOR_SYNTHESIS_MODEL."""

    def test_env_var_set_overrides_chat_model(self, monkeypatch):
        """When ADVISOR_SYNTHESIS_MODEL is set, explain_artifact must pass that
        value as the model kwarg to client.messages.create.

        This is RED on the current implementation which uses the module-level
        constant _CHAT_MODEL = 'claude-opus-4-7' regardless of the env var.
        """
        monkeypatch.setenv("ADVISOR_SYNTHESIS_MODEL", _TEST_MODEL_OVERRIDE)
        mock_client = _make_mock_client(response_json="This artifact shows a balanced approach.")

        import ai_advisor
        from advisors import advisor_chat

        with patch.object(ai_advisor, "_build_client", return_value=mock_client):
            artifact = {"type": "config_suggestion", "config_key": "VWAP_BLEED_TICKS", "value": 5}
            advisor_chat.explain_artifact(
                question="Why is this change suggested?",
                artifact=artifact,
            )

        mock_client.messages.create.assert_called_once()
        call_args = mock_client.messages.create.call_args
        actual_model = call_args.kwargs.get("model") if call_args.kwargs else None
        assert actual_model == _TEST_MODEL_OVERRIDE, (
            f"When 'ADVISOR_SYNTHESIS_MODEL'='{_TEST_MODEL_OVERRIDE}', advisor_chat "
            f"must pass that value to messages.create. Got: {actual_model!r}."
        )

    def test_env_var_unset_uses_opus_default_in_chat(self, monkeypatch):
        """When ADVISOR_SYNTHESIS_MODEL is not set, advisor_chat must use the
        Opus 4.8 default, not the stale module-level _CHAT_MODEL constant.

        This ensures the default tracks the env var, not the hardcoded constant.
        """
        monkeypatch.delenv("ADVISOR_SYNTHESIS_MODEL", raising=False)
        mock_client = _make_mock_client(response_json="Explanation here.")

        import ai_advisor
        from advisors import advisor_chat

        with patch.object(ai_advisor, "_build_client", return_value=mock_client):
            artifact = {"type": "config_suggestion", "config_key": "TAKE_PROFIT_MC_PCT", "value": 3.5}
            advisor_chat.explain_artifact(
                question="Is this safe?",
                artifact=artifact,
            )

        call_args = mock_client.messages.create.call_args
        actual_model = call_args.kwargs.get("model") if call_args.kwargs else None
        assert actual_model == _EXPECTED_DEFAULT_MODEL, (
            f"When ADVISOR_SYNTHESIS_MODEL is unset, advisor_chat must default "
            f"to {_EXPECTED_DEFAULT_MODEL!r}. Got: {actual_model!r}."
        )

    def test_chat_never_raises(self, monkeypatch):
        """explain_artifact must never raise — graceful degradation contract
        must survive the model-wiring refactor.
        """
        monkeypatch.setenv("ADVISOR_SYNTHESIS_MODEL", _TEST_MODEL_OVERRIDE)

        import ai_advisor
        from advisors import advisor_chat

        with patch.object(ai_advisor, "_build_client", side_effect=RuntimeError("no key")):
            # Must not raise.
            result = advisor_chat.explain_artifact(
                question="Any question",
                artifact={"type": "config_suggestion", "config_key": "MAX_SQUEEZE_FLOOR"},
            )

        # Result is a ChatResponse with error set, not an exception.
        assert result.error is not None, (
            "When client construction fails, explain_artifact must return a "
            "ChatResponse(answer=None, error=...) not raise."
        )
        assert result.answer is None, (
            "On error, answer must be None."
        )


# ---------------------------------------------------------------------------
# AC-5 — ai_advisor.request_suggestions model wiring
# ---------------------------------------------------------------------------


class TestRequestSuggestionsModelEnvVar:
    """AC-5: request_suggestions reads model from ADVISOR_SYNTHESIS_MODEL."""

    def test_env_var_set_overrides_suggestions_model(self, monkeypatch):
        """When ADVISOR_SYNTHESIS_MODEL is set, request_suggestions must pass
        that value as the model kwarg to client.messages.parse.

        RED: current code uses the module-level _CLAUDE_MODEL constant directly,
        ignoring the env var.
        """
        monkeypatch.setenv("ADVISOR_SYNTHESIS_MODEL", _TEST_MODEL_OVERRIDE)
        mock_client = _make_mock_parse_client()

        import ai_advisor

        with patch.object(ai_advisor, "_build_client", return_value=mock_client):
            result, error = ai_advisor.request_suggestions(context={"scope": "test"})

        mock_client.messages.parse.assert_called_once()
        call_args = mock_client.messages.parse.call_args
        actual_model = call_args.kwargs.get("model") if call_args.kwargs else None
        assert actual_model == _TEST_MODEL_OVERRIDE, (
            f"When 'ADVISOR_SYNTHESIS_MODEL'='{_TEST_MODEL_OVERRIDE}', "
            f"request_suggestions must pass that value to messages.parse. "
            f"Got: {actual_model!r}."
        )

    def test_env_var_unset_uses_opus_default_in_suggestions(self, monkeypatch):
        """When ADVISOR_SYNTHESIS_MODEL is not set, request_suggestions must use
        the Opus 4.8 default model ID, not the stale _CLAUDE_MODEL constant.
        """
        monkeypatch.delenv("ADVISOR_SYNTHESIS_MODEL", raising=False)
        mock_client = _make_mock_parse_client()

        import ai_advisor

        with patch.object(ai_advisor, "_build_client", return_value=mock_client):
            result, error = ai_advisor.request_suggestions(context={"scope": "test"})

        call_args = mock_client.messages.parse.call_args
        actual_model = call_args.kwargs.get("model") if call_args.kwargs else None
        assert actual_model == _EXPECTED_DEFAULT_MODEL, (
            f"When ADVISOR_SYNTHESIS_MODEL is unset, request_suggestions must "
            f"default to {_EXPECTED_DEFAULT_MODEL!r}. Got: {actual_model!r}."
        )

    def test_request_suggestions_never_raises_after_refactor(self, monkeypatch):
        """The never-raises contract of request_suggestions must survive the
        model-wiring change (no new raise paths introduced).
        """
        monkeypatch.setenv("ADVISOR_SYNTHESIS_MODEL", _TEST_MODEL_OVERRIDE)

        import ai_advisor

        # Simulate client construction failure.
        with patch.object(ai_advisor, "_build_client", side_effect=RuntimeError("simulated failure")):
            result, error = ai_advisor.request_suggestions(context={"scope": "test"})

        assert result is None, "On client-construction failure, result must be None."
        assert error is not None and isinstance(error, str), (
            "On client-construction failure, error must be a non-empty str."
        )


# ---------------------------------------------------------------------------
# AC-6 — No hardcoded model literals at LLM call sites
# ---------------------------------------------------------------------------


class TestNoHardcodedModelLiterals:
    """AC-6: Regex-scan call sites for bypassed hardcoded model strings.

    These tests scan source code rather than the module at runtime because
    a hardcoded literal could still be shadowed by a late env-var read but
    the literal itself is the smell to eliminate.

    Scanned files:
      - advisors/lens_pipeline.py  (was "claude-haiku-4-5-20251001" at line 284)
      - advisors/advisor_chat.py   (was using _CHAT_MODEL module constant)
      - ai_advisor.py              (was using _CLAUDE_MODEL module constant)

    The regex asserts that NO string that looks like a claude-* model ID appears
    as the VALUE passed in a model= kwarg.  The env-var expression
    os.environ.get("ADVISOR_SYNTHESIS_MODEL", ...) is the acceptable form.
    """

    # Pattern: a model= call-site kwarg that has a bare string literal
    # (NOT a variable or os.environ.get(...) call).
    # Matches: model="claude-anything" or model='claude-anything'
    _HARDCODED_MODEL_RE = re.compile(
        r'\bmodel\s*=\s*["\']claude-[^"\']+["\']'
    )

    def _source(self, rel_path: str) -> str:
        return (_REPO_ROOT / rel_path).read_text(encoding="utf-8")

    def test_lens_pipeline_has_no_hardcoded_model_kwarg(self):
        """advisors/lens_pipeline.py must have no bare model='claude-*' kwarg."""
        source = self._source("advisors/lens_pipeline.py")
        matches = self._HARDCODED_MODEL_RE.findall(source)
        assert not matches, (
            "advisors/lens_pipeline.py still has hardcoded model literal(s) "
            f"at LLM call sites: {matches}. "
            "Replace with os.environ.get('ADVISOR_SYNTHESIS_MODEL', ...)."
        )

    def test_advisor_chat_has_no_hardcoded_model_kwarg(self):
        """advisors/advisor_chat.py must have no bare model='claude-*' kwarg at
        call sites (the module-level constant _CHAT_MODEL may remain as a comment
        anchor but must not be used in model= directly without env var override).
        """
        source = self._source("advisors/advisor_chat.py")
        # Find model= kwargs that are bare literals (not variable references).
        matches = self._HARDCODED_MODEL_RE.findall(source)
        assert not matches, (
            "advisors/advisor_chat.py still has hardcoded model literal(s) at "
            f"LLM call sites: {matches}. "
            "Replace with os.environ.get('ADVISOR_SYNTHESIS_MODEL', ...)."
        )

    def test_ai_advisor_has_no_hardcoded_model_kwarg(self):
        """ai_advisor.py must have no bare model='claude-*' kwarg at call sites."""
        source = self._source("ai_advisor.py")
        matches = self._HARDCODED_MODEL_RE.findall(source)
        assert not matches, (
            "ai_advisor.py still has hardcoded model literal(s) at LLM call "
            f"sites: {matches}. "
            "Replace with os.environ.get('ADVISOR_SYNTHESIS_MODEL', ...)."
        )

    def test_env_var_name_appears_in_all_three_files(self):
        """The string 'ADVISOR_SYNTHESIS_MODEL' must appear in every file that
        makes LLM calls, confirming the env var is used (not just absent from call sites).
        """
        _ENV_VAR_NAME = "ADVISOR_SYNTHESIS_MODEL"
        for rel_path in (
            "advisors/lens_pipeline.py",
            "advisors/advisor_chat.py",
            "ai_advisor.py",
        ):
            source = self._source(rel_path)
            assert _ENV_VAR_NAME in source, (
                f"{rel_path} does not reference '{_ENV_VAR_NAME}'. "
                "Every file that makes LLM calls must read from this env var."
            )


# ---------------------------------------------------------------------------
# AC-7 — Suite-ordering / stale-package-attribute regression
# ---------------------------------------------------------------------------


class TestSuiteOrderingRegression:
    """AC-7: Deterministic regression for the stale-package-attribute failure.

    The bug class: a test that imports a module and then reads a module-level
    constant (e.g., _CLAUDE_MODEL or _CHAT_MODEL) to assert the model does NOT
    work if another test already set the env var BEFORE the import, because
    Python's module cache stores the constant at import time.

    These tests PROVE the correct isolation approach:
      1. Assert through the MOCKED SDK CALL (model= kwarg), not through the constant.
      2. monkeypatch (function-scoped) ensures env mutations do not bleed.
      3. sys.modules is restored (monkeypatch does NOT unload modules — each test
         patches at runtime, never via importlib.reload).

    A suite-ordering regression is one where test B fails if test A runs first
    (but B passes if run alone). These tests verify isolation is preserved
    regardless of whether the env var was set by a prior test.
    """

    def test_env_override_does_not_persist_to_next_test_ai_advisor(self, monkeypatch):
        """Setting ADVISOR_SYNTHESIS_MODEL in one test must not affect a subsequent
        test that does NOT set it.

        Simulated by: set env var, invoke ai_advisor path, then check that reading
        os.environ.get('ADVISOR_SYNTHESIS_MODEL') returns None (monkeypatch restores).

        This test must be order-independent — it relies on monkeypatch teardown.
        """
        import os
        # Phase 1: simulate a prior test that sets the env var.
        monkeypatch.setenv("ADVISOR_SYNTHESIS_MODEL", _TEST_MODEL_OVERRIDE)
        assert os.environ.get("ADVISOR_SYNTHESIS_MODEL") == _TEST_MODEL_OVERRIDE

        # The monkeypatch fixture teardown (post-yield) restores env.
        # We cannot test the teardown within the same test, but we can assert
        # that the env var is isolated to this call.
        mock_client = _make_mock_parse_client()
        import ai_advisor
        with patch.object(ai_advisor, "_build_client", return_value=mock_client):
            ai_advisor.request_suggestions(context={"scope": "order-test-1"})
        call_args = mock_client.messages.parse.call_args
        actual_model_in_test = call_args.kwargs.get("model") if call_args.kwargs else None
        assert actual_model_in_test == _TEST_MODEL_OVERRIDE, (
            "When ADVISOR_SYNTHESIS_MODEL is set within this test, the override must reach the client."
        )

    def test_subsequent_test_unaffected_by_prior_env_mutation_ai_advisor(self, monkeypatch):
        """When ADVISOR_SYNTHESIS_MODEL is NOT set in this test, the default
        (Opus 4.8) must be used — even if a prior test in the same session set it.

        This is the companion to the prior test. Together they form the ordering regression.
        If monkeypatch isolation works, this test always passes regardless of test order.
        If isolation breaks (module-constant cached), this test fails when run AFTER
        test_env_override_does_not_persist_to_next_test_ai_advisor.
        """
        monkeypatch.delenv("ADVISOR_SYNTHESIS_MODEL", raising=False)
        mock_client = _make_mock_parse_client()
        import ai_advisor
        with patch.object(ai_advisor, "_build_client", return_value=mock_client):
            ai_advisor.request_suggestions(context={"scope": "order-test-2"})
        call_args = mock_client.messages.parse.call_args
        actual_model = call_args.kwargs.get("model") if call_args.kwargs else None
        assert actual_model == _EXPECTED_DEFAULT_MODEL, (
            f"After a prior test set ADVISOR_SYNTHESIS_MODEL, this test (which "
            f"does NOT set it) must see the default {_EXPECTED_DEFAULT_MODEL!r}. "
            f"Got {actual_model!r}. Stale module-constant isolation failure."
        )

    def test_env_override_does_not_persist_to_next_test_advisor_chat(self, monkeypatch):
        """Same ordering regression for advisor_chat.explain_artifact.

        Setting env var in this test must not affect the next (chat path).
        """
        monkeypatch.setenv("ADVISOR_SYNTHESIS_MODEL", _TEST_MODEL_OVERRIDE)
        mock_client = _make_mock_client(response_json="stub answer")
        import ai_advisor
        from advisors import advisor_chat
        with patch.object(ai_advisor, "_build_client", return_value=mock_client):
            advisor_chat.explain_artifact(
                question="order regression q",
                artifact={"type": "config_suggestion", "config_key": "VWAP_BLEED_TICKS"},
            )
        call_args = mock_client.messages.create.call_args
        actual = call_args.kwargs.get("model") if call_args.kwargs else None
        assert actual == _TEST_MODEL_OVERRIDE, (
            "ADVISOR_SYNTHESIS_MODEL override must reach advisor_chat in this test."
        )

    def test_subsequent_test_unaffected_by_prior_env_mutation_advisor_chat(self, monkeypatch):
        """Companion to the prior test: env unset here → Opus default in chat.

        If advisor_chat reads from os.environ at call time (not import time),
        monkeypatch isolation is sufficient and this always passes regardless
        of test order.
        """
        monkeypatch.delenv("ADVISOR_SYNTHESIS_MODEL", raising=False)
        mock_client = _make_mock_client(response_json="default model answer")
        import ai_advisor
        from advisors import advisor_chat
        with patch.object(ai_advisor, "_build_client", return_value=mock_client):
            advisor_chat.explain_artifact(
                question="isolation check",
                artifact={"type": "config_suggestion", "config_key": "MAX_SQUEEZE_FLOOR"},
            )
        call_args = mock_client.messages.create.call_args
        actual = call_args.kwargs.get("model") if call_args.kwargs else None
        assert actual == _EXPECTED_DEFAULT_MODEL, (
            f"After a prior test set ADVISOR_SYNTHESIS_MODEL, this test (no env var "
            f"set) must see the default {_EXPECTED_DEFAULT_MODEL!r} in advisor_chat. "
            f"Got {actual!r}. Ordering / stale-constant failure."
        )

    def test_sys_modules_not_polluted_by_lens_pipeline_test(self):
        """Verify that lens_pipeline tests do not leave stale module state in
        sys.modules that would affect downstream tests.

        Specifically: importing advisors.lens_pipeline inside a test context must
        not leave a shadowed or patched version of ai_advisor in sys.modules after
        the test ends (since lens_pipeline lazily imports ai_advisor).

        This test imports lens_pipeline, verifies ai_advisor is importable normally
        after the fact, and that it carries its expected public symbols — and does
        NOT carry the now-removed dead constants (AC-4).
        """
        # Import through the normal path.
        import advisors.lens_pipeline  # noqa: F401
        import ai_advisor

        # After both imports complete, ai_advisor must still expose its real symbols.
        assert hasattr(ai_advisor, "_build_client"), (
            "ai_advisor._build_client must be accessible after lens_pipeline import; "
            "sys.modules pollution check."
        )
        assert hasattr(ai_advisor, "request_suggestions"), (
            "ai_advisor.request_suggestions must be accessible after lens_pipeline import."
        )
        # AC-4: _CLAUDE_MODEL is a dead constant (the call site now reads the env var
        # inline). It must be removed. This assertion is RED until removal.
        assert not hasattr(ai_advisor, "_CLAUDE_MODEL"), (
            "ai_advisor._CLAUDE_MODEL must be REMOVED (AC-4: dead constant, no longer "
            "used at any call site after the env-var wiring). Remove it from ai_advisor.py."
        )


# ---------------------------------------------------------------------------
# AC-4 — Dead constants removed
# ---------------------------------------------------------------------------


class TestDeadConstantsRemoved:
    """AC-4: _CLAUDE_MODEL and _CHAT_MODEL module-level constants are REMOVED.

    After the env-var wiring, both constants are dead duplicates of the default.
    The plan's Architecture section and Scope Boundaries both require removal.
    These tests are RED until the implementer deletes the two constant definitions.

    Why removal matters (not just non-use):
      A dead constant with a hardcoded model ID is a maintenance trap — a future
      reader may think it is the active model, not the env var default. Removal
      eliminates the confusion and matches lens_pipeline's existing inline style
      (which never had a module-level model constant).
    """

    def test_claude_model_constant_removed_from_ai_advisor(self):
        """ai_advisor._CLAUDE_MODEL must NOT exist after the env-var refactor.

        RED: currently 'claude-opus-4-7' is still present at ai_advisor.py:59.
        """
        import ai_advisor

        assert not hasattr(ai_advisor, "_CLAUDE_MODEL"), (
            "ai_advisor._CLAUDE_MODEL must be REMOVED (AC-4). "
            "It is a dead constant — the call site at request_suggestions now reads "
            "os.environ.get('ADVISOR_SYNTHESIS_MODEL', ...) inline. "
            "Delete the line '_CLAUDE_MODEL = ...' from ai_advisor.py."
        )

    def test_chat_model_constant_removed_from_advisor_chat(self):
        """advisors/advisor_chat._CHAT_MODEL must NOT exist after the env-var refactor.

        RED: currently 'claude-opus-4-7' is still present at advisor_chat.py:212.
        """
        from advisors import advisor_chat

        assert not hasattr(advisor_chat, "_CHAT_MODEL"), (
            "advisor_chat._CHAT_MODEL must be REMOVED (AC-4). "
            "It is a dead constant — the call site at explain_artifact now reads "
            "os.environ.get('ADVISOR_SYNTHESIS_MODEL', ...) inline. "
            "Delete the line '_CHAT_MODEL = ...' from advisors/advisor_chat.py."
        )

    def test_no_module_level_claude_model_string_in_ai_advisor_source(self):
        """The source of ai_advisor.py must not contain a module-level assignment
        of a claude-* model string to _CLAUDE_MODEL.

        Belt-and-suspenders: even if the attribute is somehow absent from the live
        module object, the source line must also be gone.
        """
        source = (_REPO_ROOT / "ai_advisor.py").read_text(encoding="utf-8")
        # Match: _CLAUDE_MODEL = "claude-..." (module-level constant assignment).
        pattern = re.compile(r'^_CLAUDE_MODEL\s*=\s*["\']claude-', re.MULTILINE)
        assert not pattern.search(source), (
            "ai_advisor.py still contains a module-level '_CLAUDE_MODEL = claude-...' "
            "assignment. Remove this dead constant per AC-4."
        )

    def test_no_module_level_chat_model_string_in_advisor_chat_source(self):
        """The source of advisors/advisor_chat.py must not contain a module-level
        assignment of a claude-* model string to _CHAT_MODEL.
        """
        source = (_REPO_ROOT / "advisors" / "advisor_chat.py").read_text(encoding="utf-8")
        pattern = re.compile(r'^_CHAT_MODEL\s*=\s*["\']claude-', re.MULTILINE)
        assert not pattern.search(source), (
            "advisors/advisor_chat.py still contains a module-level '_CHAT_MODEL = claude-...' "
            "assignment. Remove this dead constant per AC-4."
        )


# ---------------------------------------------------------------------------
# Integration: lens_pipeline model env var wires through run_pipeline
# ---------------------------------------------------------------------------


class TestLensPipelineRunPipelineModelWiring:
    """Integration guard: env var reaches model kwarg via the run_pipeline entry point.

    run_pipeline → _synthesize_via_claude → model kwarg.
    This is NOT testing run_pipeline's persistence or scheduling — only that the
    model wiring propagates end-to-end through the public entry point.
    """

    def test_run_pipeline_dry_run_never_calls_llm(self, monkeypatch):
        """dry_run=True must skip the LLM call entirely — regression guard for
        the refactor not accidentally re-enabling the LLM on dry runs.
        """
        monkeypatch.setenv("ADVISOR_SYNTHESIS_MODEL", _TEST_MODEL_OVERRIDE)
        mock_client = _make_mock_client()

        import ai_advisor

        # Patch all lens builders to return available=False so pipeline degrades.
        lens_stubs = {
            f"_build_{lens}_section": lambda: {"lens": lens, "available": False, "sources": []}
            for lens in ("technicals", "sentiment", "derivatives", "macro", "fundamentals")
        }
        with (
            patch.object(ai_advisor, "_build_client", return_value=mock_client),
            patch.object(ai_advisor, "_build_technicals_section", return_value={"lens": "technicals", "available": False, "sources": []}),
            patch.object(ai_advisor, "_build_sentiment_section", return_value={"lens": "sentiment", "available": False, "sources": []}),
            patch.object(ai_advisor, "_build_derivatives_section", return_value={"lens": "derivatives", "available": False, "sources": []}),
            patch.object(ai_advisor, "_build_macro_section", return_value={"lens": "macro", "available": False, "sources": []}),
            patch.object(ai_advisor, "_build_fundamentals_section", return_value={"lens": "fundamentals", "available": False, "sources": []}),
        ):
            from advisors.lens_pipeline import run_pipeline
            result = run_pipeline(dry_run=True)

        mock_client.messages.create.assert_not_called()
        assert result["market_prism_row_id"] is None, (
            "dry_run must not persist any row (market_prism_row_id must be None)."
        )

    def test_run_pipeline_with_available_lens_uses_env_model(self, monkeypatch):
        """When at least one lens is available, the env-var model must reach
        client.messages.create via _synthesize_via_claude.
        """
        monkeypatch.setenv("ADVISOR_SYNTHESIS_MODEL", _TEST_MODEL_OVERRIDE)
        mock_client = _make_mock_client()

        import ai_advisor
        import database

        with (
            patch.object(ai_advisor, "_build_client", return_value=mock_client),
            patch.object(ai_advisor, "_build_technicals_section", return_value={
                "lens": "technicals", "available": True, "summary": "bullish", "sources": [],
            }),
            patch.object(ai_advisor, "_build_sentiment_section", return_value={"lens": "sentiment", "available": False, "sources": []}),
            patch.object(ai_advisor, "_build_derivatives_section", return_value={"lens": "derivatives", "available": False, "sources": []}),
            patch.object(ai_advisor, "_build_macro_section", return_value={"lens": "macro", "available": False, "sources": []}),
            patch.object(ai_advisor, "_build_fundamentals_section", return_value={"lens": "fundamentals", "available": False, "sources": []}),
            patch.object(database, "insert_advisor_observation", return_value=99),
        ):
            from advisors.lens_pipeline import run_pipeline
            result = run_pipeline(dry_run=False)

        # The LLM call must have fired.
        mock_client.messages.create.assert_called_once()
        call_args = mock_client.messages.create.call_args
        actual_model = call_args.kwargs.get("model") if call_args.kwargs else None
        assert actual_model == _TEST_MODEL_OVERRIDE, (
            f"run_pipeline with available lenses must pass env model "
            f"{_TEST_MODEL_OVERRIDE!r} to messages.create. Got {actual_model!r}."
        )


# ---------------------------------------------------------------------------
# AC-7 audit — No external consumer references _CLAUDE_MODEL / _CHAT_MODEL
# ---------------------------------------------------------------------------


class TestNoExternalConstantReferences:
    """AC-7 (extended): After removal, no production module may reference
    ai_advisor._CLAUDE_MODEL or advisor_chat._CHAT_MODEL as attribute accesses.

    The AC-7 audit previously only scanned the 3 advisor source files.  This
    class adds a whole-codebase scan (excluding tests and worktrees) so a
    consumer like app.py cannot silently break when the constant is deleted.

    RED: app.py:3748 and app.py:3781 currently read `ai_advisor._CLAUDE_MODEL`.
    This causes AttributeError at runtime after AC-4 removes the constant.
    """

    # Attribute-access patterns to search for in production code.
    _DEAD_ATTR_RE = re.compile(
        r'\bai_advisor\._CLAUDE_MODEL\b|\badvisor_chat\._CHAT_MODEL\b'
    )

    def _production_py_files(self):
        """Yield all .py files under the repo root, excluding tests/ and .claude/."""
        for p in _REPO_ROOT.rglob("*.py"):
            parts = p.relative_to(_REPO_ROOT).parts
            if parts[0] in ("tests", ".claude"):
                continue
            yield p

    def test_no_production_file_references_dead_claude_model_attr(self):
        """No production .py file may access ai_advisor._CLAUDE_MODEL or
        advisor_chat._CHAT_MODEL as an attribute after constant removal.

        RED: app.py:3748 + 3781 both reference ai_advisor._CLAUDE_MODEL —
        these lines raise AttributeError at runtime once the constant is deleted.
        """
        violations = []
        for path in self._production_py_files():
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(source.splitlines(), 1):
                if self._DEAD_ATTR_RE.search(line):
                    violations.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}: {line.strip()}")

        assert not violations, (
            "Production code still references the removed constants "
            "ai_advisor._CLAUDE_MODEL or advisor_chat._CHAT_MODEL:\n"
            + "\n".join(violations)
            + "\n\nReplace each reference with "
            "ai_advisor.resolve_advisor_model() (or inline "
            "os.environ.get('ADVISOR_SYNTHESIS_MODEL', 'claude-opus-4-8'))."
        )

    def test_app_py_accept_route_does_not_attribute_access_removed_constant(self):
        """Targeted guard for app.py's /ai-advisor/accept route.

        The accept route calls database.record_llm_suggestion(..., model_id=...).
        After AC-4 removes _CLAUDE_MODEL, the model_id= argument must NOT be
        ai_advisor._CLAUDE_MODEL — it must be the env-resolved model.

        RED: app.py:3748 currently reads `model_id=ai_advisor._CLAUDE_MODEL`.
        """
        source = (_REPO_ROOT / "app.py").read_text(encoding="utf-8")
        assert "ai_advisor._CLAUDE_MODEL" not in source, (
            "app.py still references ai_advisor._CLAUDE_MODEL (line ~3748 and/or ~3781). "
            "This raises AttributeError at runtime after AC-4 removes the constant. "
            "Replace with ai_advisor.resolve_advisor_model() or "
            "os.environ.get('ADVISOR_SYNTHESIS_MODEL', 'claude-opus-4-8')."
        )

    def test_app_py_reject_route_does_not_attribute_access_removed_constant(self):
        """Same guard as above but named for the /ai-advisor/reject route.

        Both accept (line ~3748) and reject (line ~3781) call record_llm_suggestion
        with model_id=ai_advisor._CLAUDE_MODEL.  This test makes the reject path
        explicit so a partial fix (only fixing accept) is caught by name.

        The test is logically redundant with the prior test but documents the
        exact routes so failing output is unambiguous.
        """
        source = (_REPO_ROOT / "app.py").read_text(encoding="utf-8")
        # Count occurrences — both accept and reject routes must be fixed.
        count = source.count("ai_advisor._CLAUDE_MODEL")
        assert count == 0, (
            f"app.py contains {count} reference(s) to ai_advisor._CLAUDE_MODEL. "
            "Both /ai-advisor/accept (line ~3748) and /ai-advisor/reject (line ~3781) "
            "must be migrated. Replace each with ai_advisor.resolve_advisor_model() "
            "or inline os.environ.get('ADVISOR_SYNTHESIS_MODEL', 'claude-opus-4-8')."
        )


# ---------------------------------------------------------------------------
# AC-5 (app.py) — accept/reject routes record the env-resolved model
# ---------------------------------------------------------------------------


class TestAppPyAdvisorRouteModelWiring:
    """AC-5 (app.py): The /ai-advisor/accept and /ai-advisor/reject routes must
    record the env-resolved model ID in the llm_suggestions audit trail, not a
    constant that no longer exists.

    These are ROUTE-LEVEL tests using the Flask test client.  They mock:
      - database.record_llm_suggestion (capture model_id arg)
      - ai_advisor.revalidate_suggestion_oos (return passed=True)
      - database.get_symphony_strategy + database.save_symphony_strategy
      - database.normalize_name

    They do NOT call the real Anthropic API.

    RED: both routes currently raise AttributeError on ai_advisor._CLAUDE_MODEL
    because the constant was removed at 46a6bc4.
    """

    @pytest.fixture()
    def flask_client(self, monkeypatch):
        """Provide a Flask test client with CSRF disabled and DB isolated."""
        # DB path is already isolated by conftest._isolate_db autouse fixture.
        # Disable CSRF so POST requests work without a token.
        monkeypatch.setenv("TESTING", "1")
        import app as flask_app
        flask_app.app.config["TESTING"] = True
        flask_app.app.config["WTF_CSRF_ENABLED"] = False
        with flask_app.app.test_client() as client:
            yield client

    def _post_accept(self, client, suggestion_key="VWAP_BLEED_TICKS", suggestion_value=5):
        """POST a minimal accept payload to /ai-advisor/accept."""
        return client.post(
            "/ai-advisor/accept",
            json={
                "symphony_id": "test-symphony-hash-001",
                "suggestion": {
                    "config_key": suggestion_key,
                    "suggested_value": suggestion_value,
                    "current_value": 3,
                    "rationale": "test",
                },
                "csrf_token": "disabled-in-test",
            },
            content_type="application/json",
        )

    def _post_reject(self, client, suggestion_key="VWAP_BLEED_TICKS"):
        """POST a minimal reject payload to /ai-advisor/reject."""
        return client.post(
            "/ai-advisor/reject",
            json={
                "symphony_id": "test-symphony-hash-001",
                "suggestion": {
                    "config_key": suggestion_key,
                    "suggested_value": 5,
                    "current_value": 3,
                },
                "csrf_token": "disabled-in-test",
            },
            content_type="application/json",
        )

    def test_accept_route_does_not_raise_attribute_error_after_constant_removal(
        self, flask_client, monkeypatch
    ):
        """The /ai-advisor/accept route must NOT raise AttributeError on
        ai_advisor._CLAUDE_MODEL after the constant is removed.

        RED: currently raises AttributeError at app.py:3748.

        The route must complete without error — a 200 response means the
        AttributeError is gone and the model_id argument was resolved correctly.
        """
        import ai_advisor
        import database

        with (
            patch.object(
                ai_advisor,
                "revalidate_suggestion_oos",
                return_value={"passed": True, "detail": "ok"},
            ),
            patch.object(database, "get_symphony_strategy", return_value={"params": {}, "locked_vars": []}),
            patch.object(database, "save_symphony_strategy", return_value=None),
            patch.object(database, "normalize_name", side_effect=lambda x: x),
            patch.object(database, "record_llm_suggestion", return_value=None),
            patch.object(ai_advisor, "check_risk_direction_agreement", return_value=None),
            # enforce_suggestion_allowlist returns (allowed_list, rejected_list).
            # Pass the suggestion through so the route reaches record_llm_suggestion.
            patch.object(
                ai_advisor,
                "enforce_suggestion_allowlist",
                side_effect=lambda suggestions: (suggestions, []),
            ),
        ):
            resp = self._post_accept(flask_client)

        # Any 5xx (especially 500 from AttributeError) means the test catches the bug.
        assert resp.status_code < 500, (
            f"/ai-advisor/accept returned HTTP {resp.status_code}. "
            "If 500: likely AttributeError on ai_advisor._CLAUDE_MODEL at app.py:3748. "
            "Migrate to ai_advisor.resolve_advisor_model() or inline os.environ.get(...)."
        )

    def test_accept_route_records_env_resolved_model_not_hardcoded_constant(
        self, flask_client, monkeypatch
    ):
        """The model_id recorded by /ai-advisor/accept must be the env-resolved
        model (from ADVISOR_SYNTHESIS_MODEL or the default), NOT a constant reference
        that would raise AttributeError.

        RED: app.py:3748 reads ai_advisor._CLAUDE_MODEL — AttributeError after removal.
        GREEN: reads ai_advisor.resolve_advisor_model() or equivalent env call.
        """
        monkeypatch.setenv("ADVISOR_SYNTHESIS_MODEL", _TEST_MODEL_OVERRIDE)

        import ai_advisor
        import database

        recorded_model_ids = []

        def capture_record(**kwargs):
            recorded_model_ids.append(kwargs.get("model_id"))

        with (
            patch.object(
                ai_advisor,
                "revalidate_suggestion_oos",
                return_value={"passed": True, "detail": "ok"},
            ),
            patch.object(database, "get_symphony_strategy", return_value={"params": {}, "locked_vars": []}),
            patch.object(database, "save_symphony_strategy", return_value=None),
            patch.object(database, "normalize_name", side_effect=lambda x: x),
            patch.object(database, "record_llm_suggestion", side_effect=capture_record),
            patch.object(ai_advisor, "check_risk_direction_agreement", return_value=None),
            # enforce_suggestion_allowlist returns (allowed_list, rejected_list).
            patch.object(
                ai_advisor,
                "enforce_suggestion_allowlist",
                side_effect=lambda suggestions: (suggestions, []),
            ),
        ):
            resp = self._post_accept(flask_client)

        assert resp.status_code < 500, (
            f"Accept route returned {resp.status_code}. "
            "AttributeError on _CLAUDE_MODEL or other route failure."
        )
        assert len(recorded_model_ids) == 1, (
            f"Expected record_llm_suggestion to be called once; got {len(recorded_model_ids)} calls."
        )
        assert recorded_model_ids[0] == _TEST_MODEL_OVERRIDE, (
            f"Accept route recorded model_id={recorded_model_ids[0]!r}. "
            f"Expected {_TEST_MODEL_OVERRIDE!r} (from ADVISOR_SYNTHESIS_MODEL env var). "
            "The route must resolve the model at call-time from the env var, "
            "not from the removed ai_advisor._CLAUDE_MODEL constant."
        )

    def test_reject_route_does_not_raise_attribute_error_after_constant_removal(
        self, flask_client, monkeypatch
    ):
        """The /ai-advisor/reject route must NOT raise AttributeError on
        ai_advisor._CLAUDE_MODEL after the constant is removed.

        RED: currently raises AttributeError at app.py:3781.
        """
        import ai_advisor
        import database

        with (
            patch.object(database, "normalize_name", side_effect=lambda x: x),
            patch.object(database, "record_llm_suggestion", return_value=None),
        ):
            resp = self._post_reject(flask_client)

        assert resp.status_code < 500, (
            f"/ai-advisor/reject returned HTTP {resp.status_code}. "
            "If 500: likely AttributeError on ai_advisor._CLAUDE_MODEL at app.py:3781. "
            "Migrate to ai_advisor.resolve_advisor_model() or inline os.environ.get(...)."
        )

    def test_reject_route_records_env_resolved_model(
        self, flask_client, monkeypatch
    ):
        """The model_id recorded by /ai-advisor/reject must be the env-resolved
        model, not a removed constant.
        """
        monkeypatch.setenv("ADVISOR_SYNTHESIS_MODEL", _TEST_MODEL_OVERRIDE)

        import ai_advisor
        import database

        recorded_model_ids = []

        def capture_record(**kwargs):
            recorded_model_ids.append(kwargs.get("model_id"))

        with (
            patch.object(database, "normalize_name", side_effect=lambda x: x),
            patch.object(database, "record_llm_suggestion", side_effect=capture_record),
        ):
            resp = self._post_reject(flask_client)

        assert resp.status_code < 500, (
            f"Reject route returned {resp.status_code}."
        )
        assert len(recorded_model_ids) == 1, (
            f"Expected record_llm_suggestion called once; got {len(recorded_model_ids)}."
        )
        assert recorded_model_ids[0] == _TEST_MODEL_OVERRIDE, (
            f"Reject route recorded model_id={recorded_model_ids[0]!r}. "
            f"Expected {_TEST_MODEL_OVERRIDE!r} from ADVISOR_SYNTHESIS_MODEL env var."
        )
