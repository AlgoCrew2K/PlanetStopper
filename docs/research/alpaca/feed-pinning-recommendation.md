# Alpaca `feed=` Pinning Recommendation

**Date:** 2026-05-12
**Author:** composer-alpaca-integration
**Status:** PROPOSAL — awaiting user decision on subscription tier

---

## Diagnostic

Three Alpaca Market Data call sites exist across the execution codebase. Two are unpinned; one is pinned.

### Call site 1 — `alpha_bot_execution.py` line 163 (3-year daily bars, Monte Carlo input)

```python
url = f"{ALPACA_BASE_URL}/stocks/bars?symbols={symbol_string}&timeframe=1Day&start={start_date}&limit=10000&adjustment=split"
```

No `feed=` parameter. Alpaca's documented default when `feed=` is omitted on the `/v2/stocks/bars`
endpoint is `sip` (the consolidated tape, requiring Algo Trader Plus subscription). This is the
dataset that feeds `run_monte_carlo()` and `calculate_20d_vol()` / `calculate_14d_atr_pct()` in
`math_engine.py`.

### Call site 2 — `alpha_bot_execution.py` line 232 (intraday 1-minute bars, VWAP input)

```python
url = f"{ALPACA_BASE_URL}/stocks/bars?symbols={symbol_string}&timeframe=1Min&start={start_utc_str}&limit=1000"
```

No `feed=` parameter. Same implicit SIP default. This is the dataset that drives `fetch_intraday_vwaps()`,
which produces `live_vwaps`. The `live_vwaps` dict is consumed directly at lines 311, 423, 465, 480–485,
and 810 — including VWAP breakdown detection (`is_vwap_broken`, `is_vwap_bleed_broken`) and all
Composer liquidation triggers. This is the most operationally critical of the two unpinned calls.

### Call site 3 — `synthetic_history.py` line 36 (all timeframes, fixture/replay input)

```python
url = f"{ALPACA_BASE_URL}/stocks/bars?symbols={symbol_string}&timeframe={timeframe}&start={start_str}&end={end_str}&limit=10000&adjustment=split&feed=iex"
```

Explicitly pins `feed=iex`. This is the data source for synthetic history generation, which produces
the training corpus consumed by `run_monte_carlo()` and the math-layer volatility functions during
backtesting and Optuna walk-forward optimization.

**Diagnostic confirmed:** both `alpha_bot_execution.py` call sites genuinely lack `feed=`; `synthetic_history.py`
explicitly uses `feed=iex`; all three call sites otherwise use the same `ALPACA_BASE_URL`
(`https://data.alpaca.markets/v2`) and the same `APCA-API-KEY-ID` / `APCA-API-SECRET-KEY` header pattern.

---

## Failure Modes by Tier

### Basic tier (free / starter)

When `feed=` is omitted on a Basic account, Alpaca's documented behavior for `/v2/stocks/bars` is a
**403 Forbidden** response with a message indicating the endpoint requires an upgraded plan. There is
no silent IEX fallback on the API side at the HTTP level — the endpoint itself is gated. The failure
therefore presents as a non-200 status code.

For call site 1 (Monte Carlo): the existing retry loop (3 attempts, `time.sleep(2 * (attempt + 1))`)
will exhaust retries and `fetch_alpaca_history()` will return an empty `historical_data` dict, causing
`run_monte_carlo()` to return the default `100.0` — meaning the MC gate never fires, and GuardAlpha
operates without probabilistic gating. This is a silent degradation, not a hard crash.

For call site 2 (VWAP): the single `try/except` block catches the non-200 silently (`print(f"Error
fetching VWAP for batch {batch}: {e}")` only fires on `requests.RequestException`, not on a 200-vs-403
branch check). A 403 response returns `vwap_data = {}`, which causes `valid_vwap_weight` to stay at
`0.0` — below the `> 0.5` gate at line 662 — meaning the VWAP breakdown check silently never fires.
GuardAlpha runs without VWAP protection. Again, silent degradation.

**On Basic tier, both unpinned calls will 403 and both protections will silently disable. There is no
operator-visible alarm.**

### Algo Trader Plus tier

Omitting `feed=` defaults to SIP on this tier. The calls succeed. However, the tier dependency is
implicit in code — there is nothing in the codebase that documents or enforces the subscription
requirement. A future operator or operator account downgrade will hit the silent failure described
above with no advance warning.

---

## Math-Input Consistency Risk

This is the more important issue, independent of subscription tier.

`synthetic_history.py` fetches training data with `feed=iex`. `alpha_bot_execution.py` fetches live
execution data with the implicit SIP default (or nothing on Basic). These are categorically different
data feeds:

- **IEX** reports trades executed on the IEX exchange only, which represents roughly 2–3% of US equity
  volume. Prices can differ from the national best bid/offer by measurable amounts, especially for
  thinly-traded or volatile tickers. High/low ranges are narrower because they exclude off-exchange
  prints.
- **SIP** (Securities Information Processor) is the consolidated tape — all US exchange prints, all
  venues. It is the de facto "market price" for OHLCV purposes.

