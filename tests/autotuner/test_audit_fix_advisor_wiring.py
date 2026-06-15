"""
Sprint 3 audit-fix Cycle A — RED tests for the autotuner-side advisor wiring
fixes:

  S3-AUDIT-001 (BLOCKER, autotuner side): after run_autotuner completes, the
                OC observation row in advisor_observations MUST carry
                subject_id == str(autotune_runs.id) for the row just inserted —
                never the "0" sentinel.

  S3-AUDIT-002 (HIGH): run_divergence_explainer MUST be invoked from production
                code.  The chosen placement is autotuner.py post-OC (consistent
                with the other two producers).  Verified by patching
                advisors.divergence_explainer.run_divergence_explainer and
                asserting it is called.

  S3-AUDIT-003 (HIGH): the OC call site MUST supply prior_runs (a list of
                previous autotune_runs for the same symphony, excluding the
                just-inserted row).  Verified by seeding prior rows with
                monotonically growing s_count and asserting the persisted OC
                verdict is WATCH (Indicator-3 drift).

  S3-AUDIT-006 (MEDIUM): a producer exception (OC OR SC OR DE) MUST NOT abort
                run_autotuner.  The autotune_runs row must still be saved and
                the function must return.  Verified by injecting an
                insert_advisor_observation failure for one producer at a time
                and asserting the autotune_runs row survives.

Strategy:
  We exercise run_autotuner end-to-end through the same patch harness used in
  tests/autotuner/test_autotune_runs_persistence.py — the DB writes are real
  against a per-test tmpfile (the global _isolate_db fixture handles the path
  redirect).  Mocks cover Optuna, synthetic history, and the VWAP helper —
  never the math engine and never the advisor producers (we want to see the
  producers fire).

No hardcoded producer-computed values; assertions are on row existence,
subject_id format, verdict enum, and producer call counts.
"""

from __future__ import annotations

import contextlib
import inspect as _inspect
import io
import sqlite3
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Re-usable helpers — mirror tests/autotuner/test_autotune_runs_persistence.py
# but copied locally to avoid cross-file fixture coupling (project memory:
# Never share state across tests via module-level mutables).
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


def _make_phase1_spec_bundle() -> int:
    """Insert a minimal all-THEORY spec bundle and return its integer id."""
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
                justification="audit-fix RED — all-THEORY bundle",
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
    """Run run_autotuner via the patch harness.  Returns (result, output)."""
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


def _fetch_advisor_observations(role: str) -> list[tuple]:
    """Return raw advisor_observations rows for a given role from the live DB."""
    import os

    db_path = os.environ["DB_PATH"]
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT id, advisor_role, subject_type, subject_id, verdict "
        "FROM advisor_observations WHERE advisor_role = ? ORDER BY id ASC",
        (role,),
    ).fetchall()
    conn.close()
    return rows


def _normalized(symphony_name: str) -> str:
    """Apply the same lower-strip normalisation run_autotuner uses internally.

    run_autotuner calls database.normalize_name(...) on the bot_state symphony
    name before saving (autotuner.py:1456 binds normalized_name = lower+strip).
    The autotune_runs row carries the normalized form; readers and seeders in
    this test file must use the same form or queries miss every row.
    """
    import database as _db

    return _db.normalize_name(symphony_name)


def _fetch_autotune_runs_for_symphony(symphony_id: str) -> list[tuple]:
    """Return autotune_runs rows for the (normalized) symphony_id.

    Normalises the argument so callers can pass the user-facing name
    ("DefensiveAlpha") and the WHERE clause still matches the stored
    lowercased PK value.
    """
    import os

    db_path = os.environ["DB_PATH"]
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT id, symphony_id FROM autotune_runs WHERE symphony_id = ? ORDER BY id ASC",
        (_normalized(symphony_id),),
    ).fetchall()
    conn.close()
    return rows


# ===========================================================================
# S3-AUDIT-001 (E2E) — OC subject_id matches autotune_runs.id, never "0".
# ===========================================================================


def test_oc_observation_subject_id_matches_autotune_runs_id_after_real_cycle():
    """After a real run_autotuner cycle, the persisted OC observation MUST
    have subject_id == str(autotune_runs.id) for the just-inserted row.

    Today subject_id is always "0" because save_autotune_run returns None and
    _AUTOTUNE_RUNS_SELECT omits id.  This test pins the end-to-end repair.
    """
    _run_autotuner(symphony_name="DefensiveAlpha")

    runs = _fetch_autotune_runs_for_symphony("DefensiveAlpha")
    assert runs, "Expected at least one autotune_runs row for DefensiveAlpha"
    run_id, _sym = runs[-1]
    assert isinstance(run_id, int) and run_id > 0

    oc_rows = _fetch_advisor_observations("OVERFITTING_CONSCIENCE")
    assert oc_rows, (
        "Expected at least one OVERFITTING_CONSCIENCE observation after "
        "run_autotuner — the producer is wired at autotuner.py:1758."
    )
    # The last OC observation must reference the autotune_runs row id.
    _id, _role, subject_type, subject_id, _verdict = oc_rows[-1]
    assert subject_type == "autotune_run", (
        f"OC subject_type must be 'autotune_run'; got {subject_type!r}."
    )
    assert subject_id != "0", (
        f"OC subject_id is '0' — S3-AUDIT-001 not fixed.  The sentinel reached "
        f"the persisted row.  Last row: {oc_rows[-1]!r}."
    )
    assert subject_id == str(run_id), (
        f"OC subject_id={subject_id!r} does not match autotune_runs.id={run_id}. "
        "S3-AUDIT-001: save_autotune_run must return lastrowid and the autotuner "
        "must propagate it directly into _oc_run['id']."
    )


