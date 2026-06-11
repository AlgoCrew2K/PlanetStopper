# Strategy Builder — Phase 1 Team Handoff State

**Purpose:** Durable team state so the Phase-1 Toxic Pair TDD team (quant-test-writer ⇄
implementer + quant-code-reviewer + composer-alpaca-integration) can resume after the
session restart that activates Agent Teams (`CLAUDE_CODE_ENABLE_AGENT_TEAMS=1` is now set
in `~/.claude/settings.json`; takes effect on next session).

## Status at handoff

- **Phase 0 complete**: grammar reference committed at
  `feature-plans/strategy-builder-composer-grammar.md`.
- **RED suite drafted, interrupted mid-flight**: `tests/advisors/test_symphony_schema.py`
  was written by the test-writer (killed before commit/handoff). Committed as WIP by the PM
  to preserve it. The relaunched test-writer must review/own it, apply the contract
  amendments below, and re-verify it is RED for the right reasons before the implementer
  starts. `tests/fixtures/symphony_schema/` holds its hand-built fixtures.
- **No production code exists yet** (`advisors/symphony_schema.py` not created) — TDD
  discipline held.

## Contract amendments (BINDING — from implementer's fixture verification)

These override the original Phase-1 brief and the grammar doc where they conflict.
Ground truth: `tests/fixtures/symphony_logic/sample_score_small.json` (866 nodes,
depth 19) and `sample_score_large.json` (8,455 nodes, depth 230) must `validate_tree() == []`.

1. `MAX_TOTAL_NODES` / `MAX_TREE_DEPTH` are CONSTRUCTION-side constants + `lint_tree`
   warnings, NOT `validate_tree` hard errors (both golden fixtures exceed any sane cap).
2. Unknown indicator fns are lint warnings, not hard errors (`standard-deviation-price`
   occurs in the large fixture). Hard errors remain: unknown step, structurally missing
   required fields, duplicate ids, malformed weight objects, `if` missing branches,
   non-asset leaves, None/garbage input.
3. `select-n` may be string OR int (`"4"` appears in fixtures).
4. `weight.den` may be the string `"100"`; `weight` appears on `asset`/`if`/`group`/
   `filter` nodes, not only children of `wt-cash-specified`.
5. Flat `lhs-window-days`/`rhs-window-days` keys DO appear in real if-child nodes
   alongside the `*-fn-params` object form — both tolerated on read; constructors emit
   the params-object form only.
6. `condition` blocks may carry `condition-type` (`compound`/`binary`/`binary-compound`),
   `operator` (`any`), `tickers` arrays, `%` placeholder tickers, `rhs: {"constant": N}`.
7. Cosmetic keys tolerated everywhere: `collapsed?`, `suppress_incomplete_warnings`,
   `window-days`, `description`, `name` on non-group nodes, `children-count`/`price`/
   `dollar_volume`/`has_marketcap` on assets.
8. Traversal must be ITERATIVE (explicit stack) — include a depth-230+ fixture test
   proving no RecursionError.
9. After convergence, the implementer amends the grammar doc with a
   "Phase-1 fixture-verification corrections" section covering 1–7.

## Reviewer notes (already delivered to reviewers' briefs)

- quant-code-reviewer: judge against the AMENDED contract; runs full default pytest +
  ruff; blast-radius check (only `advisors/symphony_schema.py`, `tests/**`, and
  feature-plans docs may change in Phase 1).
- Domain specialist: structural indistinguishability vs trees consumed/produced by
  `symphony_logic.py` / `asset_swap_engine.apply_ticker_swap`; no generation of
  unverified constructs (`wt-market-cap`, `gte`, EMA).

## Implementation notes carried from PM grounding

- `analytics.compute_quantstats_metrics()` (analytics.py:315) is the metric engine for
  Phase-2 screens; quantstats is NOT pinned in requirements.txt (lazily imported) —
  Phase-2 team must resolve.
- DD sign conventions: quantstats max_drawdown ≤ 0; Composer stats positive magnitude
  (analytics.py:840).

Full program plan: see the approved plan (PM holds it; phases 0–8).
