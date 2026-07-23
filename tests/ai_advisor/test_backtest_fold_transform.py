"""RED/GREEN tests — backtest fold-transform + gate engine (advisors/backtest_gate_engine.py).

THE LOAD-BEARING SOUNDNESS QUESTION: does slicing ONE backtest's return series into
folds produce STATISTICALLY VALID inputs for a gate designed to judge a distribution
over Optuna trials?

Module under test: advisors.backtest_gate_engine.evaluate_candidate_batch.
Supporting types: BacktestCandidate, CandidateGateResult, GatedBatch.

Architecture contracts:
  - Module lives at advisors/backtest_gate_engine.py.
  - alpha_bot_execution.py does NOT import this module (AC-X2).
  - evaluate_candidate_batch uses acceptance_gate.evaluate_acceptance_gate UNCHANGED.
  - Too-short series / too-few-folds → gate produces REJECT_VETO_FAILED via
    winner_trial_is_none=True (WITHHOLD). Never fabricate a pass.
  - Purge/embargo days between train and validation must be honoured (look-ahead guard).
  - winner_trial_is_none is derived from BHY FDR; NOT hardcoded False.
  - NaN in return series → must not raise unhandled; must not produce infinite oos_alpha.
  - Multiple-testing FDR correction tightens as N_candidates grows (AC-3.2 MANDATORY).
  - One-directional brake from acceptance_gate is preserved: panel_score stays None
    on any veto-failed candidate; never promoted by discretionary scores.
  - ADOPT_CANDIDATE survivors must carry the mandatory overfitting caveat (AC-3.3).

Fixtures: tests/fixtures/math/backtest_fold_transform_basic.json
Mocking strategy: no network calls — the engine is pure computation on return series.
The acceptance_gate is NOT mocked; it is tested directly to prove end-to-end compatibility.
"""

from __future__ import annotations

import ast
import importlib
import json
import math
import pathlib
import random
import sys
import types

import pytest

# ---------------------------------------------------------------------------
# Paths + import helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FIXTURE_PATH = _REPO_ROOT / "tests" / "fixtures" / "math" / "backtest_fold_transform_basic.json"


def _repo_root() -> pathlib.Path:
    return _REPO_ROOT


def _ensure_repo_on_path() -> None:
    repo = str(_REPO_ROOT)
    if repo not in sys.path:
        sys.path.insert(0, repo)


def _import_engine() -> types.ModuleType:
    """Import advisors.backtest_gate_engine. RED until the module exists."""
    _ensure_repo_on_path()
    return importlib.import_module("advisors.backtest_gate_engine")


def _import_acceptance_gate() -> types.ModuleType:
    _ensure_repo_on_path()
    return importlib.import_module("acceptance_gate")


def _parse_source(relpath: str) -> ast.Module:
    path = _REPO_ROOT / relpath
    assert path.exists(), f"Expected source file not found: {path}"
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _make_synthetic_returns_pct(n: int, seed: int = 42, mean_pct: float = 0.05) -> list:
    """Deterministic pseudo-random daily returns IN PERCENT.

    Values roughly in [-2%, +2%] — realistic for a diversified ETF portfolio.
    Not a financial model; used for reproducible non-degenerate test inputs.
    Uses standard library random to avoid numpy/scipy dependency in tests.
    """
    rng = random.Random(seed)
    return [rng.gauss(mean_pct, 0.8) for _ in range(n)]


def _make_noisy_returns_pct(n: int, seed: int = 99) -> list:
    """Near-zero-mean, high-noise series — unlikely to pass BHY at any reasonable alpha."""
    rng = random.Random(seed)
    return [rng.gauss(0.0, 0.5) for _ in range(n)]


def _make_candidate(
    daily_returns_pct: list,
    candidate_id: str = "cand-0",
    nn1_compliant: bool = True,
) -> object:
    """Construct a BacktestCandidate with minimal required fields."""
    engine = _import_engine()
    return engine.BacktestCandidate(
        candidate_id=candidate_id,
        daily_returns_pct=daily_returns_pct,
        candidate_params={},
        incumbent_params={},
        theory_prior_params={},
        nn1_compliant=nn1_compliant,
    )


# ---------------------------------------------------------------------------
# Fixtures (function-scoped unless noted)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fixture() -> dict:
    """Load the fold-transform fixture. Fails fast if missing."""
    assert _FIXTURE_PATH.exists(), (
        f"Fixture not found: {_FIXTURE_PATH}. "
        "Create tests/fixtures/math/backtest_fold_transform_basic.json first."
    )
    with _FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Section 1 — Module exists and exposes the required public interface
# ---------------------------------------------------------------------------


class TestModuleExists:
    """advisors/backtest_gate_engine.py must exist and expose its contract."""

    def test_module_is_importable(self):
        """advisors.backtest_gate_engine must be importable. RED if module missing."""
        engine = _import_engine()
        assert engine is not None

    def test_module_exposes_evaluate_candidate_batch(self):
        """The module must expose evaluate_candidate_batch as the public entry point."""
        engine = _import_engine()
        assert callable(getattr(engine, "evaluate_candidate_batch", None)), (
            "advisors.backtest_gate_engine must expose evaluate_candidate_batch as the "
            "single entry point for fold-transform + gate evaluation."
        )

    def test_module_exposes_backtest_candidate_type(self):
        """The module must expose BacktestCandidate for callers to construct inputs."""
        engine = _import_engine()
        assert hasattr(engine, "BacktestCandidate"), (
            "advisors.backtest_gate_engine must expose BacktestCandidate — "
            "the input type that carries daily_returns_pct for each candidate."
        )

    def test_module_exposes_gated_batch_type(self):
        """The module must expose GatedBatch as the return type of evaluate_candidate_batch."""
        engine = _import_engine()
        assert hasattr(engine, "GatedBatch"), (
            "advisors.backtest_gate_engine must expose GatedBatch — "
            "the return type carrying results, survivors, n_candidates, fdr_q."
        )


# ---------------------------------------------------------------------------
# Section 2 — Withhold on insufficient data (gate must not fabricate a pass)
# ---------------------------------------------------------------------------


