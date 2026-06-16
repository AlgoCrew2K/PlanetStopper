# advisors/lens_technicals

> Technicals lens producer: MA posture (50/200-day SMA), market breadth, and 20-day momentum from Alpaca daily bars — reuses synthetic_history cache, never raises, honest-availability contract.

**Source:** `advisors/lens_technicals.py`
**Last updated:** 2026-06-15

## Overview

`advisors/lens_technicals.py` is the technicals lens producer for the off-hours Market Prism pipeline (`advisors/lens_pipeline.py`). It is called via `ai_advisor._build_technicals_section()` (Pass 1 of the pipeline) and computes three price/trend/breadth indicators from Alpaca v2 daily bar history.

**No new Alpaca client is introduced.** Bar history is fetched through `synthetic_history.fetch_bars` — the same 270-calendar-day (≈250 trading days) window already used by the autotuner. No new credential requirement.

**Honest-availability contract (CC-3):** the module never fabricates a payload. Every failure path — transient network error, authoritative empty bars response, insufficient bar history — returns `available=False` with a named `reason`. A successful fetch with valid indicators returns `available=True`.

**Never raises (D-1 / CC-3):** every public entry point catches all exceptions. `reason` values on failure are `type(exc).__name__` only — never `str(exc)`. Named label `"no_bars_returned"` and `"insufficient_bar_history"` are used for authoritative (non-exception) unavailability.

**Off-execution-path:** this module is imported lazily inside `ai_advisor._build_technicals_section()` (CC-2). It is never imported at module level in `alpha_bot_execution.py`.

## Public API

### `_fetch_technicals(universe: list[str]) → dict`

Fetch price/trend/breadth technicals for the universe.

Calls `_get_bars` (the mockable test seam) with bounded retry on transient errors. Authoritative empty responses (`{}`) are not retried.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `universe` | `list[str]` | Ticker symbols to compute indicators for. May be empty (yields `available=False, reason="no_bars_returned"`). |

**Returns:**

| Key | Type | Description |
|-----|------|-------------|
| `available` | `bool` | `True` only when bars were fetched and at least one indicator was computable. |
| `ma_posture` | `dict \| None` | Per-ticker `{above_sma50: bool \| None, above_sma200: bool \| None}`. `None` when unavailable. |
| `breadth` | `float \| None` | Fraction of universe above 50-day SMA, computed only over tickers with sufficient bar history (≥50 bars). `None` when unavailable or no ticker had sufficient history. |
| `momentum` | `dict \| None` | Per-ticker 20-day return `(close[-1] - close[-21]) / close[-21]`. `None` when unavailable. Only tickers with ≥21 bars are included. |
| `source` | `str` | Always `"Alpaca Markets v2 daily bars — reused synthetic_history cache"`. Present on all paths for `lens_pipeline.build_citation` consumption. |
| `reason` | `str \| None` | `None` on success; `type(exc).__name__` for caught exceptions; named label for authoritative failures. |

**Named `reason` labels (non-exception unavailability):**

| Label | Meaning |
|-------|---------|
| `"no_bars_returned"` | `_get_bars` returned `{}` — authoritative empty response (not retried). |
| `"insufficient_bar_history"` | Bars were fetched but no ticker had enough history for any indicator. |

**Example (available):**
```python
result = _fetch_technicals(["SPY", "QQQ", "TLT"])
# {
#   "available": True,
#   "ma_posture": {
#       "SPY": {"above_sma50": True, "above_sma200": True},
#       "QQQ": {"above_sma50": True, "above_sma200": False},
#       "TLT": {"above_sma50": False, "above_sma200": False},
#   },
#   "breadth": 0.667,
#   "momentum": {"SPY": 0.031, "QQQ": 0.058, "TLT": -0.012},
#   "source": "Alpaca Markets v2 daily bars — reused synthetic_history cache",
#   "reason": None,
# }
```

**Example (unavailable — fetch failed):**
```python
result = _fetch_technicals(["SPY"])
# {
#   "available": False,
#   "ma_posture": None,
#   "breadth": None,
#   "momentum": None,
#   "source": "Alpaca Markets v2 daily bars — reused synthetic_history cache",
#   "reason": "ConnectionError",   # type(exc).__name__ only
# }
```

---

## Internal API (test seams and math helpers)

### `_get_bars(universe: list[str]) → dict[str, list[dict]]`

Test seam — fetches daily bars by delegating to `synthetic_history.fetch_bars`.

In production, calls `synthetic_history.fetch_bars(universe, start_str, end_str, timeframe="1Day")` with a `_HISTORY_DAYS`-day window ending today.

In tests, this function is patched via `unittest.mock.patch.object` so fixtures can inject bar data without hitting live Alpaca.

**Returns:** `{ticker: [bar_dicts]}`. Each bar dict has Alpaca v2 daily bar fields (`t`, `o`, `h`, `l`, `c`, `v`). Returns `{}` on network error (callers handle the empty case).

**Never raises** in normal operation (delegated to `synthetic_history`). Callers wrap it in `try/except` for retry logic.

---

### `_compute_sma(closes: list[float], window: int) → float | None`

Compute the simple moving average of the last `window` closes.

Returns `None` if `len(closes) < window` — no fabrication from insufficient history.

