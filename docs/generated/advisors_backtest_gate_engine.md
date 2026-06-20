# advisors/backtest_gate_engine

> M2 backtest-and-gate engine: fold-transforms Composer backtest return series into walk-forward folds and runs batch BHY/Yekutieli FDR + acceptance gate over the full candidate set; C5b (2026-06-20) adds batch PBO veto and real SPY-OOS-fold baseline.

**Source:** `advisors/backtest_gate_engine.py`
**Last updated:** 2026-06-20

## Overview

`advisors/backtest_gate_engine.py` is the reusable M2 spine called by the Strategy Builder's `propose_strategies` (and previously by M3/M4 proposal handlers). For each batch of advisor candidates it:

1. Receives `BacktestCandidate` objects — each carrying a Composer backtest return series plus optional date-keyed returns for C5b PBO/SPY computation.
2. **C5b Step 0a:** Computes a batch-level PBO (`math_engine.compute_pbo`) over the intersection of all candidates' date-keyed returns, then threads it into every `evaluate_acceptance_gate` call as `pbo=_batch_pbo`.
3. **C5b Step 0b:** Computes a real SPY-OOS baseline — aligns SPY's date-keyed returns to the candidate date span (intersection), fold-transforms via the same `_fold_transform_single`, and uses the resulting validation-fold OOS alpha as `default_oos_alpha`. SPY-unavailable degrades conservatively to `float("-inf")`.
4. Applies the fold-transform: slices every candidate's return series into the autotuner's walk-forward fold structure (60/20/20 TRAIN/VALIDATION/FROZEN-EVAL with PURGE_DAYS + EMBARGO_DAYS boundary purge).
5. Runs BHY/Yekutieli FDR across the full candidate set (`n_effective = len(candidates)` — the honest multiple-testing count).
6. Calls `acceptance_gate.evaluate_acceptance_gate` unchanged for each candidate, feeding fold-derived inputs and the batch PBO.
7. Returns a `GatedBatch` — each candidate annotated with its gate verdict, `rejection_reason`, and honest caveats.

