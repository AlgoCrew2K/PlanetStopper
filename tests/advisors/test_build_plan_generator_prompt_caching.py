"""RED tests -- prompt-caching cache_control breakpoint contract for
advisors.build_plan_generator.generate_build_plans (site #1 of the
advisor-prompt-caching cost-fix cycle, DE-ADVISOR-CACHE-001).

Team-lead ruling (reconciled with cache-impl/cache-doc): the stable cached
base is the ``reasoning_context=None`` prompt -- byte-identical to the
existing AC-8 golden fixture (tests/fixtures/strategy_builder/
generation_prompt_from_scratch_baseline.json) -- wrapped in a single
``cache_control`` block. ``reasoning_context``, when supplied, is appended as
an UNCACHED trailing block; it never touches the cached base. No reorder of
the base text is permitted -- the existing golden-fixture test in
test_build_plan_generator_reasoning_context.py stays green, untouched.

This file tests the SDK-call boundary only (``client.messages.create``
kwargs, via the ``_build_client`` mock seam) -- it never re-tests
``_build_generation_prompt`` itself, which this fix does not touch and which
is already covered by the AC-8 golden-fixture test.

No live network anywhere -- ``_build_client`` is mocked throughout; no live
count_tokens/messages.create call is made from this file.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re

import pytest

from tests.advisors._prompt_cache_test_helpers import extract_text, find_cache_control_blocks

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_GOLDEN_FIXTURE_PATH = (
    _REPO_ROOT
    / "tests"
    / "fixtures"
    / "strategy_builder"
    / "generation_prompt_from_scratch_baseline.json"
)

# Two DIFFERENT reasoning_context strings -- used to prove the cached base is
# identical regardless of which (or no) reasoning_context is supplied.
_FAKE_REASONING_CONTEXT_A = (
    "STRATEGY 'Live Portfolio' (rebalance: daily)\n  HOLD UVIX\n  HOLD TQQQ\n"
    "OPTUNA: oos_alpha=0.01 train_alpha=0.02\n"
)
_FAKE_REASONING_CONTEXT_B = (
    "STRATEGY 'Other Portfolio' (rebalance: weekly)\n  HOLD GLD\n"
    "OPTUNA: oos_alpha=0.05 train_alpha=0.09\n"
)


@pytest.fixture(scope="module")
def bpg():
    import advisors.build_plan_generator as _bpg  # noqa: PLC0415

    return _bpg


@pytest.fixture(scope="module")
def golden():
    with open(_GOLDEN_FIXTURE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Minimal SDK client/response mocks (mirrors the pattern already established
# in test_build_plan_generator_reasoning_context.py / test_..._truncation.py).
# ---------------------------------------------------------------------------


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
    """Records every kwargs dict passed to messages.create()."""

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
    """A minimal VALID-DSL diversify plan (>=2 sleeves)."""
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


def _generate(bpg, client, golden_args, *, reasoning_context=None):
    """Call generate_build_plans with the golden fixture's own call_args,
    returning the recorded messages[0]["content"] value from the LAST call."""
    objective = bpg.Objective[golden_args["objective"]]
    membership = frozenset(golden_args["membership"])
    n_plans = golden_args["n_plans"]
    bpg.generate_build_plans(
        objective, membership, n_plans=n_plans, reasoning_context=reasoning_context
    )
    assert client.calls, "generate_build_plans never called client.messages.create()."
    return client.calls[-1]["messages"][0]["content"]


# ===========================================================================
# Breakpoint exists + matches the golden-fixture base (team-lead ruling).
# ===========================================================================


def test_request_content_is_block_list_with_one_cache_control_breakpoint(bpg, monkeypatch, golden):
    client = _RecordingMockClient([_conforming_diversify_plan()])
    monkeypatch.setattr(bpg, "_build_client", lambda: client)

    content = _generate(bpg, client, golden["call_args"])

    assert isinstance(content, list), (
        "messages[0]['content'] must be a list of content blocks to carry a cache_control "
        f"breakpoint -- got {type(content).__name__}."
    )
    marked = find_cache_control_blocks(content)
    assert len(marked) == 1, (
        f"Expected exactly one cache_control-marked block, found {len(marked)}. "
        f"content={content!r}"
    )
    assert marked[0]["cache_control"] == {"type": "ephemeral"}, (
        f"cache_control block must be {{'type': 'ephemeral'}}, got {marked[0]['cache_control']!r}."
    )


def test_cache_control_block_text_matches_golden_fixture_byte_for_byte(bpg, monkeypatch, golden):
    """Team-lead ruling: the cached base is the reasoning_context=None prompt,
    byte-identical to the pre-existing AC-8 golden fixture -- the caching
    restructure must not reorder or otherwise touch that text."""
    client = _RecordingMockClient([_conforming_diversify_plan()])
    monkeypatch.setattr(bpg, "_build_client", lambda: client)

    content = _generate(bpg, client, golden["call_args"])
    marked = find_cache_control_blocks(content)
    assert marked, "no cache_control-marked block found (see the breakpoint-exists test)."

    cached_text = marked[0].get("text", "")
    assert cached_text == golden["prompt_text"], (
        "The cache_control-marked block's text is not byte-identical to the AC-8 golden "
        f"fixture. Golden length={golden['prompt_length']}, got length={len(cached_text)}."
    )
    got_sha = hashlib.sha256(cached_text.encode("utf-8")).hexdigest()
    assert got_sha == golden["prompt_sha256"], (
        f"SHA-256 mismatch on the cached block -- golden={golden['prompt_sha256']}, "
        f"got={got_sha}. The base prompt was reordered or altered by the caching change."
    )


# ===========================================================================
# reasoning_context is an UNCACHED trailing block; it never touches the base.
# ===========================================================================


def test_reasoning_context_is_appended_as_uncached_trailing_block(bpg, monkeypatch, golden):
    client = _RecordingMockClient([_conforming_diversify_plan()])
    monkeypatch.setattr(bpg, "_build_client", lambda: client)

    content = _generate(
        bpg, client, golden["call_args"], reasoning_context=_FAKE_REASONING_CONTEXT_A
    )
    assert isinstance(content, list) and len(content) >= 2, (
        "reasoning_context must add at least one additional block beyond the cached base -- "
        f"got {len(content) if isinstance(content, list) else type(content).__name__} block(s)."
    )

    marked = find_cache_control_blocks(content)
    assert len(marked) == 1, f"Expected exactly one cache_control block, found {len(marked)}."
    cache_idx = content.index(marked[0])

    trailing_text = extract_text(content[cache_idx + 1 :])
    assert "## OPERATOR CONTEXT" in trailing_text, (
        "reasoning_context's '## OPERATOR CONTEXT' section must appear in a block AFTER the "
        f"cache_control breakpoint. Trailing text: {trailing_text[:200]!r}"
    )
    assert "UVIX" in trailing_text, "the reasoning_context TEXT itself must reach the trailing block."

    for block in content[cache_idx + 1 :]:
        assert "cache_control" not in block, (
            "the reasoning_context tail must be UNCACHED -- found cache_control on a trailing "
            f"block: {block!r}"
        )

    cached_text = marked[0].get("text", "")
    assert cached_text == golden["prompt_text"], (
        "adding reasoning_context must not alter the cached base -- it changed when "
        "reasoning_context became truthy."
    )


def test_omitted_reasoning_context_sends_only_the_cached_base_block(bpg, monkeypatch, golden):
    """Sibling regression, restated for the block-list world: the existing
    production/test call shape (no reasoning_context) sends exactly ONE
    block -- the cached base -- with no operator-context tail leaking in."""
    client = _RecordingMockClient([_conforming_diversify_plan()])
    monkeypatch.setattr(bpg, "_build_client", lambda: client)

    content = _generate(bpg, client, golden["call_args"])
    assert isinstance(content, list) and len(content) == 1, (
        "Expected exactly one block when reasoning_context is omitted, got "
        f"{len(content) if isinstance(content, list) else type(content).__name__}."
    )
    assert "## OPERATOR CONTEXT" not in extract_text(content), (
        "'## OPERATOR CONTEXT' leaked into the prompt despite reasoning_context being omitted."
    )


# ===========================================================================
# Genuine reuse across two separate calls (the PM's live-probe scenario).
# ===========================================================================


def test_two_calls_same_objective_share_an_identical_cached_prefix(bpg, monkeypatch, golden):
    """Two separate generate_build_plans calls for the SAME objective/universe
    -- one with no reasoning_context, one with a DIFFERENT reasoning_context --
    must produce byte-identical cache_control blocks. This is the structural
    precondition for the PM's live cache_read_input_tokens>0 probe (two
    same-objective calls within the 5-minute TTL)."""
    client_a = _RecordingMockClient([_conforming_diversify_plan()])
    monkeypatch.setattr(bpg, "_build_client", lambda: client_a)
    content_a = _generate(bpg, client_a, golden["call_args"])

    client_b = _RecordingMockClient([_conforming_diversify_plan()])
    monkeypatch.setattr(bpg, "_build_client", lambda: client_b)
    content_b = _generate(
        bpg, client_b, golden["call_args"], reasoning_context=_FAKE_REASONING_CONTEXT_B
    )

    marked_a = find_cache_control_blocks(content_a)
    marked_b = find_cache_control_blocks(content_b)
    assert marked_a and marked_b, "both calls must carry a cache_control-marked block."
    assert marked_a[0]["text"] == marked_b[0]["text"], (
        "the cache_control-marked block must be byte-identical across two calls for the same "
        "objective/universe -- a real Anthropic cache read is only possible when the prefix "
        "bytes match exactly."
    )
    # Sanity: the two calls are genuinely different somewhere (the reasoning_context tail),
    # so the identical-prefix result above isn't trivially true.
    assert content_a != content_b, (
        "the two calls produced byte-identical full content -- reasoning_context did not "
        "actually vary between them, so the stable-prefix check above proves nothing."
    )


# ===========================================================================
# Silent-invalidator guard.
# ===========================================================================

_ISO_DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


def test_cached_block_contains_no_timestamp_or_uuid_markers(bpg, monkeypatch, golden):
    """A datetime.now()/uuid4() call accidentally reachable from the cached-base
    assembly would make the golden-fixture byte-identity test above flaky at
    best -- this is a direct structural tripwire for that failure class."""
    client = _RecordingMockClient([_conforming_diversify_plan()])
    monkeypatch.setattr(bpg, "_build_client", lambda: client)

    content = _generate(bpg, client, golden["call_args"])
    marked = find_cache_control_blocks(content)
    cached_text = marked[0].get("text", "") if marked else ""

    assert not _ISO_DATETIME_RE.search(cached_text), (
        "the cached block contains an ISO-datetime-shaped substring -- a timestamp leaked "
        "into the stable prefix, which would silently invalidate the cache on every call."
    )
    assert not _UUID_RE.search(cached_text), (
        "the cached block contains a UUID-shaped substring -- a random id leaked into the "
        "stable prefix, which would silently invalidate the cache on every call."
    )


def test_static_tool_schema_contains_no_timestamp_or_uuid_markers(bpg, monkeypatch, golden):
    """Render order is tools -> system -> messages; the cache_control breakpoint
    on the last messages block caches the (static) tools=[...] array too --
    guard it against the same invalidator class."""
    client = _RecordingMockClient([_conforming_diversify_plan()])
    monkeypatch.setattr(bpg, "_build_client", lambda: client)

    _generate(bpg, client, golden["call_args"])
    tools_json = json.dumps(client.calls[-1].get("tools", []), default=str)

    assert not _ISO_DATETIME_RE.search(tools_json), (
        "the static tools=[...] schema contains an ISO-datetime-shaped substring."
    )
    assert not _UUID_RE.search(tools_json), "the static tools=[...] schema contains a UUID-shaped substring."
