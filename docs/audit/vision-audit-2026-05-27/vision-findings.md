# Vision Audit — Sprint 3 Final (2026-05-27)

**Auditor:** vision-auditor
**Branch:** `audit/vision-audit` forked from `plan/finalist-a-scaffold @ a0591b0`
**Worktree:** `.claude/audit-worktrees/vision-audit`
**Read-only audit.** No source files edited.

## Scope note — referenced docs that don't exist

The dispatch brief named two source materials that are NOT present in the worktree at `a0591b0`:

- `docs/audit/sprint-3-cross-cycle-audit.md` — absent. The audit folder contains only `sprint-1-cross-cycle-audit.md`, `sprint-2-cross-cycle-audit.md`, and `sprint-3-port-removal-manifest.md`. There is no Sprint-3 cross-cycle audit committed yet.
- `docs/audit/math-reaudit-2026-05-27/README.md` — absent. The folder doesn't exist on this branch.

The doc-writer should treat any references to those documents as forward-looking, not as authoritative inputs that already exist.

`docs/research/risk/` exists and contains `log-time-squeeze-investigation.md` + `stop-compounding-investigation.md` — both pre-Phase-1 RCAs, not full theoretical justifications.

---

## Per-question findings

### Q1 — Does `resolve_trigger_priority` realize "vectors of decision-making"?

**Vision-match:** PARTIAL (drift in claimed-vs-actual).

**Evidence (code refs):**
- `math_engine.py:728-759` — `_TRIGGER_PRIORITY_ORDER` is a 4-element list: `VWAP Breakdown > Take-Profit > VWAP Bleed Cut > Trailing Stop`. The resolver takes 4 boolean flags and picks the canonical winner.
- The 6 "layers" cited in `README.md:21-29` and `.claude/CLAUDE.md:16` (vol-scaling, log time squeeze, parabolic ratchet, breakeven, MC, VWAP×2) are NOT 6 trigger-priority slots — they are **input computations** that feed the 4 trigger flags. Specifically:
  - Vol-scaling (`compute_active_trailing_stop`, `math_engine.py:245-289`), log time squeeze (`compute_time_squeeze_decay`, `math_engine.py:211-242`), parabolic ratchet (`compute_para_arm_decision`, `math_engine.py:185-208`), and breakeven (`compute_breakeven_update`, `math_engine.py:292-365`) all feed into the **single** `is_trailing_stop_hit` flag via `compute_exit_confirmation` (`math_engine.py:374-435`).
  - VWAP Breakdown (System A) → `is_vwap_broken`.
  - VWAP Bleed Cut (System B) → `is_vwap_bleed_broken`.
  - MC probability is an *input* to `compute_exit_confirmation` (the MC sanity gate at `math_engine.py:425`), not a standalone trigger.

**Drift:** The marketing language "6-layer exit decision" oversells the actual *independence* of the layers. The trailing-stop family (4 of the 6 "layers") collapses to ONE flag in the resolver. The genuinely independent signals at the resolver are 3: trailing-stop (vol-scaled, time-decayed, ratchet-or-breakeven adjusted, MC-sanity-gated), VWAP Breakdown, VWAP Bleed Cut, plus Take-Profit on the upside.

**Theoretical bases (assessed):**
| Layer | Theoretical basis | Strength |
|---|---|---|
| Vol-scaling (20d realized) | Standard institutional risk-sizing | Strong |
| Log time squeeze | "Tuned practitioner heuristic" — explicit no-formal-literature-provenance comment at `math_engine.py:160-162` | WEAK — flagged for empirical follow-up in code |
| Parabolic ratchet | Velocity-based protect-the-peak heuristic | Weak (operator heuristic) |
| Breakeven (HWM-anchored) | Fu & Zhang 2012 cited at `math_engine.py:340-341` | Strong |
| VWAP Breakdown/Bleed | "Dual-system VWAP defense" — no literature cite | Weak (no provenance in code) |
| MC sanity gate | Empirical kNN regime matching + Monte Carlo bootstrap | Moderate (no formal proof; practitioner-grade) |

