"""
OPTUNA-4 — OOS-Fold-Collapse v2: RED tests (Path B resolution).

Audit finding (math-reaudit MEDIUM OPTUNA-4):
  At 125 trading days × 60/20/20 split, after PURGE_DAYS=20 + EMBARGO_DAYS=1
  is applied at the validation|frozen-eval boundary, the usable validation
  window collapses to ~4 days. Statistical power on 4 days is thin.

Decision (this cycle) — Path B: document + operator-visibility.
  - The 125-day operator-data-budget stays (decision-science council §0
    binding choice; expanding the window is a data-budget Amendment, not
    an audit slide-in — see feature-plans/decision-science/engine-audit/
    walk-forward-fold-structure/plan.md "Out of scope: changing any
    fold-structure constant").
  - The BHY haircut already compensates statistically for the thin window
    (Harvey & Liu 2015 selection-bias correction; the ~4-day usable
    validation window is the COST of honest OOS reporting, not a defect).
  - The autotuner docstring must explicitly carry both halves of the
    argument — the thin-window cost AND the BHY-compensation justification
    that makes the 125-day window operationally safe.
  - A new operator-visible field `eval_window_days` is added to the
    optimization_results return dict, so every autotune cycle surfaces
    the usable-window day-counts to the operator without requiring a DB
    schema migration.
  - A new module-level constant _OOS_USABLE_VALIDATION_DAYS_EXPECTED pins
    the expected usable validation day-count derived from
    int(history_length * VALIDATION_RATIO) - PURGE_DAYS - EMBARGO_DAYS,
    so a future drift in any of those inputs trips the cross-check.

Fixture provenance: tests/fixtures/autotuner/oos_fold_collapse/
  oos_fold_collapse_pin.json — schema-derived expected values + the
  docstring-phrase contract + the operator-visibility-field contract.

No assertions hardcode producer-computed values. All numeric expectations
are derived in-test from the named ratio and purge/embargo constants the
implementation already pins (TRAIN_RATIO, VALIDATION_RATIO,
FROZEN_EVAL_RATIO, PURGE_DAYS, EMBARGO_DAYS).

Citations:
  - López de Prado 2018, Advances in Financial Machine Learning, Ch. 7.4
  - Harvey & Liu 2015, "Backtesting," J. Portfolio Management 42(1)
  - math-reaudit OPTUNA-4 finding
  - feature-plans/decision-science/engine-audit/walk-forward-fold-structure/plan.md
"""
from __future__ import annotations

import ast
import contextlib
import inspect as _inspect
import io
import json
import pathlib
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "autotuner"
    / "oos_fold_collapse"
)
_AUTOTUNER_SRC = _WORKTREE_ROOT / "autotuner.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_pin() -> dict:
    return json.loads(
        (_FIXTURE_DIR / "oos_fold_collapse_pin.json").read_text(encoding="utf-8")
    )


def _parse_autotuner_source() -> str:
    return _AUTOTUNER_SRC.read_text(encoding="utf-8")


def _parse_autotuner_ast() -> ast.Module:
    return ast.parse(_parse_autotuner_source())


def _find_module_level_assignments(tree: ast.Module) -> dict[str, Any]:
    """Return {name: value_node} for every module-level Assign / AnnAssign."""
    out: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.value is not None:
                out[node.target.id] = node.value
    return out


def _import_autotuner():
    import autotuner
    return autotuner


def _make_bundle() -> int:
    from tests.autotuner.conftest import make_phase1_theory_bundle
    return make_phase1_theory_bundle()


def _spec_bundle_kwarg() -> dict:
    import autotuner as _at
    sig = _inspect.signature(_at.run_autotuner)
    if "spec_bundle_id" not in sig.parameters:
        return {}
    return {"spec_bundle_id": _make_bundle()}


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
    """Deterministic single-symphony history; weekdays only to avoid weekend gaps."""
    tick = {
        "return": 0.5,
        "mc_prob": 50.0,
        "vol": 1.0,
        "vwap_diff": 0.0,
        "base_atr_pct": 1.0,
        "valid_vwap_weight": 1.0,
    }
    import datetime
    start = datetime.date(2025, 6, 2)  # Monday
    dates: list[str] = []
    d = start
    while len(dates) < n_days:
        if d.weekday() < 5:
            dates.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return {"sym-A": {date: [tick] for date in dates}}


