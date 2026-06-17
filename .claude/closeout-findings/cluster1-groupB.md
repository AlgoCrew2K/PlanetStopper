# Cluster 1 — Group B Findings: Warehouse, Audit-Log, Pipeline (F9–F14)

**Auditor:** closeout-audit-prism
**Worktree HEAD:** b1b6227 (branch: audit/ai-council-closeout-e2e, tracking origin/main 73dc603)
**Date:** 2026-06-17
**Market status:** OPEN (RTH). State DB: zero-write (read-only). Warehouse: no writes to real alphabot_warehouse.db from audit probes (F9 used temp DB; F10 row provenance explained). One dry-run pipeline call (no Claude spend, no DB write).

---

## F9 — Lens Data Warehouse

**[PASS]**

**Round-trip on TEMP DB:**
```python
import tempfile, os
from advisors.lens_warehouse import init_warehouse_db, persist_lens_snapshot, get_lens_snapshots

tmp = tempfile.mktemp(suffix='_closeout_test.db')
init_warehouse_db(tmp)
# journal_mode query → 'wal'  (WAL mode confirmed)
rid = persist_lens_snapshot('sentiment','SPY','gdelt',True,
    {'tone':0.5,'api_key':'SECRET_VALUE','nested':{'token':'SHOULD_BE_STRIPPED'}},
    db_path=tmp)
# rid=1, type=int, positive=True
rows = get_lens_snapshots('sentiment', db_path=tmp)
# count=1; raw_json keys=['tone','nested']; api_key not in raw_json; nested.token=None
os.unlink(tmp)
```

Results:
- `init_warehouse_db` → OK
- `journal_mode` → `wal` (WAL confirmed)
- `persist_lens_snapshot` → row id `1` (int, positive)
- `get_lens_snapshots` → 1 row returned
- `raw_json` keys: `['tone', 'nested']` — `api_key` STRIPPED (top-level)
- `nested.token` → `None` (STRIPPED recursively)
- Temp DB cleaned up

**`_strip_secrets` confirmed recursive** (`advisors/lens_warehouse.py:70–79`):
```python
_SECRET_KEY_NAMES: frozenset[str] = frozenset(
    {"api_key", "token", "secret", "password", "Authorization"}  # lens_warehouse.py:36-38
)
def _strip_secrets(payload: object) -> object:
    ...
    return {k: _strip_secrets(v) for k, v in payload.items() if k not in _SECRET_KEY_NAMES}
    ...
    return [_strip_secrets(item) for item in payload]
```
Recursive: strips from nested dicts and lists. Both `api_key` (top) and `token` (nested) removed.

**Pytest sentinel confirmed** (`advisors/lens_warehouse.py:52–58`):
```python
if path is None:
    if "pytest" in sys.modules:
        raise RuntimeError(
            f"test attempted to open the production warehouse DB "
            f"({_WAREHOUSE_DB_BASENAME}) — set db_path to a temp file in tests"
        )
```
Under pytest, calling any public function without an explicit `db_path` raises `RuntimeError`. Mirrors `database._db_file()` sentinel pattern.

**Third DB confirmed distinct** from state DB and optimization DB:
- Warehouse: `alphabot_warehouse.db` (single table: `lens_snapshots`)
- State DB: `alphabot_state.db` (20+ tables, confirmed no `lens_snapshots` table)
- No cross-DB joins in lens_warehouse.py (standalone SQLite; advisory-only)

---

## F10 — Warehouse wiring (sentiment + macro persist)

**[PASS]**

**Read-only query on real `alphabot_warehouse.db` after Group A live calls:**
```sql
SELECT lens, source, available, fetch_ts FROM lens_snapshots ORDER BY fetch_ts DESC LIMIT 10
```
Results:
```
lens=macro   source=fred  available=1  ts=2026-06-17T14:37:10Z
lens=macro   source=fred  available=1  ts=2026-06-17T14:36:55Z
lens=macro   source=fred  available=1  ts=2026-06-17T14:36:36Z
lens=macro   source=fred  available=1  ts=2026-06-17T14:36:25Z
lens=sentiment source=gdelt available=1 ts=2026-06-17T14:36:10Z
```
New rows confirmed in the warehouse after Group A live calls. Note: 4 macro rows because `_build_macro_section()` was called 4 times during Group A inspection (F4 main call + 3 introspection calls). All rows are genuine (real FRED fetch values, not test values). Provenance: closeout audit 2026-06-17 Wave 1.

