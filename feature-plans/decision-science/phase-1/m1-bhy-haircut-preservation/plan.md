# Phase 1 — M1 — BHY Haircut Preservation Under CRRA Objective

**Feature:** Preserve the Harvey-Liu + Benjamini-Hochberg-Yekutieli (BHY)
haircut machinery — including the Yekutieli `c(N)` arbitrary-dependence
factor — under the new CRRA-EU objective. **100% preservation, single
call-site swap.** Per-trial statistic changes; step-up + clamp + FDR gate do
NOT.

**Phase:** Phase 1 (HARDEN floor)

**Owner agent-type:** `optuna-specialist` (drives), `quant-test-writer`
(adversarial RED on the dependency case).

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §2.1 (binding S-2 —
  "BHY step-up + Yekutieli c(N) machinery 100% preserved; only the
  per-trial statistic changes"), §3.5 ("BHY haircut preserved unchanged;
  search space stays 6-D").
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.0
  bullet 4 (verified Yekutieli `c(N) = Σ 1/j` correctness; verified
  `c(N)` corrects multiple-testing ONLY over the Optuna search), §A.6
  W-H5 serial-correlation inheritance.
- `autotuner.py:262-356` — the entire haircut block:
  - 272-277: `HARVEY_LIU_FDR_Q` (policy dial)
  - 280-286: `_HAIRCUT_PVALUE_EPSILON` (numerical-stability dial)
  - 304-316: `compute_haircut_pvalue` (one-sided clamped p)
  - 319-356: `benjamini_hochberg_adjust` (step-up + Yekutieli c(N))
- `autotuner.py:698-732` — `_haircut_select` (the integration point).
- `autotuner.py:1018-1047` — the AI-branch haircut application after
  `study.optimize`.

## Why

The haircut is **not** a methodology preference. Per the v3 synthesis
§2.5, the Yekutieli `c(N)` factor is what keeps the haircut TRUE under
the dependence structure Optuna's TPE sampler induces across trials —
plain Benjamini-Hochberg 1995 would assume independence/PRDS and
under-correct the false-discovery rate by exactly `c(N)`. The full chain
(step-up direction, running-min monotonicity, the
`[ε, 1-ε]` clamp) is a single contract.

S-2 changes **only the per-trial statistic**. Everything downstream —
clamp, step-up, c(N), FDR gate — is preserved byte-identical. If the
implementing team accidentally "modernises" the BHY block during the M1
cycle, they silently re-introduce H-6-class category errors AND break
replay parity (the p-value clamp at `autotuner.py:314` is load-bearing
against NaN propagation).

This plan exists to make that contract **enforced by tests**, not just
asserted in a design doc.

## Deliverables

### D1 — Zero-change preservation of the BHY core

`compute_haircut_pvalue` (`autotuner.py:304-316`) and
`benjamini_hochberg_adjust` (`autotuner.py:319-356`) are **byte-identical
unchanged** after M1 ships. No signature change. No defaulting change.
No reordering. No "minor cleanup."

`HARVEY_LIU_FDR_Q = 0.05` (line 277) and `_HAIRCUT_PVALUE_EPSILON = 1e-12`
(line 286) are byte-identical unchanged.

The Yekutieli `c(N) = sum(1.0/j for j in range(1, n+1))` line
(`autotuner.py:345`) is byte-identical unchanged.

### D2 — Single call-site swap in `_haircut_select`

`_haircut_select` (`autotuner.py:698-732`) receives an explicit `tstat_fn`
parameter (see the `compute_crra_eu_tstat` plan, D2). Default value is
`compute_sortino_tstat` (backward-compatible — retained Sortino sweeps
continue to work).

The CRRA-EU path's `run_autotuner` caller passes
`tstat_fn=compute_crra_eu_tstat` AND threads the active `gamma` through
(closure or `functools.partial`). The rest of `_haircut_select` —
`compute_haircut_pvalue`, `benjamini_hochberg_adjust`, `argmin` selection,
the `HARVEY_LIU_FDR_Q` gate, the `None`-return branch — is byte-identical.

The `daily_returns` user-attr (`autotuner.py:996`) is **still recorded**;
the CRRA t-stat re-transforms from raw guard-alpha (see the
`compute_crra_eu_tstat` plan, D1).

### D3 — Sortino-sentinel filter preservation

`autotuner.py:1041-1043` filters out trials whose value equals
`math_engine._SORTINO_SENTINEL` (the 1e6 zero-downside sentinel). The
filter logic stays — the sentinel concept is Sortino-specific (a
mean-valued CRRA-EU objective has no zero-downside-divide-by-zero hazard),
so under CRRA-EU the filter is a no-op but harmless. The implementing
team **does not delete the filter** in M1's cycle; deletion would expose
a future Sortino-objective recall to the bug the filter exists to prevent.

A new "CRRA degenerate-series sentinel" is **NOT** introduced. The CRRA
t-stat's `sd(U) == 0` branch (returns `0.0` — see the
`compute_crra_eu_tstat` plan D1) ranks the trial last via `argmin p_adj`
ordering naturally. No second sentinel surface.