The user's vision of "each layer carries its own theoretical / empirical justification" is **partially** realized: two layers carry formal references (breakeven, vol-scaling), the others are practitioner heuristics with no formal provenance. `feature-plans/decision-science/README.md` lines 32-37 acknowledge this — Phase 1.5 (M3) is explicitly the R1 + R2 re-derivation of the time-squeeze curve and the VWAP HWM gate to add provenance. **Phase 1.5 is scaffold-only at this branch tip** — the re-derivation has NOT shipped.

---

### Q2 — Does CRRA-EU + N_effective haircut serve "democratizing exits"?

**Vision-match:** YES (with one operator-trust gap).

**Evidence (code refs):**
- `autotuner.py:367-400` — `compute_crra_eu_tstat = mean(U)/(sd(U)/√T)` is the genuine one-sample t-stat for a mean-valued objective (S-2 binding; H-6 category-error precedent named in docstring).
- `autotuner.py:351-364` — `derive_floored_wealth_argument` applies `WEALTH_ARG_FLOOR` (0.001) to INPUT W, never to output U (W-H4 contract). Floor source at `math_engine.py:78-82`.
- `autotuner.py:489-509` — `compute_n_effective = N_optuna + S` additive accounting; NN1-honest case (S=0) is byte-identical to legacy haircut.
- `autotuner.py:864-962` — `_haircut_select` applies BHY with Yekutieli c(N) factor over the padded N_effective (Shape A; D3 binding).
- `autotuner.py:1189-1279` — `validate_nn1_compliance` enforces NN1 at autotuner entry; default-deny on unknown freeze_discipline values.

**Story for operator-trust:** A retail operator running Composer faces *selection bias* — searching 500 trial-parameter sets and picking the best Sortino is statistically equivalent to overfitting. The user's vision of "institutional-grade risk discipline for retail" is concretely realized by:

1. **CRRA utility on per-period wealth ratios** — risk-aversion (γ) shapes WHICH parameter set the autotuner picks, biasing toward those that protected the operator in the bad days, not just those that maximized average returns.
2. **BHY haircut with Yekutieli c(N) factor** — the t-stat from the winning trial is FDR-adjusted across all trials. A trial that "wins" the sweep but only marginally beats noise is REJECTED by `p_adj > HARVEY_LIU_FDR_Q` and the AI proposal falls back to the default cascade.
3. **NN1 spec-freeze** — gamma, utility family, and wealth argument cannot be P&L-frozen; if a developer tried to tune γ on backtest returns it shows up as a BACKTEST_SELECTION row in `researcher_dof_ledger` → S++ → N_effective++ → BHY bar rises → harder to clear.

**Operator-trust gap:** the operator-facing surface (`templates/ai_advisor.html` per README §AI Advisor Producers) shows advisor *verdicts*, not a plain-language "we ran the haircut at N=510, S=3, your AI proposal cleared/didn't clear" summary. The math is correct; the operator-facing explanation is one layer of abstraction away from what they'd need to TRUST the math. The Overfitting Conscience producer (`advisors/overfitting_conscience.py:138-166`) writes a structured raw_response with `s_count`, `n_effective`, `n_optuna`, `ratio` — these reach the dashboard but as a JSON blob, not a sentence-level explanation. **This is a doc-writer / UX surface, not a code-vision drift.**

---

### Q3 — Do the 3 Advisor producers give operators genuine visibility?

**Vision-match:** PARTIAL — Overfitting Conscience and Spec Critic genuinely earn their keep; Divergence Explainer is a stub in the default config.

**Evidence (code refs):**

**Overfitting Conscience** (`advisors/overfitting_conscience.py:47-176`) — three indicators:
- I-1: `S > 0` → WATCH (any BACKTEST_SELECTION row).
- I-2: `S/N_optuna > 0.10` → BREACH escalation.
- I-3: monotonic S drift across runs → WATCH.
This producer fires meaningful verdicts grounded in researcher_dof_ledger rows the operator cannot otherwise see. **Real visibility.**

**Spec Critic** (`advisors/spec_critic.py:86-200+`) — four indicators:
- I-1: required THEORY facets present (gamma, utility_family, wealth_argument).
- I-2: freeze_discipline default-deny.
- I-3: spec age > 90 days → WATCH.
- I-4: Phase-2 facets seeded prematurely → BREACH.
**Real visibility** into spec-bundle health that no other surface exposes.

