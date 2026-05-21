# Plan Validation Verdict — M1F Real Shadow-Equity Series

**Date:** 2026-05-16
**Plan reviewed:** `feature-plans/m1f-real-shadow-equity-series.md`
**Team:** Hex (convener/risk-engine-specialist, quant-risk-researcher, optuna-specialist, sqlite-specialist, flask-dashboard-specialist, reviewer/quant-code-reviewer)
**Rounds:** Phase 1 independent first-passes × 6 specialists → Phase 3 consolidated draft v1 → Phase 4 debate (2 rounds) → Phase 5 final verdict
**Note:** flask-dashboard-specialist submitted Phase 1 findings late (after Phase 3 draft was sent) but their findings were incorporated via supplemental debate messages and are fully reflected in this verdict.

---

## 1. Executive Summary

The plan is **conditionally approved** — the core design (per-cycle shadow-equity series in a queryable table, non-blocking own-connection write, no-data sentinel propagation, shadow_hwm consumption closing I3) is methodologically sound and correctly addresses the mirroring bug documented in the dry-run-if-held RCA. However, **6 block-candidates must be resolved before dispatch**, and **16 plan amendments are required** to make the A/C precise enough for safe implementation. The most critical issues are: (a) the migration number 006 is already occupied — shadow_history must be 008; (b) the example V1 calibration query in AC-M1F.6.2 is missing a `WHERE is_post_trigger = 1` filter that makes it meaningful, and the smoke test must cover three query shapes; (c) AC-M1F.7.1's `shadow_hwm = max(shadow_return)` formula is a forced internal consistency error — the existing engine already implements `shadow_hwm` as a Model C counterfactual (math-engine-audit.md:228-233), and the plan cannot ship Model A `shadow_return` alongside Model C `shadow_hwm` without silently breaking the dashboard column's operator-visible meaning; the correct formula is `max(current_return)`; (d) the prune task scheduler placement is ambiguous and could cause an architecture-rule violation. The schema itself is sound — the `current_return` column already records the no-trigger counterfactual and `shadow_return` records the frozen value, giving V1 both needed signals without a new column.

---

## 2. Block-Candidates (must resolve before dispatch)

### BC-1: Migration number collision — 006 is already occupied
**CONSENSUS: BLOCK**
AC-M1F.1.1 specifies `migrations/006_shadow_history.sql` but `006_autotune_runs_sharpe.sql` is already registered in `database.py:508`. An implementer dispatched with this number will either silently skip the migration (if the runner skips existing numbers) or collide. Additionally, `_MIGRATION_FILES` list in `database.py` must be updated alongside the DDL file — this is not called out in AC-M1F.1.1 and is a common implementation omission.
**Resolution:** Correct migration filename to `008_shadow_history.sql` (verify exact next number against the current `_MIGRATION_FILES` list — 007 may be reserved by port-level-math-mode). Add explicit requirement to AC-M1F.1.1: "Update `_MIGRATION_FILES` list in `database.py` alongside the new DDL file."
**Sources:** sqlite-specialist (primary), reviewer (additive-first list-update rule)

### BC-2: Unenforced FK `trigger_id → exit_triggers.id` misleadingly labeled
**CONSENSUS: BLOCK**
The DDL shows `trigger_id INTEGER` with a comment "FK to exit_triggers.id" but no `FOREIGN KEY` constraint. SQLite FK enforcement is OFF by default and no existing connection in the codebase sets `PRAGMA foreign_keys = ON`. An implementer reading "FK" will expect referential integrity; a worker dispatched against this plan may add a real FOREIGN KEY constraint and introduce a PRAGMA dependency that breaks existing connection patterns, or may omit it and silently violate the plan's implied contract. H1's `exit_triggers` table itself has no enforced FKs — the pattern must be consistent.
**Resolution:** Replace "FK to exit_triggers.id" comment with "soft reference to exit_triggers.id — advisory only; no FOREIGN KEY constraint; SQLite FK enforcement is off by default across this codebase." Remove the word "FK" from the A/C entirely.
**Sources:** sqlite-specialist (primary), convener (independently flagged)

