"""RED tests — M5 Chat (Explain-only) capability (advisors/advisor_chat.py).

Module under test: advisors.advisor_chat

AC bindings:
  AC-4.1  (EXPLAIN-NOT-ADVISE BOUNDARY — MANDATORY, the load-bearing M5 check)
           Chat MUST NOT issue trade directives, propose/apply config/logic/asset
           changes, or have any write path or path to accept/apply routes.
           Verifiable: import-graph + route guard + response-content contract.
  AC-4.2  Answers grounded in surfaced advisor data (observations, gate verdicts,
           diagnostic). Chat explains existing artifacts; does NOT generate new
           unvalidated recommendations.
  AC-4.3  Chat unavailable (no LLM key / error) → clear "chat unavailable",
           never a crash, never a fabricated trade instruction.
  AC-X1   No capability calls a Composer write endpoint. Only reads (GET /score)
           + stateless POST /api/v0.1/backtest.
  AC-X2   alpha_bot_execution.py does NOT import from advisors.advisor_chat.
  AC-X3   No advisor_observation write from chat — chat is purely Q&A, zero
           DB writes of any kind.

THE ADVERSARIAL FOCUS (the explain-only boundary, per team brief):

1. **No write path (AC-4.1 hard boundary)**: an implementation that touches
   accept/apply/mutation routes, writes to the DB, or touches Composer write
   endpoints MUST FAIL these tests.  We test this via import-graph inspection and
   a spy on database.insert_advisor_observation.

2. **No trade directives in the response** (AC-4.1): the response schema must not
   contain trade-action fields.  ChatResponse carries only answer + error.

3. **Artifact anchoring (AC-4.2)**: chat is anchored to a specific artifact — it
   explains existing data and must not call generate_*_candidates(), suggest_*(),
   run_backtest(), or similar production-generating functions.

4. **Graceful degradation (AC-4.3)**: no API key → ChatResponse(answer=None,
   error containing "chat unavailable"); LLM error → same pattern.
   Never raises.  Never writes anything.

5. **Live-path isolation (AC-X2)**: alpha_bot_execution.py must not import
   advisor_chat.  Tested via source-inspection.

API contract (based on existing partial GREEN implementation at advisors/advisor_chat.py):

    def explain_artifact(question: str, artifact: dict) -> ChatResponse:
        # On success: ChatResponse(answer=<explanation>, error=None)
        # On any failure: ChatResponse(answer=None, error=<non-empty str>)
        # NEVER raises.  NEVER writes to DB.  NEVER calls Composer write endpoints.

    @dataclass
    class ChatResponse:
        answer: str | None
        error: str | None

Client factory seam:
    Reuses ``ai_advisor._build_client()`` — tests patch ``ai_advisor._build_client``.

Mocking strategy:
  - ``ai_advisor._build_client`` is patched at the source attribute.
    The mock returns a fake client whose .messages.create() returns a fixture-
    derived response object (explanation text only — no trade directive fields).
  - database.insert_advisor_observation is spied upon to assert it is NEVER called.
  - No live Anthropic calls in any non-live test.  Live tests are marked
    @pytest.mark.live and excluded from the default suite.

Fixture: tests/fixtures/ai_advisor/m5/chat_engine_explain_only.json

No hardcoded producer-computed values.  Shape / contract assertions only.
"""

from __future__ import annotations

import json
import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixture loader — schema-derived, not producer-computed.
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "ai_advisor"
    / "m5"
    / "chat_engine_explain_only.json"
)


@pytest.fixture(scope="module")
def fixture() -> dict:
    """Load the M5 chat fixture — the spec anchor for these tests."""
    assert _FIXTURE_PATH.exists(), (
        f"M5 chat fixture not found at {_FIXTURE_PATH}. "
        "This file is the spec anchor for the explain-only boundary contract."
    )
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Sample artifacts — shape only, no producer values.
# ---------------------------------------------------------------------------

