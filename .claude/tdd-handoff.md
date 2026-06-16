# TDD Handoff — lens-technicals GREEN phase

**Branch:** feat/lens-technicals
**Worktree:** /c/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/technicals-team
**RED commit:** acfb46f
**RED state:** 36 failed, 1 passed
**Phase:** green
**Plan:** feature-plans/lens-data-technicals.md (do NOT read — implementer is deliberately blind)

## Test run command

```
pytest tests/ai_advisor/test_lens_technicals.py -p no:xdist -o addopts= -m "not live and not slow and not perf" -q
```

Target: 37 passed, 0 failed.

## What the tests need (read this, not the plan)

### 1. CREATE: `advisors/lens_technicals.py`

Required module-level named constants (no magic numbers — AC-6):
```python
# Standard near-term MA window. Source: Investopedia — 50-day SMA is the
# standard near-term trend indicator for institutional equity monitoring.
_SMA_50_WINDOW: int = 50

# Standard long-term MA window. Source: Investopedia — 200-day SMA is the
# canonical long-term trend separator (bull/bear market boundary).
_SMA_200_WINDOW: int = 200

# Standard short-term momentum lookback. Source: standard 1-month return
# used in cross-sectional momentum studies (Jegadeesh & Titman 1993).
_MOMENTUM_WINDOW: int = 20

# Maximum Alpaca fetch attempts (1 initial + N-1 retries).
# Source: bounded to prevent unbounded retry loops on persistent 429.
# See GDELT RCA — unbounded 429 loop caused a PC crash.
_MAX_ATTEMPTS: int = 3  # or any int in [1, 10]

# Citation string — always present in the return dict.
_TECHNICALS_SOURCE: str = "Alpaca Markets v2 daily bars — reused synthetic_history cache"
```

Required functions:

```python
def _get_bars(universe: list[str]) -> dict[str, list[dict]]:
    """Fetch daily bars for all tickers in the universe.

    This is the test seam — tests mock this function via patch.object.
    In production, delegates to synthetic_history.fetch_bars.
    Returns {ticker: [bar_dicts]} or {} on error.
    """

def _fetch_technicals(universe: list[str]) -> dict:
    """Fetch price/trend/breadth technicals for the universe.

    Never raises. Returns:
      {"available": bool, "ma_posture": dict|None, "breadth": float|None,
       "momentum": dict|None, "source": str, "reason": str|None}
    """
```

### 2. Return shape

Success path (`available=True`):
```python
{
    "available": True,
    "ma_posture": {
        "SPY": {"above_sma50": True, "above_sma200": True},
        "QQQ": {"above_sma50": False, "above_sma200": True},
        # ... per ticker; only tickers with sufficient bars for that window
    },
    "breadth": 0.5,       # float in [0.0, 1.0]; fraction above SMA50
    "momentum": {
        "SPY": 0.047619,  # float: (close[-1] - close[-21]) / close[-21]
        "QQQ": -0.05,
        # ... per ticker; only tickers with >= 21 bars
    },
    "source": _TECHNICALS_SOURCE,
    "reason": None,
}
```

Unavailable path (`available=False`):
```python
{
    "available": False,
    "ma_posture": None,
    "breadth": None,
    "momentum": None,
    "source": _TECHNICALS_SOURCE,
    "reason": "ConnectionError",  # type(exc).__name__ ONLY — D-1
}
```

### 3. Math formulas

All formulas use the closing price field `"c"` from Alpaca bar dicts.

**SMA (simple moving average):**
```python
closes = [bar["c"] for bar in bars]
if len(closes) >= _SMA_50_WINDOW:
    sma50 = sum(closes[-_SMA_50_WINDOW:]) / _SMA_50_WINDOW
    above_sma50 = bool(closes[-1] > sma50)
else:
    above_sma50 = None  # insufficient history — not fabricated
```

**Breadth (% above SMA50):**
```python
tickers_with_sma50 = [t for t, flags in ma_posture.items() if flags["above_sma50"] is not None]
if tickers_with_sma50:
    above = sum(1 for t in tickers_with_sma50 if ma_posture[t]["above_sma50"])
    breadth = float(above / len(tickers_with_sma50))
else:
    breadth = None
```

