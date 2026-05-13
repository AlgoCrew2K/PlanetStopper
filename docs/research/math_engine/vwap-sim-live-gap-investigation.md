# Task #24 — VWAP Breakdown Sim/Live Schema Gap Investigation

**Date:** 2026-05-12
**Branch:** worktree-agent-a78df168e9929ab8f
**Investigation type:** Read-only, pre-implementation analysis
**Risk class:** Real-money calibration concern

---

## Current State

### synthetic_history.py tick schema (line-cited)

`generate_synthetic_history()` produces a `history_125d` dict keyed by `sym_id → date_str → list[tick]`.

Each tick dict is assembled at **lines 235–242**:

```python
ticks.append({
    "time":         ts[11:16],          # str  — "HH:MM" extracted from ISO timestamp
    "return":       agg_ret * 100.0,    # float — allocation-weighted intraday return, pct
    "mc_prob":      mc_prob,            # float — Monte Carlo percentile (0–100)
    "vol":          vol,                # float — 20-day realized vol (pct)
    "vwap_diff":    weighted_vwap_diff, # float — allocation-weighted (c-v)/v, raw (not pct)
    "base_atr_pct": base_atr           # float — 14-day ATR as pct of price
})
```

**Total fields: 6.** There is NO `valid_vwap_weight` field in the tick dict.

#### How `weighted_vwap_diff` is computed (lines 211–230)

The inner loop accumulates across holdings **only for tickers that have intraday bars on that date AND for that minute index** (`i < len(intraday_by_date[date_str][ticker])`):

```python
for h in holdings:
    ticker = h["ticker"]
    alloc = h["allocation"]
    if ticker in intraday_by_date[date_str] and i < len(intraday_by_date[date_str][ticker]):
        bar = intraday_by_date[date_str][ticker][i]
        c = bar['c']; v = bar['vwap']
        y_close = yesterday_closes.get(ticker, c)
        if y_close > 0:
            ret = (c - y_close) / y_close
            agg_ret += alloc * ret
        if v > 0:
            weighted_vwap_diff += alloc * ((c - v) / v)
        valid_alloc += alloc   # ← tracks coverage but NEVER written to tick dict
```

Key finding: **`valid_alloc` is computed per-tick (line 230) but is never included in the tick dict**. It is silently discarded after the inner loop. This is the direct source of the gap.

#### Coverage-proxy fields — assessment

| Field | Proxy for coverage? | Verdict |
|-------|---------------------|---------|
| `vwap_diff` | Partially — uncovered tickers contribute 0.0, pulling diff toward zero | No — it's a directional diff, not a coverage fraction |
| `valid_alloc` | Exact analog of `valid_vwap_weight` | Computed but dropped (line 230) |
| `vol`, `base_atr_pct` | Historical, coverage-unaware | No |
| `mc_prob`, `return` | Aggregate signal | No |

**Conclusion:** There is NO existing tick-schema field that reliably proxies for `valid_vwap_weight`. The computed-but-discarded `valid_alloc` is the exact quantity needed.

---

### autotuner.py consumption of tick schema (line-cited)

`run_simulation()` reads each tick at **lines 153–157**:

```python
ret        = tick.get("return", 0.0)
mc         = tick.get("mc_prob", 50.0)
vol        = tick.get("vol", 1.0)
vwap_diff  = tick.get("vwap_diff", 0.0)
base_atr_pct = tick.get("base_atr_pct", vol)
```

VWAP exit logic at **lines 222–237** (pre-canonical inline code, not delegating to `compute_vwap_breakdown_update`):

```python
is_vwap_broken = False
is_vwap_bleed_broken = False
if vwap_diff < 0:                                       # ← ONLY condition gating VWAP signals
    if safe_hwm >= p.get("VWAP_CROSS_HWM_PCT", 1.0) and ret < safe_hwm:
        vwap_ticks += 1
        if vwap_ticks >= 3: is_vwap_broken = True
    else: vwap_ticks = 0
    vwap_bleed_arm_pct = math_engine.compute_vwap_bleed_arm_threshold(...)
    if ret <= vwap_bleed_arm_pct:
        vwap_bleed_ticks += 1
        if vwap_bleed_ticks >= p.get("VWAP_BLEED_TICKS", 10): is_vwap_bleed_broken = True
    else: vwap_bleed_ticks = 0
else:
    vwap_ticks = 0
    vwap_bleed_ticks = 0
```

