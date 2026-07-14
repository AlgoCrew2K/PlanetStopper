"""RED tests — R2-3 AC-2: LLM proposes objective-directed swap PAIRS over the
operator's REAL holdings.

THE ADVERSARIAL FOCUS (mirrors R2-2's test_logic_change_engine_reasoned_generation.py
almost 1:1 — AC-2 is the load-bearing "genuinely reasons" guardrail for this port too):

  A generator that ignores the objective and returns the same pairs regardless
  is a FAIL. This file drives ``generate_reasoned_swap_candidates`` twice with
  DIFFERENT objectives against the SAME fixture tree, via a mock SDK client
  whose canned response is keyed off which objective string reaches the prompt
  — an implementation that never threads ``objective`` into the prompt text
  gets the client's "no match" fallback (empty) both times and fails the test
  for the right reason (not a coincidental equality).

  A second adversarial axis: the LLM's own claimed incumbent must never be
  trusted blind — an incumbent ticker that is NOT actually present in the
  operator's real tree must be dropped, never fabricated into a SwapCandidate
  that would later corrupt the tree via apply_ticker_swap. A candidate equal to
  its own incumbent (swap-into-self) must also be dropped.

Candidate-universe (tradeable-set) validation is covered in its OWN dedicated
file, test_asset_swap_engine_candidate_universe_validation.py — every test
here mocks get_tradeable_set() to a permissive superset so the universe axis
never confounds the assertions made here.

Contract pinned (test-writer's call, mirrors generate_reasoned_logic_candidates):
  ``generate_reasoned_swap_candidates(symphony_id, raw_value, objective, *,
  reasoning_context=None, correlation_data=None, available_assets=None,
  tradeable_universe=None, max_candidates=MAX_SUGGESTED_CANDIDATES) ->
  list[SwapCandidate]`` where ``SwapCandidate`` has ``incumbent_asset``,
  ``candidate_asset``, ``rationale`` fields. LLM tool-use output shape pinned:
  ``{"candidates": [{"incumbent_asset": str, "candidate_asset": str,
  "rationale": str}]}``.

No live network — _build_client is mocked throughout; get_tradeable_set is
mocked throughout (network-bound Alpaca call, must never fire in this file).
"""

from __future__ import annotations

import json
import pathlib

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_TREE_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "symphony_logic" / "sample_score_small.json"
)

# A real ticker present in the fixture tree (verified via extract_tickers at
# test-authoring time) — used as a valid incumbent throughout this file.
_REAL_INCUMBENT = "QQQ"
# Tickers NOT present in the fixture tree — safe non-holdings for candidate use.
_CANDIDATE_A = "IALT"
_CANDIDATE_B = "AGG"


@pytest.fixture(scope="module")
def ase():
    import advisors.asset_swap_engine as _ase  # noqa: PLC0415

    return _ase


