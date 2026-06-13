# advisors/lens_pipeline

> Scheduled off-hours lens pipeline: collects 5 lens blocks, validates citations, synthesizes a Market Prism summary via Claude, and persists exactly one MARKET_PRISM advisor_observation per run — always, regardless of lens availability.

**Source:** `advisors/lens_pipeline.py`
**Last updated:** 2026-06-13

## Overview

`advisors/lens_pipeline.py` implements the 4-pass off-hours data pipeline that drives the Market Prism summary (CYCLE4-BRIEF.md Components 7+8). It is scheduled daily at 03:00 via `app.py:run_scheduler()` and runs in a daemon thread — it never blocks the 1-minute execution scheduler.

The module uses lazy imports throughout (`import ai_advisor`, `import database` inside functions) so it is never on the execution path and cannot be accidentally imported by `alpha_bot_execution.py` (CC-2 import-boundary invariant).

**Always-emit invariant (AC-3):** Every `run_pipeline()` call (non-dry_run) writes exactly one `advisor_role="MARKET_PRISM"` row to `advisor_observations`. Even when all 5 lenses are `available=False`, the observation is written with `verdict="limited-inputs"`. The pipeline never silently no-ops.

**Never raises:** All exceptions are caught at every pass boundary. Failures are reflected in `error_count` and in per-lens `reason` fields using only `type(exc).__name__` (D-1 / CC-10 contract).

## Public API

### `run_pipeline(*, dry_run: bool = False) → dict`

Run the 4-pass lens pipeline and persist results.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `dry_run` | `bool` | If `True`, skips the DB write (Pass 4) and the Claude call (Pass 3). Returns the same dict shape with `market_prism_row_id=None`. Used in tests to avoid live DB and API calls. Default `False`. |

**Returns:** A summary dict with the following keys:

| Key | Type | Description |
|-----|------|-------------|
| `run_ts` | `str` | ISO 8601 UTC timestamp of this pipeline run. |
| `lenses_attempted` | `int` | Always 5 (the fixed total lens count). |
| `lenses_available` | `int` | Number of lenses that returned `available=True`. |
| `market_prism_row_id` | `int \| None` | Row id of the persisted `advisor_observations` row, or `None` in dry_run mode or on persistence failure. |
| `error_count` | `int` | Number of lenses that were unavailable (includes both genuine unavailability and exceptions). |

**Never raises** — all exceptions are caught and reflected in the return dict.

## 4-Pass Architecture

### Pass 1 — Per-Lens Data Collection

Calls each `ai_advisor._build_<lens>_section()` helper for the 5 lenses: `technicals`, `sentiment`, `derivatives`, `macro`, `fundamentals`. Each call is independently isolated — one lens raising an exception does not abort the pipeline. On exception, the lens is recorded as `available=False` with `reason=type(exc).__name__` only (D-1 / CC-10). Lenses that are genuinely unavailable (no data source) also return `available=False` with an explanatory reason string — no data is ever fabricated (CC-3).

### Pass 2 — Citation Assembly and Validation

Aggregates sources from all `available=True` lens blocks. Each citation is validated through `ai_advisor.build_citation` (CC-4). Malformed entries (missing URL, bad scheme, missing required fields) are silently dropped. The result is a list of valid `{title, url, published, lens}` dicts, possibly empty.

### Pass 3 — Claude Synthesis

Calls Claude (`claude-haiku-4-5-20251001`, lightest available model) to synthesize the available lens summaries into an `overall_sentiment` label and `sentiment_rationale`. The prompt requests a single JSON response; the response is parsed and validated against the allowed sentinel set (`"risk-on"`, `"neutral"`, `"risk-off"`, `"limited-inputs"`).

Degradation paths (all produce `"limited-inputs"` + an explanatory rationale string):
- All lenses unavailable → Claude call is skipped entirely.
- Claude client unavailable (no API key, SDK error) → degrades to `"limited-inputs"`.
- Claude response parse failure → degrades to `"limited-inputs"`.
- Any unexpected exception → `type(exc).__name__` in rationale only (D-1).
- `dry_run=True` → synthesis is skipped; sentinel value `"limited-inputs"` returned.

