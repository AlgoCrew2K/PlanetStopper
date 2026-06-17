# TDD Handoff
Plan: feature-plans/community-strats-route-wiring.md
Branch: feat/community-strats-route-wiring
Phase: done

## Test Files
- `tests/ui/test_strategy_builder_community_wiring.py` — route-layer RED tests (AC-1..AC-6 + security)

## Import Stubs Created
None required. This feature edits `app.py` only. All referenced modules
(`advisors.community_strats`, `advisors.strategy_builder_engine`) are already
shipped and importable. No new modules, no new stubs.

## A/C Coverage Matrix

| A/C ID | Description | Test File | Test Name(s) | Status |
|--------|-------------|-----------|--------------|--------|
| AC-1 | community candidates forwarded as `community_candidates` kwarg | test_strategy_builder_community_wiring.py | TestAC1CommunityKwargForwarding (4 tests) | RED — fails on assertion (kwarg absent) |
| AC-2 | empty/available=False → template-only, route completes | test_strategy_builder_community_wiring.py | TestAC2TemplateDegradation (5 tests) | GREEN guard — template-only worked before wiring too; must stay GREEN post-impl |
| AC-3 | force_refresh never True (bill-protection) | test_strategy_builder_community_wiring.py | TestAC3BillProtection (2 tests) | RED — fails on assertion (load never called) |
| AC-4 | load raises → degrade template-only; propose raises → error classname only (D-1) | test_strategy_builder_community_wiring.py | TestAC4NeverRaisingD1 (6 tests) | GREEN guard — existing D-1 contract tested; must stay GREEN post-impl |
| AC-5 | lazy imports inside handler; no allowlist/LIVE_EXECUTION addition | test_strategy_builder_community_wiring.py | TestAC5BoundaryPreserved (4 tests) | GREEN guard — constraint guards; must stay GREEN post-impl |
| AC-6 | empty community → response shape byte-equivalent to template-only | test_strategy_builder_community_wiring.py | TestAC6NoRegression (2 tests) | GREEN guard — shape guards; must stay GREEN post-impl |
| Security | D-1 no leak; no LIVE_EXECUTION in code; no force_refresh abuse | test_strategy_builder_community_wiring.py | TestSecurityBoundary (4 tests) | GREEN guard — must stay GREEN post-impl |

**RED count: 6 (AC-1 × 4, AC-3 × 2)**
**GREEN guards: 21 (AC-2 × 5, AC-4 × 6, AC-5 × 4, AC-6 × 2, Security × 4)**
**Total: 27 tests**

Note on AC-4 guard tests: these test the EXISTING outer try/except D-1 contract. The
AC-4 RED requirement ("community load raises → degrade template-only") is exercised
via AC-3's RED tests — after impl adds the best-effort try/except, the AC-4 guard tests
confirm the existing outer error handler is preserved. The `test_propose_strategies_still_called_when_community_load_raises`
test specifically verifies the degrade-to-template-only behavior and currently passes because
without community wiring the route always calls propose_strategies regardless; after impl
with the try/except this test must remain passing.

## Behavioral Test Plan
N/A — pure backend route change; no new UI components or navigation flows.
The Strategy Builder SPA tab renders the same JSON response shape it always has.
Visual/behavioral verification is the PM's live functional test gate:
POST `/ai-advisor/strategy-builder/run` → confirm community candidates appear
in batch when Atlas has results; confirm template-only response when Atlas
empty/raises.

## Questions for User
None — plan is unambiguous; all referenced modules exist and are shipped.

---

## IMPLEMENTER INSTRUCTIONS (read this file ONLY — do NOT read the plan)

You are `cw-implementer`. Your job: edit `app.py` ONLY to make the failing RED
tests in `tests/ui/test_strategy_builder_community_wiring.py` GREEN while keeping
the whole test tree GREEN. Write MINIMUM code. No gold-plating. No new modules.
No new routes. No signature changes.

### THE ONE FILE TO EDIT
`app.py` — specifically the `ai_advisor_strategy_builder_run()` handler.
Find it by searching for `def ai_advisor_strategy_builder_run`.

### EXACT CHANGE REQUIRED

The handler currently has a lazy import block and a `propose_strategies(...)` call.
You must make THREE additions:

**Addition 1 — extend the lazy import block (~line 3413-3418)**

Find this existing block:
```python
    # Lazy imports keep strategy_builder_engine off the live 1-minute execution path (AC-X2).
    from advisors.strategy_builder_engine import (  # noqa: PLC0415
        Objective,
        ScreenConfig,
        propose_strategies,
    )
```

