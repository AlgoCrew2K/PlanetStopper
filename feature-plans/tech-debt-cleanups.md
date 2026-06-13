# Feature: Tech-Debt Cleanups (Grouped Small Items)
Status: ready
Created: 2026-06-13

## Summary

Three independently dispatchable cleanup items grouped into one feature plan because each is small and behavior-preserving (or near-so). Sub-item C3a reconciles a dangling git stash of SEC two-call test WIP that must be either finished or explicitly dropped. Sub-item C3b removes a dead self-skip branch from the AI Advisor route layer, guarded by a route-level RED test that catches live 500s that mocked-module tests miss. Sub-item C3c removes a dead `higher_is_better` parameter from `advisors/asset_swap_engine._apply_lens_blend`. Each can be its own micro-cycle or run sequentially on one cleanup branch.

---

## C3a — Reconcile `stash@{0}` Cycle-2-SEC WIP

### Acceptance Criteria

- [ ] AC-1a: `git stash show -p stash@{0}` is inspected and a determination is made: (a) the SEC two-call test WIP is still needed (not yet landed on main) → apply onto a fresh cycle branch, finish RED→GREEN, gate, merge; or (b) the WIP is superseded by work already on main → drop with a rationale note in `DECISIONS.md`.
- [ ] AC-2a: After resolution, `git stash list` no longer contains `stash@{0}: On feature/multilens-advisor: cycle2 SEC two-call test WIP`. The stash is not left dangling.
- [ ] AC-3a: If the WIP is applied and finished: new tests are RED before implementation, GREEN after; pre-merge `-n0` gate passes; 0 new failures vs fork-point.

### Architecture

- `git stash show -p stash@{0}` — inspect the diff from the main worktree only (never in a shared team worktree — per feedback: `never git stash in a shared worktree`).
- If applied: create a cycle branch off main, `git stash pop` into it, finish the SEC two-call test (if incomplete), run Toxic Pair RED→GREEN.
- Files likely touched: `tests/ai_advisor/` (SEC lens tests), possibly `advisors/lens_pipeline.py` (SEC fetch path). Confirm from the stash diff.

### Edge Cases

- **Stash conflicts on apply:** resolve against current main manually; do not force-apply. If conflicts are too complex, drop with a note.
- **WIP already landed:** if the stash content is a subset of what is already on main, drop without applying.

---

## C3b — Route Self-Skip Cleanup

### Acceptance Criteria

- [ ] AC-4b: The self-skip branch in the AI Advisor route layer (`app.py` advisor routes) is located, confirmed dead/redundant against the current unified SPA routing, and removed.
- [ ] AC-5b: A route-level RED test is written BEFORE the removal that hits the affected route with the REAL producer module (not a fully-mocked module) and asserts the route returns a valid response (not a 500). This test is GREEN after removal.
- [ ] AC-6b: No live behavior change: the route still handles all valid request patterns correctly after the self-skip is removed.

### Architecture

- `app.py` — locate the self-skip branch (likely in one of the AI Advisor advisor routes; the Cycle-3/4/5 SPA consolidation made some skip logic dead). Confirm the exact location at dispatch.
- `tests/ai_advisor/test_advisor_routes.py` (or a new file) — add a route-level test that exercises the route with the real producer module (not `patch('module')` wholesale) to catch live 500s that mocked-module tests miss. Per feedback: "mocked-module route tests miss live 500s — hit the route with the REAL producer module."

### Edge Cases

- **Self-skip is not dead (still reachable):** if the skip is still needed for some edge case, do NOT remove it — document why and close this item as "retained, not dead."
- **Route still 500s after removal:** that is a bug exposed, not introduced. Fix the underlying cause before merging.
- **Unified SPA routing changed the skip context:** confirm against the current `app.py` SPA structure (`GET /ai-advisor` unified, all sub-routes 302-redirect) before assuming the old skip logic is dead.

---

## C3c — Dead `higher_is_better` Param in `_apply_lens_blend`

### Acceptance Criteria

- [ ] AC-7c: Confirm no caller passes `higher_is_better` meaningfully (inspect all call sites in `advisors/asset_swap_engine.py` and `tests/`).
- [ ] AC-8c: Remove the `higher_is_better` parameter and any dead branch it guarded from `_apply_lens_blend`. Update all callers to match the new signature.
- [ ] AC-9c: The asset-swap test suite passes GREEN after removal with no new failures (behavior-preserving refactor).
- [ ] AC-10c: No change-history naming appears in the refactor — identifiers describe runtime behavior, not the fact that something was removed.