**Divergence Explainer** (`advisors/divergence_explainer.py:65-141`):
- Default-config (`SECOND_WINDOW_CVAR_ENABLED` not set or "0") → writes `verdict=NOT_APPLICABLE`, `raw_response={"feature_flag": "off"}`. **Zero operator value in default config.**
- When the flag is "1" — surfaces two CVaR windows side-by-side with no signed divergence (correctly honoring the REJECT wall).

**The CVaR-divergence REJECT wall is intact** (`advisors/divergence_explainer.py:7-17`, `DECISIONS.md:51-58` DE-S3-005). No drift.

**Drift on DE in default config:** the producer writes a no-op observation row every autotune cycle. This is *audit-trail completeness*, not operator-visible insight. The user gets nothing actionable from DE today. Either turn the flag on (and acknowledge the operator-second-window surface) or document loudly that DE is dormant pending Phase-2.

---

### Q4 — Does `compute_portfolio_cvar` deliver actionable operator information?

**Vision-match:** NO (significant drift — the live path does NOT call `compute_portfolio_cvar`).

**Evidence (code refs):**

This is the most significant drift in the build.

- `math_engine.py:1185-1345` — `compute_portfolio_cvar` is fully implemented: kNN regime-matched pool, R-U general-distribution estimator, S-3 four-part display contract delivered (cvar_pct, stderr, tail_obs_count, insufficient_reason).
- `alpha_bot_execution.py:1411-1426` — **the per-cycle live path writes ALL-NONE sentinels** to `record_cvar_diagnostic`:
  ```python
  database.record_cvar_diagnostic(
      cycle_id=current_et.isoformat(),
      symphony_id=symphony_id,
      cvar_5pct=None,
      cvar_5pct_stderr=None,
      cvar_n_tail=None,
      cvar_5pct_long=None,
      cvar_n_tail_long=None,
      mode="live",
  )
  ```
  It does NOT call `math_engine.compute_portfolio_cvar`.
- Grep confirms: `compute_portfolio_cvar` is referenced in `math_engine.py` (definition + constant), in `tests/` (tests + fixtures), and in `feature-plans/decision-science/README.md` (Phase-1 binding) — and that's it. **Zero production consumers.**
- `app.py:388-409` reads `cvar_diagnostic` for the first symphony only (CVAR-001 scope limit), and that row is all-None sentinels from the unconditional live write.
- `templates/index.html:1257-1299` renders the S-3 four-part panel — but on all-None rows, it shows blank cells with the diagnostic/bias labels.

**What the operator actually sees:** the CVaR Diagnostic panel on the dashboard shows the literal labels "diagnostic, not a signal — do not trade on this" and "this CVaR estimate is a known-low-biased LOWER BOUND on tail severity" — but the numeric cells are empty (cvar_5pct is None, cvar_n_tail is None). The S-3 *display contract* fires; the underlying value is absent.

**Why this is acceptable per binding decisions:** the council verdict (`[[project-eut-cvar-migration-council-verdict]]`) and Phase-1 README §H-4 reframe M2 as "operator instrumentation" — CVaR is **never a live trigger** in Phase 1. The Phase-1 README §1.C and `feature-plans/decision-science/phase-1/m2-cvar-diagnostic/plan.md` define M2 deliverables, and the live-path wire-up appears to have been deferred. The function exists, the schema exists, the display contract exists; the **connecting wire from the per-cycle path to the function does not.**

**Drift verdict:** this is either (a) intentional — Phase-1 M2 ships the *machinery* but not the *live computation*, and the doc-writer / README should say so loudly — or (b) unintentional — the live wire-up was missed in Sprint 2's M2 cycle. The `m2-cvar-diagnostic` plan SHA is `304d907` per `feature-plans/decision-science/README.md:31`. **The doc-writer must address this gap explicitly** — the README §H-4 paragraph (`README.md:167`) describes the function as if it runs live; in reality it ships dormant.

---

