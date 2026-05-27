# Vision Audit — Sprint 3 Final — Synthesis (2026-05-27)

**Synthesizer:** vision-synthesizer (team-vision-audit)
**Branch:** `audit/vision-audit` forked from `plan/finalist-a-scaffold @ a0591b0`
**Inputs synthesized:**
- `docs/audit/vision-audit-2026-05-27/vision-findings.md` (vision-auditor)
- `docs/audit/vision-audit-2026-05-27/math-soundness.md` (math-reviewer)
- `docs/audit/vision-audit-2026-05-27/logic-trace.md` (logic-narrator)

This document is the binding verdict on whether the engine, as it stands on `a0591b0`, is sound and valuable for the user's stated vision.

---

## Executive verdict

| Gate | Verdict | One-line |
|---|---|---|
| **Vision-fit** | **MIXED** | Frame is right (retail-Composer exit overlay, symphony-only). The "6-layer" marketing language overstates resolver independence (resolver is 4-way; the 6 layers are upstream feature computations). The single most prominent dashboard surface, the S-3 CVaR panel, is dormant — function exists, schema exists, display contract fires, the live wire-up writes all-None sentinels. |
| **Math soundness** | **MIXED** | 5 of 8 math surfaces are sound and vision-fit unambiguously: CRRA-EU utility objective, CVaR diagnostic (as diagnostic), exit-priority resolver, BHY haircut + N_effective additive accounting, NN1 spec-freeze. 3 surfaces are PARTIAL: Monte Carlo gating (no regime-match-quality guard), 125-day walk-forward (purge eats OOS power to ~4-5 usable days), and the practitioner-heuristic stack of log-time-squeeze + PARA-ARM day-boundary reset (no published anchor for the specific shapes). |
| **Logic justification** | **MIXED** | Per-symphony flow, autotuner chain (125/500/CRRA-EU/NN1/BHY), and the three-producer advisor pattern all have traceable rationale chains terminating in DECISIONS.md, council synthesis, code comments, or peer-reviewed sources. **Ten open provenance questions (OQ-1..OQ-10)** name choices the code makes today with no written justification (priority order of TP-vs-Bleed-Cut, n_trials=500, MC k=150, MC paths=5000, three VWAP constants, the 15-minute open grace window, FDR q=0.05, sortino sentinel magnitude). None are bugs; each is a doc-gap. |
| **Overall** | **OPERATOR-CAUTION — VALUABLE-TO-RUN** | The institutional-grade differentiator (walk-forward + CRRA-EU + BHY + NN1 + fail-safe protective stop) is real and load-bearing. The bot will not give back gains via overfit parameters. The protective stop fail-safes are intact across MC and CVaR sentinel paths. **But** the operator-facing surface understates two soft spots that materially affect "informed decisions": (a) CVaR is dormant in the live path; (b) the MC gate has no regime-match-quality guard, so on a regime break it is most confident exactly when it is least informative. Run it, watch the dashboard, do not trust the CVaR cells until the live wire-up ships, and do not assume MC veto behavior is reliable during a regime shift. |

**One-sentence verdict for the user:** This is a sound institutional-grade exit overlay with a strong overfitting-defence pipeline; trust the trailing-stop discipline and the autotuner haircut, treat the CVaR panel as visual placeholder until Phase-1.5 lands, and assume the MC veto is unreliable during regime shifts.

---

## Cross-gate themes

Patterns surfacing in 2+ of the 3 specialist reports.

### Theme 1 — The "6-layer" framing overstates resolver independence (vision-auditor Q1, math-reviewer Surface 4, logic-narrator §2)

All three reports independently flag the same drift: the priority resolver `resolve_trigger_priority` is a **4-way** resolver (`VWAP Breakdown > Take-Profit > VWAP Bleed Cut > Trailing Stop`), not 6-way. The "6 layers" cited in `README.md:21-29` and `.claude/CLAUDE.md:16` are **upstream feature/state computations** (vol-scaling, log-time-squeeze, parabolic ratchet, breakeven, VWAP×2, MC) that feed those 4 trigger flags asymmetrically — 4 of the 6 layers collapse into the single `is_trailing_stop_hit` flag via `compute_exit_confirmation` (`math_engine.py:374-435`). Logic-narrator §2 disambiguates explicitly: "math layers compute features and intermediate states; the resolver selects among the four exit triggers any of those layers contributed to. Both tables are below." This is a documentation calibration issue, not a code drift — the math layers ARE genuinely distinct, but the marketing language conflates inputs with triggers.

