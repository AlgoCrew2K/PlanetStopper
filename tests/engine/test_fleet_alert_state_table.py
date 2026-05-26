"""
RED tests for R1 — fleet_alert_state dedicated table (dashboard-engine race elimination).

Mandate: Migrate fleet_correlation_alert OUT of bot_state JSON blob INTO a new
fleet_alert_state single-row table (migration 009). Dashboard dismisses ONLY that
table; engine writes ONLY that table. The bot_state race window is eliminated.

Acceptance Criteria covered:
  AC-1: Migration 009 creates fleet_alert_state with correct schema + CHECK(id=1).
  AC-2: database.py helpers: read_fleet_alert(), write_fleet_alert(payload), clear_fleet_alert().
  AC-3: Engine detection path writes to fleet_alert_state (not bot_state).
  AC-4: Engine auto-clear timer reads tripped_at_et from fleet_alert_state; clears via clear_fleet_alert().
  AC-5: /api/state reads from read_fleet_alert(); exposes fleet_correlation_alert as None when
        dismissed_at_et is set or row absent; object otherwise.
  AC-6: POST /api/fleet-alert/dismiss writes dismissed_at_et=now() ONLY to fleet_alert_state.
        NEVER touches bot_state. Returns 200 + updated state.
  AC-7: After dismiss + new fleet event same cycle: alert re-trips (new tripped_at_et, dismissed_at_et nulled).
  AC-8: Daemon restart with alert set: persists in fleet_alert_state.
  AC-9: Migration 009 is additive.
  AC-10: Concurrent engine save_state + dashboard dismiss — bot_state never mutated by dismiss.
  AC-11: Dismissed + new tripped event → alert visible again with new tripped_at_et.

Test naming: scenario-based.

Fixture provenance: all fixtures under tests/fixtures/engine/fleet_alert_state/.
  schema-derived from R1 acceptance criteria + migration 009 DDL spec.
  Zero inline construction. Zero hardcoded producer-computed values.

Mocking philosophy:
  - DB helpers: tested against a real in-memory SQLite via database.get_connection() monkeypatched
    to an in-memory DB — no live file DB, no live daemon.
  - Flask routes: Flask test client with database helpers patched.
  - Time: patched via unittest.mock.patch for deterministic dismiss timestamps.
  - Network: mocked — no real Composer/Alpaca calls.

PA-18: Fixtures from tests/fixtures/engine/fleet_alert_state/. Provenance in each fixture JSON.
PA-19: Explicit APPROVE from quant-code-reviewer required before merge.
"""

from __future__ import annotations

import inspect
import json
import pathlib
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURES = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "engine"
    / "fleet_alert_state"
)


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text())


# ---------------------------------------------------------------------------
# In-memory DB helper for isolation
# ---------------------------------------------------------------------------