### Q5 — Does the MC eligible-pool boundary (39 raw days minimum) protect the operator?

**Vision-match:** YES — genuinely protective, not ceremonial.

**Evidence (code refs):**
- `math_engine.py:798-811` — `run_monte_carlo` sufficiency check: `eligible_days = len(valid_dates) - (MC_VOL_WINDOW_DAYS - 1)`, sentinel returned if `eligible_days < MC_MIN_HISTORY_DAYS` (20). Minimum raw history = 20 + 19 = 39 days.
- Memory `[[project-mc-eligible-pool-vs-raw-day-boundary]]` confirms this is intentional: the first 19 days have short-sample-biased rolling vol, so admitting them lets them be mis-selected as artificially-low-vol neighbors.
- `math_engine.py:838` — early-window candidates explicitly excluded from the kNN candidate pool.
- `math_engine.py:425-428` — `compute_exit_confirmation` MC sanity gate: when `prob_beating is None` (sentinel), the gate **passes** (fail-safe). The protective stop still fires on ticks-below-stop alone. This is the F-4 hazard guarantee from the decision-science roadmap (`feature-plans/decision-science/README.md:76`).

**Operator protection:** if the bot is deployed mid-month on a fresh symphony without sufficient history, MC cannot run — but the trailing stop still fires. The operator is NOT exposed to a "MC said hold, so we held into a -20% day" failure mode. This realizes the user's "accuracy + performance over speed" tenet: the bot won't return a fast but garbage MC probability; it returns None and the heuristic floor fires.

---

### Q6 — Does NN1 spec-freeze + `freeze_discipline` add real reproducibility value?

**Vision-match:** YES — genuine falsifiability instrument.

**Evidence (code refs):**
- `autotuner.py:1180-1186` — `_assert_search_space_no_theory_facets` runs at module-load time; if the Optuna search space contains any THEORY-frozen facet (gamma, utility_family, wealth_argument), the import RAISES RuntimeError. **The daemon cannot start with a misconfigured search space.**
- `autotuner.py:1189-1279` — `validate_nn1_compliance` reads `spec_facets` AND `researcher_dof_ledger` for the bundle. Default-deny on unknown disciplines (`autotuner.py:1264-1266`). OOS-peek violations labelled distinctly (`autotuner.py:1273-1277`) — operator sees the severity gradient.
- The NN1 wall couples to the BHY haircut via `compute_n_effective`: a P&L-frozen facet → BACKTEST_SELECTION ledger row → S++ → N_effective++ → harder p_adj clearance. **Spec freeze is structurally enforced, not by lint.**

**Falsifiability story:** the operator can ask "did this winning parameter set genuinely beat the haircut, or is it overfit?" and the answer is grounded in (a) the spec_bundle hash for the trial, (b) the S/N_effective ledger for that bundle, (c) the OC verdict for the autotune run. Three independent queryable surfaces, all read-only-walled (`advisor_ro_query` enforces `COALESCE(fold_role,'') != 'frozen_eval'` per `feature-plans/decision-science/README.md:78`).

**Not process theater.** The Sprint-2 audit fix `CRRA-001 / NEFF-001 / ARCH-001` (836e0ed, per `feature-plans/decision-science/README.md:33`) caught a wiring gap where the U-transform wasn't applied in `_haircut_select` before calling `compute_crra_eu_tstat`. The fix is in `autotuner.py:916-941`. The discipline catches real bugs.

---

### Q7 — Where does the code drift from the documented vision?

**Drift 1 — CVaR live wire-up MISSING (Q4 above).** `alpha_bot_execution.py:1417-1426` writes all-None sentinels every cycle instead of calling `compute_portfolio_cvar`. The README §H-4 paragraph reads as if it runs live; it does not.

**Drift 2 — "6-layer exit decision" overstates resolver independence (Q1 above).** The trigger-priority resolver is 4-way; the 6 layers are *input computations*, not 6 trigger slots. The marketing language should be calibrated.

**Drift 3 — Divergence Explainer writes NOT_APPLICABLE rows in default config (Q3 above).** Every autotune cycle writes a no-op row. Either turn the flag on or be explicit that DE is dormant.

