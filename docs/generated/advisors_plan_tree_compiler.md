# advisors/plan_tree_compiler

> Plan-to-Tree Compiler — Component 3 of the real Strategy Builder: deterministically compiles a Component-2 build-plan DSL dict into a validated Composer `raw_value` tree using ONLY `symphony_schema` constructors, then runs a bounded validate-and-repair loop so only valid, tradeable trees reach the downstream pipeline.

**Source:** `advisors/plan_tree_compiler.py`
**Last updated:** 2026-06-20

## Overview

`advisors/plan_tree_compiler.py` is the deterministic bridge between the Opus-generated build-plan DSL (Component 2, `advisors/build_plan_generator.py`) and the Composer `raw_value` tree format that the downstream backtest pipeline consumes. It is a pure dispatch table: each DSL `kind`/`scheme` value maps 1:1 to exactly one `symphony_schema` constructor call.

The module's two contracts:

1. **Deterministic compilation (AC-14).** Same plan input → byte-identical tree output, modulo fresh `uuid4` `id` keys that `symphony_schema` assigns on every constructor call. The compiler never hand-builds node dicts; it only calls `symphony_schema` constructors so the emitted tree is always structurally valid by construction.

2. **Bounded validate-and-repair loop (AC-15, AC-16).** `symphony_schema.validate_tree` gates every compiled tree — a HARD-error tree never reaches `backtest_fn`. When `backtest_fn` is supplied and returns a tradeability rejection (HTTP 400 envelope), the compiler identifies the named in-tree ticker and prunes it, then retries. A grammar rejection (HTTP 422) is dropped immediately without ticker pruning. The loop is bounded by `MAX_REPAIR_ATTEMPTS`.

Off-execution-path. Advisory-only. Never raises — all failure paths degrade to `CompileResult(tree=None, reason=...)`.

**`market_cap` scheme is producer-deprecated.** Composer retired market-cap weighting (HTTP 422 `node-type-not-supported`; captured 2026-06-20 at `tests/fixtures/strategy_builder/wt_marketcap_deprecated_envelope.json`). Plans with any `scheme=="market_cap"` node are detected by `_has_market_cap` and dropped immediately before compilation with `reason="market_cap_scheme_deprecated"`. `backtest_fn` is never called for these plans. See `DE-SB-MARKETCAP-DEPRECATED` in `DECISIONS.md`.

## Constants

| Name | Value | Description |
|------|-------|-------------|
| `MAX_REPAIR_ATTEMPTS` | `3` | Named bound on the validate/tradeability repair loop. Must be in 1..10 (test-asserted). Three attempts provides an initial compile plus two prune-and-retry cycles before declaring a plan unrepairable. Never unbounded (AC-15). |

## Public Types

### `CompileResult`

Return type of `compile_plan`. Never raises.

```python
@dataclass
class CompileResult:
    tree:   dict | None = None   # compiled Composer raw_value tree; None on any drop
    reason: str  | None = None   # set on a drop; None on success
```

`reason` values (all strings; D-1: internal errors carry `type(exc).__name__` only):

| Reason | When |
|--------|------|
| `None` | Success — `tree` is a `validate_tree`-clean, backtest-accepted Composer tree |
| `"InvalidPlan"` | Input is not a dict |
| `"market_cap_scheme_deprecated"` | Any node in the DSL uses `scheme=="market_cap"` (Composer deprecated this; see AC-17 / DE-SB-MARKETCAP-DEPRECATED) |
| `"validate_tree_hard_error"` | Compiled tree fails `validate_tree` (HARD errors) before backtest |
| `"max_repair_attempts_exceeded"` | Tradeability repair loop exhausted `MAX_REPAIR_ATTEMPTS` without success |
| `"no_in_tree_ticker_in_400"` | HTTP 400 received but no in-tree ticker identified in envelope text — no productive prune possible |
| `"prune_degenerated_tree"` | Pruning the offending ticker left a degenerate tree (empty children where children are required) |
| `"pruned_tree_invalid"` | Post-prune `validate_tree` returned HARD errors |
| `"grammar_reject_{status}"` | HTTP 422 or other non-400 status from backtest — grammar reject, no ticker prune |
| `type(exc).__name__` | Unexpected internal error (D-1 contract) |

## API Reference

### `compile_plan(plan, *, backtest_fn=None) -> CompileResult`

