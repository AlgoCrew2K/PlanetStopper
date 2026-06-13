# advisors/lens_technicals

> B2 Alpaca-bar technicals producer for the Market Prism technicals lens: computes SMA posture (50-day, 200-day), breadth (fraction of universe above 50-day SMA), and momentum (20-day return) from `synthetic_history.fetch_bars` — no new HTTP client. Bounded retry. D-1 error contract throughout.

**Source:** `advisors/lens_technicals.py`
**Last updated:** 2026-06-13

## Overview

`advisors/lens_technicals.py` provides the `_fetch_technicals(universe)` producer function consumed by `ai_advisor._build_technicals_section`. It is the Cycle B2 implementation of the technicals lens data layer.

**Key properties:**
- **Reuses existing Alpaca fetcher.** Calls `synthetic_history.fetch_bars(universe, start_str, end_str)` — one batch call for the whole universe. No new HTTP client or new credential handling.
- **Off-execution-path.** Never imported by `alpha_bot_execution.py`; lazily imported inside `ai_advisor._build_technicals_section` (CC-2 import-boundary invariant).
- **Bounded retry.** The fetch loop retries at most `_TECH_MAX_ATTEMPTS` times before re-raising. Hard ceiling prevents unbounded loops.
- **Named constants only.** All window sizes, limits, and structural thresholds are named module-level constants with source comments. No magic numbers inline.
- **D-1 error contract.** The `reason` field in any `available=False` return carries only `type(exc).__name__`, never `str(exc)` (which may contain hostnames or partial credentials).
- **Honest-availability.** `pct_above_200sma=None` when fewer than 200 bars are available for any ticker. No fabricated values.

**Integration point:** `ai_advisor._build_technicals_section` lazy-imports this module and calls `_fetch_technicals(universe=_MARKET_PROXY_UNIVERSE)` — a fixed 14-ticker market-proxy basket (SPY, QQQ, IWM, and all 11 SPDR sector ETFs). This gives a market-level breadth/posture/momentum read for the Market Prism context. The lens is **fully functional in production**: when Alpaca is reachable, it returns `available=True` with real indicator data. The `available=False, reason="EmptyUniverse"` path is only reached if the caller explicitly passes an empty list — it is a degradation path, not the normal operating state.

**Fixture provenance:** Output shape is pinned in `tests/fixtures/math/technicals_producer_schema.json` (schema-derived, Cycle B2 RED — written before any implementation). Bar field requirements are pinned in `tests/fixtures/math/alpaca_bars_schema.json`.

## Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `_TECH_SMA_SHORT_WINDOW` | `50` | 50-day simple moving average window |
| `_TECH_SMA_LONG_WINDOW` | `200` | 200-day simple moving average window |
| `_TECH_MOMENTUM_WINDOW` | `20` | 20-day return lookback (requires 21 bars: today + 20 prior) |
| `_TECH_MAX_ATTEMPTS` | `3` | Hard ceiling on `fetch_bars` attempts. PC-crash regression guard — finite, well below the ceiling of 20. |
| `_TECH_LOOKBACK_CALENDAR_DAYS` | `380` | Calendar-day window requested from Alpaca. 250 trading days ≈ 360 calendar days; 380 provides headroom. |

## Public API

### `_fetch_technicals(universe: list) → dict`

Fetch Alpaca daily bars for the portfolio universe and compute aggregated technicals.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `universe` | `list[str]` | Ticker symbols (e.g. `["SPY", "QQQ"]`). Empty list returns `available=False, reason="EmptyUniverse"` immediately. |

**Returns:**

On success (`available=True`):
```python
{
    "available": True,
    "ma_posture": {
        "pct_above_50sma":  float,        # fraction [0, 1] of universe above 50-day SMA
        "pct_above_200sma": float | None, # fraction [0, 1] or None if < 200 bars available
        "overall": "bullish" | "bearish" | "mixed",  # majority-rules on pct_above_50sma (0.5 → "mixed")
    },
    "breadth": float,   # == ma_posture["pct_above_50sma"]
    "momentum": {
        "mean_20d_return": float,                       # cross-universe mean (close[-1]/close[-21]) - 1.0
        "direction": "positive" | "negative" | "flat",  # sign of mean_20d_return (0 → "flat")
    } | None,           # None if no ticker had >= 21 bars
    "source": "alpaca/synthetic_history",
}
```