@contextlib.contextmanager
def _autotuner_patches(
    best_params: dict,
    history: dict,
    save_autotune_run_calls: list[dict] | None = None,
):
    """Mirrors the O6 harness — stubs Optuna + math_engine + DB I/O.

    We mock network/time/DB but NEVER mock the fold-construction math —
    that is exactly what OPTUNA-4 is pinning.
    """
    fake_study = MagicMock(name="fake_optuna_study")
    fake_study.best_params = best_params.copy()
    fake_study.best_value = 1.0
    fake_study.optimize = MagicMock(return_value=None)
    fake_study.trials = []

    def capturing_save(**kwargs):
        if save_autotune_run_calls is not None:
            save_autotune_run_calls.append(kwargs)
        return 1

    with (
        patch("autotuner.optuna.create_study", return_value=fake_study),
        patch("autotuner.optuna.storages.RDBStorage", return_value=MagicMock()),
        patch(
            "autotuner.synthetic_history.generate_synthetic_history",
            return_value=history,
        ),
        patch("autotuner.database.load_chart_history", return_value={}),
        patch("autotuner.database.save_chart_archive"),
        patch(
            "autotuner.database.get_symphony_strategy",
            return_value={"params": best_params.copy(), "locked_vars": []},
        ),
        patch("autotuner.database.save_symphony_strategy"),
        patch("autotuner.database.DEFAULT_STRATEGY", best_params.copy()),
        patch(
            "autotuner.database.save_autotune_run",
            side_effect=capturing_save,
        ),
        patch(
            "autotuner.math_engine.compute_para_arm_decision",
            side_effect=lambda **kw: (0.0, False),
        ),
        patch(
            "autotuner.math_engine.compute_time_squeeze_decay",
            side_effect=lambda tr: (1.5, 0.5),
        ),
        patch(
            "autotuner.math_engine.compute_active_trailing_stop",
            side_effect=lambda *a, **kw: 5.0,
        ),
        patch(
            "autotuner.math_engine.compute_breakeven_update",
            side_effect=lambda *a, **kw: (a[3], a[4], a[2]),
        ),
        patch(
            "autotuner.math_engine.compute_vwap_bleed_arm_threshold",
            side_effect=lambda *a, **kw: -10.0,
        ),
        patch(
            "autotuner.math_engine.compute_vwap_breakdown_update",
            side_effect=lambda **kw: (0, 0, False, False),
        ),
    ):
        yield fake_study


# ===========================================================================
# Test 1 — Named module-level constant pins the usable validation window.
# ===========================================================================

