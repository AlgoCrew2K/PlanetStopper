"""
AC-3 / N-1 — frozen_eval_sharpe reset on AI proposal rejection.

When the Harvey & Liu haircut OR the OOS-cascade demotes the AI proposal to
Fallback or Default, ``frozen_eval_sharpe`` MUST be persisted as None. The
operator-facing column must never carry the Sortino of a REJECTED AI
proposal as if it were the deployed parameter set's metric. This mirrors
the existing symmetric reset at autotuner.py:1081-1083 where
``selection_tstat_value`` and ``naive_sharpe_value`` are nulled when the
proposal is invalid.

Pre-RM-H1 the BHY haircut rejected AI proposals more often than the old
DSR, so the labelling defect surfaced more often — but the defect itself
predates RM-H1.

Independent audit's N-1 finding:
  docs/research/math-audit-2/autotuner__2026-05-22.md.

Tests pin three paths:
  1. Haircut rejection (no trial clears FDR gate): frozen_eval_sharpe=None.
  2. Schema-invalid best_params (current symmetric reset path): also
     frozen_eval_sharpe=None.
  3. Baseline cascade demotes to Fallback (OOS validation fails on the
     AI's params): frozen_eval_sharpe=None.
  4. Baseline cascade demotes to Default: same.
  5. Negative path: accepted AI proposal MUST still persist
     frozen_eval_sharpe as a real numeric value (regression guard).

Provenance: tests patch ``database.save_autotune_run`` and assert the
kwarg the producer passed. No producer-computed value is asserted as a
fixed number — only None / non-None / type.
"""

from __future__ import annotations

import contextlib
import types
from unittest.mock import MagicMock, patch

import pytest

import autotuner


# ---------------------------------------------------------------------------
# Stand-in Optuna trial objects sufficient for the haircut + cascade.
# autotuner._haircut_select reads t.value, t.user_attrs["daily_returns"],
# t.params. Schema validator wants every OPTUNA_SEARCH_SPACE_KEYS key.
# ---------------------------------------------------------------------------


def _make_trial(value: float, returns: list[float], params: dict) -> object:
    """Minimal Optuna-trial stand-in. SimpleNamespace (not MagicMock) so
    attribute access does not auto-vivify and confuse the haircut filter."""
    t = types.SimpleNamespace()
    t.value = value
    t.user_attrs = {"daily_returns": list(returns)}
    t.params = dict(params)
    return t


def _full_params() -> dict:
    """A schema-VALID params payload — every OPTUNA_SEARCH_SPACE_KEYS key."""
    return {
        "TAKE_PROFIT_MC_PCT": 5.0,
        "VWAP_CROSS_HWM_PCT": 1.0,
        "VWAP_BLEED_MULTIPLIER": 1.5,
        "VWAP_BLEED_TICKS": 10,
        "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
        "MAX_PARABOLIC_SQUEEZE": 0.5,
    }


def _synthetic_history_payload() -> dict:
    """A synthetic 125-day history for a single symphony — enough days to
    survive the 60/20/20 split + PURGE_DAYS+EMBARGO_DAYS = 21 cuts at each
    boundary. With total_days=125, val_start=75, frozen_start=100,
    purged-train ≤ 54, purged-val ≤ 4, purged-frozen = 25 — non-empty
    folds suffice to drive run_autotuner past every gate.

    Returns a dict shaped exactly like
    synthetic_history.generate_synthetic_history's output.
    """
    history = {"sym-test-001": {}}
    for day_idx in range(125):
        # Date doesn't need to be a real calendar day — just a sortable
        # unique string.
        date_key = f"2026-01-{day_idx + 1:03d}"
        history["sym-test-001"][date_key] = [
            {"return": 0.0, "vol": 1.0, "mc_prob": 50.0, "vwap_diff": 0.0, "valid_vwap_weight": 1.0}
        ]
    return history


def _seed_bot_state() -> dict:
    """A bot_state with one symphony for the per-symphony loop to find."""
    return {
        "date": "2026-05-21",
        "sym-test-001": {
            "name": "TestSym",
            "account": "acct-test",
            "current_value": 10_000.0,
        },
    }


