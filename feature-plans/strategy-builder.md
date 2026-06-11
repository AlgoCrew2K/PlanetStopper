# Planet Stopper — Strategy Builder (Living Program Doc)

**Status:** Phase 1 in progress (advisors/symphony_schema.py). This is the durable
program-level reference; phase-scoped working state lives in the
`strategy-builder-phase1-handoff.md` and the pinned grammar in
`strategy-builder-composer-grammar.md`.

---

## 1. Purpose

The Strategy Builder lets Planet Stopper **construct synthetic Composer symphony
trees** (the `raw_value` decision tree) and run them through Composer's backtest
endpoint, so the AI Advisor suite can propose, screen, and compare candidate
strategies rather than only mutating live ones. Phase 1 delivers the foundational
**schema layer** — a pure, dependency-free module that builds, validates, and
describes those trees. Later phases layer screening metrics, gating, and the
dashboard surface on top of it.

The schema layer is deliberately the narrow waist of the program: every later
phase that emits or inspects a tree goes through `advisors/symphony_schema.py`, so
the grammar is pinned and enforced in exactly one place.

---

## 2. Architecture Position vs the AC-X Constraints

The Strategy Builder is an **advisor-side, off-execution-path** capability. It does
not touch the 1-minute live execution loop, does not place trades, and does not
write live position state. That keeps it clear of the project's hard architecture
constraints:

- **AC-1 (no blocking I/O on the execution path):** the schema layer is pure
  in-memory tree work with no network or DB access; backtest network calls (a later
  phase) run in the advisor suite, never in `alpha_bot_execution.py`'s per-minute
  cycle.
- **AC-2 (dashboard is not a trade-action surface):** any future Strategy Builder
  dashboard view is read/propose-only; it never becomes a live-trade button and is
  excluded from `_SETTINGS_WRITE_ALLOWLIST`.
- **AC-3 (two-DB pattern, no cross-DB joins):** schema construction is DB-free;
  advisor observations are keyed by `symphony_id` in the state DB via the existing
  `advisor_observations` accessors.
- **AC-4 (`is_live=True` is explicit):** synthetic trees built here are backtest
  candidates, never live by default.
- **AC-5 (templates open SQLite read-only):** unchanged; the builder adds no write
  path.

Blast radius for Phase 1 is fenced to `advisors/symphony_schema.py`, `tests/**`, and
these `feature-plans/strategy-builder-*.md` docs.

---

## 3. Module Map (built + planned)

| Module | Phase | Role |
|--------|-------|------|
| `advisors/symphony_schema.py` | 1 (complete) | Build / validate / lint / describe Composer `raw_value` trees. Pure stdlib; never-raises validation. |
| `advisors/composer_backtest_client.py` | (existing) | POST /backtest transport; consumes trees the schema layer produces. |
| `advisors/backtest_gate_engine.py` | (existing) | Overfitting acceptance gate around backtest results. |
| `analytics.compute_quantstats_metrics()` | 2 (planned) | Metric engine for candidate-screening (quantstats lazily imported; NOT pinned in requirements.txt — Phase-2 team must resolve). |

---

## 4. Phase 1 — `advisors/symphony_schema.py` API Reference

The public surface below is verified against the committed implementation
(`advisors/symphony_schema.py`) and its 99-test contract suite
(`tests/advisors/test_symphony_schema.py`). The module is pure stdlib (`uuid`,
`copy`) with no network or DB access.

### Inspection functions (read-only, never raise, iterative traversal)

| Function | Returns | Contract |
|----------|---------|----------|
| `validate_tree(tree)` | `list[str]` | Hard structural errors only. Returns `[]` for a valid tree; a list of error strings (each naming the offending node id) otherwise. Never raises on any input — None, scalars, lists, malformed dicts, depth/count bombs all yield a list. Read-only. |
| `lint_tree(tree)` | `list[str]` | Soft advisory warnings (never hard errors): node count > `MAX_TOTAL_NODES`, depth > `MAX_TREE_DEPTH`, indicator fns not in `KNOWN_INDICATOR_FNS` (e.g. `rsi`, `standard-deviation-price`), and `wt-cash-specified` weight numerators not summing to 100. Read-only; never raises. |
| `extract_tickers(tree)` | `set[str]` | Set of every ticker string in the tree; empty set if none. Read-only; never raises. |
| `render_rules_text(tree)` | `str` | Deterministic, operator-readable text — one indented line per node, left-to-right child order. Every ticker in the tree appears in the output. Read-only; never raises. |