class TestUsableValidationWindowConstantPinned:
    """
    The OPTUNA-4 thin-window justification rests on a SPECIFIC arithmetic
    consequence:
        int(history_length * VALIDATION_RATIO) - PURGE_DAYS - EMBARGO_DAYS
    At the 125-day operator-data-budget this is 25 - 20 - 1 = 4 days.

    The implementer must pin this number as a named module-level constant
    so a future drift in any of (history_length, VALIDATION_RATIO,
    PURGE_DAYS, EMBARGO_DAYS) trips a cross-check, instead of silently
    collapsing the usable window further.

    Adversarial angle: catch an implementer who only edits the docstring
    and adds the dict field but does NOT introduce the named constant —
    the docstring would then drift from the math the next time anyone
    changed PURGE_DAYS.
    """

    def test_named_constant_exists_at_module_scope(self):
        pin = _load_pin()
        expected_name = pin["named_constant"]["name"]

        tree = _parse_autotuner_ast()
        assignments = _find_module_level_assignments(tree)

        assert expected_name in assignments, (
            f"autotuner.py is missing the required OPTUNA-4 named constant "
            f"`{expected_name}`. It must be defined at module scope and "
            f"equal int(history_length * VALIDATION_RATIO) - PURGE_DAYS - "
            f"EMBARGO_DAYS at the 125-day operator-data-budget. "
            f"RED until the implementer adds it."
        )

    def test_named_constant_equals_derived_formula(self):
        """
        The pinned value must equal the formula derived from the existing
        ratio + purge/embargo constants — never an opaque literal.
        """
        pin = _load_pin()
        const_name = pin["named_constant"]["name"]
        expected_value = pin["named_constant"]["expected_value"]
        history_length = pin["history_length"]

        autotuner = _import_autotuner()

        # The derivation MUST line up with what the implementation already
        # pins — that is the cross-check this constant exists to enforce.
        derived = (
            int(history_length * autotuner.VALIDATION_RATIO)
            - autotuner.PURGE_DAYS
            - autotuner.EMBARGO_DAYS
        )
        assert derived == expected_value, (
            f"Fixture expected_value ({expected_value}) does not equal the "
            f"derived formula int({history_length} * VALIDATION_RATIO) "
            f"- PURGE_DAYS - EMBARGO_DAYS = {derived}. Either the fixture "
            f"or the autotuner ratio/purge constants have drifted."
        )

        assert hasattr(autotuner, const_name), (
            f"autotuner.{const_name} is not importable. RED until "
            f"implementer adds the module-level constant."
        )
        actual = getattr(autotuner, const_name)
        assert actual == derived, (
            f"autotuner.{const_name} = {actual} but the derivation "
            f"int({history_length} * VALIDATION_RATIO) - PURGE_DAYS - "
            f"EMBARGO_DAYS = {derived}. The constant must equal the "
            f"derivation — a literal that drifts from the inputs is the "
            f"failure mode this test exists to catch."
        )


# ===========================================================================
# Test 2 — run_autotuner docstring carries both halves of the Path B argument.
# ===========================================================================

class TestDocstringCarriesBothArgumentHalves:
    """
    Path B's load-bearing claim is that the BHY haircut compensates for
    the thin usable validation window — the 125-day data-budget is
    operationally safe BECAUSE the selection-bias correction is sized to
    the trial count, not the day count.

    The docstring of run_autotuner must explicitly carry that argument.
    Documentation drift here would leave the next reader unable to
    distinguish "this is a defect we tolerated" from "this is a defended
    tradeoff."

    Adversarial angle: catch the implementer who adds the field + the
    constant but quietly omits the compensation phrase from the docstring.
    """

    def test_docstring_names_eval_window_days_field(self):
        pin = _load_pin()
        required = pin["docstring_required_phrases"]["field_in_docstring"]

        autotuner = _import_autotuner()
        doc = autotuner.run_autotuner.__doc__ or ""

        assert required in doc, (
            f"run_autotuner docstring must mention the new operator-visible "
            f"field name `{required}` so the next reader can find the "
            f"contract from the source-of-truth comment. RED until added."
        )

    def test_docstring_names_bhy_compensation_argument(self):
        """
        The docstring must explicitly cite that the BHY (Benjamini-Hochberg-
        Yekutieli) selection-bias correction is what compensates for the
        thin usable validation window. Without that phrase the docstring
        only documents the cost half of the tradeoff, not the justification
        half — and the justification is what makes Path B defensible.
        """
        pin = _load_pin()
        thin = pin["docstring_required_phrases"]["thin_window_phrase"]
        comp = pin["docstring_required_phrases"]["compensation_phrase"]

        autotuner = _import_autotuner()
        doc = autotuner.run_autotuner.__doc__ or ""

        assert thin in doc, (
            f"run_autotuner docstring must name the compensating "
            f"mechanism (`{thin}`) so the OPTUNA-4 tradeoff argument is "
            f"complete. RED until added."
        )
        assert comp in doc, (
            f"run_autotuner docstring must explicitly use the phrase "
            f"`{comp}` (the technical term for what the BHY haircut "
            f"does) so the compensation argument is unambiguous. "
            f"RED until added."
        )


# ===========================================================================
# Test 3 — optimization_results carries the eval_window_days dict.
# ===========================================================================