Compile a single build-plan DSL dict into a validated Composer tree. Never raises (D-1).

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `plan` | `dict` | A build-plan dict as produced by Component 2 (`build_plan_generator.generate_build_plans`). Top-level keys: `plan_id`, `objective`, `name`, `rebalance`, `root` (NODE). |
| `backtest_fn` | `callable \| None` | Optional test seam. Signature: `backtest_fn(raw_value: dict) -> result` where `result.error` is a non-empty envelope string on failure, `None` on success. When `None`, compilation and `validate_tree` gating run but the tradeability repair loop does not (compile-only path). Production wiring to `run_backtest` is deferred to Component 5. |

**Returns:** `CompileResult`

**Execution path (in order):**

1. **Shape guard.** If `plan` is not a dict → `CompileResult(reason="InvalidPlan")`.
2. **market_cap pre-check (AC-17).** `_has_market_cap(root_node)` scans the DSL tree; if any node has `scheme=="market_cap"` → `CompileResult(reason="market_cap_scheme_deprecated")`. `backtest_fn` is never called.
3. **Compilation.** `_compile_node(root_node)` dispatches each DSL NODE to the matching `symphony_schema` constructor (see Constructor Dispatch below). Any compilation error (unknown kind/scheme, missing field, internal error) → `CompileResult(reason=type(exc).__name__)`.
4. **Root wrap.** `symphony_schema.make_root(name, rebalance, [compiled_root])` wraps the compiled root. This emits the live-required `"description": ""` field.
5. **`validate_tree` gate.** `symphony_schema.validate_tree(tree)` is called; any HARD errors → `CompileResult(reason="validate_tree_hard_error")`. A HARD-error tree NEVER reaches `backtest_fn`.
6. **Compile-only return.** If `backtest_fn is None` → `CompileResult(tree=tree, reason=None)`.
7. **Repair loop.** Each iteration calls `backtest_fn(current_tree)`:
   - `result.error is None` → success, return `CompileResult(tree=current_tree, reason=None)`.
   - HTTP 400 (tradeability): identify the in-tree offending ticker via `_find_prune_target`, prune it via `_prune_ticker_from_tree`, validate the pruned tree, then retry. Bounded by `MAX_REPAIR_ATTEMPTS`; exhaustion → `CompileResult(reason="max_repair_attempts_exceeded")`.
   - HTTP 422 or other status (grammar or unknown): drop immediately → `CompileResult(reason="grammar_reject_{status}")`. No ticker pruning.

**Example (compile-only, no backtest):**
```python
from advisors.plan_tree_compiler import compile_plan
from advisors.build_plan_generator import generate_build_plans, Objective
from advisors.universe_provider import get_tradeable_set

membership = get_tradeable_set()
gen = generate_build_plans(Objective.cut_drawdown, membership)
for plan in gen.plans:
    result = compile_plan(plan)
    if result.tree is not None:
        # tree is validate_tree-clean; pass to run_backtest (Component 5)
        pass
    else:
        print("dropped:", result.reason)
```

## Constructor Dispatch

The compiler is a pure dispatch table. Each DSL `kind`/`scheme` maps to exactly one `symphony_schema` constructor. No hand-built node dicts.

| DSL node | `symphony_schema` constructor | Notes |
|----------|-------------------------------|-------|
| `{kind:"asset", ticker}` | `make_asset(ticker)` | |
| `{kind:"weight", scheme:"equal", children}` | `make_weight_equal(children)` | |
| `{kind:"weight", scheme:"specified", children:[{node,pct}...]}` | `make_weight_specified(pairs)` | `pairs` is a list of `(compiled_node, pct)` tuples |
| `{kind:"weight", scheme:"inverse_vol", children, window_days?}` | `make_inverse_vol(children)` then `result["window-days"] = window_days` if present | Constructor default is 30; DSL can override |
| `{kind:"weight", scheme:"market_cap", ...}` | (producer-deprecated — dropped by `_has_market_cap` before dispatch) | Raises `ValueError` if reached defensively |
| `{kind:"group", name, children}` | `make_group(name, children)` | |
| `{kind:"filter", select_fn, select_n, sort_by_fn, children, window}` | `make_filter(select_fn, select_n, sort_by_fn, children, window=window)` | |
| `{kind:"if", condition, then, else}` | `make_indicator` + `make_condition` + `make_if(cond, then_children, else_children)` | Flat (non-compound) condition; rhs may be a fixed scalar or a ticker indicator |
| `{kind:"if_compound", condition, then, else}` | `_compile_condition(condition)` + `make_if_compound(compiled_cond, then_children, else_children)` | Recursive CONDITION union |

### CONDITION dispatch (for `if_compound` nodes)

