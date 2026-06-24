# TDD Handoff v2 — DE-PRISM-SOURCES-001 Overview Market Prism Sources Provenance

**From:** sov-test (quant-test-writer, team lead)
**To:** sov-db (sqlite-specialist) AND sov-flask (flask-dashboard-specialist)
**Branch:** feat/overview-sources-provenance (current after RED commit)
**Worktree:** C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/sov-sources
**Do NOT read the PM brief** — implement ONLY what is in this file.
**Do NOT merge or push to origin — the PM owns the ship gate.**

---

## RED state confirmed

29 total failing tests across 4 files. Run the bounded -n0 suite to verify:

```
DB_PATH=/tmp/sov_v2_verify.db python -m pytest \
  tests/database/test_market_prism_sources_accessor.py \
  tests/database/test_017_advisor_observations.py \
  tests/prism_scheduler/test_patch_provenance.py \
  tests/prism_scheduler/test_patch_provenance_render_contract.py \
  tests/app/test_ai_advisor_tab_sources_merge.py \
  -n0 --tb=line -q
```

Expected RED summary:
- `test_market_prism_sources_accessor.py`: 11 FAILED (accessor absent, mutation present)
- `test_017_advisor_observations.py`: 1 FAILED (`test_no_update_advisor_observation_symbol_in_database_module`)
- `test_patch_provenance.py`: 9 FAILED (v1 behavior, not INSERT)
- `test_patch_provenance_render_contract.py`: 4 FAILED (accessor mock target absent)
- `test_ai_advisor_tab_sources_merge.py`: 4 FAILED (route merge logic absent)

---

## The v2 problem statement

The v1 design added `database.update_advisor_observation_raw_response` (a mutation
accessor) and had `prism_scheduler._patch_provenance` UPDATE the existing MARKET_PRISM
row. This violated the `advisor_observations` append-only contract and failed
`test_017::test_no_update_advisor_observation_symbol_in_database_module` in full CI.

v2 fix: everything is now APPEND-ONLY. `_patch_provenance` INSERTs a new row with
`advisor_role="MARKET_PRISM_SOURCES"`. The MARKET_PRISM row is NEVER modified.
`app.py` fetches and merges the SOURCES row additively at render time.

---

## sov-db (sqlite-specialist) — implement these 3 symbols

### Symbol 1 (DELETE): Remove `update_advisor_observation_raw_response` from database.py

**Location:** database.py, around line 1108.
**Action:** Delete the entire function. This is the v1 mutation accessor that fails test_017.
**Verification:** After deletion, `test_017::test_no_update_advisor_observation_symbol_in_database_module` must PASS.

### Symbol 2 (ADD): `get_latest_market_prism_sources_for_run(run_id: str) -> dict | None`

**Location:** database.py — add near `get_latest_market_prism_summary()` (~line 1225).

**Contract:**
- Queries `advisor_observations WHERE advisor_role='MARKET_PRISM_SOURCES'`.
- Matches on `raw_response["run_id"] == run_id` (parse JSON, compare in Python).
- Returns the first matching row as a fully parsed dict (raw_response deserialized), or `None`.
- On no match: return `None` — NEVER fall back to the latest SOURCES row if run_ids differ.
  This is the no-stale-citation-bleed guard (AC-9).
- D-1 never-raises: wrap in try/except Exception, return None on error.
- Uses `get_ro_connection()` — read-only at driver level.
- Implementer chooses the query mechanism (e.g., fetch a small window newest-first, filter in Python).

**Signature:**
```python
def get_latest_market_prism_sources_for_run(run_id: str) -> dict | None:
    """Return the MARKET_PRISM_SOURCES advisor_observations row for this run_id, or None.

    Queries advisor_observations WHERE advisor_role='MARKET_PRISM_SOURCES', parses each
    row's raw_response["run_id"], and returns the first row whose run_id matches the argument.
    Returns None when no match exists — never falls back to a different run's row.

    No-stale-citation-bleed guard (AC-9): a night where all lenses are unavailable
    produces no SOURCES row; returning a different run's row would inject stale citations.

    D-1 never-raises. Uses get_ro_connection().
    """
```

### Symbol 3 (ADD): `get_latest_market_prism_sources() -> dict | None`

**Location:** database.py — add immediately after Symbol 2.

**Contract:**
- Returns the most recent MARKET_PRISM_SOURCES row by id, or None.
- Does NOT filter by run_id — just latest row.
- D-1 never-raises. Uses `get_ro_connection()`.

**Signature:**
```python
def get_latest_market_prism_sources() -> dict | None:
    """Return the most recent MARKET_PRISM_SOURCES advisor_observations row, or None.

    Ordered by id DESC LIMIT 1. D-1 never-raises. Uses get_ro_connection().
    """
```

### Also wire in `prism_scheduler._patch_provenance` (sov-db owns this file too)

**Location:** prism_scheduler.py, `_patch_provenance` function (~line 236).

The existing citation-build body (four builders, dedup-by-url, `build_citation`) is SOUND and
stays. Replace ONLY the persistence step at the end (the current `update_advisor_observation_raw_response` call).

**v2 persistence step — replace the current persistence block with:**

