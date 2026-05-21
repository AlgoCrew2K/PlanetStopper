# Plan Validation Verdict — Engine Correctness Remediation

**Date:** 2026-05-15
**Plan reviewed:** `feature-plans/engine-correctness-remediation.md`
**Team:** Hex (convener/risk-engine-specialist, quant-risk-researcher, optuna-specialist, sqlite-specialist, flask-dashboard-specialist, reviewer)
**Rounds:** Phase 1 independent first-passes × 6 specialists → Phase 2 consolidated draft v1 → Phase 2 debate responses → Phase 5 final verdict

---

## 1. Executive Summary

The plan is **conditionally approved** — the methodology and sequencing are sound for the large majority of workstreams, and the emergency fixes (E1, E2) are correctly prioritized. However, **7 block-candidates must be resolved before any dispatch**, and an additional **32 plan additions/corrections** are recommended. The most critical issues are: (a) AC-H2.2 contains a factually wrong priority order that would cause an unintended real-money behavior change if dispatched as written; (b) O2 (Deflated Sharpe) is sequenced after V1 retune despite being a stated prerequisite — the sequence must be corrected; (c) E1.5's live-verification method references the H1 telemetry table before it exists; and (d) V3's per-cycle SQLite read is blocking I/O on the execution path. The plan's core structure — Tier 1 emergencies first, Tier 2 observability second, Tier 3 methodology fixes before any retune — is methodologically correct and validated by the panel.

---

## 2. Block-Candidates (must be resolved before dispatch)

### BC-1: E1.5 live-verification references H1 which does not exist at E1 merge time
**CONSENSUS: BLOCK**
AC-E1.5 says "confirm via the trigger-attribution table (once it exists from H1)" — but E1 is sequenced at step 1, H1 at step 3. The live-verification tool doesn't exist when E1 merges. Resolution: either (a) revise AC-E1.5 to use daemon log output for live verification (search for "PARA-ARMED" in alphabot_daemon.log the morning after the fix deploys, assert no fleet-wide PARA-ARM on a non-parabolic open), OR (b) resequence H1 to step 1, E1 to step 2 — the preferred resolution per quant-risk-researcher, since H1's observability benefits all subsequent workstream verifications.
**Recommended resolution: resequence H1 before E1 (H1→E1→E2→…).**
Sources: convener, reviewer, quant-risk-researcher

### BC-2: V1 sequencing — O2 is a stated prerequisite but sequence places V1 before O2
**CONSENSUS: BLOCK — with operator decision required on one sub-question**
AC-V1.1 explicitly lists "After O1+O2+O3+O5 land" as prerequisites. The sequence places V1 at step 7 and O2 at step 10. quant-risk-researcher cites Bailey & López de Prado 2014 §5: DSR must drive SELECTION, not just reporting. optuna-specialist identifies an ambiguity: if O2 changes only the display metric (not the cascade selection criterion), V1 before O2 is technically acceptable. **Operator must clarify: does O2 change the cascade selection criterion in `run_autotuner` (AI branch picks the trial with highest deflated Sharpe), or does it only add a display column?** If selection criterion changes → V1 must move to after O2. If display only → current sequence is acceptable with AC-V1.1 revised to remove O2 from prerequisite list.
Resolution required from operator before V1 dispatch.
Sources: convener, quant-risk-researcher (STRONG AGREE), optuna-specialist (nuanced), reviewer

### BC-3: AC-H2.2 priority order is factually wrong in the plan
**CONSENSUS: BLOCK**
AC-H2.2 states priority is "VWAP Breakdown > Take-Profit > VWAP Bleed Cut > Trailing Stop (per alpha_bot_execution.py:819-831)" but the actual code implements TP > VWAP Bleed Cut > VWAP Breakdown > Trailing Stop (verified by convener and reviewer against alpha_bot_execution.py:821-832). This is not a typo — if an implementer follows AC-H2.2 as written, they will promote VWAP Breakdown above TP, which is a real-money behavior change with no documented rationale.
Plan must explicitly state: (a) current priority order is TP > VWAP Bleed Cut > VWAP Breakdown > Trailing Stop, (b) whether H2 is PRESERVING or CHANGING that order, (c) if changing — the rationale. Additionally, H2's title "Label-only trigger fix" is misleading: AC-H2.1 describes a semantic change (suppress non-winning side effects), not just a label change. The workstream name should be "Concurrent-trigger priority enforcement" or similar.
Sources: convener (discovered), reviewer (confirmed BLOCK, added scope-framing concern)