_ASSET_SWAP_ARTIFACT = {
    "artifact_type": "asset_swap_proposal",
    "artifact_id": "swap-001",
    "incumbent_asset": "GLD",
    "candidate_asset": "IALT",
    "symphony_id": "sym-alpha",
    "gate_verdict": "ADOPT_CANDIDATE",
    "objective_type": "reduce_correlation",
}

_CORRELATION_ARTIFACT = {
    "artifact_type": "correlation_diagnostic",
    "artifact_id": "corr-fleet-2026-05-31",
    "symphony_a": "sym-alpha",
    "symphony_b": "sym-beta",
    "correlation_value": 0.74,
    "obs_count": 62,
}

_GATE_VERDICT_ARTIFACT = {
    "artifact_type": "gate_verdict",
    "artifact_id": "gate-lc-007",
    "symphony_id": "sym-gamma",
    "verdict": "WITHHOLD",
    "reason": "BHY Yekutieli FDR veto",
    "n_candidates": 8,
}


# ---------------------------------------------------------------------------
# Fake LLM client — returns a well-shaped explanatory response.
# Never embeds trade directives.
# ---------------------------------------------------------------------------


def _make_fake_llm_response(
    text: str = "This swap reduces fleet correlation by replacing the incumbent with the candidate asset.",
):
    """Build a fake anthropic SDK response object.

    text is a fixture-style explanatory string — no producer-computed values
    (no rates, prices, trade amounts).  Shape assertions only.
    """
    content_block = SimpleNamespace(text=text)
    return SimpleNamespace(
        content=[content_block],
        model="claude-opus-4-7",
        stop_reason="end_turn",
    )


def _make_fake_client(text: str = None):
    """Return a fake anthropic client whose messages.create() returns a fixture response."""
    fake_response = _make_fake_llm_response(
        text=text or "This gate verdict means the candidate did not clear the adjusted threshold."
    )
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response
    return fake_client


# ===========================================================================
# Test 1 — Module existence and public API contract.
#
# GREEN must expose: explain_artifact() and ChatResponse.
# These tests are RED until advisors/advisor_chat.py is complete.
# ===========================================================================


def test_advisor_chat_module_is_importable():
    """advisors.advisor_chat must exist and be importable.

    This is RED until GREEN creates / completes the module.
    """
    import advisors.advisor_chat as ac  # noqa: F401


def test_advisor_chat_exposes_explain_artifact_function():
    """advisors.advisor_chat must expose explain_artifact as a callable.

    Signature: explain_artifact(question: str, artifact: dict) -> ChatResponse.
    """
    import advisors.advisor_chat as ac

    assert callable(getattr(ac, "explain_artifact", None)), (
        "advisors.advisor_chat must expose explain_artifact as a callable. "
        "It is the primary entrypoint for the explain-only chat surface."
    )


def test_advisor_chat_exposes_chat_response_class():
    """advisors.advisor_chat must expose ChatResponse — the return type.

    ChatResponse must have at least: answer and error fields.
    """
    import advisors.advisor_chat as ac

    cls = getattr(ac, "ChatResponse", None)
    assert cls is not None, (
        "advisors.advisor_chat must expose ChatResponse. "
        "This is the typed return value that carries the explanation."
    )


# ===========================================================================
# Test 2 — explain_artifact: success path with mocked LLM client.
#
# The explain-only boundary is the load-bearing contract (AC-4.1).
# We assert: shape of return value, no DB writes, answer is a non-empty string.
# ===========================================================================


def test_explain_artifact_returns_chat_response_on_success():
    """explain_artifact with a mocked LLM client must return ChatResponse(answer=<str>, error=None).

    Shape assertion: the returned object has a non-empty answer string and error is None.
    No hardcoded LLM output values — shape only.
    """
    import advisors.advisor_chat as ac

    with patch("ai_advisor._build_client", return_value=_make_fake_client()):
        result = ac.explain_artifact(
            question="Why did this swap survive the gate?",
            artifact=_ASSET_SWAP_ARTIFACT,
        )

    assert result is not None, "explain_artifact must return a ChatResponse"
    assert isinstance(result, ac.ChatResponse), (
        f"explain_artifact must return a ChatResponse instance, got {type(result)}"
    )
    assert result.error is None, (
        f"On success, ChatResponse.error must be None, got: {result.error!r}"
    )
    assert isinstance(result.answer, str) and result.answer, (
        "On success, ChatResponse.answer must be a non-empty string"
    )


