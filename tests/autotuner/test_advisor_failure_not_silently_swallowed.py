"""advisor-fix cycle — RED: a producer-persistence failure must be SURFACED.

AC-3 (backend half): the producer-failure path at autotuner.py:2620-2622 currently
does ``except Exception as e: logging.warning(...)`` — a SILENT swallow at WARNING
that is exactly how the broken advisor shipped (every OC write threw a TypeError and
nobody noticed for 1678 runs).

The contract this file pins:
  1. A producer raise must NOT abort run_autotuner (the autotune_runs row still
     persists) — this preserves the existing S3-AUDIT-006 guarantee.
  2. BUT the failure must be SURFACED at a visible severity (logging.error /
     logging.exception), WITH the exception type in the message, so it can never
     again rot silently. A bare logging.warning that drops the exception type is
     NOT acceptable.

We reuse the established run_autotuner patch harness from
tests/autotuner/test_audit_fix_advisor_wiring.py (copied locally — project memory:
never share state across tests via module-level mutables). The math engine and the
advisor producers are NOT mocked except the single producer we force to raise.
"""

from __future__ import annotations

import contextlib
import inspect as _inspect
import io
import logging
import sqlite3
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Local harness — mirrors test_audit_fix_advisor_wiring.py (no cross-file coupling).
# ---------------------------------------------------------------------------


def _build_bot_state(symphony_name: str = "DefensiveAlpha") -> dict:
    return {"sym-1": {"name": symphony_name, "account_uuid": "acc-1"}}


def _build_history(n_days: int = 5) -> dict:
    tick = {
        "return": 2.0,
        "mc_prob": 50.0,
        "vol": 1.5,
        "vwap_diff": 0.0,
        "base_atr_pct": 1.0,
        "valid_vwap_weight": 1.0,
    }
    dates = [f"2026-05-{d:02d}" for d in range(1, n_days + 1)]
    return {"sym-1": {d: [tick] for d in dates}}


def _fallback_params() -> dict:
    import database

    return database.DEFAULT_STRATEGY.copy()


def _ai_best_params() -> dict:
    return {
        "TRIGGER_THRESHOLD_PCT": 15.0,
        "TAKE_PROFIT_MC_PCT": 5.0,
        "VWAP_CROSS_HWM_PCT": 0.51,
        "VWAP_BLEED_MULTIPLIER": 1.5,
        "VWAP_BLEED_TICKS": 10,
        "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
        "MAX_PARABOLIC_SQUEEZE": 0.5,
    }


def _no_trigger_vwap_side_effect(**kwargs):
    return (0, 0, False, False)


def _make_phase1_spec_bundle():
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
                justification="advisor-fix RED — all-THEORY bundle",
            )
    return bundle_id, bundle_hash


@contextlib.contextmanager
def _autotuner_patches(best_params, fallback, vwap_side_effect=None):
    if vwap_side_effect is None:
        vwap_side_effect = _no_trigger_vwap_side_effect
    import database

    fake_study = MagicMock(name="fake_optuna_study")
    fake_study.best_params = best_params
    fake_study.best_value = 1.0
    fake_study.optimize = MagicMock(return_value=None)

    history = _build_history(n_days=5)

    with (
        patch("autotuner.optuna.create_study", return_value=fake_study),
        patch("autotuner.optuna.storages.RDBStorage", return_value=MagicMock()),
        patch("autotuner.synthetic_history.generate_synthetic_history", return_value=history),
        patch("autotuner.database.load_chart_history", return_value={}),
        patch("autotuner.database.save_chart_archive"),
        patch(
            "autotuner.database.get_symphony_strategy",
            return_value={"params": fallback.copy(), "locked_vars": []},
        ),
        patch("autotuner.database.DEFAULT_STRATEGY", database.DEFAULT_STRATEGY.copy()),
        patch("autotuner.math_engine.compute_vwap_breakdown_update", side_effect=vwap_side_effect),
    ):
        yield {"fake_study": fake_study}


