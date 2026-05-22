"""
Port-level aggregator: aggregate_to_port, build_port_signal, persist_port_signal_snapshot
(AC-P2.4.*, AC-P2.6.*)

aggregate_to_port is a pure function — no I/O, no state mutation.
build_port_signal / build_port_signal_with_authority are pure signal constructors.
persist_port_signal_snapshot writes to database (has I/O — not pure).

Per-field aggregation method (AC-P2.4.2, Goldberg-Mahmoud 2017 / Pospisil-Vecer 2010):
  - Value-weightable: current_return, last_percent_change, last_dollar_change, value, cash
    -> allocation-weighted sum on common-denominator quantities
  - Path-dependent: max_drawdown, simple_return, time_weighted_return
    -> recomputed from the port-equity series (value-weighting non-coincident extrema
       is mathematically meaningless: Goldberg-Mahmoud 2017; Pospisil-Vecer 2010)
  - Flow: net_deposits -> arithmetically summed
  - Unavailable / dropped: sharpe_ratio, annualized_rate_of_return -> None
"""

from __future__ import annotations

import json
import os


def aggregate_to_port(
    symphonies: list[dict],
    account_id: str,
    port_equity_series: list[dict],
) -> dict:
    """
    Aggregate per-symphony state dicts into a single port-level state dict.

    Parameters
    ----------
    symphonies:
        List of per-symphony state dicts. Each must contain at minimum:
        symphony_id, value, cash, current_return, last_percent_change,
        last_dollar_change, net_deposits.
    account_id:
        The account identifier. Stored in the result for caller convenience.
    port_equity_series:
        List of {"t": int, "port_value": float} snapshots in ascending t order.
        Used to recompute path-dependent fields (max_drawdown, simple_return).
        May be empty when only value-weightable fields are needed.

    Returns
    -------
    dict
        Aggregated port state. Returns empty-sentinel dict when symphonies=[].
    """
    # AC-P2.4.4: zero-symphony sentinel
    if not symphonies:
        return {
            "account_id": account_id,
            "empty_sentinel": True,
            "value": 0.0,
            "symphonies": [],
        }

    # --- Value-weightable fields (AC-P2.4.2) ---
    total_value = sum(s.get("value", 0.0) for s in symphonies)

    # Allocation weight for each symphony (weight = value_i / total_value)
    if total_value > 0.0:
        weights = [s.get("value", 0.0) / total_value for s in symphonies]
    else:
        # Degenerate: all zero value — equal weights
        n = len(symphonies)
        weights = [1.0 / n] * n

    cash = sum(s.get("cash", 0.0) for s in symphonies)

    # Allocation-weighted return fields (common-denominator: weight = value_i / total_value)
    current_return = sum(
        w * s.get("current_return", 0.0) for w, s in zip(weights, symphonies)
    )
    last_percent_change = sum(
        w * s.get("last_percent_change", 0.0) for w, s in zip(weights, symphonies)
    )
    # last_dollar_change is a dollar quantity — sums directly (equivalent to
    # value-weighted pct * total_value, but computed from source for precision)
    last_dollar_change = sum(s.get("last_dollar_change", 0.0) for s in symphonies)

    # AC-P2.4.2 flow field: net_deposits arithmetically summed
    net_deposits = sum(s.get("net_deposits", 0.0) for s in symphonies)

    # --- Path-dependent fields from port-equity series (AC-P2.4.2 CRITICAL) ---
    # Goldberg-Mahmoud 2017 / Pospisil-Vecer 2010: drawdown is a path functional.
    # Value-weighting non-coincident extrema is mathematically meaningless.
    max_drawdown = _compute_max_drawdown_from_series(port_equity_series)
    simple_return = _compute_simple_return_from_series(port_equity_series)

    # --- Unavailable / dropped fields (AC-P2.4.2) ---
    # sharpe_ratio and annualized_rate_of_return cannot be validly aggregated
    # across symphonies with different history lengths / return series.
    sharpe_ratio = None
    annualized_rate_of_return = None

    result = {
        "account_id": account_id,
        "value": float(total_value),
        "cash": float(cash),
        "current_return": float(current_return),
        "last_percent_change": float(last_percent_change),
        "last_dollar_change": float(last_dollar_change),
        "net_deposits": float(net_deposits),
        "sharpe_ratio": sharpe_ratio,
        "annualized_rate_of_return": annualized_rate_of_return,
        # AC-P2.4.4: flag port_mode=True for telemetry consistency (single or multi symphony)
        "port_mode": True,
    }

    if max_drawdown is not None:
        result["max_drawdown"] = float(max_drawdown)
    if simple_return is not None:
        result["simple_return"] = float(simple_return)

    return result


