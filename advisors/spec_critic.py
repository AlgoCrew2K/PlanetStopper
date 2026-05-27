"""Spec Critic — Sprint 3 producer.

Computes an AdvisorObservation dict that critiques the structural integrity of a
Phase-1 spec bundle by examining its spec_facets rows for four indicators:

  I-1  Required Phase-1 THEORY facets present: gamma, utility_family,
       wealth_argument.  Any missing facet → BREACH.
  I-2  All facets have a recognised freeze_discipline (in NN1_HONEST_DISCIPLINES
       union {BACKTEST_SELECTION}).  Any unrecognised discipline → BREACH
       (default-deny; forward-compat defence-in-depth).
  I-3  All facet frozen_at timestamps are < SPEC_AGE_WATCH_THRESHOLD_DAYS old.
       Exactly-at or beyond threshold → WATCH (advisory; does not block).
  I-4  No Phase-2 facets seeded prematurely (PHASE2_FACET_NAMES).  Any Phase-2
       facet present → BREACH ("phase scope leak").

Verdict resolution (priority highest→lowest):
  BREACH  — I-1, I-2, or I-4 fires.
  WATCH   — I-3 fires and no BREACH condition.
  CLEAR   — no indicators fire.

Wall integrity rule:
  All DB reads flow through database.advisor_ro_query (architecture constraint;
  enforced by CI lint test test_advisors_module_uses_advisor_ro_query).
  Direct connection access is prohibited — advisor_ro_query is the sole entry point.

Entry points:
  compute_spec_critic_observation(spec_bundle_id, spec_facets_rows, _now=None)
      Pure function — no DB side-effects.  Returns an AdvisorObservation dict.

  run_spec_critic(spec_bundle_id, spec_facets_rows, _now=None)
      Integration entry point: calls compute + persists via
      database.insert_advisor_observation.  Returns the new row id.

Reference: Sprint 3 dispatch brief; council synthesis §2.5.
"""

from __future__ import annotations

import datetime

import database

# ---------------------------------------------------------------------------
# Constants — Sprint 3 Spec Critic indicators.  Named per no-magic-numbers rule.
# ---------------------------------------------------------------------------

# Phase-1 THEORY facets that must be present in any valid spec bundle.
# council synthesis §2.5: gamma, utility_family, wealth_argument are the
# three canonical Phase-1 THEORY dimensions.
PHASE1_REQUIRED_FACETS: frozenset = frozenset({
    "gamma",
    "utility_family",
    "wealth_argument",
})

# Phase-2 facets that must not appear in a Phase-1 bundle ("phase scope leak").
# Sprint 3 dispatch brief; council synthesis §2.5 Phase-2 facets.
PHASE2_FACET_NAMES: frozenset = frozenset({
    "lambda",
    "hysteresis-threshold",
})

# Age threshold (inclusive): facets frozen exactly this many days ago or earlier
# trigger WATCH (spec age risk advisory).
# Sprint 3 dispatch brief §I-3.
SPEC_AGE_WATCH_THRESHOLD_DAYS: int = 90

# Disciplines that are acceptable in a Phase-1 spec bundle.
# Default-deny: any freeze_discipline NOT in this set (including BACKTEST_SELECTION
# and unknown forward-compat values) triggers BREACH (I-2 defense-in-depth).
# council synthesis §2.5; Sprint 3 dispatch brief.
_ACCEPTABLE_DISCIPLINES: frozenset = frozenset({
    "THEORY",
    "MANDATE",
    "STYLIZED_FACT",
    "POLITIS_WHITE",
    "CADENCE",
    "CALIBRATION",
})


# ---------------------------------------------------------------------------
# Pure computation
# ---------------------------------------------------------------------------

