# Feature: M1F — Real Shadow-Equity Series Instrumentation (v2 — panel-revised)
Status: ready
Created: 2026-05-15
Revised: 2026-05-16 (post panel-validation; all 6 BCs + 18 PAs applied; 3 operator decisions resolved)

## Summary

Instrument the AlphaBot engine to persist a real per-cycle shadow-equity time series. Today the M1 dashboard helpers return `dry_run == if_held` literally by construction (`analytics.py:438, 453, 414`) because `bot_state` has no shadow-equity series; `current_return` is computed from Composer's `last_percent_change` — the same source `if_held` reads. M1F closes that gap: the engine writes a `shadow_history` row per symphony per cycle; the M1 helpers consume it for genuinely-distinct `dry_run` values; `shadow_hwm` (currently underspecified) becomes meaningfully consumed; V1's calibration sweep gains a real backtest-vs-live divergence signal for post-selection validation; the EOD post-mortem can compare actual vs hypothetical engine value-add.

**v1 semantics**: **Model A** — `shadow_return` freezes at `triggered_at_return` post-trigger; the schema implicitly captures the Model C counterfactual via `current_return` (Composer live). quant-risk-researcher's dissent (Model C is the literature gold standard per Kaminski & Lo 2014, Han et al. 2016) is documented in the panel verdict §6 — accepted for v1 because the schema records both values; can be revisited when V1's validation methodology is designed.

**Companion DM workstream** (separate plan): Dashboard Market-Mode Rendering freezes dashboard visuals outside market hours, which sidesteps the post-trigger Composer-API ambiguity for M1F.7's `shadow_hwm` (no need for a runtime STALE_SHADOW_RETURN alert — the metric becomes "max Composer-reported value during the trading day," defensible under either Composer post-trigger behavior).

## Acceptance Criteria

### M1F.1 — Shadow-equity series schema + write path

- **AC-M1F.1.1**: New `migrations/008_shadow_history.sql` (BC-1 — verified next migration number; `_MIGRATION_FILES` in `database.py:503` currently lists 004-007). Migration adds `_MIGRATION_FILES` entry alongside the DDL file. Additive-first. Table:
  ```sql
  CREATE TABLE IF NOT EXISTS shadow_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    ts_et TEXT NOT NULL,                       -- hardcoded UTC-4 string per H1 pattern (PA-M1F-6; follow-up to zoneinfo across telemetry layer)
    trading_day TEXT NOT NULL,                 -- 'YYYY-MM-DD'
    symphony_id TEXT NOT NULL,
    account_id TEXT,                           -- NULLable (matches exit_triggers)
    cycle_id TEXT,                             -- format 'YYYYMMDD_HHMM' (PA-M1F-4); NULLable
    current_return REAL NOT NULL,              -- Composer's last_percent_change × 100; required at write time, no DEFAULT (PA-M1F-10)
    shadow_return REAL NOT NULL,               -- Model A: current_return pre-trigger; frozen at triggered_at_return post-trigger
    is_post_trigger INTEGER NOT NULL DEFAULT 0,
    trigger_id INTEGER                         -- ADVISORY soft reference to exit_triggers.id (BC-2 — NOT a FOREIGN KEY; SQLite FK enforcement off across this codebase)
  );
  CREATE INDEX IF NOT EXISTS idx_shadow_history_sym_day ON shadow_history (symphony_id, trading_day, ts_utc);
  CREATE INDEX IF NOT EXISTS idx_shadow_history_day ON shadow_history (trading_day, ts_utc);
  CREATE INDEX IF NOT EXISTS idx_shadow_history_ts_utc ON shadow_history (ts_utc);  -- BC-6 — for prune DELETE at ~1.1M-row scale
  ```
