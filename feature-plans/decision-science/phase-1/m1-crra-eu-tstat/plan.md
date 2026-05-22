# Phase 1 — M1 — Re-derived per-trial t-stat `compute_crra_eu_tstat` (S-2)

**Feature:** Introduce a NEW per-trial significance statistic for the CRRA-EU
objective — a genuine one-sample t-stat `t = mean(U)/(sd(U)/√T)` — replacing
the call to `compute_sortino_tstat` (`autotuner.py:289-301`) for the new
objective ONLY. Avoid the H-6 category error.

**Phase:** Phase 1 (HARDEN floor — binding condition S-2)

**Owner agent-type:** `optuna-specialist` (drives), `quant-test-writer`
(adversarial RED on the fixture).

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §2.1 (binding
  correctness requirement S-2), §4 S-2, §8 test 1.
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.0
  bullet 2 (verified `compute_sortino_tstat` returns `sortino * sqrt(T)` and
  that S-2 is correct), §A.6 (H-6 — `√T` t-stat inherits serial-correlation
  anti-conservatism; new residual W-H5), §A.7 (H-7 — §8 test 1 PINS the
  formula; does NOT validate the statistic; verb precision).
- `autotuner.py:266-271` — the inline comment on the original H-6 category
  error this re-derivation must not recommit.
- `autotuner.py:289-301` — `compute_sortino_tstat(sortino, T) → sortino *
  sqrt(T)`: the function whose call-site swaps for the CRRA objective.
- `autotuner.py:304-316` — `compute_haircut_pvalue(t_stat)`: unchanged;
  consumes whichever t-stat is passed in.
- `autotuner.py:719-723` — the t-stat call site in `_haircut_select`.

## Why

The original `compute_sortino_tstat` returns `sortino * sqrt(T)`. A Sortino
ratio is already a mean/dispersion *ratio*, so multiplying by `sqrt(T)` is
the standard bridge from a per-observation effect size to a sample
significance statistic — a genuine t-stat. **A CRRA-EU objective is a bare
mean of `U` — not a ratio**, so the same `effect_size * sqrt(T)` shape is
the H-6 category error the code already fixed once
(`autotuner.py:266-271`). The genuine significance statistic for a sample
mean is the **one-sample t-stat**:

```
t = mean(U) / (sd(U) / sqrt(T))
```

where `U = compute_crra_utility(derive_wealth_argument(g_i), gamma)` for
each per-day guard-alpha `g_i` in the validation-fold series.

Silently reusing `compute_sortino_tstat` for the CRRA objective would:
- mismatch the statistic to the functional (a mean needs `mean/(sd/sqrt(T))`,
  not `effect_size·sqrt(T)`);
- silently break the BHY haircut's calibration — `p = 1 - Φ(t_wrong)` is
  not a valid significance probability for `t_wrong`;
- recommit the H-6 category error.

S-2 is **binding** (council synthesis §4, "recommended SUBJECT TO").

## Deliverables

### D1 — `compute_crra_eu_tstat` function (`autotuner.py`)

A NEW function alongside `compute_sortino_tstat`. Signature:

```python
def compute_crra_eu_tstat(daily_returns: list[float], gamma: float) -> float:
    """Per-trial one-sample t-statistic on the CRRA-transformed series.

    Computes U_i = compute_crra_utility(derive_wealth_argument(g_i), gamma)
    for each g_i in daily_returns, then returns:
        t = mean(U) / (sd(U) / sqrt(T))
    where T = len(daily_returns) and sd(U) is the sample (n-1) standard
    deviation of U. Returns 0.0 for T <= 1, or for sd(U) == 0.0 (a
    degenerate trial — degenerate-trial protection is the haircut's job,
    not the t-stat's; surface a 0.0 t-stat so BHY ranks the trial last
    rather than passing it through as +inf).

    This is NEW statistical machinery (binding S-2). It is NOT
    `effect_size * sqrt(T)` — that is the H-6 category error
    (autotuner.py:266-271) for a mean-valued functional.

    The √T denominator carries an INDEPENDENCE assumption the data partially
    violates (W-H5, serial correlation on overlapping vol regimes). That
    exposure is inherited unchanged from compute_sortino_tstat and is
    out-of-scope for Phase 1 — see the Engine Audit BHY plan for the
    remediation workstream.
    """
```

Constraints:
- Imports `compute_crra_utility` and `derive_wealth_argument` from
  `math_engine` — single source of truth.
- Imports `WEALTH_ARG_FLOOR` from `math_engine` and applies it to the
  wealth argument **before** the CRRA transform. NEVER floors `U` (H-1).
