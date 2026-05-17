# Schema-Layer Post-Merge Audit — 2026-05-17

**Repo HEAD audited:** `0228a37` (main)
**Mode:** Read-only research. No code changes.
**Scope:** Migrations 004–008, `_MIGRATION_FILES` runner, telemetry write paths, retention rotation, `bot_state` JSON blob extensions (M1F / DM / V3), two-DB pattern integrity.

---

## 1. Migration sequence integrity

**File:** `database.py:497-549`
**`_MIGRATION_FILES` declaration:** `database.py:503-509`

```
"004_schema_migrations_tracker.sql",
"005_exit_triggers.sql",
"006_autotune_runs_sharpe.sql",
"007_autotune_runs_frozen_eval.sql",
"008_shadow_history.sql",
```

- **Order:** 004 → 005 → 006 → 007 → 008. Strictly monotonic, lexicographic, matches the on-disk filename order. **VALIDATED.**
- **Idempotency:** `run_migrations()` (`database.py:512-549`) first ensures `schema_migrations` exists via `CREATE TABLE IF NOT EXISTS` (lines 520-525), then for each entry checks `SELECT 1 FROM schema_migrations WHERE migration_name = ?` and skips if already applied (lines 528-534). `INSERT OR IGNORE` records the apply (lines 541-544). Each SQL file independently uses `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`. **VALIDATED.**
- **Additive-first invariant:** Every migration is either `CREATE TABLE IF NOT EXISTS …` (004, 005, 008) or `ALTER TABLE … ADD COLUMN … DEFAULT NULL` (006, 007). No `DROP`, no destructive `ALTER`. **VALIDATED.**
- **ISSUE (Low) — non-atomic apply / no-tracker-on-failure:** `run_migrations()` wraps `executescript + INSERT OR IGNORE` in a single try/except that catches and logs but does **not** roll back partial application. SQLite `executescript` auto-commits between statements, so a mid-script failure could leave a partial schema with no tracker row, causing a retry on next start. Risk is low because every statement is idempotent (`IF NOT EXISTS` / additive ADD COLUMN where re-add raises "duplicate column"), but the runner does not catch the duplicate-column case as a "already applied" condition either — it logs the error and leaves the tracker un-written, so the next start re-tries and re-logs. **Recommendation:** wrap each migration in `BEGIN/COMMIT`, treat "duplicate column" as success-to-record. Low severity (does not currently produce incorrect state).

---

## 2. Migration 004 — schema_migrations tracker

**File:** `migrations/004_schema_migrations_tracker.sql`

- Single `CREATE TABLE IF NOT EXISTS schema_migrations (migration_name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))`. Tracking works as expected; PK enforces single-apply-record per migration. **VALIDATED.**

---

## 3. Migration 005 — exit_triggers (H1)

**File:** `migrations/005_exit_triggers.sql:19-37`

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | matches A/C |
| `ts_utc` | TEXT NOT NULL | matches A/C |
| `ts_et` | TEXT NOT NULL | matches A/C |
| `symphony_id` | TEXT NOT NULL | matches A/C |
| `account_id` | TEXT NULLable | excluded from API per PA-9 (`database.py:840-841` SELECT list omits it) |
| `triggered_reason` | TEXT NOT NULL | matches A/C |
| `at_return` | REAL | matches A/C |
| `gate_state_json` | TEXT | JSON blob — see H2 below |
| `cycle_id` | TEXT | matches A/C |

- **Indexes (`migrations/005_exit_triggers.sql:32-37`):** `idx_exit_triggers_ts (ts_utc DESC)` and `idx_exit_triggers_symphony_ts (symphony_id, ts_utc DESC)`. **VALIDATED.**
- **H2 `also_true` extension lives in `gate_state_json`:** `alpha_bot_execution.py:1214-1221` writes `also_true` as a key inside the `gate_state` dict passed to `record_exit_trigger`. No schema migration was issued for H2. **VALIDATED.**
- **Retention 90 days / `TRIGGER_TELEMETRY_RETENTION_DAYS`:** `app.py:193-197` reads env var with default `"90"`, calls `database.prune_old_triggers(retention_days)`. `prune_old_triggers` (`database.py:848-877`) batches DELETE in 1000-row chunks. **VALIDATED.**

---

## 4. Migration 006 — autotune_runs_sharpe (O2)

**File:** `migrations/006_autotune_runs_sharpe.sql:21-22`

