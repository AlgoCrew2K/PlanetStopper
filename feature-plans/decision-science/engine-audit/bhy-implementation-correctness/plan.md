# Engine Audit — BHY Haircut Implementation Correctness

**Feature:** Audit `benjamini_hochberg_adjust`, `compute_haircut_pvalue`,
`compute_sortino_tstat`, and the p-value clamp for correctness against
Harvey-Liu 2015 and Benjamini-Hochberg-Yekutieli 2001. Confirm the
H-1 NaN-poisoning surface (CRRA unbounded below) cannot occur once
`WEALTH_ARG_FLOOR` is in place. Log W-H5 (serial-correlation
anti-conservatism) as a documented, named future workstream.

**Phase:** Engine audit (post-Phase-1; correctness-discipline hardening
— NOT a behavior change)

**Owner agent-type:** `optuna-specialist` (drives), `quant-risk-researcher`
(consults on the literature references), `quant-test-writer` (RED).

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §2.1, §2.5 (NN1
  + c(N) load-bearing).
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.0
  bullet 4 (verified Yekutieli c(N) correctness), §A.1 H-1 (NaN
  poisoning surface), §A.6 H-6 / W-H5 (serial-correlation
  anti-conservatism — explicitly out-of-scope for Phase 1, named here
  as the future workstream).
- `autotuner.py:262-356` — the entire haircut block.
- `autotuner.py:289-301` — `compute_sortino_tstat`.
- `autotuner.py:304-316` — `compute_haircut_pvalue` (clamp).
- `autotuner.py:319-356` — `benjamini_hochberg_adjust` (step-up + c(N)).
- Harvey & Liu 2015, "Backtesting," J. Portfolio Management 42(1),
  13-28. DOI 10.3905/jpm.2015.42.1.013.
- Benjamini, Hochberg & Yekutieli 2001, "The Control of the False
  Discovery Rate in Multiple Testing under Dependency," Annals of
  Statistics 29(4), 1165-1188.

## Why

The BHY haircut is the central correctness machinery of the decision-
science spine. The v3-and-divergence-evaluation council verified the
implementation correctness directly against the autotuner source
(§A.0 bullet 4) — but that verification is a point-in-time finding,
not a regression-pinned invariant. This audit converts that finding
into named, tested invariants so future PRs cannot drift the
implementation silently.

The H-1 NaN-poisoning surface is the most dangerous failure mode: a
NaN in `mean(U)` or `sd(U)` cascades through `compute_haircut_pvalue`'s
clamp (which can only clamp finite extremes, NOT NaN), then through
`benjamini_hochberg_adjust`'s running-min, silently producing NaN
adjusted p-values. The downstream `argmin p_adj` then picks an
arbitrary index, AND Gate-1 replay parity fails (NaN propagation
breaks bit-identical replay).

The H-1 surface is closed by the M1 plan's `WEALTH_ARG_FLOOR`. This
audit pins the closure with a test that exercises the surface.

W-H5 (serial-correlation anti-conservatism of the `√T` denominator)
is **disclosed-and-deferred**. The audit names it as the future
workstream and records the constraints on its remediation.

## Deliverables

### D1 — BHY closed-form pin tests

A NEW test:

```
T_BHY_closed_form —
For a fixed input vector of N raw p-values (e.g. N=10 with known values),
the expected BHY-adjusted output is computed by hand in the test source
with arithmetic commented step-by-step. Assert
benjamini_hochberg_adjust matches to 1e-15.
```

