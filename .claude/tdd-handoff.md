# TDD Handoff — DE-PRISM-SOURCES-001 Overview Market Prism Sources Provenance

**From:** sov-test (quant-test-writer)
**To:** sov-impl (sqlite-specialist)
**Branch:** feat/overview-sources-provenance @ 88bbfd4
**Worktree:** C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/sov-sources
**Do NOT read the PM brief** — implement ONLY what is in this file.

---

## RED state confirmed

```
cd C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/sov-sources
python -m pytest tests/prism_scheduler/test_patch_provenance.py tests/prism_scheduler/test_patch_provenance_render_contract.py -n0 --tb=line -q
```
Expected: **7 FAILED** (prism_scheduler._patch_provenance absent), **2 PASSED** (render-contract guards already pass — template is already correct).

```
DB_PATH=/tmp/test_sov_sources_db.db python -m pytest tests/database/test_update_advisor_observation_raw_response.py -n0 --tb=line -q
```
Expected: **3 FAILED** (database.update_advisor_observation_raw_response absent).

---

## Production symbols to implement (ONLY these 3 — no gold-plating)

### Symbol 1: `database.update_advisor_observation_raw_response(row_id: int, raw_response: dict) -> None`

**Location:** `database.py` — add after `insert_advisor_observation` (~line 1105)

**Contract:**
- Parameterized `UPDATE advisor_observations SET raw_response = ? WHERE id = ?`
- Serialise `raw_response` dict to JSON string via `json.dumps`
- Non-existent `row_id`: no-op (UPDATE affects 0 rows — that is fine, no exception)
- D-1 never-raises: wrap entire body in `try/except Exception as exc`, log `type(exc).__name__` to stderr
- No DB migration needed — `raw_response` is an existing JSON blob column
- Use `get_connection()` (this is a write path)

Example skeleton:
```python
def update_advisor_observation_raw_response(row_id: int, raw_response: dict) -> None:
    """Update raw_response on an advisor_observations row by id.

    Additive update — callers replace the full raw_response dict.
    D-1 never-raises; non-existent row_id is a silent no-op.
    No migration needed: raw_response is an existing JSON blob column.
    """
    try:
        raw_str = json.dumps(raw_response)
        conn = get_connection()
        conn.execute(
            "UPDATE advisor_observations SET raw_response = ? WHERE id = ?",
            (raw_str, row_id),
        )
        conn.commit()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        print(
            f"[database] UpdateRawResponseError: {type(exc).__name__}",
            file=sys.stderr,
        )
```

### Symbol 2: `prism_scheduler._patch_provenance(run_id: str, row: dict | None) -> bool`

**Location:** `prism_scheduler.py` — add before `main()`

**Contract:**
- Guard: `if row is None` → return False (silent no-op)
- Parse `raw_response` from `row`: if it is a string try `json.loads`; on parse failure → return False
- If `raw_response` has no `per_lens_digest` → return False
- For each of the 4 url-bearing lenses (`sentiment`, `macro`, `derivatives`, `fundamentals`):
  - Call `ai_advisor._build_<lens>_section()` — wrap in `try/except Exception: continue`
  - Collect `section.get("sources") or []`
  - For each source call `ai_advisor.build_citation(source)` — keep only non-None results
  - **AC-6 idempotency:** REPLACE (not append) `pld[lens]["article_corpus"] = valid_citations`
    This makes re-patching idempotent — second call with same builders produces the same list
- **technicals is EXCLUDED — never write article_corpus for technicals (AC-2)**
- Persist via `database.update_advisor_observation_raw_response(row["id"], raw_response)`
- Outer `try/except Exception` wraps everything (D-1): on error log `type(exc).__name__` and return False
- Returns True on success (even if some lenses got 0 citations), False on no-op/error

**Lazy imports:** use `import ai_advisor` and `import database` inside the function body to
avoid circular import issues (same pattern as other scheduler helper functions).

