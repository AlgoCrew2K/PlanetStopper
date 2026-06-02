# database

> SQLite state management for Planet Stopper: schema, migrations, and all read/write accessors for the state DB.

**Source:** `database.py`
**Last updated:** 2026-05-27

## Overview

`database.py` is the single write layer for `alphabot_state.db`. It owns schema initialization, 30 numbered migration SQL files (001–030), and every public accessor function. `_MIGRATION_FILES` wires 27 active entries (004–030); migrations 001–003 use a separate bootstrap path. The dashboard uses `get_ro_connection()` for all reads; the engine uses `get_connection()` for writes. The two-DB pattern (state DB here; Optuna studies in a separate DB) is an architecture hard rule — no cross-DB joins in application code.

WAL journal mode is enabled at `init_db()` time, allowing concurrent Flask reads while the engine holds a write lock.

## Schema Migrations

Migrations are listed in `_MIGRATION_FILES` and applied by `run_migrations()`. They are idempotent (tracked in `schema_migrations`). Current highest: **030** (`030_per_symphony_live_mode.sql`).

Notable ordering: 021 is listed before 020 — intentional. See `ARCH-002` inline comment; reordering would corrupt live DBs.

## Public API Reference

### Connection Helpers

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

#### `get_symphony_live_mode(symphony_name: str) → bool`
Returns the per-symphony live-mode flag. `False` (dry-run) is the safe default when no row exists. Added in migration 030.

#### `set_symphony_live_mode(symphony_name: str, live: bool) → None`
Sets the per-symphony live-mode flag. Called by `POST /api/symphony-settings/<name>` (CSRF-protected). `live=True` means the symphony participates in live order execution; `False` is dry-run.

---

### Regime Cache (migration 026)

#### `save_regime_label(symphony_name: str, label: str, date_str: str) → None`
Persists the regime classifier label for a symphony on a given date. Added in migration 026.

#### `get_cached_regime_label(symphony_name: str, date_str: str) → str | None`
Returns the cached regime label for a symphony/date pair, or `None` if not yet computed.

---

### Phase-1.5 M3 Bundle Registry

#### `get_or_create_phase15_m3_bundle_id() → int`
Idempotent. Inserts the Phase-1.5 M3 spec bundle if absent and returns its integer `id`. Analogous to `get_or_create_phase1_theory_bundle_id` for the M3 regime-exit spec. Added in migration 027.

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
| `pbo` | `float \| None` | Probability of backtest overfitting from CSCV gate (Phase-3) |

**Returns:** `int` — the new row id.

#### `get_latest_autotune_run(symphony_id: str, account_id=None, math_mode="per_symphony") → dict | None`
Returns the most-recent `autotune_runs` row for the symphony as a dict, or `None` if Optuna has not run.

#### `get_all_autotune_runs(limit: int = 50) → list[dict]`
Returns the `limit` most-recent rows across all symphonies (dashboard `/api/autotune-runs`).

#### `record_autotune_run(...) → None`
Deprecated port-mode variant. Writes `math_mode`, `account_id`, `sortino_sentinel_pct` columns. Use `save_autotune_run` for new Phase-1 code.

---

### Advisor Observations

Append-only audit trail. No UPDATE or DELETE accessor — rows are immutable.

#### `insert_advisor_observation(*, advisor_role, subject_type, subject_id, verdict=None, raw_response=None, spec_bundle_id=None, symphony_id=None, **kwargs) → int`
Inserts one `advisor_observations` row. Returns the new row id. `is_advisory_only` is always written as `1`. `symphony_id` is the denormalized symphony name added by migration 025 (S3-AUDIT-004) so the `/api/advisor-observations?symphony_id=` filter works without fan-out queries.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `advisor_role` | `str` | `"OVERFITTING_CONSCIENCE"`, `"SPEC_CRITIC"`, `"DIVERGENCE_EXPLAINER"`, or `"WALL_BREACH"` |
| `subject_type` | `str` | `"autotune_run"`, `"spec_bundle"`, or `"fold_role_wall"` |
| `subject_id` | `str` | String PK of the observed entity |
| `verdict` | `str \| None` | `"CLEAR"`, `"WATCH"`, `"BREACH"`, `"INFORMATIONAL"`, or `"NOT_APPLICABLE"` |
| `raw_response` | `dict \| str \| None` | Serialized to JSON; `None` → `"{}"` |
| `symphony_id` | `str \| None` | Denormalized symphony name (migration 025) |

**Returns:** `int` — new row id.

#### `get_advisor_observations_for_symphony(symphony_id: str) → list[dict]`
Returns all `advisor_observations` rows whose `symphony_id` column matches, oldest-first. Uses `get_ro_connection()`. Added in Sprint 3 (S3-AUDIT-004 + S3-AUDIT-010).

#### `get_advisor_observations_for_subject(subject_type: str, subject_id: str) → list[dict]`
Returns all rows for a given subject, oldest-first. Uses `get_ro_connection()`.

#### `get_advisor_observations_for_role(advisor_role: str, limit: int = 50) → list[dict]`
Returns rows for a given advisor role, newest-first. Uses `get_ro_connection()`.

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
Idempotent. Inserts the canonical Phase-1 all-THEORY spec bundle (gamma=2.0, utility_family=CRRA, wealth_argument=compounded_return) if absent, then returns its integer `id`. Process-local cache makes repeated calls sub-microsecond. Used by run-autotuner call sites to satisfy the NN1 Phase-1 spec_bundle_id requirement.

#### `insert_spec_bundle(*, bundle_hash, facets_json, horizon_bars=None, cvar_alpha=None, generator_family=None) → None`
Idempotent INSERT OR IGNORE. Backfills `id` from `rowid` for the just-inserted row.

