# advisors/symphony_schema

> Pure-stdlib Composer decision-tree schema layer: 16 constructors, 4 read-only inspection functions, and a grammar-pinned vocabulary that builds and validates synthetic ``raw_value`` trees for the Planet Stopper Strategy Builder. Constructor count stays at 16 — `make_weight_marketcap` was not added because Composer deprecated market-cap weighting (HTTP 422; 2026-06-20; see `DE-SB-MARKETCAP-DEPRECATED` in `DECISIONS.md`).

**Source:** `advisors/symphony_schema.py`
**Last updated:** 2026-06-20 (binary-encoding-fix: _collect_condition_tickers now collects binary-leaf lhs_ticker/rhs.ticker)

## Overview

`advisors/symphony_schema.py` implements the Phase-1 Strategy Builder schema layer. It constructs synthetic Composer `raw_value` decision trees for submission to `POST /api/v0.1/backtest` and provides read-only inspection of arbitrary trees — both ones the Strategy Builder builds and real `/score` responses captured from Composer.

The module is **pure stdlib** (`uuid`, `copy`, plain dicts). It has no Flask dependency, no database calls, no network I/O, and no live-trade paths.

### Design Contract (load-bearing)

| Invariant | Rule |
|-----------|------|
| **Never-raises** | `validate_tree`, `lint_tree`, `extract_tickers`, `render_rules_text` never raise on any input (garbage, `None`, scalars, malformed nesting) — they always return a list or set. |
| **Read-only** | All four inspection functions never mutate their input. |
| **Iterative traversal** | All tree traversal uses explicit stacks (`while stack`) — depth-230+ real fixtures and depth-5000 pathological inputs never trigger `RecursionError`. |
| **Deep-copy** | All constructors deep-copy mutable inputs (children lists, operand dicts, condition blocks) so no two parent nodes share a mutable subtree. |
| **Fresh ids** | Every constructor assigns a fresh `uuid.uuid4()` id to every node it creates. |
| **Hard vs lint** | `validate_tree` returns structural HARD errors only. Unknown indicator fns, size/depth cap violations, and weight-sum drift are lint warnings (`lint_tree`) — not hard errors. Real `/score` trees routinely exceed the construction-side caps. |

---

## Constants

### Vocabulary Sets (public)

| Constant | Type | Description |
|----------|------|-------------|
| `KNOWN_STEPS` | `frozenset[str]` | 9 confirmed step values: `root`, `group`, `if`, `if-child`, `filter`, `wt-cash-equal`, `wt-cash-specified`, `wt-inverse-vol`, `asset`. |
| `KNOWN_COMPARATORS` | `frozenset[str]` | Confirmed comparators: `gt`, `lt`, `gte`, `lte`. `gte` is corpus-verified (n≈39,596 across 10,441 symphonies — AC-1). `eq`/`neq` are absent from the corpus and excluded. |
| `KNOWN_REBALANCE` | `frozenset[str]` | Confirmed rebalance cadences: `daily`, `none`, `weekly`, `monthly`, `quarterly` (n≈58, AC-2), `yearly` (n≈27, AC-2). |
| `KNOWN_INDICATOR_FNS` | `frozenset[str]` | 13 confirmed indicator fn strings. Original 7 VERIFIED-LOCAL: `relative-strength-index`, `cumulative-return`, `max-drawdown`, `current-price`, `standard-deviation-return`, `moving-average-price`, `moving-average-return`. Six new corpus-verified (AC-3): `exponential-moving-average-price` (n≈45,816), `standard-deviation-price`, `percentage-price-oscillator`, `percentage-price-oscillator-signal`, `upper-bollinger`, `lower-bollinger`. |

### Size Ceilings (public)

| Constant | Value | Description |
|----------|-------|-------------|
| `MAX_TOTAL_NODES` | `500` | Construction-side size ceiling. Lint warning only — real `/score` trees exceed this (866 / 8455 nodes in golden fixtures). |
| `MAX_TREE_DEPTH` | `100` | Construction-side depth ceiling. Lint warning only. |
| `MAX_CONDITION_DEPTH` | `400` | Maximum depth for iterative compound condition block validation. Hard-errors when exceeded rather than descending further. Safely contains the corpus depth-230. (AC-10, grammar-foundation) |

### Compound Grammar Constants (private but documented)

| Constant | Value | Description |
|----------|-------|-------------|
| `_KNOWN_CONDITION_TYPES` | `{"binary", "binary-compound", "compound"}` | Valid `condition-type` strings on compound condition blocks. Unknown values are HARD errors. |
| `_KNOWN_OPERATORS` | `{"any", "all"}` | Valid `operator` strings on compound and binary-compound blocks. |

---

## Read-Only Inspection Functions

### `validate_tree(tree) → list[str]`

Return a list of HARD structural errors for a Composer decision tree. Empty list means the tree is structurally valid.

