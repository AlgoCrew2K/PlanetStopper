# Methodology-Grounding Post-Merge Audit — Engine Correctness Remediation

Date: 2026-05-17
Branch / SHA at audit: main @ 0228a37
Auditor: quant-risk-researcher (read-only)
Scope: All workstreams in `feature-plans/engine-correctness-remediation.merged.md` — E2, O1, O2, O5, O6, H2, H3, V1, V2, V3, plus the I1 / I2 investigation verdicts.
Method: Per-workstream pairing of (a) the cited primary literature with (b) the implementation in `math_engine.py`, `autotuner.py`, `alpha_bot_execution.py`, and the supporting fixture/test surfaces. Findings labelled per the researcher.md confidence tagging convention.

---

## 0. Charter and label conventions

Findings are tagged `VALIDATED with citation`, `ISSUE (Low/Med/High)`, or `NOT-COVERED-BY-LITERATURE (operational only)`. Empirical grades use the researcher template (`[Theoretical]` / `[Backtest]` / `[Out-of-sample backtest]` / `[Live evidence]` / `[Folklore]`).

This auditor is **read-only**. No code modified. No tests run. The report records what the merged code does and whether the literature it cites supports the claim — not whether the implementation is the right operational choice. Implementation-level recommendations are reframed as options + trade-offs.

---

## 1. E2 — Trailing-stop monotonicity ratchet wire-up

### Implementation
`math_engine.py:154-222` (`compute_breakeven_update`) accepts `previously_persisted_stop_level: float | None = None` and clamps the output as `max(previously_persisted_stop_level, stop_trigger_level)` unless `is_triggered=True`. The live caller now threads the prior persisted stop in `alpha_bot_execution.py:698-705` (per the plan AC-E2.1). Eight RED tests in `tests/math_engine/test_stop_monotonicity.py` and an additional wire-up test in `tests/execution/test_e2_trailing_stop_monotonicity_wire_up.py` exercise the clamp.

### Cited literature
The merged plan, the math-engine audit (`docs/research/dashboard/math-engine-audit.md:25, :407`), and the constants log (`docs/math_engine/constants.md:422`) all attribute the monotonicity invariant to **"Fu & Zhang 2010 canonical trailing-stop formulation."**

### Verification against primary sources
- Fu, J. & Zhang, J. (2010). "Is the trailing-stop strategy always good for stock trading?" `[High]` — exists as a working paper; subject is **probabilistic evaluation of whether a trailing-stop strategy is dominated by buy-and-hold under various return processes**. The paper analyses the risks/rewards of trailing stops; it does not introduce the monotonicity property as a definitional invariant. (Semantic Scholar listing; full text behind paywall.)
- Glynn, P. W. & Iglehart, D. L. (1995). "Trading Securities Using Trailing Stops." *Management Science* 41(6), 1096-1106. DOI: 10.1287/mnsc.41.6.1096. `[High]` — this is the **canonical** academic definition of a trailing stop: "the stop is raised to remain a fixed distance from the maximum price at which the security trades." The non-decreasing-in-the-running-maximum property is intrinsic to that definition.
- Leung, T. & Zhang, H. (2019). "Optimal Trading with a Trailing Stop." *Applied Mathematics & Optimization*. DOI: 10.1007/s00245-019-09559-0. `[High]` — formalises the trailing-stop level as a function of the running maximum; the level is monotone non-decreasing by construction.

### Finding
**ISSUE — Low/Med (citation precision).** The mathematical invariant the code enforces — *the stop is non-decreasing across cycles* — is correctly implemented and correctly tested. The attribution **to Fu & Zhang 2010** is a misattribution of provenance: Fu & Zhang evaluated the *effectiveness* of trailing stops, they did not author the monotonicity definition. The canonical citation is **Glynn & Iglehart (1995)**; a complementary modern formalisation is **Leung & Zhang (2019)**. `[Medium]` confidence — two independent sources (Semantic Scholar overview + practitioner verification, plus the Leung-Zhang explicit formulation) corroborate this.

Implementation correctness is **VALIDATED with citation** under Glynn & Iglehart 1995. The Fu & Zhang attribution in `math-engine-audit.md:25, :407, constants.md:422` should be re-anchored to Glynn & Iglehart at the next documentation sweep.

### Empirical grade
`[Theoretical]` for the invariant itself (definitional). The implementation is `[Backtest]` via the eight monotonicity scenarios in `test_stop_monotonicity.py`; no `[Live evidence]` of the wired-up clamp altering outcomes is reported yet — the plan's AC-E2.5 ("Live verification after merge") remains an open verification step.

### Replication status
The Glynn-Iglehart definition has been replicated as the standard formal definition across the academic trailing-stop literature (Han-Zhou-Zhu 2014; Leung-Zhang 2019; Kaminski-Lo 2014 — all assume the non-decreasing stop). **Replicated: Yes.**

### Regime sensitivity
The ratchet itself is regime-invariant. Where it fails: (a) gap-down opens where the persisted stop is above the new opening price (caller-side cold-reset required — explicitly handled by `is_triggered=True` bypass and the `None` sentinel on position open); (b) a position closed and re-opened mid-day with stale persisted stop — out of scope for E2's invariant; depends on caller-side reset hygiene.

---

## 2. O1 — Purge + embargo in walk-forward split

### Implementation
`autotuner.py:60-77` defines `PURGE_DAYS=20` and `EMBARGO_DAYS=1`. `autotuner.py:618-636` applies the purge + embargo at **both** boundaries (train|validation and validation|frozen-eval). Feature lookback inventory in `tests/fixtures/autotuner/purge_embargo/feature_lookback_inventory.json` documents the lookback survey: `20d_historical_vol` (20-day), `14d_atr_pct` (15-day = 14 TR periods + 1 prior close). MC kNN and decay weighting are explicitly excluded from purge sizing with stated rationale (pre-computed in tick data; objective weight, not feature window).

### Cited literature
- **López de Prado, M. (2018).** *Advances in Financial Machine Learning*. Wiley. ISBN 978-1119482086. Chapter 7 — Cross-Validation in Finance, specifically §7.4 ("Purging") and §7.4.2 ("Embargo"). The constant block at `autotuner.py:71` cites "López de Prado 2018, Advances in Financial Machine Learning, Ch. 7 (Purged k-fold CV)".

### Verification
- **PURGE sizing.** AFML Ch. 7.4: purge the training set of observations whose label *information* overlaps with the test set's observation horizon. For volatility/ATR features the relevant horizon is the **feature lookback window** (20-day vol uses the trailing 20 trading days, so a train sample on day D consumes data from days D-19 .. D). If any of those 20 days falls inside the test fold, the feature leaks. `PURGE_DAYS = max(20, 15) = 20` is the correct **max-of-feature-lookbacks** sizing.
- **EMBARGO sizing.** AFML Ch. 7.4.2 recommends an embargo proportional to the test-set fraction; de Prado proposes h ≈ 0.01 × T for many financial CV setups (~ 1.25 trading days on a 125-day window). `EMBARGO_DAYS = 1` is at the bottom of that range; defensible for a daily-cadence aggregation but arguably tight if intra-day autocorrelation is non-trivial.
- **Boundaries.** AFML §7.4.2 explicitly discusses applying the embargo *after* the test fold to prevent contamination of the *next* training fold. The implementation applies purge + embargo at **both** boundaries of a 3-way split — a stricter / more conservative variant than the 2-way scheme in the book. Defensible.
- **Excluded features.** The `_excluded_from_purge` rationale in the fixture is mechanically correct: `run_monte_carlo` is **not** invoked in the autotuner sim loop (`autotuner.py:251-258` reads `mc_prob` from pre-computed tick data); the `_GUARD_ALPHA_DECAY_RATE` weight does not consume any feature window that extends across the boundary.

