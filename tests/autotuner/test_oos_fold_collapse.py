"""
OPTUNA-4 — OOS-Fold-Collapse v2: RED tests (Path B, Option 1 — revised after
opt-optuna4 BLOCK on ebbe571).

Audit finding (math-reaudit MEDIUM OPTUNA-4):
  At 125 trading days × 60/20/20 split, after PURGE_DAYS=20 + EMBARGO_DAYS=1
  is applied at the validation|frozen-eval boundary, the usable validation
  window collapses to ~4 days. Statistical power on 4 days is thin.

Decision (this cycle) — Path B, Option 1: HONESTY framing + operator-visibility.
  - The 125-day operator-data-budget stays (decision-science council §0
    binding choice; expanding the window is a data-budget Amendment, not
    an audit slide-in — see feature-plans/decision-science/engine-audit/
    walk-forward-fold-structure/plan.md "Out of scope: changing any
    fold-structure constant").
  - The ~4-day usable validation window is an acknowledged STATISTICAL
    POWER LIMITATION and the COST of honest OOS reporting. The BHY haircut
    addresses CROSS-TRIAL SELECTION BIAS — a multiplicity problem — and
    does NOT substitute for per-trial sample length. Conflating the two
    is the category error opt-optuna4 BLOCKed on ebbe571.
  - The autotuner docstring must:
      (a) name the new `eval_window_days` field,
      (b) acknowledge the thin window as a "power limitation",
      (c) cite the engine-audit honesty wording "cost of honest OOS
          reporting",
      (d) point to the future-workstream remediation paths (purged k-fold
          CV per López de Prado 2018 Ch. 7.4, or expanded operator-data-
          budget),
      (e) name Bailey & López de Prado 2014 (Deflated Sharpe Ratio) as
          the canonical joint (N, T) framework for future work.
  - The docstring must NOT carry any phrase fusing the BHY haircut with
    the thin window — the forbidden_phrases set below is the door that
    does not reopen.
  - The same positive + negative phrase contract applies to the source
    comment co-located with `_OOS_USABLE_VALIDATION_DAYS_EXPECTED`.
  - A new operator-visible field `eval_window_days` is added to the
    optimization_results return dict, so every autotune cycle surfaces
    the usable-window day-counts to the operator without requiring a DB
    schema migration.
  - A new module-level constant _OOS_USABLE_VALIDATION_DAYS_EXPECTED pins
    the expected usable validation day-count derived from
    int(history_length * VALIDATION_RATIO) - PURGE_DAYS - EMBARGO_DAYS,
    so a future drift in any of those inputs trips the cross-check.

Fixture provenance: tests/fixtures/autotuner/oos_fold_collapse/
  oos_fold_collapse_pin.json — schema-derived expected values, the
  docstring positive/negative phrase contract, the source-comment
  positive/negative phrase contract, and the operator-visibility-field
  contract.

No assertions hardcode producer-computed values. All numeric expectations
are derived in-test from the named ratio and purge/embargo constants the
implementation already pins (TRAIN_RATIO, VALIDATION_RATIO,
FROZEN_EVAL_RATIO, PURGE_DAYS, EMBARGO_DAYS).

Citations:
  - López de Prado 2018, Advances in Financial Machine Learning, Ch. 7.4
    (purged k-fold CV, frozen-eval discipline)
  - Harvey & Liu 2015, "Backtesting," J. Portfolio Management 42(1)
    (BHY multiplicity correction — addresses cross-trial selection bias,
    NOT per-trial signal reliability)
  - Bailey & López de Prado 2014, "The Deflated Sharpe Ratio" (canonical
    joint (N, T) framework — accounts for trial count AND sample length)
  - math-reaudit OPTUNA-4 finding
  - feature-plans/decision-science/engine-audit/walk-forward-fold-structure/plan.md
  - opt-optuna4 BLOCK on ebbe571 (category-confusion diagnosis — provenance
    for this revision)
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
_FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "autotuner" / "oos_fold_collapse"
_AUTOTUNER_SRC = _WORKTREE_ROOT / "autotuner.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_pin() -> dict:
    return json.loads((_FIXTURE_DIR / "oos_fold_collapse_pin.json").read_text(encoding="utf-8"))


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
# Test 2 — run_autotuner docstring carries the Option-1 honesty framing.
# ===========================================================================


class TestDocstringHonestyFraming:
    """
    Path B's revised contract (Option 1 after opt-optuna4 BLOCK on ebbe571):
    the docstring acknowledges the ~4-day usable validation window as a
    POWER LIMITATION (not a tolerated defect) and points to the future-
    workstream remediation paths and the canonical joint (N, T) framework
    (Bailey & López de Prado 2014).

    The docstring must NOT claim that any selection-bias correction
    substitutes for adequate sample length. That category confusion was
    the ebbe571 failure mode; the forbidden_phrases set in the next class
    is the door that does not reopen.

    Adversarial angle: catch the implementer who adds the field + the
    constant but ships a docstring that either (a) omits the honesty
    framing entirely, or (b) reverts to the BHY-compensation phrasing.
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

    def test_docstring_acknowledges_power_limitation(self):
        """
        The thin window must be named as a "power limitation" — a
        statistical-power deficit, not a tolerated defect. This phrase is
        the load-bearing acknowledgement that distinguishes the honest
        Option-1 framing from any rationalisation that the limitation is
        "compensated".
        """
        pin = _load_pin()
        phrase = pin["docstring_required_phrases"]["power_limitation_phrase"]

        autotuner = _import_autotuner()
        doc = autotuner.run_autotuner.__doc__ or ""

        assert phrase in doc, (
            f"run_autotuner docstring must contain the phrase "
            f"`{phrase}` to acknowledge the ~4-day usable validation "
            f"window as a statistical-power deficit. RED until added."
        )

    def test_docstring_uses_engine_audit_honesty_wording(self):
        """
        The engine-audit plan and the existing docstring at autotuner.py
        :1311-1317 both use the wording "cost of honest OOS reporting".
        Pinning that exact wording ties the revised docstring to the
        preexisting honesty doctrine and prevents a near-miss reword that
        loses the "honesty" framing.
        """
        pin = _load_pin()
        phrase = pin["docstring_required_phrases"]["honesty_framing_phrase"]

        autotuner = _import_autotuner()
        doc = autotuner.run_autotuner.__doc__ or ""

        assert phrase in doc, (
            f"run_autotuner docstring must contain the phrase "
            f"`{phrase}` (the engine-audit honesty wording). RED until "
            f"added."
        )

    def test_docstring_points_to_future_workstream(self):
        """
        The methodologically-clean remediation paths are (a) expanding the
        operator-data-budget OR (b) combinatorial purged k-fold CV per
        López de Prado 2018 Ch. 7.4. The docstring must point to the
        latter explicitly so a future reader sees that recovering power
        is the fix — not arguing the cost away.
        """
        pin = _load_pin()
        phrase = pin["docstring_required_phrases"]["future_workstream_phrase"]

        autotuner = _import_autotuner()
        doc = autotuner.run_autotuner.__doc__ or ""

        assert phrase in doc, (
            f"run_autotuner docstring must point to the future-workstream "
            f"remediation path via the phrase `{phrase}` (López de Prado "
            f"2018 Ch. 7.4). RED until added."
        )

    def test_docstring_cites_joint_n_t_framework(self):
        """
        Bailey & López de Prado 2014 (Deflated Sharpe Ratio) is the
        canonical (N, T) joint framework. The docstring must cite it (the
        substring `Bailey` is the load-bearing token — any future reader
        looking for the joint (N, T) framework finds it via the author
        surname) so a future reader cannot repeat the BHY-only conflation.
        """
        pin = _load_pin()
        phrase = pin["docstring_required_phrases"]["joint_n_t_framework_citation"]

        autotuner = _import_autotuner()
        doc = autotuner.run_autotuner.__doc__ or ""

        assert phrase in doc, (
            f"run_autotuner docstring must cite Bailey & López de Prado "
            f"2014 (Deflated Sharpe Ratio) — the canonical joint (N, T) "
            f"framework — via the substring `{phrase}`. RED until added."
        )


