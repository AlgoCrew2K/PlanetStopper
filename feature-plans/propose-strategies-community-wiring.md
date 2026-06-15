# Feature: propose_strategies — community-candidate wiring
Status: ready
Created: 2026-06-14

## Summary
Rebuild — via a real Agent Team — the wiring that injects community-strategy candidates (from `advisors/community_strats.load_community_strategies`, merged in PR #25) into the Strategy Builder proposal engine `advisors/strategy_builder_engine.propose_strategies`. The community symphonies become additional candidates in the SAME single-batch FDR/overfit gate as the template-generated ones — never a separate, weaker gate (wide exploration through one batch-wide FDR is the anti-overfit invariant). This wiring was ripped (built by a standalone agent). Advisory-only, off-execution-path, never-raising; no `LIVE_EXECUTION` interaction.

## Recovered contract (pre-rip c1bf5dc) + current types (verified on 392251b)
Current `strategy_builder_engine` public types (DO NOT change their shapes):
- `CandidateInfo(candidate_id, tree, template_id, params, metrics={}, backtest_error=None, data_warnings=[])`
- `ProposalRun(candidates, gated_batch, screened_survivors, observations_written, error=None)`
- `Objective` enum; `ScreenConfig` dataclass; `run_backtest` (from `composer_backtest_client`, 1 req/s); `evaluate_candidate_batch` (FDR gate, input = FULL backtested batch); `database.insert_advisor_observation`.

The ripped wiring (to restore, AC-1..AC-7):
- `community_candidate_infos(community_result, *, max_candidates) -> list[CandidateInfo]` adapter — maps each `load_community_strategies` candidate `{sid,name,tree,tickers,oos_metrics,composition_hash}` to a `CandidateInfo` (`candidate_id=sid`, `tree=tree`, `template_id="community"`, `params` carrying `{sid,name,composition_hash}`, `metrics={}` pre-backtest).
- new keyword-only `community_candidates: list[CandidateInfo] | None = None` param on `propose_strategies`.
- `MAX_COMMUNITY_CANDIDATES_PER_RUN` named constant (cap).
- per-candidate backtest failure isolation (a community candidate whose `run_backtest` fails gets `backtest_error` set and is excluded from the gate, never crashing the run).
- provenance: persisted observation records the community `template_id` + `sid` in its args.
- no regression when `community_candidates` is None/[] (default path byte-for-byte unchanged).

## Acceptance Criteria
- [ ] AC-1: `community_candidate_infos(community_result, *, max_candidates)` returns `CandidateInfo`s with `template_id="community"`, `candidate_id=<sid>`, `tree` carried through, and `params` containing the sid+name+composition_hash; caps at `max_candidates`; empty/`available=False` input → `[]`.
- [ ] AC-2: `propose_strategies(..., community_candidates=<list>)` includes the community candidates in the backtested batch AND in the single FDR-gate input (`evaluate_candidate_batch` receives template + community candidates together — the gate input is the FULL batch, screens never shrink it).
- [ ] AC-3: `MAX_COMMUNITY_CANDIDATES_PER_RUN` caps how many community candidates enter a run; passing more than the cap truncates deterministically (assert the constant exists + is enforced).
- [ ] AC-4 (failure isolation): a community candidate whose `run_backtest` raises/returns an error gets `backtest_error` set and is excluded from the gate; the run completes and other candidates are unaffected (assert one bad community candidate does not reduce template-candidate processing).
- [ ] AC-5 (provenance): the persisted observation for a surviving community candidate records `template_id="community"` and the source `sid` in its `insert_advisor_observation` args.
- [ ] AC-6 (no regression): `propose_strategies` with `community_candidates=None` and with `[]` produces identical behavior/candidate set to the current default path (assert against a template-only run).
- [ ] AC-7 (advisory-safety + never-raising): the wiring touches no `LIVE_EXECUTION`/credential path, is not added to `_SETTINGS_WRITE_ALLOWLIST`, and `propose_strategies` still never raises on community-candidate failures (returns `ProposalRun` with `error` set on catastrophic failure, per existing contract).

## Architecture
Edit `advisors/strategy_builder_engine.py`: add `community_candidate_infos` adapter + `MAX_COMMUNITY_CANDIDATES_PER_RUN` + the `community_candidates` kwarg on `propose_strategies`. Inside `propose_strategies`, after building template candidates, extend the list with the (capped) community candidates, run the existing backtest loop over the combined list (per-candidate try/except sets `backtest_error`), then pass the FULL backtested batch to `evaluate_candidate_batch` (unchanged gate). Persistence path records provenance. No change to `CandidateInfo`/`ProposalRun`/`ScreenConfig`/`Objective` shapes, the FDR gate, or the screens. Consumes `community_strats` only via the caller passing `community_candidate_infos(load_community_strategies(...))` — `propose_strategies` itself does NOT import/call `load_community_strategies` (keep the loader injection at the route/caller boundary; the adapter bridges).

## Design-System Mapping
N/A — no UI in this cycle (the route/tab wiring is a later cycle).

## Edge Cases
- community_result `available=False` → adapter returns `[]`; propose_strategies behaves as template-only.
- more community candidates than the cap → truncate.
- a community tree that fails `run_backtest` → isolated, `backtest_error` set, excluded from gate.
- duplicate community vs template candidate (same tree) → both go through the gate; dedup is the loader's job, not re-done here (document).
- all community candidates fail backtest → run still completes with template results.

## Security Considerations
Advisory-only, off the execution path; no `LIVE_EXECUTION`, no credential keys, not in `_SETTINGS_WRITE_ALLOWLIST`. No new external calls beyond the existing `run_backtest` (1 req/s, fixture-testable). Community trees are validated upstream by the loader (`validate_tree`); this wiring trusts the loader's validated `tree`. No eval/exec.

## Testing Strategy
RED tests (`tests/advisors/test_community_strats_wiring.py`) mocking `run_backtest`, `evaluate_candidate_batch`, and `database.insert_advisor_observation` — NO live Composer/DB. Build trees via `symphony_schema` constructors; build community input via fixture docs run through `community_strats.load_community_strategies` with a mocked `cached_pull` (or hand-built `CandidateInfo`s for the adapter-output tests). Assert: adapter mapping + cap (AC-1/3), gate-input includes community candidates (AC-2), backtest failure isolation (AC-4), provenance in persist args (AC-5), no-regression vs template-only (AC-6), advisory-safety + never-raises (AC-7). No hardcoded producer metric values — assert shape/membership/counts. `-n0` scoped gate on `tests/advisors` + `tests/ai_advisor` before the PM merges.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Community candidates go through the SAME single-batch FDR gate | Anti-overfit invariant — wide exploration must pay one batch-wide multiple-testing correction, never a separate weaker gate |
| Adapter at the caller boundary; propose_strategies doesn't import the loader | Keep the engine decoupled from the loader; route/caller injects (mirrors the existing `live_returns` injection pattern) |
| Rebuild via Agent Team | Operator hard rule: teams default; behavior change to an existing codepath |
| Reuse recovered c1bf5dc contract | Avoid reinventing; the ripped wiring's AC matrix was sound |

## Scope Boundaries
- **IN**: `community_candidate_infos` adapter + `MAX_COMMUNITY_CANDIDATES_PER_RUN` + `community_candidates` kwarg on `propose_strategies` + per-candidate failure isolation + provenance + tests + docs.
- **OUT**: the route/Strategy-Builder-tab UI that calls this (later cycle); the frontrunner loader/builder; the lenses; any change to the FDR gate, screens, `CandidateInfo`/`ProposalRun` shapes, or `community_strats.py`; any `LIVE_EXECUTION` interaction.
