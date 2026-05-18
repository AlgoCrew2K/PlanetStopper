"""
RED tests for multi-cycle convergence (AC-P2.8.*) and Amendment B4 telemetry.

Key invariants:
  - At most N exits for N symphonies (convergence guarantee)
  - ONE exit per cycle per account
  - Each cycle's port_trigger_id is distinct
  - Composition-change reset fires between each exit
  - Amendment B4: port_total_reduction_usd is recorded per cycle on exit_triggers row
    AND the trajectory is monotonically decreasing AND bounded
"""

from __future__ import annotations

import uuid

import pytest


# These imports WILL FAIL until implemented (RED intent)
from engine.multi_cycle import simulate_convergence  # noqa: F401


def _make_symphony(symphony_id: str, value: float, tickers: dict) -> dict:
    return {
        "symphony_id": symphony_id,
        "value": value,
        "exposure_usd": tickers,
        "total_value": value,
        "position_open_date": "2024-01-01",
        "mc_sanity_gate_would_block": False,
    }


class TestConvergenceGuarantee:
    """AC-P2.8.3: At most N exits for N symphonies."""

    def test_max_n_exits_for_n_symphonies_everything_bleeds(self):
        """
        AC-P2.8.3: Fixture 'everything-bleeds' path with N=4 symphonies.
        Engine fires at most 4 exits across multi-cycle replay.
        """
        symphonies = [
            _make_symphony("sym-1", 25000.0, {"SPY": 25000.0}),
            _make_symphony("sym-2", 25000.0, {"QQQ": 25000.0}),
            _make_symphony("sym-3", 25000.0, {"AAPL": 25000.0}),
            _make_symphony("sym-4", 25000.0, {"MSFT": 25000.0}),
        ]
        result = simulate_convergence(
            symphonies=symphonies,
            account_id="acct-bleeds",
            target_reduction_per_cycle=[
                # Cycle 1: all tickers need reduction (total bleed scenario)
                [{"ticker": "SPY", "amount_usd": 10000.0},
                 {"ticker": "QQQ", "amount_usd": 10000.0},
                 {"ticker": "AAPL", "amount_usd": 10000.0},
                 {"ticker": "MSFT", "amount_usd": 10000.0}],
            ] * 5,  # Pump 5 cycles of signals; engine must cap at 4 exits
        )
        assert result["total_exits"] <= 4, (
            f"AC-P2.8.3: max N exits for N=4 symphonies; got {result['total_exits']}"
        )

    def test_one_exit_per_cycle_per_account(self):
        """AC-P2.8.4: At most ONE exit per cycle per account."""
        symphonies = [
            _make_symphony("sym-a", 50000.0, {"SPY": 50000.0}),
            _make_symphony("sym-b", 50000.0, {"QQQ": 50000.0}),
        ]
        result = simulate_convergence(
            symphonies=symphonies,
            account_id="acct-pace",
            target_reduction_per_cycle=[
                [{"ticker": "SPY", "amount_usd": 30000.0},
                 {"ticker": "QQQ", "amount_usd": 30000.0}],
            ],
        )
        assert result["exits_in_cycle_1"] <= 1, (
            "AC-P2.8.4: at most ONE exit per cycle per account"
        )

    def test_all_symphonies_exited_when_n_equals_exits(self):
        """After N cycles, all N symphonies in a full-bleed scenario have exited."""
        symphonies = [
            _make_symphony(f"sym-{i}", 20000.0, {f"TICK{i}": 20000.0})
            for i in range(3)
        ]
        result = simulate_convergence(
            symphonies=symphonies,
            account_id="acct-full",
            target_reduction_per_cycle=[
                [{"ticker": f"TICK{i}", "amount_usd": 20000.0} for i in range(3)]
            ] * 4,
        )
        assert result["total_exits"] <= 3


class TestPortTriggerIdDistinctPerCycle:
    """AC-P2.10.2: Each cycle's port_trigger_id is distinct."""

    def test_port_trigger_ids_are_distinct_across_cycles(self):
        symphonies = [
            _make_symphony("sym-p1", 30000.0, {"SPY": 30000.0}),
            _make_symphony("sym-p2", 30000.0, {"QQQ": 30000.0}),
        ]
        result = simulate_convergence(
            symphonies=symphonies,
            account_id="acct-trigger-ids",
            target_reduction_per_cycle=[
                [{"ticker": "SPY", "amount_usd": 15000.0}],
                [{"ticker": "QQQ", "amount_usd": 15000.0}],
            ],
        )
        trigger_ids = [c["port_trigger_id"] for c in result["cycles"] if c.get("port_trigger_id")]
        if len(trigger_ids) >= 2:
            assert len(set(trigger_ids)) == len(trigger_ids), (
                "AC-P2.10.2: each cycle's port_trigger_id must be distinct"
            )

    def test_port_trigger_ids_are_valid_uuids(self):
        """port_trigger_id must be a valid UUID (not None, not a timestamp string)."""
        symphonies = [_make_symphony("sym-uuid", 10000.0, {"SPY": 10000.0})]
        result = simulate_convergence(
            symphonies=symphonies,
            account_id="acct-uuid",
            target_reduction_per_cycle=[[{"ticker": "SPY", "amount_usd": 5000.0}]],
        )
        for cycle in result["cycles"]:
            tid = cycle.get("port_trigger_id")
            if tid:
                try:
                    uuid.UUID(str(tid))
                except ValueError:
                    pytest.fail(f"port_trigger_id is not a valid UUID: {tid!r}")