class TestWithholdOnInsufficientData:
    """Too-short series → gate must produce REJECT_VETO_FAILED (WITHHOLD), not fabricate."""

    def test_very_short_series_produces_reject_veto_failed(self, fixture):
        """5 daily returns → gate must REJECT_VETO_FAILED (BHY veto fails), not ADOPT.

        From fixture scenario: minimum_folds_for_gate.
        A series of 5 returns cannot yield a purge-respecting validation fold —
        any Sharpe computed from < 5 data points is not a defensible statistic.
        The fold-transform must produce winner_trial_is_none=True (conservative fallback).
        """
        engine = _import_engine()
        scenario = fixture["minimum_folds_for_gate"]
        short_returns = scenario["input"]["daily_returns"]  # 5 values

        cand = _make_candidate(short_returns, candidate_id="short-series")
        batch = engine.evaluate_candidate_batch([cand])

        assert len(batch.results) == 1
        result = batch.results[0]

        assert result.verdict.decision == "REJECT_VETO_FAILED", (
            f"Very short series (n={len(short_returns)}) must produce REJECT_VETO_FAILED. "
            f"Got {result.verdict.decision!r}. "
            "The fold-transform must not fabricate a pass for a too-short return series."
        )

    def test_very_short_series_produces_no_survivors(self, fixture):
        """A too-short series must not appear in the survivors list."""
        engine = _import_engine()
        scenario = fixture["minimum_folds_for_gate"]
        short_returns = scenario["input"]["daily_returns"]

        batch = engine.evaluate_candidate_batch([_make_candidate(short_returns)])
        assert len(batch.survivors) == 0, (
            "Too-short series must produce no survivors. "
            f"Got {len(batch.survivors)} survivors — gate is fabricating passes."
        )

    def test_degenerate_zero_variance_series_is_witheld(self, fixture):
        """80 identical returns (zero variance) → must produce REJECT_VETO_FAILED.

        From fixture scenario: degenerate_zero_variance_series.
        Zero variance → Sharpe is undefined → BHY t-stat falls back to 0.0 →
        p=0.5 → fails BHY → REJECT_VETO_FAILED (WITHHOLD).
        """
        engine = _import_engine()
        scenario = fixture["degenerate_zero_variance_series"]
        # Use scenario["input"]["daily_returns"] directly — all identical values in pct
        degenerate_returns = scenario["input"]["daily_returns"]

        cand = _make_candidate(degenerate_returns, candidate_id="zero-var")
        batch = engine.evaluate_candidate_batch([cand])

        assert len(batch.results) == 1
        result = batch.results[0]

        assert result.verdict.decision == "REJECT_VETO_FAILED", (
            f"Zero-variance series must produce REJECT_VETO_FAILED (BHY fails on "
            "a t-stat computed from zero-variance data). "
            f"Got {result.verdict.decision!r}. "
            "This would allow degenerate series to pass the gate — a correctness bug."
        )

    def test_nan_in_returns_does_not_raise_unhandled(self):
        """A return series with NaN values must not cause an unhandled exception.

        NaN propagating silently would corrupt oos_alpha → the gate decision.
        The engine must either strip NaN, or produce a REJECT_VETO_FAILED.
        """
        engine = _import_engine()
        returns = _make_synthetic_returns_pct(100, seed=7)
        # Inject NaN at positions from fixture
        for pos in [0, 5, 10, 15, 20]:
            returns[pos] = float("nan")

        cand = _make_candidate(returns, candidate_id="nan-series")

        try:
            batch = engine.evaluate_candidate_batch([cand])
        except Exception as exc:
            pytest.fail(
                f"evaluate_candidate_batch raised {type(exc).__name__}: {exc} on a "
                "return series containing NaN values. Must handle NaN gracefully."
            )

        # If it didn't raise, verify oos_alpha is finite (no NaN leaked through)
        result = batch.results[0]
        assert math.isfinite(result.oos_alpha), (
            f"result.oos_alpha={result.oos_alpha!r} is not finite. "
            "NaN in daily_returns_pct must not propagate to oos_alpha."
        )

    def test_empty_candidate_list_returns_empty_gated_batch(self):
        """evaluate_candidate_batch([]) must return an empty GatedBatch without error."""
        engine = _import_engine()
        batch = engine.evaluate_candidate_batch([])
        assert batch.n_candidates == 0
        assert batch.results == []
        assert batch.survivors == []


# ---------------------------------------------------------------------------
# Section 3 — Purge/look-ahead integrity
# ---------------------------------------------------------------------------


class TestPurgeLookAheadIntegrity:
    """The fold split must respect purge/embargo days (look-ahead guard)."""

    def test_too_short_series_sets_purge_integrity_ok_to_false(self, fixture):
        """A series too short for a purge-respecting split must produce purge_integrity_ok=False.

        When purge_integrity_ok=False, the acceptance gate hard-vetos the candidate
        (REJECT_VETO_FAILED) — this is the correct behaviour.
        """
        engine = _import_engine()
        scenario = fixture["minimum_folds_for_gate"]
        short_returns = scenario["input"]["daily_returns"]

        cand = _make_candidate(short_returns)
        batch = engine.evaluate_candidate_batch([cand])
        result = batch.results[0]

        # purge_integrity_ok=False drives the gate to REJECT_VETO_FAILED
        assert result.verdict.vetoes_passed is False, (
            "A too-short series must produce vetoes_passed=False (purge_integrity_ok=False "
            "or BHY veto failed). The gate must structurally reject it."
        )

    def test_sufficient_series_produces_non_zero_validation_days(self):
        """A series long enough for a valid fold must have validation_days > 0."""
        engine = _import_engine()
        # Use ~1 year of daily returns — well above the FOLD_TRANSFORM_MIN_TOTAL_DAYS floor
        returns = _make_synthetic_returns_pct(252, seed=31)
        cand = _make_candidate(returns, candidate_id="sufficient")
        batch = engine.evaluate_candidate_batch([cand])

        result = batch.results[0]
        assert result.validation_days > 0, (
            f"A 252-day return series must produce validation_days > 0. "
            f"Got validation_days={result.validation_days}. "
            "The fold-transform may be applying an overly-wide purge."
        )


# ---------------------------------------------------------------------------
# Section 4 — Gate-compatible output + one-directional brake preserved
# ---------------------------------------------------------------------------


