# advisors/lens_technicals.py
# Technicals lens producer: SMA posture, breadth, momentum via synthetic_history.

import datetime
import logging

import synthetic_history

logger = logging.getLogger(__name__)

# --- named constants (no magic numbers) ---
_TECH_SMA_SHORT_WINDOW: int = 50    # 50-day simple moving average
_TECH_SMA_LONG_WINDOW: int = 200    # 200-day simple moving average
_TECH_BREADTH_WINDOW: int = 50      # breadth tracks the short SMA window
_TECH_MOMENTUM_WINDOW: int = 20     # 20-day return lookback
_TECH_MAX_ATTEMPTS: int = 3         # bounded retry on transient fetch errors

# How many calendar days back to request from Alpaca to cover _TECH_SMA_LONG_WINDOW
# trading days (250 trading days ≈ 360 calendar days; 380 gives headroom).
_TECH_LOOKBACK_CALENDAR_DAYS: int = 380

_UNAVAILABLE: dict = {
    "available": False,
    "ma_posture": None,
    "breadth": None,
    "momentum": None,
    "source": None,
}


def _sma(closes: list, window: int):
    """Return the simple moving average of the last `window` closes, or None."""
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def _fetch_technicals(universe: list) -> dict:
    """Fetch technical indicators for *universe* and aggregate.

    Uses ``synthetic_history.fetch_bars`` (the project's existing Alpaca bar
    fetcher, returning ``{symbol: [bar, ...]}``) — no new HTTP client.  All
    error paths return ``available=False`` with ``reason=type(exc).__name__``
    (D-1 contract; no hostname / credential leakage).

    Return shape on success::

        {
            "available": True,
            "ma_posture": {
                "pct_above_50sma": float,         # fraction [0, 1]
                "pct_above_200sma": float | None, # None when < 200 bars
                "overall": "bullish" | "bearish" | "mixed",
            },
            "breadth": float | None,              # == pct_above_50sma
            "momentum": {
                "mean_20d_return": float,
                "direction": "positive" | "negative" | "flat",
            },
            "source": str,
        }
    """
    if not universe:
        return dict(_UNAVAILABLE, reason="EmptyUniverse")

    try:
        end_dt = datetime.datetime.now(datetime.timezone.utc)
        start_dt = end_dt - datetime.timedelta(days=_TECH_LOOKBACK_CALENDAR_DAYS)
        end_str = end_dt.strftime("%Y-%m-%dT00:00:00Z")
        start_str = start_dt.strftime("%Y-%m-%dT00:00:00Z")

        bars_by_symbol: dict = {}
        last_exc = None
        for attempt in range(1, _TECH_MAX_ATTEMPTS + 1):
            try:
                bars_by_symbol = synthetic_history.fetch_bars(universe, start_str, end_str)
                last_exc = None
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.info(
                    "lens_technicals: fetch_bars attempt %d failed: %s",
                    attempt,
                    type(exc).__name__,
                )

        if last_exc is not None:
            raise last_exc

        above_50_flags: list[bool] = []
        above_200_flags: list[bool] = []
        returns_20d: list[float] = []

        for ticker in universe:
            ticker_bars = bars_by_symbol.get(ticker, [])
            closes = [bar["c"] for bar in ticker_bars if "c" in bar]

            if len(closes) < 2:
                # zero or one close price — not enough for any indicator; skip
                continue

            # 50-day SMA posture
            sma_50 = _sma(closes, _TECH_SMA_SHORT_WINDOW)
            if sma_50 is not None:
                above_50_flags.append(closes[-1] > sma_50)

            # 200-day SMA posture
            sma_200 = _sma(closes, _TECH_SMA_LONG_WINDOW)
            if sma_200 is not None:
                above_200_flags.append(closes[-1] > sma_200)

            # 20-day return (needs 21 bars: today + 20 prior)
            if len(closes) >= _TECH_MOMENTUM_WINDOW + 1:
                ret = (closes[-1] / closes[-(_TECH_MOMENTUM_WINDOW + 1)]) - 1.0
                returns_20d.append(ret)

        if not above_50_flags:
            # no ticker had enough history for the short SMA
            return dict(_UNAVAILABLE, reason="InsufficientHistory")

        pct_above_50 = sum(above_50_flags) / len(above_50_flags)
        pct_above_200 = (
            sum(above_200_flags) / len(above_200_flags)
            if above_200_flags
            else None
        )

        # overall posture: majority-rules with 0.5 treated as "mixed"
        if pct_above_50 > 0.5:
            overall = "bullish"
        elif pct_above_50 < 0.5:
            overall = "bearish"
        else:
            overall = "mixed"

        # momentum direction
        if returns_20d:
            mean_ret = sum(returns_20d) / len(returns_20d)
            if mean_ret > 0:
                direction = "positive"
            elif mean_ret < 0:
                direction = "negative"
            else:
                direction = "flat"
            momentum = {"mean_20d_return": mean_ret, "direction": direction}
        else:
            momentum = None

        return {
            "available": True,
            "ma_posture": {
                "pct_above_50sma": pct_above_50,
                "pct_above_200sma": pct_above_200,
                "overall": overall,
            },
            "breadth": pct_above_50,
            "momentum": momentum,
            "source": "alpaca/synthetic_history",
        }

    except Exception as exc:  # noqa: BLE001
        logger.warning("lens_technicals: _fetch_technicals failed: %s", type(exc).__name__)
        return dict(_UNAVAILABLE, reason=type(exc).__name__)