def test_explain_artifact_never_calls_insert_advisor_observation():
    """Chat MUST NOT write to advisor_observations (AC-X3 / advisory-only contract).

    Chat is a Q&A surface that produces only explanatory text.  Writing an
    observation would imply the chat is surfacing a new recommendation —
    a violation of AC-4.2.
    """
    import advisors.advisor_chat as ac

    with patch("ai_advisor._build_client", return_value=_make_fake_client()):
        with patch("database.insert_advisor_observation") as mock_insert:
            ac.explain_artifact(
                question="Explain this swap to me.",
                artifact=_ASSET_SWAP_ARTIFACT,
            )

    assert mock_insert.call_count == 0, (
        f"database.insert_advisor_observation must NEVER be called from "
        f"explain_artifact — chat writes no DB records. "
        f"It was called {mock_insert.call_count} times with args: "
        f"{mock_insert.call_args_list}"
    )


def test_explain_artifact_never_writes_bot_state():
    """Chat MUST NOT write to bot_state (AC-4.1 / write-path guard).

    bot_state is the live execution table (database.save_state writes it).
    A chat function that can reach it has a write path to the execution surface —
    an absolute violation of AC-4.1.
    """
    import advisors.advisor_chat as ac

    with patch("ai_advisor._build_client", return_value=_make_fake_client()):
        with patch("database.save_state") as mock_save_state:
            ac.explain_artifact(
                question="What does this correlation mean?",
                artifact=_CORRELATION_ARTIFACT,
            )

    assert mock_save_state.call_count == 0, (
        "database.save_state must never be called from explain_artifact — "
        "save_state writes bot_state, the live execution table. "
        "Chat must have no write path (AC-4.1)."
    )


# ===========================================================================
# Test 3 — AC-4.1: no route to accept/apply/mutation paths.
#
# These tests verify that advisor_chat.py does NOT import or reference mutation
# routes or functions.  An implementation that can reach accept/apply routes
# fails AC-4.1.
# ===========================================================================


def test_advisor_chat_does_not_import_alpha_bot_execution():
    """advisors.advisor_chat must NOT import alpha_bot_execution (AC-X2).

    alpha_bot_execution is the 1-minute live engine.  Chat is offline advisory
    only.  Any import of the live engine from advisor_chat puts chat on the live
    path — an architecture violation.
    """
    saved = {
        name: sys.modules.get(name) for name in ("advisors.advisor_chat", "alpha_bot_execution")
    }
    try:
        sys.modules.pop("advisors.advisor_chat", None)
        pre_import_keys = set(sys.modules.keys())

        import importlib

        importlib.import_module("advisors.advisor_chat")

        post_import_keys = set(sys.modules.keys())
        newly_imported = post_import_keys - pre_import_keys
        assert "alpha_bot_execution" not in newly_imported, (
            "importing advisors.advisor_chat must NOT transitively import "
            "alpha_bot_execution — chat is an offline advisory surface and must "
            "never touch the 1-minute live execution path (AC-X2)"
        )
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)


def test_advisor_chat_source_does_not_reference_accept_route():
    """Source-inspection: advisor_chat.py must not reference the /accept route.

    The accept route (app.py:/ai-advisor/accept) is the mutation path that
    writes a config suggestion to live state.  Any advisor_chat that references
    this route has a potential write path to the execution surface.
    """
    advisor_chat_path = pathlib.Path(__file__).parent.parent.parent / "advisors" / "advisor_chat.py"
    if not advisor_chat_path.exists():
        pytest.skip("advisor_chat.py not yet created — GREEN creates it")

    source = advisor_chat_path.read_text(encoding="utf-8")

    assert "/ai-advisor/accept" not in source, (
        "advisor_chat.py must not reference the /accept mutation route. "
        "The accept route writes config to live state — an AC-4.1 violation."
    )
    assert "ai_advisor_accept" not in source, (
        "advisor_chat.py must not call ai_advisor_accept — "
        "that is the route handler that writes a config suggestion to live state."
    )


