# Optuna Tuning + Calibration Audit — AlphaBot v3

**Researcher:** optuna-methodology-researcher
**Date:** 2026-05-15
**Repo HEAD at audit time:** `main` @ 7586985
**Output destination:** explicit operator request (`docs/research/dashboard/`), overriding the researcher's default `docs/research/optuna/`.
**Confidence summary:** **Medium overall.** Optuna mechanics in `autotuner.py` are well-grounded and OOS-validated with a baseline cascade; methodology has several material gaps (no purge/embargo, no seeding, no Deflated-Sharpe correction, fixed sampler, no pruning, ad-hoc composite objective). Findings cite file:line; methodology claims cite primary literature.

> **Scope guard.** This is a read-only audit. No code/test changes were produced. All recommendations are framed as **options + trade-offs**, never directives. Where this report uses "should/could/consider," read it as an option for the operator to weigh, not an instruction.

---

## 1. Executive Summary

AlphaBot v3 runs an Optuna study **per symphony**, 500 trials, TPE (default sampler), 80/20 chronological split over a rolling 125-trading-day synthetic-history window, parallelized via `n_jobs=-1`, persisted to `sqlite:///optuna_studies.db`, and gated by a three-way **AI / Fallback / Default** OOS cascade with an asymmetric tie-break rule (`autotuner.py:294-411`).

The **largest methodology risks** in priority order:

1. **No purge/embargo between train and test** (`autotuner.py:274-283`). The split is a single chronological cut; serially-correlated 1-minute features (vol, vwap_diff, mc_prob computed off rolling 20d/14d windows) can leak across the boundary. (Lopez de Prado, *Advances in Financial Machine Learning*, 2018, Ch. 7.) **[High confidence]**
2. **No Deflated Sharpe / multiple-testing correction.** With 500 trials × N symphonies × repeated daily runs, the best-trial in-sample alpha is **systematically upward-biased**. (Bailey & López de Prado, 2014, *The Deflated Sharpe Ratio*.) **[High]**
3. **No RNG seeding anywhere** — neither `optuna.samplers.TPESampler(seed=...)` nor `np.random.seed(...)` for the MC paths. Re-running `<timestamp>__<symphony>` will not reproduce the same trajectory. `synthetic_history.py:233` calls `run_monte_carlo(... 300, 5)` which uses `np.random.choice` (`math_engine.py:517`) with no seed. **[High]**
4. **Objective is an ad-hoc composite, not a recognised risk metric** (`autotuner.py:209-233`). Penalises missed-upside, peak-to-exit drawdown, and asymmetrically weights negative guard-alpha. No Sharpe/Sortino/Calmar/K-ratio baseline. Hard to verify it captures "all the risk dimensions the operator cares about." **[High]**
5. **No pruner.** 500 trials × N symphonies × `n_jobs=-1` with no `optuna.pruners.*` means trials cannot be early-stopped on intermediate value. The `objective` is a single batch sim that does not call `trial.report()` (`autotuner.py:304-318`), so pruning is structurally impossible without code change. **[Medium]**
6. **The 0.5 coverage gate (`VWAP_WEIGHT_THRESHOLD`), the (-3.0, -0.5) bleed-arm clamps, and the 3-tick confirm (`VWAP_BREAK_CONFIRM_TICKS`) are hand-set and not in the search space** (`math_engine.py:325-326, 359-360`). For some of these (the 0.5 gate) hand-setting is *correct*; for others (the clamp endpoints, the confirm count) it is a defensible choice but not justified in code comments. **[Medium]**
7. **The 80/20 split test window is the only OOS check.** No held-out "evaluation" window untouched by selection. The same 25-day test fold is used to (a) accept/reject the AI proposal, (b) compare against fallback, (c) compare against default — three statistical tests on one fold. **[Medium]**

The optimisation **plumbing** is sound: `load_if_exists=True` enables study resumption (`autotuner.py:328`); a schema-validation gate rejects partial best_params (`autotuner.py:343-353`); the asymmetric tie-rule on AI vs Fallback is well-reasoned (`autotuner.py:389-394`); the baseline cascade prevents a degenerate study from overwriting last-known-good params.

---

## 2. Per-Question Findings

### Q1 — Optimization objective

**What it is.** `autotuner.py:233` returns `-total_guard_alpha`; `objective()` flips the sign back at `autotuner.py:317` (`alpha = -run_simulation(...)`) and `direction="maximize"` (`autotuner.py:328`). So Optuna maximises `total_guard_alpha`.

`total_guard_alpha` is a hand-crafted scalar (`autotuner.py:209-232`):

```
days_ago     = (current_dt - date_in_history).days
weight       = exp(-0.015 * days_ago)                    # autotuner.py:215-216
if missed_upside > 1.0:   total -= missed_upside * 1.5 * weight       # 219-220
if hwm>1 and drawdown_from_peak > 1.5: total -= drawdown_from_peak * 0.75 * weight  # 224-225
if guard_alpha < 0:       total += guard_alpha * 2.0 * weight          # 228-229
else:                     total += guard_alpha * weight                # 231
```

