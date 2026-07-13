# Advisor-Intent Audit — Claims Inventory

**Author:** audit-doc | **Phase:** 1 of 2 (inventory only — no verdicts, no corrections)
**Worktree:** `.claude/worktrees/advisor-intent-audit` @ `05da181b`
**Purpose:** every statement in operator-facing UI, docs, or internal config that claims or implies AI/LLM reasoning, "intelligent" analysis, or statistical rigor for an advisor feature. Input for audit-reasoning / audit-stats / audit-data cross-challenge rounds and for audit-lead's verdict matrix. **This document makes no findings** — see `docs/audit/ADVISOR-INTENT-AUDIT.md` (audit-lead, DRAFT as of this writing) for verdicts, and Phase 2 `doc-reconciliation.md` (this author, pending FINAL verdict) for corrections.

## AUDIENCE key
- **UI-LIVE** — renders on the live dashboard the operator actually sees (`templates/*.html`, `static/*.js` user-facing strings). Highest severity: a false claim here is a false claim in the product itself.
- **DOCS** — `feature-plans/*.md`, `docs/generated/*.md`, `README.md`, `DECISIONS.md`. Medium severity: shapes future dev/audit understanding but the operator never reads it.
- **CONFIG** — `.claude/CLAUDE.md` key-files table. Lowest severity: internal agent-orientation context only, but still governs what future workers believe is true.

