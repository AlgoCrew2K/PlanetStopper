"""
RED tests for exit_triggers telemetry schema extensions (AC-P2.10.*).

Covers: math_mode column, port_trigger_id column, partial index,
gate_state_json port payload, /api/triggers surface.
"""

from __future__ import annotations

import json
import uuid

import pytest


@pytest.fixture()
def mem_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test_telem.db"))
    import database
    database.init_db()
    yield str(tmp_path / "test_telem.db")


class TestExitTriggersSchemaExtensions:
    """AC-P2.10.1, AC-P2.10.2: math_mode and port_trigger_id columns."""

    def test_exit_triggers_has_math_mode_column(self, mem_db):
        import database
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(exit_triggers)")
        cols = {row[1] for row in cursor.fetchall()}
        conn.close()
        assert "math_mode" in cols, "AC-P2.10.1: exit_triggers must have math_mode column"

    def test_exit_triggers_has_port_trigger_id_column(self, mem_db):
        import database
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(exit_triggers)")
        cols = {row[1] for row in cursor.fetchall()}
        conn.close()
        assert "port_trigger_id" in cols, "AC-P2.10.2: exit_triggers must have port_trigger_id column"

    def test_math_mode_nullable_backfills_null(self, mem_db):
        """AC-P2.10.1: existing rows (pre-migration) have NULL math_mode — safe backfill."""
        import database
        conn = database.get_connection()
        cursor = conn.cursor()
        # Insert a row without math_mode to simulate pre-migration row
        cursor.execute(
            "INSERT INTO exit_triggers "
            "(ts_utc, ts_et, symphony_id, account_id, triggered_reason, at_return, gate_state_json, cycle_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("2024-01-01T10:00:00Z", "2024-01-01T10:00:00", "sym-legacy",
             "acct-x", "hwm_trailing_stop", 0.05, "{}", "cycle-001"),
        )
        conn.commit()
        cursor.execute("SELECT math_mode FROM exit_triggers WHERE symphony_id = 'sym-legacy'")
        row = cursor.fetchone()
        conn.close()
        assert row[0] is None, "Pre-migration rows must have NULL math_mode"

    def test_port_trigger_id_nullable(self, mem_db):
        """port_trigger_id is NULLable (per-symphony exits do not have it)."""
        import database
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO exit_triggers "
            "(ts_utc, ts_et, symphony_id, account_id, triggered_reason, at_return, gate_state_json, cycle_id, math_mode) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("2024-01-01T10:00:00Z", "2024-01-01T10:00:00", "sym-per-sym",
             "acct-y", "hwm_trailing_stop", 0.04, "{}", "cycle-002", "per_symphony"),
        )
        conn.commit()
        cursor.execute("SELECT port_trigger_id FROM exit_triggers WHERE symphony_id = 'sym-per-sym'")
        row = cursor.fetchone()
        conn.close()
        assert row[0] is None, "Per-symphony exits must have NULL port_trigger_id"


class TestExitTriggersPartialIndex:
    """AC-P2.10.5: Partial index on port_trigger_id WHERE NOT NULL."""

    def test_partial_index_exists(self, mem_db):
        import database
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_exit_triggers_port_trigger_id'"
        )
        row = cursor.fetchone()
        conn.close()
        assert row is not None, (
            "AC-P2.10.5: partial index idx_exit_triggers_port_trigger_id must exist"
        )


class TestPortDrivenExitTelemetry:
    """AC-P2.10.3: gate_state_json for port-driven exit carries full telemetry payload."""

    REQUIRED_GATE_STATE_KEYS = {
        "target_reduction",
        "candidate_scores",
        "selected_symphony_id",
    }

    def test_port_driven_exit_gate_state_has_required_keys(self, mem_db):
        import database

        gate_state = {
            "target_reduction": [{"ticker": "SPY", "amount_usd": 5000.0, "reason_for_this_ticker": "hwm_drawdown"}],
            "candidate_scores": [
                {"symphony_id": "sym-winner", "score": 100.0},
                {"symphony_id": "sym-runner-up", "score": 500.0},
            ],
            "selected_symphony_id": "sym-winner",
            "tie_breaker_used": None,
            "abort_flag": False,
        }

        trigger_id = str(uuid.uuid4())
        database.record_exit_trigger(
            ts_utc="2024-11-01T11:00:00Z",
            ts_et="2024-11-01T11:00:00",
            symphony_id="sym-winner",
            account_id="acct-port",
            triggered_reason="hwm_trailing_stop",
            at_return=0.05,
            gate_state_json=json.dumps(gate_state),
            cycle_id="cycle-port-001",
            math_mode="port_level",
            port_trigger_id=trigger_id,
        )

        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT gate_state_json FROM exit_triggers WHERE port_trigger_id = ?",
            (trigger_id,),
        )
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        stored = json.loads(row[0])
        missing = self.REQUIRED_GATE_STATE_KEYS - stored.keys()
        assert not missing, f"AC-P2.10.3: gate_state_json missing keys: {missing}"

    def test_per_symphony_exit_math_mode_recorded(self, mem_db):
        """Per-symphony exits set math_mode='per_symphony' on the exit_triggers row."""
        import database
        database.record_exit_trigger(
            ts_utc="2024-11-01T12:00:00Z",
            ts_et="2024-11-01T12:00:00",
            symphony_id="sym-per",
            account_id="acct-per",
            triggered_reason="hwm_trailing_stop",
            at_return=0.04,
            gate_state_json="{}",
            cycle_id="cycle-per-001",
            math_mode="per_symphony",
        )

        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT math_mode FROM exit_triggers WHERE symphony_id = 'sym-per'"
        )
        row = cursor.fetchone()
        conn.close()
        assert row[0] == "per_symphony"