#### `insert_spec_bundle_facet(*, bundle_hash, facet_name, facet_value, freeze_discipline, justification=None, calibration_evidence=None) → int`
Inserts one `spec_facets` row. Raises `ValueError` for unrecognized `freeze_discipline`. INSERT OR IGNORE on the `(bundle_hash, facet_name)` UNIQUE constraint (migration 024).

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
Returns all ledger rows for the given spec bundle, ordered by `id`. Uses read-only connection.

#### `count_dof_backtest_selections(spec_bundle_id: str | None = None) → int`
Returns `S = SUM(n_configs_searched)` for `BACKTEST_SELECTION` rows. When `spec_bundle_id` is None, sums across all bundles.

#### `get_researcher_dof_ledger_for_run(run_timestamp, winning_spec_bundle_id=None) → list[dict]`
Returns `BACKTEST_SELECTION` ledger rows, excluding frozen-eval-tainted rows and the winning bundle (plan D4).

---

### CVaR Diagnostics

#### `record_cvar_diagnostic(cycle_id, symphony_id, cvar_5pct, cvar_5pct_stderr, cvar_n_tail, cvar_5pct_long, cvar_n_tail_long, *, mode) → None`
Writes one `cvar_diagnostics` telemetry row. `mode` is required keyword-only (`"live"` or `"replay"`). `cvar_n_tail` is coerced from `None` to `0` (NOT NULL constraint). `cvar_n_tail_long` may be `NULL` (no long window in Phase 1).

#### `read_cvar_diagnostic_for_cycle(cycle_id: str, symphony_id: str) → dict | None`
Returns the most-recent `cvar_diagnostics` row for the (cycle_id, symphony_id) pair, or `None`.

#### `read_cvar_diagnostic_for_symphony(symphony_id: str) → dict | None`
Returns the most-recent `cvar_diagnostics` row for the symphony across all cycles, ordered by `ts_utc DESC`. Uses `get_ro_connection()`.

---

### Composition Hash

#### `compute_composition_hash(symphony_ids: list[str]) → str`
Returns a 16-character hex SHA-256 digest of the sorted symphony-id list. Order-independent. Used to detect composition changes without deep object comparison.

---

### LLM Suggestions Audit Trail

#### `record_llm_suggestion(*, session_id, created_at, symphony_name, operator_identity, prompt_inputs, model_id, generation_settings, raw_response, validation_results, param_name, operator_decision, decision_at=None, operator_note=None, before_value=None, after_value=None, oos_revalidation=None) → int`
Inserts one immutable `llm_suggestions` audit row. Returns the new row id. Dict-typed params are JSON-serialized.

#### `get_suggestions_for_symphony(symphony_name: str) → list[dict]`
Returns all `llm_suggestions` rows for the symphony, oldest-first. JSON blobs deserialized.

#### `get_suggestions_for_session(session_id: str) → list[dict]`
Returns all `llm_suggestions` rows for the session, oldest-first. JSON blobs deserialized.

---

### Fleet Alert State

#### `read_fleet_alert() → dict | None`
Returns the `fleet_alert_state` singleton row (id=1) as a dict. `tripped_symphonies` is deserialized from JSON.

#### `write_fleet_alert(payload: dict) → None`
Upserts the singleton fleet-alert row. `tripped_symphonies` is a list stored as JSON.

#### `clear_fleet_alert() → None`
Deletes the singleton row. Idempotent.

---

### Exit Trigger Telemetry

#### `record_exit_trigger(*, symphony_id, account_id=None, triggered_reason, at_return=None, gate_state=None, gate_state_json=None, cycle_id=None, ts_utc=None, ts_et=None, math_mode=None, port_trigger_id=None) → None`
Writes one `exit_triggers` telemetry row. Opens its own connection; swallows exceptions so a telemetry failure never fails the cycle.

#### `get_recent_exit_triggers(limit: int = 50) → list[dict]`
Returns the `limit` most-recent `exit_triggers` rows across all symphonies.

---

### Shadow History

#### `record_shadow_observation(*, symphony_id, account_id, cycle_id, ts_utc, ts_et, trading_day, current_return, shadow_return, is_post_trigger, trigger_id, position_epoch=None) → None`
Writes one `shadow_history` telemetry row. Swallows exceptions. Invalidates the in-memory `_shadow_cr_cache` for the affected symphony.

---

### Port State (SITE-D1 KEEP-DISPLAY)

#### `read_port_state(account_id: str) → dict | None`
Returns the `port_state` row for the account as a dict, or `None`. Display-only; no decision math routed through port-level state after Sprint 3.

#### `write_port_state(account_id: str, state_dict: dict) → None`
Upserts the `port_state` row for the account. Read-modify-write on existing row; stamps `updated_at`.

## Types

### Module-level Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `DB_FILE` | env `DB_PATH` or `"alphabot_state.db"` | Active state DB path |
| `DEFAULT_STRATEGY` | dict | Default per-symphony strategy parameters |
| `DEFAULT_LOCKED_VARS` | `["TRIGGER_THRESHOLD_PCT"]` | Default locked strategy variables |
| `PHASE1_THEORY_GAMMA` | `"2.0"` | Canonical Phase-1 CRRA gamma (frozen) |
| `PHASE1_THEORY_UTILITY_FAMILY` | `"CRRA"` | Canonical Phase-1 utility family (frozen) |
| `PHASE1_THEORY_WEALTH_ARGUMENT_FORMULA` | `"compounded_return"` | Canonical Phase-1 wealth-argument (frozen) |

## Internal Dependencies

- `hashlib`, `json`, `sqlite3`, `uuid` — stdlib
- No imports from other Planet Stopper modules (dependency-free base layer)
