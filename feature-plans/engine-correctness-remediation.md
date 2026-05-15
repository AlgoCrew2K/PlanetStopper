# Feature: Engine Correctness Remediation
Status: ready
Created: 2026-05-15

## Summary

Comprehensive remediation plan addressing all findings from the math-engine audit panel (`docs/research/dashboard/vwap-audit.md`, `math-engine-audit.md`, `math-engine-methodology-review.md`, `optuna-tuning-audit.md`). Two real-money defects are live on `main` (PARA-ARM-at-open, trailing-stop monotonicity); a structural calibration-methodology gap underpins multiple symptoms; the VWAP system is mathematically correct but aggressively tuned with no trigger-attribution observability. This plan organizes 14 workstreams into 4 tiers — Emergency, Observability, Calibration-Methodology, VWAP-tuning, Investigations — sequenced so dependent retunes only run after upstream methodology fixes land. **Supersedes `feature-plans/vwap-remediation.md`** (the 5 VWAP workstreams are reorganized into this plan in dependency order).

## Acceptance Criteria

### TIER 1 — EMERGENCIES (real-money exposure now on main)

#### E1 — PARA-ARM-at-open velocity bug
- **AC-E1.1**: `database.py:140` new-day reset stops wiping `prev_return` to `0.0` blind. On new-day reset, `prev_return` is set such that **cycle-1 produces zero velocity** (the first observed `current_return` defines a baseline; velocity is computed only after a second observation). Concrete approach proposed: persist yesterday's terminal `current_return` and use it for cycle-1 `prev_return`, OR sentinel the state so cycle-1 unconditionally yields `velocity=0`. Phase-A worker selects.
- **AC-E1.2**: A symphony opening +2.0% at market open (with no subsequent intraday movement on cycle-1) does NOT auto-PARA-ARM. Verified by RED test using a fixture replay.
- **AC-E1.3**: PARA-ARM CAN still fire on cycle-2 (and later) when actual intraday velocity ≥ threshold. Verified by a second RED test.
- **AC-E1.4**: `autotuner.py:94` (which replays the same bug in simulation) gets the same fix. Tuned `PARABOLIC_VELOCITY_THRESHOLD` values are flagged as **needing retune** (handled in V1 after methodology fixes ship).
- **AC-E1.5**: Live verification after merge: restart the daemon, confirm via the trigger-attribution table (once it exists from H1) that the next morning's open does NOT auto-PARA-ARM all positive-gap symphonies.

#### E2 — Trailing-stop monotonicity ratchet wire-up
- **AC-E2.1**: `alpha_bot_execution.py:698-705` passes `previously_persisted_stop_level` to `compute_breakeven_update` so the canonical Fu & Zhang 2010 ratchet semantics apply on the live path. Math layer is already correct; engine ignores it today.
- **AC-E2.2**: Trailing stops CANNOT decrease tick-to-tick. RED test: simulate a sequence where `compute_breakeven_update`'s naive return would lower the stop relative to the prior cycle's persisted level; assert the persisted level holds.
- **AC-E2.3**: Existing `tests/math_engine/test_stop_monotonicity.py` (7 scenarios, 4 of which already exercise the ratchet) — confirm all 7 GREEN on the live consumer path post-fix.
- **AC-E2.4**: No regression to the existing breakeven-lock behavior on positions that should genuinely have their stop advance.

### TIER 2 — OBSERVABILITY (foundation; everything else depends on this)