On any failure (`available=False`):
```python
{
    "available":  False,
    "ma_posture": None,
    "breadth":    None,
    "momentum":   None,
    "source":     None,
    "reason":     str,  # type(exc).__name__ only (D-1), or "EmptyUniverse" / "InsufficientHistory"
}
```

**Never raises.** All exceptions are caught and converted to `available=False` returns.

**Degradation paths:**

| Condition | Result |
|-----------|--------|
| `universe` is empty | `available=False, reason="EmptyUniverse"` |
| All `_TECH_MAX_ATTEMPTS` fetch attempts fail | re-raises last exception → outer except → `available=False, reason=type(exc).__name__` |
| Ticker has 0 or 1 close prices | Ticker skipped from all indicators |
| No ticker reaches 50-bar floor | `available=False, reason="InsufficientHistory"` |
| Some tickers have < 200 bars | `pct_above_200sma=None` (honest-availability; 50-SMA and momentum still computed) |
| No ticker has >= 21 bars | `momentum=None` |
| Network error / timeout | `available=False, reason="ConnectionError"` (or actual exception class name) |

## Internal API

### `_sma(closes: list, window: int) → float | None`

Return the simple moving average of the last `window` close prices, or `None` if fewer than `window` prices are available. Pure arithmetic — no external calls.

## Error Contract (D-1)

All `except` blocks in this module use only `type(exc).__name__` in the `reason` field. `str(exc)` is never surfaced: it may contain hostnames, partial URLs, or Alpaca request parameters.

The literal strings `"EmptyUniverse"` and `"InsufficientHistory"` are used for the corresponding structural early-exit cases (not exception-derived, but safe: they contain no credential or host information).

## Test Coverage

**Test files:**
- `tests/ai_advisor/test_lens_technicals.py` (55 tests)
- `tests/ai_advisor/test_technicals_golden.py` (11 golden-fixture math tests)

| Class | Tests | Coverage |
|-------|-------|---------|
| `TestProducerExists` | 3 | Module importable, function callable, `universe` parameter present |
| `TestProducerShape` | 12 | Required keys, bool available, float-or-None breadth, ma_posture dict, vocab invariants, source string, pct bounds |
| `TestUnavailableOnError` | 6 | Exception → available=False, D-1 reason, ma_posture/breadth/source all None on error, never raises |
| `TestEdgeCases` | 6 | Insufficient history (50-day/200-day), zero-bar ticker excluded, empty universe |
| `TestBoundedRetry` | 3 | `_TECH_MAX_ATTEMPTS` exists, is finite, retry count bounded on persistent error |
| `TestConstants` | 4 | All five named constants exist |
| `TestSyntheticHistoryReuse` | 2 | References `synthetic_history`, no new HTTP client |
| `TestFixtureContract` | 6 | Schema fixtures exist, required keys, provenance headers |
| `TestPipelineIntegration` | 5 | `_build_technicals_section` wired (not stub), calls `_fetch_technicals`, returns correct shape |
| `TestSecurity` | 5 | No eval/exec, no hardcoded API keys, no LIVE_EXECUTION, D-1 on all error paths |
| `TestSMAGolden` | 4 | pct_above_50sma=1.0 for uptrend, =0.0 for downtrend, pct_above_200sma=None at 100 bars, =1.0 at 250 bars |
| `TestBreadthGolden` | 2 | breadth==pct_above_50sma, breadth=0.5 for half-above/half-below |
| `TestMomentumGolden` | 5 | direction=positive/negative for up/downtrend, sign matches mean_20d_return, overall=bullish/bearish |

**Fixtures:** `tests/fixtures/math/technicals_producer_schema.json` and `tests/fixtures/math/alpaca_bars_schema.json` — schema-derived provenance (Cycle B2 RED, written before implementation).
