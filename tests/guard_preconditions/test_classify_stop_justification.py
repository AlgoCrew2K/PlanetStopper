"""
RED tests -- guard_preconditions.classify_stop_justification (AC-2, AC-3,
AC-4, AC-9).

VERDICT-CLASS RESOLUTION (documented here because AC-3's literal wording
leaves one region under-specified -- flagged to the PM per the plan-first
protocol, non-blocking open question, see .claude/tdd-handoff.md):

  AC-3 gives NOT_MET's condition as "rho + CI < SR AND random-walk not
  rejected". No formula for a standalone random-walk-rejection test appears
  anywhere in the plan (no Ljung-Box/Durbin-Watson threshold, and AC-1's
  required outputs list rho/CI/SR/N only -- no test statistic) -- read as
  DESCRIPTIVE commentary on why NOT_MET implies the K&L expected-return-drag
  conclusion, not an independently computed gate.

  The literal per-class conditions (SUFFICIENT_MET: rho-CI>=SR; SUFFICIENT_
  LIKELY: rho>=SR but CI overlaps; NEGATIVE_EDGE: SR<=0) leave one numeric
  region unaddressed: rho < SR but rho+CI >= SR (point estimate says "not
  met" but the CI reaches up to touch/cross SR). This is NOT sufficient_
  LIKELY's mirror (SUFFICIENT_LIKELY requires rho>=SR by the plan's own
  wording) and the plan defines exactly 5 classes with no 6th "ambiguous
  negative" bucket. RESOLVED as an ORDERED PRIORITY CHAIN (this is the
  contract these tests pin):

      1. n_obs < N_MIN_OBS (or SR/rho undefined)   -> INSUFFICIENT_DATA
      2. elif SR <= 0                              -> NEGATIVE_EDGE
      3. elif rho - CI >= SR                        -> SUFFICIENT_MET
      4. elif rho >= SR                              -> SUFFICIENT_LIKELY
      5. else (includes the ambiguous region above) -> NOT_MET

  Rationale for defaulting the ambiguous region to NOT_MET rather than
  SUFFICIENT_LIKELY: (a) SUFFICIENT_LIKELY's own plan-literal condition
  requires rho>=SR, which this region violates by construction; (b) AC-4
  requires the copy never overclaim sufficiency -- defaulting an uncertain,
  point-estimate-negative case to NOT_MET (whose copy is scoped to
  "expected-return... sufficient condition not met", never "stop
  unjustified") is the conservative, honest reading; (c) it is the natural
  terminal else-branch of a priority chain, matching how AC-3's bullets read
  as an ordered list of positive-membership checks.
"""

from __future__ import annotations

import inspect
import json
import math
import pathlib

import pytest

import guard_preconditions as gp
from tests.guard_preconditions._reference_stats import (
    reference_acf1,
    reference_ci95,
    reference_sharpe_daily,
)

_FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "math"

_ALL_VERDICTS = {
    "SUFFICIENT_MET",
    "SUFFICIENT_LIKELY",
    "NOT_MET",
    "NEGATIVE_EDGE",
    "INSUFFICIENT_DATA",
}


def _stats(
    rho: float, rho_ci: float, sharpe_daily: float, n_obs: int = 100, insufficient_reason=None
):
    return gp.PersistenceStats(
        rho=rho,
        rho_ci=rho_ci,
        sharpe_daily=sharpe_daily,
        n_obs=n_obs,
        insufficient_reason=insufficient_reason,
    )


# ---------------------------------------------------------------------------
# AC-3: exactly 5 verdict classes, exact spelling
# ---------------------------------------------------------------------------


