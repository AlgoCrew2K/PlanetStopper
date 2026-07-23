# advisors/overfitting_conscience

> Sprint 3 producer that characterises the overfitting risk of a completed autotune run by examining the BACKTEST_SELECTION accumulator (S counter) relative to N_effective.

**Source:** `advisors/overfitting_conscience.py`
**Last updated:** 2026-05-27 (module itself unchanged; see 2026-07-12 wiring note below)

## Overview

The Overfitting Conscience (OC) is one of three Sprint 3 Advisor producers. It runs post-walk-forward, after `database.save_autotune_run` returns the new run id. It produces an immutable `advisor_observations` row with verdict `CLEAR`, `WATCH`, or `BREACH`.

Three Phase-1 indicators:

- **I-1** — S > 0: any `BACKTEST_SELECTION` row for the bundle → WATCH or BREACH.
- **I-2** — S/N_optuna > 0.10 (strict): escalate to BREACH.
- **I-3** — Operator drift: monotonically growing S across consecutive runs on the same symphony → WATCH (floored; escalated by I-2 when ratio also fires).

**Wall integrity rule:** All DB reads must go through `database.advisor_ro_query`. Direct `get_connection()` / `get_ro_connection()` calls from Advisor code are prohibited. Enforced by CI lint test `test_advisors_module_uses_advisor_ro_query`.

**I-3 wiring gap fixed upstream (advisor-rewire cycle, Workstream E, 2026-07-12) — this module's own source is UNCHANGED.** `autotune_run["s_count"]` (part of the `prior_runs` rows this function consumes for I-3 drift detection) was structurally `NULL` on every historical row before this cycle, because `database.save_autotune_run` had no `s_count` parameter and no caller populated the `023_autotune_runs_s_count.sql` column that had existed since an earlier migration. Since I-3 requires `>= 2` prior runs with non-`NULL`, increasing `s_count`, drift could never fire on live data — not because this module's logic was wrong, but because its input was always empty. The fix (`database.save_autotune_run(s_count=...)` + a hoisted DoF-ledger sum in `autotuner.py`, see `docs/generated/autotuner.md` "s_count Wiring") is entirely upstream of this module; `compute_overfitting_conscience_observation`'s I-1/I-2/I-3 logic was verified already-correct and left untouched. NULL-tolerance for legacy pre-fix rows is preserved (no crash, no retroactive verdict change).

## API Reference

### `compute_overfitting_conscience_observation(autotune_run: dict, ledger_rows: list[dict], prior_runs: list[dict] | None = None) → dict`

Pure function. Returns an `AdvisorObservation` dict describing overfitting risk for one run. No DB side-effects.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `autotune_run` | `dict` | Row from `autotune_runs` — must include `id`, `symphony_id`, `spec_bundle_id`, `n_effective`, `s_count`. Missing keys raise `KeyError` (pre-020 schema guard). |
| `ledger_rows` | `list[dict]` | `researcher_dof_ledger` rows pre-fetched by caller via `advisor_ro_query`. Only rows matching the run's `spec_bundle_id` with `evidence_source == "BACKTEST_SELECTION"` are counted. |
| `prior_runs` | `list[dict] \| None` | Optional earlier autotune run rows for the same symphony, oldest-first. Used only for I-3 drift detection. As of 2026-07-12, `s_count` on these rows is genuinely populated for runs recorded after the Workstream E fix — I-3 can now actually fire on live data (previously structurally unreachable; see "Overview" above). |

**Returns:** `dict` with keys:
- `advisor_role`: `"OVERFITTING_CONSCIENCE"`
- `subject_type`: `"autotune_run"`
- `subject_id`: `str(run_id)`
- `verdict`: `"CLEAR"` | `"WATCH"` | `"BREACH"`
- `raw_response`: dict with counts, ratio, and drift notes
- `is_advisory_only`: `1`
- `spec_bundle_id`: bundle_hash of the active run

**Example:**
```python
obs = compute_overfitting_conscience_observation(
    autotune_run=run_row,
    ledger_rows=advisor_ro_query("SELECT ..."),
    prior_runs=prior_run_rows,
)
# obs["verdict"] in ("CLEAR", "WATCH", "BREACH")
```

**Error conditions:**
- `ValueError` when `autotune_run["id"]` is `0`, `None`, or negative — protects the audit trail against sentinel subject-ids (S3-AUDIT-007).

---

### `run_overfitting_conscience(autotune_run: dict, ledger_rows: list[dict], prior_runs: list[dict] | None = None) → int`

Integration entry point. Calls `compute_overfitting_conscience_observation` then persists the result via `database.insert_advisor_observation`.

**Returns:** `int` — the new `advisor_observations` row id.

## Types

### Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `S_RATIO_BREACH_THRESHOLD` | `0.10` | S/N_optuna ratio above which verdict escalates to BREACH (strict >) |

### Verdict Logic

| Condition | Verdict |
|-----------|---------|
| S == 0 | `CLEAR` |
| S > 0 and S/N_optuna > 0.10 | `BREACH` |
| S > 0 and S/N_optuna ≤ 0.10 | `WATCH` |
| Drift detected and verdict would be `CLEAR` | `WATCH` (floor) |

## Internal Dependencies

- `database` — `insert_advisor_observation`, `advisor_ro_query` (sole approved read path)