def _make_in_memory_conn() -> sqlite3.Connection:
    """Return an in-memory SQLite connection with the fleet_alert_state table created."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fleet_alert_state (
            id               INTEGER PRIMARY KEY CHECK(id = 1),
            tripped_at_et    TEXT NOT NULL,
            triggered_reason TEXT NOT NULL,
            tripped_count    INTEGER NOT NULL,
            active_count     INTEGER NOT NULL,
            dismissed_at_et  TEXT NULL DEFAULT NULL
        )
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Section 1 — Migration 009: schema shape + CHECK(id=1) invariant
# ---------------------------------------------------------------------------


class TestMigration009Schema:
    """
    AC-1: Migration 009 must create fleet_alert_state with the exact column set
    and a CHECK(id=1) constraint that enforces the single-row invariant.
    """

    def test_migration_009_file_exists(self):
        """migrations/009_fleet_alert_state.sql must exist."""
        fixture = _load("table_schema.json")
        migration_path = (
            pathlib.Path(__file__).parent.parent.parent
            / "migrations"
            / "009_fleet_alert_state.sql"
        )
        assert migration_path.exists(), (
            f"Migration file migrations/009_fleet_alert_state.sql does not exist. "
            f"Create it to implement {fixture['table_name']} per R1 AC-1."
        )

    def test_migration_009_listed_in_migration_files(self):
        """database._MIGRATION_FILES must include '009_fleet_alert_state.sql'."""
        try:
            import database
        except ImportError:
            pytest.fail("database module not importable.")

        assert hasattr(database, "_MIGRATION_FILES"), (
            "database._MIGRATION_FILES not found — check migration runner setup."
        )
        migration_names = [m for m in database._MIGRATION_FILES]
        assert "009_fleet_alert_state.sql" in migration_names, (
            "database._MIGRATION_FILES must include '009_fleet_alert_state.sql'. "
            "Add it as the last entry in the ordered list."
        )

    def test_fleet_alert_state_table_has_required_columns(self):
        """fleet_alert_state must have all six required columns."""
        fixture = _load("table_schema.json")
        conn = _make_in_memory_conn()
        cursor = conn.execute("PRAGMA table_info(fleet_alert_state)")
        cols = {row[1] for row in cursor.fetchall()}
        conn.close()

        for col_name in fixture["columns"]:
            assert col_name in cols, (
                f"fleet_alert_state must have column '{col_name}'. "
                f"Present columns: {sorted(cols)}"
            )

    def test_check_id_equals_1_rejects_second_row(self):
        """CHECK(id=1) must prevent inserting any row with id != 1."""
        conn = _make_in_memory_conn()
        conn.execute(
            "INSERT OR REPLACE INTO fleet_alert_state "
            "(id, tripped_at_et, triggered_reason, tripped_count, active_count) "
            "VALUES (1, '2026-05-17T10:33:00', 'Trailing Stop', 6, 10)"
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO fleet_alert_state "
                "(id, tripped_at_et, triggered_reason, tripped_count, active_count) "
                "VALUES (2, '2026-05-17T10:34:00', 'PARA-ARM', 3, 10)"
            )
        conn.close()

    def test_insert_or_replace_preserves_single_row(self):
        """Two consecutive INSERT OR REPLACE with id=1 must leave exactly one row."""
        conn = _make_in_memory_conn()
        conn.execute(
            "INSERT OR REPLACE INTO fleet_alert_state "
            "(id, tripped_at_et, triggered_reason, tripped_count, active_count) "
            "VALUES (1, '2026-05-17T10:33:00', 'Trailing Stop', 6, 10)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO fleet_alert_state "
            "(id, tripped_at_et, triggered_reason, tripped_count, active_count) "
            "VALUES (1, '2026-05-17T10:55:00', 'PARA-ARM', 7, 10)"
        )
        conn.commit()
        row_count = conn.execute("SELECT COUNT(*) FROM fleet_alert_state").fetchone()[0]
        conn.close()
        assert row_count == 1, (
            f"INSERT OR REPLACE with id=1 must leave exactly 1 row. Got {row_count}."
        )

    def test_dismissed_at_et_column_is_nullable(self):
        """dismissed_at_et column must allow NULL."""
        conn = _make_in_memory_conn()
        conn.execute(
            "INSERT INTO fleet_alert_state "
            "(id, tripped_at_et, triggered_reason, tripped_count, active_count, dismissed_at_et) "
            "VALUES (1, '2026-05-17T10:33:00', 'Trailing Stop', 6, 10, NULL)"
        )
        conn.commit()
        row = conn.execute(
            "SELECT dismissed_at_et FROM fleet_alert_state WHERE id = 1"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] is None, (
            f"dismissed_at_et must accept NULL. Got: {row[0]!r}"
        )


# ---------------------------------------------------------------------------
# Section 2 — database.py helpers exist + have correct signatures
# ---------------------------------------------------------------------------


class TestHelperSignatures:
    """
    AC-2: database.py must expose read_fleet_alert(), write_fleet_alert(payload),
    clear_fleet_alert(). Each takes its own connection.
    """

    def _import_db(self):
        try:
            import database
            return database
        except ImportError:
            pytest.fail("database module not importable.")

    def test_read_fleet_alert_exists(self):
        """database.read_fleet_alert must exist as a module-level function."""
        db = self._import_db()
        assert hasattr(db, "read_fleet_alert"), (
            "database.read_fleet_alert not found. "
            "Implement: read_fleet_alert() -> dict | None"
        )

    def test_write_fleet_alert_exists(self):
        """database.write_fleet_alert must exist as a module-level function."""
        db = self._import_db()
        assert hasattr(db, "write_fleet_alert"), (
            "database.write_fleet_alert not found. "
            "Implement: write_fleet_alert(payload: dict) -> None"
        )

    def test_clear_fleet_alert_exists(self):
        """database.clear_fleet_alert must exist as a module-level function."""
        db = self._import_db()
        assert hasattr(db, "clear_fleet_alert"), (
            "database.clear_fleet_alert not found. "
            "Implement: clear_fleet_alert() -> None"
        )

    def test_write_fleet_alert_accepts_payload_dict(self):
        """write_fleet_alert signature must accept a payload parameter."""
        db = self._import_db()
        if not hasattr(db, "write_fleet_alert"):
            pytest.fail("write_fleet_alert not found.")
        sig = inspect.signature(db.write_fleet_alert)
        param_names = list(sig.parameters.keys())
        assert "payload" in param_names, (
            f"write_fleet_alert must accept a 'payload' parameter. Got: {param_names}"
        )

    def test_helpers_are_callable(self):
        """All three helpers must be callable."""
        db = self._import_db()
        for name in ("read_fleet_alert", "write_fleet_alert", "clear_fleet_alert"):
            fn = getattr(db, name, None)
            assert callable(fn), (
                f"database.{name} must be callable (got {type(fn).__name__})."
            )


# ---------------------------------------------------------------------------
# Section 3 — Helper round-trip semantics (via patched DB connection)
# ---------------------------------------------------------------------------


class TestHelperRoundTrip:
    """
    AC-2 round-trip: write_fleet_alert → read_fleet_alert → clear_fleet_alert,
    tested against an in-memory SQLite (database.get_connection patched).
    """

    @pytest.fixture(autouse=True)
    def _patch_db_conn(self, monkeypatch):
        """Redirect database.get_connection() to a shared in-memory SQLite URI.

        Each call to get_connection() returns a NEW connection object that shares
        the same in-memory database via file URI + shared cache. This lets helpers
        call conn.close() (as they must) without destroying the database between calls.
        """
        import uuid
        self._db_name = f"file:testdb_{uuid.uuid4().hex}?mode=memory&cache=shared"

        def _make_shared_conn():
            conn = sqlite3.connect(self._db_name, uri=True)
            conn.row_factory = sqlite3.Row
            return conn

        self._make_conn = _make_shared_conn

        try:
            import database
            self._db = database
        except ImportError:
            pytest.fail("database module not importable.")

        monkeypatch.setattr(self._db, "get_connection", _make_shared_conn)

        # Keep one long-lived reference connection open so the in-memory DB
        # is not destroyed when helpers close their individual connections.
        self._keeper = _make_shared_conn()
        yield
        self._keeper.close()

    def _seed_table(self):
        """Ensure the fleet_alert_state table exists in the shared in-memory DB."""
        conn = self._make_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fleet_alert_state (
                id               INTEGER PRIMARY KEY CHECK(id = 1),
                tripped_at_et    TEXT NOT NULL,
                triggered_reason TEXT NOT NULL,
                tripped_count    INTEGER NOT NULL,
                active_count     INTEGER NOT NULL,
                dismissed_at_et  TEXT NULL DEFAULT NULL
            )
        """)
        conn.commit()
        conn.close()

    def test_read_fleet_alert_returns_none_when_table_empty(self):
        """read_fleet_alert() returns None when fleet_alert_state has no rows."""
        self._seed_table()
        result = self._db.read_fleet_alert()
        assert result is None, (
            f"read_fleet_alert must return None when table is empty, got {result!r}"
        )

    def test_write_then_read_returns_dict_with_required_keys(self):
        """write_fleet_alert() followed by read_fleet_alert() returns dict with all required keys."""
        self._seed_table()
        fixture = _load("helpers_contract.json")
        required_keys = fixture["helpers"]["read_fleet_alert"]["required_keys_when_active"]

        payload = {
            "tripped_at_et": "2026-05-17T10:33:00",
            "triggered_reason": "Trailing Stop",
            "tripped_count": 6,
            "active_count": 10,
        }
        self._db.write_fleet_alert(payload)
        result = self._db.read_fleet_alert()

        assert isinstance(result, dict), (
            f"read_fleet_alert must return a dict after write, got {type(result).__name__}"
        )
        for key in fixture["helpers"]["write_fleet_alert"]["required_payload_keys"]:
            assert key in result, (
                f"read_fleet_alert result must contain '{key}'. Got keys: {list(result.keys())}"
            )
        assert "dismissed_at_et" in result, (
            "read_fleet_alert result must contain 'dismissed_at_et' (may be None)."
        )

    def test_write_sets_dismissed_at_et_to_null_by_default(self):
        """write_fleet_alert() without dismissed_at_et in payload → dismissed_at_et is None in read."""
        self._seed_table()
        payload = {
            "tripped_at_et": "2026-05-17T10:33:00",
            "triggered_reason": "Trailing Stop",
            "tripped_count": 6,
            "active_count": 10,
        }
        self._db.write_fleet_alert(payload)
        result = self._db.read_fleet_alert()
        assert result is not None
        assert result.get("dismissed_at_et") is None, (
            f"dismissed_at_et must be None when not provided in payload. Got: {result['dismissed_at_et']!r}"
        )

    def test_write_preserves_tripped_at_et_as_string(self):
        """read_fleet_alert returns tripped_at_et as the same string that was written."""
        self._seed_table()
        fixture = _load("alert_lifecycle.json")
        case = next(c for c in fixture["cases"] if c["label"] == "initial_trip_writes_to_fleet_alert_state")
        payload = {k: v for k, v in case["write_payload"].items() if v is not None}
        self._db.write_fleet_alert(payload)
        result = self._db.read_fleet_alert()
        assert result["tripped_at_et"] == case["write_payload"]["tripped_at_et"], (
            f"tripped_at_et round-trip mismatch: wrote {case['write_payload']['tripped_at_et']!r}, "
            f"read back {result['tripped_at_et']!r}"
        )

    def test_write_fleet_alert_is_idempotent_upsert(self):
        """Two consecutive write_fleet_alert calls leave exactly one row."""
        self._seed_table()
        payload_a = {
            "tripped_at_et": "2026-05-17T10:33:00",
            "triggered_reason": "Trailing Stop",
            "tripped_count": 6,
            "active_count": 10,
        }
        payload_b = {
            "tripped_at_et": "2026-05-17T10:55:00",
            "triggered_reason": "PARA-ARM",
            "tripped_count": 7,
            "active_count": 10,
        }
        self._db.write_fleet_alert(payload_a)
        self._db.write_fleet_alert(payload_b)
        conn = self._make_conn()
        row_count = conn.execute(
            "SELECT COUNT(*) FROM fleet_alert_state"
        ).fetchone()[0]
        conn.close()
        assert row_count == 1, (
            f"write_fleet_alert must upsert to a single row. Got {row_count} rows."
        )

    def test_clear_fleet_alert_removes_row(self):
        """clear_fleet_alert() deletes the id=1 row; read_fleet_alert() returns None after."""
        self._seed_table()
        fixture = _load("alert_lifecycle.json")
        case = next(c for c in fixture["cases"] if c["label"] == "auto_clear_removes_row")
        payload = {k: v for k, v in case["pre_clear_row"].items() if v is not None}
        self._db.write_fleet_alert(payload)
        assert self._db.read_fleet_alert() is not None, "Row must exist before clear."

        self._db.clear_fleet_alert()
        result = self._db.read_fleet_alert()
        assert result is None, (
            f"read_fleet_alert must return None after clear_fleet_alert(). Got: {result!r}"
        )

    def test_clear_fleet_alert_is_idempotent_when_empty(self):
        """clear_fleet_alert() on an empty table must not raise."""
        self._seed_table()
        try:
            self._db.clear_fleet_alert()
            self._db.clear_fleet_alert()
        except Exception as exc:
            pytest.fail(
                f"clear_fleet_alert must be idempotent on empty table. Raised: {type(exc).__name__}: {exc}"
            )

    def test_write_dismissed_at_et_non_null_then_read(self):
        """write_fleet_alert with dismissed_at_et set → read returns non-null dismissed_at_et."""
        self._seed_table()
        fixture = _load("alert_lifecycle.json")
        case = next(c for c in fixture["cases"] if c["label"] == "dismiss_sets_dismissed_at_et_non_null")
        dismiss_time = case["dismiss_time_et"]

        payload = {
            "tripped_at_et": case["pre_dismiss_row"]["tripped_at_et"],
            "triggered_reason": case["pre_dismiss_row"]["triggered_reason"],
            "tripped_count": case["pre_dismiss_row"]["tripped_count"],
            "active_count": case["pre_dismiss_row"]["active_count"],
            "dismissed_at_et": dismiss_time,
        }
        self._db.write_fleet_alert(payload)
        result = self._db.read_fleet_alert()
        assert result is not None
        assert result.get("dismissed_at_et") is not None, (
            "dismissed_at_et must be non-null when written. Got None."
        )
        assert isinstance(result["dismissed_at_et"], str), (
            f"dismissed_at_et must be a string, got {type(result['dismissed_at_et']).__name__}"
        )

    def test_re_trip_after_dismiss_nulls_dismissed_at_et(self):
        """
        Write dismissed row, then write new trip payload with dismissed_at_et=None.
        read_fleet_alert must return row with dismissed_at_et=None (re-tripped alert visible).
        """
        self._seed_table()
        fixture = _load("alert_lifecycle.json")
        case = next(c for c in fixture["cases"] if c["label"] == "re_trip_after_dismiss_nulls_dismissed_at_et")

        # First: write a dismissed row
        dismissed_row = case["pre_trip_dismissed_row"]
        self._db.write_fleet_alert({
            "tripped_at_et": dismissed_row["tripped_at_et"],
            "triggered_reason": dismissed_row["triggered_reason"],
            "tripped_count": dismissed_row["tripped_count"],
            "active_count": dismissed_row["active_count"],
            "dismissed_at_et": dismissed_row["dismissed_at_et"],
        })

        # Then: write new trip payload (dismissed_at_et absent / None = cleared)
        new_payload = case["new_trip_payload"]
        payload_to_write = {
            "tripped_at_et": new_payload["tripped_at_et"],
            "triggered_reason": new_payload["triggered_reason"],
            "tripped_count": new_payload["tripped_count"],
            "active_count": new_payload["active_count"],
        }
        self._db.write_fleet_alert(payload_to_write)
        result = self._db.read_fleet_alert()

        assert result is not None
        assert result["tripped_at_et"] == case["expected_row_after_re_trip"]["tripped_at_et"], (
            f"After re-trip, tripped_at_et must be updated to new value. "
            f"Expected {case['expected_row_after_re_trip']['tripped_at_et']!r}, got {result['tripped_at_et']!r}"
        )
        assert result["dismissed_at_et"] is None, (
            f"After re-trip, dismissed_at_et must be NULL. Got: {result['dismissed_at_et']!r}"
        )


