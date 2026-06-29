# TDD Handoff — DE-ADVISOR-LATENCY: Cache-Serve Market Lenses

**Phase:** RED (complete — adv-db accessor GREEN at bd06722, adv-app Surface B outstanding)
**RED commit:** `cb471a7` (tests); `2af9ac2` (AC-6 guard addition)
**Branch:** `feat/advisor-latency-cache-serve`
**Worktree:** `C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/advisor-latency`
**Feature plan:** `feature-plans/advisor-latency-cache-serve.md` (Status: ready)

## Failing test command (bounded, mem-capped)

```
ALPHABOT_TEST_MEM_CAP_GB=24 DB_PATH="C:/Users/paulm/AppData/Local/Temp/pytest_latency_test.db" \
  python -m pytest \
    tests/database/test_market_lens_cache_accessor.py \
    tests/ai_advisor/test_advisor_latency_cache_serve.py \
    -n0 --tb=short -q
```

**Expected output today (Surface B still outstanding):** 8 FAILED, 19 passed (27 total)
- Surface A (accessor) GREEN since bd06722 — 9 DB tests now pass
- Surface B (ai_advisor.py + prism_scheduler.py) outstanding — 8 RED remain

---

## Per-surface task split

### Surface A — DB accessor (`adv-db` / sqlite-specialist)

**File to modify:** `database.py`
**Tests:** `tests/database/test_market_lens_cache_accessor.py` (9 RED)

Implement `get_latest_market_lens_cache() -> dict | None`:

```python
def get_latest_market_lens_cache() -> dict | None:
    """Return the most recent MARKET_LENS_CACHE advisor_observations row, or None.

    SELECT ... WHERE advisor_role = 'MARKET_LENS_CACHE' ORDER BY id DESC LIMIT 1
    Deserializes raw_response from JSON. Uses get_ro_connection() (architecture constraint 5).
    D-1 never-raises.
    """
```

Pattern: copy `get_latest_market_prism_summary()` (line 1180), change role filter to
`'MARKET_LENS_CACHE'`, wrap in `try/except` returning None (D-1).

**No schema migration needed** — reuses existing `advisor_observations` table.

After implementing and going GREEN on the 9 DB tests, SendMessage `adv-app` that the
accessor is ready (sha of commit).

---

### Surface B — Producer + serve path + reword (`adv-app` / flask-dashboard-specialist)

**Files to modify:** `ai_advisor.py`, `prism_scheduler.py`
**Tests:** `tests/ai_advisor/test_advisor_latency_cache_serve.py` (8 RED — AC-1, AC-3, AC-4 x3, AC-5, AC-6, AC-7)

#### B-1: `ai_advisor.persist_market_lens_cache(sections: dict)` (AC-2)

Add to `ai_advisor.py`:

```python
def persist_market_lens_cache(sections: dict) -> None:
    """Persist all 5 structured lens payloads as a MARKET_LENS_CACHE cache row.

    sections: dict of lens_name -> _build_*_section() output (structured, not prose).
    Captures captured_at = UTC now. Append-only (latest wins on serve).
    D-1 never-raises.
    """
    try:
        from datetime import datetime, UTC
        captured_at = datetime.now(UTC).isoformat()
        raw = {"captured_at": captured_at, "lenses": dict(sections)}
        database.insert_advisor_observation(
            advisor_role="MARKET_LENS_CACHE",
            subject_type="portfolio",
            subject_id="global",
            verdict=None,
            raw_response=raw,
            symphony_id="",
        )
    except Exception as exc:
        logger.warning("persist_market_lens_cache failed: %s", type(exc).__name__)
```

Wire into `prism_scheduler._patch_provenance()` after the existing MARKET_PRISM_SOURCES insert.
The existing `_patch_provenance` only runs 4 builders (no technicals). Add technicals separately.
Call `ai_advisor.persist_market_lens_cache(all_5_sections)` at the end of `_patch_provenance`.

#### B-2: `assemble_advisor_context` cache-serve path (AC-1, AC-3, AC-4, AC-5, AC-7)

Add this module-level constant near the other constants in `ai_advisor.py`:

```python
# Freshness window for the nightly MARKET_LENS_CACHE bundle.
# Default 36 h covers a missed night plus allows the next council run to refresh.
_LENS_CACHE_MAX_AGE_HOURS = 36
```

Add BEFORE the existing live-fetch block (lines 1544-1582) in `assemble_advisor_context`:

