"""
RED tests for per-account parameters in port-mode (AC-P2.3.*).

These duplicate the parameter-lookup contract as a dedicated file to ensure the
implementer has a clear spec for the params routing layer.
"""

from __future__ import annotations

import pytest

# These imports WILL FAIL until implemented (RED intent)
from engine.params import get_params_for_altitude  # noqa: F401


PER_SYMPHONY_PARAMS = {
    "PARABOLIC_VELOCITY_THRESHOLD": 0.001,
    "VWAP_CROSS_HWM_PCT": 1.0,
    "TAKE_PROFIT_MC_PCT": 5.0,
    "VWAP_BLEED_MULTIPLIER": 1.5,
    "VWAP_BLEED_TICKS": 10,
    "MAX_PARABOLIC_SQUEEZE": 0.5,
}

PER_ACCOUNT_PARAMS = {
    "acct-portmode": {
        "PARABOLIC_VELOCITY_THRESHOLD": 0.003,
        "VWAP_CROSS_HWM_PCT": 1.8,
    }
}


class TestModeSpecificParamRouting:

    def test_port_altitude_uses_per_account_parabolic_threshold(self):
        """AC-P2.3.1: PARABOLIC_VELOCITY_THRESHOLD from per-account in port-altitude."""
        params = get_params_for_altitude(
            altitude="port_level",
            symphony_id="sym-any",
            account_id="acct-portmode",
            per_symphony_params=PER_SYMPHONY_PARAMS,
            per_account_params=PER_ACCOUNT_PARAMS,
        )
        assert params["PARABOLIC_VELOCITY_THRESHOLD"] == pytest.approx(0.003, rel=1e-6)

    def test_port_altitude_uses_per_account_vwap_cross_hwm_pct(self):
        """AC-P2.3.1: VWAP_CROSS_HWM_PCT from per-account in port-altitude."""
        params = get_params_for_altitude(
            altitude="port_level",
            symphony_id="sym-any",
            account_id="acct-portmode",
            per_symphony_params=PER_SYMPHONY_PARAMS,
            per_account_params=PER_ACCOUNT_PARAMS,
        )
        assert params["VWAP_CROSS_HWM_PCT"] == pytest.approx(1.8, rel=1e-6)

    def test_per_symphony_altitude_uses_per_symphony_parabolic_threshold(self):
        """AC-P2.3.4: Per-symphony altitude never falls back to per-account values."""
        params = get_params_for_altitude(
            altitude="per_symphony",
            symphony_id="sym-any",
            account_id="acct-portmode",
            per_symphony_params=PER_SYMPHONY_PARAMS,
            per_account_params=PER_ACCOUNT_PARAMS,
        )
        assert params["PARABOLIC_VELOCITY_THRESHOLD"] == pytest.approx(0.001, rel=1e-6)

    def test_mode_invariant_params_identical_in_both_altitudes(self):
        """AC-P2.3.2: Mode-invariant params (TAKE_PROFIT_MC_PCT, VWAP_BLEED_MULTIPLIER,
        VWAP_BLEED_TICKS, MAX_PARABOLIC_SQUEEZE) are per-symphony in BOTH altitudes."""
        port = get_params_for_altitude(
            altitude="port_level",
            symphony_id="sym-any",
            account_id="acct-portmode",
            per_symphony_params=PER_SYMPHONY_PARAMS,
            per_account_params=PER_ACCOUNT_PARAMS,
        )
        per_sym = get_params_for_altitude(
            altitude="per_symphony",
            symphony_id="sym-any",
            account_id="acct-portmode",
            per_symphony_params=PER_SYMPHONY_PARAMS,
            per_account_params=PER_ACCOUNT_PARAMS,
        )
        for param in ["TAKE_PROFIT_MC_PCT", "VWAP_BLEED_MULTIPLIER", "VWAP_BLEED_TICKS", "MAX_PARABOLIC_SQUEEZE"]:
            assert port[param] == pytest.approx(per_sym[param], rel=1e-6), (
                f"AC-P2.3.2: {param} must be identical in both altitudes"
            )

    def test_two_altitudes_are_independent_param_spaces(self):
        """AC-P2.3.4: The two altitudes are independent param spaces."""
        port = get_params_for_altitude(
            altitude="port_level",
            symphony_id="sym-any",
            account_id="acct-portmode",
            per_symphony_params=PER_SYMPHONY_PARAMS,
            per_account_params=PER_ACCOUNT_PARAMS,
        )
        per_sym = get_params_for_altitude(
            altitude="per_symphony",
            symphony_id="sym-any",
            account_id="acct-portmode",
            per_symphony_params=PER_SYMPHONY_PARAMS,
            per_account_params=PER_ACCOUNT_PARAMS,
        )
        # Mode-specific params differ between altitudes
        assert port["PARABOLIC_VELOCITY_THRESHOLD"] != per_sym["PARABOLIC_VELOCITY_THRESHOLD"], (
            "AC-P2.3.4: mode-specific params must differ between altitudes"
        )
        # Mutating one altitude's params must not affect the other
        port["PARABOLIC_VELOCITY_THRESHOLD"] = 999.0
        per_sym_again = get_params_for_altitude(
            altitude="per_symphony",
            symphony_id="sym-any",
            account_id="acct-portmode",
            per_symphony_params=PER_SYMPHONY_PARAMS,
            per_account_params=PER_ACCOUNT_PARAMS,
        )
        assert per_sym_again["PARABOLIC_VELOCITY_THRESHOLD"] == pytest.approx(0.001, rel=1e-6), (
            "AC-P2.3.4: altitude param dicts must be independent objects, not shared references"
        )