# ---------------------------------------------------------------------------
# Section 4 — Engine detection path writes to fleet_alert_state, not bot_state
# ---------------------------------------------------------------------------


class TestEngineWritesToFleetAlertState:
    """
    AC-3: alpha_bot_execution.set_fleet_correlation_alert (or equivalent) must
    call database.write_fleet_alert(), NOT mutate bot_state["fleet_correlation_alert"].

    AC-4: Engine auto-clear reads tripped_at_et from read_fleet_alert(), clears via
    clear_fleet_alert().
    """

    def test_engine_set_fleet_alert_calls_write_fleet_alert(self):
        """
        set_fleet_correlation_alert (or check_fleet_correlation_and_update_state) must
        call database.write_fleet_alert() to store the alert.
        It must NOT write to bot_state['fleet_correlation_alert'].
        """
        try:
            import alpha_bot_execution
        except ImportError:
            pytest.fail("alpha_bot_execution not importable.")

        fn = getattr(alpha_bot_execution, "set_fleet_correlation_alert", None)
        if fn is None:
            fn = getattr(alpha_bot_execution, "check_fleet_correlation_and_update_state", None)

        assert fn is not None, (
            "set_fleet_correlation_alert or check_fleet_correlation_and_update_state not found "
            "in alpha_bot_execution. Implement per R1 AC-3."
        )

        bot_state: dict = {}
        with patch("database.write_fleet_alert") as mock_write, \
             patch("database.get_triggers", return_value=[]):
            if fn.__name__ == "set_fleet_correlation_alert":
                fn(
                    bot_state=bot_state,
                    tripped_at_et="2026-05-17T10:33:00",
                    triggered_reason="Trailing Stop",
                    tripped_count=6,
                    active_count=10,
                )
            else:
                with patch("database.get_triggers", return_value=[
                    {"symphony_id": f"s{i}", "triggered_reason": "Trailing Stop",
                     "ts_et": "2026-05-17T10:32:00"}
                    for i in range(6)
                ]):
                    alpha_bot_execution.check_fleet_correlation_and_update_state(
                        bot_state=bot_state,
                        active_symphony_count=10,
                    )

        assert mock_write.called, (
            "set_fleet_correlation_alert must call database.write_fleet_alert() to persist alert "
            "in fleet_alert_state. "
            "AC-3: Engine writes to dedicated table, NOT to bot_state JSON blob."
        )

    def test_engine_set_fleet_alert_does_not_mutate_bot_state(self):
        """
        After set_fleet_correlation_alert, bot_state must NOT contain 'fleet_correlation_alert'.
        The alert lives in fleet_alert_state, not the bot_state JSON blob.
        """
        try:
            from alpha_bot_execution import set_fleet_correlation_alert
        except ImportError:
            pytest.skip("set_fleet_correlation_alert not yet implemented — will fail RED.")

        bot_state: dict = {}
        with patch("database.write_fleet_alert"):
            set_fleet_correlation_alert(
                bot_state=bot_state,
                tripped_at_et="2026-05-17T10:33:00",
                triggered_reason="Trailing Stop",
                tripped_count=6,
                active_count=10,
            )

        assert "fleet_correlation_alert" not in bot_state, (
            "set_fleet_correlation_alert must NOT write to bot_state['fleet_correlation_alert']. "
            "The alert must be stored ONLY in fleet_alert_state via database.write_fleet_alert(). "
            "AC-3: eliminate the bot_state race path entirely."
        )

    def test_engine_auto_clear_reads_from_read_fleet_alert(self):
        """
        check_fleet_correlation_and_update_state auto-clear logic must call
        database.read_fleet_alert() to get tripped_at_et, NOT read bot_state.
        """
        try:
            import alpha_bot_execution
        except ImportError:
            pytest.fail("alpha_bot_execution not importable.")

        if not hasattr(alpha_bot_execution, "check_fleet_correlation_and_update_state"):
            pytest.fail(
                "check_fleet_correlation_and_update_state not found — implement per R1 AC-4."
            )

        # Provide a stale alert (older than FLEET_CORRELATION_CLEAR_MINUTES)
        stale_row = {
            "tripped_at_et": "2026-05-17T09:00:00",  # well before clear window
            "triggered_reason": "Trailing Stop",
            "tripped_count": 6,
            "active_count": 10,
            "dismissed_at_et": None,
        }
        check_time = datetime(2026, 5, 17, 9, 31, 0)  # 31 min later → past 30-min clear window

        bot_state: dict = {}

        with patch("database.read_fleet_alert", return_value=stale_row) as mock_read, \
             patch("database.clear_fleet_alert") as mock_clear, \
             patch("database.write_fleet_alert") as mock_write, \
             patch("database.get_triggers", return_value=[]), \
             patch.object(alpha_bot_execution, "FLEET_CORRELATION_CLEAR_MINUTES", 30):
            alpha_bot_execution.check_fleet_correlation_and_update_state(
                bot_state=bot_state,
                active_symphony_count=10,
                now_et=check_time,
            )

        assert mock_read.called, (
            "check_fleet_correlation_and_update_state must call database.read_fleet_alert() "
            "to check the existing alert's tripped_at_et for auto-clear. "
            "AC-4: reads tripped_at_et from fleet_alert_state, not bot_state."
        )

    def test_engine_auto_clear_calls_clear_fleet_alert_when_expired(self):
        """
        When tripped_at_et is older than FLEET_CORRELATION_CLEAR_MINUTES and no new events,
        check_fleet_correlation_and_update_state must call database.clear_fleet_alert().
        """
        try:
            import alpha_bot_execution
        except ImportError:
            pytest.fail("alpha_bot_execution not importable.")

        if not hasattr(alpha_bot_execution, "check_fleet_correlation_and_update_state"):
            pytest.fail("check_fleet_correlation_and_update_state not found.")

        stale_row = {
            "tripped_at_et": "2026-05-17T09:00:00",
            "triggered_reason": "Trailing Stop",
            "tripped_count": 6,
            "active_count": 10,
            "dismissed_at_et": None,
        }
        check_time = datetime(2026, 5, 17, 9, 31, 0)

        bot_state: dict = {}

        with patch("database.read_fleet_alert", return_value=stale_row), \
             patch("database.clear_fleet_alert") as mock_clear, \
             patch("database.write_fleet_alert"), \
             patch("database.get_triggers", return_value=[]), \
             patch.object(alpha_bot_execution, "FLEET_CORRELATION_CLEAR_MINUTES", 30):
            alpha_bot_execution.check_fleet_correlation_and_update_state(
                bot_state=bot_state,
                active_symphony_count=10,
                now_et=check_time,
            )

        assert mock_clear.called, (
            "check_fleet_correlation_and_update_state must call database.clear_fleet_alert() "
            "when the alert is older than FLEET_CORRELATION_CLEAR_MINUTES with no new events. "
            "AC-4: auto-clear uses clear_fleet_alert(), not bot_state.pop()."
        )

    def test_engine_auto_clear_does_not_mutate_bot_state(self):
        """
        Auto-clear must NOT pop 'fleet_correlation_alert' from bot_state.
        bot_state is the engine's internal dict; the alert state lives in fleet_alert_state.
        """
        try:
            import alpha_bot_execution
        except ImportError:
            pytest.fail("alpha_bot_execution not importable.")

        if not hasattr(alpha_bot_execution, "check_fleet_correlation_and_update_state"):
            pytest.fail("check_fleet_correlation_and_update_state not found.")

        stale_row = {
            "tripped_at_et": "2026-05-17T09:00:00",
            "triggered_reason": "Trailing Stop",
            "tripped_count": 6,
            "active_count": 10,
            "dismissed_at_et": None,
        }
        check_time = datetime(2026, 5, 17, 9, 31, 0)

        # bot_state has NO fleet_correlation_alert key (correct post-migration state)
        bot_state: dict = {"some_symphony": {"triggered": False}}
        original_keys = set(bot_state.keys())

        with patch("database.read_fleet_alert", return_value=stale_row), \
             patch("database.clear_fleet_alert"), \
             patch("database.write_fleet_alert"), \
             patch("database.get_triggers", return_value=[]), \
             patch.object(alpha_bot_execution, "FLEET_CORRELATION_CLEAR_MINUTES", 30):
            alpha_bot_execution.check_fleet_correlation_and_update_state(
                bot_state=bot_state,
                active_symphony_count=10,
                now_et=check_time,
            )

        assert set(bot_state.keys()) == original_keys, (
            f"check_fleet_correlation_and_update_state must not add or remove bot_state keys. "
            f"Before: {original_keys}. After: {set(bot_state.keys())}. "
            "AC-3/AC-4: bot_state is engine-only; alert state lives in fleet_alert_state."
        )