Example skeleton:
```python
def _patch_provenance(run_id: str, row: "dict | None") -> bool:
    """Post-council patch: rebuild per-lens validated article_corpus citations
    and persist them into the MARKET_PRISM row's raw_response.

    Reuses ai_advisor._build_*_section + build_citation — no reinvented logic.
    D-1 never-raises; AC-4: does not gate or prevent sys.exit(0) in main().
    Returns True if patch attempted, False if no-op/error.
    """
    try:
        if row is None:
            return False
        raw = row.get("raw_response") or {}
        if isinstance(raw, str):
            try:
                import json as _json  # noqa: PLC0415
                raw = _json.loads(raw)
            except Exception:  # noqa: BLE001
                return False
        pld = raw.get("per_lens_digest")
        if not isinstance(pld, dict):
            return False

        import ai_advisor  # noqa: PLC0415

        _BUILDERS = {
            "sentiment": ai_advisor._build_sentiment_section,
            "macro": ai_advisor._build_macro_section,
            "derivatives": ai_advisor._build_derivatives_section,
            "fundamentals": ai_advisor._build_fundamentals_section,
            # technicals intentionally omitted — AC-2: Alpaca bar data has no public urls
        }

        for lens, builder in _BUILDERS.items():
            if lens not in pld:
                continue
            try:
                section = builder()
            except Exception:  # noqa: BLE001
                continue  # D-1: this lens contributes no citations
            sources = section.get("sources") or []
            valid = [
                c for c in (ai_advisor.build_citation(s) for s in sources)
                if c is not None
            ]
            pld[lens]["article_corpus"] = valid  # AC-6: replace not append

        import database as _db  # noqa: PLC0415

        _db.update_advisor_observation_raw_response(row["id"], raw)
        return True
    except Exception as exc:  # noqa: BLE001
        print(
            f"[prism_scheduler] PatchProvenanceError: {type(exc).__name__}",
            file=sys.stderr,
        )
        return False
```

### Symbol 3: Wire `_patch_provenance` into `prism_scheduler.main()`

**Location:** `prism_scheduler.py`, `main()` function — in the SUCCESS path (currently around line 259-262).

**Current code:**
```python
if row is not None:
    print("[prism_scheduler] Run completed successfully.")
    sys.exit(0)
```

**Change to:**
```python
if row is not None:
    print("[prism_scheduler] Run completed successfully.")
    _patch_provenance(run_id, row)  # AC-4: D-1 never-raises; does not gate sys.exit(0)
    sys.exit(0)
```

**AC-4 critical:** `_patch_provenance` must NOT prevent `sys.exit(0)`. Its D-1 contract
ensures it never raises, so `sys.exit(0)` always fires after it returns. The council
run's success verdict is independent of whether the patch succeeds.

---

## GREEN target

After implementing the 3 symbols:

```bash
cd C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/sov-sources

# Prism scheduler unit tests + render-contract
python -m pytest tests/prism_scheduler/test_patch_provenance.py tests/prism_scheduler/test_patch_provenance_render_contract.py -n0 -q

# DB accessor tests
DB_PATH=/tmp/test_sov_sources_db.db python -m pytest tests/database/test_update_advisor_observation_raw_response.py -n0 -q
```

Target: **12 passed / 0 failed / 0 errors** total across both runs.

Then ruff check + format on changed files:
```bash
python -m ruff check prism_scheduler.py database.py
python -m ruff format --check prism_scheduler.py database.py
```

---

## Scope boundaries (do NOT touch)

- `templates/ai_advisor.html` — already renders article_corpus correctly (no change)
- `app.py` — no route change needed
- No DB migration — raw_response is an existing column
- `advisors/lens_pipeline.py` — reuse, do not modify
- `.claude/agents/prism-*.md` role files — do not change
- Do NOT create a PR or merge to main

---

## After GREEN

Run the two test commands above; confirm 12 passed / 0 failed / 0 errors.
Commit path-scoped (NOT `git add -A`):
```bash
git add prism_scheduler.py database.py
git commit -m "fix(prism-sources): _patch_provenance + update_advisor_observation_raw_response (DE-PRISM-SOURCES-001)"
```

Then `SendMessage` sov-test: "GREEN — <N> passed / 0 failed / 0 errors. SHA=<sha>. Ready for review cycle."