**Confirmed in `alphabot_warehouse.db` NOT `alphabot_state.db`:**
- `os.path.realpath(wh_path) == os.path.realpath(state_path)` → `False`
- `lens_snapshots` in state DB → `False` (no such table)
- Data separation confirmed.

**`raw_json` secrets check on a live macro row:**
- `api_key`: `False` | `token`: `False` | `secret`: `False` | `password`: `False` | `Authorization`: `False`
- FRED series data stored as `{"series": {...}}` — no API keys embedded.

**Warehouse wiring static cites:**

Sentiment persist (`ai_advisor.py:633–645`):
```python
# AC-4: persist this lens snapshot to the warehouse (lazy import, CC-2).
try:
    from advisors import lens_warehouse  # noqa: PLC0415
    lens_warehouse.persist_lens_snapshot(
        lens="sentiment", symbol=None, source="gdelt",
        available=True,
        raw_payload={"article_count": len(articles), "tone_score": tone_score},
    )
except Exception:
    pass  # D-1: warehouse errors never surface to callers
```

Macro persist (`ai_advisor.py:858–870`):
```python
# AC-4: persist this lens snapshot to the warehouse (lazy import, CC-2).
try:
    from advisors import lens_warehouse  # noqa: PLC0415
    lens_warehouse.persist_lens_snapshot(
        lens="macro", symbol=None, source="fred",
        available=True,
        raw_payload={"series": series_data},
    )
except Exception:
    pass  # D-1: warehouse errors never surface to callers
```

Both: lazy import (CC-2), D-1 guarded (`pass` on any warehouse error).

**Note:** Per CLAUDE.md, only sentiment (GDELT) and macro (FRED) are declared "wired (non-hollow)" to the warehouse. Technicals, derivatives, and fundamentals are NOT listed as warehouse-wired — consistent with the code (no `persist_lens_snapshot` calls in those builders).

---

## F11 — Migration 032 + prism_audit_log accessors

**[PASS]**

**`_MIGRATION_FILES` last entry — static cite + runtime check:**
```python
database._MIGRATION_FILES[-1]  # → "032_prism_audit_log.sql"
len(database._MIGRATION_FILES)  # → 29 (wiring 004–032, 021 before 020 per ARCH-002)
```
Migration 032 is the last wired migration.

**Round-trip on TEMP DB:**
```python
os.environ['DB_PATH'] = tmp  # temp DB, not alphabot_state.db
database.init_db()            # runs all 29 migrations including 032
row_id = database.insert_prism_audit_entry(
    'closeout-f11-test-31660', 'technicals_analyst', 'initial_read', 'F11 round-trip verification'
)
# → row_id=1, positive=True
rows = database.get_prism_audit_for_run('closeout-f11-test-31660')
# → count=1
#   run_id='closeout-f11-test-31660'
#   agent_role='technicals_analyst'
#   phase='initial_read'
#   content='F11 round-trip verification'
```
Round-trip verified. Append-only confirmed (no update/delete accessor exists per docstring at `database.py:1225`).

**`insert_prism_audit_entry` signature** (`database.py:1217–1249`):
```python
def insert_prism_audit_entry(run_id: str, agent_role: str, phase: str, content: str) -> int:
    ...
    cursor.execute(
        "INSERT INTO prism_audit_log (run_id, agent_role, phase, content) VALUES (?, ?, ?, ?)",
        (run_id, agent_role, phase, content),
    )
    ...
    return row_id  # always > 0
```
Parameterized query (no f-strings); returns `int`.

**`insert_advisor_observation` always stores `is_advisory_only=1`** (`database.py:1069–1091`):
```python
# is_advisory_only is always stored as 1 regardless of any caller-supplied value
# in **kwargs — the Advisor never moves money.
...
cursor.execute(
    "INSERT INTO advisor_observations "
    "... VALUES (?, ?, ?, ?, ?, 1, ?, ?)",  # hardcoded 1 at position 6
    ...
)
```
`is_advisory_only=1` is hardcoded at the SQL layer — cannot be overridden by any caller.

