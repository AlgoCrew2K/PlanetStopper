# guard_preconditions

> Per-symphony Kaminski & Lo (2014) stop-justification precondition math: lag-1 return persistence vs. daily Sharpe ratio, classified into 5 honest verdict classes. Pure stdlib, no I/O.

**Source:** `guard_preconditions.py`
**Last updated:** 2026-07-24 (guard-alpha-preconditions, live-gate correction R3, `DE-GUARD-ALPHA-PRECONDITIONS-001` -- added a retention-interaction disclosure to the N_MIN_OBS entry below; no change to this module's own code, which carries zero R3 diff). Prior: 2026-07-23 (first doc-gen entry, new module)

## Overview

`guard_preconditions.py` answers, per symphony, "is a trailing-stop overlay theoretically justified on THIS symphony's if-held return series?" using Kaminski & Lo (2014, *Journal of Financial Markets* 18, 234-254, DOI 10.1016/j.finmar.2013.07.001) -- the canonical direct evidence on when trailing-stop rules add or destroy expected-return value. Under random-walk (IID) returns the stopping premium is always negative; under momentum, value can exist when the return series' lag-1 autocorrelation (ρ) meets or exceeds the strategy's own Sharpe ratio (SR) at the same sampling frequency -- a *sufficient*, not necessary, condition. This module is a pure statistics layer: it takes a daily if-held return series and returns a classified verdict, with no knowledge of where the series came from (that is `autotuner.build_if_held_replay_series` and `analytics.get_shadow_current_return_daily_series` — see those modules' docs). Advisory-only, off the 1-minute execution path; consumed by `app.py`'s `GET /api/guard-alpha-preconditions` route (see `docs/generated/app.md`).

**Not the same concept as the existing $-saved Guard Alpha.** `docs/generated/analytics.md`'s `compute_windowed_symphony_guard_alpha` and `app.py`'s `guard_alpha_summary()` measure REALIZED dollar/percent savings from stops that already fired. This module measures a *theoretical precondition* -- whether the statistical evidence supports the stop's expected-return case AT ALL, independent of whether any stop has fired yet. A symphony can show large realized $-saved and still classify `NOT_MET` here (the K&L theorem is about the population-level expected-return direction, not any single realized outcome), and vice versa.

## API Reference

### `PersistenceStats` (frozen dataclass)

| Field | Type | Description |
|-------|------|-------------|
| `rho` | `float` | Box-Jenkins lag-1 sample autocorrelation of the daily return series. `math.nan` on a zero-variance series (mathematically undefined, never a raised `ZeroDivisionError`). |
| `rho_ci` | `float` | **Half-width** of the 95% CI (not a tuple/interval) -- callers compute the band as `[rho - rho_ci, rho + rho_ci]`. |
| `sharpe_daily` | `float` | Daily, non-annualized Sharpe (`mean / sample_std(ddof=1)`, house convention matching `analytics.py:488`). `math.nan` alongside `rho` on a zero-variance series. |
| `n_obs` | `int` | Count of non-`None` observations in the input. |
| `insufficient_reason` | `str \| None` | Set (non-`None`) when `n_obs < N_MIN_OBS` or the series is zero-variance; `None` on a genuinely sufficient sample. |

### `compute_persistence_stats(daily_returns: list) -> PersistenceStats`

Computes ρ, its 95% CI half-width, and the daily Sharpe ratio for one symphony's if-held daily return series. **Pure function** -- no I/O, no DB access, no network.

- Accepts a list that may contain `None` entries (missing trading days). `None`s are dropped **pairwise** for the lag-1 product (a gap does not make the values on either side of it newly "adjacent") and by plain omission for the mean/variance -- matches `tests/guard_preconditions/_reference_stats.py`'s independent reference formulas at `rel=1e-9` tolerance.
- ρ: single overall mean, sum of consecutive present-pair products in the numerator, sum of squared deviations over all present points in the denominator (Box-Jenkins lag-1 sample ACF).
- Stats are still computed below `N_MIN_OBS` (a thin-but-real sample remains inspectable, just explicitly flagged via `insufficient_reason`) -- never withheld.
- A zero-variance (flat/degenerate) series sets `insufficient_reason` and returns `rho`/`sharpe_daily` as `math.nan` rather than raising.

### `classify_stop_justification(stats: PersistenceStats) -> str`

Classifies one symphony's verdict as an **ordered priority chain** (order is load-bearing -- see the code comment and `tests/guard_preconditions/test_classify_stop_justification.py`'s module docstring for the full AC-3 ambiguous-region rationale):

1. `stats.insufficient_reason is not None` → `"INSUFFICIENT_DATA"`
2. `elif stats.sharpe_daily <= 0` → `"NEGATIVE_EDGE"`
3. `elif rho - rho_ci - sharpe_daily >= -_BOUNDARY_EPS` → `"SUFFICIENT_MET"`
4. `elif rho - sharpe_daily >= -_BOUNDARY_EPS` → `"SUFFICIENT_LIKELY"`
5. `else` (includes the ambiguous region: ρ < SR but ρ+CI ≥ SR) → `"NOT_MET"`

**Step 2 must gate before any ρ-vs-SR comparison** -- a negative or zero Sharpe is trivially "cleared" by any real ρ, so checking ρ first would wrongly classify a stop as justified when the underlying edge is negative (an explicit adversarial trap in the test suite).

Takes exactly one paired `PersistenceStats` argument -- never separate `rho`/`sharpe` floats -- so a caller structurally cannot pass a wrong-frequency (e.g. annualized) Sharpe alongside a daily ρ/CI. A dedicated regression test (`TestAnnualizationFlipRegression`) asserts the annualized value would flip the verdict on a fixture, guarding against exactly this unit-mismatch class.

Both inclusive boundary comparisons use `_BOUNDARY_EPS = 1e-9` to absorb float64 subtraction noise at exact-by-construction boundaries (e.g. `0.30 - 0.10 == 0.19999999999999998` in IEEE 754 binary64) without blurring the test suite's much larger intentional distinctions (`1e-7`).

### `verdict_copy(verdict: str) -> str`

Returns the human-readable rationale string for one of the 5 verdict classes (`_VERDICT_COPY` dict, see Types below). Every entry across the set states the sufficient-not-necessary framing; **none ever renders "stop unjustified."** `NOT_MET`'s copy is deliberately scoped as an **evidence statement, not a settled fact** ("this evidence does not demonstrate... not disproven either") -- the K&L random-walk-drag theorem is presented as the conditional theorem it is, never as a claim proven true for that specific symphony (PM ruling, see `DE-GUARD-ALPHA-PRECONDITIONS-001` in `DECISIONS.md`). `NOT_MET`'s copy also states explicitly that variance/drawdown reduction can survive under an IID return process -- the CRRA-EU objective legitimately values that independent of this precondition.

## Types

### `N_MIN_OBS: int = 40`

`[PM-ASSUMED]` per the feature plan's AC-3, aligned with `synthetic_history._MC_WARMUP_TRADING_DAYS` (39). **Known retention interaction (honest disclosure, PM live-gate finding, 2026-07-24):** the droplet's `shadow_history` table retains ~23 trading days, below this floor -- so the SHADOW sample (see `analytics.get_shadow_current_return_daily_series`) will practically render `INSUFFICIENT_DATA` in production until the operator extends retention. This is a known product-level knob, not a defect this module introduces or is scoped to fix; `N_MIN_OBS` itself stays unchanged and principled. The REPLAY sample (`autotuner.build_if_held_replay_series`, N≈250) is unaffected. See `DE-GUARD-ALPHA-PRECONDITIONS-001`'s Live-Gate Correction section in `DECISIONS.md`.

### `_Z_95_TWO_SIDED: float = 1.96`

95% two-sided normal critical value, the Bartlett (1946) / Box & Jenkins (1976) white-noise asymptotic standard-error multiplier for a sample lag-1 ACF (`SE ≈ 1/√N`). Shared, by citation, with `tests/guard_preconditions/_reference_stats.py`'s independent `Z_95` constant.

### `_BOUNDARY_EPS: float = 1e-9`

Float-comparison tolerance for `classify_stop_justification`'s two inclusive boundary checks -- see that function's entry above.

### `_VERDICT_COPY: dict[str, str]`

The 5 verdict-class → rationale-string mapping `verdict_copy` reads from. Not part of the public API surface (leading underscore); documented here because its content is the AC-4 honesty contract itself, not incidental.

## Internal Dependencies

None -- pure stdlib (`dataclasses`, `math`). Consumed by `autotuner.build_if_held_replay_series`'s caller (`app.py`'s `GET /api/guard-alpha-preconditions`, see `docs/generated/app.md`) and by `analytics.get_shadow_current_return_daily_series`'s caller (same route). Golden fixtures: `tests/fixtures/math/persistence_stats_ar1_positive.json` (AR(1) φ=0.55 -- `SUFFICIENT_LIKELY`), `persistence_stats_negative_edge.json` (same AR(1) shocks, negative drift -- `NEGATIVE_EDGE`), `persistence_stats_iid_control.json` (φ=0 -- `NOT_MET`).

See `DE-GUARD-ALPHA-PRECONDITIONS-001` in `DECISIONS.md` for the sample-design ruling, the citation correction, and the deferred regime-switching scope boundary.
