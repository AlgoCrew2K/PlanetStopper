# Advisor Remediation — Phase R1: Honesty + Statistical Wiring
**Status: ready**
**Branch:** `fix/advisor-remediation-r1` (off origin/main 05da181b) · **Worktree:** `.claude/worktrees/advisor-r1`
**Source of truth:** `docs/audit-inputs/ADVISOR-INTENT-AUDIT.md` (verdict @ 08b0bcc0, PM-gate-passed) + `docs/audit-inputs/doc-reconciliation.md` (exact copy corrections) + `docs/audit-inputs/claims-inventory.md`. Every AC below cites the audit finding it closes. When this plan and the audit doc disagree on a file:line, the audit doc wins — it was adversarially verified.

## Summary
The advisor-intent audit (2026-07-13) found the advisor suite misrepresents itself: deterministic engines wear Claude branding (F4), the statistical gate's teeth (PBO veto, SPY baseline) are wired into Strategy Builder only (F2), operator Evaluate buttons run N=1 where the FDR correction is a no-op (F2), rejects all display the wrong reason (F6), a fake "measured" statistic is surfaced with a false docstring (F7), SB degrades silently with dead safety code (F5), and survivors carry no power caveat despite near-zero power at reachable fold lengths (F3). **R1 makes every surface honest and wires the existing, already-proven statistical machinery into the two neglected engines.** R1 does NOT add LLM reasoning (that is R2); it stops the suite from lying about what it does and closes the cheap, high-value statistical gaps with in-repo patterns (mirror DE-SB-CULL-001 + sbe.py:807-826).

## Acceptance Criteria

### Attribution honesty (F4, Gap D)
- **AC-1** The page-global "Claude-powered suggestion engine — operator must approve each change through safety gates" subtitle (templates/ai_advisor.html:987-989) is REPLACED by per-tab attribution per doc-reconciliation §1.1: deterministic tabs (Logic Changes, Asset Swaps) carry an honest "deterministic analysis — no AI reasoning; statistical gate only" label; real-LLM tabs (Chat, Run Advisor) retain Claude attribution; Strategy Builder is labeled "Opus-generated + community candidates, statistically gated." No tab implies LLM reasoning where none exists.
- **AC-2** The Market Prism block (ai_advisor.html:1066-1273) gains explicit model attribution (council, claude-opus-4-8) per doc-reconciliation §1.2.
- **AC-3** SB run-controls note (ai_advisor.html:1718) corrected per doc-reconciliation §1.3: describes Opus generation + Atlas community sourcing + FDR/PBO/SPY gate; "from templates" language removed (T1-T7 template path is dead code — audit B.2-secondary).

### Statistical wiring (F2, Gap B)
- **AC-4** PBO veto CAN FIRE for Asset Swaps and Logic Changes: all batch-capable `evaluate_candidate_batch` call sites in asset_swap_engine.py (:1047/:1277) and logic_change_engine.py (:1289/:1466) pass `dated_returns=` per candidate (mirror strategy_builder_engine.py:857). The `_PBO_MIN_CONFIGS=2` and `>=8 aligned dates` guards are PRESERVED (audit-proved load-bearing: K=1 → PBO=1.0 always → would veto everything).
- **AC-5** Real SPY-OOS baseline for Asset Swaps and Logic Changes: both engines source `spy_returns_fn` (mirror sbe.py:807-826) instead of `default_oos_alpha=0.0`; SPY-fetch-failure → `+inf` sentinel → conservative WITHHOLD (edge-14 semantics preserved, never a silent fall-back to beats-zero).
- **AC-6** N=1 honesty on the operator Evaluate buttons (both engines): when the batch size is 1, the result panel states "single-candidate check — no multiple-testing correction applies (N=1)" and does NOT display FDR/Yekutieli branding. The N>1 weekly paths keep FDR labeling.

