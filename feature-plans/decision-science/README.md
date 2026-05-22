# Decision-Science Roadmap — Feature-Plan Index

**Owner (coordinator):** risk-architect (`risk-engine-specialist`), decision-science-scaffold team.
**Status:** Scaffold — TDD-cycle-sized plans. **This folder ships PLANS only.** A separate downstream effort executes them under the project's Agent-Teams TDD discipline.
**Branch:** `plan/finalist-a-scaffold` (forked from `origin/main` with the v3 evidence base cherry-picked at `413a806`).

---

## 0. Read-this-first

This roadmap implements the **user-ratified verdict** of the Decision-Science Council (`docs/handoff/decision-science-council-synthesis.md`) and the v3-and-divergence evaluation (`docs/handoff/decision-science-v3-and-divergence-evaluation.md`). The verdict — *"harden now; migrate only on evidence — and the evidence may never arrive"* — translates into four work-streams:

- **Phase 1 — HARDEN-core floor.** M1 (CRRA-EU autotuner objective) + M2 (CVaR diagnostic) + the overfitting-accounting and provenance spine. A **complete, terminal-acceptable architecture** — if the user stops permanently here, that is full success.
- **Phase 1.5 — fast-follow on its own TDD cycle.** M3 (re-derive R1 time-squeeze decay curve + R2 VWAP System-A HWM gate) under the S-1 two-stage parity gate with a **committed per-cycle attribution table**.
- **Phase 2 — Finalist B, evidence-gated.** Scaffolded NOW per the user's directive. **May never unlock.** Four preconditions (synthesis §5.1) gate execution; one (precondition d — a powered validation design) **may be structurally unsatisfiable**.
- **Engine-audit work-stream — parallel to all phases.** Diagnosis-only audits feeding remediation tasks back into the cycles.

**User-ratified binding context (every plan honors these — listed once here, NOT repeated in plan bodies):**

1. **Finalist A is the adopted design.** The "literal replace" scope word is reinterpreted as "harden now; replace-as-co-signal later only if evidence earns it."
2. **§6.2 CVaR-budget reframing accepted.** 5% CVaR ships as a diagnostic + at most a co-signal, **never** a calibrated live budget.
3. **Regime & Decision Narrator advisor role is Phase-2-conditional.** Structurally inapplicable at Phase 1 (no drift to narrate).
4. **"EUT+CVaR" ships as "CVaR-with-risk-aversion-shaping."** No literal `E[U(exit)]` vs `E[U(hold)]` crossover layer; gamma shapes the CVaR thresholds.

### 0.1 Why two finalists, not three (H-9 — the Finalist-C exclusion argued in-body)

The evaluation flagged v3 as having argued the Finalist-C exclusion **only** in its §10 compliance index, never in its body. Per H-9 disposition, the argument is restated here once, in the roadmap that the implementing team actually reads — the place a reader of the two-finalist structure naturally looks:

**There is no coherent standalone third finalist.** The only candidate "third path" — pre-committing to the evidence-gated Phase-2 roadmap — is **not a separate architecture; it is Finalist B**. Finalist A is the terminal-acceptable floor; Finalist B is *Finalist A plus the evidence-gated Phase-2 roadmap*; "pre-commit to Phase 2" is therefore a **choice within Finalist B's framing**, not a distinct third architecture. The genuine decision space is **two finalists plus the user's pre-commit choice** — and the user already made that choice (scaffold Phase 2 now, evidence-gate execution). So the roadmap structure is two finalists, with Phase 2's gating preconditions making the user's pre-commit visible and reversible.

A second candidate sometimes raised — *"Finalist A + a permanent diagnostic-grade CVaR layer that never moves money"* — collapses into Finalist A by inspection: M2 already ships that diagnostic-grade layer (see synthesis §3.1 and `phase-1/m2-cvar-diagnostic/plan.md`). It is not a separate architecture; it is what Finalist A delivers.

---

## 1. Cross-cutting hazards (binding for EVERY relevant plan's hazard callouts)

The downstream builder must SEE these in every relevant plan and CANNOT miss them:

