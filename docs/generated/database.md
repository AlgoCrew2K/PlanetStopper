# database

> SQLite state management for Planet Stopper: schema, migrations, all read/write accessors for the state DB, and a pytest sentinel guard that structurally prevents tests from writing to the production DB.

**Source:** `database.py`
**Last updated:** 2026-06-29 (DE-ADVISOR-LATENCY: `get_latest_market_lens_cache()` accessor; prior: DE-PRISM-SOURCES-001 `get_latest_market_prism_sources_for_run`)

## Overview

`database.py` is the single write layer for `alphabot_state.db`. It owns schema initialization, 32 numbered migration SQL files (001–032), and every public accessor function. `_MIGRATION_FILES` wires 29 active entries (004–032); migrations 001–003 use a separate bootstrap path. The dashboard uses `get_ro_connection()` for all reads; the engine uses `get_connection()` for writes. The two-DB pattern (state DB here; Optuna studies in a separate DB) is an architecture hard rule — no cross-DB joins in application code.

WAL journal mode is enabled at `init_db()` time, allowing concurrent Flask reads while the engine holds a write lock.

**Pytest sentinel guard (added 2026-06-10):** `_db_file()` raises `RuntimeError` when `"pytest" in sys.modules` AND the resolved path basename equals `alphabot_state.db`. This is gated on `sys.modules` so the live daemon (which never imports pytest) is completely unaffected. Tests must set `DB_PATH` to a temp file before triggering any DB access; `tests/conftest.py` does this via `pytest_configure()` (the earliest hook, before collection) and reinforces it with an autouse `_isolate_db` fixture per test.

## Schema Migrations

Migrations are listed in `_MIGRATION_FILES` and applied by `run_migrations()`. They are idempotent (tracked in `schema_migrations`). Current highest: **032** (`032_prism_audit_log.sql`).

Notable ordering: 021 is listed before 020 — intentional. See `ARCH-002` inline comment; reordering would corrupt live DBs.

Migrations 026–032:
- `026_mc_regime_match_telemetry.sql` — regime match columns on `exit_triggers`
- `027_regime_label_cache.sql` — `regime_label_cache` table
- `028_autotune_runs_pbo.sql` — `pbo` column on `autotune_runs`
- `029_exit_triggers_also_true.sql` — `also_true_json` column on `exit_triggers`
- `030_per_symphony_live_mode.sql` — `live_mode` on `symphony_strategies`, `config_audit_log` table
- `031_shadow_history_sym_ts_index.sql` — composite index on `shadow_history (symphony_id, ts_utc)`
- `032_prism_audit_log.sql` — `prism_audit_log` table + `idx_prism_audit_log_run_id` index (Prism Phase 1)

## Public API Reference

### Connection Helpers

#### `_db_file() → str` (internal)

Resolves the active DB path: explicit `DB_FILE` override first; then `DB_PATH` env var; then `DB_FILE` default. **Pytest sentinel guard:** raises `RuntimeError` when `"pytest" in sys.modules` and `os.path.basename(resolved) == "alphabot_state.db"`. This converts a silent test→prod-DB leak into a loud, immediate failure. Completely inert in the live daemon.

#### `get_connection() → sqlite3.Connection`
Opens a read-write connection to the state DB (10s timeout).

#### `get_ro_connection() → sqlite3.Connection`
Opens a read-only connection via SQLite URI `?mode=ro`. Dashboard read handlers and Advisor reads use this to avoid holding a write lock.

#### `init_db() → None`
Creates base tables, inserts sentinel rows, then calls `run_migrations()`. Sets WAL journal mode.

#### `run_migrations() → None`
Applies any pending migration files from `_MIGRATION_FILES`. Idempotent — already-applied migrations are skipped. Swallows `duplicate column name` errors (H1 DUAL-WRITE reconciliation).

---

### Lock Management

#### `acquire_lock() → bool`
Attempts to set `execution_lock.is_locked = 1`. Returns `False` if a lock has been held for under 60 seconds (stale-expiry guard). Used by `alpha_bot_execution.py` to serialize per-minute cycles.