**Drift 4 — Dashboard CVaR panel shows the FIRST symphony only.** `app.py:388-409` CVAR-001 scope limit: multi-symphony portfolios silently omit other symphonies' CVaR rows. README does not flag this. The user vision is "operators see what's happening" — a multi-symphony operator sees only one. Coupled with Drift 1, on a multi-symphony portfolio the operator sees one symphony's all-None panel.

**Drift 5 — Port-level deprecation is complete in code but README §32-34 still uses confusing language.** The README says "Decision math is symphony-level only — port-level decision math was deprecated in Sprint 3 (Stream A). Port state display surfaces are retained (AX-2 badge helpers, restart_notice), but no autonomous port-level decision logic remains in production code." This is correct per `DECISIONS.md:41-48` DE-S3-004 and `engine/` directory listing (only `exit_authority.py` + `params.py` remain). **No code drift; the README §32-34 language is fine.** No actionable drift here.

**Drift 6 — Phase 1.5 (M3) provenance work has not shipped.** `feature-plans/decision-science/README.md` lists Phase 1.5 as "scaffold-only at branch tip 4cf7be3". The log time-squeeze curve still has the "tuned practitioner heuristic — no formal literature provenance" comment at `math_engine.py:160-162`. The vision tenet "each layer carries its own theoretical / empirical justification" is **incomplete** until M3 ships. Doc-writer should NOT claim full provenance.

**Drift 7 — `docs/audit/sprint-3-cross-cycle-audit.md` and `docs/audit/math-reaudit-2026-05-27/README.md` referenced in dispatch brief do not exist on this branch.** Either they are pending creation (in which case the doc-writer should NOT cite them as authoritative) or they are on a different branch.

---

### Q8 — Elevator pitch (draft for doc-writer)

> **AlphaBot is a risk engine for retail Composer.trade operators. Composer holds a basket of symphonies through the day; AlphaBot watches each one minute-by-minute and exits to cash when the math says the day's gain is at risk. The math combines four ways of catching a turn — a volatility-scaled trailing stop, a VWAP breakdown defender, a VWAP bleed-cut for slow drifts, and a take-profit trigger on exceptional moves — and each of those is gated by a Monte Carlo "is today actually bad?" sanity check against 125 days of regime-matched history. Behind the scenes, the autotuner uses 500 walk-forward trials per symphony with risk-aversion-shaped utility (CRRA) and a Harvey-Liu/Benjamini-Hochberg overfitting haircut that rejects parameter sets it can't statistically distinguish from noise. Three AI Advisors flag overfitting risk, spec-bundle integrity, and (when enabled) divergence between CVaR windows. The operator gets institutional-grade exit discipline without having to write any of it themselves.**

(4-6 sentences; ~110 words. Adjusts to operator level without overselling the "6-layer" claim.)

---

## Vision-realization scorecard

| Vision tenet | Realized? | Confidence | Notes |
|---|---|---|---|
| Democratizing institutional risk discipline | YES | High | CRRA + BHY + walk-forward is genuinely institutional-grade; runs on retail Composer accounts. |
| Multiple independent decision vectors | PARTIAL | High | 4 trigger slots, not 6. Trailing-stop family collapses to 1 flag. README oversells. |
| Each layer has its own theoretical justification | PARTIAL | High | Breakeven (Fu&Zhang) and vol-scaling: yes. Time-squeeze, parabolic, VWAP×2: practitioner heuristics, no formal cite. M3 re-derivation has NOT shipped. |
| No degradation in UI behavior; dashboard is observability not action | YES | High | `dashboard-side-effect-ban` plan landed (79983c4); arch constraint 2 holds. |
| Accuracy + performance over speed | YES | High | MC sentinel + eligible-pool boundary; fail-safe protective stop; H-3 non-blocking I/O discipline for telemetry. |
| CVaR-divergence REJECT wall | YES | High | DE-S3-005 binding; no signed-divergence quantity persisted/displayed. Verified in `divergence_explainer.py:7-17`. |
| Symphony-level decision math only | YES | High | All 4 port-level modules deleted (DE-S3-004); `engine/exit_authority.py` retained as display-only. |
| CVaR diagnostic delivers actionable operator info | NO | High | **Function exists; live path does not call it. Dashboard shows all-None rows under the S-3 panel.** |

