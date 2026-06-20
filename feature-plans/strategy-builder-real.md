# Strategy Builder - Real Opus-Driven Symphony Builder

**Spec writer:** spec-writer
**Date:** 2026-06-20
**Tier:** 3 (complex / multi-feature / new external integrations)
**Status:** ready

> Gate-1 acceptance-criteria document. This bounds the **WHAT**. The HOW
> (worker decomposition, exact module layout, prompt text) is the PM's Gate-2
> decision. The DESIGN is operator-decided and formalized here - workers may NOT
> substitute a different architecture.

---

## Summary

Replace the Strategy Builder's fixed 7-template stamper
(`advisors/strategy_builder_engine.py::_generate_candidate_trees`, lines
392-543) with a **real Opus-driven symphony builder**. Opus generates diverse
strategy build-plans, proposing tickers from its own (objective-guided) market
knowledge; each proposed ticker is validated for membership in a weekly-cached
**full tradeable universe set**; a deterministic compiler translates each plan
into a valid Composer `raw_value` tree via the existing
`advisors/symphony_schema.py` constructors, exercising the FULL grammar (nested
groups, every weighting scheme including a new `wt-marketcap`, simple + compound
conditions, filters); a validate-and-repair loop guarantees only valid,
tradeable trees reach the **unchanged downstream pipeline** (Composer backtest ->
single-batch FDR gate -> screens -> persist as advisory observations).
**Composer `/backtest` is the final arbiter of tradeability.** The builder is
**dual-mode**: alongside the net-new Opus builds it also SUGGESTS existing Atlas
community strategies that MATCH the objective (objective-ranked via the stats the
community loader already returns); both candidate sources flow through the SAME
backtest + FDR gate and are surfaced together, provenance-tagged (built-new vs
atlas-suggested). It supports FOUR objectives: diversify / cut_drawdown /
lift_risk_adjusted / volatility_mitigation. The overfitting cull is
strengthened to autotuner-grade (the ONE deliberate downstream change): a PBO
veto is wired into `acceptance_gate` (today disabled) and the always-zero
OOS-alpha baseline is replaced with a real SPY-OOS-over-the-fold benchmark, with
built-new and Atlas candidates culled identically. The builder runs
**automatically weekly AND on-demand**. It is **advisory-only**: it proposes
symphonies for operator review and NEVER auto-deploys, NEVER touches
`LIVE_EXECUTION`, and adds NO new settings-write path.

This ships **fully functional in one cycle** - operator directive: no MVP, no
staging, no fast-follows. Full grammar + full dynamic universe + weekly
automation all land together.

### Design correction (operator, 2026-06-20) - NO liquidity ranking

Component 1 is **membership/validation only**. There is NO dollar-volume
computation, NO liquidity ranking, NO curated "liquid palette", NO top-N. The
provider does exactly: **fetch the full tradeable set -> cache weekly ->
membership lookup**. Opus proposes tickers from its own market knowledge; the
membership set validates them; Composer `/backtest` is the final tradeability
arbiter. The universe DATA SOURCE itself is being live-validated in parallel, so
confirming that source (host, creds, count, pagination) is the **gated first
milestone (AC-1)** - the build must not assume it.

### The components (operator-decided design - formalize, do not redesign)

| # | Component | New / Changed | Role |
|---|-----------|---------------|------|
| 1 | **Tradeable Universe Provider** | new module | Confirm the validated data source, then fetch the FULL tradeable US-equity set as a membership/validation set. Weekly-cached. NO ranking/palette. |
| 2 | **Opus Build-Plan Generator** | new module | Anthropic SDK structured/tool-use generation of N_plans (=12/objective) diverse, objective-shaped build-plans (a strategy DSL - NOT raw Composer JSON); proposes tickers from its own market knowledge. |
| 2b | **Atlas Suggestion (objective-matched)** | extends existing | Pull Atlas community strategies and admit the ones MATCHING the objective (via stats the loader returns) as candidates alongside built-new; provenance-tagged. Weekly-cached (bill-protection). |
| 3 | **Plan->Tree Compiler** | new module | Deterministically compile each build-plan into a valid Composer tree via `symphony_schema` constructors; validate; repair loop. Includes net-new `make_weight_marketcap`. |
| 4 | **Weekly Scheduler** | new script + systemd timer | Run the builder unattended per objective; persist survivors. Plus on-demand route parity. |
| 5 | **Downstream pipeline** | UNCHANGED (except 5b) | `propose_strategies` tail: backtest -> FDR gate -> screens -> persist -> SPA tab. |
| 5b | **Overfitting cull strengthened** | one deliberate gate change | Wire PBO veto into `acceptance_gate` + real SPY-OOS-alpha baseline; ALL candidates (built-new + Atlas) culled identically (autotuner-grade). |

---

## Current State (grounded in code)

### Replacement target
- `advisors/strategy_builder_engine.py::_generate_candidate_trees(objective, universe)` (lines 392-543) is the **7-template stamper** to replace. It takes `universe[:10]` (line 403; hardcoded 10-ticker cap) and emits `CandidateInfo` objects from templates T1-T7 (`equal_weight_basket`, `specified_weight_basket`, `inverse_vol_basket`, `trend_switch`, `rsi_rotation`, `momentum_top_n`, `low_vol_floor`, lines 263-384).
- `propose_strategies(...) -> ProposalRun` (lines 855-1061) is the **public entry point**. It calls `_generate_candidate_trees` at line 915, then backtests (Step 2), FDR-gates the **full batch** (Step 3, lines 964-969), screens survivors (Step 4), and persists (Steps 5/5b). **Public signature must be preserved** — callers are `app.py:3816` and (per project CLAUDE.md) `autotuner.py`.
- `Objective` enum (lines 73-78): `diversify` / `cut_drawdown` / `lift_risk_adjusted`. `ScreenConfig` dataclass (lines 81-97). `MAX_CANDIDATES_PER_RUN = 30` (line 40).
- `_has_composer_key()` (lines 181-187) gates the whole run; returns a `ProposalRun` with the no-key error when absent (lines 905-912).