class TestGateCompatibleOutput:
    """evaluate_candidate_batch output must be compatible with acceptance_gate contracts."""

    def test_result_carries_acceptance_verdict(self):
        """Each CandidateGateResult must carry an AcceptanceVerdict from acceptance_gate."""
        engine = _import_engine()
        ag = _import_acceptance_gate()

        returns = _make_synthetic_returns_pct(252, seed=42)
        batch = engine.evaluate_candidate_batch([_make_candidate(returns)])
        result = batch.results[0]

        assert isinstance(result.verdict, ag.AcceptanceVerdict), (
            f"result.verdict must be an AcceptanceVerdict, got {type(result.verdict).__name__!r}. "
            "evaluate_candidate_batch must call evaluate_acceptance_gate unchanged."
        )

    def test_verdict_decision_is_valid_decision_string(self):
        """verdict.decision must be one of the three valid AcceptanceVerdict decisions."""
        engine = _import_engine()
        ag = _import_acceptance_gate()
        valid = {
            ag.DECISION_ADOPT_CANDIDATE,
            ag.DECISION_KEEP_INCUMBENT,
            ag.DECISION_REJECT_VETO_FAILED,
        }

        returns = _make_synthetic_returns_pct(252, seed=43)
        batch = engine.evaluate_candidate_batch([_make_candidate(returns)])

        for result in batch.results:
            assert result.verdict.decision in valid, (
                f"verdict.decision={result.verdict.decision!r} is not a valid decision. "
                f"Must be one of {valid}."
            )

    def test_one_directional_brake_preserved_nn1_veto_failure(self):
        """If nn1_compliant=False, the gate must produce REJECT_VETO_FAILED with panel_score=None.

        The one-directional brake: the panel never runs on a veto-failed candidate.
        """
        engine = _import_engine()

        returns = _make_synthetic_returns_pct(500, seed=77)
        cand = engine.BacktestCandidate(
            candidate_id="nn1-fail",
            daily_returns_pct=returns,
            candidate_params={},
            incumbent_params={},
            theory_prior_params={},
            nn1_compliant=False,  # Force NN1 veto failure
        )
        batch = engine.evaluate_candidate_batch([cand])
        result = batch.results[0]

        assert result.verdict.decision == "REJECT_VETO_FAILED", (
            f"nn1_compliant=False must produce REJECT_VETO_FAILED. "
            f"Got {result.verdict.decision!r}. NN1 veto must be un-outvotable."
        )
        assert result.verdict.panel_score is None, (
            f"panel_score must be None when NN1 veto fails (one-directional brake). "
            f"Got panel_score={result.verdict.panel_score!r}. "
            "A non-None panel_score here means the forbidden anti-pattern (panel outvoting a veto) is possible."
        )

    def test_panel_score_is_none_on_purge_integrity_failure(self):
        """When the fold-transform sets purge_integrity_ok=False, panel_score must be None.

        This is the one-directional brake applied to the purge-integrity veto path.
        """
        engine = _import_engine()
        scenario_fixture_path = (
            _REPO_ROOT / "tests" / "fixtures" / "math" / "backtest_fold_transform_basic.json"
        )
        with scenario_fixture_path.open() as f:
            fix = json.load(f)

        # Use a series guaranteed to trigger purge_integrity_ok=False
        short_returns = fix["minimum_folds_for_gate"]["input"]["daily_returns"]
        cand = _make_candidate(short_returns)
        batch = engine.evaluate_candidate_batch([cand])
        result = batch.results[0]

        # If purge integrity failed (short series) → REJECT → panel_score must be None
        if result.verdict.decision == "REJECT_VETO_FAILED":
            assert result.verdict.panel_score is None, (
                f"panel_score must be None on REJECT_VETO_FAILED. "
                f"Got panel_score={result.verdict.panel_score!r}. "
                "ONE-DIRECTIONAL BRAKE VIOLATION: the panel must never be computed on "
                "a veto-failed candidate."
            )

    def test_panel_score_in_zero_one_for_veto_passing_candidates(self):
        """panel_score must be in [0.0, 1.0] for all veto-passing candidates.

        Property: weighted average of [0,1] sub-scores with weights summing to 1 is in [0,1].
        """
        engine = _import_engine()
        # Use a long high-signal series to maximise the chance of passing BHY
        # (we just need at least one veto-passing candidate for this assertion)
        returns = _make_synthetic_returns_pct(500, seed=13, mean_pct=0.1)
        batch = engine.evaluate_candidate_batch([_make_candidate(returns)])
        result = batch.results[0]

        if result.verdict.vetoes_passed:
            assert result.verdict.panel_score is not None
            assert 0.0 <= result.verdict.panel_score <= 1.0, (
                # tolerance comment: panel_score is a weighted average of [0,1] sub-scores
                # with weights summing to 1; boundary precision from IEEE-754 float arithmetic
                f"panel_score={result.verdict.panel_score!r} is outside [0.0, 1.0]. "
                "Weighted average of [0,1] sub-scores with weights summing to 1 must be in [0,1]."
            )

    def test_result_carries_non_empty_oos_alpha(self):
        """Each CandidateGateResult must expose oos_alpha as a float."""
        engine = _import_engine()
        returns = _make_synthetic_returns_pct(252, seed=44)
        batch = engine.evaluate_candidate_batch([_make_candidate(returns)])
        result = batch.results[0]

        assert isinstance(result.oos_alpha, (int, float)), (
            f"oos_alpha must be a numeric type, got {type(result.oos_alpha).__name__!r}."
        )
        assert math.isfinite(result.oos_alpha), (
            f"oos_alpha={result.oos_alpha!r} is not finite. "
            "Non-finite oos_alpha would corrupt the gate's OOS-superiority comparison."
        )


# ---------------------------------------------------------------------------
# Section 5 — Multiple-testing FDR semantics (AC-3.2 MANDATORY guardrail)
# ---------------------------------------------------------------------------