---

## Hand-off to doc-writer

**Key narrative threads + code anchors for the README rewrite:**

1. **Frame around exits, not trading.** AlphaBot does not enter positions; it exits them. The operator's job is to construct the Composer symphony; AlphaBot's job is to know when to liquidate to cash. Anchor: `README.md:5` (current summary captures this — keep the framing).

2. **Calibrate the "6-layer" language.** The 4-trigger resolver (`math_engine.py:728-759`) is the architectural truth. The 6 inputs (vol-scaling, time-squeeze, parabolic, breakeven, MC, VWAP×2) feed the 4 triggers asymmetrically — 4 of them collapse into the single trailing-stop flag, VWAP×2 split across two flags, take-profit is its own flag. Suggested language: "four canonical exit triggers, each fed by independent risk signals."

3. **CVaR is dormant, not active.** If the doc-writer claims `compute_portfolio_cvar` runs per-cycle, that is factually wrong on `a0591b0`. Acceptable framing options: (a) "the function and schema are in place; the live wire-up is staged for Phase 1.5"; (b) "CVaR ships as a diagnostic instrument — the per-cycle telemetry row is currently a stub for the future computation". Don't claim it's live. Code anchors: `alpha_bot_execution.py:1417-1426` (the stub write), `math_engine.py:1185-1345` (the dormant function).

4. **The haircut is the operator-trust mechanism.** Walk-forward + CRRA + BHY-with-Yekutieli-c(N) + N_effective additive accounting is *the* institutional discipline. README §`EOD Autotuning` covers this but understates it. Code anchors: `autotuner.py:367-400` (t-stat), `autotuner.py:489-509` (N_effective), `autotuner.py:864-962` (haircut), `autotuner.py:1189-1279` (NN1 wall).

5. **Three advisors, not four — and one is dormant.** OC and SC are operational. DE is feature-flagged off by default. README §AI Advisor Producers already says this; preserve it. Don't claim 4 advisors (Narrator is deferred per DE-S3-003).

6. **Port-level is gone.** Don't reintroduce port-level language as "current state." Per `[[project-port-level-deprecation-directive]]` and DE-S3-004 the management surface is symphony-only. README §`Symphony-Level Database Architecture` covers this cleanly — preserve.

7. **The fail-safe protective stop is the floor.** Across every layer, the trailing stop fires on ticks-below-stop *alone* when the upstream signal (MC, CVaR) is unavailable. This is `math_engine.py:425-428` (MC sentinel = pass), and is the F-4 hazard guarantee per the decision-science roadmap. Operators should be told the bot fails *safe*, not *open*.

8. **Reproducibility = NN1 + spec_bundles + researcher_dof_ledger.** Three tables form the "show your work" audit trail. The operator can ask "why did the autotuner pick THESE parameters?" and there's a queryable answer. Code anchors: `autotuner.py:1180-1186` (NN1 module-load guard), `autotuner.py:1189-1279` (compliance validator), `advisors/overfitting_conscience.py:96-110` (S accounting).

**Things the doc-writer should NOT claim:**
- Do not claim full theoretical provenance for all 6 layers (M3 has not shipped).
- Do not claim CVaR runs per cycle (the live wire-up is a stub).
- Do not claim 6 independent trigger paths (the resolver is 4-way).
- Do not cite `docs/audit/sprint-3-cross-cycle-audit.md` or `docs/audit/math-reaudit-2026-05-27/README.md` — neither exists on this branch.

**Things the doc-writer SHOULD highlight:**
- The walk-forward + BHY haircut + NN1 freeze story IS the institutional-grade differentiator. Lead with it.
- The fail-safe protective stop discipline (MC sentinel, CVaR sentinel, NaN/Inf rejection at 11 math-function boundaries per `README.md:174`) is the load-bearing safety claim.
- Symphony-level scoping is intentional — the user mandated it in Sprint 3.

---

*End of vision-audit findings. Auditor: vision-auditor. Read-only audit on `audit/vision-audit @ a0591b0`. No source files edited.*