@pytest.fixture(scope="module")
def fixture_tree():
    with open(_FIXTURE_TREE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture()
def _permissive_universe(ase, monkeypatch):
    """Mock get_tradeable_set() to a permissive superset covering every ticker
    used in this file's tests — isolates the objective/trust adversarial axes
    from the universe-membership axis (that axis has its own dedicated file)."""
    universe = frozenset({_CANDIDATE_A, _CANDIDATE_B, _REAL_INCUMBENT, "SPY", "GLD"})
    monkeypatch.setattr(ase, "get_tradeable_set", lambda **kwargs: universe)
    return universe


# ---------------------------------------------------------------------------
# Mock SDK client doubles
# ---------------------------------------------------------------------------


class _RecordingMockBlock:
    def __init__(self, payload):
        self.type = "tool_use"
        self.input = payload


class _RecordingMockResponse:
    def __init__(self, candidates):
        self.stop_reason = "tool_use"
        self.content = [_RecordingMockBlock({"candidates": candidates})]


class _FixedMockClient:
    """Always returns the same canned candidate list, regardless of prompt content."""

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


class _ObjectiveKeyedMockClient:
    """Returns a DIFFERENT canned candidate list depending on which
    objective-marker substring is found in the outgoing prompt text. An
    implementation that never embeds the objective in the prompt gets the "no
    marker matched" empty fallback on every call — the adversarial trap."""

    def __init__(self, responses_by_marker: dict):
        self._responses_by_marker = responses_by_marker
        self.calls: list[dict] = []

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.calls.append(kwargs)
            prompt = kwargs.get("messages", [{}])[0].get("content", "")
            for marker, candidates in self._outer._responses_by_marker.items():
                if marker in prompt:
                    return _RecordingMockResponse(candidates)
            return _RecordingMockResponse([])  # no marker matched -> empty

    @property
    def messages(self):
        return self._Messages(self)


class _RaisingMockClient:
    class _Messages:
        def create(self, **kwargs):
            raise RuntimeError("simulated SDK failure")

    @property
    def messages(self):
        return self._Messages()


# ===========================================================================
# AC-2 CORE ADVERSARIAL: two objectives, same tree, must produce different pairs.
# ===========================================================================


def test_two_objectives_produce_different_pairs_on_same_tree(
    ase, fixture_tree, _permissive_universe, monkeypatch
):
    """ADVERSARIAL: an objective-ignoring generator fails this test. Two distinct
    objectives against the identical tree must yield DIFFERENT swap-candidate
    sets — proven via a mock client keyed on objective text reaching the prompt."""
    pair_a = {
        "incumbent_asset": _REAL_INCUMBENT,
        "candidate_asset": _CANDIDATE_A,
        "rationale": "targets drawdown reduction",
    }
    pair_b = {
        "incumbent_asset": _REAL_INCUMBENT,
        "candidate_asset": _CANDIDATE_B,
        "rationale": "targets risk-adjusted lift",
    }
    client = _ObjectiveKeyedMockClient(
        {"reduce_drawdown": [pair_a], "lift_risk_adjusted": [pair_b]}
    )
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    obj_a = ase.SwapObjective(
        objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
    )
    result_a = ase.generate_reasoned_swap_candidates("sym-1", fixture_tree, obj_a)

    obj_b = ase.SwapObjective(
        objective_type="lift_risk_adjusted", target_pair=None, measured_value=0.0
    )
    result_b = ase.generate_reasoned_swap_candidates("sym-1", fixture_tree, obj_b)

    assert result_a, (
        "AC-2 precondition failed: reduce_drawdown produced zero candidates — the "
        "objective marker never reached the prompt (or parsing is broken)."
    )
    assert result_b, (
        "AC-2 precondition failed: lift_risk_adjusted produced zero candidates — the "
        "objective marker never reached the prompt (or parsing is broken)."
    )
    candidates_a = {c.candidate_asset for c in result_a}
    candidates_b = {c.candidate_asset for c in result_b}
    assert candidates_a != candidates_b, (
        f"AC-2 GAP (adversarial FAIL): two different objectives produced the SAME "
        f"swap candidates on the same tree — the generator is ignoring the objective. "
        f"reduce_drawdown={candidates_a!r}, lift_risk_adjusted={candidates_b!r}"
    )


# ===========================================================================
# Real-holding-only incumbents: never trust the LLM's claimed incumbent.
# ===========================================================================


def test_reasoned_pair_targeting_nonexistent_incumbent_is_dropped(
    ase, fixture_tree, _permissive_universe, monkeypatch
):
    """An LLM-proposed pair whose incumbent_asset is NOT actually held in
    raw_value must be dropped — never fabricated into a SwapCandidate."""
    bogus_pair = {
        "incumbent_asset": "NOT-A-REAL-HOLDING",
        "candidate_asset": _CANDIDATE_A,
        "rationale": "should be dropped",
    }
    client = _FixedMockClient(candidates=[bogus_pair])
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    objective = ase.SwapObjective(
        objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
    )
    result = ase.generate_reasoned_swap_candidates("sym-1", fixture_tree, objective)

    assert result == [], (
        f"AC-2 GAP: a pair whose incumbent is not a real holding was not dropped. Got {result!r}"
    )


def test_reasoned_pair_swap_into_self_dropped(ase, fixture_tree, _permissive_universe, monkeypatch):
    """A pair proposing incumbent_asset == candidate_asset (swap into self, a
    no-op that wastes a backtest) must be dropped."""
    self_pair = {
        "incumbent_asset": _REAL_INCUMBENT,
        "candidate_asset": _REAL_INCUMBENT,
        "rationale": "no-op, should be dropped",
    }
    client = _FixedMockClient(candidates=[self_pair])
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    objective = ase.SwapObjective(
        objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
    )
    result = ase.generate_reasoned_swap_candidates("sym-1", fixture_tree, objective)

    assert result == [], f"AC-2 GAP: a swap-into-self pair was not dropped. Got {result!r}"


def test_reasoned_generator_bounded_by_max_candidates(
    ase, fixture_tree, _permissive_universe, monkeypatch
):
    """The reasoned generator must never return more than max_candidates, even
    when the LLM proposes more."""
    pairs = [
        {"incumbent_asset": _REAL_INCUMBENT, "candidate_asset": t, "rationale": "x"}
        for t in (_CANDIDATE_A, _CANDIDATE_B, "SPY", "GLD")
    ]
    client = _FixedMockClient(candidates=pairs)
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    objective = ase.SwapObjective(
        objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
    )
    result = ase.generate_reasoned_swap_candidates(
        "sym-1", fixture_tree, objective, max_candidates=2
    )
    assert len(result) <= 2, (
        f"AC-2 GAP: generator returned {len(result)} candidates, exceeding max_candidates=2."
    )


# ===========================================================================
# D-1: never raises, degrades cleanly on malformed/failed LLM output.
# ===========================================================================


def test_malformed_tool_output_returns_empty_list_no_raise(
    ase, fixture_tree, _permissive_universe, monkeypatch
):
    """A tool_use block whose input has no 'candidates' key (or a non-list
    value) must degrade to an empty list, never raise."""

    class _MalformedResponse:
        stop_reason = "tool_use"

        class _Block:
            type = "tool_use"
            input = {"not_candidates": "garbage"}

        content = [_Block()]

    class _MalformedClient:
        class _Messages:
            def create(self, **kwargs):
                return _MalformedResponse()

        @property
        def messages(self):
            return self._Messages()

    monkeypatch.setattr(ase, "_build_client", lambda: _MalformedClient())
    objective = ase.SwapObjective(
        objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
    )
    result = ase.generate_reasoned_swap_candidates("sym-1", fixture_tree, objective)
    assert result == [], f"D-1 GAP: malformed tool output should degrade to [], got {result!r}"


def test_llm_client_construction_or_call_failure_returns_empty_list_no_raise(
    ase, fixture_tree, _permissive_universe, monkeypatch
):
    """_build_client raising (e.g. missing ANTHROPIC_API_KEY) or the SDK call
    itself raising must both degrade to an empty candidate list, never raise
    out of generate_reasoned_swap_candidates (D-1)."""

    def _raise():
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    monkeypatch.setattr(ase, "_build_client", _raise)
    objective = ase.SwapObjective(
        objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
    )
    result = ase.generate_reasoned_swap_candidates("sym-1", fixture_tree, objective)
    assert result == [], (
        f"D-1 GAP: client construction failure should degrade to [], got {result!r}"
    )

    monkeypatch.setattr(ase, "_build_client", lambda: _RaisingMockClient())
    result2 = ase.generate_reasoned_swap_candidates("sym-1", fixture_tree, objective)
    assert result2 == [], f"D-1 GAP: SDK call failure should degrade to [], got {result2!r}"
