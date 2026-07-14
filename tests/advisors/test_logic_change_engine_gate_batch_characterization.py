"""RED tests — R2-2 AC-4: the FDR/PBO/SPY gate is byte-unchanged and the reasoned
path is batch-corrected — ALL successfully-backtested LLM-proposed variants are
gated as ONE evaluate_candidate_batch call, never per-candidate.

Two characterization layers:
  1. A tripwire pinning evaluate_candidate_batch's parameter set EXACTLY as it
     exists before any R2-2 change (mirrors R2-1's identical
     test_gate_signature_unchanged_reasoning_context_never_leaks_into_gate_math
     tripwire in test_strategy_builder_engine_reasoning_provenance.py) — a future
     implementer threading run_id/reasoning_context into the gate itself would
     silently break R1 gate-parity.
  2. N reasoned candidates -> evaluate_candidate_batch called EXACTLY ONCE with
     all N -> gate_batch.n_candidates == N (the FDR denominator). Per-candidate
     gating (N separate calls, n_effective=1 each) silently disables the
     multiple-testing correction and must FAIL this file.

Mocking: run_backtest mocked (deterministic synthetic returns, zero live calls).
_build_client mocked to return N canned edits. evaluate_candidate_batch itself
is SPIED (wraps=), never mocked — the real gate math runs.
"""

from __future__ import annotations

import inspect
import json
import pathlib
from unittest.mock import MagicMock

import pytest

from advisors.backtest_gate_engine import evaluate_candidate_batch

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_TREE_PATH = _REPO_ROOT / "tests" / "fixtures" / "symphony_logic" / "sample_score_small.json"

# The exact current parameter set of the FDR/PBO/SPY gate — pinned BEFORE any
# R2-2 change (same frozenset R2-1 pinned; the gate has not changed since).
_GATE_SIGNATURE_BEFORE_R2_2 = frozenset(
    {"candidates", "incumbent_oos_alpha", "default_oos_alpha", "spy_returns_fn"}
)


@pytest.fixture(scope="module")
def lce():
    import advisors.logic_change_engine as _lce  # noqa: PLC0415

    return _lce


@pytest.fixture()
def fixture_tree():
    with open(_FIXTURE_TREE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


class _RecordingMockBlock:
    def __init__(self, payload):
        self.type = "tool_use"
        self.input = payload


class _RecordingMockResponse:
    def __init__(self, edits):
        self.stop_reason = "tool_use"
        self.content = [_RecordingMockBlock({"edits": edits})]


class _FixedMockClient:
    def __init__(self, edits):
        self._edits = edits

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            return _RecordingMockResponse(self._outer._edits)

    @property
    def messages(self):
        return self._Messages(self)


def _fake_backtest_result(seed: int = 0):
    from advisors.composer_backtest_client import BacktestResult
    from datetime import date, timedelta

    returns: dict[str, float] = {}
    d = date(2022, 1, 1)
    for i in range(100):
        returns[d.isoformat()] = 0.001 * (1 + ((i + seed) % 5) * 0.1 - 0.2)
        d += timedelta(days=1)
    return BacktestResult(stats={"sharpe": 0.5}, data_warnings=[], daily_returns=returns)


def _patch_common(lce, monkeypatch):
    monkeypatch.setattr(lce, "_has_composer_key", lambda: True)
    monkeypatch.setattr(lce.database, "insert_advisor_observation", MagicMock(return_value=1))
    monkeypatch.setattr(lce, "run_backtest", lambda *a, **k: _fake_backtest_result())


# ===========================================================================
# Tripwire: gate signature byte-unchanged.
# ===========================================================================


def test_evaluate_candidate_batch_signature_unchanged():
    actual = frozenset(inspect.signature(evaluate_candidate_batch).parameters.keys())
    assert actual == _GATE_SIGNATURE_BEFORE_R2_2, (
        f"AC-4 GAP: evaluate_candidate_batch's signature changed from "
        f"{sorted(_GATE_SIGNATURE_BEFORE_R2_2)} to {sorted(actual)} — the FDR/PBO/SPY gate "
        "must stay byte-unchanged by R2-2 (R1 parity)."
    )


# ===========================================================================
# N candidates -> ONE batch call, N-sized.
# ===========================================================================


def test_n_reasoned_candidates_gated_in_one_batch_call(lce, fixture_tree, monkeypatch):
    _patch_common(lce, monkeypatch)
    params = lce.extract_numeric_params(fixture_tree)[:3]
    edits = [
        {"node_path": p["node_path"], "param_key": p["param_key"], "new_value": p["value"] + 1}
        for p in params
    ]
    client = _FixedMockClient(edits=edits)
    monkeypatch.setattr(lce, "_build_client", lambda: client)

    gate_spy = MagicMock(wraps=lce.evaluate_candidate_batch)
    monkeypatch.setattr(lce, "evaluate_candidate_batch", gate_spy)

    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    result = lce.suggest_logic_changes("sym-1", fixture_tree, objective)

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


def test_operator_path_still_single_batch_call_n1(lce, fixture_tree, monkeypatch):
    """Regression: propose_operator_logic_change (max_candidates=1 UX) still
    submits exactly one candidate in one batch call under the NEW reasoned
    generator — N=1 shape preserved."""
    _patch_common(lce, monkeypatch)
    params = lce.extract_numeric_params(fixture_tree)
    edit = {
        "node_path": params[0]["node_path"],
        "param_key": params[0]["param_key"],
        "new_value": params[0]["value"] + 1,
    }
    client = _FixedMockClient(edits=[edit])
    monkeypatch.setattr(lce, "_build_client", lambda: client)

    gate_spy = MagicMock(wraps=lce.evaluate_candidate_batch)
    monkeypatch.setattr(lce, "evaluate_candidate_batch", gate_spy)

    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    lce.propose_operator_logic_change(
        "sym-1", fixture_tree, objective=objective, change_description="tighten it"
    )

    assert gate_spy.call_count == 1, (
        f"AC-4 GAP: propose_operator_logic_change called the gate {gate_spy.call_count} times."
    )
    _args, kwargs = gate_spy.call_args
    candidates = _args[0] if _args else kwargs.get("candidates")
    assert len(candidates) == 1, (
        f"AC-4 GAP: operator path batch carried {len(candidates)} candidates, expected 1."
    )