**Doc-writer guidance (consensus across all three reports):** Suggested README framing — "six independent risk signals feeding four canonical exit triggers, resolved by a fixed priority order." Don't claim 6 independent trigger paths.

### Theme 2 — CVaR ships dormant, not live (vision-auditor Q4, math-reviewer Surface 2, logic-narrator §1)

Vision-auditor's strongest drift finding (and the most acute) is that `alpha_bot_execution.py:1417-1426` writes all-None sentinels to `record_cvar_diagnostic` every cycle instead of calling `math_engine.compute_portfolio_cvar`. The function exists (`math_engine.py:1185-1345`), the schema exists, the dashboard renders the S-3 four-part panel — but the numeric cells are blank because there is no production caller for the function. Logic-narrator §1 corroborates: "the loop ends with a `record_cvar_diagnostic` telemetry write … and 'no-action' reduces to a state-update pass." Math-reviewer Surface 2 treats the diagnostic-only stance as defensible (sample is too thin for live-trigger statistical power) but does not corroborate that the live path actually computes it; it assesses the *function's* soundness, not the *live wire-up's* status.

**Consensus on framing:** The Phase-1 design *intentionally* keeps CVaR as a diagnostic, not a live trigger (council verdict, `[[project-eut-cvar-migration-council-verdict]]`). What is **not intentional or is undocumented** is that the per-cycle telemetry row is empty in production. Either it ships dormant (the function is staged for Phase 1.5/Phase 2) and the README must say so, or this is a Sprint 2 M2 wire-up gap that was missed. The README §H-4 paragraph at `README.md:167` reads as if it runs live; it does not.

### Theme 3 — Regime-match quality is the highest-leverage open gap (vision-auditor Q5/Drift, math-reviewer Critique #1, logic-narrator §2 row MC)