class TestOptimizationResultsExposesEvalWindowDays:
    """
    Path B's operator-visibility contract: every autotune cycle must
    surface the usable-window day-counts on the returned dict so the
    operator sees the thin-window cost. No DB migration is required —
    the data is in-memory at the call site.

    Adversarial angle: catch the implementer who adds the constant + the
    docstring but forgets the runtime emission. A static-only fix would
    leave the operator blind on the live cycle.
    """

    def test_optimization_results_has_eval_window_days_subdict(self):
        pin = _load_pin()
        field_name = pin["operator_visibility_field"]["field_name"]
        required_subkeys = pin["operator_visibility_field"]["required_subkeys"]

        n_days = pin["history_length"]
        history = _build_history(n_days)
        params = _default_params()
        bot_state = {
            "sym-A": {"name": "OPTUNA-4 Path B Test", "account_uuid": "acc-1"}
        }

        buf = io.StringIO()
        with _autotuner_patches(params, history):
            with contextlib.redirect_stdout(buf):
                result = _import_autotuner().run_autotuner(
                    bot_state,
                    "2026-05-10",
                    ["acc-1"],
                    **_spec_bundle_kwarg(),
                )

        assert isinstance(result, dict) and result, (
            "run_autotuner must return a non-empty optimization_results dict. "
            f"Got: {result!r}"
        )

        # There is exactly one symphony in this fixture.
        sym_key = next(iter(result))
        sym_entry = result[sym_key]
        assert isinstance(sym_entry, dict), (
            f"optimization_results[{sym_key!r}] must be a dict; got "
            f"{type(sym_entry).__name__}"
        )

        assert field_name in sym_entry, (
            f"optimization_results[{sym_key!r}] is missing the OPTUNA-4 "
            f"operator-visibility field `{field_name}`. RED until the "
            f"implementer emits this dict on every per-symphony entry."
        )

        ewd = sym_entry[field_name]
        assert isinstance(ewd, dict), (
            f"optimization_results[{sym_key!r}][{field_name!r}] must be a "
            f"dict (per-subkey day-counts), not {type(ewd).__name__}."
        )

        missing = [k for k in required_subkeys if k not in ewd]
        assert not missing, (
            f"optimization_results[{sym_key!r}][{field_name!r}] is missing "
            f"required subkeys {missing}. Required: {required_subkeys}. "
            f"Got keys: {sorted(ewd.keys())}."
        )

    def test_eval_window_days_values_match_actual_fold_arithmetic(self):
        """
        The day-counts must be derived from the actual fold-construction
        logic, not hardcoded. Concretely:
          usable_validation_days
            == int(history_length * VALIDATION_RATIO) - PURGE_DAYS - EMBARGO_DAYS
          raw_validation_days
            == int(history_length * (TRAIN_RATIO + VALIDATION_RATIO))
               - int(history_length * TRAIN_RATIO)
          raw_frozen_eval_days
            == history_length - int(history_length * (TRAIN_RATIO + VALIDATION_RATIO))
          purge_days  == PURGE_DAYS
          embargo_days == EMBARGO_DAYS
        """
        pin = _load_pin()
        field_name = pin["operator_visibility_field"]["field_name"]
        n_days = pin["history_length"]
        history = _build_history(n_days)
        params = _default_params()
        bot_state = {
            "sym-A": {"name": "OPTUNA-4 Arithmetic Test", "account_uuid": "acc-1"}
        }

        buf = io.StringIO()
        with _autotuner_patches(params, history):
            with contextlib.redirect_stdout(buf):
                result = _import_autotuner().run_autotuner(
                    bot_state,
                    "2026-05-10",
                    ["acc-1"],
                    **_spec_bundle_kwarg(),
                )

        autotuner = _import_autotuner()

        val_start_idx = int(n_days * autotuner.TRAIN_RATIO)
        frozen_start_idx = int(
            n_days * (autotuner.TRAIN_RATIO + autotuner.VALIDATION_RATIO)
        )
        derived = {
            "raw_validation_days": frozen_start_idx - val_start_idx,
            "usable_validation_days": max(
                0,
                (frozen_start_idx - val_start_idx)
                - autotuner.PURGE_DAYS
                - autotuner.EMBARGO_DAYS,
            ),
            "raw_frozen_eval_days": n_days - frozen_start_idx,
            "purge_days": autotuner.PURGE_DAYS,
            "embargo_days": autotuner.EMBARGO_DAYS,
        }

        sym_key = next(iter(result))
        ewd = result[sym_key].get(field_name, {})

        for k, expected in derived.items():
            actual = ewd.get(k)
            assert actual == expected, (
                f"optimization_results[{sym_key!r}][{field_name!r}][{k!r}] "
                f"= {actual!r}; expected {expected!r} (derived from "
                f"TRAIN_RATIO/VALIDATION_RATIO/PURGE_DAYS/EMBARGO_DAYS at "
                f"history_length={n_days}). The emission must be derived "
                f"from the live constants, never hardcoded."
            )


