# advisors/strategy_builder_engine

> Phase-2 Strategy Builder proposal engine: drives the real C1→C2→C3 builder pipeline to generate candidates, backtests them, gates via Harvey-Liu FDR + C5b PBO veto + SPY-OOS baseline, and persists survivors as advisory observations.

**Source:** `advisors/strategy_builder_engine.py`
**Last updated:** 2026-06-20

## Overview

`advisors/strategy_builder_engine.py` proposes new candidate symphonies from scratch (versus engines that mutate live ones). The pipeline is: generate candidate trees via the real C1→C2→C3 builder (C4 body swap) and/or caller-injected community strategies → backtest via `composer_backtest_client` (1 req/s) → gate the full batch via `backtest_gate_engine.evaluate_candidate_batch` (Harvey-Liu BHY FDR, **C5b: + PBO veto + real SPY-OOS baseline**) → apply `ScreenConfig` post-gate presentation filters → persist survivors and rejected candidates as advisory observations.

Off-execution-path (never imported from `alpha_bot_execution.py`). Advisory-only (`is_advisory_only=1` on all persisted observations). Never raises — all exceptions surface as `ProposalRun.error`.

## Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `MAX_CANDIDATES_PER_RUN` | `30` | Maximum built-new candidates generated per run (cap applied inside `_generate_candidate_trees`) |
| `MAX_COMMUNITY_CANDIDATES_PER_RUN` | `20` | Hard cap on community-sourced candidates admitted per run (enforced inside `propose_strategies` regardless of adapter output size) |
| `SPARKLINE_TARGET_POINTS` | `60` | Downsampled equity-curve resolution (≈2.5 years of daily data at 5px/pt on a 280px card) |
| `SCREEN_MIN_CAGR_DEFAULT` | `0.0` | Default minimum annualized return screen |
| `SCREEN_MIN_SHARPE_DEFAULT` | `0.0` | Default minimum Sharpe ratio screen |
| `SCREEN_MIN_CALMAR_DEFAULT` | `0.0` | Default minimum Calmar ratio screen |
| `SCREEN_MAX_ABS_DRAWDOWN_DEFAULT` | `0.50` | Default maximum absolute drawdown magnitude |
| `SCREEN_MAX_BLENDED_ABS_DRAWDOWN_DEFAULT` | `0.40` | Default maximum blended (candidate 50/50 live) drawdown |
| `SCREEN_MAX_CORRELATION_DEFAULT` | `0.85` | Default maximum Pearson correlation vs live portfolio |

## Public Types

### `Objective` (enum)

Steers the C2 build-plan generator for a proposal run. Four values (Q1-A, C4), matching `build_plan_generator.Objective` by `.value` (string-keyed):

| Value | Description |
|-------|-------------|
| `diversify` | Multi-sleeve allocation (>=2 container children) |
| `cut_drawdown` | Regime gate or inverse-vol weight |
| `lift_risk_adjusted` | Momentum/quality filter |
| `volatility_mitigation` | Inverse-vol weight or low/min-vol filter (added Q1-A, C4) |

Maps to `build_plan_generator.Objective` via `_gen.Objective(objective.value)` — no numeric index, future-safe.

### `ScreenConfig` (dataclass)

Post-gate presentation filter. Defaults are the named constants above. Applied to gate survivors only — never to the gate input (shrinking the gate input corrupts the FDR correction, AC-3.2). A `None` metric value causes a candidate to fail closed (excluded from `screened_survivors`).

```python
@dataclass
class ScreenConfig:
    min_cagr: float = SCREEN_MIN_CAGR_DEFAULT
    min_sharpe: float = SCREEN_MIN_SHARPE_DEFAULT
    min_calmar: float = SCREEN_MIN_CALMAR_DEFAULT
    max_abs_drawdown: float = SCREEN_MAX_ABS_DRAWDOWN_DEFAULT
    max_blended_abs_drawdown: float = SCREEN_MAX_BLENDED_ABS_DRAWDOWN_DEFAULT
    max_correlation: float = SCREEN_MAX_CORRELATION_DEFAULT
```

### `CandidateInfo` (dataclass)

Per-candidate state: tree, provenance, backtest metrics, and error if backtest failed.

```python
@dataclass
class CandidateInfo:
    candidate_id: str
    tree: dict
    template_id: str        # provenance: "built-new" (C4), "atlas-suggested" (C5), or plan.provenance
    params: dict
    metrics: dict = field(default_factory=dict)
    backtest_error: str | None = None
    data_warnings: list = field(default_factory=list)
```