### Finding
**VALIDATED with citation.** Purge by max-feature-lookback (20 days) is faithful to López de Prado 2018, Ch. 7.4 (Wiley, ISBN 978-1119482086).

`[Medium]` confidence on **embargo sizing**: AFML Ch. 7.4.2's "~1% of T" guidance (Eq. 7.5 in some editions) suggests ≥1 day for T=125 but does not insist on 1 specifically; some practitioners use 2-5 days for trade-decision-cadence overlap. Defensibility is high but the choice is not pinned by the book. Logged as **Open Question O1-Q1: re-check embargo sizing once H1 telemetry produces a measurable autocorrelation estimate of cycle-to-cycle objective values.**

`[High]` confidence on **purge sizing** and on **applying purge+embargo at both boundaries** of the 3-fold split — exceeds the book's minimum and is methodologically defensible.

### Empirical grade
`[Theoretical]` — purge/embargo is a methodological correctness fix, not a return-attribution claim. The validity of the fold-splitting can be tested by an RED on synthetic feature-lookback fixtures (`tests/autotuner/test_o1_purge_embargo.py` does exactly this).

### Replication status
Purged k-fold CV is the **standard** methodology in quant ML CV — replicated across the AFML reference and downstream packages (`mlfinlab` open-source, e.g.). **Replicated: Yes.**

### Regime sensitivity
Embargo of 1 day is fragile in regimes with strong daily autocorrelation in objective values (e.g., trending markets where adjacent-day Sortino correlates strongly). The conservative remediation would be `EMBARGO_DAYS ≥ 2` until autocorrelation is empirically measured. Out of scope for this audit.

---

## 3. O2 — Deflated-Sharpe correction at trial selection

### Implementation
`autotuner.py:122-155` (`compute_deflated_sharpe_ratio`) implements:

```python
denom_sq = 1.0 - gamma3 * SR_obs + ((gamma4 - 1.0) / 4.0) * (SR_obs ** 2)
return (SR_obs - SR_0) * sqrt(T - 1) / sqrt(denom_sq)
```

`autotuner.py:711-763` collects all completed trials, computes population variance, skewness `gamma3`, and kurtosis `gamma4` from the cross-trial Sortino distribution, then re-ranks all trials by DSR and selects the **DSR-maximising** trial. Naive Sortino is retained for reporting.

### Cited literature
- **Bailey, D.H. & López de Prado, M. (2014).** "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and Non-Normality." *Journal of Portfolio Management* 40(5), 94-107. (Note: The docstring at `autotuner.py:135` cites *Financial Analysts Journal*; the **correct journal** is *Journal of Portfolio Management* per SSRN abstract_id=2460551.) DOI / SSRN: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551.

### Verification of the four moments
The classic Bailey-López de Prado (2014) Probabilistic Sharpe Ratio / Deflated Sharpe Ratio formula uses:
1. `N` — number of trials/strategies tried → implemented as `T_train = len(validation_dates_purged)` BUT this is the **number of observations in the return series**, not the **number of trials**. **This is the standard convention in the BPdP formula** — `T` denotes the observation count, and the multiplicity correction enters via the cross-trial moments γ3, γ4 (computed across N trials) feeding the denominator. The implementation does compute moments across `n_trials = len(completed_trials)` (line 720) and uses `T = T_train` (observation count) in the `sqrt(T-1)` numerator — this is faithful to Eq. 9 of the paper.
2. `σ_SR` — variance across trial Sharpe ratios → implemented at line 723 as population variance `variance_v = sum((v - mean_v)**2 for v in trial_values) / n_trials`. The DSR formula does not use `σ_SR` *directly* in the denominator — instead, the σ across trials informs the **expected-max-SR** correction implicit in the SR_0 baseline. The implementation passes `SR_0 = 0.0` as a fixed null baseline (line 736), which is the **simpler PSR-style formulation** rather than the full DSR that derives SR_0 from the expected max of N independent Sharpes. `[Medium]` confidence — this is a simplification of the published DSR.
3. `γ3` (skewness of trial-Sharpe distribution) → line 726, population third moment divided by σ³. `[High]`
4. `γ4` (kurtosis) → line 727, population fourth moment divided by σ⁴. `[High]`

### Finding
**ISSUE — Medium.** The denominator structure (`1 - γ3·SR + (γ4-1)/4·SR²`) is faithful to **Bailey & López de Prado 2014 Eq. 9** for the Probabilistic Sharpe Ratio under non-normal returns. The implementation correctly uses all four moments {N (trials, → moments), σ (used to derive γ3, γ4), γ3, γ4}.

However:
- **Citation drift**: docstring says *Financial Analysts Journal*; the canonical 2014 paper is in *Journal of Portfolio Management* 40(5). Some Bailey-de-Prado work appears in *Financial Analysts Journal* (2017 *Real and Synthetic Backtests*) but **not** the DSR paper. **Recommend re-anchoring the docstring citation** at the next sweep.
- **SR_0 simplification**: passing `SR_0 = 0.0` reduces DSR to a PSR-like form. The full DSR replaces SR_0 with the expected maximum Sharpe across N independent random trials — `E[max(SR_n)] = (1-γ_e)·Φ⁻¹(1-1/N) + γ_e·Φ⁻¹(1 - 1/(N·e))`, where γ_e is the Euler-Mascheroni constant. This expected-max correction is the **selection-bias** correction the paper is famous for. Without it, the implementation is essentially using PSR with the trial-cross moments, **not** full DSR. Logged as **Open Question O2-Q1: should the implementation use the full BPdP 2014 expected-max-SR correction for SR_0? Trade-off: more conservative trial selection (preferred for honest OOS) vs. potentially over-deflating when γ3/γ4 are already strongly non-normal.**

`[High]` confidence on the denominator structure (Eq. 9 of the paper).
`[Medium]` confidence on the SR_0 simplification — this is a deliberate documentation+implementation gap. Defensible as v1; pin it down in a follow-up.

### Empirical grade
`[Theoretical]` for the formula. `[Backtest]` for any reported DSR value from the autotuner — but the DSR is a *correction*, not a return claim, so the empirical grading is at the input-Sharpe level.

### Replication status
DSR / PSR is **the** standard backtest-overfit correction in academic quant CV. **Replicated: Yes** across SSRN follow-up papers and `mlfinlab`'s implementation.

### Regime sensitivity
DSR/PSR assumes the cross-trial Sharpe distribution is well-approximated by its first 4 moments — fails when the distribution is bimodal or heavily mixed across regimes. With 500 trials per symphony on a Sortino objective and a Sortino-bounded `1e6` sentinel for zero-downside cases, the distribution can be skewed by sentinel values; this is **not** explicitly trimmed in the implementation. **Open Question O2-Q2: are sentinel-`1e6` Sortinos excluded from the gamma-moment computation?** Looking at `autotuner.py:718-720`, `trial_values = [t.value for t in completed_trials]` includes everything Optuna returned, which could include sentinels. This bleeds into γ3, γ4 estimation.

---

## 4. O5 — Sortino objective replacing the ad-hoc composite