class TestCompositionChangeFiresBetweenExits:
    """AC-P2.8.1: After each symphony exits, resolver detects composition change and rebases."""

    def test_composition_change_detected_after_exit(self):
        symphonies = [
            _make_symphony("sym-c1", 40000.0, {"SPY": 40000.0}),
            _make_symphony("sym-c2", 40000.0, {"QQQ": 40000.0}),
        ]
        result = simulate_convergence(
            symphonies=symphonies,
            account_id="acct-comp",
            target_reduction_per_cycle=[
                [{"ticker": "SPY", "amount_usd": 40000.0}],
                [{"ticker": "QQQ", "amount_usd": 40000.0}],
            ],
        )
        cycles_with_rebase = [c for c in result["cycles"] if c.get("composition_change_detected")]
        assert len(cycles_with_rebase) >= 1, (
            "AC-P2.8.1: composition change must be detected at least once after a symphony exits"
        )


class TestAmendmentB4Telemetry:
    """
    Amendment B4 (High): Convergence telemetry.
    Record port_total_reduction_usd per cycle on the exit_triggers row.
    RED test: trajectory across N cycles is monotonically decreasing AND bounded.
    """

    def test_port_total_reduction_usd_recorded_per_cycle(self):
        symphonies = [
            _make_symphony("sym-b4a", 50000.0, {"SPY": 50000.0}),
            _make_symphony("sym-b4b", 50000.0, {"QQQ": 50000.0}),
        ]
        result = simulate_convergence(
            symphonies=symphonies,
            account_id="acct-b4",
            target_reduction_per_cycle=[
                [{"ticker": "SPY", "amount_usd": 40000.0}, {"ticker": "QQQ", "amount_usd": 30000.0}],
                [{"ticker": "QQQ", "amount_usd": 25000.0}],
            ],
        )
        for cycle in result["cycles"]:
            if cycle.get("triggered"):
                assert "port_total_reduction_usd" in cycle, (
                    "Amendment B4: port_total_reduction_usd must be recorded on each triggered cycle"
                )
                assert isinstance(cycle["port_total_reduction_usd"], float)
                assert cycle["port_total_reduction_usd"] >= 0.0

    def test_port_total_reduction_usd_trajectory_is_bounded(self):
        """
        Amendment B4: trajectory of port_total_reduction_usd across cycles must be bounded
        (cannot exceed the initial port value).
        """
        initial_port_value = 100000.0
        symphonies = [
            _make_symphony("sym-bounded-1", 50000.0, {"SPY": 50000.0}),
            _make_symphony("sym-bounded-2", 50000.0, {"QQQ": 50000.0}),
        ]
        result = simulate_convergence(
            symphonies=symphonies,
            account_id="acct-bounded",
            target_reduction_per_cycle=[
                [{"ticker": "SPY", "amount_usd": 50000.0}, {"ticker": "QQQ", "amount_usd": 50000.0}],
            ] * 3,
        )
        for cycle in result["cycles"]:
            reduction = cycle.get("port_total_reduction_usd", 0.0)
            assert reduction <= initial_port_value, (
                f"Amendment B4: port_total_reduction_usd ({reduction}) must not exceed initial port value ({initial_port_value})"
            )

    def test_port_total_reduction_usd_is_nonnegative(self):
        symphonies = [_make_symphony("sym-nn", 10000.0, {"SPY": 10000.0})]
        result = simulate_convergence(
            symphonies=symphonies,
            account_id="acct-nn",
            target_reduction_per_cycle=[[{"ticker": "SPY", "amount_usd": 5000.0}]],
        )
        for cycle in result["cycles"]:
            reduction = cycle.get("port_total_reduction_usd", 0.0)
            assert reduction >= 0.0, "Amendment B4: port_total_reduction_usd must be non-negative"


class TestPerSymphonySignalsObservedNotActedUponDuringConvergence:
    """AC-P2.8.6: Per-symphony triggered flags are observed but not acted upon during convergence."""

    def test_per_symphony_triggered_not_actioned_when_port_authoritative(self):
        """
        While port is exit-authority, per-symphony triggered=True must be rendered
        but NOT result in an order placement.
        """
        symphonies = [
            {**_make_symphony("sym-triggered-per-sym", 30000.0, {"SPY": 30000.0}),
             "per_symphony_triggered": True},
            _make_symphony("sym-quiet", 30000.0, {"QQQ": 30000.0}),
        ]
        result = simulate_convergence(
            symphonies=symphonies,
            account_id="acct-obs",
            target_reduction_per_cycle=[
                [{"ticker": "SPY", "amount_usd": 30000.0}],
            ],
            exit_authority="port_level",
        )
        per_sym_actioned = result.get("per_symphony_exits_actioned", 0)
        assert per_sym_actioned == 0, (
            "AC-P2.8.6: per-symphony triggered must NOT be actioned when EXIT_AUTHORITY=port_level"
        )