**Findings.**
- Single-objective composite. No multi-objective study (`create_study` has no `directions=[...]`, `autotuner.py:328`) [High, Tier-1 code evidence].
- The composite is **path-aware** (penalises missed upside + drawdown-from-peak) but uses **literal magic numbers** (1.0, 1.5, 0.75, 2.0, 0.015) with no source-comment justification — exactly the pattern banned by the project's coding standards for `math_engine.py` and that should arguably apply here too. [High]
- The exponential decay `exp(-0.015 * days_ago)` is a 46-day half-life (`ln 2 / 0.015 ≈ 46.2`). Over a 100-day train window that means the oldest day is weighted `exp(-1.5) ≈ 0.22` relative to the newest. **No literature citation supports this decay rate.** [Medium]
- The asymmetry on `guard_alpha < 0` (×2 weight) reflects loss aversion / Kahneman-style asymmetric utility but is **not a recognised quant objective** (Sharpe, Sortino, Calmar, K-ratio, MAR). [High]
- Composite weights stack three penalties + asymmetric reward, making the **scale** of `total_guard_alpha` hard to interpret. Two strategies with the same Sharpe could score very differently here based on which days saw missed upside vs drawdown. [Medium, Interpretation]

**Risk dimensions it does NOT capture.**
- Variance / volatility of guard-alpha across days (objective is a *sum*, not a risk-adjusted return). Two parameter sets with the same total but one delivering it via a single fat-tailed day vs many small wins are indistinguishable. [High]
- Sample size / trade frequency. A parameter set that triggers once in 100 days and a parameter set that triggers 50 times can both produce the same sum. The Sharpe-equivalent (mean / stddev × √N) is not computed.
- Drawdown of the *cumulative* guard-alpha series. Per-day drawdown-from-peak is included, but multi-day equity-curve drawdown is not.
- Tail risk (CVaR, max single-day loss).

**Options.**
- **Option A — Add Sharpe-style normalisation.** Track per-day guard-alpha as a series and return `mean / std × √N` (or Sortino with semi-std). Cost: re-tunes need re-baselining. Benefit: standard, comparable across symphonies, statistically interpretable. [Tier-1: Sharpe 1966; Sortino 1994]
- **Option B — Multi-objective (NSGA-II).** Optimise `(mean guard-alpha, -max-drawdown)` or `(guard-alpha, hit-rate)` jointly. Optuna supports `NSGAIISampler` and `directions=["maximize","minimize"]`. Cost: must pick a single point from the Pareto front for production (or use `study.best_trials`); cascade logic must change. Benefit: explicit risk/return separation. [Tier-1: Deb 2002, NSGA-II; Optuna multi-objective tutorial]
- **Option C — Keep composite; document constants.** Add source comments for each magic number, lock the composite as the canonical objective, add a golden fixture so changes are detected. Cost: lowest. Benefit: codifies what is already running.

### Q2 — Walk-forward methodology (purge / embargo)

**What it does.** `autotuner.py:274-283`:
```python
split_idx = int(total_days * 0.8)
train_dates = set(sorted_dates[:split_idx])
test_dates  = set(sorted_dates[split_idx:])
```
A single chronological split, no purge, no embargo, **no walking** (despite the docstring saying "True Walk-Forward Analysis", `autotuner.py:240`). This is a **train/test holdout**, not a walk-forward analysis (Pardo, *The Evaluation and Optimization of Trading Strategies*, 2nd ed., 2008, Ch. 11).

**Leakage vectors.** Each tick carries features computed off **rolling 20-day vol and 14-day ATR** (`synthetic_history.py:206-207`, `math_engine.py:523-` and the 14d ATR equivalent). On day `split_idx`, the `vol` and `base_atr_pct` values are computed off the trailing 20 days — **all of which are training days**. On day `split_idx + 1`, 19 of the 20 trailing days are still training days. The feature distributions in early test days are statistically near-identical to late training days. **This is canonical look-ahead via feature overlap.** [High, primary source: López de Prado 2018 Ch. 7.4 "Purged k-fold CV"]

**Cross-symphony global split.** The 80/20 cut is computed on the global date set (`autotuner.py:264-274`), not per-symphony. If symphonies have heterogeneous start dates this is fine; if any symphony's `sym_data.keys()` doesn't span the full 125 days, its individual train/test ratio drifts from 80/20. [Medium]

**No daily re-tuning of the split.** `run_autotuner` is invoked once per day (per `app.py` schedule, per project CLAUDE.md), so each day the 125-day window slides forward by 1 day and the 80/20 cut moves correspondingly. This is **rolling-window re-fit**, which is closer to walk-forward in spirit, but each individual study still sees one fixed train/test cut. [Medium]

