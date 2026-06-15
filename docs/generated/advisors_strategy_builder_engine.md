# advisors/strategy_builder_engine

> Phase-2 Strategy Builder proposal engine: builds candidate symphonies from templates and community-sourced strategies, backtests them, gates via Harvey-Liu FDR, and persists survivors as advisory observations.

**Source:** `advisors/strategy_builder_engine.py`
**Last updated:** 2026-06-14

## Overview

`advisors/strategy_builder_engine.py` proposes new candidate symphonies from scratch (versus engines that mutate live ones). The pipeline is: generate candidate trees from the 7-template library and/or from caller-injected community strategies → backtest via `composer_backtest_client` (1 req/s) → gate the full batch via `backtest_gate_engine.evaluate_candidate_batch` (Harvey-Liu BHY FDR) → apply `ScreenConfig` post-gate presentation filters → persist survivors and rejected candidates as advisory observations.

Off-execution-path (never imported from `alpha_bot_execution.py`). Advisory-only (`is_advisory_only=1` on all persisted observations). Never raises — all exceptions surface as `ProposalRun.error`.

## Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `MAX_CANDIDATES_PER_RUN` | `30` | Maximum template-generated candidates per run |
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

Steers template selection and parameter ranges for a proposal run.

| Value | Description |
|-------|-------------|
| `diversify` | Templates favouring diversification (T1 equal-weight, T3 inverse-vol, T6 momentum) |
| `cut_drawdown` | Templates minimising drawdown (T7 low-vol, T3 inverse-vol, T4 trend-switch) |
| `lift_risk_adjusted` | Templates targeting risk-adjusted return (T6 momentum, T5 RSI, T2 specified-weight) |

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

Per-candidate state: tree, template provenance, backtest metrics, and error if backtest failed.

```python
@dataclass
class CandidateInfo:
    candidate_id: str
    tree: dict
    template_id: str        # "T1"–"T7" for templates; "community" for community-sourced
    params: dict
    metrics: dict = field(default_factory=dict)
    backtest_error: str | None = None
    data_warnings: list = field(default_factory=list)
```

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

### `community_candidate_infos(community_result, *, max_candidates) -> list[CandidateInfo]`

Map a `load_community_strategies` result dict to a capped list of `CandidateInfo` objects for injection into `propose_strategies`.

Each candidate `{sid, name, tree, tickers, oos_metrics, composition_hash}` becomes a `CandidateInfo` with:
- `candidate_id = sid`
- `template_id = "community"`
- `params = {sid, name, composition_hash}` (provenance carrier — AC-5)
- `metrics = {}` (filled after backtest)
- `backtest_error = None`

Returns `[]` when `community_result` is not a dict, `available` is `False`, or `candidates` is missing/empty. Never raises — any unexpected error returns `[]`.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `community_result` | `dict` | Return value of `load_community_strategies` |
| `max_candidates` | `int` | Hard cap on returned list length (first-N, deterministic) |

**Returns:** `list[CandidateInfo]`

**Example:**

```python
from advisors.community_strats import load_community_strategies
from advisors.strategy_builder_engine import community_candidate_infos, MAX_COMMUNITY_CANDIDATES_PER_RUN

result = load_community_strategies(min_oos_sharpe=0.5)
community = community_candidate_infos(result, max_candidates=MAX_COMMUNITY_CANDIDATES_PER_RUN)
# pass community to propose_strategies(community_candidates=community, ...)
```

---

### `propose_strategies(objective, universe, screen_config, live_returns, symphony_id, *, incumbent_oos_alpha, default_oos_alpha, community_candidates) -> ProposalRun`

Propose new candidate symphonies from scratch. Never raises.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `objective` | `Objective` | Steers template selection and parameter ranges |
| `universe` | `list[str]` | Ticker symbols (at most 10 used by template generator) |
| `screen_config` | `ScreenConfig` | Post-gate presentation filter — applied to survivors only |
| `live_returns` | `list[float]` | Chronological daily portfolio returns in percent scale; used for blended-drawdown and correlation screens; may be empty |
| `symphony_id` | `str` | Composer symphony ID to key observations to; defaults to `""` |
| `incumbent_oos_alpha` | `float` | OOS alpha of the incumbent strategy, passed to `evaluate_candidate_batch` |
| `default_oos_alpha` | `float` | Fallback OOS alpha when no incumbent alpha is available |
| `community_candidates` | `list[CandidateInfo] \| None` | Optional pre-built `CandidateInfo` objects from `community_candidate_infos`. Appended to the template-generated list and flow through the **same single-batch FDR gate** (AC-2). Capped at `MAX_COMMUNITY_CANDIDATES_PER_RUN` internally (AC-3). `None` and `[]` are identical — no community candidates are injected (AC-6). |

