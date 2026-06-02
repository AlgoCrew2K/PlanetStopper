<!-- ARCHIVED from audit/comprehensive-soundness @ 848b492, original date 2026-05-30. Optimization-methodology findings: OPT-INVALID-1 (H-1) confirmed here; fixed in H-1 merge (memory/project_adaptive_exit_direction.md); CPCV finding fed DE-WF-002 (DECISIONS.md). -->
# Pillar 2 — Optimization Methodology Validity (autotuner.py walk-forward)

**Auditor:** optmethod-auditor (audit-soundness team)
**Date:** 2026-05-30
**Worktree HEAD:** 8586ab2
**Scope:** Statistical validity of the autotuner.py walk-forward optimization — fold construction, BHY overfitting haircut, CRRA-EU t-stat objective, NN1 spec-freeze. Per task #2.
**Evidence standard:** file:line for code (paths relative to the shared worktree `autotuner.py`); cited sources for methodology claims.

---

## Headline verdict

**The tuning stack is structurally underpowered for per-symphony parameter selection at the 125-day data budget, and the code knows it and says so.** The walk-forward *machinery* is mostly correct in form (genuine purge+embargo, a correctly-implemented BHY selection-bias haircut, a structurally-enforced spec-freeze gate). But the *selection signal it feeds that machinery* is a t-statistic computed on ~4 daily observations, which is below the threshold where the normal/t sampling-distribution approximation is defensible. No amount of selection-bias correction repairs a 4-sample per-trial estimate. **In addition, I found one concrete code defect (OPT-INVALID-1) that means the CRRA-EU branch's haircut significance test is computed with the WRONG t-statistic** — the exact H-6 category error the surrounding code claims to have fixed.

Per-item verdicts:

| Item | Question | Verdict |
|------|----------|---------|
| (a) | Is a ~4-day validation fold meaningful for selection? | **INVALID** (fitting noise) |
| (b) | Does the BHY haircut correct selection bias, and does it admit it can't fix thin T? | **VALID** (haircut is correct + honestly scoped) — but see OPT-INVALID-1 |
| (c) | Is the 125-day window representative across regimes? | **MARGINAL→INVALID** (single path, ~6 months, 1 fold) |
| (d) | Is the CRRA-EU t-stat a sound selection criterion at this sample size? | **INVALID at T≈4** (objective is sound in form; sample size is not) |
| (e) | Does NN1 spec-freeze prevent circular/look-ahead provenance? | **VALID** (structurally enforced, default-deny) |

---

## (a) The ~4-day validation fold — INVALID for selection

**Code confirms the vision's claim exactly.** Fold construction at `autotuner.py:1779-1802`:
- `val_start_idx = int(125 * 0.60) = 75`; `frozen_start_idx = int(125 * 0.80) = 100` (`:1779-1780`).
- `raw_val_dates = sorted_dates[75:100]` → **25 raw validation days** (`:1786`).
- The Optuna objective does NOT see the raw 25. It sees `validation_dates_purged = sorted_dates[75 : 100 − PURGE_DAYS − EMBARGO_DAYS] = sorted_dates[75:79]` → **4 days** (`:1799-1802`; `PURGE_DAYS=20`, `EMBARGO_DAYS=1`).
- The objective closure reads `history_validation` (the purged 4-day fold) at `:1854`. So the number Optuna maximizes over 500 trials is a statistic of **4 daily returns**.

The pin constant `_OOS_USABLE_VALIDATION_DAYS_EXPECTED = int(125*0.2) − 20 − 1 = 4` (`:375-377`) and its comment (`:362-374`) state plainly: "with T≈4 the per-trial t-stat sampling distribution is too thin for the normal-CDF approximation in compute_haircut_pvalue to be defensible."