- **AC-M1F.1.2**: New `database.record_shadow_observation(symphony_id, account_id, cycle_id, ts_utc, ts_et, trading_day, current_return, shadow_return, is_post_trigger, trigger_id)` helper. Uses literal `sqlite3.connect(DB_FILE, timeout=10.0)` (matches H1's `record_exit_trigger`, not `get_connection()`). Try/except SWALLOWS failures with ERROR log; cycle MUST NOT fail on telemetry. The cycle's `save_state` transaction is NOT joined. Write must supply both `current_return` and `shadow_return` (NOT NULL columns, no DEFAULT — intentional per PA-M1F-10) or raise.
- **AC-M1F.1.3**: New `database.prune_old_shadow_history(retention_days)` — uses portable subquery pattern (PA-M1F-5): `DELETE FROM shadow_history WHERE id IN (SELECT id FROM shadow_history WHERE ts_utc < ? ORDER BY ts_utc LIMIT 1000)` looped until done. Avoids dependency on `SQLITE_ENABLE_UPDATE_DELETE_LIMIT`. `.env`: `SHADOW_HISTORY_RETENTION_DAYS=180` (rationale: 3× safety margin over Glasserman 2003's 60-120 day backtest-reconciliation window per PA-M1F-7; NOT a seasonal-pattern claim).
- **AC-M1F.1.4**: Rotation runs in the existing background scheduler callback in `app.py` (NOT in any Flask route handler — BC-4). Add `prune_old_shadow_history()` alongside the existing `prune_old_triggers()` call in that callback. Daily cadence inherited from the scheduler.

### M1F.2 — Shadow-return computation per cycle

- **AC-M1F.2.1**: In the data phase of `alpha_bot_execution.py`, for each symphony every cycle, compute `shadow_return` as follows:
  - If `bot_state[symphony_id]["triggered"]` is **False** for today (dict notation per PA-M1F-3): `shadow_return = current_return` (still holding; mirrors live).
  - If `bot_state[symphony_id]["triggered"]` is **True** for today: `shadow_return = bot_state[symphony_id]["triggered_at_return"]` (frozen at trigger time; Model A).
- **AC-M1F.2.2**: Call `database.record_shadow_observation(...)` after computing `shadow_return`. Non-blocking (own connection, swallow failures).
- **AC-M1F.2.3**: Reset semantics on new trading day — the day's first cycle records a baseline row; `triggered=True` from yesterday is wiped by the existing new-day reset, so today's `shadow_return = current_return` until a new trigger fires today.
- **AC-M1F.2.4**: Daemon-restart resilience — on startup, `resume_shadow_baselines()` reads the latest `shadow_history` row per symphony for the current `trading_day` to resume from the right baseline. **`resume_shadow_baselines()` runs AFTER `wipe_transient_state()` completes for the current cycle** (PA-M1F-2) so yesterday's rows don't overwrite today's wiped state.
- **AC-M1F.2.5**: **Composer fetch failure gate** (PA-M1F-15) — explicit A/C: shadow_observation write is gated on successful Composer data fetch for the symphony in that cycle. If fetch failed, no row is written for that symphony that cycle (consistent with the cycleat-fix preserve-prior-values pattern).

### M1F.3 — M1 helper consumer migration

- **AC-M1F.3.1**: `analytics.py:get_symphony_today_change` — `dry_run` reads the latest `shadow_history` row's `shadow_return` for the symphony's current `trading_day`. `if_held` continues to read `current_return` (Composer-reported). Genuinely distinct values.
- **AC-M1F.3.2**: `analytics.py:get_symphony_cumulative_return` — `dry_run` reads the cumulative `shadow_return` trajectory. **Specifics (PA-M1F-16)**:
  - **Day-row selection**: the LAST row per trading_day by `ts_utc` (EOD value) is the per-day shadow_return.
  - **Chain-link formula**: `cumulative_dry_run = (∏(1 + r_i/100)) - 1` applied to per-day EOD shadow_return values, expressed as percent.
  - **Missing-day handling**: if any historical trading day has no shadow_history rows, exclude that day from the chain; if the chain has fewer than 2 distinct days, `dry_run = None` (sentinel).
  - **Caching**: results cached per (symphony_id, trading_day); invalidated when a new shadow_history row is recorded for that day.
- **AC-M1F.3.3**: `analytics.py:get_symphony_max_drawdown` — `dry_run` reads peak-to-trough drawdown of the cumulative shadow trajectory. **Boundary (PA-M1F-16b)**: if fewer than 2 distinct trading days exist in shadow_history, `dry_run = None`.
- **AC-M1F.3.4**: Portfolio helpers (`get_portfolio_*`) automatically benefit — they value-weight per-symphony values; once per-symphony differs, portfolio aggregates differ.
- **AC-M1F.3.5**: When a symphony has zero rows in `shadow_history` (just-deployed, fresh DB), the helpers return `dry_run = None`. NO silent fall-back to `if_held` (avoids the cycleat-fix-class regression).
- **AC-M1F.3.6**: Portfolio aggregator excludes `None` contributors when value-weighting (consistent with existing cycleat-fix CR/MDD None-skip behavior).

### M1F.4 — Dashboard surfacing + column rename

- **AC-M1F.4.1**: Rename `<th>` element at **`templates/table_partial.html:62`** (PA-M1F-1 — flask-dashboard-specialist confirmed exact target line) from "If Held (Shadow)" → **"Held Return"** (operator-approved label). The column content (`shadow_str` block at lines 117, 122-129 — showing live return + Guard Alpha diff via `sym.current_return` / `sym.triggered_at_return`) is NOT changed; just the header label is updated to remove the misleading overload of "Held" + "Shadow."
- **AC-M1F.4.2**: Portfolio strip + per-symphony CR/TC/MDD cells now show **genuinely different** dry_run vs if_held values whenever the shadow trajectory diverges from live.
- **AC-M1F.4.3**: New **Shadow Performance** widget (PA-M1F-13):
  - Compact horizontal strip below the H1 triggers strip; established dashboard stack: fleet banner (V3, when shipped) → portfolio strip → triggers strip → **shadow performance strip** → symphony table.
  - Per-symphony pill badges showing the post-trigger divergence: `current_return - shadow_return WHERE is_post_trigger = 1`.
  - **Sign convention (rendered as legend in widget)**: green when `current_return < shadow_return` post-trigger (AlphaBot exited before price dropped — engine helped); red when `current_return > shadow_return` post-trigger (AlphaBot exited before price recovered — engine cost).
  - Max ~48px height single-row pill format to preserve viewport budget when multiple strips visible simultaneously.
  - Empty state: strip HIDDEN entirely (not "0 events today" placeholder). Null guard (PA-M1F-new): widget JS uses the same `v == null → '—'` pattern as cycleat-fix's `pctColor` null guard; renders '—' for symphonies with no shadow_history rows; never NaN% or 0.00%.
- **AC-M1F.4.4**: `/api/state` extension (PA-M1F-14): extend existing endpoint with a `shadow_divergence` key (Option A — no new endpoint). Structure: `{"by_symphony": {"<id>": {"today": float|null, "cumulative": float|null}}, "portfolio_today": float|null}`. Shadow_history read is one lightweight GROUP BY query per polling cycle; not on the execution path.

### M1F.5 — EOD post-mortem integration

- **AC-M1F.5.1**: `alpha_bot_execution.py` EOD post-mortem branch computes per-symphony `eod_divergence = current_return - shadow_return`. **Row selection (PA-M1F-5b)**: uses the LAST row for the day by `ts_utc DESC LIMIT 1`; explicitly documents that this may not be the market-close row if the engine was down for the session's final interval.
- **AC-M1F.5.2**: Portfolio-level EOD divergence (value-weighted) also recorded in `post_mortem_<date>.json`.
- **AC-M1F.5.3**: **Observational-only** (PA-M1F-9): divergence computation does NOT condition any live-order call (`place_order`, `submit_order`, `liquidate`, `cancel_order`). Plan must explicitly state this in the implementation docstring.
- **AC-M1F.5.4**: ERROR-level log + dashboard banner fires if EOD cannot compute divergence (e.g., no shadow_history rows for a symphony that traded today) — surfaces coverage gaps rather than silently emitting NaN.

### M1F.6 — V1 calibration consumer (validation interface)

- **AC-M1F.6.1**: **V1's role clarification** (BC-5): V1's Optuna sweep uses `synthetic_history` (Alpaca tick data) as its simulation input. `shadow_history` is V1's **post-selection validation layer** — used after the sweep to compare predicted divergence patterns against real-world live divergences. These are distinct roles. V1's optimization input is NOT shadow_history.
- **AC-M1F.6.2**: V1's primary validation query (the corrected example): `SELECT trading_day, AVG(current_return - shadow_return) AS avg_post_trigger_divergence FROM shadow_history WHERE symphony_id = ? AND is_post_trigger = 1 GROUP BY trading_day` — note the **`WHERE is_post_trigger = 1`** filter, without which the signal is diluted to noise by pre-trigger rows where divergence = 0 by construction (BC-5).
- **AC-M1F.6.3**: Smoke test exercises **THREE** query shapes (BC-5):
  1. Per-day post-trigger alpha-attribution: AC-M1F.6.2's query.
  2. Per-cycle intraday trajectory: `SELECT ts_utc, current_return, shadow_return, is_post_trigger, trigger_id FROM shadow_history WHERE symphony_id = ? AND trading_day = ? ORDER BY ts_utc ASC` — for identifying where intraday divergence opens.
  3. HWM reconstruction: `SELECT trading_day, MAX(current_return) AS shadow_hwm_counterfactual FROM shadow_history WHERE symphony_id = ? GROUP BY trading_day` — proves M1F.7's counterfactual HWM is recoverable from the schema.
- **AC-M1F.6.4**: **V1 bootstrap period gate (PA-M1F-11)**: shadow_history accumulates from M1F deploy-day. V1 runs within the first 125 trading days will have a sparse or empty frozen-eval fold against shadow_history. V1's report implements a three-state check: `sample_size < 30` → "indeterminate"; `≥30 and no divergence` → "provisional_no_overfit"; `≥30 and divergence detected` → "overfit_confirmed". (N≥30 per Bailey/de-Prado 2014's interpretability threshold.)
- **AC-M1F.6.5**: `math_mode` column on shadow_history (PA-M1F-8): `math_mode TEXT NOT NULL DEFAULT 'per_symphony'` — discriminator for the future port-level-math-mode workstream so V1's queries can filter by mode.

### M1F.7 — `shadow_hwm` consumption (closing I3)

- **AC-M1F.7.1**: **`shadow_hwm = max(current_return)` over the day's shadow_history rows** (BC-3 — corrected from the v1 plan's incorrect `max(shadow_return)`). Computed by: `SELECT MAX(current_return) FROM shadow_history WHERE symphony_id = ? AND trading_day = ?`. Preserves the existing dashboard field's semantic (counterfactual HWM per math-engine-audit.md:228, 233: "post-trigger peak tracker... shows what HWM 'would have been' if the engine had not exited").
  - **Composer-API behavior framing**: under either Composer post-trigger behavior (continues tracking OR freezes at trigger time), `max(current_return)` is a defensible "peak Composer-reported value during the trading day" metric. The DM workstream (Dashboard Market-Mode Rendering — separate plan) freezes dashboard visuals at market close, sidestepping any operator-confusion about post-close staleness. The Composer-API research (`docs/research/composer/last-percent-change-post-trigger-behavior.md`) concluded behavior is undocumented; the DM design makes empirical resolution unnecessary for v1.
