# Feature Plan — community-strats wiring into propose_strategies (slice 2)

**Status:** ready
**Branch:** `pr/community-strats-wiring` (worktree `.claude/pr-worktrees/community-wire`, forked from origin/main `c4d6a36`)
**Classification:** NEW CODEPATH — full TDD (RED → GREEN → review), gated-solo flow, PM-gated. Slice 2 of the community-strats lane (slice 1 = the loader, merged #19).

## Summary
Slice 1 built `community_strats.load_community_strategies` (read-only loader → candidate trees). Slice 2 wires those community candidates into `strategy_builder_engine.propose_strategies` so they flow through the SAME pipeline as the T1–T7 template candidates: **real Composer backtest (`run_backtest`) → BHY-FDR gate → screens → persist**. Composer's backtest is the authoritative validity+performance gate — community candidates earn their place exactly like generated ones. Cost-safe: bounded within the existing `MAX_CANDIDATES_PER_RUN` (≤30) cap, so a run backtests a normal-sized batch, NOT the 8k library.

## [PM-ASSUMED] design (documented; operator may redirect before merge)
- **`propose_strategies` stays Mongo-free / pure.** It does NOT import `community_strats` or touch Mongo. It accepts a caller-supplied `community_candidates: list[CandidateInfo] | None = None` (default None → behavior identical to today). This keeps the engine unit-testable with synthetic `CandidateInfo` (no live Mongo in CI) and preserves separation of concerns.
- **Thin adapter** `community_candidate_infos(records: list[dict]) -> list[CandidateInfo]` (in `strategy_builder_engine.py` or a small adapter) maps loader records → `CandidateInfo(candidate_id=f"community:{sid}", tree=tree, template_id="community", params={"sid": sid, "name": name})`. The eventual app route (later slice/UI) calls `load_community_strategies()` → select/cap → `community_candidate_infos()` → `propose_strategies(..., community_candidates=...)`.
- **Injection point:** in `propose_strategies` Step 1, after `_generate_candidate_trees`, extend `candidate_infos` with the community candidates, then dedup by `candidate_id` and enforce the total cap. Community sub-budget: `MAX_COMMUNITY_CANDIDATES_PER_RUN` ([PM-ASSUMED] = 15) so templates + community ≤ `MAX_CANDIDATES_PER_RUN`; community truncated beyond the sub-budget (logged, not silent).
- **Provenance on persist:** survivor/rejected observations from community candidates carry `template_id="community"` and the `sid`/`name` in the persisted params/raw_response, so the UI can mark them as community-sourced.
- **Slice-1 deferred nit folded here (same lane):** add the `sharpe_filtered` counter to `community_strats.load_community_strategies` stats so the sum invariant holds exactly even when `min_oos_sharpe` is set.

## Acceptance Criteria
- **AC-1 (adapter):** `community_candidate_infos(records)` returns one `CandidateInfo` per record with `candidate_id="community:<sid>"`, `template_id="community"`, `tree` = the record's tree, and `sid`/`name` carried in `params`. Skips records missing a tree/sid (never raises).
- **AC-2 (injection):** `propose_strategies(..., community_candidates=[...])` includes the community candidates in the batch that is backtested (Step 2), FDR-gated (Step 3 — gate sees the FULL backtested batch incl. community), screened (Step 4), and persisted (Step 5) — identical handling to template candidates.
- **AC-3 (bound + dedup):** total candidates entering backtest ≤ `MAX_CANDIDATES_PER_RUN`; community capped at `MAX_COMMUNITY_CANDIDATES_PER_RUN`; no `candidate_id` collision between template and community candidates (dedup keeps one, deterministically).
- **AC-4 (failure isolation):** a community candidate whose Composer backtest errors is omitted from the gate batch (like a failing template), never aborting the run; `ProposalRun.candidates` contains only successfully-backtested infos.
- **AC-5 (provenance):** a persisted observation originating from a community candidate records `template_id="community"` + `sid` (assert via the persist path / returned info).
- **AC-6 (no regression):** `propose_strategies(..., community_candidates=None)` (and `[]`) produces byte-identical behavior to today; FDR integrity preserved (gate input = all successfully-backtested candidates). Never-raises preserved.
- **AC-7 (sharpe_filtered counter):** `community_strats.load_community_strategies` stats gains `sharpe_filtered`; invariant `pulled == valid + deduped + missing_edn_string + parse_failed + validate_rejected + sharpe_filtered` holds for ALL `min_oos_sharpe` values; the slice-1 comment caveat is updated to the exact invariant.

## Architecture
`strategy_builder_engine.py`: new `community_candidate_infos` adapter + `community_candidates` param on `propose_strategies` + `MAX_COMMUNITY_CANDIDATES_PER_RUN` constant + injection/dedup/cap in Step 1. `community_strats.py`: add `sharpe_filtered` counter (increment at the `min_oos_sharpe` filter point; add to `_EMPTY_STATS`). No Flask/route change (the route wiring is a later slice). No engine/math/LIVE_EXECUTION interaction. Composer backtest is the existing `run_backtest` (real API at proposal time; tests mock it).

## Edge Cases
- community list larger than the sub-budget → truncate to `MAX_COMMUNITY_CANDIDATES_PER_RUN` (logged).
- a community tree that no longer validates → it still gets backtested (Composer is the arbiter); if Composer rejects, backtest errors → omitted (AC-4). (We do NOT re-run validate_tree here — Composer's backtest is the gate.)
- empty/None community_candidates → no-op (AC-6).
- duplicate community sids within the input → dedup by candidate_id.

## Security Considerations
None new. `propose_strategies` stays off-execution-path/advisory-only; no Mongo/credentials in the engine (the loader owns Mongo). No new secrets. The community trees are external data but Composer's backtest (not our code) executes them.

## Testing Strategy
RED tests (engine): mock `run_backtest` (no live Composer in CI); inject synthetic `community_candidates` (built via symphony_schema, incl. a frontrunner-shaped one) + template candidates; assert they're backtested, gate-batched, screened, persisted with `community` provenance; assert the cap/dedup; assert AC-6 no-regression with None. RED tests (loader): `sharpe_filtered` counter increments + the full invariant. Full `tests/advisors/` + `tests/ai_advisor/` `-n0` gate before merge. PM runs a bounded live functional check: load a few real community candidates → `propose_strategies` with a real Composer backtest on ≤3 of them → confirm the pipeline runs end-to-end.

## Scope Boundaries
- IN: the adapter, the `community_candidates` param + injection/cap/dedup, community provenance on persist, the `sharpe_filtered` counter.
- OUT: the Flask route / UI wiring that calls the loader + propose_strategies (later slice); the `frontrunners` collection / overlay application (slice 3); any change to the FDR/screen math; selecting WHICH community candidates to pass (caller's job — a later selection helper).