`template_id` carries provenance; it is never `"T1"`–`"T7"` for built-new candidates (the old template-stamper was removed in C4). It is never `"community"` for atlas-sourced candidates after C5 (the `community_candidate_infos` adapter was deleted; the tag is now `"atlas-suggested"`).

### `ProposalRun` (dataclass)

Result of a `propose_strategies` call. Never raises — check `error` on failure.

```python
@dataclass
class ProposalRun:
    candidates: list[CandidateInfo]       # successfully-backtested candidates only
    gated_batch: GatedBatch
    screened_survivors: list[CandidateGateResult]
    observations_written: int
    error: str | None = None
```

## API Reference

### `propose_strategies(objective, universe, screen_config, live_returns, symphony_id, *, incumbent_oos_alpha, default_oos_alpha, community_candidates) -> ProposalRun`

Propose new candidate symphonies from scratch. Never raises.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `objective` | `Objective` | Steers the C2 generator (4-value Q1-A enum) |
| `universe` | `list[str]` | Optional ticker override. Non-empty → used as the C1 membership set (Q2-A). Empty `[]` → `_generate_candidate_trees` self-sources from `universe_provider.get_tradeable_set()`. Route and scheduler always pass `[]`. |
| `screen_config` | `ScreenConfig` | Post-gate presentation filter — applied to survivors only |
| `live_returns` | `list[float]` | Chronological daily portfolio returns in percent scale; used for blended-drawdown and correlation screens; may be empty |
| `symphony_id` | `str` | Composer symphony ID to key observations to; defaults to `""` |
| `incumbent_oos_alpha` | `float` | OOS alpha of the incumbent strategy, passed to `evaluate_candidate_batch` |
| `default_oas_alpha` | `float` | Fallback OOS alpha when no incumbent alpha is available. In production, the C5b SPY-fold baseline — sourced internally by `propose_strategies` via a `run_backtest` call before the candidate loop — overrides this value inside `evaluate_candidate_batch`. Callers do not need to wire the SPY baseline; it is automatic as of C5b (commit 5d6e04a). |
| `community_candidates` | `list[CandidateInfo] \| None` | Optional pre-built `CandidateInfo` objects. As of C5, callers supply these via `build_plan_generator.load_atlas_candidates(objective)` (the canonical path for both the route and the scheduler). Appended to the built-new list and flow through the **same single-batch FDR gate** (AC-2). Capped at `MAX_COMMUNITY_CANDIDATES_PER_RUN` internally (AC-3). `None` and `[]` are identical — no community candidates are injected (AC-6). |

**Returns:** `ProposalRun` where:
- `candidates` contains only successfully-backtested `CandidateInfo` objects
- `gated_batch.n_candidates` equals the number of successfully-backtested candidates
- `screened_survivors` is a subset of `gated_batch.survivors`
- `error` is non-None on catastrophic failure

**FDR integrity invariant:** `evaluate_candidate_batch` receives ALL successfully-backtested candidates — built-new (real C1→C2→C3) and atlas-suggested together in one batch. Wide exploration pays one batch-wide multiple-testing correction. Screens apply only to gate survivors. The gate input is never pre-filtered or split.

**Pipeline:**

```
Step 1:  _generate_candidate_trees(objective, universe)
         C4: C1 (self-source or universe override) → C2 (build_plan_generator) → C3 (compile)
         → CandidateInfo list (provenance="built-new")
Step 1b: extend with community_candidates[:MAX_COMMUNITY_CANDIDATES_PER_RUN]
         (no-op when community_candidates is None or [])
Step 2a: run_backtest on 100%-SPY Benchmark tree (once per run) → _spy_returns_dict (AC-25)
         On error or empty daily_returns: _spy_returns_dict = {} → conservative WITHHOLD in gate
Step 2:  run_backtest per candidate — per-candidate try/except (backtest_error on failure)
         Populate dated_returns from result.daily_returns (date keys preserved, pct-scaled ×100)
Step 3:  evaluate_candidate_batch(ALL backtested candidates, spy_returns_fn=lambda: _spy_returns_dict)
         C5b: + batch PBO veto (dated_returns) + SPY-OOS-fold baseline (spy_returns_fn)
Step 4:  _passes_screens on gate survivors only
Step 5:  persist survivors + rejected candidates
```