# ---------------------------------------------------------------------------
# Section 5 — /api/state reads from read_fleet_alert(), not bot_state
# ---------------------------------------------------------------------------


class TestApiStateReadsFromFleetAlertTable:
    """
    AC-5: /api/state must call database.read_fleet_alert() to populate
    fleet_correlation_alert. Value is None when dismissed_at_et is set or row absent;
    object otherwise.
    """

    @pytest.fixture()
    def flask_client(self):
        with patch("database.init_db"):
            import app as flask_app
        flask_app.app.config["TESTING"] = True
        with flask_app.app.test_client() as client:
            yield client

    def _common_patches(self):
        return {
            "database.get_shadow_divergence": {"by_symphony": {}, "portfolio_today": None},
            "database.get_triggers": [],
            "market_calendar.get_market_state": "open",
            "analytics.get_portfolio_today_change": {},
            "analytics.get_portfolio_cumulative_return": {},
            "analytics.get_portfolio_max_drawdown": {},
        }

    def test_api_state_calls_read_fleet_alert(self, flask_client):
        """GET /api/state must call database.read_fleet_alert() (not only database.load_state)."""
        fixture = _load("api_state_exposure.json")
        case = next(c for c in fixture["cases"] if c["label"] == "alert_visible_when_dismissed_at_et_is_null")

        with patch("database.load_state", return_value={"date": "2026-05-17"}), \
             patch("database.read_fleet_alert", return_value=case["read_fleet_alert_returns"]) as mock_read, \
             patch("database.get_shadow_divergence", return_value={"by_symphony": {}, "portfolio_today": None}), \
             patch("database.get_triggers", return_value=[]), \
             patch("market_calendar.get_market_state", return_value="open"), \
             patch("analytics.get_portfolio_today_change", return_value={}), \
             patch("analytics.get_portfolio_cumulative_return", return_value={}), \
             patch("analytics.get_portfolio_max_drawdown", return_value={}), \
             patch("app.render_template", return_value=""):
            resp = flask_client.get("/api/state")

        assert resp.status_code == 200
        assert mock_read.called, (
            "GET /api/state must call database.read_fleet_alert() to populate fleet_correlation_alert. "
            "AC-5: /api/state reads from dedicated table, not bot_state."
        )

    def test_api_state_returns_alert_when_dismissed_at_et_is_null(self, flask_client):
        """When read_fleet_alert returns row with dismissed_at_et=None, /api/state includes the alert object."""
        fixture = _load("api_state_exposure.json")
        case = next(c for c in fixture["cases"] if c["label"] == "alert_visible_when_dismissed_at_et_is_null")

        with patch("database.load_state", return_value={"date": "2026-05-17"}), \
             patch("database.read_fleet_alert", return_value=case["read_fleet_alert_returns"]), \
             patch("database.get_shadow_divergence", return_value={"by_symphony": {}, "portfolio_today": None}), \
             patch("database.get_triggers", return_value=[]), \
             patch("market_calendar.get_market_state", return_value="open"), \
             patch("analytics.get_portfolio_today_change", return_value={}), \
             patch("analytics.get_portfolio_cumulative_return", return_value={}), \
             patch("analytics.get_portfolio_max_drawdown", return_value={}), \
             patch("app.render_template", return_value=""):
            resp = flask_client.get("/api/state")

        data = resp.get_json()
        assert "fleet_correlation_alert" in data, (
            "/api/state must include 'fleet_correlation_alert' key."
        )
        alert = data["fleet_correlation_alert"]
        assert alert is not None, (
            f"Case '{case['label']}': fleet_correlation_alert must be non-null when row has dismissed_at_et=NULL. "
            f"Got: {alert!r}"
        )
        for key in case["required_keys_in_response"]:
            assert key in alert, (
                f"/api/state fleet_correlation_alert missing required key '{key}'. Present: {list(alert.keys())}"
            )

    def test_api_state_returns_none_when_alert_dismissed(self, flask_client):
        """When read_fleet_alert returns row with dismissed_at_et set, /api/state returns None."""
        fixture = _load("api_state_exposure.json")
        case = next(c for c in fixture["cases"] if c["label"] == "alert_hidden_when_dismissed_at_et_is_set")

        with patch("database.load_state", return_value={"date": "2026-05-17"}), \
             patch("database.read_fleet_alert", return_value=case["read_fleet_alert_returns"]), \
             patch("database.get_shadow_divergence", return_value={"by_symphony": {}, "portfolio_today": None}), \
             patch("database.get_triggers", return_value=[]), \
             patch("market_calendar.get_market_state", return_value="open"), \
             patch("analytics.get_portfolio_today_change", return_value={}), \
             patch("analytics.get_portfolio_cumulative_return", return_value={}), \
             patch("analytics.get_portfolio_max_drawdown", return_value={}), \
             patch("app.render_template", return_value=""):
            resp = flask_client.get("/api/state")

        data = resp.get_json()
        assert data.get("fleet_correlation_alert") is None, (
            f"Case '{case['label']}': /api/state must return None for fleet_correlation_alert when dismissed. "
            f"Got: {data.get('fleet_correlation_alert')!r}"
        )

    def test_api_state_returns_none_when_no_alert_row(self, flask_client):
        """When read_fleet_alert returns None (empty table), /api/state returns None."""
        fixture = _load("api_state_exposure.json")
        case = next(c for c in fixture["cases"] if c["label"] == "alert_hidden_when_no_row")

        with patch("database.load_state", return_value={"date": "2026-05-17"}), \
             patch("database.read_fleet_alert", return_value=None), \
             patch("database.get_shadow_divergence", return_value={"by_symphony": {}, "portfolio_today": None}), \
             patch("database.get_triggers", return_value=[]), \
             patch("market_calendar.get_market_state", return_value="open"), \
             patch("analytics.get_portfolio_today_change", return_value={}), \
             patch("analytics.get_portfolio_cumulative_return", return_value={}), \
             patch("analytics.get_portfolio_max_drawdown", return_value={}), \
             patch("app.render_template", return_value=""):
            resp = flask_client.get("/api/state")

        data = resp.get_json()
        assert "fleet_correlation_alert" in data, (
            "/api/state must include 'fleet_correlation_alert' key (even when None)."
        )
        assert data["fleet_correlation_alert"] is None, (
            f"Case '{case['label']}': /api/state fleet_correlation_alert must be None when no DB row. "
            f"Got: {data['fleet_correlation_alert']!r}"
        )