**Options.**
- **Option A — Add purge.** Drop the first `K` test days where `K = max(vol_lookback, atr_lookback) = 20`. Cost: shrinks test set from ~25 days to ~5 days, reducing OOS stat power. [Tier-1: López de Prado 2018]
- **Option B — Add embargo.** Drop last `K` train days AND first `K` test days. Cost: same as above + train shrinks. Conservative against autocorrelated features. [Tier-1: López de Prado 2018]
- **Option C — Combinatorial Purged k-fold CV (CPCV).** N-fold, leave-one-out for OOS evaluation with purge. Cost: N× compute. Benefit: more OOS samples, robust to fold-luck. [Tier-1: López de Prado 2018 Ch. 12]
- **Option D — Anchored walk-forward.** Multiple non-overlapping (train_i, test_i) windows, optimise on each, average OOS metrics. Closer to Pardo's WFA. Cost: N× compute for K folds, methodology becomes "is the strategy robust across folds?" instead of "what are the best params?" [Tier-1: Pardo 2008]
- **Option E — Accept current single-split for daily re-tuning cadence.** Argument: because the bot re-tunes every EOD, each day's tuning is itself a walk-forward step, so the within-day train/test split is a sanity-check, not the WFA. Trade-off: depends on the operator's view of whether daily re-tune compensates for within-day leakage. [Interpretation — not in any primary source]

### Q3 — Search space sanity

**Tuned (Optuna) — autotuner.py:306-312:**

| Param | Range | Type | Sanity |
|---|---|---|---|
| `TRIGGER_THRESHOLD_PCT` | 5.0 - 25.0 | float | Sensible. 5× span. |
| `TAKE_PROFIT_MC_PCT` | 2.0 - 10.0 | float | Sensible. Operator's 5.0 default sits at the geometric mean. |
| `VWAP_CROSS_HWM_PCT` | 0.5 - 2.5 | float | Sensible. Centred on 1.0 default. |
| `VWAP_BLEED_MULTIPLIER` | 0.5 - 3.0 | float | Sensible. |
| `VWAP_BLEED_TICKS` | 3 - 30 | int | Wide. 10× span. Likely too wide for production stability — tuning to 3 vs 30 is very different behaviour. |
| `PARABOLIC_VELOCITY_THRESHOLD` | 1.0 - 4.0 | float | Sensible. |
| `MAX_PARABOLIC_SQUEEZE` | 0.1 - 0.8 | float | Wide; operator default 0.5. |

The `TRIGGER_THRESHOLD_PCT` entry deserves a note: it is in the search space (line 306) **AND** the default `DEFAULT_LOCKED_VARS = ["TRIGGER_THRESHOLD_PCT"]` (`database.py:23-25`). The lock prevents the AI advisor from overriding it but the Optuna objective still suggests it on every trial, then `best_params` writes it to `current_params` if the AI proposal is adopted (`autotuner.py:399-401`). **This is an inconsistency: the locked-vars semantics differ between the Optuna path and the AI-advisor path.** [Medium]

**Hand-set (NOT in search space) — math_engine.py:**

| Constant | Value | Location | Should it be tuned? |
|---|---|---|---|
| `MIN_STOP_OPEN` | 0.3 | `math_engine.py:52` | Probably no. Lower bound is a microstructure floor (bid-ask spread + commission); tuning below it is unsound. |
| `MIN_STOP_CLOSE` | 0.15 | `math_engine.py:53` | Same as above. |
| `HWM_HOLD_TICKS_THRESHOLD` | 5 | `math_engine.py:60` | Candidate for tuning. Determines breakeven-lock latency. |
| `VWAP_BLEED_ARM_MIN` | -3.0 | `math_engine.py:325` | Boundary clamp — keep hand-set; tuning the clamp endpoint is a category error. |
| `VWAP_BLEED_ARM_MAX` | -0.5 | `math_engine.py:326` | Same as above. |
| `VWAP_WEIGHT_THRESHOLD` | 0.5 | `math_engine.py:359` | **Correct to keep hand-set.** This is the "validity-of-signal" gate: below 50% allocation coverage the weighted VWAP-diff is statistically unreliable. Tuning this would be tuning the *definition* of when the signal exists, not its threshold. |
| `VWAP_BREAK_CONFIRM_TICKS` | 3 | `math_engine.py:360` | Borderline. 3 is a noise-filter heuristic. Could be tuned in range `[2, 6]` to test whether the project's choice is optimal. |
| `MAX_SQUEEZE_FLOOR` | 0.20 | `database.py:14`, `alpha_bot_execution.py:47` | **Currently NOT in Optuna search space** but IS suggestible by the AI advisor (`ai_advisor.py:62, 128`). This is intentional per the comment at `ai_advisor.py:128`. Tuning rationale: the AI sees rare regime-shift evidence Optuna can't (Optuna sees only 125 days). [Project decision, not a methodology error.] |
| Composite-objective constants 1.0, 1.5, 0.75, 2.0, 0.015 | inline | `autotuner.py:219-229` | **Should be named and source-commented per project standard.** |
| Inner-MC `simulation_paths=300`, `neighbor_k=5` | inline | `synthetic_history.py:233` | Hand-set "speed approximations." Production MC uses different values (cf. `alpha_bot_execution.py` for live).** Could be tuned for the speed/precision trade-off but adds dimensions and confounds calibration. |