def test_advisor_chat_source_does_not_reference_composer_write_endpoints():
    """Source-inspection: advisor_chat.py must not reference Composer write endpoints.

    AC-X1: only reads (GET /score) + stateless POST /api/v0.1/backtest are allowed.
    The Composer write endpoints are: POST/PUT /symphonies, /copy, /deploy, go-to-cash.
    """
    advisor_chat_path = pathlib.Path(__file__).parent.parent.parent / "advisors" / "advisor_chat.py"
    if not advisor_chat_path.exists():
        pytest.skip("advisor_chat.py not yet created — GREEN creates it")

    source = advisor_chat_path.read_text(encoding="utf-8")

    forbidden_patterns = [
        "go-to-cash",
        "/copy",
        "/deploy",
        "PUT /api",
    ]
    for pattern in forbidden_patterns:
        assert pattern not in source, (
            f"advisor_chat.py must not reference Composer write endpoint pattern "
            f"'{pattern}' — chat is read-only (AC-X1)"
        )


def test_alpha_bot_execution_does_not_import_advisor_chat():
    """alpha_bot_execution.py must not import from advisors.advisor_chat (AC-X2).

    Source-inspection: parse alpha_bot_execution.py as text and check for any
    import of advisor_chat.  The live 1-minute engine must remain uncontaminated
    by advisor modules.
    """
    abe_path = pathlib.Path(__file__).parent.parent.parent / "alpha_bot_execution.py"
    assert abe_path.exists(), (
        f"alpha_bot_execution.py not found at {abe_path} — this is a project-critical file"
    )

    source = abe_path.read_text(encoding="utf-8")

    assert "advisor_chat" not in source, (
        "alpha_bot_execution.py must not import advisor_chat — "
        "chat is an offline advisory surface (AC-X2). "
        "Importing it would put the chat module on the 1-minute live path."
    )


# ===========================================================================
# Test 4 — AC-4.3: graceful degradation when LLM is unavailable.
#
# No API key → ChatResponse(answer=None, error containing "chat unavailable").
# LLM API error → same pattern.
# NEVER raises.  NEVER writes to DB.  NEVER returns a fabricated trade instruction.
# ===========================================================================


def test_explain_artifact_returns_error_when_api_key_missing():
    """No ANTHROPIC_API_KEY → ChatResponse(answer=None, error containing 'chat unavailable').

    AC-4.3: the operator must see a clear 'chat unavailable' message, not a crash.
    """
    import advisors.advisor_chat as ac

    def _raise_no_key():
        raise RuntimeError("ANTHROPIC_API_KEY is not set — chat is unavailable.")

    with patch("ai_advisor._build_client", side_effect=_raise_no_key):
        result = ac.explain_artifact(
            question="Why did this swap survive?",
            artifact=_ASSET_SWAP_ARTIFACT,
        )

    assert result.answer is None, "When API key is missing, ChatResponse.answer must be None"
    assert isinstance(result.error, str) and result.error, (
        "When API key is missing, ChatResponse.error must be a non-empty string"
    )
    error_lower = result.error.lower()
    assert "chat unavailable" in error_lower or "unavailable" in error_lower, (
        f"Error message must contain 'chat unavailable' or 'unavailable' "
        f"so the UI can show the operator a clear status. Got: {result.error!r}"
    )


def test_explain_artifact_never_raises_on_api_key_missing():
    """explain_artifact must NEVER raise when API key is missing (AC-4.3).

    The whole contract: graceful degradation to ChatResponse(answer=None, error=...).
    A raised exception crashes the route handler.
    """
    import advisors.advisor_chat as ac

    def _raise_no_key():
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")

    # The test passes only if explain_artifact does NOT raise.
    with patch("ai_advisor._build_client", side_effect=_raise_no_key):
        try:
            result = ac.explain_artifact(
                question="What does this correlation figure mean?",
                artifact=_CORRELATION_ARTIFACT,
            )
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"explain_artifact raised {type(exc).__name__}: {exc} — "
                "it must NEVER raise; the contract is to return "
                "ChatResponse(answer=None, error=<message>). "
                "A crash here propagates to the Flask route handler."
            )