### Pass 4 — Persistence

Writes one `advisor_observations` row via `database.insert_advisor_observation`:

```python
advisor_role    = "MARKET_PRISM"
subject_type    = "portfolio"
subject_id      = "global"
verdict         = overall_sentiment   # "risk-on" | "neutral" | "risk-off" | "limited-inputs"
is_advisory_only = 1                  # hard-wired in insert_advisor_observation (CC-1)
raw_response    = {
    "run_ts": "...",
    "per_lens_digest": {
        "technicals":  {"available": bool, "summary"?: str, "sources"?: list},
        "sentiment":   {"available": bool, "summary"?: str, "sources"?: list},
        "derivatives": {"available": bool, "reason"?: str},
        "macro":       {"available": bool, "summary"?: str, "sources"?: list},
        "fundamentals":{"available": bool, "summary"?: str, "sources"?: list},
    },
    "overall_sentiment":    str,   # one of the 4 valid labels
    "sentiment_rationale":  str,
    "available_lens_count": int,
    "total_lens_count":     5,
    "sources":              list,  # validated citation dicts, possibly empty
}
```

On persistence failure, `market_prism_row_id` in the return dict is `None` and the error is logged at ERROR level (type name only). The pipeline still returns normally.

Skipped in `dry_run=True` mode.

## Scheduler Wiring (app.py)

```python
# run_scheduler() — daily at 03:00
schedule.every().day.at("03:00").do(_run_lens_pipeline)

def _run_lens_pipeline():
    """Non-blocking wrapper — starts daemon thread and returns immediately."""
    import threading
    t = threading.Thread(target=_lens_pipeline_worker, daemon=True, name="lens-pipeline")
    t.start()

def _lens_pipeline_worker():
    """Lazily imports run_pipeline to keep advisors.lens_pipeline off the execution path."""
    try:
        from advisors.lens_pipeline import run_pipeline
        result = run_pipeline()
        logger.info("Lens pipeline complete: %s", result)
    except Exception as exc:
        logger.error("Lens pipeline failed: %s", type(exc).__name__)
```

The daemon thread means the scheduler thread returns immediately. The pipeline never blocks the 1-minute execution cadence.

## Design Invariants

| Code | Invariant |
|------|-----------|
| CC-1 | `is_advisory_only=1` hard-wired in `insert_advisor_observation` — no money-moving code. |
| CC-2 | Never imported at module level in `alpha_bot_execution.py`. Lazy import inside the worker thread. Assertable via static source scan. |
| CC-3 | Lenses degrade to `available=False` with reason; data is never fabricated. |
| CC-4 | Citations validated through `ai_advisor.build_citation`; invalid entries are dropped. |
| CC-10 | D-1 error contract — `type(exc).__name__` only in persisted text and WARNING+ logs. |
| AC-3 | Exactly one `MARKET_PRISM` row written per non-dry_run call, even when all lenses are unavailable. |

## Related Accessors

### `database.get_latest_market_prism_summary() → dict | None`

Returns the most recent `MARKET_PRISM` advisor_observations row (deserialized `raw_response`), or `None` when no row exists. Uses `get_ro_connection()` — read-only. Ordered by `id DESC LIMIT 1`. Intended for the Cycle-5 Overview tab to read the nightly summary.

## Internal Dependencies

- `ai_advisor` — `_build_technicals_section`, `_build_sentiment_section`, `_build_derivatives_section`, `_build_macro_section`, `_build_fundamentals_section`, `build_citation`, `_build_client` (all lazy imports)
- `database` — `insert_advisor_observation` (lazy import)
- `app.py` — `_run_lens_pipeline` / `_lens_pipeline_worker` wrappers; `run_scheduler()` daily job registration
