"""
RED tests — H4 telemetry helper: live-mode swallows, replay-mode raises.

Contract under test (h4-telemetry-helper plan deliverable 2):
  write_telemetry_row(table_name, row_dict, *, mode: Literal["live","replay"])

Live mode:
  - Swallows sqlite3.Error (any subclass) — cycle must never fail on telemetry.
  - Returns None.
  - Logs exactly one WARNING line containing the table_name, the error type,
    and the cycle_id from row_dict.
  - Does NOT echo any other values from row_dict (gate 7 — no payload leak).

Replay mode:
  - Does NOT catch sqlite3.Error — the error propagates to the caller.
  - A replay that cannot persist is loud-broken by design (H4 binding).

Binding references:
  - h4-telemetry-helper plan §Deliverables (1) and (2)
  - record_shadow_observation precedent: database.py:1147-1194
  - Architecture constraint 1: no blocking I/O on the execution path
  - Project rule 4: is_live=True explicit — here the analogue is mode="live"
    (no default; caller always states the mode explicitly)

Fixture provenance:
  tests/fixtures/math/telemetry_helper_write_basic.json
  — schema-derived; no producer-computed values asserted.
"""

from __future__ import annotations

import json
import logging
import pathlib
import sqlite3
from unittest.mock import patch

import pytest

import database as db

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    pathlib.Path(__file__).parents[1] / "fixtures" / "math" / "telemetry_helper_write_basic.json"
)


@pytest.fixture(scope="module")
def telemetry_fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Helpers — create the target table in the isolated test DB
# ---------------------------------------------------------------------------


def _create_cvar_diagnostics(conn: sqlite3.Connection) -> None:
    """Create a minimal cvar_diagnostics table for write tests.

    Column list mirrors 021-cvar-diagnostics plan; ts_utc gets a DEFAULT so
    callers may omit it — matching the migration spec.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cvar_diagnostics (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id         TEXT    NOT NULL,
            ts_utc           TEXT    NOT NULL DEFAULT (datetime('now')),
            symphony_id      TEXT    NOT NULL,
            cvar_5pct        REAL    DEFAULT NULL,
            cvar_5pct_stderr REAL    DEFAULT NULL,
            cvar_n_tail      INTEGER DEFAULT NULL,
            cvar_5pct_long   REAL    DEFAULT NULL,
            cvar_n_tail_long INTEGER DEFAULT NULL
        )
        """
    )
    conn.commit()


@pytest.fixture
def db_with_cvar_table(tmp_path, monkeypatch):
    """Isolated DB with cvar_diagnostics present; module DB_PATH redirected."""
    db_path = str(tmp_path / "test_telemetry.db")
    monkeypatch.setenv("DB_PATH", db_path)
    db.init_db()
    conn = sqlite3.connect(db_path)
    try:
        _create_cvar_diagnostics(conn)
    finally:
        conn.close()
    return db_path


# ---------------------------------------------------------------------------
# 1. Live mode swallows sqlite3.OperationalError
# ---------------------------------------------------------------------------


def test_live_mode_swallows_operational_error_returns_none(db_with_cvar_table, telemetry_fixture):
    """Live mode: write to a non-existent table raises OperationalError internally;
    write_telemetry_row swallows it and returns None — cycle must never fail
    on telemetry (architecture constraint 1 + H4 live-swallow contract).
    """
    row = dict(telemetry_fixture["row_dict"])

    # Write to a table that does not exist — forces OperationalError
    result = db.write_telemetry_row(
        "table_that_does_not_exist",
        row,
        mode="live",
    )

    assert result is None, (
        f"write_telemetry_row in live mode must return None on swallowed error; got {result!r}"
    )


def test_live_mode_swallows_error_does_not_raise(db_with_cvar_table, telemetry_fixture):
    """Live mode: no exception escapes write_telemetry_row even on DB error.

    This is the structural property that makes M2 telemetry safe on the
    execution path — the cycle proceeds regardless of whether the write
    succeeded.
    """
    row = dict(telemetry_fixture["row_dict"])

    # Must not raise — any uncaught exception here is a test failure
    db.write_telemetry_row("nonexistent_table", row, mode="live")


# ---------------------------------------------------------------------------
# 2. Live mode logs exactly once on error, with required fields, no payload leak
# ---------------------------------------------------------------------------