### Architecture

- `advisors/asset_swap_engine.py` — `_apply_lens_blend` function: remove `higher_is_better` param + dead conditional branch.
- All callers of `_apply_lens_blend` within `asset_swap_engine.py` (internal) — update to remove the arg.
- `tests/ai_advisor/test_asset_swap_engine.py` — verify all existing tests pass (no new tests needed for a behavior-preserving param removal; no Toxic Pair required per project rules for covered-path refactors).

### Edge Cases

- **A caller passes `higher_is_better=True/False`:** if any caller does pass it, the behavior of the removed branch must be confirmed first before removal (does the caller rely on it or is it always the same value?). If a caller relies on it, it is not dead — close as "retained."
- **Naming rule:** the refactor must not introduce names like `removedHigherIsBetter` or `legacyBlend`. Identifiers describe what the function does, not its change history.

---

## Architecture (Shared)

No new Flask routes, no DB schema changes, no new external APIs. All three items are `app.py` / `advisors/` / `tests/` changes only.

## Design-System Mapping

N/A — backend feature, no UI surface. (All 10 are backend/infra; the Cycle-5 Market Prism Overview UI already shipped separately.)

## Edge Cases

See per-sub-item edge cases above. Shared: all three items are behavior-preserving except C3a (which may finish real WIP). If any item turns out not to be dead/redundant, close that sub-item as "retained, not dead" and do not remove.

## Security Considerations

- **C3a (SEC WIP):** if the stash is applied, it adds SEC API fixture tests. Fixture provenance rule applies: captured-from-producer or schema-derived — not parser+fixture co-design. D-1 contract on any SEC fetch error path.
- **C3b (route self-skip):** removing dead route logic does not add new attack surface. The route-level RED test verifies no regression. D-1 contract unchanged on the route.
- **C3c (`higher_is_better` param):** parameter removal in internal-only function. No new external input, no new data exposure. No security implications.
- **No `LIVE_EXECUTION` interaction** in any of the three items.

## Testing Strategy

**C3a:** if WIP is applied → Toxic Pair TDD (new codepath). If dropped → no tests needed; DECISIONS.md note is the artifact.

**C3b:** route-level RED test written BEFORE removal (per feedback: `route_level_red_for_mocked_analytics_fns`). The test calls the route with the real producer module (not a wholesale mock). Must assert the route returns a valid response and not a 500. Run: `pytest tests/ai_advisor -n0`.

**C3c:** existing `tests/ai_advisor/test_asset_swap_engine.py` — run the full asset-swap suite after removal and confirm GREEN with 0 new failures. No new tests needed (behavior-preserving refactor on a covered path). Run: `pytest tests/ai_advisor -n0`.

**Run protocol:** `DB_PATH` set via `tests/conftest.py`; targeted: `pytest tests/ai_advisor -n0 -o addopts= -p no:xdist`. One pytest at a time.

## Decisions

| Decision | Rationale |
|----------|-----------|
| C3a resolved first (inspect before apply/drop) | Cannot determine action without reading the stash diff; always diagnose before acting |
| C3b gets a route-level RED test before removal | Per feedback: mocked-module tests miss live 500s; a route-level test with the real producer catches the class of bug that cost a prod incident |
| C3c requires no Toxic Pair (covered-path refactor) | Behavior-preserving param removal on a path already covered by the asset-swap suite; no new codepath introduced |
| Naming rule enforced on C3c | Per project rule: identifiers describe runtime behavior, not change history; no `removed*` or `legacy*` names |
| Each sub-item is independently dispatchable | They share no code surface; can be run as one cleanup branch or three micro-dispatches |

## Scope Boundaries

- **IN**: C3a — inspect + apply/drop `stash@{0}`; C3b — locate + confirm dead self-skip + route-level RED test + remove; C3c — confirm dead + remove `higher_is_better` + update callers + run asset-swap suite; doc-gen notes any user-visible change in `DECISIONS.md`
- **OUT**: new advisor features; Epic A/B work; schema migrations; production behavior changes beyond the self-skip removal

**Dependencies:** none hard. Lowest priority — schedule around Epic A.

**Team note:** can be a single "cleanup" cycle (one branch, sequential small commits) or three micro-dispatches. Each is behavior-preserving except C3a. Hard rule: handle C3a from the MAIN worktree only (never `git stash` in a shared team worktree). Naming rule on C3c: no change-history language in identifiers.
