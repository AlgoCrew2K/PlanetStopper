"""
AC-1 (MA-2 CRITICAL) -- CPCV must score genuinely disjoint units.

RULED MECHANISM (team-lead, split-level scoring -- Option A, superseding
this plan's original "consume train_dates in _aggregate_cpcv_paths"
framing): a canonical N=6/k=2/phi=5 CPCV "backtest path" is a REFIT-WORLD
construct -- each of the 5 assembled paths necessarily reconstructs the
FULL eligible window (mathematically forced: phi paths each assembled from
N/k=3 non-overlapping splits whose k=2-group test sets union to ALL N
groups -- confirmed live against the CURRENT canonical implementation:
_aggregate_cpcv_paths(_generate_cpcv_folds(...)) produces 5 IDENTICAL
60/60-date path sets on the existing basic fixture). Path multiplicity only
produces genuinely DIFFERENT scores when a DIFFERENT trained model owns
each date-segment per path -- this codebase has no per-fold refit
whatsoever (guard_alpha for a date is a pure function of (date, trial
params), independent of any fold/path assignment), so canonical path-level
reconstruction is STRUCTURALLY INCOMPATIBLE with non-identical scores, no
matter how _aggregate_cpcv_paths's date-attribution is implemented.

The honest CSCV-native granularity is the C(6,2)=15 SPLIT ensemble itself
-- each split's OWN test_dates (already, by construction, a pairwise-
distinct ~1/3-of-window subset per _generate_cpcv_folds, confirmed below)
is scored directly, with the trial's aggregate statistic now a mean/
dispersion over 15 split-level scores instead of 5 path-level scores.
_aggregate_cpcv_paths + phi-path reconstruction are RETIRED from the
SCORING path (r2-stats's trace decides retain-with-documented-role vs.
delete-if-zero-consumers for any other caller).

This file pins the OBSERVABLE CONTRACT, not a specific internal variable
name inside the objective() closure:
  (1) the 15 split test-sets are pairwise-distinct, each ~1/3 of the
      eligible window, purge-constrained -- ALREADY TRUE of the existing
      _generate_cpcv_folds (confirmatory regression pin, not new RED).
  (2)+(3)+(5) demonstrated via the SAME production primitives
      (_generate_cpcv_folds + _collect_sim_returns +
      math_engine.compute_crra_eu_objective) the objective() closure
      already uses per-path today -- proving the desired per-split
      contract is achievable, and pinning exact per-split isolation
      (a single date's data change affects ONLY the splits whose
      test_dates include that date).
  (4) the historical 5x-identical-full-window degeneracy stays as a
      documented failing-baseline pin, conditional on _aggregate_cpcv_paths
      surviving retirement (skipped gracefully if it is deleted).
  A source-level pin forces objective() itself to move off
  _aggregate_cpcv_paths -- this is the genuinely RED structural gap today.

Fixture: a real 90-day date range (matching the existing CPCV basic
fixture's N=6/k=2/purge=2/embargo=1 parameters, 6 groups of 15 days each)
with REAL tick sequences -- one group ("hot", group index 3) carries a
genuine qualifying trailing-stop trigger sequence (reused from
tests/autotuner/test_ac4_r2_residual_tripwire.py's _three_confirming_tick_sequence
scenario) on every day; all other days are flat (never arm, never fire).
This makes _collect_sim_returns produce REAL, non-empty triggered returns
for exactly the 5 splits whose test_dates include the hot group, and empty
results for the other 10 -- discriminating genuinely, not by mocking the
simulation core.
"""

from __future__ import annotations

import ast
import datetime as _dt
import pathlib

import pytest

import autotuner
import math_engine

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_AUTOTUNER_PATH = _REPO_ROOT / "autotuner.py"