| Hazard | Source | Binding statement |
|---|---|---|
| **NN1 spec-freeze (★ load-bearing)** | synthesis §2.5 verbatim | Generator family + horizon convention + every newly-frozen facet (gamma, utility family, wealth argument, M3 curves, M2 alpha/window, block length, regime features, hysteresis thresholds, lambda) MUST be frozen by THEORY / MANDATE / STYLIZED_FACT — **NEVER** by strategy P&L. P&L-frozen = uncounted test event = BHY haircut a lie by omission. `spec_facets.freeze_discipline = 'BACKTEST_SELECTION'` is forbidden for these facets. |
| **BHY haircut integrity** | synthesis §2.2 | `N_effective = N_optuna + S` (additive). Yekutieli c(N) preserved. The CRRA-EU per-trial t-stat is the genuine one-sample `mean(U)/(sd(U)/√T)` (S-2) — NEVER `effect_size·√T` (H-6 category error precedent at `autotuner.py:266-271`). Phase-2 paths' multi-testing T is **independent tail-obs count**, NEVER the path count. |
| **Replay-determinism stack** | F-2 ★, M-2 ★ | Phase 1 = **1 anchor** (M2's CVaR off the `cycle_id`-seeded kNN pool). Phase 2 = 5 anchors (`tier1_seed`, calibration, history fingerprint, spec bundle hash, hysteresis-state snapshot). Every stochastic site uses `np.random.default_rng(seed)` — NEVER the numpy global RNG (D-3 ★). |
| **MC sentinel discipline (F-4 ★)** | `math_engine.py:71-74`, project memory `project_mc_sentinel_consumer_blast_radius` | `MC_INSUFFICIENT_HISTORY_SENTINEL = None` is out-of-band; mirrored by `CVaRAssessment.cvar_pct = None` and `ForwardPathBundle.paths = None`. The protective stop ALWAYS fires on ticks-below-stop alone in every sentinel-triggered branch. The new core NEVER disables the safety floor. |
| **`run_monte_carlo` blast radius (G-1 ★, H7)** | memory `project_mc_sentinel_consumer_blast_radius` | `run_monte_carlo`'s signature is **frozen** through both phases until the LAST symphony cuts over. 7+ consumers across `alpha_bot_execution.py`, `reporting.py`, `synthetic_history.py`, `autotuner.py`. The Phase-2 forward-path simulator is a **NET-NEW function** `simulate_forward_paths` (G-2 ★ NOT a "parameter change"). |
| **Advisor scope-boundary integrity (I-1/I-2/I-3 ★, M-1 ★)** | synthesis §3.6, attack rubric Family I + M | All 4 Advisor roles are **read-only**. The Advisor's data-access layer is **structurally** walled from `fold_role = 'frozen_eval'` (the `COALESCE(fold_role,'') != 'frozen_eval'` filter — H3 SQL-NULL trap). The dashboard is read-only (architecture constraint 2). The Regime & Decision Narrator is **Phase-2-conditional**. |
| **Two-DB boundary (E-2 ★)** | architecture constraint 3 | All decision-science migrations land in the **state DB**. Zero optimization-DB migrations. The haircut copies the `D_spec` count from state DB into the autotune run — never cross-joins. |
| **Layered exit logic preserved (G-3, project anti-pattern)** | risk-engine charter | The 6 incumbent layers (vol-scaling, time-squeeze, parabolic ratchet, breakeven, VWAP×2, MC) are **retained as permanent safety floor**. `resolve_trigger_priority` is **kept and extended, never replaced, never collapsed into a single condition**. The Phase-2 CVaR co-signal is the 5th input — appended LAST, structurally cannot solely fire an exit. |
| **Architecture constraint 1 (no blocking I/O on execution path)** | project CLAUDE.md | M2's per-cycle `INSERT` is "zero **decision** impact, non-zero non-blocking I/O cost" (H-3 wording fix) — routed through the H4 `live|replay` telemetry helper, benchmarked vs minute budget. Phase-2 Tier 1 is out-of-band; Tier 2 is light array reduction only. **GARCH MLE is the binding concern** — never per-cycle. |
| **`is_live` explicit (F-1 ★, architecture constraint 4)** | project CLAUDE.md | No new code path can reach `submit_order` / `place_order` / `cancel_order` / `liquidate` without an explicit `is_live=True` flag check. Default-False everywhere. |
| **Fixture-provenance non-circular (D-2 ★)** | global rule `feedback_verify_backend_contract_before_fixtures` | Every golden fixture is captured-from-producer OR schema-derived with a runtime validator OR producer-owner-signed-off. **NEVER** authored alongside the same code under test (parser+fixture co-design — automatic Gate-1 fail). |
| **CVaR-divergence REJECT (§B binding)** | evaluation §B.6 | The operator-optional second-window residue is allowed; it surfaces TWO honest CVaR numbers each under its own S-3 contract. **NO signed-divergence quantity is ever persisted, displayed, or surfaced.** Schema tripwire test forbids `cvar_divergence` and `regime_recency_weight` columns. Disjoint baseline preferred (§B.5). |
| **S-3 four-part display contract for M2** | synthesis §3.1 + §4 + H-2 | M2's diagnostic display carries ALL FOUR of: (a) stderr **on the distinct-tail-observation count** (~7-8, NEVER the resample count 5000 per H-2); (b) `n_tail`; (c) the literal "diagnostic, not a signal — do not trade on this" label; (d) the literal **bias warning** "this CVaR estimate is a known-low-biased LOWER BOUND on tail severity." Element (d) load-bearing. |
| **S-2 re-derived t-stat (binding correctness)** | synthesis §4 + H-1 | `compute_crra_eu_tstat(U) = mean(U)/(sd(U)/√T)` replaces `compute_sortino_tstat` for the CRRA-EU objective. The H-6 category-error precedent at `autotuner.py:266-271` is in scope. |
| **W-H4 `WEALTH_ARG_FLOOR > 0` on input W (H-1 fix)** | evaluation §A.1 | CRRA is unbounded below; "bounded" was false in v3. The floor is a named module-scope constant on the **input wealth argument W**, **NEVER** on the output utility U (flooring U inflates the t-stat — anti-conservative). |
| **H1 migration 022 dual-write** | migration plan §6 H1 | Migration 022's 9 new `autotune_runs` columns dual-written to BOTH the `ALTER` SQL AND `init_db()`'s `CREATE TABLE autotune_runs`. The duplicate-column swallow at `database.py:770-779` reconciles overlap. Omitting either → one population silently lacks columns. |
| **K-1 ★ — Gate-1 named committed artifact** | attack rubric K-1 | "We ran a backtest and it looked fine" KILLS. Bit-level reference required. **Prose summary fails K-1; the per-cycle attribution table passes it** (S-1 binding). Parity asserted on the decision record, **excluding** `id` (autoincrement) and `ts_utc` (wall-clock) per H-8 A3. |

---

## 2. The PLANS — sequenced by dependency

Phase numbering is the user-facing roadmap. Within a phase, plans run in TDD-cycle order (RED before GREEN). Cross-phase blocks are explicit.

### Phase 1 — HARDEN-core floor (recommended terminal-acceptable architecture)

#### 1.A — Persistence + accounting spine (migrations + ledgers)

These five plans are the **defensibility deliverable**. They land FIRST and the M1/M2 work consumes them.

| Plan | Owner-domain | Depends on |
|---|---|---|
| `phase-1/016-spec-bundles/plan.md` | persistence-architect | nothing (foundation) |
| `phase-1/017-advisor-observations/plan.md` | persistence-architect | 016 |
| `phase-1/018-researcher-dof-ledger/plan.md` | persistence-architect | 016 |
| `phase-1/019-fold-role-columns/plan.md` | persistence-architect | nothing |
| `phase-1/h4-telemetry-helper/plan.md` | persistence-architect | nothing (foundation) |
| `phase-1/advisor-frozen-eval-wall/plan.md` | persistence-architect | 019 |
| `phase-1/test-frozen-eval-wall-tripwire/plan.md` | quant-test-writer | advisor-frozen-eval-wall |
| `phase-1/nn1-spec-freeze-discipline/plan.md` | tuning-architect | 016, 018 |
| `phase-1/n-effective-additive-accounting/plan.md` | tuning-architect | 018, 020 |
| `phase-1/spec-bundles-dof-ledger-integration/plan.md` | tuning-architect | 016, 018, 020 |
| `phase-1/mc-sentinel-blast-radius/plan.md` | critic / risk-architect | nothing |
| `phase-1/replay-determinism-anchor/plan.md` | critic | h4 helper |
| `phase-1/shadow-logging-pattern/plan.md` | persistence-architect | h4 helper |
| `phase-1/logging-redaction/plan.md` | critic | nothing |
| `phase-1/live-vs-replay-safety-boundary/plan.md` | critic | h4 helper |
| `phase-1/dashboard-side-effect-ban/plan.md` | flask-dashboard-specialist | nothing |

#### 1.B — M1 (CRRA-EU autotuner objective — R3 closure)

| Plan | Owner-domain | Depends on |
|---|---|---|
| `phase-1/020-autotune-runs-eut/plan.md` | persistence-architect | 016, 018, h1 dual-write |
| `phase-1/m1-crra-eu-objective/plan.md` | tuning-architect | 016, 018, 020 |
| **`phase-1/m1-crra-eu-autotuner-objective/plan.md`** *(risk-architect — canonical integration plan)* | risk-architect | 016, 018, 020 |
| `phase-1/m1-crra-eu-tstat/plan.md` (S-2) | tuning-architect | 020 |
| `phase-1/m1-bhy-haircut-preservation/plan.md` | tuning-architect | m1-crra-eu-tstat |
| `phase-1/red-test-1-crra-tstat-pin/plan.md` (§8 Test 1; S-2 + H-7) | quant-test-writer | m1-crra-eu-tstat |
| `phase-1/red-test-2-m1-wealth-argument/plan.md` (§8 Test 2; W-H2 + W-H4) | quant-test-writer | m1-crra-eu-objective |
| `phase-1/red-test-4-replay-determinism/plan.md` (§8 Test 4) | quant-test-writer | replay-determinism-anchor |

**Cross-review note (coordinator):** there are three M1-domain plans (`m1-crra-eu-objective`, `m1-crra-eu-tstat`, `m1-bhy-haircut-preservation`, plus the integration plan `m1-crra-eu-autotuner-objective`). Each focuses on a distinct slice; the cross-review phase will confirm they decompose cleanly along the slices and the integration plan binds them.

#### 1.C — M2 (CVaR diagnostic — operator instrumentation per H-4 re-label)

| Plan | Owner-domain | Depends on |
|---|---|---|
| `phase-1/021-cvar-diagnostics/plan.md` | persistence-architect | h4 helper |
| **`phase-1/m2-cvar-diagnostic/plan.md`** *(risk-architect — canonical integration plan, includes §B second-window residue)* | risk-architect | 021, h4 helper, mc-sentinel-blast-radius |
| `phase-1/second-window-residue/plan.md` (§B operator-optional) | risk-architect / persistence-architect | m2-cvar-diagnostic, 021 |
| `phase-1/red-test-3-m2-cvar-known-pool/plan.md` (§8 Test 3; H-2) | quant-test-writer | m2-cvar-diagnostic |
| `phase-1/test-m2-stderr-correctness/plan.md` (H-2 standalone) | quant-test-writer | m2-cvar-diagnostic |
| `phase-1/test-m2-write-latency/plan.md` (H-3 benchmark) | quant-test-writer | m2-cvar-diagnostic, h4 helper |

#### 1.D — Phase-1 validation gates

| Plan | Owner-domain | Depends on |
|---|---|---|
| `phase-1/gate-1-replay-parity/plan.md` (Gate 1) | critic | M1, M2 GREEN |
| `phase-1/test-gate1-backtest-replay-parity/plan.md` | quant-test-writer | gate-1-replay-parity |
| `phase-1/gate-2-live-shadow-quality/plan.md` (Gate 2 — diagnostic-quality, NOT trigger behavior) | critic | M2 GREEN |
| `phase-1/test-gate2-shadow-diagnostic-quality/plan.md` | quant-test-writer | gate-2-live-shadow-quality |

### Phase 1.5 — M3 (R1 + R2 re-derivation under S-1 two-stage parity gate)

| Plan | Owner-domain | Depends on |
|---|---|---|
| `phase-1.5/schema-deltas-for-m3/plan.md` | persistence-architect | spec_bundles (Phase 1) |
| `phase-1.5/s1-two-stage-parity-gate/plan.md` (the S-1 harness) | quant-test-writer | Phase 1 GREEN |
| `phase-1.5/m3-two-stage-parity-gate-autotuner-side/plan.md` (autotuner-side support) | tuning-architect | s1 |
| `phase-1.5/red-test-5-s1-parity-gate/plan.md` (§8 Test 5) | quant-test-writer | s1 |
| **`phase-1.5/m3-redrive-provenance-gaps/plan.md`** *(risk-architect — the curve work)* | risk-architect | s1, schema-deltas |

### Phase 2 — Finalist B, evidence-gated (may never unlock)

**Gating preconditions** (synthesis §5.1) — ALL FOUR must PASS in writing before any Phase-2 plan executes:

- (a) M2 evidence does NOT show gross uninformativeness AND a separately-powered discriminating test becomes constructible (per H-4: M2 can only KILL Phase 2, never advance it).
- (b) Gate-zero tail-data audit (sub-5% tail observations per regime cluster are sufficient).
- (c) Latency + bucket arithmetic (B-2/B-3): measured prototype proves Tier-1 pre-open batch finishes with margin AND regime buckets are populous.
- (d) A powered validation design exists OR the trigger ships diagnostic-grade-permanent. **May be structurally unsatisfiable.**

| Plan | Owner-domain | Depends on |
|---|---|---|
| `phase-2/binding-preconditions/plan.md` | critic | Phase-1 + Phase-1.5 GREEN; engine-audit findings closed |
| `phase-2/025-shadow-decisions/plan.md` | persistence-architect | preconditions PASS |
| `phase-2/026-path-generator/plan.md` | persistence-architect | preconditions PASS |
| `phase-2/027-decision-core-state/plan.md` | persistence-architect | preconditions PASS |
| **`phase-2/tier1-seed-determinism/plan.md`** *(risk-architect)* | risk-architect | 026, h4 helper |
| `phase-2/test-tier1-seed-determinism/plan.md` | quant-test-writer | tier1-seed-determinism |
| **`phase-2/simulate-forward-paths/plan.md`** *(risk-architect — NET-NEW function)* | risk-architect | tier1-seed-determinism, 026 |
| **`phase-2/cvar-cosignal-hysteresis-trigger/plan.md`** *(risk-architect)* | risk-architect | simulate-forward-paths |
| `phase-2/test-cvar-cosignal-hysteresis/plan.md` | quant-test-writer | cvar-cosignal-hysteresis-trigger |
| `phase-2/abstain-failsafe-coverage/plan.md` | critic | cvar-cosignal-hysteresis-trigger, simulate-forward-paths |
| `phase-2/test-abstain-fail-safe/plan.md` | quant-test-writer | abstain-failsafe-coverage |
| **`phase-2/priority-resolver-cvar-cosignal/plan.md`** *(risk-architect — resolver extended additively)* | risk-architect | cvar-cosignal-hysteresis-trigger; **gated by engine-audit/priority-resolver-ordering-audit** |
| `phase-2/lambda-frozen-by-mandate/plan.md` | tuning-architect | nothing |
| `phase-2/gamma-2d-search-space/plan.md` | tuning-architect | M1 |
| `phase-2/fold-structure-nn2-narrowed/plan.md` | tuning-architect | nothing |
| `phase-2/multi-testing-tail-obs-accounting/plan.md` (T = independent tail-obs, NEVER path count) | tuning-architect | nothing |
| `phase-2/joint-var-es-coverage-backtest/plan.md` (Acerbi-Székely / Fissler-Ziegel) | critic / tuning-architect | preconditions PASS |
| `phase-2/test-joint-var-es-coverage-backtest/plan.md` | quant-test-writer | joint-var-es-coverage-backtest |
| `phase-2/test-operator-second-window/plan.md` | quant-test-writer | second-window-residue (Phase 1) |

### Engine audit — parallel work-stream (diagnosis only, no code)

The audits run alongside the cycles. Findings feed remediation tasks back into the cycles. **NO code ships from any audit plan.**

| Plan | Owner-domain | Runs after |
|---|---|---|
| `engine-audit/math-engine-end-to-end-audit/plan.md` (NaN/Inf + sentinels + magic numbers) | risk-architect / critic | Phase 1.5 GREEN |
| `engine-audit/priority-resolver-ordering-audit/plan.md` | risk-architect | Phase 1.5 GREEN; gates Phase-2 resolver extension |
| `engine-audit/abstain-failsafe-coverage-audit/plan.md` | critic | Phase 1.5 GREEN |
| `engine-audit/live-execution-path-latency-audit/plan.md` (Phase-2 entry-gate B-2/B-3) | risk-architect / critic | Phase 1.5 GREEN; gates Phase 2 |
| `engine-audit/live-vs-replay-stack-audit/plan.md` | critic | Phase 1.5 GREEN |
| `engine-audit/audit-live-vs-replay-determinism/plan.md` | critic | Phase 1.5 GREEN |
| `engine-audit/replay-determinism-coverage/plan.md` | critic | Phase 1.5 GREEN |
| `engine-audit/audit-property-based-invariants/plan.md` | critic | Phase 1.5 GREEN |
| `engine-audit/audit-test-coverage-gaps/plan.md` | critic | Phase 1.5 GREEN |
| `engine-audit/fixture-provenance-coverage-audit/plan.md` | critic | Phase 1.5 GREEN |
| `engine-audit/h1-divergence-audit/plan.md` (migration vs init_db) | persistence-architect | Phase 1 GREEN |
| `engine-audit/full-schema-audit/plan.md` | persistence-architect | Phase 1 GREEN |
| `engine-audit/backup-restore-strategy/plan.md` | persistence-architect | Phase 1 GREEN |
| `engine-audit/composer-alpaca-client-safety-audit/plan.md` | composer-alpaca-integration | Phase 1 GREEN |
| `engine-audit/dashboard-read-only-enforcement-audit/plan.md` | flask-dashboard-specialist | Phase 1 GREEN |
| `engine-audit/reporting-pipeline-audit/plan.md` (Discord + QuickChart) | composer-alpaca-integration | Phase 1 GREEN |
| `engine-audit/sampler-choice/plan.md` (Optuna TPE) | tuning-architect | Phase 1 GREEN |
| `engine-audit/pruner-choice/plan.md` (Optuna pruner) | tuning-architect | Phase 1 GREEN |
| `engine-audit/study-persistence-versioning/plan.md` | tuning-architect | Phase 1 GREEN |

---

## 3. Verification — every Finalist-A binding item from the synthesis is covered

| Synthesis binding item | Plan(s) covering it |
|---|---|
| **M1** — CRRA-EU offline objective replacing 5 loss-aversion constants | `phase-1/m1-crra-eu-autotuner-objective/plan.md` (canonical) + `phase-1/m1-crra-eu-objective/plan.md` (slice) |
| **M2** — CVaR diagnostic off existing kNN pool | `phase-1/m2-cvar-diagnostic/plan.md` (canonical) + `phase-1/021-cvar-diagnostics/plan.md` (migration) |
| **M3** — R1 time-squeeze + R2 VWAP System-A re-derivation | `phase-1.5/m3-redrive-provenance-gaps/plan.md` (curves) + `phase-1.5/s1-two-stage-parity-gate/plan.md` (harness) |
| **S-1** — two-stage parity gate + per-cycle attribution table | `phase-1.5/s1-two-stage-parity-gate/plan.md` + `phase-1.5/red-test-5-s1-parity-gate/plan.md` |
| **S-2** — re-derived t-stat `compute_crra_eu_tstat` | `phase-1/m1-crra-eu-tstat/plan.md` + `phase-1/red-test-1-crra-tstat-pin/plan.md` |
| **S-3** — four-part display contract | `phase-1/m2-cvar-diagnostic/plan.md` §S-3 section + `phase-1/test-m2-stderr-correctness/plan.md` |
| **W-H2** — wealth-argument derivation | `phase-1/m1-crra-eu-autotuner-objective/plan.md` §W-H2 + `phase-1/red-test-2-m1-wealth-argument/plan.md` |
| **W-H4 — `WEALTH_ARG_FLOOR`** (H-1 fix) | `phase-1/m1-crra-eu-autotuner-objective/plan.md` §H-1 |
| **H-2 — stderr on distinct-tail count** | `phase-1/m2-cvar-diagnostic/plan.md` §H-2 + `phase-1/test-m2-stderr-correctness/plan.md` |
| **H-3 — non-blocking I/O cost, not "zero"** | `phase-1/m2-cvar-diagnostic/plan.md` §H-3 + `phase-1/test-m2-write-latency/plan.md` + `engine-audit/live-execution-path-latency-audit/plan.md` |
| **H-4 — M2 re-label (operator instrumentation; kill-switch only for Phase 2)** | `phase-1/m2-cvar-diagnostic/plan.md` §H-4 |
| **H-5 — Phase-1 floor removes R3 only** | `phase-1.5/m3-redrive-provenance-gaps/plan.md` (doc-sweep correction) |
| **H-6 — serial-correlation residual W-H5** | `phase-1/m1-crra-eu-autotuner-objective/plan.md` §H-6 (disclose-and-accept) |
| **H-7 — PINS, not VALIDATES** | `phase-1/red-test-1-crra-tstat-pin/plan.md` (verb fix in test name) |
| **H-8 — migration filename + table-count + parity-exclusion** | `phase-1/016-spec-bundles/plan.md`, `phase-1/019-fold-role-columns/plan.md`, `phase-1/gate-1-replay-parity/plan.md` |
| **H-9 — Finalist-C exclusion argued in body** | README §0.1 (this file) — argument restated where the implementing team reads it; the synthesis doc-sweep PR is a follow-up but H-9 is closed by §0.1 alone |
| **§B operator-optional second window (DISJOINT, no signed-divergence)** | `phase-1/second-window-residue/plan.md` + `phase-1/m2-cvar-diagnostic/plan.md` §B + `phase-2/test-operator-second-window/plan.md` |
| **NN1 spec-freeze** | `phase-1/nn1-spec-freeze-discipline/plan.md` + every plan's hazard callout |
| **`N_effective = N_optuna + S` additive accounting** | `phase-1/n-effective-additive-accounting/plan.md` |
| **5 Phase-1 migrations + 2 ALTERs** | `phase-1/016-`, `017-`, `018-`, `019-`, `020-`, `021-cvar-diagnostics/` |
| **Phase-1 replay-determinism anchor = 1** | `phase-1/replay-determinism-anchor/plan.md` |
| **MC sentinel blast radius (7+ consumers)** | `phase-1/mc-sentinel-blast-radius/plan.md` |
| **Gate 1 (backtest-replay parity)** | `phase-1/gate-1-replay-parity/plan.md` + `phase-1/test-gate1-backtest-replay-parity/plan.md` |
| **Gate 2 (live shadow N-weeks-clean diagnostic quality)** | `phase-1/gate-2-live-shadow-quality/plan.md` + `phase-1/test-gate2-shadow-diagnostic-quality/plan.md` |
| **Phase-2 forward-path simulator (net-new G-2)** | `phase-2/simulate-forward-paths/plan.md` |
| **Phase-2 CVaR co-signal hysteresis** | `phase-2/cvar-cosignal-hysteresis-trigger/plan.md` + `phase-2/test-cvar-cosignal-hysteresis/plan.md` |
| **Phase-2 priority resolver extension (5th signal)** | `phase-2/priority-resolver-cvar-cosignal/plan.md` |
| **Phase-2 `tier1_seed` (M-2 ★)** | `phase-2/tier1-seed-determinism/plan.md` + `phase-2/test-tier1-seed-determinism/plan.md` |
| **Phase-2 `lambda` frozen by mandate (NOT searched)** | `phase-2/lambda-frozen-by-mandate/plan.md` |
| **Phase-2 four preconditions** | `phase-2/binding-preconditions/plan.md` |
| **Advisor 4 roles read-only + structural wall** | `phase-1/advisor-frozen-eval-wall/plan.md` + `phase-1/test-frozen-eval-wall-tripwire/plan.md` + `phase-1/017-advisor-observations/plan.md` |
| **Dashboard read-only enforcement** | `phase-1/dashboard-side-effect-ban/plan.md` + `engine-audit/dashboard-read-only-enforcement-audit/plan.md` |

**Coverage statement:** every binding item from the synthesis, every hole H-1..H-9 from the evaluation, and every load-bearing (★) gate from the attack rubric is mapped to at least one plan.

---

## 4. Cross-review consolidation notes (coordinator)

The solo phase produced **duplicates** that the cross-review phase will reconcile:

- **M1 slices vs M1 integration.** `m1-crra-eu-objective` + `m1-crra-eu-tstat` + `m1-bhy-haircut-preservation` are slices that compose into the canonical `m1-crra-eu-autotuner-objective`. Integration: either (i) keep the slices as one TDD cycle each + an integration cycle, or (ii) collapse into one canonical cycle covering all slices. The synthesis treats them as ONE cycle (M1) so option (ii) is preferred — the slices become subsections of the canonical plan during cross-review.
- **M2 slice vs M2 integration.** `m2-cvar-diagnostic` is the canonical integration plan; `021-cvar-diagnostics` is the migration slice; `second-window-residue` is the §B enrichment slice. The three compose cleanly.
- **S-1 plan format reconciliation (correction).** An earlier draft of this README mis-framed `phase-1.5/s1-two-stage-parity-gate/plan.md` as a "15-line table-form stub." It is **not a stub.** Critic authored it in an intentional one-row-per-field tabular layout (the same dense shape used across all 26 critic-domain plans) — each cell carries the equivalent of a section. Substantively it covers: Stage 1 reference fixture (`tests/fixtures/s1-stage1-reference/decision-records.jsonl`), the replay-parity harness extended with a `--reference=<path>` flag + attribution-table emitter, `tests/autotuner/test_s1_stage1_pre_m3_parity.py`, `tests/autotuner/test_s1_stage2_post_m3_attribution.py` with the per-cycle attribution table columns enumerated (`cycle_id`, `symphony_id`, `field_changed`, `old_value`, `new_value`, `attributed_curve`, `intended_direction`, `direction_check_passed`), post-M3 reference promotion as the new committed reference, the H-5 floor-removes-R3-only carry-through, and the H-8/A3 exclusion-list dependency. K-1 attribution-table-passes-it / prose-fails-it is the operating standard. Risk-architect held a longer prose draft (six explicit RED tests S-1.1..S-1.6 + dedicated `tests/parity/s1_two_stage_gate/` module) outside the file tree — the two are **materially equivalent on substance**; critic's tabular plan **stands as canonical**, no supersession. The cross-review may pull naming or schema details from either draft if useful, but the canonical S-1 plan is critic's existing file.
- **Engine-audit lanes.** The audit work-stream has more lanes than initially scoped (`sampler-choice`, `pruner-choice`, `study-persistence-versioning`, `reporting-pipeline-audit`, `composer-alpaca-client-safety-audit` are teammate additions). All are in-scope; the README indexes them all.
- **Phase-2 RED-test-plan slices** (`test-cvar-cosignal-hysteresis`, `test-abstain-fail-safe`, `test-tier1-seed-determinism`, `test-joint-var-es-coverage-backtest`, `test-operator-second-window`) are test-author-owned siblings to the implementer-owned plans; the cross-review will confirm they decompose cleanly per the Toxic-Pair RED-before-GREEN discipline.

---

## 5. The TDD-cycle granularity contract (binding for the downstream builder)

Every plan in this folder is sized for **one TDD cycle**: RED tests written first, GREEN implementation second, optional REVISE phase after the test-writer reviews the implementation. Team composition per the project CLAUDE.md is **Quad** (test-writer + implementer + `quant-code-reviewer` + domain specialist matched to the surface touched). Math-layer changes always add `quant-test-writer` as the adversarial test author.

If a downstream builder finds a plan is too big for one cycle, the plan **must be split**, not partially-shipped. If a plan is too small (a config-only edit or one-line fix), it qualifies for the Agent-Teams exception list (project CLAUDE.md `Exceptions — no team required`) and runs as a solo cycle.

---

## 6. The user-facing rollback story (every phase independently shippable)

- **Stop at Phase 1.** Terminal-acceptable architecture. The defensibility upgrade ships; the engine continues to make exit decisions exactly as today (M1 changes only WHICH parameter set the autotuner SELECTS; M2 is purely a diagnostic).
- **Stop at Phase 1.5.** R1 + R2 provenance flags closed (the engine self-flags closed on those layers). The post-M3 engine is the new committed reference under S-1.
- **Stop at "Phase 2 fails to unlock."** Synthesis §0 verbatim: *"stopping permanently at Finalist A is a full success, not a project failure."* The user has the entire HARDEN-core delivered with full provenance and full accounting.
- **Phase 2 cutover failure.** Migration plan §6 H6: the legacy engine + ALL its tables stay live and untouched through the entire shadow + per-symphony-cutover period + a 20-trading-day post-cutover observation window with the legacy as inverted shadow. Legacy-drop is human-operator-authorized only.

---

## 7. What this scaffold DOES NOT do

- It does not write code. Every plan is a plan.
- It does not authorize Phase-2 execution. The four preconditions (synthesis §5.1) gate execution; the audit lanes feed the latency / data audits that inform precondition (b) and (c).
- It does not adjudicate the slice-vs-integration plan duplicates. Cross-review phase 3 of the team workflow owns that consolidation.
- It does not create PRs. A separate cycle, with explicit user authorization, opens the PR for the downstream build.
- It does not commit to specific dates. The roadmap is sequenced by dependency, not by calendar.

---

*Coordinator: risk-architect. Reviewers (the council that produced the synthesis): tuning-architect, persistence-architect, skeptic, critic. The user gates the Phase-2 preconditions in writing before any Phase-2 plan executes.*