def test_explain_artifact_returns_error_on_llm_api_error():
    """LLM API error → ChatResponse(answer=None, non-empty error string).

    An API error mid-call (timeout, rate limit, SDK exception) must degrade
    to ChatResponse(answer=None, error=...), not crash.
    """
    import advisors.advisor_chat as ac

    class _FakeLLMError(Exception):
        pass

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = _FakeLLMError("Rate limit exceeded.")

    with patch("ai_advisor._build_client", return_value=fake_client):
        result = ac.explain_artifact(
            question="Why was this logic change withheld?",
            artifact=_GATE_VERDICT_ARTIFACT,
        )

    assert result.answer is None, "On LLM API error, ChatResponse.answer must be None"
    assert isinstance(result.error, str) and result.error, (
        "On LLM API error, ChatResponse.error must be a non-empty string"
    )


def test_explain_artifact_never_raises_on_llm_api_error():
    """explain_artifact must NEVER raise on LLM API error (AC-4.3).

    Covers the never-raise contract for a different failure mode: the client was
    built successfully but the API call itself fails.
    """
    import advisors.advisor_chat as ac

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = Exception("Connection timeout.")

    with patch("ai_advisor._build_client", return_value=fake_client):
        try:
            result = ac.explain_artifact(
                question="Why did this swap survive?",
                artifact=_ASSET_SWAP_ARTIFACT,
            )
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"explain_artifact raised {type(exc).__name__}: {exc} on LLM error — "
                "must never raise; return ChatResponse(answer=None, error=...) instead."
            )


def test_explain_artifact_does_not_write_db_when_unavailable():
    """When LLM is unavailable, explain_artifact must write NOTHING to the DB.

    Intersection of AC-4.3 and AC-X3: even in error states, no DB mutation
    happens.  A write-on-failure would insert a fabricated or error-state entry
    as an advisor_observation — a dangerous error amplification.
    """
    import advisors.advisor_chat as ac

    def _raise_no_key():
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")

    with patch("ai_advisor._build_client", side_effect=_raise_no_key):
        with patch("database.insert_advisor_observation") as mock_insert:
            ac.explain_artifact(
                question="Explain this.",
                artifact=_ASSET_SWAP_ARTIFACT,
            )

    assert mock_insert.call_count == 0, (
        "database.insert_advisor_observation must not be called when LLM is "
        "unavailable — chat writes no DB records in any code path."
    )


# ===========================================================================
# Test 5 — AC-4.2: artifact anchoring — chat does NOT generate new
# unvalidated recommendations.
#
# The adversarial angle: a chat implementation that calls generate_*_candidates()
# or suggest_*() would generate NEW recommendations — an AC-4.2 violation.
# We verify via spy that the swap/logic/correlation generation paths are never
# invoked from explain_artifact.
# ===========================================================================


def test_explain_artifact_does_not_call_suggest_swaps():
    """explain_artifact must NOT call advisors.asset_swap_engine.suggest_swaps.

    A chat that calls suggest_swaps would generate NEW unvalidated swap
    recommendations — an AC-4.2 violation.  Chat explains existing artifacts;
    it does not generate new ones.
    """
    import advisors.advisor_chat as ac

    with patch("ai_advisor._build_client", return_value=_make_fake_client()):
        with patch("advisors.asset_swap_engine.suggest_swaps") as mock_suggest:
            ac.explain_artifact(
                question="Should I try other swaps?",
                artifact=_ASSET_SWAP_ARTIFACT,
            )

    assert mock_suggest.call_count == 0, (
        "explain_artifact must NOT call asset_swap_engine.suggest_swaps — "
        "calling it would generate NEW unvalidated swap recommendations, "
        "violating the explain-only boundary (AC-4.2). "
        f"It was called {mock_suggest.call_count} times."
    )