**Critical finding:** The autotuner's inline simulation gates VWAP signals solely on `vwap_diff < 0`. It does NOT call `compute_vwap_breakdown_update` and does NOT check any `valid_vwap_weight` threshold. This is the exact divergence from live behavior.

---

### Canonical math_engine.compute_vwap_breakdown_update signature (lines 286–360)

```python
def compute_vwap_breakdown_update(
    is_triggered: bool,
    valid_vwap_weight: float,    # ← gate condition (a)
    weighted_vwap_diff: float,   # ← gate condition (b)
    safe_hwm: float,
    current_return: float,
    vwap_cross_hwm_pct: float,
    vwap_bleed_arm_pct: float,
    vwap_bleed_ticks_threshold: int,
    current_vwap_ticks: int,
    current_vwap_bleed_ticks: int,
) -> tuple[int, int, bool, bool]:
```

Gate (math_engine.py line 341):
```python
if not (valid_vwap_weight > VWAP_WEIGHT_THRESHOLD and weighted_vwap_diff < 0):
    return 0, 0, False, False     # BRANCH 2: both counters reset, no signals
```

- `VWAP_WEIGHT_THRESHOLD = 0.5` (line 282)
- Both conditions are strict: weight uses `>` (not `>=`), diff uses `<` (not `<=`)

In **live execution** (alpha_bot_execution.py lines 476, 642–653):
1. `weighted_vwap_diff, valid_vwap_weight = math_engine.compute_vwap_signals(holdings, live_vwaps)` — both computed from real Alpaca VWAP data
2. Both are passed to `compute_vwap_breakdown_update`, so the weight threshold gate is enforced

In **simulation** (autotuner.py lines 222–237):
1. Only `vwap_diff` is read from the tick dict
2. Gate is `if vwap_diff < 0` — effectively assumes `valid_vwap_weight = 1.0` always
3. Does not call `compute_vwap_breakdown_update` at all

---

## Calibration Risk Concretized

### What `valid_vwap_weight` means in live

`math_engine.compute_vwap_signals()` (lines 233–244): for each holding, it adds `allocation` to `valid_vwap_weight` only when:
- the ticker is present in `live_vwaps` (Alpaca returned bars for it today)
- the VWAP value is > 0

So `valid_vwap_weight` is the **portfolio-fraction covered by valid VWAP data**. A value of 0.3 means only 30% of the portfolio's weight has live VWAP readings.

### When does the gate bite in live?

The gate `valid_vwap_weight > 0.5` blocks VWAP signals whenever less than 50% of the portfolio (by allocation weight) has Alpaca intraday coverage. This happens in real scenarios:
- Thinly-traded ETFs or leveraged instruments with IEX feed gaps
- Early-morning ticks before all instruments have opened
- Any tick where Alpaca returns empty bars for one or more larger-weight holdings

### Quantitative estimate of over-firing

Let `P_gate` = probability, per tick, that `valid_vwap_weight <= 0.5` in live (i.e., gate would block the signal). The calibration drift is:

**Extra VWAP signal firing rate in sim = P_gate × P(vwap_diff < 0)**

For a typical AlphaBot symphony:
- Portfolios are concentrated (3–8 holdings, ETFs, highly liquid): `P_gate` is likely **5–20%** on most days (IEX feed is reasonably complete for large ETFs), but can spike to **30–60%** on:
  - First 5–15 minutes of session (bars accumulating)
  - Days with IEX feed anomalies or halted instruments
- `P(vwap_diff < 0)` is roughly 50% (random walk intraday)

**Rough estimate:** Sim fires VWAP signals **5–15% more often** on ordinary days, and **15–30% more often** on low-coverage days that drive the worst-case live non-firing scenarios.

