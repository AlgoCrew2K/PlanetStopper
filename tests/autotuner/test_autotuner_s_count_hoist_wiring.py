"""RED tests — Workstream E / AC-E2 + AC-E3 (end-to-end wiring).

AC-E2 (feature-plans/advisor-weekly-suggestions.md):
  "autotuner.py hoists the DoF-ledger sum (SELECT evidence_source,
   n_configs_searched ... WHERE spec_bundle_id = ?, currently at
   ~autotuner.py:2860-2865, computed too late) to BEFORE the save_autotune_run
   call (~autotuner.py:2837) and passes the summed S as s_count=. The
   current-run I-1/I-2 S computation and verdicts are unchanged (control-flow
   order change only)."

AC-E3:
  "Given >= 2 prior BACKTEST_SELECTION runs for a symphony with non-NULL,
   increasing s_count, overfitting_conscience sets drift_signal_available=True
   and Indicator-3 fires (WATCH-floor verdict)."

Why this is genuinely RED (not just a signature check): even after AC-E1 gives
save_autotune_run an s_count parameter, autotuner.py's actual call site at
~2837-2855 does NOT pass s_count= at all today — the DoF-ledger sum is computed
AFTER the save (lines 2860-2865) and only used for the IN-MEMORY OC call for
the CURRENT run (_oc_run["s_count"] = d_spec, never persisted). This means
every row in autotune_runs.s_count stays NULL forever, so a later run's
prior_runs query (autotuner.py:2876-2880, `SELECT id, symphony_id, s_count ...`)
always sees None for every prior row -- Indicator-3 (operator drift) can
structurally never fire on live data, even though the pure function
(overfitting_conscience.compute_overfitting_conscience_observation) already
handles non-NULL drift correctly (see tests/ai_advisor/test_overfitting_conscience.py).
These tests prove the WIRING gap end-to-end via real run_autotuner() calls
that persist to (and read back from) an isolated per-test SQLite DB.

Mocking strategy: Optuna (create_study/RDBStorage), synthetic_history, chart
history, and the VWAP breakdown helper are mocked (no live network, no real
optimization sweep — mirrors tests/autotuner/test_audit_fix_crra_neff_arch.py's
established harness). save_autotune_run and the OC/advisor_observations write
path are left REAL so the full DB round-trip is exercised — that round trip is
exactly what's broken today.
"""

from __future__ import annotations

import contextlib
import io
import json
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Shared fixture params: a single fake Optuna trial/study, minimal 10-day
# history (6 train / 2 validation / 2 frozen-eval at 60/20/20) — enough for
# run_autotuner to complete a full symphony cycle without live optimization.
# ---------------------------------------------------------------------------

_FAKE_TRIAL_PARAMS = {
    "TRIGGER_THRESHOLD_PCT": 15.0,
    "TAKE_PROFIT_MC_PCT": 5.0,
    "VWAP_CROSS_HWM_PCT": 0.51,
    "VWAP_BLEED_MULTIPLIER": 1.5,
    "VWAP_BLEED_TICKS": 10,
    "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
    "MAX_PARABOLIC_SQUEEZE": 0.5,
}

_TICK = {
    "return": 1.5,
    "mc_prob": 50.0,
    "vol": 1.2,
    "vwap_diff": 0.0,
    "base_atr_pct": 1.0,
    "valid_vwap_weight": 1.0,
}


