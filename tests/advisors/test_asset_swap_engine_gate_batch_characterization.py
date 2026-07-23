"""RED tests — R2-3 AC-4: the FDR/PBO/SPY gate is byte-unchanged and the
reasoned path is batch-corrected — ALL successfully-backtested LLM-proposed
swap pairs are gated as ONE evaluate_candidate_batch call, never per-candidate.

Two characterization layers (mirrors R2-2's identical file):
  1. A tripwire pinning evaluate_candidate_batch's parameter set EXACTLY as it
     exists before any R2-3 change — a future implementer threading
     run_id/reasoning_context into the gate itself would silently break R1/R2-2
     gate-parity.
  2. N reasoned candidates -> evaluate_candidate_batch called EXACTLY ONCE with
     all N -> gate_batch.n_candidates == N (the FDR denominator). Per-candidate
     gating (N separate calls, n_effective=1 each) silently disables the
     multiple-testing correction and must FAIL this file.

Mocking: run_backtest mocked (deterministic synthetic returns, zero live calls).
_build_client mocked to return N canned pairs. get_tradeable_set mocked
permissive. evaluate_candidate_batch itself is SPIED (wraps=), never mocked —
the real gate math runs.
"""

from __future__ import annotations

import inspect
import json
import pathlib
from unittest.mock import MagicMock

import pytest

from advisors.backtest_gate_engine import evaluate_candidate_batch

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_TREE_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "symphony_logic" / "sample_score_small.json"
)

_REAL_INCUMBENT = "QQQ"

# The exact current parameter set of the FDR/PBO/SPY gate — same frozenset R1
# and R2-2 pinned; the gate has not changed since.
_GATE_SIGNATURE_BEFORE_R2_3 = frozenset(
    {"candidates", "incumbent_oos_alpha", "default_oos_alpha", "spy_returns_fn"}
)


@pytest.fixture(scope="module")
def ase():
    import advisors.asset_swap_engine as _ase  # noqa: PLC0415

    return _ase


@pytest.fixture()
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

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            return _RecordingMockResponse(self._outer._candidates)

    @property
    def messages(self):
        return self._Messages(self)


def _fake_backtest_result(seed: int = 0):
    from datetime import date, timedelta

    from advisors.composer_backtest_client import BacktestResult

    returns: dict[str, float] = {}
    d = date(2022, 1, 1)
    for i in range(100):
        returns[d.isoformat()] = 0.001 * (1 + ((i + seed) % 5) * 0.1 - 0.2)
        d += timedelta(days=1)
    return BacktestResult(stats={"sharpe": 0.5}, data_warnings=[], daily_returns=returns)


def _patch_common(ase, monkeypatch):
    monkeypatch.setattr(ase, "_has_composer_key", lambda: True)
    monkeypatch.setattr(
        ase, "get_tradeable_set", lambda **kwargs: frozenset({"CAND0", "CAND1", "CAND2"})
    )
    monkeypatch.setattr(ase.database, "insert_advisor_observation", MagicMock(return_value=1))
    monkeypatch.setattr(ase, "run_backtest", lambda *a, **k: _fake_backtest_result())


def _objective(ase):
    return ase.SwapObjective(objective_type="reduce_drawdown", target_pair=None, measured_value=0.0)


# ===========================================================================
# Tripwire: gate signature byte-unchanged.
# ===========================================================================


def test_evaluate_candidate_batch_signature_unchanged():
    actual = frozenset(inspect.signature(evaluate_candidate_batch).parameters.keys())
    assert actual == _GATE_SIGNATURE_BEFORE_R2_3, (
        f"AC-4 GAP: evaluate_candidate_batch's signature changed from "
        f"{sorted(_GATE_SIGNATURE_BEFORE_R2_3)} to {sorted(actual)} — the FDR/PBO/SPY gate "
        "must stay byte-unchanged by R2-3 (R1/R2-2 parity)."
    )


# ===========================================================================
# N candidates -> ONE batch call, N-sized.
# ===========================================================================


def test_n_reasoned_pairs_gated_in_one_batch_call(ase, fixture_tree, monkeypatch):
    _patch_common(ase, monkeypatch)
    pairs = [
        {"incumbent_asset": _REAL_INCUMBENT, "candidate_asset": t, "rationale": "x"}
        for t in ("CAND0", "CAND1", "CAND2")
    ]
    client = _FixedMockClient(candidates=pairs)
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    gate_spy = MagicMock(wraps=ase.evaluate_candidate_batch)
    monkeypatch.setattr(ase, "evaluate_candidate_batch", gate_spy)

    result = ase.suggest_swaps(
        "sym-1", fixture_tree, _objective(ase), {}, ["CAND0", "CAND1", "CAND2"]
    )

    assert gate_spy.call_count == 1, (
        f"AC-4 GAP: evaluate_candidate_batch was called {gate_spy.call_count} times for "
        "3 candidates — must be called EXACTLY ONCE with the full batch (per-candidate "
        "gating silently disables the FDR correction)."
    )
    _args, kwargs = gate_spy.call_args
    candidates = _args[0] if _args else kwargs.get("candidates")
    assert len(candidates) == 3, (
        f"AC-4 GAP: the single batch call carried {len(candidates)} candidates, expected 3."
    )
    assert result.gate_batch.n_candidates == 3, (
        f"AC-4 GAP: gate_batch.n_candidates={result.gate_batch.n_candidates}, expected 3 — "
        "the FDR denominator must reflect the FULL batch size."
    )