### Grammar / compiler surface
- `advisors/symphony_schema.py` — 16 constructors + read-only `validate_tree` (lines 187-269, HARD errors, never raises) / `lint_tree` / `extract_tickers` / `render_rules_text`. Vocabulary frozensets: `KNOWN_STEPS` (9 values, lines 46-58 — **note: NO `wt-marketcap`**), `KNOWN_INDICATOR_FNS` (13, lines 67-85), `KNOWN_COMPARATORS` (line 90), `KNOWN_REBALANCE` (lines 95-97), `_KNOWN_CONDITION_TYPES` and `_KNOWN_OPERATORS` (lines 112-116).
- Weighting constructors today: `make_weight_equal` (`wt-cash-equal`), `make_weight_specified` (`wt-cash-specified`), `make_inverse_vol` (`wt-inverse-vol`, emits required `window-days: 30`, line 828). **There is NO market-cap weighting constructor and `wt-marketcap` is absent from `KNOWN_STEPS`** — net-new work, field contract externally unverified.
- Live-required fields already discovered: `make_root` emits an empty `description` (line 790, was HTTP 400); `make_inverse_vol` emits `window-days: 30` (line 828, was HTTP 422). The market-cap constructor likely has an analogous unverified required field.
- Compound-condition constructors exist and validate: `make_condition_operand`, `make_constant_rhs`, `make_binary_condition`, `make_binary_compound_condition`, `make_compound_condition`, `make_if_compound` (lines 950-1114); `make_if` (flat) lines 1117-1158.
- **Grammar doc location correction:** the brief states `strategy-builder-composer-grammar.md` is MISSING. It is NOT — it was renamed `feature-plans/strategy-builder-composer-grammar.completed.md` in the 2026-06-19 plan-archive cycle (commit `903db0b`). The grammar contract is intact there; the team READS that file (and may produce a refreshed living copy from it + `symphony_schema.py` if Gate-2 wants one), not author it from scratch.

### Backtest + gate (downstream, unchanged)
- `advisors/composer_backtest_client.py::run_backtest(...) -> BacktestResult` (lines 237-387). Never raises (AC-X5); failure yields a `BacktestResult` with `stats=None` and an `error` reason. Bounded backoff 1->2->4->8s (line 55), 429 respects `Retry-After` (line 331), non-retryable errors return the error envelope `HTTP {status}: {text-prefix}` (line 360). **That envelope string is what the repair loop parses to split a grammar-422 from a tradeability-400.**
- `advisors/backtest_gate_engine.py::evaluate_candidate_batch(...)` — single-batch BHY/Yekutieli FDR across the FULL candidate set (N candidates = N trials). `SURVIVOR_OVERFITTING_CAVEAT` (lines 109-115). Thin series -> WITHHOLD, never fabricate (lines 36-37). **This is the load-bearing overfit guard and becomes MORE critical with LLM-generated candidates** — an LLM can emit many plausible-looking strategies; FDR is what stops selection bias.

