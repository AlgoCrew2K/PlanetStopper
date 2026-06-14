# Feature Plan — symphony_schema compound ANY/ALL construction (Cycle B)

**Status:** ready
**Branch:** `pr/symphony-compound-construction` (worktree `.claude/pr-worktrees/symphony-compound`, forked from origin/main `73bce60`)
**Classification:** NEW CODEPATH (new construction logic) — full TDD (RED → GREEN → review). Executed via gated solo isolated-worktree agents per the rogue-team charter (PM-ferried Red/Green/Revise), PM-gated.

## Summary
`symphony_schema.py`'s constructors can build only FLAT single-condition `if-child` nodes. The corpus-validated v2 grammar (§7) proves real symphonies use a rich, recursive `condition` block for ANY/ALL logic — and this is exactly the structure of a **frontrunner overlay** (operator: "a logic overlay evaluated first that fires on ANY watched ticker hitting an RSI extreme → vol basket"). This cycle adds constructors that emit the §7 `condition`-block forms (`binary`, `binary-compound`, `compound`) and an `if` that carries them, so the Strategy Builder can construct frontrunner overlays and any compound-gated symphony. GAP-1/GAP-2 from the v2 audit §11.

## Proposed Constructor API ([PM-ASSUMED] — Gate-2/HOW; flag before merge to reshape)
All emit the §7.2 operand shape `{"fn", "ticker", "params":{"window"}}` (distinct from the flat form's `"val"`/`"fn-params"`), fresh uuids where ids apply, deep-copied children/conditions:
- `make_condition_operand(fn, ticker, *, window) -> dict` → `{"fn":fn, "ticker":ticker, "params":{"window":window}}`
- `make_constant_rhs(value) -> dict` → `{"constant": value}`
- `make_binary_condition(lhs_operand, comparator, rhs) -> dict` → `{"condition-type":"binary", "lhs":…, "comparator":…, "rhs":…}` (rhs = a constant-rhs OR an operand)
- `make_binary_compound_condition(fn, tickers, comparator, rhs, *, window, operator="any") -> dict` → `{"condition-type":"binary-compound", "operator":operator, "tickers":[…], "lhs":{"fn":fn,"ticker":"%","params":{"window":window}}, "comparator":…, "rhs":…}` — **the frontrunner primitive** ("RSI of ANY(tickers) > X")
- `make_compound_condition(operator, conditions) -> dict` → `{"condition-type":"compound", "operator":operator, "conditions":[…deepcopy…]}` (joins N conditions with any/all; nestable)
- `make_if_compound(condition_block, *, then_children, else_children) -> dict` → an `if` node whose true `if-child` carries the authoritative `condition` block (validate_tree exempts flat fields when a `condition` dict is present, per v2 §11 NON-GAP); else-branch `is-else-condition?=True`. **The existing flat `make_if` is UNCHANGED** (non-breaking; new constructor is separate).

## Acceptance Criteria
- **AC-1:** `make_condition_operand` emits the §7.2 operand shape exactly (`fn`/`ticker`/`params.window`).
- **AC-2:** `make_constant_rhs` emits `{"constant": value}` (numeric).
- **AC-3:** `make_binary_condition` emits a `binary` leaf; supports BOTH rhs forms (constant-rhs and operand-rhs / ticker comparison) per v2 §7.2.
- **AC-4:** `make_binary_compound_condition` emits a `binary-compound` block: `operator` ∈ {any, all}, `tickers` is the supplied list, lhs `ticker` is the `"%"` placeholder, exactly per v2 §7 (the frontrunner primitive).
- **AC-5:** `make_compound_condition` emits a `compound` block joining N child conditions with `operator` any/all; supports NESTING (compound-in-compound, and binary-compound leaves) — reproduces the real v2 §7.3 ANY example structure.
- **AC-6:** `make_if_compound` emits an `if` whose true `if-child` carries the authoritative `condition` block and an else `if-child` with `is-else-condition?=True`; `validate_tree` returns `[]` (no hard error) for the result wrapped in a valid root.
- **AC-7 (integration — the frontrunner overlay):** a full overlay — `make_if_compound(make_binary_compound_condition("relative-strength-index", [watched…], "gt", make_constant_rhs(80), window=10, operator="any"), then=[vol basket], else=[base subtree])` wrapped in `make_root` — `validate_tree == []`; `extract_tickers` returns watched + basket + base tickers; `render_rules_text` renders the ANY-gate readably.
- **AC-8 (validator awareness):** `validate_tree` HARD-ERRORS on a malformed compound block — `operator` not in {any, all}, or `condition-type` unknown, or `compound` missing `conditions`, or `binary-compound` missing `tickers`. (Small validator enhancement so the new forms are validated, not merely tolerated.)
- **AC-9 (invariants):** `operator` accepts only `"any"`/`"all"` at construction (else `ValueError`); fresh uuids; deep-copied children/conditions (mutating an input list must not mutate the built tree); the read-only fns (`validate_tree`/`lint_tree`/`extract_tickers`/`render_rules_text`) remain never-raising.
- **AC-10 (regression):** existing flat `make_if`/`make_condition` outputs and all existing tests unchanged; full `symphony_schema` suite green.
- **AC-11 (folded Cycle A review nits):** fill the `n=?` placeholders in the `KNOWN_INDICATOR_FNS` comment (PPO=99, PPO-signal=100, upper-bollinger=1, lower-bollinger=1); remove the duplicate `test_unknown_comparator_eq_produces_error` test (keep the re-pointed one + the AC-4 guard); add a docstring note that upper/lower-bollinger are corpus-observed in sort-by-fn position only.

## Architecture
New constructors appended to `advisors/symphony_schema.py` (pure stdlib, same style: deepcopy children, `_fresh_id()`). `validate_tree` gains a compound-block validation branch (AC-8) — recurse `condition.conditions[]`, check `condition-type`/`operator`/required keys. `extract_tickers`/`render_rules_text` extended to walk the `condition` block (the readers already partially handle it per v2 §11; verify + extend). No I/O, off-execution-path, advisory-only.

## Edge Cases
- Deeply nested compound blocks (compound→compound→binary) — iterative or bounded-recursion walk (existing module is iterative for trees; condition blocks are shallow in corpus but guard depth).
- `binary-compound` with a single ticker (corpus shows `tickers:["FDL"]`) — valid.
- rhs as operand (ticker comparison) inside binary-compound (v2 §7.4 ALL example) — supported.
- `"%"` placeholder must be emitted literally for binary-compound lhs ticker; must NOT be emitted for plain `binary`.
- empty `tickers` or empty `conditions` → construction `ValueError` (degenerate gate).

## Security Considerations
None new. Pure-stdlib tree construction/validation; no I/O, no secrets, no LIVE_EXECUTION, off-execution-path.

## Testing Strategy
RED tests built from the REAL v2 §7.3 (ANY) and §7.4 (ALL) corpus examples as golden fixtures (captured-from-corpus provenance). Assert exact emitted shapes (AC-1..6), the integration overlay validates + extracts (AC-7), malformed blocks error (AC-8), invariants incl. deep-copy isolation (AC-9), and no regression (AC-10). Full `symphony_schema` suite + `-n0` gate on `tests/advisors/` + `tests/ai_advisor/` before merge.

## Scope Boundaries
- IN: the 6 new constructors + `validate_tree` compound-awareness + `extract_tickers`/`render_rules_text` compound-walk + the folded Cycle A nits.
- OUT: wiring frontrunner overlays into `strategy_builder_engine` templates (a later cycle); the Mongo community-strats ingester; PPO/Bollinger param-key construction (short/long/smooth-window — GAP-7); market-cap weighting (GAP-6); any consumer of the new constructors.