### BC-3: AC-M1F.7.1 `shadow_hwm` formula is wrong — and the plan is internally inconsistent between Model A and Model C
**CONSENSUS: BLOCK**
AC-M1F.7.1 states `shadow_hwm = max(shadow_return)` for the symphony's current trading day. Under the plan's own Model A semantics (shadow_return freezes at triggered_at_return post-trigger), `max(shadow_return)` = the trigger price for all post-trigger rows — a constant. This silently changes `shadow_hwm`'s existing semantic from "what HWM would have reached if held" (Model C intent explicitly confirmed by math-engine-audit.md:228, 233: "post-trigger peak tracker | NO (stays monotone post-trigger) | Shows what HWM 'would have been' if the engine had not exited") to "the frozen trigger price," which is a regression. The dashboard column the operator currently reads as a Model C counterfactual would silently change meaning with no UI signal of the shift — the same failure mode the dry-run-if-held RCA documented (M1 helpers silently mirrored if_held for over a release).

**This is a forced choice, not an operator preference.** The plan cannot ship with Model A `shadow_return` AND Model C `shadow_hwm` semantics simultaneously without internal inconsistency. The existing engine already uses `shadow_hwm` as Model C (the audit confirms it); the new `shadow_return` under Model A creates a contradiction within the same telemetry layer.

**The correct formula is `shadow_hwm = max(current_return)` over the day's shadow_history rows.** `current_return` is Composer's live symphony value, which continues updating even after AlphaBot exits (Composer's allocation remains until the sell executes). This preserves the existing Model C semantic of shadow_hwm, is trivially computable from the schema (`SELECT MAX(current_return) FROM shadow_history WHERE symphony_id = ? AND trading_day = ?`), and requires no new column or schema change. The schema already records both Model A (shadow_return = frozen) and Model C (current_return = live counterfactual) values in every row — this is the implicit Option 2 the panel converged on.

**Resolution:** Correct AC-M1F.7.1 to read: "shadow_hwm = max(current_return) for the symphony's current trading_day, computed from shadow_history. This preserves the field's existing semantic (counterfactual HWM — what the symphony's peak return would have been if AlphaBot had not exited). The formula is: `SELECT MAX(current_return) FROM shadow_history WHERE symphony_id = ? AND trading_day = ?`"

**Note on Composer API dependency:** Does Composer's `last_percent_change` for a symphony continue updating after AlphaBot fires a sell signal for the remainder of that trading day? If yes, `current_return` in shadow_history is a valid Model C counterfactual. If Composer freezes or removes the value upon sell detection, a separate data source would be needed. This must be verified before implementation via the composer-api-researcher. Flag this as an explicit pre-implementation check in the plan.
**Sources:** optuna-specialist (Phase 2 supplement — M1F.7 formula correction; Phase 3 — full agreement on internal consistency forcing), quant-risk-researcher (supplemental + Phase 3 strengthening — shadow_hwm Model C intent per math-engine-audit.md:228-233)

### BC-4: Prune task placement — route-vs-scheduler ambiguity
**CONSENSUS: BLOCK**
AC-M1F.1.4 says `prune_old_shadow_history()` is added "via the existing daily-scheduled prune task in `app.py`" alongside `prune_old_triggers()`. The plan does not explicitly state this is a background scheduler callback, not a Flask route handler. Architecture constraint 2 (dashboard is read-only; never an action surface for live trades) and constraint 5 (templates open SQLite read-only; UI never reruns the engine) require state-mutating operations to run in the scheduler, not routes. The ambiguity is genuine: an implementer reading "added to the existing daily-scheduled prune task" might wire it into a route if the existing prune task is called from a route context.
**Resolution:** Amend AC-M1F.1.4 to read: "Prune task executes in the existing background scheduler callback in `app.py` (NOT in any Flask route handler). The scheduler callback already calls `prune_old_triggers()`; add `prune_old_shadow_history()` alongside it in the same callback function body."
**Sources:** reviewer (primary BLOCK)

### BC-5: AC-M1F.6.2 query is insufficient for V1's validation needs; smoke test shape wrong
**CONSENSUS: BLOCK**
The plan's example query `SELECT trading_day, AVG(current_return - shadow_return) FROM shadow_history WHERE symphony_id = ? GROUP BY trading_day` mixes pre-trigger rows (where shadow_return = current_return by construction, so divergence = 0) with post-trigger rows (where divergence is meaningful). The signal is diluted to noise by the un-filtered aggregation. The critical clarification from debate: V1 sweeps against `synthetic_history` (Alpaca tick data via the autotuner's `run_simulation`), NOT against shadow_history. Shadow_history is V1's post-selection **validation** layer — used after the sweep to verify whether the selected parameters produce real-world divergence patterns matching the simulation. The plan's AC-M1F.6 conflates these two roles.