class TestPortTriggerIdAndMathModeAsTopLevelColumns:
    """
    BLOCK 2 regression guard: math_mode and port_trigger_id must be passed as
    top-level kwargs to record_exit_trigger (mapping to dedicated columns), NOT
    embedded inside the gate_state dict. Embedding them inside gate_state leaves
    the dedicated columns NULL, breaking AC-P2.10.1, AC-P2.10.2, and the partial
    index idx_exit_triggers_port_trigger_id.
    """

    def test_port_trigger_id_lands_in_dedicated_column(self, mem_db):
        """
        port_trigger_id passed as top-level kwarg must appear in the
        port_trigger_id column, not only inside gate_state_json.
        """
        import database
        tid = str(uuid.uuid4())
        database.record_exit_trigger(
            symphony_id="sym-col-check",
            account_id="acct-col",
            triggered_reason="Port-Level",
            at_return=0.05,
            gate_state={"port_total_reduction_usd": 10000.0},
            cycle_id="cycle-col-001",
            math_mode="port_level",
            port_trigger_id=tid,
        )
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT port_trigger_id, math_mode FROM exit_triggers WHERE symphony_id = 'sym-col-check'"
        )
        row = cursor.fetchone()
        conn.close()
        assert row is not None
        assert row[0] == tid, (
            "BLOCK 2: port_trigger_id must land in the dedicated column, not buried in gate_state_json"
        )
        assert row[1] == "port_level", (
            "BLOCK 2: math_mode must land in the dedicated column, not buried in gate_state_json"
        )

    def test_embedding_in_gate_state_only_leaves_column_null(self, mem_db):
        """
        Passing math_mode/port_trigger_id only inside gate_state dict (the bug)
        leaves the dedicated columns NULL. This test confirms the bug behaviour
        to pin that the fix must route them as top-level kwargs.
        """
        import database
        tid = str(uuid.uuid4())
        # Intentionally wrong: values inside gate_state only, not as top-level kwargs
        database.record_exit_trigger(
            symphony_id="sym-bug-repro",
            account_id="acct-bug",
            triggered_reason="Port-Level",
            at_return=0.05,
            gate_state={
                "port_trigger_id": tid,
                "math_mode": "port_level",
                "port_total_reduction_usd": 5000.0,
            },
            cycle_id="cycle-bug-001",
            # math_mode and port_trigger_id NOT passed as top-level kwargs
        )
        conn = database.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT port_trigger_id, math_mode FROM exit_triggers WHERE symphony_id = 'sym-bug-repro'"
        )
        row = cursor.fetchone()
        conn.close()
        assert row is not None
        assert row[0] is None, (
            "Embedding port_trigger_id inside gate_state only must leave the dedicated column NULL "
            "(confirms the call-site bug — the fix must pass it as a top-level kwarg)"
        )
        assert row[1] is None, (
            "Embedding math_mode inside gate_state only must leave the dedicated column NULL"
        )


class TestApiTrigersSurface:
    """AC-P2.10.4: /api/triggers surfaces math_mode, port_trigger_id, gate_state_json."""

    def test_api_triggers_returns_math_mode(self, mem_db):
        """
        /api/triggers endpoint must include math_mode in each row.
        This tests the database.get_exit_triggers() helper, not the HTTP layer.
        """
        import database
        database.record_exit_trigger(
            ts_utc="2024-11-01T13:00:00Z",
            ts_et="2024-11-01T13:00:00",
            symphony_id="sym-api",
            account_id="acct-api",
            triggered_reason="vwap_breakdown",
            at_return=0.03,
            gate_state_json="{}",
            cycle_id="cycle-api-001",
            math_mode="port_level",
            port_trigger_id=str(uuid.uuid4()),
        )

        from database import get_recent_exit_triggers
        rows = get_recent_exit_triggers(limit=10)
        assert len(rows) >= 1
        row = next(r for r in rows if r["symphony_id"] == "sym-api")
        assert "math_mode" in row, "AC-P2.10.4: exit trigger rows must include math_mode"
        assert "port_trigger_id" in row, "AC-P2.10.4: exit trigger rows must include port_trigger_id"
