# Advisor-Intent Audit — Doc Reconciliation (Phase 2)

**Author:** audit-doc | **Status:** DRAFTED, NOT APPLIED — every correction below is a proposal for the PM's remediation program (see `ADVISOR-INTENT-AUDIT.md` §"Remediation options", Gaps A-H). This file changes nothing by itself. All edits require the PM to dispatch implementation work through the normal Toxic-Pair TDD process — corrections that touch `.py` files are code changes, not doc-only changes, and several are paired with new UI elements (not pure copy edits).
**Source of truth:** `docs/audit/ADVISOR-INTENT-AUDIT.md` (audit-lead, FINAL, commit `e0321071` + post-gate accuracy revision `08b0bcc0` — "guardrail per-engine precision + fixture-verified T=121," no headline/top-5 change) + `docs/audit/claims-inventory.md` (this author, Phase 1 + staged corrections, cross-corroborated by all 4 auditors).
**Worktree:** `.claude/worktrees/advisor-intent-audit` @ branch `audit/advisor-intent`.

Every correction below cites: current text (verbatim) → corrected text (drafted) → owning finding (F#/Gap letter) → audience tier (UI-LIVE > DOCS > CONFIG, per the claims-inventory severity convention).

---

## Section 1 — UI copy corrections (`templates/*.html`, `static/*.js`) — highest severity, operator-facing

### 1.1 Page-level "Claude-powered" subtitle (F4, Gap D)

**File:** `templates/ai_advisor.html:987-989`
**Current:**
```
<h1 class="adv-title">AI Advisor</h1>
<p class="adv-subtitle">
    Claude-powered suggestion engine — operator must approve each change through safety gates
</p>
```
**Defect:** page-global banner renders above all 6 tabs. TRUE for 3 (Run Advisor, Chat, Market Prism — all real Opus). FALSE for 2 (Logic Changes, Asset Swaps — zero LLM). MISLEADING for 1 (Strategy Builder — only built-new candidates are LLM). The single most likely root cause of the operator's stated distrust (first thing rendered on page load).
**Corrected direction (Gap D, Tier 1 — "per-tab attribution instead of a page-global banner"):** remove the page-global subtitle claim of universal Claude-power. Replace with a neutral page description (e.g. "Advisory suite — operator must approve each change through safety gates," dropping "Claude-powered"), and add a per-tab badge/label on each of the 6 tab nav buttons indicating reasoning mode: e.g. `tab-logic-changes` and `tab-asset-swaps` get a "Deterministic" or "Rule-based" badge; `tab-strategy-builder` gets "Opus-generated (built-new) + community library"; `strategy-builder-tab`'s existing risk badge (`ai_advisor.html:1048`, "Highest overfitting risk") is a precedent pattern to extend for this purpose. `tab-chat` and the Overview tab (Market Prism block) should carry an explicit "Claude Opus" badge (see 1.2 below — currently ZERO attribution there).
**Effort:** Tier 1 (copy + small template change), no code-behavior change. Requires a UX pass on the 6-tab badge design, not a pure text edit.

### 1.2 Market Prism block — missing model attribution (F4, Gap D)

**File:** `templates/ai_advisor.html:1066-1273` (the entire `market-prism-block`)
**Current:** no line anywhere in the block names Claude, Opus, or any model. The block header is just `<span class="prism-block-title">Market Prism</span>` (line 1078).
**Defect:** the one genuinely real, best-documented LLM pipeline in the suite (real `claude -p` council, `prism_scheduler.py`) is the LEAST attributed surface in the UI — opposite failure mode from 1.1.
**Corrected direction:** add an explicit attribution line near the block header, e.g. a small `<span class="prism-model-badge">Synthesized by Claude Opus 4.8</span>` reading from a template context var (the model id is already resolvable server-side via `ADVISOR_SYNTHESIS_MODEL`/CLAUDE.md's `resolve_advisor_model()` accessor per `ai_advisor.py:63-69`, referenced in `CLOSEOUT-VERDICT.md`'s C2-COMMENT-1 finding — reuse that accessor, do not hardcode a new literal).
**Effort:** Tier 1, small template + one new context key from `app.py:ai_advisor_tab()`.

### 1.3 Strategy Builder run-controls-note — stale "from templates" copy (F5, Gap A/E; resolves the pre-verified cross-doc contradiction)

**File:** `templates/ai_advisor.html:1717-1721`
**Current:**
```
<p class="run-controls-note">
    The advisor generates candidate symphonies from templates, backtests
    each one, and applies FDR/Yekutieli multiple-testing correction. Only
    candidates that clear the gate are surfaced as proposals.
</p>
```
**Defect:** git-blamed `7908d77b0`, 2026-06-13 — describes the retired 7-template stamper (`_generate_candidate_trees`, T1-T7, dead on the reachable path since `DE-SB-GEN-001` shipped 2026-06-20). Omits the Atlas community-candidate source entirely. Also uses plural "candidates ... are surfaced as proposals" when the gate structurally caps `ADOPT_CANDIDATE` at exactly one survivor per run (see 1.5 below).
**Corrected text (audit-reasoning's wording, per R-F3 verdict — final, not accidentally-honest):**
```
The advisor generates candidate strategies via Claude Opus (plus ranked
community strategies from the Atlas library), compiles and backtests
each one, and applies an FDR/Yekutieli-calibrated significance bar.
Only the single strongest candidate that clears the bar is ever
promoted per run.
```
**REQUIRED PAIRING (F5, load-bearing per audit-reasoning — not optional):** this copy fix alone is insufficient. Add a run-level provenance line/banner rendered with EVERY run result (success or empty), e.g.:
```
This run: {{ built_new_count }} candidate(s) generated by Opus,
{{ atlas_count }} from the community library.
```
Without this, a run where Opus silently produced 0 plans (any of the 6 D-1 failure branches in `generate_build_plans`, `build_plan_generator.py:166/1096/1107/1111/1147/1155`) but Atlas candidates still populate the result renders as an ordinary success — the operator cannot tell built-new-only from a degraded Atlas-only run. Requires new fields threaded from `strategy_builder_engine.propose_strategies()` → `app.py:4642` route response → template (currently only per-candidate `template_id` distinguishes built-new vs community, `app.py:4689` — no run-level rollup exists).
**Effort:** Tier 1 for the copy; Tier 1-2 for the provenance line (new response fields + template + route-error cause surfacing, since the current route-error branch is a static `"strategy-builder-error"` token with no cause, `app.py:4669` — F5 also flags this should carry the real cause class, D-1-safe).

### 1.4 Strategy Builder / Logic Changes gate-strength claim (F2, Gap B — informational, not a copy fix by itself)

**Files:** `templates/ai_advisor.html:1533-1534` (Logic Changes) and `:1647-1650` (Strategy Builder) — "FDR/Yekutieli correction applied" risk-banner language.
**Defect:** these banners are accurate word-for-word (FDR/Yekutieli IS applied) but the underlying gate strength is NOT uniform: Strategy Builder alone has PBO veto + SPY-OOS baseline wired; Logic Changes and Asset Swaps gate on "beats a flat 0.0% return" with PBO structurally incapable of firing (F2). This is a **code fix** (Gap B, Tier 1: wire `dated_returns=`/`spy_returns_fn=` into the two engines' `evaluate_candidate_batch` calls, mirroring `DE-SB-CULL-001` + `strategy_builder_engine.py:807-826`), not a doc fix — flagging here so the copy is NOT "corrected" to a weaker claim when the actual right fix is closing the wiring gap. If the PM chooses NOT to close the wiring gap, THEN the copy needs a caveat distinguishing Strategy Builder's full gate from the other two engines' partial gate.

### 1.5 Gate-cardinality mislabel (F6, Gap F)

**File:** `templates/ai_advisor.html:1904`
**Current:** `Selected on backtest across {{ n_cand }} candidates. FDR correction applied ({{ n_cand }} tested, threshold Yekutieli-adjusted). Overfitting risk cannot be eliminated — only resisted.`
**Defect (audit-stats' formal lemma, `ADVISOR-INTENT-AUDIT.md` §A.3 — the math is DEFENSIBLE, this is a cardinality-implication defect):** invites a "controlled-FDP SET was returned" reading when the declared set is capped at exactly 1 by construction (`backtest_gate_engine.py:788-830`, argmin-only selection).
**Corrected text (audit-stats' final recommended string, `ADVISOR-INTENT-AUDIT.md:205`):**
```
Tested against an FDR-calibrated significance bar accounting for
{{ n_cand }} alternatives; only the single strongest candidate is
ever promoted per run.
```
**Effort:** Tier 1, pure copy edit — the underlying value (`n_cand`) is unchanged.

### 1.6 Rejection copy never distinguishes rejection class (F6, Gap F)

**File:** `templates/ai_advisor.html:1996` and `:2002` (the `sb_withheld` block, `:1957-2010`)
**Current:** `<strong>Gate withheld:</strong> this candidate did not clear the FDR correction threshold.` / `Selected on backtest: gate withheld this candidate — selection-bias risk exceeds the FDR correction threshold.`
**Defect (audit-doc 10.5, confirmed render-path):** `rejection_reason` (`pbo_veto` / `below_spy_alpha` / `fdr_not_winner`, computed with real stage-order precedence in `backtest_gate_engine.py`) is never read or rendered anywhere in `ai_advisor.html` (zero matches). A candidate that individually cleared the FDR-adjusted threshold but lost the argmin tie-break gets the SAME "did not clear the FDR correction threshold" text as a PBO-vetoed or SPY-baseline-missed candidate — the wrong statistical claim for that subset, not merely imprecise.
**Corrected direction:** render `{{ rr.get('rejection_reason') }}`-branched copy, e.g.:
- `pbo_veto` → "This candidate failed the overfitting-robustness (PBO) check."
- `below_spy_alpha` → "This candidate did not beat the SPY benchmark over the same period."
- `fdr_not_winner` → "This candidate did not clear the FDR-calibrated significance bar — OR cleared it but was not the single strongest candidate this run" (the winner-take-all caveat, since `fdr_not_winner` conflates both cases and the code does not currently distinguish them further — see Gap F Tier 2 below if a finer distinction is wanted).
**Effort:** Tier 1 — `rejection_reason` is already computed and persisted (`backtest_gate_engine.py` `CandidateGateResult.rejection_reason`); this is template-only work (add the field to the Jinja context, branch the copy). Note this same `sb_withheld` block and generic copy pattern likely also renders for Logic Changes and Asset Swaps rejected-candidate cards (shared macro/pattern per claims-inventory entry 1.2) — verify blast radius before implementing.
**Gap F Tier 2 (design decision, not a copy fix):** if the product wants multiple proposals per run instead of winner-take-all, admit all `p_adj <= q` candidates (a set-cardinality change) rather than argmin-only — PM Gate-2 scoping decision, out of scope for a copy correction.

### 1.7 Missing statistical-power caveat on survivor cards (F3, Gap C — NEW, not previously staged in claims-inventory)

**File:** `templates/ai_advisor.html:1904` area (survivor `caveat-text` block) and the equivalent Logic-Changes/Asset-Swaps survivor rendering.
**Defect:** audit-stats' detection-power derivation (`ADVISOR-INTENT-AUDIT.md` §A.2.5, §F3) shows near-zero statistical power at the gate's own minimum fold length (T=13) AND at a **fixture-verified real-symphony anchor T=121** (series_len=606, added in the post-gate accuracy revision): even a ~100%/yr edge yields N=1 detection 3.3-25% (T=13) / 46-98% (T=121, higher N=1 power at large assumed edges but still weak at realistic ones), while N=12 (the batch/BHY-corrected case that matters for gated results) detects **0% at every economically-plausible effect size across BOTH T anchors**, reaching only 13.3% at T=121 even at an implausible μ=0.40%/day. "Survived the gate" is being presented to the operator as a validated finding when it carries very little statistical evidentiary weight at realistic sample sizes, now confirmed robust across a genuine ~10x range of T (13 to 121 days), not just an illustrative extrapolation. No UI text currently communicates this. **Sub-finding also flagged by the audit:** the gate imposes no minimum-power or minimum-series-length guard — `composer_backtest_client.run_backtest` sends no lookback/date-range parameter, so a short-history symphony is silently gated at the near-powerless T≈13 floor.
**Corrected direction (Gap C, Tier 1):** add a caveat to every survivor card, e.g.: *"This candidate cleared the statistical gate, but the underlying backtest window may be too short to reliably distinguish genuine edge from noise. Treat as a candidate for further scrutiny, not a proven result."* Placement: alongside the existing `SURVIVOR_OVERFITTING_CAVEAT` caveat already rendered per `templates/ai_advisor.html:1904-1909`'s `caveats-block` — this is an ADDITIONAL caveat, not a replacement.
**Gap C Tier 2 (design decision):** require a minimum series length / min-power threshold before a survivor is presented as an edge at all — a code-level gate change, PM Gate-2 scoping. Directly actionable now that the audit confirms the gate has no such floor today.
**Effort:** Tier 1, copy addition to an existing caveats-block pattern.

### 1.8 Guardrails UI implies uniform behavior across 3 non-uniform engines (F8, matrix row 8 — NEW, from the post-gate accuracy revision)

**File:** `templates/ai_advisor.html:2204-2208`
**Current:** "Overfitting Conscience and Spec Critic are guardrails that check the shared, frozen THEORY spec — a uniform CLEAR verdict is the healthy expected result, not a stub. Per-symphony recommendations come from the Run Advisor."
**Defect (revised verdict, matrix row 8 + Appendix B.4 — supersedes the earlier "clean control group" read this audit's Phase 1 inventory gave Feature 8):** the three guardrail producers are NOT uniform. **Spec Critic is a genuine active control** (validates 4 structural indicators; an empty/missing-facet bundle correctly triggers BREACH, not a default CLEAR — so a CLEAR is a real evaluated pass). **Overfitting Conscience is a narrow, partly-dead finding** — its live CLEAR is legitimate (NN1 spec-freeze means s=0, so there genuinely is no selection-bias from the one DoF source it checks), BUT its I-3 operator-drift sub-indicator is structurally dead (needs ≥2 prior runs with non-NULL `s_count`; the code documents live `s_count` is always NULL), and the name "Overfitting Conscience" oversells a one-source check as general overfitting protection. **Divergence Explainer is dead** — `SECOND_WINDOW_CVAR_ENABLED` defaults off, so it writes a `NOT_APPLICABLE` stub row every time; the underlying divergence concept was "permanently rejected" per an internal council decision (`divergence_explainer.py:113`) — yet it still emits audit rows under the advisor banner and isn't excluded from the guardrails section named in this UI copy. The current UI copy names ONLY two of the three ("Overfitting Conscience and Spec Critic") — Divergence Explainer is omitted from the copy entirely, but its rows presumably still appear in the Advisor Observations table below this note (per `app.py`'s advisor-roles loop), so an operator seeing a DE row with no explanatory copy has no context that it's a permanently-rejected, structurally-inert feature.
**Corrected direction:** update the copy to name all three engines by their actual behavior, e.g.: *"Spec Critic is an active guardrail checking the shared, frozen THEORY spec structure — a CLEAR verdict means the spec was evaluated and passed. Overfitting Conscience checks one narrow overfitting-risk source (backtest-selection degrees of freedom) — a CLEAR here does not mean 'no overfitting risk exists,' only that this one source is clean. Divergence Explainer is disabled by default and its rows are informational-only, not currently monitoring anything active."*
**Effort:** Tier 1, copy correction — this is purely a template text change, the underlying deterministic behavior is unaffected. No code fix needed for this specific finding (unlike F2/F3/F5/F6 above, which pair copy fixes with wiring/design gaps).

---

## Section 2 — `docs/generated/*.md` corrections

### 2.1 `docs/generated/advisors_backtest_gate_engine.md:156` (F2, F6)

**Current:** `FDR integrity invariant: evaluate_candidate_batch must receive ALL successfully-backtested candidates — built-new (Opus) and Atlas-suggested together — in one call... n_effective = len(candidates) is the honest multiple-testing count.`
**Defect (audit-stats):** accurate for HOW `n_effective` is computed, but silent on scope — implies uniform gate strength across all three engines when PBO veto + SPY-OOS baseline are wired ONLY for Strategy Builder (confirmed zero `dated_returns=`/`spy_returns_fn=` at `asset_swap_engine.py:1047,1075,1277` and `logic_change_engine.py:1289,1316,1466`).
**Corrected addition (append, do not remove existing text):**
```
**Wiring scope caveat:** the PBO overfitting veto and the SPY-relative
OOS baseline — the two OTHER gate-strengthening checks beyond BHY/FDR —
are wired only for Strategy Builder's evaluate_candidate_batch call
sites. Asset Swaps and Logic Changes call sites pass neither
dated_returns= nor spy_returns_fn=, so their PBO veto never fires and
their OOS baseline is "beats a flat 0.0% return," not SPY-relative.
See ADVISOR-INTENT-AUDIT.md F2 for the full trace.
```

### 2.2 `docs/generated/advisors_asset_swap_engine.md` (F2, F4 — two-part framing required per audit-data's final reinforcement)

**Current (§14-18):** "lens-informed ranking" via `_apply_lens_blend`; "Lens scoring influences ranking only — the BHY-FDR gate is unchanged."
**Defect:** true as a description of the mechanism WHEN IT FIRES, but silent on reachability — a reader could infer the operator-clicked evaluate button is lens-informed. It is not.
**Corrected addition (two-part, per audit-data's explicit reinforcement — a weekly-only caveat ALONE is insufficient):**
```
**Reachability caveat (two parts — both required for an honest
picture):**
(a) The operator-clicked evaluate route (POST /ai-advisor/asset-swaps/evaluate,
app.py:4240->4312) never passes lens_scores to propose_operator_swap
(signature default None, asset_swap_engine.py:996) — on that surface,
_apply_lens_blend is a permanent no-op and lens_evidence persists as {}.
Zero lens influence on any operator-clicked swap.
(b) Even where lens_scores IS wired (the weekly scheduler path only,
via weekly_suggestions_scheduler.py), the blend reads a SINGLE lens
(technicals.momentum only — sentiment/derivatives/macro are excluded
as market-wide scalars, fundamentals excluded by design;
extract_lens_scores, asset_swap_engine.py:129-202), weighted 0.25, and
never affects the gate itself (ranking-influence only).
```
Also correct the `advisor_chat.py:144` oversell this doc's framing feeds into — see Section 3.2.

### 2.3 `docs/generated/advisors_logic_change_engine.md:46` (F7 — false claim, HIGH priority)

**Current (API Reference table row):** `| measured_value | float | The live backtest measurement driving this objective (e.g. current max-drawdown, current Sharpe) — never a hardcoded heuristic |`
**Defect:** FALSE in every production caller. `measured_value` is hardcoded `0.0` at four production call sites total, spanning BOTH Logic Changes and Asset Swaps (`app.py:4308` — `SwapObjective(measured_value=0.0)` in the asset-swaps-evaluate route; `app.py:4451` — `LogicChangeObjective(measured_value=0.0)` in the logic-changes-evaluate route; `weekly_suggestions_scheduler.py:136,382` — both weekly paths). For Logic Changes specifically, it is read ONLY inside display f-strings (`logic_change_engine.py:491,499,504,509,514,519,524` and `773-807`) — the actual tweak-generation branch (`generate_objective_directed_candidates`, `:531-762`) selects by `objective.objective_type` (`:602`), NOT by `measured_value`. Zero effect on tweak direction/magnitude. The operator-facing rationale literally reads "targets reduction of the measured 0.0% drawdown …" — a fabricated-looking number that is always exactly 0.0, never a real measurement. **The identical pattern exists in Asset Swaps** (`SwapObjective.measured_value`, both the field comment at `asset_swap_engine.py:164` and the class docstring at `:225-228`, read only in `_build_swap_rationale`'s display f-strings at `:703-729`, e.g. "the measured 0.00 correlation" per `ase.py:710`) — this parallel was independently found by this author during Phase-2 drafting AND confirmed by audit-lead's post-gate revision (F7 now explicitly names `ase.py:225-227`). See 3.1/3.3 below; that field has no corresponding `docs/generated` API-table entry to correct (`advisors_asset_swap_engine.md` doesn't document it), only the code-level comment/docstring.
**Corrected text:**
```
| measured_value | float | Display-only value embedded in the
generated rationale text (e.g. "targets reduction of the measured
X% drawdown"). Does NOT influence tweak direction or magnitude — the
tweak generator selects by objective.objective_type alone. Every
current production caller passes 0.0 (never a live backtest
measurement) — see ADVISOR-INTENT-AUDIT.md F7. The identical pattern
exists in Asset Swaps' SwapObjective.measured_value. |
```
**Companion code-docstring fix:** see Section 3.1 (the source docstring this doc was presumably generated from carries the same false claim and should be corrected at the source, or the next doc-gen regeneration will reintroduce the error).

### 2.4 `docs/generated/advisors_build_plan_generator.md:10` (F1, F5 — context-blindness caveat, not a false claim but an incomplete one)

**Current:** "advisors/build_plan_generator.py is the Opus-backed brain of the real Strategy Builder... replacing the 7-template stamper."
**Assessment:** TRUE as a mechanism description (real Opus SDK structured tool-use, confirmed `build_plan_generator.py:1078-1084`). NOT false — but incomplete in a way that matters for intent-fidelity: it doesn't disclose that the prompt itself never sees the operator's symphony, portfolio, backtest stats, or lens data (`_build_generation_prompt`, `build_plan_generator.py:947-1030` = objective name + DSL grammar + 3 static examples + `sorted(membership)[:20]` sample tickers only).
**Corrected addition (append):**
```
**Context-blindness caveat:** the generation prompt does NOT include
the operator's live symphony tree, portfolio composition, backtest
statistics, or any of the 5 market lens blocks — only the requested
objective name, the DSL grammar, three static worked examples, and a
20-ticker sample of the tradeable universe. Strategy Builder proposes
NEW strategies from scratch; it does not reason about the operator's
EXISTING symphony. See ADVISOR-INTENT-AUDIT.md F1 for the full trace
and F5 for the silent-degradation risk when generation fails.
```

---

## Section 3 — Code docstring/comment corrections (out of the doc-writer's normal lane, drafted because the audit explicitly names these as stale/overselling claims — PM decides whether to route through a code-fix cycle or accept as-is)

### 3.1 `advisors/logic_change_engine.py:206-209` (F7 — the source of the docs/generated error in 2.3)

**Current (docstring, quoted verbatim by the audit):** "`measured_value` ... always a measurement from the live backtest stats — never a hardcoded heuristic."
**Corrected text:** mirror the corrected `docs/generated` table row in 2.3 above — describe `measured_value` as display-only rationale text, not a claim about its provenance being always-live.
**Note:** this is a one-line-plus-context docstring edit inside a `.py` file — code, not `docs/generated`. Flagging per the audit's explicit naming (Gap G, "fix the false docstring") but this is properly a code-change ticket for the remediation program, not something audit-doc applies directly.

### 3.2 `advisors/advisor_chat.py:144` (F4 sharpening)

**Current (inline comment on the `CHAT_ARTIFACT_ALLOWED_FIELDS` allowlist entry):** `"lens_evidence",  # {lens: signal} dict from multi-lens overlay`
**Defect:** "multi-lens overlay" oversells the mechanism — `extract_lens_scores` (`asset_swap_engine.py:129-202`) reads only `technicals.momentum`; sentiment/derivatives/macro are excluded as market-wide scalars and fundamentals is excluded by design (see 2.2 above).
**Corrected text:** `"lens_evidence",  # {lens: signal} dict from the (currently single-lens: technicals.momentum only) blend`

### 3.3 `advisors/asset_swap_engine.py:164` AND `:225-228` (F7 — two instances, both confirmed)

**Current (field-level comment, `:164`):** `measured_value: float  # Measured input driving this objective — never hardcoded wisdom`
**Current (class docstring, `:225-228`):** `measured_value: / The measured input driving this objective (e.g., the measured correlation coefficient, the measured max-drawdown magnitude). Never hardcoded wisdom — always a measurement from the live correlation diagnostic or backtest stats.`
**Defect:** same false-provenance claim as 3.1, for the same reason: `app.py:4308` constructs `SwapObjective(measured_value=0.0, ...)` in the only production route (asset-swaps-evaluate), and the field is read only inside `_build_swap_rationale`'s display f-strings (`asset_swap_engine.py:703-729`, e.g. `f"({obj_type}, measured_value={measured})."` at `:729`, and "the measured 0.00 correlation" per `ase.py:710`) — never used to steer candidate generation or ranking.
**Corrected text (both locations, same substance):** describe `measured_value` as display-only rationale text; does not steer generation/ranking; every current production caller passes 0.0.
**Verification note:** I found the `:164` field comment independently while drafting 2.3, before seeing the audit's revision. audit-lead's post-gate accuracy revision (`08b0bcc0`) independently confirmed the same parallel defect, citing the `:225-227` class docstring specifically — two independent traces converging on the same module, different exact lines within it (the field comment and the class docstring both carry the claim and both need the fix).

---

## Section 4 — `.claude/CLAUDE.md` key-files table corrections (drafted, NOT applied — PM approval required before any CLAUDE.md edit per project convention)

The project CLAUDE.md key-files entries for the advisor modules are largely ACCURATE about mechanism (they correctly say "Claude-backed", "real Opus SDK structured tool-use", etc. — none of the entries this audit reviewed contain a factually false claim about which modules call an LLM). The gap is completeness, not falsehood: the entries don't carry the context-blindness, silent-degradation, or statistical-substance caveats this audit surfaces. Recommended additions (append to existing entries, do not rewrite):

- **`ai_advisor.py` entry:** already accurate ("Claude-backed config advisor"). No change needed — this is the one feature (Run Advisor) where the page subtitle claim is TRUE.
- **`advisors/build_plan_generator.py` entry:** append a short context-blindness note (mirrors 2.4): *"Generation prompt is objective+grammar+examples+ticker-sample ONLY — no symphony/portfolio/backtest/lens data; Strategy Builder proposes new strategies, it does not reason about the operator's existing one."*
- **`advisors/strategy_builder_engine.py` entry:** append a silent-degradation note (mirrors 1.3/F5): *"6 D-1 failure branches in build_plan_generator can each silently reduce a run to Atlas-only community candidates with run.error=None — the only signal is per-candidate template_id; no run-level indicator exists yet (see ADVISOR-INTENT-AUDIT.md F5, Gap E)."* Also note `live_returns=[]` is hard-coded on the route (`app.py:4642-4649`), silently skipping the blended-drawdown/live-Pearson screens, and the compiler's tradeability-repair loop is dead on the route (no `backtest_fn` passed to `compile_plan`, `strategy_builder_engine.py:379`).
- **`advisors/asset_swap_engine.py` / `advisors/logic_change_engine.py` entries (currently under the general `advisors/` row, not broken out individually):** if these get their own CLAUDE.md rows in a future update, they should explicitly state "zero LLM, direct or indirect, on every reachable path" and "PBO veto structurally cannot fire; gates on beats-a-flat-0.0%-return, not SPY-relative" — this codebase's convention of naming exactly what a module does NOT do (see the extensive D-1/never-raises/off-execution-path pattern already used throughout the table) applies well here.
- **`advisors/overfitting_conscience.py` / `advisors/spec_critic.py` / `advisors/divergence_explainer.py` entries (if broken out individually in the future):** per the post-gate revision's per-engine breakdown (F8, Appendix B.4), note Spec Critic is a genuine active control, Overfitting Conscience checks one narrow DoF source with a structurally-dead drift sub-indicator, and Divergence Explainer is dead-by-default (feature flag off) yet still emits `NOT_APPLICABLE` audit rows.
- **General:** consider whether the `.claude/CLAUDE.md` should gain a short cross-reference line pointing to `docs/audit/ADVISOR-INTENT-AUDIT.md` as the canonical source for "what actually reasons vs what's deterministic" across the advisor suite, given how much drift this audit found between UI/doc claims and reachable-path reality.

---

## Section 5 — Historical docs needing a superseded banner

### 5.1 `docs/audit/CLOSEOUT-VERDICT.md` (dated 2026-06-17) — HIGH priority

**Why this needs a banner, not a rewrite:** this prior audit verified the AI Advisor system "END-TO-END with ZERO closeout-blocking code defects" and gave clean PASS verdicts to Cluster 2 (F22-F40, the AI Advisor suite) including:
- "F28-F30 Correlations / Asset Swaps / Logic Changes: route+gate+blend PASS"
- "F32-F34 Strategy Builder: PASS (template-only in prod = HF-1)"
- "F35-F37 Community + gate infra: F36/F37 PASS"

These verdicts were TRUE for the narrower question that closeout audit asked (does the route exist, does it call the right function, does a gate run at all) at the state of the code on 2026-06-17 — they are not fabricated or wrong findings. But two things have since changed the picture:
1. **Facts changed:** Strategy Builder's "template-only in prod" (HF-1) was fixed 3 days later by `DE-SB-GEN-001` (2026-06-20) — the community-candidate route wiring HF-1 flagged as hollow is now wired. A reader today would be reading a stale "template-only" characterization that is no longer accurate (though it WAS accurate 2026-06-17 to 2026-06-20).
2. **Scope was narrower than intent-fidelity:** "route+gate+blend PASS" verified structural wiring, not whether the gate has any statistical teeth (PBO/SPY wiring — this audit's F2) or whether any reasoning happens over the operator's symphony at all (F1). A reader coming to `CLOSEOUT-VERDICT.md` today, without ever seeing `ADVISOR-INTENT-AUDIT.md`, would reasonably but incorrectly conclude these features satisfy the operator's original intent.

**Drafted banner (to insert at the top of the file, below the title, before "Synthesis lead:"):**
```
> **SUPERSEDED — 2026-07-13.** This closeout verified STRUCTURAL
> wiring (do routes exist, do they call the right function, does a
> gate run) as of 2026-06-17. It did NOT evaluate whether the advisor
> suite delivers the operator's actual intent — genuine LLM reasoning
> over the live symphony, and statistically meaningful gating. For
> that assessment, see `docs/audit/ADVISOR-INTENT-AUDIT.md`
> (2026-07-13), which found: Logic Changes and Asset Swaps are 100%
> deterministic with no LLM on any reachable path (F1); their FDR
> gates lack the PBO veto and SPY-relative baseline Strategy Builder
> has (F2); and Strategy Builder's "template-only in prod" finding
> below (HF-1) was resolved 2026-06-20 by DE-SB-GEN-001, three days
> after this closeout — Strategy Builder now defaults to real Opus
> generation. The structural-wiring findings below remain historically
> accurate for their stated scope and date; do not read them as a
> verdict on reasoning fidelity or statistical substance.
```

### 5.2 Other `docs/audit/` files reviewed — no banner needed

- `docs/audit/security-review.md` — different scope entirely (credential/key-handling security audit, not reasoning fidelity). No contradiction with this audit's findings.
- `docs/audit/vision-audit-2026-05-27/vision-findings.md` — predates the M1-M4 AI Advisor suite (Logic Changes/Asset Swaps/Strategy Builder/Chat); its advisor-related content is about the Sprint-3 guardrail producers (Overfitting Conscience/Spec Critic/Divergence Explainer), and while its "Three advisors, not four ... don't claim 4 advisors" framing is still directionally consistent with the post-gate revision's finding that DE is dead-by-default, this audit's per-engine breakdown (1.8/F8) is more precise (DE isn't just "dormant," it's permanently-rejected-but-still-emitting-rows). Not a contradiction, but worth a cross-reference note rather than a banner if this file is ever revisited.
- `docs/audit/sprint-1-cross-cycle-audit.md`, `sprint-2-cross-cycle-audit.md`, `sprint-3-port-removal-manifest.md`, `docs/audit/final-audit-2026-05-29/` — none reference the AI Advisor suite (Logic Changes/Asset Swaps/Strategy Builder/Chat/Run Advisor/Market Prism) by name; out of scope.

---

## Section 6 — Summary correction checklist (for the PM's remediation-program scoping)

| # | Target | Type | Effort tier | Owning finding |
|---|---|---|---|---|
| 1.1 | `ai_advisor.html:987-989` page subtitle | UI copy + new per-tab badges | 1 (copy) / needs UX pass (badges) | F4, Gap D |
| 1.2 | `ai_advisor.html:1066-1273` Market Prism attribution | UI copy + new template context key | 1 | F4, Gap D |
| 1.3 | `ai_advisor.html:1717-1721` SB "from templates" copy | UI copy + new provenance-line UI element (required pairing) | 1 (copy) / 1-2 (provenance line) | F5, Gap A/E |
| 1.4 | FDR banner uniformity (Logic Changes/Strategy Builder) | code fix (preferred) or copy caveat | code: 1 (wiring exists to copy); copy: 1 | F2, Gap B |
| 1.5 | `ai_advisor.html:1904` gate-cardinality string | UI copy (exact text drafted) | 1 | F6, Gap F |
| 1.6 | `ai_advisor.html:1996/2002` rejection copy | UI copy, branch on existing `rejection_reason` field | 1 | F6, Gap F |
| 1.7 | Survivor-card power caveat | UI copy addition | 1 | F3, Gap C |
| 1.8 | `ai_advisor.html:2204-2208` guardrail-uniformity copy | UI copy (exact text drafted) | 1 | F8 |
| 2.1 | `advisors_backtest_gate_engine.md:156` | docs/generated addition | 1 | F2, F6 |
| 2.2 | `advisors_asset_swap_engine.md` | docs/generated addition (two-part) | 1 | F2, F4 |
| 2.3 | `advisors_logic_change_engine.md:46` | docs/generated correction (false claim) | 1 | F7 |
| 2.4 | `advisors_build_plan_generator.md:10` | docs/generated addition | 1 | F1, F5 |
| 3.1 | `logic_change_engine.py:206-209` | code docstring fix | 1 (code-change ticket) | F7 |
| 3.2 | `advisor_chat.py:144` | code comment fix | 1 (code-change ticket) | F4 |
| 3.3 | `asset_swap_engine.py:164,225-228` | code comment + docstring fix (two instances) | 1 (code-change ticket) | F7 |
| 4.* | `.claude/CLAUDE.md` key-files table | CONFIG additions (PM approval required) | 1 | F1, F5, F8 |
| 5.1 | `docs/audit/CLOSEOUT-VERDICT.md` | superseded banner | 1 | cross-cutting |

**Not a doc correction — code-level Gate-2 scoping decisions for the PM (see `ADVISOR-INTENT-AUDIT.md` Gaps A/B/C/F Tier 2/3):** whether to port the Strategy Builder architecture (LLM-plan → compile → validate → backtest → FDR/PBO/SPY gate) to Logic Changes/Asset Swaps so they genuinely reason over the symphony (Gap A Tier 3); whether to wire PBO/SPY into the two under-gated engines (Gap B Tier 1, "the fix already exists, just needs mirroring"); whether to require a minimum series length before presenting a survivor as an edge (Gap C Tier 2); whether to admit multiple `p_adj<=q` candidates instead of argmin-only (Gap F Tier 2). None of these are doc corrections — they're architecture decisions this document explicitly does not make.