class TestVerdictClassNames:
    def test_all_five_classes_reachable_with_exact_names(self):
        cases = [
            ("SUFFICIENT_MET", _stats(rho=0.5, rho_ci=0.05, sharpe_daily=0.1)),
            ("SUFFICIENT_LIKELY", _stats(rho=0.5, rho_ci=0.45, sharpe_daily=0.1)),
            ("NOT_MET", _stats(rho=0.01, rho_ci=0.05, sharpe_daily=0.5)),
            ("NEGATIVE_EDGE", _stats(rho=0.9, rho_ci=0.05, sharpe_daily=-0.2)),
            (
                "INSUFFICIENT_DATA",
                _stats(
                    rho=0.5,
                    rho_ci=0.05,
                    sharpe_daily=0.1,
                    n_obs=10,
                    insufficient_reason="n_obs below N_MIN_OBS",
                ),
            ),
        ]
        for expected, stats in cases:
            got = gp.classify_stop_justification(stats)
            assert got == expected, f"stats={stats!r} expected verdict {expected!r}, got {got!r}"
            assert got in _ALL_VERDICTS


# ---------------------------------------------------------------------------
# AC-3: boundary straddles (the discriminating >= vs > edge cases)
# ---------------------------------------------------------------------------


class TestSufficientMetBoundary:
    def test_exact_equality_rho_minus_ci_equals_sr_is_sufficient_met(self):
        """AC-3: 'rho - CI >= SR' -- the boundary is inclusive."""
        stats = _stats(rho=0.30, rho_ci=0.10, sharpe_daily=0.20)  # rho-ci == sr exactly
        assert gp.classify_stop_justification(stats) == "SUFFICIENT_MET"

    def test_just_below_boundary_is_sufficient_likely(self):
        stats = _stats(rho=0.30, rho_ci=0.1000001, sharpe_daily=0.20)  # rho-ci < sr by epsilon
        assert gp.classify_stop_justification(stats) == "SUFFICIENT_LIKELY"


class TestSufficientLikelyBoundary:
    def test_exact_equality_rho_equals_sr_is_sufficient_likely(self):
        """AC-3: 'rho >= SR but CI overlaps' -- rho==SR with a wide CI (does
        not clear SUFFICIENT_MET) lands in SUFFICIENT_LIKELY, not NOT_MET."""
        stats = _stats(rho=0.20, rho_ci=0.15, sharpe_daily=0.20)  # rho == sr exactly, ci overlaps
        assert gp.classify_stop_justification(stats) == "SUFFICIENT_LIKELY"

    def test_just_below_sr_is_not_met(self):
        stats = _stats(rho=0.1999999, rho_ci=0.15, sharpe_daily=0.20)  # rho < sr by epsilon
        assert gp.classify_stop_justification(stats) == "NOT_MET"


class TestNotMetAmbiguousRegion:
    def test_rho_below_sr_but_ci_reaches_sr_is_not_met(self):
        """Pins the priority-chain resolution documented in this module's
        docstring: rho < SR (point estimate says not met) but rho+CI >= SR
        (CI reaches up to touch SR) is NOT_MET, not SUFFICIENT_LIKELY --
        SUFFICIENT_LIKELY's own condition requires rho>=SR."""
        stats = _stats(rho=0.10, rho_ci=0.20, sharpe_daily=0.20)  # rho<sr, rho+ci=0.30>sr
        assert gp.classify_stop_justification(stats) == "NOT_MET"

    def test_confidently_below_sr_is_not_met(self):
        stats = _stats(rho=0.01, rho_ci=0.02, sharpe_daily=0.50)  # rho+ci << sr
        assert gp.classify_stop_justification(stats) == "NOT_MET"