_SYM_ID = "sym-ac1-split-001"
_N_GROUPS = 6
_K_TEST = 2
_PURGE_DAYS = 2
_EMBARGO_DAYS = 1
_N_DAYS_PER_GROUP = 15
_N_TOTAL_DAYS = _N_GROUPS * _N_DAYS_PER_GROUP  # 90
_HOT_GROUP_IDX = 3
_GAMMA = 2.0

_PARAMS = {
    "TRIGGER_THRESHOLD_PCT": 15.0,
    "TAKE_PROFIT_MC_PCT": 5.0,
    "VWAP_CROSS_HWM_PCT": 99.0,
    "VWAP_BLEED_MULTIPLIER": 1.5,
    "VWAP_BLEED_TICKS": 999,
    "PARABOLIC_VELOCITY_THRESHOLD": 99.0,
    "MAX_PARABOLIC_SQUEEZE": 0.5,
}


def _sorted_dates() -> list[str]:
    base = _dt.date(2025, 1, 1)
    return [(base + _dt.timedelta(days=i)).isoformat() for i in range(_N_TOTAL_DAYS)]


def _tick(ret: float, mc: float) -> dict:
    return {
        "time": "tick",
        "return": ret,
        "mc_prob": mc,
        "vol": 1.0,
        "vwap_diff": 0.0,
        "base_atr_pct": 0.5,
        "valid_vwap_weight": 0.0,
    }


def _qualifying_trigger_sequence(magnitude: float = -20.0) -> list[dict]:
    """3 confirming ticks -- reused scenario from
    test_ac4_r2_residual_tripwire.py, guaranteed to fire "Trailing Stop"
    under the hardcoded default exit_confirm_ticks=3 with these params.
    A trailing 5th tick (fixed return=0.0, never reached by the exit loop
    since the stop already fired at tick_idx=3) decouples eod_return
    (ticks[-1], computed once before the loop) from `magnitude` -- without
    it, guard_alpha = triggered_return - eod_return is magnitude-INVARIANT
    (both terms shift by the same amount and cancel), which would make a
    magnitude perturbation a no-op on guard_alpha regardless of production
    correctness."""
    return [
        _tick(0.0, 10.0),
        _tick(magnitude, 70.0),
        _tick(magnitude - 1.0, 70.0),
        _tick(magnitude - 2.0, 70.0),
        _tick(0.0, 10.0),
    ]


def _flat_no_trigger_tick() -> list[dict]:
    """A single tick that never arms (mc=50.0, outside [5,15))."""
    return [_tick(0.1, 50.0)]


def _build_history_data(hot_group_idx: int = _HOT_GROUP_IDX, hot_magnitude: float = -20.0) -> dict:
    dates = _sorted_dates()
    days: dict[str, list[dict]] = {}
    for i, date in enumerate(dates):
        group_idx = i // _N_DAYS_PER_GROUP
        if group_idx == hot_group_idx:
            days[date] = _qualifying_trigger_sequence(hot_magnitude)
        else:
            days[date] = _flat_no_trigger_tick()
    return {_SYM_ID: days}


def _generate_folds() -> list[dict]:
    return autotuner._generate_cpcv_folds(
        sorted_dates=_sorted_dates(),
        n_groups=_N_GROUPS,
        k_test=_K_TEST,
        purge_days=_PURGE_DAYS,
        embargo_days=_EMBARGO_DAYS,
    )


def _split_score(history_data: dict, test_dates: list[str]) -> float:
    """Mirrors the objective() closure's own per-unit computation formula
    (autotuner.py: path_scores.append(math_engine.compute_crra_eu_objective(
    [r / RETURN_PCT_TO_FRACTION for r in path_returns], gamma))) -- applied
    to a SPLIT's own test_dates instead of a PATH's full-window dates."""
    date_set = set(test_dates)
    split_history = {
        _SYM_ID: {d: t for d, t in history_data[_SYM_ID].items() if d in date_set}
    }
    returns_pct = autotuner._collect_sim_returns(
        _PARAMS, split_history, [_SYM_ID], "2025-06-01", {}
    )
    fractions = [r / autotuner.RETURN_PCT_TO_FRACTION for r in returns_pct]
    return math_engine.compute_crra_eu_objective(fractions, _GAMMA)