**Methodology assessment.** A selection criterion built on 4 observations is selecting on noise. With n=4: (i) the t(n−1)=t(3) distribution is far from normal in the tails, so a normal-CDF p-value is miscalibrated [High]; (ii) statistical power is critically low — for a moderate effect (d=0.5) even n=10 gives power <0.2, so n=4 is far worse [High] ([statproofbook one-sample t-test power](https://statproofbook.github.io/P/ug-ttest1power.html); [ResearchGate small-sample discussion](https://www.researchgate.net/post/statistical_tests_for_small_sample_size_n4)); (iii) variance estimated from 4 points is itself extremely noisy, and the t-stat divides by that noisy SE, so the ranking across 500 trials is dominated by sampling noise in the denominator. Selecting argmax over 500 noisy 4-sample statistics is close to selecting the trial with the luckiest 4 days.

**My interpretation:** at T≈4 the validation fold cannot distinguish a genuinely better parameter set from a luckier one. The selection is effectively choosing noise, and the frozen-eval fold (consumed once, `:2071`) is then asked to certify a noise-selected winner. **Verdict: INVALID** as a basis for per-symphony parameter selection.

---

## (b) The BHY haircut — VALID and honestly scoped (but see OPT-INVALID-1)

**The haircut is correctly implemented for what it does.** `benjamini_hochberg_adjust` (`:711-748`) is a correct BHY step-up: ascending-sorted p-values, `p_adj_(k) = min_{j>=k} [ (N·c(N)/j) · p_(j) ]`, clamped to [0,1], mapped back to input order, with the Yekutieli arbitrary-dependence factor `c(N) = Σ_{j=1..N} 1/j` (`_yekutieli_c_n`, `:701-708`). The Yekutieli factor is the **correct** choice here because the TPE sampler makes trials dependent (BH-1995 assumes independence/PRDS and would under-correct) — this matches Harvey & Liu 2015's prescription of BHY over Bonferroni/Holm for best-of-N strategy selection [High] ([Harvey & Liu 2015, "Backtesting"](https://people.duke.edu/~charvey/Research/Published_Papers/P120_Backtesting.PDF)).

**The N_effective additive accounting is sound.** `compute_n_effective = n_optuna + S` (`:761-811`) where S sums `n_configs_searched` over BACKTEST_SELECTION ledger rows, excluding frozen-eval-tainted and winner-bundle rows. Padding S copies of p=1.0 ("tested and rejected at no significance") into the BHY input (`_haircut_select:1254-1265`) so the Yekutieli c(N) is computed over the honest N — this is a defensible conservative upper bound (errs toward rejecting genuine signal, never toward passing a spurious one).

**Critically, the haircut's scope is honestly stated.** The `:362-374` comment is correct that "BHY's multiplicity correction addresses cross-trial selection bias independently of T; it does NOT substitute for thin per-trial sample length." This is the right division of labor in the literature: Harvey-Liu BHY corrects *selection bias across trials*; the *per-trial sample-length* problem is the domain of the Probabilistic / Deflated Sharpe Ratio (sample length, skew, kurtosis) [High] ([Bailey & López de Prado 2014, Deflated Sharpe Ratio](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)). The code even names DSR as the canonical joint (N,T) framework to consult (`:371-372`). So the BHY layer is VALID and the code's self-admission that it cannot fix thin T is **correct, not hand-waving**.

### OPT-INVALID-1 — DEFECT: the CRRA-EU haircut uses the wrong t-statistic

**`_haircut_select` ignores its `tstat_fn` parameter inside the per-trial loop.** The function signature and docstring (`:1184`, `:1203-1207`) say `tstat_fn` selects the t-stat (default `compute_sortino_tstat`, pass `compute_crra_eu_tstat` for the CRRA-EU path). The call site correctly routes it: `_objective_kind == "crra_eu"` → `_tstat_fn = compute_crra_eu_tstat` (`:1953-1954`), passed as `tstat_fn=_tstat_fn` (`:1976-1977`). **But the loop body hardcodes `compute_sortino_tstat`:**

```
1251:        tstats.append(compute_sortino_tstat(series, seed=trial_idx))
```

`tstat_fn` is never called. There is also a confusing dead-ish block at `:1238-1243` (CRRA-001 comment about U-transforming `daily_returns`) and `_crra_gamma` is computed at `:1243` then never used. Net effect: **for a CRRA-EU bundle, Optuna optimizes the CRRA-EU objective, but the haircut significance gate scores each trial with the Sortino bootstrap t-stat** (`compute_sortino_tstat` → Sortino / bootstrap-SE, `:650-683`). This is precisely the **H-6 category error** the code elsewhere takes pains to name and forbid (`compute_crra_eu_tstat` docstring `:529-534`: "a mean-valued functional needs the one-sample t-stat, NOT effect_size*sqrt(T)"). The mean-valued CRRA-EU objective is being gated by a Sortino sampling distribution.

**Consequence:** the deploy/reject decision for CRRA-EU symphonies is made on a mis-calibrated p-value. This does not make the system *less* conservative in an obvious direction (the Sortino bootstrap path returns t=0 → p=0.5 → reject when SE is unavailable, `:661-665`, which is conservative), but the *calibration* of which trials clear the q=0.05 FDR gate is wrong for the production objective. **This is a concrete, fixable code defect, not a data-wall limitation.** [High — verified by reading the loop and the call site on HEAD 8586ab2.]

*Note for synthesizer:* whether the production bundles are actually `crra_eu` determines blast radius. The CLAUDE.md key-files table and README describe CRRA-EU as the live objective, so I treat this as live-path-relevant, but I did not execute the daemon to confirm `_objective_kind` at runtime. State plainly: **runtime objective_kind unconfirmed by execution; defect confirmed by static read.**

---

## (c) Is 125 days representative across regimes? — MARGINAL→INVALID

`run_autotuner` uses **a single chronological split** (one train / one validation / one frozen-eval fold, `:1785-1787`). This is single-path walk-forward. The code's own comment (`:368-370`) names the remedy: "adopt combinatorial purged k-fold cross-validation per López de Prado 2018 Ch. 7.4 to recover statistical power without expanding total history."

**Methodology:** single-path walk-forward yields high-variance performance estimates because the result is contingent on one specific historical ordering; CPCV generates *multiple* backtest paths from the same data, which is what makes "statistical backtesting" (a distribution of outcomes, PBO, DSR) possible and drives the probability of false discovery down [High] ([LdP CPCV overview, SSRN 3257497](https://www.scribd.com/document/756967224/ssrn-3257497); [Purged cross-validation, Wikipedia](https://en.wikipedia.org/wiki/Purged_cross-validation)). 125 trading days ≈ 6 months covers, at best, one-to-a-few regimes. If the forward regime differs from the 125-day window (the Kaminski-Lo momentum-vs-mean-reversion concern flagged in the vision §5.1), parameters are tuned to the wrong world.

**My interpretation:** with a single 6-month path and one validation fold, regime-representativeness is unprovable from the data the product will have. The code correctly identifies CPCV as the escape, but **CPCV is documented as a future workstream, not implemented** — `run_autotuner` is single-split. **Verdict: MARGINAL on form (purge+embargo present), INVALID on power (1 path, 1 fold, ~6 months).**

---

## (d) Is the CRRA-EU t-stat a sound selection criterion at this sample size? — INVALID at T≈4

The CRRA-EU objective is **sound in form**: `compute_crra_eu_tstat = mean(U)/(sd(U)/√T)` (`:520-553`) is the correct one-sample t-statistic for a mean-valued functional, uses sample stdev (ddof=1, `:550`), and correctly returns 0.0 for T≤1 or constant series. The CRRA utility transform itself (γ-frozen, wealth-ratio floored) is theory-grounded. The form is not the problem.

**The sample size is the problem.** This t-stat is evaluated on the same ~4-day purged validation fold (item a). At T=4: the √T=2 scaling is computed from a 4-point mean and a 4-point sd; `compute_haircut_pvalue` then pushes this through a **normal** CDF (`1 − Φ(t)`, `:686-698`), not even a t(3) CDF. So the per-trial p-value is doubly mis-calibrated at small T: (i) the statistic's true distribution is t(3)-like with heavy tails, and (ii) it is mapped through Φ as if T were large [High] ([Student's t-test, Wikipedia](https://en.wikipedia.org/wiki/Student's_t-test); the n=4 "very small / questionable approximation" regime per the small-sample search). The code's `:364-365` comment ("too thin for the normal-CDF approximation ... to be defensible") is **correct**.

**Verdict: INVALID at the live sample size.** The objective is the right objective; it is being asked to discriminate on 4 observations, where it cannot.

---

## (e) NN1 spec-freeze — VALID (genuinely prevents circular/look-ahead provenance)

NN1 is enforced **structurally and at multiple gates**, default-deny:
- `validate_search_space_nn1()` runs **before** `optuna.create_study` (`:1638-1640`), so a theory-frozen facet leaked into the search space aborts the run before Optuna can tour it.
- `validate_nn1_compliance` (`:1501-1589`) is a hard gate: every `freeze_discipline` must be in `NN1_HONEST_DISCIPLINES` (default-deny — unknown values are violations, `:1559,:1576-1578`); any `BACKTEST_SELECTION` facet → `RuntimeError`, refuse to start (`:1680-1688`); any `OOS` evidence_source in the ledger (frozen-eval peek) is flagged as a stricter violation (`:1580-1588`).
- Bundle **integrity** is verified: stored `bundle_hash` is recomputed from `facets_json` and a mismatch aborts (`:1667-1677`) — a tampered bundle cannot run.
- The clever structural property: a `BACKTEST_SELECTION` facet that slips through as a ledger row inflates S → inflates N_effective → inflates Yekutieli c(N) → raises the FDR bar for *every* trial (`:751-758`). So circular provenance doesn't just get flagged, it **mathematically penalizes** the haircut. NN1-honest case (S=0) is byte-identical to the un-penalized haircut.

**Methodology:** this is the correct application of the multiple-testing principle that hand-selection done offline is still a test that must be counted (the "you can't game the gate by pre-filtering by hand" property). It directly addresses circular/look-ahead parameter provenance: a parameter chosen because a backtest liked it is either refused outright (facet-level) or counted against the N (ledger-level). **Verdict: VALID.** This is the strongest piece of the stack.

*One caveat I could not fully verify:* NN1 enforces that *frozen* facets are honestly provenanced, but it does **not** address whether the *Optuna-tuned* parameters (the search-space facets) are themselves overfit to the thin fold — that risk is owned by the BHY haircut (item b) and the sample-size wall (items a, d), not by NN1. NN1 is necessary but not sufficient for trustworthy parameters; it is correctly scoped to provenance, not power.

---

## The honest question: can this stack produce trustworthy parameters at all?

**Short answer: not from 125 days with a single split, no — and the code says so in writing.** The stack is a well-built selection-bias defense (BHY + NN1) wrapped around a per-trial signal that is too thin to be meaningful (T≈4). The selection-bias machinery is necessary but operates downstream of a noise-dominated objective. Correcting for "you tried 500 things" does not help when each of the 500 things is scored on 4 data points.

This mirrors the project's *own* documented walls (vision §6.1): the council rejected the live CVaR trigger because tail-risk action is un-validatable at this data scale (~6-37 tail days vs ~1,000 needed). The same data wall applies to per-symphony parameter *selection*: a 4-day validation fold cannot certify a parameter set.

**The two remediation paths the code names are the right ones** ([High], both LdP-grounded):
1. **Expand the data budget** (more history) — directly raises T per fold.
2. **CPCV** (López de Prado 2018 Ch. 7.4) — recovers statistical power *without* more total history by generating multiple backtest paths and a distribution of outcomes, enabling DSR/PBO-style "statistical backtesting" ([LdP CPCV](https://www.scribd.com/document/756967224/ssrn-3257497)).

Neither is implemented; `run_autotuner` is single-split today. Until one is, **per-symphony tuning is structurally underpowered**, and the most defensible operating posture is the one the project already adopts elsewhere: treat tuned parameters as provisional and let the deploy-only-if-it-clears-the-bar gate (which, when no trial clears, keeps yesterday's params, `:1983-1991`) do its job as a *no-harm* default rather than a *positive-evidence* selector.

---

## What is provable vs unprovable (stated plainly)

- **Provable from code (verified on HEAD 8586ab2):** the fold arithmetic yields T≈4 for the Optuna objective; BHY + Yekutieli is correctly implemented; N_effective additive accounting is sound; NN1 is a structural default-deny hard gate; **OPT-INVALID-1 (haircut uses Sortino t-stat regardless of objective_kind) is a real defect.**
- **Provable from literature:** n=4 is below the defensible-approximation regime for the t/normal sampling distribution; BHY corrects selection bias but not sample length; CPCV/DSR are the LdP-prescribed remedies; the code's self-assessment of its own power wall is accurate.
- **Unprovable / not verified this pass:** whether production bundles are actually `crra_eu` at runtime (I did not execute the daemon — OPT-INVALID-1's blast radius depends on this); whether the 125-day windows the operator's symphonies actually see are regime-representative of their forward periods (unprovable from the data at this scale — this is the central pipe-dream risk).

---

## Sources

- [Harvey, C.R. & Liu, Y. (2015). "Backtesting." J. Portfolio Management 42(1)](https://people.duke.edu/~charvey/Research/Published_Papers/P120_Backtesting.PDF) — Tier 2 (named experts, the haircut framework the code cites). Accessed 2026-05-30. Confirms BHY-over-Bonferroni for best-of-N, nonlinear haircut, selection-bias scope.
- [Bailey, D.H. & López de Prado, M. (2014). "The Deflated Sharpe Ratio." SSRN 2460551](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551) — Tier 1/2. Accessed 2026-05-30. Confirms DSR/PSR own the joint (N, sample-length, non-normality) problem — the layer BHY does not cover.
- [López de Prado — Combinatorial Purged Cross-Validation (SSRN 3257497 overview)](https://www.scribd.com/document/756967224/ssrn-3257497) — Tier 2. Accessed 2026-05-30. Confirms single-path walk-forward = high variance / one path; CPCV = multiple paths, lower PBO, "statistical backtesting."
- [Purged cross-validation — Wikipedia](https://en.wikipedia.org/wiki/Purged_cross-validation) — Tier 3 corroboration of CPCV mechanics (purge + embargo + combinatorial paths).
- [One-sample t-test power — Book of Statistical Proofs](https://statproofbook.github.io/P/ug-ttest1power.html) — Tier 3. Accessed 2026-05-30. Minimum-detectable-effect / power at small n.
- [Small-sample t-test (n=4) discussion — ResearchGate](https://www.researchgate.net/post/statistical_tests_for_small_sample_size_n4) — Tier 3/5. Used only to corroborate the "n=4 is questionable for the approximation" claim, which is also supported by the t-distribution tail behavior (Tier 1 statistical fact).
- [Student's t-test — Wikipedia](https://en.wikipedia.org/wiki/Student's_t-test) — Tier 3. t(n−1) vs normal tail divergence at small n.
- **Code (Tier 1, this repo, HEAD 8586ab2):** `autotuner.py:1779-1802` (fold split), `:1854` (objective reads purged fold), `:375-377` + `:362-374` (T≈4 pin + self-admission), `:711-748` (BHY), `:761-811` (N_effective), `:1184-1272` (_haircut_select; `:1251` the defect), `:1953-1977` (call-site routing), `:520-553` (compute_crra_eu_tstat), `:686-698` (normal-CDF p-value), `:1501-1589` + `:1637-1688` (NN1 gates).