def _compute_max_drawdown_from_series(port_equity_series: list[dict]) -> float | None:
    """
    Compute maximum drawdown from a port-equity series.

    Max drawdown = min over all (peak, trough) pairs where peak < trough in time
    of (trough_value - peak_value) / peak_value.

    Sign convention: NEGATIVE — a 20% drawdown is returned as -0.20, a
    no-drawdown (monotonic-rise) series as a genuine 0.0. This is the same
    negative-convention family as analytics.compute_quantstats_metrics. It is
    DELIBERATELY the opposite of analytics.get_symphony_max_drawdown, which
    returns a POSITIVE magnitude (a 20% drawdown as +20.0) for app.py templates;
    the two conventions are intentionally split, not a bug — see
    get_symphony_max_drawdown for the positive-magnitude side.

    Returns None when the series is degenerate:
      - fewer than 2 points (no peak-to-trough excursion is possible), or
      - the first value is non-positive (a zero or negative starting equity
        makes every drawdown ratio undefined / sign-inverted, since the peak
        starts there and only ratchets up). Mirrors the sibling
        _compute_simple_return_from_series initial==0.0 guard. A port-equity
        series is normally strictly positive; a non-positive first value is a
        degenerate snapshot, not a tradeable baseline.

    Pospisil-Vecer 2010: drawdown at time t is (S_t - sup_{s<=t} S_s) / sup_{s<=t} S_s.
    Maximum drawdown is the infimum of this over all t.
    """
    if not port_equity_series or len(port_equity_series) < 2:
        return None

    values = [p["port_value"] for p in port_equity_series]
    # A zero-anchored series (first value non-positive) has a degenerate
    # baseline: even a later real drawdown cannot be expressed against a
    # non-positive starting peak without ambiguity. Mirror the sibling
    # _compute_simple_return_from_series initial==0.0 guard.
    if values[0] <= 0.0:
        return None
    max_dd = 0.0
    peak = values[0]
    for v in values[1:]:
        if v > peak:
            peak = v
        else:
            # peak is strictly positive here: the first-value guard above
            # rejected a non-positive values[0], and peak only ratchets up.
            dd = (v - peak) / peak
            if dd < max_dd:
                max_dd = dd
    # max_dd is <= 0.0; return as negative float (convention: drawdown is negative)
    return float(max_dd)


def _compute_simple_return_from_series(port_equity_series: list[dict]) -> float | None:
    """
    Compute simple return from port-equity series: (final - initial) / initial.

    Returns None when the series is insufficient (fewer than 2 points).
    """
    if not port_equity_series or len(port_equity_series) < 2:
        return None

    initial = port_equity_series[0]["port_value"]
    final = port_equity_series[-1]["port_value"]
    if initial == 0.0:
        return None
    return float((final - initial) / initial)


# ---------------------------------------------------------------------------
# Port signal construction (AC-P2.6.*)
# ---------------------------------------------------------------------------

