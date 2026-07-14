# Feature Plan — R2-2: Logic Changes Reasoning Port + Provenance

**Status:** ready
**Branch:** `feature/advisor-r2-2-logic-changes` (off origin/main `8d1b9770`)
**Program:** R2 — make the AI Advisor genuinely reason. Sub-cycle 2 of 3 (R2-1 shipped `8d1b9770`; R2-3 = Asset Swaps). Scoped by spec-writer (Tier-3), PM-ratified.
**Advisory-only, off-execution-path.** Ships DIRECT to origin/main via FF after the PM live gate (no PR).
**ALL-NEW LLM CODEPATH → Toxic Pair (Quint) team, not solo agents.**

## Summary
Today the Logic Changes tab is honestly labelled "Deterministic — no AI reasoning": `advisors/logic_change_engine.py` either parses an operator's typed change-description or applies fixed-multiplier parameter tweaks, backtests the variants, and runs them through the R1-closed FDR/PBO/SPY gate. R2-2 makes Logic Changes genuinely **reason** — reuse R2-1's shipped `ai_advisor.build_reasoning_context` (real rendered tree + live stats + 5 lens blocks + honest per-source manifest) so an LLM proposes objective-directed edits to the operator's ACTUAL symphony, and surface run-level provenance using R2-1's Design-B pattern. **Key de-risking finding (spec-writer):** the engine ALREADY edits the raw `/score` tree in production (`LogicTweak` + `apply_logic_tweak` deep-copy the real tree and set one numeric parameter at a `node_path`) — so this is Option B with NO representation change; R2-2 swaps the *fixed-multiplier* generator for an *LLM reasoned* one and adds a `validate_tree` guard. No gate-math change (R1 closed that axis).