- **AC-M1F.7.2**: **Source-of-truth split (PA-M1F-new2)**: `bot_state[symphony_id]["shadow_hwm"]` continues to be persisted as an in-memory write-through cache; the `shadow_history` table is the canonical source. On daemon restart, `resume_shadow_baselines()` reconciles the in-memory cache against the table. If they diverge, the table wins.
- **AC-M1F.7.3**: When M1F merges, I3's investigation workstream closes — verdict: shadow_hwm is now meaningfully consumed.

## Architecture

| Surface | Files touched |
|---------|---------------|
| Schema | `migrations/008_shadow_history.sql` (additive-first); `database.py:503` `_MIGRATION_FILES` list extended (BC-1) |
| Engine write path | `alpha_bot_execution.py` data phase — call `record_shadow_observation` per symphony per cycle (M1F.2) |
| Engine state helpers | `database.py` — `record_shadow_observation`, `prune_old_shadow_history`, `load_latest_shadow_row(symphony_id, trading_day)`, `resume_shadow_baselines` |
| Analytics consumer | `analytics.py` — `get_symphony_*` helpers query `shadow_history` for `dry_run` (M1F.3) |
| Dashboard | `templates/table_partial.html:62` (column rename), `templates/index.html` (new Shadow Performance widget), `app.py /api/state` extension (shadow_divergence key) |
| EOD post-mortem | `alpha_bot_execution.py` post-mortem branch — write divergence fields to `post_mortem_<date>.json` (M1F.5) |
| Scheduled task | `app.py` background scheduler callback — `prune_old_shadow_history()` added (BC-4 — explicitly NOT a Flask route handler) |
| `.env` | `SHADOW_HISTORY_RETENTION_DAYS=180` |
| Tests | `tests/shadow/test_shadow_history.py` + `tests/fixtures/shadow/*.json` (schema-derived; PA-18) |

