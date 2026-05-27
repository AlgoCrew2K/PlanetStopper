"""Divergence Explainer — Sprint 3 Stream B producer.

Computes an AdvisorObservation dict that surfaces two independent CVaR window
values for operator visibility. This is the sole surviving residue of the
CVaR-divergence idea evaluation (decision-science-v3 §B.6).

CRITICAL ARCHITECTURAL CONSTRAINT (binding, permanent):
  The observation MUST contain two honest CVaR values, each rendered
  independently under its own full four-part S-3 contract.
  The observation MUST NOT contain any signed divergence quantity — no
  arithmetic difference, ratio, or threshold-shaped affordance between
  the two windows. The divergence idea was REJECTED (decision-science council
  §B.9); this module carries the rejection into every produced row.

  Forbidden raw_response keys: "divergence", "signed_divergence",
  "cvar_diff", "cvar_delta", "window_divergence", "divergence_pct", "delta",
  and any semantic equivalent (spread, gap, difference, diff, etc.).

Feature flag (SECOND_WINDOW_CVAR_ENABLED):
  §B is NOT YET ENABLED in production. Default: off.
  When off: writes one NOT_APPLICABLE row for the audit trail.
  When on:  writes one INFORMATIONAL row with both window values.

Wall integrity rule:
  All DB reads flow through database.advisor_ro_query (architecture constraint;
  enforced by CI lint test test_advisors_module_uses_advisor_ro_query).
  Direct connection access is prohibited — advisor_ro_query is the sole
  approved read path for all DB reads in this module.

Entry points:
  compute_divergence_explainer_observation(autotune_run, cvar_row, *, second_window_enabled)
      Pure function — no DB side-effects. Returns an AdvisorObservation dict.

  run_divergence_explainer(autotune_run, cvar_row, *, second_window_enabled=None)
      Integration entry point: resolves the feature flag from the env var when
      second_window_enabled is None, fetches the cvar_diagnostics row via
      database.advisor_ro_query when cvar_row is None and §B is on, then
      calls compute + persists via database.insert_advisor_observation.
      Returns the new row id.

Reference: Sprint 3 dispatch brief §B; decision-science-v3 §B.6–B.9;
           tests/fixtures/math/divergence_explainer_scenarios.json.
"""

from __future__ import annotations

import os

import database

# ---------------------------------------------------------------------------
# Constants — feature flag name per fixture provenance.
# ---------------------------------------------------------------------------

# Environment variable that gates §B second-window CVaR visibility.
# Default: disabled ("0" or absent). Set to "1" to enable.
# decision-science-v3 §B.6: operator-optional residue, no architecture unlocked.
_FLAG_ENV_VAR = "SECOND_WINDOW_CVAR_ENABLED"


# ---------------------------------------------------------------------------
# Pure computation
# ---------------------------------------------------------------------------

