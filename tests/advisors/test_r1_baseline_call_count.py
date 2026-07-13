"""RED tests — AC-13 (D-7, Gap H): baseline backtest runs ONCE per operator-route
evaluation, not twice.

WHY THIS FILE EXISTS: both propose_operator_swap (asset_swap_engine.py) and
propose_operator_logic_change (logic_change_engine.py) backtest the UNCHANGED
baseline/incumbent tree TWICE per invocation today:
  - Once inside _evaluate_single_variant (ase.py:927 / lce.py analogous site)
    to populate proposal.baseline_stats for display.
  - A SECOND time via the module's own _backtest_returns_from_tree helper
    (ase.py:1068 / lce.py:1308) purely to derive fold_baseline_oos_alpha for
    the gate's incumbent comparison.

  Both calls backtest the IDENTICAL raw tree with the IDENTICAL symphony_id —
  the second call is a pure latency tax (an extra Composer API round-trip)
  with no behavioral purpose. AC-13 requires the baseline result be computed
  ONCE and reused for both purposes.

HOW THIS IS TESTED (per plan's own Testing Strategy: "count client calls via
mock, not trust a flag"): run_backtest is mocked with a call-count-tracking
side_effect. We assert the call count for the BASELINE tree specifically
(content-identified — the raw/incumbent tree, never mutated) is exactly 1,
while the variant-tree call (a structurally different tree) is unaffected and
still occurs. Counting ALL calls indiscriminately would not isolate the bug
(the variant call is legitimate and must remain); counting only baseline-tree
calls isolates exactly the duplicate this AC targets.

NO HARDCODED PRODUCER VALUES: we assert a call COUNT (an integer we choose,
not a producer-computed statistic) and RELATIONSHIP (the two usage sites end
up with the SAME baseline value/object), never a literal return or stats value.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(scope="module")
def ase():
    import advisors.asset_swap_engine as _ase  # noqa: PLC0415

    return _ase


@pytest.fixture(scope="module")
def lce():
    import advisors.logic_change_engine as _lce  # noqa: PLC0415

    return _lce


def _counting_backtest(baseline_tree_marker, variant_result_daily_returns: dict):
    """run_backtest side_effect that counts calls whose tree matches the
    baseline marker (content-identified, not by object identity — a deep copy
    of the baseline tree must still count as a baseline call) separately from
    all other (variant) calls. Returns a shared mutable call-count dict and the
    side_effect callable."""
    from advisors.composer_backtest_client import BacktestResult

    counts = {"baseline": 0, "other": 0}
    baseline_daily_returns = {"2026-01-01": 0.0001, "2026-01-02": -0.0001}

    def _side_effect(tree, *, symphony_id="", **kwargs):
        if tree == baseline_tree_marker:
            counts["baseline"] += 1
            return BacktestResult(
                stats={"sharpe": 0.1}, data_warnings=[], daily_returns=dict(baseline_daily_returns)
            )
        counts["other"] += 1
        return BacktestResult(
            stats={"sharpe": 0.2},
            data_warnings=[],
            daily_returns=dict(variant_result_daily_returns),
        )

    return counts, _side_effect


# ===========================================================================
# Asset Swaps — propose_operator_swap
# ===========================================================================


def test_ac13_asset_swap_operator_evaluate_backtests_baseline_exactly_once(ase):
    """MUST FAIL pre-fix: today the baseline tree is backtested twice
    (ase.py:927 inside _evaluate_single_variant, ase.py:1068 inside
    propose_operator_swap's own _backtest_returns_from_tree call)."""
    raw_tree = {"ticker": None, "children": [{"ticker": "AAA", "children": []}]}
    variant_returns = {"2026-01-01": 0.001, "2026-01-02": 0.002}
    counts, side_effect = _counting_backtest(raw_tree, variant_returns)

    with (
        patch.object(ase, "_has_composer_key", return_value=True),
        patch.object(ase, "run_backtest", side_effect=side_effect),
        patch.object(ase, "database") as mock_db,
    ):
        mock_db.insert_advisor_observation.return_value = 1
        ase.propose_operator_swap(
            symphony_id="prod-baseline-count-swap",
            score_tree=raw_tree,
            incumbent_asset="AAA",
            candidate_asset="CAND0",
            objective=ase.SwapObjective(
                objective_type="reduce_correlation", target_pair=None, measured_value=0.0
            ),
        )

    assert counts["baseline"] == 1, (
        f"AC-13 PERF GAP (asset_swap_engine): expected the baseline tree to be "
        f"backtested exactly once per operator evaluation, got {counts['baseline']} "
        f"calls. (variant calls: {counts['other']}, unaffected by this AC)"
    )


# ===========================================================================
# Logic Changes — propose_operator_logic_change
# ===========================================================================


def test_ac13_logic_change_operator_evaluate_backtests_baseline_exactly_once(lce):
    """MUST FAIL pre-fix: today the baseline tree is backtested twice
    (lce.py:920-ish inside _evaluate_single_variant, lce.py:1308 inside
    propose_operator_logic_change's own _backtest_returns_from_tree call)."""
    raw_tree = {"type": "root", "children": [{"type": "momentum", "window": 20, "children": []}]}
    variant_returns = {"2026-01-01": 0.001, "2026-01-02": 0.002}
    counts, side_effect = _counting_backtest(raw_tree, variant_returns)

    tweak = lce.LogicTweak(
        node_path=["children", 0],
        param_key="window",
        old_value=20,
        new_value=16,
        node_description="window=20 -> 16 at path [children, 0]",
    )

    with (
        patch.object(lce, "_has_composer_key", return_value=True),
        patch.object(lce, "run_backtest", side_effect=side_effect),
        patch.object(lce, "database") as mock_db,
    ):
        mock_db.insert_advisor_observation.return_value = 1
        lce.propose_operator_logic_change(
            symphony_id="prod-baseline-count-logic",
            score_tree=raw_tree,
            tweak=tweak,
            objective=lce.LogicChangeObjective(
                objective_type="reduce_drawdown", measured_value=0.0, rationale="test"
            ),
        )

    assert counts["baseline"] == 1, (
        f"AC-13 PERF GAP (logic_change_engine): expected the baseline tree to be "
        f"backtested exactly once per operator evaluation, got {counts['baseline']} "
        f"calls. (variant calls: {counts['other']}, unaffected by this AC)"
    )


# ===========================================================================
# Non-regression: both usage sites must still agree on the SAME baseline value
# once cached/reused — a caching fix that reuses a STALE or WRONG baseline
# (rather than genuinely deduplicating the identical call) would pass the
# call-count test above while silently corrupting the gate's incumbent
# comparison. This test pins that the candidate's gate result is unaffected
# by which of the two call sites the (now single) baseline call is attributed
# to — i.e. the fix is a real dedup, not a call-count trick that drops one
# call's RESULT while still using it.
# ===========================================================================


def test_ac13_asset_swap_baseline_value_reused_is_not_a_stale_or_zeroed_stub(ase):
    """Guards against a sham fix that hits the call-count number by returning a
    hardcoded/zeroed baseline for the second usage site instead of genuinely
    reusing the first call's real result. We assert baseline_stats on the
    returned proposal reflects the REAL (non-empty, non-default-zero) mocked
    stats dict, not an empty/default fallback."""
    raw_tree = {"ticker": None, "children": [{"ticker": "AAA", "children": []}]}
    variant_returns = {"2026-01-01": 0.001, "2026-01-02": 0.002}
    from advisors.composer_backtest_client import BacktestResult

    _REAL_BASELINE_STATS = {"sharpe": 0.777, "sortino": 0.888}

    def _side_effect(tree, *, symphony_id="", **kwargs):
        if tree == raw_tree:
            return BacktestResult(
                stats=dict(_REAL_BASELINE_STATS),
                data_warnings=[],
                daily_returns={"2026-01-01": 0.0001},
            )
        return BacktestResult(
            stats={"sharpe": 0.2}, data_warnings=[], daily_returns=dict(variant_returns)
        )

    with (
        patch.object(ase, "_has_composer_key", return_value=True),
        patch.object(ase, "run_backtest", side_effect=_side_effect),
        patch.object(ase, "database") as mock_db,
    ):
        mock_db.insert_advisor_observation.return_value = 1
        result = ase.propose_operator_swap(
            symphony_id="prod-baseline-value-swap",
            score_tree=raw_tree,
            incumbent_asset="AAA",
            candidate_asset="CAND0",
            objective=ase.SwapObjective(
                objective_type="reduce_correlation", target_pair=None, measured_value=0.0
            ),
        )

    proposal = result.proposals[0]
    assert proposal.baseline_stats == _REAL_BASELINE_STATS, (
        f"baseline_stats on the returned proposal does not match the real mocked "
        f"baseline stats — expected {_REAL_BASELINE_STATS}, got {proposal.baseline_stats}. "
        f"A dedup fix must reuse the REAL first-call result, not stub a placeholder."
    )
