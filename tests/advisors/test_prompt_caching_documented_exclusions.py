"""RED tests -- documented prompt-caching EXCLUSIONS for four advisor SDK
call sites (cache-fix cycle, DE-ADVISOR-CACHE-001).

Team-lead ruling (this SUPERSEDES an earlier draft that would have pinned a
cache_control breakpoint on advisor_chat.py -- superseded after cache-impl's
REAL ``client.messages.count_tokens()`` ground truth came back): these four
sites' stable (non-per-call-volatile) prompt text is well under every
current model's minimum-cacheable-prefix floor. Adding a cache_control
marker here would be a silent economic no-op
(``cache_creation_input_tokens`` == 0 forever, per the API's documented
behavior for a too-short prefix), not a real fix. DOCUMENT-EXCLUDE: pin
TODAY's bare-string (no cache_control) shape as the intended, permanent
behavior for this cycle, so a future edit doesn't "helpfully" add a marker
here without re-deriving this case.

Measured (cache-impl, real ``count_tokens``, no mocking of the prompt
builders themselves -- see cache-impl's SendMessage for the full readout):
  - asset_swap_engine.py (generate_reasoned_swap_candidates): 393 tok
    stable-only, ~745 tok generously combined with a tool-schema proxy.
    Model floor: 2048 (fable-5). Bounded to a 40-ticker sample + ~3 static
    lines by design -- no restructuring rescues this without growing the
    universe sample, which is a scope decision outside this cycle.
  - logic_change_engine.py (generate_reasoned_logic_candidates): ~98 tok on
    a representative single-param probe. Model floor: 2048 (fable-5).
  - advisor_chat.py (explain_artifact): ~537 tok
    (_EXPLAIN_ONLY_SYSTEM_PROMPT). Model floor: 4096
    (claude-opus-4-8, the ADVISOR_SYNTHESIS_MODEL default) -- the HIGHEST
    floor of any current model.
  - ai_advisor.py (request_suggestions): ~79 tok (the static instructions
    portion of _build_messages, excluding the per-call JSON context tail).
    Model floor: 2048 (fable-5, model_config.get_advisor_suggestion_model()
    default).

lens_pipeline.py was excluded from the start of this cycle (same reasoning,
independently converged on by the PM/team-lead/cache-impl before any RED was
written) and has no test here.

No live network anywhere -- the LLM client seam is mocked throughout; no
live count_tokens/messages.create/messages.parse call is made from this file.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from tests.advisors._prompt_cache_test_helpers import find_cache_control_blocks

# ===========================================================================
# asset_swap_engine.py -- generate_reasoned_swap_candidates
# ===========================================================================


def _swap_fixture_tree():
    """A real-shape (unwrapped) raw_value tree -- extract_tickers walks
    "children", not a build-plan-DSL "root" wrapper, so the tree IS the root
    node itself."""
    return {
        "kind": "weight",
        "scheme": "equal",
        "children": [
            {"kind": "asset", "ticker": "SPY"},
            {"kind": "asset", "ticker": "TLT"},
        ],
    }


def test_asset_swap_engine_request_has_no_cache_control_breakpoint(monkeypatch):
    import advisors.asset_swap_engine as ase

    calls: list[dict] = []
    client = MagicMock()

    def _create(**kwargs):
        calls.append(kwargs)
        block = MagicMock()
        block.type = "tool_use"
        block.input = {"candidates": []}
        response = MagicMock()
        response.content = [block]
        return response

    client.messages.create.side_effect = _create
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    ase.generate_reasoned_swap_candidates(
        "sym-1",
        _swap_fixture_tree(),
        ase.SwapObjective(objective_type="reduce_drawdown", target_pair=None, measured_value=0.0),
        # Bypass get_tradeable_set() entirely (a live Alpaca call) -- this
        # test inspects the request shape, not the real tradeable universe.
        tradeable_universe=frozenset({"SPY", "TLT", "AGG"}),
    )

    assert calls, "generate_reasoned_swap_candidates never called client.messages.create()."
    content = calls[-1].get("messages", [{}])[0].get("content", "")
    assert isinstance(content, str), (
        "asset_swap_engine.py's prompt is documented-excluded from this cycle's caching fix "
        f"(393 tok stable vs 2048 floor) -- content must stay a bare string, got "
        f"{type(content).__name__}. If this now carries a cache_control breakpoint, the "
        "token-count rationale above must be re-verified and this test updated, not deleted."
    )
    assert not find_cache_control_blocks(content), (
        "a cache_control marker appeared on a documented-excluded site -- re-verify the "
        "token count before adding one; a marker below the model's minimum-cacheable-"
        "prefix floor is a silent no-op, not a fix."
    )


# ===========================================================================
# logic_change_engine.py -- generate_reasoned_logic_candidates
# ===========================================================================


def test_logic_change_engine_request_has_no_cache_control_breakpoint(monkeypatch):
    import advisors.logic_change_engine as lce

    calls: list[dict] = []
    client = MagicMock()

    def _create(**kwargs):
        calls.append(kwargs)
        block = MagicMock()
        block.type = "tool_use"
        block.input = {"edits": []}
        response = MagicMock()
        response.content = [block]
        return response

    client.messages.create.side_effect = _create
    monkeypatch.setattr(lce, "_build_client", lambda: client)

    raw_value = {"root": {"kind": "weight", "window": 30}}
    lce.generate_reasoned_logic_candidates(
        "sym-1",
        raw_value,
        lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0),
    )

    assert calls, "generate_reasoned_logic_candidates never called client.messages.create()."
    content = calls[-1].get("messages", [{}])[0].get("content", "")
    assert isinstance(content, str), (
        "logic_change_engine.py's prompt is documented-excluded from this cycle's caching "
        f"fix (~98 tok stable vs 2048 floor) -- content must stay a bare string, got "
        f"{type(content).__name__}."
    )
    assert not find_cache_control_blocks(content), (
        "a cache_control marker appeared on a documented-excluded site -- re-verify the "
        "token count before adding one."
    )


# ===========================================================================
# advisors/advisor_chat.py -- explain_artifact
# ===========================================================================


_MINIMAL_ARTIFACT = {
    "artifact_type": "asset_swap_proposal",
    "artifact_id": "swap-001",
    "symphony_id": "sym-alpha",
    "gate_verdict": "ADOPT_CANDIDATE",
}


def test_advisor_chat_request_has_no_cache_control_breakpoint(monkeypatch):
    import ai_advisor
    from advisors import advisor_chat

    calls: list[dict] = []
    client = MagicMock()

    def _create(**kwargs):
        calls.append(kwargs)
        block = MagicMock()
        block.text = "An explanation."
        response = MagicMock()
        response.content = [block]
        return response

    client.messages.create.side_effect = _create
    monkeypatch.setattr(ai_advisor, "_build_client", lambda: client)

    result = advisor_chat.explain_artifact("Why did this pass?", _MINIMAL_ARTIFACT)

    assert calls, "explain_artifact never called client.messages.create()."
    system = calls[-1].get("system", "")
    assert isinstance(system, str), (
        "advisor_chat.py's system prompt is documented-excluded from this cycle's caching "
        "fix (~537 tok vs the 4096-tok Opus 4.8 floor, the highest of any current model) -- "
        f"system must stay a bare string, got {type(system).__name__}."
    )
    assert not find_cache_control_blocks(system), (
        "a cache_control marker appeared on a documented-excluded site -- re-verify the "
        "token count (and which model ADVISOR_SYNTHESIS_MODEL resolves to) before adding one."
    )
    assert result.error is None, f"explain_artifact unexpectedly errored: {result.error!r}"


# ===========================================================================
# ai_advisor.py -- request_suggestions
# ===========================================================================


def test_ai_advisor_request_suggestions_has_no_cache_control_breakpoint(monkeypatch):
    import ai_advisor

    calls: list[dict] = []
    client = MagicMock()

    def _parse(**kwargs):
        calls.append(kwargs)
        from anthropic.types.parsed_message import ParsedMessage, ParsedTextBlock
        from anthropic.types.usage import Usage

        parsed = ai_advisor.ConfigSuggestionsResponse(suggestions=[])
        block = ParsedTextBlock.model_construct(
            type="text", text='{"suggestions": []}', parsed_output=parsed, citations=None
        )
        return ParsedMessage.model_construct(
            id="msg_fake",
            type="message",
            role="assistant",
            model="claude-fable-5",
            content=[block],
            stop_reason="end_turn",
            stop_sequence=None,
            usage=Usage(input_tokens=1, output_tokens=1),
        )

    client.messages.parse.side_effect = _parse
    monkeypatch.setattr(ai_advisor, "_build_client", lambda: client)

    ai_advisor.request_suggestions({"symphony_name": "Test"})

    assert calls, "request_suggestions never called client.messages.parse()."
    content = calls[-1].get("messages", [{}])[0].get("content", "")
    assert isinstance(content, str), (
        "ai_advisor.py's request_suggestions prompt is documented-excluded from this "
        f"cycle's caching fix (~79 tok static instructions vs 2048 floor) -- content must "
        f"stay a bare string, got {type(content).__name__}."
    )
    assert not find_cache_control_blocks(content), (
        "a cache_control marker appeared on a documented-excluded site -- re-verify the "
        "token count before adding one."
    )