**State DB: zero writes from F11 probe.** The F11 round-trip used a temp DB (`DB_PATH` set to temp path before database import). Live state DB was untouched.

---

## F12 — Agent-callable CLI writer

**[PASS]**

**CLI round-trip on TEMP DB:**
```bash
echo "F12 CLI round-trip test content" | DB_PATH=/tmp/... python -m advisors.prism_audit_write \
    --run-id closeout-f12-test \
    --role technicals_analyst \
    --phase initial_read
```
```
STDOUT: '1'
STDERR: ''
Return code: 0
Row ID: 1 (positive=True)
Round-trip rows: 1
  content: 'F12 CLI round-trip test content'
```
STDOUT is a positive integer row id, STDERR is empty, exit code 0.

**Error arm (missing `--run-id`):**
```
STDOUT: '' (empty — no row id, no traceback)
STDERR: 'usage: prism_audit_write [-h] --run-id RUN_ID --role ROLE --phase PHASE\nprism_audit_write: error: the following arguments are required: --run-id'
Return code: 2 (non-zero — correct)
Traceback in STDOUT: False
```
D-1 confirmed: no traceback in STDOUT; argparse usage + error in STDERR (not a Python traceback).

**D-1 error contract in source** (`advisors/prism_audit_write.py:42–82`):
```python
def _main(argv: list[str] | None = None) -> int:
    """Entry point — returns exit code. Never raises uncaught exceptions."""
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2          # argparse already wrote usage to stderr; no traceback
    except Exception as exc:
        sys.stderr.write(f"{type(exc).__name__}\n")  # D-1: type name only
        return 2
    ...
    except Exception as exc:
        sys.stderr.write(f"{type(exc).__name__}\n")  # D-1: type name only
        return 1
```
All paths: either exit-2 (argparse handles its own STDERR) or `type(exc).__name__` to STDERR. No traceback, no message, no raw exception.

No Flask dependency confirmed (file imports only `argparse`, `sys` at module level; `database` imported lazily inside `_main`).

**State DB: zero writes.** F12 probe used a temp DB path.

---

## F13 — Nightly pipeline 4-pass (Wave 1: dry-run shape + code cite)

**[PASS — Wave 1 portion]**

**`run_pipeline(dry_run=True)` — shape check:**
```python
result = lens_pipeline.run_pipeline(dry_run=True)
# keys: ['run_ts', 'lenses_attempted', 'lenses_available', 'market_prism_row_id', 'error_count']
# → run_ts: '2026-06-17T14:52:13.827756+00:00'
# → lenses_attempted: 5  (all 5 lenses called)
# → lenses_available: 5  (all 5 available — consistent with Group A live calls)
# → market_prism_row_id: None  (dry_run=True skips DB write — confirmed)
# → error_count: 0
```
All 5 lenses attempted with per-lens isolation; no DB write (dry_run); no Claude spend. Shape matches the documented `dict` contract.

**Dry-run skips synthesis and DB write — code cite** (`advisors/lens_pipeline.py:376–413`):
```python
# Pass 3 — Synthesis via Claude (skipped in dry_run).
if dry_run:
    ...
    sentiment_rationale = "dry_run — synthesis skipped."

...
if not dry_run:
    # Pass 4 — Write the MARKET_PRISM row.
    market_prism_row_id = database.insert_advisor_observation(...)
```

**F20 code cite — `ADVISOR_SYNTHESIS_MODEL` env var:**

`advisors/lens_pipeline.py:285`:
```python
model=os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-8"),
```
Default: `claude-opus-4-8`. No hardcoded `claude-haiku-4-5-20251001`.

`ai_advisor.py:63–69` (shared accessor):
```python
def resolve_advisor_model() -> str:
    ...
    return os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-8")
```

`ai_advisor.py:1639` (second call site):
```python
model=os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-8"),
```

**Haiku literal grep:**
- `grep 'claude-haiku' advisors/lens_pipeline.py` → 0 hits
- `grep 'claude-haiku' ai_advisor.py` → 0 hits

