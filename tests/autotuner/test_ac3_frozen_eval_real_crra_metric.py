"""
AC-3 (MA-9, HIGH) -- frozen-eval must produce a REAL metric under the
production CRRA-EU objective.

Bug (audit finding H2 / MA-9): autotuner.py hardcodes
``frozen_eval_sharpe_value = None`` on the crra_eu branch (Sortino is
inapplicable to a CRRA-EU objective, so the pre-fix code simply gives up
rather than computing the CRRA-EU analog). 20% of the walk-forward data
budget (the frozen fold) buys ZERO measurement for every CRRA-EU-governed
symphony -- which, per THEORY 2.0, is the production objective_kind.

Fix target: compute a real frozen-eval metric on the crra_eu branch using
the SAME primitives the CRRA-EU selection objective already uses
(math_engine.compute_crra_eu_objective over the frozen fold's
_collect_sim_returns output, pct->fraction via RETURN_PCT_TO_FRACTION --
mirroring run_simulation_crra_eu's own conversion at autotuner.py:1949).

Already-wired consumer surface (confirmed by direct read, NOT something
this cycle needs to build): ``database.save_autotune_run(...,
frozen_eval_sharpe=frozen_eval_sharpe_value, ...)`` persists to
``autotune_runs.frozen_eval_sharpe`` (unconditionally reached, regardless of
objective_kind), and ``reporting.py:497-500`` already renders it in the
Discord "Sortino (naive \\ selection t-stat \\ frozen-eval)" line via a
None-safe ``_fmt``. AC-3's fix therefore only needs to stop the crra_eu
branch from computing None -- the "reported AND consumed" requirement is
satisfied by the EXISTING persistence + display wiring once a real value
flows into it (pinned below as a regression guard, not built here).

Golden discipline: expected numeric values are derived by calling the REAL
math_engine.compute_crra_eu_objective on a known, mocked frozen-fold return
series -- never a hand-typed literal.

Harness: adapted from tests/autotuner/test_n1_frozen_eval_sharpe_reset.py's
established _run_autotuner_with_patches pattern (same patch stack), with
get_spec_facets_for_bundle configured to trigger the crra_eu branch
(utility_family=CRRA) instead of the Sortino default that file deliberately
avoids the crra_eu branch for (its own comment: "using a CRRA-EU bundle
would set frozen_eval_sharpe=None by design and break the accepted-proposal
regression guard" -- that IS this file's target gap).
"""

from __future__ import annotations

import contextlib
import pathlib
import types
from unittest.mock import MagicMock, patch

import pytest

import autotuner
import math_engine

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_REPORTING_PATH = _REPO_ROOT / "reporting.py"

# A deterministic, non-degenerate frozen-fold return series (raw percent,
# matching _collect_sim_returns's T5 provenance contract) -- chosen with
# mixed sign so compute_crra_eu_objective's utility floor / mean genuinely
# exercises both the gain and loss branches, not a degenerate all-positive
# or all-negative series.
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


def _expected_crra_eu_frozen_eval() -> float:
    """The golden: call the REAL compute_crra_eu_objective on the SAME
    frozen-fold return series the harness feeds _collect_sim_returns,
    applying the SAME pct->fraction conversion run_simulation_crra_eu uses
    (autotuner.py:1949) -- never a hand-typed expected number."""
    fractions = [r / autotuner.RETURN_PCT_TO_FRACTION for r in _FROZEN_FOLD_RETURNS_PCT]
    return math_engine.compute_crra_eu_objective(fractions, _GAMMA)