def _run_autotuner_with_patches(
    *,
    haircut_return,
    study_best_params: dict,
    sim_side_effect=None,
    sim_return=-1.0,
    sortino_return: float = 0.7,
) -> dict:
    """Drive run_autotuner under heavy patching; return the captured
    save_autotune_run kwargs.

    Reuses one patch stack across the four scenario classes so each test
    only varies (a) the haircut decision and (b) the OOS ordering.

    Note: run_autotuner now requires an explicit spec_bundle_id (NN1 Phase-1
    strict). The harness satisfies this by:
      1. Passing spec_bundle_id=1.
      2. Patching database.get_spec_bundle_by_id to return a stub row without
         facets_json — this skips the hash integrity check (autotuner.py:1664).
      3. Patching validate_nn1_compliance to return (True, []) — no violations.
      4. Patching database.get_spec_facets_for_bundle to return Sortino-branch
         facets (objective_kind absent → falls back to sortino_loss_aversion).
    """
    captured: dict = {}

    def _capture_save(**kwargs):
        captured.update(kwargs)

    # Stub bundle row: no facets_json → hash integrity check skipped (per
    # autotuner.py:1664: "may be absent on mocked rows in tests"). No facet
    # entries → objective_kind absent → sortino_loss_aversion branch. N1 tests
    # specifically test frozen_eval_sharpe behavior on the Sortino branch;
    # using a CRRA-EU bundle would set frozen_eval_sharpe=None by design
    # (autotuner.py:2073) and break the accepted-proposal regression guard.
    _stub_bundle_row = {"bundle_hash": "test-stub-hash"}
    _stub_facets: list = []

    _sim_patch_kwargs: dict = (
        {"side_effect": sim_side_effect}
        if sim_side_effect is not None
        else {"return_value": sim_return}
    )

    # ExitStack flattens the deep nesting; avoids "too many statically nested
    # blocks" SyntaxError from Python's 20-level hard limit.
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(autotuner, "_haircut_select", return_value=haircut_return))
        stack.enter_context(
            patch.object(autotuner, "compute_sortino_ratio", return_value=sortino_return)
        )
        stack.enter_context(patch.object(autotuner, "run_simulation", **_sim_patch_kwargs))
        stack.enter_context(
            patch.object(autotuner, "_collect_sim_returns", return_value=[0.01, -0.005, 0.008])
        )
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
        mock_create_study = stack.enter_context(patch("autotuner.optuna.create_study"))
        stack.enter_context(patch.object(autotuner.optuna.storages, "RDBStorage"))
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

        study = MagicMock()
        study.best_value = 0.5
        study.best_params = study_best_params
        study.trials = [_make_trial(0.5, [0.01, -0.005, 0.008], _full_params())]
        mock_create_study.return_value = study

        bot_state = _seed_bot_state()
        try:
            autotuner.run_autotuner(
                bot_state,
                current_date_str="2026-05-21",
                account_uuids=["acct-test"],
                spec_bundle_id=1,
            )
        except Exception as exc:  # pragma: no cover - diagnostic only
            # The harness may raise from an unrelated branch (warning emit,
            # logging side effect). What matters is whether save_autotune_run
            # ran and what kwargs were captured.
            captured.setdefault("_harness_exception", f"{type(exc).__name__}: {exc}")

    return captured


# ---------------------------------------------------------------------------
# 1 — Haircut-rejection path.
# ---------------------------------------------------------------------------


