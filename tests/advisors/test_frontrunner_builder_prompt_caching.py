"""RED tests -- prompt-caching cache_control breakpoint contract for
advisors.frontrunner_builder.generate_candidate_overlay (site #7 of the
advisor-prompt-caching cost-fix cycle, DE-ADVISOR-CACHE-001).

Team-lead ruling (reconciled with cache-impl's real count_tokens ground
truth -- messages-only ~1889 tok, combined with the static tool schema
~2650-2750 tok, clearing Fable 5's 2048 floor via tools+messages together,
per "render order is tools -> system -> messages... a breakpoint on the last
[messages] block caches [the preceding] tools ... together"): this site needs
a genuine REORDER, not just a call-site wrap -- today's
``_build_generation_prompt`` interpolates the volatile watched/atlas/edge-signal
hints in the MIDDLE of the prompt (between the invariant HARD REQUIREMENTS
block and the invariant tool-usage+examples block). The fix relocates those
hints to a TRAILING labeled section (mirroring build_plan_generator's R2-1
"## OPERATOR CONTEXT" pattern), producing one contiguous invariant leading
span wrapped in cache_control, with the hints appended after, uncached.

Content-set-preservation (team-lead's explicit ask (a)): the reorder must not
drop or reword any of the invariant instructional text -- only relocate the
hints. Existing frontrunner tests assert substring PRESENCE, not order, so
they would not catch content silently going missing during the reorder. This
file pins that guarantee against a golden fixture
(tests/fixtures/frontrunner_builder/generation_prompt_stable_content_baseline.json)
captured from the REAL pre-reorder producer.

No-trade safety boundary (team-lead's ask (b)): this file does not re-test
the no-invest/no-deploy adversarial source-scan
(tests/security/test_frontrunner_no_trade_boundary.py) -- that test is
untouched by a prompt-only reorder and must stay green; flagged in the
handoff for cache-impl to re-run, not duplicated here.

No live network anywhere -- _build_client is mocked throughout.
"""

from __future__ import annotations

import json
import pathlib
import re
from unittest.mock import MagicMock

import pytest

from tests.advisors._prompt_cache_test_helpers import extract_text, find_cache_control_blocks

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_GOLDEN_FIXTURE_PATH = (
    _REPO_ROOT
    / "tests"
    / "fixtures"
    / "frontrunner_builder"
    / "generation_prompt_stable_content_baseline.json"
)

_SIGNAL_CONTEXT_A = {
    "watched_tickers": ["SPY", "QQQ"],
    "atlas_patterns": [{"pattern": "rsi_overbought_cascade"}],
    "edge_signals": {"SPY:14:70": "keep"},
}
_SIGNAL_CONTEXT_B = {
    "watched_tickers": ["IWM"],
    "atlas_patterns": [{"pattern": "vix_spike_hedge"}],
    "edge_signals": {"IWM:20:65": "keep"},
}


@pytest.fixture(scope="module")
def frb():
    import advisors.frontrunner_builder as _frb  # noqa: PLC0415

    return _frb


