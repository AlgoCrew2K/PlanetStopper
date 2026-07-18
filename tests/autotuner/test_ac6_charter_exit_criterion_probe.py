"""
AC-6 (no-regression) -- the program charter's R2 exit criterion: "a nightly
run whose selection/adoption numbers are demonstrably out-of-sample (probe
harness kept as a regression test)".

This is the END-TO-END tie-together of AC-1 (split-level CPCV selection) and
AC-2 (adoption holdout disjointness): a REAL (not history-mocked)
run_autotuner drive, spying on which DATES actually flow through the
selection-scoring primitive (_collect_sim_returns_dated, used exclusively by
trial scoring during study.optimize) versus the adoption-cascade primitive
(run_simulation, used exclusively for oos_alpha/fallback_oos_alpha/
default_oos_alpha, called only AFTER selection completes) -- and asserts
their date-sets are DISJOINT.

Mechanism-agnostic by construction: this test does not assume which fold
r2-stats sources history_test from, nor how the per-split selection loop is
structured internally -- it spies on the two PRODUCTION FUNCTIONS
(_collect_sim_returns_dated, run_simulation) that are, respectively,
EXCLUSIVELY the selection-scoring path and EXCLUSIVELY the adoption-cascade
path (confirmed by direct read: no other caller of either function exists
in run_autotuner's body), so it holds regardless of internal redesign
details.

RED today (verified live): history_test = history_validation_full is a
SUBSET of the CPCV-eligible window trial selection scores on -- the two
date-sets this test captures are NOT disjoint pre-fix.

This is a KEPT regression test (not a one-off probe) -- per the charter,
it stays in the suite permanently as the exit-criterion proof.
"""

from __future__ import annotations

import contextlib
import types
from unittest.mock import MagicMock, patch

import pytest

import autotuner

_FROZEN_FOLD_RETURNS_PCT = [1.2, -0.8, 0.5, -2.1, 0.3]
_GAMMA = 2.0


def _make_trial(value: float, returns: list[float], params: dict) -> object:
    t = types.SimpleNamespace()
    t.value = value
    t.user_attrs = {"daily_returns": list(returns)}
    t.params = dict(params)
    return t


def _full_params() -> dict:
    return {
        "TAKE_PROFIT_MC_PCT": 5.0,
        "VWAP_CROSS_HWM_PCT": 1.0,
        "VWAP_BLEED_MULTIPLIER": 1.5,
        "VWAP_BLEED_TICKS": 10,
        "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
        "MAX_PARABOLIC_SQUEEZE": 0.5,
    }


def _synthetic_history_payload() -> dict:
    """125 real days -- date CONTENT is irrelevant to this probe (it only
    captures WHICH DATES flow through which function), so flat returns are
    sufficient; date coverage across the full 60/20/20 split is what matters."""
    history = {"sym-test-001": {}}
    for day_idx in range(125):
        date_key = f"2026-01-{day_idx + 1:03d}"
        history["sym-test-001"][date_key] = [
            {"return": 0.0, "vol": 1.0, "mc_prob": 50.0, "vwap_diff": 0.0, "valid_vwap_weight": 1.0}
        ]
    return history


def _seed_bot_state() -> dict:
    return {
        "date": "2026-05-21",
        "sym-test-001": {
            "name": "TestSym",
            "account": "acct-test",
            "current_value": 10_000.0,
        },
    }


class _DateCapturingSpy:
    """Wraps a real function, recording the set of date-keys present in its
    history_data argument (2nd positional or 'history_data' kwarg) on every
    call, without altering behavior."""

    def __init__(self, real_fn):
        self._real_fn = real_fn
        self.seen_dates: set[str] = set()
        self.call_count = 0

    def __call__(self, *args, **kwargs):
        self.call_count += 1
        history_data = kwargs.get("history_data", args[1] if len(args) > 1 else None)
        if isinstance(history_data, dict):
            for sym_dates in history_data.values():
                if isinstance(sym_dates, dict):
                    self.seen_dates.update(sym_dates.keys())
        return self._real_fn(*args, **kwargs)