**Impact on `calculate_20d_vol` and `calculate_14d_atr_pct`:**

Both functions consume `historical_data[date][ticker]["daily_ret"]`, `"high"`, `"low"`, `"close"` —
all sourced from `fetch_alpaca_history()` via the unpinned SIP call in production, but from
`fetch_bars(..., feed=iex)` in synthetic history. The daily return distributions, ATR ranges, and
rolling volatility estimates will differ between the two feeds. The Optuna walk-forward tuner
(`autotuner.py`) optimizes parameters against the IEX corpus. Those parameters are then applied during
live execution against SIP data. The parameter surface is mis-matched to the input distribution.

**Impact on `run_monte_carlo`:**

The neighbor-matching engine computes Euclidean distances in `(spy_return, rolling_vol)` space. The
rolling vol dimension is trained on IEX SPY daily returns; live vol is computed from SIP SPY daily
returns. For a liquid benchmark like SPY the feed difference is small (SPY volume is predominantly on
listed exchanges), but for the holdings in each symphony this divergence can be meaningful.

**Impact on VWAP (`fetch_intraday_vwaps`, call site 2):**

Intraday VWAP is computed from 1-minute bars. On IEX, intraday bars exclude substantial volume
(dark pools, off-exchange prints, other venues). The SIP-default intraday bars include all this volume.
VWAP levels differ. The VWAP breakdown thresholds (`VWAP_CROSS_HWM_PCT`, `VWAP_BLEED_ARM_PCT`) were
likely calibrated (manually or via Optuna) against IEX-derived intraday data from synthetic history.
Applying those thresholds to SIP-based live VWAP means the signal fires at a different point in
price/volume space than intended.

**Summary:** The feed mismatch is a systematic bias between training inputs and live execution inputs
across all three math-layer consumers. The consistency risk is not hypothetical — it is present in
every production run today on an Algo Trader Plus account.

---

## Options

### Option A: Pin `feed=iex` globally

Change both `alpha_bot_execution.py` call sites to append `&feed=iex`, making all three call sites
byte-equivalent on this axis. No change required to `synthetic_history.py`.

**Pros:**
- Eliminates the training/execution feed mismatch — the single most important correctness issue.
- Works on any subscription tier, including Basic. Removes the implicit Algo Trader Plus dependency.
- Minimal diff: two one-line URL string changes.
- `synthetic_history.py` already uses this; the pattern is established in the codebase.
- Fixture files captured via `/api-fixture` against IEX will remain consistent with production behavior.

**Cons:**
- IEX represents ~2–3% of US equity volume. For thinly-traded ETFs or leveraged products, IEX may
  have gaps (no prints) on some minutes, producing zero-volume bars or missing bars entirely.
- Operators on Algo Trader Plus give up the richer SIP tape. If they want full-market VWAP, they
  cannot get it without changing `synthetic_history.py` too.
- Does not surface the subscription tier dependency — it eliminates it, which may feel like a
  downgrade to an Algo Trader Plus subscriber who expects consolidated tape data.

**Test impact:** All existing fixture-driven tests that were captured with `feed=iex` (or no feed
specified, given Basic-tier test accounts) continue to pass without modification.

**synthetic_history.py change:** None required.

**Fixture replay:** Fully compatible. IEX fixtures remain valid.

---

### Option B: Pin `feed=sip` explicitly

Change both `alpha_bot_execution.py` call sites to append `&feed=sip`. Also change
`synthetic_history.py` line 36 from `&feed=iex` to `&feed=sip`.

**Pros:**
- Makes the Algo Trader Plus requirement explicit in code rather than implicit in documentation (or
  absent entirely). A Basic-tier account will 403 immediately at first call rather than silently
  degrade.
- Aligns training data (synthetic history) with production data — both use SIP — resolving the
  feed mismatch.
- Full-market VWAP for operators who have the subscription.

**Cons:**
- Hard requirement: all operators must have Algo Trader Plus. There is no graceful fallback for
  Basic-tier accounts — they get 403 on every bar fetch.
- `synthetic_history.py` must change, which is a larger diff and affects the fixture corpus.
- Any existing IEX-captured fixtures used in tests will produce different numeric results under SIP.
  All golden-fixture tests against `synthetic_history.py` outputs must be recaptured.
- IEX fixtures captured via `/api-fixture` are no longer representative of production data.

**Test impact:** Golden-fixture tests for `calculate_20d_vol`, `calculate_14d_atr_pct`, `run_monte_carlo`
need new SIP-sourced fixtures. Non-trivial capture effort. All tests that assert on bar-level numeric
values must be recaptured.

**synthetic_history.py change:** Line 36 changes from `&feed=iex` to `&feed=sip`.

**Fixture replay:** IEX fixtures are no longer valid as production-representative fixtures for SIP
paths. A fixture migration is required before implementation can be verified.

---

### Option C: Env-var-driven `feed=`

Introduce an `ALPACA_DATA_FEED` env var (defaulting to `iex` for safety) and apply it at all three
call sites. Example:

