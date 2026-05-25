"""
RED tests — H4 helper: conn-unbound-on-connect-failure + live-swallow / replay-raise.

Carried-forward note from H4 cycle (rev-h4 via impl-h4):
  database.py record_shadow_observation (now write_telemetry_row) opens
  conn = sqlite3.connect(...); on connect() failure conn is never bound so
  conn.close() is skipped.  This is NOT a correctness issue (SQLite releases
  on GC) but must be explicitly tested:

  Test: mock sqlite3.connect to raise immediately; assert that:
    (a) live mode — no exception escapes write_telemetry_row (swallow contract intact)
    (b) replay mode — the connect() exception propagates (replay-raise contract intact)
    (c) no conn.close() AttributeError or NameError leaks from the failure path
        (i.e., the unbound-conn case is handled gracefully even if conn.close()
        is NOT called — the test confirms the swallow catches the OperationalError
        from connect() itself, not a NameError from trying to .close() an unbound name)

These are the hostile tests the team-lead required pinned in this cycle.

Additional hostile invariants from the team-lead brief:
  - Truthy-non-bool mode values are rejected (already in test_telemetry_helper_no_default_mode.py
    for write_telemetry_row, but pinned here for the record_cvar_diagnostic wrapper too)
  - replay mode never opens a network connection (structural property)

Binding refs:
  - live-vs-replay-safety-boundary plan (team-lead BINDING HAZARDS note)
  - H4 telemetry helper (write_telemetry_row + record_cvar_diagnostic)
  - database.py:1485-1539 (write_telemetry_row implementation)
  - record_shadow_observation precedent: database.py:1423 (conn-unbound pattern)
  - Architecture constraint 1: no blocking I/O on execution path
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch, MagicMock

import pytest

import database as db


# ---------------------------------------------------------------------------
# Fixture: minimal valid cvar_diagnostics row (schema-derived, no producer values)
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_row():
    """Minimal row dict matching the cvar_diagnostics schema.

    Values are test identifiers, not producer-computed outputs.
    """
    return {
        "cycle_id": "CYCLE_CONN_FAIL_TEST_001",
        "symphony_id": "SYM_CONN_FAIL",
        "cvar_5pct": None,
        "cvar_5pct_stderr": None,
        "cvar_n_tail": 0,
        "cvar_5pct_long": None,
        "cvar_n_tail_long": None,
    }


# ---------------------------------------------------------------------------
# 1. conn-unbound-on-connect-failure: live mode swallows OperationalError from connect()
# ---------------------------------------------------------------------------


class TestConnUnboundOnConnectFailureLiveMode:
    """sqlite3.connect() raising OperationalError must be caught in live mode.

    The 'conn-unbound' case: connect() raises before conn is ever assigned,
    so the except block must catch the exception directly (not via conn.close()).
    Live mode must swallow the error — the cycle must never fail on telemetry.
    """

    def test_live_mode_swallows_connect_failure_returns_none(self, minimal_row):
        """When sqlite3.connect() raises, live mode swallows it and returns None."""
        def _failing_connect(*args, **kwargs):
            raise sqlite3.OperationalError("unable to open database file (test-induced)")

        with patch("sqlite3.connect", side_effect=_failing_connect):
            result = db.write_telemetry_row(
                "cvar_diagnostics",
                minimal_row,
                mode="live",
            )

        assert result is None, (
            "write_telemetry_row in live mode must return None when sqlite3.connect() fails; "
            f"got {result!r}. "
            "The conn-unbound case: OperationalError from connect() must be caught by "
            "the sqlite3.Error handler before conn is ever assigned."
        )

    def test_live_mode_does_not_raise_on_connect_failure(self, minimal_row):
        """No exception escapes write_telemetry_row in live mode when connect() fails.

        Verifies the swallow is unconditional — the cycle path is never broken
        by a telemetry connect failure (architecture constraint 1).
        """
        def _failing_connect(*args, **kwargs):
            raise sqlite3.OperationalError("disk full (test-induced)")

        with patch("sqlite3.connect", side_effect=_failing_connect):
            # Must not raise — any exception here is a test failure
            try:
                db.write_telemetry_row("cvar_diagnostics", minimal_row, mode="live")
            except Exception as exc:
                pytest.fail(
                    f"write_telemetry_row(mode='live') raised {type(exc).__name__} when "
                    f"sqlite3.connect() failed: {exc}. "
                    "Live mode must swallow ALL sqlite3.Error from any point in the write path "
                    "including the connect() call itself."
                )

    def test_live_mode_conn_unbound_produces_no_name_error(self, minimal_row):
        """conn-unbound case produces no NameError or AttributeError.

        If write_telemetry_row has a finally: conn.close() block, and conn is
        never bound because connect() raised, Python raises NameError: 'conn'.
        That would mean the except swallowed the OperationalError but a NameError
        escapes. This test asserts neither NameError nor AttributeError leaks.
        """
        def _failing_connect(*args, **kwargs):
            raise sqlite3.OperationalError("journal file is locked (test-induced)")

        with patch("sqlite3.connect", side_effect=_failing_connect):
            try:
                db.write_telemetry_row("cvar_diagnostics", minimal_row, mode="live")
            except (NameError, AttributeError) as exc:
                pytest.fail(
                    f"write_telemetry_row raised {type(exc).__name__} when sqlite3.connect() "
                    f"failed in live mode: {exc}. "
                    "This is the conn-unbound defect: a finally: conn.close() block "
                    "references conn before it is assigned. Fix: guard with "
                    "'if conn is not None: conn.close()' or restructure to avoid finally."
                )
            except Exception:
                pass  # Other exceptions are caught by the live-swallow tests above


# ---------------------------------------------------------------------------
# 2. conn-unbound-on-connect-failure: replay mode propagates the connect() error
# ---------------------------------------------------------------------------


class TestConnUnboundOnConnectFailureReplayMode:
    """sqlite3.connect() raising OperationalError must propagate in replay mode.

    A replay that cannot open the DB is loud-broken by design (H4 binding).
    The connect() OperationalError must propagate unchanged.
    """

    def test_replay_mode_propagates_connect_failure(self, minimal_row):
        """When sqlite3.connect() raises, replay mode lets it propagate."""
        def _failing_connect(*args, **kwargs):
            raise sqlite3.OperationalError("no such file or directory (test-induced)")

        with patch("sqlite3.connect", side_effect=_failing_connect):
            with pytest.raises(sqlite3.OperationalError):
                db.write_telemetry_row(
                    "cvar_diagnostics",
                    minimal_row,
                    mode="replay",
                )

    def test_replay_mode_connect_failure_not_swallowed_as_none(self, minimal_row):
        """Replay mode must NOT return None when connect() fails — it must raise.

        An implementation that catches all exceptions and returns None would be
        safe for live mode but wrong for replay — this test pins the distinction.
        """
        def _failing_connect(*args, **kwargs):
            raise sqlite3.OperationalError("database is locked (test-induced)")

        raised = False
        with patch("sqlite3.connect", side_effect=_failing_connect):
            try:
                result = db.write_telemetry_row(
                    "cvar_diagnostics",
                    minimal_row,
                    mode="replay",
                )
                # If we got here without raising, the replay-raise contract is broken
                pytest.fail(
                    f"write_telemetry_row(mode='replay') returned {result!r} "
                    "instead of raising when sqlite3.connect() failed. "
                    "Replay mode must propagate DB errors so a broken replay is loud-broken."
                )
            except sqlite3.OperationalError:
                raised = True

        assert raised, (
            "Replay mode must raise sqlite3.OperationalError on connect() failure."
        )


# ---------------------------------------------------------------------------
# 3. Hostile: truthy-non-bool mode is rejected by record_cvar_diagnostic
# ---------------------------------------------------------------------------


class TestRecordCvarDiagnosticModeRejection:
    """record_cvar_diagnostic must reject truthy-non-bool mode values.

    This is the is_live=True-exact analogue: the mode= parameter on the
    wrapper must be exactly 'live' or 'replay'. Passing True (truthy bool)
    silently routes into the live-swallow path — a correctness defect that
    would swallow replay-context errors.

    Plan risk R4: mode default disallowed + only literals accepted.
    """

    @pytest.mark.parametrize("bad_mode", [
        True,    # truthy bool — must not be accepted
        1,       # truthy int
        False,   # falsy bool
        0,       # falsy int
        "Live",  # wrong casing
        "LIVE",  # wrong casing
        None,    # None
        "",      # empty string
    ])
    def test_record_cvar_diagnostic_rejects_invalid_mode(self, bad_mode):
        """record_cvar_diagnostic must raise TypeError or ValueError for non-literal mode.

        The thin wrapper must not silently accept truthy-non-bool values —
        it must delegate the mode validation to write_telemetry_row, which
        enforces the Literal["live", "replay"] contract.
        """
        with pytest.raises((TypeError, ValueError)):
            db.record_cvar_diagnostic(
                cycle_id="CYCLE_MODE_REJECT_001",
                symphony_id="SYM_REJECT",
                cvar_5pct=None,
                cvar_5pct_stderr=None,
                cvar_n_tail=0,
                cvar_5pct_long=None,
                cvar_n_tail_long=None,
                mode=bad_mode,  # type: ignore[arg-type]
            )

    def test_record_cvar_diagnostic_omitting_mode_raises_type_error(self):
        """Omitting mode= from record_cvar_diagnostic must raise TypeError.

        The mode parameter is keyword-only with no default — same contract as
        write_telemetry_row (plan risk R4 / arch constraint 4 analogue).
        """
        with pytest.raises(TypeError, match="mode"):
            db.record_cvar_diagnostic(  # type: ignore[call-arg]
                cycle_id="CYCLE_NO_MODE_001",
                symphony_id="SYM_NO_MODE",
                cvar_5pct=None,
                cvar_5pct_stderr=None,
                cvar_n_tail=0,
                cvar_5pct_long=None,
                cvar_n_tail_long=None,
                # mode= intentionally omitted
            )


# ---------------------------------------------------------------------------
# 4. Replay mode never opens a network connection
# ---------------------------------------------------------------------------


def test_write_telemetry_row_replay_never_opens_socket(minimal_row, tmp_path, monkeypatch):
    """Hostile: replay mode must perform zero network I/O.

    Derived from the team-lead brief: replay mode never opens network connection.
    Patches socket.socket to bomb on instantiation — any socket creation in
    the replay write path is a binding violation of the replay-isolation contract.

    Uses a tmp_path DB so the replay write path actually runs (not mocked out).
    """
    import socket

    db_path = str(tmp_path / "replay_no_socket.db")
    monkeypatch.setenv("DB_PATH", db_path)
    db.init_db()

    # Create the cvar_diagnostics table in the test DB
    conn_setup = sqlite3.connect(db_path)
    try:
        conn_setup.execute(
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
        conn_setup.commit()
    finally:
        conn_setup.close()

    row = {**minimal_row, "cycle_id": "CYCLE_REPLAY_NO_SOCKET_001"}
    original_socket = socket.socket

    class _BombSocket:
        def __init__(self, *args, **kwargs):
            raise AssertionError(
                "write_telemetry_row(mode='replay') must not open a network socket. "
                "SQLite is a file-backed DB — no network I/O is required."
            )

    with patch.object(socket, "socket", _BombSocket):
        # Must complete without BombSocket being instantiated
        db.write_telemetry_row("cvar_diagnostics", row, mode="replay")