### D4 — `selection_tstat` persistence column

The `autotune_runs` row's `selection_tstat` column (already-present per
the calibration-sweep work this branch carries) continues to be the
**winner's t-statistic** — under the CRRA objective, that is the
`compute_crra_eu_tstat(winner.daily_returns, gamma)` value, not the
naive Sortino. The column's semantic stays "higher-is-better significance
scalar"; the dashboard / Discord surface continues to read it without
change.

## Dependencies

- **Blocks:** Phase 1 — M1 CRRA-EU objective plan completion (the
  `_haircut_select` call-site swap is the final wiring step after the
  objective ships).
- **Blocked by:** Phase 1 — `compute_crra_eu_tstat` plan (the new t-stat
  must exist).
- **Soft-coupled to:** the additive N_effective plan (D2/D3 in this plan
  unchanged regardless of `S`; `N_effective` enters the `c(N)` argument as
  `N` in a future cycle — see the additive plan).

## Golden-fixture tests required

### T1 — `benjamini_hochberg_adjust` byte-identical pin (regression)

Fixture: a frozen N=10 p-value vector. Compute the expected adjusted
p-values once (hand-derived in the test file with comments showing the
arithmetic). Assert `benjamini_hochberg_adjust(p_values)` returns the
expected vector to `1e-15`. Tripwire: any future PR that "cleans up" the
step-up direction or the running-min order breaks this test.

### T2 — Yekutieli c(N) closed-form pin

Assert `c(N) = harmonic_number(N)` for `N in [1, 5, 10, 100, 500]` to
`1e-15`. Catches a future drift toward log-approximation
(`c(N) ≈ ln(N) + γ`).

### T3 — Clamp boundary pin

Assert `compute_haircut_pvalue(t_stat)` returns exactly
`_HAIRCUT_PVALUE_EPSILON` for `t_stat = 10.0` (Φ saturates beyond ~8.3 in
IEEE-754 double) and `1 - _HAIRCUT_PVALUE_EPSILON` for `t_stat = -10.0`.
Catches a future loosening of the clamp.

### T4 — End-to-end haircut under Sortino vs CRRA-EU on the SAME trial set

Fixture: a small fake-trials list (~20 trials), each carrying a
`daily_returns` user-attr. Two runs:
1. `_haircut_select(trials, tstat_fn=compute_sortino_tstat_wrapper)`
2. `_haircut_select(trials, tstat_fn=compute_crra_eu_tstat_wrapper(gamma=2.0))`

Assert:
- both runs reach the same `benjamini_hochberg_adjust` invocation count
  (same step-up traversal);
- the FDR gate threshold `HARVEY_LIU_FDR_Q` is consulted exactly once per
  run;
- the winner's `selection_tstat` matches the `tstat_fn(winner)` output —
  i.e. the t-stat persisted matches the t-stat used for selection (not
  re-derived from `trial.value` post-hoc, which would be wrong for the
  CRRA path).

### T5 — Sortino-sentinel filter retention