### Implementation
`autotuner.py:91-119`:

```python
def compute_sortino_ratio(returns, target=SORTINO_TARGET_RETURN):
    n = len(returns)
    mean_r = sum(returns) / n
    sum_sq_downside = sum(min(r - target, 0.0) ** 2 for r in returns)
    mean_sq_downside = sum_sq_downside / n
    downside_deviation = math.sqrt(mean_sq_downside)
    if downside_deviation == 0.0:
        return 1e6
    return mean_r / downside_deviation
```

`SORTINO_TARGET_RETURN = 0.0` (line 59). Used as the Optuna objective at `autotuner.py:683`.

### Cited literature
- **Sortino, F. A. & van der Meer, R. (1991).** "Downside Risk." *Journal of Portfolio Management* 17(4), 27-31. DOI: 10.3905/jpm.1991.409343. (Note: the docstring at `autotuner.py:98` and the constant comment at `:58` cite **1994** as the year; the canonical Sortino-Vdr Meer paper is **1991**. There is a 1994 *Journal of Investing* paper by Sortino & Price — "Performance Measurement in a Downside Risk Framework" — which extends the framework. Both are legitimate citations; the **1991** paper is the one that introduces downside deviation as the denominator.)

### Verification of the formula
Sortino-Vdr Meer 1991 defines the downside risk measure as **target semivariance**:

```
DSR² = (1/N) · Σ max(0, T - r_i)²
```

i.e. squared shortfall below target T, averaged over **all N observations** (not just downside ones). The Sortino ratio is `(R̄ - T) / sqrt(DSR²)`.

The implementation:
- Uses `min(r - target, 0.0) ** 2` — equivalent to `max(0, target - r) ** 2` modulo sign. **Correct.**
- Divides by N (line 115) — **population denominator across all observations**, matching Sortino-Vdr Meer. **Correct.**
- Numerator is `mean_r`, not `mean_r - target` — but since `SORTINO_TARGET_RETURN = 0.0`, this is **mathematically identical**. **Correct under the chosen target.**

### Finding
**VALIDATED with citation.** Downside-deviation denominator matches Sortino & van der Meer 1991 ("Downside Risk", *JPM* 17(4), 27-31). The population denominator (divide by N, not N_downside) is faithful to the published formula.

`[High]` confidence.

**ISSUE — Low (citation year drift).** The cited year in the comment block and docstring is **1994**; the canonical paper introducing downside deviation is **Sortino & van der Meer 1991**. Sortino-Price 1994 extends the framework but is not the source for the denominator definition. Recommend re-anchoring the citation at next sweep.

### Empirical grade
`[Theoretical]` (definitional metric). `[Backtest]` for the autotuner's use of it as an objective — but Sortino is a metric, not a return claim.

### Replication status
Sortino is **a** standard practitioner downside-risk measure; replicated across CFA Institute curriculum, Carver 2015, López de Prado 2018. **Replicated: Yes.**

### Regime sensitivity
The `1e6` sentinel when `downside_deviation == 0.0` is **operationally necessary** for Optuna TPE (needs finite values) but is **not** part of the Sortino-Vdr Meer formulation. It creates a regime-dependent artifact: in a sample where every observation exceeds target (e.g., a short pure-uptrend backtest), the objective value is artificially capped at 1e6, which may distort the cross-trial moment estimation in O2. See **Open Question O2-Q2 above** — the two issues are linked.

---

## 5. O6 — Frozen-eval fold (60/20/20 split)

### Implementation
`autotuner.py:79-88`:
```python
TRAIN_RATIO = 0.60
VALIDATION_RATIO = 0.20
FROZEN_EVAL_RATIO = 0.20
```

`autotuner.py:606-646`: history is partitioned `60/20/20` by date index; purge+embargo applied at both fold boundaries. The Optuna objective scores on **validation only** (line 683 `history_validation` parameter). The frozen-eval fold is consumed exactly once post-selection at line 822 (`history_frozen`), producing `frozen_eval_sharpe_value`. Frozen-eval data is **withheld** from every trial callback (verified by tracing: `history_frozen` is not passed into the closure `objective(trial)`).

### Cited literature
The plan's AC-O6 (in the merged plan) and `autotuner.py:547-567` cite **López de Prado 2018, Ch. 7.4 frozen-eval**.

### Verification against primary sources
AFML Ch. 7 covers **purged k-fold CV** (§7.3-7.4) and **the embargo** (§7.4.2). The book's prescription for **honest performance reporting under multiple testing** is more nuanced than a literal "60/20/20 frozen-eval" recipe — the book discusses **combinatorial purged CV** (§7.6) and the **bagging of paths** as alternatives to a single hold-out. **Strict 60/20/20 train/validation/frozen-eval is not a literal recipe in AFML Ch. 7.4** — it is a practical adaptation of the chapter's principle that *the final evaluation set must be consumed exactly once and never touched during selection*.

`[Medium]` confidence on the **exact 60/20/20 split**: defensible as an operational choice — the ratios are not pinned by the book — but the citation should clarify that 60/20/20 is the operator's choice **consistent with** Ch. 7 principles, not **prescribed by** them.

### Finding
**VALIDATED (principle); ISSUE — Low (citation precision on the exact ratio).** The frozen-eval principle (held-out fold consumed exactly once post-selection) **is** faithful to AFML Ch. 7.4 hygiene. The **60/20/20 ratio itself** is an operator choice and is not prescribed by the book. The implementation correctly withholds the frozen fold from every Optuna trial.

**Verification that the frozen fold is genuinely held out:** confirmed by code trace.
1. `objective(trial)` at line 670 closes over `history_validation` only (not `history_frozen`).
2. `study.optimize(...)` at line 697 cannot access `history_frozen`.
3. `frozen_eval_returns` is computed exactly once at line 822 from `history_frozen`, **after** all trials complete and best params are selected.
4. No code path reads `history_frozen` inside the optimization loop. **VALIDATED.**

`[High]` confidence on the held-out invariant.
`[Medium]` confidence on the 60/20/20 ratio choice (operational, not literature-prescribed).

### Empirical grade
`[Theoretical]` for the methodology. `[Backtest]` for any frozen_eval_sharpe value reported.

### Replication status
The "consume the held-out fold once" principle is universal in quant CV. The specific 60/20/20 ratio varies across practitioner usage (60/20/20, 70/15/15, 50/25/25 are all common). **Replicated: Yes (principle); No (specific ratio).**

### Regime sensitivity
Per the docstring at `autotuner.py:561-567`: "After PURGE_DAYS=20 at each boundary, the usable validation and frozen-eval windows are roughly 5-7 days each on a 125-day total history." This is **a real constraint** — a 5-7 day frozen fold is statistically thin for a Sortino estimate. Bailey-de-Prado 2014 effectively requires N ≥ 30 observations for an interpretable Sharpe/Sortino (see also the M1F plan validation `PA-M1F-11`). **The frozen-eval Sortino on a 5-7 day window is high-variance.** This is acknowledged in the docstring as "the cost of honest OOS reporting." Logged as **Open Question O6-Q1: expand history window to ≥ 250 days OR adopt combinatorial purged CV (AFML §7.6) for a higher-power OOS estimate.**

---

## 6. H2 — Multi-trigger priority resolution

### Implementation
`alpha_bot_execution.py:1076-1118`: when multiple trigger conditions evaluate True, the engine resolves a **fixed priority order** (VWAP Breakdown > Take-Profit > VWAP Bleed Cut > Trailing Stop) **before** any side-effect runs. The resolved `reason` and the `also_true` list of co-firing rules are propagated to the execution queue and (via H1) to the telemetry table.

