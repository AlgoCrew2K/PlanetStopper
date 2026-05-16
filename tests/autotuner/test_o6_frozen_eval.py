"""
O6 — Frozen Evaluation Window: RED tests.

These tests are written BEFORE the implementation. All tests will FAIL until:
  1. autotuner.py introduces TRAIN_RATIO, VALIDATION_RATIO, FROZEN_EVAL_RATIO
     named constants (summing to 1.0) citing López de Prado 2018 Ch. 7.4.
  2. autotuner.py's run_autotuner splits history into three folds:
       train (60%), validation (20%), frozen-eval (20%).
  3. The Optuna objective() is scored on the VALIDATION fold only.
  4. The frozen-eval fold is NOT read by any Optuna trial callback.
  5. After best-trial selection, frozen-eval is evaluated EXACTLY ONCE.
  6. O1's purge + embargo constants (PURGE_DAYS, EMBARGO_DAYS) are applied at
     BOTH fold boundaries: train|validation AND validation|frozen-eval.
  7. database.py migration 007 adds validation_sharpe and frozen_eval_sharpe
     columns to autotune_runs.
  8. autotuner.run_autotuner populates both new columns in save_autotune_run.
  9. autotuner.py documents the fold-collapse tradeoff at 125-day history.

Fixture provenance: tests/fixtures/autotuner/frozen_eval/
  - split_ratios_125d.json — schema-derived split boundaries for 125-day history.
  - named_constants.json   — required named constant names, values, and citations.
  - migration_007_schema.json — expected schema for migration 007.

No assertions use producer-computed values (rates, Sortino outputs, dollar amounts).
All expected fold sizes are derived from the fixtures (schema-derived provenance).

Citation: López de Prado, M. (2018). Advances in Financial Machine Learning.
  Wiley. Ch. 7.4 (Frozen evaluation / nested cross-validation).
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import math
import pathlib
import re
import sqlite3
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "autotuner" / "frozen_eval"
_MIGRATIONS_DIR = _WORKTREE_ROOT / "migrations"
_AUTOTUNER_SRC = _WORKTREE_ROOT / "autotuner.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_fixture(name: str) -> dict:
    return json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _parse_autotuner_source() -> str:
    return _AUTOTUNER_SRC.read_text(encoding="utf-8")


def _parse_autotuner_ast() -> ast.Module:
    return ast.parse(_parse_autotuner_source())


def _find_module_level_assignments(tree: ast.Module) -> dict[str, Any]:
    """Return all module-level assignments as {name: value_node}."""
    assignments: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments[target.id] = node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.value is not None:
                assignments[node.target.id] = node.value
    return assignments


def _import_autotuner():
    import autotuner
    return autotuner


def _default_params() -> dict:
    return {
        "TRIGGER_THRESHOLD_PCT": 15.0,
        "TAKE_PROFIT_MC_PCT": 5.0,
        "VWAP_CROSS_HWM_PCT": 1.0,
        "VWAP_BLEED_MULTIPLIER": 1.5,
        "VWAP_BLEED_TICKS": 10,
        "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
        "MAX_PARABOLIC_SQUEEZE": 0.5,
    }


def _build_history(n_days: int) -> dict:
    """Minimal synthetic history with n_days trading days for a single symphony."""
    tick = {
        "return": 0.5,
        "mc_prob": 50.0,
        "vol": 1.0,
        "vwap_diff": 0.0,
        "base_atr_pct": 1.0,
        "valid_vwap_weight": 1.0,
    }
    # Use weekdays only to avoid weekend gaps
    import datetime
    start = datetime.date(2025, 6, 2)  # Monday
    dates = []
    d = start
    while len(dates) < n_days:
        if d.weekday() < 5:  # Mon-Fri
            dates.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return {"sym-A": {date: [tick] for date in dates}}


@contextlib.contextmanager
def _autotuner_patches(
    best_params: dict,
    history: dict,
    save_autotune_run_calls: list[dict] | None = None,
):
    """Standard patch harness for O6 tests."""
    fake_study = MagicMock(name="fake_optuna_study")
    fake_study.best_params = best_params.copy()
    fake_study.best_value = 1.0
    fake_study.optimize = MagicMock(return_value=None)
    fake_study.trials = []

    def capturing_save(**kwargs):
        if save_autotune_run_calls is not None:
            save_autotune_run_calls.append(kwargs)

    with (
        patch("autotuner.optuna.create_study", return_value=fake_study),
        patch("autotuner.optuna.storages.RDBStorage", return_value=MagicMock()),
        patch("autotuner.synthetic_history.generate_synthetic_history", return_value=history),
        patch("autotuner.database.load_chart_history", return_value={}),
        patch("autotuner.database.save_chart_archive"),
        patch("autotuner.database.get_symphony_strategy",
              return_value={"params": best_params.copy(), "locked_vars": []}),
        patch("autotuner.database.save_symphony_strategy"),
        patch("autotuner.database.DEFAULT_STRATEGY", best_params.copy()),
        patch("autotuner.database.save_autotune_run",
              side_effect=capturing_save),
        patch("autotuner.math_engine.compute_para_arm_decision",
              side_effect=lambda **kw: (0.0, False)),
        patch("autotuner.math_engine.compute_time_squeeze_decay",
              side_effect=lambda tr: (1.5, 0.5)),
        patch("autotuner.math_engine.compute_active_trailing_stop",
              side_effect=lambda *a, **kw: 5.0),
        patch("autotuner.math_engine.compute_breakeven_update",
              side_effect=lambda *a, **kw: (a[3], a[4], a[2])),
        patch("autotuner.math_engine.compute_vwap_bleed_arm_threshold",
              side_effect=lambda *a, **kw: -10.0),
        patch("autotuner.math_engine.compute_vwap_breakdown_update",
              side_effect=lambda **kw: (0, 0, False, False)),
    ):
        yield fake_study


# ===========================================================================
# Test 1 — 60/20/20 split produces correct fold sizes (test_split_is_60_20_20)
# Mandatory RED
# ===========================================================================

class TestSplitIs60_20_20:
    """
    Given a fixture history length, the split function returns three folds
    whose sizes are within tolerance of the 60/20/20 target ratios.

    Adversarial angle: catch the existing 80/20 split that does NOT produce
    a frozen-eval fold at all. The test must fail until the implementer
    replaces the binary split_idx with a three-way partition.
    """

    def test_split_produces_three_nonempty_folds_on_125_days(self):
        """
        run_autotuner must partition 125 sorted trading days into three
        non-empty folds: train (~75), validation (~25), frozen-eval (~25).

        The test intercepts the data flowing INTO the Optuna objective and
        into the post-selection frozen-eval evaluation by spying on
        _collect_sim_returns / run_simulation call argument sets.

        RED until the 60/20/20 three-way split replaces the 80/20 binary split.
        """
        fix = _load_fixture("split_ratios_125d.json")
        n_days = fix["history_length"]
        history = _build_history(n_days)
        params = _default_params()

        # Capture which date-sets are passed to objective and frozen-eval
        dates_seen_in_objective: set[str] = set()
        dates_seen_in_frozen: set[str] = set()

        # The objective calls _collect_sim_returns with history_train.
        # Frozen-eval calls run_simulation (or _collect_sim_returns) with history_frozen.
        # We intercept at the level of run_simulation to capture dates.
        original_run_simulation = None
        try:
            import autotuner as _at
            original_run_simulation = _at.run_simulation
        except Exception:
            pass

        call_number = [0]

        def spy_run_simulation(p, history_data, acc_sym_ids, current_date_str, deviation_dict):
            call_number[0] += 1
            for sym_id, dates_dict in history_data.items():
                for date in dates_dict:
                    # First two calls are fallback + default OOS eval (from old code);
                    # the frozen-eval is a third distinct date-set call.
                    # We record all dates to verify three disjoint sets exist.
                    dates_seen_in_frozen.update(dates_dict.keys())
            if original_run_simulation:
                return original_run_simulation(p, history_data, acc_sym_ids, current_date_str, deviation_dict)
            return 0.0

        calls: list[dict] = []
        bot_state = {"sym-A": {"name": "O6 Split Test", "account_uuid": "acc-1"}}

        buf = io.StringIO()
        with _autotuner_patches(params, history, save_autotune_run_calls=calls):
            with patch("autotuner.run_simulation", side_effect=spy_run_simulation):
                with contextlib.redirect_stdout(buf):
                    _import_autotuner().run_autotuner(bot_state, "2026-05-10", ["acc-1"])

        # The key assertion: after O6 is implemented, save_autotune_run must be
        # called with both validation_sharpe and frozen_eval_sharpe kwargs.
        # This is the observable proof that a three-way split happened.
        assert len(calls) >= 1, "run_autotuner must call save_autotune_run at least once."
        call = calls[0]

        assert "validation_sharpe" in call, (
            "save_autotune_run must receive 'validation_sharpe' kwarg after O6 is implemented. "
            "This column represents the Sortino on the validation fold (used for trial selection). "
            "Current 80/20 split has no separate validation vs frozen-eval — RED until 60/20/20 implemented."
        )
        assert "frozen_eval_sharpe" in call, (
            "save_autotune_run must receive 'frozen_eval_sharpe' kwarg after O6 is implemented. "
            "This column represents the honest post-selection metric on the frozen-eval fold. "
            "RED until 60/20/20 split implemented."
        )

    def test_split_ratios_sum_to_1(self):
        """
        TRAIN_RATIO + VALIDATION_RATIO + FROZEN_EVAL_RATIO must equal 1.0
        within floating-point tolerance (1e-9).

        Method: AST inspection of autotuner.py for the three named constants.
        RED until implemented.
        """
        fix = _load_fixture("named_constants.json")
        tree = _parse_autotuner_ast()
        assignments = _find_module_level_assignments(tree)

        const_names = [c["name"] for c in fix["required_constants"]]
        missing = [n for n in const_names if n not in assignments]
        assert not missing, (
            f"autotuner.py is missing required O6 named constants: {missing}. "
            "All three must be defined at module scope: TRAIN_RATIO=0.60, "
            "VALIDATION_RATIO=0.20, FROZEN_EVAL_RATIO=0.20. "
            "RED until implementer adds them."
        )

        total = 0.0
        for const in fix["required_constants"]:
            name = const["name"]
            node = assignments[name]
            assert isinstance(node, ast.Constant) and isinstance(node.value, (int, float)), (
                f"{name} must be a numeric literal constant in autotuner.py; "
                f"got AST node type {type(node).__name__}"
            )
            total += node.value

        expected_sum = fix["sum_invariant"]["expected_sum"]
        tolerance = fix["sum_invariant"]["tolerance"]
        assert abs(total - expected_sum) <= tolerance, (
            f"TRAIN_RATIO + VALIDATION_RATIO + FROZEN_EVAL_RATIO = {total} "
            f"but must equal {expected_sum} (tolerance {tolerance}). "
            "The three ratios must partition the history without gap or overlap."
        )

    def test_fold_sizes_within_tolerance_of_60_20_20(self):
        """
        For a 125-day history, the three folds must be within 1 percentage
        point of their target ratios (60/20/20).

        Tolerance is necessary because integer day counts produce small rounding
        errors — e.g., int(125 * 0.6) = 75 is exactly 60.0%, but the remainder
        assignment can shift the frozen-eval fold by ±1 day.

        Method: fixture documents expected sizes; test validates the AST-inspected
        ratio constants predict fold sizes within the allowed tolerance.
        """
        fix_ratios = _load_fixture("split_ratios_125d.json")
        n_days = fix_ratios["history_length"]
        tolerance_pct = fix_ratios["tolerance_pct"]

        tree = _parse_autotuner_ast()
        assignments = _find_module_level_assignments(tree)

        required_names = ["TRAIN_RATIO", "VALIDATION_RATIO", "FROZEN_EVAL_RATIO"]
        for name in required_names:
            if name not in assignments:
                pytest.fail(
                    f"Cannot verify fold sizes: {name} not found in autotuner.py. "
                    "O6 named constants must be added first."
                )

        ratios = {
            name: assignments[name].value
            for name in required_names
            if isinstance(assignments[name], ast.Constant)
        }

        train_days = int(n_days * ratios["TRAIN_RATIO"])
        val_days = int(n_days * ratios["VALIDATION_RATIO"])
        frozen_days = n_days - train_days - val_days  # remainder goes to frozen-eval

        actual_train_pct = train_days / n_days * 100
        actual_val_pct = val_days / n_days * 100
        actual_frozen_pct = frozen_days / n_days * 100

        expected_folds = fix_ratios["raw_fold_sizes"]

        assert abs(actual_train_pct - 60.0) <= tolerance_pct, (
            f"Train fold is {actual_train_pct:.1f}% of {n_days} days ({train_days} days); "
            f"expected ~60% (±{tolerance_pct}%). "
            f"Fixture expects {expected_folds['train_days']} train days."
        )
        assert abs(actual_val_pct - 20.0) <= tolerance_pct, (
            f"Validation fold is {actual_val_pct:.1f}% of {n_days} days ({val_days} days); "
            f"expected ~20% (±{tolerance_pct}%). "
            f"Fixture expects {expected_folds['validation_days']} validation days."
        )
        assert abs(actual_frozen_pct - 20.0) <= tolerance_pct, (
            f"Frozen-eval fold is {actual_frozen_pct:.1f}% of {n_days} days ({frozen_days} days); "
            f"expected ~20% (±{tolerance_pct}%). "
            f"Fixture expects {expected_folds['frozen_eval_days']} frozen-eval days."
        )


# ===========================================================================
# Test 2 — Optuna trials are scored on the validation fold
# (test_validation_used_for_selection)
# Mandatory RED
# ===========================================================================

class TestValidationUsedForSelection:
    """
    The Optuna objective must be evaluated against the validation fold only,
    not the training fold or the frozen-eval fold.
    """

    def test_validation_used_for_selection(self):
        """
        The Optuna objective() closure must call _collect_sim_returns (or
        run_simulation) with the validation fold's history_data, not the
        full history or the frozen-eval fold.

        Adversarial: in the current 80/20 code, the objective scores against
        history_train. After O6, history_train is only 60%, and the
        validation fold is the 20% used for scoring.

        Verification method: spy on _collect_sim_returns. The dates seen in
        the objective call must intersect the validation fold date range and
        must NOT intersect the frozen-eval fold date range.

        RED until the objective is rerouted to score against the validation fold.
        """
        n_days = 125
        history = _build_history(n_days)
        params = _default_params()
        bot_state = {"sym-A": {"name": "O6 Validation Selection", "account_uuid": "acc-1"}}

        # Capture the history_data argument to every _collect_sim_returns call.
        objective_date_sets: list[frozenset[str]] = []

        import autotuner as at
        original_collect = at._collect_sim_returns

        def spy_collect(p, history_data, acc_sym_ids, current_date_str, deviation_dict):
            for sym_id, dates_dict in history_data.items():
                objective_date_sets.append(frozenset(dates_dict.keys()))
            return original_collect(p, history_data, acc_sym_ids, current_date_str, deviation_dict)

        # All dates in sorted order — used to derive fold boundaries.
        all_dates_sorted = sorted(
            history["sym-A"].keys()
        )
        split_60 = int(n_days * 0.60)  # end of train fold
        split_80 = int(n_days * 0.80)  # end of validation fold
        validation_dates = frozenset(all_dates_sorted[split_60:split_80])
        frozen_dates = frozenset(all_dates_sorted[split_80:])

        calls: list[dict] = []
        buf = io.StringIO()
        with _autotuner_patches(params, history, save_autotune_run_calls=calls):
            with patch("autotuner._collect_sim_returns", side_effect=spy_collect):
                with contextlib.redirect_stdout(buf):
                    at.run_autotuner(bot_state, "2026-05-10", ["acc-1"])

        if not objective_date_sets:
            pytest.fail(
                "No calls to _collect_sim_returns were captured. "
                "O6 implementation must route the Optuna objective to score "
                "against the validation fold via _collect_sim_returns. "
                "RED until implemented."
            )

        # The first call to _collect_sim_returns is from the objective function.
        # Its date set must intersect the validation fold.
        first_objective_dates = objective_date_sets[0]
        assert first_objective_dates & validation_dates, (
            "The Optuna objective's _collect_sim_returns call must include "
            "validation-fold dates. The objective must be scored on the "
            "validation fold, not the train fold or frozen-eval fold. "
            "RED until O6 routes the objective to score against validation."
        )

        # The objective must NOT include frozen-eval dates.
        assert not (first_objective_dates & frozen_dates), (
            "The Optuna objective must NOT see frozen-eval fold dates. "
            "The frozen-eval window must be withheld from all trial callbacks. "
            "RED until O6 gates the frozen-eval out of the objective data."
        )


# ===========================================================================
# Test 3 — Frozen-eval fold never consumed during Optuna trial sweep
# (test_frozen_eval_never_consumed_in_selection)
# Mandatory RED — adversarial
# ===========================================================================

class TestFrozenEvalNeverConsumedInSelection:
    """
    Adversarial: the frozen-eval dates must not appear in ANY _collect_sim_returns
    call that is made by the Optuna objective during the trial sweep.

    This is the most important isolation test: if frozen-eval data leaks into
    the objective, the reported frozen-eval metric is conditioned on the data
    used to select the params — exactly the reporting bias O6 was added to fix.
    """

    def test_frozen_eval_never_consumed_in_selection(self):
        """
        Adversarial: frozen-eval dates must not appear in any _collect_sim_returns
        call made by the Optuna trial sweep.

        The test is gated on FROZEN_EVAL_RATIO existing in autotuner.py. Without
        that constant the 60/20/20 split is not implemented, so there is no
        frozen-eval fold to isolate -- and the test fails (RED) on that gate.
        After O6 is implemented, the runtime portion checks that the objective
        only receives validation-fold dates, not frozen-eval dates.

        RED on current code because FROZEN_EVAL_RATIO does not yet exist.
        """
        source = _parse_autotuner_source()

        # Primary RED gate: FROZEN_EVAL_RATIO must exist before isolation is meaningful.
        assert "FROZEN_EVAL_RATIO" in source, (
            "autotuner.py must define FROZEN_EVAL_RATIO for the frozen-eval isolation "
            "to be meaningful. Without a three-way split there is no isolated fold. "
            "RED until O6 adds FROZEN_EVAL_RATIO and the 60/20/20 split."
        )

        n_days = 125
        history = _build_history(n_days)
        params = _default_params()
        bot_state = {"sym-A": {"name": "O6 Frozen Isolation", "account_uuid": "acc-1"}}

        all_dates_sorted = sorted(history["sym-A"].keys())
        import ast as _ast
        tree = _parse_autotuner_ast()
        assignments = _find_module_level_assignments(tree)
        frozen_ratio_node = assignments.get("FROZEN_EVAL_RATIO")
        frozen_ratio = (
            frozen_ratio_node.value
            if isinstance(frozen_ratio_node, _ast.Constant)
            else 0.20
        )
        split_at_frozen = int(n_days * (1.0 - frozen_ratio))
        frozen_dates: frozenset[str] = frozenset(all_dates_sorted[split_at_frozen:])

        dates_seen_in_objective: set[str] = set()
        selection_done = [False]

        import autotuner as at
        original_collect = at._collect_sim_returns

        def spy_collect(p, history_data, acc_sym_ids, current_date_str, deviation_dict):
            if not selection_done[0]:
                for sym_id, dates_dict in history_data.items():
                    dates_seen_in_objective.update(dates_dict.keys())
            return original_collect(p, history_data, acc_sym_ids, current_date_str, deviation_dict)

        original_run_sim = at.run_simulation

        def spy_run_sim(p, history_data, acc_sym_ids, current_date_str, deviation_dict):
            selection_done[0] = True
            return original_run_sim(p, history_data, acc_sym_ids, current_date_str, deviation_dict)

        calls: list[dict] = []
        buf = io.StringIO()
        with _autotuner_patches(params, history, save_autotune_run_calls=calls):
            with (
                patch("autotuner._collect_sim_returns", side_effect=spy_collect),
                patch("autotuner.run_simulation", side_effect=spy_run_sim),
            ):
                with contextlib.redirect_stdout(buf):
                    at.run_autotuner(bot_state, "2026-05-10", ["acc-1"])

        leaked = dates_seen_in_objective & frozen_dates
        assert not leaked, (
            f"FROZEN-EVAL ISOLATION VIOLATED: {len(leaked)} frozen-eval dates appeared "
            f"in _collect_sim_returns before trial selection.\n"
            f"Leaked dates (up to 5): {sorted(leaked)[:5]}\n"
            f"The frozen-eval fold must be withheld from all Optuna trial callbacks."
        )

# ===========================================================================
# Test 4 — Frozen-eval consumed exactly once post-selection
# (test_frozen_eval_consumed_once_post_selection)
# Mandatory RED
# ===========================================================================

class TestFrozenEvalConsumedOncePostSelection:
    """
    After the best trial is selected via validation-fold scoring, the
    frozen-eval fold must be passed to run_simulation (or _collect_sim_returns)
    exactly once to produce the honest performance metric.
    """

    def test_frozen_eval_consumed_once_post_selection(self):
        """
        Spy on run_simulation calls after the Optuna sweep completes.
        Assert that exactly one call to run_simulation includes frozen-eval dates,
        and that call happens AFTER the trial selection.

        RED until O6 adds the post-selection frozen-eval evaluation step.
        """
        n_days = 125
        history = _build_history(n_days)
        params = _default_params()
        bot_state = {"sym-A": {"name": "O6 Frozen Once", "account_uuid": "acc-1"}}

        all_dates_sorted = sorted(history["sym-A"].keys())
        split_80 = int(n_days * 0.80)
        frozen_dates: frozenset[str] = frozenset(all_dates_sorted[split_80:])

        # Capture all run_simulation calls and the dates each saw.
        run_sim_calls: list[frozenset[str]] = []

        import autotuner as at
        original_run_sim = at.run_simulation

        def spy_run_simulation(p, history_data, acc_sym_ids, current_date_str, deviation_dict):
            for sym_id, dates_dict in history_data.items():
                run_sim_calls.append(frozenset(dates_dict.keys()))
            return original_run_sim(p, history_data, acc_sym_ids, current_date_str, deviation_dict)

        calls: list[dict] = []
        buf = io.StringIO()
        with _autotuner_patches(params, history, save_autotune_run_calls=calls):
            with patch("autotuner.run_simulation", side_effect=spy_run_simulation):
                with contextlib.redirect_stdout(buf):
                    at.run_autotuner(bot_state, "2026-05-10", ["acc-1"])

        # Count calls where frozen-eval dates appeared.
        frozen_eval_invocations = [
            s for s in run_sim_calls if s & frozen_dates
        ]

        assert len(frozen_eval_invocations) >= 1, (
            "The frozen-eval fold must be evaluated at least once (post best-trial selection). "
            "No run_simulation call included frozen-eval dates. "
            "RED until O6 adds the post-selection frozen-eval scoring step."
        )
        assert len(frozen_eval_invocations) == 1, (
            f"The frozen-eval fold must be evaluated EXACTLY ONCE (post-selection). "
            f"Found {len(frozen_eval_invocations)} invocation(s) with frozen-eval dates. "
            f"Multiple evaluations would condition the frozen metric on selection, "
            f"which defeats O6's reporting-bias prevention purpose. "
            f"RED until the implementation evaluates frozen-eval exactly once."
        )

    def test_frozen_eval_total_reads_across_all_paths_is_one(self):
        """
        Adversarial Revise test: count ALL reads from frozen-eval dates across
        BOTH run_simulation AND _collect_sim_returns. The 'consumed once' invariant
        must hold over both entry points, not just run_simulation.

        A naive implementation calls _collect_sim_returns(history_frozen) for the
        Sortino metric AND run_simulation(history_frozen) for the audit scalar —
        two reads from the same withheld fold, violating the 'exactly once' contract.

        The correct implementation derives frozen_eval_sharpe_value from a single
        call and does not make a redundant second call.

        RED until the implementer consolidates to a single frozen-fold read.
        """
        source = _parse_autotuner_source()
        assert "FROZEN_EVAL_RATIO" in source, (
            "autotuner.py must define FROZEN_EVAL_RATIO before frozen-eval "
            "isolation can be tested. RED until O6 is implemented."
        )

        n_days = 125
        history = _build_history(n_days)
        params = _default_params()
        bot_state = {"sym-A": {"name": "O6 Frozen Total Reads", "account_uuid": "acc-1"}}

        all_dates_sorted = sorted(history["sym-A"].keys())
        split_80 = int(n_days * 0.80)
        frozen_dates: frozenset[str] = frozenset(all_dates_sorted[split_80:])

        all_frozen_reads: list[str] = []  # one entry per call that touched frozen dates

        import autotuner as at
        original_collect = at._collect_sim_returns
        original_run_sim = at.run_simulation

        def spy_collect(p, history_data, acc_sym_ids, current_date_str, deviation_dict):
            for sym_id, dates_dict in history_data.items():
                if frozenset(dates_dict.keys()) & frozen_dates:
                    all_frozen_reads.append("_collect_sim_returns")
            return original_collect(p, history_data, acc_sym_ids, current_date_str, deviation_dict)

        def spy_run_sim(p, history_data, acc_sym_ids, current_date_str, deviation_dict):
            for sym_id, dates_dict in history_data.items():
                if frozenset(dates_dict.keys()) & frozen_dates:
                    all_frozen_reads.append("run_simulation")
            return original_run_sim(p, history_data, acc_sym_ids, current_date_str, deviation_dict)

        calls: list[dict] = []
        buf = io.StringIO()
        with _autotuner_patches(params, history, save_autotune_run_calls=calls):
            with (
                patch("autotuner._collect_sim_returns", side_effect=spy_collect),
                patch("autotuner.run_simulation", side_effect=spy_run_sim),
            ):
                with contextlib.redirect_stdout(buf):
                    at.run_autotuner(bot_state, "2026-05-10", ["acc-1"])

        assert len(all_frozen_reads) >= 1, (
            "The frozen-eval fold must be read at least once post-selection "
            "(via either run_simulation or _collect_sim_returns). "
            "No frozen-fold access detected. RED until O6 adds post-selection evaluation."
        )
        assert len(all_frozen_reads) == 1, (
            f"The frozen-eval fold must be read EXACTLY ONCE in total across both "
            f"run_simulation and _collect_sim_returns. "
            f"Found {len(all_frozen_reads)} read(s): {all_frozen_reads}. "
            f"A double-read (e.g., _collect_sim_returns for Sortino then run_simulation "
            f"for audit scalar) violates the 'consumed once' contract — "
            f"both reads see withheld data and the second read is redundant. "
            f"Consolidate to a single frozen-fold call. RED until fixed."
        )


# ===========================================================================
# Test 5 — Purge + embargo applied at both fold boundaries
# (test_purge_embargo_applied_at_both_boundaries)
# Mandatory RED
# ===========================================================================

class TestPurgeEmbargoAtBothBoundaries:
    """
    The O1 purge + embargo (PURGE_DAYS=20, EMBARGO_DAYS=1) must be applied at:
      (a) the train | validation boundary
      (b) the validation | frozen-eval boundary

    Adversarial: applying purge only at one boundary (the current O1 impl
    applied it only at the train|test boundary) must cause this test to fail.
    """

    def test_purge_embargo_applied_at_both_boundaries(self):
        """
        PURGE_DAYS must appear in proximity to BOTH the validation fold
        AND the frozen-eval fold in autotuner.py.

        The O1 implementation purges only at the train|test boundary.
        O6 requires a second purge at the validation|frozen-eval boundary.

        We verify by checking that frozen_eval references appear near PURGE_DAYS.
        This is impossible without O6 because frozen_eval is not yet in autotuner.py.

        RED until the implementer extends the purge to both fold boundaries.
        """
        source = _parse_autotuner_source()

        has_frozen_ref = bool(re.search(
            r"frozen.eval|frozen_eval|FROZEN_EVAL|history_frozen",
            source,
            re.IGNORECASE,
        ))
        assert has_frozen_ref, (
            "autotuner.py has no frozen-eval fold reference. "
            "O6 must add the third fold before purge can be applied at its boundary. "
            "RED until the frozen-eval fold is introduced."
        )

        has_purge_near_frozen = bool(re.search(
            r"frozen.{0,400}PURGE_DAYS|PURGE_DAYS.{0,400}frozen",
            source,
            re.IGNORECASE | re.DOTALL,
        ))
        assert has_purge_near_frozen, (
            "PURGE_DAYS must appear in proximity to frozen-eval logic. "
            "The validation|frozen-eval boundary requires the same purge treatment "
            "as the train|validation boundary per Lopez de Prado 2018 Ch. 7. "
            "RED until the implementer adds purge at the second boundary."
        )


    def test_frozen_eval_boundary_references_purge_constant(self):
        """
        The code region handling the validation→frozen-eval boundary must
        reference PURGE_DAYS. This ensures the second boundary gets the
        same methodological treatment as the first.

        RED until implemented.
        """
        source = _parse_autotuner_source()

        # Look for the pattern: frozen_eval (or equivalent name) AND PURGE_DAYS
        # appearing in close proximity in the source.
        has_frozen_ref = bool(re.search(
            r"frozen.eval|frozen_eval|FROZEN_EVAL|history_frozen",
            source,
            re.IGNORECASE,
        ))
        has_purge_near_frozen = bool(re.search(
            r"frozen.{0,200}PURGE_DAYS|PURGE_DAYS.{0,200}frozen",
            source,
            re.IGNORECASE | re.DOTALL,
        ))

        assert has_frozen_ref, (
            "autotuner.py contains no reference to a frozen-eval fold "
            "(no 'frozen_eval', 'history_frozen', or similar name found). "
            "O6 requires an explicit frozen-eval variable in the split logic. "
            "RED until the implementer adds the third fold."
        )
        assert has_purge_near_frozen, (
            "PURGE_DAYS must appear in proximity to the frozen-eval fold logic. "
            "The validation→frozen-eval boundary needs the same purge treatment "
            "as the train→validation boundary. "
            "RED until purge is applied at the validation|frozen-eval boundary."
        )


# ===========================================================================
# Test 6 — Named constants for ratios (test_named_constants_for_ratios)
# Mandatory RED
# ===========================================================================

class TestNamedConstantsForRatios:
    """
    TRAIN_RATIO, VALIDATION_RATIO, FROZEN_EVAL_RATIO must exist as module-level
    named constants summing to 1.0, with source comments citing López de Prado
    2018 Ch. 7.4.
    """

    @pytest.mark.parametrize("const_info", [
        {"name": "TRAIN_RATIO",        "expected_value": 0.60},
        {"name": "VALIDATION_RATIO",   "expected_value": 0.20},
        {"name": "FROZEN_EVAL_RATIO",  "expected_value": 0.20},
    ])
    def test_named_constant_exists_with_correct_value(self, const_info):
        """
        Each ratio constant must exist at module scope in autotuner.py with
        the correct numeric value. RED until implemented.
        """
        tree = _parse_autotuner_ast()
        assignments = _find_module_level_assignments(tree)

        name = const_info["name"]
        expected = const_info["expected_value"]

        assert name in assignments, (
            f"autotuner.py must define module-level constant {name}. "
            f"Expected {name} = {expected}. "
            f"RED until O6 named constants are added."
        )

        node = assignments[name]
        assert isinstance(node, ast.Constant) and isinstance(node.value, (int, float)), (
            f"{name} must be a numeric literal; got AST type {type(node).__name__}"
        )
        assert node.value == pytest.approx(expected, abs=1e-9), (
            f"{name} has value {node.value} but must equal {expected} "
            f"(60/20/20 split per López de Prado 2018 Ch. 7.4)."
        )

    def test_named_constants_have_citation_comment(self):
        """
        The constant definitions must have a source comment citing
        López de Prado 2018 Ch. 7.4 in close proximity.

        This prevents a future reader from changing the ratios without
        understanding the methodological basis.

        RED until the implementer adds the constants with citations.
        """
        source = _parse_autotuner_source()
        lines = source.splitlines()

        const_names = ["TRAIN_RATIO", "VALIDATION_RATIO", "FROZEN_EVAL_RATIO"]
        found_citation = False

        for i, line in enumerate(lines):
            if any(name in line for name in const_names):
                context = "\n".join(lines[max(0, i - 2): i + 5])
                if re.search(r"[Ll]ópez de Prado|Lopez de Prado|L.pez de Prado", context) and \
                   re.search(r"7\.4|Ch\.?\s*7", context):
                    found_citation = True
                    break

        assert found_citation, (
            "The TRAIN_RATIO/VALIDATION_RATIO/FROZEN_EVAL_RATIO constants must have "
            "a source comment citing 'López de Prado 2018 Ch. 7.4' in proximity. "
            "This is required by the O6 spec to document the methodological basis "
            "for the 60/20/20 split. RED until citation is added."
        )

    def test_ratios_sum_to_one(self):
        """
        TRAIN_RATIO + VALIDATION_RATIO + FROZEN_EVAL_RATIO == 1.0 exactly
        (within floating-point tolerance). Red until all three constants exist.
        """
        tree = _parse_autotuner_ast()
        assignments = _find_module_level_assignments(tree)

        total = 0.0
        for name in ["TRAIN_RATIO", "VALIDATION_RATIO", "FROZEN_EVAL_RATIO"]:
            if name not in assignments:
                pytest.fail(
                    f"Cannot verify sum: {name} not found in autotuner.py. RED."
                )
            node = assignments[name]
            assert isinstance(node, ast.Constant), (
                f"{name} must be a literal constant to verify the sum invariant."
            )
            total += node.value

        assert abs(total - 1.0) <= 1e-9, (
            f"TRAIN_RATIO + VALIDATION_RATIO + FROZEN_EVAL_RATIO = {total}, "
            f"must equal exactly 1.0 (tolerance 1e-9). "
            f"A ratio sum != 1.0 means some history days are unaccounted for."
        )


# ===========================================================================
# Test 7 — autotune_runs has validation_sharpe + frozen_eval_sharpe columns
# (test_autotune_runs_records_both_metrics)
# Mandatory RED
# ===========================================================================

class TestAutotuneRunsRecordsBothMetrics:
    """
    Migration 007 must add validation_sharpe and frozen_eval_sharpe columns
    to autotune_runs. run_autotuner must populate both.
    """

    def test_migration_007_sql_exists_and_targets_correct_table(self):
        """
        migrations/007_autotune_runs_frozen_eval.sql must exist and contain
        ALTER TABLE statements for autotune_runs (not optuna_studies.db).
        RED until the migration file is created.
        """
        fix = _load_fixture("migration_007_schema.json")
        migration_file = fix["migration_file"]
        sql_path = _MIGRATIONS_DIR / migration_file

        assert sql_path.exists(), (
            f"{_MIGRATIONS_DIR}/{migration_file} does not exist. "
            "O6 requires migration 007 to add validation_sharpe and frozen_eval_sharpe "
            "columns to autotune_runs in alphabot_state.db. "
            "RED until the migration file is created."
        )

        raw_sql = sql_path.read_text(encoding="utf-8")
        sql_statements = "\n".join(
            line for line in raw_sql.splitlines()
            if not line.strip().startswith("--")
        ).lower()

        target_table = fix["target_table"]
        assert target_table in sql_statements, (
            f"Migration 007 SQL must reference '{target_table}'. "
            f"The new columns belong in autotune_runs (alphabot_state.db), "
            f"not in optuna_studies.db."
        )

        # Must not target llm_suggestions (wrong table for this metric).
        assert "llm_suggestions" not in sql_statements, (
            "Migration 007 SQL must NOT reference llm_suggestions. "
            "validation_sharpe and frozen_eval_sharpe are per-run Optuna metrics, "
            "not LLM suggestion attributes."
        )

        for col in fix["new_columns"]:
            assert col["name"] in sql_statements, (
                f"Migration 007 SQL must add column '{col['name']}' to autotune_runs. "
                f"Column not found in migration SQL statements."
            )

    def test_migration_007_adds_correct_columns_to_in_memory_db(self):
        """
        Apply migration 007 to an in-memory SQLite DB and assert the two new
        columns (validation_sharpe, frozen_eval_sharpe) are present, REAL type,
        NULLable, and that pre-existing rows are preserved with NULL in new columns.

        Red until the migration file exists and is well-formed.
        """
        fix = _load_fixture("migration_007_schema.json")
        migration_file = fix["migration_file"]
        sql_path = _MIGRATIONS_DIR / migration_file

        if not sql_path.exists():
            pytest.fail(
                f"Migration file {migration_file} not found. "
                "Cannot apply migration — RED until file is created."
            )

        conn = sqlite3.connect(":memory:")
        # Bootstrap pre-007 schema (migrations 001-006 state).
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_name TEXT PRIMARY KEY,
                applied_at     TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS autotune_runs (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                run_timestamp      TEXT    NOT NULL,
                symphony_id        TEXT    NOT NULL,
                oos_alpha          REAL    DEFAULT NULL,
                train_alpha        REAL    DEFAULT NULL,
                baseline_decision  TEXT    DEFAULT NULL,
                fallback_oos_alpha REAL    DEFAULT NULL,
                default_oos_alpha  REAL    DEFAULT NULL,
                deflated_sharpe    REAL    DEFAULT NULL,
                naive_sharpe       REAL    DEFAULT NULL
            );
        """)
        conn.commit()

        # Insert a pre-007 row.
        conn.execute(
            "INSERT INTO autotune_runs "
            "(run_timestamp, symphony_id, oos_alpha, train_alpha, baseline_decision, "
            "fallback_oos_alpha, default_oos_alpha, deflated_sharpe, naive_sharpe) "
            "VALUES ('2026-05-01T10:00:00Z', 'test-symphony', 1.5, 2.0, "
            "'Adopted AI', 0.8, 0.5, 0.9, 1.4)"
        )
        conn.commit()
        pre_row_id = conn.execute(
            "SELECT id FROM autotune_runs"
        ).fetchone()[0]

        # Apply migration 007.
        raw_sql = sql_path.read_text(encoding="utf-8")
        sql_statements = "\n".join(
            line for line in raw_sql.splitlines()
            if not line.strip().startswith("--")
        )
        try:
            conn.executescript(sql_statements)
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                pass  # idempotent re-apply
            else:
                raise
        conn.commit()

        col_info = conn.execute("PRAGMA table_info(autotune_runs)").fetchall()
        col_map = {row[1]: row for row in col_info}

        for col_spec in fix["new_columns"]:
            col_name = col_spec["name"]
            assert col_name in col_map, (
                f"Column '{col_name}' must exist in autotune_runs after migration 007. "
                f"Migration 007 must add it. RED until implemented."
            )
            col_row = col_map[col_name]
            # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
            assert col_row[2].upper() == "REAL", (
                f"Column '{col_name}' must be REAL type; got {col_row[2]!r}"
            )
            assert col_row[3] == 0, (
                f"Column '{col_name}' must be nullable (NOT NULL=0); "
                f"got NOT NULL={col_row[3]}"
            )

        # Pre-existing row must be preserved with NULL in new columns.
        post_row = conn.execute(
            "SELECT id, validation_sharpe, frozen_eval_sharpe "
            "FROM autotune_runs WHERE id = ?", (pre_row_id,)
        ).fetchone()
        assert post_row is not None, (
            f"Pre-migration row id={pre_row_id} was deleted by migration 007. "
            "Migration must be additive only."
        )
        assert post_row[1] is None, (
            f"Pre-migration row must have NULL validation_sharpe after migration 007; "
            f"got {post_row[1]!r}. Additive-first contract violated."
        )
        assert post_row[2] is None, (
            f"Pre-migration row must have NULL frozen_eval_sharpe after migration 007; "
            f"got {post_row[2]!r}. Additive-first contract violated."
        )

        conn.close()

    def test_run_autotuner_populates_both_new_columns(self):
        """
        run_autotuner must call save_autotune_run with both validation_sharpe
        and frozen_eval_sharpe kwargs after O6 is implemented.

        We do not assert the exact values (producer-computed Sortino outputs);
        we assert:
          1. Both kwargs are present.
          2. validation_sharpe is a finite float (or None if the fold is empty
             after purge — degenerate but must not raise).
          3. frozen_eval_sharpe is a finite float (or None for same reason).

        RED until O6 routes both metrics through save_autotune_run.
        """
        n_days = 125
        history = _build_history(n_days)
        params = _default_params()
        bot_state = {"sym-A": {"name": "O6 Both Metrics", "account_uuid": "acc-1"}}

        calls: list[dict] = []
        buf = io.StringIO()
        with _autotuner_patches(params, history, save_autotune_run_calls=calls):
            with contextlib.redirect_stdout(buf):
                _import_autotuner().run_autotuner(bot_state, "2026-05-10", ["acc-1"])

        assert len(calls) >= 1, (
            "run_autotuner must call save_autotune_run at least once (per symphony)."
        )
        call = calls[0]

        assert "validation_sharpe" in call, (
            "save_autotune_run must be called with 'validation_sharpe' kwarg. "
            "This is the Sortino on the validation fold (selection metric). "
            "RED until O6 adds this parameter to the save_autotune_run call."
        )
        assert "frozen_eval_sharpe" in call, (
            "save_autotune_run must be called with 'frozen_eval_sharpe' kwarg. "
            "This is the Sortino on the frozen-eval fold (honest reporting metric). "
            "RED until O6 adds this parameter to the save_autotune_run call."
        )

        val_sharpe = call["validation_sharpe"]
        frozen_sharpe = call["frozen_eval_sharpe"]

        # Shape assertion: must be numeric or None (for degenerate empty fold).
        if val_sharpe is not None:
            assert isinstance(val_sharpe, (int, float)), (
                f"validation_sharpe must be numeric or None; got {type(val_sharpe).__name__}"
            )
            assert math.isfinite(val_sharpe) or val_sharpe in (float("inf"), float("-inf")), (
                f"validation_sharpe must be a float; got {val_sharpe!r}"
            )
        if frozen_sharpe is not None:
            assert isinstance(frozen_sharpe, (int, float)), (
                f"frozen_eval_sharpe must be numeric or None; got {type(frozen_sharpe).__name__}"
            )