#### H1 — Trigger Attribution Telemetry
- **AC-H1.1**: New `exit_triggers` table in the state SQLite DB. Schema: `id`, `ts_utc`, `ts_et`, `symphony_id`, `account_id`, `triggered_reason`, `at_return`, `gate_state_json` (HWM, vwap_ticks, bleed_ticks, mc_prob, symphony_vol + the gate values that crossed thresholds), `cycle_id`. Index on `(ts_utc DESC)` and `(symphony_id, ts_utc DESC)`.
- **AC-H1.2**: Engine writes one row per trigger fire at the single set site (`alpha_bot_execution.py:936`). Non-blocking; same transaction as the cycle's state write.
- **AC-H1.3**: Telemetry persists across daemon restarts.
- **AC-H1.4**: Dashboard exposes per-symphony "Last trigger" cell with timestamp + reason (or `—` if none today). Compact aggregate widget shows trigger-count-by-reason for the current day.
- **AC-H1.5**: `GET /api/triggers?since=<iso_ts>&symphony_id=<id>&reason=<r>` returns historical triggers as JSON for programmatic export. Read-only.
- **AC-H1.6**: Retention 90 days (configurable via `.env` `TRIGGER_TELEMETRY_RETENTION_DAYS=90`); rotation happens via a daily-scheduled vacuum task, not inline.

### TIER 3 — CALIBRATION METHODOLOGY (must precede any retunes)

#### O1 — Purge + embargo in walk-forward split
- **AC-O1.1**: `autotuner.py:274-283` (train/test split) implements a **purge** between train and test that removes train samples whose features lookback into the test fold. Vol features (`calculate_20d_vol`) need a 20-day purge; ATR features (14-day) need a 14-day purge — the engine purges by the MAX of feature lookbacks.
- **AC-O1.2**: An **embargo** period (default 1 trading day, configurable) is enforced between the train-end and test-start to prevent autocorrelation leakage. Embargo size is a constant with source comment.
- **AC-O1.3**: RED tests using a deterministic feature-pipeline fixture confirm: a train sample whose 20-day window overlaps any test sample is excluded; no train/test pair has a same-day ts within the embargo window.
- **AC-O1.4**: Documentation of the methodology in the `autotuner.py` docstring citing López de Prado 2018 Ch. 7.

#### O2 — Deflated-Sharpe correction at trial selection
- **AC-O2.1**: `autotuner.py` final selection of best trial computes a deflated Sharpe ratio (Bailey & López de Prado 2014 formula) using the number of trials run and the variance across them. The naive `best_alpha_train` is supplemented with `deflated_sharpe` in the selection logic.
- **AC-O2.2**: Discord report / `llm_suggestions` table records BOTH the naive and deflated values; UI surfaces the deflated for any review surface.
- **AC-O2.3**: RED test pins the deflation math against the published formula on a fixture sample.

#### O3 — Study name convention
- **AC-O3.1**: `autotuner.py:328` uses `study_name=f"{timestamp}__{normalized_name}"` with `load_if_exists=False` — fresh study per run, matching the project CLAUDE.md gotcha note. No more resumed studies accumulating trials across days.
- **AC-O3.2**: Existing accumulated studies in the optimization DB are migrated to the new naming OR archived; the migration path is documented.
- **AC-O3.3**: RED test pins that a new run produces a study with the timestamp prefix and does NOT find/extend a prior study with the same symphony name.

