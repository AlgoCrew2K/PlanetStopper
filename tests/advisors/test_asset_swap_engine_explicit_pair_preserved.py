"""RED tests — R2-3 AC-12: explicit-pair operator contract byte-preserved;
deterministic helpers intact; the deleted deterministic generator confirmed gone.

Contract (Q1/Q4): when the operator supplies BOTH incumbent_asset AND
candidate_asset, propose_operator_swap evaluates that EXACT pair — the
reasoned generator is never invoked, the tradeable-universe seam is never
invoked, and the resulting SwapProposalResult shape/candidate_id format is
identical to pre-R2-3 behavior. Non-generation helpers (apply_ticker_swap,
extract_tickers, _apply_lens_blend, extract_lens_scores, persistence keying)
remain behaviorally unchanged. generate_objective_directed_candidates (Q4) is
DELETED — a static-analysis check confirms no production caller and no
definition remains anywhere in the module.
"""

from __future__ import annotations

import ast
import json
import pathlib
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_TREE_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "symphony_logic" / "sample_score_small.json"
)
_ENGINE_PATH = _REPO_ROOT / "advisors" / "asset_swap_engine.py"
_REAL_INCUMBENT = "QQQ"


@pytest.fixture(scope="module")
def ase():
    import advisors.asset_swap_engine as _ase  # noqa: PLC0415

    return _ase