- Two `ALTER TABLE autotune_runs ADD COLUMN <col> REAL DEFAULT NULL` statements: `deflated_sharpe`, `naive_sharpe`. NULLable + explicit DEFAULT NULL. Existing rows are preserved with NULL. **VALIDATED.**
- Note on idempotency: SQLite raises "duplicate column name" on re-apply. The schema_migrations tracker prevents this in normal flow (the migration is recorded after a successful apply). **VALIDATED (relies on tracker).**

---

## 5. Migration 007 — autotune_runs_frozen_eval (O6)

**File:** `migrations/007_autotune_runs_frozen_eval.sql:9-10`

- Two `ALTER TABLE autotune_runs ADD COLUMN <col> REAL DEFAULT NULL` statements: `validation_sharpe`, `frozen_eval_sharpe`. Same additive-first pattern. **VALIDATED.**
- The `init_db()` `CREATE TABLE` for `autotune_runs` (`database.py:52-67`) lists ALL columns including 006+007 additives, so a fresh DB skips the migrations (still recorded by name to the tracker once `run_migrations()` runs). **VALIDATED.**

---

## 6. Migration 008 — shadow_history (M1F)

**File:** `migrations/008_shadow_history.sql:8-25`

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | implicit |
| `ts_utc` | TEXT NOT NULL | matches A/C |
| `ts_et` | TEXT NOT NULL | matches A/C |
| `trading_day` | TEXT NOT NULL | matches A/C |
| `symphony_id` | TEXT NOT NULL | matches A/C |
| `account_id` | TEXT NULLable | matches A/C |
| `cycle_id` | TEXT NULLable | matches A/C |
| `current_return` | REAL NOT NULL | PA-M1F-10 — must be supplied |
| `shadow_return` | REAL NOT NULL | PA-M1F-10 — must be supplied |
| `is_post_trigger` | INTEGER NOT NULL DEFAULT 0 | matches A/C |
| `trigger_id` | INTEGER NULLable | advisory soft-ref, NO FK (per BC-2) |
| `math_mode` | TEXT NOT NULL DEFAULT 'per_symphony' | AC-M1F.6.5 — additional column not in audit checklist but present |

- **No FOREIGN KEY clause** on `trigger_id` — confirmed by inspection. **VALIDATED.**
- **Indexes (`migrations/008_shadow_history.sql:23-25`):** `idx_shadow_history_sym_day (symphony_id, trading_day, ts_utc)`, `idx_shadow_history_day (trading_day, ts_utc)`, `idx_shadow_history_ts_utc (ts_utc)`. **VALIDATED.** (The first index is `(sym, day, ts)` not `(sym, day)` — covers both the prefix `(sym, day)` query and `ORDER BY ts_utc` per-symphony-day.)
- **`ts_et` hardcoded UTC-4 (no DST handling):** `database.record_shadow_observation` receives `ts_et` as a string from the caller, but the H1 telemetry sibling — `database.record_exit_trigger` at `database.py:572-575` — computes `ts_et = (now_utc - timedelta(hours=4))`, fixed UTC-4. Same pattern is enforced for shadow callers by AC-M1F. **VALIDATED.**

---

## 7. `bot_state` JSON additive fields

The `bot_state` table is `(id INTEGER PRIMARY KEY, data TEXT)` (`database.py:37`) — single-row JSON blob. New keys are pure JSON additions; no schema migration required. **VALIDATED.**

### M1F `shadow_divergence` (read-side shape)

Produced by `database.get_shadow_divergence` (`database.py:772-809`):

```
{"by_symphony": {<id>: {"today": float|None, "cumulative": float|None}},
 "portfolio_today": float|None}
```

Consumed in `app.py:249-256` (waiting state) and `app.py:391-410` (live state). Both paths share the same key name `portfolio_today` and same `by_symphony` substructure. **VALIDATED.**

- **ISSUE (Low) — `cumulative` is hardcoded None:** `database.py:804` writes `"cumulative": None` unconditionally; no rolling-sum query is run. Whether this is intentional (M1F may only define `today` as a real value) is plan-dependent; the shape is stable. Flagging as Low because consumers must handle `None` gracefully and the field exists for forward compatibility.

### DM `last_market_close_snapshot` (write-once-per-day at EOD)

Written in `alpha_bot_execution.py:722-746`:

```
{"trading_day": <YYYY-MM-DD>,
 "captured_at_et": <HH:MM:SS ET>,
 "data_as_of": <HH:MM ET>,
 "portfolio_strip": {"today_change": None, "cumulative_return": None, "max_drawdown": None},
 "shadow_divergence": {"by_symphony": <dict>, "portfolio": float|None},
 "accounts_map": <dict>}
```

