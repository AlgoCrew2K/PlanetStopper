# Strategy Builder — Phase 2 Contract: `advisors/strategy_builder_engine.py`

> **PHASE 2 COMPLETE — 2026-06-12.** Delivered by Toxic Pair TDD team (resumed
> session after a mid-phase lead crash; no work lost). Final: 97 passed / 2
> conditional skips / 0 failed; ruff clean; zero full-suite collateral vs
> baseline. Reviewer round 1 found 1 BLOCKER (`select-fn` values must be
> `top`/`bottom` per grammar §3.5) + 2 MAJOR (head→tail alignment in
> correlation/blended-DD screens; T6/T7 `sort-by-fn` unverified) — all fixed,
> round 2 dual APPROVE. **PM-ACCEPTED deviation:** T6/T7 emit
> `cumulative-return`/`standard-deviation-return` in sort-by position where
> they are verified only as indicator fns — documented in code + grammar doc;
> runtime degradation is graceful (failed backtest → candidate excluded and
> marked). First live backtest run will attest or refute; revisit then.
> [PM-ASSUMED] values implemented as ledgered in the team exit report
> (MAX_CANDIDATES_PER_RUN=30, screen defaults, objective→template mapping).

**Status:** BINDING contract for the Phase-2 Toxic Pair TDD team. Where this doc
conflicts with the living doc (`strategy-builder.md`), this doc wins for Phase 2.
Grammar authority: `strategy-builder-composer-grammar.md` incl. §16. Schema layer
authority: `advisors/symphony_schema.py` (Phase 1, shipped, 108-test contract).

**PM-ASSUMED markers:** the original Phase-2 conversation was not fully committed
to docs. Items tagged `[PM-ASSUMED]` are the PM's reconstruction — implement as
specified, but flag in the exit report so the operator can correct cheaply.

---

## 1. Purpose

`advisors/strategy_builder_engine.py` proposes **new candidate symphonies from
scratch** (vs the existing engines that mutate live ones): build trees from a
pinned template library via `symphony_schema` constructors → backtest the batch
via `composer_backtest_client.run_backtest` → screen on quantstats metrics →
apply the **single-batch FDR gate** via
`backtest_gate_engine.evaluate_candidate_batch` → persist survivors as advisory
observations. Off-execution-path (AC-1); advisory-only (AC-2/AC-4).

## 2. Hard requirements

1. **Every emitted tree** passes `symphony_schema.validate_tree(tree) == []`
   before any transport, and uses ONLY VERIFIED-LOCAL/VERIFIED-COMMUNITY grammar
   constructs (no `wt-market-cap`, no `gte`, no `exponential-moving-average-price`).
2. **FDR integrity (AC-3.2):** ONE call to `evaluate_candidate_batch` per
   proposal run, containing **every candidate that was backtested** — metric
   screens must NOT shrink the batch before the gate (that would silently lower
   the multiplicity bar). Screens apply to the gate's *survivor list* afterward
   (ranking/filtering presentation), never to the gate's input. n_candidates in
   the persisted observation must equal the number backtested.
3. **Candidate generation is objective-directed and bounded:** a run takes an
   explicit objective (enum: `diversify`, `cut_drawdown`, `lift_risk_adjusted`)
   `[PM-ASSUMED]` and emits a bounded batch (`MAX_CANDIDATES_PER_RUN = 30`
   `[PM-ASSUMED]`, named constant + comment). No brute-force grids.
4. **Returns-scale discipline:** `BacktestResult.daily_returns` are log returns
   keyed by ISO date; `BacktestCandidate.daily_returns_pct` wants chronological
   %-scale; `compute_quantstats_metrics` wants %-scale in, returns fraction-scale
   out with `max_drawdown <= 0`. Follow the existing conversion in
   `asset_swap_engine.py` EXACTLY (do not re-derive). Sign conventions get
   dedicated golden tests.