```python
# AC-6 idempotency: if a SOURCES row already exists for this run_id, skip INSERT.
existing = _db.get_latest_market_prism_sources_for_run(run_id)
if existing is not None:
    return True  # already patched

# INSERT the new append-only SOURCES row (v2: no UPDATE).
_db.insert_advisor_observation(
    advisor_role="MARKET_PRISM_SOURCES",
    subject_type="portfolio",
    subject_id="global",
    verdict=None,
    raw_response={"run_id": run_id, "per_lens_digest": sources_per_lens_digest},
    symphony_id="",
)
return True
```

Where `sources_per_lens_digest` is the dict already built by the existing citation-build body:
`{lens: {"article_corpus": [valid citations]}, ...}` for url-bearing lenses that had any citations.

**Note on `insert_advisor_observation` call:** The existing function (database.py:1053) accepts
`verdict: str | None = None` — passing `verdict=None` is fine. The `symphony_id=""` parameter
is also accepted (migration 025 added that column).

The function must also add `import database as _db` at top of function if not already present
(it may already do a lazy import or use a module-level reference — match the existing import style).

---

## sov-flask (flask-dashboard-specialist) — implement the merge in app.py

### Location: `ai_advisor_tab()` in app.py, around line 3694-3720

Find the block that fetches `market_prism_summary` and the humanization block that follows it.
Insert a SOURCES merge block BETWEEN them (after fetch, before humanize):

```python
# ------------------------------------------------------------------ #
# Additively merge MARKET_PRISM_SOURCES article_corpus into the       #
# per_lens_digest for url-bearing lenses (DE-PRISM-SOURCES-001 v2).  #
# Matched by run_id — no stale citation bleed if run_ids differ.     #
# ------------------------------------------------------------------ #
if market_prism_summary:
    try:
        _mp_raw = market_prism_summary.get("raw_response") or {}
        if isinstance(_mp_raw, str):
            import json as _json  # noqa: PLC0415
            _mp_raw = _json.loads(_mp_raw)
        _mp_run_id = _mp_raw.get("run_id") if isinstance(_mp_raw, dict) else None
        if _mp_run_id:
            _sources_row = database.get_latest_market_prism_sources_for_run(_mp_run_id)
            if _sources_row is not None:
                _src_raw = _sources_row.get("raw_response") or {}
                _src_pld = _src_raw.get("per_lens_digest", {}) if isinstance(_src_raw, dict) else {}
                _mp_pld = _mp_raw.get("per_lens_digest", {}) if isinstance(_mp_raw, dict) else {}
                for _src_lens, _src_lens_data in _src_pld.items():
                    if isinstance(_src_lens_data, dict) and isinstance(_mp_pld.get(_src_lens), dict):
                        _corpus = _src_lens_data.get("article_corpus")
                        if _corpus:
                            _mp_pld[_src_lens]["article_corpus"] = _corpus
    except Exception:
        pass  # Merge failure must never crash the route — honest empty-state.
```

**Critical placement:** this merge block must come BEFORE the `humanize_lens_summary` block,
so the article_corpus is present in per_lens_digest when humanization runs.

**Contract:**
- If `market_prism_summary` is None: skip entirely (no-op).
- Extract `run_id` from `market_prism_summary["raw_response"]["run_id"]`.
- Call `database.get_latest_market_prism_sources_for_run(run_id)` -> `sources_row`.
- If `sources_row` is None (no SOURCES row for this run): skip merge entirely.
  NEVER fall back to `database.get_latest_market_prism_sources()` — that would bleed
  stale citations from a different run (AC-9 hard requirement).
- If `sources_row` present: merge `sources_row["raw_response"]["per_lens_digest"][lens]["article_corpus"]`
  into `market_prism_summary["raw_response"]["per_lens_digest"][lens]` for each matching lens.
- Entire block wrapped in try/except — never crashes the route.
- Template: UNCHANGED.

---

## GREEN target

After implementing all symbols (sov-db + sov-flask):

```
DB_PATH=/tmp/sov_v2_green.db python -m pytest \
  tests/database/test_market_prism_sources_accessor.py \
  tests/database/test_017_advisor_observations.py \
  tests/prism_scheduler/test_patch_provenance.py \
  tests/prism_scheduler/test_patch_provenance_render_contract.py \
  tests/app/test_ai_advisor_tab_sources_merge.py \
  -n0 -q
```

Target: **ALL passed / 0 failed / 0 errors** across all 5 files.

Then ruff check + format on changed files:
```
python -m ruff check database.py prism_scheduler.py app.py
python -m ruff format --check database.py prism_scheduler.py app.py
```

---

## Scope boundaries (do NOT touch)

- `templates/ai_advisor.html` — already renders article_corpus correctly (no change).
- `.claude/agents/prism-*.md` role files — do not change (synthesizer keeps writing MARKET_PRISM).
- No DB migration — no new columns, no schema changes.
- Do NOT create a PR or merge to main — PM owns the ship gate.
- Do NOT run the full/uncapped/-n>4 pytest suite — it reboots the host.

---

## After GREEN

Run the 5-file bounded -n0 suite and confirm all pass / 0 failed / 0 errors.
Commit path-scoped (NOT `git add -A`):
```
git -C "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/sov-sources" \
  add database.py prism_scheduler.py app.py
git -C "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/sov-sources" \
  commit -m "fix(prism-sources): v2 append-only SOURCES row + merge (DE-PRISM-SOURCES-001)"
```

Then `SendMessage` the PM (team-lead) "GREEN: <N> passed / 0 failed / 0 errors. SHA=<sha>. Ready for review cycle."