def compute_spec_critic_observation(
    spec_bundle_id: str,
    spec_facets_rows: list[dict],
    _now: "datetime.datetime | None" = None,
) -> dict:
    """Return an AdvisorObservation dict critiquing the Phase-1 spec bundle.

    Parameters
    ----------
    spec_bundle_id:
        The bundle_hash (TEXT) identifying the spec bundle under review.
        Used as subject_id and spec_bundle_id in the returned observation.
    spec_facets_rows:
        List of spec_facets row dicts pre-fetched by the caller via
        advisor_ro_query.  Each dict must include at minimum:
          'facet_name', 'freeze_discipline', 'frozen_at' (ISO-8601 string).
        sqlite3.Row objects are normalised to plain dicts automatically.
    _now:
        The reference datetime against which staleness is computed.  Defaults
        to datetime.datetime.now(timezone.utc).  Injected in tests to produce
        deterministic boundary behaviour.
    """
    if _now is None:
        _now = datetime.datetime.now(datetime.timezone.utc)
    reference_date = _now.date() if hasattr(_now, "date") else _now

    # Normalise rows: sqlite3.Row supports key access but not .get();
    # convert to plain dicts so both dict and sqlite3.Row inputs work uniformly.
    def _to_dict(r):
        if isinstance(r, dict):
            return r
        return dict(r)

    rows = [_to_dict(r) for r in spec_facets_rows]

    # --- I-1: Required Phase-1 THEORY facets present ---
    present_facets = {r.get("facet_name") for r in rows}
    missing_facets = sorted(PHASE1_REQUIRED_FACETS - present_facets)

    # --- I-2: All freeze_disciplines recognised (default-deny) ---
    unrecognised_disciplines = [
        r for r in rows
        if r.get("freeze_discipline") not in _ACCEPTABLE_DISCIPLINES
    ]

    # --- I-3: Spec age — oldest frozen_at across all facets ---
    stale = False
    for r in rows:
        frozen_at_str = r.get("frozen_at")
        if not frozen_at_str:
            continue
        try:
            # Accept ISO-8601 date or datetime strings.
            if "T" in frozen_at_str or " " in frozen_at_str:
                sep = "T" if "T" in frozen_at_str else " "
                facet_date = datetime.date.fromisoformat(frozen_at_str.split(sep)[0])
            else:
                facet_date = datetime.date.fromisoformat(frozen_at_str)
        except (ValueError, AttributeError):
            continue
        age_days = (reference_date - facet_date).days
        if age_days >= SPEC_AGE_WATCH_THRESHOLD_DAYS:
            stale = True
            break

    # --- I-4: No Phase-2 facets present (phase scope leak) ---
    phase2_leaks = sorted(present_facets & PHASE2_FACET_NAMES)

    # --- Verdict resolution ---
    breach = bool(missing_facets or unrecognised_disciplines or phase2_leaks)

    if breach:
        verdict = "BREACH"
    elif stale:
        verdict = "WATCH"
    else:
        verdict = "CLEAR"

    # --- raw_response ---
    if verdict == "CLEAR":
        raw_response: dict = {
            "note": (
                "all 3 facets present, all THEORY, frozen<90d"
            ),
        }
    else:
        raw_response = {}
        if missing_facets:
            raw_response["missing_facets"] = missing_facets
        if unrecognised_disciplines:
            raw_response["unrecognised_disciplines"] = [
                {
                    "facet_name": r.get("facet_name"),
                    "freeze_discipline": r.get("freeze_discipline"),
                }
                for r in unrecognised_disciplines
            ]
        if phase2_leaks:
            raw_response["phase2_scope_leak"] = phase2_leaks
        if stale and not breach:
            raw_response["stale_note"] = (
                f"facet frozen_at >= {SPEC_AGE_WATCH_THRESHOLD_DAYS} days old; "
                "spec age advisory"
            )

    return {
        "advisor_role": "SPEC_CRITIC",
        "subject_type": "spec_bundle",
        "subject_id": str(spec_bundle_id),
        "verdict": verdict,
        "raw_response": raw_response,
        "is_advisory_only": 1,
        "spec_bundle_id": str(spec_bundle_id),
    }


# ---------------------------------------------------------------------------
# Integration entry point
# ---------------------------------------------------------------------------

def run_spec_critic(
    spec_bundle_id: str,
    spec_facets_rows: list[dict],
    _now: "datetime.datetime | None" = None,
) -> int:
    """Compute the spec observation and persist it via insert_advisor_observation.

    Returns the new advisor_observations row id.

    DB reads (if any) must go through database.advisor_ro_query — this function
    does not open connections directly (wall integrity contract).
    """
    obs = compute_spec_critic_observation(spec_bundle_id, spec_facets_rows, _now)
    row_id: int = database.insert_advisor_observation(
        advisor_role=obs["advisor_role"],
        subject_type=obs["subject_type"],
        subject_id=obs["subject_id"],
        verdict=obs["verdict"],
        raw_response=obs["raw_response"],
        spec_bundle_id=obs["spec_bundle_id"],
    )
    return row_id