def test_live_mode_logs_warning_once_on_error(db_with_cvar_table, telemetry_fixture, caplog):
    """Live mode: on a swallowed error, exactly one WARNING record is emitted.

    Gate 7 (no payload leak): the log message contains table_name and the
    error type but does NOT contain arbitrary row_dict values such as
    cvar_5pct or cvar_5pct_stderr.  The cycle_id is the one permitted
    identifier (it is the correlation handle, not financial data).
    """
    row = dict(telemetry_fixture["row_dict"])
    table_name = "nonexistent_table"

    with caplog.at_level(logging.WARNING, logger="database"):
        db.write_telemetry_row(table_name, row, mode="live")

    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) == 1, (
        f"Expected exactly 1 WARNING log on live-mode swallow; "
        f"got {len(warning_records)}: {[r.message for r in warning_records]}"
    )

    log_text = warning_records[0].message
    assert table_name in log_text, (
        f"Log message must include table_name '{table_name}'; got: {log_text!r}"
    )
    # The error class name (or 'Error') must appear so an operator can diagnose
    assert "Error" in log_text or "error" in log_text, (
        f"Log message must contain the error type; got: {log_text!r}"
    )


def test_live_mode_log_does_not_leak_row_payload_values(
    db_with_cvar_table, telemetry_fixture, caplog
):
    """Gate 7: live-mode error log must NOT echo financial field values from row_dict.

    The log shows table_name, error type, and cycle_id.  It must not echo
    cvar_5pct, cvar_5pct_stderr, or cvar_n_tail values — those are the
    financial payload (overlay gate 7, h4-telemetry-helper plan §Deliverables 5).
    """
    # Use a row with recognisable sentinel values in the financial columns
    # so we can grep for them in the log output.
    row = {
        "cycle_id": "CYCLE_LEAK_TEST_001",
        "symphony_id": "SYM_LEAK_TEST",
        "cvar_5pct": 0.0314159,  # distinctive float — must NOT appear in log
        "cvar_5pct_stderr": 0.0271828,  # distinctive float — must NOT appear in log
        "cvar_n_tail": 7,  # distinctive int — must NOT appear in log
        "cvar_5pct_long": None,
        "cvar_n_tail_long": None,
    }

    with caplog.at_level(logging.WARNING, logger="database"):
        db.write_telemetry_row("nonexistent_table", row, mode="live")

    for record in caplog.records:
        log_text = record.message + record.getMessage()
        assert "0.0314159" not in log_text, (
            f"Log must not echo cvar_5pct value; found in: {log_text!r}"
        )
        assert "0.0271828" not in log_text, (
            f"Log must not echo cvar_5pct_stderr value; found in: {log_text!r}"
        )


# ---------------------------------------------------------------------------
# 3. Replay mode: error propagates — does NOT swallow
# ---------------------------------------------------------------------------


def test_replay_mode_raises_on_db_error(db_with_cvar_table, telemetry_fixture):
    """Replay mode: errors propagate — a replay that cannot persist its
    decision row is loud-broken (H4 binding; replay-mode-raise contract).

    CC-002: an unknown table_name now raises ValueError (allowlist check) rather
    than sqlite3.OperationalError.  The contract is satisfied by either: replay
    mode propagates rather than swallowing.
    """
    row = dict(telemetry_fixture["row_dict"])

    with pytest.raises((sqlite3.Error, ValueError)):
        db.write_telemetry_row("nonexistent_table", row, mode="replay")


def test_replay_mode_raises_on_db_integrity_error(db_with_cvar_table, telemetry_fixture):
    """Replay mode: sqlite3.IntegrityError propagates for a bad write to an
    allowlisted table that exists in the DB.

    Uses cvar_diagnostics (in _WRITE_TELEMETRY_TABLES) with a NULL value in the
    NOT NULL cycle_id column to force IntegrityError — confirming that DB-level
    errors (distinct from allowlist ValueError) also propagate in replay mode.
    """
    row = {
        "cycle_id": None,  # NOT NULL violation → IntegrityError
        "symphony_id": "SYM_REPLAY_ERR_001",
        "cvar_5pct": None,
        "cvar_5pct_stderr": None,
        "cvar_n_tail": None,
        "cvar_5pct_long": None,
        "cvar_n_tail_long": None,
    }

    with pytest.raises(sqlite3.IntegrityError):
        db.write_telemetry_row("cvar_diagnostics", row, mode="replay")


