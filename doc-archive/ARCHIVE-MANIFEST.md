# doc-archive — Archive Manifest

**Curator:** final-audit-synthesis (doc-archival, solo)
**Date:** 2026-06-02
**Base SHA:** af8dee2 (main at archive time)
**Archive branch:** cycle/archive-stale-worktrees
**Method:** Each doc read in full from its stale branch ref via `git show <ref>:<path>`. "Already in main" claims verified by grep against docs/, DECISIONS.md, README.md, and memory files before classification. No doc was classified DISCARD without that verification.

---

## Summary

All 5 stale branches contain archive-worthy content not fully present in main. All were ARCHIVED.
Total archived: 18 documents across 5 branches.
Discarded: 0 documents. (All docs held lasting provenance value not captured elsewhere in main.)

Safe to prune: YES — see §Prune Safety Confirmation below.

---

## Branch-by-Branch Table

### 1. audit/math-engine-reaudit @ e246d08 (2026-05-27)

5 files added in `docs/audit/math-reaudit-2026-05-27/`. The 25-finding math engine re-audit (PASS verdict, 0 BLOCKER, 3 HIGH). Findings were the direct precursor audit feeding the walk-forward overhaul (OPTUNA-4 OOS fold collapse → DE-WF-001/002) and later PERF cycles. NOT present in main's `docs/audit/` tree (verified: grep returned only stale-branch citation notices in vision-audit-2026-05-27 that the doc "doesn't exist on this branch").

| Document | Decision | Archive Path | Why / Where conclusion lives in main |
|---|---|---|---|
| `docs/audit/math-reaudit-2026-05-27/README.md` | **ARCHIVED** | `doc-archive/audits/math-reaudit-2026-05-27/README.md` | 25-finding summary + cross-gate themes; open OPTUNA-1/6/PERF-003 are the backlog head; conclusion referenced in `docs/audit/README.md` listing and `docs/audit/final-audit-2026-05-29/math-soundness.md` (PERF-005 RED-IS-GREEN note). Full provenance needed for future performance/Optuna work. |
| `docs/audit/math-reaudit-2026-05-27/accuracy-findings.md` | **ARCHIVED** | `doc-archive/audits/math-reaudit-2026-05-27/accuracy-findings.md` | MATH-ACC-001 (brief phrasing vs code), MATH-ACC-002/003 (doc-drift items). Verified CORRECT surfaces still referenced by `docs/audit/final-audit-2026-05-29/math-soundness.md`. |
| `docs/audit/math-reaudit-2026-05-27/logic-findings.md` | **ARCHIVED** | `doc-archive/audits/math-reaudit-2026-05-27/logic-findings.md` | LOGIC-M-1 (spec_critic.py docstring drift) and LOW findings. Doc-only items; provenance for future spec-critic update. |
| `docs/audit/math-reaudit-2026-05-27/optuna-findings.md` | **ARCHIVED** | `doc-archive/audits/math-reaudit-2026-05-27/optuna-findings.md` | OPTUNA-1/6/2/7 open findings; OPTUNA-4 OOS-fold collapse fed DE-WF-001/002 (`DECISIONS.md`); OPTUNA-8/5/9 verified CORRECT. Full methodology reasoning not captured elsewhere. |
| `docs/audit/math-reaudit-2026-05-27/performance-findings.md` | **ARCHIVED** | `doc-archive/audits/math-reaudit-2026-05-27/performance-findings.md` | PERF-003 (double vol call) open; PERF-005 closed by OPTUNA-6 fix per `docs/audit/final-audit-2026-05-29/math-soundness.md`. Full per-finding evidence needed for future perf optimization sprint. |

---

### 2. audit/sprint-3-cross-cycle @ c072c56 (2026-05-27)

1 file added: `docs/audit/sprint-3-cross-cycle-audit.md`. 11-finding Sprint 3 Stream B cross-cycle audit (BLOCK verdict). NOT present in main's `docs/audit/` (only sprint-1, sprint-2, sprint-3-port-removal-manifest exist there; confirmed by `ls docs/audit/`). Findings S3-AUDIT-001 BLOCKER through S3-AUDIT-004 HIGH drove the audit-fix cycles that produced `7b47376` and `be74f4f`. Their conclusions are recorded in `DECISIONS.md` DE-S3-001/002 and `memory/project_sprint_3_complete.md`, but the per-finding call-path verification evidence is NOT preserved in main.

| Document | Decision | Archive Path | Why / Where conclusion lives in main |
|---|---|---|---|
| `docs/audit/sprint-3-cross-cycle-audit.md` | **ARCHIVED** | `doc-archive/audits/sprint-3-cross-cycle/sprint-3-cross-cycle-audit.md` | Full call-path evidence for S3-AUDIT-001 (OC subject_id=0 broken chain through `database.py:507-546`), S3-AUDIT-002 (DE never invoked), S3-AUDIT-003 (prior_runs=None inert), S3-AUDIT-004 (symphony_id schema mismatch). Conclusions in `DECISIONS.md` DE-S3-001/002; per-finding evidence not elsewhere. The CVaR-REJECT wall verification (finding S3-AUDIT-004 adjacent prose) and port-decision absence confirmation are also here at call-path depth. |

