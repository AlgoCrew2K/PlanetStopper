# Feature Plan — DE-SYMPH-001: recursive nested condition-block validation

**Status:** ready
**Branch:** `pr/symphony-nested-validation` (worktree `.claude/pr-worktrees/symph-nested`, forked from origin/main `d4f3337`)
**Classification:** NEW behavior in an existing codepath (validator recursion) — full TDD (RED → GREEN → review), gated-solo flow, PM-gated. Closes the DE-SYMPH-001 deferral from Cycle B (#17).

## Summary
`symphony_schema._validate_condition_block` (added in Cycle B) validates only the TOP-LEVEL `condition` block — its docstring says "Checks the top-level condition block only (not sub-conditions)." A `compound` block nests other condition blocks under `conditions[]` (the §7 grammar: compound → [binary | binary-compound | compound]). Today a malformed NESTED sub-condition (unknown `condition-type`, `operator` not in {any,all}, `compound` missing `conditions`, `binary-compound` missing `tickers`) passes `validate_tree` because recursion stops at the top. This closes that gap: `_validate_condition_block` recurses into `compound.conditions[]`, applying the same hard-error checks at every depth. Strengthens the validator that community-strats consumption relies on (deeply-nested real frontrunner/compound symphonies).

## Acceptance Criteria
- **AC-1 (nested unknown condition-type):** a `compound` whose `conditions[]` contains a sub-block with an unknown `condition-type` → `validate_tree` returns a HARD error naming the bad nested type.
- **AC-2 (nested bad operator):** a `compound` containing a nested `compound` or `binary-compound` with `operator` not in {any, all} → HARD error.
- **AC-3 (nested missing keys):** a nested `compound` missing `conditions`, or a nested `binary-compound` missing `tickers` → HARD error.
- **AC-4 (deep nesting):** the checks apply at arbitrary depth (compound → compound → binary-compound), not just depth-1; a malformed block two+ levels down is caught.
- **AC-5 (valid nested passes):** a well-formed deeply-nested compound (e.g. the real v2 §7.3 ANY example structure — a compound of binary + binary-compounds — optionally wrapped in another compound) → `validate_tree` returns NO condition-block error.
- **AC-6 (never-raises + bounded):** malformed/cyclic/pathologically-deep inputs never raise (outer contract preserved); recursion is depth-bounded (a `condition`-block depth cap, lint-or-stop — NOT an infinite recursion) so a hostile deeply-nested input can't blow the stack. Document the cap.
- **AC-7 (no regression):** all existing `symphony_schema` tests stay green; top-level validation behavior unchanged; `extract_tickers`/`render_rules_text` (already recursive) untouched.

## Architecture
Modify `advisors/symphony_schema._validate_condition_block` (~line 326): after the existing top-level checks, when `ct == "compound"` and `conditions` is a list, recurse into each sub-condition (extend `errs` with `_validate_condition_block(sub, parent_node_id)` per sub-dict). Add a depth bound (e.g. an internal `_depth` param or an iterative explicit stack with a `MAX_CONDITION_DEPTH` cap — matching the module's iterative-traversal ethos) so deeply-nested/cyclic input is bounded, never-raising. Pure stdlib, off-execution-path. No constructor change.

## Edge Cases
- `conditions` present but not a list, or contains non-dict items → skip those gracefully (no raise), validate the dict ones.
- nested block missing `condition-type` entirely → caught by the unknown-type check (None not in known set).
- pathological depth (e.g. 10k nested compounds) → bounded by the cap; emit a single "exceeds max condition depth" hard error rather than recursing unbounded.
- empty `conditions: []` on a compound → the top-level "missing conditions" check only fires on absence, not emptiness; emptiness is structurally degenerate but [PM-ASSUMED] not a hard error here (out of scope — flag only if a test demands it).

## Security Considerations
None new. Read-only validator; bounded recursion prevents a stack-exhaustion DoS from a hostile community `edn_string` (defense-in-depth for the untrusted-data ingestion path). No I/O, no secrets.

## Testing Strategy
RED tests build nested condition blocks via `symphony_schema` constructors (`make_compound_condition` nesting `make_binary_compound_condition`/`make_compound_condition`) AND hand-built malformed nested dicts (since constructors won't emit invalid ones). Assert HARD errors at depth (AC-1..4), valid-nested passes (AC-5 — use the real §7.3 shape), never-raises on pathological depth (AC-6), no regression (AC-7). Full `symphony_schema` suite + `-n0` gate on `tests/advisors/` + `tests/ai_advisor/` before merge.

## Scope Boundaries
- IN: recursive validation of `compound.conditions[]` in `_validate_condition_block` + depth bound + tests; update the function docstring + the CLAUDE.md "nested-block validation deferred — DE-SYMPH-001" note to "implemented".
- OUT: validating the leaf operand internals (lhs/rhs fn/ticker/params shapes) beyond what exists (separate concern); constructor changes; empty-`conditions` semantics; any non-condition-block validation.