@pytest.fixture()
def fixture_tree():
    with open(_FIXTURE_TREE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _fake_backtest_result():
    from datetime import date, timedelta

    from advisors.composer_backtest_client import BacktestResult

    returns: dict[str, float] = {}
    d = date(2022, 1, 1)
    for i in range(100):
        returns[d.isoformat()] = 0.001 * (1 + (i % 5) * 0.1 - 0.2)
        d += timedelta(days=1)
    return BacktestResult(stats={"sharpe": 0.5}, data_warnings=[], daily_returns=returns)


def _objective(ase):
    return ase.SwapObjective(
        objective_type="reduce_correlation", target_pair=None, measured_value=0.0
    )


# ===========================================================================
# AC-12 CORE: explicit-pair bypasses the reasoned generator entirely.
# ===========================================================================


def test_explicit_pair_never_calls_reasoned_generator(ase, fixture_tree, monkeypatch):
    monkeypatch.setattr(ase, "_has_composer_key", lambda: True)
    monkeypatch.setattr(ase, "run_backtest", lambda *a, **k: _fake_backtest_result())
    monkeypatch.setattr(ase.database, "insert_advisor_observation", MagicMock(return_value=1))
    gen_spy = MagicMock(return_value=[])
    monkeypatch.setattr(ase, "generate_reasoned_swap_candidates", gen_spy)

    ase.propose_operator_swap(
        symphony_id="sym-1",
        score_tree=fixture_tree,
        objective=_objective(ase),
        incumbent_asset=_REAL_INCUMBENT,
        candidate_asset="AGG",
    )

    assert gen_spy.call_count == 0, (
        f"AC-12 GAP: explicit-pair mode called generate_reasoned_swap_candidates "
        f"{gen_spy.call_count} time(s); it must be bypassed entirely."
    )


def test_explicit_pair_candidate_id_format_byte_preserved(ase, fixture_tree, monkeypatch):
    """The candidate_id format is a pinned, pre-R2-3 contract:
    '{symphony_id}:{incumbent_asset}->{candidate_asset}' — unchanged by R2-3."""
    monkeypatch.setattr(ase, "_has_composer_key", lambda: True)
    monkeypatch.setattr(ase, "run_backtest", lambda *a, **k: _fake_backtest_result())
    monkeypatch.setattr(ase.database, "insert_advisor_observation", MagicMock(return_value=1))

    result = ase.propose_operator_swap(
        symphony_id="sym-1",
        score_tree=fixture_tree,
        objective=_objective(ase),
        incumbent_asset=_REAL_INCUMBENT,
        candidate_asset="AGG",
    )

    assert result.proposals, "AC-12 precondition: explicit-pair mode must produce one proposal."
    proposal = result.proposals[0]
    assert proposal.candidate_id == f"sym-1:{_REAL_INCUMBENT}->AGG", (
        f"AC-12 GAP: candidate_id format changed. Got {proposal.candidate_id!r}, "
        f"expected 'sym-1:{_REAL_INCUMBENT}->AGG' (byte-preserved pre-R2-3 format)."
    )
    assert proposal.incumbent_asset == _REAL_INCUMBENT
    assert proposal.candidate_asset == "AGG"
    # Byte-preserved pre-R2-3 derivation (advisors/asset_swap_engine.py,
    # unchanged): symphony_name prefers the REAL tree's own "name" field over
    # the caller-supplied symphony_id when the tree has one — the fixture
    # does, so the expected string is derived from fixture_tree["name"]
    # itself (never a hardcoded guess at what that field happens to contain;
    # feedback_no_hardcoded_test_values).
    expected_symphony_name = fixture_tree.get("name") or "sym-1"
    assert proposal.apply_guidance == (
        f"To apply: open {expected_symphony_name} in Composer and swap "
        f"{_REAL_INCUMBENT} → AGG manually."
    ), f"AC-12 GAP: apply_guidance template changed. Got {proposal.apply_guidance!r}."


def test_explicit_pair_incumbent_not_in_tree_still_produces_pre_r2_3_error_message(
    ase, fixture_tree, monkeypatch
):
    """Byte-preserved: a ticker not present in the tree still produces the
    exact pre-R2-3 'not in the symphony tree' backtest_error wording (proves
    the R2-3 validate_tree guard did NOT replace this pre-existing check)."""
    monkeypatch.setattr(ase, "_has_composer_key", lambda: True)
    monkeypatch.setattr(ase.database, "insert_advisor_observation", MagicMock(return_value=1))

    result = ase.propose_operator_swap(
        symphony_id="sym-1",
        score_tree=fixture_tree,
        objective=_objective(ase),
        incumbent_asset="NOT-A-REAL-HOLDING",
        candidate_asset="AGG",
    )

    assert result.proposals, "precondition: a proposal shell must still be produced."
    reason = result.proposals[0].backtest_error
    assert reason and "not in the symphony tree" in reason, (
        f"AC-12 GAP: the pre-R2-3 'not in the symphony tree' error wording changed. Got {reason!r}"
    )


# ===========================================================================
# Non-generation helpers unchanged.
# ===========================================================================


def test_apply_ticker_swap_unchanged(ase, fixture_tree):
    """apply_ticker_swap: deep-copies + substitutes, never mutates input."""
    original_json = json.dumps(fixture_tree, sort_keys=True)
    result_tree = ase.apply_ticker_swap(fixture_tree, _REAL_INCUMBENT, "AGG")
    assert json.dumps(fixture_tree, sort_keys=True) == original_json, (
        "AC-12 GAP: apply_ticker_swap mutated its input tree."
    )
    assert "AGG" in ase.extract_tickers(result_tree)
    assert _REAL_INCUMBENT not in ase.extract_tickers(result_tree)


def test_extract_tickers_unchanged(ase, fixture_tree):
    tickers = ase.extract_tickers(fixture_tree)
    assert _REAL_INCUMBENT in tickers, "AC-12 GAP: extract_tickers no longer finds a known holding."
    assert ase.extract_tickers({}) == set(), "AC-12 GAP: extract_tickers({}) must degrade to set()."
    assert ase.extract_tickers(None) == set(), (
        "AC-12 GAP: extract_tickers(None) must degrade to set()."
    )


def test_apply_lens_blend_unchanged(ase):
    candidates = [{"ticker": "A", "score": 0.1}, {"ticker": "B", "score": 0.5}]
    assert ase._apply_lens_blend(candidates, None) == candidates, (
        "AC-12 GAP: _apply_lens_blend(candidates, None) must be a no-op (backward-compat)."
    )
    assert ase._apply_lens_blend(candidates, {}) == candidates, (
        "AC-12 GAP: _apply_lens_blend(candidates, {}) must be a no-op."
    )


def test_extract_lens_scores_unchanged(ase):
    assert ase.extract_lens_scores({}) == {}, "AC-12 GAP: extract_lens_scores({}) must be {}."
    assert ase.extract_lens_scores("not-a-dict") == {}, (
        "AC-12 GAP: extract_lens_scores must degrade gracefully on malformed input."
    )


# ===========================================================================
# Q4: generate_objective_directed_candidates DELETED — confirmed via AST.
# ===========================================================================


def test_generate_objective_directed_candidates_no_longer_defined(ase):
    """AST-based confirmed-deletion check (mirrors R2-2's identical check for
    logic_change_engine's deleted fixed-multiplier family)."""
    source = _ENGINE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    defined_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "generate_objective_directed_candidates" not in defined_names, (
        "AC-12/Q4 GAP: generate_objective_directed_candidates is still DEFINED in "
        "advisors/asset_swap_engine.py — Q4 requires full deletion, not deprecation."
    )
    assert not hasattr(ase, "generate_objective_directed_candidates"), (
        "AC-12/Q4 GAP: advisors.asset_swap_engine still exposes "
        "generate_objective_directed_candidates as a module attribute."
    )


def test_generate_objective_directed_candidates_has_no_production_caller():
    """Grep-style confirmation across app.py and advisors/ that no production
    module still references the deleted symbol (test files are reconciled
    separately, not in scope for this check)."""
    production_files = [
        _REPO_ROOT / "app.py",
        _REPO_ROOT / "advisors" / "asset_swap_engine.py",
        _REPO_ROOT / "advisors" / "weekly_suggestions_scheduler.py",
    ]
    for path in production_files:
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8")
        assert "generate_objective_directed_candidates" not in source, (
            f"AC-12/Q4 GAP: {path} still references generate_objective_directed_candidates."
        )