**Returns:** `ProposalRun` where:
- `candidates` contains only successfully-backtested `CandidateInfo` objects
- `gated_batch.n_candidates` equals the number of successfully-backtested candidates
- `screened_survivors` is a subset of `gated_batch.survivors`
- `error` is non-None on catastrophic failure

**FDR integrity invariant:** `evaluate_candidate_batch` receives ALL successfully-backtested candidates — template-generated and community-sourced together in one batch. This is the anti-overfit invariant: wide exploration pays one batch-wide multiple-testing correction. Screens apply only to gate survivors (post-gate presentation). The gate input is never pre-filtered or split.

**Pipeline (Step 1b — community injection):**

```
Step 1:  _generate_candidate_trees(objective, universe)    → template candidates
Step 1b: extend with community_candidates[:MAX_COMMUNITY_CANDIDATES_PER_RUN]
         (no-op when community_candidates is None or [])
Step 2:  run_backtest per candidate — per-candidate try/except (backtest_error on failure)
Step 3:  evaluate_candidate_batch(ALL backtested candidates)  ← full batch, FDR gate
Step 4:  _passes_screens on gate survivors only
Step 5:  persist survivors + rejected candidates
```

---

### Template Library — 7 Templates

| ID | Function | Description |
|----|----------|-------------|
| T1 | `equal_weight_basket(tickers, name)` | Equal-weight allocation |
| T2 | `specified_weight_basket(weighted_tickers, name)` | Specified-weight allocation |
| T3 | `inverse_vol_basket(tickers, name)` | Inverse-volatility weighted allocation |
| T4 | `trend_switch(signal_ticker, ma_window, risk_on_tickers, risk_off_tickers, name)` | Trend-following switch (signal > MA → risk-on) |
| T5 | `rsi_rotation(signal_ticker, rsi_window, threshold, overbought_tickers, neutral_tickers, name)` | RSI-based rotation |
| T6 | `momentum_top_n(universe, n, window, name)` | Top-N by cumulative return |
| T7 | `low_vol_floor(universe, n, window, name)` | Bottom-N by max-drawdown (least-drawdown floor) |

T6/T7 sort-by-fn values (`"cumulative-return"`, `"max-drawdown"`) are PM-accepted unverified-grammar deviations — see phase-2 contract header in the source file.

## Community-Candidate Wiring (propose-wiring cycle, 2026-06-14)

The `community_candidates` keyword argument on `propose_strategies` enables caller-injected community symphonies from `advisors/community_strats.load_community_strategies`. The design choices:

1. **Single-batch FDR gate (anti-overfit invariant).** Community and template candidates enter `evaluate_candidate_batch` together as one batch. Splitting them into a separate gate would give each group a weaker multiple-testing correction, increasing false discovery. The batch-wide correction is the price of wide exploration.

2. **Adapter at the caller boundary.** `propose_strategies` does not import or call `load_community_strategies`. The caller obtains the community result, passes it through `community_candidate_infos`, and injects the adapter output via `community_candidates`. This mirrors the existing `live_returns` injection pattern and keeps the engine decoupled from the loader.

3. **Per-candidate failure isolation (AC-4).** Each community candidate's `run_backtest` call is wrapped in a per-candidate `try/except`. A failing backtest sets `backtest_error` on the `CandidateInfo` and excludes it from the gate — other candidates are unaffected and the run completes normally.

4. **Provenance in persisted observations (AC-5).** Community-sourced survivors record `template_id="community"` and the source `sid` in the observation's `params`. Downstream surfaces (chat artifacts, dashboard cards) can identify community-origin candidates.

5. **No-regression guarantee (AC-6).** When `community_candidates` is `None` or `[]`, the `if community_candidates:` guard is false — the extend is skipped and the execution path is byte-for-byte identical to the pre-wiring code.

6. **Rebuilt via Agent Team.** The prior wiring (ripped at `ad3a637`) was built by a solo agent. This version was built via the Toxic Pair TDD composition on `team/propose-strategies-wiring`. 39/39 GREEN at `4edbe92`.

## Provenance Tags

`template_id` in `CandidateInfo.params` identifies origin:

| Value | Source |
|-------|--------|
| `"T1"` – `"T7"` | Template-generated (objective-directed) |
| `"community"` | Community-sourced via `community_candidate_infos` adapter |

## Internal Dependencies

- `advisors.symphony_schema` — all 7 template constructors; `render_rules_text`
- `advisors.backtest_gate_engine` — `evaluate_candidate_batch`, `BacktestCandidate`, `CandidateGateResult`, `GatedBatch`, `HARVEY_LIU_FDR_Q`, `SURVIVOR_OVERFITTING_CAVEAT`
- `advisors.composer_backtest_client` — `run_backtest` (1 req/s pacing)
- `analytics` — `compute_quantstats_metrics`
- `database` — `insert_advisor_observation`

No import of `alpha_bot_execution`, `autotuner`, or any execution module. Off-execution-path; advisory-only.