# ===========================================================================
# (1) Confirmatory: the 15 split test-sets are ALREADY pairwise-distinct,
# purge-constrained subsets (~1/3 of the window each). This is TRUE of the
# existing _generate_cpcv_folds -- a regression guard, not new RED.
# ===========================================================================


class TestSplitTestSetsAlreadyPairwiseDistinct:
    def test_fifteen_splits_generated(self):
        folds = _generate_folds()
        assert len(folds) == 15

    def test_all_fifteen_test_date_sets_are_pairwise_distinct(self):
        folds = _generate_folds()
        test_sets = [frozenset(f["test_dates"]) for f in folds]
        assert len(set(test_sets)) == len(test_sets), (
            "Two or more of the 15 splits share an IDENTICAL test_dates set -- "
            "the per-split scoring redesign requires genuinely distinct units."
        )

    def test_each_split_test_set_is_roughly_one_third_of_the_window(self):
        folds = _generate_folds()
        for i, f in enumerate(folds):
            frac = len(f["test_dates"]) / _N_TOTAL_DAYS
            assert 0.20 <= frac <= 0.40, (
                f"Split {i} test_dates covers {frac:.2%} of the window -- expected "
                "~1/3 (k/N=2/6) for a k=2-group test combination, not the full window."
            )

    def test_no_split_test_set_equals_the_full_eligible_window(self):
        folds = _generate_folds()
        full_window = set(_sorted_dates())
        for i, f in enumerate(folds):
            assert set(f["test_dates"]) != full_window, (
                f"Split {i}'s test_dates equals the FULL eligible window -- this is "
                "exactly MA-2's degeneracy at the split level, which must not occur."
            )


# ===========================================================================
# Structural: objective() must no longer route trial scoring through
# _aggregate_cpcv_paths's full-window path reconstruction.
# ===========================================================================


def test_run_autotuner_objective_source_does_not_reference_full_window_path_histories() -> None:
    """RED (pre-fix, confirmed live via AST inspection): the objective()
    closure inside run_autotuner iterates ``_cpcv_path_histories`` -- the
    precomputed list of 5 FULL-WINDOW path-sliced history dicts built via
    ``_aggregate_cpcv_paths`` in the enclosing per-symphony loop (closure-
    captured, not built inside objective() itself -- a call-site-level AST
    search scoped only to objective()'s own body would miss this reference,
    since the _aggregate_cpcv_paths() CALL lives one scope up; this test
    checks for the closure-captured NAME reference instead, which is where
    objective() actually consumes the degenerate full-window construction).
    AC-1 (split-level scoring, team-lead RULED Option A) requires trial
    scoring to move off this full-window path reconstruction entirely --
    score each of the 15 real CPCV splits directly on its own test_dates
    instead (e.g. iterating _cpcv_folds or an equivalent per-split history
    precompute, never _cpcv_path_histories)."""
    src = _AUTOTUNER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(_AUTOTUNER_PATH))

    run_autotuner_fn = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run_autotuner"),
        None,
    )
    assert run_autotuner_fn is not None, "run_autotuner function not found in autotuner.py"

    objective_fn = next(
        (
            n
            for n in ast.walk(run_autotuner_fn)
            if isinstance(n, ast.FunctionDef) and n.name == "objective"
        ),
        None,
    )
    assert objective_fn is not None, "objective() closure not found inside run_autotuner"

    referenced_names = {
        node.id for node in ast.walk(objective_fn) if isinstance(node, ast.Name)
    }
    assert "_cpcv_path_histories" not in referenced_names, (
        "objective() still references _cpcv_path_histories (the closure-captured "
        "5-full-window-path precompute fed by _aggregate_cpcv_paths) -- AC-1 "
        "(split-level scoring, team-lead RULED Option A) requires trial scoring to "
        "move off full-window path reconstruction entirely. Score each of the 15 real "
        "CPCV splits directly on its own test_dates instead."
    )