---

### `_compute_momentum(closes: list[float]) → float | None`

Compute the `_MOMENTUM_WINDOW`-day return: `(close[-1] - close[-21]) / close[-21]`.

Returns `None` if `len(closes) < 21` — no fabrication from insufficient history.

Source: Jegadeesh & Titman (1993) cross-sectional momentum.

---

## Named Constants

All constants carry inline source comments (math_engine.py coding standard / AC-6 — no magic numbers).

| Constant | Value | Meaning | Source |
|----------|-------|---------|--------|
| `_SMA_50_WINDOW` | `50` | Near-term SMA window | Investopedia — standard near-term trend indicator for institutional equity monitoring |
| `_SMA_200_WINDOW` | `200` | Long-term SMA window | Investopedia — canonical long-term trend separator (bull/bear market boundary) |
| `_MOMENTUM_WINDOW` | `20` | Momentum lookback (days) | Jegadeesh & Titman (1993) — standard 1-month cross-sectional momentum lookback |
| `_MAX_ATTEMPTS` | `3` | Max fetch attempts (1 initial + 2 retries) | GDELT RCA — unbounded 429 loops caused a crash; finite bound is mandatory (AC-4) |
| `_RETRY_BACKOFF_S` | `2.0` | Seconds between retries | Short for a nightly advisory lens; not on the execution path |
| `_TECHNICALS_SOURCE` | `"Alpaca Markets v2 daily bars — reused synthetic_history cache"` | Citation string | Always present for `lens_pipeline.build_citation` consumption |
| `_HISTORY_DAYS` | `270` | Calendar days of bar history to fetch | 270 calendar days ≈ 250 trading days after weekends + holidays; matches synthetic_history.py window |

---

## Indicators

### MA Posture

Computed per ticker. Compares the last close against the 50-day and 200-day simple moving average.

- `above_sma50`: `True`/`False` when ≥50 bars available; `None` when insufficient history.
- `above_sma200`: `True`/`False` when ≥200 bars available; `None` when insufficient history.

A ticker with <200 bars (but ≥50) will have `above_sma200=None` and `above_sma50` computed normally.

### Market Breadth

Fraction of the universe above their 50-day SMA. Computed only over tickers that have `above_sma50 is not None` (i.e., ≥50 bars of history). Tickers with insufficient history are excluded from the denominator — no dilution from data gaps.

`breadth = above_count / len(tickers_with_sma50)`

Returns `None` when no ticker in the universe has ≥50 bars.

### 20-Day Momentum

Per-ticker 20-trading-day return: `(close[-1] - close[-21]) / close[-21]`.

Requires 21 bars minimum (bar[-1] and bar[-21]). Tickers with fewer bars are omitted from the `momentum` dict — never set to `0.0`.

The `momentum` key in the return dict is `None` when no ticker had sufficient bars; otherwise it is a `{ticker: float}` dict, possibly sparse (tickers with insufficient history omitted).

---

## Retry Protocol

`_fetch_technicals` retries `_get_bars` up to `_MAX_ATTEMPTS` times on any `Exception`. Between attempts, it sleeps `_RETRY_BACKOFF_S` seconds.

Authoritative empty responses (`{}` returned without raising) are **not retried** — an empty response means Alpaca had no bars for the requested tickers, not a transient error.

After `_MAX_ATTEMPTS` exhaustion, returns `available=False, reason=type(last_exc).__name__`.

---

## Design Invariants

| Code | Invariant |
|------|-----------|
| D-1 | `reason` is `type(exc).__name__` only on exception paths — `str(exc)` never reaches the returned dict or logs at WARNING+ level. |
| CC-2 | Never imported at module level in `alpha_bot_execution.py`. Lazy-imported inside `ai_advisor._build_technicals_section()`. |
| CC-3 | Never fabricates a payload. `available=False` with a named `reason` on every unavailable path. |
| AC-4 | Bounded retry — `_MAX_ATTEMPTS=3`. Authoritative empty responses are not retried. |
| AC-5 | No new Alpaca client introduced — delegates to `synthetic_history.fetch_bars`. |
| AC-6 | No magic numbers — every window/threshold is a named module-level constant with a source comment. |

---

## Internal Dependencies

- `synthetic_history` — `fetch_bars` (lazy import inside `_get_bars`; provides the 270-day daily bar cache)
- `time` — `sleep` for retry backoff
- `logging` — structured log at `WARNING` (retry), `INFO` (success); no exception text in logs (D-1)

## Wiring in `ai_advisor.py`

`ai_advisor._build_technicals_section()` (`ai_advisor.py:439-482`) is the only production caller. It lazy-imports this module (CC-2), derives the universe from `database.load_state()` holdings (tickers across all monitored symphonies), and calls `_fetch_technicals(universe)`, and wraps the result into the lens block shape consumed by `lens_pipeline.run_pipeline()`:

```python
{
    "lens": "technicals",
    "available": bool,
    "payload": {                         # None when available=False
        "ma_posture": ...,
        "breadth": ...,
        "momentum": ...,
    },
    "reason": str,                       # only when available=False
    "sources": [],                       # technicals has no URL citations
}
```

The `_data` argument on `_build_technicals_section` is unused and reserved for future caller pre-injection (test-mockable hook).