### Gate transparency (F6, Gap F + F3, Gap C)
- **AC-7** `rejection_reason` (pbo_veto / below_spy_alpha / fdr_not_winner) is rendered per rejected candidate on all three advisor result surfaces; the blanket "did not clear the FDR correction threshold" copy (ai_advisor.html:1996/2002) is replaced; `fdr_not_winner` renders as "cleared the significance bar but was not the single promoted winner" (doc-reconciliation §1.6).
- **AC-8** Gate-strength copy corrected per audit-stats' recommended string (doc-reconciliation §1.5): "tested against an FDR-calibrated significance bar accounting for N alternatives; only the single strongest candidate is promoted per run" — replacing "FDR correction applied (N tested)" (ai_advisor.html:1904).
- **AC-9** Survivor cards carry a statistical-power caveat when the validation fold length is below a named constant `MIN_POWER_FOLD_DAYS` (source: audit F3 — near-zero power at T=13 floor, weak at T=121; constant value + source comment required per math-layer rules; doc-reconciliation §1.7).

### Honest data (F7, Gap G)
- **AC-10** `measured_value` is REAL or ABSENT: the four production call sites (app.py:4308, app.py:4451, weekly_suggestions_scheduler.py:136/:382) either compute the actual objective statistic from available data or the rationale/description strings drop the "measured X" claim entirely. The false docstrings at logic_change_engine.py:206-209 AND asset_swap_engine.py:225-227 are corrected to describe actual behavior.

### SB observability + dead code (F5, Gap E)
- **AC-11** Run-level mode banner on SB results: built-new count + Atlas count + an explicit "LLM generation produced 0 plans (degraded)" notice when all built-new branches failed; the route error branch surfaces a cause CATEGORY (still sanitized — never echoes raw exception text, AC-23 precedent preserved).
- **AC-12** The SB route passes `backtest_fn` into `compile_plan` (reviving the AC-16 tradeability-repair loop, dead at sbe.py:379) AND either populates `live_returns` (reviving the drawdown/Pearson screens skipped at sbe.py:746-749) or renders an explicit "screens skipped — no live returns at route time" indicator. No silent skips.

### Performance (D-7, Gap H)
- **AC-13** The baseline backtest runs ONCE per operator-route evaluation (currently twice: lce.py:920+1308; ase.py:927+1068) — cached/passed through; identical results, ~⅓ latency cut on both Evaluate buttons.

### Guardrail honesty (F8 revision, B.4)
- **AC-14** Divergence Explainer stops polluting the observations feed: either no NOT_APPLICABLE rows are emitted while `SECOND_WINDOW_CVAR_ENABLED` is off, or rows are labeled "feature disabled." Overfitting Conscience UI description names its actual scope (BACKTEST_SELECTION degrees-of-freedom only — not general overfitting protection). Spec Critic untouched (genuine control).

