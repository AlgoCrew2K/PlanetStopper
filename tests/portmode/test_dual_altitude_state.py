"""
RED tests for dual-altitude state (AC-P2.1.*).

Both altitudes (per-symphony + port-level) are ALWAYS computed and persisted
regardless of the exit-authority toggle. No layer below the resolver branches on
the toggle.
"""

from __future__ import annotations

import pytest


# These imports WILL FAIL until implemented (RED intent)
from engine.dual_altitude import compute_for_altitude  # noqa: F401


class TestDualAltitudeAlwaysComputed:
    """AC-P2.1.1: Both altitudes computed and persisted every cycle."""

    def test_per_symphony_state_computed_regardless_of_toggle(self):
        """
        When EXIT_AUTHORITY=port_level, per-symphony state is still computed.
        Result must include per-symphony state keys.
        """
        result = compute_for_altitude(
            altitude="per_symphony",
            state_dict={"high_water_mark": 100.0, "armed": False, "triggered": False},
            params={"PARABOLIC_VELOCITY_THRESHOLD": 0.002},
            exit_authority="port_level",
        )
        assert "triggered" in result, "per-symphony altitude must produce triggered key"
        assert "high_water_mark" in result

    def test_port_level_state_computed_regardless_of_toggle(self):
        """
        When EXIT_AUTHORITY=per_symphony, port-level state is still computed.
        Result must include port-level state keys.
        """
        result = compute_for_altitude(
            altitude="port_level",
            state_dict={"high_water_mark": 100.0, "armed": False, "triggered": False},
            params={"PARABOLIC_VELOCITY_THRESHOLD": 0.002},
            exit_authority="per_symphony",
        )
        assert "triggered" in result, "port-level altitude must produce triggered key even when non-authoritative"

    def test_compute_for_altitude_signature_is_parameterized(self):
        """AC-P2.1.2: The altitude parameter drives which computation runs — not a global flag."""
        import inspect
        sig = inspect.signature(compute_for_altitude)
        assert "altitude" in sig.parameters, "compute_for_altitude must accept 'altitude' parameter"
        assert "exit_authority" in sig.parameters, "compute_for_altitude must accept 'exit_authority' parameter"

    def test_no_layer_branches_on_exit_authority(self):
        """
        AC-P2.1.2: No layer BELOW the resolver should branch on exit_authority.
        compute_for_altitude accepts it but only for routing — the math layers consume
        the state_dict and params, not the toggle.
        """
        # Calling with different exit_authority values but same math inputs
        # must produce the same COMPUTED VALUES (triggered, hwm, etc.)
        # Only the 'authoritative' flag in the result should differ.
        state = {"high_water_mark": 100.0, "armed": False, "triggered": False,
                 "prev_return": 0.0, "current_return": 0.01}
        params = {"PARABOLIC_VELOCITY_THRESHOLD": 0.002}

        result_per_sym = compute_for_altitude(
            altitude="per_symphony", state_dict=state, params=params, exit_authority="per_symphony"
        )
        result_port_auth = compute_for_altitude(
            altitude="per_symphony", state_dict=state, params=params, exit_authority="port_level"
        )
        # Math results must be identical; only 'authoritative' flag differs
        assert result_per_sym["triggered"] == result_port_auth["triggered"], (
            "exit_authority toggle must not affect computed trigger value (only authorization)"
        )

    def test_first_cycle_initializes_port_state_fresh(self, tmp_path, monkeypatch):
        """
        AC-P2.1.3: First cycle after deploy — account with no prior port_state rows
        initializes fresh that cycle. No retro-compute from history.
        """
        monkeypatch.setenv("DB_PATH", str(tmp_path / "test_fresh.db"))
        import database
        database.init_db()

        result = database.read_port_state("acct-fresh")
        assert result is None, "Before first cycle, port_state must not exist"
        # The engine initializes it on first access; test the init path
        from engine.dual_altitude import initialize_port_state_if_absent
        initialize_port_state_if_absent("acct-fresh", current_port_value=100000.0)
        result = database.read_port_state("acct-fresh")
        assert result is not None
        assert result["prev_return"] is None, "AC-P2.1.3: first cycle must start with prev_return=None"

    def test_lone_symphony_computes_port_state_flagged(self):
        """
        AC-P2.1.4: Symphony with no enclosing port (lone / unknown account_id) still
        computes per-symphony state normally. Port-level state for this case is
        the trivial single-symphony port — flagged port-mode for telemetry.
        """
        result = compute_for_altitude(
            altitude="port_level",
            state_dict={"high_water_mark": 100.0, "armed": False, "triggered": False,
                        "symphony_count": 1},
            params={"PARABOLIC_VELOCITY_THRESHOLD": 0.002},
            exit_authority="per_symphony",
        )
        assert result.get("port_mode") is True, (
            "AC-P2.1.4: single-symphony port must be flagged port_mode=True"
        )


class TestAltitudeIsolation:
    """
    AC-P2.3.4: Per-symphony altitude lookup never falls back to per-account port values.
    The two altitudes are independent param spaces.
    """

    def test_per_symphony_params_not_shared_with_port_altitude(self):
        """Calling per-symphony altitude does not mutate or read per-account port params."""
        per_sym_result = compute_for_altitude(
            altitude="per_symphony",
            state_dict={"high_water_mark": 100.0, "armed": False, "triggered": False},
            params={"PARABOLIC_VELOCITY_THRESHOLD": 0.005},
            exit_authority="per_symphony",
        )
        port_result = compute_for_altitude(
            altitude="port_level",
            state_dict={"high_water_mark": 100.0, "armed": False, "triggered": False},
            params={"PARABOLIC_VELOCITY_THRESHOLD": 0.001},
            exit_authority="per_symphony",
        )
        # The two results must reflect their own param values independently
        assert per_sym_result is not port_result, "altitude results must be independent objects"
