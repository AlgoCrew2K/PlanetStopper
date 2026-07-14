# advisors/strategy_builder_engine

> Phase-2 Strategy Builder proposal engine: drives the real C1→C2→C3 builder pipeline to generate candidates, backtests them, gates via Harvey-Liu FDR + C5b PBO veto + SPY-OOS baseline, persists survivors as advisory observations, and (R2-1) carries a run-level provenance object — generation model, mode, injected-evidence manifest, run-id — on every `ProposalRun`; (AC-10) also queues survivors for the Frontrunner Builder's shared approval-to-Composer-create path.

**Source:** `advisors/strategy_builder_engine.py`
**Last updated:** 2026-07-14 (branch-integration merge — Frontrunner Builder AC-10 retrofit `f1592a2` integrated with R2-1 provenance; 2026-07-13 (R2-1 -- `ProposalRun.run_id`/`.provenance` + `propose_strategies(reasoning_context=, reasoning_manifest=, run_id=)`, `DE-ADVISOR-R2-1-001`; Internal Dependencies corrected per r2-review's Finding-2 (transitive `alpha_bot_execution` import via the new `import ai_advisor` edge) -- see below; prior: advisor-outage-degrade: honest backtest_unavailable rollup, DE-SB-DEGRADE-001; also reconciled a pre-existing gap -- ProposalRun.error_category, added in R1 AC-11, was never documented here until now) ALSO: 2026-07-11 (AC-10 Frontrunner Builder retrofit, `f1592a2`; prior: 2026-06-20)

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
    tradeability_unverified: bool = False  # advisor-outage-degrade, see below
```

`tradeability_unverified` is `True` only when `plan_tree_compiler.compile_plan` degraded this candidate's tree on an infra/transport failure (Composer unreachable) instead of pruning or dropping it — the tree's tradeability against Composer was never confirmed (advisor-outage-degrade, `DE-SB-DEGRADE-001`). Community candidates (Atlas-sourced, not compiled via `plan_tree_compiler`) always default `False`.

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
    error_category: str | None = None       # R1 AC-11: sanitized type(exc).__name__ -- safe to surface, `error` is not
    backtest_unavailable: bool = False       # advisor-outage-degrade AC-4: True iff >=1 candidate was tradeability-unverified
    backtest_unavailable_count: int = 0      # advisor-outage-degrade AC-4: count, computed pre-Step-2-filter (see below)
    run_id: str = ""                         # R2-1 AC-6: UUID minted once per run, stable across every return path
    provenance: dict | None = None           # R2-1 AC-4/AC-6: {generation_model, mode, evidence_injected, run_id}
```

`error_category` (R1, AC-11 -- a pre-existing field this file never documented until this pass) lets the route surface a safe, sanitized failure cause (`type(exc).__name__`) without ever echoing `error`'s raw exception text, which may carry hostnames, paths, or credentials (the same AC-23 precedent as the route's own error boundary).

`backtest_unavailable` / `backtest_unavailable_count` (advisor-outage-degrade, `DE-SB-DEGRADE-001`) roll up the honest outage signal from `CandidateInfo.tradeability_unverified`. **Computed from the FULL pre-Step-2-backtest `candidate_infos` list, NOT from `candidates` above** — `candidates` is filtered to only those whose OWN Step-2 metrics `run_backtest` call also succeeded, which a sustained outage would fail too, silently zeroing a `candidates`-derived count in exactly the case this flag exists to surface. Verified by hand for the sustained-outage case: `run.candidates` goes empty while `backtest_unavailable_count` still reports the true count.

`run_id` / `provenance` (R2-1, `DE-ADVISOR-R2-1-001`) — see the dedicated "R2-1 — Reasoning-Context Threading + Provenance Contract" section below for the full shape, minting rules, and honesty guarantees.

## API Reference

### `propose_strategies(objective, universe, screen_config, live_returns, symphony_id, *, incumbent_oos_alpha, default_oas_alpha, community_candidates, reasoning_context, reasoning_manifest, run_id) -> ProposalRun`

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
| `reasoning_context` | `str \| None` | R2-1: an optional, ready-to-inject operator-context text block (see `ai_advisor.build_reasoning_context`), threaded into `_generate_candidate_trees` → `build_plan_generator.generate_build_plans`. Additive/keyword, default `None` — every pre-R2-1 caller's exact call shape is unaffected. |
| `reasoning_manifest` | `dict \| None` | R2-1: the honest per-source manifest paired with `reasoning_context` (see `ai_advisor.build_reasoning_context`). Stamped into `ProposalRun.provenance["evidence_injected"]` verbatim; falls back to `ai_advisor._EMPTY_MANIFEST` (never fabricated as present) when omitted. |
| `run_id` | `str \| None` | R2-1: an optional caller-supplied run id, used verbatim when provided; a fresh `str(uuid.uuid4())` is minted when omitted. Threaded onto `ProposalRun.run_id` and `ProposalRun.provenance["run_id"]`. |

**Returns:** `ProposalRun` where:
- `candidates` contains only successfully-backtested `CandidateInfo` objects
- `gated_batch.n_candidates` equals the number of successfully-backtested candidates
- `screened_survivors` is a subset of `gated_batch.survivors`
- `error` is non-None on catastrophic failure
- `backtest_unavailable` / `backtest_unavailable_count` (advisor-outage-degrade AC-4) are `True`/`>0` when one or more candidates were emitted tradeability-unverified by `plan_tree_compiler.compile_plan` because Composer's backtest endpoint was unreachable — an honest signal distinct from a normal gate rejection or from `error`. Computed over the full candidate list BEFORE the Step-2 backtest-success filter, so it stays accurate even when the same outage also empties `candidates`.
- `run_id` / `provenance` (R2-1) are populated on EVERY return path, including the earliest error returns (see the R2-1 section below) — never `None`/`""` by omission.

**FDR integrity invariant:** `evaluate_candidate_batch` receives ALL successfully-backtested candidates — built-new (real C1→C2→C3) and atlas-suggested together in one batch. Wide exploration pays one batch-wide multiple-testing correction. Screens apply only to gate survivors. The gate input is never pre-filtered or split.

**Pipeline:**

```
Step 0:  mint run_id + provenance UNCONDITIONALLY, before the try block (R2-1)
Step 1:  _generate_candidate_trees(objective, universe, reasoning_context=reasoning_context)
         C4: C1 (self-source or universe override) → C2 (build_plan_generator, R2-1 threads
             reasoning_context into the generation prompt) → C3 (compile)
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

`build_plan_generator.generate_build_plans(gen_objective, membership_set, reasoning_context=reasoning_context)` is called (the `reasoning_context=` kwarg is R2-1; `None` when the run is not symphony-scoped). The `sbe.Objective` value is mapped to `build_plan_generator.Objective` via `.value` (string-keyed, 4-way). If the generator returns no plans (`result.plans` empty), `_generate_candidate_trees` returns `[]` cleanly (D-1 honest degradation, logs `result.reason`).

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

## Frontrunner Builder Retrofit (AC-10, 2026-07-11, commit f1592a2)

`_persist_survivor` additionally queues every non-rejected candidate onto the `frontrunner_proposals` table via `database.insert_frontrunner_proposal(symphony_id, proposal_source="strategy_builder_retrofit", candidate_tree=info.tree, metrics_json={cagr, sharpe, calmar, max_drawdown})`, immediately after the existing `advisor_observations` persist. This closes the pre-existing gap where a Strategy Builder survivor could only ever be an advisory observation, never a Composer upload. `proposal_source` distinguishes these rows from the Frontrunner Builder's own (`"frontrunner_builder"`); both flow through the SAME `advisors.frontrunner_builder.approve_frontrunner_proposal` on operator approval -- one shared approval-to-Composer-create path for the whole feature, not two. This module does NOT import `advisors.frontrunner_builder` -- the queue write goes through `database.insert_frontrunner_proposal` directly, so there is no cross-module coupling beyond the shared table + shared approval function (called from elsewhere, not from here). D-1: a queue-write failure is logged and swallowed -- it never breaks the `advisor_observations` persist that already succeeded above it. See `DE-FRONTRUNNER-001` in `DECISIONS.md` and `docs/generated/advisors_frontrunner_builder.md`.

---

## AC-4 Outage Rollup — `backtest_unavailable` (advisor-outage-degrade, DE-SB-DEGRADE-001, commit 4230641b, 2026-07-13)

Before this fix, `plan_tree_compiler.compile_plan`'s repair loop treated ANY non-400 `backtest_fn` failure — including Composer infra outages (timeouts, connection/DNS errors, 5xx, 429-exhausted) — the same as a genuine HTTP-422 grammar rejection, dropping the plan. A real Composer outage therefore silently zeroed this engine's output with no distinguishable reason from "the gate rejected everything."

`_generate_candidate_trees` now threads `compile_result.tradeability_unverified` forward onto each `CandidateInfo`. `propose_strategies` computes `backtest_unavailable_count = sum(1 for info in candidate_infos if info.tradeability_unverified)` over the FULL pre-Step-2 list (see the `ProposalRun` field notes above for why NOT `candidates`), and surfaces `backtest_unavailable = backtest_unavailable_count > 0` on the returned `ProposalRun`.

No change to `evaluate_candidate_batch`, the FDR gate, or any screen — a tradeability-unverified candidate still competes for survivorship on its actual backtest metrics exactly like any other candidate (Step 2's own `run_backtest` call is unaffected by `plan_tree_compiler`'s classification; the two are independent Composer calls). This field is purely an honesty signal for the operator, not a new filter.

Route/UI surfacing (AC-5) shipped the same cycle — see `DE-SB-DEGRADE-001` in `DECISIONS.md`.

---

## R2-1 — Reasoning-Context Threading + Provenance Contract (`DE-ADVISOR-R2-1-001`, 2026-07-13)

**Cross-cutting contract, not an SB-only feature.** This is the shared provenance surface `DE-ADVISOR-R2-1-001` establishes for the whole R2 program — R2-2 (Logic Changes) and R2-3 (Asset Swaps) reuse the SAME `ai_advisor.build_reasoning_context` assembler and extend the SAME `provenance` shape to their own engines/routes, rather than each port inventing its own.

### `provenance` — minted unconditionally, before the try block

```python
run_id = run_id or str(uuid.uuid4())
provenance: dict = {
    "generation_model": model_config.get_advisor_suggestion_model(),
    "mode": "build-new",
    "evidence_injected": reasoning_manifest or ai_advisor._EMPTY_MANIFEST,
    "run_id": run_id,
}
```

This dict is built at the TOP of `propose_strategies`, before `_has_composer_key()` is even checked — so every return path below, including the earliest error returns (missing Composer key, an exception in Step 1), carries the SAME `run_id`/`provenance`, never fabricated and never left `None` by omission. `run_id`/`generation_model`/`mode` are cheap, non-fabricated facts about the CALL ITSELF (not a claim that generation succeeded) — the honesty burden is carried entirely by `evidence_injected`'s own per-source values, which already reflect whatever `reasoning_manifest` the caller actually passed in (built by `ai_advisor.build_reasoning_context` before any Composer-key check ran).

**The `evidence_injected` manifest is the honesty artifact — not a footnote.** It is `reasoning_manifest` verbatim (never re-derived or summarized): the SAME per-source `present`/`absent`/`available`/`stale` dict `ai_advisor.build_reasoning_context` returned. A caller that never ran `build_reasoning_context` at all (or ran it on a from-scratch request) gets `ai_advisor._EMPTY_MANIFEST` — every key `"absent"`, never a fabricated `"available"`. This is what makes R2's "reasoning is real AND observable" thesis concrete at the engine layer: the exact same manifest an operator can inspect on the response JSON is the exact same object gating what was injected into the generation prompt — there is no second, lossy summary in between.

### Threading

`reasoning_context` flows: `propose_strategies(reasoning_context=)` → `_generate_candidate_trees(objective, universe, reasoning_context=reasoning_context)` → `build_plan_generator.generate_build_plans(..., reasoning_context=reasoning_context)` → `_build_generation_prompt(..., reasoning_context=reasoning_context)` (see [advisors/build_plan_generator](advisors_build_plan_generator.md)). `reasoning_manifest` does NOT thread through this chain — it is consumed once, at the top of `propose_strategies`, to build `provenance["evidence_injected"]`, and never passed to the generator (the generator only needs the already-rendered prompt text).

### Persistence

`run_id` and `evidence_injected` are stamped into every persisted advisory observation's `raw_response` alongside the existing survivor/rejected fields — so any proposal traces back to the exact run and the exact evidence manifest that produced it (AC-6, traceability).

### Route-boundary serialization guard (a named pattern for R2-2/R2-3 to reuse)

`app.py`'s `ai_advisor_strategy_builder_run()` route reads `run.provenance` defensively:

```python
provenance = getattr(run, "provenance", None)
if not isinstance(provenance, dict):
    provenance = None
```

**Why `getattr(..., default)` alone is NOT enough here:** several pre-existing test fixtures construct a bare `MagicMock()` as a `ProposalRun` stand-in. `MagicMock` auto-vivifies ANY attribute access into a new child `Mock` object rather than raising `AttributeError` — so `getattr(mock_run, "provenance", None)`'s `default` branch never actually fires against a mock missing that attribute; it silently returns a non-`None`, non-dict `Mock`. Passed straight to `jsonify()`, that raises `TypeError: Object of type Mock is not JSON serializable`. The `isinstance(provenance, dict)` check is the only reliable guard against this — and it fails CLOSED (`None`, never a fabricated dict) rather than raising. This is the same defensive shape as the pre-existing `getattr(run, "backtest_unavailable_count", 0)` read one paragraph above it in the route, but that field only needs `bool()`/`int()` coercion (safe against a truthy-but-wrong `Mock`); `provenance` is handed straight to `jsonify()` as a nested object, where a `Mock` is fatal, not just wrong. See [app](app.md) for the full route section and [static/ai_advisor.js](static_ai_advisor_js.md) for the render side.

### AC-9 wording reconciliation (r2-review gate finding)

The feature plan's AC-9 reads "bounded so a large real tree can't blow `build_plan_generator.MAX_OUTPUT_TOKENS`" — this engine module has no involvement in that bound at all (it lives entirely in `ai_advisor.build_reasoning_context` via `_MAX_TREE_RENDER_CHARS`, an INPUT-context bound, independent of `MAX_OUTPUT_TOKENS`). Documented at the source of truth: see [ai_advisor](ai_advisor.md)'s `build_reasoning_context` entry, "AC-9 wording reconciliation." Noted here only so a reader arriving at this engine's AC-9 references is pointed to the accurate account rather than the plan's loose phrasing.

### What R2-1 deliberately did NOT change

- No new admission concept, DSL change, or provenance TAG (`built-new`/`atlas-suggested` are unchanged) — `provenance` (the R2-1 dict) and `template_id` (the pre-existing built-new/atlas-suggested tag) are two independently-named concepts that happen to share the English word "provenance"; do not conflate them (see the route-side disambiguation note in [static/ai_advisor.js](static_ai_advisor_js.md)).
- No gate/PBO/FDR/SPY math change — R1 parity is untouched (characterization-tested).
- No change to `evaluate_candidate_batch`, `backtest_gate_engine`, or any screen.
- The from-scratch (non-symphony-scoped) path never calls `ai_advisor.build_reasoning_context` at all (the route-level decision, not this engine's) — `reasoning_context`/`reasoning_manifest` arrive as `None` on that path, so `provenance["evidence_injected"]` is `ai_advisor._EMPTY_MANIFEST` and `_generate_candidate_trees` byte-preserves the pre-R2-1 generation prompt (AC-8).

---

## Internal Dependencies

- `ai_advisor` — `_EMPTY_MANIFEST` (R2-1, module-level `import ai_advisor` at `strategy_builder_engine.py:21` — NOT a lazy/CC-2 import like the other `advisors.*` dependencies below; used only as the `provenance["evidence_injected"]` fallback default when `reasoning_manifest` is omitted). This module does NOT call `ai_advisor.build_reasoning_context` itself — that call happens at the route layer (see [app](app.md)); the engine only consumes the already-assembled `reasoning_context` string and `reasoning_manifest` dict as plain parameters.
- `model_config` — `get_advisor_suggestion_model()` (R2-1, `provenance["generation_model"]` source — read at call time, never a hardcoded literal)
- `advisors.universe_provider` — `get_tradeable_set()` (C1, CC-2 lazy import inside `_generate_candidate_trees`)
- `advisors.build_plan_generator` — `generate_build_plans`, `Objective` (C2, CC-2 lazy import); `load_atlas_candidates` is the canonical community-admission path for both route and scheduler callers
- `advisors.plan_tree_compiler` — `compile_plan` (C3, CC-2 lazy import)
- `advisors.symphony_schema` — used internally by `plan_tree_compiler`; no longer called directly from this module
- `advisors.backtest_gate_engine` — `evaluate_candidate_batch`, `BacktestCandidate`, `CandidateGateResult`, `GatedBatch`, `HARVEY_LIU_FDR_Q`, `SURVIVOR_OVERFITTING_CAVEAT`
- `advisors.composer_backtest_client` — `run_backtest` (1 req/s pacing; also used for SPY benchmark sourcing, Step 2a, AC-25)
- `analytics` — `compute_quantstats_metrics`
- `database` — `insert_advisor_observation`, `insert_frontrunner_proposal` (AC-10 retrofit)

**Direct imports at this file's own top level:** no `alpha_bot_execution`, `autotuner`, or execution-module import — verified by grepping this file directly.

**Transitive import (R2-1, ACCEPTED — r2-review Finding-2):** this module's new `import ai_advisor` (line 21) transitively imports `alpha_bot_execution` at module-load time via `ai_advisor.py`'s own `import symphony_logic` (`ai_advisor.py:30`) → `symphony_logic.py`'s `from alpha_bot_execution import COMPOSER_BASE_URL, get_composer_headers` (`symphony_logic.py:19`). Reviewed and accepted for three reasons: (1) **import-only, no cycle** — `alpha_bot_execution.py` does not import back up this chain; (2) **not a new dependency, only a new path to an old one** — `ai_advisor.py` already carried this exact transitive import before R2-1 (its `import symphony_logic` predates this cycle); R2-1 makes it reachable through a second route (`strategy_builder_engine` → `ai_advisor`), it does not introduce it; (3) **Architecture Constraint #1 ("no blocking I/O on the execution path") stays intact** — nothing in this import chain executes I/O at import time, and `strategy_builder_engine.py` itself is lazy-imported inside the route handler (CC-2, `app.py`), so none of this chain loads at daemon startup regardless. Practical consequence: the literal claim "no execution-module import" is no longer exactly true for this file's full transitive closure (only for its own top-level `import` statements) — off-execution-path and advisory-only remain true in the sense that matters (no execution-path caller, no engine-cycle I/O, no startup-path load). **Known follow-up (non-blocking, logged 2026-07-13):** strengthen the SB import-guard test to a full-source-text transitive scan, matching the precedent in `tests/ai_advisor/test_correlation_diagnostic_guards.py`, so this accepted transitive path is explicitly asserted rather than left to an implicit direct-import-only check. See `DE-ADVISOR-R2-1-001` in `DECISIONS.md`.

The sole production callers are `app.py:3813` (`ai_advisor_strategy_builder_run` route) and `advisors/strategy_builder_scheduler.py` (`run_weekly_build`). `autotuner.py` does NOT call `propose_strategies` — a prior doc claim to the contrary was stale (corrected in C4 doc pass).
