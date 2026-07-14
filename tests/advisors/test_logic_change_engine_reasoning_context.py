"""RED tests — R2-2 AC-1: reasoning_context threading into the reasoned logic-change
generator's LLM prompt.

Contract pinned (per team-lead's ruling + r2-2-fe coordination, R2-2):
  ``advisors.logic_change_engine.generate_reasoned_logic_candidates(
      symphony_id, raw_value, objective, *, reasoning_context=None,
      change_description=None, max_candidates=MAX_SUGGESTED_CANDIDATES,
  ) -> list[LogicTweak]`` — the new LLM-backed replacement for the fixed-multiplier
  ``generate_objective_directed_candidates`` on the reasoned path. Mock seam:
  ``advisors.logic_change_engine._build_client()`` (same convention as
  ``build_plan_generator._build_client`` / ``ai_advisor._build_client``).

  ``propose_operator_logic_change(..., *, reasoning_context=None,
  reasoning_manifest=None)`` and ``suggest_logic_changes(..., *,
  reasoning_context=None, reasoning_manifest=None)`` gain two new keyword params
  — the CALLER (the route, for the operator path) is responsible for calling
  ``ai_advisor.build_reasoning_context`` and threading its two return values in;
  the engine itself never calls it. Both entry points thread ``reasoning_context``
  straight through to ``generate_reasoned_logic_candidates``.

Design choice: this file does NOT re-test ``ai_advisor.build_reasoning_context``'s
own assembly logic (rendered tree + live stats + 5 lens blocks) — that is R2-1
shipped code with its own test coverage. This file proves two things only:
  1. The reasoned generator embeds a given reasoning_context STRING verbatim into
     the actual LLM prompt payload (the SDK-boundary proof — mirrors
     test_build_plan_generator_reasoning_context.py's
     test_generate_build_plans_threads_reasoning_context_into_sdk_prompt).
  2. The two public entry points thread reasoning_context/reasoning_manifest
     through to the generator unchanged (mirrors SB's
     test_reasoning_context_threaded_into_generate_candidate_trees_call).

No live network — the LLM client seam (_build_client) is mocked throughout.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_TREE_PATH = _REPO_ROOT / "tests" / "fixtures" / "symphony_logic" / "sample_score_small.json"


@pytest.fixture(scope="module")
def lce():
    import advisors.logic_change_engine as _lce  # noqa: PLC0415

    return _lce


@pytest.fixture(scope="module")
def fixture_tree():
    with open(_FIXTURE_TREE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


# A hand-built reasoning-context string standing in for what
# ai_advisor.build_reasoning_context would actually return — this file tests the
# THREADING mechanism, not the assembler. Matches the exact convention established
# in test_build_plan_generator_reasoning_context.py's _FAKE_REASONING_CONTEXT.
_FAKE_REASONING_CONTEXT = (
    "OPERATOR'S CURRENT STRATEGY:\nHOLD UVIX\nHOLD TQQQ\n\n"
    "LIVE PERFORMANCE EVIDENCE:\ntrain_alpha=0.02 oos_alpha=0.01 baseline_decision=KEEP\n"
)


# ---------------------------------------------------------------------------
# Recording-mock SDK client — mirrors test_build_plan_generator_reasoning_context.py
# ---------------------------------------------------------------------------


class _RecordingMockBlock:
    def __init__(self, payload):
        self.type = "tool_use"
        self.input = payload


class _RecordingMockResponse:
    def __init__(self, edits):
        self.stop_reason = "tool_use"
        self.content = [_RecordingMockBlock({"edits": edits})]


class _RecordingMockClient:
    """Records every kwargs dict passed to messages.create()."""

    def __init__(self, edits=None):
        self._edits = edits if edits is not None else []
        self.calls: list[dict] = []

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.calls.append(kwargs)
            return _RecordingMockResponse(self._outer._edits)

    @property
    def messages(self):
        return self._Messages(self)


def _prompt_text(client: _RecordingMockClient) -> str:
    assert client.calls, "generate_reasoned_logic_candidates never called client.messages.create()."
    return client.calls[0].get("messages", [{}])[0].get("content", "")


# ===========================================================================
# AC-1: reasoning_context reaches the actual SDK prompt payload.
# ===========================================================================


def test_reasoned_generator_embeds_reasoning_context_in_llm_prompt(lce, fixture_tree, monkeypatch):
    """AC-1: a truthy reasoning_context string must appear verbatim in the prompt
    sent to the (mocked) SDK client — proving the operator's real tree/stats/lens
    context, not just the objective name, reaches the LLM."""
    client = _RecordingMockClient(edits=[])
    monkeypatch.setattr(lce, "_build_client", lambda: client)

    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    lce.generate_reasoned_logic_candidates(
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


def test_reasoned_generator_omits_context_section_when_falsy(lce, fixture_tree, monkeypatch):
    """Complement: reasoning_context omitted or falsy (None/'') must produce a
    prompt with NO trace of the operator-context text — the scheduler's
    default-None call site must not leak a stale/empty section."""
    for falsy in (None, ""):
        client = _RecordingMockClient(edits=[])
        monkeypatch.setattr(lce, "_build_client", lambda: client)
        objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
        lce.generate_reasoned_logic_candidates(
            "sym-1", fixture_tree, objective, reasoning_context=falsy
        )
        prompt = _prompt_text(client)
        assert "UVIX" not in prompt, (
            f"GAP: prompt contains reasoning-context content despite falsy "
            f"reasoning_context={falsy!r}."
        )


def test_reasoned_generator_never_dumps_raw_json_tree(lce, fixture_tree, monkeypatch):
    """Negative invariant (AC-1's 'never a raw JSON dump'): the prompt must never
    contain a full json.dumps() serialization of the raw tree — the generator may
    reference specific tweakable params, but not the entire raw_value verbatim."""
    client = _RecordingMockClient(edits=[])
    monkeypatch.setattr(lce, "_build_client", lambda: client)
    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    lce.generate_reasoned_logic_candidates("sym-1", fixture_tree, objective)

    prompt = _prompt_text(client)
    full_dump = json.dumps(fixture_tree)
    assert full_dump not in prompt, (
        "AC-1 GAP: the prompt contains a full raw json.dumps() of the tree — "
        "must be a bounded, human-readable representation, never a raw dump."
    )


# ===========================================================================
# Threading — the two public entry points pass reasoning_context/reasoning_manifest
# straight through to generate_reasoned_logic_candidates.
# ===========================================================================


def _patch_common(lce, monkeypatch, *, has_key=True):
    monkeypatch.setattr(lce, "_has_composer_key", lambda: has_key)
    monkeypatch.setattr(lce.database, "insert_advisor_observation", MagicMock(return_value=1))


def test_propose_operator_logic_change_threads_reasoning_context_to_generator(
    lce, fixture_tree, monkeypatch
):
    """propose_operator_logic_change must forward reasoning_context/reasoning_manifest
    to generate_reasoned_logic_candidates(...) unchanged — proven via the mocked
    generator's call_args, not by re-testing generation internals."""
    _patch_common(lce, monkeypatch)
    gen_mock = MagicMock(return_value=[])
    monkeypatch.setattr(lce, "generate_reasoned_logic_candidates", gen_mock)

    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    manifest = {"tree": "present", "stats": "absent"}
    lce.propose_operator_logic_change(
        "sym-1",
        fixture_tree,
        objective=objective,
        change_description="tighten the window",
        reasoning_context=_FAKE_REASONING_CONTEXT,
        reasoning_manifest=manifest,
    )

    assert gen_mock.called, "GAP: generate_reasoned_logic_candidates was never called."
    _args, kwargs = gen_mock.call_args
    assert kwargs.get("reasoning_context") == _FAKE_REASONING_CONTEXT, (
        f"GAP: propose_operator_logic_change did not thread reasoning_context. kwargs={kwargs!r}"
    )


def test_suggest_logic_changes_threads_reasoning_context_to_generator(lce, fixture_tree, monkeypatch):
    """suggest_logic_changes must forward reasoning_context/reasoning_manifest to
    generate_reasoned_logic_candidates(...) unchanged (weekly-scheduler entry point,
    signature-symmetric with propose_operator_logic_change)."""
    _patch_common(lce, monkeypatch)
    gen_mock = MagicMock(return_value=[])
    monkeypatch.setattr(lce, "generate_reasoned_logic_candidates", gen_mock)

    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    manifest = {"tree": "present", "stats": "present"}
    lce.suggest_logic_changes(
        "sym-1",
        fixture_tree,
        objective,
        reasoning_context=_FAKE_REASONING_CONTEXT,
        reasoning_manifest=manifest,
    )

    assert gen_mock.called, "GAP: generate_reasoned_logic_candidates was never called."
    _args, kwargs = gen_mock.call_args
    assert kwargs.get("reasoning_context") == _FAKE_REASONING_CONTEXT, (
        f"GAP: suggest_logic_changes did not thread reasoning_context. kwargs={kwargs!r}"
    )


def test_suggest_logic_changes_omitted_reasoning_context_defaults_to_none(
    lce, fixture_tree, monkeypatch
):
    """Sibling regression: the weekly scheduler's existing call shape (no
    reasoning_context kwarg at all — weekly_suggestions_scheduler.py is untouched
    this cycle) must still reach the generator with reasoning_context=None, not
    raise a TypeError for a missing required arg."""
    _patch_common(lce, monkeypatch)
    gen_mock = MagicMock(return_value=[])
    monkeypatch.setattr(lce, "generate_reasoned_logic_candidates", gen_mock)

    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    lce.suggest_logic_changes("sym-1", fixture_tree, objective)

    assert gen_mock.called, "GAP: generate_reasoned_logic_candidates was never called."
    _args, kwargs = gen_mock.call_args
    assert kwargs.get("reasoning_context") is None, (
        f"GAP: omitted reasoning_context did not default to None. kwargs={kwargs!r}"
    )
