# database

> SQLite state management for Planet Stopper: schema, migrations, all read/write accessors for the state DB, and a pytest sentinel guard that structurally prevents tests from writing to the production DB.

**Source:** `database.py`
**Last updated:** 2026-07-25 (strategy-incubation-gate, `DE-INCUBATION-GATE-001` -- new migration 037 `strategy_incubation`/`incubation_daily` tables + 7 new accessors, see the "Strategy Incubation Ledger" section below. Prior: 2026-07-24 (exit-friction-realized-savings, `DE-EXIT-FRICTION-REALIZED-001` -- new `get_exit_turnover_stats`/`compute_est_annual_friction_drag_pct` accessors in the Exit Trigger Telemetry section below; no schema migration, `exit_triggers` already existed. Prior: 2026-07-14 (branch-integration merge — Frontrunner Builder migration renumbered 033→**034** `frontrunner_proposals` on top of main's migration 033 `candidate_alert_state`; combines: Frontrunner Builder `frontrunner_proposals` table + accessors + `_VALID_DOF_EVIDENCE_SOURCES` addition; candidate-alert cycle migration 033 `candidate_alert_state` — five new accessors, `get_candidate_alert_viewed_marker`/`set_candidate_alert_viewed_marker`/`mark_candidate_alert_viewed`/`get_candidate_alert_new_valid_count`/`get_candidate_alert_last_run`, back the header candidate-alert indicator; prior: Workstream E, advisor-rewire cycle: `save_autotune_run` gains `s_count`; prior: 2026-07-09 DE-PROD-ACCURACY-001: `save_state` sanitizes numpy/non-finite values via `_sanitize_state_for_json` before every write, new `load_latest_shadow_row`/`load_earliest_shadow_row` Stage-1 accessors, `record_exit_trigger` now returns the inserted row id; prior: 2026-07-02 DE-PRISM-NUMERIC-VERIFY-001 `get_latest_market_prism_verification_for_run` accessor; prior: DE-ADVISOR-LATENCY `get_latest_market_lens_cache()`; DE-PRISM-SOURCES-001 `get_latest_market_prism_sources_for_run`)

## Overview

`database.py` is the single write layer for `alphabot_state.db`. It owns schema initialization, 37 numbered migration SQL files (001-037), and every public accessor function. `_MIGRATION_FILES` wires 34 active entries (004-037); migrations 001-003 use a separate bootstrap path. The dashboard uses `get_ro_connection()` for all reads; the engine uses `get_connection()` for writes. The two-DB pattern (state DB here; Optuna studies in a separate DB) is an architecture hard rule — no cross-DB joins in application code.

WAL journal mode is enabled at `init_db()` time, allowing concurrent Flask reads while the engine holds a write lock.

**Pytest sentinel guard (added 2026-06-10):** `_db_file()` raises `RuntimeError` when `"pytest" in sys.modules` AND the resolved path basename equals `alphabot_state.db`. This is gated on `sys.modules` so the live daemon (which never imports pytest) is completely unaffected. Tests must set `DB_PATH` to a temp file before triggering any DB access; `tests/conftest.py` does this via `pytest_configure()` (the earliest hook, before collection) and reinforces it with an autouse `_isolate_db` fixture per test.

## Schema Migrations

Migrations are listed in `_MIGRATION_FILES` and applied by `run_migrations()`. They are idempotent (tracked in `schema_migrations`). Current highest: **037** (`037_strategy_incubation.sql`), following **036** (`036_sleeve_rule_fires.sql`) and **035** (`035_sleeves.sql`, the Managed Sleeves epic, PR #94); see `docs/generated/sleeves.md` for the sleeves schema and the "Strategy Incubation Ledger" section below for 037.

Notable ordering: 021 is listed before 020 — intentional. See `ARCH-002` inline comment; reordering would corrupt live DBs.

Migrations 026–037:
- `026_mc_regime_match_telemetry.sql` — regime match columns on `exit_triggers`
- `027_regime_label_cache.sql` — `regime_label_cache` table
- `028_autotune_runs_pbo.sql` — `pbo` column on `autotune_runs`
- `029_exit_triggers_also_true.sql` — `also_true_json` column on `exit_triggers`
- `030_per_symphony_live_mode.sql` — `live_mode` on `symphony_strategies`, `config_audit_log` table
- `031_shadow_history_sym_ts_index.sql` — composite index on `shadow_history (symphony_id, ts_utc)`
- `032_prism_audit_log.sql` — `prism_audit_log` table + `idx_prism_audit_log_run_id` index (Prism Phase 1)
- `033_candidate_alert_state.sql` — `candidate_alert_state` table (single-row viewed-marker for the header candidate-alert indicator; see `feature-plans/candidate-alert.md`)
- `034_frontrunner_proposals.sql` — `frontrunner_proposals` table (Frontrunner Builder AC-8/9/10; renumbered 033→034 during branch integration to sit after main's migration 033 `candidate_alert_state`): a MUTABLE approval-status lifecycle table (`pending`/`approved`/`rejected`/`uploaded`), deliberately separate from the append-only `advisor_observations` (which has no update accessor, by design). Shared by both the Frontrunner Builder's own candidates and the `strategy_builder_engine.propose_strategies` retrofit (`proposal_source` column distinguishes `'frontrunner_builder'` from `'strategy_builder_retrofit'`). Accessors: `insert_frontrunner_proposal`, `update_frontrunner_proposal_status`, `get_frontrunner_proposal`, `get_frontrunner_proposals_for_symphony`, `get_pending_frontrunner_proposals`, `count_uploaded_frontrunner_proposals`. Additive, idempotent (`IF NOT EXISTS`), two indexes (`symphony_id`, `approval_status`).
- `035_sleeves.sql` / `036_sleeve_rule_fires.sql` — Managed Sleeves epic (PR #94); see `docs/generated/sleeves.md`.
- `037_strategy_incubation.sql` — `strategy_incubation` (per-candidate incubation-ledger status row) + `incubation_daily` (per-candidate-per-day forward-return observations, `UNIQUE(candidate_hash, trading_day)`) tables. Additive-only, no existing table modified. See the "Strategy Incubation Ledger" section below and `docs/generated/advisors_incubation.md`.

An earlier migration, `023_autotune_runs_s_count.sql`, added the `s_count` column to `autotune_runs` — but until the advisor-rewire cycle (2026-07-12, Workstream E) no caller ever populated it; see `save_autotune_run` below.

**DE-PRISM-NUMERIC-VERIFY-001 adds no migration.** `MARKET_PRISM_VERIFICATION` is a new `advisor_role` value on the existing `advisor_observations` table — same no-schema-change pattern as `MARKET_PRISM_SOURCES` and `MARKET_LENS_CACHE`.

**Frontrunner Builder DoF-ledger isolation (2026-07-11, no migration).** `_VALID_DOF_EVIDENCE_SOURCES` (the app-layer frozenset gating `insert_dof_ledger_row`'s `evidence_source` argument — no SQL CHECK constraint) gains an additive member, `"OVERLAY_BACKTEST_SELECTION"`, distinct from the autotuner's own `"BACKTEST_SELECTION"`. This is the real isolation mechanism keeping the Frontrunner Builder's search-breadth DoF-ledger rows out of the autotuner's N_effective overfitting haircut — every consumer that aggregates `researcher_dof_ledger` for N_effective (`count_dof_backtest_selections`, `get_researcher_dof_ledger_for_run`) filters on the literal string `evidence_source='BACKTEST_SELECTION'`, so a distinct value is excluded by construction. See `DE-FRONTRUNNER-001` in `DECISIONS.md` and `docs/generated/advisors_frontrunner_builder.md`.

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
Writes `state_dict` as JSON to `bot_state`. Calls `_sanitize_state_for_json(state_dict)` before `json.dumps` (DE-PROD-ACCURACY-001, Finding 1).

#### `_sanitize_state_for_json(value) → Any` (internal)
Recursively coerces a `bot_state` tree to JSON-safe native Python: `np.integer → int`, `np.bool_ → bool`, any float (numpy or plain) through the module's `_finite_or_none` idiom so `NaN`/`±inf` persist as `None` rather than crash the save or poison downstream money math. Unknown non-JSON types still raise `TypeError` — no silent serialization of anything the sanitizer doesn't explicitly recognize. Added after a 2026-07-09 production incident: a numpy `int64` on a Take-Profit path crashed `save_state` with plain `json.dumps` on 3 consecutive cycles, each failed save losing that cycle's `triggered=True` and causing the next cycle to reload pre-trigger state and re-fire the same exit (4 duplicate `exit_triggers` rows in one morning; up to 4 duplicate sell submissions in `LIVE_EXECUTION`). Implemented as a recursive walk rather than a `json.dumps(default=...)` hook because `default=` is never invoked for a plain `float('nan')` — it serializes "successfully" as a poison token and never reaches the hook.

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
| `s_count` | `int \| None` | **Wired 2026-07-12 (Workstream E, AC-E1/E2).** SUM of `n_configs_searched` over `BACKTEST_SELECTION` rows in `researcher_dof_ledger` for this run's `spec_bundle_id` — distinct from `d_spec` (which is `COUNT DISTINCT` bundles). Migration `023_autotune_runs_s_count.sql` added the column, but no caller populated it until this cycle — every row's `s_count` was `NULL` forever, so a later run's `prior_runs` query always saw `None` and `overfitting_conscience`'s Indicator-3 (operator drift) could structurally never fire on live data. Uses `is not None`, not a truthiness check, so `s_count=0` (the honest NN1-compliant no-BACKTEST_SELECTION-evidence case) persists as literal `0`, never coerced to `NULL` — callers must pass `s_count=0` explicitly, not omit the kwarg, to record that case. Default `None` (legacy pre-023 rows / not-yet-wired callers). |

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
| `advisor_role` | `str` | `"OVERFITTING_CONSCIENCE"`, `"SPEC_CRITIC"`, `"DIVERGENCE_EXPLAINER"`, `"WALL_BREACH"`, `"MARKET_PRISM"`, `"MARKET_PRISM_SOURCES"`, `"MARKET_LENS_CACHE"`, `"MARKET_PRISM_VERIFICATION"`, `"ASSET_SWAP"`, or `"LOGIC_CHANGE"` (the latter two producers pre-date this list but gained their first production caller and dashboard surfacing in the advisor-rewire cycle, 2026-07-12 — see `app.py`'s `_ADVISOR_ROLES`) |
| `subject_type` | `str` | `"autotune_run"`, `"spec_bundle"`, `"fold_role_wall"`, or `"portfolio"` |
| `subject_id` | `str` | String PK of the observed entity |
| `verdict` | `str \| None` | `"CLEAR"`, `"WATCH"`, `"BREACH"`, `"INFORMATIONAL"`, `"NOT_APPLICABLE"`, `"neutral"`, `"bullish"`, `"bearish"`, `"limited-inputs"`, `"ADOPT_CANDIDATE"`, `"KEEP_INCUMBENT"`, or `"REJECT_VETO_FAILED"` (the last three are `acceptance_gate.py`'s decision strings, written by the weekly ASSET_SWAP/LOGIC_CHANGE/STRATEGY_BUILDER producers and read by the Candidate Alert accessors below) |
| `raw_response` | `dict \| str \| None` | Serialized to JSON; `None` → `"{}"` |
| `symphony_id` | `str \| None` | Denormalized symphony name (migration 025) |

**Returns:** `int` — new row id.

#### `get_advisor_observations_for_symphony(symphony_id: str) → list[dict]`
Returns all `advisor_observations` rows whose `symphony_id` column matches, oldest-first. Uses `get_ro_connection()`. **Takes only `symphony_id`** — a caller passing `advisor_role=`/`limit=` kwargs raises `TypeError` (this was the AC-A1 dedup bug in `advisors/strategy_builder_scheduler.py` — see `docs/generated/advisors_strategy_builder_scheduler.md`).

#### `get_advisor_observations_for_subject(subject_type: str, subject_id: str) → list[dict]`
Returns all rows for a given subject, oldest-first.

#### `get_advisor_observations_for_role(advisor_role: str, limit: int = 50) → list[dict]`
Returns rows for a given advisor role, newest-first.

#### `get_latest_market_prism_summary() → dict | None`
Returns the most recently inserted `advisor_observations` row with `advisor_role="MARKET_PRISM"`, deserialized (including `raw_response` as a dict), or `None` when no row exists. Used by the Cycle-5 Overview tab to render the always-on Market Prism block.

#### `get_latest_market_prism_sources_for_run(run_id: str) → dict | None`
Returns the MARKET_PRISM_SOURCES row for this `run_id`, or `None`. Uses an exact `json_extract(raw_response,'$.run_id') = ?` SQLite match — no scan-and-compare loop. Returns `None` on mismatch — **no fallback to a different run's citations** (stale-citation-bleed guard). D-1 never-raises; uses `get_ro_connection()`. Used by `app.py:ai_advisor_tab()` to ensure the citation overlay matches the currently-displayed MARKET_PRISM row. Each MARKET_PRISM_SOURCES row holds rebuilt-at-patch-time citation metadata in `raw_response.per_lens_digest[lens].article_corpus = [{url, title, published}]`.

#### `get_latest_market_prism_verification_for_run(run_id: str) → dict | None`

**New (DE-PRISM-NUMERIC-VERIFY-001, AC-9).** Structural mirror of `get_latest_market_prism_sources_for_run` — same exact `json_extract(raw_response,'$.run_id') = ?` match, same `ORDER BY id DESC LIMIT 1`, same no-stale-bleed guard (returns `None` on mismatch — a run where the verifier found no `cited_numbers`, or errored, produces no VERIFICATION row for that run_id; falling back to a different run's row would show last night's checks against tonight's read), same D-1 / `get_ro_connection()` discipline.

Returns the `MARKET_PRISM_VERIFICATION` `advisor_observations` row for this `run_id`, or `None`. Used by:
- `advisors/prism_numeric_verifier.persist_verification()` — the idempotency check (a row already existing for `run_id` skips the INSERT, AC-8).
- `app.py:ai_advisor_tab()` — the AC-10 Overview render overlay (fetches by the `MARKET_PRISM` row's own `run_id` to attach per-check verification badges).

D-1 never-raises: any exception (DB error, parse failure) degrades to `None`. See [advisors/prism_numeric_verifier](advisors_prism_numeric_verifier.md) for the row's `raw_response` shape (`{run_id, verified_at, checks, summary, verdict}`).

`"MARKET_PRISM_VERIFICATION"` is **NOT** added to `app.py`'s `_ADVISOR_ROLES` — keeps it out of the Overview `observations` loop and the `_preview_text` stamp, exactly like `MARKET_PRISM_SOURCES` and `MARKET_LENS_CACHE`.

#### `get_latest_market_lens_cache() → dict | None`

Returns the most recent `MARKET_LENS_CACHE` advisor_observations row as a fully-parsed dict (with `raw_response` deserialized from JSON), or `None`.

Used by `ai_advisor.assemble_advisor_context` to serve the 5 market-wide lens blocks from the nightly cache instead of making 17–29 live external API calls per advisor click (DE-ADVISOR-LATENCY). **Also used (advisor-rewire cycle, 2026-07-12) by `advisors.weekly_suggestions_scheduler._fetch_lens_scores()`** to source the market-wide `lens_scores` dict passed into the weekly asset-swap loop's `suggest_swaps` calls — see `docs/generated/advisors_weekly_suggestions_scheduler.md`.

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

#### `record_exit_trigger(*, symphony_id, account_id=None, triggered_reason, at_return=None, gate_state=None, gate_state_json=None, cycle_id=None, ts_utc=None, ts_et=None, math_mode=None, also_true_json=None, regime_match_pct=None, regime_suppressed=None, regime_label=None, ...) → int | None`
Writes one `exit_triggers` telemetry row. Opens its own connection; swallows exceptions so a telemetry failure never fails the cycle — returns `None` on a swallowed failure. Key migration additions:
- `also_true_json` (migration 029) — co-fired exit reasons, promoted to a dedicated column for SQL queryability
- `regime_match_pct`, `regime_suppressed`, `regime_label` (migration 026) — MC regime-match telemetry

**Return value (DE-PROD-ACCURACY-001, Finding 10):** now returns the inserted row id (previously always returned `None`). The trigger-success site in `alpha_bot_execution.py` stashes the returned id as `bot_state[sym_id]["_last_trigger_id"]`, the write side of the read that already fed `record_shadow_observation`'s `trigger_id` parameter below — closing a gap where `_last_trigger_id` had exactly one reader and zero writers, so `shadow_history.trigger_id` could structurally never populate (0 of 25,218 rows linked as of the 2026-07-09 audit).

#### `get_recent_exit_triggers(limit: int = 50) → list[dict]`
Returns the `limit` most-recent `exit_triggers` rows across all symphonies.

#### `get_exit_turnover_stats(symphony_id: str, *, now_utc: datetime | None = None) → dict` (exit-friction-realized-savings, `DE-EXIT-FRICTION-REALIZED-001`, 2026-07-24)
Per-symphony exit-turnover stats from `exit_triggers`, keyed by window (`_TURNOVER_WINDOWS_DAYS = (30, 90, 365)`): `{window: {"exit_count": int, "coverage_days": int}}`.

**`coverage_days` honesty contract (RULING C, AC-8, credited to ga2-tw's pre-RED recon):** `min(window, actual_days)`, where `actual_days` is the day-span between `now_utc` and the EARLIEST `exit_triggers` row for this symphony (0 with zero rows). `exit_triggers` is pruned daily via `prune_old_triggers`/`TRIGGER_TELEMETRY_RETENTION_DAYS` (default 90, `app.py:788`, operator-configurable) — a bare `365`-day `exit_count` would silently imply a full year of coverage the table structurally cannot back once retention pruning (or a young symphony) has capped real history well below that. This is OPERATOR KNOB #2, alongside `SHADOW_HISTORY_RETENTION_DAYS` (default 180) which gates the Kaminski-Lo precondition sample size (`N_MIN_OBS=40`, see `docs/generated/guard_preconditions.md`) — both retention knobs belong on the operator's radar as one decision surface.

Exit-leg-only (documented limitation, not a bug) — `exit_triggers` only ever records exit events; re-entry is implicit in Composer's daily rebalance and not discretely logged (inferring round-trips from `bot_state["triggered"]` transitions is explicitly deferred, per the feature plan's Scope Boundaries).

Never raises — an empty table or any DB error degrades to `{"exit_count": 0, "coverage_days": 0}` for every window.

#### `compute_est_annual_friction_drag_pct(turnover_stats: dict, friction_pct: float) → float` (exit-friction-realized-savings, `DE-EXIT-FRICTION-REALIZED-001`, 2026-07-24)
Pure function, no DB access. Scales the 365-day window's `exit_count` up by `365 / coverage_days` (correcting for a retention-capped window under-counting a true year) and multiplies by `friction_pct` (callers pass `autotuner.SIM_EXIT_FRICTION_PCT` — this function stays decoupled from `autotuner`, no `autotuner`↔`database` import coupling). Returns `0.0` (never raises, never divides by zero) when `coverage_days` is 0.

---

### Shadow History

#### `record_shadow_observation(*, symphony_id, account_id, cycle_id, ts_utc, ts_et, trading_day, current_return, shadow_return, is_post_trigger, trigger_id, position_epoch=None) → None`
Writes one `shadow_history` telemetry row. Swallows exceptions.

#### `load_latest_shadow_row(symphony_id: str, trading_day: str, et_cutoff: str | None = None) → dict | None`

**New (DE-PROD-ACCURACY-001, Finding 2).** Returns the most-recent `shadow_history` row for a symphony+day, or `None`. When `et_cutoff` (an ET time-of-day string, `"HH:MM:SS"`) is given, only rows at/before that time qualify — used by Stage-1 post-mortem (`reporting.generate_eod_snapshot`) to hold its declared snapshot basis when it runs off-schedule (the engine ticks past close to ~16:04; a bare latest-row read would otherwise silently re-base a late run onto EOD values). Swallows exceptions, logs at ERROR, returns `None` on failure.

#### `load_earliest_shadow_row(symphony_id: str, trading_day: str) → dict | None`

**New (DE-PROD-ACCURACY-001, Finding 2 Revise-phase).** ASC twin of `load_latest_shadow_row` — returns the EARLIEST `shadow_history` row for a symphony+day, or `None`. Stage-1's degradation tier for a day whose shadow rows are ALL after the snapshot cutoff (daemon started after 15:55 ET): the earliest row of the day is nearest the declared basis, and real off-basis shadow data beats the action-phase-clobbered `bot_state` value. Swallows exceptions, logs at ERROR, returns `None` on failure. See [reporting](reporting.md) for the full three-tier `if_held_source` sourcing contract these two accessors feed.

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

---

### Candidate Alert (header indicator, migration 033)

Backs the always-visible header candidate-alert indicator (`templates/_chrome.html` + `static/chrome.js`) — see `feature-plans/candidate-alert.md` and `DE-CANDIDATE-ALERT-001` in `DECISIONS.md`. One single-row viewed-marker table plus four accessors that read the existing `advisor_observations` table; no new observation-writing path is added.

**Table schema (`candidate_alert_state`):**
| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PRIMARY KEY CHECK (id = 1) | Pins the table to exactly one row |
| `last_viewed_observation_id` | INTEGER | NOT NULL DEFAULT 0 | `advisor_observations.id` of the last row the operator has viewed. `0` = nothing viewed yet — never collides with a real row id (AUTOINCREMENT PK starts at 1) |
| `updated_at` | TEXT | NULLable | Set by `set_candidate_alert_viewed_marker` on every write |

**Verdict-classification note:** `acceptance_gate.py` defines exactly three decision strings — `DECISION_ADOPT_CANDIDATE` (`"ADOPT_CANDIDATE"`), `DECISION_KEEP_INCUMBENT`, `DECISION_REJECT_VETO_FAILED`. `KEEP_INCUMBENT` is the common "no benefit, nothing changed" outcome for ASSET_SWAP/LOGIC_CHANGE rows (persisted verbatim by `asset_swap_engine.py`/`logic_change_engine.py`) and must NOT count as a valid new candidate — so `ADOPT_CANDIDATE` (`_CANDIDATE_ALERT_SURVIVOR_VERDICT`) is the sole survivor condition below. This is stricter than the feature plan's original wording (`verdict != "REJECT_VETO_FAILED"`, which would have also counted `KEEP_INCUMBENT` as a survivor); see `DE-CANDIDATE-ALERT-001`.

**Role scope:** all five accessors below are scoped to `_CANDIDATE_ALERT_WEEKLY_ROLES = ("ASSET_SWAP", "LOGIC_CHANGE", "STRATEGY_BUILDER")` — the three weekly-suggestion producer roles. A row from any other `advisor_role` (`MARKET_PRISM`, `OVERFITTING_CONSCIENCE`, etc.) never influences the marker, the survivor count, or the last-run aggregate, even if its `verdict` string happens to coincidentally match `"ADOPT_CANDIDATE"`.

#### `get_candidate_alert_viewed_marker() → int`

Returns the current `last_viewed_observation_id`, or `0` when unset (fresh migration, nothing viewed yet — the same value structurally). Read-only (`get_ro_connection()`, architecture constraint 5). Never raises: any exception, including a DB that predates migration 033, degrades to `0`.

#### `set_candidate_alert_viewed_marker(observation_id: int) → int`

UPSERTs the marker via `INSERT ... ON CONFLICT(id) DO UPDATE SET last_viewed_observation_id = MAX(existing, excluded)`. Monotonic by construction — a call with a lower id (an out-of-order request, a stale client re-POSTing) can never regress a marker that has already advanced further (AC-5 idempotency).

**Returns:** `int` — the resulting stored value (not necessarily `observation_id`, if the existing marker was already higher).

#### `mark_candidate_alert_viewed() → int`

Advances the marker to `MAX(id)` over `advisor_observations` rows in `_CANDIDATE_ALERT_WEEKLY_ROLES`, then calls `set_candidate_alert_viewed_marker` with that value. **Zero required arguments — server-computed only:** the caller cannot supply an arbitrary observation id, so a malicious or buggy client cannot set the marker to a value the operator hasn't actually seen. Returns `0` (no-op write) when no weekly-suggestion row has ever been written. Backs `POST /api/candidate-alert/mark-viewed`.

#### `get_candidate_alert_new_valid_count() → int`

Counts `advisor_observations` rows in `_CANDIDATE_ALERT_WEEKLY_ROLES` where `verdict = 'ADOPT_CANDIDATE'` AND `id > <current viewed marker>` (strictly greater — a row exactly at the marker was already viewed). Read-only; never raises (degrades to `0` on any exception — a DB error, malformed data, or a row with an odd/absent verdict is fail-closed as "not counted," never mis-badged as a survivor). Backs `GET /api/candidate-alert`'s `new_valid_count` field.

#### `get_candidate_alert_last_run() → dict | None`

Returns the latest weekly-suggestion batch's status. No `run_id`/batch column exists for these three roles — the three weekly engines run back-to-back within one invocation of `advisors.weekly_suggestions_scheduler.run_weekly_suggestions()`, so the calendar date (UTC, via `substr(created_at, 1, 10)`) of the most recent row is used as the "one run" grouping key.

**Returns:** `{"ran_at": <max created_at that date>, "evaluated": <row count that date>, "survivors": <subset with verdict='ADOPT_CANDIDATE'>}`, or `None` only when the table has ZERO weekly-suggestion rows ever. `survivors: 0` is a valid, honest result — an all-rejected run still proves the weekly job is alive (AC-3, "know it's working" case); it is not treated as equivalent to "never ran." Read-only; never raises (degrades to `None` on any exception).

**Example:**
```python
last_run = database.get_candidate_alert_last_run()
if last_run is None:
    print("no weekly run yet")
else:
    print(f"{last_run['ran_at']}: {last_run['evaluated']} evaluated, {last_run['survivors']} survived")
```

### Strategy Incubation Ledger (migration 037)

Backs the Strategy Incubation Gate — see `docs/generated/advisors_incubation.md` and `DE-INCUBATION-GATE-001` in `DECISIONS.md`. Two additive tables; no existing schema modified.

**Table `strategy_incubation`** — one row per admitted candidate:

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | |
| `candidate_hash` | TEXT | NOT NULL UNIQUE | Tree-structural SHA-256 — MUST match `community_strats._composition_hash` exactly (NOT `compute_composition_hash`, which hashes `list[str]` symphony IDs for portfolio-set identity, an unrelated concept) |
| `tree_json` | TEXT | NOT NULL | The compiled Composer `raw_value` tree, JSON-serialized — server-side only, never exposed via `GET /api/incubation` |
| `objective` | TEXT | NOT NULL | Matches `strategy_builder_engine.Objective` enum value |
| `provenance` | TEXT | NOT NULL | `"built-new"` \| `"atlas-suggested"` — same provenance-tag rule as the rest of Strategy Builder (never `"T1"`-`"T7"`, never `"community"`) |
| `admitted_at` | TEXT | NOT NULL DEFAULT `datetime('now')` | Incubation clock anchor; never reset while `INCUBATING`/`PROMOTED`; reset on a refractory reentry |
| `backtest_mdd_pct` | REAL | | Gate-time backtest max drawdown, stored as a positive percentage magnitude (e.g. `8.0` = an 8% drawdown). Caller converts from quantstats' negative-fraction convention (`abs(x) * 100.0`) before insert — see `advisors_incubation.md` |
| `status` | TEXT | NOT NULL DEFAULT `'INCUBATING'` | `INCUBATING` \| `PROMOTED` \| `FAILED` \| `EXPIRED` |
| `status_reason` | TEXT | NULLable | Static token only, never `str(exc)` (C5 sanitized-error precedent). Known tokens: `"mdd_breach"`, `"fetch_failures_exhausted"`, `"composer_422_tree_invalid"`, `"forward_alpha_negative"`. NULL while `INCUBATING`/`PROMOTED` |
| `status_changed_at` | TEXT | NULLable | Timestamp of the last status transition; the refractory-window anchor. NULL until the first transition away from `INCUBATING` |
| `promoted_at` | TEXT | NULLable | Set only on the transition to `PROMOTED` |
| `fetch_failure_count` | INTEGER | NOT NULL DEFAULT 0 | Durable consecutive-Composer-fetch-failure counter for the daily tick (added same-cycle, `record_incubation_fetch_outcome` addendum below — cannot be derived from `incubation_daily` gaps, since a genuine fetch failure leaves zero rows there; an in-memory counter would reset on every `app.py` restart). The only sanctioned writer is `record_incubation_fetch_outcome` — no raw SQL against this column from `advisors/incubation.py` or elsewhere |

Index: `idx_strategy_incubation_status` on `status` (accelerates `get_incubating()`'s hot filter).

**Table `incubation_daily`** — one row per candidate per observed forward trading day:

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | |
| `candidate_hash` | TEXT | NOT NULL | Soft FK to `strategy_incubation.candidate_hash` (no `PRAGMA foreign_keys` anywhere in this codebase — documented, not DB-enforced, matching migrations 035/036's precedent) |
| `trading_day` | TEXT | NOT NULL | ISO date string, a genuine NYSE trading day (`market_calendar.is_trading_day`), never a weekday proxy |
| `forward_return_pct` | REAL | NOT NULL | Candidate's simple daily return for that day, pct scale (times 100 of the raw fraction `composer_backtest_client` returns) |
| `spy_return_pct` | REAL | NULLable | Shared SPY benchmark return for that same day, same pct scale; NULL when the shared SPY call failed that tick — candidate rows are still recorded, promotion evaluation defers |
| | | UNIQUE(`candidate_hash`, `trading_day`) | Idempotency guard: a tick re-run or a same-day double-fire never double-inserts — `append_incubation_day` relies on this via `INSERT OR IGNORE`. Promoted by review to a hard requirement: tick idempotency must be structural (DB-enforced), never just "the loop only looks at dates after the last known day" application logic |

**Layering note (module placement):** `database.py` never imports FROM `advisors.*`. Because `register_incubation_candidate`'s cap-and-refractory checks run inside this module, their two constants live here rather than in `advisors/incubation.py`:

```python
MAX_INCUBATING = 20  # cap on concurrently INCUBATING rows [PM-ASSUMED]
INCUBATION_REFRACTORY_DAYS = 90  # a FAILED/EXPIRED hash cannot be re-admitted for this many days [PM-ASSUMED]
```

The other three incubation constants (`INCUBATION_WINDOW_TRADING_DAYS`, `INCUBATION_MDD_BREACH_MULT`, `INCUBATION_MAX_FETCH_FAILURES`) are tick/promotion-only and live in `advisors/incubation.py` — see `docs/generated/advisors_incubation.md`.

#### `register_incubation_candidate(candidate_hash, tree_json, objective, provenance, backtest_mdd_pct) -> dict`

Admits a candidate, or no-ops, per the idempotency/refractory/cap contract. Write path (`get_connection()`), parameterized SQL.

- No existing row -> cap-checked INSERT. `reason=None` on success, `"cap_exceeded"` on refusal (`count(status='INCUBATING') >= MAX_INCUBATING`).
- Existing row `INCUBATING`/`PROMOTED` -> permanent no-op, clock never resets. `reason="already_tracked"`.
- Existing row `FAILED`/`EXPIRED`, still within `INCUBATION_REFRACTORY_DAYS` of `status_changed_at` -> no-op. `reason="refractory_window"`. `EXPIRED` is treated identically to `FAILED` for this window (a `ga3-tw` coordination decision — both represent "this hash did not make it").
- Existing row `FAILED`/`EXPIRED`, refractory window elapsed -> cap-checked UPDATE-in-place (`candidate_hash` is UNIQUE, so this is always an UPDATE, never a second INSERT) — a genuinely fresh incubation attempt with a reset clock (`admitted_at`, `status_reason`, `status_changed_at`, `promoted_at` all cleared; `tree_json`/`objective`/`provenance`/`backtest_mdd_pct` refreshed to the newly-proposed values). `reason="refractory_reentry"` on success, `"cap_exceeded"` on refusal.

**Returns:** `{"admitted": bool, "status": str | None, "reason": str | None}`.

#### `append_incubation_day(candidate_hash, trading_day, forward_return_pct, spy_return_pct) -> bool`

Records one candidate's forward return for one trading day via `INSERT OR IGNORE` against the `UNIQUE(candidate_hash, trading_day)` constraint — never a Python-side SELECT-then-INSERT check (that has a TOCTOU gap the DB constraint closes for free). Returns `True` if a new row was inserted, `False` if the (candidate_hash, trading_day) pair already existed. Write path, parameterized SQL.

#### `set_incubation_status(candidate_hash, status, status_reason=None) -> None`

Dumb write — updates `status`, `status_reason`, `status_changed_at=datetime('now')`, and `promoted_at=datetime('now')` iff `status == "PROMOTED"` (left untouched otherwise). Valid-transition logic lives in `advisors/incubation.py`, not here (same layering as `set_symphony_live_mode`). Write path.

#### `get_incubating() -> list[dict]`

All `strategy_incubation` rows with `status='INCUBATING'`, oldest `admitted_at` first. Read path (`get_ro_connection()`, architecture constraint 5).

#### `get_incubation_overview() -> list[dict]`

All rows regardless of status, each augmented with `days_observed` (a `COUNT(*)` over `incubation_daily` for that `candidate_hash` — pure SQL aggregation, not a re-run of promotion-decision logic; architecture constraint 5's "UI never reruns the engine" refers to the decision itself, which is already persisted in `status`). Read path. Never raises for an empty ledger — returns `[]`. Sole read path for `GET /api/incubation` and `ai_advisor_tab()`'s live-join badge stamping — see `docs/generated/app.md`.

#### `record_incubation_fetch_outcome(candidate_hash, ok) -> int`

Added same-cycle (2026-07-25), PM ruling — supersedes an earlier "no new accessor, raw SQL from `advisors/incubation.py`" first-pass decision. The only sanctioned way `advisors/incubation.py` touches `fetch_failure_count` — no raw SQL against that column from outside `database.py` (same layering principle as the constants split above: state-DB writes go through named accessors, callers never hand-roll SQL against them). `ok=False` (a fetch error, not a 422 — those go straight to `set_incubation_status(..., "EXPIRED", ...)` and never call this) increments the counter by 1; `ok=True` (a successful fetch, regardless of whether it yielded any new date keys) resets it to 0. Returns the resulting count after the write so the caller can compare it against `INCUBATION_MAX_FETCH_FAILURES` in the same call, no second round-trip query. Policy (the threshold comparison, what to do once the count is reached) stays in `advisors/incubation.py` — this accessor only stores and returns the count. Write path, parameterized SQL.

#### `get_incubation_daily_series(candidate_hash) -> tuple[list[float], list[float | None]]`

Added same-cycle (2026-07-25), a real schema gap found while implementing `run_incubation_tick()` — none of the accessors above return the actual `incubation_daily` values for a candidate; `get_incubation_overview()` only gives a `days_observed` count, and `evaluate_promotion` (see `advisors_incubation.md`) cannot run without the real per-day series. Returns `(forward_return_pct, spy_return_pct)` — two lists, ordered by `trading_day` ascending, index-aligned (index `i` in both lists is the same `trading_day`, since both columns come from the same row — no separate date-matching needed on this read path). `spy_return_pct` entries are `None` wherever that row's column is NULL (the SPY-missing degradation case) — never skipped/compacted, which would misalign the two lists. Shaped to drop directly into `evaluate_promotion`'s first two positional args: `evaluate_promotion(*database.get_incubation_daily_series(hash), backtest_mdd_pct, days_observed)`. Returns `([], [])` for a candidate with zero recorded days or an unknown hash — never raises. Read path (`get_ro_connection()`).

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
