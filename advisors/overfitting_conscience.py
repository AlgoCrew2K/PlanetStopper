"""Overfitting Conscience — Sprint 3 producer.

Computes an AdvisorObservation dict that characterises the overfitting risk
of a completed autotune run by examining the researcher_dof_ledger BACKTEST_SELECTION
accumulator (S counter) relative to the run's N_effective budget.

Three Phase-1 indicators:
  I-1  S > 0:  any BACKTEST_SELECTION row for the bundle → WATCH or BREACH.
  I-2  S/N_optuna ratio > 0.1 (strict):  escalate to BREACH.
       N_optuna = N_effective - S  (council synthesis §2.2).
  I-3  Operator drift: monotonically growing S across consecutive runs on the
       same symphony → WATCH (floor; escalated by I-2 when ratio also fires).

Wall integrity rule:
  All DB reads flow through database.advisor_ro_query (architecture constraint;
  enforced by CI lint test test_advisors_module_uses_advisor_ro_query).
  Direct connection access is prohibited — advisor_ro_query is the sole entry point.

Entry points:
  compute_overfitting_conscience_observation(autotune_run, ledger_rows, prior_runs=None)
      Pure function — no DB side-effects.  Returns an AdvisorObservation dict.

  run_overfitting_conscience(autotune_run, ledger_rows, prior_runs=None)
      Integration entry point: calls compute + persists via
      database.insert_advisor_observation.  Returns the new row id.

Reference: Sprint 3 dispatch brief; council synthesis §2.2, §2.5.
"""

from __future__ import annotations

import database

# ---------------------------------------------------------------------------
# Constants — Sprint 3 §I-2.  Named per no-magic-numbers rule.
# ---------------------------------------------------------------------------

# S/N_optuna ratio threshold above which verdict escalates to BREACH (strict >).
# council synthesis §2.5 NN1 spec-freeze hazard; Sprint 3 dispatch brief §I-2.
S_RATIO_BREACH_THRESHOLD = 0.10


# ---------------------------------------------------------------------------
# Pure computation
# ---------------------------------------------------------------------------