### Cited literature
The plan's AC-H2.1/.2 do not cite any external source for the priority order. The math-engine audit (`docs/research/dashboard/math-engine-audit.md:26-27`) describes the prior state ("labeling decision, not a gating decision") but does not appeal to literature.

### Verification
Searched for a published framework on cascading exit-rule priority resolution in:
- López de Prado 2018, *Advances in Financial Machine Learning* (Ch. 3 Triple-Barrier Method — defines profit-take + stop-loss + time-exit as **independent label boundaries**, not a *priority cascade*; the label is the **first barrier hit**, which is a *temporal* tiebreaker, not a *categorical* priority).
- Carver 2015, *Systematic Trading* — argues against stacked exit rules entirely (Ch. "Stoploss"); does not prescribe a priority ordering because the recommendation is "use one rule."
- Han, Zhou & Zhu 2014 ("Taming Momentum Crashes") — uses a single stop-loss rule, no priority cascade.
- Kaminski & Lo 2014 — single stop-loss rule, no priority cascade.

### Finding
**NOT-COVERED-BY-LITERATURE (operational only).** There is **no published academic framework** for cascading exit-rule priority resolution that the auditor could find. The dominant literature treatment is either (a) a single rule, or (b) the first-barrier-hit *temporal* convention of AFML's Triple-Barrier Method. AlphaBot's priority order (VWAP-Breakdown > TP > VWAP-Bleed > Trailing-Stop) is an **operational policy choice** — defensible on the operator's safety-first logic ("VWAP-Breakdown is the most-information-rich exit signal; capitulate first when it fires") but not literature-grounded.

`[Folklore — high adoption / low evidence]` — concurrent trigger resolution is a frequent practitioner concern but rarely formalised.

**This is not a deficiency** — the audit checklist explicitly admits this as a possible outcome. Logged: the operator should understand H2's priority is a **policy** ranking, not a **theorem**. The implementation correctly resolves before side-effects (validated by trace) and records the co-firing candidates (correct closure of the labeling-vs-gating gap from `math-engine-audit.md:26-27`).

### Empirical grade
`[Theoretical]` (priority is a definition, not a return claim).

### Replication status
**Not replicated** because not published. Operational pattern, internal to the project.

### Regime sensitivity
A priority ordering is fragile when one signal is dominant — e.g., if VWAP-Breakdown fires 80% of the time, then TP / Bleed / Trailing-Stop almost never display as the *resolved* reason, which can mask their independent contribution. H1's `also_true` recording is the mitigation: it preserves the co-firing data for post-mortem analysis. Defensible.

---

## 7. H3 — Monte Carlo deterministic seeding

### Implementation
`math_engine.py:479-485`:
```python
def derive_cycle_mc_seed(cycle_id: str) -> int:
    return int(hashlib.sha256(cycle_id.encode()).hexdigest(), 16) % MC_SEED_MODULUS
```
Where `MC_SEED_MODULUS = 2**31` (line 50). `run_monte_carlo` accepts a `seed` kwarg (line 488) and instantiates an **isolated** `np.random.default_rng(seed)` (line 558) — explicitly avoiding the numpy global RNG.

### Cited literature on numerical reproducibility seed derivation
- **NIST SP 800-90A Rev. 1 (2015)**, *Recommendation for Random Number Generation Using Deterministic Random Bit Generators*. NIST CSRC. — SHA-256-based seed derivation is the **cryptographic best-practice** when deriving a deterministic seed from a string identifier. Collision resistance: SHA-256 is preimage- and collision-resistant at the 2^128 level for practical purposes (NIST FIPS 180-4). DOI / URL: https://doi.org/10.6028/NIST.SP.800-90Ar1.
- **NumPy SeedSequence docs** (numpy.org, "Random sampling — NumPy v1.x Manual"). `np.random.default_rng()` accepts arbitrary int seeds and internally uses `SeedSequence` to spawn distinct streams — accepting a SHA-256-derived integer is the **standard** way to seed a parallel/reproducible numpy RNG. URL: https://numpy.org/doc/stable/reference/random/index.html.
- **Matsumoto & Nishimura (1998)**, "Mersenne Twister: A 623-dimensionally equidistributed uniform pseudo-random number generator." *ACM TOMACS* 8(1), 3-30. — Original MT19937. Numpy's `default_rng()` defaults to PCG64, which is also acceptable for Monte Carlo work (period 2^128; better statistical properties than MT19937). The seed-derivation pattern is independent of the PRNG choice.

### Verification of collision resistance
The implementation truncates SHA-256 to `2**31` (31 bits). On a daily cycle cadence (cycle_id format `YYYYMMDD_HHMM`), the unique cycle_ids per year are ~252 trading days × 390 minutes = ~98,280 cycles. The **birthday paradox** for a 31-bit space gives expected collision after ~√(2^31) ≈ 46,340 draws — i.e., within a **single year** the expected number of seed collisions is ~1. **This is not adequate collision resistance** if the operator requires zero seed collisions across the audit horizon.

For comparison: `MC_SEED_MODULUS = 2**32` would give expected first collision at ~65,536 draws (still ~1 per year); `2**63` (the natural numpy seed-space upper bound) would give expected first collision at ~3.04e9 draws (far beyond any audit horizon).

### Finding
**VALIDATED (pattern); ISSUE — Low/Med (modulus sizing).**

- The **SHA-256 derivation pattern** is standard in numerical reproducibility / cryptographic seed derivation. `[High]` confidence — NIST + numpy docs corroborate.
- The **31-bit truncation** (`2**31`) is a residual cap from numpy's pre-`default_rng()` era (the legacy `np.random.seed()` accepts up to `2**32 - 1`). `np.random.default_rng()` accepts arbitrary 64-bit (and larger) seeds via `SeedSequence`. **The 31-bit cap is unnecessary** and creates a measurable collision rate (~1 collision/year on intraday cycles). 

**Open Question H3-Q1: should `MC_SEED_MODULUS` be raised to `2**63 - 1` (or removed entirely; pass the full SHA-256 int to `default_rng` which accepts arbitrary-size ints)?** Trade-off: zero downside; trivial change; the only reason to keep `2**31` is backward-compat with the legacy `np.random.seed()` API, which is **not** used here.

The **isolation** via `np.random.default_rng(seed)` (line 558) — explicitly avoiding mutating the numpy global RNG — is correct and matches the numpy-recommended pattern for reproducible parallel work. `[High]` confidence.

### Empirical grade
`[Theoretical]` — reproducibility is a determinism property, not a return claim.

### Replication status
SHA-256 → int → seed is the **standard** reproducibility pattern. **Replicated: Yes.**

### Regime sensitivity
Not regime-sensitive. Determinism is invariant across data regimes.

---

## 8. V1 — Calibration sweep (PARA + VWAP after methodology fixes ship)

### Implementation
`tests/calibration/test_v1_calibration_sweep.py` exists; the production `run_calibration_sweep` function is expected to live in `autotuner.py`. The plan (AC-V1.1) requires the sweep to run **after O1 + O2 + O3 + O5 land**, using the corrected E1 velocity baseline.