---

### 3. research/adaptive-spike @ 7683c30 (2026-05-30)

3 files added at `research/` (top-level). Synthesis recommendation + two supporting research tracks for the adaptive system. NOT present anywhere in main's `docs/research/` or any other main tree path. Key direction decisions are captured in `memory/project_adaptive_exit_direction.md`, but the detailed file:line derivations, literature grading, and design-option tables are NOT reproduced there.

| Document | Decision | Archive Path | Why / Where conclusion lives in main |
|---|---|---|---|
| `research/00-ADAPTIVE-RECOMMENDATION.md` | **ARCHIVED** | `doc-archive/research/adaptive-spike/00-ADAPTIVE-RECOMMENDATION.md` | Synthesized verdict: REACTIVE achievable now; ADAPTIVE-LEARNED mostly data-blocked; ~1-knob budget from two independent tracks. H-1 file:line verification included. Direction in `memory/project_adaptive_exit_direction.md`; detailed derivations and convergence evidence not elsewhere. |
| `research/01-acceptance-gate-design.md` | **ARCHIVED** | `doc-archive/research/adaptive-spike/01-acceptance-gate-design.md` | Role-mapping (VETOES vs DISCRETIONARY vs EXCLUDED) with file:line for all 6 components; 4 new discretionary criteria (D1-D4) with computable definitions; backtest substrate design using `external_data/` daily panel. Phase 3b implemented acceptance_gate.py @ 0d79fc7; detailed design rationale not in generated docs. |
| `research/02-adaptive-frontier.md` | **ARCHIVED** | `doc-archive/research/adaptive-spike/02-adaptive-frontier.md` | Honest maximum of adaptivity (REACTIVE / LEARNED-daily / DATA-BLOCKED tiers); external_data scope analysis (daily-only, ~7% synthetic); intraday data availability assessment (TAQ/TickData ~2004, not free). Literature citations graded by tier. Direction in `memory/project_adaptive_exit_direction.md`; detailed frontier analysis not elsewhere. |

---

### 4. audit/comprehensive-soundness @ 848b492 + c0a12e2 (2026-05-30)

9 files added at `audit/` (top-level). Comprehensive 7-pillar soundness audit with SOUND-BUT-UNPROVABLE verdict, plus runtime-auditor corroboration amendment. NOT present in main's `docs/` tree (verified: grep for "SOUND-BUT-UNPROVABLE", "OPT-INVALID-1", "comprehensive soundness" returned 0 hits in docs/). H-1/H-2/H-3 fixes are all merged; high-level verdict is in `memory/project_adaptive_exit_direction.md`; but the per-pillar evidence, empirical measurements, vision-mechanism map, and UX design deliverable are NOT in main.

| Document | Decision | Archive Path | Why / Where conclusion lives in main |
|---|---|---|---|
| `audit/00-SYNTHESIS.md` (c0a12e2 — final) | **ARCHIVED** | `doc-archive/audits/comprehensive-soundness/00-SYNTHESIS.md` | Full SOUND-BUT-UNPROVABLE verdict with per-pillar table, vision→mechanism map (all 7 criteria named), prioritized findings (H-1/H-2/OQ-1), cross-auditor contradiction reconciliation, "could not determine" section. Verdict headline in `memory/project_adaptive_exit_direction.md`; per-pillar evidence and structured verdict not elsewhere. |
| `audit/01-reconstructed-vision.md` | **ARCHIVED** | `doc-archive/audits/comprehensive-soundness/01-reconstructed-vision.md` | Plain-language reconstruction of all 6 math layers + 4 exit triggers with file:line citations; written for a non-quant reader; used as anchor for all subsequent adaptive work. Not in docs/; high provenance value for onboarding or future design work. |
| `audit/findings/pillar2-mathimpl.md` | **ARCHIVED** | `doc-archive/audits/comprehensive-soundness/findings/pillar2-mathimpl.md` | Math implementation audit: zero Critical/High/Medium; L-1 (compute_tp_confirmation test gap); all 8 layers confirmed numerically correct. Not in docs/; referenced by synthesis §4. |
| `audit/findings/pillar2-optmethod.md` | **ARCHIVED** | `doc-archive/audits/comprehensive-soundness/findings/pillar2-optmethod.md` | Optimization methodology: OPT-INVALID-1 (H-1) confirmed here; underpowered signal analysis; CPCV named-but-unimplemented (fed DE-WF-002). Full evidence trail for H-1 fix. |
| `audit/findings/pillar2-theory.md` | **ARCHIVED** | `doc-archive/audits/comprehensive-soundness/findings/pillar2-theory.md` | Theory audit: citation-misuse tensions (Danielsson-Zigrand, Leung-Zhang, below-VWAP evidence); SOUND IN FORM verdict with documented honest scope limits. Provenance for future theory-layer changes. |
| `audit/findings/pillar3-empirical.md` | **ARCHIVED** | `doc-archive/audits/comprehensive-soundness/findings/pillar3-empirical.md` | Empirical-validity: Guard Alpha day-clustered t=1.52 NS; intraday lag-1 AC = −0.036 (mean-reverting); SPY/TQQQ blocked (no creds). These measurements are the evidential basis for the "mean-reverting regime → restraint" design decisions in Phase 3. Not elsewhere in main. |
| `audit/findings/pillar4-runtime.md` | **ARCHIVED** | `doc-archive/audits/comprehensive-soundness/findings/pillar4-runtime.md` | Runtime: H-2 RT-01 (cross-process lost-update race — later reclassified as non-race at H-2 fix); H-3 OQ-1 (fail-to-arm on missing MC — resolved FAIL-OPEN @ 2f67504). Per-finding evidence not in main. |
| `audit/findings/pillar5-advisor.md` | **ARCHIVED** | `doc-archive/audits/comprehensive-soundness/findings/pillar5-advisor.md` | AI Advisor audit: producer honesty assessment, north-star tiering (S→U→P), Divergence Explainer default-inert finding. Conclusions in DECISIONS.md DE-S3-002; full advisor-tier derivation not elsewhere. |
| `audit/findings/ux-design-deliverable.md` | **ARCHIVED** | `doc-archive/audits/comprehensive-soundness/findings/ux-design-deliverable.md` | UX risk-adjusted metrics design: Tier 1 (computable now) vs Tier 2 (needs benchmark feed) split; frontend prompt for risk-adjusted dashboard. Phase 2/2b implemented this; the design specification not preserved in generated docs. |