Off-execution-path: MUST NOT be imported or called from `alpha_bot_execution.py` (architecture constraint #1).

## Load-Bearing Soundness Requirements

- **One-directional-brake invariant:** no discretionary score can resurrect a veto-failed candidate.
- **Honest multiple-testing count:** `n_effective = len(candidates)` — identical to the autotuner's semantics (autotuner.py:_haircut_select docstring, plan D3).
- **Purge integrity:** fold-split respects `PURGE_DAYS` on the validation-fold boundary. Too-short series → WITHHOLD (never fabricate).
- **NN1 compliance:** Composer backtest trees are not BACKTEST_SELECTION-spec facets; `nn1_compliant=True` is correct for all Composer backtest paths.
- **C5b SPY date-alignment (not positional):** SPY is aligned to the candidate date span via date intersection BEFORE fold-transform; positional-only alignment would land the fold window on different calendar dates for a longer SPY series, producing a wrong baseline.

## Constants

### Fold-transform constants (imported from autotuner — single source of truth)

| Constant | Source | Description |
|----------|--------|-------------|
| `TRAIN_RATIO` | `autotuner` | 0.60 — train fold fraction |
| `VALIDATION_RATIO` | `autotuner` | 0.20 — validation fold fraction |
| `PURGE_DAYS` | `autotuner` | Boundary purge width (train-side) |
| `EMBARGO_DAYS` | `autotuner` | Embargo width at train-validation boundary |
| `HARVEY_LIU_FDR_Q` | `autotuner` | BHY FDR significance level |
| `FOLD_TRANSFORM_MIN_VALIDATION_DAYS` | local | 5 — minimum validation days for a defensible t-stat |
| `FOLD_TRANSFORM_MIN_TOTAL_DAYS` | local | Derived minimum total series length: `ceil((PURGE_DAYS + EMBARGO_DAYS + 5) / (1 - TRAIN_RATIO))` |

### C5b constants

| Constant | Value | Description |
|----------|-------|-------------|
| `_BATCH_PBO_GAMMA` | `1.0` | CRRA risk-aversion coefficient passed to `math_engine.compute_pbo`; mirrors `autotuner.GAMMA` |
| `_PBO_MIN_CONFIGS` | `2` | Minimum number of date-keyed configs to compute a meaningful batch PBO; fewer → `pbo=None`, veto does not fire |
| `_PBO_MIN_ALIGNED_DATES` | `8` | Minimum intersection dates across all configs; fewer → `pbo=None` (CSCV needs ≥1 date per block, S=8 blocks) |
| `_SPY_UNAVAILABLE_DEFAULT_OOS_ALPHA` | `float("-inf")` | Conservative fallback when SPY is unavailable; ensures every candidate fails the alpha gate (WITHHOLD, never silent fallback to beats-zero) |
| `SPY_BENCHMARK_TICKER` | `"SPY"` | US equity broad-market benchmark ticker (SPDR S&P 500 ETF) for the AC-25 OOS-fold baseline |

### Caveat constants

| Constant | Description |
|----------|-------------|
| `SURVIVOR_OVERFITTING_CAVEAT` | Mandatory caveat appended to every `ADOPT_CANDIDATE` result |
| `THIN_WINDOW_CAVEAT` | Appended when validation window is below `FOLD_TRANSFORM_MIN_VALIDATION_DAYS` |

## Public Types

### `BacktestCandidate` (NamedTuple)

One advisor-proposed variant to be fold-transformed and gated.

| Field | Type | Description |
|-------|------|-------------|
| `candidate_id` | `str` | Opaque identifier for operator traceability |
| `daily_returns_pct` | `list[float]` | Chronologically ordered daily returns in percent; used for fold-transform and BHY t-stat |
| `candidate_params` | `dict` | Parameter vector for panel stability scoring (D2) |
| `incumbent_params` | `dict` | Live incumbent's parameter dict for stability comparison |
| `theory_prior_params` | `dict` | Theory-anchor parameter dict for prior-anchor scoring (D4) |
| `nn1_compliant` | `bool` | Default `True` for all Composer backtest paths; override for audit |
| `purge_integrity_ok` | `bool` | Default `True`; series-derived check wins over caller-supplied `True` |
| `dated_returns` | `dict[str, float]` | **C5b (AC-24/25).** Date-keyed returns (`"YYYY-MM-DD" -> pct`). Enables batch PBO computation and SPY date-alignment. Default `{}` — existing callers without date keys continue to work unchanged; PBO veto and SPY baseline degrade gracefully when empty. |

### `CandidateGateResult` (NamedTuple)

Gate result for one candidate.

| Field | Type | Description |
|-------|------|-------------|
| `candidate_id` | `str` | Echoes the `BacktestCandidate` identifier |
| `verdict` | `AcceptanceVerdict` | From `acceptance_gate.evaluate_acceptance_gate` |
| `validation_days` | `int` | Days in the post-purge validation fold |
| `oos_alpha` | `float` | Sum of validation-fold daily returns (percent) |
| `caveats` | `list[str]` | Plain-text caveats; always non-empty for `ADOPT_CANDIDATE` |
| `winner_p_adj` | `float \| None` | BHY-adjusted p-value for this candidate (audit trail) |
| `rejection_reason` | `str \| None` | **C5b.** Why the candidate was culled, or `None` for survivors. Deterministic stage-order precedence — see below. |

#### `rejection_reason` stage-order precedence (C5b, shipped at f41b299)

The precedence ensures the most-specific cause is recorded. A candidate that triggers multiple gates reports the highest-priority cause:

| Priority | Value | Condition |
|----------|-------|-----------|
| 1 (survivor) | `None` | `verdict.decision == "ADOPT_CANDIDATE"` |
| 2 (Stage-1) | `"pbo_veto"` | `_batch_pbo is not None and _batch_pbo > PBO_REJECT_THRESHOLD` |
| 3 (Stage-2) | `"below_spy_alpha"` | `spy_returns_fn is not None and fold.oos_alpha <= _effective_default_oos_alpha` |
| 4 (catch-all) | `"fdr_not_winner"` | BHY non-winner, nn1 failure, purge failure, or thin-window |

PBO veto (`pbo_veto`) dominates `below_spy_alpha` because PBO is the `acceptance_gate` Stage-1 hard veto — a high-PBO batch is too sample-dependent to consider further, regardless of alpha. A candidate that is both high-PBO and below-SPY reports `"pbo_veto"` so the operator can see which gate fired first.

The `"pbo_veto"` string deliberately contains `"pbo"` (lowercase) so live-probe scripts can identify PBO culls with a case-insensitive substring check.

### `GatedBatch` (NamedTuple)

Result of `evaluate_candidate_batch`.

| Field | Type | Description |
|-------|------|-------------|
| `results` | `list[CandidateGateResult]` | Per-candidate results in input order |
| `survivors` | `list[CandidateGateResult]` | Results where `verdict.decision == "ADOPT_CANDIDATE"` |
| `n_candidates` | `int` | Total candidates (FDR denominator audit trail) |
| `fdr_q` | `float` | `HARVEY_LIU_FDR_Q` used for the correction |

## API Reference

### `evaluate_candidate_batch(candidates, *, incumbent_oos_alpha, default_oos_alpha, spy_returns_fn) -> GatedBatch`

Fold-transform a batch of Composer backtest candidates and run them through the gate.

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `candidates` | `Sequence[BacktestCandidate]` | — | Batch to gate; empty input returns an empty `GatedBatch` |
| `incumbent_oos_alpha` | `float` | `0.0` | Live incumbent's OOS alpha; used as `fallback_oos_alpha` in the gate (KEEP_INCUMBENT when candidate does not beat it) |
| `default_oos_alpha` | `float` | `0.0` | Global-default params' OOS alpha; overridden by the SPY-fold baseline when `spy_returns_fn` is supplied and non-empty |
| `spy_returns_fn` | `Callable[..., dict] \| None` | `None` | **C5b (AC-25) injectable seam.** Returns SPY's date-keyed returns (`dict[str, float]`). When supplied and non-empty: SPY series is date-aligned to the candidate span, fold-transformed, and the resulting validation-fold OOS alpha replaces `default_oos_alpha`. When `None` or empty: `_SPY_UNAVAILABLE_DEFAULT_OOS_ALPHA` (`float("-inf")`) is used — ensures no candidate clears the alpha gate (conservative WITHHOLD, never silent fallback to beats-zero). Production callers inject a real SPY fetch; tests inject a fixed fixture series. |

**Returns:** `GatedBatch`

**Pipeline:**

```
C5b Step 0a: batch PBO (math_engine.compute_pbo over dated_returns intersection)
C5b Step 0b: SPY-fold baseline (align SPY to candidate dates → _fold_transform_single)
Step 1: _fold_transform_single per candidate (60/20/20 + purge)
Step 2: compute_sortino_tstat per candidate (seed=idx, deterministic)
Step 3: BHY/Yekutieli FDR over full batch (n_effective = N)
Step 4: BHY winner = argmin p_adj over veto-eligible candidates
Step 5: evaluate_acceptance_gate per candidate (pbo=_batch_pbo, default_oos_alpha=_effective_default_oos_alpha)
        → rejection_reason cascade (pbo_veto > below_spy_alpha > fdr_not_winner)
```

**FDR integrity invariant:** `evaluate_candidate_batch` must receive ALL successfully-backtested candidates — built-new (Opus) and Atlas-suggested together — in one call. Screens NEVER shrink the gate input. `n_effective = len(candidates)` is the honest multiple-testing count; raising N raises the correction bar.

**C5b batch PBO details (AC-24):**

- `dated_returns` dicts from all candidates are collected into a list of configs.
- If `len(configs) >= _PBO_MIN_CONFIGS` (2) AND the intersection of all date keys has `>= _PBO_MIN_ALIGNED_DATES` (8) dates, `math_engine.compute_pbo` is called with the intersection dates and `_BATCH_PBO_GAMMA=1.0`.
- Result is threaded into every `evaluate_acceptance_gate(pbo=_batch_pbo)` call.
- If either condition is not met, `_batch_pbo` stays `None` — the veto correctly does NOT fire (no false reject on thin batches).
- Mirrors `autotuner.py:2699-2711` wiring pattern.

**C5b SPY-fold baseline details (AC-25):**

- SPY dates are restricted to the union of candidate dates (intersection of SPY dates with candidate span) so the fold window lands on the same calendar dates as the candidates.
- The aligned SPY value list is fed to the same `_fold_transform_single` used for candidates.
- If SPY returns an empty series OR fails, `_effective_default_oos_alpha = _SPY_UNAVAILABLE_DEFAULT_OOS_ALPHA = float("-inf")`, ensuring all candidates WITHHOLD rather than silently falling back to beats-zero.

**Atlas parity (AC-26):**

Atlas community candidates and built-new (Opus) candidates flow through the SAME call, receive the SAME batch PBO, the SAME SPY-fold baseline, and the SAME BHY/Yekutieli FDR correction. Advertised community `oos_metrics` are structurally inert in the gate (parameter stability scoring only float-coerces shared param keys; dict metrics never influence survival). Identical fresh return series produce identical gate verdicts regardless of provenance.

## Internal Helpers

### `_fold_transform_single(daily_returns_pct) -> _FoldResult`

Slices a daily return series into the walk-forward fold structure (60/20/20 + PURGE_DAYS boundary purge). Returns validation-fold returns, OOS alpha (sum), validation days, purge integrity flag, and thin-window flag. Series shorter than `FOLD_TRANSFORM_MIN_TOTAL_DAYS` returns an empty fold with `purge_integrity_ok=False` (WITHHOLD, never fabricate).

Non-finite values are stripped before computation (conservative: gate on finite observations; too-few remaining → WITHHOLD via min-length check).

### `_compute_parameter_stability_score(candidate_params, incumbent_params) -> float`

Panel criterion D2 (parameter move-magnitude penalty). Normalised L1 distance scaled to `[0.0, 1.0]`; 1.0 = identical to incumbent, 0.0 = maximally far. Returns 0.5 (neutral prior) when params are empty or share no keys. Zero-sample-cost — consumes no validation budget.

### `_compute_prior_anchor_score(candidate_params, theory_prior_params) -> float`

Panel criterion D4 (prior-anchor / theory-consistency). Same normalised-L1 formula as stability score; 1.0 = matches theory prior exactly. Returns 0.5 neutral prior on empty or no-shared-keys input.

## Internal Dependencies

- `acceptance_gate` — `AcceptanceVerdict`, `evaluate_acceptance_gate`
- `autotuner` — fold constants (`TRAIN_RATIO`, `VALIDATION_RATIO`, `PURGE_DAYS`, `EMBARGO_DAYS`, `HARVEY_LIU_FDR_Q`); `benjamini_hochberg_adjust`, `compute_haircut_pvalue`, `compute_sortino_tstat`
- `math_engine` — `compute_pbo` (C5b batch PBO, AC-24); `PBO_REJECT_THRESHOLD` (imported locally inside the per-candidate loop to avoid circular import risk)

No import of `alpha_bot_execution`, `app`, or any execution module. Off-execution-path; advisory-only.
