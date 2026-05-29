# advisors/spec_critic

> Sprint 3 producer that critiques the structural integrity of a Phase-1 spec bundle by checking facet completeness, freeze-discipline validity, spec age, and phase-scope leaks.

**Source:** `advisors/spec_critic.py`
**Last updated:** 2026-05-27

## Overview

The Spec Critic is one of three Sprint 3 Advisor producers. It runs post-walk-forward alongside the Overfitting Conscience. It checks the `spec_facets` rows for the winning bundle against four structural indicators and produces an immutable `advisor_observations` row.

Four Phase-1 indicators (priority highest → lowest):

- **I-1** — Required Phase-1 THEORY facets present: `gamma`, `utility_family`, `wealth_argument`. Any missing → BREACH.
- **I-2** — All facets have a recognised `freeze_discipline` (one of `THEORY`, `MANDATE`, `STYLIZED_FACT`, `POLITIS_WHITE`, `CADENCE`, `CALIBRATION`). Default-deny: any unrecognized discipline (including `BACKTEST_SELECTION` and unknown forward-compat values) → BREACH.
- **I-3** — All facet `frozen_at` timestamps are younger than `SPEC_AGE_WATCH_THRESHOLD_DAYS` (90 days). Exactly-at or beyond → WATCH (advisory; does not block).
- **I-4** — No Phase-2 facets (`lambda`, `hysteresis-threshold`) present in the bundle ("phase scope leak") → BREACH.

**Wall integrity rule:** All DB reads must go through `database.advisor_ro_query`. Direct connection access is prohibited.

## API Reference

### `compute_spec_critic_observation(spec_bundle_id: str, spec_facets_rows: list[dict], _now: datetime | None = None) → dict`

Pure function. Returns an `AdvisorObservation` dict critiquing the Phase-1 spec bundle.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `spec_bundle_id` | `str` | The bundle_hash TEXT identifying the spec bundle under review |
| `spec_facets_rows` | `list[dict]` | `spec_facets` rows pre-fetched via `advisor_ro_query`. Must include `facet_name`, `freeze_discipline`, `frozen_at` |
| `_now` | `datetime \| None` | Reference datetime for staleness computation. Injected in tests for deterministic behaviour; defaults to `datetime.now(UTC)` |

**Returns:** `dict` with keys:
- `advisor_role`: `"SPEC_CRITIC"`
- `subject_type`: `"spec_bundle"`
- `subject_id`: `str(spec_bundle_id)`
- `verdict`: `"CLEAR"` | `"WATCH"` | `"BREACH"`
- `raw_response`: dict with `missing_facets`, `unrecognised_disciplines`, `phase2_scope_leak`, `stale_note` when applicable
- `is_advisory_only`: `1`
- `spec_bundle_id`: `str(spec_bundle_id)`

**Example:**
```python
obs = compute_spec_critic_observation(
    spec_bundle_id=bundle_hash,
    spec_facets_rows=advisor_ro_query("SELECT facet_name, freeze_discipline, frozen_at FROM spec_facets WHERE bundle_hash = ?", (bundle_hash,)),
)
```

---

### `run_spec_critic(spec_bundle_id: str, spec_facets_rows: list[dict], _now: datetime | None = None, symphony_id: str | None = None) → int`

Integration entry point. Calls `compute_spec_critic_observation` then persists the result via `database.insert_advisor_observation`.

`symphony_id` is forwarded to `insert_advisor_observation` so the `/api/advisor-observations?symphony_id=` filter can locate Spec Critic rows by symphony name (S3-AUDIT-004).

**Returns:** `int` — the new `advisor_observations` row id.

## Types

### Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `PHASE1_REQUIRED_FACETS` | `frozenset{"gamma", "utility_family", "wealth_argument"}` | Phase-1 THEORY facets that must be present |
| `PHASE2_FACET_NAMES` | `frozenset{"lambda", "hysteresis-threshold"}` | Phase-2 facets that must not appear in a Phase-1 bundle |
| `SPEC_AGE_WATCH_THRESHOLD_DAYS` | `90` | Facets frozen this many or more days ago trigger WATCH |
| `_ACCEPTABLE_DISCIPLINES` | frozenset | Set of acceptable `freeze_discipline` values (default-deny for I-2) |

### Verdict Logic

| Condition | Verdict |
|-----------|---------|
| I-1, I-2, or I-4 fires | `BREACH` |
| I-3 fires and no BREACH | `WATCH` |
| No indicators fire | `CLEAR` |

## Internal Dependencies

- `database` — `insert_advisor_observation`, `advisor_ro_query` (sole approved read path)
- `datetime` — staleness computation
