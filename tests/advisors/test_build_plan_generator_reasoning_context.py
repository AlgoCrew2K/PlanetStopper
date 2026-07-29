"""RED tests — R2-1 AC-1/AC-2/AC-8/AC-9: reasoning_context threaded through the
generation seam chain (generate_build_plans -> _build_generation_prompt).

Contract pinned (per r2-engine, confirmed via SendMessage before this file was
written):
  - ``_build_generation_prompt(objective, n_plans=..., membership=None,
    reasoning_context=None)`` — additive 4th keyword param, a STRING (the
    rendered prompt_context produced by ai_advisor.build_reasoning_context,
    tested separately in test_reasoning_context_assembler.py).
  - Injected as a new ``## OPERATOR CONTEXT`` prompt section ONLY when truthy.
  - ``generate_build_plans(objective, membership_set, *, n_plans=...,
    reasoning_context=None)`` threads the same string straight through to
    ``_build_generation_prompt``.
  - AC-8: when ``reasoning_context`` is omitted or explicitly ``None``, the
    from-scratch prompt text is LITERALLY byte-identical to the pre-R2-1
    producer output — proven against a golden fixture captured from the REAL
    producer BEFORE any R2-1 change landed (Gate-1 fixture provenance).

No live network — the LLM client seam (_build_client) is mocked throughout;
this file never constructs a real anthropic client.

Prompt-caching cross-cutting note (cache-fix cycle, DE-ADVISOR-CACHE-001):
the two ``sent_content`` extraction sites below route through
``extract_text()`` rather than treating ``messages[0]["content"]`` as a bare
string — the caching restructure (tested separately in
test_build_plan_generator_prompt_caching.py) turns that value into a list of
content blocks. ``extract_text()`` is shape-agnostic (string or block-list),
so these existing AC-1/AC-2/AC-8 assertions keep testing the same thing
regardless of which shape is in effect.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from tests.advisors._prompt_cache_test_helpers import extract_text

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_GOLDEN_FIXTURE_PATH = (
    _REPO_ROOT
    / "tests"
    / "fixtures"
    / "strategy_builder"
    / "generation_prompt_from_scratch_baseline.json"
)


@pytest.fixture(scope="module")
def bpg():
    import advisors.build_plan_generator as _bpg  # noqa: PLC0415

    return _bpg


@pytest.fixture(scope="module")
def golden():
    with open(_GOLDEN_FIXTURE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


# A hand-built reasoning-context string standing in for what
# ai_advisor.build_reasoning_context would actually return — this file tests
# the THREADING mechanism, not the assembler (assembler is tested directly in
# test_reasoning_context_assembler.py). Not a "fixture" concern: it simulates
# an upstream collaborator's return value, matching the mocking convention
# used throughout this test suite for downstream-of-a-tested-seam inputs.
_FAKE_REASONING_CONTEXT = (
    "STRATEGY 'Live Portfolio' (rebalance: daily)\n  HOLD UVIX\n  HOLD TQQQ\n"
    "OPTUNA: oos_alpha=0.01 train_alpha=0.02\n"
)


# ===========================================================================
# AC-1/AC-2 — reasoning_context markers present in the prompt when injected.
# ===========================================================================


def test_reasoning_context_marker_and_content_present_when_truthy(bpg):
    """AC-1/AC-2: a truthy reasoning_context string must appear verbatim in the
    generated prompt, under a distinct '## OPERATOR CONTEXT' section header."""
    prompt = bpg._build_generation_prompt(
        bpg.Objective.diversify,
        12,
        frozenset({"SPY", "QQQ"}),
        reasoning_context=_FAKE_REASONING_CONTEXT,
    )
    assert "## OPERATOR CONTEXT" in prompt, (
        "AC-1/AC-2 GAP: prompt does not contain the '## OPERATOR CONTEXT' section "
        "header when reasoning_context is truthy."
    )
    assert _FAKE_REASONING_CONTEXT.strip() in prompt or "UVIX" in prompt, (
        "AC-1/AC-2 GAP: the injected reasoning_context text does not appear in the "
        "generated prompt — the operator's real tree/stats context was dropped."
    )


def test_reasoning_context_section_absent_when_falsy(bpg):
    """AC-8 (complement): when reasoning_context is falsy (None or ''), the
    '## OPERATOR CONTEXT' section must NOT appear at all — never an empty
    section header with no content."""
    for falsy in (None, ""):
        prompt = bpg._build_generation_prompt(
            bpg.Objective.diversify, 12, frozenset({"SPY", "QQQ"}), reasoning_context=falsy
        )
        assert "## OPERATOR CONTEXT" not in prompt, (
            f"AC-8 GAP: '## OPERATOR CONTEXT' section present despite falsy "
            f"reasoning_context={falsy!r} — from-scratch prompts must carry zero trace "
            "of the injection mechanism."
        )


# ===========================================================================
# AC-8 — from-scratch path byte-preserved (omitted == explicit None == golden fixture).
# ===========================================================================


def test_omitted_reasoning_context_equals_explicit_none(bpg):
    """AC-8: calling without the reasoning_context kwarg at all must produce
    output BYTE-IDENTICAL to calling with reasoning_context=None explicitly —
    proves 'omitted' truly defaults to the no-op path, not some other default."""
    prompt_omitted = bpg._build_generation_prompt(
        bpg.Objective.diversify, 12, frozenset({"SPY", "QQQ", "TLT"})
    )
    prompt_explicit_none = bpg._build_generation_prompt(
        bpg.Objective.diversify, 12, frozenset({"SPY", "QQQ", "TLT"}), reasoning_context=None
    )
    assert prompt_omitted == prompt_explicit_none, (
        "AC-8 GAP: omitting reasoning_context produces different output than passing "
        "reasoning_context=None explicitly — the two must be byte-identical."
    )


def test_from_scratch_prompt_byte_identical_to_golden_fixture_captured_pre_r2_1(bpg, golden):
    """AC-8 (the literal proof): with reasoning_context omitted, today's exact
    call args (Objective.diversify, n_plans=12, membership={SPY,QQQ,TLT}) must
    reproduce BYTE-FOR-BYTE the prompt captured from the REAL producer before
    any R2-1 implementation change landed (tests/fixtures/strategy_builder/
    generation_prompt_from_scratch_baseline.json, captured at commit
    c0cacd4790d53e0fca69bdf654d38e6b396b5660).

    This is the literal 'from-scratch path byte-preserved when reasoning_context
    is None' proof AC-8 requires — not a symmetry inference, a golden-fixture
    regression pin."""
    membership = frozenset(golden["call_args"]["membership"])
    objective = bpg.Objective[golden["call_args"]["objective"]]
    n_plans = golden["call_args"]["n_plans"]

    prompt = bpg._build_generation_prompt(objective, n_plans, membership)

    assert prompt == golden["prompt_text"], (
        "AC-8 GAP: the from-scratch prompt (reasoning_context omitted) is no longer "
        "byte-identical to the pre-R2-1 golden fixture. Any diff here means the R2-1 "
        "seam-threading change altered behavior on the path that must be byte-preserved. "
        f"Golden length={golden['prompt_length']}, got length={len(prompt)}."
    )
    got_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    assert got_sha == golden["prompt_sha256"], (
        f"AC-8 GAP: SHA-256 mismatch — golden={golden['prompt_sha256']}, got={got_sha}. "
        "Byte-preservation of the from-scratch path is violated."
    )


# ===========================================================================
# Threading — generate_build_plans passes reasoning_context all the way to the
# SDK call's message content (the actual seam chain, not just the leaf function).
# ===========================================================================


class _RecordingMockBlock:
    def __init__(self, payload):
        self.type = "tool_use"
        self.input = payload
        self.name = "emit_build_plans"


class _RecordingMockResponse:
    def __init__(self, plans):
        self.stop_reason = "tool_use"
        self.content = [_RecordingMockBlock({"plans": plans})]


class _RecordingMockClient:
    """Records every kwargs dict passed to messages.create() so the test can
    inspect the actual prompt text that reached the (mocked) SDK boundary."""

    def __init__(self, plans):
        self._plans = plans
        self.calls: list[dict] = []

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.calls.append(kwargs)
            return _RecordingMockResponse(self._outer._plans)

    @property
    def messages(self):
        return self._Messages(self)


def _conforming_diversify_plan():
    """A minimal VALID-DSL diversify plan (>=2 sleeves) — reused shape from the
    existing C2 test conventions (test_build_plan_generator_prompt.py)."""
    return {
        "plan_id": "p1",
        "objective": "diversify",
        "name": "P1",
        "rebalance": "daily",
        "root": {
            "kind": "group",
            "name": "port",
            "children": [
                {
                    "kind": "weight",
                    "scheme": "equal",
                    "children": [{"kind": "asset", "ticker": "SPY"}],
                },
                {
                    "kind": "weight",
                    "scheme": "equal",
                    "children": [{"kind": "asset", "ticker": "QQQ"}],
                },
            ],
        },
    }


def test_generate_build_plans_threads_reasoning_context_into_sdk_prompt(bpg, monkeypatch):
    """The full chain: generate_build_plans(..., reasoning_context=...) must
    thread the string all the way into the actual messages=[{"content": ...}]
    payload sent to the (mocked) SDK client — proving the seam is wired end to
    end, not just at the leaf _build_generation_prompt function."""
    client = _RecordingMockClient([_conforming_diversify_plan()])
    monkeypatch.setattr(bpg, "_build_client", lambda: client)

    universe = frozenset({"SPY", "QQQ", "TLT"})
    result = bpg.generate_build_plans(
        bpg.Objective.diversify, universe, n_plans=12, reasoning_context=_FAKE_REASONING_CONTEXT
    )

    assert client.calls, "generate_build_plans never called client.messages.create() at all."
    sent_content = extract_text(client.calls[0].get("messages", [{}])[0].get("content", ""))
    assert "## OPERATOR CONTEXT" in sent_content, (
        "GAP: generate_build_plans did not thread reasoning_context into the actual SDK "
        "prompt payload — '## OPERATOR CONTEXT' section absent from messages[0]['content']."
    )
    assert "UVIX" in sent_content, (
        "GAP: the reasoning_context TEXT itself (not just the section header) did not "
        "reach the SDK prompt payload."
    )
    # Sanity: the call still succeeds and admits the conforming plan (threading the new
    # param must not break the existing generation pipeline).
    assert result.plans, f"generate_build_plans returned no plans, reason={result.reason!r}"


def test_generate_build_plans_omitted_reasoning_context_sends_no_operator_section(bpg, monkeypatch):
    """Sibling regression: generate_build_plans called WITHOUT reasoning_context
    (the existing call shape every current production/test caller uses) must
    send a prompt with NO '## OPERATOR CONTEXT' section — proves the additive
    param does not leak a section header into the untouched from-scratch path."""
    client = _RecordingMockClient([_conforming_diversify_plan()])
    monkeypatch.setattr(bpg, "_build_client", lambda: client)

    universe = frozenset({"SPY", "QQQ", "TLT"})
    bpg.generate_build_plans(bpg.Objective.diversify, universe, n_plans=12)

    assert client.calls, "generate_build_plans never called client.messages.create() at all."
    sent_content = extract_text(client.calls[0].get("messages", [{}])[0].get("content", ""))
    assert "## OPERATOR CONTEXT" not in sent_content, (
        "GAP: '## OPERATOR CONTEXT' leaked into the SDK prompt payload despite "
        "reasoning_context being omitted entirely."
    )
