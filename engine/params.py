"""
Per-altitude parameter routing (AC-P2.3.*).

MODE_SPECIFIC params (PARABOLIC_VELOCITY_THRESHOLD, VWAP_CROSS_HWM_PCT) are
looked up per-account in port-altitude, per-symphony in per-symphony-altitude.

MODE_INVARIANT params (TAKE_PROFIT_MC_PCT, VWAP_BLEED_MULTIPLIER, VWAP_BLEED_TICKS,
MAX_PARABOLIC_SQUEEZE) are always per-symphony in both altitudes (panel M1).

The two altitudes are independent param spaces: each call returns a fresh dict so
mutating the caller's copy has no cross-altitude side effects (AC-P2.3.4).
"""

from __future__ import annotations

# Mode-specific: overridden by per-account values in port-level altitude (AC-P2.3.3)
_MODE_SPECIFIC_PARAMS = frozenset(
    {
        "PARABOLIC_VELOCITY_THRESHOLD",
        "VWAP_CROSS_HWM_PCT",
    }
)

# Mode-invariant: always per-symphony regardless of altitude (AC-P2.3.2, panel M1)
_MODE_INVARIANT_PARAMS = frozenset(
    {
        "TAKE_PROFIT_MC_PCT",
        "VWAP_BLEED_MULTIPLIER",
        "VWAP_BLEED_TICKS",
        "MAX_PARABOLIC_SQUEEZE",
    }
)


def get_params_for_altitude(
    altitude: str,
    symphony_id: str,
    account_id: str,
    per_symphony_params: dict,
    per_account_params: dict,
) -> dict:
    """
    Return the resolved parameter dict for the given altitude.

    In port_level altitude: mode-specific params come from per_account_params[account_id];
    all others come from per_symphony_params.
    In per_symphony altitude: all params come from per_symphony_params.

    Always returns a fresh copy — callers may mutate without cross-altitude pollution.
    """
    result = dict(per_symphony_params)

    if altitude == "port_level":
        account_overrides = per_account_params.get(account_id, {})
        for key in _MODE_SPECIFIC_PARAMS:
            if key in account_overrides:
                result[key] = account_overrides[key]

    return result