- Uses `statistics.stdev` (sample, n-1) or the equivalent — NOT `pstdev`.
  The one-sample t-stat denominator is the sample standard deviation by
  convention; using the population stdev would inflate `t` by a `sqrt(n /
  (n-1))` factor and silently shift the haircut calibration.
- Returns a `float` — never NaN or inf if the inputs are finite. If
  `sd(U) == 0.0` (degenerate constant series), returns `0.0`, not
  `float('inf')`. The haircut then ranks the trial last; degenerate-trial
  detection is the haircut's job (see the BHY preservation plan).
- Pure: no side effects, no logging, no DB writes.

### D2 — Call-site swap in `_haircut_select` (`autotuner.py:719-723`)

The current `_haircut_select` computes `compute_sortino_tstat(t.value, T_i)`
for each completed trial. The swap is **per-objective**, not wholesale:

- If the objective is the new CRRA-EU `run_simulation_crra_eu` →
  `tstats.append(compute_crra_eu_tstat(series, current_gamma))` where
  `series = t.user_attrs.get("daily_returns", [])` (the raw guard-alpha
  series that M1 still records, see the M1 objective plan D6).
- If the objective is the legacy Sortino (any retained calibration sweep) →
  `tstats.append(compute_sortino_tstat(t.value, T_i))` — unchanged.

The recommended implementation: `_haircut_select` takes an explicit
`tstat_fn: Callable[[Trial], float]` parameter so the caller — `run_autotuner`
— picks the right t-stat for the active objective. This makes the
"which t-stat for which objective" decision explicit at the call site,
prevents silent reuse, and is testable.

### D3 — `compute_sortino_tstat` retention

`compute_sortino_tstat` is **NOT** deleted. It remains the per-trial
statistic for any retained Sortino-objective study (calibration sweeps that
use the Sortino objective for reasons unrelated to the deployment
objective). Its docstring gains a one-line warning:

> WARNING: appropriate ONLY for the Sortino objective (a ratio). For a
> mean-valued objective (e.g. CRRA-EU), use compute_crra_eu_tstat; reusing
> this function for a mean is the H-6 category error
> (autotuner.py:266-271).

### D4 — Inline comment at `autotuner.py:266-271`

The existing H-6 comment is updated to reference the NEW pair:

> The H-6 category error was a Sharpe-derived deflation applied to a
> Sortino. Since 2026, the same category-discipline applies between
> compute_sortino_tstat (Sortino objective) and compute_crra_eu_tstat
> (CRRA-EU objective) — a mean-valued functional needs the one-sample
> t-stat, not effect_size·√T.

## Dependencies

- **Blocks:** Phase 1 — BHY haircut preservation plan (the
  `_haircut_select` call-site swap; the BHY preservation plan ratifies
  this).
- **Blocked by:** Phase 1 — M1 CRRA-EU objective plan (`compute_crra_utility`,
  `derive_wealth_argument`, `WEALTH_ARG_FLOOR` must exist in `math_engine`).

## Golden-fixture tests required

(This is **§8 test 1** of the council's regression spec. Per H-7, the verb
is "PINS the formula" — not "validates the statistic." A unit test pins
the wiring; a methodology validation against the data's dependence
structure is W-H5 future work.)

### T1 — Formula PIN (§8 test 1, primary)

Fixture: a deterministic `U`-series with `sd(U) != 1`. Construct by:
- choose a known `(g_1, …, g_T)` guard-alpha series with `T = 25` (mimics
  validation fold);
- compute `U_i` outside the SUT using NumPy primitives (an independent
  reference);
- assert `compute_crra_eu_tstat(g_series, gamma)` returns the analytic
  `mean(U) / (sd(U) / sqrt(T))` within `1e-10`.
- assert `compute_crra_eu_tstat(g_series, gamma) != compute_sortino_tstat(
  effect_size_of(U), T)` — the negative-pin that catches a future
  copy-paste of the Sortino shape.

### T2 — Near-floor wealth sub-case (H-1 / W-H4 finite-t)

Fixture: a guard-alpha series where one day produces a wealth argument at
`WEALTH_ARG_FLOOR + 1e-9` (after derivation). Assert:
- the returned `t` is **finite**;
- `mean(U)` is **finite**;
- `sd(U)` is **finite**;
- the floor was applied to `W`, not to `U` (spy on the call to
  `compute_crra_utility` and assert its argument is `>= WEALTH_ARG_FLOOR`,
  and that none of the `U_i` values appears as a hand-clamped floor of `U`
  itself).

### T3 — Sample vs population stdev pin