Fixture: a fake-trials list including a trial with
`value == math_engine._SORTINO_SENTINEL`. Assert it is filtered out of
the haircut input under BOTH `tstat_fn` choices. Tripwire against a
"this filter is Sortino-only, delete it" PR.

### T6 — Replay parity (Gate 1) — haircut bit-identical

Fixture: the validation-fold replay reference. Run the full
`run_autotuner` on a deterministic seed under CRRA-EU; assert
the `selection_tstat`, `p_adj`, and `winner_params` written to the
`autotune_runs` row match a committed frozen reference, bit-identical.
Catches numerical-reduction-order changes that would break Gate-1 parity.

## Definition of Done

1. T1-T6 RED on a clean implementer commit, GREEN after.
2. `pytest tests/autotuner/` PASSES — full suite, including any existing
   BHY tests (which must not be modified to accommodate the swap).
3. Lines `autotuner.py:262-356` and `:272-286` are **diff-empty** in the
   M1 commit (verified by diff inspection in the PR review).
4. `_haircut_select`'s signature gains exactly one new parameter
   (`tstat_fn`); no other field changes.
5. Commit message: `feat(autotuner): study_name=<TS>__<symphony>, BHY
   haircut preserved byte-identical, _haircut_select gains tstat_fn
   parameter; n_trials=500; objective=CRRA-EU mean(U)`.

## Risk callouts

- **"Modernise the BHY block."** The single biggest risk is a future PR
  that "cleans up" `benjamini_hochberg_adjust` — e.g. swaps the running-min
  for `numpy.minimum.accumulate`, or replaces the descending-rank loop
  with an ascending one. Any such change breaks the running-min
  monotonicity contract. T1 catches the numerical form; the
  diff-empty DoD step catches the textual form.
- **`c(N)` log-approximation drift.** `c(N) = ln(N) + 0.5772...` is a
  common asymptotic approximation. For `N=500`, `ln(500) ≈ 6.21` vs
  `c(500) ≈ 6.79` — a ~10% under-correction that silently weakens the
  FDR gate. T2 catches this.
- **Sentinel filter deletion.** A maintainer reasonably argues "CRRA-EU
  has no sentinel, this filter is dead code." T5 catches the deletion;
  the filter is retained for the Sortino-objective sweep path that is
  NOT being deleted.
- **`selection_tstat` semantic drift.** Currently persists the *winner's*
  t-stat. Under the CRRA swap, a careless implementation might persist
  `compute_sortino_tstat(study.best_value, T)` because `study.best_value`
  is the path of least resistance. That value is the Sortino t-stat on a
  CRRA-objective value — a category error of the H-6 family. T4 catches
  this.
- **W-H5 inheritance.** The independence assumption violation in the
  `√T` denominator carries through. **NOT** a regression — Sortino's
  `sortino·√T` inherited the same. The haircut's *selection* step
  (argmin `p_adj`) is roughly common-mode under serial correlation, but
  the absolute `p_adj` vs `HARVEY_LIU_FDR_Q` gate IS affected, so a
  borderline trial set could clear a gate it should not (council §A.6).
  Disclosed; remediation is the Engine Audit BHY plan.
- **Yekutieli c(N) over what N?** Phase 1 NN1-honest case has `S = 0`,
  so `N = N_optuna` and the c(N) call uses the trial count exactly as
  today. The additive `N_effective = N_optuna + S` plan installs the
  N-redirection only WHEN `S > 0` — and that case is flagged loud
  precisely so it cannot pass silently.

## Out of scope

- Any change to `compute_haircut_pvalue` body or signature — explicit
  preservation discipline.
- Any change to `benjamini_hochberg_adjust` body or signature — explicit
  preservation discipline.
- The N-redirection from `n_optuna` to `n_effective` — owned by the
  additive N_effective plan.
- The HAC / Newey-West W-H5 remediation — Engine Audit BHY plan,
  explicitly out-of-scope for Phase 1.
- Per-trial t-stat function itself — owned by the `compute_crra_eu_tstat`
  plan.
- CRRA utility / wealth-argument derivation — owned by the M1 objective
  plan.