### Docs (Gap D/doc)
- **AC-15** All doc-reconciliation §2 corrections applied to docs/generated/*.md; CLAUDE.md key-files corrections DRAFTED (file provided to PM for application — doc-writer never edits CLAUDE.md directly).

### Model routing (OPERATOR DIRECTIVE 2026-07-13: "anything it suggests should be using fable")
- **AC-16** Every SUGGESTION-PRODUCING LLM call uses Fable (`claude-fable-5`) via a single named, env-overridable accessor (e.g. `ADVISOR_SUGGESTION_MODEL`, default `claude-fable-5`): (a) `ai_advisor.request_suggestions` (currently `claude-opus-4-8` at ai_advisor.py:1759-1765); (b) `advisors/build_plan_generator.py` generation call (currently `claude-opus-4-8` at :1078-1084). Model swap is config-only — no prompt/schema/max-token changes in this AC. Explain-only Chat and the Market Prism council are OUT of this AC's scope (different intent class; operator can flip them separately). ATTRIBUTION COHERENCE: every attribution surface added by AC-1/AC-2 renders the model ACTUALLY RESOLVED per surface (accessor-driven, never a hardcoded model-name string in copy) — a "Fable" suggestion surface must never display "Opus", and vice versa. R2 inheritance: all new reasoning engines (Logic Changes/Asset Swaps ports) MUST source their model from the same suggestion accessor. Implementers consult the claude-api reference for the exact model id/params before wiring. Tests: accessor-monkeypatch pattern (as already planned for AC-2) applied to both suggestion call sites — assert the resolved model reaches the SDK call and the rendered attribution.

## Architecture
- Engine wiring (AC-4/5/10/13): advisors/asset_swap_engine.py, advisors/logic_change_engine.py, advisors/weekly_suggestions_scheduler.py, app.py route param assembly. The wiring PATTERN already exists in strategy_builder_engine.py (dated_returns :857, spy_returns_fn :871, SPY sourcing :807-826) — mirror it, do not invent.
- UI/routes (AC-1/2/3/6/7/8/9/11): templates/ai_advisor.html, static/ai_advisor.js, static/ai_advisor_asset_swaps.js, app.py response fields (rejection_reason, mode counts, N, fold length already computed engine-side — thread them through the JSON).
- Guardrails (AC-14): advisors/divergence_explainer.py emission path + observations-feed rendering; OC description string only.
- Exact target copy strings: doc-reconciliation.md §1 (drafted per-correction; use them verbatim unless a reviewer flags a conflict).

## Edge Cases
- SPY fetch fails on an operator route → WITHHOLD with visible reason (below_spy_alpha + "SPY baseline unavailable" caveat), never silently gate against 0.0.
- N=1 + PBO: PBO must remain None at K=1 (guard preserved) — the N=1 panel must NOT claim a PBO check happened.
- dated_returns intersection <8 dates on weekly batches → PBO None (passes) — surface "PBO: insufficient aligned history" rather than implying it ran.
- SB: Atlas unavailable AND LLM degraded → 0 candidates → run banner must say so explicitly (not an empty page).
- Weekly scheduler paths get the SAME wiring as routes (4 call sites total per engine family — audit lists them; missing one recreates the F2 gap silently).
- rejection_reason absent on legacy persisted rows → render nothing rather than a wrong reason.

## Security Considerations
- Advisory-only surfaces: no `LIVE_EXECUTION` touch, no `_SETTINGS_WRITE_ALLOWLIST` changes, no new write endpoints. CSRF posture unchanged.
- Error surfacing stays sanitized: cause CATEGORY tokens only, never raw exception/response bodies (D-1 + AC-23 precedents).
- No credentials in tests; fixture-first for Composer/SPY (capture via /api-fixture if a new fixture is needed — no live hammering).

## Testing Strategy
- Skill-driven TDD: test-writer runs `/tdd` on this plan → RED + `.claude/tdd-handoff.md`; implementers run `/tdd-implement`; finalize with `/tdd-finalize`.
- Engine tests (AC-4/5/10/13): fixture-driven; assert call-site wiring (dated_returns/spy_returns_fn present) via behavior — a candidate that SHOULD pbo-veto DOES; a SPY-outperformed candidate is withheld; baseline backtest call-count == 1 per route eval (mock the client, count calls). Derive values from fixtures — never hardcode producer values.
- Route/render tests (AC-1/2/3/6/7/8/9/11): route-level JSON field assertions + template rendering assertions (rejection_reason strings, N=1 label, mode banner). JS syntax via the parametrized tests/js_syntax module only.
- NO full local pytest (host reboot risk): targeted `-n0` module runs with temp DB_PATH; full-tree = CI.
- PM live E2E gate (PM-owned, after GREEN): PM drives Evaluate buttons + SB run + observations feed in own browser against a droplet DB snapshot; verifies every AC from the rendered UI before ship.

## Scope Boundaries
- **NOT in R1:** LLM reasoning of any kind (R2: SB context injection + porting the SB pipeline to Logic Changes/Asset Swaps); multi-discovery gate admission (all p_adj≤q — R2 decision, [PM-ASSUMED] approved for weekly batches only); autotuner changes of ANY kind (its single-winner selection is correct in its own context — audit F6); new endpoints; lens-pipeline changes; council changes.
- The two audit could-not-determines (typical T distribution; on-demand advisor cache staleness) are backlog, not R1.
