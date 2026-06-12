# Planet Stopper — Strategy Builder (Living Program Doc)

**Status:** Phase 1 complete (`advisors/symphony_schema.py`, 108-test contract suite,
2026-06-11). Phase 2 in progress (`advisors/strategy_builder_engine.py` — templates,
screens, FDR gate). This is the durable program-level reference; phase-scoped working
state lives in `strategy-builder-phase1-handoff.md` (Phase 1 archive) and the binding
Phase-2 contract in `strategy-builder-phase2-contract.md`. Grammar pinned in
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
| `analytics.compute_quantstats_metrics()` | 2 (in progress) | Metric engine for candidate-screening (quantstats pinned in requirements.txt by Phase-2 team). |
| `advisors/strategy_builder_engine.py` | 2 (in progress) | Propose candidate symphonies from templates T1-T7 → backtest → FDR-gate → persist survivors as advisor observations. |

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

## 5. How Phase 1 Feeds Phase 2

Phase 1 is the construction and validation substrate. Phase 2 consumes it directly:

- **Tree construction:** all candidate trees are built exclusively via Phase-1
  constructors; every emitted tree is validated with `validate_tree(tree) == []`
  before any network transport.
- **Grammar discipline:** Phase 2 uses only VERIFIED-LOCAL/VERIFIED-COMMUNITY
  constructs (no `wt-market-cap`, no `gte`, no `exponential-moving-average-price`).
- **Human-readable proposals:** `render_rules_text` is embedded in every persisted
  advisor observation so the dashboard can display proposals without re-interpreting
  the raw tree.
- **DD sign conventions:** quantstats `max_drawdown <= 0` (fraction-scale); Composer
  stats report positive magnitude (`analytics.py:840`). Phase 2 follows the
  conversion in `asset_swap_engine.py` exactly.

---

## 6. Phase 2 — `advisors/strategy_builder_engine.py`

Phase 2 adds a proposal engine that generates **new candidate symphonies from
scratch** (vs existing engines that mutate live ones). Binding contract:
`feature-plans/strategy-builder-phase2-contract.md`.

### 6.1 Public entry point

```python
propose_strategies(
    objective,        # Objective enum: diversify | cut_drawdown | lift_risk_adjusted  [PM-ASSUMED]
    universe,         # list[str] tickers available for construction
    screen_config,    # ScreenConfig dataclass (see §6.4)
    live_returns,     # dict[str, float] — live portfolio daily returns, %-scale
    ...
) -> ProposalRun
```

`ProposalRun` is a NamedTuple/dataclass carrying: `candidates`, `gated_batch`
summary, `screened_survivors`, `observations_written`, `error`. Never raises —
failures surface via the `error` field; one candidate's failure never aborts the
batch.

### 6.2 Template library (T1-T7) `[PM-ASSUMED selection]`

All templates are pure functions returning trees built exclusively via
`symphony_schema` constructors. Every output must pass `validate_tree() == []`
and round-trip `json.dumps`.

| ID | Function | Construction |
|----|----------|--------------|
| T1 | `equal_weight_basket(tickers)` | root → wt-cash-equal → assets |
| T2 | `specified_weight_basket(weighted_tickers)` | root → wt-cash-specified (weights sum 100) |
| T3 | `inverse_vol_basket(tickers)` | root → wt-inverse-vol → assets |
| T4 | `trend_switch(signal_ticker, ma_window, risk_on, risk_off)` | if current-price > moving-average-price(window) → risk-on basket else defensive basket |
| T5 | `rsi_rotation(signal_ticker, rsi_window, threshold, overbought_children, neutral_children)` | if relative-strength-index > threshold → overbought branch else neutral branch |
| T6 | `momentum_top_n(universe, n, window)` | filter select-top n by cumulative-return(window) |
| T7 | `low_vol_floor(universe, n, window)` | filter select-bottom n by standard-deviation-return(window) |

Composability (e.g. T4 whose risk-on child is a T6 filter) is allowed but not
required for Phase 2.

### 6.3 Objective enum `[PM-ASSUMED]`

