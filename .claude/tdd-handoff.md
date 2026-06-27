# TDD Handoff v3 — DE-PRISM-SOURCES-001 cleanup lap (dead accessor + json_extract)

**From:** sov-test (quant-test-writer, team lead)
**To:** sov-db (sqlite-specialist)
**Branch:** feat/overview-sources-provenance (current after cleanup-RED commit)
**Worktree:** C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/sov-sources
**Do NOT read the PM brief** — implement ONLY what is in this file.
**Do NOT merge or push to origin — the PM owns the ship gate.**

---

## RED state (cleanup lap)

Run the bounded -n0 suite to verify RED:

```
DB_PATH=/tmp/sov_cleanup_verify.db python -m pytest \
  tests/database/test_market_prism_sources_accessor.py \
  tests/database/test_017_advisor_observations.py \
  tests/prism_scheduler/test_patch_provenance.py \
  tests/prism_scheduler/test_patch_provenance_render_contract.py \
  tests/app/test_ai_advisor_tab_sources_merge.py \
  -n0 --tb=line -q
```

Expected RED summary:
- `test_market_prism_sources_accessor.py`: 1 FAILED (A-11: prefix match not caught by LIMIT-20 scan)
- All other files: all passed (already GREEN from prior lap)

---

## Cleanup lap — what changed

Two /review findings surfaced after the prior GREEN:

1. `get_latest_market_prism_sources()` (no run_id arg) has ZERO production callers —
   deleted from tests (A-5, A-6, A-9 removed). sov-db deletes it from database.py.

2. `get_latest_market_prism_sources_for_run` uses `ORDER BY id DESC LIMIT 20` + Python
   loop matching on `raw.get("run_id") == run_id`. This is a scan, not an exact match.
   sov-db converts to `json_extract(raw_response, '$.run_id') = ?` exact SQL equality.

---

## sov-db (sqlite-specialist) — two changes to database.py

### Change 1 (DELETE): Remove `get_latest_market_prism_sources()` from database.py

**Location:** database.py ~line 1238. Delete the entire function (no run_id variant).
Zero production callers confirmed by review grep. No callers in prism_scheduler.py or app.py.

### Change 2 (MODIFY): Convert `get_latest_market_prism_sources_for_run` to exact SQL match

**Location:** database.py ~line 1205-1235. Replace the current LIMIT-20 scan with an exact
`json_extract` query.

**Current implementation (LIMIT-20 scan — replace this):**
```python
cursor.execute(
    "SELECT "
    + ", ".join(_ADVISOR_OBSERVATION_COLUMNS)
    + " FROM advisor_observations WHERE advisor_role = 'MARKET_PRISM_SOURCES'"
    + " ORDER BY id DESC LIMIT 20",
)
rows = cursor.fetchall()
conn.close()
for row in rows:
    parsed = _parse_advisor_observation_row(row, _ADVISOR_OBSERVATION_COLUMNS)
    raw = parsed.get("raw_response") or {}
    if isinstance(raw, dict) and raw.get("run_id") == run_id:
        return parsed
return None
```

**New implementation (exact json_extract match):**
```python
cursor.execute(
    "SELECT "
    + ", ".join(_ADVISOR_OBSERVATION_COLUMNS)
    + " FROM advisor_observations"
    + " WHERE advisor_role = 'MARKET_PRISM_SOURCES'"
    + " AND json_extract(raw_response, '$.run_id') = ?"
    + " ORDER BY id DESC LIMIT 1",
    (run_id,),
)
row = cursor.fetchone()
conn.close()
if row is None:
    return None
return _parse_advisor_observation_row(row, _ADVISOR_OBSERVATION_COLUMNS)
```

**Key constraints:**
- Use `json_extract(raw_response, '$.run_id') = ?` — EXACT equality (NOT LIKE '%...%').
  A prefix/substring match would falsely match run_ids that contain the search string.
- Droplet is Ubuntu 24.04 / SQLite 3.45 — json_extract is fully supported.
- `raw_response` is stored as a JSON text column — json_extract works on it directly.
- Keep `get_ro_connection()` — read-only path unchanged.
- Keep D-1 `try/except Exception: return None` wrapper — unchanged.
- The returned row must have `raw_response` as a parsed dict (not a JSON string).
  The existing `_parse_advisor_observation_row` handles this — keep using it.
- NO-FALLBACK contract preserved: if `json_extract` finds no match, `fetchone()` returns
  `None` → function returns `None`. No scan fallback.

**Docstring update** — update to reflect the exact-match implementation:
```
Queries advisor_observations WHERE advisor_role='MARKET_PRISM_SOURCES' AND
json_extract(raw_response, '$.run_id') = run_id. Returns the most recent matching
row (ORDER BY id DESC LIMIT 1), or None when no match exists.
```

---

## GREEN target

After implementing both changes:

```
DB_PATH=/tmp/sov_cleanup_green.db python -m pytest \
  tests/database/test_market_prism_sources_accessor.py \
  tests/database/test_017_advisor_observations.py \
  tests/prism_scheduler/test_patch_provenance.py \
  tests/prism_scheduler/test_patch_provenance_render_contract.py \
  tests/app/test_ai_advisor_tab_sources_merge.py \
  -n0 -q
```

Target: **ALL passed / 0 failed / 0 errors** across all 5 files.

Then ruff check + format:
```
python -m ruff check database.py
python -m ruff format --check database.py
```

---

## Scope boundaries (do NOT touch)

- `app.py` — UNCHANGED (merge logic already correct; no callers of deleted function).
- `prism_scheduler.py` — UNCHANGED (idempotency guard calls `get_latest_market_prism_sources_for_run`, not the deleted variant).
- `templates/ai_advisor.html` — UNCHANGED.
- No DB migration — json_extract works on existing text column with no schema change.
- Do NOT create a PR or merge to main — PM owns the ship gate.
- Do NOT run the full/uncapped/-n>4 pytest suite — it reboots the host.

---

## After GREEN

Run the 5-file bounded -n0 suite and confirm all pass / 0 failed / 0 errors.
Commit path-scoped (NOT `git add -A`):
```
git -C "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/sov-sources" \
  add database.py
git -C "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/sov-sources" \
  commit -m "fix(db): delete dead get_latest_market_prism_sources + json_extract exact match (DE-PRISM-SOURCES-001)"
```

Then `SendMessage` the PM (team-lead) "GREEN: <N> passed / 0 failed / 0 errors. SHA=<sha>."
