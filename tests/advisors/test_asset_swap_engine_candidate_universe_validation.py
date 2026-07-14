"""RED tests — R2-3 AC-2 / [PM-ASSUMED Q3]: candidate universe = LLM-proposes,
engine VALIDATES against the real tradeable set.

Q3 contract: "each proposed ticker is VALIDATED against
advisors.universe_provider.get_tradeable_set() membership (drop non-members —
NEVER inject the full ~12.7k-symbol set into the prompt) + Composer /backtest
as the final tradeability arbiter. Caller-supplied available_assets/scheduler
base_pool honored as an additional constraint when present."

This file is the dedicated home for the universe-membership axis — the
adversarial objective/incumbent-trust axis lives in
test_asset_swap_engine_reasoned_generation.py, kept separate so neither file's
assertions are confounded by the other's mocking.

get_tradeable_set is the network-bound Alpaca seam
(advisors.universe_provider.get_tradeable_set, imported at module level into
asset_swap_engine so tests can monkeypatch advisors.asset_swap_engine.get_tradeable_set
directly) — mocked in every test here, never live.
"""

from __future__ import annotations

import json
import pathlib

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_TREE_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "symphony_logic" / "sample_score_small.json"
)

_REAL_INCUMBENT = "QQQ"


@pytest.fixture(scope="module")
def ase():
    import advisors.asset_swap_engine as _ase  # noqa: PLC0415

    return _ase