### Methodology-stack verification
V1 is a *consumer* of the methodology stack established by O1 (purge+embargo), O2 (DSR), O3 (study naming), O5 (Sortino objective), O6 (frozen-eval). Per the existing autotuner code paths:
- `autotuner.py:670-685` — Optuna objective consumes O1's purge-reduced `history_validation` and O5's `compute_sortino_ratio`. **Inherits the methodology stack correctly.**
- `autotuner.py:711-763` — O2 DSR post-selection re-ranking. **Applied.**
- `autotuner.py:696` — O3 timestamped study names. **Applied.**
- `autotuner.py:822-823` — O6 frozen-eval consumed once. **Applied.**

V1's sweep does not introduce a new objective or split; it reuses the autotuner's existing infrastructure, which means the O1-O6 stack flows through to V1 automatically.

### Cited literature
None unique to V1 — it is a tuning workstream over the methodology stack. The plan cites the same Bailey & López de Prado / Sortino / AFML references via inheritance.

### Finding
**VALIDATED (no new methodology gap introduced by V1 itself).** V1 inherits O1/O2/O5/O6 correctly via the existing autotuner code paths.

**Single methodology concern carried forward from M1F plan-validation `PA-M1F-11`:** the M1F plan calls out a **three-state fold-sufficiency check** required to handle the bootstrap period when shadow_history is sparse. V1 will face the same statistical-power concern on the frozen-eval fold (5-7 effective days per O6's purge-reduced window). The M1F PA-M1F-11 framing (N≥30 per Bailey/de-Prado) is the right standard. **Open Question V1-Q1: does V1's reporting layer surface the frozen-eval N when below the Bailey-de-Prado N≥30 threshold?** If not, an operator reading the V1 sweep report may interpret a high frozen-eval Sortino on N=5 as a robust signal when it is actually a high-variance one.

`[Medium]` confidence — the inheritance is verified; the sample-size disclosure is the only outstanding methodology gap.

### Empirical grade
`[Backtest]` for the sweep itself. **Live evidence** of V1's tuned parameters is gated by the plan's AC-V1.4 (post-deploy verification via H1 telemetry).

### Replication status
Inherits replication status from O1/O2/O5/O6.

### Regime sensitivity
Inherits regime sensitivity from O1 (embargo size) and O6 (frozen-eval fold thinness). No V1-specific regime sensitivity beyond those.

---

## 9. V2 — Open-window time gate (15-min default)

### Implementation
`math_engine.py:454-476`:
```python
VWAP_OPEN_WINDOW_GRACE_MINUTES_DEFAULT = 15

def is_in_open_window_grace(current_et, execution_start_hhmm, grace_minutes) -> bool:
    ...
    return exec_start_naive <= current_time_naive < grace_end_naive
```
The grace window suppresses VWAP-Breakdown and VWAP-Bleed-Cut signals only — TP and Trailing-Stop continue to fire (per plan AC-V2.1).

### Cited literature on session-open volatility windows
- **Wood, McInish & Ord (1985)** "An Investigation of Transactions Data for NYSE Stocks." *Journal of Finance* 40(3), 723-739. DOI: 10.1111/j.1540-6261.1985.tb04996.x. — Documents U-shape: **first 30 minutes have the highest realised volatility** of the session.
- **Andersen & Bollerslev (1997)** "Intraday periodicity and volatility persistence in financial markets." *Journal of Empirical Finance* 4(2-3), 115-158. DOI: 10.1016/S0927-5398(97)00004-2. — Replicates the U-shape with Flexible Fourier Form on DM/USD and S&P 500 futures 1986-1996. **Confirms: 09:30-10:00 ET is the high-vol window**.
- **Admati & Pfleiderer (1988)** "A Theory of Intraday Patterns: Volume and Price Variability." *Review of Financial Studies* 1(1), 3-40. — Mechanism: liquidity-trader clustering at open and close.
- **ICI (2012)** "Key Data Undercut Critics' Arguments on ETFs and Intraday Volatility." (Practitioner research; corroborates the academic literature on the first-30-min vol peak.)

### Verification of intent
The audit checklist asks: "**V2 suppresses VWAP in that window. Verify this is consistent with intent (suppress false positives from morning chop, NOT suppress real signals).**"

The U-shape literature shows **morning IS the high-volatility window**. A VWAP-cross signal in the first 15 minutes after `EXECUTION_START_TIME` is happening **inside** the highest-volatility part of the day. Two interpretations:

1. **Operator intent (per plan AC-V2.1)**: morning vol is mostly *noise* relative to the symphony's medium-frequency trend signal. Suppressing VWAP signals in this window is a **noise filter**.
2. **U-shape literature interpretation**: morning vol is **real** signal — it reflects price discovery from overnight news, opening auction dynamics, gap-and-fade behaviour. Suppressing VWAP signals in this window risks **missing real downside moves**.

These two interpretations are in **direct tension**. The merged plan's framing (AC-V2.1) chooses interpretation (1); the U-shape literature supports interpretation (2). **Neither is definitively wrong** — the trade-off depends on whether the operator believes the noise-to-signal ratio in the open window justifies the suppression cost.

### Finding
**ISSUE — Medium (methodology vs literature tension).** V2's 15-min suppression is **internally consistent with the plan's stated intent** (suppress false positives from morning chop), but the U-shape literature (Wood-McInish-Ord 1985, Andersen-Bollerslev 1997) explicitly characterises 09:30-10:00 ET as the **highest realised-volatility window of the session** — meaning VWAP-Breakdown signals fired in that window are **statistically the most likely to be real** in terms of volatility magnitude, even though the **signal-to-noise ratio** is the operator's actual concern.

The operator should be aware: **the 15-min default is a noise-filter policy, not a literature-grounded threshold.** The U-shape literature does not provide an exact "correct" grace-window length; it does provide a **lower bound argument** that 15 minutes is the minimum coherent open-volatility window per Wood-McInish-Ord's 1971-1972 NYSE study and `[High]` corroboration from Andersen-Bollerslev 1997. A grace window of 30 minutes (matching Wood-McInish-Ord's "first 30 min" observation) would be **more literature-consistent**; 15 minutes is **defensibly tight**.

**Open Question V2-Q1: should `VWAP_OPEN_WINDOW_GRACE_MINUTES` be re-tuned via H1's trigger-attribution telemetry once 30+ days of post-V2 live data exists? The literature-grounded prior is 30 min; the plan's 15 min should be validated empirically.**

`[Medium]` confidence — the tension is real but the operator's noise-filter intent is also defensible.

### Empirical grade
`[Theoretical]` for the U-shape grounding. The 15-min specific value is `[Folklore — operator-set]` until H1 telemetry produces an empirical retune.

### Replication status
The U-shape itself is **replicated** across 40+ years of intraday-vol literature (Wood-McInish-Ord 1985; Andersen-Bollerslev 1997; Heston-Korajczyk-Sadka 2010; Hong-Wang 2000). **Replicated: Yes.** The specific 15-min vs 30-min grace-window choice is not replicated because not published.

### Regime sensitivity
On FOMC / CPI / earnings-release days the morning window is dominated by the announcement spike, not by ordinary U-shape behavior — V2's noise filter is more or less appropriate depending on whether the operator wants to **react to** or **wait through** the announcement. The U-shape literature explicitly excludes announcement days; V2 does not currently distinguish.

---

## 10. V3 — Fleet-correlation circuit breaker