## PM-ASSUMED resolutions (vacation-autonomy — operator may redirect)
- **[PM-ASSUMED Q1] Scope = reason the objective-directed generation** (`generate_objective_directed_candidates`, the fixed-multiplier "Deterministic" script). BOTH entry points route through the reasoned generator: the tab (`propose_operator_logic_change`) — with the operator's free-text `change_description` RETAINED as a steering hint into the LLM prompt (honored, not removed, not deterministic-parse-only) — AND the weekly scheduler (`suggest_logic_changes`). The tab attribution label flips off "Deterministic — no AI reasoning" for the reasoned path. *(Risk: changes the operator-initiated path's semantics from deterministic exact-parse to LLM-reasons-steered-by-description; that deterministic exact-parse IS the "no reasoning" behavior R2 targets, so routing through reasoning is thesis-aligned. Operator can redirect to preserve exact-parse.)*
- **[PM-ASSUMED Q2] Option B, numeric-parameter tweaks ONLY.** Structural add/remove of logic nodes is OUT (larger scope; the engine doesn't support it; would risk gate parity).
- **[PM-ASSUMED Q3] Request-response + "Running…" UX** (SSE deferred), no separate cost ceiling beyond the existing advisor model config — same as R2-1.

## Acceptance Criteria
- **AC-1 — Real context injected into reasoning.** For a symphony-scoped run the LLM prompt includes `build_reasoning_context`'s bounded rendered tree + live stats + 5 lens blocks (never a raw JSON dump). Test: fixture tree + seeded lens cache → assert rendered rule/ticker + lens/stat markers present in the assembled prompt.
- **AC-2 — LLM proposes objective-directed edits over the real tree.** The fixed-multiplier generator is replaced (for the reasoned path) by an LLM emitting candidate edits addressed to actual nodes/parameters of the operator's current tree, directionally consistent with the objective. Test: two objectives on the same fixture tree → different, objective-aligned edits (adversarial: a generator that ignores objective is a FAIL).
- **AC-3 — Edits apply to the raw tree and are structurally re-validated.** Each proposed edit is applied to a deep copy of the real `/score` tree and validated via `symphony_schema.validate_tree` before backtest; an edit that fails validation is dropped with an honest reason (never fabricated, never backtested). Test: an LLM edit producing a structurally-invalid tree is excluded; a valid edit proceeds. (Net-new guard over today's `apply_logic_tweak`.)
- **AC-4 — Gate byte-unchanged and batch-corrected.** All successfully-backtested LLM-proposed variants are gated as ONE `evaluate_candidate_batch` call (SPY-OOS + PBO + BHY/Yekutieli FDR), never per-candidate. Test: characterization test asserting the gate call/inputs unchanged from origin/main + FDR-denominator test (N candidates → single batch).
- **AC-5 — Provenance object on the run response.** Route JSON carries `provenance {generation_model, mode, evidence_injected, run_id}` — `generation_model` from `model_config.get_advisor_suggestion_model()` at call time, `evidence_injected` = the AC-1 manifest, `mode="logic-change"`. Present on every return path incl. error/no-key (real 4-key dict, never None, never fabricated). Test: route JSON asserts `generation_model` == accessor value under env override + provenance present on a degraded run.
- **AC-6 — Honest degradation, never fabricated.** No `symphony_id` / tree-fetch fails / lens cache cold-stale / LLM unavailable → the per-source manifest reflects each gap (`absent`/`stale`), the run proceeds without fabricated context, and LLM failure degrades to a clean "no reasoned proposal this run," not a crash. Never raises (D-1). Test: mock each failure; assert honest manifest + no placeholder + no raise.
- **AC-7 — `run_id` persisted + traceable.** A UUID `run_id` minted once per run, returned in `provenance`, AND written into every persisted `advisor_observations.raw_response` for that run (extends `_persist_observation`, which writes no `run_id` today). Test: one stable id across the response and the persisted rows.
- **AC-8 — Provenance + attribution rendered in the tab.** The Logic Changes result render surfaces model + injected-evidence summary + run-id via a dedicated `data-testid` (honest empty-state when a field is absent), and the tab attribution label (`templates/ai_advisor.html:1064`) no longer reads "Deterministic — no AI reasoning" for the reasoned path. Test: JS/DOM testid vs mocked run response; template assertion the stale label is gone/updated.
- **AC-9 — Invariants preserved.** Advisory-only + off-execution-path (import-guard: no `alpha_bot_execution`/`math_engine` import), CSRF unchanged, not in `_SETTINGS_WRITE_ALLOWLIST`, D-1 error tokens stay `type(exc).__name__` (no `str(exc)` echo), injected context leaks no credentials. Test: import-guard + allowlist + error-token characterization.
- **AC-10 — Credential-less mocked-green + bounded prompt.** All cred env vars empty (not unset), LLM client seam + real-tree fetch seam mocked → the full reasoned path runs with zero live Composer/Anthropic calls; injected context is deterministically length-capped so a large real tree can't blow the LLM output budget. Test: whole path under the credential-empty fixture; oversized fixture tree stays under the documented bound.

## Architecture (reuse vs new — re-verify file:line before editing @ 8d1b9770)
**REUSE:**
- `ai_advisor.build_reasoning_context(symphony_id, objective, *, composer_symphony_id=None) -> (prompt_context, manifest)` (ai_advisor.py:1700-1803) — verbatim; pass the `LogicChangeObjective` (its `objective` param is a no-op today, so it passes cleanly).
- `symphony_logic.fetch_symphony_score` — real full-tree source (already used at app.py:4568).
- `apply_logic_tweak` / `_navigate_to_node` / `extract_numeric_params` (logic_change_engine.py:377-451) — existing raw-tree edit primitives.
- `symphony_schema.validate_tree` — never-raising structural gate (NEW call site; add after `apply_logic_tweak`, before backtest — mirror `plan_tree_compiler`'s validate-before-return).
- `evaluate_candidate_batch` + `_spy_returns_fn_for` + `dated_returns` PBO (logic_change_engine.py:873-893, :1360/:1515) — gate byte-unchanged.
- Design-B provenance contract (strategy_builder_engine.py:130-164) + route `getattr`+`isinstance` guard (app.py:4968-4970) + JS render idiom (ai_advisor.js:806-831) + `model_config.get_advisor_suggestion_model()`.

**NEW:**
- A reasoned candidate generator (LLM call) that consumes `build_reasoning_context` output and emits `LogicTweak`-shaped edits — home is HOW (inside `logic_change_engine` vs. a sibling module; keep off-path via lazy import; analogous to `build_plan_generator` for SB). Mock seam: an `_build_client`-style LLM seam.
- `run_id` + `provenance` fields on the logic-change run result; `run_id` threaded into `_persist_observation`.
- A `validate_tree` guard on each edited tree.
- Provenance render block + new `data-testid` (e.g. `lc-live-generation-provenance`, distinct from SB's `sb-live-generation-provenance`) in the logic-changes JS; tab-attribution label update.

## Edge Cases
- No `symphony_id` → no reasoning, manifest `tree: absent`, honest empty result, from-scratch path byte-preserved.
- `fetch_symphony_score` empty/error → `tree: absent`, no fabrication.
- Lens cache cold/stale → per-lens `absent`/`stale` (reuse R2-1's classifier).
- LLM unavailable / malformed output → clean "no reasoned proposal," provenance still present, run does not raise.
- LLM proposes an invalid or out-of-tree edit → dropped at the `validate_tree`/`apply_logic_tweak` guard (AC-3).
- Oversized real tree → bounded render (AC-10).
- Zero survivors after the gate → valid non-error outcome (existing `NO_SURVIVORS_MESSAGE` contract).

## Security Considerations
- D-1 never-raises across the reasoned generator + route.
- No credential leakage into the injected context (preserve `assemble_advisor_context`'s no-`os.environ`-dump guarantee).
- Route/error tokens stay `type(exc).__name__` — never echo `str(exc)` or raw LLM error text.
- `run_id` is a UUID (no PII). Advisory-only; read `/score` + stateless `/backtest` only, no Composer write/trade calls.

## Testing Strategy
- Credential-less (cred vars = `""`, NOT unset) + mocked LLM seam + mocked real-tree fetch + seeded lens-cache fixture; zero unmocked live API.
- Fixture provenance is a Gate-1 hard rule: real-tree fixture captured-from-producer or schema-derived (reuse `tests/fixtures/composer/*` / `tests/fixtures/strategy_builder/*` / `tests/fixtures/symphony_logic/*`), never hand-invented; assert manifest SHAPE/presence, not producer-computed stat values.
- Surfaces: engine + route-JSON + JS/DOM testid + import-guard + gate-untouched characterization + `run_id`-in-persisted-rows.
- Run `-n0` + `ALPHABOT_TEST_MEM_CAP_GB=24` + scratch `DB_PATH`. PM gate = the FULL route-touching superset both cred modes + CI `-n2` (authoritative) + first-hand render.

## Scope Boundaries
- OUT: any gate/PBO/SPY/FDR/BHY math change (R1 closed it — parity only). OUT: Asset Swaps port (R2-3). OUT: per-run SSE streaming (request-response + "Running…" stays). OUT: an Apply/one-click-mutate button (guidance stays advise-only plain-text). OUT: structural add/remove of logic nodes (numeric-parameter edits only — Option B).
- IN and a HARD ship requirement: the provenance surface — do not ship reasoning without it (R2 cross-cutting rule).