# ===========================================================================
# S3-AUDIT-002 — Divergence Explainer is invoked from production.
# ===========================================================================


def test_run_divergence_explainer_is_called_during_run_autotuner():
    """A production call site for run_divergence_explainer MUST exist.

    Verified by patching advisors.divergence_explainer.run_divergence_explainer
    and asserting it fires during a run_autotuner invocation.
    """
    import advisors.divergence_explainer as de_mod

    with patch.object(de_mod, "run_divergence_explainer", return_value=99) as mocked:
        _run_autotuner(symphony_name="DefensiveAlpha")

    assert mocked.called, (
        "advisors.divergence_explainer.run_divergence_explainer was NEVER called "
        "during run_autotuner.  S3-AUDIT-002: add a production call site (audit "
        "recommends autotuner.py post-OC mirror)."
    )


def test_divergence_explainer_persists_a_row_when_flag_off():
    """With SECOND_WINDOW_CVAR_ENABLED off (default), DE writes a NOT_APPLICABLE
    row for the audit trail.  Verified by reading advisor_observations after
    a run_autotuner cycle.
    """
    # Ensure flag is off for this test (clear any prior set).
    import os

    os.environ.pop("SECOND_WINDOW_CVAR_ENABLED", None)

    _run_autotuner(symphony_name="DefensiveAlpha")

    de_rows = _fetch_advisor_observations("DIVERGENCE_EXPLAINER")
    assert de_rows, (
        "No DIVERGENCE_EXPLAINER row written during run_autotuner.  S3-AUDIT-002 + "
        "divergence_explainer.py contract: even with the flag off, a NOT_APPLICABLE "
        "row must be persisted per cycle for the audit trail."
    )
    # On the off-flag path, the verdict must be NOT_APPLICABLE.
    _id, _role, _subject_type, _subject_id, verdict = de_rows[-1]
    assert verdict == "NOT_APPLICABLE", (
        f"DE flag-off verdict must be 'NOT_APPLICABLE'; got {verdict!r}."
    )


# ===========================================================================
# S3-AUDIT-003 — OC prior_runs supplied; Indicator-3 monotonic drift fires.
# ===========================================================================