def build_port_signal(port_state: dict, cycle_id: str) -> dict:
    """
    Build the port-level signal payload from the current port_state.

    Returns the AC-P2.6.1 schema:
      triggered, triggered_reason, fired_at_cycle, target_reduction,
      port_total_reduction_usd.

    Pure function — reads port_state and env; no DB writes.
    target_reduction derivation (AC-P2.6.2) uses:
      - hwm_trailing_stop: per-ticker drawdown contributions
        (peak_exposure_usd - current_exposure_usd), clamped at 0
      - Other trigger families: uses ticker_drawdowns if provided as a fallback
    """
    triggered = bool(port_state.get("triggered", False))
    triggered_reason = port_state.get("triggered_reason")

    if not triggered:
        return {
            "triggered": False,
            "triggered_reason": triggered_reason,
            "fired_at_cycle": cycle_id,
            "target_reduction": [],
            "port_total_reduction_usd": 0.0,
        }

    # Build target_reduction per AC-P2.6.2
    target_reduction = _derive_target_reduction(port_state, triggered_reason)

    port_total_reduction_usd = float(
        sum(item["amount_usd"] for item in target_reduction)
    )

    return {
        "triggered": True,
        "triggered_reason": triggered_reason,
        "fired_at_cycle": cycle_id,
        "target_reduction": target_reduction,
        "port_total_reduction_usd": port_total_reduction_usd,
    }


def build_port_signal_with_authority(
    port_state: dict,
    cycle_id: str,
    exit_authority: str,
) -> dict:
    """
    Build port signal and annotate with 'actioned' flag based on exit_authority.

    AC-P2.6.5: When EXIT_AUTHORITY=per_symphony, the port signal is computed but
    NOT actioned (actioned=False). When EXIT_AUTHORITY=port_level, actioned=True
    iff triggered=True.
    """
    signal = build_port_signal(port_state=port_state, cycle_id=cycle_id)
    actioned = (exit_authority == "port_level") and signal["triggered"]
    signal["actioned"] = bool(actioned)
    signal["exit_authority"] = exit_authority
    return signal


def _derive_target_reduction(port_state: dict, triggered_reason: str | None) -> list[dict]:
    """
    Derive per-ticker reduction targets from port_state per trigger family (AC-P2.6.2).
    """
    # HWM trailing-stop: (peak_exposure - current_exposure) clamped at 0
    if triggered_reason == "hwm_trailing_stop":
        peak_exposures = port_state.get("ticker_peak_exposures", {})
        current_exposures = port_state.get("ticker_current_exposures", {})
        # Also accept the shorthand ticker_drawdowns for callers that pre-compute
        ticker_drawdowns = port_state.get("ticker_drawdowns", {})

        items = []
        if peak_exposures:
            for ticker, peak in peak_exposures.items():
                current = current_exposures.get(ticker, 0.0)
                amount = max(0.0, float(peak) - float(current))
                if amount > 0.0:
                    items.append({
                        "ticker": ticker,
                        "amount_usd": amount,
                        "reason_for_this_ticker": "hwm_drawdown",
                    })
        elif ticker_drawdowns:
            for ticker, amount in ticker_drawdowns.items():
                if float(amount) > 0.0:
                    items.append({
                        "ticker": ticker,
                        "amount_usd": float(amount),
                        "reason_for_this_ticker": "hwm_drawdown",
                    })
        return items

    # Fallback: use ticker_drawdowns if provided
    ticker_drawdowns = port_state.get("ticker_drawdowns", {})
    if ticker_drawdowns:
        return [
            {
                "ticker": ticker,
                "amount_usd": float(amount),
                "reason_for_this_ticker": triggered_reason or "unknown",
            }
            for ticker, amount in ticker_drawdowns.items()
            if float(amount) > 0.0
        ]

    return []


def persist_port_signal_snapshot(account_id: str, signal: dict) -> None:
    """
    Persist the port signal's last_target_reduction_json in port_state (AC-P2.6.4).

    Only persists when triggered=True (AC-P2.6.3: non-triggered signals do NOT
    update last_target_reduction_json).

    Has I/O — not pure. Imports database at call time to avoid circular imports.
    """
    if not signal.get("triggered"):
        return

    import database
    database.write_port_state(account_id, {
        "last_target_reduction_json": json.dumps(signal),
    })