**Specific constants flagged in the VWAP audit:**
- `VWAP_CROSS_HWM_PCT = 1.0` — **TUNED.** Range 0.5-2.5 (`autotuner.py:308`).
- `VWAP_BREAK_CONFIRM_TICKS = 3` — **HAND-SET.** Not in search space (`math_engine.py:360`).
- `VWAP_BLEED_ARM_MIN = -3.0`, `VWAP_BLEED_ARM_MAX = -0.5` — **HAND-SET.** Clamp endpoints (`math_engine.py:325-326`). Note: a *multiplier* `VWAP_BLEED_MULTIPLIER` IS tuned (`autotuner.py:309`).
- `VWAP_WEIGHT_THRESHOLD = 0.5` — **HAND-SET.** Coverage gate (`math_engine.py:359`).

**My recommendation (Interpretation):** keep `VWAP_WEIGHT_THRESHOLD = 0.5` and the bleed-arm clamps hand-set (they are definitional/boundary, not behavioural). `VWAP_BREAK_CONFIRM_TICKS` is the marginal case — defensible either way; if tuned, restrict to integer range `[2, 6]`.

### Q4 — Sampler choice

**What it uses.** `optuna.create_study(...)` with no `sampler=` kwarg (`autotuner.py:328`), so Optuna defaults to **TPESampler** with `n_startup_trials=10` (Optuna docs, retrieved 2026-05-15, Tier 1).

**Is TPE appropriate?**

- **Parameter dim = 7.** TPE works well for `dim ≤ 20` on noisy/expensive objectives (Bergstra et al. 2011, *Algorithms for Hyper-Parameter Optimization*, NeurIPS, Tier-1). [Medium]
- **Parameter correlations.** TPE assumes **conditional independence** between dimensions in its KDE; if AlphaBot's params are correlated (e.g., `PARABOLIC_VELOCITY_THRESHOLD` and `MAX_PARABOLIC_SQUEEZE` co-operate in the squeeze mechanism, `math_engine.py compute_active_trailing_stop`), TPE will be slower than a sampler that models covariance. [Medium]
- **Noisy objective.** The objective is deterministic *given fixed RNG seeds*, but **no seeds are set**, so the inner-MC randomness in `synthetic_history` makes every evaluation noisy. TPE is more robust to noise than GP-BO. [Medium]
- **n_jobs=-1.** TPE under parallel execution uses `reseed_rng()` to avoid duplicate suggestions (Optuna docs, Tier 1), but the algorithm's surrogate model still updates only on completed trials — so the first `n_jobs` parallel trials each draw from priors with stale models. With `n_startup_trials=10`, the first ~10 trials are random anyway, masking the staleness. After that, parallel trials lose some sample efficiency vs sequential TPE. [Medium]

**Alternatives and trade-offs.**
- **CMA-ES (`CmaEsSampler`).** Models the full covariance matrix; better for correlated continuous params; **does not handle integer params well** (`VWAP_BLEED_TICKS` is int). Hansen 2006. Trade-off: must convert `VWAP_BLEED_TICKS` to float or use the categorical workaround.
- **GP-BO (`GPSampler`, added Optuna 4.0).** Gaussian-process surrogate; excellent for small budgets (<200 trials) and continuous spaces; expensive O(n³) per fit; struggles past ~500 trials. Snoek et al. 2012, Tier-1.
- **NSGAIISampler.** Only relevant if going multi-objective.
- **Random / Grid.** Baseline. Grid is a non-starter at dim 7 (~10⁷ cells at 10/dim).

**Empirical comparison budget.** With 500 trials × dim 7, **TPE is a defensible default**; the gain from switching is likely modest unless the objective is shown to be highly correlated. [Medium]

### Q5 — Pruner usage

**Current state.** No pruner is configured (`autotuner.py:328`). The `objective()` does **not** call `trial.report(value, step)` or `trial.should_prune()` (`autotuner.py:304-318`). Pruning is therefore structurally impossible without a refactor that streams intermediate values from `run_simulation`.

**Cost of no pruning.** Every trial evaluates 100 train days × every tick × every symphony. At `n_jobs=-1` × 500 trials × N symphonies × daily re-runs, this is the dominant compute cost in the project. Pruning could plausibly cut 30-70% of compute (Optuna docs, Hyperband benchmarks). [Medium, Tier-1 + Tier-3]