---

## C4 Body Swap — `_generate_candidate_trees` (commit 5ae6c8c)

`_generate_candidate_trees` was the old 7-template stamper (T1–T7 via `symphony_schema` constructors). In C4 it was replaced with the real C1→C2→C3 pipeline:

### C1 — Universe (Q2-A)

`universe_provider.get_tradeable_set()` is called when `universe` is empty (the default for both the route and the scheduler). A non-empty `universe` argument overrides the C1 membership set — it is used as-is as a `frozenset`. This allows direct injection in tests or by future callers that want a specific subset.

### C2 — Build-plan generation

`build_plan_generator.generate_build_plans(gen_objective, membership_set)` is called. The `sbe.Objective` value is mapped to `build_plan_generator.Objective` via `.value` (string-keyed, 4-way). If the generator returns no plans (`result.plans` empty), `_generate_candidate_trees` returns `[]` cleanly (D-1 honest degradation, logs `result.reason`).

### C3 — Plan compilation

Each plan from C2 is fed to `plan_tree_compiler.compile_plan(plan)`. Plans where `compile_result.tree is None` are dropped (e.g. `market_cap_scheme_deprecated` reason); the run continues. Successfully compiled candidates become `CandidateInfo` with:
- `candidate_id = plan["plan_id"]`
- `template_id = plan.get("provenance", "built-new")` — never `"T1"`–`"T7"`
- `params = {plan_id, name, objective, provenance}`

Loop caps at `MAX_CANDIDATES_PER_RUN`. D-1: any unexpected exception degrades to `[]` (logs class name only).

### What is gone

The old `_generate_candidate_trees` contained ~140 lines of T1–T7 `symphony_schema` constructor calls with hardcoded parameter sweep loops. All of that is removed. There are no more T1–T7 template IDs on built-new candidates. `symphony_schema` constructors are still used inside `plan_tree_compiler.compile_plan` (the C3 layer), but they are driven by the plan DSL from C2, not stamped directly here.

---

## C5 — Dual-Mode Atlas Admission + Adapter Deletion (commits 1d5dd48, 147a181, 2026-06-20)

**Component 5** unifies the community-candidate admission path — sourcing strategies from **algo-db.com** (via `captplanet.strategies`) — across the route and the weekly scheduler onto `build_plan_generator.load_atlas_candidates(objective)`, and deletes the orphaned `community_candidate_infos` adapter.

### What changed

**Route rewire (1d5dd48):** `POST /ai-advisor/strategy-builder/run` previously called `load_community_strategies` + the now-deleted `community_candidate_infos` adapter (unranked, no objective-matching). It now calls `build_plan_generator.load_atlas_candidates(objective)` — the same objective-matched admission path used by the scheduler. Atlas candidates reaching `propose_strategies` are now tagged `provenance="atlas-suggested"` (not `"community"`).

**Scheduler dual-mode (147a181):** `strategy_builder_scheduler.run_weekly_build` previously called `propose_strategies(community_candidates=[])` — no atlas injection on the weekly path. It now calls `_bpg.load_atlas_candidates(objective)` per objective and forwards the result as `community_candidates=` to `propose_strategies`. The weekly run is genuinely dual-mode: built-new (Opus C2) AND objective-matched atlas-suggested in ONE FDR batch, bill-protected (`force_refresh=False` inside the wrapper), D-1 (Atlas failure → built-new-only).

**Adapter deletion (147a181):** `community_candidate_infos` (70 lines, the old unranked first-N adapter) was deleted from `strategy_builder_engine.py`. It had zero production callers after the route rewire. The `propose_strategies` `community_candidates=` kwarg path is preserved unchanged — only the unranked standalone adapter function is gone. The engine docstring reference to `community_candidate_infos` was updated to point to `build_plan_generator.load_atlas_candidates` (`strategy_builder_engine.py:758`).

### Provenance tags after C5

`template_id` in `CandidateInfo` identifies origin:

| Value | Source |
|-------|--------|
| `"built-new"` (or `plan.provenance`) | C4 real pipeline: C1→C2→C3 |
| `"atlas-suggested"` | C5 objective-matched admission via `build_plan_generator.load_atlas_candidates` (source: **algo-db.com** `captplanet.strategies` collection) |

**Note:** `"T1"`–`"T7"` no longer appear on built-new candidates (removed in C4). `"community"` no longer appears — the `community_candidate_infos` adapter that emitted it is deleted (C5, 147a181).

