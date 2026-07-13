"""RED tests — AC-4/AC-5 PRODUCTION-PATH wiring for logic_change_engine.py.

Sibling file to test_asset_swap_production_wiring.py — same C2-hollow-trap
lesson (see that file's module docstring for the full rationale), applied to
logic_change_engine.py's THREE evaluate_candidate_batch call sites (lce.py
:1289 defensive zero-candidate branch, :1316 the N=1 operator-Evaluate path
inside propose_operator_logic_change, :1466 the real batch path inside
suggest_logic_changes). audit F2 traced all three to the same gap: the sole
BacktestCandidate(...) construction (in _evaluate_single_variant) omits
dated_returns=, and no call site passes spy_returns_fn=.

SCOPE PER PM DIRECTIVE: identical to the asset_swap sibling file — AC-4/AC-5
wiring required at all three call sites unconditionally, including the N=1
operator-Evaluate site, where PBO stays structurally inert (guard, not a
wiring gap) but the SPY-fold baseline is a real, visible behavior change.

WHAT IS MOCKED: run_backtest (date-keyed daily_returns dicts, dispatched by
tree content — a logic-change variant tree is identified by its tweaked
`window` value at children[0], since these trees differ by parameter value
rather than by ticker composition), _has_composer_key (-> True),
generate_objective_directed_candidates (-> a controlled list of LogicTweak
objects, each structurally valid against the shared raw tree so
apply_logic_tweak succeeds for real), database (no real persistence).

WHAT IS NOT MOCKED: evaluate_candidate_batch, math_engine.compute_pbo,
apply_logic_tweak, the fold transform, BHY/Yekutieli FDR — identical
non-mock set to the asset_swap sibling and to test_cull_production_wiring.py.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from math_engine import PBO_REJECT_THRESHOLD, compute_pbo

# ---------------------------------------------------------------------------
# Module under test.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def lce():
    import advisors.logic_change_engine as _lce  # noqa: PLC0415

    return _lce


# ---------------------------------------------------------------------------
# Date-keyed return fixtures — identical construction to the two sibling
# production-wiring files so the real compute_pbo behaviour is provably the
# same math across all three engines.
# ---------------------------------------------------------------------------

_DATES = [f"2026-{m:02d}-{d:02d}" for m in range(1, 9) for d in range(1, 11)]


def _per_block_overfit_logreturns() -> list[dict[str, float]]:
    blocks = [_DATES[i * 10 : (i + 1) * 10] for i in range(8)]
    configs: list[dict[str, float]] = []
    for b in range(8):
        cfg: dict[str, float] = {}
        for i, blk in enumerate(blocks):
            for d in blk:
                cfg[d] = 0.20 if i == b else -0.01
        configs.append(cfg)
    return configs


def _below_spy_logreturns(n: int, *, level: float) -> list[dict[str, float]]:
    return [{d: level * (0.5 + 0.1 * j) for d in _DATES} for j in range(n)]


def _spy_logreturns(level: float) -> dict[str, float]:
    return {d: level for d in _DATES}


def test_production_fixture_high_pbo_exceeds_threshold_via_real_compute_pbo():
    pbo = compute_pbo(_per_block_overfit_logreturns(), _DATES, 1.0)
    assert pbo > PBO_REJECT_THRESHOLD, (
        f"high-PBO production fixture must exceed the reject threshold; got {pbo}"
    )


def test_production_below_spy_fixture_is_low_pbo_via_real_compute_pbo():
    pbo = compute_pbo(_below_spy_logreturns(3, level=0.001), _DATES, 1.0)
    assert pbo <= PBO_REJECT_THRESHOLD, (
        f"below-SPY production fixture must be LOW-pbo so the SPY cause is isolated; got {pbo}"
    )


# ---------------------------------------------------------------------------
# Tree / tweak helpers. node_path=["children", 0] matches the codebase's own
# established test convention (tests/ai_advisor/test_logic_change_engine.py
# _make_logic_tweak) so apply_logic_tweak succeeds for real, never producing a
# silent None-variant that would vacuously skip the candidate.
# ---------------------------------------------------------------------------


def _raw_tree() -> dict:
    return {"type": "root", "children": [{"type": "momentum", "window": 20, "children": []}]}


def _make_tweak(lce, new_value: float):
    return lce.LogicTweak(
        node_path=["children", 0],
        param_key="window",
        old_value=20,
        new_value=new_value,
        node_description=f"window=20 -> {new_value} at path [children, 0]",
    )


def _tree_window(tree: object) -> object:
    """Extract the children[0].window value used to identify which candidate
    variant a given backtest call is for — content-based dispatch, never
    call-order (mirrors the ticker-based dispatch in the asset_swap sibling)."""
    if isinstance(tree, dict):
        children = tree.get("children") or []
        if children and isinstance(children[0], dict):
            return children[0].get("window")
    return None


def _is_spy_benchmark_tree(tree: object) -> bool:
    """SPY benchmark tree identified by content: its only ticker is SPY (mirrors
    both sibling production-wiring files' SPY-detection convention — the AC-25
    'existing backtest path' seam sources SPY via the same run_backtest client
    used for candidates)."""
    tickers: set[str] = set()
    stack = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("ticker"):
                tickers.add(node["ticker"])
            for child in node.get("children", []) or []:
                stack.append(child)
    return tickers == {"SPY"}


def _spy_aware_backtest(
    series_by_window: dict[float, dict[str, float]],
    spy_series: dict[str, float],
    *,
    baseline_series: dict[str, float] | None = None,
):
    from advisors.composer_backtest_client import BacktestResult

    _baseline = baseline_series or {d: -0.001 for d in _DATES}

    def _side_effect(tree, *, symphony_id="", **kwargs):
        if _is_spy_benchmark_tree(tree):
            return BacktestResult(
                stats={"sharpe": 1.0}, data_warnings=[], daily_returns=dict(spy_series)
            )
        window = _tree_window(tree)
        if window in series_by_window:
            return BacktestResult(
                stats={"sharpe": 0.5},
                data_warnings=[],
                daily_returns=dict(series_by_window[window]),
            )
        # Baseline / incumbent tree (window == 20, unchanged).
        return BacktestResult(
            stats={"sharpe": 0.4}, data_warnings=[], daily_returns=dict(_baseline)
        )

    return _side_effect


def _all_rejection_reasons(result) -> list:
    return [
        getattr(r, "rejection_reason", "<<no rejection_reason field>>")
        for r in result.gate_batch.results
    ]


# ===========================================================================
# AC-4 BATCH — PBO veto must FIRE through the REAL suggest_logic_changes.
# ===========================================================================


def test_ac4_production_high_pbo_batch_is_pbo_vetoed_end_to_end(lce):
    """MUST FAIL pre-wiring — identical gap to the asset_swap sibling: the sole
    BacktestCandidate(...) construction omits dated_returns=."""
    configs = _per_block_overfit_logreturns()
    windows = [10.0 + i for i in range(len(configs))]  # distinct new_value per candidate
    series_by_window = dict(zip(windows, configs))
    tweaks = [_make_tweak(lce, w) for w in windows]
    spy = _spy_logreturns(0.0)

    with (
        patch.object(lce, "_has_composer_key", return_value=True),
        patch.object(lce, "generate_objective_directed_candidates", return_value=tweaks),
        patch.object(lce, "run_backtest", side_effect=_spy_aware_backtest(series_by_window, spy)),
        patch.object(lce, "database") as mock_db,
    ):
        mock_db.insert_advisor_observation.return_value = 1
        result = lce.suggest_logic_changes(
            symphony_id="prod-pbo-logic",
            score_tree=_raw_tree(),
            objective=lce.LogicChangeObjective(
                objective_type="reduce_drawdown", measured_value=0.0, rationale="test"
            ),
        )

    reasons = _all_rejection_reasons(result)
    assert any("pbo" in str(r).lower() for r in reasons), (
        "PRODUCTION HOLLOW GAP (AC-4, logic_change_engine): a high-PBO batch through "
        "the REAL suggest_logic_changes produced NO candidate with a 'pbo' "
        f"rejection_reason. Got reasons: {reasons}"
    )


# ===========================================================================
# AC-5 BATCH — SPY-fold baseline must BITE through the REAL suggest_logic_changes.
# ===========================================================================


def test_ac5_production_below_spy_batch_rejected_below_spy_alpha_end_to_end(lce):
    """MUST FAIL pre-wiring — identical gap to the asset_swap sibling."""
    configs = _below_spy_logreturns(3, level=0.0005)
    windows = [10.0 + i for i in range(len(configs))]
    series_by_window = dict(zip(windows, configs))
    tweaks = [_make_tweak(lce, w) for w in windows]
    spy = _spy_logreturns(0.02)

    with (
        patch.object(lce, "_has_composer_key", return_value=True),
        patch.object(lce, "generate_objective_directed_candidates", return_value=tweaks),
        patch.object(lce, "run_backtest", side_effect=_spy_aware_backtest(series_by_window, spy)),
        patch.object(lce, "database") as mock_db,
    ):
        mock_db.insert_advisor_observation.return_value = 1
        result = lce.suggest_logic_changes(
            symphony_id="prod-spy-logic",
            score_tree=_raw_tree(),
            objective=lce.LogicChangeObjective(
                objective_type="reduce_drawdown", measured_value=0.0, rationale="test"
            ),
        )

    reasons = _all_rejection_reasons(result)
    assert any(str(r) == "below_spy_alpha" for r in reasons), (
        "PRODUCTION HOLLOW GAP (AC-5, logic_change_engine): a positive-but-below-SPY "
        "batch through the REAL suggest_logic_changes produced NO candidate with a "
        f"'below_spy_alpha' rejection_reason. Got reasons: {reasons}"
    )


# ===========================================================================
# N=1 negative invariant (AC-4 edge case).
# ===========================================================================


def test_ac4_n1_operator_evaluate_never_carries_pbo_veto_even_after_wiring(lce):
    """A single-candidate operator Evaluate must NEVER report rejection_reason
    == 'pbo_veto' — _PBO_MIN_CONFIGS=2 keeps PBO structurally None at K=1."""
    candidate_series = {d: 0.05 for d in _DATES}
    spy = _spy_logreturns(0.0)

    with (
        patch.object(lce, "_has_composer_key", return_value=True),
        patch.object(
            lce, "run_backtest", side_effect=_spy_aware_backtest({16.0: candidate_series}, spy)
        ),
        patch.object(lce, "database") as mock_db,
    ):
        mock_db.insert_advisor_observation.return_value = 1
        result = lce.propose_operator_logic_change(
            symphony_id="prod-n1-pbo-logic",
            score_tree=_raw_tree(),
            tweak=_make_tweak(lce, 16.0),
            objective=lce.LogicChangeObjective(
                objective_type="reduce_drawdown", measured_value=0.0, rationale="test"
            ),
        )

    reasons = _all_rejection_reasons(result)
    assert "pbo_veto" not in [str(r) for r in reasons], (
        f"AC-4 N=1 GUARD VIOLATED: propose_operator_logic_change reported a "
        f"pbo_veto rejection_reason at N=1. Got reasons: {reasons}"
    )


# ===========================================================================
# N=1 SPY visible-behavior-change (AC-5).
# ===========================================================================


def test_ac5_n1_operator_evaluate_beats_zero_loses_to_spy_withholds(lce):
    """MUST FAIL pre-wiring: today the N=1 operator-Evaluate path gates on
    beats-a-flat-0.0%-return only."""
    candidate_series = {d: 0.0005 for d in _DATES}
    spy = _spy_logreturns(0.02)

    with (
        patch.object(lce, "_has_composer_key", return_value=True),
        patch.object(
            lce, "run_backtest", side_effect=_spy_aware_backtest({16.0: candidate_series}, spy)
        ),
        patch.object(lce, "database") as mock_db,
    ):
        mock_db.insert_advisor_observation.return_value = 1
        result = lce.propose_operator_logic_change(
            symphony_id="prod-n1-spy-logic",
            score_tree=_raw_tree(),
            tweak=_make_tweak(lce, 16.0),
            objective=lce.LogicChangeObjective(
                objective_type="reduce_drawdown", measured_value=0.0, rationale="test"
            ),
        )

    reasons = _all_rejection_reasons(result)
    assert any(str(r) == "below_spy_alpha" for r in reasons), (
        "PRODUCTION HOLLOW GAP (AC-5, N=1 operator Evaluate, logic_change_engine): "
        "a candidate that beats 0.0% but loses to SPY was not withheld with "
        f"below_spy_alpha. Got reasons: {reasons}"
    )
    assert result.gate_batch.results[0].verdict.decision != "ADOPT_CANDIDATE", (
        "A below-SPY candidate must not be ADOPT_CANDIDATE once the SPY baseline is wired."
    )
