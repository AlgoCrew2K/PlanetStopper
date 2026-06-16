"""Technicals lens producer — Alpaca Markets v2 daily bars.

Computes price/trend/breadth indicators from 250-day bar history reused from
synthetic_history.fetch_bars (no new Alpaca client added — AC-5).

Indicators computed:
  - MA posture: price relative to 50-day and 200-day SMA per ticker
  - Breadth: fraction of universe above 50-day SMA
  - Momentum: 20-day return per ticker

Contracts:
  - Never raises (D-1 / CC-3).
  - All exception reasons are type(exc).__name__ only (D-1 — no str(exc)).
  - No magic numbers: every window/threshold is a named module-level constant
    with a source comment (math_engine.py coding standard / AC-6).
  - Off-execution-path: no blocking I/O on the execution path.
  - Bounded retry on transient Alpaca errors; authoritative empty response
    is not retried (AC-4).
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Named constants — no magic numbers (math_engine.py coding standard / AC-6)
# ---------------------------------------------------------------------------

# Standard near-term MA window. Source: Investopedia — 50-day SMA is the
# standard near-term trend indicator for institutional equity monitoring.
_SMA_50_WINDOW: int = 50

# Standard long-term MA window. Source: Investopedia — 200-day SMA is the
# canonical long-term trend separator (bull/bear market boundary).
_SMA_200_WINDOW: int = 200

# Standard short-term momentum lookback. Source: standard 1-month return
# used in cross-sectional momentum studies (Jegadeesh & Titman 1993).
_MOMENTUM_WINDOW: int = 20

# Maximum fetch attempts (1 initial + N-1 retries). Bounded to prevent
# unbounded retry loops on persistent 429. Source: GDELT RCA —
# unbounded 429 loop caused a PC crash; finite bound is mandatory (AC-4).
_MAX_ATTEMPTS: int = 3

# Backoff interval between retries, in seconds.
# Kept short for a nightly advisory lens; not on the execution path.
_RETRY_BACKOFF_S: float = 2.0

# Citation string — always present in the return dict for lens_pipeline
# build_citation() consumption.
_TECHNICALS_SOURCE: str = "Alpaca Markets v2 daily bars — reused synthetic_history cache"

# The date range passed to synthetic_history.fetch_bars.
# 270 calendar days covers ~250 trading days (accounting for weekends +
# holidays).  Source: synthetic_history.py uses the same window.
_HISTORY_DAYS: int = 270


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_bars(universe: list[str]) -> dict[str, list[dict]]:
    """Fetch daily bars for all tickers in the universe.

    This is the test seam — tests mock this function via patch.object so they
    can inject bar fixtures without hitting real Alpaca.  In production,
    delegates to synthetic_history.fetch_bars.

    Args:
        universe: list of ticker symbols to fetch.

    Returns:
        {ticker: [bar_dicts]} where each bar_dict has at minimum the Alpaca
        v2 daily bar fields (t, o, h, l, c, v).  Returns {} on network error
        (callers must handle the empty case).
    """
    import datetime

    import synthetic_history  # noqa: PLC0415 — CC-2 lazy import

    end_dt = datetime.date.today()
    start_dt = end_dt - datetime.timedelta(days=_HISTORY_DAYS)
    start_str = start_dt.isoformat()
    end_str = end_dt.isoformat()

    return synthetic_history.fetch_bars(universe, start_str, end_str, timeframe="1Day")


# ---------------------------------------------------------------------------
# Indicator math helpers — pure functions, no I/O
# ---------------------------------------------------------------------------


def _compute_sma(closes: list[float], window: int) -> float | None:
    """Compute the simple moving average of the last `window` closes.

    Returns None if len(closes) < window (insufficient history — no fabrication).
    """
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def _compute_momentum(closes: list[float]) -> float | None:
    """Compute the 20-day return: (close[-1] - close[-21]) / close[-21].

    Returns None if len(closes) < 21 (need bar[-1] and bar[-21]).
    Source: Jegadeesh & Titman (1993) cross-sectional momentum.
    """
    needed = _MOMENTUM_WINDOW + 1  # bar[-1] and bar[-21]
    if len(closes) < needed:
        return None
    return (closes[-1] - closes[-needed]) / closes[-needed]


# ---------------------------------------------------------------------------
# Producer — public entry point
# ---------------------------------------------------------------------------


def _fetch_technicals(universe: list[str]) -> dict:
    """Fetch price/trend/breadth technicals for the universe.

    Calls _get_bars (test-mockable seam) with bounded retry on transient
    errors.  Authoritative empty responses ({}) are not retried.

    Never raises.  Returns:
      {
        "available": bool,
        "ma_posture": dict | None,   # per-ticker {above_sma50, above_sma200}
        "breadth": float | None,     # fraction of universe above SMA50
        "momentum": dict | None,     # per-ticker 20-day return
        "source": str,
        "reason": str | None,        # type(exc).__name__ on failure (D-1)
      }
    """
    # --- Bounded retry on transient errors (AC-4) ---
    bars: dict[str, list[dict]] = {}
    last_exc: Exception | None = None

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            result = _get_bars(universe)
            # Authoritative empty response: do not retry
            bars = result
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "lens_technicals: _get_bars attempt %d/%d failed: %s",
                attempt,
                _MAX_ATTEMPTS,
                type(exc).__name__,
            )
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_RETRY_BACKOFF_S)

    # --- Unavailable: fetch failed after exhaustion ---
    if last_exc is not None:
        return {
            "available": False,
            "ma_posture": None,
            "breadth": None,
            "momentum": None,
            "source": _TECHNICALS_SOURCE,
            "reason": type(last_exc).__name__,  # D-1: never str(exc)
        }

    # --- Unavailable: authoritative empty response ---
    if not bars:
        return {
            "available": False,
            "ma_posture": None,
            "breadth": None,
            "momentum": None,
            "source": _TECHNICALS_SOURCE,
            "reason": "no_bars_returned",
        }

    # --- Compute indicators per ticker ---
    try:
        ma_posture: dict[str, dict] = {}
        momentum: dict[str, float] = {}

        for ticker, bar_list in bars.items():
            if not bar_list:
                # Zero-bar ticker: skip entirely (no ZeroDivisionError risk)
                continue

            closes = [bar["c"] for bar in bar_list]

            # MA posture — 50-day and 200-day SMA
            sma50 = _compute_sma(closes, _SMA_50_WINDOW)
            sma200 = _compute_sma(closes, _SMA_200_WINDOW)

            above_sma50 = bool(closes[-1] > sma50) if sma50 is not None else None
            above_sma200 = bool(closes[-1] > sma200) if sma200 is not None else None

            ma_posture[ticker] = {
                "above_sma50": above_sma50,
                "above_sma200": above_sma200,
            }

            # Momentum — 20-day return
            mom_val = _compute_momentum(closes)
            if mom_val is not None:
                momentum[ticker] = float(mom_val)

        # --- Breadth: fraction of universe above SMA50 ---
        # Exclude tickers without sufficient bars for SMA50 (above_sma50 is None)
        tickers_with_sma50 = [
            t for t, flags in ma_posture.items() if flags["above_sma50"] is not None
        ]

        if tickers_with_sma50:
            above_count = sum(1 for t in tickers_with_sma50 if ma_posture[t]["above_sma50"])
            breadth: float | None = float(above_count / len(tickers_with_sma50))
        else:
            breadth = None

        # --- Unavailable: no meaningful data after computing indicators ---
        if not ma_posture and not momentum:
            return {
                "available": False,
                "ma_posture": None,
                "breadth": None,
                "momentum": None,
                "source": _TECHNICALS_SOURCE,
                "reason": "insufficient_bar_history",
            }

        logger.info(
            "lens_technicals: available tickers=%d breadth=%s",
            len(ma_posture),
            breadth,
        )
        return {
            "available": True,
            "ma_posture": ma_posture,
            "breadth": breadth,
            "momentum": momentum if momentum else None,
            "source": _TECHNICALS_SOURCE,
            "reason": None,
        }

    except Exception as exc:
        # Defense-in-depth: catch any unexpected indicator-math exception
        logger.exception("lens_technicals: unexpected error in indicator computation")
        return {
            "available": False,
            "ma_posture": None,
            "breadth": None,
            "momentum": None,
            "source": _TECHNICALS_SOURCE,
            "reason": type(exc).__name__,  # D-1: never str(exc)
        }