def _run_autotuner(symphony_name: str = "DefensiveAlpha"):
    import autotuner

    bot_state = _build_bot_state(symphony_name)
    spec_bundle_id, bundle_hash = _make_phase1_spec_bundle()
    buf = io.StringIO()

    sig = _inspect.signature(autotuner.run_autotuner)
    extra = {"spec_bundle_id": spec_bundle_id} if "spec_bundle_id" in sig.parameters else {}

    with _autotuner_patches(_ai_best_params(), _fallback_params()):
        with contextlib.redirect_stdout(buf):
            result = autotuner.run_autotuner(bot_state, "2026-05-10", ["acc-1"], **extra)
    return result, buf.getvalue(), bundle_hash


def _fetch_autotune_runs_for_symphony(symphony_name: str) -> list[tuple]:
    import os
    import database as _db

    db_path = os.environ["DB_PATH"]
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT id, symphony_id FROM autotune_runs WHERE symphony_id = ? ORDER BY id ASC",
        (_db.normalize_name(symphony_name),),
    ).fetchall()
    conn.close()
    return rows


# ===========================================================================
# AC-3 backend — OC producer failure is surfaced (visible) AND non-aborting.
# ===========================================================================


def test_oc_producer_failure_is_logged_at_visible_severity(caplog):
    """When run_overfitting_conscience raises, run_autotuner must log the failure
    at ERROR (or via logging.exception) — never silently at WARNING with the
    exception type dropped.

    The shipped bug was a WARNING that nobody read for 1678 runs. The fix must
    raise the visibility so the failure surfaces in the daemon log and in any
    error-level alerting.
    """
    import advisors.overfitting_conscience as oc_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated OC persistence failure")

    with caplog.at_level(logging.WARNING):
        with patch.object(oc_mod, "run_overfitting_conscience", side_effect=_boom):
            _run_autotuner(symphony_name="DefensiveAlpha")

    # There must be at least one record about the failure.
    oc_records = [
        r
        for r in caplog.records
        if "overfitting" in r.getMessage().lower()
        or "OVERFITTING" in r.getMessage()
        or "advisory" in r.getMessage().lower()
    ]
    assert oc_records, (
        "No log record surfaced for the OC producer failure. The exception was "
        "swallowed silently — AC-3 requires the failure to be surfaced."
    )
    # At least one of those records must be ERROR-level (or carry exc_info).
    surfaced = [r for r in oc_records if r.levelno >= logging.ERROR or r.exc_info is not None]
    assert surfaced, (
        "The OC producer failure was logged only at WARNING with no exc_info — "
        "that is the silent-swallow pattern that shipped the broken advisor. "
        "AC-3: log at logging.error / logging.exception so it cannot rot silently."
    )
    # The surfaced record must name the exception (type or message) so an operator
    # can act — a bare 'advisory only' string is not actionable.
    assert any(
        "RuntimeError" in r.getMessage()
        or "simulated OC persistence failure" in r.getMessage()
        or (r.exc_info is not None)
        for r in surfaced
    ), (
        "The surfaced failure record does not carry the exception type/message "
        "(or exc_info). Include the underlying error so the failure is diagnosable."
    )


def test_oc_producer_failure_does_not_abort_run_autotuner(caplog):
    """Surfacing the failure must NOT regress S3-AUDIT-006: the autotune_runs row
    must still persist and run_autotuner must still return.
    """
    import advisors.overfitting_conscience as oc_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated OC persistence failure")

    with patch.object(oc_mod, "run_overfitting_conscience", side_effect=_boom):
        # Must not raise out of run_autotuner.
        result, _out, _hash = _run_autotuner(symphony_name="DefensiveAlpha")

    rows = _fetch_autotune_runs_for_symphony("DefensiveAlpha")
    assert rows, (
        "The autotune_runs row did not persist after an OC producer raise. "
        "Surfacing the failure must not abort the cycle (S3-AUDIT-006)."
    )