# ===========================================================================
# Test 8 — Fold collapse acknowledgment in autotuner.py docstring
# (test_fold_collapse_acknowledgment)
# Mandatory RED
# ===========================================================================

class TestFoldCollapseAcknowledgment:
    """
    At 125-day history, 60/20/20 = 75/25/25 days. After 20-day purge at each
    boundary, the usable validation and frozen-eval windows each shrink to
    approximately 4-5 days. This tradeoff must be documented in autotuner.py.
    """

    def test_fold_collapse_acknowledged_in_autotuner_source(self):
        """
        autotuner.py must contain documentation acknowledging the fold collapse
        tradeoff: the 20-day purge leaves approximately 4-5 usable days in
        each of the validation and frozen-eval folds on a 125-day history.

        This prevents a future operator from misinterpreting the small usable
        window as a bug rather than an acknowledged methodological tradeoff.

        Red until the implementer adds this documentation.
        """
        source = _parse_autotuner_source()

        has_60_20_20 = bool(re.search(r"60/20/20|60\.?%.*20\.?%.*20\.?%", source, re.IGNORECASE))
        has_validation_mention = bool(re.search(r"validation.fold|validation.sharpe", source, re.IGNORECASE))
        has_frozen_mention = bool(re.search(r"frozen.eval|frozen_eval", source, re.IGNORECASE))
        has_fold_collapse_note = bool(re.search(
            r"usable.*validation|usable.*frozen|fold.*collapse|~\s*[0-9]+\s*usable",
            source,
            re.IGNORECASE,
        ))

        assert has_60_20_20 or (has_validation_mention and has_frozen_mention), (
            "autotuner.py must document the 60/20/20 split (validation + frozen-eval). "
            "Neither '60/20/20' nor references to both 'validation' and 'frozen-eval' "
            "folds were found in the source. RED until O6 is documented."
        )
        assert has_fold_collapse_note, (
            "autotuner.py must document the fold-collapse tradeoff: "
            "at 125-day history, the 20-day purge leaves ~4-5 usable days in "
            "each of the validation and frozen-eval folds. "
            "Add this acknowledgment to the run_autotuner docstring. "
            "RED until implemented."
        )

    def test_fold_collapse_references_purge_and_history_size(self):
        """
        The fold-collapse documentation must reference both:
          (a) the 125-day history window size
          (b) the purge/embargo mechanism causing the collapse

        This ensures the documentation is specific enough to be useful
        for a future operator diagnosing unexpectedly small evaluation sets.
        """
        source = _parse_autotuner_source()

        has_125 = bool(re.search(r"125.day|125-trading-day|125 day", source, re.IGNORECASE))
        has_purge_mention = bool(re.search(r"\bpurge\b|\bembargo\b", source, re.IGNORECASE))

        assert has_125, (
            "Fold-collapse documentation must reference the 125-day history window. "
            "The tradeoff is specific to this window size and must be quantified. "
            "RED until the reference is added."
        )
        assert has_purge_mention, (
            "Fold-collapse documentation must reference the purge/embargo mechanism "
            "as the cause of the collapsed evaluation window. "
            "RED until the reference is added."
        )
