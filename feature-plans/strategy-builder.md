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
| `advisors/symphony_schema.py` | 1 (in progress) | Build / validate / lint / describe Composer `raw_value` trees. Pure, never-raises validation. |
| `advisors/composer_backtest_client.py` | (existing) | POST /backtest transport; consumes trees the schema layer produces. |
| `advisors/backtest_gate_engine.py` | (existing) | Overfitting acceptance gate around backtest results. |
| `analytics.compute_quantstats_metrics()` | 2 (planned) | Metric engine for candidate-screening (quantstats lazily imported; NOT pinned in requirements.txt — Phase-2 team must resolve). |

---

## 4. Phase 1 — `advisors/symphony_schema.py` API Reference

> **PLACEHOLDER — finalized after Toxic Pair convergence.** The public surface
> below is taken from the RED contract (`tests/advisors/test_symphony_schema.py`)
> and the binding handoff amendments; arg/return semantics are verified against the
> committed implementation before this section is marked final.

### Contract guarantees (from the RED suite)

- **`validate_tree(tree) -> list[str]`** never raises on any input (None, scalars,
  lists, malformed dicts, depth/count bombs); returns a list of hard-error strings.
- **`lint_tree(tree) -> list[str]`** returns soft-warning strings; read-only; never
  conflates warnings with hard errors.
- **`extract_tickers(tree) -> set[str]`** and **`render_rules_text(tree) -> str`**
  are read-only and deterministic.
- Constructors (`make_asset`, `make_indicator`, `make_condition`, `make_if`,
  `make_weight_equal`, `make_weight_specified`, `make_inverse_vol`, `make_group`,
  `make_filter`, `make_root`) emit fresh UUID-v4 ids per call, share no mutable
  child lists, and produce trees that `validate_tree` accepts.
- Constants: `KNOWN_STEPS`, `KNOWN_INDICATOR_FNS`, `KNOWN_COMPARATORS`,
  `KNOWN_REBALANCE`, `MAX_TREE_DEPTH`, `MAX_TOTAL_NODES`.

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