```python
class Objective(enum.Enum):
    diversify            = "diversify"
    cut_drawdown         = "cut_drawdown"
    lift_risk_adjusted   = "lift_risk_adjusted"
```

The objective steers template selection and parameter ranges within a run; it does
not override the FDR gate.

### 6.4 ScreenConfig defaults `[PM-ASSUMED values — flagged for operator review]`

`ScreenConfig` is a dataclass with named-constant defaults. Screens are
**post-gate presentation filters** only; they never shrink the batch that enters
`evaluate_candidate_batch` (FDR integrity — see §6.5).

| Constant | Default | Meaning |
|----------|---------|---------|
| `SCREEN_MIN_CAGR_DEFAULT` | `0.0` `[PM-ASSUMED]` | Minimum CAGR (fraction-scale) to surface a survivor |
| `SCREEN_MIN_SHARPE_DEFAULT` | `0.0` `[PM-ASSUMED]` | Minimum Sharpe ratio |
| `SCREEN_MIN_CALMAR_DEFAULT` | `0.0` `[PM-ASSUMED]` | Minimum Calmar ratio |
| `SCREEN_MAX_ABS_DRAWDOWN_DEFAULT` | `0.50` `[PM-ASSUMED]` | Max candidate-level absolute drawdown (0.50 = 50%) |
| `SCREEN_MAX_BLENDED_ABS_DRAWDOWN_DEFAULT` | `0.40` `[PM-ASSUMED]` | Max blended abs drawdown — candidate returns blended 50/50 with live portfolio |
| `SCREEN_MAX_CORRELATION_DEFAULT` | `0.85` `[PM-ASSUMED]` | Max Pearson correlation vs live portfolio daily returns |

None-metric (insufficient data for a screen) → screen fails closed (candidate
marked as failing, never silently passed).

### 6.5 FDR integrity protocol (AC-3.2)

**One call** to `backtest_gate_engine.evaluate_candidate_batch` per proposal run,
containing **every candidate that was backtested**. The ordering is strict:

1. Build all candidates from templates (bounded by `MAX_CANDIDATES_PER_RUN = 30`
   `[PM-ASSUMED]`).
2. Backtest the full batch (Composer 1 req/s pacing — engine never adds parallel
   calls).
3. Pass the entire backtested batch to `evaluate_candidate_batch` in a single call.
4. Apply `ScreenConfig` screens to the gate's *survivor list* for ranking/presentation.

Metric screens must **NOT** shrink the batch before step 3 — that would silently
lower the multiplicity bar and invalidate the FDR correction. The persisted
observation's `n_candidates` must equal the number backtested in step 2.

### 6.6 Persistence

Survivors are written via `database.insert_advisor_observation`
(symphony_id-keyed, advisory-only). Each observation carries: objective, template
id, params, `render_rules_text` output, metric dict, gate verdict + `winner_p_adj`,
`n_candidates`, and the mandatory overfitting caveat used by other advisor engines.

### 6.7 Returns-scale discipline

`BacktestResult.daily_returns` are log returns keyed by ISO date.
`BacktestCandidate.daily_returns_pct` wants chronological %-scale.
`compute_quantstats_metrics` wants %-scale in, returns fraction-scale out with
`max_drawdown <= 0`. The conversion follows `asset_swap_engine.py` exactly — do
not re-derive. Sign conventions have dedicated golden tests.

---

## 7. Provenance & Open Questions

The grammar's evidence tiers (VERIFIED-LOCAL / VERIFIED-COMMUNITY / UNVERIFIED) and
the runtime-tolerant open questions (OQ-1…OQ-11 — e.g. `wt-market-cap`, `gte`,
weight-sum enforcement) live in `strategy-builder-composer-grammar.md`. The binding
Phase-1 contract amendments (which override the grammar doc where they conflict) live
in `strategy-builder-phase1-handoff.md`. The binding Phase-2 contract lives in
`strategy-builder-phase2-contract.md`. This living doc summarizes; those files are
the source of truth for grammar and contracts respectively.