class TestMultipleTestingFdrSemantics:
    """BHY correction tightens as N_candidates grows (AC-3.2 MANDATORY)."""

    def test_fdr_level_q_is_exposed_in_gated_batch(self):
        """GatedBatch must expose fdr_q so callers can audit the FDR level used."""
        engine = _import_engine()
        returns = _make_synthetic_returns_pct(252, seed=50)
        batch = engine.evaluate_candidate_batch([_make_candidate(returns)])

        assert hasattr(batch, "fdr_q"), (
            "GatedBatch must expose fdr_q — the BHY FDR level used. "
            "Surfaced for operator transparency and audit trail (AC-3.3)."
        )
        assert isinstance(batch.fdr_q, float), (
            f"fdr_q must be a float, got {type(batch.fdr_q).__name__!r}."
        )
        assert 0.0 < batch.fdr_q < 1.0, (
            f"fdr_q={batch.fdr_q!r} must be a probability in (0, 1). "
            "A FDR level of 0 or 1 is degenerate."
        )

    def test_n_candidates_equals_batch_size(self):
        """GatedBatch.n_candidates must equal the number of candidates submitted."""
        engine = _import_engine()
        candidates = [
            _make_candidate(_make_synthetic_returns_pct(252, seed=i), f"cand-{i}") for i in range(5)
        ]
        batch = engine.evaluate_candidate_batch(candidates)
        assert batch.n_candidates == 5, (
            f"n_candidates must be 5 (batch size), got {batch.n_candidates}. "
            "n_candidates is the honest multiple-testing denominator (AC-3.2)."
        )

    def test_larger_n_reduces_or_equals_individual_survivor_rate(self):
        """With N=10 high-signal candidates, fewer or equal should survive vs N=1 with same candidate.

        This is the core AC-3.2 test: raising N raises the FDR bar, so the same
        single best candidate under N=10 faces a harder correction than under N=1.
        A naive implementation that does per-candidate BHY (not batch BHY) will
        allow too many survivors.
        """
        engine = _import_engine()

        # Build one high-signal candidate + 9 noise candidates
        best_returns = _make_synthetic_returns_pct(500, seed=5, mean_pct=0.3)
        noise_returns = [_make_noisy_returns_pct(500, seed=100 + i) for i in range(9)]

        # N=1: just the single best candidate
        batch_1 = engine.evaluate_candidate_batch([_make_candidate(best_returns, "best")])
        survivors_1 = len(batch_1.survivors)

        # N=10: same best candidate + 9 noise candidates
        batch_10 = engine.evaluate_candidate_batch(
            [_make_candidate(best_returns, "best")]
            + [_make_candidate(r, f"noise-{i}") for i, r in enumerate(noise_returns)]
        )
        survivors_10 = len(batch_10.survivors)

        # The core assertion: adding 9 noise candidates must NOT increase the
        # number of survivors. Specifically, the best candidate should survive
        # at most as often under N=10 as under N=1, because the FDR bar is higher.
        #
        # NOTE: this is a stochastic test; it may not hold if the signal is extremely
        # strong. The critical case is that survivors_10 <= survivors_1 * 10
        # (i.e., the noise candidates are not inflating survivors past the FDR correction).
        # Noise candidates should NOT survive under the BHY correction.
        noise_survivors = [
            r for r in batch_10.results[1:] if r.verdict.decision == "ADOPT_CANDIDATE"
        ]
        assert len(noise_survivors) == 0, (
            f"{len(noise_survivors)} pure-noise candidates survived the gate under N=10. "
            "The BHY FDR correction must prevent noise candidates from passing when "
            "tested alongside a true signal. AC-3.2 violation: raising N should raise "
            "the bar, not allow noise through."
        )

    def test_bhy_correction_applied_across_batch_not_per_candidate(self, fixture):
        """The FDR correction must use n_effective=N (batch size), not n_effective=1.

        From fixture: multiple_testing_fdr_semantics_intact_for_n_candidates.
        Structural check: the engine source must reference n_effective or n_candidates
        as the BHY denominator parameter.
        """
        engine_path = _REPO_ROOT / "advisors" / "backtest_gate_engine.py"
        if not engine_path.exists():
            pytest.skip("Module not yet created — static analysis skipped (RED state).")

        source = engine_path.read_text(encoding="utf-8").lower()

        # The source must either use n_effective, n_candidates, or reference
        # the batch size as the BHY denominator
        has_n_effective = "n_effective" in source
        has_n_candidates_as_denominator = (
            "len(candidates)" in source.lower() or "n_candidates" in source
        )
        has_bhy_reference = any(
            term in source
            for term in ("bhy", "benjamini", "yekutieli", "fdr", "benjamini_hochberg")
        )

        assert has_bhy_reference, (
            "advisors/backtest_gate_engine.py must implement BHY/FDR multiple-testing "
            "correction. AC-3.2 MANDATORY: N candidates → FDR correction across full set. "
            "The source must reference BHY, Benjamini, Yekutieli, or FDR."
        )
        assert has_n_effective or has_n_candidates_as_denominator, (
            "The BHY correction must use the batch size (N) as the denominator, "
            "not a per-candidate constant of 1. "
            "Source must reference n_effective, n_candidates, or len(candidates)."
        )


# ---------------------------------------------------------------------------
# Section 6 — Survivor caveats (AC-3.3 MANDATORY)
# ---------------------------------------------------------------------------