# ===========================================================================
# (2)+(3)+(5): using the SAME production primitives the objective() closure
# already calls per-path today, demonstrate per-split scoring is genuinely
# non-identical, precisely isolated to the perturbed split, and exhibits
# non-zero dispersion -- the target contract for whatever internal wiring
# r2-stats lands.
# ===========================================================================


class TestSplitLevelScoringPrimitivesProduceGenuineDispersion:
    def test_per_split_scores_are_not_all_identical(self):
        """Property (2): scoring each of the 15 real splits directly (not
        via full-window path reconstruction) on the hot/flat discriminating
        fixture must NOT collapse to one repeated value the way MA-2's
        degenerate path-level aggregation does."""
        history_data = _build_history_data()
        folds = _generate_folds()
        scores = [_split_score(history_data, f["test_dates"]) for f in folds]

        assert len(set(scores)) > 1, (
            f"All 15 split-level scores are IDENTICAL ({scores[0]!r}) on a fixture "
            "deliberately constructed so only 5 of 15 splits contain the triggering "
            "'hot' group -- this reproduces MA-2's degeneracy at the split level."
        )

    def test_splits_containing_the_hot_group_score_nonzero_others_score_exactly_zero(self):
        """Precise mechanistic pin: splits whose test_dates include ANY hot-group
        date must score non-zero (real triggered returns); splits with none of
        the hot group must score EXACTLY 0.0 (compute_crra_eu_objective's own
        empty-series contract) -- not a coincidental near-miss."""
        history_data = _build_history_data()
        folds = _generate_folds()
        hot_dates = set(_sorted_dates()[_HOT_GROUP_IDX * _N_DAYS_PER_GROUP : (_HOT_GROUP_IDX + 1) * _N_DAYS_PER_GROUP])

        hot_splits = [f for f in folds if hot_dates & set(f["test_dates"])]
        cold_splits = [f for f in folds if not (hot_dates & set(f["test_dates"]))]
        assert hot_splits, "Fixture self-check: no split contains the hot group -- adjust fixture."
        assert cold_splits, "Fixture self-check: every split contains the hot group -- adjust fixture."

        for f in hot_splits:
            score = _split_score(history_data, f["test_dates"])
            assert score != 0.0, (
                f"A split containing the hot group scored exactly 0.0 -- expected a "
                f"real nonzero triggered-return score. test_dates sample: "
                f"{sorted(f['test_dates'])[:3]}..."
            )
        for f in cold_splits:
            score = _split_score(history_data, f["test_dates"])
            assert score == 0.0, (
                f"A split NOT containing the hot group scored {score!r} (expected exactly "
                "0.0, compute_crra_eu_objective's empty-series contract) -- unexpected "
                "triggering on a flat-tick-only window."
            )

    def test_perturbing_a_single_hot_date_changes_only_the_splits_containing_it(self):
        """Property (3), window-sensitivity: mutating ONE specific date's tick
        magnitude must change the score of ONLY the splits whose test_dates
        include that exact date, and leave every other split's score
        byte-identical. This is the precise per-split isolation guarantee
        genuine CPCV split-level scoring must provide."""
        history_data = _build_history_data()
        folds = _generate_folds()

        target_date = _sorted_dates()[_HOT_GROUP_IDX * _N_DAYS_PER_GROUP]  # first hot-group day
        baseline_scores = {i: _split_score(history_data, f["test_dates"]) for i, f in enumerate(folds)}

        # Mutate ONLY target_date's tick sequence (deeper decline -> more negative
        # guard_alpha) -- a real, non-mocked perturbation via the same primitive
        # construction the fixture already uses.
        mutated = _build_history_data()
        mutated[_SYM_ID][target_date] = _qualifying_trigger_sequence(magnitude=-40.0)

        affected_split_indices = {
            i for i, f in enumerate(folds) if target_date in f["test_dates"]
        }
        assert affected_split_indices, "Fixture self-check: target_date not in any split's test_dates."

        for i, f in enumerate(folds):
            mutated_score = _split_score(mutated, f["test_dates"])
            if i in affected_split_indices:
                assert mutated_score != baseline_scores[i], (
                    f"Split {i} (contains the perturbed date {target_date}) did not change "
                    f"score after perturbation ({mutated_score!r} == baseline "
                    f"{baseline_scores[i]!r}) -- expected genuine sensitivity to its own window."
                )
            else:
                assert mutated_score == baseline_scores[i], (
                    f"Split {i} (does NOT contain the perturbed date {target_date}) changed "
                    f"score after perturbation ({baseline_scores[i]!r} -> {mutated_score!r}) -- "
                    "a split must be isolated to its own test_dates window; leakage from an "
                    "unrelated date is a purge/isolation violation."
                )

    def test_split_score_vector_has_nonzero_variance(self):
        """Property (5): the vector of per-split scores (whatever attribute
        the eventual implementation stores it under) must have non-zero
        variance on this discriminating fixture -- today's path-level
        aggregation produces a degenerate all-identical (zero-variance)
        vector, which is part of MA-2's documented damage (the dispersion
        consumers this vector eventually feeds -- sentinel detection,
        any future t-stat over split scores -- receive zero signal)."""
        history_data = _build_history_data()
        folds = _generate_folds()
        scores = [_split_score(history_data, f["test_dates"]) for f in folds]

        mean = sum(scores) / len(scores)
        variance = sum((s - mean) ** 2 for s in scores) / len(scores)
        assert variance > 0.0, (
            f"Split-level score vector {scores!r} has ZERO variance -- MA-2's "
            "degeneracy (5x/15x identical scores) reproduced. A genuinely disjoint "
            "split ensemble on this discriminating fixture must show real dispersion."
        )