#### `release_lock() → None`
Clears the execution lock. Preserves `timestamp` for stale-expiry inspection.

---

### Bot State

#### `load_state() → dict`
Returns the current `bot_state` JSON blob as a Python dict.

#### `save_state(state_dict: dict) → None`
Writes `state_dict` as JSON to `bot_state`.

#### `wipe_transient_state(state_dict: dict) → dict`
Clears per-cycle transient keys (HWM, trigger flags, counters) from all symphony sub-dicts. Stamps a new `position_epoch` when the position was triggered (AC-3). Returns the mutated dict.

#### `mint_position_epoch() → str`
Returns a fresh `uuid4().hex` — opaque, collision-free epoch identifier stamped at each position-lifecycle boundary.

---

### Symphony Strategy

#### `get_symphony_strategy(symphony_name: str) → dict`
Returns `{"params": {...}, "locked_vars": [...]}` for the named symphony. Auto-inserts `DEFAULT_STRATEGY` / `DEFAULT_LOCKED_VARS` if absent.

#### `save_symphony_strategy(symphony_name: str, params: dict, locked_vars: list) → None`
Upserts a symphony's strategy parameters and locked-var list.

#### `normalize_name(name: str) → str`
Returns `name.strip().lower()` — canonical form for all symphony name lookups.

---

### Per-Symphony Live Mode (migration 030)

#### `get_symphony_live_mode(symphony_name: str) → int`
Returns the per-symphony live_mode flag: `1` = live, `0` = dry-run (default when no row exists). Normalizes `symphony_name` the same way `save_symphony_strategy` does. Architecture rule 4: `is_live=True` is explicit, never by omission.

#### `set_symphony_live_mode(symphony_name: str, live: int, operator: str) → None`
Sets the per-symphony live_mode flag and writes an immutable `config_audit_log` entry. `live` must be `0` (dry-run) or `1` (live). `operator` is the caller-supplied identity string recorded in the audit log. If no `symphony_strategies` row exists, creates a minimal row with `DEFAULT_STRATEGY` params before setting live_mode. Called by `POST /api/symphony-settings/<name>` (CSRF-protected).

---

### Regime Cache (migration 027)

#### `save_regime_label(symphony_id: str, label: str, as_of_date: str) → None`
Persists the regime classifier label for a symphony on a given date in the `regime_label_cache` table.

#### `get_cached_regime_label(symphony_id: str, as_of_date: str) → str | None`
Returns the cached regime label for a symphony/date pair, or `None` if not yet computed.

---

### Phase-1.5 M3 Bundle Registry

#### `get_or_create_phase15_m3_bundle_id() → int`
Idempotent. Inserts the Phase-1.5 M3 spec bundle if absent and returns its integer `id`. Analogous to `get_or_create_phase1_theory_bundle_id` for the M3 regime-exit spec.

---

### Autotune Run Persistence

#### `save_autotune_run(...) → int`
Inserts one `autotune_runs` row and returns the new `cursor.lastrowid`. Sprint 3 fix (S3-AUDIT-001): previously returned `None`.

**Key parameters:**
| Name | Type | Description |
|------|------|-------------|
| `run_timestamp` | `str` | ISO-8601 timestamp of the run |
| `symphony_id` | `str` | Symphony identifier |
| `oos_alpha` | `float \| None` | OOS guard-alpha |
| `train_alpha` | `float \| None` | Train fold guard-alpha |
| `baseline_decision` | `str \| None` | "AI", "fallback", or "default" |
| `spec_bundle_id` | `str \| None` | bundle_hash of the active Phase-1 spec bundle |
| `n_effective` | `int \| None` | N_optuna + S (honest multiple-testing count) |
| `d_spec` | `int \| None` | COUNT DISTINCT BACKTEST_SELECTION bundle ids |
| `gamma` | `float \| None` | Frozen CRRA risk-aversion coefficient |
| `overfitting_verdict` | `str \| None` | Overfitting Conscience summary string |
| `pbo` | `float \| None` | Probability of backtest overfitting from CSCV gate (Phase-3; migration 028) |

