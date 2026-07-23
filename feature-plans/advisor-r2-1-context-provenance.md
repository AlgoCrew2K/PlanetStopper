# Feature Plan — R2-1: SB Reasoning-Context Injection + Provenance Contract

**Status:** ready
**Branch:** `feature/advisor-r2-reasoning` (off origin/main `5f353145`)
**Program:** R2 — make the AI Advisor genuinely reason (audit F1) + give reasoning real provenance (speed can't impersonate intelligence). Scoped by r2-spec (Tier-3), PM-ratified.
**Advisory-only, off-execution-path.** Ships DIRECT to origin/main via FF after the PM live gate (no PR).
**ALL-NEW CODEPATHS → Toxic Pair (Quint) team, not solo agents.**

## PM decomposition ruling (context for this sub-cycle)
R2 = 3 sub-cycles: **R2-1 (THIS)** context-assembler + provenance contract, proven on Strategy Builder → **R2-2** Logic Changes reasoning port → **R2-3** Asset Swaps port. Provenance is a CROSS-CUTTING contract established here and extended per port (never split into a late standalone cycle — no port ships reasoning without its provenance surface). Open-question rulings: run_id PERSISTED (Q3=yes); request-response + "Running…" acceptable, SSE-per-run deferred (Q4); structural-edit representation deferred to R2-2's Gate-2 with a lean toward Option B (edit real tree + validate_tree) (Q2). R1 already CLOSED the statistical axis (gate parity F2) + honesty axis (F4) — R2 PRESERVES both, does not rebuild them.

## Summary
Make Strategy Builder genuinely reason over the operator's ACTUAL symphony instead of just an objective name, and make that reasoning OBSERVABLE. Build a reusable reasoning-context assembler that injects {real symphony tree (rendered), live stats, the 5 market-lens blocks, tradeable universe} into SB's generation prompt, and surface run-level provenance (generation model, mode, injected-evidence manifest, run-id) on the SB run route + tab. This is the shared enabler + provenance contract that R2-2/R2-3 reuse; it also closes R1's explicitly-deferred SB context-blindness gap. NO structural-edit capability, NO gate-math change, NO port of the deterministic engines this cycle.

## Acceptance Criteria
- **AC-1 — Real tree injected.** When the run is symphony-scoped (`symphony_id` resolving to a Composer hash), the generation prompt includes a bounded, human-readable rendering of the operator's CURRENT tree (via `symphony_schema.render_rules_text` — NOT a raw multi-KB JSON dump). Test: fixture tree → assert rendered rule/ticker markers present in the prompt.
- **AC-2 — Live stats + lens blocks injected.** Prompt also includes live stats + the 5 lens blocks sourced from the existing nightly cache-serve path (`assemble_advisor_context`'s lens-cache path — NOT a fresh live fan-out). Test: seeded lens-cache fixture → assert lens+stat markers present.
- **AC-3 — Honest degradation, never fabricated.** No `symphony_id` / tree-fetch fails / lens cache cold-stale → an explicit per-source manifest (`tree: absent|present`, `stats: …`, per-lens `available|stale|absent`) and the run proceeds WITHOUT fabricated context. Assembler never raises (D-1). Test: mock each failure; assert the manifest reflects the gap and no placeholder is injected.
- **AC-4 — Provenance object on the run response.** SB run-route JSON carries a `provenance` object: `generation_model` (from `model_config.get_advisor_suggestion_model()` at call time — never a hardcoded literal), `mode` (`"build-new"`), `evidence_injected` (the AC-3 manifest), `run_id`. Test: route JSON asserts `generation_model` == accessor value under an env override.
- **AC-5 — Provenance rendered on the SB tab.** `sbRunAnalysis()` renders the model + injected-evidence summary + run-id via a dedicated `data-testid` (honest empty-state when a field is absent — mirror the R1/degrade render pattern). Test: JS/DOM testid vs a mocked run response.
- **AC-6 — Stable run-id, persisted + traceable.** A UUID `run_id` minted once per run, threaded onto the run result, AND persisted with the run's advisory observations (`advisor_observations.raw_response`) so any proposal traces back to its run + injected-evidence manifest. Test: one stable id across the response + the persisted rows.
- **AC-7 — Credential-less + mocked green.** All 7 cred env vars = `""` (empty, NOT unset — `.env` auto-refills unset), LLM client seam (`build_plan_generator._build_client`) + real-tree fetch seam mocked → the full path runs and every new test passes; ZERO unmocked live Composer/Anthropic calls. Test: whole cycle under the credential-empty fixture + mocked seams.
- **AC-8 — Invariants preserved.** Advisory-only + off-execution-path (no `alpha_bot_execution.py`/`math_engine.py` import — import-guard test); CSRF unchanged; NOT added to `_SETTINGS_WRITE_ALLOWLIST`; the FDR/PBO/SPY/BHY gate is BYTE-unchanged (R1 parity untouched — characterization test). The non-symphony-scoped from-scratch path is byte-preserved when `reasoning_context is None`.
- **AC-9 — Bounded prompt.** Injected context is bounded so a large real tree can't blow `build_plan_generator.MAX_OUTPUT_TOKENS`; the render is deterministically length-capped/summarized. Test: oversized fixture tree → injected context stays under the documented bound.

## Architecture (reuse vs new — exact seams, file:line from r2-spec's investigation @ 5f353145; re-verify before editing)
**REUSE:**
- `build_plan_generator._build_generation_prompt(objective, n_plans, membership)` (~bpg.py:915, membership-only today ~:949) — the injection point. Add an optional `reasoning_context` threaded through the seam chain: `app.py` SB run route → `strategy_builder_engine._generate_candidate_trees` (~sbe.py:822) → `generate_build_plans` (~sbe.py:387) → `_build_generation_prompt`. ALL params keyword/optional; `None` → from-scratch path byte-preserved.
- `ai_advisor.assemble_advisor_context` (~ai_advisor.py:1508) — reuse its lens-cache-serve path (~:1594-1671) + staleness classifier (~:1604-1619) + optuna/volatility blocks. GAP: it emits CONDENSED logic (`get_condensed_logic` ~:1581), not the full tree → R2-1 additionally sources the full real tree.
- `symphony_logic.fetch_symphony_score` (used app.py:4396, :4568) — the real full-tree source the deterministic engines already use.
- `symphony_schema.render_rules_text(tree)` (~:652) + `extract_tickers` (~:585) + `validate_tree` (~:187) — deterministic, inspects arbitrary real `/score` trees; renders for the prompt (AC-1/AC-9).
- `model_config.get_advisor_suggestion_model()` (~:29, default `claude-fable-5`, read at call time) — provenance model source (AC-4).
- Provenance surfaces: SB run-route JSON (~app.py:4930-4949) + `static/ai_advisor.js sbRunAnalysis()` (~:718, existing provenance lines ~:778-805) + tab attribution seam (`templates/ai_advisor.html:1074`).
- LLM test seam: `build_plan_generator._build_client()` (~:156, patched in tests).

**NEW:**
- `build_reasoning_context(symphony_id, objective, *, composer_symphony_id) -> (prompt_context, manifest)` — Gate-2/HOW: home in `ai_advisor.py` OR a new `advisors/reasoning_context.py` (implementer's call; keep off-execution-path via lazy imports).
- Additive `reasoning_context` param on the 4-function generation seam chain (keyword/optional).
- `run_id` + `provenance` fields on `ProposalRun` + route JSON.
- A provenance render block + new `data-testid` in `sbRunAnalysis()`.

## Edge Cases
- No `symphony_id` → tree not injected, manifest `tree: absent`, from-scratch generation byte-preserved.
- `fetch_symphony_score` errors/empty → `tree: absent`, no fabrication.
- Lens cache cold/stale → per-lens manifest `absent`/`stale` (reuse the existing staleness classifier).
- Oversized real tree → bounded render (AC-9).
- `ADVISOR_SUGGESTION_MODEL` runtime override → provenance reflects the actual accessor value.

## Security Considerations
- D-1 never-raises across the assembler + the seam chain.
- Injected context NEVER leaks credentials — preserve `assemble_advisor_context`'s no-`os.environ`-dump guarantee for the new tree/stat injection.
- Provenance/error tokens stay `type(exc).__name__` (no `str(exc)` echo — matches the route contract).
- `run_id` is a UUID (no PII).

## Testing Strategy
- Credential-less (7 cred vars = "") + mocked LLM (`_build_client`) + mocked real-tree fetch + seeded lens-cache fixture; ZERO unmocked live API.
- Fixture provenance (Gate-1 hard rule): real-tree fixture captured-from-producer (reuse `tests/fixtures/composer/*` / `tests/fixtures/strategy_builder/*`) or schema-derived — NEVER hand-invented; assert manifest SHAPE/PRESENCE, not producer-computed stat values.
- Surfaces: engine/route-JSON + JS/DOM testid + import-guard (no alpha_bot_execution/math_engine) + gate-untouched characterization.
- Run `-n0` + `ALPHABOT_TEST_MEM_CAP_GB=24` + scratch DB_PATH; PM gate = CI `-n2` (authoritative) + first-hand render.

## Scope Boundaries
- OUT: porting Logic Changes / Asset Swaps to reason (R2-2 / R2-3 — their tabs stay "Deterministic — no AI reasoning" until their engine actually reasons); the structural-edit representation decision (R2-2 Gate-2); ANY gate/PBO/SPY/FDR/BHY math change (R1 closed it); per-run progress streaming (SSE deferred; request-response + "Running…" stays); multi-discovery/measured_value/reject-copy line-items.
- PROVENANCE UI is IN and is a HARD ship requirement — do not ship the context injection without the provenance surface.