### Implementation
`alpha_bot_execution.py:298-322` — `detect_fleet_correlation` is a pure function returning `(tripped, dominant_reason)`. The breaker is **observational only** (AC-V3.2) — sets `bot_state["fleet_correlation_alert"]` and surfaces a dashboard banner; does **not** alter trigger dispatch. Auto-clear after `FLEET_CORRELATION_CLEAR_MINUTES` (line 377).

### Cited literature on portfolio-level correlation alerts
Searched for published frameworks on multi-strategy correlation circuit breakers:

- **CFTC / SEC Joint Advisory Committee (2010)**, *Findings Regarding the Market Events of May 6, 2010* ("Flash Crash Report"). https://www.sec.gov/news/studies/2010/marketevents-report.pdf. Discusses systemic-correlation risk during the Flash Crash but does **not** prescribe an exposure-cap or breaker rule.
- **Khandani & Lo (2007/2011)**, "What Happened to the Quants in August 2007?" *Journal of Portfolio Management* / *Journal of Investment Management* 5(4), 5-54. SSRN: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1015987. Documents simultaneous deleveraging across multiple quant strategies; identifies correlation alerts as a *post-hoc* signal of crowding, not a prescriptive breaker design. `[High]`.
- **Bouchaud & Potters (2003)**, *Theory of Financial Risk and Derivative Pricing*. Cambridge UP. Ch. 8-9 — covers correlation-spike detection via eigenvalue decomposition of the return correlation matrix, but at portfolio-construction time, not as a live-trading breaker.
- **NYSE Rule 80B and Reg SCI (post-2012)** — define market-wide circuit breakers triggered by aggregate-index moves, not by trigger-rule correlation. `[High]` for the rule existence; not directly applicable.

No published academic framework prescribes a "X% of active strategies firing the same exit reason within T minutes" detection rule. The closest practitioner literature is the post-Flash-Crash discussion of cross-strategy correlation as a **systemic-risk indicator**, not a per-portfolio breaker design.

### Finding
**NOT-COVERED-BY-LITERATURE (operational only).** V3 is a **defensible operational pattern** drawn from the post-Flash-Crash systemic-correlation discussion (Khandani-Lo 2007/2011 closest cousin), but the specific algorithm ("> pct_threshold of active symphonies fire same reason within window") is **not** literature-grounded. The audit panel's verdict ("today's 11-cascade was a real signal; engine must not second-guess") is the operator's defensible policy choice.

V3's **observational-only** design (AC-V3.2 — does not gate triggers) is exactly the right risk posture **given the literature gap**: the breaker surfaces a signal that the operator can act on, rather than imposing a literature-unsupported gating decision on live trades.

`[Folklore — high adoption / low evidence]` for the specific algorithm; `[Medium]` for the systemic-correlation framing (Khandani-Lo).

### Empirical grade
`[Theoretical]` for the systemic-correlation mechanism. `[Backtest]` is **not yet possible** because no historical cascade has been telemetry-recorded (H1 just shipped).

### Replication status
The systemic-correlation framing (Khandani-Lo, Flash Crash Report) is replicated. **The specific breaker design is not.**

### Regime sensitivity
The breaker is regime-sensitive by construction: in a strong-trend regime where most symphonies are TP-firing, the breaker trips on legitimate alpha capture and surfaces a false-positive alert. The observational-only posture absorbs this risk safely.

---

## 11. I1 + I2 verdicts — cross-check

### I1 — log-time squeeze
**Existing investigation:** `docs/research/risk/log-time-squeeze-investigation.md`.

**Verdict:** `log10(1 + 9t)` decay has **no identifiable precedent** in the academic intraday-vol literature surveyed (Andersen-Bollerslev 1997; Heston-Korajczyk-Sadka 2010; Wood-McInish-Ord 1985; Admati-Pfleiderer 1988; Hong-Wang 2000) or in Carver 2015 (the principal practitioner reference). The closest practitioner cousins (TradersPost-style linear decay, EWMA half-life) use **different curve families**.

**Recommended follow-up (per the investigation):** A 4-way A/B walk-forward backtest comparing log / linear / convex / exponential curves on 3 representative symphonies.

### Audit verification
The cited literature is correctly reviewed in the investigation document:
- Wood-McInish-Ord 1985, *J. Finance* 40(3), DOI 10.1111/j.1540-6261.1985.tb04996.x — `[High]`, verified.
- Admati-Pfleiderer 1988, *Rev. Financial Studies* 1(1), 3-40 — `[High]`, verified.
- Andersen-Bollerslev 1997, *J. Empirical Finance* 4(2-3), DOI 10.1016/S0927-5398(97)00004-2 — `[High]`, verified.
- Hong-Wang 2000, *J. Finance* 55(1), 297-354 — `[High]`, verified.
- Almgren-Chriss 2000, *J. Risk* 3(2), 5-39 — `[High]`, verified.
- Heston-Korajczyk-Sadka 2010, *J. Finance* 65(4) — `[High]`, verified.
- Carver 2015, *Systematic Trading*, Harriman House — `[Expert]`, verified.

The verdict that no peer-reviewed source uses `log10(1 + 9t)` for intraday risk overlays is **independently corroborated** by this audit's search.

**Finding for I1: VALIDATED.** The investigation's literature survey and verdict ("unprecedented; A/B test recommended") are faithful to the cited primary sources. The recommended 4-way A/B (log / linear / `t²` / `1 - exp(-3t)`) is methodologically sound — it varies exactly one parameter (the curve family) while holding all other math-engine constants fixed.

### I2 — stop-compounding
**Existing investigation:** `docs/research/risk/stop-compounding-investigation.md`.

**Verdict:** The audit panel's "8× compounding" framing is incorrect; the actual compounded tightening is **~3.0× regime-invariant** across the trading day (PARA squeeze × time-squeeze decay, with breakeven OR-merged into the same multiplier branch). The bite is **concentrated in the final 30 minutes** of the session (15:30 - 16:00 ET) where the curve floors at `dyn_mult=0.5` and `dyn_min_stop=0.15`. **Material, but bounded.** Recommended remediation option (a) — cap the compounded tightness at a literature-grounded floor (e.g., `COMPOUND_FLOOR_FRAC`) — preferred over decoupling the three mechanisms.

### Audit verification
Cited literature:
- Kaminski & Lo 2014, "When do stop-loss rules stop losses?" *J. Financial Markets* 18, 234-254. DOI: 10.1016/j.finmar.2013.07.001. SSRN abstract_id=968338 — `[High]`, verified. The **"stops subtract value under random walk"** result is correctly applied: under a random-walk DGP with positive risk premium, the stopping premium Δμ is **monotonically non-positive**; AlphaBot's late-day compounding amplifies this drag in the 15:30-16:00 window. **The compounding-aggravates-Kaminski-Lo argument is faithful.**
- Han, Zhou & Zhu 2014, "Taming Momentum Crashes: A Simple Stop-Loss Strategy." SSRN abstract_id=2407199 — `[Backtest only, single source]`, correctly flagged as offsetting the Kaminski-Lo drag for momentum-tilted symphonies.
- López de Prado 2018 Ch. 3 — Triple-Barrier Method — `[Expert, widely-adopted]`, correctly framed as the *canonical* alternative to compounded stops (PT, SL, time-exit as **independent** boundaries).
- Carver 2015 — `[Practitioner, single source]`, correctly framed as a critic of stacked stop rules.