**Returns:** `int` — the new row id.

#### `get_latest_autotune_run(symphony_id: str, account_id=None, math_mode="per_symphony") → dict | None`
Returns the most-recent `autotune_runs` row for the symphony as a dict, or `None` if Optuna has not run.

#### `get_all_autotune_runs(limit: int = 50) → list[dict]`
Returns the `limit` most-recent rows across all symphonies (dashboard `/api/autotune-runs`).

---

### Advisor Observations

Append-only audit trail. No UPDATE or DELETE accessor — rows are immutable.

#### `insert_advisor_observation(*, advisor_role, subject_type, subject_id, verdict=None, raw_response=None, spec_bundle_id=None, symphony_id=None, **kwargs) → int`
Inserts one `advisor_observations` row. Returns the new row id. `is_advisory_only` is always written as `1`. `symphony_id` is the denormalized symphony name added by migration 025 so the `/api/advisor-observations?symphony_id=` filter works without fan-out queries.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `advisor_role` | `str` | `"OVERFITTING_CONSCIENCE"`, `"SPEC_CRITIC"`, `"DIVERGENCE_EXPLAINER"`, `"WALL_BREACH"`, `"MARKET_PRISM"`, `"MARKET_PRISM_SOURCES"`, or `"MARKET_LENS_CACHE"` |
| `subject_type` | `str` | `"autotune_run"`, `"spec_bundle"`, `"fold_role_wall"`, or `"portfolio"` |
| `subject_id` | `str` | String PK of the observed entity |
| `verdict` | `str \| None` | `"CLEAR"`, `"WATCH"`, `"BREACH"`, `"INFORMATIONAL"`, `"NOT_APPLICABLE"`, `"neutral"`, `"bullish"`, `"bearish"`, or `"limited-inputs"` |
| `raw_response` | `dict \| str \| None` | Serialized to JSON; `None` → `"{}"` |
| `symphony_id` | `str \| None` | Denormalized symphony name (migration 025) |

**Returns:** `int` — new row id.

#### `get_advisor_observations_for_symphony(symphony_id: str) → list[dict]`
Returns all `advisor_observations` rows whose `symphony_id` column matches, oldest-first. Uses `get_ro_connection()`.

#### `get_advisor_observations_for_subject(subject_type: str, subject_id: str) → list[dict]`
Returns all rows for a given subject, oldest-first.

#### `get_advisor_observations_for_role(advisor_role: str, limit: int = 50) → list[dict]`
Returns rows for a given advisor role, newest-first.

#### `get_latest_market_prism_summary() → dict | None`
Returns the most recently inserted `advisor_observations` row with `advisor_role="MARKET_PRISM"`, deserialized (including `raw_response` as a dict), or `None` when no row exists. Used by the Cycle-5 Overview tab to render the always-on Market Prism block.

#### `get_latest_market_prism_sources_for_run(run_id: str) → dict | None`
Returns the MARKET_PRISM_SOURCES row for this `run_id`, or `None`. Uses an exact `json_extract(raw_response,'$.run_id') = ?` SQLite match — no scan-and-compare loop. Returns `None` on mismatch — **no fallback to a different run's citations** (stale-citation-bleed guard). D-1 never-raises; uses `get_ro_connection()`. Used by `app.py:ai_advisor_tab()` to ensure the citation overlay matches the currently-displayed MARKET_PRISM row. Each MARKET_PRISM_SOURCES row holds rebuilt-at-patch-time citation metadata in `raw_response.per_lens_digest[lens].article_corpus = [{url, title, published}]`.

#### `get_latest_market_lens_cache() → dict | None`

Returns the most recent `MARKET_LENS_CACHE` advisor_observations row as a fully-parsed dict (with `raw_response` deserialized from JSON), or `None`.

Used by `ai_advisor.assemble_advisor_context` to serve the 5 market-wide lens blocks from the nightly cache instead of making 17–29 live external API calls per advisor click (DE-ADVISOR-LATENCY).