### BC-4: H2 scope undersell — autotuner.py missing from Architecture table
**CONSENSUS: BLOCK**
If H2 suppresses non-winning counter advancement (below_stop_count, vwap_ticks, etc.) on multi-trigger cycles, this changes observable state that the autotuner replay simulates. The H2 Architecture table does NOT include autotuner.py as a touched file. If counter semantics change under H2, the autotuner simulation produces different counter trajectories than production. This must be addressed: either (a) confirm counters still advance for non-winners (only side-effects are suppressed, not counter accumulation) and document this explicitly, OR (b) add autotuner.py to the H2 Architecture table with explicit scope of the simulation changes needed.
Sources: reviewer (supplemental pass)

### BC-5: O2 schema migration — wrong target table, no additive-first mandate
**CONSENSUS: BLOCK**
AC-O2.2 says "Discord report / llm_suggestions table records BOTH the naive and deflated values." sqlite-specialist identifies this as a category error: deflated Sharpe is a per-run Optuna selection metric; it belongs in `autotune_runs`, not `llm_suggestions`. The correct migration is:
```sql
ALTER TABLE autotune_runs ADD COLUMN deflated_sharpe REAL DEFAULT NULL;
ALTER TABLE autotune_runs ADD COLUMN naive_sharpe    REAL DEFAULT NULL;
```
`llm_suggestions` can cross-reference via its existing `validation_results` JSON blob — no schema change needed there. Additionally, the plan does not mandate the additive-first pattern (NULLable + DEFAULT + migration file). Without explicit naming of the target table, a worker will touch the wrong table.
Sources: sqlite-specialist (BLOCK), reviewer (BLOCK per additive-first rule)

### BC-6: V3 per-cycle SQLite read is blocking I/O on the execution path
**CONSENSUS: BLOCK** (escalated from FLAG by reviewer)
AC-V3.4 says "Detection algorithm reads from H1's exit_triggers table." Per-cycle SQLite reads are blocking I/O on a timing-sensitive 1-minute execution path. The VWAP audit confirms the execution path is latency-sensitive (10:35 cascade timing was documented). V3 must use one of: (a) an in-memory write-through cache of recent triggers that H1 populates at write time (reading from memory, not DB, per cycle), or (b) a separate scheduled task running fleet detection outside the execution loop. The A/C must mandate which pattern and make the non-blocking guarantee explicit.
Sources: reviewer (escalated), convener (flagged in cross-cutting), sqlite-specialist (noted)

### BC-7: E2 fixture provenance — conditional downgrade to FLAG
**CONSENSUS: CONDITIONAL FLAG** (reviewer offered to downgrade from BLOCK)
AC-E2.2's "simulate a sequence" is ambiguous about whether the test input is inline-constructed (M2 anti-pattern risk) or fixture-based. reviewer accepts that for a pure math-layer wiring test, named-constant parametrized tests are equivalent to golden fixtures IF: the stop sequence is defined as named parameter sets in tests/fixtures/math_engine/stop_sequence_*.json with no bare literals in assertions. **Resolution: add this sentence to AC-E2.2: "The simulated stop sequence is defined as a named fixture in tests/fixtures/math_engine/stop_sequence_*.json — no bare literals in test assertions."** With that language added, BC-7 is resolved as a FLAG.
Sources: reviewer (proposed conditional resolution), convener

---

## 3. Per-Workstream Findings

### E1 — PARA-ARM-at-open velocity bug
**CONSENSUS: Plan direction correct. Three required plan edits.**
1. AC-E1.1 must lock the HOW decision before dispatch: operator must choose between (a) sentinel approach — `prev_return = None` in wipe, cycle-1 unconditionally yields velocity=0; or (b) persist-yesterday approach — wipe stores yesterday's terminal current_return as next-day's prev_return. These have different behavioral semantics: sentinel means overnight gap NEVER counts toward velocity; persist-yesterday means overnight gap velocity is measured from yesterday's close. This is a domain decision, not an implementer decision. (Source: convener)
2. AC-E1.4 must specify the exact fix for autotuner.py:94's `prev_return = 0.0` initialization — must match whatever production fix is chosen, so V1's retune is calibrated against corrected velocity. (Source: convener)
3. AC-E1.5 live-verification must be revised per BC-1 resolution. If H1 is resequenced before E1, the trigger-attribution table IS available at E1 merge time and AC-E1.5 as written is valid. (Source: convener, reviewer)
4. tests/database/test_wipe_state.py:147 currently asserts `prev_return == 0.0` as a correctness pin — the E1 team must update this assertion. (Source: sqlite-specialist)
**DISSENT: None.**