No stale Haiku literal on the synthesis path. C1 (PR #41, merged at 73dc603) is reflected in code.

**[interpretation]** `lens_pipeline.py:285` reads `os.environ.get(...)` directly rather than calling `ai_advisor.resolve_advisor_model()` — two reads of the same env var with the same default literal. Both resolve to `claude-opus-4-8`. Minor cohesion gap (duplicate default literal), not a defect. Flagged as a non-blocking note for the doc-writer (consistent with the scope matrix AC-1 note).

**Wave 2 (non-dry-run live write) — BLOCKED** pending PM "deploy done" signal.

---

## F14 — 03:00 scheduler wiring

**[PASS]**

**Static cite — `run_scheduler` 03:00 registration** (`app.py:437–443`):
```python
def run_scheduler():
    schedule.every().minute.at(":00").do(threaded_trigger)
    schedule.every().minute.at(":00").do(_refresh_account_totals)
    schedule.every().day.at("02:00").do(_run_trigger_retention)
    # Component 7+8: daily off-hours lens pipeline — Market Prism summary (CYCLE4-BRIEF.md).
    # Runs at 03:00 (off-hours) so it never overlaps the live market-hours execution path.
    schedule.every().day.at("03:00").do(_run_lens_pipeline)
```
03:00 job registered; comment confirms off-hours (no overlap with `:00` execution path).

**CC-2 lazy import confirmed** (`app.py:410–423`):
```python
def _lens_pipeline_worker() -> None:
    """Background worker that runs the off-hours lens pipeline.
    Imported lazily to keep advisors.lens_pipeline off the execution path (CC-2).
    """
    try:
        from advisors.lens_pipeline import run_pipeline  # lazy — not module-level (CC-2)
        result = run_pipeline()
        ...
    except Exception as exc:
        _daemon_log.error("Lens pipeline worker failed: %s", type(exc).__name__)
```

Import of `advisors.lens_pipeline` is inside the worker function body — never at module-level. CC-2 confirmed. `_run_lens_pipeline` spawns a daemon thread (`app.py:433`), so the scheduler returns immediately and never blocks the 1-min execution path.

---

## Summary — Group B

| Feature | Status | Key Evidence |
|---|---|---|
| F9 Warehouse round-trip | PASS | Temp DB: WAL confirmed, secret-strip (`api_key`+`token` recursive), row round-trips; sentinel: `lens_warehouse.py:52–58` RuntimeError under pytest |
| F10 Warehouse wiring | PASS | Real warehouse DB has 4 macro + 1 sentiment rows from Group A; separate from state DB (no `lens_snapshots` in state); raw_json zero secrets; persist cites `ai_advisor.py:633–645`/`:858–870` |
| F11 Migration 032 + accessors | PASS | `_MIGRATION_FILES[-1]='032_prism_audit_log.sql'` (29 total); temp DB round-trip: insert→fetch returns all 4 fields; `is_advisory_only=1` hardcoded at SQL layer `database.py:1091`; zero state-DB writes |
| F12 CLI writer | PASS | Subprocess: STDOUT='1' (positive int), exit 0; error arm STDOUT empty, exit 2, no traceback; D-1: `type(exc).__name__` on all error paths in `prism_audit_write.py:51–78`; no Flask dep; zero state-DB writes |
| F13 Nightly pipeline 4-pass (Wave 1) | PASS (partial) | dry_run=True: lenses_attempted=5, lenses_available=5, market_prism_row_id=None; env-var read at `lens_pipeline.py:285` default `claude-opus-4-8`; zero Haiku literals; non-dry-run BLOCKED (Wave 2) |
| F14 Scheduler wiring | PASS | `app.py:443` `schedule.every().day.at("03:00").do(_run_lens_pipeline)`; CC-2 lazy import at `app.py:417`; daemon thread at `:433` — scheduler never blocks |

**No new FAILs in Group B.** The F4-DOC-1 finding from Group A remains the only active closeout FAIL.

**AC-12 confirmation:** State DB received zero writes from all Group B probes. F9 and F12 used isolated temp DBs. F11 used a temp DB. F13 dry_run skipped the DB write. The real `alphabot_warehouse.db` received no writes from audit probes (Group A live-lens calls appended genuine lens snapshot rows — same as what the 03:00 nightly pipeline would produce; noted as provenance: closeout audit 2026-06-17).