### Constructors (build-only; `validate_tree` is the validation gate)

Each emits a plain dict with grammar-doc field shapes, a fresh `uuid.uuid4()` id,
and **deep-copies its children** so no two parents ever share a mutable subtree.
Constructors do not themselves validate.

| Constructor | Signature | Builds |
|-------------|-----------|--------|
| `make_asset` | `(ticker, *, name="", exchange="")` | `asset` leaf (§3.9) |
| `make_root` | `(name, rebalance, children)` | `root` (§3.1) |
| `make_weight_equal` | `(children)` | `wt-cash-equal` (§3.6) |
| `make_weight_specified` | `(children_with_weights)` — list of `(child, num)` tuples; each child gets `weight={"num": num, "den": 100}` | `wt-cash-specified` (§5.1) |
| `make_inverse_vol` | `(children)` | `wt-inverse-vol` (§3.8) |
| `make_group` | `(name, children)` | `group` (§3.2) |
| `make_filter` | `(select_fn, select_n, sort_by_fn, children, *, window)` — emits nested `sort-by-fn-params: {"window": window}` | `filter` (§3.5) |
| `make_indicator` | `(fn, ticker, *, window)` → `{"fn", "fn-params": {"window"}, "val"}` descriptor | condition operand (§7) |
| `make_condition` | `(lhs_indicator, comparator, rhs)` — numeric `rhs` → `rhs-fixed-value? = True`, `rhs-val = str(rhs)`; string `rhs` → `rhs-fixed-value? = False`, `rhs-val = rhs` | condition descriptor consumed by `make_if` |
| `make_if` | `(condition, *, then_children, else_children)` — true-branch if-child carries flat `lhs-fn`/`lhs-fn-params`/`lhs-val`/`comparator`/`rhs-*`; else-branch carries only `is-else-condition? = True` | `if` + two `if-child` nodes (§3.3/§3.4) |

### Constants (grammar-pinned vocabulary; VERIFIED-LOCAL only)

`KNOWN_STEPS` (9 step values), `KNOWN_INDICATOR_FNS` (7 fns; the abbreviation `rsi`
is deliberately absent — the canonical token is `relative-strength-index`),
`KNOWN_COMPARATORS` (`gt`/`lt`/`lte`; `gte`/`eq` excluded per OQ-2),
`KNOWN_REBALANCE` (`daily`/`none`/`weekly`/`monthly`), `MAX_TOTAL_NODES` (500),
`MAX_TREE_DEPTH` (100). The two size caps are **lint thresholds, not validation
errors** (see below).

### Lint vs validate split (key design rule)

`validate_tree` returns **hard errors** — structural problems that make a tree
invalid (unknown step, missing required fields, duplicate ids, malformed weights,
`if` missing branches, non-asset leaves, garbage input). `lint_tree` returns **soft
warnings** — policy/heuristic concerns that do not invalidate the tree (node-count /
depth caps, unknown-but-tolerated indicator fns, weight sums ≠ 100). The two golden
fixtures (866 nodes / depth 19, and 8,455 nodes / depth 230) exceed any sane
node/depth cap yet must `validate_tree() == []`, which is exactly why caps are lint
warnings and not validation errors. Traversal is iterative (explicit stack) so the
depth-230 fixture cannot trigger `RecursionError`.

---

## 5. How Phase 1 Feeds Phases 2+

Phase 1 is the construction and validation substrate. Downstream:

- **Phase 2 (screening):** build candidate trees via the constructors → backtest via
  `composer_backtest_client` → score with `analytics.compute_quantstats_metrics()`.
  Note the DD sign conventions: quantstats `max_drawdown ≤ 0` while Composer stats
  report positive magnitude (`analytics.py:840`).
- **Later phases (gating, proposals, dashboard):** every emitted tree is gated by
  `validate_tree` before transport; `render_rules_text` powers human-readable
  proposal summaries and advisor observations.

---

## 6. Provenance & Open Questions

The grammar's evidence tiers (VERIFIED-LOCAL / VERIFIED-COMMUNITY / UNVERIFIED) and
the runtime-tolerant open questions (OQ-1…OQ-11 — e.g. `wt-market-cap`, `gte`,
weight-sum enforcement) live in `strategy-builder-composer-grammar.md`. The binding
Phase-1 contract amendments (which override the grammar doc where they conflict) live
in `strategy-builder-phase1-handoff.md`. This living doc summarizes; those two are
the source of truth for grammar and contract respectively.