def _make_phase1_theory_bundle() -> tuple[int, str]:
    """Insert an all-THEORY Phase-1 bundle; return (integer id, bundle_hash TEXT).

    Idempotent within a test's isolated DB (INSERT OR IGNORE), mirrors the
    established pattern in tests/autotuner/test_audit_fix_crra_neff_arch.py.
    """
    import database as _db

    canon_facets = {
        "gamma": "2.0",
        "utility_family": "CRRA",
        "wealth_argument": "compounded_return",
    }
    canonical_json = _db.canonicalize_facets_json(canon_facets)
    bundle_hash = _db.hash_facets_json(canonical_json)
    _db.insert_spec_bundle(bundle_hash=bundle_hash, facets_json=canonical_json)

    conn = _db.get_connection()
    bundle_id = conn.execute(
        "SELECT id FROM spec_bundles WHERE bundle_hash = ?", (bundle_hash,)
    ).fetchone()[0]
    conn.close()

    existing = _db.get_spec_facets_for_bundle(bundle_hash)
    existing_names = {r["facet_name"] for r in existing}
    for name, value in canon_facets.items():
        if name not in existing_names:
            _db.insert_spec_bundle_facet(
                bundle_hash=bundle_hash,
                facet_name=name,
                facet_value=value,
                freeze_discipline="THEORY",
                justification="advisor-rewire RED test — all-THEORY bundle",
            )
    return bundle_id, bundle_hash


@contextlib.contextmanager
def _mocked_autotuner_env():
    """Patch harness: fake Optuna study/trial + minimal history + DB helper
    stubs. save_autotune_run and the OC persistence path are left REAL."""
    import database as db_module

    fake_trial = MagicMock()
    fake_trial.value = 0.8
    fake_trial.user_attrs = {"daily_returns": [0.5, 1.0, -0.2, 0.3, 0.7]}
    fake_trial.params = _FAKE_TRIAL_PARAMS.copy()

    fake_study = MagicMock(name="fake_study")
    fake_study.best_params = fake_trial.params.copy()
    fake_study.best_value = 0.8
    fake_study.optimize = MagicMock(return_value=None)
    fake_study.trials = [fake_trial]

    history = {"sym-1": {f"2026-05-{d:02d}": [_TICK] for d in range(1, 11)}}

    with (
        patch("autotuner.optuna.create_study", return_value=fake_study),
        patch("autotuner.optuna.storages.RDBStorage", return_value=MagicMock()),
        patch("autotuner.synthetic_history.generate_synthetic_history", return_value=history),
        patch("autotuner.database.load_chart_history", return_value={}),
        patch("autotuner.database.save_chart_archive"),
        patch(
            "autotuner.database.get_symphony_strategy",
            return_value={"params": db_module.DEFAULT_STRATEGY.copy(), "locked_vars": []},
        ),
        patch("autotuner.database.DEFAULT_STRATEGY", db_module.DEFAULT_STRATEGY.copy()),
        patch(
            "autotuner.math_engine.compute_vwap_breakdown_update",
            side_effect=lambda **kw: (0, 0, False, False),
        ),
    ):
        yield


def _run_once(bot_state, spec_bundle_id):
    import autotuner

    buf = io.StringIO()
    with _mocked_autotuner_env(), contextlib.redirect_stdout(buf):
        autotuner.run_autotuner(
            bot_state,
            "2026-05-10",
            ["acc-1"],
            spec_bundle_id=spec_bundle_id,
        )


# ===========================================================================
# AC-E2 — the summed DoF-ledger S is persisted to autotune_runs.s_count
# ===========================================================================


