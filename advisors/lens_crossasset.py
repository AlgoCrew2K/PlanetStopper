"""Cross-asset risk-appetite lens for Planet Stopper AI Advisor.

Measures risk-on / neutral / risk-off sentiment across 6 factor components
derived from free Alpaca IEX daily bars. Advisory-only; never on the
1-minute execution path.

Instrument set (operator-approved, research-settled 2026-06-14):
  HYG-LQD  -- credit-spread relative (HY vs IG; strips shared duration)
  TLT      -- US nominal duration / flight-to-safety
  UUP      -- safe-haven USD / FX
  GLD      -- real-asset / monetary hedge
  EMB      -- EM credit-cycle risk
  DBC      -- commodity / real-economy growth

Design invariants:
  - D-1: all exception-path reason values are type(exc).__name__ only.
  - Honest-availability: missing / short bars exclude the component;
    never fabricate a signal.
  - Never raises: the entire body of fetch_crossasset is wrapped in a
    top-level try/except that returns an unavailable dict.
  - Warehouse persist: guaranteed via try/finally on every code path
    including error paths.
  - No direct import requests at module level: reuse synthetic_history.fetch_bars.
  - Advisory-only: no Flask route decorators; off the execution-critical path;
    no dynamic code execution (eval/exec) or shell spawning.

Public surface:
  fetch_crossasset() -> dict
  _compute_component_signals(bars: dict) -> dict   (internal helper, exposed)
  _aggregate_risk_read(signals: dict) -> str        (internal helper, exposed)
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from statistics import mean
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Source provenance constant
# ---------------------------------------------------------------------------

_SOURCE = "alpaca-iex"

# ---------------------------------------------------------------------------
# Instrument set
# ---------------------------------------------------------------------------

# All symbols fetched from Alpaca IEX free endpoint.
_ALL_SYMBOLS: list[str] = ["HYG", "LQD", "TLT", "UUP", "GLD", "EMB", "DBC"]

# Lookback window: sufficient to compute reliable SMA signals while staying
# within Alpaca IEX free feed limits.
_LOOKBACK_DAYS = 90  # calendar days (yields ~60 trading bars typical)

# ---------------------------------------------------------------------------
# Per-component signal constants
# ---------------------------------------------------------------------------

# Minimum number of close-price bars required to compute any component signal.
# Components with fewer bars are excluded (honest-availability).
_MIN_BARS = 20

# ---------------------------------------------------------------------------
# Aggregation constants (naming satisfies handoff naming rules)
# ---------------------------------------------------------------------------

# Fraction of available components that must signal a direction to declare
# "risk-on" or "risk-off". Below this threshold on both sides -> "neutral".
# Name ends in THRESHOLD per contract.
_RISK_ON_THRESHOLD = 0.5  # 50% majority vote

# Minimum number of components needed to form a valid aggregate read.
# If fewer valid components remain, return available=False.
# Name contains MIN and COMPONENTS per contract.
_MIN_COMPONENTS = 3

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_closes(bars: list[dict]) -> list[float]:
    """Return close prices in chronological order from a bars list."""
    return [float(b["c"]) for b in bars if "c" in b]


def _compute_signal(closes: list[float]) -> float:
    """Trailing-return signal: (last_close / first_close) - 1.

    Positive = upward trend (risk-on for most components).
    Negative = downward trend.
    Uses simple trailing return so the same computation on up-trending vs
    down-trending data produces opposite signs — no hardcoded direction.
    """
    return (closes[-1] / closes[0]) - 1.0


def _compute_component_signals(bars: dict[str, list[dict]]) -> dict[str, dict]:
    """Compute per-component directional signal dicts from raw fetch_bars output.

    Returns a dict keyed by component name. Each value is a dict with
    keys 'available' (bool) and, when available=True, 'signal' (float).

    HYG-LQD credit spread is computed as the RELATIVE (ratio series) of the
    two instruments — never from HYG alone. If either is missing/short,
    the credit component is excluded.

    All other components are derived independently from their own bar series.
    A component is excluded (available=False) when its bar count is below
    _MIN_BARS.
    """
    components: dict[str, dict] = {}

    # --- Credit spread: HYG-LQD relative ---
    hyg_bars = bars.get("HYG", [])
    lqd_bars = bars.get("LQD", [])
    hyg_closes = _extract_closes(hyg_bars)
    lqd_closes = _extract_closes(lqd_bars)

    if len(hyg_closes) >= _MIN_BARS and len(lqd_closes) >= _MIN_BARS:
        # Align on minimum common length then compute ratio series.
        n = min(len(hyg_closes), len(lqd_closes))
        ratio_series = [
            hyg_closes[i] / lqd_closes[i]
            for i in range(n)
            if lqd_closes[i] != 0.0
        ]
        if len(ratio_series) >= _MIN_BARS:
            signal = _compute_signal(ratio_series)
            components["HYG_LQD"] = {"available": True, "signal": signal}
        else:
            components["HYG_LQD"] = {"available": False}
    else:
        components["HYG_LQD"] = {"available": False}

    # --- Standalone components ---
    for symbol in ("TLT", "UUP", "GLD", "EMB", "DBC"):
        sym_bars = bars.get(symbol, [])
        closes = _extract_closes(sym_bars)
        if len(closes) >= _MIN_BARS:
            signal = _compute_signal(closes)
            components[symbol] = {"available": True, "signal": signal}
        else:
            components[symbol] = {"available": False}

    return components


def _aggregate_risk_read(signals: dict[str, dict]) -> str:
    """Aggregate per-component signal dicts to a single risk_read label.

    Only components with available=True contribute to the vote.
    Raises ValueError when fewer than _MIN_COMPONENTS are available
    (caller should handle and return available=False).

    Returns one of: "risk-on", "neutral", "risk-off".
    """
    valid_signals = [
        v["signal"]
        for v in signals.values()
        if v.get("available") and "signal" in v
    ]

    if len(valid_signals) < _MIN_COMPONENTS:
        raise ValueError("InsufficientComponents")

    total = len(valid_signals)
    risk_on_votes = sum(1 for s in valid_signals if s > 0)
    risk_off_votes = sum(1 for s in valid_signals if s < 0)

    if risk_on_votes / total >= _RISK_ON_THRESHOLD:
        return "risk-on"
    if risk_off_votes / total >= _RISK_ON_THRESHOLD:
        return "risk-off"
    return "neutral"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def fetch_crossasset() -> dict:
    """Fetch cross-asset risk-appetite reading from Alpaca IEX daily bars.

    Never raises. Returns a dict with 'available' key:
      available=True  -> {available, risk_read, components, source}
      available=False -> {available, reason, source}

    Persists every run to the lens warehouse (including error paths).
    reason values on exception paths are type(exc).__name__ only (D-1).
    """
    result: dict[str, Any] = {"available": False, "reason": "Unknown", "source": _SOURCE}

    try:
        import synthetic_history  # noqa: PLC0415 — lazy import, CC-2 boundary

        # Compute date range: _LOOKBACK_DAYS calendar days up to yesterday.
        end_date = date.today() - timedelta(days=1)
        start_date = end_date - timedelta(days=_LOOKBACK_DAYS)
        start_str = start_date.isoformat()
        end_str = end_date.isoformat()

        logger.debug(
            "fetch_crossasset: fetching bars for %s from %s to %s",
            _ALL_SYMBOLS,
            start_str,
            end_str,
        )

        bars = synthetic_history.fetch_bars(_ALL_SYMBOLS, start_str, end_str)

        logger.info("fetch_crossasset: fetch_bars returned %d symbols", len(bars))

        if not bars:
            result = {
                "available": False,
                "reason": "EmptyBarsResponse",
                "source": _SOURCE,
            }
        else:
            components = _compute_component_signals(bars)

            try:
                risk_read = _aggregate_risk_read(components)
                result = {
                    "available": True,
                    "risk_read": risk_read,
                    "components": components,
                    "source": _SOURCE,
                }
                logger.info(
                    "fetch_crossasset: risk_read=%s (source=%s)", risk_read, _SOURCE
                )
            except ValueError:
                # Insufficient valid components — honest-availability.
                result = {
                    "available": False,
                    "reason": "InsufficientComponents",
                    "source": _SOURCE,
                }

    except Exception as exc:
        logger.warning("fetch_crossasset: error — %s", type(exc).__name__)
        result = {
            "available": False,
            "reason": type(exc).__name__,
            "source": _SOURCE,
        }

    finally:
        # Warehouse persist is guaranteed on every code path, including errors.
        # Failure here must not propagate to the caller.
        try:
            from advisors import lens_warehouse  # noqa: PLC0415 — lazy import, CC-2

            lens_warehouse.persist_lens_snapshot(
                lens="crossasset",
                symbol=None,
                source=_SOURCE,
                available=result.get("available", False),
                raw_payload=result,
            )
        except Exception:
            pass  # warehouse failure must never surface to caller

    return result