More critically: the calibration drift is **asymmetric and adversarial** — the sim over-tunes precisely on the regime (low coverage / partial data) where live most often fails to fire. Optuna will set aggressive VWAP parameters (`VWAP_CROSS_HWM_PCT` lower, `VWAP_BLEED_TICKS` shorter) that are calibrated against a signal that fires more than live will allow.

**Affected Optuna parameters:**
- `VWAP_CROSS_HWM_PCT` — directly tuned against VWAP Breakdown signals
- `VWAP_BLEED_TICKS` — directly tuned against VWAP Bleed Cut signals
- `VWAP_BLEED_MULTIPLIER` — indirectly, since bleed arm threshold interacts with VWAP gate

---

## Options

### Option X: Constant 1.0 in caller (trivial)

**Mechanism:** In `autotuner.py run_simulation()`, replace the inline VWAP gate with a call to `compute_vwap_breakdown_update(..., valid_vwap_weight=1.0, ...)`.

**Pro:**
- Zero schema change
- Eliminates the un-canonical inline code divergence (simulation now uses the same math function as live)
- Trivial to implement (1 cycle)

**Con — critical:**
- Sim still behaves as if every tick has 100% VWAP coverage, which is more permissive than the **average** live case
- Does NOT model low-coverage days at all
- Closes the "canonical function" gap but leaves the "coverage modeling" gap open
- Net calibration improvement: modest (stops the inline drift) but does not fix the root cause

**Assessment:** Better than status quo but not a real fix. Primarily a code-quality improvement that happens to partially reduce overfitting.

---

### Option Y: Extend tick schema with `valid_vwap_weight` (recommended)

**Mechanism:** In `synthetic_history.py process_day()` inner loop, the `valid_alloc` variable (line 230) already computes exactly what is needed. Extend the tick dict to emit it:

```python
# In synthetic_history.py ticks.append({...}) — add one field:
"valid_vwap_weight": valid_alloc,   # float — sum of allocations with live bar coverage
```

In `autotuner.py run_simulation()`, read the new field and pass it to `compute_vwap_breakdown_update`:

```python
valid_vwap_weight = tick.get("valid_vwap_weight", 1.0)   # fallback 1.0 for backward compat
# ... then call compute_vwap_breakdown_update with real valid_vwap_weight
```

**Pro:**
- Closes the gap directly and completely
- `valid_alloc` already tracks exactly the right quantity (sum of allocations for tickers with intraday bars on that minute)
- Uses the canonical `compute_vwap_breakdown_update` in simulation — full behavioral parity with live
- Modeling is accurate: days where Alpaca had partial bar coverage (e.g., small-cap ETFs with IEX gaps) will suppress VWAP signals in sim the same way live does

**Con:**
- Cache invalidation: all existing `cache/synthetic_history_*.json` files will have the old 6-field schema. Must be invalidated (delete cache files or bump cache key)
- Existing Optuna study parameters become stale — recalibration required (see Section 5)
- Moderate implementation cost: 1–2 cycles (schema change + autotuner read + cache invalidation + test fixture update)

**Assessment:** The correct fix. The data is already computed; it just needs to be surfaced and consumed.

---

### Option Z: Coverage-aware `vwap_diff` (schema trick)

**Mechanism:** In `synthetic_history.py`, continue computing `weighted_vwap_diff` using only covered tickers (current behavior). Rename the emitted field to `coverage_weighted_vwap_diff` to document the semantics. In `autotuner.py`, pass `valid_vwap_weight=1.0` to `compute_vwap_breakdown_update` with the argument that the diff already factors in coverage (uncovered tickers contribute 0.0, so a strongly negative diff implies the covered fraction IS below VWAP).

**Pro:**
- No tick schema extension needed (still 6 fields, just documenting existing semantics)
- Preserves cache compatibility