class TestAutotunerPersistsNonNullSCount:
    def test_nn1_honest_run_persists_s_count_zero_not_null(self):
        """No BACKTEST_SELECTION ledger rows -> S=0. AC-E2 requires the computed
        (zero) sum to be PASSED to save_autotune_run, not omitted -- an omitted
        kwarg would leave the column NULL, indistinguishable from a legacy
        pre-023 row (migration 023 comment: 'NULL for legacy heuristic-path rows').
        """
        import database as db_module

        bot_state = {"sym-1": {"name": "S Count Honest Symphony", "account_uuid": "acc-1"}}
        _spec_bundle_id, _bundle_hash = _make_phase1_theory_bundle()

        _run_once(bot_state, _spec_bundle_id)

        conn = db_module.get_connection()
        row = conn.execute(
            "SELECT s_count FROM autotune_runs WHERE symphony_id = 's count honest symphony'"
        ).fetchone()
        conn.close()

        assert row is not None, "Expected an autotune_runs row after run_autotuner completes."
        assert row[0] == 0, (
            f"autotune_runs.s_count = {row[0]!r}, expected 0 (computed, honest NN1 case).\n"
            f"  AC-E2: run_autotuner must hoist the DoF-ledger sum computation and pass\n"
            f"  s_count=<sum> to save_autotune_run on every call, even when the sum is 0 —\n"
            f"  a NULL here means the column was never wired at all."
        )

    def test_run_with_backtest_selection_ledger_rows_persists_summed_s_count(self):
        """Seed one researcher_dof_ledger BACKTEST_SELECTION row tagged to the run's
        spec_bundle_id (bundle_hash) with n_configs_searched=9. AC-E2: the persisted
        autotune_runs.s_count must equal that sum (9), read back from the DB — not
        just present in the in-memory OC call, which already worked pre-fix."""
        import database as db_module

        bot_state = {"sym-1": {"name": "S Count Ledger Symphony", "account_uuid": "acc-1"}}
        spec_bundle_id, bundle_hash = _make_phase1_theory_bundle()

        db_module.insert_dof_ledger_row(
            facet_name="test_facet_s_count",
            facet_category="specification",
            decision_type="SEARCHED",
            evidence_source="BACKTEST_SELECTION",
            n_configs_searched=9,
            touched_frozen_eval=0,
            spec_bundle_id=bundle_hash,
        )

        _run_once(bot_state, spec_bundle_id)

        conn = db_module.get_connection()
        row = conn.execute(
            "SELECT s_count FROM autotune_runs WHERE symphony_id = 's count ledger symphony'"
        ).fetchone()
        conn.close()

        assert row is not None
        assert row[0] == 9, (
            f"autotune_runs.s_count = {row[0]!r}, expected 9 (SUM of n_configs_searched\n"
            f"  over BACKTEST_SELECTION researcher_dof_ledger rows matching this run's\n"
            f"  spec_bundle_id). AC-E2: the ledger-sum query at ~autotuner.py:2860-2865\n"
            f"  must be hoisted BEFORE save_autotune_run and its result passed as s_count=."
        )


# ===========================================================================
# AC-E3 — end-to-end: wiring E1+E2 makes Indicator-3 drift ACTUALLY fire
# ===========================================================================