class TestHaircutRejectionNullsFrozenEvalSharpe:
    """When _haircut_select returns winner_trial=None (no trial clears the
    FDR gate), save_autotune_run must receive frozen_eval_sharpe=None."""

    def test_haircut_rejection_persists_none_frozen_eval_sharpe(self):
        """Force _haircut_select to reject; pin the captured kwarg."""
        captured = _run_autotuner_with_patches(
            haircut_return=(None, 0.99, 1.2),  # no winner
            study_best_params=_full_params(),
        )
        assert captured, (
            "AC-3 / N-1: save_autotune_run never reached on the haircut-"
            "rejection path. Harness diagnostic: "
            f"{captured.get('_harness_exception', '<no exception>')}"
        )
        assert "frozen_eval_sharpe" in captured, (
            "AC-3 / N-1: save_autotune_run must pass 'frozen_eval_sharpe' "
            f"on the haircut-rejection path. Got: {sorted(captured.keys())}."
        )
        assert captured["frozen_eval_sharpe"] is None, (
            "AC-3 / N-1 VIOLATED: haircut rejection persisted "
            f"frozen_eval_sharpe={captured['frozen_eval_sharpe']!r}. The "
            "AI proposal was REJECTED by the FDR gate; the operator-"
            "facing metric must be None — matching the symmetric reset of "
            "selection_tstat_value + naive_sharpe_value at "
            "autotuner.py:1081-1083."
        )


# ---------------------------------------------------------------------------
# 2 — Schema-invalid best_params.
# ---------------------------------------------------------------------------


class TestSchemaInvalidProposalNullsFrozenEvalSharpe:
    """Empty / schema-incomplete best_params: the existing null-set must
    include frozen_eval_sharpe."""

    def test_empty_best_params_persists_none_frozen_eval_sharpe(self):
        """Degenerate study with empty best_params -> ai_proposal_invalid
        branch fires; save_autotune_run.frozen_eval_sharpe must be None."""
        captured = _run_autotuner_with_patches(
            # Accept at the haircut so the failure isolates to the schema
            # check.
            haircut_return=(
                _make_trial(0.5, [0.01, -0.005], {}),
                0.001,
                3.0,
            ),
            study_best_params={},  # empty -> schema invalid
        )
        assert captured, (
            f"save_autotune_run never reached. "
            f"{captured.get('_harness_exception', '<no exception>')}"
        )
        assert "frozen_eval_sharpe" in captured
        assert captured["frozen_eval_sharpe"] is None, (
            "AC-3 / N-1 VIOLATED: schema-invalid best_params persisted "
            f"frozen_eval_sharpe={captured['frozen_eval_sharpe']!r}. The "
            "proposal was rejected wholesale (ai_proposal_invalid=True); "
            "the metric must be None to match the symmetric null-set."
        )


# ---------------------------------------------------------------------------
# 3 — Baseline-cascade demotion (Fallback / Default).
# ---------------------------------------------------------------------------


class TestCascadeDemotionNullsFrozenEvalSharpe:
    """Baseline cascade demotes the AI proposal -> frozen_eval_sharpe=None."""

    @pytest.mark.parametrize(
        "branch_name, raw_sim_values",
        [
            # run_simulation is called THREE times: AI best_p, fallback,
            # default. The autotuner negates each: oos_alpha =
            # -run_simulation(...). So to produce
            # AI_OOS < fallback_OOS AND fallback >= default ("Reverted to
            # Fallback"), pick run_simulation returns where AI returns the
            # LARGEST positive value (most NEGATIVE OOS), fallback the
            # smallest, and default in between.
            ("Reverted to Fallback", (3.0, 1.0, 2.0)),
            ("Reset to Global Default", (3.0, 2.0, 1.0)),
        ],
    )
    def test_cascade_demotion_nulls_frozen_eval_sharpe(
        self, branch_name: str, raw_sim_values: tuple
    ):
        """The AI clears the haircut but OOS validation demotes it. Pin
        that frozen_eval_sharpe is None on each cascade demotion branch.

        Sign convention: run_simulation returns guard-alpha-magnitude; the
        caller negates. AI alpha = -run_simulation(ai_best_p). For
        "Reverted to Fallback": need oos_alpha (AI) NOT strictly greater
        than both baselines AND fallback_oos_alpha >= default_oos_alpha.
        With AI=3.0 -> oos_alpha=-3.0, fallback=1.0 -> -1.0, default=2.0
        -> -2.0: -3 < -1 AND -3 < -2 (AI fails strict-positive) and
        -1 >= -2 (fallback >= default) -> "Reverted to Fallback". For
        "Reset to Global Default": fallback=2.0, default=1.0
        -> -2 < -1, so default wins.
        """
        sim_iter = iter(raw_sim_values)

        def _sim_side_effect(*args, **kwargs):
            try:
                return next(sim_iter)
            except StopIteration:
                return 0.0

        captured = _run_autotuner_with_patches(
            haircut_return=(
                _make_trial(0.5, [0.01, -0.005], _full_params()),
                0.001,
                3.0,
            ),
            study_best_params=_full_params(),
            sim_side_effect=_sim_side_effect,
        )
        assert captured, (
            f"save_autotune_run never reached. "
            f"{captured.get('_harness_exception', '<no exception>')}"
        )
        assert captured.get("baseline_decision") == branch_name, (
            "Harness sanity: expected baseline_decision="
            f"{branch_name!r}; got {captured.get('baseline_decision')!r}. "
            "Re-check sign convention on raw_sim_values."
        )
        assert captured.get("frozen_eval_sharpe") is None, (
            f"AC-3 / N-1 VIOLATED on '{branch_name}': "
            f"frozen_eval_sharpe={captured.get('frozen_eval_sharpe')!r} "
            "was persisted even though the cascade demoted the AI "
            "proposal. The deployed params are NOT the AI's; the "
            "operator-facing metric must be None."
        )