**Con — critical logic error:**
- The argument is invalid. A `weighted_vwap_diff` of -0.001 (barely negative) with 10% coverage is very different from -0.001 with 100% coverage. The weight gate exists precisely to exclude the low-sample case where a single holding's drift produces a spuriously negative diff. Passing 1.0 unconditionally does not fix this.
- The live gate does NOT look at the magnitude of `vwap_diff` as a coverage proxy — it looks at `valid_vwap_weight` independently
- This option masks the problem with incorrect semantics without actually improving calibration

**Assessment:** Not recommended. Introduces a misleading semantic that could confuse future maintainers and does not fix the calibration gap.

---

### Option W: Hybrid — conditional fallback with floor

**Mechanism:** Extend the tick schema with `valid_vwap_weight` (same as Option Y), but instead of a hard block when weight <= 0.5, attenuate the signal strength. Specifically, when `0.0 < valid_vwap_weight <= 0.5`, scale `weighted_vwap_diff` by `valid_vwap_weight / VWAP_WEIGHT_THRESHOLD` rather than zero it out.

**Pro:**
- Models partial-coverage days as a degraded (rather than absent) signal
- More realistic: in live, 40% coverage still provides directional information

**Con:**
- Diverges from live behavior: live is binary (weight > 0.5 = evaluate, else = reset counters). A smooth attenuation would create a new calibration surface that live never sees.
- Increases model complexity for uncertain gain
- Requires a new constant and a new test fixture

**Assessment:** Interesting but premature. Would require verifying live behavior should be changed to match. Out of scope for a calibration-parity fix. Revisit only if the binary gate in live is itself re-evaluated.

---

### Option V: Re-write autotuner to use live VWAP coverage data from Alpaca

**Mechanism:** During the walk-forward simulation, for each historical date, fetch actual Alpaca 1-min bars for the symphony's tickers (same source as `intraday_by_date` in `synthetic_history.py`) and compute `valid_vwap_weight` at each minute from the real bar availability. This would be perfectly accurate.

**Pro:**
- Gold-standard accuracy — sim would use exactly the same coverage logic as live

**Con:**
- `synthetic_history.py` already does this: `intraday_by_date[date_str]` IS the Alpaca 1-min bar dict. The coverage is already implicitly tracked in the inner loop. `valid_alloc` at line 230 IS the per-minute real coverage fraction computed from actual Alpaca data.
- This option is effectively Option Y — the data is already there; we just need to emit it

**Assessment:** Equivalent to Option Y. No additional implementation needed beyond emitting the already-computed value.

---

## Recommendation

**Implement Option Y: Extend tick schema with `valid_vwap_weight`.**

### Rationale

1. **The data is already computed.** `valid_alloc` at line 230 of `synthetic_history.py` is the exact quantity needed. The implementation delta is: (a) rename to `valid_vwap_weight` or add it as an additional key, (b) include it in the tick dict, (c) read it in `autotuner.py`, (d) switch to calling `compute_vwap_breakdown_update` with real values.

2. **It closes the calibration gap completely.** After the fix, sim and live will use identical gating logic driven by identical coverage data (both sourced from the same Alpaca intraday bars).

3. **It eliminates the inline code divergence.** The autotuner's inline VWAP state machine (lines 222–237) is a duplicate of `compute_vwap_breakdown_update` that has already drifted from the canonical implementation. Replacing it with a direct call removes a maintenance liability.

4. **Option X is insufficient.** Passing `valid_vwap_weight=1.0` eliminates the inline divergence but leaves the over-firing problem for low-coverage days — the exact regime where calibration matters most.

5. **Option Z is logically incorrect.** The coverage-proxy argument for vwap_diff is invalid.

### Trade-offs

| Dimension | Assessment |
|-----------|------------|
| Implementation effort | Low-medium: 1 targeted cycle (schema + autotuner + cache invalidation + test fixture) |
| Cache compatibility | Breaking: all `cache/synthetic_history_*.json` files must be deleted or cache key bumped |
| Optuna study validity | Parameters must be re-tuned after fix (see Section 5) |
| Live code change required | None — fix is entirely in sim/calibration path |
| Risk of regression | Low — the change makes sim MORE restrictive (fires fewer signals), never more permissive |