#### O4 — Locked-vars consistency
- **AC-O4.1**: `TRIGGER_THRESHOLD_PCT` (currently in `DEFAULT_LOCKED_VARS`) is consistently locked across both the AI advisor write path AND the Optuna suggest path. Pick one of: include in Optuna search but mark "locked, not adoptable"; OR exclude from Optuna search entirely. Operator decision (default: exclude from Optuna search entirely since it's locked).
- **AC-O4.2**: All other vars in `DEFAULT_LOCKED_VARS` audited for the same divergence; fixed if found.
- **AC-O4.3**: RED test: a var in `DEFAULT_LOCKED_VARS` cannot be suggested by Optuna AND cannot be adopted by AI advisor.

#### O5 — Composite objective replacement
- **AC-O5.1**: `autotuner.py:219-229` ad-hoc objective with 5 inline magic numbers (`1.0, 1.5, 0.75, 2.0, 0.015`) is replaced with a recognized quant metric: Sharpe (annualized), Sortino, or Calmar. Operator picks; default proposal: Sharpe-equivalent on the test fold for parsimony.
- **AC-O5.2**: The new objective is documented with citations and the existing magic numbers retired (or moved to named constants if they encode something the new objective doesn't capture).
- **AC-O5.3**: RED test exercises the new objective on a fixture and pins the math against the published formula.

### TIER 4 — VWAP-SPECIFIC TUNING (depends on Tier 3 methodology fixes)

#### V1 — Calibration Sweep (PARA + VWAP after methodology fixes ship)
- **AC-V1.1**: After O1+O2+O3+O5 land, run Optuna walk-forward sweep over: `PARABOLIC_VELOCITY_THRESHOLD` (now calibrated against the corrected velocity signal from E1), `VWAP_CROSS_HWM_PCT` (currently 1.0), `VWAP_BREAK_CONFIRM_TICKS` (currently hand-set 3 — open question whether to tune), bleed-arm clamp endpoints (currently hand-set; recommendation says leave clamp endpoints hand-set).
- **AC-V1.2**: Sweep produces a per-symphony recommendation report comparing current vs proposed values + expected impact on trigger frequency from historical replay.
- **AC-V1.3**: Rollout is per-symphony, operator-gated; no fleet-wide flip.
- **AC-V1.4**: Post-deploy verification via H1's trigger-attribution table — confirm post-tune trigger frequency aligns with the sweep's prediction.

#### V2 — Open-window time gate
- **AC-V2.1**: New `.env` constant `VWAP_OPEN_WINDOW_GRACE_MINUTES=15`. VWAP-Breakdown and VWAP-Bleed-Cut suppress in the first 15 min after `EXECUTION_START_TIME`. TP and Trailing Stop continue to fire.
- **AC-V2.2**: RED tests pin: VWAP-Breakdown does NOT fire in the grace window; TP DOES fire in the grace window; the grace respects a runtime change of `EXECUTION_START_TIME`.

#### V3 — Fleet-decorrelation circuit breaker (observational only)
- **AC-V3.1**: When >50% of active symphonies fire the SAME `triggered_reason` within 3 minutes (both thresholds `.env`-configurable), engine sets `bot_state["fleet_correlation_alert"]` and surfaces a high-visibility dashboard banner.
- **AC-V3.2**: OBSERVATIONAL ONLY — does NOT block triggers, does NOT alter exit decisions. Audit confirmed today's 11-cascade was a real signal (partly amplified by E1, but real); engine must not second-guess.
- **AC-V3.3**: Banner clears automatically after 30 min of no further fleet events; operator can dismiss manually.
- **AC-V3.4**: Detection algorithm reads from H1's `exit_triggers` table.

### TIER 5 — CONCURRENT-TRIGGER + INVESTIGATIONS

#### H2 — Label-only trigger fix (explicit priority resolution at side-effect level)
- **AC-H2.1**: When multiple trigger conditions evaluate True in the same cycle, the engine resolves priority BEFORE executing side effects (`triggered=True`, counter advancement, sell-to-cash, event log). Today all four side-effects execute regardless; only the displayed `triggered_reason` reflects priority.
- **AC-H2.2**: Priority order remains: VWAP Breakdown > Take-Profit > VWAP Bleed Cut > Trailing Stop (per `alpha_bot_execution.py:819-831`). Code makes this explicit at the side-effect dispatch site.
- **AC-H2.3**: H1's telemetry records the resolved trigger, the candidates that were also True, and the gate-state at that moment. This closes the "labeling artifact" risk identified by the audit.
- **AC-H2.4**: RED test: feed a fixture where TP + Trailing Stop + VWAP Breakdown ALL evaluate True; assert only VWAP Breakdown's side effects run; assert telemetry records "VWAP Breakdown (with TP, Trailing Stop also True)".

#### H3 — MC RNG seeding
- **AC-H3.1**: `run_monte_carlo` (`math_engine.py:517`) accepts an optional `seed` parameter. Production cycles seed with a deterministic per-cycle value (e.g. hash of `cycle_id`); tests can pass an explicit seed.
- **AC-H3.2**: `autotuner.py` MC replays seed deterministically so a given trial is bit-for-bit reproducible.
- **AC-H3.3**: RED tests pin reproducibility: same input + same seed → same `mc_prob` output across runs.

#### I1 — Log-time squeeze overfit investigation
- **AC-I1.1**: A research worker (quant-risk-researcher) investigates whether `log10(1 + 9*t)` time-decay has any literature precedent and proposes alternatives grounded in academic / practitioner sources (Heston-style time-of-day, EMA half-life, linear decay, etc.).
- **AC-I1.2**: If a literature-grounded alternative is recommended: scope a follow-up team to A/B-test it against the current curve on the historical fixtures.
- **AC-I1.3**: If the current curve is defensible: document the rationale + retire the open question.

#### I2 — Stop-compounding (PARA + breakeven + time-squeeze) investigation
- **AC-I2.1**: A research worker simulates the compounding behavior in the Kaminski-Lo "stops subtract value under random walk" regime — measure the actual stop tightness across the trading day for current parameter values.
- **AC-I2.2**: Identify the regime / time window where the 8× compounding actually bites; quantify the empirical late-day P&L impact.
- **AC-I2.3**: If material: scope a follow-up team to either (a) cap the compounded tightness, or (b) decouple PARA + breakeven + time-squeeze into a single coherent activation function.

#### I3 — `shadow_hwm` consumption audit
- **AC-I3.1**: A read-only audit determines whether `shadow_hwm` is actually consumed anywhere for live-vs-backtest reconciliation. The methodology review noted it as "genuine asset if consumed."
- **AC-I3.2**: If consumed: document the consumption point + add a regression test.
- **AC-I3.3**: If unused: either remove the field (reduce engine surface) OR surface it for actual reconciliation use.

## Architecture

| WS | Files touched | Team composition |
|----|---------------|------------------|
| E1 | `database.py:140`, `alpha_bot_execution.py` (consumer of `prev_return`), `autotuner.py:94` (replay), tests | Quad: quant-test-writer + risk-engine-specialist + sqlite-specialist + quant-code-reviewer |
| E2 | `alpha_bot_execution.py:698-705`, tests | Trio: quant-test-writer + risk-engine-specialist + quant-code-reviewer |
| H1 | `database.py` (new schema + migration), `alpha_bot_execution.py:936` (write), `app.py` (route + state), `templates/index.html` + `table_partial.html` (UI), tests | Pent: quant-test-writer + risk-engine-specialist + sqlite-specialist + flask-dashboard-specialist + quant-code-reviewer |
| O1 | `autotuner.py` (purge + embargo logic), tests | Quad: quant-test-writer + optuna-specialist + risk-engine-specialist + quant-code-reviewer |
| O2 | `autotuner.py` (deflation math + selection), `database.py` (audit table), tests | Quad: quant-test-writer + optuna-specialist + sqlite-specialist + quant-code-reviewer |
| O3 | `autotuner.py:328`, optimization DB migration script, tests | Trio: quant-test-writer + optuna-specialist + quant-code-reviewer |
| O4 | `autotuner.py:306,399-401`, `database.py:23-25`, tests | Trio: quant-test-writer + sqlite-specialist + quant-code-reviewer |
| O5 | `autotuner.py:219-229`, tests | Quad: quant-test-writer + optuna-specialist + risk-engine-specialist + quant-code-reviewer |
| V1 | `autotuner.py` (sweep), report script, tests | Quad: quant-test-writer + optuna-specialist + risk-engine-specialist + quant-code-reviewer. Operator gates per-symphony rollout. |
| V2 | `math_engine.py` (new gate helper), `alpha_bot_execution.py` (consumer), `.env`, tests | Trio: quant-test-writer + risk-engine-specialist + quant-code-reviewer |
| V3 | `alpha_bot_execution.py` (detection), `app.py` (route + banner state), `templates/index.html` (banner UI), tests | Pent: quant-test-writer + risk-engine-specialist + flask-dashboard-specialist + quant-code-reviewer + ux-expert |
| H2 | `alpha_bot_execution.py:819-831` + side-effect dispatch site, tests | Quad: quant-test-writer + risk-engine-specialist + quant-code-reviewer (+ H1 telemetry must already exist) |
| H3 | `math_engine.py:517`, `autotuner.py` MC replays, tests | Trio: quant-test-writer + risk-engine-specialist + quant-code-reviewer |
| I1 | Research worker only (no code) → conditional follow-up | Solo: quant-risk-researcher (research), optionally Trio TDD if fix scoped |
| I2 | Research worker only (no code) → conditional follow-up | Solo: quant-risk-researcher + risk-engine-specialist (research), optionally TDD if fix scoped |
| I3 | Read-only audit → conditional follow-up | Solo: risk-engine-specialist |

## Sequence (dependency-driven)

1. **E1** — PARA-ARM-at-open fix (engine + autotuner replay). Flags PARABOLIC_VELOCITY_THRESHOLD for retune.
2. **E2** — Trailing-stop monotonicity wire-up.
3. **H1** — Telemetry foundation (observability everything else needs).
4. **O3** — Study name convention (fresh studies — must precede any retune so we don't accumulate stale trials).
5. **O5** — Composite objective replacement (must precede any retune so retunes optimize the right metric).
6. **O1** — Purge + embargo (must precede any retune so train/test is honest).
7. **V1** — Calibration sweep (now with corrected E1 velocity + Tier-3 methodology).
8. **H2** — Label-only trigger fix (so V1 sweep results readable via H1 telemetry are interpretable).
9. **V2** — Open-window time gate.
10. **O2** — Deflated-Sharpe correction at selection.
11. **O4** — Locked-vars consistency.
12. **H3** — MC RNG seeding.
13. **I1** — Log-time squeeze investigation.
14. **I2** — Stop-compounding investigation.
15. **I3** — shadow_hwm consumption audit.
16. **V3** — Fleet circuit breaker (last — depends on H1; nice-to-have once V1+V2 likely already reduced trigger rate).

## Edge Cases

- **E1 cycle-1 baseline**: when persisting yesterday's terminal `current_return` for next-day cycle-1 `prev_return`, handle the "no prior trading day" case (first daemon run, fresh state DB) — sentinel to zero-velocity until 2 observations.
- **E2 stop reset**: when a new position is opened or a position is fully closed, the persisted stop level must reset — verify this transition.
- **H1 telemetry write failure**: the cycle MUST NOT fail if the telemetry write fails. Wrap in try/except with ERROR-level log; continue with the cycle (the trigger semantics matter more than logging the trigger).
- **H1 retention rotation**: never delete during a cycle write; run as a separate scheduled task.
- **O1 fold boundary**: a feature's lookback window may straddle the fold boundary partially — purge by the full lookback length, not partial.
- **O3 migration**: existing studies in the optimization DB must be archived or migrated; document the procedure.
- **V1 per-symphony rollout**: a symphony whose retune flips the trigger frequency dramatically (>2× change) — surface for explicit operator review before deploy.
- **H2 priority when none fires**: the priority table only resolves WHICH trigger to act on when multiple are True. If zero fire, no action. Verify.
- **H3 seeding determinism**: the per-cycle seed must be reproducible across daemon restarts of the same cycle (use a deterministic hash of cycle_id, not the process RNG).

## Security Considerations

- **No new external surfaces** introduced. `GET /api/triggers` is read-only, internal-only (operator dashboard on localhost / LAN).
- **Telemetry table**: no PII; symphony names + IDs are non-sensitive identifiers. Retention bounded.
- **No XSS**: data-driven strings (timestamps, trigger reasons, gate-state JSON) come from the engine's own writes, rendered through Jinja autoescape.
- **No new Composer/Alpaca API surface**: all workstreams operate on existing engine state.
- **AI advisor / Optuna locked-vars (O4)**: the consistency fix is itself a security-class concern — ensures a locked var cannot be silently mutated via the Optuna path.

## Testing Strategy

- **TDD for every workstream** via real Agent Teams (project hard requirement). No solo-implementer approximations.
- **Adversarial RED first** — `quant-test-writer` is the lead for every math-layer workstream.
- **Fixture-first** — use existing captured fixtures (`tests/fixtures/composer/symphony_stats_meta.json`, deterministic state-DB snapshots).
- **ZERO live calls in the test tier** (C2.2 lesson).
- **Live verification after merge** — operator-led, post-restart, on real Composer data. The PM MUST NOT declare a cycle complete on test pass alone (M2-class lesson). For workstreams touching engine math (E1, E2, V1, V2, H2, H3) a market-hours live observation is required to confirm correctness.
- **Cross-workstream baseline** — each PR keeps the baseline pytest count growing (1346+ at this point); each merge confirms the new tests increment.
- **Live-tier integration tests** — `test_live_*.py` gated tests that exercise the live Composer/Alpaca path are written for the highest-risk workstreams (E1, V1, H2) and run on-demand via `/run-tests --include-live`.
- **Trigger-attribution telemetry (H1) is the operator-facing verification tool for every subsequent workstream** — V1, V2, V3, H2, E1 all use H1's table to verify post-deploy behavior matches the test-tier prediction.

## Decisions

| Decision | Rationale |
|----------|-----------|
| Single comprehensive plan vs. multiple smaller ones | Cross-workstream dependencies (E1 retune blocked by O1+O3+O5; V1 blocked by E1+H1+O*; H2 blocked by H1; V3 blocked by H1) require sequencing discipline that's clearer in one plan. |
| Supersedes `feature-plans/vwap-remediation.md` | The five VWAP workstreams are reorganized into this plan as W1 (telemetry, now H1), V1 (calibration, now sequenced after Tier-3 methodology), V2 (open-window), V3 (circuit breaker), and W5 (PARA investigation, now an EMERGENCY E1). |
| EMERGENCIES first (E1, E2) | Real-money exposure live on main. E1 is the root cause of today's 11-symphony PARA-ARM cascade; E2 means trailing stops can drop tick-to-tick. |
| Methodology fixes (Tier 3) BEFORE V1 retune | Retuning against a broken walk-forward + ad-hoc objective re-introduces the same calibration overfit the audits flagged. We fix the optimization process, then re-run the optimization. |
| H1 telemetry BEFORE H2 (label-only fix) | H2's RED test asserts telemetry records the resolved trigger + the candidates. H1 must exist first. |
| V3 (fleet circuit breaker) is OBSERVATIONAL only | Today's 11-cascade was a real signal (partly amplified by E1, but real). The breaker surfaces; the operator decides. |
| Investigations (I1, I2, I3) split as research → conditional fix | These are open methodological questions, not confirmed defects. Fix scope depends on the verdict, so research runs first. |
| Live verification mandatory after engine workstreams | The M2-class lesson — test pass ≠ live correctness. Specifically for E1/E2/H2 the operator AND the PM verify on live data. |

## Scope Boundaries

**IN:**
- All 14 workstreams above, in the sequence specified.
- Each workstream is operator-gated for dispatch (PM doesn't auto-launch the next workstream).
- The math layer itself stays correct — these workstreams fix WIRING, METHODOLOGY, OBSERVABILITY, and TUNING, not the formulas (which the panel confirmed correct).

**OUT:**
- Replacing the multi-trigger architecture with single-rule logic.
- Adding new exit-trigger types beyond TP / Trailing Stop / VWAP Breakdown / VWAP Bleed.
- Changing the `EXECUTION_START_TIME` default.
- Cross-symphony aggregation changes (portfolio-level signals).
- Rewriting the math formulas (they're correct).
- Daemon singleton (already shipped via `b7b6b0f`).
- Restart.ps1 mechanics (already shipped).
- Hot reload / observability for app.py changes (separate concern).
- Math-engine layer extensions (e.g. new risk indicators) — separate `/scaffold` if needed.