**Never raises.** Never mutates input. Iterative DFS.

**Hard errors detected:**
- Top-level input is not a dict, or root step ≠ `"root"`
- Unknown `step` value (not in `KNOWN_STEPS`)
- Unknown `comparator` on a true-branch if-child (not in `KNOWN_COMPARATORS`)
- Unknown `rebalance` on root (not in `KNOWN_REBALANCE`)
- Missing required fields: root needs `name`/`rebalance`/`children`; asset needs `ticker`; if-child needs `lhs-fn` (unless carrying a compound `condition` block)
- **Compound `condition` blocks** (grammar-foundation AC-10): unknown `condition-type`, bad `operator`, missing `conditions` key, missing `tickers` key on `binary-compound` — all hard errors at any nesting depth, bounded by `MAX_CONDITION_DEPTH`

**Not hard errors** (lint-only):
- Unknown indicator fn string
- Tree size or depth exceeding construction-side caps
- Weight-sum drift

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `tree` | `Any` | The tree to validate. Any input type accepted without raising. |

**Returns:** `list[str]` — error strings, possibly empty.

---

### `lint_tree(tree) → list[str]`

Return soft warnings for a decision tree. Warnings are advisory — they do not block backtest submission.

**Never raises.** Never mutates input. Iterative DFS.

**Lint warnings include:**
- Unknown indicator fn string (not in `KNOWN_INDICATOR_FNS`)
- Tree exceeds `MAX_TOTAL_NODES` or `MAX_TREE_DEPTH`
- `wt-cash-specified` weight numerators do not sum to 100

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `tree` | `Any` | The tree to lint. |

**Returns:** `list[str]` — warning strings, possibly empty.

---

### `extract_tickers(tree) → set[str]`

Return the set of all real ticker strings present in the tree.

**Never raises.** Never mutates input. Iterative DFS.

**AC-9 (grammar-foundation):** Extended to collect tickers from compound `condition` blocks via `_collect_condition_tickers`. Collects:
- `binary-compound` nodes: `tickers[]` list (the broadcast lhs operand pool)
- `binary` nodes (binary-encoding-fix, 2026-06-20): `lhs_ticker` (the lhs operand ticker) and `rhs.ticker` (the rhs comparison ticker, when present) — both skipping the `%` placeholder. Prior to this fix, `extract_tickers` did not descend into binary-leaf `lhs_fn`/`lhs_ticker` fields, so a strategy gating on e.g. RSI(PSR) referenced PSR in the condition but `extract_tickers` returned an empty set for that condition operand — causing the membership validator in the generator to incorrectly pass or reject plans. This fix closes a pre-existing `extract_tickers` blind spot for binary condition operands.

The `%` placeholder emitted by `make_binary_compound_condition` for the broadcast lhs operand is excluded in all paths — it is a grammar placeholder, not a real ticker.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `tree` | `Any` | The tree to extract tickers from. |

**Returns:** `set[str]` — real ticker strings. Never contains `"%"`. Empty set if no tickers found.

---

### `render_rules_text(tree) → str`

Return a deterministic, human-readable text representation of the tree's decision rules.

**Never raises.** Never mutates input. Iterative DFS.

**AC-9 (grammar-foundation):** Extended to render compound condition blocks — `binary-compound` nodes render as `WHEN ANY/all([tickers]) fn comparator rhs`, surfacing the gate type and watched tickers in human-readable form.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `tree` | `Any` | The tree to render. |

**Returns:** `str` — multi-line human-readable rendering. Empty string on non-dict input.

---

## Constructors

All constructors return `dict` nodes with a fresh `uuid.uuid4()` id and deep-copied inputs. None raise on valid input; they raise `ValueError` on explicit invalid arguments (documented per function).

### Original 10 Constructors (Phase-1)

#### `make_asset(ticker, *, name="", exchange="") → dict`

Build a leaf asset node.

| Param | Type | Description |
|-------|------|-------------|
| `ticker` | `str` | Ticker symbol (e.g. `"SPY"`). |
| `name` | `str` | Optional display name. |
| `exchange` | `str` | Optional exchange identifier. |

---

#### `make_root(name, rebalance, children) → dict`

Build the root node of a symphony tree.

**Live-required fields (2026-06-18):** The output now includes `"description": ""`. The live Composer `POST /api/v0.1/backtest` API enforces this field and returns HTTP 400 when it is absent. The default empty string is the value present in every real `/score` response.

| Param | Type | Description |
|-------|------|-------------|
| `name` | `str` | Symphony display name. |
| `rebalance` | `str` | Rebalance cadence — must be in `KNOWN_REBALANCE`. |
| `children` | `list` | Child nodes (deep-copied). |