# ===========================================================================
# (4) Documented failing baseline: the historical 5x-identical-full-window
# degeneracy, reproduced via _aggregate_cpcv_paths directly -- conditional
# on that function surviving retirement (r2-stats's trace decides
# retain-vs-delete). Skips gracefully, never errors, if deleted.
# ===========================================================================


class TestFullWindowDegeneracyDocumentedBaseline:
    def test_aggregate_cpcv_paths_still_reproduces_the_historical_full_window_degeneracy(self):
        """If _aggregate_cpcv_paths survives (retained with a documented
        non-scoring role), it must STILL exhibit the audit's original
        5x-identical-full-window property when invoked standalone -- this
        is intrinsic to canonical CPCV path reconstruction at N=6/k=2, not
        a bug in _aggregate_cpcv_paths itself (see this file's module
        docstring). It is retired from the SCORING path, not "fixed" to
        produce different paths, since that would contradict canonical CPCV.
        """
        autotuner_module = autotuner
        if not hasattr(autotuner_module, "_aggregate_cpcv_paths"):
            pytest.skip(
                "_aggregate_cpcv_paths has been deleted (r2-stats's trace found zero "
                "surviving consumers) -- the historical degeneracy is now structurally "
                "impossible to reach at all, which supersedes this documentation pin."
            )
        folds = _generate_folds()
        paths = autotuner_module._aggregate_cpcv_paths(folds, n_paths=5)
        path_sets = [frozenset(p) for p in paths]
        assert len(set(path_sets)) == 1, (
            f"_aggregate_cpcv_paths produced {len(set(path_sets))} distinct path sets "
            "instead of the historically-documented single degenerate full-window set "
            "-- if this changed, re-verify AC-1's root-cause framing (this file's module "
            "docstring) is still accurate; canonical N=6/k=2 CPCV path reconstruction "
            "should still force every path to the full window."
        )