class TestNegativeEdgeBoundary:
    def test_sr_exactly_zero_is_negative_edge_not_not_met(self):
        """AC-3: 'SR <= 0' -- the boundary is inclusive (<=, not <)."""
        stats = _stats(rho=0.01, rho_ci=0.30, sharpe_daily=0.0)
        assert gp.classify_stop_justification(stats) == "NEGATIVE_EDGE"

    def test_sr_negative_with_high_rho_is_negative_edge_not_sufficient(self):
        """THE adversarial trap AC-3 names explicitly: 'SR <= 0 -> rho >= SR
        is trivially satisfiable' -- a naive implementation that checks
        rho>=SR (or rho-CI>=SR) BEFORE gating on SR<=0 would wrongly return
        SUFFICIENT_MET/LIKELY here, since any real rho trivially clears a
        negative SR."""
        stats = _stats(rho=0.90, rho_ci=0.05, sharpe_daily=-0.30)
        got = gp.classify_stop_justification(stats)
        assert got == "NEGATIVE_EDGE", (
            f"SR<=0 must gate BEFORE the rho-vs-SR comparison -- rho=0.90 with "
            f"SR=-0.30 trivially satisfies rho-CI>=SR (0.85>=-0.30), so a "
            f"naive implementation would wrongly return SUFFICIENT_MET here. "
            f"Got {got!r}, expected NEGATIVE_EDGE (AC-3's explicit trap)."
        )

    def test_negative_edge_from_golden_fixture(self):
        """Real (constructed, not fixture-hardcoded-verdict) data path: the
        negative_edge fixture's own rho/sharpe, fed through the real
        compute_persistence_stats -> classify_stop_justification pipeline."""
        fixture = json.loads(
            (_FIXTURE_DIR / "persistence_stats_negative_edge.json").read_text(encoding="utf-8")
        )
        stats = gp.compute_persistence_stats(fixture["daily_returns_pct"])
        assert gp.classify_stop_justification(stats) == "NEGATIVE_EDGE"


class TestInsufficientDataPriority:
    def test_insufficient_reason_set_overrides_any_rho_sr_combination(self):
        """INSUFFICIENT_DATA must be checked FIRST -- even a stats object
        whose rho/CI/SR would otherwise read as a confident SUFFICIENT_MET
        must classify INSUFFICIENT_DATA once insufficient_reason is set."""
        stats = _stats(
            rho=0.99,
            rho_ci=0.01,
            sharpe_daily=0.01,
            n_obs=5,
            insufficient_reason="n_obs=5 < N_MIN_OBS=40",
        )
        assert gp.classify_stop_justification(stats) == "INSUFFICIENT_DATA"


# ---------------------------------------------------------------------------
# AC-2: unit comparability enforced structurally + the annualization-flip
# regression guard
# ---------------------------------------------------------------------------


class TestUnitComparabilityStructural:
    def test_classify_accepts_exactly_one_paired_stats_argument(self):
        """AC-2: 'the comparison function takes them as one paired input so a
        caller cannot pass an annualized Sharpe [as a separate raw float]'.
        Pinned structurally via the function's signature -- a two-float-
        argument signature (rho, sharpe) would let a caller pass a
        wrong-frequency sharpe positionally with no coupling to how rho/CI
        were derived."""
        sig = inspect.signature(gp.classify_stop_justification)
        params = list(sig.parameters.values())
        assert len(params) == 1, (
            f"classify_stop_justification must take exactly ONE argument (a "
            f"PersistenceStats), got signature {sig!r} with {len(params)} "
            "parameters -- a multi-float signature reopens the unit-mismatch "
            "hole AC-2 exists to close."
        )


