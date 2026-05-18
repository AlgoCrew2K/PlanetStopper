"""
RED tests for port_state typed table, helpers, and wipe_transient_state allowlist
(AC-P2.5.*)

These tests target database.py additions:
  - port_state typed table (migration 010_port_state.sql)
  - read_port_state(account_id), write_port_state(account_id, state), clear_port_state(account_id)
  - wipe_transient_state allowlist refactor (AC-P2.5.3)
  - new-day reset sentinel for port_state (AC-P2.5.4)
  - composition change reset (AC-P2.5.5)
  - port_breakeven_active non-latching (AC-P2.5.6)

All tests use in-memory SQLite (not file-based) to avoid test pollution.
"""

from __future__ import annotations

import sqlite3
import json

import pytest

# These imports WILL FAIL until database.py is extended (RED intent)
from database import (  # noqa: F401
    read_port_state,
    write_port_state,
    clear_port_state,
    wipe_transient_state,
)


@pytest.fixture()
def mem_db(tmp_path, monkeypatch):
    """
    Provide a fresh in-memory SQLite connection with the port_state table applied.
    Monkeypatches database.get_connection to return this in-memory DB.
    """
    db_path = str(tmp_path / "test_state.db")
    monkeypatch.setenv("DB_PATH", db_path)

    import database
    database.init_db()
    yield db_path


# ---------------------------------------------------------------------------
# AC-P2.5.1: port_state typed table structure
# ---------------------------------------------------------------------------

class TestPortStateTable:

    REQUIRED_COLUMNS = {
        "account_id",
        "high_water_mark",
        "safe_hwm",
        "shadow_hwm",
        "vwap_ticks_json",
        "vwap_bleed_ticks_json",
        "mc_history_json",
        "mc_prob",
        "armed",
        "para_armed",
        "port_breakeven_active",
        "triggered",
        "triggered_reason",
        "prev_return",
        "current_return",
        "composition_hash",
        "last_target_reduction_json",
        "last_selected_symphony_id",
        "updated_at",
    }

    def test_port_state_table_exists(self, mem_db):
        import database
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='port_state'"
        )
        row = cursor.fetchone()
        conn.close()
        assert row is not None, "port_state table must exist after init_db()"

    def test_port_state_has_required_columns(self, mem_db):
        import database
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(port_state)")
        cols = {row[1] for row in cursor.fetchall()}
        conn.close()
        missing = self.REQUIRED_COLUMNS - cols
        assert not missing, f"port_state table missing columns: {missing}"

    def test_account_id_is_primary_key(self, mem_db):
        import database
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(port_state)")
        rows = cursor.fetchall()
        conn.close()
        pk_cols = [row[1] for row in rows if row[5] == 1]
        assert "account_id" in pk_cols, "account_id must be the PRIMARY KEY of port_state"


# ---------------------------------------------------------------------------
# AC-P2.5.2: CRUD helpers
# ---------------------------------------------------------------------------

class TestPortStateHelpers:

    def test_read_returns_none_when_no_row(self, mem_db):
        result = read_port_state("acct-nonexistent")
        assert result is None

    def test_write_then_read_roundtrip(self, mem_db):
        state = {
            "high_water_mark": 105000.0,
            "safe_hwm": 100000.0,
            "shadow_hwm": 103000.0,
            "vwap_ticks_json": "[]",
            "vwap_bleed_ticks_json": "[]",
            "mc_history_json": "[]",
            "mc_prob": 0.75,
            "armed": True,
            "para_armed": False,
            "port_breakeven_active": False,
            "triggered": False,
            "triggered_reason": None,
            "prev_return": 0.05,
            "current_return": 0.06,
            "composition_hash": "abc123",
            "last_target_reduction_json": None,
            "last_selected_symphony_id": None,
        }
        write_port_state("acct-001", state)
        result = read_port_state("acct-001")
        assert result is not None
        assert result["high_water_mark"] == pytest.approx(105000.0, rel=1e-6)
        assert result["composition_hash"] == "abc123"

    def test_write_overwrites_on_same_account_id(self, mem_db):
        write_port_state("acct-002", {"high_water_mark": 1000.0, "composition_hash": "hash-v1"})
        write_port_state("acct-002", {"high_water_mark": 2000.0, "composition_hash": "hash-v2"})
        result = read_port_state("acct-002")
        assert result["high_water_mark"] == pytest.approx(2000.0, rel=1e-6)
        assert result["composition_hash"] == "hash-v2"

    def test_clear_removes_row(self, mem_db):
        write_port_state("acct-003", {"high_water_mark": 999.0, "composition_hash": "x"})
        clear_port_state("acct-003")
        result = read_port_state("acct-003")
        assert result is None

    def test_clear_nonexistent_account_does_not_raise(self, mem_db):
        clear_port_state("acct-ghost")  # Should not raise


# ---------------------------------------------------------------------------
# AC-P2.5.3: wipe_transient_state allowlist — port_state NOT clobbered
# ---------------------------------------------------------------------------

