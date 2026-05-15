# Feature: M1F — Real Shadow-Equity Series Instrumentation
Status: ready (queued behind E2; pending panel-validation before dispatch)
Created: 2026-05-15

## Summary

Instrument the AlphaBot engine to persist a real per-cycle shadow-equity time series — the actual numerical trajectory of "what each symphony's equity would be if AlphaBot's exits had fired when they triggered." Today the M1 dashboard helpers return `dry_run == if_held` literally by construction (`analytics.py:438, 453, 414`) because `bot_state` has no shadow-equity series; `current_return` is computed from Composer's `last_percent_change` — the same source the `if_held` value reads. M1F closes that gap: the engine writes a `shadow_history` row per symphony per cycle; the M1 helpers consume it to produce genuinely-distinct `dry_run` values; `shadow_hwm` (which exists today as an underspecified field) becomes meaningfully consumed; the V1 calibration sweep gains a real backtest-vs-live divergence signal; the EOD post-mortem can compare actual vs hypothetical engine value-add.

Sequenced AFTER E2 lands (E2 is small, ~1 cycle), BEFORE V1 calibration sweep (so V1's recommendations are calibrated against a real shadow trajectory). Operator dispatches the Hex validation panel against this plan before any workstream dispatch — same protocol as the engine-correctness-remediation plan.

## Acceptance Criteria

### M1F.1 — Shadow-equity series schema + write path
- **AC-M1F.1.1**: New `migrations/006_shadow_history.sql` (additive-first, NULLable + DEFAULT where applicable). Table:
  ```sql
  CREATE TABLE IF NOT EXISTS shadow_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    ts_et TEXT NOT NULL,
    trading_day TEXT NOT NULL,
    symphony_id TEXT NOT NULL,
    account_id TEXT,
    cycle_id TEXT,
    current_return REAL NOT NULL,          -- live, Composer-reported
    shadow_return REAL NOT NULL,           -- AlphaBot-hypothetical
    is_post_trigger INTEGER NOT NULL DEFAULT 0,
    trigger_id INTEGER                     -- FK to exit_triggers.id when post-trigger
  );
  CREATE INDEX IF NOT EXISTS idx_shadow_history_sym_day ON shadow_history (symphony_id, trading_day, ts_utc);
  CREATE INDEX IF NOT EXISTS idx_shadow_history_day ON shadow_history (trading_day, ts_utc);
  ```
- **AC-M1F.1.2**: New `database.record_shadow_observation(symphony_id, account_id, cycle_id, ts_utc, ts_et, trading_day, current_return, shadow_return, is_post_trigger, trigger_id)` helper. Opens its OWN sqlite3 connection (does NOT join the cycle's state-write transaction). Try/except SWALLOWS failures with ERROR log; cycle MUST NOT fail on telemetry. Same pattern as H1's `record_exit_trigger`.
- **AC-M1F.1.3**: New `database.prune_old_shadow_history(retention_days)` — batched DELETE LIMIT 1000 loop. `.env` configurable via `SHADOW_HISTORY_RETENTION_DAYS=180` (longer than H1's 90 since shadow history is the foundation of backtest reconciliation; default 180 = ~9 months).
- **AC-M1F.1.4**: Rotation runs via the existing daily-scheduled prune task in `app.py` (the one H1 introduced); add `prune_old_shadow_history()` alongside `prune_old_triggers()`.

### M1F.2 — Shadow-return computation per cycle
- **AC-M1F.2.1**: In the data phase of `alpha_bot_execution.py`, for each symphony every cycle, compute `shadow_return` as follows:
  - If symphony's `bot_state[symphony_id].triggered` is **False** for today: `shadow_return = current_return` (still holding; mirrors live).
  - If symphony's `bot_state[symphony_id].triggered` is **True** for today: `shadow_return = bot_state[symphony_id].triggered_at_return` (frozen at trigger time; no further intraday movement).
- **AC-M1F.2.2**: Call `database.record_shadow_observation(...)` after computing `shadow_return`. Non-blocking (own connection, swallow failures).
- **AC-M1F.2.3**: Reset semantics on new trading day — the day's first cycle records a baseline row; `triggered=True` from yesterday is wiped by the existing new-day reset, so today's `shadow_return = current_return` until a new trigger fires today.
- **AC-M1F.2.4**: Daemon-restart resilience — on startup, the engine reads the latest `shadow_history` row per symphony for the current `trading_day` to resume from the right baseline (no re-init that would lose intra-day state).

### M1F.3 — M1 helper consumer migration
- **AC-M1F.3.1**: `analytics.py:get_symphony_today_change` — `dry_run` now reads the latest `shadow_history` row's `shadow_return` for the symphony's current `trading_day`. `if_held` continues to read `current_return` (Composer-reported). Genuinely distinct values.
- **AC-M1F.3.2**: `analytics.py:get_symphony_cumulative_return` — `dry_run` reads the cumulative `shadow_return` trajectory (chain-link of daily shadow returns across the symphony's history, computed from `shadow_history`); `if_held` continues to read Composer's `simple_return * 100`. Genuinely distinct values.
- **AC-M1F.3.3**: `analytics.py:get_symphony_max_drawdown` — `dry_run` reads the peak-to-trough drawdown of the cumulative shadow trajectory; `if_held` continues to read Composer's `max_drawdown * 100` (post-cycleat-fix scaling). Genuinely distinct values.
- **AC-M1F.3.4**: Portfolio helpers (`get_portfolio_*`) automatically benefit — they value-weight per-symphony values; once per-symphony differs, portfolio aggregates differ.
- **AC-M1F.3.5**: When a symphony has zero rows in `shadow_history` (just-deployed, fresh DB), the helpers return `dry_run = None` (the no-data sentinel from the cycleat-fix work) — render as `—`. Do NOT silently fall back to `if_held` again.
- **AC-M1F.3.6**: No-data sentinel handling for the portfolio aggregator: a symphony with `dry_run = None` is excluded from the value-weighted aggregation (consistent with the existing CR/MDD None-skip behavior).

### M1F.4 — Dashboard surfacing + column rename
- **AC-M1F.4.1**: `templates/table_partial.html` — rename the existing "If Held (Shadow)" column to "Shadow Peak Return" (or similar — operator picks the exact label during plan review; this column shows `shadow_hwm` / `triggered_at_return` per `table_partial.html:187`, which is conceptually distinct from the M1 dry_run trajectory). The renaming clears the UX confusion where two unrelated "shadow" surfaces share a name.
- **AC-M1F.4.2**: The portfolio strip + per-symphony CR/TC/MDD cells now show **genuinely different** dry_run vs if_held values whenever the shadow trajectory diverges from live (post-trigger fires, or accumulated daily exit gains).
- **AC-M1F.4.3**: A new dashboard widget or tab — "Shadow Performance" — shows per-symphony `(live - shadow)` divergence today + cumulative since-deploy. Compact strip below the existing triggers strip; pill badges colored by sign (green = AlphaBot helped, red = AlphaBot cost). Operator-led discovery surface.

### M1F.5 — EOD post-mortem integration
- **AC-M1F.5.1**: `alpha_bot_execution.py` EOD post-mortem branch computes a per-symphony `eod_divergence = current_return - shadow_return` from the day's `shadow_history` rows. Writes to the existing `post_mortem_<date>.json` alongside the existing post-mortem fields.
- **AC-M1F.5.2**: Portfolio-level EOD divergence (value-weighted) also recorded.
- **AC-M1F.5.3**: An ERROR-level log + dashboard banner fires if EOD post-mortem cannot compute divergence (e.g., no shadow_history rows for a symphony that traded today) — surfaces a coverage gap rather than silently emitting NaN.

### M1F.6 — V1 calibration consumer (interface only)
- **AC-M1F.6.1**: This plan does NOT change V1's sweep logic — that's V1's own workstream. M1F provides the **`shadow_history` table as a stable interface** that V1 will consume in its own cycle.
- **AC-M1F.6.2**: Schema is queryable via SQL: `SELECT trading_day, AVG(current_return - shadow_return) FROM shadow_history WHERE symphony_id = ? GROUP BY trading_day` reconstructs the divergence time series.
- **AC-M1F.6.3**: M1F's testing strategy includes a smoke test exercising this query shape to confirm the schema supports V1's intended consumption.

### M1F.7 — `shadow_hwm` consumption (closing I3)
- **AC-M1F.7.1**: This plan addresses I3 (the shadow_hwm consumption audit) by making the field meaningful: `shadow_hwm = max(shadow_return)` for the symphony's current `trading_day`, computed from `shadow_history`. Updated in the data phase.
- **AC-M1F.7.2**: `bot_state[symphony_id].shadow_hwm` continues to be persisted (for cross-cycle continuity and dashboard read), but its source-of-truth is the shadow_history table.
- **AC-M1F.7.3**: When M1F merges, I3's investigation workstream is closed (its audit verdict becomes "consumed — by M1F").

## Architecture

| Surface | Files touched |
|---------|---------------|
| Engine write path | `alpha_bot_execution.py` data phase — call `record_shadow_observation` per symphony per cycle |
| State helpers | `database.py` — new `record_shadow_observation`, `prune_old_shadow_history`, `load_latest_shadow_row(symphony_id, trading_day)`, daemon-startup `resume_shadow_baselines` |
| Schema | `migrations/006_shadow_history.sql` (additive-first) |
| Analytics consumer | `analytics.py` — `get_symphony_*` helpers query `shadow_history` for `dry_run` values |
| Dashboard | `templates/table_partial.html` (column rename) + `templates/index.html` (new "Shadow Performance" widget) + `app.py /api/state` (surface divergence summary) |
| EOD post-mortem | `alpha_bot_execution.py` post-mortem branch — write divergence fields to `post_mortem_<date>.json` |
| Scheduled task | `app.py` — `prune_old_shadow_history()` added to existing daily prune scheduler |
| `.env` | `SHADOW_HISTORY_RETENTION_DAYS=180` |
| Tests | `tests/shadow/test_shadow_history.py` + `tests/fixtures/shadow/*.json` (schema-derived; PA-18) |

**Team composition**: Pent (5)
- `quant-test-writer` (lead — golden fixtures critical for the per-cycle write semantics)
- `risk-engine-specialist` (cycle write path + EOD divergence + shadow_hwm consumer)
- `sqlite-specialist` (schema + migration 006 + retention rotation + index design)
- `flask-dashboard-specialist` (column rename + new Shadow Performance widget + /api/state surface)
- `quant-code-reviewer` (discipline gate + scope discipline + PA-18/PA-19 enforcement)

## Edge Cases

- **Symphony first appears mid-cycle** (e.g., operator added a new symphony in Composer mid-day): the data phase's first observation creates the baseline row; `shadow_return = current_return` for the first row; subsequent rows accumulate normally.
- **Trigger fires mid-cycle**: the post-trigger row records `shadow_return = triggered_at_return`, `is_post_trigger = 1`, `trigger_id = <exit_triggers.id>`. All subsequent rows for the symphony today carry the frozen value.
- **Multiple triggers fire same day**: the SECOND trigger doesn't change shadow_return (it's already frozen from the first). H2's priority resolution handles the trigger semantics; M1F just records.
- **New day reset**: existing engine wipe (post-E1) clears `triggered=False`; first row of new day records `shadow_return = current_return`.
- **Position closes mid-day, new position opens later same day**: the new position resumes shadow tracking from current_return at its first observation; M1F doesn't currently distinguish position-1-shadow from position-2-shadow within a day (simpler v1 model). Flagged for v2 refinement if operator cares.
- **Daemon restart mid-day**: startup queries `shadow_history` for the latest row per symphony for the current `trading_day`; resumes from there. No re-init that would lose intra-day baseline.
- **Composer fetch failure on a cycle**: the data phase preserves prior `bot_state` (cycleat-fix pattern). M1F shadow write should skip that cycle for the affected symphony (don't write a row with stale `current_return`).
- **Fresh DB / fresh deploy with no history**: `shadow_history` is empty; M1 helpers return `dry_run = None` sentinel (renders as `—`); the dashboard shows the no-data state explicitly, not the prior false-mirroring.
- **Trigger fires THEN gets re-evaluated** (H2's priority resolution may change the chosen triggered_reason): shadow_history doesn't re-write old rows; the frozen value stays at the originally-recorded `triggered_at_return`. If H2 changes the priority order in a future cycle, that's a forward-looking change; historical shadow_history rows are immutable.
- **Daylight-saving boundary**: `trading_day` and `ts_et` both use the `get_current_et` helper (which must use `zoneinfo`); cross-DST consistency is verified by a fixture.

## Security Considerations

- **No new external API surfaces** introduced. All shadow data is engine-internal.
- **`record_shadow_observation` failure path**: ERROR log + swallow. Cycle continues. Same posture as H1's `record_exit_trigger`. No information leakage in error logs.
- **`/api/state` surface for the divergence widget**: read-only. Returns aggregated per-symphony divergence numbers and per-port portfolio divergence. No PII (symphony IDs only). No new auth surface.
- **No XSS surfaces** in the new "Shadow Performance" widget — all data-driven strings are numeric or come from controlled engine writes; Jinja autoescape applies.
- **Retention boundary** — `shadow_history` is bounded at 180 days default; configurable via `.env`. Backstops disk growth.
- **Composer/Alpaca auth boundary unchanged**.

## Testing Strategy

- **TDD via real Pent Agent Team** — operator dispatches the Hex validation panel against this plan BEFORE dispatching the implementation team. Same protocol as the engine-correctness-remediation plan.
- **Adversarial RED first** — quant-test-writer leads. Golden fixtures in `tests/fixtures/shadow/` cover: pre-trigger row sequence, post-trigger frozen row, new-day baseline, mid-cycle position open, daemon-restart baseline-resume, retention rotation. Each fixture has a provenance comment (PA-18).
- **No inline literal assertions** — PA-18 strict. Reviewer BLOCKs on bare literals.
- **ZERO live Composer/Alpaca/Anthropic calls** in the test tier — mock `fetch_symphony_stats` and `current_return` computation; use captured fixtures.
- **Live verification mandatory after merge** — operator restarts daemon; PM verifies via `/api/triggers` (existing H1 endpoint) AND a new `/api/state` divergence field that (a) shadow_history starts accumulating, (b) M1 helpers produce genuinely different dry_run vs if_held values for symphonies that have post-trigger rows, (c) the dashboard "Shadow Performance" widget renders, (d) the renamed table column reads correctly, (e) prune task runs nightly without locking the cycle path. **Test pass ≠ live correctness** (M2-class lesson).
- **Cross-workstream regression** — 1372 baseline at E2 completion; M1F adds tests; no existing tests should break.
- **`shadow_hwm` regression coverage** — verify post-M1F that `bot_state.shadow_hwm` derives correctly from the shadow_history table; existing `shadow_hwm` consumers don't change behavior unexpectedly.
- **Explicit PA-19 reviewer APPROVE message via SendMessage before merge** — not task-board status.

## Decisions

| Decision | Rationale |
|----------|-----------|
| Sequenced AFTER E2, BEFORE V1 | E2 is small (~1 cycle) and shares `alpha_bot_execution.py`; merging it first avoids conflict. V1's calibration sweep needs the shadow_history interface to use real shadow trajectories; if V1 runs before M1F, the recommendations are partly garbage. |
| Storage: separate `shadow_history` table (not JSON blob on bot_state) | Queryable, supports retention rotation, time-series friendly. Same pattern as H1's `exit_triggers`. JSON blob would be opaque to SQL. |
| 180-day retention default | Shadow history is foundation for backtest-hygiene reconciliation; 90 days too short for seasonal pattern analysis. Operator can adjust via .env. |
| Frozen-at-trigger semantics (no further movement post-trigger) | Simpler v1 model: AlphaBot's exit semantically equals "sell to cash at trigger price." Cash doesn't move. v2 could model post-exit cash-equivalent compounding if operator wants. |
| Same `non-blocking, own-connection` posture as H1's record_exit_trigger | The cycle MUST NOT fail on telemetry; transactional isolation prevents that (H1's panel verdict PA-6 lesson). |
| Column rename in `table_partial.html` (not deletion) | The existing "If Held (Shadow)" column is read by operator habit; rename clarifies; deletion would surprise. Operator picks the new label during plan review. |
| `shadow_hwm` becomes consumed by M1F (closes I3) | I3 was scoped as a research investigation. M1F's existence resolves the open question — `shadow_hwm` becomes the daily peak of the shadow trajectory. |
| No-data sentinel for symphonies with empty shadow_history | Same posture as the cycleat-fix None sentinel — `—` rendering, not silent fall-back-to-live. Operator can tell when data is missing. |
| Operator dispatches Hex validation panel before workstream dispatch | Matches the engine-correctness-remediation plan flow. M1F is large enough to warrant the same scrutiny (real-money signal foundation). |

## Scope Boundaries

**IN:**
- All seven AC groups (M1F.1 through M1F.7) above.
- New `shadow_history` table + migration 006.
- `record_shadow_observation` helper + retention rotation.
- Engine data-phase write per symphony per cycle.
- `analytics.py` helper migration to query shadow_history.
- Dashboard column rename + new "Shadow Performance" widget.
- EOD post-mortem divergence fields.
- `shadow_hwm` consumption (closes I3).

**OUT:**
- V1 calibration sweep changes (M1F provides the interface; V1's workstream consumes it).
- Position-1-vs-position-2 shadow distinction within a single day (v2 if operator wants).
- Cash-equivalent post-exit compounding model (v2; current model freezes shadow at trigger price).
- Cross-symphony shadow correlation analysis (separate workstream if needed).
- Replacing the existing per-symphony "If Held (Shadow)" column with the M1F surface — that column is renamed for clarity but its underlying logic (showing `shadow_hwm` / `triggered_at_return`) stays separate from the M1 helpers' new shadow trajectory.
- Port-level shadow aggregation (lives in the port-level-math-mode plan; orthogonal).
- Backfilling shadow_history for historical days before deploy (operator can manually if needed; v1 starts accumulating from M1F deploy).

## Dependencies

- **E2 must merge first** (small surface, ~1 cycle).
- **H1 telemetry must be live** (already merged) — M1F's post-trigger rows reference `exit_triggers.id` via `trigger_id`.
- After M1F merges, the **I3 investigation workstream closes** (consumption verdict: "consumed by M1F").
- **V1 calibration sweep** consumes shadow_history when its workstream runs.
- The **port-level-math-mode plan** can extend M1F into port-level aggregation in its own cycle (no blocking dependency).

## Hand-off

Plan saved. Next steps:
1. Operator reviews this plan.
2. Operator dispatches the Hex validation panel (`team: m1f-plan-validation`, 6 specialists: convener/risk-engine + quant-risk-researcher + sqlite-specialist + flask-dashboard-specialist + optuna-specialist + reviewer; back-and-forth debate to ONE consolidated verdict — same protocol as the engine-correctness-remediation plan validation).
3. PM integrates the verdict's findings into this plan (v2).
4. Operator approves v2.
5. PM dispatches the M1F Pent implementation team after E2 merges.