---

## Calibration Impact

**Assessment: Required-to-recalibrate.**

Existing Optuna study parameters in `optuna_studies.db` (if any exist on the production host) should be treated as **invalidated** after Option Y lands. Reasoning:

1. The fix makes the VWAP gate more restrictive in simulation — VWAP Breakdown and VWAP Bleed Cut signals will fire less often across all 125 walk-forward training days.
2. Parameters that were optimal under the over-permissive regime (particularly `VWAP_CROSS_HWM_PCT` tuned lower, `VWAP_BLEED_TICKS` tuned shorter) will no longer be optimal. The correct parameters in the fixed sim will reflect real coverage rates and may shift materially.
3. The magnitude of shift depends on how often IEX feed gaps affected the training period. For a liquid ETF portfolio (e.g., all large-cap ETFs), drift may be modest (5–10% parameter shift). For portfolios with thinner instruments, drift could be substantial.
4. The safe posture for a real-money system is to re-run the full walk-forward on the next scheduled autotuner cycle (Friday EOD), which will automatically supersede the stale study with corrected calibration.

**Action:** After deploying the fix, either:
- Delete `optuna_studies.db` to force a clean re-run (recommended for correctness)
- OR allow the next Friday EOD autotune to run 500 Bayesian trials against the corrected sim, which will organically move parameters toward the correct optimum (acceptable if `load_if_exists=True` is in use — Optuna will add trials rather than restart)

The load_if_exists=True path (autotuner.py line 299) means no action is strictly required; the next autotune will continue from existing trials and new trials will populate the corrected search surface. However, existing best parameters from the corrupted study may be retained as seed until enough new trials push them out. Deleting the DB is cleaner.

---

## Implementation Plan (if user accepts Option Y)

### Cycle A — Schema Extension (synthetic_history.py)
1. In `process_day()` inner loop (line 230): rename local `valid_alloc` to `valid_vwap_weight` for clarity
2. Add `"valid_vwap_weight": valid_vwap_weight` to the tick dict in `ticks.append()` (line 235–242)
3. Invalidate cache: update the cache key hash to include a schema version string, OR add a `SCHEMA_VERSION = 2` constant to `synthetic_history.py` that is incorporated into `cache_file` path. This prevents stale 6-field cache files from silently feeding the autotuner.
4. Tests: update any golden-fixture tests that assert on the tick dict schema to include the new field

### Cycle B — Autotuner Consumption (autotuner.py)
1. In `run_simulation()`, read `valid_vwap_weight = tick.get("valid_vwap_weight", 1.0)` alongside existing tick reads (fallback=1.0 preserves pre-cache-refresh backward compatibility)
2. Replace the inline VWAP state machine (lines 222–237) with a call to `math_engine.compute_vwap_breakdown_update(...)` using both `valid_vwap_weight` and `vwap_diff` from the tick dict. Compute `vwap_bleed_arm_pct` beforehand (already done at line 229).
3. Preserve state variables `vwap_ticks` and `vwap_bleed_ticks` using the returned values from `compute_vwap_breakdown_update`

### Cycle C — Test Coverage (quant-test-writer)
1. Add a golden-fixture test for `compute_vwap_breakdown_update` with `valid_vwap_weight <= 0.5` input — must return `(0, 0, False, False)`
2. Add a synthetic_history tick dict fixture asserting `valid_vwap_weight` field is present and in `[0.0, 1.0]`
3. Add an autotuner sim regression test: with a synthetic tick where `valid_vwap_weight = 0.3`, confirm VWAP signals do not fire regardless of `vwap_diff`

### Post-deployment
1. Delete stale cache files: `cache/synthetic_history_*.json`
2. On next Friday EOD, observe autotuner output — new best parameters should reflect the corrected (more restrictive) VWAP calibration surface
3. Optionally delete `optuna_studies.db` to ensure a clean restart with the fixed calibration

**Total cycles: 3** (A: schema, B: autotuner, C: tests). Can be compressed to 2 if the implementation agent handles both A+B in one cycle.
