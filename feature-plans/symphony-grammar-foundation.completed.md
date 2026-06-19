# Feature: Symphony-Schema Grammar Foundation (validator widening + compound ANY/ALL construction + nested validation)
Status: ready
Created: 2026-06-14

## Summary
Rebuild — via a real Agent Team — the `advisors/symphony_schema.py` grammar foundation that was ripped (it had been built by standalone agents in violation of the team rule). It is the keystone the community-strats and frontrunner work depend on. Three things, one cohesive file: (1) widen the validator/lint allowlists to the corpus-verified Composer grammar; (2) add constructors for the §7 compound `condition` blocks (ANY/ALL gates — the frontrunner-overlay primitive); (3) make `validate_tree` compound-aware AND recursive into nested sub-conditions, bounded against hostile depth. Pure-stdlib, off-execution-path, advisory-only, never-raising.

## Grammar ground truth (corpus-verified, 10,441 real Composer symphonies — from prior corpus audit, recorded in project memory)
- Comparators in real use: `gt`, `lt`, `gte` (n≈39,596), `lte`. `eq`/`neq` do NOT occur.
- Rebalance values: `daily`, `none`, `weekly`, `monthly`, `quarterly` (n≈58), `yearly` (n≈27).
- Indicator fns beyond the original 7 that ARE real: `exponential-moving-average-price` (n≈45,816), `standard-deviation-price` (n≈5,572), `percentage-price-oscillator`, `percentage-price-oscillator-signal`, `upper-bollinger`, `lower-bollinger`.
- Compound conditions: an `if-child` may carry a `condition` block. `condition-type` ∈ {`binary`, `binary-compound`, `compound`}. `operator` ∈ {`any`, `all`} ONLY (selects OR vs AND). `compound` holds `conditions:[...]` (nestable). `binary-compound` broadcasts one predicate over `tickers:[...]` with lhs `ticker="%"` placeholder ("RSI of ANY(tickers) > X" — the frontrunner primitive). Operand shape `{fn, ticker, params:{window}}`; rhs is `{constant:N}` or an operand.

## Acceptance Criteria
- [ ] AC-1: `validate_tree` returns NO hard error for an `if-child` with `comparator:"gte"`; `eq`/`neq` still error. (`KNOWN_COMPARATORS` = {gt,lt,gte,lte}.)
- [ ] AC-2: `validate_tree` returns NO hard error for a `root` with `rebalance:"quarterly"` and for `"yearly"`; an unknown rebalance (e.g. `"hourly"`) still errors. (`KNOWN_REBALANCE` = {daily,none,weekly,monthly,quarterly,yearly}.)
- [ ] AC-3: `lint_tree` emits NO unknown-fn warning for the 6 new indicator fns; an abbreviation like `"rsi"` still warns. (Allowlist widened; exactness preserved.)
- [ ] AC-4: `make_condition_operand(fn, ticker, *, window)` → `{fn, ticker, params:{window}}`; `make_constant_rhs(value)` → `{constant:value}`.
- [ ] AC-5: `make_binary_condition(lhs_operand, comparator, rhs)` → a `binary` leaf (rhs = constant-rhs OR operand).
- [ ] AC-6: `make_binary_compound_condition(fn, tickers, comparator, rhs, *, window, operator="any")` → `binary-compound` with `operator`∈{any,all}, `tickers` list, lhs `ticker="%"` (the frontrunner primitive).
- [ ] AC-7: `make_compound_condition(operator, conditions)` → `compound` block joining N conditions with any/all; nestable (compound-in-compound).
- [ ] AC-8: `make_if_compound(condition_block, *, then_children, else_children)` → an `if` whose true if-child carries the authoritative `condition` block; existing flat `make_if`/`make_condition` UNCHANGED.
- [ ] AC-9: a full frontrunner overlay (`make_if_compound(make_binary_compound_condition("relative-strength-index",[tickers],"gt",make_constant_rhs(80),window=10,operator="any"), then=[vol basket], else=[base])`) wrapped in `make_root` → `validate_tree == []`; `extract_tickers` returns watched+basket+base (excluding the `"%"` placeholder); `render_rules_text` renders the ANY gate.
- [ ] AC-10: `validate_tree` HARD-ERRORS on a malformed compound block — bad `operator`, unknown `condition-type`, `compound` missing `conditions`, `binary-compound` missing `tickers` — at the TOP level AND at any NESTED depth (recursive), bounded by a depth cap so a pathologically deep (e.g. 5000) input never raises / never blows the stack. Absent-`condition-type` sub-items are tolerated (raw binary leaves), not errored.
- [ ] AC-11 (invariants): `operator` other than any/all at construction → ValueError; empty tickers/conditions → ValueError; fresh uuid ids; deep-copied inputs (mutating a caller list after construction must not mutate the tree); read-only fns (`validate_tree`/`lint_tree`/`extract_tickers`/`render_rules_text`) never raise.
- [ ] AC-12 (no regression): all pre-existing symphony_schema tests pass; flat constructors unchanged.