@pytest.fixture(scope="module")
def golden():
    with open(_GOLDEN_FIXTURE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Minimal SDK client/response mocks. n_attempts=1 everywhere below -- these
# tests inspect the REQUEST, not the generated overlay, so an
# invalid/rejected candidate is fine as long as exactly one call happens.
# ---------------------------------------------------------------------------


class _RecordingMockBlock:
    def __init__(self, payload):
        self.type = "tool_use"
        self.input = payload
        self.name = "emit_frontrunner_overlay"


class _RecordingMockResponse:
    def __init__(self, overlay):
        self.stop_reason = "tool_use"
        self.content = [_RecordingMockBlock({"overlay": overlay})]


class _RecordingMockClient:
    """Records every kwargs dict passed to messages.create()."""

    def __init__(self, overlay):
        self._overlay = overlay
        self.calls: list[dict] = []

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.calls.append(kwargs)
            return _RecordingMockResponse(self._outer._overlay)

    @property
    def messages(self):
        return self._Messages(self)


def _deliberately_rejectable_overlay():
    """No VIX-family ticker in the fire branch -- deliberately rejected by
    AC-4a so the function returns after exactly one call (n_attempts=1)
    without needing a fully compilable candidate."""
    return {
        "kind": "if",
        "condition": {
            "lhs_fn": "relative-strength-index",
            "lhs_ticker": "SPY",
            "window": 14,
            "comparator": "gt",
            "rhs": {"fixed": 70},
        },
        "then": [{"kind": "asset", "ticker": "SPY"}],
        "else": [{"kind": "asset", "ticker": "QQQ"}],
    }


def _generate(frb, client, signal_context):
    frb.generate_candidate_overlay(signal_context, n_attempts=1)
    assert client.calls, "generate_candidate_overlay never called client.messages.create()."
    return client.calls[-1]["messages"][0]["content"]


# ===========================================================================
# Breakpoint exists.
# ===========================================================================


def test_request_content_is_block_list_with_one_cache_control_breakpoint(frb, monkeypatch):
    client = _RecordingMockClient(_deliberately_rejectable_overlay())
    monkeypatch.setattr(frb, "_build_client", lambda: client)

    content = _generate(frb, client, _SIGNAL_CONTEXT_A)

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


# ===========================================================================
# Content-set-preservation: the reorder relocates hints, it never drops or
# rewords the invariant instructional text (team-lead ask (a)).
# ===========================================================================


def test_cached_block_still_contains_the_full_invariant_instructional_text(frb, monkeypatch, golden):
    client = _RecordingMockClient(_deliberately_rejectable_overlay())
    monkeypatch.setattr(frb, "_build_client", lambda: client)

    content = _generate(frb, client, _SIGNAL_CONTEXT_A)
    marked = find_cache_control_blocks(content)
    assert marked, "no cache_control-marked block found (see the breakpoint-exists test)."
    cached_text = marked[0].get("text", "")

    assert golden["stable_before_hints_text"] in cached_text, (
        "the HARD REQUIREMENTS / node-shape instructional text (everything before today's "
        "mid-prompt hints block) is missing from the cached block -- the reorder must "
        "RELOCATE the hints, not drop or reword the surrounding invariant text. "
        f"Golden span length={len(golden['stable_before_hints_text'])}."
    )
    assert golden["stable_after_hints_text"] in cached_text, (
        "the tool-usage instructions + both worked examples (everything after today's "
        "mid-prompt hints block) are missing from the cached block -- same content-"
        f"preservation requirement. Golden span length={len(golden['stable_after_hints_text'])}."
    )


def test_cached_block_no_longer_contains_the_hints_placeholder_text(frb, monkeypatch):
    """Sibling to the preservation test: the hints must actually be RELOCATED
    out of the cached block, not merely duplicated into a trailing block too
    -- otherwise every distinct signal_context would still invalidate the
    'cached' block, defeating the whole point of the reorder."""
    client = _RecordingMockClient(_deliberately_rejectable_overlay())
    monkeypatch.setattr(frb, "_build_client", lambda: client)

    content = _generate(frb, client, _SIGNAL_CONTEXT_A)
    marked = find_cache_control_blocks(content)
    cached_text = marked[0].get("text", "") if marked else ""

    assert "SPY, QQQ" not in cached_text, (
        "the watched-tickers hint ('SPY, QQQ') appears inside the cached block -- it must "
        "be relocated to an uncached trailing block, not left (or duplicated) in the "
        "cached span."
    )


# ===========================================================================
# Volatile hints appear in an UNCACHED trailing block.
# ===========================================================================


def test_signal_context_hints_appear_in_an_uncached_trailing_block(frb, monkeypatch):
    client = _RecordingMockClient(_deliberately_rejectable_overlay())
    monkeypatch.setattr(frb, "_build_client", lambda: client)

    content = _generate(frb, client, _SIGNAL_CONTEXT_A)
    assert isinstance(content, list) and len(content) >= 2, (
        "the relocated hints must live in at least one additional block beyond the cached "
        f"base -- got {len(content) if isinstance(content, list) else type(content).__name__}."
    )
    marked = find_cache_control_blocks(content)
    assert len(marked) == 1, f"Expected exactly one cache_control block, found {len(marked)}."
    cache_idx = content.index(marked[0])

    trailing_text = extract_text(content[cache_idx + 1 :])
    assert "SPY" in trailing_text and "QQQ" in trailing_text, (
        "the watched_tickers hint must reach a trailing block after the cache_control "
        f"breakpoint. Trailing text: {trailing_text[:300]!r}"
    )
    assert "rsi_overbought_cascade" in trailing_text, (
        "the atlas_patterns hint must reach the trailing block."
    )
    assert "keep" in trailing_text, "the edge_signals hint must reach the trailing block."

    for block in content[cache_idx + 1 :]:
        assert "cache_control" not in block, (
            f"the hints tail must be UNCACHED -- found cache_control on a trailing block: {block!r}"
        )


# ===========================================================================
# Genuine reuse across two separate calls with DIFFERENT signal_context.
# ===========================================================================


def test_two_calls_different_signal_context_share_an_identical_cached_prefix(frb, monkeypatch):
    """Two generate_candidate_overlay calls with DIFFERENT watched tickers /
    atlas patterns / edge signals must produce byte-identical cache_control
    blocks -- the structural precondition for a real cache read across
    separate build runs (PM's live probe, if feasible for this site)."""
    client_a = _RecordingMockClient(_deliberately_rejectable_overlay())
    monkeypatch.setattr(frb, "_build_client", lambda: client_a)
    content_a = _generate(frb, client_a, _SIGNAL_CONTEXT_A)

    client_b = _RecordingMockClient(_deliberately_rejectable_overlay())
    monkeypatch.setattr(frb, "_build_client", lambda: client_b)
    content_b = _generate(frb, client_b, _SIGNAL_CONTEXT_B)

    marked_a = find_cache_control_blocks(content_a)
    marked_b = find_cache_control_blocks(content_b)
    assert marked_a and marked_b, "both calls must carry a cache_control-marked block."
    assert marked_a[0]["text"] == marked_b[0]["text"], (
        "the cache_control-marked block must be byte-identical across two calls with "
        "different signal_context -- a real Anthropic cache read is only possible when "
        "the prefix bytes match exactly."
    )
    # Sanity: the two calls are genuinely different somewhere (the hints tail), so the
    # identical-prefix result above isn't trivially true.
    assert content_a != content_b, (
        "the two calls produced byte-identical full content -- signal_context did not "
        "actually vary between them, so the stable-prefix check above proves nothing."
    )


# ===========================================================================
# Seam-preservation regression guard.
#
# Caught mid-GREEN this cycle, not by any RED test above: a first
# implementation draft bypassed _build_generation_prompt entirely (routing
# the cache_control split through new private helpers directly), silently
# breaking test_frontrunner_builder_signal_wiring.py's
# test_generation_prompt_carries_positive_edge_signal_stat_lines, which spies
# on _build_generation_prompt by name via patch.object(...). The tests above
# only inspect the FINAL request shape -- they can't tell which internal
# function produced it, so a future refactor that reaches the correct final
# shape via a different seam would pass all of them while breaking that spy
# again. This test closes that gap directly, inside the file whose whole
# point is to touch this call site.
# ===========================================================================


def test_generate_candidate_overlay_calls_the_real_build_generation_prompt(frb, monkeypatch):
    client = _RecordingMockClient(_deliberately_rejectable_overlay())
    monkeypatch.setattr(frb, "_build_client", lambda: client)

    spy = MagicMock(wraps=frb._build_generation_prompt)
    monkeypatch.setattr(frb, "_build_generation_prompt", spy)

    frb.generate_candidate_overlay(_SIGNAL_CONTEXT_A, n_attempts=1)

    assert spy.called, (
        "generate_candidate_overlay no longer calls the real _build_generation_prompt -- "
        "test_frontrunner_builder_signal_wiring.py spies on this exact function by name. "
        "Deriving the cache_control split via new private helpers is fine, but the call "
        "site must still invoke _build_generation_prompt itself (e.g. via a byte-slice "
        "against an independently-computed stable prefix), not bypass it."
    )


# ===========================================================================
# Silent-invalidator guard.
# ===========================================================================

_ISO_DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


def test_cached_block_contains_no_timestamp_or_uuid_markers(frb, monkeypatch):
    client = _RecordingMockClient(_deliberately_rejectable_overlay())
    monkeypatch.setattr(frb, "_build_client", lambda: client)

    content = _generate(frb, client, _SIGNAL_CONTEXT_A)
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


def test_static_tool_schema_contains_no_timestamp_or_uuid_markers(frb, monkeypatch):
    """Render order is tools -> system -> messages; per cache-impl's own
    ground-truthing, this site only clears the 2048-token floor when the
    static tools=[_EMIT_OVERLAY_TOOL] schema is counted alongside the
    messages block -- guard it against the same invalidator class."""
    client = _RecordingMockClient(_deliberately_rejectable_overlay())
    monkeypatch.setattr(frb, "_build_client", lambda: client)

    _generate(frb, client, _SIGNAL_CONTEXT_A)
    tools_json = json.dumps(client.calls[-1].get("tools", []), default=str)

    assert not _ISO_DATETIME_RE.search(tools_json), (
        "the static tools=[...] schema contains an ISO-datetime-shaped substring."
    )
    assert not _UUID_RE.search(tools_json), "the static tools=[...] schema contains a UUID-shaped substring."
