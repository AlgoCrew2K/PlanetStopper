"""RED tests — AC-10 SUFFICIENCY EXTENSION (Toxic Pair review finding,
team-lead msg 91b1c29a): r1-engine's AC-10 GREEN (df4e1eee) is correctly
minimal to the originally-committed RED (test_r1_measured_value_honesty.py),
which only exercised the reduce_drawdown branch of each engine's
_build_objective_rationale. AC-10's acceptance bar is "no fabricated
measured claim" — full stop, not "none on the reduce_drawdown branch."

CONFIRMED BY READING THE CURRENT (post-GREEN) SOURCE DIRECTLY, not assumed:
  asset_swap_engine.py._build_objective_rationale (line 694):
    - reduce_correlation  -> STILL "the {measured:.2f} correlation..." (line 712) — UNFIXED
    - reduce_drawdown     -> FIXED (no measured_value reference at all)
    - lift_risk_adjusted  -> STILL "the measured Sharpe of {measured:.2f}" — UNFIXED
    - else (catch-all)    -> STILL "measured_value={measured}" literal — UNFIXED
  logic_change_engine.py._build_objective_rationale (line 774):
    - reduce_drawdown           -> FIXED (no measured_value reference at all)
    - lift_risk_adjusted        -> STILL "the measured Sharpe of {measured:.2f}" — UNFIXED
    - reduce_turnover           -> STILL "(signal-change rate: {measured:.2f})" — UNFIXED
    - improve_momentum_timing   -> STILL "(measured: {measured:.2f})" — UNFIXED
    - reduce_whipsaw            -> STILL "(signal rate: {measured:.2f})" — UNFIXED
    - else (catch-all)          -> STILL "measured_value={measured}" literal — UNFIXED

WEEKLY SCHEDULER SITES (weekly_suggestions_scheduler.py, function-level, no
HTTP route — per team-lead's explicit instruction): the logic-change site
(:135) uses objective_type="reduce_drawdown" — the ALREADY-FIXED branch, so
that specific call site is coincidentally honest today (confirmed by
reading _DEFAULT_LOGIC_CHANGE_OBJECTIVE_TYPE's value directly, not assumed —
pinned as a regression guard below). The asset-swap site (:380) uses
objective_type="reduce_correlation" — the STILL-BROKEN branch, so that call
site genuinely needs the fix (RED below).

TEST STRATEGY: function-level, calling _build_objective_rationale directly
with measured_value=0.0 (mirrors every current production caller) — no
route, no backtest mocking needed. Per-branch fabricated substrings are
pinned to the EXACT current wording (read from source, not guessed) so each
assertion targets a real, verifiable defect.
"""

from __future__ import annotations

import pytest

# ===========================================================================
# asset_swap_engine.py — all branches.
# ===========================================================================


@pytest.fixture(scope="module")
def ase():
    import advisors.asset_swap_engine as _ase  # noqa: PLC0415

    return _ase


@pytest.mark.parametrize(
    "objective_type,fabricated_substring",
    [
        ("reduce_correlation", "0.00 correlation"),
        ("lift_risk_adjusted", "measured Sharpe of 0.00"),
        (
            "volatility_mitigation",
            "measured_value=0.0",
        ),  # falls to the else catch-all — not a real objective_type, proves the catch-all itself is honest for any unrecognized value
    ],
)
def test_ac10_asset_swap_rationale_honest_across_all_branches(
    ase, objective_type, fabricated_substring
):
    """MUST FAIL pre-fix for reduce_correlation/lift_risk_adjusted/the
    catch-all — only reduce_drawdown was fixed by the original RED's scope."""
    objective = ase.SwapObjective(
        objective_type=objective_type, target_pair=None, measured_value=0.0
    )
    rationale = ase._build_objective_rationale("CAND0", "AAA", objective)
    assert fabricated_substring not in rationale, (
        f"AC-10 GAP ({objective_type}): rationale still carries the "
        f"fabricated '{fabricated_substring}' phrase. Full rationale: {rationale!r}"
    )


def test_ac10_asset_swap_reduce_drawdown_regression_guard(ase):
    """Non-regression: confirms the already-fixed branch stays fixed (this
    test passed both before and after this file's own RED — pinning it
    prevents a future edit from re-introducing the fabricated phrase here
    while fixing the other branches)."""
    objective = ase.SwapObjective(
        objective_type="reduce_drawdown", target_pair=None, measured_value=0.0
    )
    rationale = ase._build_objective_rationale("CAND0", "AAA", objective)
    assert "measured" not in rationale.lower(), (
        f"AC-10 REGRESSION: reduce_drawdown rationale carries the word "
        f"'measured' again. Full rationale: {rationale!r}"
    )