class TestWipeTransientStateAllowlist:
    """
    AC-P2.5.3: wipe_transient_state must NOT clobber reserved keys.
    Reserved keys that must survive: date, last_execution_mode,
    last_market_close_snapshot. port_state is a SEPARATE TABLE so bot_state
    clobber does not reach it — but this test confirms wipe_transient_state
    does not mutate keys with non-symphony-shaped values.
    """

    def test_wipe_does_not_clobber_reserved_date_key(self):
        state = {
            "date": "2024-11-01",
            "sym-ABC": {
                "high_water_mark": 100.0,
                "armed": True,
            },
        }
        wipe_transient_state(state)
        assert state["date"] == "2024-11-01", (
            "wipe_transient_state must not clobber reserved 'date' key"
        )

    def test_wipe_does_not_clobber_last_execution_mode(self):
        state = {
            "last_execution_mode": "LIVE",
            "sym-XYZ": {
                "high_water_mark": 100.0,
                "armed": False,
            },
        }
        wipe_transient_state(state)
        assert state["last_execution_mode"] == "LIVE"

    def test_wipe_does_not_clobber_last_market_close_snapshot(self):
        state = {
            "last_market_close_snapshot": {"price": 450.0},
            "sym-QRS": {
                "high_water_mark": 100.0,
                "armed": False,
            },
        }
        wipe_transient_state(state)
        assert state["last_market_close_snapshot"] == {"price": 450.0}, (
            "wipe_transient_state latent bug (AC-P2.5.3): must not clobber last_market_close_snapshot"
        )

    def test_wipe_resets_symphony_transient_fields(self):
        """Per-symphony transient fields ARE reset (existing behavior preserved)."""
        state = {
            "sym-AAA": {
                "high_water_mark": 110.0,
                "armed": True,
                "triggered": True,
                "mc_history": [0.8, 0.9],
            },
        }
        wipe_transient_state(state)
        assert state["sym-AAA"]["armed"] is False
        assert state["sym-AAA"]["triggered"] is False
        assert state["sym-AAA"]["mc_history"] == []


# ---------------------------------------------------------------------------
# AC-P2.5.4: new-day reset sentinel for port_state (prev_return=None)
# ---------------------------------------------------------------------------

class TestNewDayResetSentinel:

    def test_new_day_reset_sets_prev_return_none(self, mem_db):
        """
        AC-P2.5.4: On new-day reset for each account, port_state.prev_return = None
        so cycle-1 yields zero velocity and PARA-ARM cannot fire on the opening gap.
        """
        write_port_state("acct-newday", {
            "high_water_mark": 100000.0,
            "prev_return": 0.05,
            "composition_hash": "hash-a",
        })

        import database
        database.new_day_reset_port_state("acct-newday")

        result = read_port_state("acct-newday")
        assert result["prev_return"] is None, (
            "AC-P2.5.4: port_state.prev_return must be None after new-day reset"
        )


# ---------------------------------------------------------------------------
# AC-P2.5.5: Composition change reset
# ---------------------------------------------------------------------------

class TestCompositionChangeReset:

    def test_composition_change_rebases_port_state(self, mem_db):
        """
        AC-P2.5.5: On composition change (new composition_hash detected), port_state
        resets: prev_return=None, mc_history=[], vwap_ticks=[], vwap_bleed_ticks=[],
        high_water_mark=current_value, port_breakeven_active=False, armed=False,
        para_armed=False.
        """
        write_port_state("acct-compose", {
            "high_water_mark": 100000.0,
            "prev_return": 0.05,
            "mc_history_json": "[0.8, 0.9]",
            "vwap_ticks_json": "[1, 2, 3]",
            "vwap_bleed_ticks_json": "[1]",
            "armed": True,
            "para_armed": True,
            "port_breakeven_active": True,
            "stop_trigger": 0.03,
            "composition_hash": "old-hash",
        })

        import database
        database.rebase_port_state_on_composition_change(
            "acct-compose",
            new_composition_hash="new-hash",
            current_port_value=110000.0,
        )

        result = read_port_state("acct-compose")
        assert result["prev_return"] is None
        assert json.loads(result["mc_history_json"]) == []
        assert json.loads(result["vwap_ticks_json"]) == []
        assert json.loads(result["vwap_bleed_ticks_json"]) == []
        assert result["high_water_mark"] == pytest.approx(110000.0, rel=1e-6)
        assert result["port_breakeven_active"] == 0 or result["port_breakeven_active"] is False
        assert result["armed"] == 0 or result["armed"] is False
        assert result["para_armed"] == 0 or result["para_armed"] is False
        assert result["composition_hash"] == "new-hash"

    def test_composition_change_resets_stop_trigger(self, mem_db):
        """
        Amendment BC-3 follow-up: composition-change rebase MUST also reset
        stop_trigger=None (in addition to port_breakeven_active=False).
        This is required for R3a ratchet correctness.
        """
        write_port_state("acct-stoptrig", {
            "high_water_mark": 100000.0,
            "stop_trigger": 0.04,
            "composition_hash": "old-hash-b",
        })

        import database
        database.rebase_port_state_on_composition_change(
            "acct-stoptrig",
            new_composition_hash="new-hash-b",
            current_port_value=100000.0,
        )

        result = read_port_state("acct-stoptrig")
        assert result.get("stop_trigger") is None, (
            "Amendment BC-3: stop_trigger must be None after composition-change rebase"
        )


# ---------------------------------------------------------------------------
# AC-P2.5.6: port_breakeven_active is non-latching
# ---------------------------------------------------------------------------

class TestPortBreakevenNonLatching:

    def test_port_breakeven_active_can_go_false_after_true(self, mem_db):
        """
        AC-P2.5.6: port_breakeven_active is non-latching. Unlike per-symphony
        breakeven_locked (which is permanently True once set), port_breakeven_active
        is re-evaluated each cycle and CAN revert to False.
        """
        write_port_state("acct-breakeven", {
            "port_breakeven_active": True,
            "composition_hash": "hash-be",
        })
        write_port_state("acct-breakeven", {
            "port_breakeven_active": False,
            "composition_hash": "hash-be",
        })
        result = read_port_state("acct-breakeven")
        is_false = result["port_breakeven_active"] in (0, False, None)
        assert is_false, (
            "AC-P2.5.6: port_breakeven_active is non-latching; must be overwriteable to False"
        )