## CLAIM-TYPE key
- **reasoning-provenance** — claims/implies a specific model or LLM call produced the output (bar #1)
- **statistical-rigor** — claims a statistical correction/gate/method was applied (bar #2)
- **data-freshness** — claims data recency/liveness (bar #3)
- **honest-scope** — explicitly limits/describes what the feature does NOT do (control group — these are candidates that PASS the claim-check, included for completeness so the auditors can confirm they hold)
- **plural-output** — claims/implies the gate can surface MORE THAN ONE proposal per run (new tag, see Addendum below)

---

## Feature 1 — Logic Changes

| # | Quote | Location | Audience | Claim-type |
|---|---|---|---|---|
| 1.1 | "Logic-change proposals carry the highest overfitting risk. FDR/Yekutieli correction applied. Survivors are sound-but-unprovable." | `templates/ai_advisor.html:1533-1534` | UI-LIVE | statistical-rigor |
| 1.2 | "Selected on backtest across {{ n_cand }} candidates. FDR correction applied ({{ n_cand }} tested, threshold Yekutieli-adjusted). Overfitting risk cannot be eliminated — only resisted." | `templates/ai_advisor.html:1904` (shared macro, renders for SB but also structurally identical for Logic Changes survivor cards) | UI-LIVE | statistical-rigor |
| 1.3 | Module overview describes "parameter-tweak proposals" via `_parse_change_description_to_tweak` / `generate_objective_directed_candidates` — no LLM/Claude/Opus keyword anywhere in the doc | `docs/generated/advisors_logic_change_engine.md:1-24` | DOCS | honest-scope (control — matches the seed finding, no overclaim found) |
| 1.4 | Global page subtitle "Claude-powered suggestion engine" sits above ALL SIX tabs including this one (see 5.1) | `templates/ai_advisor.html:988` | UI-LIVE | reasoning-provenance (cross-feature bleed — flagged once here, primary entry at 5.1) |
| 1.5 | "N backtested logic candidates → acceptance applies a multiple-testing/FDR correction across the FULL set... wired by passing the complete bt_candidates list as ONE batch to `evaluate_candidate_batch`. Under no circumstances are candidates gated individually — that would silently disable the FDR denominator." | `advisors/logic_change_engine.py:38-42` | DOCS | plural-output (same winner-take-all structural cap applies here as Strategy Builder — see Addendum) |

**Note:** no Logic-Changes-specific doc or UI text claims LLM reasoning. The only reasoning-provenance exposure for this feature is the page-level subtitle (1.4/5.1), which is feature-agnostic and appears on every tab. **audit-reasoning confirms (2026-07-13T19:08, R-F3 message): the 5.1 subtitle is FALSE for this tab — zero LLM, direct or indirect.**

## Feature 2 — Asset Swaps

| # | Quote | Location | Audience | Claim-type |
|---|---|---|---|---|
| 2.1 | "Advise only — no trades are placed from this screen. Swap proposals that survive the gate are recommendations only. Apply changes manually in Composer." | `templates/ai_advisor.html:1462-1465` | UI-LIVE | honest-scope |
| 2.2 | Module overview: "lens-informed ranking" via `_apply_lens_blend`; "Lens scoring influences ranking only — the BHY-FDR gate is unchanged" | `docs/generated/advisors_asset_swap_engine.md:14-18` | DOCS | statistical-rigor (accurate self-description — not an LLM claim, a blend-formula claim; **see 2.5 — this claim is TRUE only for the weekly path, never the operator-clicked route**) |
| 2.3 | Doc explicitly documents its OWN prior failure: `extract_lens_scores` read a fabricated key no real producer emits; "441 mocked tests stayed green... dead on real data until this fix" (DE-LENS-SCORE-SHAPE-001, fixed 2026-07-12) | `docs/generated/advisors_asset_swap_engine.md:20` | DOCS | data-freshness (self-disclosed historical gap — see 2.5 for the route-level confirmation this required) |
| 2.4 | `suggest_swaps` comment: "Gate all successfully-backtested candidates together (honest n_effective = N)." | `advisors/asset_swap_engine.py:1267` | DOCS | plural-output (same winner-take-all structural cap — see Addendum) |
| 2.5 | **Route-level confirmation (audit-data, 2026-07-13T19:10):** `POST /ai-advisor/asset-swaps/evaluate` (`app.py:4240→4312`) calls `propose_operator_swap` **without ever passing `lens_scores`** (default `None`, `asset_swap_engine.py:996`). On the operator-clicked evaluate button, `lens_scores` is **always `None`** → `_apply_lens_blend` is a permanent no-op → `lens_evidence` persists as `{}` (`asset_swap_engine.py:1103`). The 2026-07-12 fix (2.3) reaches only `weekly_suggestions_scheduler.py`, never this route. Even when the weekly path DOES fire, the blend uses only ONE lens (`technicals.momentum`) despite 2.2's "lens-informed" (plural-sounding) framing. | `app.py:4240-4312`; `advisors/asset_swap_engine.py:996,1103` | DOCS/behavior (not UI copy, but directly falsifies what 2.2's "lens-informed" doc claim implies for the reachable operator route) | reasoning-provenance / data-freshness |

## Feature 3 — Strategy Builder (highest-severity zone — direct cross-doc contradiction found)

| # | Quote | Location | Audience | Claim-type |
|---|---|---|---|---|
| 3.1 | **"The advisor generates candidate symphonies from templates, backtests each one, and applies FDR/Yekutieli multiple-testing correction. Only candidates that clear the gate are surfaced as proposals."** — git-blamed to commit `7908d77b0`, **2026-06-13** | `templates/ai_advisor.html:1717-1721` | UI-LIVE | reasoning-provenance / statistical-rigor / **plural-output** ("candidates ... are surfaced as proposals" — plural; see Addendum, this is structurally capped at ONE per run) |
| 3.2 | "advisors/build_plan_generator.py is the **Opus-backed brain** of the real Strategy Builder... It produces the plans that the Component 3 compiler translates into Composer trees, **replacing the 7-template stamper** in `_generate_candidate_trees`" | `docs/generated/advisors_build_plan_generator.md:10` | DOCS | reasoning-provenance |
| 3.3 | "Tradeable US-equity membership provider for the **real Opus-driven Strategy Builder** (Component 1)" | `docs/generated/advisors_universe_provider.md:3` | DOCS | reasoning-provenance |
| 3.4 | "Component 3 of the real Opus-driven Strategy Builder... introduces `advisors/plan_tree_compiler.py`... the deterministic bridge from the Component-2 build-plan DSL to Composer `raw_value` trees" | `DECISIONS.md:2050` | DOCS | reasoning-provenance |
| 3.5 | "Replace the Strategy Builder's fixed **7-template stamper**" (title/summary of the feature plan that built the real pipeline) | `feature-plans/strategy-builder-real.completed.md:17` | DOCS | reasoning-provenance |
| 3.6 | `_generate_candidate_trees` is named "the **7-template stamper** to replace" — emits `CandidateInfo` from templates T1-T7 | `feature-plans/strategy-builder-real.completed.md:76` | DOCS | reasoning-provenance (this is the ORIGINAL template-based implementation description — the thing 3.1's UI copy still describes) |
| 3.7 | Original (pre-real-pipeline) plan: "Build all candidates **from templates** (bounded by `MAX_CANDIDATES_PER_RUN = 30`...)" | `feature-plans/strategy-builder.completed.md:256` | DOCS | reasoning-provenance (source of the UI copy's phrasing — see finding below) |
| 3.8 | Multiple `DE-SB-GEN-*` decision entries document **real, live, unmocked Opus SDK calls** used as the acceptance gate for Components 2/2b/3 (e.g. "PM-run live exam: real Opus SDK call... Gate passes when at least 1 validate_tree-clean symphony is produced per objective") — dated 2026-06-20 through 2026-06-20 (Revise rounds) | `DECISIONS.md:1855-2317` (DE-SB-GEN-001, DE-SB-GEN-DRIFT-FIX, and 2 follow-on Revise entries) | DOCS | reasoning-provenance (this evidence is now CONFIRMED to reflect the reachable default path — see R-F3 verdict below, not just build-time verification) |
| 3.9 | "Logic-change proposals carry the highest overfitting risk. A multiple-testing correction (FDR/Yekutieli) is applied across ALL backtested candidates" (SB risk banner — does not say where candidates come from) | `templates/ai_advisor.html:1647-1650` | UI-LIVE | statistical-rigor |
| 3.10 | "**No candidate cleared the gate this run.** This is a valid outcome — the gate rejected all candidates to protect against overfitting." — note: SINGULAR "candidate" here, inconsistent with the plural "candidates ... are surfaced as proposals" in 3.1's run-controls-note just above it | `templates/ai_advisor.html:1783-1788` | UI-LIVE | honest-scope (control, but see internal plural/singular inconsistency noted) |
| 3.11 | CLAUDE.md key-files table: "**Opus Build-Plan Generator** for the real Strategy Builder... SDK structured tool-use generation of `N_PLANS_PER_OBJECTIVE=12` diverse objective-shaped build-plans" | `.claude/CLAUDE.md` (build_plan_generator.py row) | CONFIG | reasoning-provenance |
| 3.12 | `sv.map(function (s) { return card(s, 'survivor'); })` renders into `data-testid="sb-live-survivor-cards"` (plural class/testid name; array-map architecture) | `static/ai_advisor.js:758` | UI-LIVE (code, not copy) | plural-output |
| 3.13 | Docs consistently use plural "survivors" throughout with no stated cap: "0 survivors is a VALID outcome, not an error"; "`screened_survivors` is a subset of `gated_batch.survivors`" | `feature-plans/strategy-builder-real.completed.md:224,112`; `docs/generated/advisors_strategy_builder_engine.md:3` | DOCS | plural-output |

**R-F3 verdict received (audit-reasoning, 2026-07-13T19:08) — resolves the open direction question below.** The on-demand SB route **DEFAULTS to real Opus generation**; it does NOT commonly degrade to templates. Evidence cited: the reachable path calls the real Opus client (`build_plan_generator.py:1078`, `claude-opus-4-8` tool-use); audit-data corroborates the PM's two observed runs (12 local / 22 droplet) were both genuine LLM runs (12 == `N_PLANS_PER_OBJECTIVE` built-new; 22 = 12 built-new + ~10 Atlas). Template-degradation is a structural risk (6 D-1 branches), not the normal case. **Correction direction (final, per audit-reasoning): the 3.1 UI copy is NOT accidentally honest — it is stale-and-wrong on two counts: (a) "templates" describes the retired T1-T7 stamper, dead on the reachable path; (b) it omits the Atlas community-candidate source entirely.** Suggested replacement (audit-reasoning's wording, staged — see Staged Corrections section below): *"Opus generates candidate strategies (plus ranked community strategies from the Atlas library), compiles + backtests each, and applies FDR/Yekutieli correction."* **audit-reasoning's closing reinforcement (2026-07-13T19:17): the run-level provenance-line recommendation below is the LOAD-BEARING half of this finding, not an optional add-on — the unobservability of which mode ran matters as much as the stale copy itself.** Additional HIGH finding to fold into any SB copy rewrite: there is no run-level indicator of which mode ran — a run can silently serve Atlas-only community candidates (LLM produced 0 plans) as an ordinary success, with `template_id` (built-new vs community) as the only per-candidate signal. Required pairing: a run-level provenance line, e.g. *"N built-new by Opus, M from community library."*

**Pre-verified finding (provable from grep + git history alone, independent of the auditors' runtime verdict):** the UI copy at 3.1 (`templates/ai_advisor.html:1718-1720`, authored **2026-06-13**) describes the ORIGINAL 7-template stamper design (3.6/3.7). The real Opus-driven replacement shipped one week later — `DE-SB-GEN-001` (Components 2+2b) is dated **2026-06-20** (3.8), explicitly "replaces the 7-template stamper." **The UI copy was never updated when the real pipeline shipped**, and per the R-F3 verdict above, this is a confirmed defect requiring correction toward "Opus," not accidental honesty.

## Feature 4 — Chat

| # | Quote | Location | Audience | Claim-type |
|---|---|---|---|---|
| 4.1 | "This chat explains advisor findings. It cannot apply changes, place trades, or generate new recommendations." | `templates/ai_advisor.html:2059-2062` | UI-LIVE | honest-scope |
| 4.2 | "Chat unavailable... Anthropic API key not configured. Set `ANTHROPIC_API_KEY` to enable explanations." | `templates/ai_advisor.html:2066-2071` | UI-LIVE | honest-scope (honest gating — degradation is visible to the operator) |
| 4.3 | "Explain-only chat backend... calls **Claude** to explain a specific surfaced artifact in plain language" | `docs/generated/advisors_advisor_chat.md:3` | DOCS | reasoning-provenance |
| 4.4 | Model: `os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-8")`, 30s timeout, "embedded in a **system-prompted Claude call**" | `docs/generated/advisors_advisor_chat.md:24,36` | DOCS | reasoning-provenance |

**audit-reasoning confirms (2026-07-13T19:08): REAL Opus, `advisor_chat.py:384`. The 5.1 page subtitle is TRUE for this tab.**

## Feature 5 — Run Advisor / per-symphony suggestions

| # | Quote | Location | Audience | Claim-type |
|---|---|---|---|---|
| 5.1 | **"Claude-powered suggestion engine — operator must approve each change through safety gates"** — page-level subtitle, renders above ALL SIX tabs (Overview / Correlations / Asset Swaps / Logic Changes / Chat / Strategy Builder), not scoped to this feature alone | `templates/ai_advisor.html:987-989` | UI-LIVE | reasoning-provenance — **highest-severity single claim in this inventory**: accurate for THIS feature (5.2-5.4), directly contradicted by the Feature-1 seed finding (Logic Changes has no LLM invocation) and by Feature 7/8 (Correlations + autotune guardrails are explicitly rule-based/pure-measurement) if the subtitle is read as describing the whole page, which its placement/scope (above the tab nav, not inside a tab panel) invites. **FINAL grading received (audit-reasoning, 2026-07-13T19:08, confirmed closed 19:17): TRUE for 3 of 6 tabs — Run Advisor (this feature, real Opus, `ai_advisor.py:1759`), Chat (F4, real Opus, `advisor_chat.py:384`), Overview/Market Prism (F6, real Opus council). FALSE for Logic Changes (F1) and Asset Swaps (F2) — both zero-LLM deterministic. MISLEADING for Strategy Builder (F3) — only built-new candidates are LLM-produced; community candidates and any degraded run are not. Verdict: MISPLACED/INVERTED attribution (true-but-overscoped), not fabricated. "Good to lock" per audit-reasoning.** |
| 5.2 | Button label "**Run Claude advisor**" | `templates/ai_advisor.html:1285`, `static/ai_advisor.js:360` | UI-LIVE | reasoning-provenance (scoped correctly — this button IS the per-symphony `request_suggestions` call) |
| 5.3 | "Claude-backed config advisor: context assembly, per-symphony assessment, **structured-output Claude call** via ADVISOR_SYNTHESIS_MODEL, safety gates (7-item allowlist, risk-direction check, OOS re-validation)" | `docs/generated/ai_advisor.md:3` | DOCS | reasoning-provenance |
| 5.4 | "Claude will reason without OOS data" (empty-state copy when no Optuna run exists) | `docs/generated/ai_advisor.md:84` | DOCS | reasoning-provenance |
| 5.5 | "Defense-in-depth: even if Claude hallucinates a key or emits a credential, it can never reach a live config write" / "The engine never trusts Claude's self-reported `risk_direction`" | `docs/generated/ai_advisor.md:162,168` | DOCS | honest-scope (documents adversarial-to-the-LLM safety design — control, not an overclaim) |

## Feature 6 — Market Prism

| # | Quote | Location | Audience | Claim-type |
|---|---|---|---|---|
| 6.1 | "Market Prism" block header + sentiment chip + per-lens digest — **no explicit model/Claude attribution anywhere in the rendered UI** for this block | `templates/ai_advisor.html:1066-1273` | UI-LIVE | **absence-of-claim** (opposite-direction finding: the UI never tells the operator this IS Claude-generated — only the page-level subtitle 5.1 implies it, and that subtitle sits far above this block with no visual association. Relevant to bar #4 "can the operator tell WHICH mode ran" — under-attribution, not over-attribution. **Confirmed real: audit-reasoning's 5.1 grading names this "Overview/Market Prism, real Opus council" as one of the 3 tabs where the subtitle is TRUE.**) |
| 6.2 | "No overnight market read yet — the off-hours pipeline runs daily at 03:00." | `templates/ai_advisor.html:1267-1269` | UI-LIVE | honest-scope |
| 6.3 | "Scheduled off-hours lens pipeline: collects 5 lens blocks, validates citations, **synthesizes a Market Prism summary via Claude**" | `docs/generated/advisors_lens_pipeline.md:3` | DOCS | reasoning-provenance |
| 6.4 | "Nightly Market Prism scheduler wrapper — invokes the Market Prism council via a **vanilla-primary headless Claude session**" | `docs/generated/prism_scheduler.md:3` | DOCS | reasoning-provenance |
| 6.5 | "A full 6-agent Opus council realistically costs $5–10/run" | `docs/generated/prism_scheduler.md:25` | DOCS | reasoning-provenance — **audit-data (2026-07-13T19:10, confirmed final 19:17): out of this audit's runtime scope (droplet/council excluded per brief). Checkable in principle against `spend_log`/`total_cost_usd` vs `MAX_BUDGET_USD=15.0` cap, but labeled "claim plausible vs the cap; not runtime-verified" rather than confirmed. Live-verify go/no-go routed to audit-lead/PM, not resolved here.** |
| 6.6 | DE-SYNTH-001: default model upgraded Haiku -> Opus 4.8 for all 3 advisor LLM call sites, rationale "cost difference... negligible (~$0.001/call delta)" | `DECISIONS.md:1001,1011` | DOCS | reasoning-provenance |

## Feature 7 — Correlations

| # | Quote | Location | Audience | Claim-type |
|---|---|---|---|---|
| 7.1 | Tab badge tooltip: "**Low risk — pure measurement**" | `templates/ai_advisor.html:1012` | UI-LIVE | honest-scope (control — accurately signals no AI/LLM involvement) |
| 7.2 | "Correlations destabilize toward 1.0 in market stress." (crisis caveat, always-on) | `templates/ai_advisor.html:1343-1348` | UI-LIVE | honest-scope |

**Note:** no reasoning-provenance claim found anywhere for this feature, in UI or docs — matches the brief's expectation ("verify it claims nothing more").

## Feature 8 — Autotune guardrail advisors (Overfitting Conscience / Spec Critic / Divergence Explainer)

| # | Quote | Location | Audience | Claim-type |
|---|---|---|---|---|
| 8.1 | "Overfitting Conscience and Spec Critic are **guardrails that check the shared, frozen THEORY spec** — a uniform CLEAR verdict is the healthy expected result, not a stub. Per-symphony recommendations come from the **Run Advisor**" | `templates/ai_advisor.html:2204-2208` | UI-LIVE | honest-scope (control — explicitly disambiguates rule-based guardrails from the LLM-backed Run Advisor) |
| 8.2 | OC doc: "characterises... by examining the BACKTEST_SELECTION accumulator (S counter) relative to N_effective" — "**Pure function**. No DB side-effects." Three named deterministic indicators I-1/I-2/I-3 | `docs/generated/advisors_overfitting_conscience.md:3,10,26` | DOCS | honest-scope (control — no LLM keyword anywhere in the doc) |
| 8.3 | Spec Critic doc: four named deterministic indicators I-1..I-4, "**Pure function**" | `docs/generated/advisors_spec_critic.md:3,25` | DOCS | honest-scope (control) |
| 8.4 | Divergence Explainer doc: "**Pure function**", feature-flag-gated, forbidden-keys list enforced structurally | `docs/generated/advisors_divergence_explainer.md:3,26` | DOCS | honest-scope (control) |

**Note:** this is the cleanest feature in the inventory — UI and docs agree, both explicitly rule-based, no overclaim anywhere found.

## Feature 9 — Weekly suggestions scheduler / candidate-alert badge

| # | Quote | Location | Audience | Claim-type |
|---|---|---|---|---|
| 9.1 | Header badge tooltip default: "**No weekly run yet**"; badge shows a bare count, no reasoning claim | `templates/_chrome.html:60-72` | UI-LIVE | honest-scope (control — pure count/status indicator) |

## Feature 10 — Gate engine (cross-cutting)

| # | Quote | Location | Audience | Claim-type |
|---|---|---|---|---|
| 10.1 | "Selected on backtest across {{ n_cand }} candidates. FDR correction applied ({{ n_cand }} tested, threshold Yekutieli-adjusted). Overfitting risk cannot be eliminated — only resisted." | `templates/ai_advisor.html:1904` | UI-LIVE | statistical-rigor (renders per-survivor-card across Logic Changes, Asset Swaps, and Strategy Builder — the single most-repeated statistical claim in the UI. **audit-stats (2026-07-13T19:06, confirmed final 19:17): the underlying BHY/Yekutieli math is DEFENSIBLE — correctly computed, not fabricated. The correction target is narrower: this string's implied cardinality is wrong (invites reading "a controlled-discovery SET was returned" when the set size is always <=1), not its statistical method. Staged replacement text below.**) |
| 10.2 | "**Gate withheld:** this candidate did not clear the FDR correction threshold." / "Selected on backtest: gate withheld this candidate — selection-bias risk exceeds the FDR correction threshold." | `templates/ai_advisor.html:1996,2002` | UI-LIVE | statistical-rigor |
| 10.3 | "FDR integrity invariant: `evaluate_candidate_batch` must receive ALL successfully-backtested candidates — built-new (Opus) and Atlas-suggested together — in one call... `n_effective = len(candidates)` is the **honest** multiple-testing count" | `docs/generated/advisors_backtest_gate_engine.md:156` | DOCS | statistical-rigor — **audit-stats (2026-07-13T19:06): accurate for HOW `n_effective` is computed, but doesn't disclose that the two OTHER gate-strengthening checks (PBO veto, SPY-OOS baseline) are wired ONLY for Strategy Builder — confirmed zero `dated_returns=`/`spy_returns_fn=` at any of `asset_swap_engine.py:1047,1075,1277` or `logic_change_engine.py:1289,1316,1466`. If this doc implies uniform gate strength across all three features, that's a second correction target (staged below).** |
| 10.4 | "Identical fresh return series produce identical gate verdicts regardless of provenance" | `docs/generated/advisors_backtest_gate_engine.md:176` | DOCS | statistical-rigor |
| 10.5 | **Render-path confirmation (resolves audit-lead's F5 `[confirm render path]` open item, 2026-07-13):** the `sb_withheld` block (`templates/ai_advisor.html:1957-2010`) renders the SAME two hardcoded strings (10.2's quotes) for EVERY rejected candidate — verified via grep that `rejection_reason` (the field `backtest_gate_engine.py` computes with real precedence `pbo_veto` / `below_spy_alpha` / `fdr_not_winner`) is **never read or rendered anywhere in `ai_advisor.html`** (zero matches). A candidate whose own `p_adj[idx] <= HARVEY_LIU_FDR_Q` (i.e. it individually cleared the FDR-adjusted threshold) but lost the argmin tie-break to a better candidate still gets this exact "did not clear the FDR correction threshold" text — the wrong statistical claim for that subset, not merely an imprecise one. PBO-vetoed and SPY-baseline-missed candidates render identically; the operator cannot distinguish any of the three rejection classes from the UI. | `templates/ai_advisor.html:1957-2010` (no `rejection_reason` reference anywhere in file) | UI-LIVE | statistical-rigor |

---

## Staged Phase-2 correction targets (received from teammates 2026-07-13T19:01-19:18, cross-challenge round CLOSED — NOT yet approved by audit-lead's FINAL verdict, do not apply)

These are drafted-elsewhere suggestions relayed to me by the named auditor, held here so Phase 2 can move immediately once audit-lead removes the DRAFT banner. **I have not endorsed or independently re-verified the specific replacement wording** — only the underlying facts, which are cited to their originating auditor above. All four teammates have confirmed their entries are accurately captured and closed the cross-challenge thread (audit-stats 19:17, audit-reasoning 19:17, audit-data 19:17) — no further sharpening expected from their side; only audit-lead's final verdict can still change these.

| Target | Current text | Suggested correction | Source |
|---|---|---|---|
| `templates/ai_advisor.html:1904` (10.1) | "FDR correction applied ({{n_cand}} tested, threshold Yekutieli-adjusted)" | "tested against an FDR-calibrated significance bar accounting for N alternatives; only the single strongest candidate is ever promoted per run" — keeps the math claim accurate, fixes the implied cardinality | audit-stats, 2026-07-13T19:06, confirmed final 19:17 |
| `docs/generated/advisors_backtest_gate_engine.md:156` (10.3) | "the honest multiple-testing count" (no PBO/SPY-OOS scope caveat) | add a caveat noting PBO veto + SPY-OOS baseline are wired ONLY for Strategy Builder, not Asset Swaps/Logic Changes | audit-stats, 2026-07-13T19:06, confirmed final 19:17 |
| `templates/ai_advisor.html:1718-1720` (3.1) | "The advisor generates candidate symphonies from templates..." | "Opus generates candidate strategies (plus ranked community strategies from the Atlas library), compiles + backtests each, and applies FDR/Yekutieli correction." **REQUIRED PAIRING (not optional — the load-bearing half per audit-reasoning's 19:17 closing note):** add a run-level provenance line, e.g. "N built-new by Opus, M from community library" (new UI element, not just a copy edit) — without it, an Atlas-only-because-Opus-produced-zero-plans run still reads as an ordinary success | audit-reasoning, 2026-07-13T19:08, reinforced + closed 19:17 |
| `docs/generated/advisors_asset_swap_engine.md` (2.2/2.3) | "lens-informed ranking" framed without route scope | **Two-part framing required (audit-data's 19:17 reinforcement — a weekly-only caveat alone is insufficient and could still let a reader infer the operator button is lens-informed):** (a) the operator EVALUATE route is entirely lens-BLIND — `lens_scores` is never passed, always `None` (`app.py:4312` / signature default `asset_swap_engine.py:996`) — zero lens influence on the operator-clicked swap; AND (b) even where `lens_scores` IS wired (weekly path only), the blend is single-lens (`technicals.momentum`), 0.25-weighted, and never gate-affecting | audit-data, 2026-07-13T19:10, reinforced + closed 19:17 |
| `docs/generated/prism_scheduler.md:25` (6.5) | "$5–10/run" (stated as fact) | no text change suggested yet — label as "plausible vs `MAX_BUDGET_USD=15.0` cap, not runtime-verified" if it needs a qualifier; go/no-go on live-verifying routed to audit-lead/PM | audit-data, 2026-07-13T19:10, confirmed final 19:17 |

---

## Addendum — winner-take-all single-survivor cap vs plural UI/doc language (requested by audit-lead, 2026-07-13)

**Request:** audit-lead asked for the exact SB UI copy implying multiple gated proposals, cross-referenced against a code-confirmed structural cap of exactly ONE `ADOPT_CANDIDATE` survivor per `evaluate_candidate_batch` call ("backtest_gate_engine.py:788-830, winner-take-all argmin selection — PM-confirmed").

**Code verification performed (read-only, this worktree, `05da181b`+`audit/advisor-intent`):**

1. `advisors/backtest_gate_engine.py:788` — `best_i = min(veto_eligible_indices, key=lambda i: p_adj[i])` — a single argmin over the whole batch. `winner_idx` (line 784) is `int | None`, never a list.
2. `advisors/backtest_gate_engine.py:830` — `this_winner_trial_is_none = idx != winner_idx` — every candidate EXCEPT the single argmin winner gets `winner_trial_is_none=True`.
3. `acceptance_gate.py:209-214` — `vetoes_passed = (not winner_trial_is_none) and nn1_compliant and purge_integrity_ok and _pbo_veto_passed`. Any candidate with `winner_trial_is_none=True` (i.e. every non-winner) fails `vetoes_passed` unconditionally — `panel_score` is never computed for it (line 221-227, "the panel is NEVER computed on a veto-failed candidate"), and `decision=DECISION_REJECT_VETO_FAILED` is forced regardless of that candidate's own OOS alpha or panel score.
4. `acceptance_gate.py:257-264` — only the single winner_idx candidate can reach the `DECISION_ADOPT_CANDIDATE` branch, and even it must additionally clear `oos_alpha > both baselines` AND the panel-score margin.
5. `advisors/backtest_gate_engine.py:905` — `survivors = [r for r in results if r.verdict.decision == "ADOPT_CANDIDATE"]` — by (1)-(4), this list is **structurally bounded to length 0 or 1** for any single call to `evaluate_candidate_batch`, regardless of how many candidates (up to `MAX_CANDIDATES_PER_RUN=30` built-new + `MAX_COMMUNITY_CANDIDATES_PER_RUN=20` community = 50) were backtested.
6. Call-site count check: `advisors/strategy_builder_engine.py:867` is the **sole** `evaluate_candidate_batch` call site inside `propose_strategies()`. The on-demand route (`app.py:4642`, `POST /ai-advisor/strategy-builder/run`) calls `propose_strategies()` exactly once per request, with a single `objective` (operator-selected or default `"diversify"` — never a multi-objective fan-out on this route). **Conclusion: a single "Run analysis" click can structurally never surface more than 1 gated Strategy Builder proposal.**
7. **Generalization beyond Strategy Builder** — CONFIRMED by audit-stats independently (2026-07-13T19:06, via the PBO-never-fires angle, exact matching call sites: `asset_swap_engine.py:1047,1075,1277`; `logic_change_engine.py:1289,1316,1466`) and by audit-lead's DRAFT F2. My own trace: `advisors/asset_swap_engine.py` and `advisors/logic_change_engine.py` each call `evaluate_candidate_batch` from separate call sites for their two modes (operator-initiated single-candidate: `asset_swap_engine.py:1075` / `logic_change_engine.py:1316`, each passing a single-element list; advisor-suggested batch: `asset_swap_engine.py:1277` / `logic_change_engine.py:1466`, each passing the full candidate list) — self-documented at `logic_change_engine.py:38-42` and `asset_swap_engine.py:1267`. The identical argmin/veto logic in steps 1-5 applies to every one of those calls too. **Logic Changes' and Asset Swaps' advisor-suggested modes are ALSO capped at 1 survivor per run** — now independently corroborated by three teammates (myself, audit-stats, audit-lead) via three different analytical angles; audit-stats explicitly flagged (2026-07-13T19:17) this two-angle convergence as evidence that should carry weight in the final verdict.
8. **Render-path confirmation (2026-07-13, resolves audit-lead's F5 open item):** see inventory entry 10.5 above — `rejection_reason` is never surfaced in `ai_advisor.html`; all three rejection classes (PBO veto / below-SPY-baseline / lost-the-argmin) render identical generic "did not clear the FDR correction threshold" copy.

**UI/doc language implying plural output (verbatim, file:line):**

| Quote | Location | Implies |
|---|---|---|
| "Only candidates **that clear the gate** are surfaced **as proposals**." | `templates/ai_advisor.html:1719-1720` | plural — "candidates" (subject) + "proposals" (object) both unqualified plural nouns |
| `data-testid="sb-live-survivor-cards"` / `.proposal-cards` class; `sv.map(function (s) { return card(s, 'survivor'); })` | `static/ai_advisor.js:758` | plural — array-map rendering architecture, plural testid/class naming |
| "`screened_survivors` is a subset of `gated_batch.survivors`" | `feature-plans/strategy-builder-real.completed.md:112` | plural — "survivors" (no doc anywhere states the practical/structural max is 1) |
| "0 survivors is a VALID outcome, not an error" | `feature-plans/strategy-builder-real.completed.md:224` | plural framing of the outcome space (0 vs. unstated-but-implied "some") — never states "0 or 1, never more" |
| Module docstring: "gates via Harvey-Liu FDR + C5b PBO veto + SPY-OOS baseline, and **persists survivors** as advisory observations" | `docs/generated/advisors_strategy_builder_engine.md:3` | plural |

**Internal inconsistency noted (not requested, found while pulling the above):** the SB empty-state copy (3.10, `ai_advisor.html:1784`) uses **singular** "No **candidate** cleared the gate this run" — a few lines below the plural "candidates ... are surfaced as proposals" (3.1, `ai_advisor.html:1719-1720`) in the same tab panel. Whoever wrote the empty-state may have been implicitly aware the realistic outcome is 0-or-1, while the run-controls-note above it was not adjusted to match. Both quotes are in the same template file, ~65 lines apart.

**No claim made here about whether "plural" framing is itself misleading** — that judgment (bar #2 statistical-rigor / bar #4 honest-UI) belongs to audit-stats and audit-lead. This addendum only establishes: (a) the code-level cap is real and mechanically forced, with exact file:line for every step of the chain; (b) the UI/doc language never states that cap; (c) the generalization to Logic Changes/Asset Swaps is now independently corroborated by three teammates via three different analytical angles (my call-site trace, audit-stats' PBO-wiring trace, audit-lead's DRAFT F2).

Notified: audit-lead (this addendum answers the 2026-07-13T18:57 request; 10.5/point 8 answers F5's open render-path item).

---

## Summary — where the false/misleading-claim risk concentrates

1. **Highest severity, UI-LIVE:** the page-level subtitle "Claude-powered suggestion engine" (5.1, `ai_advisor.html:988`) — scoped to the whole page, not one tab. **FINAL grading (audit-reasoning, closed 19:17): TRUE for Run Advisor/Chat/Market Prism, FALSE for Logic Changes/Asset Swaps, MISLEADING for Strategy Builder. "Good to lock."** This single line is the most likely root cause of the operator's stated distrust — it is the FIRST thing rendered on the page. Corroborated by audit-lead's DRAFT F1 (inverted reasoning attribution).
2. **Cross-doc contradiction, now RESOLVED by R-F3:** Strategy Builder UI copy (3.1, dated 2026-06-13, describes the pre-Opus 7-template stamper) vs. `docs/generated/advisors_build_plan_generator.md` + `DECISIONS.md` DE-SB-GEN-001 (dated 2026-06-20, describes the real Opus replacement). **audit-reasoning's R-F3 verdict: the route defaults to real Opus generation; the stale copy is NOT accidentally honest — it must be corrected toward "Opus + Atlas," with a run-level provenance line as the load-bearing half of the fix, not an optional extra** (see Staged Corrections). Tracked as audit-lead's DRAFT F6.
3. **Winner-take-all vs. plural framing (Addendum, code-verified, now triple-corroborated):** every UI/doc description of the gate implies a possibly-many output, when the shared `acceptance_gate`/`backtest_gate_engine` machinery structurally caps `ADOPT_CANDIDATE` at exactly one candidate per `evaluate_candidate_batch` call — confirmed for Strategy Builder's on-demand route by a full call-path trace, and now confirmed for Logic Changes/Asset Swaps by three independent traces (mine, audit-stats', audit-lead's DRAFT F2). **audit-stats confirms the underlying BHY/Yekutieli math is DEFENSIBLE — this is a cardinality-framing defect, not a broken-math defect** (staged correction text above). Compounded by the render-path finding (10.5): rejected candidates never surface WHY they were rejected. Tracked as audit-lead's DRAFT F5.
4. **Statistical branding without full substance on Asset Swaps + Logic Changes (audit-stats + audit-data, 2026-07-13T19:06-19:17):** both features inherit the FDR/Yekutieli UI copy but not the PBO veto or SPY-OOS baseline wiring (SB-only), and the Asset Swaps operator route is permanently lens-blind (`lens_scores` always `None` on the reachable path — 2.5) while even the weekly path's lens influence is thin (single-lens, 0.25-weighted, non-gate-affecting). Staged corrections above require the full two-part framing, not a partial caveat.
5. **Opposite-direction finding:** Market Prism (6.1) never explicitly attributes itself to Claude/Opus in the rendered UI, despite having the most real, well-documented LLM pipeline in the suite. Under-attribution is also a bar #4 gap. Tracked as part of audit-lead's DRAFT F1.
6. **Clean control group:** Correlations (Feature 7) and the three autotune guardrail advisors (Feature 8) have zero overclaim anywhere in UI or docs — both explicitly self-describe as deterministic/rule-based. Useful as the audit's positive baseline for what an honest advisory feature's docs/UI should look like.

Notified: audit-reasoning, audit-stats, audit-data, audit-lead. Cross-challenge round CLOSED as of 2026-07-13T19:18 — all four teammates confirmed their entries and staged corrections are accurately captured; no further sharpening expected from their side. **Still blocked on audit-lead's FINAL (non-DRAFT) verdict before drafting `doc-reconciliation.md`.**