def test_operator_path_still_single_batch_call_n1(ase, fixture_tree, monkeypatch):
    """Regression: propose_operator_swap in reasoned mode (max_candidates=1 UX,
    neither ticker supplied) still submits exactly one candidate in one batch
    call under the NEW reasoned generator — N=1 shape preserved."""
    _patch_common(ase, monkeypatch)
    pair = {"incumbent_asset": _REAL_INCUMBENT, "candidate_asset": "CAND0", "rationale": "x"}
    client = _FixedMockClient(candidates=[pair])
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    gate_spy = MagicMock(wraps=ase.evaluate_candidate_batch)
    monkeypatch.setattr(ase, "evaluate_candidate_batch", gate_spy)

    ase.propose_operator_swap(
        symphony_id="sym-1", score_tree=fixture_tree, objective=_objective(ase)
    )

    assert gate_spy.call_count == 1, (
        f"AC-4 GAP: propose_operator_swap (reasoned mode) called the gate "
        f"{gate_spy.call_count} times."
    )
    _args, kwargs = gate_spy.call_args
    candidates = _args[0] if _args else kwargs.get("candidates")
    assert len(candidates) == 1, (
        f"AC-4 GAP: operator reasoned-mode batch carried {len(candidates)} candidates, expected 1."
    )


# ===========================================================================
# AC-X5: one candidate's backtest failure never aborts the rest of the batch.
# ===========================================================================


def test_one_candidate_backtest_failure_does_not_abort_the_rest_of_the_batch(
    ase, fixture_tree, monkeypatch
):
    """N=3 reasoned candidates, the SECOND's run_backtest call errors — the
    other two must still be backtested, gated, and surfaced; the batch must
    not abort (AC-X5)."""
    monkeypatch.setattr(ase, "_has_composer_key", lambda: True)
    monkeypatch.setattr(
        ase, "get_tradeable_set", lambda **kwargs: frozenset({"CAND0", "CAND1", "CAND2"})
    )
    monkeypatch.setattr(ase.database, "insert_advisor_observation", MagicMock(return_value=1))

    pairs = [
        {"incumbent_asset": _REAL_INCUMBENT, "candidate_asset": t, "rationale": "x"}
        for t in ("CAND0", "CAND1", "CAND2")
    ]
    client = _FixedMockClient(candidates=pairs)
    monkeypatch.setattr(ase, "_build_client", lambda: client)

    from advisors.composer_backtest_client import BacktestResult

    error_result = BacktestResult(
        error="HTTP 500: Internal Server Error", stats=None, data_warnings=[], daily_returns={}
    )
    call_count = {"n": 0}

    def _flaky_backtest(*a, **k):
        call_count["n"] += 1
        # Deterministic call-index targeting (all 3 candidates share the same
        # tree shape) — this engine backtests baseline THEN variant per
        # candidate: [baseline-1, variant-1, baseline-2, variant-2, baseline-3,
        # variant-3, batch-level-baseline-returns, SPY-benchmark]. Call #4 is
        # candidate 2's VARIANT backtest.
        if call_count["n"] == 4:
            return error_result
        return _fake_backtest_result(seed=call_count["n"])

    monkeypatch.setattr(ase, "run_backtest", _flaky_backtest)

    gate_spy = MagicMock(wraps=ase.evaluate_candidate_batch)
    monkeypatch.setattr(ase, "evaluate_candidate_batch", gate_spy)

    result = ase.suggest_swaps(
        "sym-1", fixture_tree, _objective(ase), {}, ["CAND0", "CAND1", "CAND2"]
    )

    assert result is not None, "AC-X5 GAP: suggest_swaps must not raise on a backtest error."
    n_total = len(result.proposals)
    n_failed = sum(1 for p in result.proposals if p.backtest_error)
    assert n_total == 3, f"AC-X5 GAP: expected all 3 candidates surfaced, got {n_total}."
    assert 0 < n_failed < n_total, (
        f"AC-X5 GAP: expected exactly one failed candidate among {n_total}, got {n_failed} failed "
        "— one backtest failure must not abort the rest of the batch, and must not be silently "
        "dropped either."
    )
    assert gate_spy.called, "AC-X5 GAP: the surviving candidates must still reach the gate."
    _args, kwargs = gate_spy.call_args
    gated = _args[0] if _args else kwargs.get("candidates")
    assert len(gated) == n_total - n_failed, (
        f"AC-X5 GAP: {len(gated)} candidates reached the gate, expected {n_total - n_failed} "
        "(the successfully-backtested ones only)."
    )