def test_explain_artifact_does_not_call_suggest_logic_changes():
    """explain_artifact must NOT call advisors.logic_change_engine.suggest_logic_changes.

    Same adversarial boundary as the swap test: generating new logic-change
    candidates from a chat call violates AC-4.2.
    """
    import advisors.advisor_chat as ac

    with patch("ai_advisor._build_client", return_value=_make_fake_client()):
        with patch("advisors.logic_change_engine.suggest_logic_changes") as mock_suggest:
            ac.explain_artifact(
                question="What logic change should I try instead?",
                artifact=_GATE_VERDICT_ARTIFACT,
            )

    assert mock_suggest.call_count == 0, (
        "explain_artifact must NOT call logic_change_engine.suggest_logic_changes — "
        "calling it would generate NEW unvalidated logic-change proposals, "
        "violating the explain-only boundary (AC-4.2). "
        f"It was called {mock_suggest.call_count} times."
    )


def test_explain_artifact_does_not_call_run_backtest():
    """explain_artifact must NOT call advisors.composer_backtest_client.run_backtest.

    Chat is explain-only.  Running a NEW backtest from a chat call is an
    AC-4.2 violation: it would generate new data that could be confused for
    a recommendation surface.
    """
    import advisors.advisor_chat as ac

    with patch("ai_advisor._build_client", return_value=_make_fake_client()):
        with patch("advisors.composer_backtest_client.run_backtest") as mock_bt:
            ac.explain_artifact(
                question="Can you backtest a variant of this for me?",
                artifact=_ASSET_SWAP_ARTIFACT,
            )

    assert mock_bt.call_count == 0, (
        "explain_artifact must NOT call run_backtest — running a backtest from "
        "chat would generate new unvalidated data (AC-4.2 violation). "
        f"It was called {mock_bt.call_count} times."
    )


# ===========================================================================
# Test 6 — AC-4.1 adversarial: the ChatResponse schema has NO action fields.
#
# An implementation that adds "apply_this_trade" or "trade_directive" fields
# to ChatResponse would fail these tests.  The response type must be
# structurally incapable of carrying a trade instruction.
# ===========================================================================


def test_chat_response_has_no_trade_action_fields():
    """ChatResponse must NOT have fields that could carry a trade instruction.

    If the dataclass has fields like apply_trade, trade_directive, action, or
    config_write, it is a structural violation of AC-4.1.
    The schema itself should make the explain-only boundary visible.
    """
    import advisors.advisor_chat as ac

    cls = getattr(ac, "ChatResponse", None)
    if cls is None:
        pytest.skip("ChatResponse not yet implemented — GREEN creates it")

    if hasattr(cls, "__dataclass_fields__"):
        field_names = set(cls.__dataclass_fields__.keys())
    elif hasattr(cls, "_fields"):
        field_names = set(cls._fields)
    elif hasattr(cls, "model_fields"):
        field_names = set(cls.model_fields.keys())
    else:
        import inspect

        sig = inspect.signature(cls.__init__)
        field_names = set(sig.parameters.keys()) - {"self"}

    forbidden_fields = {
        "apply_trade",
        "trade_directive",
        "action",
        "config_write",
        "accept",
        "deploy",
        "mutation",
        "trade",
        "execute",
    }
    violations = field_names & forbidden_fields
    assert not violations, (
        f"ChatResponse has fields that imply action: {sorted(violations)}. "
        "The response type must be structurally incapable of carrying a trade "
        "instruction — remove these fields to enforce AC-4.1."
    )


def test_chat_response_has_required_explain_only_fields():
    """ChatResponse must have at least answer and error fields.

    These two fields encode the entire graceful-degradation contract (AC-4.3):
    exactly one is non-None in normal operation.  An implementation that uses
    a 2-tuple or a bare string instead of a typed dataclass is not testable.
    """
    import advisors.advisor_chat as ac

    cls = getattr(ac, "ChatResponse", None)
    if cls is None:
        pytest.skip("ChatResponse not yet implemented — GREEN creates it")

    if hasattr(cls, "__dataclass_fields__"):
        field_names = set(cls.__dataclass_fields__.keys())
    elif hasattr(cls, "_fields"):
        field_names = set(cls._fields)
    elif hasattr(cls, "model_fields"):
        field_names = set(cls.model_fields.keys())
    else:
        import inspect

        sig = inspect.signature(cls.__init__)
        field_names = set(sig.parameters.keys()) - {"self"}

    required = {"answer", "error"}
    missing = required - field_names
    assert not missing, (
        f"ChatResponse is missing required fields: {sorted(missing)}. "
        "answer carries the explanation on success; error carries the "
        "degradation message on failure. Both must be present."
    )