(This is the same test surface as the Phase-1 BHY-preservation plan's
T1, scoped to the audit's regression-pin role.)

### D2 — Yekutieli c(N) closed-form pin

A NEW test:

```
T_c_n_harmonic —
Assert c(N) = sum(1.0/j for j in range(1, N+1)) at N in
[1, 5, 10, 100, 500, 1000] to 1e-15. Catches log-approximation drift.
```

### D3 — `compute_haircut_pvalue` clamp boundaries

A NEW test:

```
T_pvalue_clamp_boundary —
- compute_haircut_pvalue(10.0)  == _HAIRCUT_PVALUE_EPSILON
- compute_haircut_pvalue(-10.0) == 1.0 - _HAIRCUT_PVALUE_EPSILON
- compute_haircut_pvalue(0.0)   ≈ 0.5
- _HAIRCUT_PVALUE_EPSILON == 1e-12
```

### D4 — `compute_sortino_tstat` formula pin

A NEW test:

```
T_sortino_tstat_formula —
- compute_sortino_tstat(sortino=2.0, T=100) == 2.0 * sqrt(100) == 20.0
- compute_sortino_tstat(sortino=2.0, T=0)   == 0.0  (degenerate guard)
```

This is the **Sortino** tstat pin. The new `compute_crra_eu_tstat`
(Phase-1 plan) has its own pin in that plan.

### D5 — H-1 NaN-poisoning surface closure (CRRA path)

A NEW test:

```
T_h1_nan_closure —
Construct a guard-alpha series where ONE day produces a wealth
argument at math_engine.WEALTH_ARG_FLOOR + 1e-9 (just above the
floor). Run the full BHY pipeline:
  1. compute_crra_eu_tstat(series, gamma=5.0) → t
  2. compute_haircut_pvalue(t) → p
  3. benjamini_hochberg_adjust([p, p2, p3, ...]) → adjusted
Assert NONE of the values is NaN or inf — every output is finite.

Construct a SECOND series where the wealth argument is naively
unfloored (a hypothetical alternative impl). Assert the t-stat is
non-finite. This is the negative-pin proving the floor is
load-bearing.
```

### D6 — Sortino-sentinel filter retention

A NEW test (re-asserts the Phase-1 BHY preservation plan's T5 in the
audit scope):

```
T_sortino_sentinel_filter —
A fake-trials list including a trial with value=math_engine._SORTINO_SENTINEL
is filtered out of haircut input. The filter line is at
autotuner.py:1041-1043.
```

### D7 — W-H5 serial-correlation documentation fixture

A NEW **documentation fixture** test (not a remediation):

```
T_w_h5_documentation —
Construct a U-series with KNOWN positive lag-1 autocorrelation (e.g.
AR(1) with phi=0.3). Compute compute_crra_eu_tstat under the naive
sqrt(T) assumption. Compute also the "effective sample size" t-stat
that would adjust T to T_eff = T * (1-phi)/(1+phi) (Newey-West-ish
heuristic). Assert the naive t-stat is LARGER than the T_eff-adjusted
one — confirming the documented W-H5 anti-conservatism direction.

This test does NOT change the autotuner's behaviour. It is a
DOCUMENTATION FIXTURE that makes the W-H5 residual VISIBLE in the
test suite, not just asserted in a design doc.
```

### D8 — Step-up direction (descending-rank) pin

A NEW test:

```
T_stepup_descending —
Property-based: for any random p-vector of N in [5, 50], assert that
benjamini_hochberg_adjust output is monotone non-decreasing in the
sorted-by-raw-p rank — the step-up property. Catches a future
"swap the loop direction" PR that would reverse the step-up logic.
```

### D9 — Audit findings record

`findings.md` committed alongside the plan:
- Confirmation that the BHY implementation matches Harvey-Liu 2015 +
  Benjamini-Hochberg-Yekutieli 2001 textbook form.
- Confirmation that the c(N) factor is the exact harmonic number.
- Confirmation that the clamp prevents IEEE-754 saturation.
- Statement of W-H5 as a documented, deferred workstream with the
  reasoning:
  - the `√T` denominator assumes independent observations;
  - guard-alpha days carry residual serial correlation;
  - the autotuner's purge/embargo addresses fold-boundary leakage,
    NOT within-fold serial correlation;
  - the inflation is roughly common-mode across trials, so the
    *selection* step is less distorted than the *absolute*
    significance level (the FDR gate at p_adj <= 0.05);
  - remediation requires HAC / Newey-West / T_eff adjustment;
  - at ~5-day frozen-fold scale the lag-1 autocorrelation is itself
    unestimable, so T_eff is structurally unconstructible at that
    scale;
  - closing W-H5 for the CRRA t-stat without closing it for the
    legacy `compute_sortino_tstat` is incoherent;
  - therefore W-H5 is a Phase-2-or-future workstream, NOT a Phase-1
    fix.

### D10 — No behavior change

The audit is PIN-and-DOCUMENT. The haircut machinery's behaviour is
byte-identical before and after.

## Dependencies

- **Soft-coupled to:** Phase 1 — BHY haircut preservation plan (this
  audit is the regression-pin layer; the preservation plan is the
  "do not touch" layer).
- **Soft-coupled to:** Phase 1 — M1 CRRA-EU objective plan (T5
  exercises the W-H4 wealth-floor closure).

## Golden-fixture tests required

(D1-D8 above are themselves the test list; T1 = D1, etc.)

## Definition of Done

1. T1-T8 RED on a clean implementer commit, GREEN after.
2. `pytest tests/autotuner/` PASSES — unchanged behaviour, all
   regression-pin tests green.
3. `findings.md` committed with the W-H5 documented-residual record.
4. The haircut block (`autotuner.py:262-356`) is **diff-empty** at
   the audit commit — the audit adds tests, not behaviour.
5. Commit message: `feat(autotuner): study_name=<TS>__<symphony>,
   BHY implementation-correctness audit — closed-form pins for
   benjamini_hochberg_adjust + c(N) + clamp + compute_sortino_tstat;
   H-1 NaN-closure regression; W-H5 documentation fixture;
   n_trials=500; objective=<unchanged>`.

## Risk callouts

- **The "clean up BHY" PR.** The single most likely failure: a
  maintainer "simplifies" `benjamini_hochberg_adjust` — swapping the
  running-min for `numpy.minimum.accumulate`, replacing the descending
  loop with an ascending one, dropping the explicit `c(N)` term in
  favour of a closed-form expression. T1 (closed-form pin) AND T8
  (step-up direction property) catch the form. The diff-empty DoD
  catches the textual form.
- **c(N) log-approximation drift.** T2 catches it.
- **NaN propagation re-opens.** If a future PR drops the
  `WEALTH_ARG_FLOOR` from `math_engine.py` "as cleanup," T5's
  negative pin catches it.
- **W-H5 false closure.** A maintainer claims to have "fixed" W-H5
  with a HAC adjustment but only for the CRRA path (not the Sortino
  path). T7's documentation fixture (which uses plain `√T`)
  continues to pass — but a partial closure is incoherent. Surface
  to PM for the proper closure path.
- **`_HAIRCUT_PVALUE_EPSILON` value drift.** T3 pins it to 1e-12. A
  loosening (e.g. 1e-9) would shorten the tail of the clamp and
  could let degenerate p=0 slip through under extreme t-statistics.
- **Two-sided p-value confusion.** `compute_haircut_pvalue` returns
  the ONE-SIDED p (1 - Φ(t)). A future PR converting to two-sided
  would silently halve the gate's strictness. T3's `t=0.0 → p≈0.5`
  pin catches the conversion (a two-sided pvalue at t=0 is 1.0, not
  0.5).
- **Audit findings stale.** `findings.md` records a point-in-time
  audit. If the haircut machinery is later modified (intentionally,
  via a PM-surfaced methodology change), the findings.md must be
  updated. The "diff-empty" DoD catches accidental modifications;
  intentional modifications require a separate plan.

## Out of scope

- Modifying the haircut machinery — out of scope; the audit
  PIN-and-DOCUMENTs, does not change.
- HAC / Newey-West W-H5 remediation — explicitly future workstream;
  the audit names and documents the residual but does not close it.
- The `compute_crra_eu_tstat` formula validation — owned by the
  Phase-1 `compute_crra_eu_tstat` plan.
- The additive `N_effective` consumer — owned by the additive
  accounting plan.
- The Sortino-sentinel filter mechanics — owned by the Phase-1 BHY
  preservation plan.
- The p-value clamp value itself (1e-12) — preserved.