**Row shape when present:**
```
{
    ...,                     # standard advisor_observations columns
    "raw_response": {
        "captured_at": "<ISO UTC timestamp>",
        "lenses": {
            "technicals":  { "lens": "technicals", "available": ..., ... },
            "sentiment":   { ... },
            "derivatives": { ... },
            "macro":       { ... },
            "fundamentals":{ ... }
        }
    }
}
```

**Ordering:** `ORDER BY id DESC LIMIT 1` — insertion order is a reliable recency proxy for sequential nightly writes (all writes are from `prism_scheduler._patch_provenance`; no concurrent multi-writer for this role).

**D-1 never-raises:** any exception (DB error, missing connection, parse failure) degrades to `None` — the caller treats `None` as cache miss and produces honest `available=False` lens blocks. Uses `get_ro_connection()` per architecture constraint 5.

**Cold-start:** returns `None` before the first nightly council run has written a row. The caller (`assemble_advisor_context`) handles this gracefully without falling back to live lens fetches.

**Source:** `database.py:1234–1270`

> **`update_advisor_observation_raw_response` REMOVED (DE-PRISM-SOURCES-001 v1 rejection):** A v1 UPDATE accessor was drafted but rejected because `advisor_observations` is append-only — no UPDATE path exists or is permitted. Callers that need to associate new data with an existing observation must insert a new row with a linking `run_id`.

> **`get_latest_market_prism_sources()` REMOVED:** The no-run_id variant had zero production callers after `app.py` was wired to the `_for_run` accessor. Deleted to avoid dead API surface.

---

### Advisor Wall (frozen-eval access guard)

#### `advisor_ro_query(sql: str, params: tuple = ()) → list`
The **sole** entry point from Advisor code to the state DB. Wraps the caller's SQL in a COALESCE guard that excludes `fold_role = 'frozen_eval'` and untagged rows. Rejects bare `fold_role !=` / `fold_role <>` / `OR 1=1` predicates with `ValueError` (writes a `WALL_BREACH` audit row first). Post-hoc tripwire raises `RuntimeError` if a `frozen_eval` row reaches the result set despite the wrap.

**Returns:** `list[sqlite3.Row]`

#### `query_wall_breach_tripwire() → list`
Returns `researcher_dof_ledger` rows where `touched_frozen_eval = 1` AND `created_at > spec_bundles.frozen_at`. Non-empty result = hard CI failure.

---

### Spec-Bundle Registry

#### `get_or_create_phase1_theory_bundle_id() → int`
Idempotent. Inserts the canonical Phase-1 all-THEORY spec bundle (gamma=2.0, utility_family=CRRA, wealth_argument=compounded_return) if absent, then returns its integer `id`. Process-local cache makes repeated calls sub-microsecond.

#### `insert_spec_bundle(*, bundle_hash, facets_json, horizon_bars=None, cvar_alpha=None, generator_family=None) → None`
Idempotent INSERT OR IGNORE. Backfills `id` from `rowid` for the just-inserted row.

#### `insert_spec_bundle_facet(*, bundle_hash, facet_name, facet_value, freeze_discipline, justification=None, calibration_evidence=None) → int`
Inserts one `spec_facets` row. Raises `ValueError` for unrecognized `freeze_discipline`.

#### `get_spec_bundle(bundle_hash: str) → dict | None`
Returns the `spec_bundles` row as a dict, or `None`.

#### `get_spec_bundle_by_id(spec_bundle_id: int) → dict | None`
Returns the `spec_bundles` row by integer `id`, or `None`.

#### `get_spec_facets_for_bundle(bundle_hash: str) → list[dict]`
Returns all `spec_facets` rows for the given hash, ordered by `id`.

#### `canonicalize_facets_json(facets: dict) → str`
Deterministic JSON: sorted keys, compact separators. Used to compute the `bundle_hash`.

#### `hash_facets_json(canonical_json: str) → str`
Returns the hex-encoded SHA-256 digest of `canonical_json.encode("utf-8")`.

---

### Researcher DOF Ledger

Append-only degrees-of-freedom ledger for the NN1 multiple-testing haircut.