---

### 5. research/consensus-exit @ e402980 + 930d5d5 (2026-05-30)

4 files added at `research/` (top-level). Weighted-consensus exit Gate-1 research (3 tracks + synthesis). NOT present in main's `docs/research/` or any other path (verified: grep for "GATE1-RECOMMENDATION", "warm-start priors", "tuning-methodologist" returned 0 hits in main). High-level direction in `memory/project_adaptive_exit_direction.md`; detailed per-criterion prior derivations, per-weight gating designs, and integration blast-radius map are NOT in main.

| Document | Decision | Archive Path | Why / Where conclusion lives in main |
|---|---|---|---|
| `research/00-GATE1-RECOMMENDATION.md` (930d5d5 — final) | **ARCHIVED** | `doc-archive/research/consensus-exit/00-GATE1-RECOMMENDATION.md` | Full Gate-1 document: sound-experiment/unsound-statistical-claim executive summary; recommended design (pooled, prior-anchored, ~1-knob budget); H-1/H-3 hard dependencies with file:line; N-confirms placement load-bearing decision; per-criterion weight priors with literature basis. Direction in `memory/project_adaptive_exit_direction.md`; detailed design not elsewhere. |
| `research/01-design-priors-validatability.md` | **ARCHIVED** | `doc-archive/research/consensus-exit/01-design-priors-validatability.md` | Design space taxonomy (linear score / weighted voting / hazard model); warm-start priors per criterion with literature grading; dimensionality vs data analysis (~5-15 independent regime units → 0-1 movable weights); validatability verdict. Feeds Phase 3b warm-start; not in generated docs. |
| `research/02-tuning-gate-h1.md` | **ARCHIVED** | `doc-archive/research/consensus-exit/02-tuning-gate-h1.md` | Permission-to-tune mechanism (BHY extension per-weight); pooling vs per-symphony data math; regularization/shrinkage analysis (L1-toward-prior on Dirichlet simplex); H-1 fix spec (exact statistic form for consensus objective). H-1 merged; detailed gate-statistics derivation not in main. |
| `research/03-integration-map.md` | **ARCHIVED** | `doc-archive/research/consensus-exit/03-integration-map.md` | Engine integration map: dual-mirror (live + replay) parity constraint; exact graft points (`resolve_trigger_priority` at alpha_bot_execution.py:1619 and autotuner.py:1066); N-confirms state-dict placement; H-3 arming-vs-scoring layer distinction (critical nuance). Phase 3c blast-radius evidence not in main. |

---

## Prune Safety Confirmation

All 5 stale branches are safe to prune. Verification:

| Branch | Ref | Worthwhile content | Status |
|---|---|---|---|
| audit/math-engine-reaudit | e246d08 | 5 docs → archived to `doc-archive/audits/math-reaudit-2026-05-27/` | SAFE TO PRUNE |
| audit/sprint-3-cross-cycle | c072c56 | 1 doc → archived to `doc-archive/audits/sprint-3-cross-cycle/` | SAFE TO PRUNE |
| research/adaptive-spike | 7683c30 | 3 docs → archived to `doc-archive/research/adaptive-spike/` | SAFE TO PRUNE |
| audit/comprehensive-soundness | 848b492 + c0a12e2 | 9 docs → archived to `doc-archive/audits/comprehensive-soundness/` | SAFE TO PRUNE |
| research/consensus-exit | e402980 + 930d5d5 | 4 docs → archived to `doc-archive/research/consensus-exit/` | SAFE TO PRUNE |

No doc was discarded without verifying its conclusion was fully captured in main. All 18 documents had provenance value not fully replicated in main's docs/, DECISIONS.md, README.md, or memory files — they were ARCHIVED.
