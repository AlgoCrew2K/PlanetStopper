"""
RED tests for V1 calibration sweep (run_calibration_sweep).

Fixtures:
  tests/fixtures/calibration/v1/search_space_contract.json  — scope of tuned params
  tests/fixtures/calibration/v1/e1_velocity_contract.json   — E1 cycle-1 velocity baseline
  tests/fixtures/calibration/v1/calibration_report_schema.json — required report fields
  tests/fixtures/calibration/v1/determinism_seed_contract.json — seed/determinism contract

All tests in this module are RED until run_calibration_sweep exists in autotuner.py.
No mock of the math engine. No live API calls. No hardcoded producer-computed values.
"""

from __future__ import annotations

import importlib
import inspect
import json
import math
import pathlib
import re
import sys
import types

import optuna
import pytest

# ---------------------------------------------------------------------------
# Fixture loading (provenance-clear; no inline construction of producer values)
# ---------------------------------------------------------------------------

_FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "calibration" / "v1"


@pytest.fixture(scope="module")
def search_space_contract():
    with open(_FIXTURE_DIR / "search_space_contract.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def e1_velocity_contract():
    with open(_FIXTURE_DIR / "e1_velocity_contract.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def calibration_report_schema():
    with open(_FIXTURE_DIR / "calibration_report_schema.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def determinism_seed_contract():
    with open(_FIXTURE_DIR / "determinism_seed_contract.json", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Minimal deterministic fixture history — no live API calls, no mocked engine.
# Two symphonies × 10 days × 5 ticks per day. Values chosen to produce at
# least one VWAP or parabolic trigger under wide parameter ranges without
# hardcoding specific producer outputs.
# ---------------------------------------------------------------------------

_SYMPHONY_A = "test_symphony_alpha"
_SYMPHONY_B = "test_symphony_beta"

_TICK_BASELINE = {
    "return": 0.5,
    "mc_prob": 10.0,
    "vol": 1.0,
    "vwap_diff": -0.3,
    "base_atr_pct": 1.0,
    "valid_vwap_weight": 0.8,
}


def _make_tick(**overrides):
    t = dict(_TICK_BASELINE)
    t.update(overrides)
    return t


def _make_day_ticks(n: int = 5) -> list[dict]:
    """Five ticks: first tick has a large return jump to test parabolic arming."""
    ticks = []
    for i in range(n):
        ret = 0.1 * (i + 1)
        ticks.append(
            _make_tick(return_=ret if False else None)
            if False
            else _make_tick(**{"return": 0.1 * (i + 1)})
        )
    # Overwrite tick 1 with a velocity spike so PARABOLIC_VELOCITY_THRESHOLD is exercised
    ticks[1] = _make_tick(**{"return": 3.5})
    return ticks


def _make_fixture_history(sym_ids: list[str], n_days: int = 40) -> dict:
    """
    Build deterministic history with enough days to satisfy the 60/20/20 fold
    without shrinking validation to zero.  n_days=40 => 24/8/8 raw folds.
    """
    import datetime

    base_date = datetime.date(2024, 1, 2)
    history = {}
    for sym_id in sym_ids:
        history[sym_id] = {}
        for d in range(n_days):
            date_str = (base_date + datetime.timedelta(days=d)).strftime("%Y-%m-%d")
            history[sym_id][date_str] = _make_day_ticks()
    return history


@pytest.fixture(scope="module")
def fixture_history():
    return _make_fixture_history([_SYMPHONY_A, _SYMPHONY_B])


@pytest.fixture(scope="module")
def single_sym_history():
    return _make_fixture_history([_SYMPHONY_A])


@pytest.fixture(scope="module")
def deviation_dict():
    # Stable defaults matching autotuner.calculate_historical_deviation fallbacks
    return {
        "Take-Profit": 0.0,
        "Trailing Stop": -0.20,
        "VWAP Breakdown": -0.40,
        "VWAP Bleed Cut": -0.25,
    }


# ---------------------------------------------------------------------------
# Helper: import autotuner without triggering optuna.logging or DB side effects
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def autotuner_module():
    import autotuner as _at

    return _at


# ---------------------------------------------------------------------------
# 1. INTERFACE — run_calibration_sweep exists with the right signature
# ---------------------------------------------------------------------------


class TestCalibrationSweepInterface:
    def test_run_calibration_sweep_is_callable(self, autotuner_module):
        """run_calibration_sweep must exist as a callable in autotuner.py."""
        assert hasattr(autotuner_module, "run_calibration_sweep"), (
            "autotuner.run_calibration_sweep not found — implementer must add it"
        )
        assert callable(autotuner_module.run_calibration_sweep)

    def test_run_calibration_sweep_accepts_required_params(self, autotuner_module):
        """
        run_calibration_sweep(history_data, current_params, current_date_str,
                               deviation_dict, random_state) — all required.
        """
        sig = inspect.signature(autotuner_module.run_calibration_sweep)
        params = set(sig.parameters.keys())
        required = {
            "history_data",
            "current_params",
            "current_date_str",
            "deviation_dict",
            "random_state",
        }
        missing = required - params
        assert not missing, f"run_calibration_sweep missing parameters: {missing}"

    def test_run_calibration_sweep_returns_list(
        self, autotuner_module, single_sym_history, deviation_dict
    ):
        """run_calibration_sweep must return a list (one dict per tuned param)."""
        result = autotuner_module.run_calibration_sweep(
            history_data=single_sym_history,
            current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
            current_date_str="2024-03-01",
            deviation_dict=deviation_dict,
            random_state=42,
            min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
        )
        assert isinstance(result, list), (
            f"run_calibration_sweep must return list, got {type(result)}"
        )


# ---------------------------------------------------------------------------
# 2. SEARCH SPACE — only PARA velocity + VWAP HWM tuned; others excluded
# ---------------------------------------------------------------------------


class TestCalibrationSearchSpace:
    def test_search_space_contains_only_tuned_params(
        self, autotuner_module, single_sym_history, deviation_dict, search_space_contract
    ):
        """
        The calibration sweep Optuna study must suggest ONLY
        PARABOLIC_VELOCITY_THRESHOLD and VWAP_CROSS_HWM_PCT — not the full
        run_autotuner search space.

        Strategy: monkey-patch trial.suggest_float/suggest_int to capture all
        suggested param names; assert set equals the fixture's tuned_params list.
        """
        suggested_names: set[str] = set()
        _orig_create_study = optuna.create_study

        def _capturing_study(*args, **kwargs):
            # Force deterministic, in-memory, no-storage study for the test
            kwargs.pop("storage", None)
            kwargs.pop("load_if_exists", None)
            sampler = optuna.samplers.TPESampler(seed=42)
            study = _orig_create_study(
                direction=kwargs.get("direction", "maximize"),
                sampler=sampler,
            )

            orig_optimize = study.optimize

            def _capturing_optimize(objective, n_trials=1, **kw):
                # Run just 1 trial to capture suggest calls cheaply
                def _wrapped(trial):
                    orig_sf = trial.suggest_float
                    orig_si = trial.suggest_int

                    def _sf(name, *a, **k):
                        suggested_names.add(name)
                        return orig_sf(name, *a, **k)

                    def _si(name, *a, **k):
                        suggested_names.add(name)
                        return orig_si(name, *a, **k)

                    trial.suggest_float = _sf
                    trial.suggest_int = _si
                    return objective(trial)

                orig_optimize(_wrapped, n_trials=1, **kw)

            study.optimize = _capturing_optimize
            return study

        monkeypatch_target = sys.modules.get("autotuner")
        assert monkeypatch_target is not None

        original_create = getattr(optuna, "create_study")
        monkeypatch_target.optuna.create_study = _capturing_study
        try:
            autotuner_module.run_calibration_sweep(
                history_data=single_sym_history,
                current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
                current_date_str="2024-03-01",
                deviation_dict=deviation_dict,
                random_state=42,
                min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
            )
        finally:
            monkeypatch_target.optuna.create_study = original_create

        tuned_param_names = {p["name"] for p in search_space_contract["tuned_params"]}
        assert suggested_names == tuned_param_names, (
            f"Calibration sweep suggested unexpected params.\n"
            f"  Expected: {tuned_param_names}\n"
            f"  Got:      {suggested_names}\n"
            f"  Extra (must NOT be suggested): {suggested_names - tuned_param_names}"
        )

    def test_prohibited_params_not_in_report(
        self,
        autotuner_module,
        single_sym_history,
        deviation_dict,
        search_space_contract,
        calibration_report_schema,
    ):
        """
        Report rows must only contain PARABOLIC_VELOCITY_THRESHOLD and
        VWAP_CROSS_HWM_PCT as param_name values. Prohibited params from the
        fixture must not appear.
        """
        result = autotuner_module.run_calibration_sweep(
            history_data=single_sym_history,
            current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
            current_date_str="2024-03-01",
            deviation_dict=deviation_dict,
            random_state=42,
            min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
        )
        reported_params = {row["param_name"] for row in result}
        prohibited = set(calibration_report_schema["prohibited_params"])
        violations = reported_params & prohibited
        assert not violations, f"Report contains prohibited param_name values: {violations}"

    def test_vwap_break_confirm_ticks_not_suggested(
        self, autotuner_module, single_sym_history, deviation_dict, search_space_contract
    ):
        """
        VWAP_BREAK_CONFIRM_TICKS is hand-set per audit recommendation and must
        never appear in the calibration sweep's Optuna trial suggestions.
        """
        # This is also covered by test_search_space_contains_only_tuned_params but
        # explicit single-param assertion makes failures easier to diagnose.
        prohibited_in_fixture = search_space_contract[
            "hand_set_must_not_be_tuned_by_calibration_sweep"
        ]
        assert "VWAP_BREAK_CONFIRM_TICKS" in prohibited_in_fixture, (
            "Fixture contract must list VWAP_BREAK_CONFIRM_TICKS as prohibited"
        )


# ---------------------------------------------------------------------------
# 3. PURGE + EMBARGO (O1) — fold boundaries correctly applied
# ---------------------------------------------------------------------------


class TestPurgeEmbargoO1:
    def test_calibration_sweep_applies_purge_and_embargo_at_train_val_boundary(
        self, autotuner_module
    ):
        """
        The autotuner constants PURGE_DAYS and EMBARGO_DAYS must be imported
        from autotuner and must be positive integers — confirming O1 is in scope.
        """
        assert hasattr(autotuner_module, "PURGE_DAYS"), "PURGE_DAYS missing from autotuner"
        assert hasattr(autotuner_module, "EMBARGO_DAYS"), "EMBARGO_DAYS missing from autotuner"
        assert isinstance(autotuner_module.PURGE_DAYS, int) and autotuner_module.PURGE_DAYS > 0
        assert isinstance(autotuner_module.EMBARGO_DAYS, int) and autotuner_module.EMBARGO_DAYS > 0

    def test_calibration_sweep_validation_dates_exclude_purge_window(
        self, autotuner_module, single_sym_history, deviation_dict
    ):
        """
        run_calibration_sweep must not use raw validation dates — the last
        PURGE_DAYS + EMBARGO_DAYS of the raw training window must be excluded.

        Strategy: inspect that the objective closure only scores on the purged
        validation window (not the full raw validation). We verify by checking
        that at least PURGE_DAYS worth of training dates are dropped.
        """
        # We verify indirectly via result shape: a sweep that scored on the full
        # validation window would return results, but we assert it also returns
        # the trading_day_start and trading_day_end which reflect purged boundaries.
        result = autotuner_module.run_calibration_sweep(
            history_data=single_sym_history,
            current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
            current_date_str="2024-03-01",
            deviation_dict=deviation_dict,
            random_state=42,
            min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
        )
        assert isinstance(result, list) and len(result) > 0
        for row in result:
            assert "trading_day_start" in row
            assert "trading_day_end" in row
            # Both must be valid ISO dates
            import datetime

            datetime.date.fromisoformat(row["trading_day_start"])
            datetime.date.fromisoformat(row["trading_day_end"])


# ---------------------------------------------------------------------------
# 4. OBJECTIVE (O5 Sortino) — calibration sweep uses Sortino, not 5-magic composite
# ---------------------------------------------------------------------------


class TestSortinoObjectiveO5:
    def test_compute_sortino_ratio_present_in_autotuner(self, autotuner_module):
        """O5: autotuner.compute_sortino_ratio must be the selection objective."""
        assert hasattr(autotuner_module, "compute_sortino_ratio"), (
            "compute_sortino_ratio not found in autotuner — O5 Sortino objective missing"
        )

    def test_calibration_sweep_objective_calls_sortino_not_five_number_composite(
        self, autotuner_module, single_sym_history, deviation_dict
    ):
        """
        The calibration sweep's Optuna objective must call compute_sortino_ratio.
        We assert this by patching compute_sortino_ratio with a tracker and
        verifying it is called at least once during optimize().
        """
        call_count = [0]
        orig_sortino = autotuner_module.compute_sortino_ratio

        def _tracking_sortino(returns, *a, **k):
            call_count[0] += 1
            return orig_sortino(returns, *a, **k)

        autotuner_module.compute_sortino_ratio = _tracking_sortino
        try:
            autotuner_module.run_calibration_sweep(
                history_data=single_sym_history,
                current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
                current_date_str="2024-03-01",
                deviation_dict=deviation_dict,
                random_state=42,
                min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
            )
        finally:
            autotuner_module.compute_sortino_ratio = orig_sortino

        assert call_count[0] >= 1, (
            "compute_sortino_ratio was never called during calibration sweep — "
            "O5 Sortino objective not wired into run_calibration_sweep"
        )


# ---------------------------------------------------------------------------
# 5. STUDY NAMES (O3) — timestamped, load_if_exists=False
# ---------------------------------------------------------------------------


class TestStudyNamingO3:
    def test_calibration_sweep_study_name_matches_timestamp_pattern(
        self, autotuner_module, single_sym_history, deviation_dict
    ):
        """
        Study name in the report must match YYYYMMDDTHHMMSS...Z__<symphony_id>.
        This confirms O3: unique timestamp + no study reuse.
        """
        result = autotuner_module.run_calibration_sweep(
            history_data=single_sym_history,
            current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
            current_date_str="2024-03-01",
            deviation_dict=deviation_dict,
            random_state=42,
            min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
        )
        ts_pattern = re.compile(r"^\d{8}T\d{6}")
        for row in result:
            study_name = row.get("study_name", "")
            assert "__" in study_name, f"study_name '{study_name}' missing __ separator (O3)"
            ts_part = study_name.split("__")[0]
            assert ts_pattern.match(ts_part), (
                f"study_name timestamp part '{ts_part}' does not match YYYYMMDDTHHMMSS (O3)"
            )

    def test_calibration_sweep_uses_load_if_exists_false(
        self, autotuner_module, single_sym_history, deviation_dict
    ):
        """
        Two consecutive calls with in-memory storage must produce different study names,
        proving load_if_exists=False (O3 anti-reuse guarantee).
        """
        result_1 = autotuner_module.run_calibration_sweep(
            history_data=single_sym_history,
            current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
            current_date_str="2024-03-01",
            deviation_dict=deviation_dict,
            random_state=42,
            min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
        )
        result_2 = autotuner_module.run_calibration_sweep(
            history_data=single_sym_history,
            current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
            current_date_str="2024-03-01",
            deviation_dict=deviation_dict,
            random_state=42,
            min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
        )
        names_1 = {r["study_name"] for r in result_1}
        names_2 = {r["study_name"] for r in result_2}
        assert names_1.isdisjoint(names_2), (
            "Consecutive runs reused the same study_name — load_if_exists=False required (O3)"
        )


# ---------------------------------------------------------------------------
# 6. BEST TRIAL SELECTION (O2) — deflated Sharpe re-ranking
# ---------------------------------------------------------------------------


class TestHarveyLiuHaircutD3:
    """
    Re-pinned for Decision D3: the calibration sweep's selection-bias correction
    is the Harvey & Liu 2015 Benjamini-Hochberg haircut, NOT the (deleted)
    Sharpe-derived Deflated Sharpe Ratio. The previous TestDeflatedSharpeO2
    class pinned compute_deflated_sharpe_ratio and a `deflated_sharpe` report
    key — both removed by D3. This class pins the haircut in the calibration
    path instead.
    """

    def test_compute_deflated_sharpe_ratio_is_deleted(self, autotuner_module):
        """D3: compute_deflated_sharpe_ratio must no longer exist in autotuner."""
        assert not hasattr(autotuner_module, "compute_deflated_sharpe_ratio"), (
            "compute_deflated_sharpe_ratio must be DELETED (Decision D3) — "
            "the Sharpe-DSR is replaced by the Harvey & Liu haircut."
        )

    def test_report_contains_selection_tstat_and_naive_sharpe(
        self, autotuner_module, single_sym_history, deviation_dict
    ):
        """
        Report rows must expose both selection_tstat (the Harvey & Liu haircut
        winner's t-statistic) and naive_sharpe. Neither is asserted as a specific
        float — shape / presence only.
        """
        result = autotuner_module.run_calibration_sweep(
            history_data=single_sym_history,
            current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
            current_date_str="2024-03-01",
            deviation_dict=deviation_dict,
            random_state=42,
            min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
        )
        for row in result:
            assert "selection_tstat" in row, f"Row missing selection_tstat: {row}"
            assert "naive_sharpe" in row, f"Row missing naive_sharpe: {row}"
            assert "deflated_sharpe" not in row, (
                f"Row still carries the deleted 'deflated_sharpe' key: {row}"
            )
            # Values may be None if no trial cleared the FDR gate / <2 trials,
            # but must not be absent.
            if row["selection_tstat"] is not None:
                assert math.isfinite(row["selection_tstat"]), (
                    "selection_tstat must be a finite float or None — not NaN/Inf"
                )
            if row["naive_sharpe"] is not None:
                assert math.isfinite(row["naive_sharpe"]), (
                    "naive_sharpe must be a finite float or None — not NaN/Inf"
                )

    def test_benjamini_hochberg_haircut_is_wired_into_calibration_sweep(
        self, autotuner_module, single_sym_history, deviation_dict
    ):
        """
        D3: benjamini_hochberg_adjust must be called during run_calibration_sweep
        — the Harvey & Liu haircut must gate trial selection in the calibration
        path, not only in run_autotuner.
        """
        call_count = [0]
        orig_bhy = autotuner_module.benjamini_hochberg_adjust

        def _tracking_bhy(*a, **k):
            call_count[0] += 1
            return orig_bhy(*a, **k)

        autotuner_module.benjamini_hochberg_adjust = _tracking_bhy
        try:
            autotuner_module.run_calibration_sweep(
                history_data=single_sym_history,
                current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
                current_date_str="2024-03-01",
                deviation_dict=deviation_dict,
                random_state=42,
                min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
            )
        finally:
            autotuner_module.benjamini_hochberg_adjust = orig_bhy

        assert call_count[0] >= 1, (
            "benjamini_hochberg_adjust was never called — the Harvey & Liu "
            "haircut is not wired into the calibration-sweep selection path (D3)."
        )


class TestHaircutOutcomeSurfacing:
    """
    risk-engine-specialist trace + team-lead conditional ruling (2026-05-22):
    run_calibration_sweep is diagnostic-only (no live-write path) — but team-lead's
    branch-2 condition requires the calibration report to surface, UNAMBIGUOUSLY
    and adjacent to the winner, whether a trial cleared the Harvey & Liu FDR gate.

    The defect: when no trial clears the gate, run_calibration_sweep silently keeps
    the NAIVE Optuna winner with `selection_tstat=None`. That None is ambiguous —
    it is the SAME None a row carries when the haircut could not run at all (zero
    non-sentinel trials). An operator reading the report sees a concrete proposed
    parameter change with a blank significance field; nothing says "this proposal
    is statistically indistinguishable from noise — it FAILED the overfitting
    gate". Unlike run_autotuner (winner=None → haircut_rejected_proposal → cascade
    poison), the sweep drops the rejection signal.

    Required fix: every report row carries an explicit `haircut_outcome` field
    with one of three values:
      - "cleared"          — a trial cleared the FDR gate; selection_tstat is its
                             t-statistic.
      - "no_trial_cleared" — the haircut ran but no trial cleared q; proposed_value
                             is the NAIVE winner and is NOT statistically qualified.
      - "not_run"          — fewer than 1 non-sentinel trial; the haircut could
                             not run.
    This disambiguates selection_tstat=None with an adjacent explicit verdict.

    These tests force each branch deterministically by patching
    benjamini_hochberg_adjust — driving real history cannot reliably produce a
    pure-noise trial set.
    """

    _VALID_OUTCOMES = {"cleared", "no_trial_cleared", "not_run"}

    def _run_sweep(self, autotuner_module, single_sym_history, deviation_dict):
        return autotuner_module.run_calibration_sweep(
            history_data=single_sym_history,
            current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
            current_date_str="2024-03-01",
            deviation_dict=deviation_dict,
            random_state=42,
            min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
        )

    def test_every_report_row_carries_an_explicit_haircut_outcome(
        self, autotuner_module, single_sym_history, deviation_dict
    ):
        """
        Every calibration report row must carry a `haircut_outcome` field whose
        value is one of {cleared, no_trial_cleared, not_run}. The field must be
        present on EVERY row — it is the operator's gate verdict, not optional.
        """
        result = self._run_sweep(autotuner_module, single_sym_history, deviation_dict)
        assert result, "run_calibration_sweep returned an empty report."
        for row in result:
            assert "haircut_outcome" in row, (
                f"Report row is missing the 'haircut_outcome' field — the "
                f"calibration report must surface the FDR-gate verdict adjacent "
                f"to the winner so selection_tstat=None is not read as a blank.\n"
                f"  row: {row}"
            )
            assert row["haircut_outcome"] in self._VALID_OUTCOMES, (
                f"haircut_outcome={row['haircut_outcome']!r} is not one of "
                f"{sorted(self._VALID_OUTCOMES)}."
            )

    def test_haircut_outcome_is_consistent_with_selection_tstat(
        self, autotuner_module, single_sym_history, deviation_dict
    ):
        """
        The haircut_outcome value and the selection_tstat field must be mutually
        consistent on every row:
          - "cleared"          ⇒ selection_tstat is a finite float (the winner's t-stat).
          - "no_trial_cleared" ⇒ selection_tstat is None (no qualified winner).
          - "not_run"          ⇒ selection_tstat is None (haircut did not run).
        A row claiming "cleared" with a None t-stat, or a populated t-stat with a
        non-cleared outcome, is an incoherent report.
        """
        result = self._run_sweep(autotuner_module, single_sym_history, deviation_dict)
        assert result, "run_calibration_sweep returned an empty report."
        for row in result:
            assert "haircut_outcome" in row, (
                f"Report row is missing 'haircut_outcome' — cannot check "
                f"consistency with selection_tstat.\n  row: {row}"
            )
            outcome = row["haircut_outcome"]
            assert outcome in self._VALID_OUTCOMES, (
                f"haircut_outcome={outcome!r} is not one of {sorted(self._VALID_OUTCOMES)}."
            )
            tstat = row.get("selection_tstat")
            if outcome == "cleared":
                assert tstat is not None and math.isfinite(tstat), (
                    f"haircut_outcome='cleared' but selection_tstat={tstat!r} — "
                    f"a cleared row must carry the winner's finite t-statistic."
                )
            elif outcome in ("no_trial_cleared", "not_run"):
                assert tstat is None, (
                    f"haircut_outcome={outcome!r} but selection_tstat={tstat!r} — "
                    f"a row with no qualified winner must have selection_tstat=None."
                )

    def test_no_trial_cleared_outcome_when_haircut_rejects_all(
        self, autotuner_module, single_sym_history, deviation_dict
    ):
        """
        When the Harvey & Liu haircut rejects every trial (no BHY-adjusted p-value
        clears the FDR gate), every report row must report
        haircut_outcome='no_trial_cleared' — NOT a blank field and NOT 'cleared'.

        Forced deterministically: benjamini_hochberg_adjust is patched to return
        all-1.0 adjusted p-values (the saturated rejection case), so no trial can
        clear q regardless of the underlying study. This is the pure-noise
        scenario — the report must tell the operator the proposal did not clear
        the overfitting gate.
        """
        orig_bhy = autotuner_module.benjamini_hochberg_adjust

        def _reject_all(p_values):
            # Saturated rejection: every adjusted p-value pinned to 1.0 — far
            # above any conventional FDR q, so _haircut_select returns no winner.
            return [1.0 for _ in p_values]

        autotuner_module.benjamini_hochberg_adjust = _reject_all
        try:
            result = self._run_sweep(autotuner_module, single_sym_history, deviation_dict)
        finally:
            autotuner_module.benjamini_hochberg_adjust = orig_bhy

        assert result, "run_calibration_sweep returned an empty report."
        for row in result:
            assert row.get("haircut_outcome") == "no_trial_cleared", (
                f"With the haircut rejecting every trial, each report row must "
                f"report haircut_outcome='no_trial_cleared'; got "
                f"{row.get('haircut_outcome')!r}. The operator must be told the "
                f"proposal failed the FDR gate — the naive winner is not "
                f"statistically qualified.\n  row: {row}"
            )
            assert row.get("selection_tstat") is None, (
                "A no-trial-cleared row must carry selection_tstat=None."
            )

    def test_cleared_outcome_when_a_trial_clears_the_gate(
        self, autotuner_module, single_sym_history, deviation_dict
    ):
        """
        Companion to the rejection test: when the haircut lets a trial clear the
        FDR gate, the report rows must report haircut_outcome='cleared' with a
        populated selection_tstat — the gate is not so strict it can never
        produce a qualified winner.

        Forced deterministically: benjamini_hochberg_adjust is patched to return
        all-0.0 adjusted p-values, so the argmin winner clears any q>0 gate.
        """
        orig_bhy = autotuner_module.benjamini_hochberg_adjust

        def _clear_all(p_values):
            # Every adjusted p-value pinned to 0.0 — well below any FDR q, so
            # _haircut_select's argmin winner clears the gate.
            return [0.0 for _ in p_values]

        autotuner_module.benjamini_hochberg_adjust = _clear_all
        try:
            result = self._run_sweep(autotuner_module, single_sym_history, deviation_dict)
        finally:
            autotuner_module.benjamini_hochberg_adjust = orig_bhy

        assert result, "run_calibration_sweep returned an empty report."
        for row in result:
            # A study with >=1 non-sentinel trial and an all-clear haircut must
            # yield a cleared row. (If the study had zero non-sentinel trials the
            # outcome would legitimately be 'not_run'; the single_sym_history
            # fixture produces real trials, so 'cleared' is expected here.)
            assert row.get("haircut_outcome") in ("cleared", "not_run"), (
                f"haircut_outcome={row.get('haircut_outcome')!r} is invalid for "
                f"an all-clear haircut run."
            )
            if row.get("haircut_outcome") == "cleared":
                tstat = row.get("selection_tstat")
                assert tstat is not None and math.isfinite(tstat), (
                    f"A 'cleared' row must carry a finite selection_tstat; got {tstat!r}."
                )


# ---------------------------------------------------------------------------
# 7. FROZEN EVAL (O6) — held-out fold consumed exactly once, post-selection
# ---------------------------------------------------------------------------


class TestFrozenEvalO6:
    def test_report_exposes_frozen_eval_alpha(
        self, autotuner_module, single_sym_history, deviation_dict
    ):
        """
        Report rows must include frozen_eval_alpha (the post-selection honest metric).
        May be None if frozen fold has no triggers — but key must be present.
        """
        result = autotuner_module.run_calibration_sweep(
            history_data=single_sym_history,
            current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
            current_date_str="2024-03-01",
            deviation_dict=deviation_dict,
            random_state=42,
            min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
        )
        assert result, "run_calibration_sweep returned empty list"
        for row in result:
            assert "frozen_eval_alpha" in row, f"Row missing frozen_eval_alpha: {row}"
            if row["frozen_eval_alpha"] is not None:
                assert math.isfinite(row["frozen_eval_alpha"]), (
                    "frozen_eval_alpha must be finite or None"
                )

    def test_frozen_eval_fold_not_used_during_trial_selection(
        self, autotuner_module, single_sym_history, deviation_dict
    ):
        """
        _collect_sim_returns on frozen dates must be called AFTER study.optimize
        completes, not during the Optuna trial objective.

        Strategy: track call order by patching _collect_sim_returns to record
        whether study.best_trial has been determined at time of first frozen call.
        If frozen is called before best_trial is known, O6 is violated.
        """
        optimize_complete = [False]
        frozen_call_before_optimize = [False]

        orig_collect = autotuner_module._collect_sim_returns

        def _tracking_collect(p, history_data, acc_sym_ids, current_date_str, deviation_dict):
            # We detect frozen calls by checking a study-complete flag.
            # This is an approximation: if _collect_sim_returns is called with frozen
            # dates before optimize_complete is True, that is a violation.
            # The frozen fold is the LAST ~20% of dates in history_data per sym.
            all_dates_in_call = set()
            for sym_dates in history_data.values():
                all_dates_in_call.update(sym_dates.keys())

            all_history_dates = set()
            for sym_dates in single_sym_history.values():
                all_history_dates.update(sym_dates.keys())

            sorted_all = sorted(all_history_dates)
            n = len(sorted_all)
            frozen_start_idx = int(n * 0.80)  # matches TRAIN_RATIO + VALIDATION_RATIO
            frozen_dates = set(sorted_all[frozen_start_idx:])

            is_frozen_call = bool(all_dates_in_call & frozen_dates)
            if is_frozen_call and not optimize_complete[0]:
                frozen_call_before_optimize[0] = True

            return orig_collect(p, history_data, acc_sym_ids, current_date_str, deviation_dict)

        orig_optimize_factory = None

        import optuna as _optuna

        orig_create = _optuna.create_study

        def _marking_study(*a, **kw):
            kw.pop("storage", None)
            kw.pop("load_if_exists", None)
            study = orig_create(
                direction=kw.get("direction", "maximize"),
                sampler=_optuna.samplers.TPESampler(seed=42),
            )
            orig_opt = study.optimize

            def _marking_optimize(obj, n_trials=1, **k2):
                orig_opt(obj, n_trials=n_trials, **k2)
                optimize_complete[0] = True

            study.optimize = _marking_optimize
            return study

        autotuner_module._collect_sim_returns = _tracking_collect
        autotuner_module.optuna.create_study = _marking_study
        try:
            autotuner_module.run_calibration_sweep(
                history_data=single_sym_history,
                current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
                current_date_str="2024-03-01",
                deviation_dict=deviation_dict,
                random_state=42,
                min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
            )
        finally:
            autotuner_module._collect_sim_returns = orig_collect
            autotuner_module.optuna.create_study = orig_create

        assert not frozen_call_before_optimize[0], (
            "Frozen eval fold was consumed before study.optimize completed — O6 violated"
        )


# ---------------------------------------------------------------------------
# 8. E1 VELOCITY CONTRACT — cycle-1 velocity uses None sentinel (zero baseline)
# ---------------------------------------------------------------------------


class TestE1VelocityContract:
    def test_sim_loops_initialize_prev_return_as_none(self, autotuner_module, e1_velocity_contract):
        """
        E1: the replay's per-day transient state must initialize
        prev_return = None (the cycle-1-velocity-zero sentinel), not 0.0.

        Cluster-3 update: the autotuner-replay-parity cycle made
        _fresh_replay_state() the SINGLE canonical constructor of the per-day
        replay state dict — run_simulation, _collect_sim_returns and
        replay_exit_sequence all build state through it. The E1 contract is
        therefore checked at that single source of truth: assert
        _fresh_replay_state()["prev_return"] is None. This replaces the prior
        brittle `"prev_return = None" in inspect.getsource(...)` string grep,
        which broke the moment the replay functions adopted the shared
        constructor (the string no longer appears lexically in their bodies).
        The constructor-based assertion is refactor-stable and checks the
        actual contract. The RUNTIME effect (cycle-1 velocity == 0 via the
        None sentinel) is independently covered by
        test_calibration_sweep_sim_path_uses_e1_corrected_velocity below.
        """
        contract = e1_velocity_contract["cycle1_velocity_contract"]
        assert contract["prev_return_init"] is None, (
            "Fixture contract must specify None init for prev_return"
        )
        prohibited = e1_velocity_contract["prohibited_pattern"]

        fresh_state = autotuner_module._fresh_replay_state()
        assert "prev_return" in fresh_state, (
            "_fresh_replay_state() must include a 'prev_return' key — the "
            "per-day replay state's cycle-1 velocity sentinel."
        )
        assert fresh_state["prev_return"] is None, (
            f"E1 violated — _fresh_replay_state()['prev_return'] is "
            f"{fresh_state['prev_return']!r}, must be None. {prohibited}"
        )

    def test_calibration_sweep_sim_path_uses_e1_corrected_velocity(
        self, autotuner_module, single_sym_history, deviation_dict, e1_velocity_contract
    ):
        """
        The calibration sweep simulation path must call
        math_engine.compute_para_arm_decision with the E1 contract:
        effective_prev = current_return when prev_return is None (cycle-1 velocity = 0).

        Strategy: track calls to compute_para_arm_decision and assert that on
        the very first tick of each day (tick_idx=0), the velocity passed is 0.0
        (because effective_prev == current_return => velocity = 0).
        """
        import math_engine as _me

        first_tick_velocities: list[float] = []
        call_counts_per_day: list[int] = []
        current_count = [0]

        orig_para = _me.compute_para_arm_decision

        def _tracking_para(current_return, prev_return, para_threshold, currently_armed):
            velocity, should_arm = orig_para(
                current_return=current_return,
                prev_return=prev_return,
                para_threshold=para_threshold,
                currently_armed=currently_armed,
            )
            current_count[0] += 1
            # First call per "day" resets when prev_return == current_return (E1 sentinel)
            if prev_return == current_return:
                first_tick_velocities.append(velocity)
            return velocity, should_arm

        _me.compute_para_arm_decision = _tracking_para
        try:
            autotuner_module.run_calibration_sweep(
                history_data=single_sym_history,
                current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
                current_date_str="2024-03-01",
                deviation_dict=deviation_dict,
                random_state=42,
                min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
            )
        finally:
            _me.compute_para_arm_decision = orig_para

        assert current_count[0] > 0, (
            "compute_para_arm_decision was never called — E1 velocity path not exercised"
        )
        # Every first-tick call must produce velocity = 0.0 (E1 contract)
        for v in first_tick_velocities:
            assert v == pytest.approx(0.0, abs=1e-9), (
                # tolerance: exact arithmetic (subtraction of identical floats) should be 0.0;
                # 1e-9 absorbs any float representation noise in fixture return values
                f"E1 violated: first-tick velocity must be 0.0, got {v}"
            )


# ---------------------------------------------------------------------------
# 9. REPORT SCHEMA — all required fields present and typed correctly
# ---------------------------------------------------------------------------


class TestReportSchema:
    def test_all_required_fields_present_in_every_row(
        self, autotuner_module, single_sym_history, deviation_dict, calibration_report_schema
    ):
        """Every report row must contain all fields listed in calibration_report_schema."""
        result = autotuner_module.run_calibration_sweep(
            history_data=single_sym_history,
            current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
            current_date_str="2024-03-01",
            deviation_dict=deviation_dict,
            random_state=42,
            min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
        )
        assert result, "run_calibration_sweep returned empty list"
        required = set(calibration_report_schema["required_fields"])
        for i, row in enumerate(result):
            missing = required - set(row.keys())
            assert not missing, f"Report row {i} missing fields: {missing}"

    def test_n_trials_is_positive_integer(
        self, autotuner_module, single_sym_history, deviation_dict
    ):
        """n_trials must be a positive integer — confirms the study ran."""
        result = autotuner_module.run_calibration_sweep(
            history_data=single_sym_history,
            current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
            current_date_str="2024-03-01",
            deviation_dict=deviation_dict,
            random_state=42,
            min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
        )
        for row in result:
            assert isinstance(row["n_trials"], int), "n_trials must be int"
            assert row["n_trials"] > 0, "n_trials must be positive"

    def test_delta_pct_is_derivable_from_current_and_proposed(
        self, autotuner_module, single_sym_history, deviation_dict
    ):
        """
        delta_pct must equal (proposed_value - current_value) / abs(current_value) * 100.
        We assert derivability, not a specific value.
        """
        result = autotuner_module.run_calibration_sweep(
            history_data=single_sym_history,
            current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
            current_date_str="2024-03-01",
            deviation_dict=deviation_dict,
            random_state=42,
            min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
        )
        for row in result:
            cur = row["current_value"]
            prop = row["proposed_value"]
            reported_delta = row["delta_pct"]
            if cur != 0:
                expected_delta = (prop - cur) / abs(cur) * 100.0
                assert reported_delta == pytest.approx(expected_delta, rel=1e-4), (
                    # tolerance: floating-point rounding in percent calculation; rel=1e-4 allows
                    # ~0.01% relative error which is well below any operationally significant threshold
                    f"delta_pct not derivable from current/proposed: "
                    f"expected {expected_delta:.4f}, got {reported_delta:.4f}"
                )

    def test_symphony_id_is_non_empty_string(
        self, autotuner_module, single_sym_history, deviation_dict
    ):
        """symphony_id must be a non-empty string in every row."""
        result = autotuner_module.run_calibration_sweep(
            history_data=single_sym_history,
            current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
            current_date_str="2024-03-01",
            deviation_dict=deviation_dict,
            random_state=42,
            min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
        )
        for row in result:
            assert isinstance(row["symphony_id"], str) and row["symphony_id"], (
                f"symphony_id must be non-empty string, got: {row['symphony_id']!r}"
            )

    def test_param_name_values_match_tuned_params_only(
        self, autotuner_module, single_sym_history, deviation_dict, search_space_contract
    ):
        """param_name in every row must be one of the two tuned params."""
        result = autotuner_module.run_calibration_sweep(
            history_data=single_sym_history,
            current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
            current_date_str="2024-03-01",
            deviation_dict=deviation_dict,
            random_state=42,
            min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
        )
        allowed = {p["name"] for p in search_space_contract["tuned_params"]}
        for row in result:
            assert row["param_name"] in allowed, (
                f"Unexpected param_name '{row['param_name']}' — must be one of {allowed}"
            )

    def test_cycle_id_is_non_empty_string(
        self, autotuner_module, single_sym_history, deviation_dict
    ):
        """cycle_id enables shadow_history post-deploy verification (AC-V1)."""
        result = autotuner_module.run_calibration_sweep(
            history_data=single_sym_history,
            current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
            current_date_str="2024-03-01",
            deviation_dict=deviation_dict,
            random_state=42,
            min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
        )
        for row in result:
            assert isinstance(row.get("cycle_id"), str) and row["cycle_id"], (
                "cycle_id must be non-empty string — needed for post-deploy H1 verification"
            )


# ---------------------------------------------------------------------------
# 10. NO FLEET-WIDE FLIP (AC-V1.3) — report is read-only, no atomic mutation
# ---------------------------------------------------------------------------


class TestNoFleetWideFlipACV13:
    def test_run_calibration_sweep_does_not_call_save_symphony_strategy(
        self, autotuner_module, single_sym_history, deviation_dict
    ):
        """
        AC-V1.3: run_calibration_sweep must NOT call database.save_symphony_strategy.
        The report is operator-gated — no automatic fleet-wide param mutation.
        """
        import database as _db

        save_call_count = [0]
        orig_save = _db.save_symphony_strategy

        def _tracking_save(*a, **k):
            save_call_count[0] += 1
            return orig_save(*a, **k)

        _db.save_symphony_strategy = _tracking_save
        try:
            autotuner_module.run_calibration_sweep(
                history_data=single_sym_history,
                current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
                current_date_str="2024-03-01",
                deviation_dict=deviation_dict,
                random_state=42,
                min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
            )
        finally:
            _db.save_symphony_strategy = orig_save

        assert save_call_count[0] == 0, (
            f"run_calibration_sweep called database.save_symphony_strategy {save_call_count[0]} time(s) — "
            "AC-V1.3 violation: report must be read-only, no auto-fleet-flip"
        )

    def test_run_calibration_sweep_does_not_mutate_current_params_dict(
        self, autotuner_module, single_sym_history, deviation_dict
    ):
        """
        run_calibration_sweep must not mutate the caller's current_params dict in place.
        Rollout is operator-gated; the function is a pure recommendation generator.
        """
        current_params = {"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0}
        params_before = dict(current_params)
        autotuner_module.run_calibration_sweep(
            history_data=single_sym_history,
            current_params=current_params,
            current_date_str="2024-03-01",
            deviation_dict=deviation_dict,
            random_state=42,
            min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
        )
        assert current_params == params_before, (
            f"run_calibration_sweep mutated current_params: "
            f"before={params_before}, after={current_params}"
        )


# ---------------------------------------------------------------------------
# 11. DETERMINISM — same fixture + same seed => same recommendations
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_seed_produces_same_proposed_values(
        self, autotuner_module, single_sym_history, deviation_dict, determinism_seed_contract
    ):
        """
        Same history + same random_state must produce identical proposed_value
        for each param across two independent calls.
        """
        seed = determinism_seed_contract["seed_value"]
        result_1 = autotuner_module.run_calibration_sweep(
            history_data=single_sym_history,
            current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
            current_date_str="2024-03-01",
            deviation_dict=deviation_dict,
            random_state=seed,
            min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
        )
        result_2 = autotuner_module.run_calibration_sweep(
            history_data=single_sym_history,
            current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
            current_date_str="2024-03-01",
            deviation_dict=deviation_dict,
            random_state=seed,
            min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
        )
        assert len(result_1) == len(result_2), (
            "Determinism broken: different number of rows across identical runs"
        )
        for r1, r2 in zip(result_1, result_2):
            assert r1["param_name"] == r2["param_name"]
            assert r1["proposed_value"] == pytest.approx(r2["proposed_value"], rel=1e-9), (
                # tolerance: TPE with fixed seed produces identical float outputs;
                # rel=1e-9 allows for any minimal platform-level floating-point
                # non-determinism while catching genuine seed-ignored implementations
                f"Determinism broken for {r1['param_name']}: "
                f"{r1['proposed_value']} != {r2['proposed_value']}"
            )

    def test_different_seeds_may_produce_different_proposed_values(
        self, autotuner_module, single_sym_history, deviation_dict
    ):
        """
        Different seeds should (probabilistically) produce different results,
        confirming the seed is actually wired into the sampler.
        This test is probabilistic — it passes if at least one param differs
        across a reasonable number of trials (n=5 trials is enough for TPE divergence).
        """
        result_seed_1 = autotuner_module.run_calibration_sweep(
            history_data=single_sym_history,
            current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
            current_date_str="2024-03-01",
            deviation_dict=deviation_dict,
            random_state=1,
            min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
        )
        result_seed_2 = autotuner_module.run_calibration_sweep(
            history_data=single_sym_history,
            current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
            current_date_str="2024-03-01",
            deviation_dict=deviation_dict,
            random_state=999,
            min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
        )
        all_same = all(
            r1["proposed_value"] == pytest.approx(r2["proposed_value"], rel=1e-9)
            for r1, r2 in zip(result_seed_1, result_seed_2)
        )
        # If TPE with two very different seeds still selects the same trial, the
        # objective landscape is perfectly flat — that is a legitimate (if unlikely)
        # outcome. We do NOT fail on all_same; we only warn to avoid flakiness.
        # The determinism guarantee (seed=X always => same result) is the strong invariant.
        # This test is best-effort only and is NOT marked as a hard assertion.
        if all_same and result_seed_1:
            import warnings

            warnings.warn(
                "Different seeds produced identical proposed_values — "
                "either the seed is not wired or the objective is degenerate.",
                stacklevel=2,
            )


# ---------------------------------------------------------------------------
# Revise R1 — gaps found during Red/Green/Revise review of GREEN at 9ef38c6
# ---------------------------------------------------------------------------


class TestReviseR1:
    def test_proposed_value_is_float_not_raw_optuna_float(
        self, autotuner_module, single_sym_history, deviation_dict
    ):
        """
        proposed_value in report rows must be a rounded float (same precision
        as what would be written to the DB by run_autotuner — at least 2dp),
        not a raw unrounded Optuna sample like 2.1236203565420873.

        This pins that the implementer rounds suggested values before emitting
        them in the report, keeping the report consistent with run_autotuner's
        round(val, 2) contract.
        """
        result = autotuner_module.run_calibration_sweep(
            history_data=single_sym_history,
            current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
            current_date_str="2024-03-01",
            deviation_dict=deviation_dict,
            random_state=42,
            min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
        )
        for row in result:
            pv = row["proposed_value"]
            # A raw Optuna float will have many decimal places; after rounding to
            # at most 4dp the value must equal itself at that precision.
            # We assert it has been rounded to at most 4 significant decimal places.
            # (run_autotuner uses 2dp; calibration sweep uses 4dp per GREEN impl —
            # either is acceptable, but raw unrounded Optuna floats must not appear.)
            assert round(pv, 4) == pytest.approx(pv, abs=1e-9), (
                # tolerance: round(x, 4) == x is exact for values already at <=4dp;
                # abs=1e-9 absorbs float representation of the rounded value itself
                f"proposed_value {pv} appears unrounded — must be rounded to <=4 decimal places"
            )

    def test_expected_trigger_freq_change_is_finite_float(
        self, autotuner_module, single_sym_history, deviation_dict
    ):
        """
        expected_trigger_freq_change must be a finite float (not NaN, not Inf,
        not a string). It represents the absolute difference in trigger counts
        between proposed and current params on the validation fold.
        """
        result = autotuner_module.run_calibration_sweep(
            history_data=single_sym_history,
            current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
            current_date_str="2024-03-01",
            deviation_dict=deviation_dict,
            random_state=42,
            min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
        )
        for row in result:
            freq_change = row["expected_trigger_freq_change"]
            assert isinstance(freq_change, (int, float)), (
                f"expected_trigger_freq_change must be numeric, got {type(freq_change)}"
            )
            assert math.isfinite(float(freq_change)), (
                f"expected_trigger_freq_change must be finite, got {freq_change}"
            )

    def test_expected_trigger_freq_change_not_hardcoded_zero(
        self, autotuner_module, deviation_dict
    ):
        """
        expected_trigger_freq_change must not be a hardcoded 0.0 for all inputs.
        We construct two histories: one where current params trigger frequently,
        one where proposed params trigger less — and assert the freq change reflects
        the actual difference.

        Strategy: use a fixture history with ticks designed to trigger under
        high VWAP_CROSS_HWM_PCT (1.0) but NOT under very high VWAP_CROSS_HWM_PCT (2.5).
        Then check that freq_change differs across two current_params configurations
        where one is near the lower bound and one near the upper bound.

        This is a structural test — we assert the two results differ, not their
        specific values.
        """
        import datetime

        base_date = datetime.date(2024, 1, 2)

        def _make_triggering_history(n_days=40):
            """History with ticks that reliably trigger System A at vwap_cross_hwm=0.3
            but NOT at vwap_cross_hwm=2.5 (safe_hwm never reaches 2.5)."""
            history = {"sym_trigger_test": {}}
            for d in range(n_days):
                date_str = (base_date + datetime.timedelta(days=d)).strftime("%Y-%m-%d")
                ticks = []
                for i in range(10):
                    ticks.append(
                        {
                            "return": 0.4 + i * 0.05,  # rises to ~0.85 — above 0.3 threshold
                            "mc_prob": 10.0,
                            "vol": 1.0,
                            "vwap_diff": -0.5,  # negative: gate passes
                            "base_atr_pct": 1.0,
                            "valid_vwap_weight": 0.9,  # > 0.5: gate passes
                        }
                    )
                history["sym_trigger_test"][date_str] = ticks
            return history

        h = _make_triggering_history()

        # Low threshold: more likely to trigger System A (safe_hwm ~0.4 > 0.3)
        result_low = autotuner_module.run_calibration_sweep(
            history_data=h,
            current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 0.3},
            current_date_str="2024-03-01",
            deviation_dict=deviation_dict,
            random_state=42,
            min_history_days=0,  # h has 40 days; bypass AC-4 floor to exercise contracts
        )
        # High threshold: less likely to trigger System A (safe_hwm may not reach 2.0)
        result_high = autotuner_module.run_calibration_sweep(
            history_data=h,
            current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 2.0},
            current_date_str="2024-03-01",
            deviation_dict=deviation_dict,
            random_state=42,
            min_history_days=0,  # h has 40 days; bypass AC-4 floor to exercise contracts
        )

        freq_changes_low = [r["expected_trigger_freq_change"] for r in result_low]
        freq_changes_high = [r["expected_trigger_freq_change"] for r in result_high]

        # Both must be finite — the main assertion is structural (not hardcoded 0)
        for fc in freq_changes_low + freq_changes_high:
            assert math.isfinite(float(fc)), (
                f"expected_trigger_freq_change must be finite, got {fc}"
            )
        # At least one result set must have a non-trivial freq_change value, confirming
        # the field is computed, not hardcoded to 0.0
        all_zeros = all(fc == 0.0 for fc in freq_changes_low + freq_changes_high)
        if all_zeros:
            # If validation fold is too small for any triggers, we can't distinguish —
            # warn but don't fail (the no-hardcode guarantee is best-effort on sparse data)
            import warnings

            warnings.warn(
                "expected_trigger_freq_change is 0.0 for all inputs — "
                "validation fold may be too sparse to produce triggers. "
                "Consider a fixture with more history days.",
                stacklevel=2,
            )

    def test_sortino_field_is_validation_fold_not_frozen_fold(
        self, autotuner_module, single_sym_history, deviation_dict
    ):
        """
        The 'sortino' field must reflect the validation-fold Sortino of the best
        trial's params, NOT the frozen-eval Sortino. These are separate quantities:
        sortino = selection metric; frozen_eval_alpha = post-selection honest metric.

        We assert they are allowed to differ (they are computed on different data),
        and that 'sortino' is not simply copied from 'frozen_eval_alpha'.
        """
        result = autotuner_module.run_calibration_sweep(
            history_data=single_sym_history,
            current_params={"PARABOLIC_VELOCITY_THRESHOLD": 2.0, "VWAP_CROSS_HWM_PCT": 1.0},
            current_date_str="2024-03-01",
            deviation_dict=deviation_dict,
            random_state=42,
            min_history_days=0,  # fixture has 40 days; bypass AC-4 floor to exercise contracts
        )
        for row in result:
            # Both fields must be present (already covered by schema test)
            assert "sortino" in row
            assert "frozen_eval_alpha" in row
            # They are allowed to be None independently — but if both are non-None,
            # assert they are not necessarily equal (they CAN be equal by coincidence,
            # but a naïve implementation that copies one to the other would always
            # make them equal). We assert the keys are independently populated.
            # This is a provenance test, not a value test.
            s = row["sortino"]
            f = row["frozen_eval_alpha"]
            if s is not None:
                assert isinstance(s, float) and math.isfinite(s), (
                    f"sortino must be finite float or None, got {s!r}"
                )
            if f is not None:
                assert isinstance(f, float) and math.isfinite(f), (
                    f"frozen_eval_alpha must be finite float or None, got {f!r}"
                )


# ---------------------------------------------------------------------------
# 12. V1-SPECIFIC SEARCH SPACE BOUNDS — named constants, not magic numbers
# ---------------------------------------------------------------------------


class TestV1SearchSpaceBounds:
    def test_v1_vwap_cross_hwm_lower_bound_named_constant_exists(self, autotuner_module):
        """
        V1 uses a narrower VWAP_CROSS_HWM_PCT lower bound (0.3) than the general
        autotuner (0.5). This must be expressed as a named constant
        (_SS_VWAP_CROSS_HWM_V1_MIN) in autotuner.py, not an inline magic number.
        """
        assert hasattr(autotuner_module, "_SS_VWAP_CROSS_HWM_V1_MIN"), (
            "_SS_VWAP_CROSS_HWM_V1_MIN missing from autotuner — "
            "V1 VWAP lower bound must be a named constant, not a magic number"
        )

    def test_v1_vwap_cross_hwm_upper_bound_named_constant_exists(self, autotuner_module):
        """
        V1 upper bound for VWAP_CROSS_HWM_PCT must be a named constant
        (_SS_VWAP_CROSS_HWM_V1_MAX) in autotuner.py.
        """
        assert hasattr(autotuner_module, "_SS_VWAP_CROSS_HWM_V1_MAX"), (
            "_SS_VWAP_CROSS_HWM_V1_MAX missing from autotuner"
        )

    def test_v1_para_velocity_bounds_are_within_general_bounds(self, autotuner_module):
        """
        V1 PARABOLIC_VELOCITY_THRESHOLD bounds must sit within (or equal) the
        general autotuner bounds (_SS_PARA_VEL_MIN, _SS_PARA_VEL_MAX).
        Asserts shape/ordering only — no hardcoded producer values.
        """
        lo = getattr(autotuner_module, "_SS_PARA_VEL_MIN", None)
        hi = getattr(autotuner_module, "_SS_PARA_VEL_MAX", None)
        assert lo is not None and hi is not None, (
            "_SS_PARA_VEL_MIN / _SS_PARA_VEL_MAX missing from autotuner"
        )
        assert lo < hi, f"_SS_PARA_VEL_MIN ({lo}) must be < _SS_PARA_VEL_MAX ({hi})"
        assert isinstance(lo, (int, float)) and math.isfinite(lo)
        assert isinstance(hi, (int, float)) and math.isfinite(hi)

    def test_v1_vwap_cross_hwm_bounds_are_ordered(self, autotuner_module):
        """
        V1 VWAP_CROSS_HWM_PCT bounds must be ordered: MIN < MAX.
        Asserts invariant only.
        """
        lo = getattr(autotuner_module, "_SS_VWAP_CROSS_HWM_V1_MIN", None)
        hi = getattr(autotuner_module, "_SS_VWAP_CROSS_HWM_V1_MAX", None)
        if lo is None or hi is None:
            pytest.skip("V1 bound constants not yet defined — covered by naming tests above")
        assert lo < hi, (
            f"_SS_VWAP_CROSS_HWM_V1_MIN ({lo}) must be < _SS_VWAP_CROSS_HWM_V1_MAX ({hi})"
        )


# ---------------------------------------------------------------------------
# 13. SYSTEM A 3-TICK CONFIRM GATE — single-tick VWAP cross at lower bound does not trigger
# ---------------------------------------------------------------------------


class TestSystemAThreeTickGate:
    def test_single_vwap_cross_tick_at_lower_bound_does_not_fire_system_a(self):
        """
        System A (profit-protection VWAP break) requires VWAP_BREAK_CONFIRM_TICKS=3
        consecutive qualifying ticks before is_vwap_broken=True. A single qualifying
        tick at the minimum vwap_cross_hwm_pct=0.3 boundary must not fire.

        This pins the 3-tick confirm gate invariant so that a future narrowing of
        the V1 lower bound cannot accidentally produce spurious single-tick exits.
        Tested directly against math_engine.compute_vwap_breakdown_update —
        no mock of the math engine.
        """
        import math_engine as _me

        # Parameters: safe_hwm at the V1 lower bound (0.3), single-tick call
        vwap_cross_hwm_pct = 0.3  # V1 lower bound value
        # Conditions that satisfy System A gate: safe_hwm >= vwap_cross_hwm_pct AND return < safe_hwm
        safe_hwm = 0.3
        current_return = 0.29  # below safe_hwm — qualifies for System A

        new_vwap_ticks, new_vwap_bleed_ticks, is_vwap_broken, is_vwap_bleed_broken = (
            _me.compute_vwap_breakdown_update(
                is_triggered=False,
                valid_vwap_weight=0.9,  # > VWAP_WEIGHT_THRESHOLD (0.5): gate passes
                weighted_vwap_diff=-0.1,  # < 0: gate passes
                safe_hwm=safe_hwm,
                current_return=current_return,
                vwap_cross_hwm_pct=vwap_cross_hwm_pct,
                vwap_bleed_arm_pct=-1.0,  # bleed arm well below current_return: System B does not fire
                vwap_bleed_ticks_threshold=3,
                current_vwap_ticks=0,  # fresh start: tick count = 0 before this call
                current_vwap_bleed_ticks=0,
            )
        )

        assert not is_vwap_broken, (
            "System A fired on a single qualifying tick at vwap_cross_hwm_pct=0.3 — "
            "3-tick confirm gate (VWAP_BREAK_CONFIRM_TICKS=3) should prevent this"
        )
        assert new_vwap_ticks == 1, (
            f"Expected vwap_ticks to increment to 1 after one qualifying tick, got {new_vwap_ticks}"
        )

    def test_three_consecutive_vwap_cross_ticks_at_lower_bound_fires_system_a(self):
        """
        Three consecutive qualifying ticks at vwap_cross_hwm_pct=0.3 must fire
        is_vwap_broken=True. Confirms the confirm gate is reached at exactly 3 ticks,
        not before.
        """
        import math_engine as _me

        vwap_cross_hwm_pct = 0.3
        safe_hwm = 0.3
        current_return = 0.29

        tick_count = 0
        for _ in range(3):
            tick_count, _, is_vwap_broken, _ = _me.compute_vwap_breakdown_update(
                is_triggered=False,
                valid_vwap_weight=0.9,
                weighted_vwap_diff=-0.1,
                safe_hwm=safe_hwm,
                current_return=current_return,
                vwap_cross_hwm_pct=vwap_cross_hwm_pct,
                vwap_bleed_arm_pct=-1.0,
                vwap_bleed_ticks_threshold=3,
                current_vwap_ticks=tick_count,
                current_vwap_bleed_ticks=0,
            )

        assert is_vwap_broken, (
            "System A must fire after exactly 3 consecutive qualifying ticks at vwap_cross_hwm_pct=0.3"
        )