**Momentum (20-day return):**
```python
if len(closes) >= _MOMENTUM_WINDOW + 1:  # need closes[-1] and closes[-21]
    momentum_val = (closes[-1] - closes[-(_MOMENTUM_WINDOW + 1)]) / closes[-(_MOMENTUM_WINDOW + 1)]
```

### 4. Edge cases

- **Zero-bar ticker** (`bars == []`): skip entirely. No ZeroDivisionError.
- **< 50 bars**: `above_sma50=None`; ticker excluded from breadth denominator.
- **< 200 bars but >= 50**: `above_sma50` computed; `above_sma200=None`.
- **< 21 bars**: ticker not included in `momentum` dict.
- **Empty universe / all tickers have 0 bars**: `available=False`.
- **`_get_bars` raises**: catch it, return `available=False, reason=type(exc).__name__`.
- **`_get_bars` returns {}**: `available=False`.

### 5. Bounded retry in `_get_bars`

On transient errors (e.g. HTTP 429, `HTTPError`, `ConnectionError`), retry up to `_MAX_ATTEMPTS` total calls.
Between retries: `time.sleep(...)` (tests patch it).
On authoritative empty response `{}`: do NOT retry — return it immediately (1 call only).
After exhaustion: re-raise or return `{}` (caller catches it).

### 6. MODIFY: `ai_advisor.py` — wire `_build_technicals_section`

Replace the stub body (around line 439) with the real wiring.

Pattern to follow: `_build_sentiment_section` (line ~454):

```python
def _build_technicals_section(_data: object = None) -> dict:
    """Technicals lens block — Alpaca price/trend/breadth producer."""
    from advisors import lens_technicals  # noqa: PLC0415

    try:
        result = lens_technicals._fetch_technicals([])
    except Exception as exc:
        result = {"available": False, "reason": type(exc).__name__}

    if result.get("available"):
        return {
            "lens": "technicals",
            "available": True,
            "payload": {
                "ma_posture": result.get("ma_posture"),
                "breadth": result.get("breadth"),
                "momentum": result.get("momentum"),
            },
            "sources": [],
        }
    else:
        return {
            "lens": "technicals",
            "available": False,
            "reason": result.get("reason", "unavailable"),
            "payload": None,
            "sources": [],
        }
```

Note: `lens_technicals._fetch_technicals` is mocked in wiring tests via `patch.object(lens_technicals, "_fetch_technicals", ...)` — so the real producer call happens only in integration. This is correct.

## Files to commit (path-scoped — never git add -A)

```
git add advisors/lens_technicals.py ai_advisor.py
```

## Status Log

- [2026-06-15] implementer: GREEN complete — 37/37 tests passing, 0 test bugs documented. New file lint ✓. Pre-existing ai_advisor.py lint issues (I001, F841, E501 — 3 issues) and pre-existing test failure (test_derivatives_stub_still_returns_available_false) are unrelated to this cycle and confirmed at RED commit.

## Test File Issues (for test-writer to fix)

None.

## Disputed Tests

None.

## Implementation Notes

- Created `advisors/lens_technicals.py` with `_get_bars` (test seam → `synthetic_history.fetch_bars`) and `_fetch_technicals` (producer entry point).
- All 5 module-level constants present with source comments: `_SMA_50_WINDOW=50`, `_SMA_200_WINDOW=200`, `_MOMENTUM_WINDOW=20`, `_MAX_ATTEMPTS=3`, `_TECHNICALS_SOURCE`.
- Retry loop is in `_fetch_technicals` (not in `_get_bars`) — each retry is one call to `_get_bars`; authoritative empty {} response is not retried.
- Zero-bar tickers skip via `if not bar_list: continue` before any arithmetic.
- `above_sma50`/`above_sma200` are `None` (not False) when insufficient history — matches the handoff spec.
- Breadth excludes tickers with `above_sma50 is None` from both numerator and denominator.
- D-1 honored throughout: every exception path uses `type(exc).__name__` only.
- Replaced stub in `ai_advisor.py:_build_technicals_section` with the real wiring (lazy import CC-2, honest-availability propagation).
- The Cycle-2 guard test `test_technicals_stub_still_returns_available_false` still passes because in the test environment (no Alpaca credentials), `_fetch_technicals([])` correctly returns `available=False` — the wiring behaves honestly.

## After GREEN

Send `SendMessage` to `team-lead` with: "GREEN: <SHA> — N passed / 0 failed. Ready for review."

I will then route reviewer and doc-writer.