**Finding for I2: VALIDATED.** The empirical compounding measurement (`docs/research/risk/scripts/i2_compounding_sim.py`) is a **reproducible side-calculation** under flat-random-walk price assumption. The 3.0× regime-invariant tightening figure is correctly derived; the late-day concentration is correctly localised to 15:30-16:00 ET. The Kaminski-Lo application is faithful. The Han-Zhou-Zhu offset is correctly flagged as a **partial** mitigation. **The recommended `COMPOUND_FLOOR_FRAC` cap is well-grounded** in the literature: Carver 2015 explicitly criticises stacked rules; capping the compounded multiplier preserves the existing math-layer formulation while restoring a literature-defensible floor on stop tightness.

### Cross-check between I1 and I2
The two investigations are **consistent**: both identify the same problematic regime (the final 30 minutes of the session) and both recommend a **shape change** rather than a parameter retune. I1 targets the curve family; I2 targets the compounded multiplier floor. The two fixes are **independent** (I1 changes the curve family; I2 caps the floor) and can be tested separately or jointly via the 4-way A/B framework I1 proposes.

`[High]` confidence — the audits' cross-citation hygiene is clean; no contradictions found.

---

## 12. Pre-existing methodology gaps not covered by the workstream set

The auditor flags the following methodological gaps in the codebase that **are not addressed by the engine-correctness-remediation plan** and are not blocked by any open workstream. Each is reframed as an option + trade-off, not a recommendation.

### Gap 12.1 — Bootstrap-period statistical-power disclosure
The plan's O6 frozen-eval fold (5-7 effective days after purge+embargo on a 125-day history) **fails the Bailey-de-Prado N ≥ 30 sample-size standard** for an interpretable Sortino/Sharpe. The implementation correctly *computes* `frozen_eval_sharpe_value` but **does not surface N** in the autotune report. An operator reading a high frozen-eval Sortino on N=5 may misinterpret high-variance noise as a robust signal. The M1F plan validation `PA-M1F-11` proposes a three-state check (insufficient / provisional / overfit-confirmed) at N=30; **the autotuner does not implement this check**. Logged as **Open Question 12-Q1**.

### Gap 12.2 — Sortino sentinel pollution of DSR moments
The `1e6` sentinel returned by `compute_sortino_ratio` (line 118) when `downside_deviation == 0.0` is passed into the DSR cross-trial moments γ3, γ4 calculation (`autotuner.py:719`) **without trimming**. On trials where every observation exceeds target, the sentinel inflates γ3 (extreme positive skew) and γ4 (extreme kurtosis), which can **systematically over-deflate** the DSR of legitimate non-sentinel trials. The fix is a one-line trim — `trial_values = [v for v in trial_values if v < 1e5]` — but it is **not** in scope of any current workstream. Logged as **Open Question 12-Q2**.

### Gap 12.3 — Embargo size empirical calibration
`EMBARGO_DAYS = 1` is at the **lower end** of the López de Prado 2018 §7.4.2 prescription (~1% of T ≈ 1.25 days for T=125). No empirical autocorrelation estimate of cycle-to-cycle objective values has been produced; the embargo is a literature-defensible default but not an empirically-tuned value. **H1 telemetry provides the raw material for this estimate** (multi-day cycle-objective autocorrelation can be computed from `exit_triggers` + `autotune_runs` joins) but the join is not currently performed. Logged as **Open Question 12-Q3**.

### Gap 12.4 — DSR `SR_0` simplification
The implementation uses `SR_0 = 0.0` (a PSR-style null) rather than the BPdP 2014 expected-max-SR correction. This simplifies the formula but **does not correct for the expected-maximum-Sharpe selection bias across N trials** — the very effect DSR was named for. The full correction (`SR_0 = (1-γ_e)·Φ⁻¹(1-1/N) + γ_e·Φ⁻¹(1 - 1/(N·e))`) is a 3-line addition. Logged as **Open Question 12-Q4**.

### Gap 12.5 — Provenance of `log10(1+9t)` and `DECAY_CURVE_SCALAR=9`
Per I1 (open question section), the **origin** of the `log10(1+9t)` curve and the specific `DECAY_CURVE_SCALAR=9` value is not in any commit message, design document, or comment available in the git history. This is a **provenance gap** — independent of whether the curve is the right shape. The fallback rationale comment proposed by I1 is a defensible exit if the A/B test produces no improvement, but it does not retroactively establish the original choice. Logged as **Open Question 12-Q5** (acknowledged in I1; restated here for completeness).

### Gap 12.6 — H3 modulus sizing on intraday-cycle space
Per Section 7 above: `MC_SEED_MODULUS = 2**31` admits ~1 expected collision per year of intraday cycles. **Trivial fix** (raise to `2**63 - 1`); not in scope of any workstream. Logged as **Open Question 12-Q6**.

---

## 13. Workstream summary table

| WS | Literature backing | Finding |
|----|---|---|
| E2 | Glynn & Iglehart 1995 (canonical); Leung & Zhang 2019 (modern formalism) | VALIDATED implementation; ISSUE-Low citation drift (Fu & Zhang 2010 → should be Glynn & Iglehart 1995) |
| O1 | López de Prado 2018 Ch. 7.4 | VALIDATED (purge by max-feature-lookback); embargo size defensible but not empirically pinned |
| O2 | Bailey & López de Prado 2014 | VALIDATED denominator (Eq. 9); ISSUE-Med journal-name drift; ISSUE-Med SR_0 simplification (PSR-style not full DSR) |
| O5 | Sortino & van der Meer 1991 | VALIDATED denominator; ISSUE-Low year drift (1994 → 1991) |
| O6 | López de Prado 2018 Ch. 7.4 (principle) | VALIDATED (frozen-fold-held-out invariant); ISSUE-Low (60/20/20 ratio is operational, not prescribed) |
| H2 | None published | NOT-COVERED-BY-LITERATURE (operational policy; defensible) |
| H3 | NIST SP 800-90A; numpy SeedSequence | VALIDATED pattern; ISSUE-Low modulus sizing (`2**31` admits 1 collision/year) |
| V1 | Inherits O1/O2/O5/O6 stack | VALIDATED inheritance; ISSUE-Low (N-disclosure on frozen-eval) |
| V2 | Wood-McInish-Ord 1985; Andersen-Bollerslev 1997; Admati-Pfleiderer 1988 | ISSUE-Med (15-min suppression vs U-shape literature tension; intent is internally consistent but not literature-grounded) |
| V3 | Khandani & Lo 2007/2011 (closest cousin) | NOT-COVERED-BY-LITERATURE (operational; observational-only design absorbs the literature gap safely) |
| I1 | Surveyed — log10(1+9t) has no precedent | VALIDATED verdict (unprecedented; A/B test well-grounded) |
| I2 | Kaminski & Lo 2014; Han et al. 2014; AFML Ch. 3; Carver 2015 | VALIDATED verdict (material but bounded; COMPOUND_FLOOR_FRAC cap is literature-grounded) |

---

## 14. Open questions (logged for follow-up; not blocking)