AC-M1F.6.3's smoke test must exercise three query shapes:
1. **Per-day post-trigger alpha-attribution** (the corrected V1 validation query): `SELECT trading_day, AVG(current_return - shadow_return) AS avg_post_trigger_divergence FROM shadow_history WHERE symphony_id = ? AND is_post_trigger = 1 GROUP BY trading_day`
2. **Per-cycle intraday trajectory**: `SELECT ts_utc, current_return, shadow_return, is_post_trigger, trigger_id FROM shadow_history WHERE symphony_id = ? AND trading_day = ? ORDER BY ts_utc ASC` — needed to identify where intraday divergence opens and verify timestamp semantics
3. **HWM reconstruction**: `SELECT trading_day, MAX(current_return) AS shadow_hwm_counterfactual FROM shadow_history WHERE symphony_id = ? GROUP BY trading_day` — proves the counterfactual HWM is recoverable from the schema without additional columns

Additionally, the plan must explicitly document the V1/shadow_history relationship: "V1's Optuna sweep uses synthetic_history (Alpaca data) as its simulation input; shadow_history is V1's post-selection validation layer for comparing simulated predictions against real-world live divergences."
**Sources:** optuna-specialist (primary — Gaps 1-3 + V1 role clarification), quant-risk-researcher (V1 objective shape), convener (independently flagged intraday-vs-EOD averaging)

### BC-6: Missing prune index for `ts_utc`
**CONSENSUS: BLOCK**
The plan's two proposed indexes (`idx_shadow_history_sym_day ON shadow_history (symphony_id, trading_day, ts_utc)` and `idx_shadow_history_day ON shadow_history (trading_day, ts_utc)`) do not lead with `ts_utc` alone. The `prune_old_shadow_history` DELETE `WHERE ts_utc < ?` will full-table-scan at ~1.1M rows at steady state (180-day retention × 11 symphonies × ~390 cycles/day × 252 days/year ÷ 2), locking the state DB for multiple seconds and violating Architecture constraint 1 (no blocking I/O on the 1-minute execution path). H1's `exit_triggers` migration has `idx_exit_triggers_ts ON exit_triggers (ts_utc DESC)` for exactly this reason.
**Resolution:** Add a third index: `CREATE INDEX IF NOT EXISTS idx_shadow_history_ts_utc ON shadow_history (ts_utc);` — matching the H1 pattern. The existing two indexes serve the read queries; this index serves the prune DELETE.
**Sources:** sqlite-specialist (primary)

---

## 3. Per-A/C-Group Findings

### M1F.1 — Shadow-equity series schema + write path
**CONSENSUS: Direction correct. Six required plan amendments (BC-1, BC-2, BC-6 above + three below).**

**PA-M1F-5 — DELETE LIMIT syntax dependency:**
Batched `DELETE ... LIMIT 1000` (AC-M1F.1.3) requires SQLite compiled with `SQLITE_ENABLE_UPDATE_DELETE_LIMIT`. Not guaranteed on all Python SQLite distributions. The safe portable pattern: `DELETE FROM shadow_history WHERE id IN (SELECT id FROM shadow_history WHERE ts_utc < ? ORDER BY ts_utc LIMIT 1000)`. AC-M1F.1.3 must use the subquery form.
Source: convener

**PA-M1F-6 — `ts_et` timezone must be consistent with H1:**
The plan specifies `get_current_et` with `zoneinfo` for DST correctness, but H1's `record_exit_trigger:574` uses hardcoded UTC-4. Panel consensus (sqlite-specialist primary, optuna-specialist deferred): **match H1's hardcoded UTC-4 for now**. Rationale: consistency within the telemetry layer outweighs correctness for a display-only field; a split where shadow_history uses zoneinfo and exit_triggers uses UTC-4 creates a more confusing inconsistency than either choice alone. The plan must document the choice explicitly in AC-M1F.1.1 and file a follow-up task to unify both tables to zoneinfo if DST-correct ts_et is desired across the telemetry layer.
Source: sqlite-specialist (primary consensus position), optuna-specialist (deferred to sqlite-specialist), convener

