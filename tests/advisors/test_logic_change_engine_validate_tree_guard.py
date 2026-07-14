"""RED tests — R2-2 AC-3: edits apply to the raw tree and are structurally
re-validated via symphony_schema.validate_tree before backtest.

Net-new guard over today's apply_logic_tweak: apply_logic_tweak only checks that
the target node/param_key/old_value exist and match (a NAVIGATION check) — it has
no opinion on whether the RESULTING tree is still structurally valid per the
Composer grammar. The reasoned generator can propose an edit that navigates to a
real node (so apply_logic_tweak succeeds) but corrupts a structural field (e.g.
overwriting a node's own "step") — that variant must be dropped by a NEW
validate_tree call before any backtest is attempted, with an honest, non-fabricated
reason, and must be distinguishable from the pre-existing "tweak not found"
rejection message (proves this is a genuinely new guard, not a mislabeled old one).

Attack construction: the fixture tree's ROOT node (node_path=[]) always has
"step": "root" (a HARD validate_tree requirement — advisors/symphony_schema.py's
validate_tree flags `tree.get("step") != "root"`). Overwriting it via an edit
targeting param_key="step" is structurally valid per apply_logic_tweak (the root
dict has that key) but produces a tree validate_tree WILL flag — this is the
minimal, tree-agnostic way to construct a guaranteed-invalid variant without
hand-inventing tree structure (Gate-1 fixture provenance: reuses the real fixture
tree, only the edit dict is hand-built).

Mocking strategy: advisors.composer_backtest_client.run_backtest is patched +
spied (never live). advisors.logic_change_engine._build_client is mocked to
return the crafted edit(s). The REAL evaluate_candidate_batch / acceptance_gate
math runs unmocked.
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
def lce():
    import advisors.logic_change_engine as _lce  # noqa: PLC0415

    return _lce


@pytest.fixture()
def fixture_tree():
    with open(_FIXTURE_TREE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture()
def real_param(lce, fixture_tree):
    params = lce.extract_numeric_params(fixture_tree)
    assert params, "self-guard: fixture tree has no numeric params."
    return params[0]


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


def _fake_backtest_result():
    from advisors.composer_backtest_client import BacktestResult
    from datetime import date, timedelta

    returns: dict[str, float] = {}
    d = date(2022, 1, 1)
    for i in range(100):
        returns[d.isoformat()] = 0.001 * (1 + (i % 5) * 0.1 - 0.2)
        d += timedelta(days=1)
    return BacktestResult(stats={"sharpe": 0.5}, data_warnings=[], daily_returns=returns)


def _invalid_step_edit() -> dict:
    """An edit targeting the ROOT node's own 'step' field — apply_logic_tweak
    navigates/applies it fine (root is a dict, 'step' exists), but the RESULT
    fails validate_tree's HARD 'root node must have step=root' check."""
    return {"node_path": [], "param_key": "step", "new_value": "not-a-real-step"}


def _valid_numeric_edit(real_param: dict) -> dict:
    return {
        "node_path": real_param["node_path"],
        "param_key": real_param["param_key"],
        "new_value": real_param["value"] + 1,
    }


def _patch_common(lce, monkeypatch):
    monkeypatch.setattr(lce, "_has_composer_key", lambda: True)
    monkeypatch.setattr(lce.database, "insert_advisor_observation", MagicMock(return_value=1))


# ===========================================================================
# AC-3 CORE: an invalid LLM edit is dropped before backtest, honest reason.
# ===========================================================================


def test_structurally_invalid_llm_edit_dropped_before_backtest(lce, fixture_tree, monkeypatch):
    _patch_common(lce, monkeypatch)
    client = _FixedMockClient(edits=[_invalid_step_edit()])
    monkeypatch.setattr(lce, "_build_client", lambda: client)

    backtest_spy = MagicMock(side_effect=lambda *a, **k: _fake_backtest_result())
    monkeypatch.setattr(lce, "run_backtest", backtest_spy)

    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    result = lce.suggest_logic_changes("sym-1", fixture_tree, objective)

    assert backtest_spy.call_count == 0, (
        f"AC-3 GAP: run_backtest was called {backtest_spy.call_count} time(s) for a "
        "structurally-invalid variant — it must be dropped BEFORE any backtest."
    )
    assert result.message == lce.NO_SURVIVORS_MESSAGE, (
        f"AC-3: zero survivors expected (only candidate was invalid). Got message={result.message!r}"
    )
    assert result.rejected_candidates, (
        "AC-3 GAP: the invalid candidate must still be surfaced (rejected), not silently vanish."
    )
    reason = result.rejected_candidates[0].backtest_error
    assert reason, "AC-3 GAP: the dropped candidate must carry an honest, non-empty reason."
    assert "not found" not in reason.lower(), (
        f"AC-3 GAP: reason={reason!r} reuses the pre-existing 'tweak not found' wording — "
        "this must be a DISTINCT, genuinely-new validate_tree guard message, since "
        "apply_logic_tweak DID succeed here (the node/param_key were found)."
    )


def test_valid_reasoned_edit_proceeds_to_backtest(lce, fixture_tree, real_param, monkeypatch):
    _patch_common(lce, monkeypatch)
    client = _FixedMockClient(edits=[_valid_numeric_edit(real_param)])
    monkeypatch.setattr(lce, "_build_client", lambda: client)

    backtest_spy = MagicMock(side_effect=lambda *a, **k: _fake_backtest_result())
    monkeypatch.setattr(lce, "run_backtest", backtest_spy)

    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    lce.suggest_logic_changes("sym-1", fixture_tree, objective)

    assert backtest_spy.call_count >= 1, (
        "AC-3 GAP: a structurally-valid numeric edit must reach run_backtest — "
        "the validate_tree guard must not reject valid edits."
    )


def test_mixed_batch_drops_invalid_keeps_valid(lce, fixture_tree, real_param, monkeypatch):
    """A batch with ONE invalid + ONE valid edit: only the valid one is
    backtested and gated; the invalid one is rejected with an honest reason;
    nothing is fabricated."""
    _patch_common(lce, monkeypatch)
    client = _FixedMockClient(edits=[_invalid_step_edit(), _valid_numeric_edit(real_param)])
    monkeypatch.setattr(lce, "_build_client", lambda: client)

    backtest_spy = MagicMock(side_effect=lambda *a, **k: _fake_backtest_result())
    monkeypatch.setattr(lce, "run_backtest", backtest_spy)

    gate_spy = MagicMock(wraps=lce.evaluate_candidate_batch)
    monkeypatch.setattr(lce, "evaluate_candidate_batch", gate_spy)

    objective = lce.LogicChangeObjective(objective_type="reduce_drawdown", measured_value=0.0)
    result = lce.suggest_logic_changes("sym-1", fixture_tree, objective)

    assert gate_spy.called, "AC-3 precondition: evaluate_candidate_batch must be reached."
    _args, kwargs = gate_spy.call_args
    candidates = _args[0] if _args else kwargs.get("candidates")
    assert len(candidates) == 1, (
        f"AC-3 GAP: expected exactly 1 candidate reaching the gate (the valid one), "
        f"got {len(candidates)} — the invalid edit leaked into the batch."
    )
    assert len(result.rejected_candidates) >= 1, (
        "AC-3 GAP: the invalid candidate must be surfaced in rejected_candidates, never silently dropped."
    )
    invalid_reasons = [
        p.backtest_error for p in result.proposals if p.tweak and p.tweak.param_key == "step"
    ]
    assert invalid_reasons and invalid_reasons[0], (
        "AC-3 GAP: the invalid (step-corrupting) candidate must carry a non-empty, honest reason."
    )