Extend it to also import `community_candidate_infos` and `MAX_COMMUNITY_CANDIDATES_PER_RUN`,
AND add a second lazy import for `load_community_strategies`. Result:
```python
    # Lazy imports keep strategy_builder_engine off the live 1-minute execution path (AC-X2).
    from advisors.strategy_builder_engine import (  # noqa: PLC0415
        MAX_COMMUNITY_CANDIDATES_PER_RUN,
        Objective,
        ScreenConfig,
        community_candidate_infos,
        propose_strategies,
    )
    from advisors.community_strats import load_community_strategies  # noqa: PLC0415
```

**Addition 2 — community load block (insert BEFORE the try: propose_strategies block)**

Find the line `try:` that wraps `propose_strategies`. BEFORE it, insert:
```python
    # Load community candidates — best-effort; never block template-only run (AC-4).
    community_candidates: list = []
    try:
        _community = load_community_strategies(force_refresh=False)
        community_candidates = community_candidate_infos(
            _community, max_candidates=MAX_COMMUNITY_CANDIDATES_PER_RUN
        )
    except Exception as exc:
        _daemon_log.warning("community-strats load skipped: %s", type(exc).__name__)
        community_candidates = []
```

**Addition 3 — pass community_candidates into propose_strategies call**

Find the existing `propose_strategies(...)` call and add `community_candidates=community_candidates`:
```python
        run = propose_strategies(
            objective=objective,
            universe=universe,
            screen_config=ScreenConfig(),
            live_returns=[],
            symphony_id=symphony_id,
            community_candidates=community_candidates,
        )
```

That is the complete change. NO other files. NO new routes. NO allowlist changes.

### PATCH TARGET EXPLANATION (for tests)

The tests mock:
- `advisors.community_strats.load_community_strategies` — the module where it lives
- `advisors.strategy_builder_engine.community_candidate_infos` — same
- `advisors.strategy_builder_engine.propose_strategies` — same

These patch targets work because the lazy `from X import Y` inside the handler
re-evaluates on every call, reaching back to the module's namespace. The tests
intercept at the module level, which is the correct seam.

### HOW TO RUN THE TARGET TESTS

```bash
cd /tmp && python -m pytest "C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\.claude\worktrees\community-wiring-team\tests\ui\test_strategy_builder_community_wiring.py" -v -p no:xdist --override-ini="addopts="
```

Target: all tests PASS (currently RED).

### ALSO RUN TO CONFIRM NO REGRESSION

```bash
cd /tmp && python -m pytest "C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\.claude\worktrees\community-wiring-team\tests\ui" "C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\.claude\worktrees\community-wiring-team\tests\advisors" -v -p no:xdist --override-ini="addopts=" -n0
```

### AFTER GREEN

Commit path-scoped (NEVER `git add -A`):
```bash
git -C "C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\.claude\worktrees\community-wiring-team" add app.py
git -C "C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\.claude\worktrees\community-wiring-team" commit -m "fix(community-wiring): wire community candidates into strategy-builder route (HF-1)"
```

Then SendMessage `cw-test-writer` with: "GREEN — all tests pass on <SHA>. Ready for review."

---

## Test File Issues (for test-writer to fix)
None yet.

## Disputed Tests
None.

## Status Log
- [2026-06-17] test-writer: Starting RED phase
- [2026-06-17] test-writer: RED complete — 27 tests (6 failing RED on assertions, 21 passing GREEN guards), 0 stubs created. Fixed one test that fired on docstring mention of LIVE_EXECUTION (used AST walk to check executable code only). All 6 RED failures are correct: load_community_strategies never called (AC-3), community_candidates kwarg absent from propose_strategies call (AC-1).
- [2026-06-17] implementer: GREEN complete — 27/27 tests passing, 0 test bugs documented. Regression suite (tests/ui + tests/advisors): 1134 passed, 17 skipped, 0 failures. Typecheck N/A (Python). Lint deferred to /tdd-finalize.

## Implementation Notes
- Addition 1 (lazy imports): extended `from advisors.strategy_builder_engine import (...)` to include `MAX_COMMUNITY_CANDIDATES_PER_RUN` and `community_candidate_infos` (alphabetical order within the block); added `from advisors.community_strats import load_community_strategies  # noqa: PLC0415` as a second lazy import on the line immediately following. Both stay inside the handler — off the 1-minute execution path.
- Addition 2 (community load block): inserted best-effort try/except immediately before the existing `try: run = propose_strategies(...)`. On exception logs only `type(exc).__name__` via `_daemon_log.warning` and falls back to `community_candidates = []`. The outer propose_strategies call always proceeds regardless.
- Addition 3 (kwarg forwarding): added `community_candidates=community_candidates` to the existing `propose_strategies(...)` call. No other arguments changed.
- No new routes, no allowlist changes, no template/JS changes, no CSRF changes, no LIVE_EXECUTION references added.
