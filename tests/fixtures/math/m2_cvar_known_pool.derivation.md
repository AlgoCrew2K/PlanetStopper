# Derivation: M2 CVaR Known-Pool Fixture Reference Values

## Pool Design

N = 150 (MC_DEFAULT_NEIGHBOR_K). alpha = 0.05 (CVAR_ALPHA_DEFAULT).

The pool was constructed manually to place an **atom at the alpha-quantile** — the
critical edge case for the Rockafellar-Uryasev atom-handling formula:

- 7 values strictly below the VaR: -0.05, -0.04, -0.03, -0.025, -0.02, -0.015, -0.012
- 1 atom AT the VaR (the 5%-quantile): -0.01
- 142 values above the VaR: 0.001, 0.002, ..., 0.142

This construction ensures the naive "mean of k below-VaR values" estimator differs
from the correct R-U value, giving the fixture its discriminating power (A-1 rubric item).

## Step 1: VaR (5th-percentile quantile)

```
k = floor(alpha * N) = floor(0.05 * 150) = floor(7.5) = 7
alpha * N = 7.5
VaR = sorted_pool[k] = sorted_pool[7] = -0.01   (0-indexed; the 8th-smallest value)
```

The 7 values at indices 0–6 are strictly below VaR. The atom at index 7 equals VaR.

## Step 2: R-U General-Distribution CVaR

Rockafellar & Uryasev (2002) general-distribution CVaR for a discrete sample of N values:

```
CVaR_alpha = (1/alpha) * (1/N) * [ sum(pool_i for pool_i < VaR) + (alpha*N - k) * VaR ]
```

The term `(alpha*N - k)` = 7.5 - 7 = **0.5** is the fractional weight of the atom.

```
sum_below = -0.05 + (-0.04) + (-0.03) + (-0.025) + (-0.02) + (-0.015) + (-0.012)
          = -0.192

fractional_weight = 7.5 - 7 = 0.5

CVaR = (1/0.05) * (1/150) * (-0.192 + 0.5 * (-0.01))
     = 20 * (1/150) * (-0.197)
     = 20 * (-0.001313333...)
     = -0.026266666...
```

**Reference value: cvar_5pct = -0.026266666666666667**

### Why the naive estimator fails

The naive estimator (mean of the k=7 strictly-below-VaR values):

```
naive_cvar = mean([-0.05, -0.04, -0.03, -0.025, -0.02, -0.015, -0.012])
           = -0.192 / 7
           = -0.027428571...
```

The naive value (-0.02743) differs from the R-U value (-0.02627) by about 4%. A test
asserted at rel=1e-9 will fail for a naive implementation, passing only for the
correct R-U atom-handling formula.

## Step 3: n_tail_distinct and Stderr (H-2 binding)

The **distinct genuine tail-observation count** for the H-2 stderr denominator:

```
tail_values = [-0.05, -0.04, -0.03, -0.025, -0.02, -0.015, -0.012, -0.01]
n_tail_distinct = 8   (7 below-VaR + 1 atom; atom receives fractional_weight=0.5)
```

Note: including the atom in the distinct count is appropriate because the atom IS a
distinct historical tail observation — it contributes to the CVaR estimate. The
denominator for the standard error is the count of distinct observations that
contribute, not the resample count.

```
std_tail = std(tail_values, ddof=1)
         = std([-0.05, -0.04, -0.03, -0.025, -0.02, -0.015, -0.012, -0.01], ddof=1)
         = 0.014124...   (computed numerically)

stderr_correct = std_tail / sqrt(n_tail_distinct)
               = 0.014124 / sqrt(8)
               = 0.014124 / 2.828427
               = 0.004988379353199651
```

**Reference value: cvar_5pct_stderr_correct = 0.004988379353199651**

### The wrong value (H-2 guard)

```
stderr_wrong_resample = std_tail / sqrt(5000)
                      = 0.014124 / 70.7107
                      = 0.00019953517412798607
```

**Forbidden value: cvar_5pct_stderr_wrong_resample = 0.00019953517412798607**

Ratio: stderr_correct / stderr_wrong_resample = sqrt(5000/8) = **25.0x**.

A 5% tolerance band around `stderr_correct` = [0.00474, 0.00524].
The wrong value (0.0001995) is 25x smaller — far outside this band.
A 50%-band negative assertion (asserting displayed value is NOT within 50% of the wrong value)
is conservative: the 50% band around 0.0001995 = [0.0000998, 0.0002993], which does not
include 0.004988. The 25x ratio gives ample margin.

## Discriminating Power Summary

| Estimator | Value | Note |
|-----------|-------|------|
| R-U CVaR (correct) | -0.026267 | Atom at fractional_weight=0.5 included |
| Naive CVaR (wrong) | -0.027429 | Only k=7 below-VaR values, no atom |
| stderr_correct (n=8) | 0.004988 | Distinct tail obs denominator |
| stderr_wrong (n=5000) | 0.0001995 | Resample count denominator (25x too small) |