class TestAnnualizationFlipRegression:
    # Named constant (not a magic number) for THIS test's simulation of a
    # caller's mistake -- 252 U.S. trading days/year, the standard
    # annualization factor. Not a production constant (annualized display is
    # explicitly OUT of scope per the plan's Scope Boundaries).
    _TRADING_DAYS_PER_YEAR = 252

    def test_wrongly_annualized_sharpe_flips_the_verdict(self):
        """AC-2's core regression anchor: using the SAME rho/CI but a
        WRONGLY-annualized sharpe_daily (as a caller who forgot the
        same-frequency requirement would produce) must classify DIFFERENTLY
        than the correct daily-frequency stats -- proving the unit mismatch
        is observable and dangerous, not merely theoretical."""
        fixture = json.loads(
            (_FIXTURE_DIR / "persistence_stats_ar1_positive.json").read_text(encoding="utf-8")
        )
        returns = fixture["daily_returns_pct"]
        rho = reference_acf1(returns)
        ci = reference_ci95(len(returns))
        sr_daily = reference_sharpe_daily(returns)
        sr_wrongly_annualized = sr_daily * math.sqrt(self._TRADING_DAYS_PER_YEAR)

        correct_stats = _stats(rho=rho, rho_ci=ci, sharpe_daily=sr_daily, n_obs=len(returns))
        wrong_stats = _stats(
            rho=rho, rho_ci=ci, sharpe_daily=sr_wrongly_annualized, n_obs=len(returns)
        )

        correct_verdict = gp.classify_stop_justification(correct_stats)
        wrong_verdict = gp.classify_stop_justification(wrong_stats)

        assert correct_verdict != wrong_verdict, (
            f"Same rho={rho!r}/CI={ci!r}, only sharpe_daily scaled by "
            f"sqrt(252)={math.sqrt(self._TRADING_DAYS_PER_YEAR)!r} (daily "
            f"sr={sr_daily!r} -> wrongly-annualized sr={sr_wrongly_annualized!r}). "
            f"Expected the verdict to FLIP ({correct_verdict!r} -> different "
            f"class) to demonstrate the annualization mismatch is dangerous, "
            f"but both classified {correct_verdict!r}. If this ever legitimately "
            "stops flipping because the fixture's numbers changed, pick a new "
            "fixture where sr_daily is small enough that sr*sqrt(252) crosses "
            "a verdict boundary."
        )
        # This fixture's real numbers (see fixture provenance): daily sr and
        # rho put it in SUFFICIENT_LIKELY; the wrongly-annualized sr is large
        # enough to push it to NOT_MET. Pinned as an exact-value regression
        # anchor, not just "differs":
        assert correct_verdict == "SUFFICIENT_LIKELY"
        assert wrong_verdict == "NOT_MET"


# ---------------------------------------------------------------------------
# AC-4: verdict copy -- sufficient-not-necessary framing, never overclaims
# ---------------------------------------------------------------------------


class TestVerdictCopy:
    @pytest.mark.parametrize("verdict", sorted(_ALL_VERDICTS))
    def test_every_verdict_has_nonempty_copy(self, verdict):
        copy = gp.verdict_copy(verdict)
        assert isinstance(copy, str) and copy.strip(), (
            f"verdict_copy({verdict!r}) must return a non-empty string."
        )

    @pytest.mark.parametrize("verdict", sorted(_ALL_VERDICTS))
    def test_no_verdict_copy_ever_says_stop_unjustified(self, verdict):
        copy = gp.verdict_copy(verdict).lower()
        assert "stop unjustified" not in copy, (
            f"AC-4: verdict_copy({verdict!r})={copy!r} must never render the "
            "literal phrase 'stop unjustified' -- NOT_MET is an expected-return "
            "statement only; variance/drawdown reduction survives under IID."
        )

    def test_sufficient_met_copy_mentions_sufficient(self):
        copy = gp.verdict_copy("SUFFICIENT_MET").lower()
        assert "sufficient" in copy

    def test_sufficient_likely_copy_mentions_sufficient(self):
        copy = gp.verdict_copy("SUFFICIENT_LIKELY").lower()
        assert "sufficient" in copy

    def test_not_met_copy_scoped_to_expected_return_only(self):
        """AC-4: 'NOT_MET is an expected-return statement only -- variance/
        drawdown reduction survives under IID (the CRRA objective legitimately
        values it)'."""
        copy = gp.verdict_copy("NOT_MET").lower()
        assert "expected" in copy and "return" in copy, (
            f"NOT_MET copy={copy!r} must scope the claim to expected return "
            "specifically, not a blanket 'the stop has no value' statement."
        )

    def test_some_verdict_copy_states_sufficient_not_necessary(self):
        """AC-4: 'Verdict copy states the condition is sufficient, not
        necessary'. Checked across the combined copy text rather than pinned
        to one specific verdict, since the plan does not specify which."""
        combined = " ".join(gp.verdict_copy(v).lower() for v in _ALL_VERDICTS)
        assert "not necessary" in combined, (
            "AC-4 requires at least one verdict's copy to state the sufficient-"
            "not-necessary framing explicitly ('not necessary' substring not "
            f"found anywhere across all 5 verdict_copy() outputs: {combined!r})"
        )

    def test_not_met_copy_is_evidence_scoped_not_a_demonstrated_claim(self):
        """PM RULING A (2026-07-23, on TestNotMetAmbiguousRegion's terminal-
        else region): rho < SR but rho+CI >= SR CANNOT statistically rule the
        sufficient condition in OR out. The copy must state this as an
        EVIDENCE finding -- 'not demonstrated' at the 95% CI level -- never
        as a settled fact about THIS symphony. The K&L expected-return-drag
        conclusion must stay framed as the CONDITIONAL THEOREM ('under
        random-walk dynamics, a stop overlay reduces expected return'), never
        asserted as a demonstrated claim about this symphony's own data.
        Declarative "is not met" framing (the pre-ruling copy) overclaims in
        the negative direction, violating AC-4's spirit just as surely as an
        overclaim in the positive direction would."""
        copy = gp.verdict_copy("NOT_MET").lower()
        assert "not demonstrated" in copy, (
            f"NOT_MET copy={copy!r} must state the sufficient condition is "
            "'not demonstrated' at the 95% CI level (evidence-scoped, per PM "
            "ruling 2026-07-23) rather than asserting it as a settled fact."
        )
        assert "random-walk" in copy or "random walk" in copy, (
            f"NOT_MET copy={copy!r} must frame the K&L expected-return-drag "
            "conclusion as the CONDITIONAL theorem ('under random-walk "
            "dynamics...'), never as a demonstrated claim about this "
            "specific symphony -- PM ruling 2026-07-23."
        )