# ===========================================================================
# Test 7 — AC-4.3: the unavailability message contains "chat unavailable".
#
# The constant CHAT_UNAVAILABLE_PREFIX must start with "chat unavailable"
# so the UI's "chat unavailable" condition check is reliable.
# ===========================================================================


def test_chat_unavailable_prefix_constant_contains_required_phrase():
    """CHAT_UNAVAILABLE_PREFIX must start with 'chat unavailable' (lowercase).

    The Flask route and/or UI checks for this prefix to decide whether to show
    the 'chat unavailable' UI state.  If the constant changes, the UI check
    breaks silently.  This test pins the contract.
    """
    import advisors.advisor_chat as ac

    prefix = getattr(ac, "CHAT_UNAVAILABLE_PREFIX", None)
    assert prefix is not None, (
        "advisors.advisor_chat must expose CHAT_UNAVAILABLE_PREFIX — "
        "the UI uses it to detect and display the 'chat unavailable' state."
    )
    assert prefix.lower().startswith("chat unavailable"), (
        f"CHAT_UNAVAILABLE_PREFIX must start with 'chat unavailable' "
        f"(case-insensitive), got: {prefix!r}"
    )


def test_explain_artifact_error_starts_with_unavailable_prefix_on_no_key():
    """Error returned on missing API key must start with CHAT_UNAVAILABLE_PREFIX.

    This pins the contract between the error message and the UI check:
    the UI shows the 'chat unavailable' panel when the error starts with
    CHAT_UNAVAILABLE_PREFIX.  If explain_artifact returns a differently-prefixed
    error, the UI shows the wrong state.
    """
    import advisors.advisor_chat as ac

    def _raise_no_key():
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")

    with patch("ai_advisor._build_client", side_effect=_raise_no_key):
        result = ac.explain_artifact(
            question="Explain this.",
            artifact=_ASSET_SWAP_ARTIFACT,
        )

    assert result.answer is None
    prefix = getattr(ac, "CHAT_UNAVAILABLE_PREFIX", "chat unavailable")
    assert result.error.lower().startswith(prefix.lower()), (
        f"Error message must start with CHAT_UNAVAILABLE_PREFIX={prefix!r}. Got: {result.error!r}"
    )


# ===========================================================================
# Test 8 — AC-4.1 route guard: app.py chat route must be constrained.
#
# Source-inspection test: any chat route that calls accept/apply/mutation
# functions would fail AC-4.1.  We parse app.py for chat route handlers
# and verify they contain no mutation calls.
# ===========================================================================


def test_app_py_chat_route_does_not_call_accept_function():
    """If a chat route exists in app.py, it must not call accept/mutation functions.

    This is a source-inspection test.  Once GREEN adds a chat route, this test
    verifies the route handler does not call mutation functions.
    """
    app_path = pathlib.Path(__file__).parent.parent.parent / "app.py"
    assert app_path.exists(), f"app.py not found at {app_path}"

    source = app_path.read_text(encoding="utf-8")

    # Find the chat route definition block (if it exists yet).
    chat_markers = ["/ai-advisor/chat", "advisor_chat_route", "ai_advisor_chat"]
    chat_route_idx = -1
    for marker in chat_markers:
        idx = source.find(marker)
        if idx != -1:
            chat_route_idx = idx
            break

    if chat_route_idx == -1:
        # Chat route not yet added — GREEN will add it.
        pytest.skip("Chat route not yet added to app.py — GREEN adds it")

    # Extract a window around the chat route definition (~200 lines).
    window = source[chat_route_idx : chat_route_idx + 8000]

    # Use call-site patterns (trailing paren) to avoid false positives on
    # docstring mentions like "MUST NOT call revalidate_suggestion_oos".
    # A docstring saying "must not call X" is not a mutation call.
    forbidden_in_chat_route = [
        "ai_advisor_accept(",
        "revalidate_suggestion_oos(",
        "save_symphony_strategy(",
        "update_bot_state(",
        "flush_state(",
    ]
    for forbidden in forbidden_in_chat_route:
        assert forbidden not in window, (
            f"The chat route handler contains call site '{forbidden}' — a mutation function. "
            "Chat must have no write path (AC-4.1). "
            "Remove this call from the chat route handler."
        )