@pytest.fixture(scope="module")
def fixture_tree():
    with open(_FIXTURE_TREE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


class _RecordingMockBlock:
    def __init__(self, payload):
        self.type = "tool_use"
        self.input = payload


class _RecordingMockResponse:
    def __init__(self, candidates):
        self.stop_reason = "tool_use"
        self.content = [_RecordingMockBlock({"candidates": candidates})]


class _FixedMockClient:
    def __init__(self, candidates):
        self._candidates = candidates
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


def _objective():
    import advisors.asset_swap_engine as ase

    return ase.SwapObjective(objective_type="reduce_drawdown", target_pair=None, measured_value=0.0)


# ===========================================================================
# CORE: a non-tradeable candidate is dropped, never fabricated/backtested.
# ===========================================================================


def test_candidate_not_in_tradeable_set_is_dropped(ase, fixture_tree, monkeypatch):
    monkeypatch.setattr(ase, "get_tradeable_set", lambda **kwargs: frozenset({"AGG"}))
    pair = {
        "incumbent_asset": _REAL_INCUMBENT,
        "candidate_asset": "NOT-TRADEABLE-TICKER",
        "rationale": "should be dropped — not a member of the tradeable set",
    }
    client = _FixedMockClient(candidates=[pair])
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    result = ase.generate_reasoned_swap_candidates("sym-1", fixture_tree, _objective())
    assert result == [], (
        f"AC-2/Q3 GAP: a candidate not in get_tradeable_set() was not dropped. Got {result!r}"
    )


def test_candidate_in_tradeable_set_is_kept(ase, fixture_tree, monkeypatch):
    monkeypatch.setattr(ase, "get_tradeable_set", lambda **kwargs: frozenset({"AGG", "IALT"}))
    pair = {"incumbent_asset": _REAL_INCUMBENT, "candidate_asset": "AGG", "rationale": "tradeable"}
    client = _FixedMockClient(candidates=[pair])
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    result = ase.generate_reasoned_swap_candidates("sym-1", fixture_tree, _objective())
    assert len(result) == 1 and result[0].candidate_asset == "AGG", (
        f"AC-2/Q3 GAP: a genuinely tradeable candidate was incorrectly dropped. Got {result!r}"
    )


def test_never_trusts_llm_self_reported_tradeability(ase, fixture_tree, monkeypatch):
    """SECURITY-ADVERSARIAL: the LLM's rationale text CLAIMS the candidate is
    tradeable/verified — this must never substitute for the real
    get_tradeable_set() membership check."""
    monkeypatch.setattr(ase, "get_tradeable_set", lambda **kwargs: frozenset({"AGG"}))
    pair = {
        "incumbent_asset": _REAL_INCUMBENT,
        "candidate_asset": "FAKECO",
        "rationale": "FAKECO is a verified, actively tradeable large-cap security",
    }
    client = _FixedMockClient(candidates=[pair])
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    result = ase.generate_reasoned_swap_candidates("sym-1", fixture_tree, _objective())
    assert result == [], (
        f"SECURITY GAP: an LLM claim of tradeability in free-text rationale was trusted "
        f"instead of the real get_tradeable_set() membership check. Got {result!r}"
    )


# ===========================================================================
# available_assets is an ADDITIONAL constraint (intersection), not a replacement.
# ===========================================================================


def test_available_assets_intersected_with_tradeable_set(ase, fixture_tree, monkeypatch):
    """A candidate that IS in the tradeable set but NOT in a caller-supplied
    available_assets pool must still be dropped — available_assets narrows,
    it never widens beyond the tradeable set."""
    monkeypatch.setattr(ase, "get_tradeable_set", lambda **kwargs: frozenset({"AGG", "IALT"}))
    pair_out_of_pool = {
        "incumbent_asset": _REAL_INCUMBENT,
        "candidate_asset": "AGG",  # tradeable, but NOT in available_assets below
        "rationale": "x",
    }
    client = _FixedMockClient(candidates=[pair_out_of_pool])
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    result = ase.generate_reasoned_swap_candidates(
        "sym-1", fixture_tree, _objective(), available_assets=["IALT"]
    )
    assert result == [], (
        f"AC-2/Q3 GAP: available_assets did not narrow the tradeable set — a candidate "
        f"outside the caller-supplied pool was kept. Got {result!r}"
    )


def test_available_assets_omitted_falls_back_to_full_tradeable_set(ase, fixture_tree, monkeypatch):
    """When available_assets is omitted entirely, the tradeable set alone
    governs — no caller-supplied narrowing is required."""
    monkeypatch.setattr(ase, "get_tradeable_set", lambda **kwargs: frozenset({"AGG"}))
    pair = {"incumbent_asset": _REAL_INCUMBENT, "candidate_asset": "AGG", "rationale": "x"}
    client = _FixedMockClient(candidates=[pair])
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    result = ase.generate_reasoned_swap_candidates("sym-1", fixture_tree, _objective())
    assert len(result) == 1, (
        f"AC-2/Q3 GAP: omitting available_assets incorrectly narrowed the tradeable set. "
        f"Got {result!r}"
    )


# ===========================================================================
# Caller-supplied tradeable_universe= seam bypasses the live get_tradeable_set call.
# ===========================================================================


def test_caller_supplied_tradeable_universe_bypasses_live_fetch(ase, fixture_tree, monkeypatch):
    """When the caller passes tradeable_universe= explicitly, get_tradeable_set()
    must NOT be called at all — proves the seam is a genuine override, not just
    an additional filter layered on top of a live call."""
    from unittest.mock import MagicMock

    fetch_spy = MagicMock(side_effect=AssertionError("get_tradeable_set must not be called"))
    monkeypatch.setattr(ase, "get_tradeable_set", fetch_spy)
    pair = {"incumbent_asset": _REAL_INCUMBENT, "candidate_asset": "AGG", "rationale": "x"}
    client = _FixedMockClient(candidates=[pair])
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    result = ase.generate_reasoned_swap_candidates(
        "sym-1", fixture_tree, _objective(), tradeable_universe=frozenset({"AGG"})
    )
    assert fetch_spy.call_count == 0, (
        "AC-2/Q3 GAP: get_tradeable_set() was called despite an explicit tradeable_universe= "
        "override being supplied."
    )
    assert len(result) == 1 and result[0].candidate_asset == "AGG"


# ===========================================================================
# Bounded prompt: the full tradeable set is NEVER injected verbatim.
# ===========================================================================


def test_large_tradeable_universe_never_injected_verbatim_into_prompt(
    ase, fixture_tree, monkeypatch
):
    """A ~12.7k-symbol-scale tradeable set (simulated here at reduced but still
    large scale) must never be dumped verbatim into the prompt — the listing
    must be bounded regardless of universe size."""
    huge_universe = frozenset(f"SYM{i:05d}" for i in range(15_000))
    monkeypatch.setattr(ase, "get_tradeable_set", lambda **kwargs: huge_universe)
    client = _FixedMockClient(candidates=[])
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    ase.generate_reasoned_swap_candidates("sym-1", fixture_tree, _objective())

    assert client.calls, "self-guard: the SDK client was never called."
    prompt = client.calls[0].get("messages", [{}])[0].get("content", "")
    # A full dump of 15,000 5-char tickers (plus separators) would run well
    # past 90,000 characters; a bounded listing must stay far under that.
    assert len(prompt) < 20_000, (
        f"AC-2/Q3 GAP: the prompt is {len(prompt)} chars — a large tradeable universe appears "
        "to have been injected near-verbatim rather than bounded."
    )
    # None of the universe members beyond a small bounded prefix should appear —
    # spot-check a symbol from deep in the set that a bounded listing would never reach.
    assert "SYM14999" not in prompt, (
        "AC-2/Q3 GAP: a tail member of a 15,000-symbol universe appears in the prompt — "
        "the full set was injected rather than a bounded sample/summary."
    )