def _run_autotuner_crra_eu_with_patches(
    *,
    haircut_return,
    study_best_params: dict,
    sim_side_effect=None,
    sim_return: float = -1.0,
) -> dict:
    """Drive run_autotuner on the CRRA-EU branch under heavy patching;
    return the captured save_autotune_run kwargs. _collect_sim_returns is
    patched to return the SAME deterministic series for every fold
    (train/validation/frozen alike) -- this test only cares about the
    frozen-eval computation, not fold-differentiated content."""
    captured: dict = {}

    def _capture_save(**kwargs):
        captured.update(kwargs)

    _stub_bundle_row = {"bundle_hash": "test-stub-hash-crra"}
    # Facets trigger the crra_eu branch: utility_family=CRRA (objective_kind
    # absent -> falls back to crra_eu per autotuner.py's own discriminator:
    # "'crra_eu' if _utility_family.upper() == 'CRRA' else 'sortino_loss_aversion'").
    _stub_facets = [
        {"facet_name": "utility_family", "facet_value": "CRRA"},
        {"facet_name": "gamma", "facet_value": str(_GAMMA)},
    ]

    _sim_patch_kwargs: dict = (
        {"side_effect": sim_side_effect}
        if sim_side_effect is not None
        else {"return_value": sim_return}
    )

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(autotuner, "_haircut_select", return_value=haircut_return))
        stack.enter_context(patch.object(autotuner, "run_simulation", **_sim_patch_kwargs))
        stack.enter_context(
            patch.object(
                autotuner, "_collect_sim_returns", return_value=list(_FROZEN_FOLD_RETURNS_PCT)
            )
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
        stack.enter_context(patch.object(autotuner.database, "DEFAULT_STRATEGY", new=_full_params()))
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
        study.trials = [_make_trial(0.5, _FROZEN_FOLD_RETURNS_PCT, _full_params())]
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
            captured.setdefault("_harness_exception", f"{type(exc).__name__}: {exc}")

    return captured


# ---------------------------------------------------------------------------
# Core RED: accepted CRRA-EU proposal must persist a REAL frozen-eval metric.
# ---------------------------------------------------------------------------


class TestCrraEuFrozenEvalProducesRealMetric:
    def test_accepted_crra_eu_proposal_persists_nonnull_frozen_eval_sharpe(self):
        """AI clears every gate on the crra_eu branch; save_autotune_run's
        frozen_eval_sharpe must be a finite numeric, NOT None. RED today
        (verified live): the crra_eu branch hardcodes None unconditionally."""
        sim_iter = iter([-1.0, 2.0, 3.0])

        def _sim_side_effect(*args, **kwargs):
            try:
                return next(sim_iter)
            except StopIteration:
                return 0.0

        captured = _run_autotuner_crra_eu_with_patches(
            haircut_return=(
                _make_trial(0.5, _FROZEN_FOLD_RETURNS_PCT, _full_params()),
                0.001,
                3.0,
            ),
            study_best_params=_full_params(),
            sim_side_effect=_sim_side_effect,
        )
        assert captured, (
            "AC-3: save_autotune_run never reached on the crra_eu accept path. "
            f"Harness diagnostic: {captured.get('_harness_exception', '<no exception>')}"
        )
        assert captured.get("baseline_decision") == "Adopted AI", (
            "Harness sanity: expected 'Adopted AI'; got "
            f"{captured.get('baseline_decision')!r}."
        )
        assert captured.get("frozen_eval_sharpe") is not None, (
            "AC-3 (MA-9) VIOLATED: the crra_eu branch persisted "
            "frozen_eval_sharpe=None on an ACCEPTED proposal. 20% of the "
            "data budget (the frozen fold) bought zero measurement -- the "
            "'honest post-selection metric' the design promises does not "
            "exist for CRRA-EU-governed symphonies (production's objective_kind)."
        )
        assert isinstance(captured["frozen_eval_sharpe"], (int, float)), (
            f"frozen_eval_sharpe must be numeric. Got "
            f"{captured['frozen_eval_sharpe']!r} "
            f"({type(captured['frozen_eval_sharpe']).__name__})."
        )

    def test_accepted_crra_eu_frozen_eval_matches_real_crra_objective_golden(self):
        """The persisted value must equal math_engine.compute_crra_eu_objective
        applied to the SAME frozen-fold return series (pct->fraction via
        RETURN_PCT_TO_FRACTION) -- never an arbitrary or Sortino-branch
        value smuggled onto the crra_eu path."""
        sim_iter = iter([-1.0, 2.0, 3.0])

        def _sim_side_effect(*args, **kwargs):
            try:
                return next(sim_iter)
            except StopIteration:
                return 0.0

        captured = _run_autotuner_crra_eu_with_patches(
            haircut_return=(
                _make_trial(0.5, _FROZEN_FOLD_RETURNS_PCT, _full_params()),
                0.001,
                3.0,
            ),
            study_best_params=_full_params(),
            sim_side_effect=_sim_side_effect,
        )
        assert captured.get("baseline_decision") == "Adopted AI"
        expected = _expected_crra_eu_frozen_eval()
        assert captured.get("frozen_eval_sharpe") == pytest.approx(expected, rel=1e-9), (
            f"AC-3 (MA-9): frozen_eval_sharpe={captured.get('frozen_eval_sharpe')!r} does "
            f"not match the real compute_crra_eu_objective golden {expected!r} computed "
            "over the same frozen-fold series. The persisted value must be the genuine "
            "CRRA-EU utility metric, not a placeholder or a Sortino-branch leak."
        )


# ---------------------------------------------------------------------------
# Regression safety: AC-3's fix must not disturb N-1's null-set discipline
# on the crra_eu branch (rejection / cascade-demotion still -> None).
# ---------------------------------------------------------------------------


class TestCrraEuFrozenEvalStillNullsOnRejection:
    def test_haircut_rejection_still_persists_none_on_crra_eu_branch(self):
        """N-1's reset discipline (autotuner.py:2921-2922) must hold for
        crra_eu exactly as it does for Sortino: a REJECTED proposal's
        frozen-eval metric must never be persisted as if deployed."""
        captured = _run_autotuner_crra_eu_with_patches(
            haircut_return=(None, 0.99, 1.2),  # no winner -> rejected
            study_best_params=_full_params(),
        )
        assert captured, (
            f"save_autotune_run never reached. "
            f"{captured.get('_harness_exception', '<no exception>')}"
        )
        assert captured.get("frozen_eval_sharpe") is None, (
            "AC-3 REGRESSION: fixing the crra_eu branch's happy path must not "
            "break N-1's null-set discipline -- a haircut-rejected proposal must "
            f"still persist frozen_eval_sharpe=None. Got "
            f"{captured.get('frozen_eval_sharpe')!r}."
        )

    def test_cascade_demotion_to_fallback_still_persists_none_on_crra_eu_branch(self):
        sim_iter = iter([3.0, 1.0, 2.0])  # -> "Reverted to Fallback"

        def _sim_side_effect(*args, **kwargs):
            try:
                return next(sim_iter)
            except StopIteration:
                return 0.0

        captured = _run_autotuner_crra_eu_with_patches(
            haircut_return=(
                _make_trial(0.5, _FROZEN_FOLD_RETURNS_PCT, _full_params()),
                0.001,
                3.0,
            ),
            study_best_params=_full_params(),
            sim_side_effect=_sim_side_effect,
        )
        assert captured.get("baseline_decision") == "Reverted to Fallback", (
            "Harness sanity: expected 'Reverted to Fallback'; got "
            f"{captured.get('baseline_decision')!r}."
        )
        assert captured.get("frozen_eval_sharpe") is None, (
            "AC-3 REGRESSION: cascade demotion to Fallback on the crra_eu branch "
            f"must still persist frozen_eval_sharpe=None. Got "
            f"{captured.get('frozen_eval_sharpe')!r}."
        )


# ---------------------------------------------------------------------------
# Already-wired consumer surface: confirm reporting.py still references
# frozen_eval_sharpe (regression guard against silent removal of the
# existing display wiring this cycle relies on rather than rebuilds).
# ---------------------------------------------------------------------------


def test_reporting_still_renders_frozen_eval_sharpe_in_the_selection_line():
    src = _REPORTING_PATH.read_text(encoding="utf-8")
    assert "frozen_eval_sharpe" in src, (
        "reporting.py no longer references frozen_eval_sharpe -- AC-3's fix relies on "
        "this EXISTING Discord display wiring (the 'Sortino (naive \\ selection t-stat "
        "\\ frozen-eval)' line) to satisfy the 'reported and consumed' requirement "
        "without building a new consumer. If this surface was intentionally removed, "
        "AC-3's own consumer requirement needs a new home -- flag to PM, don't let this "
        "regress silently."
    )