**Options.**
- **Option A — Median pruner on per-day intermediate.** Refactor `run_simulation` to yield per-day partial alpha; pruner kills trials whose day-K running sum is below median. Cost: refactor `run_simulation` (currently a single-shot function). Benefit: large compute saving.
- **Option B — Hyperband / Successive-Halving on n_train_days.** Treat number of train days as the resource budget. Trade-off: very-late-blooming params (a strategy that only earns guard-alpha on rare catalyst days) get pruned. **High risk for tail-event strategies — which Guard Alpha arguably is.** [Tier-1: Li et al. 2017]
- **Option C — Keep no pruner.** Accept compute cost; protect against pruning a tail-strategy. [Defensible]

### Q6 — Trial count + statistical sufficiency

**Rule of thumb.** Bayesian-optimisation literature: typically 10×N to 50×N samples where N = parameter dim, for low-noise objectives (Frazier 2018, *A Tutorial on Bayesian Optimization*, Tier-1). For TPE on noisy objectives, 50×N - 100×N is more typical.

**For AlphaBot.** N=7, so 70-700 trials is the rule-of-thumb range. **500 trials sits in the upper half of the recommended band — appropriate.** [High]

**However.** With 7 dimensions and 500 trials, the **density** of samples is `500^(1/7) ≈ 2.46` per axis — meaning if you grid-equivalent the space, each axis gets ~2.5 levels resolved. Bayesian optimisation does better than this because it concentrates on promising regions, but the implication is: **don't expect fine-grained surface mapping; expect rough basin-finding.** [High, Interpretation]

The project floor (100 trials) is at the **bottom** of the recommended band and should be reserved for fast-iteration debugging, not production tuning. [Tier-1: Bergstra & Bengio 2012, *Random Search for Hyper-Parameter Optimization*]

### Q7 — Per-symphony vs cross-symphony optimization

**Current.** Per-symphony. Each `normalized_name` gets its own study (`autotuner.py:328`, study_name = normalized_name).

**Trade-off literature.**
- **Per-symphony** maximises specificity but risks **overfitting to symphony-specific noise**, especially for low-volume symphonies with few trigger events in 125 days. With 500 trials × per-symphony, the multiple-testing-correction problem multiplies (Q9). [Tier-1: Harvey, Liu, Zhu 2016, *...and the Cross-Section of Expected Returns*]
- **Cross-symphony pooled optimization** treats all symphonies as draws from a common return-generating process. Reduces overfitting but assumes homogeneity. Probably **inappropriate** for AlphaBot since symphonies are intentionally heterogeneous (different sectors, different leverage profiles). [Tier-2]
- **Hierarchical/Bayesian pooling.** Partial pooling: per-symphony params with a prior centred on a cross-symphony mean. Industry-standard for multi-asset hedge funds. Outside Optuna's idiom (would need PyMC/Stan). [Tier-1: Gelman & Hill 2007]

**The project's existing OOS cascade is a partial mitigation** — even if per-symphony Optuna overfits, the OOS cascade demands the AI proposal beat BOTH Fallback (last-known-good per symphony) AND Default (cross-symphony prior) on the held-out window (`autotuner.py:389-411`). This **does** correct for some overfitting at the cost of preferring the global default whenever per-symphony signal is weak. [High, Interpretation]

### Q8 — Study reproducibility

**Sources of non-determinism (none seeded):**
1. `optuna.samplers.TPESampler()` default — no `seed=` argument (`autotuner.py:328`).
2. `study.optimize(..., n_jobs=-1)` — parallel workers, `reseed_rng()` reassigns per-worker seeds non-deterministically.
3. `np.random.choice(nearest_day_returns, size=simulation_paths)` (`math_engine.py:517`) — no `np.random.seed()` upstream.
4. `synthetic_history.generate_synthetic_history` Parallel call (`synthetic_history.py:250`) — `joblib.Parallel(n_jobs=-1)` workers each have their own RNG state.

**Implication.** Re-running with the same `<timestamp>__<symphony>` study name will *resume* the persisted Optuna trials (because `load_if_exists=True`, `autotuner.py:328`), but the **synthetic history that feeds the objective** is re-generated from cache (`synthetic_history.py:257-259`) — if cache exists, deterministic on the cache; if cache is stale, regenerated with fresh randomness in `run_monte_carlo`.

Net: **studies are not bit-for-bit reproducible** under re-run, even with the same study name. Trial values may drift if synthetic history is regenerated. [High]