5. **Rate limiting:** Composer 1 req/s — reuse the client's existing
   pacing/retry; the engine never adds parallel backtest calls.
6. **Never-raises engine surface:** the public entry point returns a result
   object with an `error` field on any failure (one candidate's failure never
   aborts the batch — mirror `composer_backtest_client` AC-X5).
7. **Fixture-first:** all tests run on fixtures (captured backtest responses +
   synthetic returns); zero live API calls in the default suite.
8. **Persistence:** survivors → `database.insert_advisor_observation`
   (symphony_id-keyed, advisory-only), carrying: objective, template id, params,
   `render_rules_text` output, metric dict, gate verdict + `winner_p_adj`,
   n_candidates, and the mandatory overfitting caveat used by the other engines.
9. **quantstats dependency:** verify whether quantstats is pinned in
   requirements.txt; if not, pin it (additive). Resolve the living-doc note §3.

## 3. Template library (the "CFIT-style" set) `[PM-ASSUMED selection]`

Pinned, parameterized templates — each a pure function returning a tree built
exclusively from `symphony_schema` constructors. Initial set (all constructs
verified in grammar doc):

| ID | Template | Construction |
|----|----------|--------------|
| T1 | `equal_weight_basket(tickers)` | root → wt-cash-equal → assets |
| T2 | `specified_weight_basket(weighted_tickers)` | root → wt-cash-specified (weights sum 100) |
| T3 | `inverse_vol_basket(tickers)` | root → wt-inverse-vol → assets |
| T4 | `trend_switch(signal_ticker, ma_window, risk_on, risk_off)` | if current-price > moving-average-price(window) → risk-on basket else defensive basket |
| T5 | `rsi_rotation(signal_ticker, rsi_window, threshold, overbought_children, neutral_children)` | if relative-strength-index > threshold → overbought branch else neutral branch |
| T6 | `momentum_top_n(universe, n, window)` | filter select-top n by cumulative-return(window) |
| T7 | `low_vol_floor(universe, n, window)` | filter select-bottom n by standard-deviation-return(window) |

Composability (e.g., T4 whose risk-on child is a T6 filter) is allowed but not
required for Phase 2. Every template output must validate clean and round-trip
`json.dumps`.

## 4. Screens (configurable, post-gate presentation filters)

`ScreenConfig` dataclass with named-constant defaults `[PM-ASSUMED defaults —
name them, comment them, and flag in exit report]`: `min_cagr`, `min_sharpe`,
`min_calmar`, `max_abs_drawdown` (candidate-level), `max_blended_abs_drawdown`
(dual-level DD: candidate returns blended 50/50 with the live portfolio's
returns series — the second level), `max_correlation` (Pearson vs live portfolio
daily returns). All metrics from `analytics.compute_quantstats_metrics` +
stdlib/`statistics` correlation. None-metric (insufficient data) → screen fails
closed (candidate marked, not silently passed).

## 5. Module shape

- `propose_strategies(objective, universe, screen_config, live_returns, ...) -> ProposalRun`
  (NamedTuple/dataclass: candidates, gated_batch summary, screened_survivors,
  observations_written, error).
- Templates + screens + orchestration in `advisors/strategy_builder_engine.py`;
  no new DB accessors (use existing `database.py` API); no app.py changes in
  Phase 2 (dashboard surface is a later phase).
- Blast radius: `advisors/strategy_builder_engine.py`, `tests/**`,
  `feature-plans/strategy-builder*.md`, `requirements.txt` (quantstats pin only),
  `.claude/CLAUDE.md` (key-files row, PM applies).

## 6. Team & process (per project hard requirement)

Quint via Agent Teams: test-writer (`quant-test-writer`) ⇄ implementer Toxic
Pair, `quant-code-reviewer`, domain reviewer (`composer-alpaca-integration`),
doc-writer. Minimum 2 adversarial cycles. Full-suite collateral check at close.
Exit report must list every `[PM-ASSUMED]` item with the value chosen.