```python
_lenses_from_cache = None
_lens_data_as_of: str | None = None
_lens_data_stale: bool = True

_cached_row = database.get_latest_market_lens_cache()
if _cached_row is not None:
    raw_cache = _cached_row.get("raw_response") or {}
    cached_lenses = raw_cache.get("lenses")
    captured_at_str = raw_cache.get("captured_at")
    if isinstance(cached_lenses, dict) and len(cached_lenses) >= 5 and captured_at_str:
        try:
            from datetime import datetime, UTC
            captured_at_dt = datetime.fromisoformat(captured_at_str).astimezone(UTC)
            age_hours = (datetime.now(UTC) - captured_at_dt).total_seconds() / 3600
            _lens_data_stale = age_hours > _LENS_CACHE_MAX_AGE_HOURS
            _lens_data_as_of = captured_at_str
            _lenses_from_cache = cached_lenses
        except Exception:
            pass  # D-1: treat as cache miss

# Cold-start fallback (AC-5): do NOT fan out to all 5 live builders.
# Honest degradation: "lens_cache_unavailable" for each lens.
if _lenses_from_cache is None:
    _lenses_from_cache = {
        name: {
            "lens": name,
            "available": False,
            "reason": "lens_cache_unavailable",
            "payload": None,
            "sources": [],
        }
        for name in ("technicals", "sentiment", "derivatives", "macro", "fundamentals")
    }
```

Then REPLACE the 5-builder live-fetch lines (1544-1582) with:

```python
# No live builder calls when cache is present or on cold-start.
# All 5 lens blocks come from _lenses_from_cache (cache-hit or degraded).
```

And in the `context` dict, replace the 5 live-fetch keys with:

```python
"lenses": _lenses_from_cache,
"lens_data_as_of": _lens_data_as_of,
"lens_data_stale": _lens_data_stale,
# Backward-compat aliases so existing consumers (request_suggestions, _build_messages)
# can still read context["technicals"] etc. directly:
"technicals": _lenses_from_cache.get("technicals") or {},
"sentiment": _lenses_from_cache.get("sentiment") or {},
"derivatives": _lenses_from_cache.get("derivatives") or {},
"macro": _lenses_from_cache.get("macro") or {},
"fundamentals": _lenses_from_cache.get("fundamentals") or {},
```

#### B-3: Empty-state reword (AC-8)

In `build_assessment_from_context` at ai_advisor.py:1436, replace:

```python
# OLD (alarming — must be removed):
"Optuna has not yet run for this symphony — no walk-forward "
"validation evidence is available. Config is unvalidated; "
"Claude is reasoning without OOS data."
```

With a clearer message that still conveys: no walk-forward OOS evidence, Claude reasoning
without OOS data. The test asserts the OLD PHRASE is gone, not the exact new wording.
Example:

```python
"Walk-forward optimization (Optuna) has not run for this symphony yet. "
"No out-of-sample (OOS) validation evidence is available — the current "
"config is unvalidated. Claude will reason without OOS data."
```

---

## Key constraints (hard rules)

- **NEVER add `MARKET_LENS_CACHE` to `app._ADVISOR_ROLES`** (AC-9 guard test enforces this)
- **D-1 on all new paths**: malformed cache raw_response / accessor error -> cache miss ->
  cold-start fallback -> no raise ever escapes from `assemble_advisor_context`
- **Append-only**: `persist_market_lens_cache` only INSERTs, never updates
- **No schema migration**: reuses `advisor_observations` as-is (no new columns/tables)
- **Stale bundles MUST be served** with the stale label, never discarded + re-fetched (AC-4)
- **Cold-start must NOT fan out to all 5 builders** — the "lens_cache_unavailable" fallback
  is the correct cold-start path (AC-5 test asserts sum_of_builders_called < 5)
- **`get_ro_connection()` in the accessor** — not `get_connection()` (L-5 test inspects source)
- **NEVER merge to main** — report GREEN to `adv-test` and stop

---

## After implementing GREEN

Run:
```
ALPHABOT_TEST_MEM_CAP_GB=24 DB_PATH="C:/Users/paulm/AppData/Local/Temp/pytest_latency_green.db" \
  python -m pytest \
    tests/database/test_market_lens_cache_accessor.py \
    tests/ai_advisor/test_advisor_latency_cache_serve.py \
    -n0 --tb=short -q
```

Target: **0 FAILED, 26 passed**.

Then SendMessage `adv-test` with the GREEN commit SHA + full test output.