```python
ALPACA_DATA_FEED = os.getenv("ALPACA_DATA_FEED", "iex")
# ...
url = f"...&feed={ALPACA_DATA_FEED}"
```

**Pros:**
- Operators choose at deployment time: `iex` for Basic/consistent-with-training, `sip` for
  Algo Trader Plus / full-market.
- Default of `iex` is safe — no regression for Basic accounts, consistent with `synthetic_history.py`
  current behavior.
- Follows the existing project pattern: virtually every other parameter in `alpha_bot_execution.py`
  is env-var driven (`TRIGGER_THRESHOLD_PCT`, `VWAP_CROSS_HWM_PCT`, `NEIGHBOR_K`, etc.).
- Allows `synthetic_history.py` to optionally pick up the same env var, enabling full consistency
  when set to `sip`.

**Cons:**
- Adds a config knob that operators must know to set. Default of `iex` means an Algo Trader Plus
  subscriber who never sets the var gets IEX data despite having SIP access.
- The training/execution feed mismatch is only resolved if the operator consciously sets the same
  value for both `alpha_bot_execution.py` and `synthetic_history.py` paths (or if both read from the
  same env var — which requires `synthetic_history.py` to adopt it too).
- Complicates test matrix: tests must parameterize over feed values or choose a canonical test feed.
  The project's fixture-first mandate means fixtures must be captured per feed value.
- "Config-driven" does not protect against misconfiguration — an operator could run `synthetic_history`
  with `iex` and `alpha_bot_execution` with `sip`, recreating the mismatch under a different label.

**Test impact:** Tests should set `ALPACA_DATA_FEED=iex` explicitly in their env setup. Existing IEX
fixtures remain valid when the env var is `iex`. A new suite of SIP fixtures would be required only
if SIP behavior is tested.

**synthetic_history.py change:** Replace hardcoded `&feed=iex` with `&feed={ALPACA_DATA_FEED}` (or
keep `iex` hardcoded there and accept a possible mismatch if the operator sets `ALPACA_DATA_FEED=sip`
in the execution path only — this should be explicitly documented as a misconfiguration risk).

**Fixture replay:** IEX fixtures remain valid under default. SIP fixtures must be separately captured
for SIP path validation.

---

## Recommendation

Option A (pin `feed=iex` globally) is the recommended starting point, with Option C as the upgrade
path if the operator confirms Algo Trader Plus and wants SIP data.

The primary argument is correctness, not tier compatibility: the training/execution feed mismatch
(synthetic history uses IEX, live execution uses implicit SIP) is a systematic bias that affects every
math-layer consumer today regardless of subscription tier. Option A eliminates that mismatch immediately
with a two-line change and zero fixture migration cost. Option B also eliminates the mismatch but at
the cost of a full SIP fixture recapture, a hard Basic-tier break, and a larger diff to `synthetic_history.py`.
Option C defers the decision to the operator without guaranteeing the mismatch is resolved.

The secondary argument is failure mode quality: Option A turns a silent degradation (SIP 403 returns
empty data, math layers default-safe) into a functioning system on any tier. The current implicit SIP
default is the worst of all worlds — it works only on one tier, fails silently on another, and is
inconsistent with the training data on both.

If the operator is confirmed on Algo Trader Plus and the decision is made to move to SIP everywhere,
the correct sequence is: (1) capture SIP fixtures for all three call sites via `/api-fixture` with
provenance note "captured-from-producer on Algo Trader Plus account"; (2) migrate golden-fixture tests;
(3) implement Option B or C. Skipping fixture recapture before implementation is a Gate-1 fail per
project rules.

---

## Open Questions for User

1. **What subscription tier is the operator account on?** Basic or Algo Trader Plus? This determines
   whether Option B is viable at all and whether the current implicit SIP default is even working.

2. **Has the training/execution feed mismatch caused observable calibration drift?** VWAP thresholds
   and Optuna-tuned parameters were calibrated against IEX data. If the system is running live on SIP,
   the parameter values may be mis-scaled. A re-run of walk-forward optimization under Option A (pure
   IEX) vs Option B (pure SIP) would quantify the drift — worth doing before choosing a direction.

3. **Should `synthetic_history.py` change to match the chosen feed?** If Option A is chosen,
   `synthetic_history.py` stays as-is (already IEX). If Option B or C is chosen, `synthetic_history.py`
   must be updated in the same PR to preserve the training/execution consistency guarantee.

4. **What is the env var name if Option C is chosen?** Suggested: `ALPACA_DATA_FEED`. Needs to be
   added to `.env.example` (if one exists) and documented in the operator runbook.

5. **Are there existing `/api-fixture` captures for the Alpaca bars endpoint?** If so, were they
   captured with or without `feed=iex`? Fixtures captured without a `feed=` parameter represent SIP
   data on Algo Trader Plus or a 403 on Basic — they are not interchangeable with IEX fixtures.
   Provenance must be verified before any fixture is used in a test covering the new pinned call sites.