**Output includes:**
- `"step": "root"`
- `"name"`, `"rebalance"`, `"id"` (fresh uuid4), `"children"` (deep-copied)
- `"description": ""` (additive default; live-required by Composer /backtest as of 2026-06-18)

---

#### `make_weight_equal(children) → dict`

Build a `wt-cash-equal` node (equal weight across children).

---

#### `make_weight_specified(children_with_weights) → dict`

Build a `wt-cash-specified` node with explicit numeric weights.

| Param | Type | Description |
|-------|------|-------------|
| `children_with_weights` | `list[tuple]` | List of `(child_node, weight)` pairs. Weights are numeric; must sum to 100 to pass lint. |

---

#### `make_inverse_vol(children) → dict`

Build a `wt-inverse-vol` node (inverse-volatility weight across children).

**Live-required fields (2026-06-18):** The output now includes `"window-days": 30`. The live Composer `POST /api/v0.1/backtest` API enforces this field and returns HTTP 422 ("unknown-function-parameter") when it is absent. 30 is the Composer UI default and the value carried by real `/score` fixtures (`sample_score_large.json` — VERIFIED-LOCAL). The prior grammar doc OQ-8 note "no params observed; omit" is superseded.

| Param | Type | Description |
|-------|------|-------------|
| `children` | `list` | Child asset or allocation nodes (deep-copied). |

**Output includes:**
- `"step": "wt-inverse-vol"`
- `"id"` (fresh uuid4), `"children"` (deep-copied)
- `"window-days": 30` (additive default; live-required by Composer /backtest as of 2026-06-18)

---

#### `make_group(name, children) → dict`

Build a named `group` node.

---

#### `make_filter(sort_by_fn, sort_by_ticker, sort_by_window, select_top, children) → dict`

Build a `filter` node that ranks children by an indicator and selects the top-N.

---

#### `make_indicator(fn, ticker, *, window) → dict`

Build a flat indicator descriptor for use with `make_condition`. Emits the `fn-params` / `val` shape required by `make_if`'s flat true-branch if-child.

| Param | Type | Description |
|-------|------|-------------|
| `fn` | `str` | Indicator fn string (e.g. `"relative-strength-index"`). |
| `ticker` | `str` | Ticker symbol. |
| `window` | `int` | Lookback window in days. |

---

#### `make_condition(lhs, comparator, rhs) → dict`

Build a flat condition descriptor for use with `make_if`. Takes `make_indicator(...)` dicts as lhs/rhs.

| Param | Type | Description |
|-------|------|-------------|
| `lhs` | `dict` | A `make_indicator(...)` descriptor. |
| `comparator` | `str` | One of `KNOWN_COMPARATORS`. |
| `rhs` | `dict` | A `make_indicator(...)` or `{"rhs-val": N, "rhs-fixed-value?": True}` dict. |

---

#### `make_if(condition, *, then_children, else_children) → dict`

Build an if node from a flat `make_condition(...)` descriptor. Extracts `lhs-fn`/`comparator`/`rhs-*` fields onto the true-branch if-child. Use `make_if_compound` for compound condition blocks.

---

### 6 New Constructors (grammar-foundation AC-4..AC-8)

#### `make_condition_operand(fn, ticker, *, window) → dict`

Build a condition operand for use in compound condition constructors.

Returns the grammar §7 compound-operand shape: `{"fn": fn, "ticker": ticker, "params": {"window": window}}`.

Distinct from `make_indicator`: use this for compound conditions (`make_binary_condition`, `make_binary_compound_condition`); use `make_indicator` for flat conditions (`make_condition` + `make_if`).

The `ticker="%"` placeholder is faithfully emitted when passed — used internally by `make_binary_compound_condition`.

| Param | Type | Description |
|-------|------|-------------|
| `fn` | `str` | Indicator fn string. |
| `ticker` | `str` | Ticker symbol (or `"%"` for placeholder). |
| `window` | `int` | Lookback window (keyword-only). |

---

#### `make_constant_rhs(value) → dict`

Build a constant rhs descriptor: `{"constant": value}`.

| Param | Type | Description |
|-------|------|-------------|
| `value` | `Any` | Numeric scalar (int, float, or JSON-compatible). Stored as-is. |

---

#### `make_binary_condition(lhs_operand, comparator, rhs) → dict`

Build a binary leaf condition (grammar §7 `condition-type="binary"`).

| Param | Type | Description |
|-------|------|-------------|
| `lhs_operand` | `dict` | A `make_condition_operand(...)` dict (deep-copied). |
| `comparator` | `str` | One of `KNOWN_COMPARATORS`. |
| `rhs` | `dict` | A `make_constant_rhs(...)` or another `make_condition_operand(...)` dict (deep-copied). |

**Returns:** `{"condition-type": "binary", "lhs": ..., "comparator": ..., "rhs": ...}`

---

#### `make_binary_compound_condition(fn, tickers, comparator, rhs, *, window, operator="any") → dict`