class _FakeTrial:
    """Minimal stand-in for an optuna.Trial sufficient to drive the REAL
    objective() closure end-to-end: suggest_float/suggest_int return the
    midpoint of the requested range (the actual suggested value is
    irrelevant to this probe -- only WHICH DATES flow through selection
    vs. adoption matters), set_user_attr records into .user_attrs so
    downstream code (filter_sortino_sentinels / _haircut_select) can read
    daily_returns / path_scores / cscv_date_returns exactly as it would
    from a real optuna.trial.FrozenTrial."""

    def __init__(self):
        self.params: dict = {}
        self.user_attrs: dict = {}
        self.value: float | None = None

    def suggest_float(self, name, low, high, *args, **kwargs):
        val = (low + high) / 2.0
        self.params[name] = val
        return val

    def suggest_int(self, name, low, high, *args, **kwargs):
        val = int((low + high) // 2)
        self.params[name] = val
        return val

    def set_user_attr(self, key, value):
        self.user_attrs[key] = value


class _FakeStudy:
    """Stand-in for optuna.Study: .optimize(objective, n_trials, n_jobs)
    calls the REAL objective() closure with a _FakeTrial for real (no
    Optuna storage/DB involved at all), so the genuine trial-scoring code
    path executes end-to-end while every Optuna-internal detail (RDB
    storage, sampler, pruner) stays out of scope for this probe."""

    def __init__(self):
        self.trials: list[_FakeTrial] = []
        self.best_value: float | None = None
        self.best_params: dict = {}

    def optimize(self, objective_fn, n_trials: int, n_jobs: int = 1):
        for _ in range(n_trials):
            trial = _FakeTrial()
            trial.value = objective_fn(trial)
            self.trials.append(trial)
        best = max(self.trials, key=lambda t: t.value if t.value is not None else float("-inf"))
        self.best_value = best.value
        self.best_params = dict(best.params)


def _run_probe(*, study_best_params: dict) -> dict:
    """Drive run_autotuner with REAL _collect_sim_returns_dated and REAL
    run_simulation (both spied, never mocked) so genuine date-flow can be
    observed. optuna.create_study is replaced with a _FakeStudy that runs
    the REAL objective() closure via a _FakeTrial -- no real Optuna
    storage/DB is ever touched, avoiding storage-layer mocking entirely."""
    selection_spy = _DateCapturingSpy(autotuner._collect_sim_returns_dated)
    adoption_spy = _DateCapturingSpy(autotuner.run_simulation)

    _stub_bundle_row = {"bundle_hash": "test-stub-hash-ac6"}
    _stub_facets = [
        {"facet_name": "utility_family", "facet_value": "CRRA"},
        {"facet_name": "gamma", "facet_value": str(_GAMMA)},
    ]

    captured_save: dict = {}

    def _capture_save(**kwargs):
        captured_save.update(kwargs)

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(autotuner, "_collect_sim_returns_dated", selection_spy))
        stack.enter_context(patch.object(autotuner, "run_simulation", adoption_spy))
        stack.enter_context(patch.object(autotuner, "OPTUNA_N_TRIALS_PRODUCTION", 1))
        stack.enter_context(
            patch.object(
                autotuner,
                "calculate_historical_deviation",
                return_value={
                    "Take-Profit": 0.0,
                    "Trailing Stop": -0.2,
                    "VWAP Breakdown": -0.4,
                    "VWAP Bleed Cut": -0.25,
                },
            )
        )
        stack.enter_context(
            patch.object(
                autotuner.synthetic_history,
                "generate_synthetic_history",
                return_value=_synthetic_history_payload(),
            )
        )
        stack.enter_context(patch.object(autotuner, "_apply_optuna_archive_migration_if_needed"))
        stack.enter_context(patch("autotuner.optuna.create_study", return_value=_FakeStudy()))
        stack.enter_context(
            patch.object(autotuner.database, "save_autotune_run", side_effect=_capture_save)
        )
        stack.enter_context(patch.object(autotuner.database, "save_symphony_strategy"))
        stack.enter_context(patch.object(autotuner.database, "save_chart_archive"))
        stack.enter_context(
            patch.object(
                autotuner.database,
                "load_chart_history",
                return_value={"date": "2026-05-21", "symphonies": {}},
            )
        )
        stack.enter_context(
            patch.object(
                autotuner.database,
                "get_symphony_strategy",
                return_value={"params": _full_params(), "locked_vars": []},
            )
        )
        stack.enter_context(
            patch.object(autotuner.database, "DEFAULT_STRATEGY", new=_full_params())
        )
        stack.enter_context(
            patch.object(
                autotuner.database,
                "normalize_name",
                side_effect=lambda s: (s or "").strip().lower(),
            )
        )
        stack.enter_context(
            patch.object(autotuner.database, "get_spec_bundle_by_id", return_value=_stub_bundle_row)
        )
        stack.enter_context(
            patch.object(
                autotuner.database, "get_spec_facets_for_bundle", return_value=_stub_facets
            )
        )
        stack.enter_context(patch.object(autotuner.database, "advisor_ro_query", return_value=[]))
        stack.enter_context(
            patch.object(autotuner, "validate_nn1_compliance", return_value=(True, []))
        )

        bot_state = _seed_bot_state()
        harness_exception = None
        try:
            autotuner.run_autotuner(
                bot_state,
                current_date_str="2026-05-21",
                account_uuids=["acct-test"],
                spec_bundle_id=1,
            )
        except Exception as exc:  # pragma: no cover - diagnostic only
            harness_exception = f"{type(exc).__name__}: {exc}"

    return {
        "selection_dates": selection_spy.seen_dates,
        "selection_call_count": selection_spy.call_count,
        "adoption_dates": adoption_spy.seen_dates,
        "adoption_call_count": adoption_spy.call_count,
        "captured_save": captured_save,
        "harness_exception": harness_exception,
    }


