"""RED tests — AC-7b (added mid-cycle, PM adjudication, plan commits
b730fa35/39d56fd5): a new engine-side rejection_reason token,
`oos_inferior_to_incumbent`, for the KEEP_INCUMBENT-on-OOS-grounds case.

WHY THIS EXISTS: a candidate that clears every hard veto (BHY winner, PBO,
SPY baseline) and is the batch's genuine FDR winner, but still loses to the
incumbent on the OOS-superiority precondition (acceptance_gate.py:257),
today persists rejection_reason=None — INDISTINGUISHABLE from a survivor in
the persisted record (the catch-all "fdr_not_winner" branch never fires for
this case pre-AC-7b, since `this_winner_trial_is_none` is False for the true
FDR winner; the candidate falls through backtest_gate_engine.py's rejection_
reason logic to the `else: "fdr_not_winner"` branch today, MISLABELING it).
Post-AC-17, this becomes the MOST COMMON Evaluate-path rejection (since
empty-params candidates no longer fail on panel-score alone), making this
class load-bearing.

CONTRACT (plan @ 39d56fd5, precedence corrected): stage-ordered
  pbo_veto > below_spy_alpha > fdr_not_winner (EXPLICIT this_winner_trial_is_none
  check, no longer a blind catch-all) > oos_inferior_to_incumbent > None (survivor)
oos_inferior_to_incumbent is the precise residual: cleared every veto, WAS
the batch's FDR winner, beat SPY, and still lost to the incumbent.
Implementation contained to backtest_gate_engine.py (new branch using the
already-available incumbent_oos_alpha batch param); acceptance_gate.py
untouched.

FIXTURES: uses MATCHING (non-empty, identical) candidate/incumbent/theory-
prior params — mirrors test_r1_n1_honesty.py's `_matching_candidate` pattern
— to isolate the OOS-inferior rejection class from the separate AC-17
panel-score-tie mechanism (both are tested independently; conflating them
here would make this file's RED signal ambiguous about which defect it's
proving).
"""

from __future__ import annotations

import pytest

_DATES = [f"2026-{m:02d}-{d:02d}" for m in range(1, 9) for d in range(1, 11)]

# Same proven-clears-BHY fixture pattern established across the R1 test
# suite (constant returns degenerate the bootstrap t-stat — see
# test_r1_n1_honesty.py's fixture-tuning notes).
_STRONG_POSITIVE_RETURNS_PCT = [1.5 if i % 10 != 9 else -0.2 for i in range(len(_DATES))]

_MATCHING_PARAMS = {"window": 20.0}


@pytest.fixture(scope="module")
def bge():
    import advisors.backtest_gate_engine as _bge  # noqa: PLC0415

    return _bge


def _matching_candidate(bge, candidate_id: str, returns_pct: list):
    return bge.BacktestCandidate(
        candidate_id=candidate_id,
        daily_returns_pct=returns_pct,
        candidate_params=dict(_MATCHING_PARAMS),
        incumbent_params=dict(_MATCHING_PARAMS),
        theory_prior_params=dict(_MATCHING_PARAMS),
    )


def test_ac7b_self_guard_matching_params_candidate_adopts_when_oos_superior(bge):
    """Self-guard: confirms the matching-params fixture reaches ADOPT_CANDIDATE
    when the OOS comparison is favorable (incumbent_oos_alpha=0.0, candidate
    beats it) — proves the fixture is a real FDR winner with a passing panel,
    isolating the OOS-inferior test below to genuinely be about the OOS
    comparison, not an accidental panel or FDR failure."""
    cand = _matching_candidate(bge, "ac7b-self-guard", list(_STRONG_POSITIVE_RETURNS_PCT))
    batch = bge.evaluate_candidate_batch([cand], incumbent_oos_alpha=0.0, default_oos_alpha=0.0)
    result = batch.results[0]
    assert result.verdict.decision == "ADOPT_CANDIDATE", (
        f"self-guard: expected the matching-params fixture to ADOPT when OOS-"
        f"superior; got {result.verdict.decision} "
        f"(winner_p_adj={result.winner_p_adj}, oos_alpha={result.oos_alpha})"
    )