# ---------------------------------------------------------------------------
# 4 — Accepted AI proposal MUST persist frozen_eval_sharpe (regression).
# ---------------------------------------------------------------------------


class TestAcceptedProposalPersistsFrozenEvalSharpe:
    """Regression guard: an over-broad null-set that wipes the metric on
    the accept path would break operator reporting on the happy case."""

    def test_accepted_proposal_persists_real_frozen_eval_sharpe(self):
        """AI clears every gate; save_autotune_run.frozen_eval_sharpe must
        be a finite numeric (NOT None)."""
        # AI wins all three OOS comparisons. AI=1.0 -> -1.0; fallback=2.0
        # -> -2.0; default=3.0 -> -3.0. -1 > -2 AND -1 > -3 AND -1 > 0:
        # passes the strict-positive AI branch ("Adopted AI"). Wait —
        # autotuner.py:1136 requires oos_alpha > both AND oos_alpha > 0.
        # With AI=-1.0 raw, oos_alpha=1.0 (positive!) — but that requires
        # the AI sim value to be NEGATIVE. Pick AI=-1.0 raw (oos=1.0),
        # fallback=2.0 (oos=-2.0), default=3.0 (oos=-3.0).
        sim_iter = iter([-1.0, 2.0, 3.0])

        def _sim_side_effect(*args, **kwargs):
            try:
                return next(sim_iter)
            except StopIteration:
                return 0.0

        captured = _run_autotuner_with_patches(
            haircut_return=(
                _make_trial(0.5, [0.01, -0.005], _full_params()),
                0.001,
                3.0,
            ),
            study_best_params=_full_params(),
            sim_side_effect=_sim_side_effect,
        )
        assert captured, (
            f"save_autotune_run never reached. "
            f"{captured.get('_harness_exception', '<no exception>')}"
        )
        assert captured.get("baseline_decision") == "Adopted AI", (
            "Harness sanity: expected 'Adopted AI'; got "
            f"{captured.get('baseline_decision')!r}. Re-check sign "
            "convention."
        )
        assert captured.get("frozen_eval_sharpe") is not None, (
            "AC-3 / N-1 REGRESSION: accepted AI proposal persisted "
            "frozen_eval_sharpe=None. The null-set is over-broad — it "
            "should only fire on rejection / cascade-demotion paths."
        )
        assert isinstance(captured["frozen_eval_sharpe"], (int, float)), (
            f"Accepted proposal: frozen_eval_sharpe must be numeric. Got "
            f"{captured['frozen_eval_sharpe']!r} "
            f"({type(captured['frozen_eval_sharpe']).__name__})."
        )