| DSL CONDITION type | `symphony_schema` constructor |
|--------------------|-------------------------------|
| `{type:"binary", lhs, comparator, rhs}` | `make_condition_operand` (lhs) + `make_constant_rhs` or `make_condition_operand` (rhs) + `make_binary_condition` |
| `{type:"binary_compound", fn, tickers, comparator, rhs, window, operator}` | `make_constant_rhs` (rhs) + `make_binary_compound_condition` |
| `{type:"compound", operator, conditions}` | recursive `_compile_condition` on each sub-condition + `make_compound_condition` |

## Repair Loop Detail

### Error-envelope split (AC-16)

The split between tradeability rejections (HTTP 400 → prune + retry) and grammar rejections (HTTP 422 → drop immediately) is made by parsing the status code from the `composer_backtest_client` error envelope format `"HTTP {status}: {text}"` (client line 360). The split is STATUS-driven, not message-text-driven — message text is used only to identify the ticker to prune, not to classify the error kind.

### Prune-target identification (AC-16 Revise-1)

`_find_prune_target(text, tree_tickers)` cross-references uppercase candidates from the envelope text against `tree_tickers` (the real ticker set extracted from the current compiled tree via `symphony_schema.extract_tickers`). Only in-tree tickers are valid prune targets — a venue/market name or an off-tree ticker in the text is not pruned (would be a no-op wasting the repair budget).

When multiple in-tree tickers appear in the text, the one immediately before the first untradable signal phrase (`"not tradable"`, `"untradable"`, `"no pricing"`) is selected — this is the ticker Composer named as the problem. Falls back to the first in-tree candidate left-to-right when no signal phrase is present.

Returns `None` when no in-tree ticker is found; `compile_plan` then drops with `reason="no_in_tree_ticker_in_400"` rather than burning the repair budget on a no-op.

### Prune degeneration guard

`_prune_ticker_from_tree` rebuilds the Composer tree bottom-up, omitting all asset nodes for the named ticker. If pruning empties a container node's `children` list, the function returns `None` (degeneration detected), and `compile_plan` drops with `reason="prune_degenerated_tree"`. The post-prune tree is also run through `validate_tree` before the next backtest attempt.

## Internal Dependencies

- `advisors.symphony_schema` — all constructors (`make_asset`, `make_weight_equal`, `make_weight_specified`, `make_inverse_vol`, `make_group`, `make_filter`, `make_indicator`, `make_condition`, `make_if`, `make_condition_operand`, `make_constant_rhs`, `make_binary_condition`, `make_binary_compound_condition`, `make_compound_condition`, `make_if_compound`, `make_root`) + `validate_tree` + `extract_tickers`. Imported at module level (pure stdlib, no I/O, no Flask dependency).

No imports from `database`, `autotuner`, `app`, `ai_advisor`, or any execution module. Off-execution-path; advisory-only.

## Design Notes

- **Constructors only — no hand-built dicts.** The compiler never constructs a Composer node dict by hand. All tree structure is produced via `symphony_schema` constructors, which assign fresh `uuid4` ids and deep-copy children. This guarantees the compiled tree is structurally sound by construction before `validate_tree` even runs.
- **Determinism modulo uuids.** Two `compile_plan` calls on the same plan produce byte-identical trees except for the `id` keys (fresh `uuid4` per node per constructor call). Tests strip `id` keys before comparing trees to assert structural determinism.
- **Backtest seam is injected, not imported.** `backtest_fn` is a caller-supplied callable. The module never imports `composer_backtest_client` directly — production wiring (`run_backtest`) is the Component 5 job. In tests, `backtest_fn` is a mock. This makes the compiler independently testable with no live Composer dependency.
- **`validate_tree` gate is pre-backtest and post-prune.** A compiled tree is validated before the first backtest call; a pruned tree is validated again before the retry. `validate_tree` HARD errors never reach `backtest_fn`.
- **market_cap pre-check is unconditional and early.** `_has_market_cap` scans the DSL NODE tree (not the compiled Composer tree) before any compilation work. This ensures Composer is never called for a market-cap plan, even if `backtest_fn` is provided.
- **D-1 contract.** The outer `try/except` in `compile_plan` catches any unexpected internal error and returns `CompileResult(reason=type(exc).__name__)`. No key, path, or exception message body ever appears in a returned `reason` string.
- **Advisory-only.** No `LIVE_EXECUTION` reference, no Composer write/deploy endpoint, no entry in `_SETTINGS_WRITE_ALLOWLIST`.