| ID | Question |
|---|---|
| O1-Q1 | Re-check `EMBARGO_DAYS=1` once H1 produces a measurable cycle-to-cycle autocorrelation estimate. |
| O2-Q1 | Should DSR use the full BPdP 2014 expected-max-SR correction for SR_0, or retain the PSR-style `SR_0 = 0.0`? |
| O2-Q2 | Should sentinel-`1e6` Sortinos be excluded from γ3/γ4 moment computation? |
| O6-Q1 | Expand history window to ≥ 250 days OR adopt combinatorial purged CV (AFML §7.6) to raise frozen-fold statistical power? |
| H3-Q1 | Raise `MC_SEED_MODULUS` to `2**63 - 1` (or remove the modulus entirely)? |
| V1-Q1 | Does V1's reporting layer surface frozen-eval N when below Bailey-de-Prado N≥30 threshold? |
| V2-Q1 | Re-tune `VWAP_OPEN_WINDOW_GRACE_MINUTES` against H1 telemetry after 30+ days; literature-grounded prior is 30 min. |
| 12-Q1 | Add Bailey-de-Prado three-state sample-size check to the autotuner report. |
| 12-Q2 | Trim Sortino-sentinels from DSR moment computation. |
| 12-Q3 | Embargo size empirical calibration via H1 telemetry. |
| 12-Q4 | DSR SR_0 expected-max-SR correction. |
| 12-Q5 | `log10(1+9t)` original provenance (acknowledged in I1). |
| 12-Q6 | H3 modulus sizing. |

---

## 15. Final brief (<200 words)

**(a) Workstreams with strong literature backing.** E2 (Glynn-Iglehart 1995 — but currently misattributed to Fu & Zhang 2010); O1 (López de Prado 2018 Ch. 7.4, faithful); O5 (Sortino & van der Meer 1991, faithful denominator); O6 (held-out principle is faithful; the exact 60/20/20 split is operator choice); I1 (literature survey thorough; verdict that `log10(1+9t)` is unprecedented is independently corroborated); I2 (Kaminski-Lo 2014 application is faithful; `COMPOUND_FLOOR_FRAC` cap is literature-grounded via Carver 2015 and AFML Ch. 3).

**(b) Issues found.** E2 citation drift (Fu & Zhang 2010 → Glynn & Iglehart 1995); O2 journal-name drift (Financial Analysts Journal → Journal of Portfolio Management) and SR_0 simplification to PSR-style; O5 year drift (1994 → 1991); H3 modulus sizing (2^31 admits 1 collision/year on intraday cycles); V2 tension between operator noise-filter intent and U-shape literature.

**(c) Methodology gaps remaining.** Bootstrap-period N-disclosure on frozen-eval; Sortino-sentinel pollution of DSR moments; embargo empirical calibration; DSR expected-max-SR correction; provenance of `log10(1+9t)`; H3 modulus.

**(d) Workstreams not covered by literature (operational policies, correctly identified as such).** H2 (priority cascade); V3 (fleet-correlation breaker). Both are observational/policy choices; defensible designs given the literature gap.

---

## 16. Auditor footnotes

- This audit is **read-only**. No code modified. Recommended follow-ups (e.g., re-anchoring the E2 citation, raising `MC_SEED_MODULUS`, trimming Sortino sentinels) are surfaced as **Open Questions**, not directives.
- Per researcher.md charter, the audit does **not** commit or push. The PM is the dispatcher of any follow-up workstream that addresses these open questions.
- All cited papers verified for existence (Semantic Scholar / SSRN / journal landing pages) within this audit session. Specific page numbers for AFML Ch. 7.4 not quoted because the book is paywalled; the citation is anchored to the chapter and section title only.

---

## References (consolidated)

1. Glynn, P. W. & Iglehart, D. L. (1995). "Trading Securities Using Trailing Stops." *Management Science* 41(6), 1096-1106. DOI: 10.1287/mnsc.41.6.1096. https://pubsonline.informs.org/doi/10.1287/mnsc.41.6.1096
2. Fu, J. & Zhang, J. (2010). "Is the trailing-stop strategy always good for stock trading?" Semantic Scholar: https://www.semanticscholar.org/paper/Is-the-trailing-stop-strategy-always-good-for-stock-Fu-Zhang/d749161b35b4690469e41e7386db1bc7b9401ee1
3. Leung, T. & Zhang, H. (2019). "Optimal Trading with a Trailing Stop." *Applied Mathematics & Optimization*. DOI: 10.1007/s00245-019-09559-0. https://link.springer.com/article/10.1007/s00245-019-09559-0
4. López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley. ISBN 978-1119482086. Ch. 3 (Triple-Barrier Method); Ch. 7 (Cross-Validation in Finance, §7.4 Purging and Embargo).
5. Bailey, D. H. & López de Prado, M. (2014). "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and Non-Normality." *Journal of Portfolio Management* 40(5), 94-107. SSRN: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
6. Sortino, F. A. & van der Meer, R. (1991). "Downside Risk." *Journal of Portfolio Management* 17(4), 27-31. DOI: 10.3905/jpm.1991.409343.
7. Wood, R. A., McInish, T. H. & Ord, J. K. (1985). "An Investigation of Transactions Data for NYSE Stocks." *Journal of Finance* 40(3), 723-739. DOI: 10.1111/j.1540-6261.1985.tb04996.x. https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.1985.tb04996.x
8. Admati, A. R. & Pfleiderer, P. (1988). "A Theory of Intraday Patterns: Volume and Price Variability." *Review of Financial Studies* 1(1), 3-40. https://academic.oup.com/rfs/article-abstract/1/1/3/1601212
9. Andersen, T. G. & Bollerslev, T. (1997). "Intraday periodicity and volatility persistence in financial markets." *Journal of Empirical Finance* 4(2-3), 115-158. DOI: 10.1016/S0927-5398(97)00004-2. https://www.sciencedirect.com/science/article/abs/pii/S0927539897000042
10. Hong, H. & Wang, J. (2000). "Trading and Returns under Periodic Market Closures." *Journal of Finance* 55(1), 297-354. http://web.mit.edu/wangj/www/pap/HongWang00.pdf
11. Heston, S. L., Korajczyk, R. A., & Sadka, R. (2010). "Intra-Day Patterns in the Cross-Section of Stock Returns." *Journal of Finance* 65(4), 1369-1407. https://www.bauer.uh.edu/departments/finance/documents/Heston-Korajczyk-Sadka-jf-2010-01-07.pdf
12. Almgren, R. & Chriss, N. (2000). "Optimal Execution of Portfolio Transactions." *Journal of Risk* 3(2), 5-39. https://www.smallake.kr/wp-content/uploads/2016/03/optliq.pdf
13. Carver, R. (2015). *Systematic Trading: A unique new method for designing trading and investing systems.* Harriman House. ISBN 978-0857194459.
14. Kaminski, K. M. & Lo, A. W. (2014). "When do stop-loss rules stop losses?" *Journal of Financial Markets* 18, 234-254. DOI: 10.1016/j.finmar.2013.07.001. SSRN: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=968338
15. Han, Y., Zhou, G. & Zhu, Y. (2014). "Taming Momentum Crashes: A Simple Stop-Loss Strategy." SSRN: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2407199
16. Khandani, A. E. & Lo, A. W. (2011). "What Happened to the Quants in August 2007? Evidence from Factors and Transactions Data." *Journal of Investment Management* 5(4), 5-54. SSRN: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1015987
17. CFTC-SEC Joint Advisory Committee (2010). *Findings Regarding the Market Events of May 6, 2010* (Flash Crash Report). https://www.sec.gov/news/studies/2010/marketevents-report.pdf
18. NIST SP 800-90A Rev. 1 (2015). *Recommendation for Random Number Generation Using Deterministic Random Bit Generators*. DOI: 10.6028/NIST.SP.800-90Ar1.
19. Investment Company Institute (2012). "Key Data Undercut Critics' Arguments on ETFs and Intraday Volatility." https://www.ici.org/viewpoints/view_12_etfs_intraday