def test_replay_mode_does_not_log_swallow_warning(db_with_cvar_table, telemetry_fixture, caplog):
    """Replay mode: on error, NO warning is logged before the exception escapes.

    The live-mode swallow emits a WARNING; the replay-mode raise must not
    emit the same message (which would imply the exception was caught then
    re-raised — an anti-pattern that could mask call-stack information).

    CC-002: unknown table_name raises ValueError from the allowlist check;
    the no-swallow-WARNING contract still holds.
    """
    row = dict(telemetry_fixture["row_dict"])

    with caplog.at_level(logging.WARNING, logger="database"):
        with pytest.raises((sqlite3.Error, ValueError)):
            db.write_telemetry_row("nonexistent_table", row, mode="replay")

    swallow_warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "nonexistent_table" in r.getMessage()
    ]
    assert len(swallow_warnings) == 0, (
        "Replay mode must not emit a swallow WARNING before raising; "
        f"found: {[r.getMessage() for r in swallow_warnings]}"
    )


# ---------------------------------------------------------------------------
# 4. Successful write in live mode inserts one row
# ---------------------------------------------------------------------------


def test_live_mode_successful_write_inserts_one_row(db_with_cvar_table, telemetry_fixture):
    """Live mode success path: one row is inserted into the target table.

    Shape assertion only — we do not assert specific column values (those
    are producer-derived; we assert the row count and that the cycle_id
    key round-trips as a non-empty string).
    """
    row = dict(telemetry_fixture["row_dict"])
    table = telemetry_fixture["table_name"]  # "cvar_diagnostics"

    result = db.write_telemetry_row(table, row, mode="live")

    assert result is None, f"write_telemetry_row success path must return None; got {result!r}"

    # Verify the row landed
    conn = sqlite3.connect(db_with_cvar_table)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM cvar_diagnostics WHERE cycle_id = ?",
            (row["cycle_id"],),
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 1, f"Expected 1 row for cycle_id={row['cycle_id']!r}; found {len(rows)}"
    inserted = dict(rows[0])
    assert isinstance(inserted["id"], int) and inserted["id"] > 0, (
        "Inserted row must have a positive integer id"
    )
    assert isinstance(inserted["cycle_id"], str) and inserted["cycle_id"], (
        "cycle_id must round-trip as a non-empty string"
    )


def test_replay_mode_successful_write_inserts_one_row(db_with_cvar_table, telemetry_fixture):
    """Replay mode success path: one row is inserted (replay write does not block).

    Uses a distinct cycle_id to avoid interference with the live-mode test.
    """
    row = {**telemetry_fixture["row_dict"], "cycle_id": "CYCLE_REPLAY_001"}
    table = telemetry_fixture["table_name"]

    # Must not raise on success
    result = db.write_telemetry_row(table, row, mode="replay")

    assert result is None

    conn = sqlite3.connect(db_with_cvar_table)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM cvar_diagnostics WHERE cycle_id = ?",
            (row["cycle_id"],),
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 1, f"Expected 1 row for cycle_id={row['cycle_id']!r}; found {len(rows)}"


# ---------------------------------------------------------------------------
# 5. Hostile: replay mode never opens a network connection
# ---------------------------------------------------------------------------


def test_replay_mode_never_opens_socket(db_with_cvar_table, telemetry_fixture):
    """Hostile: replay mode must perform zero network I/O.

    Patches socket.socket to assert it is never instantiated during a
    replay-mode write.  Any network connection attempt in replay mode is a
    binding violation of the fixture-first / replay-isolation contract.
    """
    import socket

    row = {**telemetry_fixture["row_dict"], "cycle_id": "CYCLE_NO_NET_001"}
    table = telemetry_fixture["table_name"]

    original_socket = socket.socket

    class _BombSocket:
        """Raises immediately if instantiated — proves no socket was opened."""

        def __init__(self, *args, **kwargs):
            raise AssertionError(
                "write_telemetry_row(mode='replay') must not open a network socket"
            )

    with patch.object(socket, "socket", _BombSocket):
        # If a socket is opened this will raise AssertionError, failing the test
        db.write_telemetry_row(table, row, mode="replay")


def test_live_mode_never_opens_socket(db_with_cvar_table, telemetry_fixture):
    """Live mode also must not open a network socket (architecture constraint 1).

    The helper writes to SQLite only — a socket during a live-mode write
    would be a blocking-I/O violation on the execution path.
    """
    import socket

    row = {**telemetry_fixture["row_dict"], "cycle_id": "CYCLE_LIVE_NO_NET_001"}
    table = telemetry_fixture["table_name"]

    class _BombSocket:
        def __init__(self, *args, **kwargs):
            raise AssertionError("write_telemetry_row(mode='live') must not open a network socket")

    with patch.object(socket, "socket", _BombSocket):
        db.write_telemetry_row(table, row, mode="live")