- **Write-once guard:** `alpha_bot_execution.py:722` — `if bot_state.get("last_market_close_snapshot", {}).get("trading_day") != current_date_str:`. EOD post-mortem path only. **VALIDATED.**
- **Shape stability:** Read side `app.py:225-241` remaps the snapshot's `shadow_divergence["portfolio"]` -> `shadow_divergence["portfolio_today"]` (lines 230-231) before serving — confirms a known **key-name asymmetry** between snapshot (key: `portfolio`) and live path (key: `portfolio_today`).
- **ISSUE (Low) — `shadow_divergence.portfolio` vs `portfolio_today` key asymmetry:** Snapshot stores `portfolio`, live serves `portfolio_today`. `app.py:230-231` runs a defensive remap each request. Risk: any new consumer that reads the raw snapshot without going through `/api/state` will see the wrong key. **Recommendation:** normalize at write time (engine writes `portfolio_today`) so the dashboard remap can be removed. Low severity (working as designed; defensive remap covers it).

### V3 `fleet_correlation_alert`

Written by `set_fleet_correlation_alert` (`alpha_bot_execution.py:325-338`):

```
{"tripped_at_et": <ISO no tz>,
 "triggered_reason": <str>,
 "tripped_count": <int>,
 "active_count": <int>}
```

Shape exactly matches the audit checklist. Dismiss path `dismiss_fleet_correlation_alert` (`alpha_bot_execution.py:341-349`) and auto-clear after `FLEET_CORRELATION_CLEAR_MINUTES` (lines 367-380) are idempotent `dict.pop(..., None)`. Read at `app.py:241` and `app.py:410`. **VALIDATED.**

---

## 8. Two-DB pattern integrity

- **State DB:** `alphabot_state.db` (`database.py:10`). Owns `bot_state`, `execution_lock`, `chart_history`, `chart_archive`, `symphony_strategies`, `autotune_runs`, `llm_suggestions`, `exit_triggers`, `shadow_history`, `schema_migrations`.
- **Optimization DB:** `optuna_studies.db` (`autotuner.py:519`). Owns Optuna's internal `studies` and trial tables.
- **Cross-join check:** No `ATTACH DATABASE` statements anywhere in app code (`Grep ATTACH` returned only English-prose hits — "Attach last_trigger to each symphony", multipart "file attachment", skill-doc warning grammars). No SQL cross-joins between the two DBs. **VALIDATED.**
- **Migration targeting:** `_MIGRATION_FILES` (`database.py:503-509`) targets `alphabot_state.db` only. `optuna_001_archive_accumulated_studies.sql` lives outside that list and is applied separately by `_apply_optuna_archive_migration_if_needed` (`autotuner.py:505-541`) against `optuna_studies.db`. Targeting is **correct**. **VALIDATED.**

---

## 9. Telemetry write integrity

- **H1 `record_exit_trigger` opens its own connection** (`database.py:579`) — does NOT join `save_state`'s transaction. Comment at `database.py:566-568` explicitly states this. **VALIDATED.**
- **try/except wrapper:** `database.py:578-598` wraps the entire INSERT path; on any exception, `logging.error(...)` and continue. Cycle never fails on telemetry. **VALIDATED.**
- **Call site:** `alpha_bot_execution.py:1207-1223` invokes it immediately after setting the `triggered = True` bot_state keys — same code path block, NOT same SQLite transaction. The non-blocking guarantee holds: write to bot_state JSON happens via `save_state` later in the cycle; telemetry write is independent. **VALIDATED.**
- **M1F `record_shadow_observation`** follows the same pattern (`database.py:608-655`): own connection, try/except, log-on-fail. **VALIDATED.**

---

## 10. Retention rotation

- **Trigger rotation:** `_run_trigger_retention` (`app.py:191-202`) is scheduled via `schedule.every().day.at("02:00").do(_run_trigger_retention)` (`app.py:206`). Runs at 02:00 daily — separate from minute scheduler, separate from cycle write path. **VALIDATED.**
- **Shadow rotation:** Same callback (`app.py:198-202`) — `database.prune_old_shadow_history(SHADOW_HISTORY_RETENTION_DAYS)`, default 180. Per BC-4, lives in the existing scheduler callback (not a Flask route). **VALIDATED.**
- **Batched DELETE pattern:** Both `prune_old_triggers` (`database.py:848-877`) and `prune_old_shadow_history` (`database.py:658-686`) loop with 1000-row batches and a portable `WHERE id IN (SELECT id … LIMIT 1000)` subquery (works regardless of `SQLITE_ENABLE_UPDATE_DELETE_LIMIT` build flag). **VALIDATED.**

---

## Cross-cutting concerns