### Caching, persistence, scheduler patterns to mirror
- `advisors/atlas_cache.py::cached_pull(collection_name, fetch_fn, *, ttl_days, force_refresh)` — weekly-TTL (default 7 days, line 44), never-raises, `force_refresh` escape hatch. Generic (`fetch_fn` is any zero-arg callable) — usable for the universe cache as-is.
- `advisors/lens_warehouse.py` — the THIRD DB (`alphabot_warehouse.db`), append-only, `persist_lens_snapshot(...)` and `get_lens_snapshots(...)`, recursive `_strip_secrets` (lines 70-80), pytest sentinel (lines 45-59). The pattern to mirror IF the universe is persisted. **No cross-DB joins** (CLAUDE.md Architecture Constraint #3).
- `prism_scheduler.py` — the standalone-script + OS-timer pattern: named constants (no magic numbers), idempotency guard, bounded retry (`MAX_ATTEMPTS=3`, lines 32-34), D-1 error contract (only the exception class name), env-load from project root. The weekly scheduler models this but runs WEEKLY and invokes the builder in-process via the Anthropic SDK, NOT a `claude -p` subprocess council.

### Existing API auth/host patterns
- **Composer:** `get_composer_headers()` (`alpha_bot_execution.py:163-168`) gives `x-api-key-id` + bearer authorization. `COMPOSER_BASE_URL = api.composer.trade/api/v0.1`. Composer has **no universe endpoint** (live-probed: `/assets`, `/tickers`, `/universe`, `/securities` = 404; `/search/symphonies` = 403 out-of-scope) — Composer cannot source the universe; `/backtest` is only the tradeability arbiter.
- **Alpaca DATA host (existing):** `synthetic_history.py:21` `ALPACA_BASE_URL = data.alpaca.markets/v2`; `get_alpaca_headers()` (line 299) gives `APCA-API-KEY-ID` + `APCA-API-SECRET-KEY`; `fetch_bars` batched call (batch_size=30, line 305; `feed=iex`, line 317).
- **Alpaca TRADING host (NET-NEW, VALIDATED 2026-06-20):** the universe source is `GET https://paper-api.alpaca.markets/v2/assets?status=active&asset_class=us_equity`, using the SAME `ALPACA_KEY` / `ALPACA_SECRET` via `APCA-API-KEY-ID` / `APCA-API-SECRET-KEY` headers. **Our keys are PAPER keys** — the LIVE host `api.alpaca.markets` returns 401, so the build MUST target the PAPER host. This is the FIRST call in the project to an Alpaca *trading* host; a new named constant DISTINCT from the data-host `ALPACA_BASE_URL` is required (e.g. `ALPACA_TRADING_BASE_URL = "https://paper-api.alpaca.markets"`) — never conflate the two hosts. Confirmed response: 13,889 active us_equity records / 13,060 `tradable==true`, returned as a **SINGLE flat JSON array, NO pagination** (about 1 request, under 2s; rate limit 200/window). ETFs are folded into `us_equity` (no separate `us_etf` class on this tier; SPY/QQQ confirmed present and tradable). After filtering `tradable==true` AND `exchange` in NASDAQ/NYSE/ARCA/BATS/AMEX (drop OTC) the membership set is about **12,748 symbols**.
- **Anthropic SDK:** `ai_advisor.py:_build_client()` (lines 1590-1611) builds `anthropic.Anthropic(api_key=...)`, lazy import, raises `RuntimeError` when key absent (caller degrades). Tool-use precedent: `advisors/lens_pipeline.py:284` `client.messages.create(...)`. The build-plan generator uses the SDK (NOT the `claude -p` subprocess that `prism_scheduler.py` uses).

### On-demand route
- `app.py:3759 ai_advisor_strategy_builder_run()` — POST `/ai-advisor/strategy-builder/run`, CSRF-protected by the global before_request hook, NOT in `_SETTINGS_WRITE_ALLOWLIST`, no `LIVE_EXECUTION`. Accepts JSON `{objective, universe, symphony_id?}`, lazy-imports `propose_strategies`, returns survivor/rejected/FDR JSON. **Today it accepts an operator-supplied `universe` list** (lines 3790-3795). Under the new design the universe becomes data-driven; this route rewires to the real builder while keeping its JSON response contract and advisory-only guarantees.

---

## Acceptance Criteria

> Numbered AC-N. Each is user/integrator-observable and testable. All ship in ONE cycle.

### Component 1 — Tradeable Universe Provider (membership/validation ONLY)

**AC-1 — Full tradeable-set fetch from the VALIDATED Alpaca paper source.** A new universe-provider module fetches the full active US-equity tradeable set from the confirmed source `GET https://paper-api.alpaca.markets/v2/assets?status=active&asset_class=us_equity`, authenticated with the existing `ALPACA_KEY` / `ALPACA_SECRET` via `APCA-API-KEY-ID` / `APCA-API-SECRET-KEY` headers, through a NEW named trading-host constant (e.g. `ALPACA_TRADING_BASE_URL = "https://paper-api.alpaca.markets"`) that is DISTINCT from the data-host `ALPACA_BASE_URL`. The response is a SINGLE flat JSON array (NO pagination). A test against a captured fixture asserts the request targets the paper trading host with the confirmed headers/params and that the full array is consumed in one request. (Validated 2026-06-20: ~13,889 active us_equity records, ~13,060 `tradable==true`, <2s.)

**AC-2 — Filter to the membership set.** From the fetched assets the provider keeps only those with `tradable==true` AND `exchange` in {NASDAQ, NYSE, ARCA, BATS, AMEX} (drops OTC), and **KEEPS leveraged/inverse ETFs and all ETFs** (they are folded into `us_equity`; first-class on Composer — no class-based exclusion). The result is a flat **membership/validation SET** (~12,748 symbols on the validated snapshot). A mixed fixture (tradable/untradable, OTC/listed, ETF) proves exactly the right symbols survive. **There is NO ranking, NO dollar-volume, NO top-N, NO palette** — the provider produces one unordered set.

**AC-3 — Weekly cache via the atlas_cache pattern.** Universe fetches route through `advisors/atlas_cache.cached_pull` (or an equivalent <=7-day-TTL cache) with a `force_refresh` escape hatch. A second call within the TTL returns the cached set WITHOUT a new HTTP call; `force_refresh=True` forces a refetch. A test asserts the live fetch fires at most once per TTL window.

**AC-4 — Membership lookup is the only query surface.** The provider exposes a membership check (is ticker X in the cached tradeable set?) and the full set. A test confirms an in-set ticker returns true and an off-set ticker returns false, served from cache.

**AC-5 — Honest-availability / never-raises (D-1).** Any source failure (HTTP error including a 401 from a wrong host, timeout, empty body, malformed JSON) degrades to a documented `available=False` result with a `reason` string — the provider NEVER raises and NEVER leaks an API key or path in any returned/logged string (only the exception class name). A test injects each failure mode and asserts graceful degradation.

**AC-6 — Weekly universe snapshot persisted to the warehouse, no cross-DB join.** Each weekly universe snapshot IS persisted to the third-DB warehouse pattern (`advisors/lens_warehouse.py` style — separate append-only DB; `_strip_secrets` applied) and is NEVER cross-joined with the state or optimization DBs (U-4 resolved: persist). A test asserts a snapshot row is written on a fresh weekly fetch and that no state-DB / optimization-DB import path is taken by the provider.

### Component 2 — Opus Build-Plan Generator

**AC-7 — SDK-based structured build-plan generation.** A new generator module uses the Anthropic SDK (`ANTHROPIC_API_KEY`, structured/tool-use JSON mode — NOT the `claude -p` subprocess) to emit build-plans. Given an objective (and the membership set for validation), it returns **N diverse build-plans** expressed in a constrained strategy DSL (sleeves/groups, per-sleeve tickers + weighting scheme, regime gates, filters, nesting) — **NOT raw Composer JSON**. Opus proposes tickers from its own market knowledge (no curated palette is supplied). A test with a mocked SDK asserts the generator parses the tool-use response into N well-formed build-plan objects.

**AC-8 — Objective hard-shapes structure (FOUR objectives).** The `Objective` enum is EXTENDED to four values — `diversify` / `cut_drawdown` / `lift_risk_adjusted` / **`volatility_mitigation`** — and each materially constrains plan structure, not just labels: `diversify` -> multi-sleeve low-correlation baskets (MAY leverage `advisors/correlation_diagnostic.py`); `cut_drawdown` -> defensive regime gates + inverse-vol + vol-floor filters; `lift_risk_adjusted` -> momentum / quality tilts; **`volatility_mitigation` -> low-volatility construction (inverse-vol weighting, low-vol / min-vol filters, vol-targeting sleeves)**. A test per objective (all four) asserts the generated plans exhibit the objective structural signature (e.g. `cut_drawdown` plans contain a regime gate or inverse-vol sleeve; `diversify` plans contain >=2 sleeves; `volatility_mitigation` plans contain an inverse-vol weighting or a low/min-vol filter). The four-value `Objective` enum is the confirmed input contract (U-2 resolved).

**AC-9 — Every proposed ticker is membership-validated.** Before a plan is compiled, every ticker it references is checked against the membership set (AC-2/AC-4). Tickers not in the set are pruned or the plan is rejected (see repair loop AC-15). A test feeds a plan containing an off-universe ticker and asserts it never reaches the compiler/backtest. Composer `/backtest` remains the FINAL tradeability arbiter even for in-set tickers.

**AC-10 — Diversity guarantee + plan count.** A single run produces structurally diverse plans (not N copies of one shape), where `N_plans` defaults to **12 per objective** as a named, tunable constant (U-1 resolved). A test asserts the N plans are not all structurally identical (distinct sleeve counts / weighting schemes / gates across the batch) and that the count honors the `N_plans` constant.

**AC-11 — Generator never raises / honest degradation (D-1).** A missing/invalid `ANTHROPIC_API_KEY`, an SDK error, or an unparseable response degrades to an empty plan list with a `reason` — never raises, never leaks the key/path. A test injects each failure and asserts graceful empty output.

### Component 2b — Atlas community-strategy suggestion (objective-matched)

> Dual-mode (operator clarification 2026-06-20): the builder both BUILDS net-new symphonies (Opus, Component 2) AND SUGGESTS existing Atlas community strategies that MATCH the objective. Both candidate sources flow through the SAME backtest + FDR gate (Component 5); survivors are surfaced TOGETHER, tagged by provenance. This leverages the EXISTING `advisors/community_strats.py::load_community_strategies` + `strategy_builder_engine.community_candidate_infos` (which already admit community candidates today, `template_id="community"`). The NET-NEW requirement is OBJECTIVE-MATCHED admission (not the current unfiltered top-20) + provenance tagging.

**AC-12 — Objective-matched Atlas admission.** Community strategies pulled via `load_community_strategies(force_refresh=False)` are filtered/ranked by **objective-relevance** using the stats the loader already returns (each candidate carries `oos_metrics`; the loader also exposes a `min_oos_sharpe` floor) — NOT admitted as an unfiltered top-20. The matching rule per objective: `cut_drawdown` -> lowest drawdown; `volatility_mitigation` -> lowest volatility; `lift_risk_adjusted` -> best risk-adjusted (e.g. OOS sharpe); `diversify` -> low cross-correlation vs the rest of the admitted set. The admitted count is bounded by a named constant (<= `MAX_COMMUNITY_CANDIDATES_PER_RUN`). A test per objective asserts the admitted community candidates are the objective-best by the named stat (e.g. `cut_drawdown` admits the lowest-drawdown docs first), and that a doc lacking the needed stat is handled deterministically (kept-last or excluded — documented, never crashes). Weekly Atlas-cache / bill-protection discipline is preserved (`force_refresh=False`; no extra ad-hoc pulls).

**AC-13 — Provenance tagging in results.** Every surfaced candidate (survivor AND rejected) is tagged with its provenance — **built-new** (Opus-generated) vs **atlas-suggested** (community) — in the persisted `advisor_observations` raw_response and in the route/SPA JSON, so the operator can tell at a glance which survivors are net-new builds vs matched community strategies. Built-new and atlas-suggested candidates are gated in the SAME single-batch FDR call (no separate gate, no pre-shrink — AC-21). A test asserts both provenance values appear correctly tagged end-to-end and that the FDR batch count includes both sources.

### Component 3 — Plan->Tree Compiler (deterministic)

**AC-14 — Full-grammar deterministic compilation.** The compiler translates each build-plan into a Composer `raw_value` tree using ONLY the existing `symphony_schema` constructors, and is able to emit the FULL grammar: nested `make_group`, all weighting schemes (equal / specified / inverse-vol / **market-cap**), filters (`make_filter`), and simple + compound conditions (`make_if`, `make_if_compound`, `make_compound_condition`, `make_binary_compound_condition`). Compilation is deterministic (same plan -> byte-identical tree modulo fresh uuids). Golden-fixture tests cover one tree per grammar construct.

**AC-15 — Every compiled tree passes `validate_tree` (+ grammar repair).** `symphony_schema.validate_tree` gates every compiled tree; a tree with HARD errors NEVER reaches the backtest. When a plan fails to compile or its tree fails `validate_tree`, the compiler attempts a deterministic fix (e.g. drop an invalid node) or a bounded re-prompt, bounded by a named max-attempts constant (never unbounded). A property test asserts: for any generator output the compiler admits, the resulting tree satisfies `validate_tree(tree) == []`. A test forces a repairable grammar failure and asserts repair within the bound; another forces an unrepairable one and asserts clean give-up (that plan is dropped, the run continues).

**AC-16 — Repair loop: tradeability rejections, with error-envelope split.** When `/backtest` rejects a tree because a ticker is not priceable (tradeability), the compiler prunes the offending ticker and retries; when `/backtest` rejects for a GRAMMAR reason, that is handled as AC-15, not ticker-pruning. The split is made by parsing the `composer_backtest_client` error envelope (`HTTP {status}: {text-prefix}`, client line 360) — defensively distinguishing a tradeability-400 from a grammar-422. A test injects a 400-tradeability envelope and asserts the named ticker is pruned and retried; a 422-grammar envelope asserts NO blind ticker-pruning.

**AC-17 — `wt-marketcap` is IN SCOPE; field contract captured by the team (no operator dependency).** A new `make_weight_marketcap` constructor and a `wt-marketcap` entry in `KNOWN_STEPS` are added. Because the required field contract is externally unverified, this AC REQUIRES capturing a REAL market-cap-weighted symphony `/score` to confirm the exact field shape (analogous to the `description` / `window-days` discoveries) BEFORE the constructor is finalized. **Sourcing that real symphony is an INTERNAL engineering step, NOT an operator decision:** the team finds a market-cap-weighted symphony from the Atlas community set (the `load_community_strategies` loader already pulls these — inspect their trees for a `wt-marketcap` step) or a known public market-cap symphony, then captures its `/score` (via the Composer `/score` endpoint / `composer_backtest_client`). A golden fixture derived from that real capture proves `make_weight_marketcap` emits a tree that both `validate_tree`-passes AND `/backtest`-accepts. Only if `wt-marketcap` is genuinely unobtainable after a real search does the team surface it to the PM; it is NEVER silently dropped and NEVER invented.

> **REFRAMED (2026-06-20) — producer-deprecated:** Composer retired market-cap weighting. Live probe `POST /api/v0.1/backtest` with a `wt-marketcap` node returns HTTP 422 `node-type-not-supported` / "Market cap weighting is no longer supported" (deterministic; re-probed 2x; passing `wt-cash-equal` control isolates the cause). Evidence: `tests/fixtures/strategy_builder/wt_marketcap_deprecated_envelope.json`. The realized contract (PM Option A, adopt-the-provider-contract): NO `make_weight_marketcap` constructor, NO `wt-marketcap` in `KNOWN_STEPS`; `advisors/symphony_schema.py` constructor count stays at 16. The compiler (`advisors/plan_tree_compiler.py`) drops any plan with `scheme=="market_cap"` via `_has_market_cap` before compilation, returning `CompileResult(reason="market_cap_scheme_deprecated")`. See `DE-SB-MARKETCAP-DEPRECATED` in `DECISIONS.md`.

### Component 4 — Weekly Scheduler + on-demand parity

**AC-18 — Weekly unattended run.** A standalone scheduler script (modeled on `prism_scheduler.py` + a WEEKLY systemd timer) runs the builder unattended for each objective, persisting survivors as advisory observations. It carries named constants (no magic numbers), an idempotency guard (a duplicate run in the same week is a no-op), bounded retry, and a D-1 error contract. A test asserts the idempotency guard and that the script persists via the unchanged downstream persist path.

**AC-19 — On-demand parity.** The existing `POST /ai-advisor/strategy-builder/run` route (`app.py:3759`) is rewired to the real builder and produces the SAME class of result (survivors/rejected/FDR JSON) as the weekly run, sourcing the universe from the provider (Component 1) rather than an operator-supplied ticker list. A route test asserts the response JSON contract is preserved and the real builder path is exercised.

**AC-20 — `propose_strategies` public signature preserved.** Replacing `_generate_candidate_trees` does NOT change the `propose_strategies(...)` public signature; existing callers (`app.py:3816`, `autotuner.py`) work unchanged. A test imports `propose_strategies` and asserts its signature is unchanged.

### Component 5 — Downstream invariants (unchanged but guarded)

**AC-21 — FDR gate stays the overfit guard over the FULL batch (both provenance sources; strengthened per 5b).** Every successfully-backtested candidate — built-new (Opus) AND atlas-suggested (objective-matched community) together — flows through ONE `evaluate_candidate_batch` call; screens NEVER shrink the gate input. A test asserts `gated_batch.n_candidates == len(successfully_backtested)` (counting BOTH sources), that survivors carry `SURVIVOR_OVERFITTING_CAVEAT`, and that provenance tags survive the gate (AC-13).

**AC-22 — Advisory-only safety (no live surface).** No code path in any new module or the rewired route touches `LIVE_EXECUTION`, calls a Composer write/deploy endpoint, or adds an entry to `_SETTINGS_WRITE_ALLOWLIST`. All persisted observations are `is_advisory_only=1`. A test greps the new modules + route for `LIVE_EXECUTION` / deploy / go-to-cash / allowlist mutation and asserts absence.

**AC-23 — End-to-end honest degradation.** When the universe source (Alpaca paper), Anthropic (generation), or Composer (backtest) is unavailable, the run degrades to an empty/limited result with an honest reason — never a crash, never a partial LIVE_EXECUTION side effect, never a leaked secret. A test exercises each upstream-down scenario through the full builder and asserts a clean error or empty survivors.

### Component 5b — Overfitting cull strengthened (autotuner-grade)

> Operator-approved boundary change (2026-06-20): this is the ONE deliberate downstream modification. Previously the plan declared the gate UNCHANGED; the operator has reopened that boundary to bring the Advisor cull to autotuner-grade. A trace confirmed the cull TODAY is already out-of-sample (20% validation fold via `_fold_transform_single`, `backtest_gate_engine.py:420-445`) + BHY/Yekutieli FDR (`backtest_gate_engine.py:562`), and that Atlas candidates are re-backtested FRESH with advertised `oos_metrics` discarded (`strategy_builder_engine.py:244`, `metrics={}`). Two real gaps remain (both apply to ALL candidates — built-new AND Atlas). Everything else downstream (`run_backtest`, the FDR/fold mechanics, the screens, `insert_advisor_observation`) stays AS-IS.

> **[IMPLEMENTED — C5b, 2026-06-20, end-to-end HEAD f037c83 (gate-engine ddcbb24 + production-wiring 5d6e04a + edge-14 fix 4ccea92)]**

**AC-24 — PBO veto wired in (close the disabled-veto gap).** `math_engine.compute_pbo` (Bailey & Lopez de Prado; `math_engine.py:1908`, `PBO_REJECT_THRESHOLD=0.5` at `math_engine.py:79`) is computed PER candidate and passed into `acceptance_gate.evaluate_acceptance_gate` via the gate-engine call (`backtest_gate_engine.py:635-647`), which TODAY passes no `pbo` so the value defaults to `None` and the PBO veto is structurally disabled (`acceptance_gate.py:160,203-208`, comment "NO behavior change on the Advisor path"). After this AC a candidate with `pbo > PBO_REJECT_THRESHOLD` is VETOED (mirrors the autotuner wiring at `autotuner.py:2693-2734`). The team works out the candidate-partitioning that `compute_pbo` requires (it is a CSCV/combinatorial-partition statistic — `pbo=None` when fewer than 2 configs, in which case the veto correctly does not fire). A test asserts: a high-PBO candidate is vetoed; a low-PBO candidate is not; `pbo=None` (insufficient configs) passes unchanged.

> **[IMPLEMENTED — C5b, 2026-06-20, end-to-end HEAD f037c83 (gate-engine ddcbb24 + production-wiring 5d6e04a + edge-14 fix 4ccea92)]**

**AC-25 — Real SPY-OOS-alpha baseline (close the beats-zero gap).** The OOS-alpha gate baseline is TODAY always `0.0` (`strategy_builder_engine.py` propose_strategies defaults `incumbent_oos_alpha=0.0` / `default_oos_alpha=0.0`, lines 862-863, and the route passes neither), so a candidate clears on merely-positive OOS alpha. This AC replaces the zero baseline with a REAL benchmark: each candidate must beat **SPY OOS alpha computed over the SAME validation fold** (same fold window / purge / embargo the candidate is scored on), not just beat zero. The SPY series is sourced through the existing backtest/data path (no new endpoint). Applies to built-new AND Atlas. A test asserts a candidate whose OOS alpha is positive-but-below-SPY is REJECTED, and one that beats SPY-over-the-fold survives the alpha gate.

> **[IMPLEMENTED — C5b, 2026-06-20, HEAD ddcbb24]**

**AC-26 — Atlas parity in the cull (operator-mandated).** Atlas community candidates are re-backtested FRESH (advertised `oos_metrics` discarded at `strategy_builder_engine.py:244`) and culled IDENTICALLY to built-new candidates — same fold OOS, same BHY/Yekutieli FDR, same PBO veto (AC-24), same SPY-OOS baseline (AC-25). Advertised community stats are used ONLY for objective-matched admission (AC-12), NEVER for survival. A test asserts an Atlas candidate and a built-new candidate with identical fresh return series receive the identical gate verdict, and that no advertised community stat influences any survival decision.

---

## Architecture (operator-decided — formalized, not re-proposed)

- **Five new/changed surfaces:** (1) universe-provider module (membership only), (2) Opus build-plan generator module, (3) plan->tree compiler module, (4) weekly scheduler script + systemd timer, (5) the rewired `_generate_candidate_trees` call inside `propose_strategies` + the rewired on-demand route.
- **Data flow:** universe provider (membership set from Alpaca paper /v2/assets) -> Opus generator -> N build-plans (DSL, Opus-proposed tickers) -> membership validation -> compiler -> validate + repair loop -> valid trees -> **existing** `run_backtest` (tradeability arbiter) -> **existing** `evaluate_candidate_batch` (FDR) -> **existing** screens -> **existing** `database.insert_advisor_observation`.
- **The replaced unit** is `_generate_candidate_trees` (lines 392-543). The cleanest seam (Gate-2 to confirm) is swapping that function body to drive provider+generator+compiler while keeping `propose_strategies` Steps 2-5b byte-stable. The `propose_strategies` public signature is frozen (AC-20).
- **New host constant:** `ALPACA_TRADING_BASE_URL = "https://paper-api.alpaca.markets"`, distinct from `ALPACA_BASE_URL` (data host). Forward-compat: if live Alpaca keys are ever provisioned, the LIVE host `api.alpaca.markets` needs re-testing — a one-line constant switch; today it 401s.
- **Dual-mode candidates:** built-new (Opus, `N_plans=12`/objective) AND objective-matched atlas-suggested community strategies are pooled into ONE candidate batch, provenance-tagged, and gated together (Component 5/5b). The existing `community_candidate_infos` admission path is reused + made objective-matched.
- **One deliberate downstream change (Component 5b):** the cull is strengthened to autotuner-grade — PBO veto wired into `acceptance_gate` (today disabled via `pbo=None`) + a REAL SPY-OOS-alpha baseline replacing the always-0.0 baseline. ALL candidates (built-new + Atlas) are culled identically. NO OTHER downstream change: `run_backtest`, the fold/FDR mechanics, the screens, and `insert_advisor_observation` stay as-is.
- **Caching:** universe + Atlas pulls via the weekly-TTL cache (`atlas_cache.cached_pull` / `load_community_strategies(force_refresh=False)`), bill-protected.
- **Persistence:** survivors via the unchanged downstream persist; weekly universe snapshots ARE persisted to the warehouse third-DB pattern (no cross-DB join).
- **Grammar authority:** `advisors/symphony_schema.py` constructors + `feature-plans/strategy-builder-composer-grammar.completed.md`.
- **No new architecture is permitted** — workers formalize THIS design.

## Edge Cases

1. **Empty universe.** Source returns zero tradable assets (or all OTC). Provider returns `available=False`; builder degrades to empty survivors (AC-5, AC-23).
2. **Wrong-host 401.** A call to the LIVE host `api.alpaca.markets` (paper keys) returns 401 — provider degrades honestly. Build MUST target the paper host (AC-1).
3. **Partial/short response.** The source returns a truncated array — provider degrades honestly (does NOT silently treat a short set as complete).
4. **Stale universe served from cache.** Cache TTL not yet expired but operator expects fresh — `force_refresh` must work; stale-but-served is the documented default (AC-3).
5. **Opus emits an invalid / un-compilable plan.** Off-universe tickers, unknown weighting scheme, or impossible nesting -> membership validation (AC-9) + repair loop (AC-15) handle it; unrepairable plans are dropped, the run continues.
6. **In-set ticker that Composer cannot price.** Membership says yes, `/backtest` says not-tradable -> tradeability prune+retry (AC-16). Membership is necessary, not sufficient.
7. **Repair loop exhausts.** A plan cannot be fixed within the bound -> dropped cleanly; no infinite loop, no crash (AC-15).
8. **All candidates rejected by FDR (by design).** The CRRA-EU/Harvey-Liu gate is intentionally strict; 0 survivors is a VALID outcome, not an error (project CLAUDE.md "AI Advisor empty suggestions" gotcha). The run reports an honest empty result with gate metadata (AC-21).
9. **Composer rate-limit / timeout.** `run_backtest` already bounds backoff and 429-`Retry-After`; the builder must space candidate backtests (1 req/s) and tolerate a single candidate failure without aborting the batch.
10. **Recent-IPO / thin-history tickers.** A proposed ticker with too-short history yields a thin series -> gate WITHHOLDs (never fabricates). Distinguish "not priceable" (prune, AC-16) from "thin but valid" (gate WITHHOLD).
11. **`wt-marketcap` field-capture fails.** The team first searches the Atlas community set / a known public market-cap symphony for a real `wt-marketcap` tree to capture (internal step). Only if genuinely unobtainable after that real search does the team SURFACE the blocker — never invent the field contract, never silently drop `wt-marketcap` (AC-17, U-3 resolved).
12. **No objective-matching stat on a community doc.** A pulled Atlas strategy lacks the stat an objective ranks on (e.g. no drawdown for `cut_drawdown`). Handled deterministically (kept-last or excluded — documented), never a crash (AC-12).
13. **PBO uncomputable (fewer than 2 configs).** `compute_pbo` needs >=2 partition configs; with too few it returns `None` and the PBO veto correctly does NOT fire (no false reject) — same semantics as the autotuner (AC-24).
14. **SPY OOS-alpha series unavailable for the fold.** If the SPY benchmark series cannot be obtained for the validation fold, the alpha gate degrades conservatively (treat the baseline as unmet -> WITHHOLD, never silently fall back to the old beats-zero behavior) and surfaces an honest reason (AC-25).

## Security Considerations

- **API-key handling (Anthropic + Alpaca paper).** Keys are read from env (`ANTHROPIC_API_KEY`, `ALPACA_KEY` / `ALPACA_SECRET`) exactly as the existing modules do. No key is ever written to a returned object, a persisted row, a log line, or an error string.
- **D-1 error contract everywhere.** Every new module and the rewired route surface only the exception class name on failure — never raw exception text (which may carry keys or paths), mirroring `composer_backtest_client`, `atlas_cache`, `prism_scheduler`, and the existing route handler (`app.py:3829`).
- **Warehouse secret-stripping.** If universe snapshots are persisted, the `_strip_secrets` recursion (`lens_warehouse.py:70`) applies — no credential reaches disk.
- **Prompt-injection defense-in-depth.** Opus output is the only externally-influenced surface. It is defended by FOUR independent gates: (a) structured/tool-use output (not free text), (b) every ticker membership-checked against the Alpaca-derived set (AC-9), (c) every tree `validate_tree`-gated (AC-15), (d) every tree backtested before persistence. A malicious/garbage plan cannot reach a live surface because there IS no live surface (AC-22).
- **No new write path.** Nothing added to `_SETTINGS_WRITE_ALLOWLIST`; no `LIVE_EXECUTION` interaction; no Composer deploy/go-to-cash call (AC-22).
- **Paper-host isolation.** The universe call hits the Alpaca PAPER trading host for a read-only `/v2/assets` GET — never an order/position endpoint; the new host constant must NEVER be reused for a trade-action call.
- **Rate-budget protection.** The universe source (200/window) and Composer (1 req/s) are protected by the weekly cache + the client existing pacing.

## Testing Strategy

- **Fixture-first for all external APIs.** The Alpaca paper `/v2/assets` response (captured fixture) + Composer `/backtest` responses are captured/derived fixtures; the Anthropic SDK is **mocked** (patch the build-client factory, mirroring the `ai_advisor._build_client` seam at `ai_advisor.py:1590`). NO live network in the unit suite.
- **Universe provider:** single-array consumption (no pagination); filter logic (tradable / OTC / ETF / leveraged-ETF) on a mixed fixture; membership lookup (in-set true / off-set false); cache hit/miss/force_refresh; every D-1 failure mode including a wrong-host 401. **No ranking test exists — there is no ranking.**
- **Generator:** mocked-SDK tool-use parse into N plans; per-objective structural signature; membership validation prunes off-universe tickers; diversity assertion; D-1 degradation.
- **Compiler (golden-fixture per grammar construct):** one golden tree each for nested group, equal/specified/inverse-vol/**market-cap** weighting, filter, simple condition, compound condition, `make_if_compound`. **Property test:** any admitted generator output compiles to a `validate_tree`-clean tree (AC-15).
- **Repair loop:** repairable grammar failure repairs within bound; unrepairable drops cleanly; **error-envelope split** — inject a `HTTP 400` not-tradable envelope (prune+retry) vs a `HTTP 422` grammar envelope (no blind prune).
- **`wt-marketcap`:** golden fixture derived from a REAL `/score` capture proving `validate_tree`-clean AND `/backtest`-accepted.
- **Pipeline invariants:** FDR gate gets the full batch; signature-preservation of `propose_strategies`; advisory-only grep guard; on-demand route response contract; weekly idempotency guard.
- **Strengthened cull (5b):** PBO veto fires for high-PBO / passes for low-PBO / passes for `pbo=None`; a positive-but-below-SPY-OOS candidate is REJECTED while a beats-SPY-over-the-fold candidate survives the alpha gate; an Atlas candidate and a built-new candidate with identical fresh return series get the identical verdict; no advertised community stat influences survival. SPY-fold alpha is derived from fixtures (no hardcoded literal).
- **No hardcoded producer values** (project rule): assertions on backtest/quantstats outputs derive from fixtures or assert shape/presence, never literal rates.
- **Live functional gate (PM-owned, post-merge):** the PM runs a real end-to-end build (real Alpaca paper universe, real Opus generation, real Composer backtests) on the live daemon and confirms survivors render in the SPA Strategy Builder tab — tests-green is necessary, NOT sufficient (project Merge & PR Workflow).

## Scope Boundaries

**IN scope (operator: no staging, all ships together):**
- Full Composer grammar in the compiler, **including `wt-marketcap`** (with real `/score` field-capture verification).
- Fully **dynamic, data-driven universe** — the FULL tradeable set as a membership/validation set from the validated Alpaca paper `/v2/assets` source. **NO curated ticker list, NO liquidity ranking, NO dollar-volume, NO top-N palette.**
- **Weekly automation** (scheduler + systemd timer) AND on-demand route parity.
- New universe provider, Opus generator, and plan->tree compiler modules; the new `ALPACA_TRADING_BASE_URL` constant.
- The validate + repair loop with grammar-vs-tradeability error split.
- **One deliberate downstream gate strengthening (Component 5b, operator-approved 2026-06-20):** wire the PBO veto into `acceptance_gate` (today disabled via `pbo=None`) + replace the always-0.0 OOS-alpha baseline with a REAL SPY-OOS-over-the-fold baseline. ALL candidates (built-new + Atlas) culled identically. This MOVES "FDR/gate strengthening" from out-of-scope to a controlled, bounded change.

**OUT of scope:**
- **Liquidity / dollar-volume ranking and any liquid-palette curation** — explicitly dropped per the 2026-06-20 operator correction. Component 1 is membership-only.
- **Live Alpaca trading host** — paper keys only; `api.alpaca.markets` 401s today. Live-host re-test is a future one-line constant switch, not built now.
- **Auto-deployment of any proposed symphony.** The builder PROPOSES only; deploying a survivor to live trading is a separate, operator-initiated action NOT built here. `LIVE_EXECUTION` is entirely out of scope.
- **Any change to the downstream pipeline tail BEYOND the Component-5b gate strengthening** — `run_backtest`, the fold/FDR mechanics, the screens, and `insert_advisor_observation` stay AS-IS. The ONLY deliberate gate change is 5b (PBO veto + SPY-OOS baseline, now IN scope above); no screen-threshold redesign, no fold-mechanics change, no FDR-formula change.
- **`propose_strategies` public-signature changes** (frozen — AC-20).
- **New settings-write path / allowlist entry / CSRF-exempt route.**
- **A new Composer endpoint or contract** — adopt the existing `/score` and `/backtest` contracts; never invent fields (except the `wt-marketcap` field, which is captured from a real `/score`, not invented).
- **Reconstructing the grammar doc from scratch** — it exists at `...-composer-grammar.completed.md`; at most produce a refreshed living copy if Gate-2 chooses.

## Unknowns — ALL RESOLVED (operator answers, 2026-06-20; kept for the record)

- **U-1 — Build-plan count N_plans. RESOLVED:** `N_plans` defaults to **12 per objective**, as a named tunable constant (AC-10).
- **U-2 — `Objective` enum. RESOLVED:** a FOURTH objective `volatility_mitigation` is added (now four: diversify / cut_drawdown / lift_risk_adjusted / volatility_mitigation), each with a distinct structural signature (AC-8).
- **U-3 — Real market-cap-weighted symphony for `wt-marketcap`. RESOLVED:** sourcing it is an INTERNAL engineering step, NOT an operator dependency — the team finds a market-cap-weighted symphony in the Atlas community set (the loader already pulls these) or a known public market-cap symphony and captures its `/score` (AC-17). Surfaced to the PM only if genuinely unobtainable after a real search.
- **U-4 — Universe persistence. RESOLVED:** weekly universe snapshots ARE persisted to the warehouse third-DB pattern (no cross-join) (AC-6).

## Assumptions (defensible — review at Gate-1)

- Component 1 is membership/validation ONLY (no ranking) per the 2026-06-20 operator correction; Opus self-sources tickers and Composer `/backtest` is the final tradeability arbiter.
- Universe source is the VALIDATED Alpaca paper `/v2/assets` endpoint (host/creds/count/pagination confirmed 2026-06-20); the build uses the paper host and a new distinct host constant.
- The grammar doc renamed to `...-composer-grammar.completed.md` is the authoritative grammar (not missing) — the team reads it rather than re-deriving.
- The cleanest replacement seam is swapping the `_generate_candidate_trees` body while freezing the `propose_strategies` signature (AC-20) — Gate-2 confirms exact module boundaries.
- Weekly cadence uses the `prism_scheduler.py` script+timer pattern adapted to weekly; the build runs in-process via the Anthropic SDK (not a `claude -p` subprocess).
- `atlas_cache.cached_pull` is reused for the universe cache (generic `fetch_fn`).
- Default `MAX_CANDIDATES_PER_RUN = 30`, `N_plans = 12` per objective, and the FDR gate config carry over / are added as named constants.
- Dual-mode: built-new (Opus) AND objective-matched atlas-suggested community strategies are surfaced together, provenance-tagged, through the SAME single-batch FDR gate; the existing `community_candidate_infos` admission path is reused and made objective-matched.
- This is a new codepath -> Toxic Pair TDD team is mandatory (project hard rule).