# ===========================================================================
# Test 3 — run_autotuner docstring forbids the BHY-compensation category
# confusion (the door that does not reopen).
# ===========================================================================


class TestDocstringForbidsCompensationClaims:
    """
    opt-optuna4's BLOCK on ebbe571 identified a category confusion: the
    docstring fused BHY's multiplicity-correction property ("operates
    independently of day count") with a statistical-power-substitution
    claim ("operationally safe because of the haircut"). The first is a
    true feature of BHY; the second is methodologically incorrect.

    This test enforces that none of the category-confusion phrasings
    reappear. A future PR that tries to reintroduce any of them fails
    here loudly with the offending phrase quoted.
    """

    def test_docstring_contains_no_forbidden_compensation_phrases(self):
        pin = _load_pin()
        forbidden = pin["docstring_forbidden_phrases"]["phrases"]

        autotuner = _import_autotuner()
        doc = autotuner.run_autotuner.__doc__ or ""
        # Case-insensitive scan — the category confusion does not get to
        # hide behind capitalisation tricks.
        doc_lower = doc.lower()

        hits = [p for p in forbidden if p.lower() in doc_lower]
        assert not hits, (
            f"run_autotuner docstring contains forbidden category-"
            f"confusion phrase(s): {hits}. These were the methodology "
            f"error opt-optuna4 BLOCKed on ebbe571 — the BHY haircut "
            f"addresses cross-trial selection bias independently of day "
            f"count (a FEATURE), which is NOT the same as compensating "
            f"for a thin per-trial sample length (a substitution claim). "
            f"Reword to the honesty framing (power limitation + future-"
            f"workstream pointer + Bailey 2014 citation). RED until "
            f"every forbidden phrase is removed."
        )