**Team composition**: Pent
- `quant-test-writer` (lead)
- `risk-engine-specialist` (cycle write path + EOD divergence + shadow_hwm consumer)
- `sqlite-specialist` (schema + migration 008 + retention rotation + index design + `_MIGRATION_FILES` update)
- `flask-dashboard-specialist` (column rename at `table_partial.html:62` + Shadow Performance widget + `/api/state` shadow_divergence key)
- `quant-code-reviewer` (discipline gate + PA-18/PA-19 enforcement)

## Edge Cases

- **Symphony first appears mid-cycle**: data phase's first observation creates the baseline row; `shadow_return = current_return` for the first row.
- **Trigger fires mid-cycle**: post-trigger row records `shadow_return = triggered_at_return`, `is_post_trigger = 1`, `trigger_id = <exit_triggers.id>` (advisory soft reference per BC-2).
- **Multiple triggers fire same day**: the SECOND trigger doesn't change shadow_return (already frozen). H2's priority resolution handles trigger semantics.
- **New day reset**: existing wipe clears `triggered=False`; first row of new day records `shadow_return = current_return`.
- **Position closes mid-day, new position opens later same day**: new position resumes shadow tracking from current_return at its first observation. v2 may distinguish position-1 vs position-2; v1 model is per-symphony-per-day.
- **Daemon restart mid-day**: `resume_shadow_baselines()` queries `shadow_history` for the latest row per symphony for the current `trading_day` — AFTER `wipe_transient_state` runs (PA-M1F-2).
- **Composer fetch failure on a cycle**: data phase preserves prior `bot_state` (cycleat-fix pattern); M1F skips that cycle's shadow write for the affected symphony (PA-M1F-15).
- **Fresh DB / no history**: `shadow_history` empty → M1 helpers return `dry_run = None` → dashboard shows '—'. No fall-back to live.
- **DELETE LIMIT portability**: subquery form used (PA-M1F-5) — works on all SQLite builds regardless of `SQLITE_ENABLE_UPDATE_DELETE_LIMIT` compile flag.
- **`ts_et` timezone**: hardcoded UTC-4 (matches H1) per PA-M1F-6; documented inconsistency with zoneinfo flagged as follow-up to unify the telemetry layer.
- **Bootstrap period for V1**: less than 30 days of shadow_history → V1 report returns "indeterminate" per PA-M1F-11.