def test_ac7b_oos_inferior_to_incumbent_rejection_reason(bge):
    """MUST FAIL pre-fix: today this exact case (real FDR winner, real panel
    pass, loses only on the OOS-superiority precondition) is mislabeled
    'fdr_not_winner' by the current catch-all branch — indistinguishable from
    a genuine non-winner. The SAME fixture that self-guard-proves ADOPT above
    is driven here with an artificially high incumbent_oos_alpha (forcing the
    OOS-inferior branch deterministically) to isolate this exact case."""
    cand = _matching_candidate(bge, "ac7b-oos-inferior", list(_STRONG_POSITIVE_RETURNS_PCT))
    batch = bge.evaluate_candidate_batch([cand], incumbent_oos_alpha=1000.0, default_oos_alpha=0.0)
    result = batch.results[0]
    assert result.verdict.decision == "KEEP_INCUMBENT", (
        f"self-guard: expected KEEP_INCUMBENT via the OOS-inferior branch; "
        f"got {result.verdict.decision}"
    )
    assert result.rejection_reason == "oos_inferior_to_incumbent", (
        f"AC-7b GAP: a candidate that cleared every veto, was the batch's "
        f"real FDR winner, and only lost on the OOS-superiority precondition "
        f"is mislabeled — got rejection_reason={result.rejection_reason!r}, "
        f"expected 'oos_inferior_to_incumbent'."
    )


def test_ac7b_oos_inferior_distinct_from_fdr_not_winner(bge):
    """The adversarial core: oos_inferior_to_incumbent and fdr_not_winner must
    be DIFFERENT tokens for DIFFERENT causes — a fix that just renames the
    catch-all branch (making everything oos_inferior_to_incumbent, including
    genuine FDR non-winners) would still fail this test. Uses a 2-candidate
    batch: a weak candidate that is NOT the FDR winner (this_winner_trial_is_none
    should end up True for it) alongside the strong OOS-inferior-forced
    candidate from the test above."""
    strong = _matching_candidate(
        bge, "ac7b-strong-oos-inferior", list(_STRONG_POSITIVE_RETURNS_PCT)
    )
    # A second, much weaker candidate — real but modest positive returns,
    # unlikely to be the batch's argmin winner alongside the strong one.
    weak_returns = [0.05 if i % 10 != 9 else -0.05 for i in range(len(_DATES))]
    weak = _matching_candidate(bge, "ac7b-weak-non-winner", weak_returns)

    batch = bge.evaluate_candidate_batch(
        [strong, weak], incumbent_oos_alpha=1000.0, default_oos_alpha=0.0
    )
    reasons = {r.candidate_id: r.rejection_reason for r in batch.results}

    assert reasons.get("ac7b-strong-oos-inferior") == "oos_inferior_to_incumbent", (
        f"AC-7b GAP: the batch's real FDR winner (forced OOS-inferior via "
        f"incumbent_oos_alpha) must carry 'oos_inferior_to_incumbent', not "
        f"any other reason. Got: {reasons}"
    )
    assert reasons.get("ac7b-weak-non-winner") != "oos_inferior_to_incumbent", (
        f"AC-7b GAP: the non-winner candidate must NOT also be labeled "
        f"'oos_inferior_to_incumbent' — a fix that collapses fdr_not_winner "
        f"into oos_inferior_to_incumbent for every non-adopting candidate "
        f"fails this adversarial check. Got: {reasons}"
    )


def test_ac7b_below_spy_alpha_dominates_oos_inferior_precedence(bge):
    """Precedence check: a candidate that is BOTH below the SPY baseline AND
    would separately be OOS-inferior-to-incumbent must report below_spy_alpha
    (higher in the stage-ordered precedence chain), not oos_inferior_to_
    incumbent."""
    from advisors.composer_backtest_client import (
        BacktestResult,  # noqa: F401 - not used directly, keeps import parity with sibling files
    )

    cand = _matching_candidate(bge, "ac7b-below-spy", list(_STRONG_POSITIVE_RETURNS_PCT))
    spy_series = {d: 100.0 for d in _DATES}  # SPY baseline far above the candidate.

    batch = bge.evaluate_candidate_batch(
        [cand],
        incumbent_oos_alpha=1000.0,  # would ALSO be OOS-inferior to the incumbent
        default_oos_alpha=0.0,
        spy_returns_fn=lambda: spy_series,
    )
    result = batch.results[0]
    assert result.rejection_reason == "below_spy_alpha", (
        f"AC-7b GAP: a candidate that is both below the SPY baseline AND "
        f"OOS-inferior to the incumbent must report 'below_spy_alpha' "
        f"(dominant in the precedence chain), got "
        f"rejection_reason={result.rejection_reason!r}."
    )