# ---------------------------------------------------------------------------
# AC-9: citation -- correct DOI, dead URL never referenced
# ---------------------------------------------------------------------------


class TestCitation:
    def test_source_contains_correct_doi(self):
        src = pathlib.Path(gp.__file__).read_text(encoding="utf-8")
        assert "10.1016/j.finmar.2013.07.001" in src, (
            "guard_preconditions.py must cite the Kaminski & Lo DOI "
            "10.1016/j.finmar.2013.07.001 (AC-9)."
        )

    def test_source_never_references_dead_alo_mit_edu_url(self):
        src = pathlib.Path(gp.__file__).read_text(encoding="utf-8")
        assert "alo.mit.edu" not in src, (
            "guard_preconditions.py must NOT cite the dead alo.mit.edu URL "
            "(AC-9 explicitly forbids it)."
        )

    def test_source_contains_correct_paper_title(self):
        """PM correction (2026-07-23): the paper at DOI 10.1016/j.finmar.2013.07.001
        is Kaminski & Lo, "When Do Stop-Loss Rules Stop Losses?", Journal of
        Financial Markets 18 (2014), pp. 234-254 -- NOT "An Analysis of
        Trailing Stop-Loss Strategies" / pp. 108-125 (a different, incorrect
        title that was in an earlier draft of this stub)."""
        src = pathlib.Path(gp.__file__).read_text(encoding="utf-8")
        assert "When Do Stop-Loss Rules Stop Losses?" in src, (
            'guard_preconditions.py must cite the correct paper title "When Do '
            'Stop-Loss Rules Stop Losses?" (AC-9, PM correction 2026-07-23).'
        )

    def test_source_never_references_incorrect_earlier_title(self):
        src = pathlib.Path(gp.__file__).read_text(encoding="utf-8")
        assert "An Analysis of Trailing Stop-Loss Strategies" not in src, (
            "guard_preconditions.py must NOT cite the incorrect earlier-draft "
            'title "An Analysis of Trailing Stop-Loss Strategies" -- regression '
            "guard against reverting the PM's 2026-07-23 citation correction."
        )