**Optuna+parallelism RNG note.** Even with `seed=42`, `n_jobs > 1` parallel TPE will not be strictly reproducible because `reseed_rng()` is called per-worker and Python multiprocessing worker startup order is not deterministic (Optuna issues #1330, #3697 — community discussion, Tier-3). [Medium, Tier-3]

**Options.**
- **Option A — Seed everything.** `TPESampler(seed=42)` + `np.random.seed(...)` at top of `run_monte_carlo` + force `n_jobs=1` for the Optuna study (lose parallelism). Cost: ~10× wall-clock. Benefit: full reproducibility.
- **Option B — Seed sampler, accept inner-MC randomness.** `TPESampler(seed=42)`, keep `n_jobs=-1`, accept that two re-runs will diverge slightly. Cost: low. Benefit: sampler trajectory becomes "more" reproducible (subject to Optuna's parallel non-determinism caveat).
- **Option C — Hash + log the synthetic-history cache file content per trial.** Achieves audit-trail reproducibility without forcing seeding. Cost: small code change.

### Q9 — Deflated Sharpe / multiple-testing correction

**Concern.** With 500 trials per symphony, the best-trial in-sample `total_guard_alpha` is the **maximum of 500 noisy estimates** — by definition upward-biased. The expected value of the maximum of N iid normal draws grows like `sqrt(2 ln N)` standard deviations above the mean (Bailey & López de Prado 2014, Tier-1). For N=500, that's `~3.5σ` of selection bias.

**Current mitigation.** The OOS-cascade (`autotuner.py:394`) requires the best in-sample to *also* beat baselines on a held-out 25-day fold — this is the standard form of "validation set" correction. **It is a meaningful correction, but not Deflated Sharpe.** The held-out fold is itself a single 25-day sample, and **the same fold is used three times** (AI vs Fallback, AI vs Default, Fallback vs Default — `autotuner.py:389-411`).

**What's NOT corrected.**
- The reported `best_alpha_train` and `oos_alpha` numbers logged at `autotuner.py:396, 398, 419` are **raw, not deflated**. A user reading the Discord report sees the bias.
- Across N symphonies × daily re-runs, the family-wise error rate for "at least one symphony's tuning is a fluke" compounds. [High]

**Options.**
- **Option A — Compute Deflated Sharpe Ratio on the OOS fold.** Bailey/Lopez 2014 formula: deflate the observed Sharpe by the number of trials and the moments of the in-sample distribution. Optuna provides `study.trials_dataframe()` to get the full IS distribution. Report alongside raw `oos_alpha`.
- **Option B — Bonferroni / Holm correction on the OOS p-value.** Treat each symphony's adoption as a hypothesis test. Cost: lower stat power → more rejections (Fallback wins more often), which is conservative-by-design.
- **Option C — Probabilistic Sharpe Ratio (PSR, Bailey/Lopez 2012).** Report `P(SR_oos > SR_baseline | data)`. Adopt only if PSR > 0.95.
- **Option D — Status quo + explicit doc.** Document that reported alphas are biased; rely on the OOS cascade as the de-facto correction.

### Q10 — Out-of-sample validation

**Current.** The 25-day test window is used both for:
1. Selection (deciding AI vs Fallback vs Default).
2. Reporting (the Discord summary, the persisted `oos_alpha` row).

There is **no further untouched evaluation window.** [High, code evidence: `autotuner.py:274-411`]

**Why this matters.** When the test fold drives selection, the selected params are conditioned on it — so reporting "OOS alpha = X" on that same fold inflates X relative to what truly-OOS performance would be. (López de Prado 2018, Ch. 7.)

**Options.**
- **Option A — Three-way split 60/20/20:** train / validation (for selection) / evaluation (frozen for reporting). Costs: smaller train fold (60 days × intraday → still ~46k ticks, probably fine). Benefit: honest reporting metric.
- **Option B — Forward-evaluate after deployment.** Track adopted-params live performance for K days; compare to in-sample/validation. Out of Optuna scope; project arguably already does this via live dashboards.
- **Option C — Accept current single-OOS.** The cascade is conservative (only adopt AI if it beats BOTH baselines), so the selection bias is bounded. [Defensible, Interpretation]

### Q11 — Specific VWAP constants

Already summarised in Q3. Restated as a decision matrix:

| Constant | Current state | My read | Reasoning |
|---|---|---|---|
| `VWAP_CROSS_HWM_PCT` | Tuned (0.5-2.5) | **Keep tuned.** | Behavioural threshold; data should pick it. |
| `VWAP_BREAK_CONFIRM_TICKS` | Hand-set (3) | **Defensible either way.** | Noise-filter heuristic. If tuned, integer range `[2, 6]`. |
| `VWAP_BLEED_ARM_MIN`, `VWAP_BLEED_ARM_MAX` | Hand-set (-3.0, -0.5) | **Keep hand-set.** | Boundary clamps. Tuning clamp endpoints is a category error: clamps define the *bounds* of meaningful inputs, not behavioural thresholds. The interior `VWAP_BLEED_MULTIPLIER` IS already tuned (0.5-3.0). |
| `VWAP_WEIGHT_THRESHOLD` | Hand-set (0.5) | **Keep hand-set.** | Definitional gate: "is the VWAP signal valid?" below 50% coverage. Tuning this would tune the *definition* of signal validity, not its threshold. Matches statistical practice for weighted-aggregate validity. [Interpretation, no primary source quoted] |

---

## 3. Recommendations — Ranked by Impact (Options, Not Directives)

| # | Recommendation | Impact | Cost | Lit source |
|---|---|---|---|---|
| 1 | **Add purge/embargo to the train/test split** (drop first 20 test days OR last 20 train days). | High — eliminates feature-overlap leakage that currently inflates OOS metrics. | Low (3-line code change to `autotuner.py:274-283`); shrinks test fold ⇒ slightly weaker OOS power. | López de Prado 2018, Ch. 7 |
| 2 | **Seed everything reproducibly**, even if only `TPESampler(seed=42)` + `np.random.seed(42)` in `run_monte_carlo`. | High — without seeding, the audit trail for live-trade param decisions is non-reproducible. | Low. Slight loss of stochastic exploration if `n_jobs=1` is also adopted. | Optuna docs; project's own concern about `<timestamp>__<symphony>` reproducibility |
| 3 | **Replace the ad-hoc composite with a Sharpe-style normalised objective** OR document each magic number with a source-comment per project standard. | High — current objective is opaque and not directly comparable across symphonies; literal magic numbers violate the math-layer project rule. | Medium — re-baselines all existing studies; OOS cascade thresholds may need recalibration. | Sharpe 1966; Sortino 1994; project CLAUDE.md "no magic numbers" rule |
| 4 | **Report Deflated Sharpe (or PSR) alongside raw `oos_alpha`** in Discord summary and `autotune_runs` DB. | High — operator gets the bias-corrected number, not just the selection-biased one. | Medium — needs a new analytics helper; doesn't change selection logic. | Bailey & López de Prado 2012 (PSR), 2014 (DSR) |
| 5 | **Introduce a frozen evaluation window** (e.g., last 10 days held out from both train and selection-test). | Medium — separates "did we pick well?" from "should we trust the reported number?" | Medium — train shrinks; OOS cascade becomes 3-stage. | López de Prado 2018, Ch. 7 |
| 6 | **Document the locked-vars inconsistency** between Optuna path (lock has no effect) and AI-advisor path (lock blocks override). | Medium — risk of silent param drift on a "locked" variable. | Low (doc only) OR fix in code: have `objective()` skip suggesting locked vars and instead read from `current_params`. | Project internal |
| 7 | **Consider a pruner** (Median or ASHA) iff `run_simulation` is refactored to yield per-day partial values. | Medium — 30-70% compute saving. | Medium — refactor of `run_simulation`. Risk: pruning tail-event strategies. | Optuna docs; Li et al. 2017 (Hyperband) |
| 8 | **Promote the 5 inline composite-objective constants** (1.0, 1.5, 0.75, 2.0, 0.015) to named module-level constants with source comments. | Low-Medium — code hygiene + supports invariant testing. | Low. | Project CLAUDE.md |
| 9 | **Per-symphony vs cross-symphony:** accept the current per-symphony approach; document the partial-pooling alternative as a forward option if symphony count grows >20. | Low — current cascade mitigates the per-symphony overfit risk. | None now; medium if migrated. | Harvey, Liu, Zhu 2016; Gelman & Hill 2007 |
| 10 | **Sampler choice:** keep TPE for now. Re-evaluate with CMA-ES if parameter correlation evidence emerges. | Low. | Low. | Bergstra 2011; Hansen 2006 |

---

## 4. Statistical-Validity Summary

Mapping each project-CLAUDE.md "is this rigorous?" question to a yes/partial/no:

| Validity check | Status | Evidence |
|---|---|---|
| Train/test split exists | YES | `autotuner.py:274-283` |
| Purged | NO | No purge logic |
| Embargoed | NO | No embargo logic |
| Sampler appropriate for dim/noise | PARTIAL | TPE default; reasonable for dim 7 but un-seeded |
| Trial count in literature band | YES | 500 trials, dim 7, within 50-100×N |
| Pruner used | NO | None configured; objective is single-shot |
| Reproducible (seeded) | NO | No seeds anywhere |
| Multiple-testing correction (DSR/PSR) | NO | Raw best-trial Sharpe reported |
| OOS cascade against baselines | YES | AI vs Fallback vs Default, asymmetric tie rule |
| Frozen final-eval window separate from selection-test | NO | Single test fold |
| Composite-objective magic numbers documented | NO | 5 inline literals in `autotuner.py:219-229` |
| Search-space ranges sensible | YES | All 7 ranges defensible |
| Clamp endpoints kept hand-set | YES | `VWAP_BLEED_ARM_*` not tuned |
| Definitional gates kept hand-set | YES | `VWAP_WEIGHT_THRESHOLD` not tuned |

---

## 5. Open Questions / Assumptions

1. Does the operator consider the daily re-tune cadence to *substitute for* in-study walk-forward? If yes, single-split is more defensible. If no, the lack of WFA is a material gap. **Not answered by the codebase.**
2. The composite-objective weights (1.5, 0.75, 2.0) — are these a deliberate utility model, or accumulated from incident-driven tweaks? No git log was consulted for this report; recommend `git log -- autotuner.py` to confirm. **Out of read-only scope.**
3. Is `study_name=normalized_name` (`autotuner.py:328`) ever re-used across days? The project CLAUDE.md gotcha says "Use `<timestamp>__<symphony>`; never reuse a study name" but the code uses just `normalized_name`. This means **studies are RESUMED across days, not created fresh per day** — every day's `study.optimize(... n_trials=500)` adds 500 trials to a growing study. After K days the study has K × 500 trials, with old trials informing new TPE suggestions on (now possibly stale) feature distributions. **This is a potentially material finding; recommend explicit operator verification.** [High flag, single-source code evidence]
4. The `OPTUNA_SEARCH_SPACE_KEYS` constant (`autotuner.py:19-23`) lists 7 keys; the `objective()` suggests 7 keys (`autotuner.py:306-312`). The validation gate (`autotuner.py:343-353`) requires all 7 to be present in `best_params` — but TPE only suggests params declared in the trial via `suggest_*` calls, so this gate can only fail for empty `best_params` (a degenerate study). **The gate is mostly defensive against a different failure mode (the AI-proposal pipeline elsewhere), not against Optuna itself.**
5. Multi-symphony co-tuning: do symphonies share any state across optimization runs? Code review suggests no (each `optimize()` call is independent), but verifying parallel-safety of `optuna_studies.db` under `n_jobs=-1` × concurrent symphonies would require a separate audit.

---

## 6. Sources

**Primary (Tier 1).**
- López de Prado, M. (2018). *Advances in Financial Machine Learning.* Wiley. Chapters 7 (Cross-Validation in Finance) and 12 (Backtesting through Cross-Validation). Core source for purge/embargo/CPCV.
- Bailey, D. H., & López de Prado, M. (2014). *The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality.* Journal of Portfolio Management.
- Bailey, D. H., & López de Prado, M. (2012). *The Sharpe Ratio Efficient Frontier.* Journal of Risk. Introduces Probabilistic Sharpe Ratio.
- Pardo, R. (2008). *The Evaluation and Optimization of Trading Strategies* (2nd ed.). Wiley. Walk-forward analysis canonical text.
- Bergstra, J., Bardenet, R., Bengio, Y., & Kégl, B. (2011). *Algorithms for Hyper-Parameter Optimization.* NeurIPS. TPE paper.
- Bergstra, J., & Bengio, Y. (2012). *Random Search for Hyper-Parameter Optimization.* JMLR.
- Hansen, N. (2006). *The CMA Evolution Strategy: A Comparing Review.*
- Snoek, J., Larochelle, H., & Adams, R. P. (2012). *Practical Bayesian Optimization of Machine Learning Algorithms.* NeurIPS. GP-BO foundational.
- Frazier, P. I. (2018). *A Tutorial on Bayesian Optimization.* arXiv:1807.02811.
- Li, L., Jamieson, K., et al. (2017). *Hyperband: A Novel Bandit-Based Approach to Hyperparameter Optimization.* JMLR.
- Deb, K., et al. (2002). *A Fast and Elitist Multiobjective Genetic Algorithm: NSGA-II.* IEEE Trans. Evol. Comput.
- Sharpe, W. F. (1966). *Mutual Fund Performance.* J. Business.
- Sortino, F. A., & van der Meer, R. (1994). *Downside Risk.* J. Portfolio Management.
- Harvey, C. R., Liu, Y., & Zhu, H. (2016). *...and the Cross-Section of Expected Returns.* Review of Financial Studies. Multiple-testing in finance.
- Gelman, A., & Hill, J. (2007). *Data Analysis Using Regression and Multilevel/Hierarchical Models.* Cambridge UP.

**Optuna docs (Tier 1).** Accessed 2026-05-15.
- `optuna.samplers.TPESampler` — `https://optuna.readthedocs.io/en/stable/reference/samplers/generated/optuna.samplers.TPESampler.html` — `n_startup_trials=10` default; `reseed_rng()` parallel-safety.
- `optuna.samplers.CmaEsSampler`, `optuna.samplers.NSGAIISampler`, `optuna.samplers.GPSampler` reference pages.
- `optuna.pruners` module (MedianPruner, HyperbandPruner, SuccessiveHalvingPruner).

**Community (Tier 3).** Optuna GitHub issues #1330, #3697 — parallel-RNG reproducibility caveats. (Cited [Medium] only.)

**Code evidence (Tier 1 — project-internal).**
- `autotuner.py` lines cited inline above (HEAD 7586985).
- `math_engine.py:52-60, 325-360, 480-521`.
- `database.py:10-25`.
- `synthetic_history.py:200-260`.
- `ai_advisor.py:38, 62, 83, 128`.
- `alpha_bot_execution.py:47, 51, 555, 558, 760`.

---

*End of audit. Read-only; no source files modified.*