**PA-M1F-10 — DEFAULT clauses on NOT NULL columns — CLOSED, no action:**
Reviewer flagged `current_return REAL NOT NULL` and `shadow_return REAL NOT NULL` as lacking DEFAULT clauses. After debate, sqlite-specialist and optuna-specialist agree these are intentionally NOT NULL with no DEFAULT — they are required at write time and `DEFAULT 0.0` would be semantically wrong (a zero shadow_return is a meaningful value, not a missing-data sentinel). For a fresh `CREATE TABLE IF NOT EXISTS` (not `ALTER TABLE`), there is no partial-deploy risk. The write helper (AC-M1F.1.2) must supply both values or raise; document this explicitly in the A/C. No DDL change required.
Source: sqlite-specialist (intentional design), optuna-specialist (confirming — retracted earlier +1 on this flag)

**Additional schema observation (sqlite-specialist):** `account_id` and `cycle_id` nullability should be commented in the DDL for consistency with exit_triggers pattern. `cycle_id` format (e.g., `YYYYMMDD_HHMM`) must be pinned in the A/C (PA-M1F-4 below) to enable future JOIN against exit_triggers.

**Connection pattern (sqlite-specialist, OBS 4):** `record_shadow_observation` should use literal `sqlite3.connect(DB_FILE, timeout=10.0)` — matching H1's `record_exit_trigger:579` — not `get_connection()`, to visually enforce the isolation guarantee at code-review time.

**DISSENT: None.**

### M1F.2 — Shadow-return computation per cycle
**CONSENSUS: Direction correct (Model A is a valid v1 choice). Two required plan amendments.**

The panel reached consensus that Model A (freeze-at-trigger) is acceptable for v1 given that: (a) `current_return` in shadow_history already records the Composer live value post-trigger (the no-trigger counterfactual is implicitly present in the existing column — no new `counterfactual_return` column needed), (b) the alpha-attribution query can be derived by filtering `WHERE is_post_trigger = 1` and comparing `current_return - shadow_return`, and (c) V1's sweep uses synthetic_history not shadow_history as its optimization input. The DISSENT position (quant-risk-researcher's Option 2) is noted but not a block: the schema already implements Option 2 implicitly.

**PA-M1F-2 — Daemon-restart resume sequencing must be explicit:**
AC-M1F.2.4 does not specify whether `resume_shadow_baselines()` runs before or after `wipe_transient_state()`. If it runs before, yesterday's rows may be loaded into bot_state before the new-day wipe clears triggered state, creating a stale `triggered_at_return` overwrite. The A/C must state: "`resume_shadow_baselines()` runs AFTER `wipe_transient_state()` completes for the current cycle."
Source: convener

**PA-M1F-3 — `bot_state["triggered"]` field path must be explicit:**
AC-M1F.2.1 uses dot notation (`bot_state[symphony_id].triggered`) but the actual structure is a dict (`bot_state[symphony_id]["triggered"]`). Correct the A/C to use dict notation throughout.
Source: convener

**PA-M1F-15 — Composer fetch failure gate must be explicit A/C:**
The edge-cases section mentions "if Composer fetch fails, skip shadow write" in prose only. This must be an explicit A/C in M1F.2: "shadow_observation write is gated on successful Composer data fetch for the symphony in that cycle. If fetch failed, no row is written for that symphony that cycle (consistent with the cycleat-fix pattern)."
Source: convener

**Open question for pre-implementation verification:** Does Composer's `last_percent_change` for a symphony continue updating after AlphaBot fires a sell signal for the remainder of that trading day? This determines whether `current_return` in shadow_history is a valid Model C counterfactual. Dispatch composer-api-researcher to verify before the implementation team starts work on M1F.2.
Source: optuna-specialist (critical dependency flagged in supplement)

**DISSENT — quant-risk-researcher (recorded, not a block):** Model C (hold-out counterfactual) is the literature gold standard for trailing-stop evaluation (Kaminski & Lo 2014, Han et al. 2016). Model A underestimates AlphaBot's value-add in down-trending post-trigger windows and prevents V1 from detecting premature triggers using the pure shadow_return signal. However, since the schema implicitly records both values, and V1's sweep does not directly use shadow_history as its optimization input, this dissent does not block dispatch. It is recorded for the operator's consideration when V1's validation methodology is designed.

### M1F.3 — M1 helper consumer migration
**CONSENSUS: Direction correct. Two required plan amendments.**