Fixture: a small `T = 5` series with known sample and population stdev that
differ by a measurable factor `sqrt(5/4) ≈ 1.118`. Assert
`compute_crra_eu_tstat(...)` matches the **sample-stdev** computation, not
the population-stdev one. Catches a future `pstdev` swap.

### T4 — Degenerate-series guard

Fixture: a constant `U`-series (`sd(U) == 0`). Assert the function returns
`0.0`, not `inf` and not NaN. The haircut then ranks this trial last —
that behaviour is asserted in the BHY preservation plan's tests, not here.

### T5 — Per-objective routing in `_haircut_select`

Fixture: a small synthetic Optuna trial set. Run `_haircut_select` with
`tstat_fn = compute_sortino_tstat`-style closure; then with `tstat_fn =
compute_crra_eu_tstat`-style closure on the same fixture. Assert the two
routings produce **different** winner rankings AND that swapping is the
explicit caller's choice (not auto-detected from trial type). Catches a
future "let me just always use the CRRA t-stat" silent drift.

### T6 — H-6 negative-pin regression

Static-analysis-style: assert that `compute_crra_eu_tstat` source does NOT
contain the substring `effect_size * sqrt(T)` or `value * sqrt(T)`.
Tripwire against a future PR that "simplifies" the function back into the
H-6 shape.

## Definition of Done

1. T1-T6 RED on a clean implementer commit, GREEN after implementation.
2. `pytest tests/autotuner/` + `tests/execution/` + `tests/engine/` PASS
   (the math_engine wholesale-mock rule).
3. `compute_crra_eu_tstat` lives in `autotuner.py` next to
   `compute_sortino_tstat` — they are siblings.
4. `_haircut_select` takes an explicit `tstat_fn` parameter; `run_autotuner`
   picks it explicitly for each study.
5. Inline H-6 comment at `autotuner.py:266-271` updated.
6. Commit message: `feat(autotuner): study_name=<TS>__<symphony>, +new
   per-trial t-stat compute_crra_eu_tstat (S-2), call-site tstat_fn
   parametrization in _haircut_select; n_trials=500; objective=CRRA-EU
   mean(U)`.

## Risk callouts

- **The H-6 category error.** The single biggest risk is a future PR
  "simplifying" `compute_crra_eu_tstat` back into `effect_size * sqrt(T)`
  shape. T6 catches the textual form; T1's negative-pin catches the
  numerical form. **Both** tests must ship.
- **Sample-vs-population stdev.** `statistics.stdev` (n-1) is the
  one-sample t-stat convention. A `numpy.std()` without `ddof=1` would
  silently use n. T3 pins this.
- **Serial correlation (W-H5 inheritance).** The `√T` denominator assumes
  independent observations. Guard-alpha days within a 125-day fold are
  NOT independent (overlapping 20-day vol regimes; autocorrelated squared
  returns). The serial-correlation inflation is **inherited unchanged
  from `compute_sortino_tstat`** — S-2 is not a regression on this front
  — but it is a documented W-H5 residual. The remediation (HAC /
  Newey-West / `T_eff`) is **explicitly out-of-scope for Phase 1**
  (v3-and-divergence-evaluation §A.6) because closing it for CRRA without
  also closing it for the legacy Sortino is incoherent, and the
  ~5-day frozen fold makes `T_eff` correction structurally unconstructible.
- **Verb precision (H-7).** The plan must NOT claim T1 "verifies" or
  "validates" S-2. T1 **pins** the formula's wiring. A methodology
  validation against serial-correlated data is W-H5, future work.
- **NaN poisoning surface.** If `WEALTH_ARG_FLOOR` is mis-sized or
  `derive_wealth_argument` produces a non-positive output pre-floor,
  `U` is non-finite, `sd(U)` is non-finite, the t-stat is non-finite, the
  haircut's running-min (`autotuner.py:349-354`) is poisoned, the FDR
  gate silently breaks, AND Gate-1 replay parity fails. T2 catches the
  near-floor case; the M1 objective plan's risk callouts cover the
  upstream derivation.

## Out of scope

- The CRRA utility function itself — `compute_crra_utility` lives in
  `math_engine.py` and is owned by the M1 objective plan.
- The wealth-argument derivation — `derive_wealth_argument` is owned by
  the M1 objective plan (W-H2).
- BHY step-up + Yekutieli c(N) — unchanged; owned by the BHY preservation
  plan.
- Multi-testing N adjustment — `N_effective = N_optuna + S` — owned by
  the additive N_effective plan.
- HAC / Newey-West remediation of W-H5 — explicitly future workstream, out
  of Phase 1.
- Deletion of `compute_sortino_tstat` — explicitly retained for retained
  Sortino-objective studies.