The Monte Carlo veto and the CVaR diagnostic share the same kNN regime-locality pool (math-reviewer Cross-surface Coherence #2). Neither thresholds the mean kNN distance to detect "the 150 nearest neighbors are bad fits because the current regime is unprecedented." This is the failure mode the user's mandate is most exposed to: **the bot most confidently veto-blocks exits during a regime break, exactly when the historical conditional CDF is least informative.** Vision-auditor confirms the fail-safe at `math_engine.py:425-428` (MC sentinel → exit gate passes), so a regime-break failure cannot *cause* a held-bag scenario — the trailing stop still fires on ticks-below-stop. But on the *other* side — the bot vetoing exits when the operator's intuition says regime-break — there is no upstream guard. Logic-narrator §2 documents this in the MC layer row.

**Highest-leverage single change** (math-reviewer Critique #1): add a regime-match-quality guard that fails MC to "allow exit" and CVaR to `cvar_pct=None` with `insufficient_reason="regime unprecedented"` when mean kNN distance exceeds a threshold.

### Theme 4 — Practitioner heuristics + thin calibration window = the weakest math link (math-reviewer Surface 7/8, vision-auditor Q1/Drift 6, logic-narrator §6 OQ-6)

Three layers — log-time-squeeze decay, parabolic ratchet day-boundary reset, and the VWAP×2 thresholds — have no published theoretical anchor. The shapes are practitioner constructs with coherent rationales but no peer-reviewed source. Combined with the 125-day calibration window (which after PURGE_DAYS=20 at both fold boundaries leaves ~4-5 usable frozen-eval days), the BHY haircut is doing heavy lifting against a sample too thin to reliably certify any single trial as skill. Math-reviewer's "weakest math link" finding: "the haircut can correctly reject overfit trials AND simultaneously be unable to certify any trial as skill — the autotuner can become a system that mostly says 'no' without giving the operator a path forward." Phase-1.5 M3 (R1 + R2) is the documented remediation track for the log-time-squeeze and VWAP HWM gate; **it has not shipped.** This is logic-narrator's OQ-6, the only OQ already on a remediation track.

### Theme 5 — Defensibility is structural, not lint (math-reviewer Surfaces 5/6, vision-auditor Q2/Q6, logic-narrator §3)

The NN1 spec-freeze + BHY haircut + N_effective additive accounting + walk-forward chain is the consensus institutional-grade strength. All three reports converge: NN1 is structurally enforced (autotuner refuses to import with a misconfigured search space at `autotuner.py:1180-1186`); a P&L-frozen facet inflates S → inflates N_effective → raises the BHY bar — making "I picked this parameter because the backtest liked it" *structurally unrepresentable* rather than caught by lint. Math-reviewer ranks this as "the strongest single discipline in the engine for the user's stated mandate." Vision-auditor confirms the wiring catches real bugs (Sprint-2 CRRA-001/NEFF-001/ARCH-001 fix at 836e0ed). Logic-narrator §3 traces the migration argument back to council synthesis §2.1 (replacing 5 hand-tuned multipliers with 1 theory-frozen γ).

### Theme 6 — Divergence Explainer is a stub in default config (vision-auditor Q3, logic-narrator §4)

Both reports flag that DE writes `verdict=NOT_APPLICABLE` rows every autotune cycle in default config (`SECOND_WINDOW_CVAR_ENABLED` off). This is audit-trail completeness, not operator value. The CVaR-divergence REJECT wall is intact (DE-S3-005; verified in `advisors/divergence_explainer.py:7-17`) — so the producer correctly does NOT compute signed divergence. But in default config the operator gets nothing actionable.

---

## Critical recommendations

Three things the user should know before running the bot, ordered by mandate-criticality.

### Recommendation 1 — The CVaR panel is presently a placeholder. Treat empty cells as expected, not as a bot failure.

**What you see:** Dashboard S-3 CVaR Diagnostic panel renders four cells with the labels "diagnostic, not a signal — do not trade on this" and "this CVaR estimate is a known-low-biased LOWER BOUND on tail severity" — but the numeric cells are blank. This is because `alpha_bot_execution.py:1417-1426` writes all-None sentinels every cycle instead of calling `math_engine.compute_portfolio_cvar` (which IS fully implemented at `math_engine.py:1185-1345`).

**Why this matters:** The dashboard panel exists. If you assume it's live, an empty cell looks like a defect. It isn't a defect — the design ships the *machinery* but defers the *live computation*. Per council verdict, this is intentional (CVaR is never a live trigger in Phase 1; the operator-information story will fully fire only when Phase 1.5 wires the function into the per-cycle path).

**What to do:** Read the panel as "the slot is reserved; the number arrives in a future release." Don't make trading decisions on the absence of a CVaR signal.

**Doc-writer must address explicitly.** This is the highest-priority README correction — README §H-4 currently reads as if the function runs live.

### Recommendation 2 — The MC veto can over-block exits during a regime break. Watch for it.

**What you see:** When MC says "in the 150 historically-most-similar days, you usually recover from here," the bot blocks the trailing-stop exit. This is by design (don't capitulate at noisy local lows).

**Why this matters:** On a regime break, the 150 nearest neighbors are all *least bad fits* — they share *some* features with today, but the regime they belong to is gone. The MC veto is most confident exactly when the historical conditional CDF is least informative. AlphaBot does not currently warn the operator when regime-match quality is poor (math-reviewer Surface 3; vision-auditor Q5 acknowledges the fail-safe; logic-narrator §2 MC row).

**What you DON'T have to worry about:** The fail-safe protective stop discipline is intact (`math_engine.py:425-428`). When MC returns the insufficient-history sentinel, the trailing stop fires on ticks-below-stop alone. So the bot will not hold you into a -20% day because MC said "all good" — the protective floor is independent.

**What to do:** During a major regime shift (the most consequential moments), trust your own read of the regime over the bot's MC veto. The bot exits safely on the trailing stop alone; if intuition says regime-break, do not assume an MC-vetoed exit is the bot disagreeing with you on solid ground.

### Recommendation 3 — The autotuner haircut is doing its job, but the calibration window is statistically thin. Read autotuner refusals as a feature, not a failure.

**What you see:** Some nights, the autotuner refuses to deploy new parameters and keeps yesterday's. This is the BHY haircut catching the multiple-testing problem — 500 Optuna trials inflate the best raw Sortino by selection alone, and the haircut adjusts the threshold up. If the winning trial doesn't clear the raised bar, the cascade falls back to the default.

**Why this matters:** Combined with the 125-day calibration window (which leaves ~4-5 usable frozen-eval days after purging), the autotuner CAN become a system that mostly says "no" without giving you a path forward. This is not a bug — it is the honest cost of refusing to deploy overfit parameters. Math-reviewer Surface 7 frames it: "the score is the right shape (no peeking, no contamination) but has wide error bars."

**What to do:** When the autotuner refuses, don't override it. The dashboard surfaces (Overfitting Conscience) tell you *why* — read those messages. The institutional discipline is what protects you from a backtest-overfit deployment; honor it. Future work (extending the calibration history beyond 125 days) is the right fix; manually overriding the haircut today is not.

---

## Backlog

Open items from this vision audit + math-reaudit that don't block running, but should be addressed.

### Tier-A (highest leverage, addresses critical recommendations 1-2 directly)

| ID | Item | Source | Effort |
|---|---|---|---|
| **B-A1** | Wire `compute_portfolio_cvar` into the per-cycle path (replace the all-None sentinel write at `alpha_bot_execution.py:1417-1426` with a real call). Then the dashboard S-3 panel surfaces the actual diagnostic numbers operators are looking at. | Vision-auditor Q4 / Drift 1; logic-narrator §1 | Small (call site swap + integration test) |
| **B-A2** | Add a regime-match-quality guard to MC gating and CVaR. When mean kNN distance > threshold, MC returns sentinel (fail-safe to "allow exit") and CVaR returns `cvar_pct=None` with `insufficient_reason="regime unprecedented"`. | Math-reviewer Critique #1; vision-auditor Q5 implicit; logic-narrator MC row | Medium (threshold calibration + telemetry surface) |
| **B-A3** | Calibrate the "6-layer / 4-trigger" language in README, project CLAUDE.md, and any operator-facing documentation. Suggested phrasing: "six independent risk signals feeding four canonical exit triggers." | Vision-auditor Q1 / Drift 2; math-reviewer Surface 4; logic-narrator §2 | Trivial (doc-only) |

### Tier-B (operator-facing transparency)

| ID | Item | Source | Effort |
|---|---|---|---|
| **B-B1** | Multi-symphony CVaR display. `app.py:388-409` (CVAR-001 scope limit) reads CVaR for the first symphony only. Multi-symphony operators see only one symphony's panel. | Vision-auditor Q4 / Drift 4 | Medium (frontend + read path) |
| **B-B2** | Resolve or tag the 10 open provenance questions (OQ-1..OQ-10) in logic-narrator §6. OQ-1 (TP-vs-Bleed-Cut priority position) and OQ-2 (n_trials=500 power analysis) are the highest-leverage. OQ-6 is already on the Phase-1.5 M3 R2 remediation track. | Logic-narrator §6 | Small per OQ (citation or `[open question]` tag) |
| **B-B3** | Surface the BHY haircut's failure mode to the operator dashboard explicitly: "autotuner refused deployment: best raw Sortino X, BHY-corrected threshold Y at N=500." This operationalizes "informed decisions" by showing the operator *why* the haircut rejected. | Math-reviewer Critique #5 | Small (display surface; OC producer is upstream) |
| **B-B4** | Document the PARA-ARM day-boundary semantic. The `prev_return=0` reset at each new day auto-arms PARA on any symphony opening above threshold (observed 2026-05-15: 11 of 11 symphonies armed PARA on the open). May be intended or unintended — operator must decide. | Math-reviewer Critique #3 / Surface 8 | Small (decision + comment + optional config) |

### Tier-C (Phase-1.5 / Phase-2 track items)

| ID | Item | Source | Effort |
|---|---|---|---|
| **B-C1** | Ship Phase-1.5 M3 (R1 + R2): re-derive the log-time-squeeze curve with formal theoretical/empirical provenance and re-derive the VWAP HWM gate. The vision tenet "each layer carries its own theoretical justification" is incomplete until this lands. | Vision-auditor Q1 / Drift 6; math-reviewer Surface 8; logic-narrator OQ-6 | Large (research + calibration) |
| **B-C2** | Either turn on `SECOND_WINDOW_CVAR_ENABLED` and surface the operator's second CVaR window, OR document explicitly in README that DE is dormant pending Phase 2. Today every autotune cycle writes a NOT_APPLICABLE row. | Vision-auditor Q3 / Drift 3 | Trivial (flag + README) or Medium (full surface) |
| **B-C3** | Extend the calibration window beyond 125 days OR document explicitly that the frozen-eval window is statistically thin (~4-5 days after purge) and what that means for autotuner-refusal frequency. | Math-reviewer Surface 7 / Critique #6 | Large (data source) or Trivial (doc-only) |
| **B-C4** | Reconcile dispatch-brief references. The briefs cited `docs/audit/sprint-3-cross-cycle-audit.md` and `docs/audit/math-reaudit-2026-05-27/README.md` — neither exists on `a0591b0`. Either they are pending creation (doc-writer should not cite as authoritative) or they are on a different branch. | Vision-auditor scope note / Drift 7 | Small (branch audit + doc-writer brief) |

### Resolved / no-action

The CVaR-divergence REJECT wall (DE-S3-005) is **intact and correct.** Do not relitigate this — math-reviewer Surface 2 + vision-auditor Q3 + logic-narrator §4 + project memory `[[project-cvar-divergence-validation-wall]]` all confirm the wall holds. Port-level deprecation is complete in code; README §32-34 language is correct (vision-auditor Drift 5 confirms). The fail-safe protective stop discipline is intact across all sentinel paths (`math_engine.py:425-428`).

---

## Hand-off to doc-writer

The doc-writer's job is the long-form README replacement. This synthesis aligns with the README's required framing on the following critical points — all three specialist reports converge on these:

1. **Lead with what the bot is FOR** — a risk overlay for retail Composer.trade operators. Vision-auditor Q8 elevator pitch (~110 words) is the consensus framing; math-reviewer's non-expert pitches per surface (Blocks A-H) are paste-ready content for the math section.

2. **Calibrate the "6-layer" language.** Both vision-auditor's hand-off and logic-narrator's §2 dual-table call for the same correction: 6 math layers feed 4 exit triggers, asymmetrically. Don't conflate.

3. **Be honest about CVaR.** The doc-writer MUST NOT claim `compute_portfolio_cvar` runs per cycle. The accurate framing is: "the function and schema are in place; the live wire-up is staged for Phase 1.5" or "CVaR ships as a diagnostic instrument — the per-cycle telemetry row is currently a stub for the future computation." See Recommendation 1 above and Tier-A backlog item B-A1.

4. **Be honest about the MC regime-match gap.** The doc-writer should describe MC veto behavior including its known failure mode (regime breaks). See Recommendation 2 above and Tier-A backlog item B-A2.

5. **Lead the institutional-grade story with the haircut chain.** Walk-forward + CRRA-EU + BHY + N_effective + NN1 spec-freeze is *the* differentiator from a naive backtest-and-deploy approach. Math-reviewer Surfaces 1, 5, 6 + vision-auditor Q2/Q6 + logic-narrator §3 all converge. This is what the README should be proudest of.

6. **Three advisors, not four.** OC + SC are operational; DE is feature-flagged off by default; Narrator is deferred per DE-S3-003. Do not claim four advisors.

7. **Port-level is gone.** Don't reintroduce port-level language as current state. The management surface is symphony-only per `[[project-port-level-deprecation-directive]]` and DE-S3-004.

8. **The fail-safe protective stop is the floor.** Across every layer, the trailing stop fires on ticks-below-stop alone when upstream signals (MC, CVaR) are unavailable. This is the load-bearing safety claim — the bot fails *safe*, not *open*.

9. **Reproducibility = NN1 + spec_bundles + researcher_dof_ledger.** Three tables form the queryable "show your work" audit trail. The operator can ask "why did the autotuner pick THESE parameters?" and there's a queryable answer.

10. **Do not cite documents that don't exist on this branch.** `docs/audit/sprint-3-cross-cycle-audit.md` and `docs/audit/math-reaudit-2026-05-27/README.md` are referenced in dispatch briefs but absent from `a0591b0`. Treat as forward-looking, not as authoritative inputs.

The doc-writer should treat the math-reviewer's Blocks A-H as paste-ready content for the README's math section (non-expert pitch + published reference + code anchor per surface) and the logic-narrator's §1-§5 as paste-ready content for the README's flow/autotuner/advisor/dashboard chapters.

---

## Hard-rule compliance log

- **Read-only.** This document is the only artifact authored by vision-synthesizer; no production code touched.
- **Worktree only.** Written to `docs/audit/vision-audit-2026-05-27/SYNTHESIS.md` in the `audit/vision-audit` worktree.
- **Call-path verified.** Every code reference in this synthesis traces to a specialist report whose author Read/Grep-verified the code. Where this synthesis names a specific file path or line range, it does so by citing the specialist report whose audit established it; this synthesizer did not introduce new code references.
- **PM commits.** Per `[[feedback-researcher-agents-lack-bash-pm-ferries-commits]]`, this file is written but not committed by this agent; the unified commit (all 4 specialist files + this synthesis + doc-writer's README replacement) lands after doc-writer signals completion to team-lead.

*End of vision-audit synthesis. Synthesizer: vision-synthesizer. Branch `audit/vision-audit @ a0591b0`. No source files edited.*