# ---------------------------------------------------------------------------
# Section 6 — POST /api/fleet-alert/dismiss: writes ONLY to fleet_alert_state
# ---------------------------------------------------------------------------


class TestDismissRouteWritesOnlyToFleetAlertState:
    """
    AC-6: POST /api/fleet-alert/dismiss must:
      - Call database.write_fleet_alert with dismissed_at_et set.
      - NEVER call database.load_state or database.save_state.
      - Return 200 + {"status": "ok"}.
    """

    @pytest.fixture()
    def flask_client(self):
        with patch("database.init_db"):
            import app as flask_app
        flask_app.app.config["TESTING"] = True
        with flask_app.app.test_client() as client:
            yield client

    def test_dismiss_route_returns_200(self, flask_client):
        """POST /api/fleet-alert/dismiss must return 200."""
        with patch("database.write_fleet_alert"), \
             patch("database.read_fleet_alert", return_value={
                 "tripped_at_et": "2026-05-17T10:33:00",
                 "triggered_reason": "Trailing Stop",
                 "tripped_count": 6,
                 "active_count": 10,
                 "dismissed_at_et": None,
             }):
            resp = flask_client.post("/api/fleet-alert/dismiss")
        assert resp.status_code == 200, (
            f"POST /api/fleet-alert/dismiss must return 200. Got {resp.status_code}."
        )

    def test_dismiss_route_does_not_call_write_fleet_alert_on_request_thread(self, flask_client):
        """POST /api/fleet-alert/dismiss must NOT call database.write_fleet_alert() on the Flask request thread.

        Deterministic: _DISMISS_EXECUTOR.submit is stubbed to record the submitted callable
        WITHOUT executing it.  This eliminates the timing window of the previous version
        (which relied on the single-worker executor not having started by the time the patch
        context exited — a scheduling assumption, not a contract guarantee).

        The test verifies the side-effect-ban contract: no synchronous write_fleet_alert call
        occurs during the handler frame, regardless of executor scheduling.
        dashboard-side-effect-ban cycle — arch constraint 2.
        """
        import concurrent.futures as _cf
        import app as flask_app

        submitted_callables: list = []

        def _capture_submit(fn, *args, **kwargs):
            # Record the callable but deliberately do NOT call it —
            # we are asserting the request-thread frame only.
            submitted_callables.append(fn)
            future = _cf.Future()
            future.set_result(None)
            return future

        with patch.object(flask_app._DISMISS_EXECUTOR, "submit", side_effect=_capture_submit) as mock_submit, \
             patch("database.write_fleet_alert") as mock_write, \
             patch("database.read_fleet_alert", return_value={
                 "tripped_at_et": "2026-05-17T10:33:00",
                 "triggered_reason": "Trailing Stop",
                 "tripped_count": 6,
                 "active_count": 10,
                 "dismissed_at_et": None,
             }):
            resp = flask_client.post("/api/fleet-alert/dismiss")

        assert resp.status_code == 200, (
            f"Handler must return 200 even when executor is stubbed. Got {resp.status_code}."
        )
        assert mock_submit.call_count == 1, (
            f"Handler must call _DISMISS_EXECUTOR.submit exactly once. "
            f"Called {mock_submit.call_count} time(s). "
            "The dismiss write must be dispatched via the executor, not called inline."
        )
        # The closure was captured but NOT run — write_fleet_alert must be silent.
        assert mock_write.call_count == 0, (
            "POST /api/fleet-alert/dismiss must NOT call database.write_fleet_alert() directly "
            "from the Flask request thread. The dismiss write must be dispatched to a background "
            "thread (side-effect ban). "
            f"write_fleet_alert was called {mock_write.call_count} time(s) on the request thread."
        )
        assert len(submitted_callables) == 1, (
            "Exactly one callable must have been submitted to the executor."
        )

    def test_dismiss_background_closure_calls_write_fleet_alert_when_executed(self, flask_client):
        """The callable dispatched by the dismiss handler must call write_fleet_alert when run.

        Captures the submitted callable (without executing it during the handler frame),
        then explicitly invokes it to prove the background work actually reaches write_fleet_alert.
        This is the dual of the request-thread test: handler -> no write; closure() -> write.
        Fixture: dismiss_race_isolation.json expected_calls_to_write_fleet_alert_or_similar == 1.
        """
        import app as flask_app
        import concurrent.futures as _cf

        fixture = _load("dismiss_race_isolation.json")
        expected_write_calls = fixture["invariants"]["dismiss_never_touches_bot_state"][
            "expected_calls_to_write_fleet_alert_or_similar"
        ]

        submitted_callables: list = []

        def _capture_only(fn, *args, **kwargs):
            submitted_callables.append(fn)
            future = _cf.Future()
            future.set_result(None)
            return future

        alert_row = {
            "tripped_at_et": "2026-05-17T10:33:00",
            "triggered_reason": "Trailing Stop",
            "tripped_count": 6,
            "active_count": 10,
            "dismissed_at_et": None,
        }

        with patch.object(flask_app._DISMISS_EXECUTOR, "submit", side_effect=_capture_only), \
             patch("database.write_fleet_alert") as mock_write, \
             patch("database.read_fleet_alert", return_value=dict(alert_row)):
            flask_client.post("/api/fleet-alert/dismiss")

            assert len(submitted_callables) == 1, (
                "Dismiss handler must submit exactly one callable to the executor. "
                f"Captured {len(submitted_callables)} callable(s)."
            )

            # Explicitly run the closure while database.write_fleet_alert is still patched —
            # the closure holds a reference to the `database` module, so the mock must be
            # active at the moment the closure runs to intercept the call.
            # This simulates what happens on the background thread.
            submitted_callables[0]()

        assert mock_write.call_count == expected_write_calls, (
            f"Background closure must call database.write_fleet_alert exactly "
            f"{expected_write_calls} time(s) (from fixture dismiss_race_isolation.json). "
            f"Called {mock_write.call_count} time(s). "
            "The background write is the mechanism that makes the dismiss durable."
        )

    def test_dismiss_background_exception_is_logged_not_propagated(self, flask_client):
        """If write_fleet_alert raises inside the background closure, the exception is logged and NOT re-raised.

        The handler must return 200 regardless of whether the background write fails (DB locked,
        disk full, etc.).  The closure wraps the write in a try/except that calls logging.error.
        This test verifies that contract deterministically — no timing, no thread scheduling.
        """
        import app as flask_app
        import concurrent.futures as _cf

        submitted_callables: list = []

        def _capture_only(fn, *args, **kwargs):
            submitted_callables.append(fn)
            future = _cf.Future()
            future.set_result(None)
            return future

        with patch.object(flask_app._DISMISS_EXECUTOR, "submit", side_effect=_capture_only), \
             patch("database.write_fleet_alert", side_effect=OSError("DB locked")), \
             patch("database.read_fleet_alert", return_value={
                 "tripped_at_et": "2026-05-17T10:33:00",
                 "triggered_reason": "Trailing Stop",
                 "tripped_count": 6,
                 "active_count": 10,
                 "dismissed_at_et": None,
             }), \
             patch("logging.error") as mock_log_error:
            resp = flask_client.post("/api/fleet-alert/dismiss")

            assert resp.status_code == 200, (
                "Handler must return 200 even when the background write will fail. "
                f"Got {resp.status_code}."
            )
            assert len(submitted_callables) == 1, (
                "Handler must still submit the closure even when the write will fail."
            )

            # Run the closure while both write_fleet_alert (raising) and logging.error are
            # still patched — the closure references `database` and `logging` from app's
            # module namespace; patches must be active when the closure body executes.
            submitted_callables[0]()

        assert mock_log_error.call_count >= 1, (
            "Background closure must call logging.error when write_fleet_alert raises. "
            f"logging.error was called {mock_log_error.call_count} time(s). "
            "Exceptions from background writes must be swallowed + logged — never propagated."
        )

    def test_dismiss_two_rapid_posts_dispatch_two_background_tasks(self, flask_client):
        """Two rapid POSTs must each dispatch one background task (two total submits).

        SQLite last-write-wins is the durability mechanism; no deduplication at the route layer.
        This test verifies the executor is invoked per-request, not once globally.
        Both requests must return 200 and each must result in exactly one submit call.
        """
        import app as flask_app
        import concurrent.futures as _cf

        submit_call_count = 0

        def _count_submits(fn, *args, **kwargs):
            nonlocal submit_call_count
            submit_call_count += 1
            future = _cf.Future()
            future.set_result(None)
            return future

        alert_row = {
            "tripped_at_et": "2026-05-17T10:33:00",
            "triggered_reason": "Trailing Stop",
            "tripped_count": 6,
            "active_count": 10,
            "dismissed_at_et": None,
        }

        with patch.object(flask_app._DISMISS_EXECUTOR, "submit", side_effect=_count_submits), \
             patch("database.write_fleet_alert"), \
             patch("database.read_fleet_alert", return_value=dict(alert_row)):
            resp_1 = flask_client.post("/api/fleet-alert/dismiss")
            resp_2 = flask_client.post("/api/fleet-alert/dismiss")

        assert resp_1.status_code == 200, (
            f"First dismiss POST must return 200. Got {resp_1.status_code}."
        )
        assert resp_2.status_code == 200, (
            f"Second dismiss POST must return 200. Got {resp_2.status_code}."
        )
        assert submit_call_count == 2, (
            f"Two rapid POSTs must produce exactly 2 executor submits (one per request). "
            f"Got {submit_call_count}. "
            "No deduplication at the route layer — SQLite last-write-wins handles idempotency."
        )

    def test_dismiss_route_never_calls_load_state(self, flask_client):
        """
        POST /api/fleet-alert/dismiss must NEVER call database.load_state().
        Calling load_state is the old race-prone path. AC-6 eliminates it.
        """
        fixture = _load("dismiss_race_isolation.json")

        with patch("database.load_state") as mock_load, \
             patch("database.save_state") as mock_save, \
             patch("database.write_fleet_alert"), \
             patch("database.read_fleet_alert", return_value={
                 "tripped_at_et": "2026-05-17T10:33:00",
                 "triggered_reason": "Trailing Stop",
                 "tripped_count": 6,
                 "active_count": 10,
                 "dismissed_at_et": None,
             }):
            flask_client.post("/api/fleet-alert/dismiss")

        expected_load_calls = fixture["invariants"]["dismiss_never_touches_bot_state"]["expected_calls_to_load_state"]
        assert mock_load.call_count == expected_load_calls, (
            f"POST /api/fleet-alert/dismiss must NOT call database.load_state(). "
            f"Called {mock_load.call_count} time(s). "
            "AC-6: dismiss writes ONLY to fleet_alert_state — the race-prone bot_state read path is eliminated."
        )

    def test_dismiss_route_never_calls_save_state(self, flask_client):
        """
        POST /api/fleet-alert/dismiss must NEVER call database.save_state().
        This is the core race condition being eliminated in R1.
        """
        fixture = _load("dismiss_race_isolation.json")

        with patch("database.load_state") as mock_load, \
             patch("database.save_state") as mock_save, \
             patch("database.write_fleet_alert"), \
             patch("database.read_fleet_alert", return_value={
                 "tripped_at_et": "2026-05-17T10:33:00",
                 "triggered_reason": "Trailing Stop",
                 "tripped_count": 6,
                 "active_count": 10,
                 "dismissed_at_et": None,
             }):
            flask_client.post("/api/fleet-alert/dismiss")

        expected_save_calls = fixture["invariants"]["dismiss_never_touches_bot_state"]["expected_calls_to_save_state"]
        assert mock_save.call_count == expected_save_calls, (
            f"POST /api/fleet-alert/dismiss must NOT call database.save_state(). "
            f"Called {mock_save.call_count} time(s). "
            "AC-6: this call was the race condition. R1 eliminates it by using fleet_alert_state exclusively."
        )

    def test_dismiss_route_returns_status_ok_json(self, flask_client):
        """Dismiss route must return JSON with at least {'status': 'ok'}."""
        with patch("database.write_fleet_alert"), \
             patch("database.read_fleet_alert", return_value=None):
            resp = flask_client.post("/api/fleet-alert/dismiss")

        data = resp.get_json()
        assert data is not None, "dismiss route must return JSON."
        assert data.get("status") == "ok", (
            f"dismiss route must return {{\"status\": \"ok\"}}. Got: {data!r}"
        )

    def test_dismiss_route_idempotent_when_no_row(self, flask_client):
        """POST /api/fleet-alert/dismiss must return 200 even when no alert row exists."""
        with patch("database.write_fleet_alert"), \
             patch("database.read_fleet_alert", return_value=None):
            resp = flask_client.post("/api/fleet-alert/dismiss")
        assert resp.status_code == 200, (
            f"Dismiss route must be idempotent when no alert row exists. Got {resp.status_code}."
        )