## Security Considerations

- No new external API surfaces.
- `record_shadow_observation` failure path: ERROR log + swallow. Cycle continues. Same posture as H1's `record_exit_trigger`.
- `/api/state` shadow_divergence extension: read-only; no PII; aggregated divergence numbers per symphony and portfolio.
- Retention bounded at 180 days.
- No new Composer/Alpaca/Anthropic auth surfaces.

## Testing Strategy

- **TDD via real Pent Agent Team** (project hard requirement).
- **Adversarial RED first** — quant-test-writer leads.
- **PA-18 fixture provenance — strict**: golden fixtures in `tests/fixtures/shadow/` captured from synthetic per-symphony inputs; provenance comments documenting each fixture's purpose. Bare literals in assertions = reviewer BLOCK.
- **Tests against the REAL bot_state shape** (M2-class lesson).
- **ZERO live calls in test tier** — mock Composer/Alpaca; use captured fixtures.
- **PA-19 explicit reviewer APPROVE message via SendMessage required before merge** — task-board status NOT sufficient.
- **Multi-session pull-main discipline** — fetch + merge origin/main before each commit; include "branch at <SHA> merged with origin/main <main_SHA>" in handoffs.
- **Live verification mandatory post-merge** — operator restarts daemon; PM verifies via `/api/state` + `/api/triggers` that (a) shadow_history accumulates, (b) M1 helpers produce genuinely distinct dry_run vs if_held values for symphonies with post-trigger rows, (c) Shadow Performance widget renders correctly with proper sign convention, (d) column rename appears, (e) prune task runs in scheduler without locking cycle path.