**PA-M1F-16 — Chain-link formula must be specified:**
AC-M1F.3.2 says dry_run reads "the cumulative shadow_return trajectory (chain-link of daily shadow returns)" but does not specify: (a) which row per day is used (last row of day by ts_utc order), (b) the chain-link formula (`∏(1 + r_i/100) - 1` applied to EOD shadow_return per day), (c) what happens on days with no shadow_history rows (exclude from the chain; dry_run = None if any day is missing). At 180 days × ~390 rows/day the analytics query is heavy — the A/C must specify whether results are cached (recommended: cache per trading_day, invalidate on new shadow_history row).
Source: convener

**PA-M1F-16b — MDD boundary condition:**
AC-M1F.3.3 requires peak-to-trough drawdown of the cumulative shadow trajectory. With a single row (first cycle after deploy), drawdown is undefined (need ≥ 2 data points). The A/C must specify: if fewer than 2 distinct trading days exist in shadow_history, dry_run for MDD = None.
Source: convener

**DISSENT: None.**

### M1F.4 — Dashboard surfacing + column rename
**CONSENSUS: Direction correct. Four required plan amendments.**

**PA-M1F-1 — Column rename target and label must be specified precisely:**
AC-M1F.4.1 references "the existing 'If Held (Shadow)' column" but flask-dashboard-specialist (confirmed by RCA at `dry-run-if-held-mirroring-rca.md:50-64`) identifies the correct target as `table_partial.html:62` — the "If Held" column (`shadow_str` block showing live return + Guard Alpha diff). This is col 7, not col 6 (which is already "Shadow Peak" / `shadow_hwm`). The plan's column description ("shows `shadow_hwm` / `triggered_at_return`") correctly points at `triggered_at_return` being in col 7's `shadow_str` content (lines 117, 122-129), but the header name is wrong. Plan must specify: target = `table_partial.html:62` `<th>` element; recommended new label = **"Held Return"** (panel consensus — short, unambiguous, distinguishable from both M1's `if_held` helper key and the new M1F "shadow" vocabulary). Operator must confirm or supply alternate.
Source: flask-dashboard-specialist (primary), convener (column label unresolved)

**PA-M1F-13 — Shadow Performance widget must reference established dashboard stack and sign convention:**
AC-M1F.4.3 says "compact strip below the existing triggers strip" but does not cite the established stacking order from the prior panel verdict (fleet banner → portfolio strip → triggers strip → shadow performance strip → symphony table). Additionally, the sign convention for pill badge colors is ambiguous and depends on Model A vs C semantics:
- Under Model A: `divergence = current_return - shadow_return`; post-trigger drop means `divergence < 0` = AlphaBot helped = green (counterintuitive: green when negative)
- Under Model C (or correct Model A interpretation using counterfactual): `divergence > 0` = AlphaBot helped = green (intuitive)
The AC must state the sign convention explicitly: "pill badge is green when `current_return < shadow_return` post-trigger (AlphaBot exited before price dropped), red when `current_return > shadow_return` post-trigger (AlphaBot exited before price recovered). Legend must be rendered in the widget." The implementer must also constrain strip to single-row pill format (max ~48px height) to preserve viewport budget when both triggers strip and shadow performance strip are visible simultaneously.
Source: flask-dashboard-specialist (sign convention analysis), convener (stack order)

**PA-M1F-14 — /api/state extension must be explicit:**
The Architecture table says "/api/state (surface divergence summary)" without specifying whether divergence data is added to the existing endpoint or served from a new one. Panel consensus (flask-dashboard-specialist): **extend `/api/state` with a `shadow_divergence` key** consistent with how `portfolio_strip` is handled today. Structure: `{"by_symphony": {"<id>": {"today": float|null, "cumulative": float|null}}, "portfolio_today": float|null}`. A new endpoint is not needed; the `shadow_history` read is one lightweight GROUP BY query per polling cycle and is not on the execution path.
Source: flask-dashboard-specialist (Option A recommendation), convener (flagged ambiguity)

**PA-M1F-new — Null guard for Shadow Performance widget must be explicit:**
When shadow_history has zero rows (fresh deploy), the widget's JS rendering path must apply the same `v == null → '---'` pattern used by `pctColor` and the Jinja template sentinels. The A/C must explicitly state: "Widget renders '—' for all symphonies when no shadow_history data exists; never renders NaN% or 0.00%." The existing `pctColor` null guard (`v == null ? 'text-slate-400'`) correctly covers M1F.3.5/3.6's `dry_run = None` sentinel — no changes needed to that path.
Source: flask-dashboard-specialist

**DISSENT: None.**

### M1F.5 — EOD post-mortem integration
**CONSENSUS: Direction correct. Two required plan amendments.**

**PA-M1F-9 — EOD divergence must be explicitly observational-only:**
AC-M1F.5.1 computes `eod_divergence` in the EOD post-mortem branch of `alpha_bot_execution.py` but does not state this is observational only. The A/C must explicitly add: "Divergence computation is observational only; does NOT condition any live-order call (`place_order`, `submit_order`, `liquidate`, `cancel_order`)."
Source: reviewer (primary)

**PA-M1F-5b — EOD divergence row selection:**
AC-M1F.5.1 says "from the day's shadow_history rows" without specifying which row. The A/C must state: "EOD divergence uses the LAST row for the day (by ts_utc DESC LIMIT 1); explicitly documents that this may not be the market-close row if the engine was down for the session's final interval."
Source: convener

**DISSENT: None.**

### M1F.6 — V1 calibration consumer (interface only)
**CONSENSUS: Plan direction correct but A/C requires significant clarification. See BC-5 above.**

Key clarifications required (beyond BC-5's block resolution):
1. AC-M1F.6.1 must explicitly state: "V1's Optuna sweep uses `synthetic_history` (Alpaca tick data) as its simulation input; `shadow_history` is V1's post-selection validation layer for comparing sweep predictions against real-world live divergences. These are distinct roles."
2. AC-M1F.6.2's corrected query (with `WHERE is_post_trigger = 1`) answers "on triggered days, by how much did live price move post-exit?" — this is the validation signal. V1's team must design their validation methodology around this signal shape.
3. **PA-M1F-11 — V1 bootstrap period risk — three-state fold-sufficiency check:** shadow_history only accumulates from M1F deploy-day. V1 runs within the first 125 trading days will have a sparse or empty frozen-eval fold. `compute_sortino_ratio` returns `0.0` for empty series and `1e6` for zero downside deviation — neither is a correct "insufficient sample" sentinel. Per Bailey/de-Prado 2014, N≥30 independent observations are required before a Sortino/DSR value is interpretable. V1's report must implement a three-state check: `sample_size < 30` → "indeterminate"; `≥30 and no divergence` → "provisional_no_overfit"; `≥30 and divergence detected` → "overfit_confirmed".
Source: optuna-specialist (primary), quant-risk-researcher (Bailey/de-Prado N≥30 framing)

**DISSENT: None on direction; debate closed on BC-5 resolution.**

### M1F.7 — `shadow_hwm` consumption (closing I3)
**CONSENSUS: Plan direction correct (closes I3). One required plan amendment — BC-3 above.**

I3's investigation workstream closes cleanly. The open question from the prior engine-correctness panel (Section 6.3 of math-engine-audit.md: "was shadow_hwm originally intended to be the canonical HWM and high_water_mark a deprecated alias?") remains unanswered by the git history — but M1F resolves the consumption question by giving shadow_hwm a concrete source-of-truth. This is sufficient to close I3.

**PA-M1F-new2 — In-memory vs table source-of-truth split:**
AC-M1F.7.2 says "bot_state shadow_hwm continues to be persisted... but its source-of-truth is the shadow_history table." This is a two-source-of-truth design. The A/C must specify the synchronization contract: (a) the in-memory update logic at `alpha_bot_execution.py:462-463 / 628-629` is REMOVED post-M1F (table is now canonical), OR (b) the in-memory update is PRESERVED as a write-through cache that is reconciled with the table on daemon restart via `resume_shadow_baselines()`. Option (b) is more resilient; if chosen, the A/C must specify what happens when they diverge (table wins).
Source: convener

**DISSENT: None on direction.**

---

## 4. Cross-Cutting Findings

### Sequencing
M1F is correctly sequenced after E2 (already merged) and before V1. The M1F implementation team's branch must fork from the E2-merged commit on main, not from a pre-E2 state. Both workstreams touch `alpha_bot_execution.py`; forking after E2 merges avoids a conflict-prone parallel branch.

### Required plan amendments (summary list)
| ID | Finding | Source |
|----|---------|--------|
| BC-1 | Migration 008 (not 006) + _MIGRATION_FILES update | sqlite-specialist, reviewer |
| BC-2 | trigger_id advisory soft reference (not FK) | sqlite-specialist, convener |
| BC-3 | shadow_hwm = max(current_return) not max(shadow_return) | optuna-specialist, quant-risk-researcher |
| BC-4 | Prune runs in background scheduler callback, not Flask route | reviewer |
| BC-5 | AC-M1F.6.2 query needs is_post_trigger=1; smoke test 3 shapes; V1 role clarified | optuna-specialist, quant-risk-researcher, convener |
| BC-6 | Add idx_shadow_history_ts_utc index for prune DELETE | sqlite-specialist |
| PA-M1F-2 | resume_shadow_baselines() runs after wipe_transient_state() | convener |
| PA-M1F-3 | bot_state["triggered"] dict notation | convener |
| PA-M1F-4 | cycle_id format pinned (e.g., YYYYMMDD_HHMM) | convener |
| PA-M1F-5 | DELETE subquery form for LIMIT portability | convener |
| PA-M1F-5b | EOD divergence row = last by ts_utc | convener |
| PA-M1F-6 | ts_et timezone decision (zoneinfo recommended) | sqlite-specialist, convener |
| PA-M1F-7 | 180-day retention rationale: drop "seasonal" claim; correct to "3× safety margin over 60-120 day backtest-reconciliation window" | quant-risk-researcher |
| PA-M1F-8 | Add math_mode TEXT NOT NULL DEFAULT 'per_symphony' to schema | optuna-specialist |
| PA-M1F-9 | EOD divergence observational-only — no live-order conditioning | reviewer |
| PA-M1F-10 | NOT NULL columns intentionally have no DEFAULT — CLOSED, no action (debate settled) | sqlite-specialist, optuna-specialist |
| PA-M1F-11 | V1 bootstrap period risk: three-state fold-sufficiency check (N≥30 per Bailey/de-Prado) | optuna-specialist, quant-risk-researcher |
| PA-M1F-12 | Position-churn v2 SLA: re-evaluate if >1 position-change/day/symphony in first 30 days | quant-risk-researcher |
| PA-M1F-13 | Shadow Performance widget: established stack order + sign convention + height constraint | flask-dashboard-specialist, convener |
| PA-M1F-14 | /api/state shadow_divergence key (Option A — extend existing endpoint) | flask-dashboard-specialist |
| PA-M1F-15 | Composer fetch failure gate as explicit A/C | convener |
| PA-M1F-16 | Chain-link formula, day-row selection rule, caching strategy | convener |
| PA-M1F-new | Widget null guard explicit A/C | flask-dashboard-specialist |
| PA-M1F-new2 | shadow_hwm in-memory vs table source-of-truth split | convener |
| PA-M1F-1 | Column rename target: table_partial.html:62; label "Held Return" (operator confirms) | flask-dashboard-specialist, convener |

### Required pre-implementation verifications (before dispatching the Pent team)
1. **Composer API behavior post-trigger:** Does `last_percent_change` continue updating after AlphaBot fires a sell? Dispatch composer-api-researcher to verify. This determines whether `current_return` in shadow_history serves as a valid no-trigger counterfactual.
2. **Correct migration number:** Count entries in `_MIGRATION_FILES` in `database.py` to confirm 008 is the right next number (007 may be reserved by port-level-math-mode plan).
3. **Operator label confirmation:** "Held Return" for `table_partial.html:62` column — operator must confirm or supply alternate.

### What the plan gets RIGHT (consensus credit)
- Per-cycle series in a queryable table, not a JSON blob — matches López de Prado AFML Ch. 14 backtest-hygiene discipline
- Non-blocking own-connection write with try/except swallow — H1's PA-6 lesson correctly encoded
- No-data sentinel `dry_run = None` propagation — cycleat-fix lesson correctly encoded, no silent fall-back-to-live
- EOD divergence as a separate post-mortem field — ex-post performance attribution discipline
- shadow_hwm consumption — genuinely closes I3
- Security posture — no new external API surfaces, read-only /api/state addition, Jinja autoescape covers XSS
- Schema implicitly records both Model A (shadow_return) and Model C (current_return) values — maximum flexibility with no additional column cost
- 180-day default retention is adequate for backtest-reconciliation use case (3× safety margin over Glasserman 2003's 60-120 day empirical distribution requirement)

---

## 5. Operator Decision Questions

**OD-1 (required before dispatch):** Column rename label — panel recommends "Held Return" for `table_partial.html:62`. Confirm or supply alternate.

**OD-2 (required before dispatch):** Is the 180-day retention rationale acceptable as corrected (drop "seasonal pattern analysis"; substitute "3× safety margin over backtest-reconciliation window")? Or extend default to 252 days for seasonal coverage?

**OD-3 (informational — not a block):** Model A (freeze-at-trigger) is the plan's v1 design and is acceptable given the schema implicitly records the counterfactual. quant-risk-researcher's dissent (prefer Model C / Option 2) is recorded. Operator should be aware that `AVG(current_return - shadow_return) WHERE is_post_trigger = 1` is the post-trigger divergence query that V1 will use for post-selection validation — this IS directional and does surface premature-exit signals.

---

## 6. Open Dissents

### Retained dissent — quant-risk-researcher on Model A semantic
quant-risk-researcher maintains that Model C (hold-out counterfactual) is the literature gold standard for trailing-stop evaluation (Kaminski & Lo 2014, Han et al. 2016; Han, Zhou & Zhu 2016 JBF) and that Model A's frozen-equity framing prevents clean alpha-attribution. The panel consensus (based on optuna-specialist's clarification that V1 uses synthetic_history for its objective, and that the schema already records current_return as an implicit counterfactual) accepts Model A for v1 while preserving the counterfactual signal. quant-risk-researcher accepts this consensus position under protest — the dissent is documented but does not block dispatch.

---

## 7. Sign-Off Block

**convener (risk-engine-specialist):** Plan is conditionally approved. The 6 block-candidates are real and must be resolved before dispatch. The shadow_hwm formula correction (BC-3: max(current_return) not max(shadow_return)) is the most subtle but consequential finding — it preserves dashboard semantic integrity. The schema design is sound; the query documentation and smoke test coverage are the primary implementation-readiness gaps. Pre-implementation Composer API verification is mandatory before M1F.2 coding begins. Ready to proceed once blocks and operator decisions are resolved.

**quant-risk-researcher:** Core direction approved. BC-3 resolution (shadow_hwm = max(current_return)) correctly preserves the existing counterfactual semantic. My dissent on Model A vs C is recorded above — I accept the panel consensus. The retained concern: operators interpreting the Shadow Performance widget under Model A semantics must understand the sign convention clearly (green = price dropped after exit, not "AlphaBot beat buy-and-hold"). The sign convention A/C (PA-M1F-13) adequately addresses this. PA-M1F-7 (180-day retention rationale) must be corrected before dispatch. Ready.

**optuna-specialist:** Conditionally approved. BC-5 resolution correctly reframes V1's shadow_history role as post-selection validation, not optimization input — this distinction was absent from the plan and its absence would have caused V1's team to design the wrong interface. math_mode discriminator (PA-M1F-8) should be added now. BC-3 (shadow_hwm formula) correction is correct and trivially implementable. Composer API empirical verification is a required pre-implementation check. Ready once blocks resolved.

**sqlite-specialist:** Schema-layer issues are addressed by BC-1, BC-2, BC-6, PA-M1F-5, PA-M1F-6, PA-M1F-10. The migration numbering collision is the most operationally dangerous finding — a silent skip would leave the schema in an indeterminate state. The prune index (BC-6) is a correctness requirement, not a performance nicety, at 1.1M rows. The ts_et timezone decision (PA-M1F-6) must be made explicitly before dispatch; recommend zoneinfo for correctness and a follow-up to align H1's hardcoded UTC-4. Ready once blocks resolved.

**flask-dashboard-specialist:** Dashboard-layer findings resolved by PA-M1F-1 (column rename target and label), PA-M1F-13 (sign convention + stack order + height constraint), PA-M1F-14 (extend /api/state with shadow_divergence key), PA-M1F-new (widget null guard). The column rename target ambiguity in the plan (wrong column name cited) is a dispatch safety issue — the implementer must be given `table_partial.html:62` as the exact target line. No blockers from dashboard domain. Ready once operator confirms column label.

**reviewer (quant-code-reviewer):** BC-4 (prune task placement) and PA-M1F-9 (EOD divergence observational-only) address the two architectural boundary concerns I raised. PA-18 fixture provenance is explicitly required in the plan's testing strategy. PA-19 explicit reviewer APPROVE message gate is already in the plan. Schema reversibility concern (PA-M1F-10, DEFAULT clauses) is addressed. Sequencing is clean — M1F is genuinely independent of remaining engine-correctness workstreams. Ready once blocks resolved.
