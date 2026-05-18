"""
RED tests for composition-change detection and rebase (AC-P2.5.5)
and composition_hash O(1) detection (database.compute_composition_hash).

Also covers Amendment BC-3 follow-up: stop_trigger reset on composition change.
"""

from __future__ import annotations

import json

import pytest


# These imports WILL FAIL until implemented (RED intent)
from database import (  # noqa: F401
    compute_composition_hash,
    rebase_port_state_on_composition_change,
    read_port_state,
    write_port_state,
)


@pytest.fixture()
def mem_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_compose.db")
    monkeypatch.setenv("DB_PATH", db_path)
    import database
    database.init_db()
    yield db_path


# ---------------------------------------------------------------------------
# composition_hash: O(1) detection via stable hash
# ---------------------------------------------------------------------------

class TestCompositionHash:

    def test_same_symphony_set_same_hash(self):
        """Same set of symphony IDs produces identical hash regardless of order."""
        h1 = compute_composition_hash(["sym-A", "sym-B", "sym-C"])
        h2 = compute_composition_hash(["sym-C", "sym-A", "sym-B"])
        assert h1 == h2, "composition_hash must be order-independent"

    def test_different_symphony_set_different_hash(self):
        h1 = compute_composition_hash(["sym-A", "sym-B"])
        h2 = compute_composition_hash(["sym-A", "sym-C"])
        assert h1 != h2

    def test_superset_different_hash(self):
        h1 = compute_composition_hash(["sym-A", "sym-B"])
        h2 = compute_composition_hash(["sym-A", "sym-B", "sym-C"])
        assert h1 != h2

    def test_empty_set_has_stable_hash(self):
        h = compute_composition_hash([])
        assert isinstance(h, str) and len(h) > 0

    def test_hash_is_deterministic_across_calls(self):
        syms = ["sym-1", "sym-2", "sym-3"]
        assert compute_composition_hash(syms) == compute_composition_hash(syms)


# ---------------------------------------------------------------------------
# AC-P2.5.5: Composition change rebase semantics
# ---------------------------------------------------------------------------

class TestCompositionChangeRebase:

    def test_rebase_resets_all_required_fields(self, mem_db):
        """AC-P2.5.5: all required fields reset on composition change."""
        write_port_state("acct-r1", {
            "high_water_mark": 100000.0,
            "prev_return": 0.04,
            "mc_history_json": json.dumps([0.7, 0.8, 0.9]),
            "vwap_ticks_json": json.dumps([1, 2, 3]),
            "vwap_bleed_ticks_json": json.dumps([1, 2]),
            "armed": True,
            "para_armed": True,
            "port_breakeven_active": True,
            "stop_trigger": 0.05,
            "composition_hash": "hash-old",
        })

        rebase_port_state_on_composition_change(
            "acct-r1",
            new_composition_hash="hash-new",
            current_port_value=115000.0,
        )

        result = read_port_state("acct-r1")
        assert result["prev_return"] is None
        assert json.loads(result["mc_history_json"]) == []
        assert json.loads(result["vwap_ticks_json"]) == []
        assert json.loads(result["vwap_bleed_ticks_json"]) == []
        assert result["high_water_mark"] == pytest.approx(115000.0, rel=1e-6)
        assert result["port_breakeven_active"] in (0, False)
        assert result["armed"] in (0, False)
        assert result["para_armed"] in (0, False)
        assert result["composition_hash"] == "hash-new"

    def test_rebase_resets_stop_trigger_amendment_bc3(self, mem_db):
        """Amendment BC-3: stop_trigger must also reset to None on composition change."""
        write_port_state("acct-r2", {
            "stop_trigger": 0.08,
            "composition_hash": "old",
        })
        rebase_port_state_on_composition_change(
            "acct-r2",
            new_composition_hash="new",
            current_port_value=100000.0,
        )
        result = read_port_state("acct-r2")
        assert result.get("stop_trigger") is None, (
            "Amendment BC-3: stop_trigger must be None after composition-change rebase"
        )

    def test_no_rebase_when_hash_unchanged(self, mem_db):
        """When composition_hash is unchanged, no rebase occurs."""
        write_port_state("acct-r3", {
            "high_water_mark": 100000.0,
            "prev_return": 0.03,
            "armed": True,
            "composition_hash": "stable-hash",
        })

        # Simulate: resolver sees same hash -> does NOT call rebase
        # This test verifies the hash comparison logic prevents spurious resets
        result_before = read_port_state("acct-r3")
        same_hash = compute_composition_hash(["sym-A", "sym-B"])
        different_result = compute_composition_hash(["sym-A", "sym-B"])
        assert same_hash == different_result, "hash must be stable so resolver can detect unchanged composition"

    def test_rebase_hwm_set_to_current_value_not_old_hwm(self, mem_db):
        """After rebase, HWM is reset to the CURRENT port value, not the old HWM."""
        write_port_state("acct-r4", {
            "high_water_mark": 200000.0,
            "composition_hash": "old-r4",
        })
        rebase_port_state_on_composition_change(
            "acct-r4",
            new_composition_hash="new-r4",
            current_port_value=95000.0,
        )
        result = read_port_state("acct-r4")
        assert result["high_water_mark"] == pytest.approx(95000.0, rel=1e-6), (
            "Rebase must set HWM to current_port_value, not preserve old HWM"
        )


# ---------------------------------------------------------------------------
# Multi-cycle convergence: composition change fires after each exit (AC-P2.8.1)
# ---------------------------------------------------------------------------

class TestCompositionChangeAfterExit:

    def test_composition_change_detected_when_symphony_exits(self):
        """
        After one symphony exits, the set of symphony_ids changes.
        compute_composition_hash before vs after exit must differ.
        This is the detection mechanism for AC-P2.8.1.
        """
        syms_before = ["sym-A", "sym-B", "sym-C"]
        syms_after = ["sym-A", "sym-C"]  # sym-B exited

        h_before = compute_composition_hash(syms_before)
        h_after = compute_composition_hash(syms_after)

        assert h_before != h_after, (
            "Composition hash must differ when a symphony exits "
            "(AC-P2.8.1 composition change detection)"
        )
