# Math Remediation Program — Charter
Status: program-charter (per-phase /scaffold plans follow, one per cycle)
Basis: `docs/audit/math-audit/VERDICT.md` @ 248d1f3a (DE-MATH-AUDIT-001) — 3 CRITICAL / 9 HIGH / 14 MEDIUM confirmed findings.
Design authority (operator delegation, 2026-07-17, verbatim in the PM ledger): **domain correctness — "the answers have right answers for a trading and quant analysis platform"; operator intent is not the arbiter.**

## Standing rulings folded into this program
- **QUESTION-01 → BUG.** Trailing-stop disarm keys on genuine recovery (risk metric back below the arm band, with hysteresis) — never on deterioration. (MA-4; fixed in R3, after the optimizer is honest, because the mandatory retune is only meaningful then.) **FIXED (math-r3b cycle, `DE-MATH-R3B-001`, 2026-07-18):** `math_engine.compute_arm_disarm_decision` disarms only on genuine recovery (`prob_underperforming` back below `TAKE_PROFIT_MC_PCT`) sustained for `DISARM_CONFIRM_TICKS=3` consecutive ticks, shared structurally by production and the autotuner replay; r3b-review's independent review APPROVED (zero blocking findings). Pending the PM's live E2E before merge/deploy. **SHIPPED @ `origin/main` `f3c7e050`** (PR #101, CI green) + droplet-deployed + verified 2026-07-18 (daemon restarted clean, WAL mtime advanced, `LIVE_EXECUTION=False`); the PM's live E2E PASSED first-hand (giveback exits `"Trailing Stop"`; a faithful reproduction of the old code's inverted disarm never exits) — supersedes the "Pending the PM's live E2E" note in this line.
- **QUESTION-02 → PAIRED.** Candidate adoption tests the candidate-minus-incumbent daily difference (paired), FDR-corrected across candidates. (MA-8; R4.)
- **P2 → real gap.** Rare-fire tail hedges are valued on conditional/stress windows; unconditional short-window gating cannot see them. (R4 design-research.)
- 6-symphony generation pass: weekly schedule (no manual budget burn).

## Phases (dependency-ordered per the verdict; each phase = its own /scaffold plan + real TDD team + full gates)

### R0 — Advisory-side quick wins (advisory path: FF after gates; no engine/autotuner diff)
- **MA-3 + M2** (CRITICAL): divide-by-100 at the gate-engine boundary + gamma aligned to frozen THEORY 2.0 — restores the overfitting veto for strategy-builder/asset-swaps/logic-changes. One boundary fix, golden-fixture tested.
- **MA-6 + MA-7** (HIGH): per-symphony Performance/Risk-Profile series sourced from `shadow_history` per-day rows; fallbacks scope-gated with honest empty states.
- Ride-alongs (MED, same surfaces): ma-perf 03 (one window definition per picker click, calendar vs trading unified), 05 (vol delta color inversion), 06 (Detail column single semantic).
- Prereq: the frontrunner branch ship (already authorized; blocked on the operator's permission click).

### R1 — Replay fidelity (trade-touching: PR + /review + PM live E2E)
- **MA-1** (CRITICAL): stamp per-tick `last_percent_change` into replay holdings before every `run_monte_carlo` call.
- **MA-10** (HIGH): add production's fail-open arming to `_replay_exit_tick`.
- **F5** (MED): pass regime-conditional `exit_confirm_ticks` into the replay.
- **F6** (conditional): resolve after the droplet `EXECUTION_START_TIME` check (phase-2 item 4).
- Acceptance heart: a replay-vs-production parity battery — same inputs, same decisions, tick-for-tick on canned days.

### R2 — Honest validation statistics (trade-touching: PR path)
- **MA-2** (CRITICAL): CPCV paths score genuinely disjoint test folds (consume the fold structure as designed) — or explicitly revert to the honest purged single-fold split.
- **MA-5** (HIGH): the adoption cascade's "OOS validation" evaluated on data outside the selection window; kill the `purge_integrity_ok=True` false attestation.
- **MA-9** (HIGH): frozen-eval produces a real CRRA-EU metric that is reported and gateable.
- **M1** (quantstats input convention: one documented producer convention, converted at the consumer boundary) — moved in from R4 (`feature-plans/math-r2.md` Decision: "M1 folded into R2 | Same statistics-honesty theme, small boundary fix, keeps R4 pure advisor-classification work"; see `DE-MATH-R2-001`).
- Exit criterion: a nightly run whose selection/adoption numbers are demonstrably out-of-sample (probe harness kept as a regression test).

### R3 — Live-path behavior corrections + retune (trade-touching: PR path; HARD-GATED on R1+R2)
- **MA-4** (HIGH, ruled bug): disarm-on-recovery with hysteresis; blast-radius review of every arm/disarm consumer; behavioral fixture battery from the audit's probe scenarios (slow-giveback days MUST exit). **FIXED (math-r3b cycle, `DE-MATH-R3B-001`, 2026-07-18):** shipped on `fix/math-r3b` — disarm-on-recovery-with-hysteresis via the shared `math_engine.compute_arm_disarm_decision` seam (production + autotuner replay, structural parity); the giveback golden (arms at peak → deteriorates to `prob>=60` → return gives back to loss → MUST exit "Trailing Stop") is GREEN; r3b-review's blast-radius/correctness/parity review APPROVED, zero blocking findings. Pending the PM's live E2E before merge/deploy — MA-11 (below) and the retune remain. **SHIPPED @ `origin/main` `f3c7e050`** (PR #101, CI green) + droplet-deployed + verified 2026-07-18 (daemon restarted clean, WAL mtime advanced, `LIVE_EXECUTION=False`); the PM's live E2E PASSED first-hand (giveback exits `"Trailing Stop"`; a faithful reproduction of the old code's inverted disarm never exits) — supersedes the "Pending the PM's live E2E" note above.
- **MA-11** (HIGH): wire `MAX_SQUEEZE_FLOOR` as the post-squeeze lower clamp (its design intent) or remove it from UI/advisor/allowlist; re-examine the [0.1, 0.8] squeeze search range once non-inert. **FIXED (math-r3c cycle, `DE-MATH-R3C-001`, 2026-07-18):** `MAX_SQUEEZE_FLOOR` (previously a dead knob -- assigned, never consumed) is wired as the post-squeeze lower clamp on the trailing-stop DISTANCE via an optional `squeeze_floor` param on `math_engine.compute_active_trailing_stop`, scoped inside the squeeze branch with a no-widening invariant, shared structurally by production and the autotuner replay; r3c-review's independent blast-radius/correctness/parity/no-widening review APPROVED (zero blocking findings, 8 gates, 7 independent reproductions); the PM's live E2E PASSED (old exits the noise dip, new survives it, both exit the genuine breakdown dip) and the operator before/after was presented pre-deploy. Pending merge to `origin/main` + droplet deploy -- the [0.1, 0.8] `MAX_PARABOLIC_SQUEEZE` range re-examination remains R3-d's, not this cycle's.
- **Full retune** on the now-honest optimizer; parameter rollout with before/after comparison to the operator.

### R4 — Advisor methodology (advisory path)
- **MA-8** (paired-difference adoption test per QUESTION-02) + **MA-12** (no-op pre-screen: series-diff/never-fires check with its own rejection_reason — recovers the ~32% wasted generation spend).
- **M3** (SPY fold calendar alignment), **M5** (bootstrap validity floor behavior at m=0). *(M1 moved to R2 — see DE-MATH-R2-001.)*
- **P2 design research**: conditional/stress-window evaluation for tail hedges (researcher phase before any A/C).

### Cross-cutting backlog (scheduled opportunistically, tracked here)
- Builder observability: INFO-level outcomes surfaced (the audit itself was blinded twice by this); LOUD billing-failure marker; generation-degradation UI marker.
- Cost guards: run-level generation budget cap; probe-scale-first as standing practice; sub-billing (`claude -p` OAuth) A/C for builder generation (operator green-light pending).
- LOW/INFO audit items (F9–F15, ma-perf 08–14, ma-stats L1–L4) — folded into whichever phase touches their file, never fixed blind (L1's doc↔code tie-adopt contradiction is load-bearing: fix the DOC, not the code, until R2 lands).

## Phase-2 droplet checks (read-only; blocked on operator SSH approval)
1. Post-mortem vintage (`if_held_source` marker scan) → determines whether History/$-saved aggregates need droplet regeneration.
2. `shadow_history` day-count + `exit_triggers` spread → is the strip-fallback defect rendering now?
3. One rendered $-saved + one windowed guard-alpha recomputed from raw droplet rows.
4. `EXECUTION_START_TIME` value → resolves F6.
5. Composer backtest default window pin (one live call) → closes the identical-CAGR dormancy bound.
6. (MAPERF-15) `symphony_live_mode` states + any live trigger days → does $-saved book ~$0 for live-sold positions? **RESOLVED (math-r2 cycle, out-of-band probe):** `last_percent_change` tracks post-sale logic, not the sold basket — the ≈$0-saved fear is refuted; `DE-GUARD-ALPHA-SAVED-001`'s design stands. See `docs/research/composer/maperf15-post-sale-lpc-semantics.md` and `DE-MATH-R2-001`. **Tripwire SHIPPED (math-f7 cycle, `DE-MATH-F7-001`, 2026-07-18):** the passive staleness tripwire this item deferred (`MAPERF15_STATIC_LPC_CYCLES=30`, log-only, never gates, market-hours-gated) landed in `alpha_bot_execution.py` alongside F7's honest post-trigger MC display fix -- this checklist item is now fully closed, not merely resolved-in-principle.

## Process law (unchanged)
Every phase: /scaffold plan committed → real Agent Team (Toxic Pair TDD + reviewer + doc-gen) → targeted `-n0` batteries + credential-less + ruff → PM first-hand live E2E → trade-touching phases via PR + /review to origin; advisory phases FF after gates. No phase builds on unshipped work.