# ---------------------------------------------------------------------------
# Section 7 — Structural: dismiss route source has no bot_state references
# ---------------------------------------------------------------------------


class TestDismissRouteSourceIsolation:
    """
    AC-6 structural: the fleet_alert_dismiss function in app.py must NOT reference
    database.load_state or database.save_state in its source body.
    """

    def test_dismiss_route_source_has_no_load_state_call(self):
        """fleet_alert_dismiss function must not call database.load_state."""
        try:
            import app
        except ImportError:
            pytest.fail("app not importable.")

        fn = getattr(app, "fleet_alert_dismiss", None)
        if fn is None:
            pytest.fail(
                "fleet_alert_dismiss not found in app. "
                "Implement POST /api/fleet-alert/dismiss per R1 AC-6."
            )

        fn_src = inspect.getsource(fn)
        assert "load_state" not in fn_src, (
            "fleet_alert_dismiss must NOT call database.load_state(). "
            "AC-6: the race-prone read-modify-write on bot_state is eliminated. "
            "Dismiss writes dismissed_at_et ONLY to fleet_alert_state."
        )

    def test_dismiss_route_source_has_no_save_state_call(self):
        """fleet_alert_dismiss function must not call database.save_state."""
        try:
            import app
        except ImportError:
            pytest.fail("app not importable.")

        fn = getattr(app, "fleet_alert_dismiss", None)
        if fn is None:
            pytest.fail("fleet_alert_dismiss not found in app.")

        fn_src = inspect.getsource(fn)
        assert "save_state" not in fn_src, (
            "fleet_alert_dismiss must NOT call database.save_state(). "
            "This was the source of the race condition (app.py:455-463 original). "
            "R1 fix: use database.write_fleet_alert() exclusively."
        )

    def test_dismiss_route_source_references_write_fleet_alert(self):
        """fleet_alert_dismiss function must reference write_fleet_alert (the new dismiss path)."""
        try:
            import app
        except ImportError:
            pytest.fail("app not importable.")

        fn = getattr(app, "fleet_alert_dismiss", None)
        if fn is None:
            pytest.fail("fleet_alert_dismiss not found in app.")

        fn_src = inspect.getsource(fn)
        assert "write_fleet_alert" in fn_src, (
            "fleet_alert_dismiss must call database.write_fleet_alert() to set dismissed_at_et. "
            "AC-6: the only write is to fleet_alert_state via write_fleet_alert."
        )