def test_oc_prior_runs_supplied_so_drift_indicator_can_fire():
    """The autotuner MUST query prior autotune_runs for the same symphony and
    pass them as `prior_runs` to `run_overfitting_conscience`.

    S3-AUDIT-003 fix contract:
      (a) seed N>=2 prior autotune_runs rows for the same symphony;
      (b) call run_autotuner;
      (c) the OC call MUST receive a `prior_runs` arg whose length matches the
          seeded prior count and whose s_count values match what was seeded.

    Why this asserts via mock-inspection rather than downstream drift effects:
      In the patched test harness `d_spec=0` (no BACKTEST_SELECTION rows in the
      researcher_dof_ledger), so the producer's `s = max(ledger_S, s_count) = 0`
      for the new run.  A series like `[1, 2, 0]` is not strictly increasing,
      so `drift_detected=False` even though prior_runs WAS supplied — the data
      arithmetic conflates "prior_runs not passed" with "passed but not
      monotonic".  Asserting at the producer call boundary tests the wiring
      contract directly and is robust to OC's internal indicator algebra.
    """
    import advisors.overfitting_conscience as oc_mod

    # Seed 2 prior autotune_runs rows for the same (normalised) symphony with
    # distinct s_count values, so the autotuner's prior_runs SELECT can return
    # them and we can verify they reach the producer.
    import os

    db_path = os.environ["DB_PATH"]
    conn = sqlite3.connect(db_path)
    _norm = _normalized("DefensiveAlpha")
    conn.execute(
        "INSERT INTO autotune_runs (run_timestamp, symphony_id, oos_alpha, "
        "train_alpha, baseline_decision, fallback_oos_alpha, default_oos_alpha, "
        "s_count, math_mode) VALUES (?, ?, 0, 0, 'Adopted AI', 0, 0, ?, 'per_symphony')",
        ("2026-05-08T00:00:00Z", _norm, 1),
    )
    conn.execute(
        "INSERT INTO autotune_runs (run_timestamp, symphony_id, oos_alpha, "
        "train_alpha, baseline_decision, fallback_oos_alpha, default_oos_alpha, "
        "s_count, math_mode) VALUES (?, ?, 0, 0, 'Adopted AI', 0, 0, ?, 'per_symphony')",
        ("2026-05-09T00:00:00Z", _norm, 2),
    )
    conn.commit()
    conn.close()

    # Patch run_overfitting_conscience so we can inspect the prior_runs arg
    # the autotuner passes.  Return value is the (mocked) row id; the producer
    # itself never runs in this test — the contract under test is the call
    # signature from the autotuner side.
    captured: dict = {"prior_runs": None, "called": False}

    def _spy(autotune_run, ledger_rows, prior_runs=None):
        captured["called"] = True
        captured["prior_runs"] = prior_runs
        return 999  # arbitrary row id

    with patch.object(oc_mod, "run_overfitting_conscience", side_effect=_spy):
        _run_autotuner(symphony_name="DefensiveAlpha")

    assert captured["called"], (
        "advisors.overfitting_conscience.run_overfitting_conscience was not "
        "called from run_autotuner.  S3-AUDIT-001/002/003 prerequisite failed."
    )
    prior = captured["prior_runs"]
    assert prior is not None, (
        "run_overfitting_conscience was called with prior_runs=None.  "
        "S3-AUDIT-003: the autotuner MUST query autotune_runs for the same "
        "symphony and pass the result as prior_runs (kwarg) so Indicator-3 "
        "drift detection can fire."
    )
    # The autotuner's SELECT excludes the just-inserted id, so prior_runs must
    # contain exactly the 2 rows we seeded above (any additional internal-loop
    # rows would also count; the strict assertion is >= 2).
    assert len(prior) >= 2, (
        f"prior_runs length = {len(prior)} (got {prior!r}); expected >= 2 "
        "after seeding 2 prior autotune_runs rows for the same symphony.  "
        "S3-AUDIT-003: the prior_runs SELECT must return all prior same-symphony "
        "rows excluding the just-inserted one."
    )
    # The seeded s_count values must round-trip — confirms the SELECT projects
    # s_count and the row-to-dict normalisation surfaces it for the producer.
    # Both dict and sqlite3.Row support key access via r["s_count"].
    s_counts = []
    for r in prior:
        try:
            s_counts.append(r["s_count"])
        except (KeyError, IndexError):
            continue
    assert 1 in s_counts and 2 in s_counts, (
        f"prior_runs s_count values = {sorted(s_counts)}; expected the seeded "
        "values 1 and 2 to be present.  S3-AUDIT-003: the SELECT must "
        "project s_count for drift detection to work."
    )


# ===========================================================================
# S3-AUDIT-006 — Producer exceptions do NOT abort run_autotuner.
# ===========================================================================


def test_run_autotuner_survives_oc_producer_exception():
    """If the Overfitting Conscience producer raises, run_autotuner must still
    complete and the autotune_runs row must persist.

    Verified by patching advisors.overfitting_conscience.run_overfitting_conscience
    to raise; the autotune_runs row count after the cycle must be >= 1.
    """
    import advisors.overfitting_conscience as oc_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated OC failure")

    with patch.object(oc_mod, "run_overfitting_conscience", side_effect=_boom):
        # Must not raise — the audit's required wrap converts it to a logger.warning.
        _run_autotuner(symphony_name="DefensiveAlpha")

    rows = _fetch_autotune_runs_for_symphony("DefensiveAlpha")
    assert rows, (
        "OC producer raise should NOT abort run_autotuner — the autotune_runs row "
        "must persist.  S3-AUDIT-006: wrap producer calls in try/except → "
        "logger.warning('advisory only: %s', e)."
    )


def test_run_autotuner_survives_sc_producer_exception():
    """Spec Critic raise must not abort run_autotuner."""
    import advisors.spec_critic as sc_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated SC failure")

    with patch.object(sc_mod, "run_spec_critic", side_effect=_boom):
        _run_autotuner(symphony_name="DefensiveAlpha")

    rows = _fetch_autotune_runs_for_symphony("DefensiveAlpha")
    assert rows, "SC producer raise should NOT abort run_autotuner.  S3-AUDIT-006."


def test_run_autotuner_survives_de_producer_exception():
    """Divergence Explainer raise must not abort run_autotuner."""
    import advisors.divergence_explainer as de_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated DE failure")

    with patch.object(de_mod, "run_divergence_explainer", side_effect=_boom):
        _run_autotuner(symphony_name="DefensiveAlpha")

    rows = _fetch_autotune_runs_for_symphony("DefensiveAlpha")
    assert rows, "DE producer raise should NOT abort run_autotuner.  S3-AUDIT-006."