class TestOperatorDriftFiresEndToEndAfterWiring:
    def test_three_consecutive_runs_with_growing_s_trigger_drift_observation(self):
        """Three consecutive run_autotuner() calls for the SAME symphony, each preceded
        by an additional BACKTEST_SELECTION ledger row tagged to the SAME spec bundle
        (so the ledger-sum accumulates: 2 -> 4 -> 6, strictly increasing across runs).

        AC-E3: after the THIRD run, the persisted OVERFITTING_CONSCIENCE
        advisor_observations row must show drift_signal_available=True AND the
        monotonic-drift verdict (raw_response['drift'] is True) -- proving the E1+E2
        wiring makes the ALREADY-CORRECT pure-function drift check (Indicator-3)
        reachable on real DB-backed prior_runs, not just in a synthetic prior_runs
        list. Before the fix, every prior autotune_runs.s_count is NULL forever, so
        drift_signal_available is structurally always False in production.
        """
        import database as db_module

        bot_state = {"sym-1": {"name": "S Count Drift Symphony", "account_uuid": "acc-1"}}
        spec_bundle_id, bundle_hash = _make_phase1_theory_bundle()

        # Three runs, each preceded by one more ledger row of the same weight (2),
        # so the cumulative sum strictly increases: 2, 4, 6.
        for _ in range(3):
            db_module.insert_dof_ledger_row(
                facet_name="test_facet_drift",
                facet_category="specification",
                decision_type="SEARCHED",
                evidence_source="BACKTEST_SELECTION",
                n_configs_searched=2,
                touched_frozen_eval=0,
                spec_bundle_id=bundle_hash,
            )
            _run_once(bot_state, spec_bundle_id)

        # Sanity precondition: s_count actually grew 2 -> 4 -> 6 in the DB (else the
        # drift assertion below would be vacuous — this locks AC-E2's contribution).
        conn = db_module.get_connection()
        s_counts = [
            r[0]
            for r in conn.execute(
                "SELECT s_count FROM autotune_runs WHERE symphony_id = 's count drift symphony' "
                "ORDER BY id ASC"
            ).fetchall()
        ]
        conn.close()
        assert s_counts == [2, 4, 6], (
            f"Precondition failed: expected s_count sequence [2, 4, 6] across the three "
            f"runs, got {s_counts!r}. The drift assertion below depends on this monotonic "
            f"growth actually landing in the DB (AC-E2)."
        )

        # The OVERFITTING_CONSCIENCE row from the THIRD run must show drift.
        conn = db_module.get_connection()
        obs_rows = conn.execute(
            "SELECT raw_response, verdict FROM advisor_observations "
            "WHERE advisor_role = 'OVERFITTING_CONSCIENCE' AND symphony_id = 's count drift symphony' "  # noqa: E501
            "ORDER BY id ASC"
        ).fetchall()
        conn.close()

        assert len(obs_rows) >= 3, (
            f"Expected an OVERFITTING_CONSCIENCE observation per run (>=3); got "
            f"{len(obs_rows)}."
        )
        last_raw = json.loads(obs_rows[-1][0])
        last_verdict = obs_rows[-1][1]

        assert last_raw.get("drift_signal_available") is True, (
            f"Third-run OVERFITTING_CONSCIENCE raw_response['drift_signal_available'] = "
            f"{last_raw.get('drift_signal_available')!r}, expected True.\n"
            f"  AC-E3: with >= 2 prior non-NULL, increasing s_count values in the DB "
            f"(2, 4 before this 6), Indicator-3 must be EVALUABLE.\n"
            f"  raw_response={last_raw!r}"
        )
        assert last_raw.get("drift") is True, (
            f"Third-run OVERFITTING_CONSCIENCE raw_response['drift'] = "
            f"{last_raw.get('drift')!r}, expected True (strictly increasing S series "
            f"2 -> 4 -> 6 must fire Indicator-3).\n  raw_response={last_raw!r}"
        )
        assert last_verdict in ("WATCH", "BREACH"), (
            f"Third-run OVERFITTING_CONSCIENCE verdict = {last_verdict!r}; drift floors "
            f"the verdict at WATCH per Indicator-3 (never CLEAR when drift fires)."
        )

    def test_null_s_count_priors_are_tolerated_no_crash(self):
        """AC-E4: historical rows with s_count IS NULL must be tolerated (drift needs
        >= 2 non-NULL priors; NULLs are skipped, never crash). Simulate a legacy prior
        row (pre-023, s_count NULL) coexisting with a fresh wired run — the fresh run
        must complete without raising, and its own s_count must still be honestly
        computed (not corrupted by the NULL neighbor)."""
        import database as db_module

        bot_state = {"sym-1": {"name": "S Count Null Tolerant Symphony", "account_uuid": "acc-1"}}
        spec_bundle_id, bundle_hash = _make_phase1_theory_bundle()

        # A legacy pre-023-style row: s_count explicitly NULL (omitted).
        db_module.save_autotune_run(
            run_timestamp="2026-05-01T00:00:00Z",
            symphony_id="s count null tolerant symphony",
            oos_alpha=0.1,
            train_alpha=0.2,
            baseline_decision="Adopted AI",
            fallback_oos_alpha=0.0,
            default_oos_alpha=0.0,
        )

        # Must not raise despite the NULL-s_count legacy neighbor.
        try:
            _run_once(bot_state, spec_bundle_id)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"run_autotuner raised {exc!r} with a NULL-s_count legacy prior row present "
                f"(AC-E4: NULLs must be tolerated, never crash)."
            )

        conn = db_module.get_connection()
        row = conn.execute(
            "SELECT s_count FROM autotune_runs WHERE symphony_id = 's count null tolerant symphony' "  # noqa: E501
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        assert row is not None and row[0] == 0, (
            f"The fresh (second) run's own s_count = {row[0]!r}, expected 0 (no ledger "
            f"evidence for this run) -- must not be corrupted by the NULL-s_count legacy "
            f"neighbor row."
        )