## Architecture
`advisors/symphony_schema.py`: widen the three `KNOWN_*` frozensets (+ source comments citing corpus counts); add `_KNOWN_CONDITION_TYPES`/`_KNOWN_OPERATORS`; add the 6 compound constructors (pure-stdlib, deepcopy, `_fresh_id()`); add `_validate_condition_block` as an ITERATIVE DFS (explicit `(cond, depth)` stack) recursing `compound.conditions[]` with `MAX_CONDITION_DEPTH` cap; extend `extract_tickers`/`render_rules_text` to walk the `condition` block (skip `"%"`). No constructor change to the flat path. Iterative traversal (corpus has depth-230 trees).

## Design-System Mapping
N/A — no UI.

## Edge Cases
- binary-compound with a single ticker; rhs as operand (ticker comparison); `"%"` emitted only for binary-compound lhs; empty `conditions:[]` (degenerate — out of scope unless a test demands); non-dict items in `conditions[]` skipped gracefully; deeply-nested + cyclic input bounded by the cap, never raises.

## Security Considerations
Pure-stdlib validator/constructor; no I/O, no secrets, no LIVE_EXECUTION, off-execution-path. Defense-in-depth: the bounded recursion prevents a stack-exhaustion DoS from a hostile community `edn_string` once this validates ingested trees. No eval/exec.

## Testing Strategy
RED tests (quant-test-writer) from the corpus grammar (tokens are grammar facts, fine to hardcode; golden compound fixtures = the real §7 ANY/ALL shapes). Cover AC-1..AC-12 incl.: exactness guards (eq/hourly/rsi still rejected), the frontrunner-overlay integration (AC-9), nested-malformed at depth-2/3 (AC-10), the 5000-deep never-raises (forces the depth cap), deep-copy isolation, no-regression. Full `tests/advisors/test_symphony_schema.py` green; `-n0` gate on `tests/advisors/` + `tests/ai_advisor/` before the PM merges.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Rebuild via a real Agent Team | Operator hard rule: teams default; this is a new codepath |
| Fold nested/recursive validation in from the start (was deferred as DE-SYMPH-001) | One cohesive validator; avoids a second cycle |
| Ground tokens in recorded corpus knowledge (not a fresh corpus pull) | The corpus audit already ran; the grammar is known + in memory; avoids an extra Atlas read |

## Scope Boundaries
- **IN**: the three KNOWN_* widenings, the 6 compound constructors, compound-aware + recursive validate_tree with depth cap, extract_tickers/render condition-walk, tests, and the doc-writer regenerating the grammar reference doc.
- **OUT**: the loaders (community_strats/frontrunner_loader), the atlas cache, propose_strategies wiring, the frontrunner two-stage builder, the lenses, any route/UI — all separate later team cycles. No constructor change to the flat path. No PPO/Bollinger param-key construction. No market-cap weighting.