# ===========================================================================
# logic_change_engine.py — all branches.
# ===========================================================================


@pytest.fixture(scope="module")
def lce():
    import advisors.logic_change_engine as _lce  # noqa: PLC0415

    return _lce


def _tweak(lce):
    return lce.LogicTweak(
        node_path=["children", 0],
        param_key="window",
        old_value=20,
        new_value=16,
        node_description="test",
    )


@pytest.mark.parametrize(
    "objective_type,fabricated_substring",
    [
        ("lift_risk_adjusted", "measured Sharpe of 0.00"),
        ("reduce_turnover", "signal-change rate: 0.00"),
        ("improve_momentum_timing", "measured: 0.00"),
        ("reduce_whipsaw", "signal rate: 0.00"),
        ("volatility_mitigation", "measured_value=0.0"),  # else catch-all
    ],
)
def test_ac10_logic_change_rationale_honest_across_all_branches(
    lce, objective_type, fabricated_substring
):
    """MUST FAIL pre-fix for every branch except reduce_drawdown."""
    objective = lce.LogicChangeObjective(
        objective_type=objective_type, measured_value=0.0, rationale="test"
    )
    rationale = lce._build_objective_rationale(_tweak(lce), objective)
    assert fabricated_substring not in rationale, (
        f"AC-10 GAP ({objective_type}): rationale still carries the "
        f"fabricated '{fabricated_substring}' phrase. Full rationale: {rationale!r}"
    )


def test_ac10_logic_change_reduce_drawdown_regression_guard(lce):
    """Non-regression: the already-fixed branch stays fixed."""
    objective = lce.LogicChangeObjective(
        objective_type="reduce_drawdown", measured_value=0.0, rationale="test"
    )
    rationale = lce._build_objective_rationale(_tweak(lce), objective)
    assert "measured" not in rationale.lower(), (
        f"AC-10 REGRESSION: reduce_drawdown rationale carries the word "
        f"'measured' again. Full rationale: {rationale!r}"
    )


# ===========================================================================
# weekly_suggestions_scheduler.py — the two production call sites, function-
# level (no HTTP route exists for the scheduler path).
# ===========================================================================


def test_ac10_weekly_asset_swap_objective_construction_produces_honest_rationale(ase):
    """MUST FAIL pre-fix: weekly_suggestions_scheduler.py's asset-swap site
    (:380) uses objective_type='reduce_correlation' (confirmed by reading
    _DEFAULT_ASSET_SWAP_OBJECTIVE_TYPE's value directly) — the STILL-BROKEN
    branch. Constructs the SwapObjective the exact same way the weekly
    scheduler does (mirrors wss.py:378-382) and drives the real rationale
    builder — no route, no backtest mocking, per team-lead's instruction."""
    import advisors.weekly_suggestions_scheduler as wss

    objective = ase.SwapObjective(
        objective_type=wss._DEFAULT_ASSET_SWAP_OBJECTIVE_TYPE,
        target_pair=None,
        measured_value=0.0,
    )
    rationale = ase._build_objective_rationale("CAND0", "AAA", objective)
    assert "0.00 correlation" not in rationale, (
        f"AC-10 GAP (weekly asset-swap site): rationale still carries the "
        f"fabricated '0.00 correlation' phrase using the scheduler's real "
        f"default objective_type ({wss._DEFAULT_ASSET_SWAP_OBJECTIVE_TYPE!r}). "
        f"Full rationale: {rationale!r}"
    )


def test_ac10_weekly_logic_change_objective_construction_already_honest(lce):
    """Regression guard, NOT a RED test: weekly_suggestions_scheduler.py's
    logic-change site (:135) uses objective_type='reduce_drawdown'
    (confirmed by reading _DEFAULT_LOGIC_CHANGE_OBJECTIVE_TYPE's value
    directly) — the branch the ORIGINAL AC-10 RED already fixed. This call
    site is coincidentally already honest today; pinned here so a future
    edit can't silently reintroduce the fabricated phrase on this specific
    production path without a test noticing."""
    import advisors.weekly_suggestions_scheduler as wss

    objective = lce.LogicChangeObjective(
        objective_type=wss._DEFAULT_LOGIC_CHANGE_OBJECTIVE_TYPE,
        measured_value=0.0,
        rationale="Weekly automatic suggestion sweep",
    )
    rationale = lce._build_objective_rationale(_tweak(lce), objective)
    assert "measured" not in rationale.lower(), (
        f"AC-10 REGRESSION (weekly logic-change site): rationale carries "
        f"the word 'measured' using the scheduler's real default "
        f"objective_type ({wss._DEFAULT_LOGIC_CHANGE_OBJECTIVE_TYPE!r}). "
        f"Full rationale: {rationale!r}"
    )