1. **No PRAGMA `journal_mode=WAL` set at connection-open in `database.py`.** `get_connection()` (`database.py:29-30`) is a vanilla `sqlite3.connect(DB_FILE, timeout=10.0)` with no journal-mode set. The agent's own operating rules (and `flask-dashboard-specialist.md:21`) require WAL for concurrent reader + single writer. SQLite defaults to delete-mode rollback journal; concurrent dashboard reads + daemon writes work under WAL only. Tests do set `PRAGMA journal_mode=WAL` (`tests/telemetry/test_exit_triggers_schema.py:89,832`; `tests/shadow/test_shadow_history.py:85`), suggesting WAL is the *expected* runtime mode but it is **not actually applied to the production DB by app code**. **ISSUE (Medium) — WAL never explicitly enabled in production.** A previously WAL-enabled DB stays WAL across opens; a fresh deploy on a new host gets default rollback mode and silently degrades concurrency. **Recommendation:** add `PRAGMA journal_mode=WAL` once at `init_db()` time (it persists).

2. **No `?mode=ro` read-only connection use anywhere.** `get_connection()` is the single entry point, and it's read/write. Dashboard read paths (`/api/state`, `/api/triggers`, etc.) all use this writable connection, in conflict with the sqlite-specialist agent rule: "Dashboard routes and read-only utilities must use the URI form with `?mode=ro`." **ISSUE (Medium) — read-only intent not enforced at the connection layer.** Risk: any errant `INSERT/UPDATE` from a dashboard handler reaches production state with no defense. Not exploited today (handlers only `SELECT`), but the agent's invariant is violated. **Recommendation:** introduce a `get_ro_connection()` helper using `sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True)` and route dashboard handlers + telemetry readers through it.

3. **`run_migrations()` swallows failures silently to log only.** `database.py:546-547` logs at ERROR but allows daemon startup to proceed even if a migration failed. Combined with concern 1 above (no atomic apply), a partial schema could go undetected. **ISSUE (Low) — migration failure does not halt boot.** Acceptable risk because every migration is currently idempotent and additive, but a future destructive migration would expose this. **Recommendation:** raise on migration failure unless a `--skip-migrations` flag is given.

4. **Pruning runs against the same writable connection as cycle writes.** `prune_old_triggers` (`database.py:863`) uses `get_connection()`, which under WAL still respects single-writer-at-a-time. The 02:00 schedule slot is outside market hours (no cycle writes happening), so contention is effectively zero. **VALIDATED in current configuration.**

5. **No fixture DB updates audited.** Per the agent's "schema diffs must be paired with a fixture update" rule, I'd expect to see fixture refresh evidence. Tests at `tests/telemetry/test_exit_triggers_schema.py`, `tests/shadow/test_shadow_history.py`, `tests/autotuner/test_o2_deflated_sharpe.py`, `tests/autotuner/test_o6_frozen_eval.py` create their own ephemeral DBs from the migration SQL at runtime (the WAL PRAGMAs above are evidence). No persistent fixture DBs to refresh. **VALIDATED (no fixture DBs to update).**

---

## Summary verdict table

| Item | Verdict |
|---|---|
| Migration sequence + ordering | VALIDATED (+ Low issue: non-atomic apply) |
| 004 schema_migrations | VALIDATED |
| 005 exit_triggers schema + indexes + retention + H2 `also_true` in JSON | VALIDATED |
| 006 autotune_runs_sharpe additive | VALIDATED |
| 007 autotune_runs_frozen_eval additive | VALIDATED |
| 008 shadow_history schema + indexes + UTC-4 + no-FK | VALIDATED |
| `bot_state.shadow_divergence` shape | VALIDATED (+ Low: `cumulative` hardcoded None) |
| `bot_state.last_market_close_snapshot` write-once + shape | VALIDATED (+ Low: `portfolio` vs `portfolio_today` key asymmetry) |
| `bot_state.fleet_correlation_alert` shape | VALIDATED |
| Two-DB pattern, no cross-joins, correct targeting | VALIDATED |
| Telemetry non-blocking + own connection + try/except | VALIDATED |
| Retention rotation separate from cycle write path | VALIDATED |
| WAL journal mode in production code | **ISSUE (Medium)** — never explicitly enabled in `database.py` |
| Read-only `?mode=ro` for dashboard | **ISSUE (Medium)** — never used; agent invariant violated |
| Migration failure halts boot | **ISSUE (Low)** — logs only |

**Aggregate:** 5 migrations fully validated. 2 Medium issues (WAL + RO connection) are pre-existing connection-layer gaps, not regressions introduced by the engine-correctness remediation. 3 Low issues are documentation/defensive-coding nudges.