### Route error-boundary sanitization (AC-23)

Pre-C5, `run.error` was echoed verbatim in the route JSON response. `run.error` is set by `propose_strategies` via `str(exc)`, which can carry API keys or internal paths. C5 (1d5dd48) sanitizes this: the route logs the full `run.error` server-side and surfaces the static token `"strategy-builder-error"` to the operator (`app.py:3840`). The route's own outer `except` already used `type(exc).__name__` (AC-23 boundary closed at the observable surface). The internal `propose_strategies` normalization (replacing `str(exc)` with the class name at `propose_strategies:965`) is a tracked follow-on, not done in this cycle.

---

## C5b Gate Strengthening (2026-06-20)

**Component 5b** brings the Advisor cull to autotuner-grade by closing two pre-C5b gaps:

**Gap 1 — PBO veto was structurally disabled.** `evaluate_candidate_batch` was called without a `pbo` argument → `None` → PBO veto never fired on the Advisor path. C5b wires `math_engine.compute_pbo` over the candidate batch's `dated_returns` intersection and threads `_batch_pbo` into every gate call.

**Gap 2 — OOS-alpha baseline always beats zero.** The `default_oas_alpha=0.0` default meant a candidate cleared merely by having positive validation-fold alpha. C5b injects a `spy_returns_fn` seam using a real SPY backtest (Step 2a).

**Atlas parity is structural (AC-26).** Atlas community candidates and built-new candidates flow through the same `evaluate_candidate_batch` call. Advertised community `oas_metrics` are structurally inert in the gate (`metrics={}` at `BacktestCandidate` construction).

**`rejection_reason` field.** Each `CandidateGateResult` carries a deterministic `rejection_reason`: `None` (survivor) → `"pbo_veto"` (Stage-1) → `"below_spy_alpha"` (Stage-2) → `"fdr_not_winner"` (catch-all). See `advisors_backtest_gate_engine.md` and `DE-SB-CULL-001` in `DECISIONS.md`.

### C5b Production Wiring (commit 5d6e04a)

`propose_strategies` handles C5b inputs internally; callers do NOT need to wire SPY or `dated_returns` (AC-20: public signature unchanged):

- **SPY sourcing (AC-25):** `run_backtest` called on `make_root("SPY Benchmark", "daily", [make_weight_equal([make_asset("SPY")])])`. On success: `_spy_returns_dict = {d: r * 100.0 ...}`. On error or empty: `_spy_returns_dict = {}` → gate's `_SPY_UNAVAILABLE_DEFAULT_OOS_ALPHA=float("+inf")` sentinel fires → conservative WITHHOLD.
- **`dated_returns` population (AC-24):** `{d: r * 100.0 for d, r in result.daily_returns.items()}` — same scale as `daily_returns_pct`.
- **Edge-14 fix (4ccea92):** original `-inf` sentinel made the withhold-clause always-false; corrected to `+inf`.

---

## Internal Dependencies

- `advisors.universe_provider` — `get_tradeable_set()` (C1, CC-2 lazy import inside `_generate_candidate_trees`)
- `advisors.build_plan_generator` — `generate_build_plans`, `Objective` (C2, CC-2 lazy import); `load_atlas_candidates` is the canonical community-admission path for both route and scheduler callers
- `advisors.plan_tree_compiler` — `compile_plan` (C3, CC-2 lazy import)
- `advisors.symphony_schema` — used internally by `plan_tree_compiler`; no longer called directly from this module
- `advisors.backtest_gate_engine` — `evaluate_candidate_batch`, `BacktestCandidate`, `CandidateGateResult`, `GatedBatch`, `HARVEY_LIU_FDR_Q`, `SURVIVOR_OVERFITTING_CAVEAT`
- `advisors.composer_backtest_client` — `run_backtest` (1 req/s pacing; also used for SPY benchmark sourcing, Step 2a, AC-25)
- `analytics` — `compute_quantstats_metrics`
- `database` — `insert_advisor_observation`

No import of `alpha_bot_execution`, `autotuner`, or any execution module. Off-execution-path; advisory-only. The sole production callers are `app.py:3813` (`ai_advisor_strategy_builder_run` route) and `advisors/strategy_builder_scheduler.py` (`run_weekly_build`). `autotuner.py` does NOT call `propose_strategies` — a prior doc claim to the contrary was stale (corrected in C4 doc pass).