def compute_divergence_explainer_observation(
    autotune_run: dict,
    cvar_row: "dict | None",
    *,
    second_window_enabled: bool,
) -> dict:
    """Return an AdvisorObservation dict carrying two independent CVaR window values.

    Parameters
    ----------
    autotune_run:
        Row dict from autotune_runs — must include 'id'. Missing 'id' raises
        KeyError intentionally: a silent no-op would corrupt the audit trail
        with subject_id='None'.
    cvar_row:
        Most-recent cvar_diagnostics row dict for the symphony, or None when
        no diagnostic row exists. Pre-fetched by the caller (no DB access here).
        Expected keys when present: cvar_5pct, cvar_n_tail, cvar_5pct_long,
        cvar_n_tail_long.
    second_window_enabled:
        Whether §B is active. False → NOT_APPLICABLE stub row. True → INFORMATIONAL
        row with two honest window values.

    Returns
    -------
    dict
        AdvisorObservation with keys: advisor_role, subject_type, subject_id,
        verdict, raw_response, is_advisory_only, spec_bundle_id.
    """
    # Mandatory column access — KeyError is intentional on a missing 'id'.
    # A silent no-op would produce subject_id='None', corrupting the audit trail.
    run_id: int = autotune_run["id"]
    spec_bundle_id: "str | None" = autotune_run.get("spec_bundle_id")

    if not second_window_enabled:
        # §B is off: write a minimal NOT_APPLICABLE row for the audit trail.
        return {
            "advisor_role": "DIVERGENCE_EXPLAINER",
            "subject_type": "autotune_run",
            "subject_id": str(run_id),
            "verdict": "NOT_APPLICABLE",
            "raw_response": {"feature_flag": "off"},
            "is_advisory_only": 1,
            "spec_bundle_id": spec_bundle_id,
        }

    # §B is on: surface both CVaR windows independently. NEVER compute a
    # signed difference — the divergence idea is permanently rejected (§B.9).
    if cvar_row is not None:
        short_cvar = cvar_row.get("cvar_5pct")
        short_tail = cvar_row.get("cvar_n_tail")
        long_cvar = cvar_row.get("cvar_5pct_long")
        long_tail = cvar_row.get("cvar_n_tail_long")
    else:
        short_cvar = None
        short_tail = None
        long_cvar = None
        long_tail = None

    # Each window value is presented honestly under its own S-3 contract.
    # No derived field, ratio, or signed difference is included.
    raw_response: dict = {
        "short_window_cvar_pct": short_cvar,
        "short_window_tail_obs": short_tail,
        "long_window_cvar_pct": long_cvar,
        "long_window_tail_obs": long_tail,
    }

    return {
        "advisor_role": "DIVERGENCE_EXPLAINER",
        "subject_type": "autotune_run",
        "subject_id": str(run_id),
        "verdict": "INFORMATIONAL",
        "raw_response": raw_response,
        "is_advisory_only": 1,
        "spec_bundle_id": spec_bundle_id,
    }


# ---------------------------------------------------------------------------
# Integration entry point
# ---------------------------------------------------------------------------

def run_divergence_explainer(
    autotune_run: dict,
    cvar_row: "dict | None",
    *,
    second_window_enabled: "bool | None" = None,
) -> int:
    """Compute the divergence-explainer observation and persist it.

    Reads SECOND_WINDOW_CVAR_ENABLED from the environment when
    second_window_enabled is None. Explicit kwarg overrides the env var —
    this is the seam for deterministic unit testing.

    Returns the new advisor_observations row id.

    DB reads (if any) must go through database.advisor_ro_query — this function
    does not open connections directly (wall integrity contract).
    """
    if second_window_enabled is None:
        flag_raw = os.environ.get(_FLAG_ENV_VAR, "0")
        second_window_enabled = flag_raw.strip() == "1"

    # When §B is on and the caller did not supply a pre-fetched CVaR row,
    # fetch the most-recent cvar_diagnostics row via the approved read path.
    # advisor_ro_query is the sole approved DB read path (wall integrity contract).
    if second_window_enabled and cvar_row is None:
        symphony_id = autotune_run.get("symphony_id")
        if symphony_id:
            rows = database.advisor_ro_query(
                "SELECT cvar_5pct, cvar_5pct_stderr, cvar_n_tail, "
                "cvar_5pct_long, cvar_n_tail_long, cycle_id, ts_utc "
                "FROM cvar_diagnostics "
                "WHERE symphony_id = ? "
                "ORDER BY ts_utc DESC LIMIT 1",
                (symphony_id,),
            )
            cvar_row = rows[0] if rows else None

    obs = compute_divergence_explainer_observation(
        autotune_run,
        cvar_row,
        second_window_enabled=second_window_enabled,
    )
    row_id: int = database.insert_advisor_observation(
        advisor_role=obs["advisor_role"],
        subject_type=obs["subject_type"],
        subject_id=obs["subject_id"],
        verdict=obs["verdict"],
        raw_response=obs["raw_response"],
        spec_bundle_id=obs["spec_bundle_id"],
    )
    return row_id