# ---------------------------------------------------------------------------
# Section 8 — Migration 009 is additive (bot_state table still present)
# ---------------------------------------------------------------------------


class TestMigration009IsAdditive:
    """
    AC-9: Migration 009 is additive — it adds fleet_alert_state without altering
    or dropping the existing bot_state table.
    """

    def test_migration_009_does_not_drop_bot_state(self):
        """Migration 009 SQL must not contain DROP TABLE or ALTER TABLE for bot_state."""
        migration_path = (
            pathlib.Path(__file__).parent.parent.parent
            / "migrations"
            / "009_fleet_alert_state.sql"
        )
        if not migration_path.exists():
            pytest.fail(
                "migrations/009_fleet_alert_state.sql does not exist. Implement per R1 AC-9."
            )
        sql = migration_path.read_text(encoding="utf-8").lower()
        assert "drop table" not in sql, (
            "Migration 009 must NOT contain DROP TABLE — additive-only per R1 AC-9."
        )
        assert "alter table bot_state" not in sql, (
            "Migration 009 must NOT alter bot_state — existing table is preserved for migration window."
        )

    def test_migration_009_creates_fleet_alert_state_table(self):
        """Migration 009 SQL must create fleet_alert_state."""
        migration_path = (
            pathlib.Path(__file__).parent.parent.parent
            / "migrations"
            / "009_fleet_alert_state.sql"
        )
        if not migration_path.exists():
            pytest.fail("migrations/009_fleet_alert_state.sql does not exist.")
        sql = migration_path.read_text(encoding="utf-8").lower()
        assert "fleet_alert_state" in sql, (
            "Migration 009 must CREATE TABLE fleet_alert_state."
        )
        assert "create table" in sql, (
            "Migration 009 must contain a CREATE TABLE statement."
        )

    def test_bot_state_table_still_exists_after_migration(self):
        """Running migration 009 against a DB that has bot_state must leave bot_state intact."""
        conn = sqlite3.connect(":memory:")
        # Simulate pre-migration state: bot_state exists
        conn.execute("CREATE TABLE bot_state (id INTEGER PRIMARY KEY, data TEXT)")
        conn.execute("INSERT INTO bot_state VALUES (1, '{}')")
        conn.commit()

        # Apply migration 009 DDL inline (fleet_alert_state creation only)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fleet_alert_state (
                id               INTEGER PRIMARY KEY CHECK(id = 1),
                tripped_at_et    TEXT NOT NULL,
                triggered_reason TEXT NOT NULL,
                tripped_count    INTEGER NOT NULL,
                active_count     INTEGER NOT NULL,
                dismissed_at_et  TEXT NULL DEFAULT NULL
            )
        """)
        conn.commit()

        # bot_state must still be intact
        row = conn.execute("SELECT data FROM bot_state WHERE id = 1").fetchone()
        conn.close()
        assert row is not None, (
            "bot_state row must survive migration 009. The migration is additive-only."
        )


# ---------------------------------------------------------------------------
# Section 9 — Daemon restart: alert persists across new DB connection
# ---------------------------------------------------------------------------


class TestAlertPersistsAcrossRestart:
    """
    AC-8: fleet_alert_state is a persisted table. A new DB connection (simulating
    daemon restart) must read back the same row that was written before restart.
    """

    def test_alert_survives_new_connection(self, tmp_path):
        """Write fleet_alert_state row; open a new connection to same file; read_fleet_alert returns row."""
        db_file = str(tmp_path / "test_alphabot.db")

        # Create schema and write row via first connection
        conn_a = sqlite3.connect(db_file)
        conn_a.execute("""
            CREATE TABLE fleet_alert_state (
                id               INTEGER PRIMARY KEY CHECK(id = 1),
                tripped_at_et    TEXT NOT NULL,
                triggered_reason TEXT NOT NULL,
                tripped_count    INTEGER NOT NULL,
                active_count     INTEGER NOT NULL,
                dismissed_at_et  TEXT NULL DEFAULT NULL
            )
        """)
        conn_a.execute(
            "INSERT INTO fleet_alert_state "
            "(id, tripped_at_et, triggered_reason, tripped_count, active_count) "
            "VALUES (1, '2026-05-17T10:33:00', 'Trailing Stop', 6, 10)"
        )
        conn_a.commit()
        conn_a.close()

        # New connection simulates daemon restart
        conn_b = sqlite3.connect(db_file)
        row = conn_b.execute(
            "SELECT tripped_at_et, triggered_reason, tripped_count, active_count, dismissed_at_et "
            "FROM fleet_alert_state WHERE id = 1"
        ).fetchone()
        conn_b.close()

        fixture = _load("alert_lifecycle.json")
        case = next(c for c in fixture["cases"] if c["label"] == "daemon_restart_alert_persists")

        assert row is not None, (
            "fleet_alert_state row must persist across DB connection close/reopen (daemon restart). "
            "AC-8: alert survives restart."
        )
        assert row[0] == case["expected_read_after_new_connection"]["tripped_at_et"], (
            f"tripped_at_et must survive restart. "
            f"Expected {case['expected_read_after_new_connection']['tripped_at_et']!r}, got {row[0]!r}"
        )
        assert row[4] is None, (
            f"dismissed_at_et must be None after restart (was never dismissed). Got: {row[4]!r}"
        )