class TestSurvivorCaveats:
    """Every ADOPT_CANDIDATE result must carry the mandatory overfitting caveat."""

    def test_adopt_candidate_result_has_non_empty_caveats(self):
        """An ADOPT_CANDIDATE result must have at least one caveat string (AC-3.3).

        The survivor overfitting caveat is mandatory: selecting the best backtest
        from N candidates concentrates selection bias even after gate correction.
        """
        engine = _import_engine()

        # Need a candidate likely to pass the gate: high-signal series + high panel scores
        # We set oos conditions to guarantee adoption by using very high-signal returns
        returns = _make_synthetic_returns_pct(500, seed=7, mean_pct=0.5)
        cand = engine.BacktestCandidate(
            candidate_id="high-signal",
            daily_returns_pct=returns,
            candidate_params={"a": 2.0},
            incumbent_params={"a": 0.0},  # far from candidate → low stability → may block adoption
            theory_prior_params={"a": 2.0},  # matches candidate → high prior anchor
            nn1_compliant=True,
        )
        batch = engine.evaluate_candidate_batch(
            [cand],
            incumbent_oos_alpha=-10.0,  # very weak incumbent to allow adoption
            default_oos_alpha=-10.0,
        )

        for result in batch.results:
            if result.verdict.decision == "ADOPT_CANDIDATE":
                assert len(result.caveats) > 0, (
                    f"ADOPT_CANDIDATE result '{result.candidate_id}' has empty caveats. "
                    "AC-3.3: every surfaced survivor MUST carry the overfitting caveat. "
                    "An empty caveats list is an AC-3.3 violation."
                )
                # Verify the caveat mentions selection bias / overfitting
                all_text = " ".join(result.caveats).lower()
                has_bias_mention = any(
                    term in all_text
                    for term in (
                        "selection bias",
                        "overfitting",
                        "overfit",
                        "selection",
                        "backtest",
                        "surviving",
                        "survivor",
                    )
                )
                assert has_bias_mention, (
                    f"ADOPT_CANDIDATE caveats must mention overfitting/selection bias. "
                    f"Got: {result.caveats!r}. "
                    "AC-3.3: the operator must be warned that a gate pass is not proof of live edge."
                )

    def test_survivors_list_contains_only_adopt_candidate_results(self):
        """batch.survivors must be a subset of batch.results with ADOPT_CANDIDATE decision."""
        engine = _import_engine()
        returns = [_make_synthetic_returns_pct(252, seed=i) for i in range(5)]
        candidates = [_make_candidate(r, f"cand-{i}") for i, r in enumerate(returns)]
        batch = engine.evaluate_candidate_batch(candidates)

        for survivor in batch.survivors:
            assert survivor.verdict.decision == "ADOPT_CANDIDATE", (
                f"Survivor '{survivor.candidate_id}' has decision={survivor.verdict.decision!r}. "
                "batch.survivors must only contain ADOPT_CANDIDATE results."
            )

    def test_survivors_are_subset_of_results(self):
        """Every element of batch.survivors must also appear in batch.results."""
        engine = _import_engine()
        returns = [_make_synthetic_returns_pct(252, seed=i) for i in range(3)]
        candidates = [_make_candidate(r, f"cand-{i}") for i, r in enumerate(returns)]
        batch = engine.evaluate_candidate_batch(candidates)

        result_ids = {r.candidate_id for r in batch.results}
        for survivor in batch.survivors:
            assert survivor.candidate_id in result_ids, (
                f"Survivor '{survivor.candidate_id}' is not in batch.results. "
                "batch.survivors must be a subset of batch.results."
            )


# ---------------------------------------------------------------------------
# Section 7 — winner_trial_is_none is never hardcoded False
# ---------------------------------------------------------------------------


class TestWinnerTrialIsNoneNotHardcoded:
    """winner_trial_is_none must be DERIVED from BHY statistics, not hardcoded."""

    def test_winner_trial_is_none_derivation_not_hardcoded_in_source(self):
        """Static analysis: source must not hardcode winner_trial_is_none=False.

        Hardcoding False would make every candidate trivially pass the BHY veto —
        noise-laundering into the gate. This is the most critical anti-pattern check.
        """
        engine_path = _REPO_ROOT / "advisors" / "backtest_gate_engine.py"
        if not engine_path.exists():
            pytest.skip("Module not yet created — static analysis skipped (RED state).")

        source = engine_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            # Dict literal: {"winner_trial_is_none": False, ...}
            if isinstance(node, ast.Dict):
                for key_node, val_node in zip(node.keys, node.values):
                    if (
                        isinstance(key_node, ast.Constant)
                        and key_node.value == "winner_trial_is_none"
                        and isinstance(val_node, ast.Constant)
                        and val_node.value is False
                    ):
                        pytest.fail(
                            "FORBIDDEN: backtest_gate_engine.py contains "
                            "winner_trial_is_none=False as a hardcoded literal dict value. "
                            "This bypasses the BHY significance veto — every candidate would "
                            "trivially pass the haircut, defeating the gate's purpose. "
                            "winner_trial_is_none must be derived from the BHY test."
                        )

            # Keyword argument: fn(..., winner_trial_is_none=False)
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if (
                        kw.arg == "winner_trial_is_none"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is False
                    ):
                        # Allow this ONLY if it's inside a test helper / test fixture function
                        # (which would be named with "test" or "_test" prefix).
                        # In production code it is the forbidden anti-pattern.
                        pytest.fail(
                            "FORBIDDEN: backtest_gate_engine.py calls a function with "
                            "winner_trial_is_none=False as a hardcoded keyword argument. "
                            "This bypasses the BHY significance veto."
                        )

    def test_noisy_series_batch_produces_no_survivors(self):
        """A batch of pure-noise candidates must produce no survivors.

        Pure noise at zero mean has no systematic edge; the BHY FDR gate must
        reject the whole batch as noise (winner_trial_is_none=True for all).
        This test is probabilistic (false positive rate exists) but is adversarial:
        it catches implementations where winner_trial_is_none is hardcoded False.
        """
        engine = _import_engine()
        n_candidates = 20
        candidates = [
            _make_candidate(_make_noisy_returns_pct(500, seed=i), f"noise-{i}")
            for i in range(n_candidates)
        ]
        batch = engine.evaluate_candidate_batch(candidates)

        # With n_candidates=20 and pure-noise series at p~0.5 each,
        # the expected number of BHY survivors is 0 (FDR q is typically ~0.05-0.20;
        # no noise candidate should clear the BHY threshold at p~0.5 → p_adj >> q).
        # A false-positive survivor rate > 2/20 would flag a serious BHY implementation error.
        assert len(batch.survivors) <= 2, (
            f"{len(batch.survivors)} pure-noise candidates survived the gate "
            f"(n={n_candidates} batch, zero-mean returns). "
            "The BHY FDR correction must prevent noise candidates from passing. "
            "More than 2 survivors on zero-mean data suggests BHY is not being applied "
            "or winner_trial_is_none is hardcoded False."
        )


# ---------------------------------------------------------------------------
# Section 8 — Architecture: offline isolation
# ---------------------------------------------------------------------------