Build a binary-compound condition (grammar §7 `condition-type="binary-compound"`).

The **frontrunner primitive**: broadcast one predicate (`fn(%) comparator rhs`) over a list of tickers with `any`/`all` semantics. The lhs ticker is the grammar §7 placeholder `"%"`; the real watched tickers live in the top-level `tickers` list.

| Param | Type | Description |
|-------|------|-------------|
| `fn` | `str` | Indicator fn string (e.g. `"relative-strength-index"`). |
| `tickers` | `list[str]` | Non-empty list of ticker strings to broadcast over. |
| `comparator` | `str` | One of `KNOWN_COMPARATORS`. |
| `rhs` | `dict` | A `make_constant_rhs(...)` dict. |
| `window` | `int` | Indicator window (keyword-only). |
| `operator` | `str` | `"any"` (OR) or `"all"` (AND). Defaults to `"any"`. |

**Raises:** `ValueError` if `operator` not in `{"any", "all"}` or if `tickers` is empty.

**Returns:** `{"condition-type": "binary-compound", "operator": ..., "lhs": {ticker: "%", ...}, "tickers": [...], "comparator": ..., "rhs": ...}`

---

#### `make_compound_condition(operator, conditions) → dict`

Build a compound condition (grammar §7 `condition-type="compound"`).

Joins N sub-conditions with `any` (OR) or `all` (AND) semantics. Sub-conditions can be binary leaves, binary-compound blocks, or nested compound blocks (fully nestable). Each sub-condition is deep-copied.

| Param | Type | Description |
|-------|------|-------------|
| `operator` | `str` | `"any"` or `"all"`. |
| `conditions` | `list[dict]` | Non-empty list of condition dicts. |

**Raises:** `ValueError` if `operator` invalid or `conditions` is empty.

**Returns:** `{"condition-type": "compound", "operator": ..., "conditions": [...]}`

---

#### `make_if_compound(condition_block, *, then_children, else_children) → dict`

Build an if node whose true-branch if-child carries a compound condition block.

Unlike `make_if` (which extracts flat lhs-fn/comparator fields from a `make_condition(...)` descriptor), `make_if_compound` stores the authoritative `condition` block dict directly on the true-branch if-child. `validate_tree` compound-block validation (Amendment 6, AC-10) recognises this and skips the flat-field requirements.

All inputs are deep-copied. Fresh uuid4 ids assigned to the if node and both if-child nodes.

| Param | Type | Description |
|-------|------|-------------|
| `condition_block` | `dict` | A compound condition dict built via `make_binary_condition`, `make_binary_compound_condition`, or `make_compound_condition`. |
| `then_children` | `list` | Allocation nodes for the true branch (keyword-only, deep-copied). |
| `else_children` | `list` | Allocation nodes for the else branch (keyword-only, deep-copied). |

**Returns:** Full `{"step": "if", "id": ..., "children": [true_child, else_child]}` node.

---

## Grammar-Foundation Additions (AC-1..AC-12, 2026-06-14)

This cycle reversed the prior OQ-2 unconfirmed stance and corpus-verified the following grammar tokens:

| Token | Count in corpus | Decision |
|-------|----------------|----------|
| `gte` (comparator) | n≈39,596 | Added to `KNOWN_COMPARATORS` — was wrongly excluded |
| `quarterly` (rebalance) | n≈58 | Added to `KNOWN_REBALANCE` |
| `yearly` (rebalance) | n≈27 | Added to `KNOWN_REBALANCE` |
| `exponential-moving-average-price` | n≈45,816 | Added to `KNOWN_INDICATOR_FNS` |
| `standard-deviation-price` | n≈5,572 | Promoted from lint-only to `KNOWN_INDICATOR_FNS` |
| `percentage-price-oscillator` | corpus-verified | Added to `KNOWN_INDICATOR_FNS` |
| `percentage-price-oscillator-signal` | corpus-verified | Added to `KNOWN_INDICATOR_FNS` |
| `upper-bollinger` | corpus-verified | Added to `KNOWN_INDICATOR_FNS` |
| `lower-bollinger` | corpus-verified | Added to `KNOWN_INDICATOR_FNS` |

**Compound condition grammar (§7):** The 6 new constructors implement the binary / binary-compound / compound `condition-type` grammar. `_validate_condition_block` enforces structural correctness at any nesting depth via an iterative DFS bounded by `MAX_CONDITION_DEPTH=400` (hard error when exceeded). The `%` placeholder on binary-compound lhs is a grammar fact, not a real ticker — `extract_tickers` and the test reference walker both exclude it.

---

## Internal Dependencies

- `uuid` — fresh node id generation (`uuid.uuid4()`)
- `copy` — deep-copy of children lists and condition block dicts (`copy.deepcopy`)
- No external imports; no Flask; no database.