#### `insert_dof_ledger_row(*, facet_name, facet_category, decision_type, evidence_source, n_configs_searched=1, touched_frozen_eval=0, spec_bundle_id=None, justification=None) → int`
Appends one `researcher_dof_ledger` row. Raises `ValueError` for invalid enum values. Returns new row id.

#### `get_dof_ledger_for_bundle(spec_bundle_id: str) → list[dict]`
Returns all ledger rows for the given spec bundle, ordered by `id`.

#### `count_dof_backtest_selections(spec_bundle_id: str | None = None) → int`
Returns `S = SUM(n_configs_searched)` for `BACKTEST_SELECTION` rows.

#### `get_researcher_dof_ledger_for_run(run_timestamp, winning_spec_bundle_id=None) → list[dict]`
Returns `BACKTEST_SELECTION` ledger rows, excluding frozen-eval-tainted rows and the winning bundle.

---

### CVaR Diagnostics

#### `record_cvar_diagnostic(cycle_id, symphony_id, cvar_5pct, cvar_5pct_stderr, cvar_n_tail, cvar_5pct_long, cvar_n_tail_long, *, mode) → None`
Writes one `cvar_diagnostics` telemetry row. `mode` is required keyword-only (`"live"` or `"replay"`).

#### `read_cvar_diagnostic_for_symphony(symphony_id: str) → dict | None`
Returns the most-recent `cvar_diagnostics` row for the symphony, or `None`.

---

### Composition Hash

#### `compute_composition_hash(symphony_ids: list[str]) → str`
Returns a 16-character hex SHA-256 digest of the sorted symphony-id list. Order-independent.

---

### LLM Suggestions Audit Trail

#### `record_llm_suggestion(*, session_id, created_at, symphony_name, operator_identity, prompt_inputs, model_id, generation_settings, raw_response, validation_results, param_name, operator_decision, decision_at=None, operator_note=None, before_value=None, after_value=None, oos_revalidation=None) → int`
Inserts one immutable `llm_suggestions` audit row. Returns the new row id.

#### `get_suggestions_for_symphony(symphony_name: str) → list[dict]`
Returns all `llm_suggestions` rows for the symphony, oldest-first.

---

### Fleet Alert State

#### `read_fleet_alert() → dict | None`
Returns the `fleet_alert_state` singleton row (id=1) as a dict.

#### `write_fleet_alert(payload: dict) → None`
Upserts the singleton fleet-alert row.

#### `clear_fleet_alert() → None`
Deletes the singleton row. Idempotent.

---

### Exit Trigger Telemetry

#### `record_exit_trigger(*, symphony_id, account_id=None, triggered_reason, at_return=None, gate_state=None, gate_state_json=None, cycle_id=None, ts_utc=None, ts_et=None, math_mode=None, also_true_json=None, regime_match_pct=None, regime_suppressed=None, regime_label=None, ...) → None`
Writes one `exit_triggers` telemetry row. Opens its own connection; swallows exceptions so a telemetry failure never fails the cycle. Key migration additions:
- `also_true_json` (migration 029) — co-fired exit reasons, promoted to a dedicated column for SQL queryability
- `regime_match_pct`, `regime_suppressed`, `regime_label` (migration 026) — MC regime-match telemetry

#### `get_recent_exit_triggers(limit: int = 50) → list[dict]`
Returns the `limit` most-recent `exit_triggers` rows across all symphonies.

---

### Shadow History

#### `record_shadow_observation(*, symphony_id, account_id, cycle_id, ts_utc, ts_et, trading_day, current_return, shadow_return, is_post_trigger, trigger_id, position_epoch=None) → None`
Writes one `shadow_history` telemetry row. Swallows exceptions.

---

### Port State (SITE-D1 KEEP-DISPLAY)

#### `read_port_state(account_id: str) → dict | None`
Returns the `port_state` row for the account as a dict, or `None`. Display-only; no decision math after Sprint 3.

#### `write_port_state(account_id: str, state_dict: dict) → None`
Upserts the `port_state` row for the account.

---

### Prism Audit Log (migration 032)