# ===========================================================================
# Test 4 — Source comment on _OOS_USABLE_VALIDATION_DAYS_EXPECTED carries
# the honesty framing and forbids the same compensation phrasings.
# ===========================================================================


class TestNamedConstantSourceComment:
    """
    Per opt-optuna4 note #3 on the ebbe571 BLOCK acknowledgement: the
    BHY-compensation paragraph in the source comment co-located with
    `_OOS_USABLE_VALIDATION_DAYS_EXPECTED` (autotuner.py near line 263)
    must be REPLACED wholesale, not edited. The revised comment must
    carry the honesty framing AND must not carry any BHY-compensation
    phrasing.

    Adversarial angle: catch the implementer who fixes the docstring but
    forgets to revise the source comment — the comment would otherwise
    remain a methodology-error landmine for the next reader.
    """

    def test_named_constant_source_comment_carries_honesty_framing(self):
        pin = _load_pin()
        anchor = pin["source_comment_required_phrases"]["anchor_line_substring"]
        must_contain = pin["source_comment_required_phrases"]["must_contain"]

        src = _parse_autotuner_source()
        lines = src.splitlines()

        # Find the line where the constant is defined; the source comment
        # is the contiguous run of `#`-prefixed lines immediately above it.
        anchor_idx = None
        for i, line in enumerate(lines):
            if anchor in line and "=" in line and not line.lstrip().startswith("#"):
                anchor_idx = i
                break
        assert anchor_idx is not None, (
            f"Could not locate the definition line for `{anchor}` in "
            f"autotuner.py. The constant must exist at module scope; "
            f"see TestUsableValidationWindowConstantPinned for the "
            f"named-constant contract."
        )

        # Walk upward collecting consecutive comment lines.
        comment_lines: list[str] = []
        j = anchor_idx - 1
        while j >= 0 and lines[j].lstrip().startswith("#"):
            comment_lines.insert(0, lines[j])
            j -= 1
        comment_text = "\n".join(comment_lines)

        assert comment_text, (
            f"No source comment found immediately above the definition "
            f"of `{anchor}` at autotuner.py line {anchor_idx + 1}. The "
            f"revised contract requires a co-located comment naming "
            f"derivation, honesty framing, drift-as-Amendment guard, "
            f"and the Bailey 2014 joint (N, T) pointer. RED until added."
        )

        missing = [p for p in must_contain if p not in comment_text]
        assert not missing, (
            f"Source comment on `{anchor}` is missing required phrase(s): "
            f"{missing}. The comment must carry the honesty framing "
            f"(`power limitation`), the future-workstream pointer "
            f"(`purged k-fold`), and the joint (N, T) framework citation "
            f"(`Bailey`). Comment block found:\n{comment_text}\nRED "
            f"until added."
        )

    def test_named_constant_source_comment_forbids_compensation_phrasings(self):
        pin = _load_pin()
        anchor = pin["source_comment_required_phrases"]["anchor_line_substring"]
        must_not_contain = pin["source_comment_required_phrases"]["must_not_contain"]

        src = _parse_autotuner_source()
        lines = src.splitlines()

        anchor_idx = None
        for i, line in enumerate(lines):
            if anchor in line and "=" in line and not line.lstrip().startswith("#"):
                anchor_idx = i
                break
        assert anchor_idx is not None, (
            f"Could not locate the definition line for `{anchor}` in "
            f"autotuner.py — see the positive-phrase test for the "
            f"co-located-comment contract."
        )

        comment_lines: list[str] = []
        j = anchor_idx - 1
        while j >= 0 and lines[j].lstrip().startswith("#"):
            comment_lines.insert(0, lines[j])
            j -= 1
        comment_text = "\n".join(comment_lines).lower()

        hits = [p for p in must_not_contain if p.lower() in comment_text]
        assert not hits, (
            f"Source comment on `{anchor}` contains forbidden "
            f"category-confusion phrase(s): {hits}. Per opt-optuna4 "
            f"note #3 on the ebbe571 BLOCK acknowledgement, the BHY-"
            f"compensation paragraph must be REPLACED wholesale, not "
            f"edited. The forbidden set is the negative half of the "
            f"door-that-does-not-reopen contract. RED until removed."
        )


