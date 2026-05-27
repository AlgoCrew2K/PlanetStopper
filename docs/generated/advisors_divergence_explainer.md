# advisors/divergence_explainer

> Sprint 3 Stream B producer that surfaces two independent CVaR window values for operator visibility. Permanently forbids any signed divergence quantity between the two windows.

**Source:** `advisors/divergence_explainer.py`
**Last updated:** 2026-05-27

## Overview

The Divergence Explainer is the third Sprint 3 Advisor producer. It is the sole surviving residue of the CVaR-divergence idea evaluation (`decision-science-v3 §B.6`).

**Critical architectural constraint (binding, permanent):**
The observation MUST contain two honest CVaR values, each rendered independently under its own full four-part S-3 contract. The observation MUST NOT contain any signed divergence quantity — no arithmetic difference, ratio, or threshold-shaped affordance between the two windows. The divergence idea was REJECTED (council §B.9).

**Forbidden `raw_response` keys:** `"divergence"`, `"signed_divergence"`, `"cvar_diff"`, `"cvar_delta"`, `"window_divergence"`, `"divergence_pct"`, `"delta"`, and any semantic equivalent.

**Feature flag (`SECOND_WINDOW_CVAR_ENABLED`):**
- §B is NOT enabled in production by default (`0` or absent).
- When off: writes one `NOT_APPLICABLE` row for the audit trail.
- When on: writes one `INFORMATIONAL` row with both window values.

**Wall integrity rule:** All DB reads must go through `database.advisor_ro_query`.

## API Reference

### `compute_divergence_explainer_observation(autotune_run: dict, cvar_row: dict | None, *, second_window_enabled: bool) → dict`

Pure function. Returns an `AdvisorObservation` dict.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `autotune_run` | `dict` | Must include `id`. Missing `id` raises `KeyError` — protects audit trail against `subject_id='None'` |
| `cvar_row` | `dict \| None` | Most-recent `cvar_diagnostics` row for the symphony. Pre-fetched by caller. Expected keys: `cvar_5pct`, `cvar_n_tail`, `cvar_5pct_long`, `cvar_n_tail_long` |
| `second_window_enabled` | `bool` | When `False`: returns `NOT_APPLICABLE` stub. When `True`: returns `INFORMATIONAL` row with both window values |

**Returns:** `dict` with keys:
- `advisor_role`: `"DIVERGENCE_EXPLAINER"`
- `subject_type`: `"autotune_run"`
- `subject_id`: `str(run_id)`
- `verdict`: `"NOT_APPLICABLE"` (§B off) or `"INFORMATIONAL"` (§B on)
- `raw_response`: `{"feature_flag": "off"}` when §B off; `{"short_window_cvar_pct", "short_window_tail_obs", "long_window_cvar_pct", "long_window_tail_obs"}` when §B on
- `is_advisory_only`: `1`
- `spec_bundle_id`: from `autotune_run.get("spec_bundle_id")`

---

### `run_divergence_explainer(autotune_run: dict, cvar_row: dict | None, *, second_window_enabled: bool | None = None) → int`

Integration entry point. Resolves the feature flag from the environment when `second_window_enabled` is `None`. Fetches the `cvar_diagnostics` row via `database.advisor_ro_query` when §B is on and `cvar_row` is `None`. Then calls `compute_divergence_explainer_observation` and persists via `database.insert_advisor_observation`.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `autotune_run` | `dict` | `autotune_runs` row dict |
| `cvar_row` | `dict \| None` | Pre-fetched CVaR row, or `None` to auto-fetch |
| `second_window_enabled` | `bool \| None` | `None` = read from `SECOND_WINDOW_CVAR_ENABLED` env var; explicit kwarg overrides for deterministic testing |

**Returns:** `int` — new `advisor_observations` row id.

## Types

### Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `_FLAG_ENV_VAR` | `"SECOND_WINDOW_CVAR_ENABLED"` | Environment variable name for the §B feature flag |

### Verdict Mapping

| `second_window_enabled` | `cvar_row` | `verdict` |
|-------------------------|------------|-----------|
| `False` | any | `"NOT_APPLICABLE"` |
| `True` | present | `"INFORMATIONAL"` with two window values |
| `True` | `None` | `"INFORMATIONAL"` with four `None` values |

## Internal Dependencies

- `database` — `insert_advisor_observation`, `advisor_ro_query` (sole approved read path for CVaR row fetch)
- `os` — reads `SECOND_WINDOW_CVAR_ENABLED` env var