class TestChartierExitCriterionSelectionAndAdoptionAreDisjoint:
    def test_harness_precondition_both_spies_were_actually_invoked(self):
        """Precondition: this probe is only meaningful if BOTH the
        selection path (_collect_sim_returns_dated, called during
        study.optimize) and the adoption path (run_simulation, called
        post-selection) genuinely ran -- a silently-skipped path would make
        the disjointness assertion vacuously true."""
        result = _run_probe(study_best_params=_full_params())
        assert result["selection_call_count"] > 0, (
            "_collect_sim_returns_dated (the selection-scoring path) was never called -- "
            f"probe is vacuous. Harness diagnostic: {result['harness_exception']}."
        )
        assert result["adoption_call_count"] > 0, (
            "run_simulation (the adoption-cascade path) was never called -- probe is "
            f"vacuous. Harness diagnostic: {result['harness_exception']}."
        )

    def test_selection_and_adoption_date_sets_are_disjoint(self):
        """The charter's exit criterion, made concrete: the union of every
        date _collect_sim_returns_dated saw during trial SELECTION must
        have EMPTY intersection with the union of every date run_simulation
        saw during the adoption cascade's OOS comparison. RED today
        (verified live): history_test's current in-sample assignment makes
        these sets overlap entirely for the adoption-cascade portion drawn
        from history_validation_full."""
        result = _run_probe(study_best_params=_full_params())
        overlap = result["selection_dates"] & result["adoption_dates"]
        assert not overlap, (
            f"Selection-phase dates and adoption-cascade dates OVERLAP on "
            f"{len(overlap)} date(s) (sample: {sorted(overlap)[:5]}). The charter's exit "
            "criterion requires the adoption cascade's OOS numbers to be computed on data "
            "genuinely disjoint from whatever CPCV selection scored on -- 'OOS validation "
            "passed!' must mean something. Selection saw "
            f"{len(result['selection_dates'])} distinct dates; adoption saw "
            f"{len(result['adoption_dates'])} distinct dates."
        )