def compute_overfitting_conscience_observation(
    autotune_run: dict,
    ledger_rows: list[dict],
    prior_runs: list[dict] | None = None,
) -> dict:
    """Return an AdvisorObservation dict describing overfitting risk for one run.

    Parameters
    ----------
    autotune_run:
        Row dict from autotune_runs — must include 'id', 'symphony_id',
        'spec_bundle_id', 'n_effective', 's_count'.  Missing keys raise
        KeyError (pre-020 schema guard — a silent no-op would produce a
        false CLEAR verdict).
    ledger_rows:
        List of researcher_dof_ledger row dicts pre-fetched by the caller
        via advisor_ro_query.  Only rows whose spec_bundle_id matches the
        run's spec_bundle_id are counted.
    prior_runs:
        Optional list of earlier autotune_run row dicts for the same
        symphony (oldest-first).  Used only for Indicator 3 drift detection.
        Rows for other symphonies are silently skipped.
    """
    # --- Mandatory column access — KeyError is intentional on pre-020 rows ---
    run_id: int = autotune_run["id"]
    # S3-AUDIT-007: sentinel 0/None/negative would corrupt the audit trail.
    # A future regression of the save_autotune_run writer (S3-AUDIT-001) must
    # not silently produce subject_id="0" rows — the producer fails-noisy here.
    if run_id in (0, None) or (isinstance(run_id, int) and run_id <= 0):
        raise ValueError(
            f"autotune_run['id']={run_id!r} is invalid; the sentinel 0/None/negative "
            "would corrupt the audit trail — every OC observation would land with "
            "subject_id='0'.  Ensure save_autotune_run returns cursor.lastrowid (S3-AUDIT-001)."
        )
    symphony_id: str = autotune_run["symphony_id"]
    spec_bundle_id: str = autotune_run["spec_bundle_id"]
    n_effective: int = autotune_run["n_effective"]
    s_count: int = autotune_run["s_count"]

    # Normalise ledger rows: sqlite3.Row supports key access but not .get();
    # convert to plain dicts so both dict and sqlite3.Row inputs work uniformly.
    def _row_to_dict(r):
        if isinstance(r, dict):
            return r
        return dict(r)

    normalised_rows = [_row_to_dict(r) for r in ledger_rows]

    # --- I-1 / I-2: Filter ledger rows to this bundle; count S ---
    matching_rows = [
        r for r in normalised_rows
        if r.get("spec_bundle_id") == spec_bundle_id
        and r.get("evidence_source") == "BACKTEST_SELECTION"
    ]
    s = sum(r.get("n_configs_searched", 1) for r in matching_rows)
    # Fall back to s_count from the run row when ledger_rows are absent or sum to 0
    # and the stored s_count is authoritative.
    # Policy: use max(ledger-derived S, stored s_count) for robustness.
    s = max(s, s_count)

    facet_names = [r.get("facet_name", "") for r in matching_rows if r.get("facet_name")]

    n_optuna = max(n_effective - s, 1)  # guard divide-by-zero; N_optuna >= 1
    ratio = s / n_optuna if s > 0 else 0.0

    # --- I-3: Operator drift (monotonically growing S across same-symphony runs) ---
    drift_detected = False
    same_symphony_prior = [
        r for r in (prior_runs or [])
        if r.get("symphony_id") == symphony_id
    ]
    if len(same_symphony_prior) >= 2:
        s_series = [r["s_count"] for r in same_symphony_prior] + [s]
        drift_detected = all(
            s_series[i] < s_series[i + 1] for i in range(len(s_series) - 1)
        )

    # --- Verdict resolution ---
    if s == 0:
        verdict = "CLEAR"
    elif ratio > S_RATIO_BREACH_THRESHOLD:
        verdict = "BREACH"
    else:
        verdict = "WATCH"

    # Drift alone (I-3) floors at WATCH when verdict would otherwise be CLEAR.
    # In practice drift implies S > 0 (which already fires I-1), but guard it.
    if drift_detected and verdict == "CLEAR":
        verdict = "WATCH"

    # --- raw_response ---
    if verdict == "CLEAR":
        raw_response: dict = {
            "s_count": 0,
            "backtest_selection_count": 0,
            "n_effective": n_effective,
            "n_optuna": n_optuna,
            "note": "no BACKTEST_SELECTION facets; N_effective equals N_optuna",
        }
    else:
        raw_response = {
            "s_count": s,
            "backtest_selection_count": s,
            "facet_names": facet_names,
            "facets": facet_names,
            "n_effective": n_effective,
            "n_optuna": n_optuna,
            "ratio": ratio,
            "s_n_optuna_ratio": ratio,
            "threshold": S_RATIO_BREACH_THRESHOLD,
            "breach_threshold": S_RATIO_BREACH_THRESHOLD,
        }
        if drift_detected:
            s_series_for_log = [r["s_count"] for r in same_symphony_prior] + [s]
            raw_response["drift"] = True
            raw_response["monotonic_trend"] = s_series_for_log
            raw_response["drift_note"] = (
                f"monotonically growing S across {len(s_series_for_log)} runs: "
                + " -> ".join(str(v) for v in s_series_for_log)
            )

    return {
        "advisor_role": "OVERFITTING_CONSCIENCE",
        "subject_type": "autotune_run",
        "subject_id": str(run_id),
        "verdict": verdict,
        "raw_response": raw_response,
        "is_advisory_only": 1,
        "spec_bundle_id": spec_bundle_id,
    }


# ---------------------------------------------------------------------------
# Integration entry point
# ---------------------------------------------------------------------------

def run_overfitting_conscience(
    autotune_run: dict,
    ledger_rows: list[dict],
    prior_runs: list[dict] | None = None,
) -> int:
    """Compute the overfitting observation and persist it via insert_advisor_observation.

    Returns the new advisor_observations row id.

    DB reads (if any) must go through database.advisor_ro_query — this function
    does not open connections directly (wall integrity contract).
    """
    obs = compute_overfitting_conscience_observation(autotune_run, ledger_rows, prior_runs)
    row_id: int = database.insert_advisor_observation(
        advisor_role=obs["advisor_role"],
        subject_type=obs["subject_type"],
        subject_id=obs["subject_id"],
        verdict=obs["verdict"],
        raw_response=obs["raw_response"],
        spec_bundle_id=obs["spec_bundle_id"],
        symphony_id=autotune_run.get("symphony_id"),
    )
    return row_id