# ===========================================================================
# Test 4 — Invariant: usable validation window stays strictly positive at
# the pinned 125-day data-budget. (T6 in the engine-audit plan.)
# ===========================================================================

class TestUsableValidationWindowStrictlyPositive:
    """
    At the operator-data-budget the usable validation window must be
    strictly > 0. A zero-day usable window would mean Optuna has nothing
    to score against — a Gate-1 ship-blocker per the engine-audit plan's
    T6 invariant.

    This is an invariant test: it does not depend on Path A vs B; it pins
    the floor below which the system must NOT be allowed to ship even by
    accident.
    """

    def test_usable_validation_days_is_strictly_positive(self):
        pin = _load_pin()
        n_days = pin["history_length"]

        autotuner = _import_autotuner()
        val_start_idx = int(n_days * autotuner.VALIDATION_RATIO + n_days * autotuner.TRAIN_RATIO) - int(n_days * autotuner.TRAIN_RATIO)
        # Equivalent to int(n_days*(TRAIN+VAL)) - int(n_days*TRAIN), which is
        # the raw validation day count under the current split.
        raw_val_days = (
            int(n_days * (autotuner.TRAIN_RATIO + autotuner.VALIDATION_RATIO))
            - int(n_days * autotuner.TRAIN_RATIO)
        )
        usable = raw_val_days - autotuner.PURGE_DAYS - autotuner.EMBARGO_DAYS

        assert usable > 0, (
            f"Usable validation window collapsed to {usable} days at "
            f"history_length={n_days} with TRAIN_RATIO="
            f"{autotuner.TRAIN_RATIO}, VALIDATION_RATIO="
            f"{autotuner.VALIDATION_RATIO}, PURGE_DAYS="
            f"{autotuner.PURGE_DAYS}, EMBARGO_DAYS="
            f"{autotuner.EMBARGO_DAYS}. A zero-day usable window means "
            f"Optuna has no observations to score the objective on — "
            f"Gate-1 ship-blocker per engine-audit plan T6."
        )

    def test_usable_validation_days_matches_pinned_expected(self):
        """
        At the operator-data-budget (125 days), the usable validation
        window must equal exactly the pinned expected value. A drift
        from this number is operator-visible only after this assertion
        fails — the test is the tripwire.
        """
        pin = _load_pin()
        n_days = pin["history_length"]
        expected = pin["usable_window_post_purge"]["usable_validation_days"]

        autotuner = _import_autotuner()
        raw_val_days = (
            int(n_days * (autotuner.TRAIN_RATIO + autotuner.VALIDATION_RATIO))
            - int(n_days * autotuner.TRAIN_RATIO)
        )
        usable = raw_val_days - autotuner.PURGE_DAYS - autotuner.EMBARGO_DAYS

        assert usable == expected, (
            f"Usable validation window is {usable} days at "
            f"history_length={n_days}; pinned expected value is "
            f"{expected}. Either the operator-data-budget changed (an "
            f"Amendment) or PURGE/EMBARGO drifted (a feature-lookback "
            f"change). Either way, surface as an Amendment — do NOT "
            f"silently update this fixture."
        )