# ===========================================================================
# Test 9 — Graceful handling of an empty LLM text response.
#
# The SDK may return a response with no text blocks (e.g. stop_reason=max_tokens
# with no content).  This must degrade gracefully, not crash or return None answer
# silently.
# ===========================================================================


def test_explain_artifact_handles_empty_llm_response():
    """An empty LLM response (no text in content) must degrade to error, not crash.

    If the SDK returns a response with empty content blocks, explain_artifact
    must return ChatResponse(answer=None, error=<non-empty string>).
    It must not return ChatResponse(answer="", error=None) — an empty answer
    is indistinguishable from an absent answer at the UI layer.
    """
    import advisors.advisor_chat as ac

    empty_response = SimpleNamespace(
        content=[],  # no blocks at all
        model="claude-opus-4-7",
        stop_reason="max_tokens",
    )
    fake_client = MagicMock()
    fake_client.messages.create.return_value = empty_response

    with patch("ai_advisor._build_client", return_value=fake_client):
        result = ac.explain_artifact(
            question="Explain this.",
            artifact=_CORRELATION_ARTIFACT,
        )

    # Either an error or a non-empty answer is acceptable; a blank answer is not.
    if result.answer is not None:
        assert result.answer.strip(), (
            "ChatResponse.answer must not be an empty or whitespace-only string — "
            "an empty answer is indistinguishable from 'nothing returned' at the UI. "
            "Return ChatResponse(answer=None, error=...) for empty responses."
        )
    else:
        # answer is None → error must be set
        assert isinstance(result.error, str) and result.error, (
            "When answer is None (empty LLM response), error must be a non-empty string"
        )


# ===========================================================================
# Test 10 — Fixture schema validation.
# ===========================================================================


def test_m5_fixture_is_valid_and_has_required_keys(fixture):
    """The M5 chat fixture must be valid JSON with required structural keys."""
    required_keys = {
        "_fixture_provenance",
        "_layer",
        "_binding_spec",
        "chat_explain_only_boundary",
        "artifact_anchor_contract",
        "llm_mock_contract",
        "chat_unavailable_contract",
        "route_guard_contract",
        "live_path_guard",
        "response_shape_contract",
        "advisory_only_persistence_contract",
    }
    missing = required_keys - set(fixture.keys())
    assert not missing, f"M5 chat fixture is missing required top-level keys: {sorted(missing)}."


def test_m5_fixture_provenance_is_schema_derived(fixture):
    """The fixture provenance must be 'schema-derived' (not producer-computed)."""
    assert fixture.get("_fixture_provenance") == "schema-derived", (
        f"Fixture provenance must be 'schema-derived', got {fixture.get('_fixture_provenance')!r}."
    )


def test_m5_fixture_live_path_guard_says_no_import(fixture):
    """The fixture must declare alpha_bot_execution_imports_chat=False."""
    guard = fixture.get("live_path_guard", {})
    assert guard.get("alpha_bot_execution_imports_chat") is False, (
        "Fixture live_path_guard.alpha_bot_execution_imports_chat must be false."
    )


def test_m5_fixture_advisory_only_says_no_db_writes(fixture):
    """The fixture must declare that chat produces no DB writes."""
    contract = fixture.get("advisory_only_persistence_contract", {})
    assert contract.get("must_not_call_insert_advisor_observation") is True
    assert contract.get("must_not_write_symphony_strategies") is True
    assert contract.get("must_not_write_bot_state") is True