Append-only deliberation trail for the Market Prism nightly pipeline. No UPDATE or DELETE accessor exists — entries are immutable. All writes use parameterized queries (`?` placeholders); user-supplied content is never interpolated into SQL.

**Table schema (`prism_audit_log`):**
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Row identifier |
| `run_id` | TEXT | NOT NULL | Nightly run identifier; joins to `MARKET_PRISM` observation's `raw_response.run_id` |
| `agent_role` | TEXT | NOT NULL | The agent that produced this entry (e.g. `"technicals_analyst"`, `"synthesizer"`) |
| `phase` | TEXT | NOT NULL | Deliberation phase (e.g. `"initial_read"`, `"synthesis"`) |
| `content` | TEXT | NOT NULL | The agent's verbatim output for that phase |
| `created_at` | TEXT | NOT NULL DEFAULT datetime('now') | UTC timestamp auto-populated on insert |

**Index:** `idx_prism_audit_log_run_id ON prism_audit_log (run_id)` — accelerates `get_prism_audit_for_run`.

#### `insert_prism_audit_entry(run_id: str, agent_role: str, phase: str, content: str) → int`

Insert one `prism_audit_log` row and return the new row id.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `run_id` | `str` | Nightly run identifier linking all entries of one pipeline run to the corresponding `MARKET_PRISM` advisor_observation |
| `agent_role` | `str` | The agent that produced this entry (e.g. `"technicals_analyst"`, `"synthesizer"`) |
| `phase` | `str` | The deliberation phase (e.g. `"initial_read"`, `"synthesis"`) |
| `content` | `str` | The agent's verbatim output for that phase |

**Returns:** `int` — the SQLite rowid of the newly inserted row (always > 0).

**Example:**
```python
row_id = database.insert_prism_audit_entry(
    run_id="2026-06-13T03:00:00+00:00",
    agent_role="technicals_analyst",
    phase="initial_read",
    content="Volatility is elevated; RSI at 72.",
)
```

#### `get_prism_audit_for_run(run_id: str) → list[dict]`

Return all `prism_audit_log` entries for a run, ordered by `id` ascending (insertion / chronological order).

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `run_id` | `str` | The nightly run identifier to query |

**Returns:** `list[dict]` — each dict has keys `id`, `run_id`, `agent_role`, `phase`, `content`, `created_at`. Returns `[]` when no rows match — never raises for an unknown `run_id`. Uses `get_ro_connection()` per architecture constraint 5 (read paths structurally isolated from the write path).

**Example:**
```python
entries = database.get_prism_audit_for_run("2026-06-13T03:00:00+00:00")
for entry in entries:
    print(entry["agent_role"], entry["phase"], entry["content"][:80])
```

## Types

### Module-level Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `DB_FILE` | env `DB_PATH` or `"alphabot_state.db"` | Active state DB path |
| `DEFAULT_STRATEGY` | dict | Default per-symphony strategy parameters |
| `DEFAULT_LOCKED_VARS` | `["TRIGGER_THRESHOLD_PCT"]` | Default locked strategy variables |

## Test Infrastructure

`tests/conftest.py` provides two layers of DB isolation that work with the sentinel guard in `_db_file()`:

1. **`pytest_configure()` hook** — fires before any module import (before collection). Sets `DB_PATH` to a `tempfile.TemporaryDirectory` session path if not already set. Ensures that when `database.py` is imported (triggering `init_db()` at module level), `_db_file()` resolves to the temp path, not `alphabot_state.db`.

2. **`_isolate_db` autouse fixture** — per-test. Uses `monkeypatch.setenv("DB_PATH", str(tmp_path / "test_alphabot_state.db"))` and calls `init_db()` so each test gets a fresh, fully-migrated schema. Monkeypatch restores the original env after each test.

The `_session_db_guard` autouse session fixture documents and asserts the session guard contract.

## Internal Dependencies

- `hashlib`, `json`, `sqlite3`, `uuid`, `sys`, `os` — stdlib
- No imports from other Planet Stopper modules (dependency-free base layer)