# ===========================================================================
# Test 5 — optimization_results carries the eval_window_days dict.
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
        bot_state = {"sym-A": {"name": "OPTUNA-4 Path B Test", "account_uuid": "acc-1"}}

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
            f"run_autotuner must return a non-empty optimization_results dict. Got: {result!r}"
        )

        # There is exactly one symphony in this fixture.
        sym_key = next(iter(result))
        sym_entry = result[sym_key]
        assert isinstance(sym_entry, dict), (
            f"optimization_results[{sym_key!r}] must be a dict; got {type(sym_entry).__name__}"
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
        bot_state = {"sym-A": {"name": "OPTUNA-4 Arithmetic Test", "account_uuid": "acc-1"}}

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
        frozen_start_idx = int(n_days * (autotuner.TRAIN_RATIO + autotuner.VALIDATION_RATIO))
        derived = {
            "raw_validation_days": frozen_start_idx - val_start_idx,
            "usable_validation_days": max(
                0,
                (frozen_start_idx - val_start_idx) - autotuner.PURGE_DAYS - autotuner.EMBARGO_DAYS,
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
# Test 6 — Invariant: usable validation window stays strictly positive at
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
        val_start_idx = int(
            n_days * autotuner.VALIDATION_RATIO + n_days * autotuner.TRAIN_RATIO
        ) - int(n_days * autotuner.TRAIN_RATIO)
        # Equivalent to int(n_days*(TRAIN+VAL)) - int(n_days*TRAIN), which is
        # the raw validation day count under the current split.
        raw_val_days = int(n_days * (autotuner.TRAIN_RATIO + autotuner.VALIDATION_RATIO)) - int(
            n_days * autotuner.TRAIN_RATIO
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
        raw_val_days = int(n_days * (autotuner.TRAIN_RATIO + autotuner.VALIDATION_RATIO)) - int(
            n_days * autotuner.TRAIN_RATIO
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
