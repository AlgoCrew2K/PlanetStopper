"""
Port-level aggregator: aggregate_to_port (AC-P2.4.*)

Pure function — no I/O, no state mutation, no time-of-day awareness.

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

    Returns None when the series is insufficient (fewer than 2 points).

    Pospisil-Vecer 2010: drawdown at time t is (S_t - sup_{s<=t} S_s) / sup_{s<=t} S_s.
    Maximum drawdown is the infimum of this over all t.
    """
    if not port_equity_series or len(port_equity_series) < 2:
        return None

    values = [p["port_value"] for p in port_equity_series]
    max_dd = 0.0
    peak = values[0]
    for v in values[1:]:
        if v > peak:
            peak = v
        else:
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