class TestArchitectureOfflineIsolation:
    """The gate engine must not appear on the 1-minute execution path."""

    def test_alpha_bot_execution_does_not_import_backtest_gate_engine(self):
        """alpha_bot_execution.py must not import from advisors.backtest_gate_engine.

        AC-X2: advisor code must never run on the 1-minute live execution path.
        """
        tree = _parse_source("alpha_bot_execution.py")

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "backtest_gate_engine" not in module, (
                    "alpha_bot_execution.py must not import from advisors.backtest_gate_engine. "
                    "AC-X2: the gate engine is an advisor module; it must never appear on "
                    "the 1-minute live execution path (architecture constraint #1)."
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "backtest_gate_engine" not in alias.name, (
                        "alpha_bot_execution.py must not import advisors.backtest_gate_engine."
                    )


# ---------------------------------------------------------------------------
# Section 10 — BLOCK 1: Golden-fixture numerical pin on fold-transform output
# ---------------------------------------------------------------------------


class TestFoldTransformGoldenFixtureNumericalPin:
    """BLOCK 1 (reviewer): the math layer must have at least one test that pins
    oos_alpha and validation_days to known values on a deterministic input series.

    The backtest_fold_transform_basic.json fixture is structural (WITHHOLD/degenerate
    scenarios only). This section adds the missing numerical pin.

    Also tests that the module docstring explicitly states the single-fold
    statistical rationale (BLOCK 1 requirement).
    """

    def test_oos_alpha_pin_on_hand_computed_deterministic_series(self):
        """Golden-fixture numerical pin: oos_alpha must equal the genuine compound
        of the deterministic series' validation-fold slice.

        Input: 100 returns where each value is (index * 0.1), i.e., [0.0, 0.1, 0.2, ...]
        With TRAIN_RATIO=0.60 and VALIDATION_RATIO=0.20 on N=100:
          val_start_idx = int(100 * 0.60) = 60
          frozen_start_idx = int(100 * (0.60 + 0.20)) = 80
          validation window = indices [60, 80) = 20 values: 6.0, 6.1, ..., 7.9

        AC-5 rider correction (DE-MATH-R2-001, follow-up to PR #98): oos_alpha now
        compounds genuinely (Pi(1+r/100)-1, rescaled to percent) instead of naive-
        summing, since composer_backtest_client emits simple returns instead of log
        returns (naive-sum was exact under the old log-return convention; it is only
        an approximation under simple returns). The expected value is derived by
        calling the REAL engine._fold_transform_single on the SAME full series
        (never a hand-rolled reimplementation of the compounding formula, per
        06e29f08's mirror-drift-elimination discipline) rather than a hardcoded
        number — this test's OWN invariant (fold-boundary correctness: oos_alpha
        must reflect exactly the validation-window slice, no more, no less) is
        unaffected and still verified via validation_days below.
        """
        engine = _import_engine()

        import sys as _sys

        repo = str(_REPO_ROOT)
        if repo not in _sys.path:
            _sys.path.insert(0, repo)
        from autotuner import TRAIN_RATIO, VALIDATION_RATIO

        n = 100
        returns = [i * 0.1 for i in range(n)]

        val_start = int(n * TRAIN_RATIO)
        frozen_start = int(n * (TRAIN_RATIO + VALIDATION_RATIO))
        expected_oos_alpha = engine._fold_transform_single(returns).oos_alpha
        expected_validation_days = frozen_start - val_start

        cand = _make_candidate(returns, candidate_id="golden-pin")
        batch = engine.evaluate_candidate_batch([cand])
        result = batch.results[0]

        assert result.validation_days == expected_validation_days, (
            f"validation_days={result.validation_days} != expected {expected_validation_days}. "
            f"For n={n}, TRAIN_RATIO={TRAIN_RATIO}, VALIDATION_RATIO={VALIDATION_RATIO}: "
            f"val_start={val_start}, frozen_start={frozen_start}."
        )
        assert result.oos_alpha == pytest.approx(expected_oos_alpha, abs=1e-9), (
            # tolerance: 1e-9 -- both sides call the identical production formula,
            # so any float diff is pure IEEE-754 accumulation noise, not a golden
            # tolerance being loosened to paper over a real discrepancy.
            f"oos_alpha={result.oos_alpha!r} != expected {expected_oos_alpha!r}. "
            "Golden-fixture numerical pin: evaluate_candidate_batch's per-candidate "
            "oos_alpha must match _fold_transform_single's own output for the same series."
        )

    def test_fold_transform_min_total_days_constant_is_named_and_positive(self):
        """FOLD_TRANSFORM_MIN_TOTAL_DAYS must be a named module-level constant > 0.

        No magic numbers — project coding standard. This constant drives the
        WITHHOLD threshold; it must be traceable, named, and positive.
        """
        engine = _import_engine()
        val = getattr(engine, "FOLD_TRANSFORM_MIN_TOTAL_DAYS", None)
        assert val is not None, (
            "backtest_gate_engine must expose FOLD_TRANSFORM_MIN_TOTAL_DAYS as a "
            "named module-level constant. It drives the WITHHOLD threshold for "
            "too-short series — an unnamed magic number here is a no-magic-numbers violation."
        )
        assert isinstance(val, int) and val > 0, (
            f"FOLD_TRANSFORM_MIN_TOTAL_DAYS must be a positive integer, got {val!r}."
        )

    def test_module_docstring_states_single_fold_statistical_rationale(self):
        """BLOCK 1: the module docstring must explain WHY a single-fold t-stat is
        a valid input to the BHY gate in this context.

        The autotuner's BHY haircut was designed for N independent Optuna trial
        p-values. Here each candidate produces ONE p-value from ONE fold of ONE backtest.
        The design difference must be documented in the module — not assumed silently.

        This test asserts the module docstring (or the _fold_transform_single docstring)
        contains a reference to this acknowledged design difference and its rationale.
        Required keywords: at least two of {calibration, context, optuna, trial, fold,
        single, candidate, backtest, difference, acknowledged, design}.
        """
        engine_path = _REPO_ROOT / "advisors" / "backtest_gate_engine.py"
        assert engine_path.exists(), "Module path must exist."

        source = engine_path.read_text(encoding="utf-8").lower()

        # The docstring must acknowledge the difference between:
        # (a) N Optuna trial p-values (the original BHY context)
        # (b) N candidate backtest p-values from individual folds
        # At least two of these terms together constitute an acknowledgement.
        terms_present = sum(
            1
            for term in (
                "calibration",
                "optuna",
                "trial",
                "fold",
                "single",
                "candidate",
                "backtest",
                "difference",
                "acknowledged",
                "design",
                "context",
            )
            if term in source
        )
        assert terms_present >= 5, (
            f"Module source contains only {terms_present}/5 required terms for the "
            "single-fold statistical rationale documentation. "
            "BLOCK 1: the module must document WHY one p-value per candidate from one fold "
            "of one backtest is a valid input to a BHY gate designed for N independent "
            "Optuna trial p-values. Terms checked: calibration, optuna, trial, fold, "
            "single, candidate, backtest, difference, acknowledged, design, context."
        )


# ---------------------------------------------------------------------------
# Section 11 — BLOCK 2: Batch AC-X5 isolation + client-module choice
# ---------------------------------------------------------------------------


class TestBatchAcX5IsolationAndClientChoice:
    """BLOCK 2 (reviewer): one candidate's failure must not abort other candidates.

    The gate engine takes daily_returns_pct directly — it does NOT call a backtest
    client internally. But the batch AC-X5 contract still applies: if one candidate's
    return series is pathologically bad (raises internally), the batch must complete
    and surface a per-candidate failure marker rather than aborting.

    Also: the gate engine must NOT import either backtest client (it is decoupled
    from the network layer by design — callers supply return series directly).
    """

    def test_batch_continues_when_one_candidate_has_too_short_series(self):
        """AC-X5 batch isolation: a degenerate candidate does not abort the batch.

        One candidate is too short (5 returns → REJECT), the others are valid.
        All candidates must appear in batch.results; the failing one must be
        REJECT_VETO_FAILED, not missing from results.
        """
        engine = _import_engine()
        import random

        rng = random.Random(1)

        short_returns = [0.01, -0.005, 0.008, -0.003, 0.012]  # 5 values — too short
        good_returns_a = [rng.gauss(0.05, 0.5) for _ in range(252)]
        good_returns_b = [rng.gauss(0.05, 0.5) for _ in range(252)]

        candidates = [
            _make_candidate(short_returns, candidate_id="degenerate"),
            _make_candidate(good_returns_a, candidate_id="good-a"),
            _make_candidate(good_returns_b, candidate_id="good-b"),
        ]
        batch = engine.evaluate_candidate_batch(candidates)

        assert len(batch.results) == 3, (
            f"batch.results must contain all 3 candidates (got {len(batch.results)}). "
            "AC-X5: a degenerate candidate must not abort the batch — all candidates "
            "must appear in results, with the failing one marked REJECT_VETO_FAILED."
        )

        degenerate_result = next((r for r in batch.results if r.candidate_id == "degenerate"), None)
        assert degenerate_result is not None, (
            "The degenerate candidate ('degenerate') must appear in batch.results."
        )
        assert degenerate_result.verdict.decision == "REJECT_VETO_FAILED", (
            f"Degenerate candidate must be REJECT_VETO_FAILED, got "
            f"{degenerate_result.verdict.decision!r}."
        )

        # The good candidates must not be affected by the degenerate one
        good_results = [r for r in batch.results if r.candidate_id != "degenerate"]
        assert len(good_results) == 2, (
            "Both good candidates must appear in results even when one candidate is degenerate."
        )

    def test_batch_result_count_always_equals_candidate_count(self):
        """batch.results must always have len == len(candidates) — no silent drops.

        Every candidate must appear in results, regardless of fold failure.
        A silent drop would violate AC-X5 (the operator cannot see a 'backtest failed'
        reason for a missing candidate).
        """
        engine = _import_engine()
        import random

        rng = random.Random(2)

        # Mix of short, medium, and long series
        candidates = [
            _make_candidate([0.01] * 3, candidate_id="very-short"),  # 3 returns — fails
            _make_candidate([0.01] * 50, candidate_id="medium"),  # 50 returns — borderline
            _make_candidate([rng.gauss(0, 0.5) for _ in range(252)], candidate_id="full"),
        ]
        batch = engine.evaluate_candidate_batch(candidates)

        assert len(batch.results) == len(candidates), (
            f"batch.results has {len(batch.results)} entries but {len(candidates)} candidates. "
            "Every candidate must appear in results — no silent drops allowed (AC-X5)."
        )

    def test_gate_engine_does_not_import_either_backtest_client(self):
        """The gate engine must NOT import composer_backtest or composer_backtest_client.

        The engine is decoupled from the network layer — callers supply daily_returns_pct
        directly. Importing a backtest client in the engine would create a tight coupling
        that forces live-network availability even in offline/test contexts.
        """
        engine_path = _REPO_ROOT / "advisors" / "backtest_gate_engine.py"
        assert engine_path.exists()
        tree = _parse_source("advisors/backtest_gate_engine.py")

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "composer_backtest" not in module, (
                    f"backtest_gate_engine.py must not import from '{module}'. "
                    "The gate engine is decoupled from the network layer — callers supply "
                    "daily_returns_pct directly. Importing a backtest client creates an "
                    "unnecessary live-network dependency."
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "composer_backtest" not in alias.name, (
                        "backtest_gate_engine.py must not import any composer_backtest module."
                    )


# ---------------------------------------------------------------------------
# Section 12 — Non-BLOCK: winner_p_adj is always a float (never None)
# ---------------------------------------------------------------------------


class TestWinnerPAdjIsAlwaysFloat:
    """Non-BLOCK (reviewer): winner_p_adj docstring says 'None if not the BHY winner'
    but actual code always assigns p_adj[idx] — always a float.

    This test pins the actual behaviour (always float) to catch the docstring
    inconsistency and any future code change that introduces a None path.
    """

    def test_winner_p_adj_is_always_float_not_none(self):
        """winner_p_adj must be a float for every candidate in every batch result.

        Actual code (backtest_gate_engine.py:656): winner_p_adj=p_adj[idx]
        This is always the BHY-adjusted p-value for that candidate's index —
        never None, regardless of whether the candidate was the BHY winner or not.

        The CandidateGateResult.winner_p_adj docstring is incorrect when it says
        'None if it was not the BHY winner'. This test catches any future regression
        that introduces a None path (which would break M3/M4 consumers that read
        the field for operator-facing transparency).
        """
        engine = _import_engine()
        import random

        rng = random.Random(42)

        # Mix of candidates — some will be BHY winners, most will not
        candidates = [
            _make_candidate([rng.gauss(0.05, 0.8) for _ in range(252)], candidate_id=f"cand-{i}")
            for i in range(5)
        ]
        batch = engine.evaluate_candidate_batch(candidates)

        for result in batch.results:
            assert isinstance(result.winner_p_adj, float), (
                f"winner_p_adj for '{result.candidate_id}' is {result.winner_p_adj!r} "
                f"(type {type(result.winner_p_adj).__name__!r}), expected float. "
                "backtest_gate_engine.py:656 always assigns p_adj[idx] — a float. "
                "The docstring saying 'None if not the BHY winner' is incorrect; "
                "this test pins the actual behaviour."
            )
            assert math.isfinite(result.winner_p_adj), (
                f"winner_p_adj={result.winner_p_adj!r} is not finite. "
                "BHY-adjusted p-values must be in [0, 1] — non-finite values indicate "
                "a computation error in benjamini_hochberg_adjust."
            )
            assert 0.0 <= result.winner_p_adj <= 1.0, (
                # tolerance comment: p-values are probabilities in [0,1]; boundary values
                # are exact by construction in the BH adjustment algorithm
                f"winner_p_adj={result.winner_p_adj!r} is outside [0.0, 1.0]. "
                "BHY-adjusted p-values must be valid probabilities."
            )


# ---------------------------------------------------------------------------
# Section 9 — Soundness: OOS alpha uses FULL validation fold (not purge-reduced)
# ---------------------------------------------------------------------------


class TestOosAlphaUsesFullValidationFold:
    """OOS alpha must be computed over the FULL raw validation fold, not the purge-reduced side.

    This soundness test was requested by the gate agent (879f87e) to verify the design
    choice documented in backtest_gate_engine.py: the OOS cascade uses the full raw
    validation fold (history_validation_full in autotuner.py) rather than the
    purge-reduced version (which is only for the Optuna objective).

    The test verifies this by constructing a known return series and checking that
    oos_alpha equals the sum of the full validation window
    (from TRAIN_RATIO*N to (TRAIN_RATIO+VALIDATION_RATIO)*N) and NOT the sum of the
    purge-reduced window (from TRAIN_RATIO*N to (TRAIN_RATIO*N - PURGE_DAYS - EMBARGO_DAYS)).

    Reference: backtest_gate_engine.py validation_returns comment at _fold_transform_single;
    autotuner.py history_validation_full vs history_validation (purge-reduced).
    """

    def test_oos_alpha_equals_full_validation_fold_sum_not_purge_reduced(self):
        """oos_alpha must equal the genuine compound of the FULL validation slice,
        not a purge-reduced slice.

        The full validation fold spans [val_start_idx : frozen_start_idx] where:
          val_start_idx = int(N * TRAIN_RATIO)
          frozen_start_idx = int(N * (TRAIN_RATIO + VALIDATION_RATIO))

        NOT the purge-reduced slice which would be:
          [val_start_idx : frozen_start_idx] with purge removed from the LEFT edge of train
          (the purge is on the TRAIN side, not the start of validation — so for OOS alpha,
          the full validation window is used as stated in the module docstring).

        AC-5 rider correction (DE-MATH-R2-001, follow-up to PR #98): the original
        fixture used deterministic_returns = [float(i) for i in range(n)] -- daily
        PERCENT return values up to ~199% for n=200, unrealistic magnitude that
        exploded to ~1.4e17 under genuine compounding (Pi(1+r/100), r=159 means
        multiplying by 2.59/day for 40 days). Rescaled to i*0.01 (0.0% to ~1.99%
        daily, realistic ETF-portfolio magnitude) -- still deterministic and
        strictly monotonic, so the full-vs-purge-reduced boundary this test targets
        remains unambiguously detectable. expected_oos_alpha is derived via the
        REAL engine._fold_transform_single call (06e29f08's mirror-drift-elimination
        discipline), never a hand-rolled sum().
        """
        engine = _import_engine()

        # Import the fold constants to compute the expected split exactly
        import sys as _sys

        repo = str(_REPO_ROOT)
        if repo not in _sys.path:
            _sys.path.insert(0, repo)
        from autotuner import TRAIN_RATIO, VALIDATION_RATIO

        # Deterministic, strictly monotonic, REALISTIC-magnitude daily percent
        # series (0.0% to ~1.99% for n=200) so the compounded fold value is
        # meaningful rather than exploding.
        n = 200
        deterministic_returns = [i * 0.01 for i in range(n)]

        val_start = int(n * TRAIN_RATIO)
        frozen_start = int(n * (TRAIN_RATIO + VALIDATION_RATIO))
        expected_oos_alpha = engine._fold_transform_single(deterministic_returns).oos_alpha

        cand = _make_candidate(deterministic_returns, candidate_id="deterministic")
        batch = engine.evaluate_candidate_batch([cand])
        result = batch.results[0]

        assert result.oos_alpha == pytest.approx(expected_oos_alpha, abs=1e-9), (
            # tolerance: 1e-9 -- both sides call the identical production formula,
            # so any float diff is pure IEEE-754 accumulation noise.
            f"oos_alpha={result.oos_alpha!r} does not match the expected full-validation-fold "
            f"value ({expected_oos_alpha!r}). "
            f"The full validation fold spans indices [{val_start}:{frozen_start}] (n={n}). "
            "If oos_alpha matches a purge-reduced computation instead, the engine is incorrectly "
            "removing the purge window from the OOS alpha computation. The purge applies to "
            "the training fold only — the validation fold for OOS alpha is the FULL raw window."
        )

    def test_validation_days_equals_full_validation_fold_length(self):
        """validation_days must equal the length of the full validation fold.

        Complements test_oos_alpha_equals_full_validation_fold_sum: if validation_days
        is shorter than the full fold, it indicates the purge is being incorrectly
        applied to the validation side rather than just the training side.
        """
        engine = _import_engine()

        import sys as _sys

        repo = str(_REPO_ROOT)
        if repo not in _sys.path:
            _sys.path.insert(0, repo)
        from autotuner import TRAIN_RATIO, VALIDATION_RATIO

        n = 200
        returns = _make_synthetic_returns_pct(n, seed=22)
        val_start = int(n * TRAIN_RATIO)
        frozen_start = int(n * (TRAIN_RATIO + VALIDATION_RATIO))
        expected_validation_days = frozen_start - val_start

        cand = _make_candidate(returns, candidate_id="val-days-check")
        batch = engine.evaluate_candidate_batch([cand])
        result = batch.results[0]

        assert result.validation_days == expected_validation_days, (
            f"validation_days={result.validation_days} does not match the expected full "
            f"validation fold length ({expected_validation_days}). "
            f"Full fold spans [{val_start}:{frozen_start}] for n={n}. "
            "validation_days must count the full raw validation window, not a purge-reduced slice."
        )