## Decisions

| Decision | Resolution |
|----------|-----------|
| Migration number | **008** (BC-1; `_MIGRATION_FILES` currently 004-007) |
| Column rename label | **"Held Return"** (panel-recommended; operator approved) |
| Retention default | **180 days** (panel-corrected rationale: 3× safety margin over Glasserman 2003's 60-120 day backtest-reconciliation window) |
| Model A vs Model C | **Model A for v1** (schema implicitly records both via `current_return`); quant-risk-researcher dissent documented in panel verdict §6 |
| `shadow_hwm` formula | **`max(current_return)`** preserves Model C counterfactual semantic from math-engine-audit (BC-3) |
| `trigger_id` constraint | **Advisory soft reference**, NOT FK (BC-2; SQLite FK enforcement off across codebase) |
| Prune location | **Background scheduler callback** in `app.py`, NOT Flask route (BC-4) |
| Composer-API ambiguity | **Resolved by companion DM workstream** — dashboard freeze-at-close removes operator concern about post-trigger Composer behavior; no runtime alert needed |
| `ts_et` timezone | **Hardcoded UTC-4** (matches H1 pattern; follow-up task to unify telemetry layer to zoneinfo) |

## Scope Boundaries

**IN:**
- All seven AC groups (M1F.1 through M1F.7) above.
- 6 block-candidates resolved (BC-1 through BC-6).
- 18 plan amendments (PA-M1F-2 through PA-M1F-16, PA-M1F-new, PA-M1F-new2, PA-M1F-1).
- Closes I3 (shadow_hwm consumption).

**OUT:**
- DM (Dashboard Market-Mode Rendering) — separate sibling plan; dispatch after M1F merges.
- V1 calibration sweep changes (M1F provides the interface).
- Position-1-vs-position-2 distinction within a single day (v2).
- Cash-equivalent post-exit compounding model (v2).
- Port-level shadow aggregation (port-level-math-mode plan).
- Replacing the existing per-symphony "If Held (Shadow)" column logic — only the header label changes; underlying `shadow_str` content untouched.
- Unifying H1's hardcoded UTC-4 and M1F's hardcoded UTC-4 with `zoneinfo` — follow-up task per PA-M1F-6.

## Dependencies

- E2 already merged ✓
- H1 already merged ✓ (M1F's post-trigger rows soft-reference `exit_triggers.id`)
- After M1F merges, **I3 investigation workstream closes**.
- V1 calibration sweep consumes shadow_history (validation interface only).
- DM (separate plan) ships after M1F.

## Hand-off

Plan v2 saved. Next: PM dispatches the M1F Pent implementation team. Multi-session discipline applies. PA-18 + PA-19 strict enforcement.
