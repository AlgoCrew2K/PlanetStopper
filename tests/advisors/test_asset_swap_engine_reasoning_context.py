"""RED tests — R2-3 AC-1: reasoning_context + correlation_data threading into
the reasoned swap generator's LLM prompt.

Contract pinned (mirrors R2-2's test_logic_change_engine_reasoning_context.py):
  ``advisors.asset_swap_engine.generate_reasoned_swap_candidates(
      symphony_id, raw_value, objective, *, reasoning_context=None,
      correlation_data=None, available_assets=None, tradeable_universe=None,
      max_candidates=MAX_SUGGESTED_CANDIDATES,
  ) -> list[SwapCandidate]`` — the LLM-backed replacement for the deleted
  fixed-statistical-sort ``generate_objective_directed_candidates``. Mock seam:
  ``advisors.asset_swap_engine._build_client()`` (same convention as
  ``logic_change_engine._build_client`` / ``build_plan_generator._build_client``).

  ``propose_operator_swap(..., *, reasoning_context=None, reasoning_manifest=None)``
  and ``suggest_swaps(..., *, reasoning_context=None, reasoning_manifest=None)``
  gain two new keyword params — the CALLER (the route, for the operator path)
  is responsible for calling ``ai_advisor.build_reasoning_context`` and
  threading its two return values in; the engine itself never calls it. Both
  entry points thread ``reasoning_context`` straight through to
  ``generate_reasoned_swap_candidates``.

This file does NOT re-test ``ai_advisor.build_reasoning_context``'s own
assembly logic (R2-1 shipped code, its own coverage) — it proves only:
  1. A given reasoning_context STRING is embedded verbatim in the actual LLM
     prompt payload (the SDK-boundary proof).
  2. ``correlation_data`` (Q4: kept as prompt EVIDENCE, never used for
     programmatic ranking anymore) reaches the prompt text too.
  3. The two public entry points thread reasoning_context/reasoning_manifest
     through to the generator unchanged.

No live network — the LLM client seam (_build_client) and get_tradeable_set
(network-bound Alpaca call) are mocked throughout.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_TREE_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "symphony_logic" / "sample_score_small.json"
)


@pytest.fixture(scope="module")
def ase():
    import advisors.asset_swap_engine as _ase  # noqa: PLC0415

    return _ase


@pytest.fixture(scope="module")
def fixture_tree():
    with open(_FIXTURE_TREE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(autouse=True)
def _permissive_universe(ase, monkeypatch):
    monkeypatch.setattr(ase, "get_tradeable_set", lambda **kwargs: frozenset({"IALT", "AGG"}))


# A hand-built reasoning-context string standing in for what
# ai_advisor.build_reasoning_context would actually return — this file tests the
# THREADING mechanism, not the assembler. Matches the convention established in
# test_logic_change_engine_reasoning_context.py's _FAKE_REASONING_CONTEXT.
_FAKE_REASONING_CONTEXT = (
    "OPERATOR'S CURRENT STRATEGY:\nHOLD UVIX\nHOLD TQQQ\n\n"
    "LIVE PERFORMANCE EVIDENCE:\ntrain_alpha=0.02 oos_alpha=0.01 baseline_decision=KEEP\n"
)


# ---------------------------------------------------------------------------
# Recording-mock SDK client
# ---------------------------------------------------------------------------


class _RecordingMockBlock:
    def __init__(self, payload):
        self.type = "tool_use"
        self.input = payload


class _RecordingMockResponse:
    def __init__(self, candidates):
        self.stop_reason = "tool_use"
        self.content = [_RecordingMockBlock({"candidates": candidates})]


class _RecordingMockClient:
    """Records every kwargs dict passed to messages.create()."""

    def __init__(self, candidates=None):
        self._candidates = candidates if candidates is not None else []
        self.calls: list[dict] = []

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.calls.append(kwargs)
            return _RecordingMockResponse(self._outer._candidates)

    @property
    def messages(self):
        return self._Messages(self)


def _prompt_text(client: _RecordingMockClient) -> str:
    assert client.calls, "generate_reasoned_swap_candidates never called client.messages.create()."
    return client.calls[0].get("messages", [{}])[0].get("content", "")


# ===========================================================================
# AC-1: reasoning_context reaches the actual SDK prompt payload.
# ===========================================================================


def test_reasoned_generator_embeds_reasoning_context_in_llm_prompt(ase, fixture_tree, monkeypatch):
    """AC-1: a truthy reasoning_context string must appear verbatim in the prompt
    sent to the (mocked) SDK client — proving the operator's real tree/stats/lens
    context, not just the objective name, reaches the LLM."""
    client = _RecordingMockClient(candidates=[])
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    objective = ase.SwapObjective(
        objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
    )
    ase.generate_reasoned_swap_candidates(
        "sym-1", fixture_tree, objective, reasoning_context=_FAKE_REASONING_CONTEXT
    )

    prompt = _prompt_text(client)
    assert "UVIX" in prompt, (
        "AC-1 GAP: the injected reasoning_context text does not appear in the "
        "generated prompt — the operator's real tree/stats context was dropped."
    )
    assert "oos_alpha=0.01" in prompt, (
        "AC-1 GAP: the reasoning_context's live-stats section did not reach the prompt."
    )


def test_reasoned_generator_omits_context_section_when_falsy(ase, fixture_tree, monkeypatch):
    """Complement: reasoning_context omitted or falsy (None/'') must produce a
    prompt with NO trace of the operator-context text — the scheduler's
    default-None call site must not leak a stale/empty section."""
    for falsy in (None, ""):
        client = _RecordingMockClient(candidates=[])
        monkeypatch.setattr(ase, "_build_client", lambda: client)
        objective = ase.SwapObjective(
            objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
        )
        ase.generate_reasoned_swap_candidates(
            "sym-1", fixture_tree, objective, reasoning_context=falsy
        )
        prompt = _prompt_text(client)
        assert "UVIX" not in prompt, (
            f"GAP: prompt contains reasoning-context content despite falsy "
            f"reasoning_context={falsy!r}."
        )


def test_reasoned_generator_never_dumps_raw_json_tree(ase, fixture_tree, monkeypatch):
    """Negative invariant (AC-1's 'never a raw JSON dump'): the prompt must never
    contain a full json.dumps() serialization of the raw tree."""
    client = _RecordingMockClient(candidates=[])
    monkeypatch.setattr(ase, "_build_client", lambda: client)
    objective = ase.SwapObjective(
        objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
    )
    ase.generate_reasoned_swap_candidates("sym-1", fixture_tree, objective)

    prompt = _prompt_text(client)
    full_dump = json.dumps(fixture_tree)
    assert full_dump not in prompt, (
        "AC-1 GAP: the prompt contains a full raw json.dumps() of the tree — "
        "must be a bounded, human-readable representation, never a raw dump."
    )


# ===========================================================================
# Q4: correlation_data is kept as prompt EVIDENCE (never programmatic ranking).
# ===========================================================================


def test_correlation_data_reaches_the_prompt_as_evidence(ase, fixture_tree, monkeypatch):
    """correlation_data (Q4: retained as evidence text, selection is the LLM's
    not a fixed statistical sort) must be visible in the prompt sent to the
    SDK — proving it still informs the LLM's reasoning even though it no
    longer drives a hand-coded ranking function."""
    client = _RecordingMockClient(candidates=[])
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    objective = ase.SwapObjective(
        objective_type="reduce_correlation", target_pair=("QQQ", "TQQQ"), measured_value=0.9
    )
    correlation_data = {"QQQ": [0.01, 0.02, -0.01], "TQQQ": [0.03, 0.06, -0.03]}
    ase.generate_reasoned_swap_candidates(
        "sym-1", fixture_tree, objective, correlation_data=correlation_data
    )

    prompt = _prompt_text(client)
    assert "QQQ" in prompt and "TQQQ" in prompt, (
        "AC-1/Q4 GAP: correlation_data's entity keys do not appear in the prompt — "
        "the correlation evidence was dropped rather than surfaced to the LLM."
    )


def test_correlation_data_omitted_produces_no_correlation_section(ase, fixture_tree, monkeypatch):
    """Complement: correlation_data omitted/empty must not leak a stale/empty
    evidence section referencing nonexistent data."""
    client = _RecordingMockClient(candidates=[])
    monkeypatch.setattr(ase, "_build_client", lambda: client)
    objective = ase.SwapObjective(
        objective_type="reduce_correlation", target_pair=None, measured_value=0.0
    )
    ase.generate_reasoned_swap_candidates("sym-1", fixture_tree, objective, correlation_data=None)

    prompt = _prompt_text(client)
    assert "TQQQ" not in prompt, (
        "GAP: prompt references correlation entity data despite correlation_data=None."
    )


# ===========================================================================
# Threading — the two public entry points pass reasoning_context/
# reasoning_manifest straight through to generate_reasoned_swap_candidates.
# ===========================================================================


def _patch_common(ase, monkeypatch, *, has_key=True):
    monkeypatch.setattr(ase, "_has_composer_key", lambda: has_key)
    monkeypatch.setattr(ase.database, "insert_advisor_observation", MagicMock(return_value=1))


def test_propose_operator_swap_threads_reasoning_context_to_generator(
    ase, fixture_tree, monkeypatch
):
    """propose_operator_swap (objective-only / reasoned mode — neither ticker
    supplied) must forward reasoning_context/reasoning_manifest to
    generate_reasoned_swap_candidates(...) unchanged."""
    _patch_common(ase, monkeypatch)
    gen_mock = MagicMock(return_value=[])
    monkeypatch.setattr(ase, "generate_reasoned_swap_candidates", gen_mock)

    objective = ase.SwapObjective(
        objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
    )
    manifest = {"tree": "present", "stats": "absent"}
    ase.propose_operator_swap(
        symphony_id="sym-1",
        score_tree=fixture_tree,
        objective=objective,
        reasoning_context=_FAKE_REASONING_CONTEXT,
        reasoning_manifest=manifest,
    )

    assert gen_mock.called, "GAP: generate_reasoned_swap_candidates was never called."
    _args, kwargs = gen_mock.call_args
    assert kwargs.get("reasoning_context") == _FAKE_REASONING_CONTEXT, (
        f"GAP: propose_operator_swap did not thread reasoning_context. kwargs={kwargs!r}"
    )


def test_suggest_swaps_threads_reasoning_context_to_generator(ase, fixture_tree, monkeypatch):
    """suggest_swaps must forward reasoning_context/reasoning_manifest to
    generate_reasoned_swap_candidates(...) unchanged (weekly-scheduler entry
    point, signature-symmetric with propose_operator_swap)."""
    _patch_common(ase, monkeypatch)
    gen_mock = MagicMock(return_value=[])
    monkeypatch.setattr(ase, "generate_reasoned_swap_candidates", gen_mock)

    objective = ase.SwapObjective(
        objective_type="reduce_correlation", target_pair=None, measured_value=0.0
    )
    manifest = {"tree": "present", "stats": "present"}
    ase.suggest_swaps(
        "sym-1",
        fixture_tree,
        objective,
        {},
        ["IALT", "AGG"],
        reasoning_context=_FAKE_REASONING_CONTEXT,
        reasoning_manifest=manifest,
    )

    assert gen_mock.called, "GAP: generate_reasoned_swap_candidates was never called."
    _args, kwargs = gen_mock.call_args
    assert kwargs.get("reasoning_context") == _FAKE_REASONING_CONTEXT, (
        f"GAP: suggest_swaps did not thread reasoning_context. kwargs={kwargs!r}"
    )


def test_suggest_swaps_omitted_reasoning_context_defaults_to_none(ase, fixture_tree, monkeypatch):
    """Sibling regression (Q5): the weekly scheduler's existing call shape (no
    reasoning_context kwarg at all — weekly_suggestions_scheduler.py is
    untouched this cycle) must still reach the generator with
    reasoning_context=None, not raise a TypeError for a missing required arg."""
    _patch_common(ase, monkeypatch)
    gen_mock = MagicMock(return_value=[])
    monkeypatch.setattr(ase, "generate_reasoned_swap_candidates", gen_mock)

    objective = ase.SwapObjective(
        objective_type="reduce_correlation", target_pair=None, measured_value=0.0
    )
    ase.suggest_swaps("sym-1", fixture_tree, objective, {}, ["IALT", "AGG"])

    assert gen_mock.called, "GAP: generate_reasoned_swap_candidates was never called."
    _args, kwargs = gen_mock.call_args
    assert kwargs.get("reasoning_context") is None, (
        f"GAP: omitted reasoning_context did not default to None. kwargs={kwargs!r}"
    )