### E2 — Trailing-stop monotonicity ratchet wire-up
**CONSENSUS: Plan direction correct. Three required plan edits.**
1. AC-E2.1 must specify the exact value: `previously_persisted_stop_level=bot_state[symphony_id].get("stop_trigger")`. (Source: convener)
2. AC-E2.2 must add fixture-provenance language per BC-7 resolution. (Source: reviewer)
3. Add AC-E2.5: "When a position closes and a new position opens for the same symphony, `previously_persisted_stop_level` resets to None/0; the first cycle of the new position does not inherit the prior position's stop floor." (Source: convener, reviewer)
4. E1 and E2 share database.py — dispatch E2 only after E1 merges to avoid merge conflict on wipe_transient_state. (Source: convener)
**DISSENT: None.**

### H1 — Trigger Attribution Telemetry
**CONSENSUS: Plan direction correct. Five required plan edits.**
1. AC-H1.2 must be corrected: the trigger write is a separate, best-effort, non-blocking write wrapped in try/except, NOT "same transaction as cycle's state write." Same-transaction semantics would mean a telemetry failure rolls back the state write — the opposite of the edge-case guidance's intent. The recommended implementation is a standalone `record_exit_trigger()` function in database.py that opens its own connection. Error handling: ERROR-level log, never propagate. (Source: sqlite-specialist, quant-risk-researcher)
2. The `exit_triggers` table write must record the post-priority-resolution `triggered_reason` (i.e., the reason selected after H2's priority logic applies), not the raw first-True reason. AC-H1.1 must make this explicit. (Source: sqlite-specialist supplement)
3. H1 retention rotation must use batched DELETE with LIMIT clause to avoid long write locks on the shared state DB. (Source: sqlite-specialist)
4. Add `schema_migrations` tracking table as the first migration in this batch (migration 004, before exit_triggers). See PA-14 for the DDL. (Source: sqlite-specialist)
5. `/api/triggers` route: add `limit` parameter (default 100, server-side clamped to max 500); exclude `account_id` entirely from the JSON response (not just mask). (Source: flask-dashboard-specialist, strengthened in Phase 2)
**Dashboard UX:** "Last trigger" data should be rendered as a sub-line in the existing Status cell (below the status badge) — not an 11th column. Pattern already established by TP-ARMED tick count sub-line. (Source: flask-dashboard-specialist, AGREE)
**Aggregate widget:** Rendered as a new horizontal strip below the portfolio strip — styled identically. Pill badges per reason. Empty state: strip hidden. NOT embedded in a tab. (Source: flask-dashboard-specialist, AGREE)
**DISSENT: None.**

### H2 — Concurrent-trigger priority enforcement (rename from "label-only trigger fix")
**CONSENSUS: Direction correct, but plan has a critical error. See BC-3 and BC-4.**
Required before dispatch: operator must clarify (a) whether current priority order TP > Bleed Cut > VWAP Breakdown > Trailing Stop is preserved, and (b) whether non-winning counters advance or are suppressed on multi-trigger cycles. Both answers change the A/C, test assertions, telemetry schema (H2.3/H2.4), and potentially autotuner.py scope.
**DISSENT: None on the block; dispute is between plan text and code reality.**

### H3 — MC RNG seeding
**CONSENSUS: Direction correct. Two plan edits recommended.**
1. AC-H3.1 must specify the exact seed scheme: e.g., `seed = hash(f'{symphony_id}:{date_str}:{cycle_index}') % (2**32)` or equivalent deterministic construction. The seed must be reproducible across daemon restarts of the same cycle. (Source: convener)
2. AC-H3.2 must specify that the same trial in the autotuner uses the same seed (per-trial determinism), not just "some seed." (Source: convener)
Late sequencing (step 12) is acknowledged: MC non-determinism affects O1/O2/V1 results, but CLT stability at 5000 paths makes this acceptable for a one-time sweep. (Source: convener, quant-risk-researcher)
**DISSENT: None.**

### O1 — Purge + embargo in walk-forward split
**CONSENSUS: Direction correct. Two required plan edits.**
1. AC-O1.1 must require an inventory of ALL features touched in run_simulation (not just vol/ATR). The kNN pool in run_monte_carlo spans the full history; the composite objective's exp(-0.015×days_ago) weighting has a ~46-day half-life. The implementer must enumerate all lookbacks and purge by the maximum discovered. (Source: quant-risk-researcher)
2. The plan must address the OOS fold collapse: at 125-day history, a 20-day purge shrinks the ~25-day test fold to ~5 usable days. The plan must either (a) extend synthetic history beyond 125 days before O1 ships, or (b) explicitly document this statistical power tradeoff and its implication for V1 sweep reliability. (Source: optuna-specialist — material gap)
3. O1 embargo constant must be mandated as a named constant with source comment in autotuner.py citing López de Prado 2018 Ch. 7. (Source: reviewer)
**UNRESOLVED — PA-28:** quant-risk-researcher initially argued O1 should precede O5 but withdrew this position in Phase 2, accepting the plan's O5→O1 order as a methodological wash. CONSENSUS: current plan order (O3→O5→O1) is acceptable.

### O2 — Deflated-Sharpe correction at trial selection
**CONSENSUS: Direction correct. Three required plan edits.**
1. AC-O2.1 must cite all four DSR formula inputs: (1) number of trials N, (2) variance of trial Sharpes (σ_SR), (3) skewness (γ_3), (4) kurtosis (γ_4) — citing Bailey & López de Prado 2014, Eq. 9. The current wording ("number of trials + variance") undercounts. (Source: quant-risk-researcher)
2. AC-O2.1 must specify that DSR applies to the AI branch only (maximum of 500 Optuna draws), NOT to fallback_oos_alpha or default_oos_alpha which are single parameter sets. (Source: optuna-specialist)
3. AC-O2.1 must note that the effective number of independent trials (N_eff) for TPE sampler is typically smaller than the raw trial count due to surrogate correlation. Either require N_eff adjustment, or explicitly accept raw N as an upper-bound-conservative approximation. (Source: quant-risk-researcher — PA-31)
4. Target table is `autotune_runs` (not `llm_suggestions`). See BC-5. (Source: sqlite-specialist)
5. Deflated Sharpe display belongs on /ai-advisor, not the main dashboard. (Source: flask-dashboard-specialist, AGREE)
**DISSENT: None on direction.**

### O3 — Study name convention
**CONSENSUS: Direction correct. One required plan edit.**
1. "Archived" in AC-O3.2 must be defined concretely: non-destructive rename via SQL prefix `'LEGACY__'` (never delete). Script targets optuna_studies.db, NOT alphabot_state.db — must be documented as a data migration on Optuna's internal studies table, stored in `migrations/optuna_001_archive_accumulated_studies.sql` with a README note distinguishing it from alphabot_state.db migrations. (Source: sqlite-specialist, optuna-specialist, reviewer)
**DISSENT: None.**

### O4 — Locked-vars consistency
**CONSENSUS: Direction correct. No plan edits needed beyond clarification.**
Confirmed as a pure Python fix in autotuner.py:objective() — no schema change. The `locked_vars` list is already in scope at line 300. (Source: optuna-specialist, sqlite-specialist)
**DISSENT: None.**

### O5 — Composite objective replacement
**CONSENSUS: Direction correct. Three required plan edits. One open operator decision.**
1. The operator must be explicitly told that retiring the 2.0× asymmetric weighting on negative guard-alpha REMOVES loss-aversion semantics from the objective (Kahneman & Tversky 1979). This is a deliberate scope decision, not a defect, but it must be explicit. (Source: quant-risk-researcher)
2. Any of the 5 inline magic numbers that survive as named constants must carry source comments in autotuner.py per the no-magic-numbers rule. (Source: reviewer)
3. The plan must flag the `oos_alpha` scale discontinuity: post-O5, `autotune_runs.oos_alpha` values are in Sharpe units (~1–3), not guard_alpha percent units. Historical rows are not comparable. Discord report format and any dashboard widgets displaying oos_alpha need updating. (Source: optuna-specialist)
4. O3→O5 dependency is load-bearing and correctly sequenced: fresh studies per run ensure all 500 trials in the new study are scored under the new objective. (Source: optuna-specialist — confirmed correct)
**OPERATOR DECISION REQUIRED — PA-5 (Sortino vs Sharpe):**
quant-risk-researcher argues Sortino is methodologically better-matched to a downside-overlay strategy (asymmetric return distribution; Sortino & van der Meer 1994, J. Portfolio Management). optuna-specialist disagrees, preferring Sharpe for parsimony (lowest-complexity standardized metric; Calmar inappropriate for short windows). CONSENSUS POSITION: Sharpe is the plan's current default and is defensible. If the operator chooses Sortino, AC-O5.1 must document the rationale. The plan must require that whichever is chosen, the A/C documents the rationale and the symmetry/asymmetry assumption explicitly. **No block; operator decision.**

### V1 — Calibration sweep
**CONSENSUS: Direction correct. Three required plan edits. Sequencing contingent on BC-2 resolution.**
1. V1 must gate on E1 being live (not just in the sequence) — AC-V1.1 should state: "V1 retune of PARABOLIC_VELOCITY_THRESHOLD is only valid after E1 is deployed and observed in production." (Source: optuna-specialist)
2. The per-symphony gating mechanism must be specified before dispatch: what does "operator-gated, per-symphony rollout" mean mechanically? Options: skip-list in .env, `skip_autotune` DB column, operator runs the sweep manually per symphony. The autotuner loop currently runs all symphonies unconditionally. (Source: optuna-specialist — material gap)
3. V1 sweep data source must be specified: runs against file-cached historical data; no live Alpaca API calls during the sweep. (Source: reviewer)
4. Add a RED test anchor to V1: quant-test-writer needs an adversarial test to work from; without one, the team has no fixture to go RED on. Proposed: "Given a fixture of historical replay data, the sweep produces a recommendation report whose predicted trigger frequency for PARA-ARM is zero when current_return < PARABOLIC_VELOCITY_THRESHOLD for all cycles after the E1 fix." (Source: reviewer — V1 has no RED test mandate)
5. `VWAP_BREAK_CONFIRM_TICKS` should NOT be swept — confirmed by quant-risk-researcher: it is a noise-filter heuristic, not a behavioral threshold; tuning it on historical data is high-overfit-risk. (Source: quant-risk-researcher, plan's own open-question caution)
**SEQUENCING: subject to BC-2 resolution (operator clarifies whether O2 drives selection — if yes, V1 moves to after O2).**

### V2 — Open-window time gate
**CONSENSUS: Direction correct. One required plan edit.**
1. The new gate helper in math_engine.py must receive a boolean `in_grace_window` flag pre-computed by the caller (alpha_bot_execution.py), NOT compute wall-clock time internally. The math layer must remain a pure function with no wall-clock side effects. (Source: convener)
2. Add explicit AC-V2.3: "TP and Trailing Stop DO fire in the grace window." The plan states this in V2.1 prose but does not make it a testable criterion. (Source: convener)
**DISSENT: None.**

### V3 — Fleet-decorrelation circuit breaker
**CONSENSUS: Observational-only design correct. Three required plan edits per BC-6.**
1. Per-cycle SQLite read must be replaced with an in-memory write-through cache pattern OR a separate scheduled task. See BC-6. (Source: reviewer, convener)
2. `FLEET_CORRELATION_ALERT_AUTO_CLEAR_MINUTES` must be a named `.env` constant (or named Python constant with source comment) — not a bare "30 min" literal in A/C or code. See BC-4. (Source: reviewer)
3. V3's ">50% active symphonies" and "3-minute window" thresholds must be named constants with source comments justifying the values (e.g., empirical observation from today's cascade, operational policy, etc.). (Source: convener)
**UX:** Banner is amber (not rose/red); manually dismissable via namespaced sessionStorage key (`alphabot_fleet_alert_dismissed_until`); positioned above portfolio strip, below header. (Source: flask-dashboard-specialist, AGREE all specialists)
**DISSENT: None on observational-only design.**

### I1 — Log-time squeeze investigation
**CONSENSUS: Scoping correct. One plan edit.**
1. AC-I1.1 must explicitly cite `docs/research/dashboard/math-engine-methodology-review.md` §2 as the starting evidence base — zero published precedent already documented there; the investigation worker should build on it, not redo it. (Source: quant-risk-researcher)
**DISSENT: None.**

### I2 — Stop-compounding investigation
**CONSENSUS: Direction correct. One plan edit.**
1. AC-I2.2 must require the investigation to produce a quantitative threshold (numeric: "compounding causes >X% adverse exits when symphony_vol > Y AND time_ratio > Z") not merely a qualitative finding. This makes the output actionable for I2.3's follow-up scope decision. (Source: quant-risk-researcher — PA-32)
**DISSENT: None.**

### I3 — shadow_hwm consumption audit
**CONSENSUS: Scoping correct. One plan edit.**
1. AC-I3.2 must explicitly state that if shadow_hwm is consumed, adding the regression test constitutes a follow-up workstream (with team composition and A/C) — it is not a solo file-edit. I3's output formally gates that follow-up dispatch. (Source: convener)
**DISSENT on disposition:** quant-risk-researcher prefers SURFACE (shadow_hwm enables live-vs-backtest reconciliation per López de Prado's Three Principles of Backtest Hygiene) over REMOVE. Convener is neutral. This is an operator decision once I3's audit is complete.

---

## 4. Cross-Cutting Consensus

### Sequencing correction (highest priority)
Recommended corrected sequence (pending BC-1, BC-2 resolution):
1. **H1** — Trigger attribution telemetry (resequenced first so E1.5 live-verify works)
2. **E1** — PARA-ARM fix
3. **E2** — Trailing-stop monotonicity wire-up (starts after E1 merges)
4. **O3** — Study name convention
5. **O5** — Composite objective replacement
6. **O1** — Purge + embargo
7. **O2** — Deflated Sharpe (PENDING BC-2 operator decision — may move to step 6 before V1)
8. **V1** — Calibration sweep (PENDING BC-2; must be after O2 if O2 drives selection)
9. **H2** — Concurrent-trigger priority enforcement (PENDING BC-3/BC-4 resolution)
10. **V2** — Open-window time gate
11. **O4** — Locked-vars consistency
12. **H3** — MC RNG seeding
13. **I1** — Log-time squeeze investigation
14. **I2** — Stop-compounding investigation
15. **I3** — shadow_hwm audit
16. **V3** — Fleet circuit breaker

### Required Testing Strategy additions
Add the following bullet to the Testing Strategy section after "Fixture-first":
> **No inline fixture construction**: No workstream may define fixture values inline in the test function body. All input sequences, parameter sets, and expected outputs must be: (a) captured from a production run via /api-fixture, (b) schema-derived with a runtime validator, or (c) defined as named constants in tests/fixtures/<surface>/ with a source comment explaining their provenance. Bare literals in assertions are a test-quality violation.

Add the following bullet to the Testing Strategy section after the "Live verification" paragraph:
> **Explicit reviewer gate**: Each workstream merge is blocked until quant-code-reviewer sends an explicit APPROVE message via SendMessage. Task-board status is not sufficient.

### Required Schema Migration additions
- migrations/004_schema_migrations_tracker.sql — CREATE TABLE schema_migrations (migration_name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now'))) — first migration in this batch
- migrations/005_exit_triggers.sql — CREATE TABLE IF NOT EXISTS exit_triggers (per AC-H1.1) plus two indexes
- migrations/006_autotune_runs_sharpe.sql — ALTER TABLE autotune_runs ADD COLUMN deflated_sharpe REAL DEFAULT NULL; ALTER TABLE autotune_runs ADD COLUMN naive_sharpe REAL DEFAULT NULL
- migrations/optuna_001_archive_accumulated_studies.sql — data migration on optuna_studies.db (Optuna's internal studies table), NOT alphabot_state.db. Documents the rename-based archival procedure.

### Additional workstream recommended (O6 — Frozen evaluation window)
quant-risk-researcher (High confidence; López de Prado 2018 Ch. 7.4) recommends adding O6: implement a 60/20/20 train/validation/frozen-eval split to prevent reporting bias where the operator sees an OOS alpha conditioned on the same fold that selected the params. Severity: methodological correctness (not real-money safety), so PA-class not BC-class. Recommended as O6, sequenced after O1+O2 and before V1. Reviewer and convener note this is consistent with the optuna audit's Rec #5 which the plan does not address.

### Workstream count correction
Plan Summary says "14 workstreams / 4 tiers" — correct to "16 workstreams / 5 tiers."

### Fu & Zhang 2010 citation
The citation appears in test_stop_monotonicity.py and test_cross_call_lifecycle.py but NOT in math_engine.py's named-constant source comments. quant-risk-researcher flags it as unverified (not in their prior methodology review; they cited Han, Zhou & Zhu 2016 and Kaminski-Lo 2014). math_engine.py's compute_breakeven_update docstring should carry the citation with full author names, year, venue, and DOI. If the citation cannot be verified, replace with Han, Zhou & Zhu 2016 (J. Banking & Finance).

---

## 5. Open Dissents

### PA-5 (Sortino vs Sharpe) — UNRESOLVED, operator decision
quant-risk-researcher: Sortino is methodologically better-matched to a downside-overlay strategy. Supported by strategy semantics (asymmetric distribution), distributional argument (existing objective already embeds asymmetric weighting), and citation (Sortino & van der Meer 1994).
optuna-specialist: Sharpe is lower-complexity and a correct choice; Calmar is worse. Does not object to Sortino but prefers parsimony.
CONSENSUS POSITION: whichever the operator chooses, the A/C must document the rationale and the symmetry/asymmetry assumption explicitly. **No block; operator decides.**

### BC-2 (O2 before V1) — UNRESOLVED, awaiting operator clarification
If O2 changes cascade selection criterion → V1 must move after O2. If O2 is display-only → current sequence acceptable with AC-V1.1 revised to remove O2 from prerequisites. Operator must answer: "Does O2 change which trial the AI branch selects, or only what gets reported?"

---

## 6. Specialist Sign-Offs

**convener (risk-engine-specialist):** Plan is conditionally approvable. The 7 block-candidates are real and must be resolved before any workstream is dispatched. The emergency sequence (E1, E2) is correctly prioritized; the methodology sequence (O3→O5→O1→V1) is sound. The H2 priority-order error is the most dangerous finding — an implementer following AC-H2.2 as written would make an unintended real-money behavior change. Resequencing H1 first is the right call. Ready to proceed once blocks are resolved.

**quant-risk-researcher:** Plan is methodologically sound on the big calls. BC-2 (O2 before V1 for DSR selection) and BC-1 (H1 before E1) are strong blocks. PA-31 (DSR effective-N) and PA-32 (I2 quantitative threshold) should be added to the plan. The Fu & Zhang 2010 citation needs verification. Sortino is my recommended default for O5 but this is an operator call. No hard-blocks beyond what the consolidated list captures.

**optuna-specialist:** Plan is feasible on all O-workstreams. Critical implementation gaps: O1 test-fold collapse (5 usable OOS days) must be addressed before V1; V1 per-symphony gating mechanism is unspecified; O5 scale discontinuity in autotune_runs must be flagged for operator. BC-2 is a real ambiguity — operator must clarify whether O2 changes selection or only display. Supports Sharpe over Sortino for parsimony. Ready for dispatch once blocks resolved.

**sqlite-specialist:** H1 schema design is correct (TEXT blob, 2 indexes). Transaction semantics error in AC-H1.2 must be corrected before dispatch (PA-6 — separate best-effort write). Target table for O2 is autotune_runs, not llm_suggestions (BC-5). O3 migration is a data migration on Optuna's internal schema — must be labeled clearly. schema_migrations tracker should be migration 004. Ready.

**flask-dashboard-specialist:** All six attributed PA items agreed. Strengthened PA-9 to exclude account_id entirely (not mask). Dashboard vertical stacking confirmed: fleet banner (amber, above portfolio strip) → portfolio strip → triggers strip → #notification → symphony table. PA-7 sub-line approach correct and will update on every morphdom poll without special handling. O2 deflated Sharpe belongs on /ai-advisor, not main dashboard. Ready.

**reviewer:** 7 block-candidates confirmed (4 from first-pass + 3 from supplemental). BC-3 and BC-4 (H2 priority error + scope undersell) are the most dangerous from a discipline standpoint — the plan text will cause an implementer to introduce an unintended behavior change. BC-7 (E2 fixture provenance) conditionally resolved to FLAG with added AC language. PA-18 wording provided for Testing Strategy. The explicit reviewer APPROVE message gate (PA-19) is non-negotiable per M2-class lesson. Ready once blocks resolved.
