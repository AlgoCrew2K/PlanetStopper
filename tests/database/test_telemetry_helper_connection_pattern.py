"""
RED tests — H4 telemetry helper: connection pattern, wrapper contract, replay-context safety.

Covers spec-h4 and rev-h4 findings not encoded in the initial RED commit:

Finding 1 / rev-h4 note:
  - write_telemetry_row must NOT pass isolation_level=None to sqlite3.connect
    (that would enable autocommit mode, diverging from the record_shadow_observation
    precedent at database.py:1199 which uses explicit commit+close)
  - write_telemetry_row MUST use timeout=10.0 (matching the precedent)

Finding 5:
  - sqlite3.IntegrityError (e.g. NULL into a NOT NULL column) must be swallowed
    in live mode — IntegrityError is a subclass of sqlite3.Error; the swallow
    contract covers ALL sqlite3.Error subclasses, not just OperationalError

Finding 6 / REQ-7:
  - record_cvar_diagnostic(...)  is a thin wrapper over write_telemetry_row;
    its body contains NO sqlite3.connect call and NO INSERT SQL — all write
    logic is delegated to write_telemetry_row; two connection paths would
    split the live-swallow / replay-raise contract

REQ-8:
  - autotuner.py contains no write_telemetry_row call with mode="live";
    every replay-context call site must pass mode="replay" explicitly (R1 from plan)

Binding references:
  - h4-telemetry-helper plan deliverable (1) connection pattern + deliverable (6)
  - record_shadow_observation precedent: database.py:1171-1224
  - live-vs-replay-safety-boundary plan risk R1 (silent-swallow hazard)
  - Architecture constraint 1: no blocking I/O on execution path
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import sqlite3
from unittest.mock import call, patch

import pytest

import database as db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WORKTREE_ROOT = pathlib.Path(__file__).parents[2]


def _read_source(rel_path: str) -> str:
    return (_WORKTREE_ROOT / rel_path).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Finding 1: connection pattern — no isolation_level=None; timeout=10.0
# ---------------------------------------------------------------------------


def test_write_telemetry_row_does_not_use_isolation_level_none():
    """Source guard: write_telemetry_row must NOT pass isolation_level=None.

    Setting isolation_level=None switches sqlite3 into autocommit mode,
    which changes the transaction semantics relative to the
    record_shadow_observation precedent.  That precedent uses an explicit
    conn.commit() — the new helper must match it.

    spec-h4 Finding 1: the plan's 'isolation_level=None' clause is a spec
    error; adopt the existing contract instead.
    """
    assert hasattr(db, "write_telemetry_row"), "write_telemetry_row not found — implement it first"
    source = inspect.getsource(db.write_telemetry_row)
    assert "isolation_level=None" not in source, (
        "write_telemetry_row must not pass isolation_level=None to sqlite3.connect. "
        "Use the record_shadow_observation pattern: "
        "sqlite3.connect(_db_file(), timeout=10.0) + explicit commit+close."
    )


def test_write_telemetry_row_uses_timeout_10():
    """Source guard: write_telemetry_row (or its internal helper) must pass timeout=10.0.

    The record_shadow_observation precedent at database.py:1199 uses
    timeout=10.0 — the same timeout as all other write-connection opens
    in this codebase.  Omitting it would use the default (5.0 s) and
    diverge from the established contract without justification.

    The shadow-logging-pattern plan §Risk callouts (rev-shadow BLOCK) explicitly
    rejects timeout=0.0 on the live path — the non-blocking guarantee is provided
    by the try/except swallow, not by a short timeout. timeout=0.0 causes data loss:
    transient sub-millisecond WAL locks that would resolve in microseconds get
    treated as permanent failures, silently dropping CVaR rows.

    CC-002: the sqlite3.connect call was moved into _write_telemetry_row_unsafe;
    we also accept timeout=10.0 in that helper's source.
    """
    assert hasattr(db, "write_telemetry_row"), "write_telemetry_row not found — implement it first"
    source = inspect.getsource(db.write_telemetry_row)
    # CC-002: sqlite3.connect lives in the private helper; check both.
    # Post fix-perf-fixture: _write_telemetry_row_unsafe was refactored to call
    # get_connection() (which routes :memory: through a shared-cache URI). The
    # timeout=10.0 contract is preserved via composition — check get_connection
    # source too so the indirection doesn't fail this contract test.
    helper_source = (
        inspect.getsource(db._write_telemetry_row_unsafe)
        if hasattr(db, "_write_telemetry_row_unsafe")
        else ""
    )
    get_connection_source = (
        inspect.getsource(db.get_connection) if hasattr(db, "get_connection") else ""
    )
    assert (
        "timeout=10.0" in source
        or "timeout=10.0" in helper_source
        or "timeout=10.0" in get_connection_source
    ), (
        "write_telemetry_row (or its internal helper, or the shared get_connection "
        "factory it now delegates to) must use timeout=10.0 in sqlite3.connect, "
        "matching the record_shadow_observation precedent."
    )


def test_write_telemetry_row_calls_commit_before_close(tmp_path, monkeypatch):
    """Behavioural: after a successful INSERT, conn.commit() is called before
    conn.close().

    Verifies the explicit commit+close pattern (not relying on autocommit).
    We intercept sqlite3.connect and return a proxy wrapper that tracks
    commit and close calls.  sqlite3.Connection methods are C-level and
    read-only on the instance, so we wrap via __getattr__ delegation.
    """
    db_path = str(tmp_path / "commit_order.db")
    monkeypatch.setenv("DB_PATH", db_path)
    db.init_db()

    # Create the target table
    conn_setup = sqlite3.connect(db_path)
    try:
        conn_setup.execute(
            """
            CREATE TABLE IF NOT EXISTS cvar_diagnostics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id TEXT NOT NULL,
                ts_utc TEXT NOT NULL DEFAULT (datetime('now')),
                symphony_id TEXT NOT NULL,
                cvar_5pct REAL DEFAULT NULL,
                cvar_5pct_stderr REAL DEFAULT NULL,
                cvar_n_tail INTEGER DEFAULT NULL,
                cvar_5pct_long REAL DEFAULT NULL,
                cvar_n_tail_long INTEGER DEFAULT NULL
            )
            """
        )
        conn_setup.commit()
    finally:
        conn_setup.close()

    row = {
        "cycle_id": "CYCLE_COMMIT_TEST_001",
        "symphony_id": "SYM_COMMIT",
        "cvar_5pct": None,
        "cvar_5pct_stderr": None,
        "cvar_n_tail": 0,
        "cvar_5pct_long": None,
        "cvar_n_tail_long": None,
    }

    call_order: list[str] = []
    real_connect = sqlite3.connect

    class _TrackingConnection:
        """Proxy that delegates to a real sqlite3.Connection but records commit/close."""

        def __init__(self, real_conn: sqlite3.Connection) -> None:
            # Store under a private name to avoid accidental recursion
            object.__setattr__(self, "_real", real_conn)

        def commit(self) -> None:
            call_order.append("commit")
            object.__getattribute__(self, "_real").commit()

        def close(self) -> None:
            call_order.append("close")
            object.__getattribute__(self, "_real").close()

        def __getattr__(self, name: str):
            return getattr(object.__getattribute__(self, "_real"), name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    def _tracking_connect(*args, **kwargs):
        real_conn = real_connect(*args, **kwargs)
        return _TrackingConnection(real_conn)

    with patch("sqlite3.connect", side_effect=_tracking_connect):
        db.write_telemetry_row("cvar_diagnostics", row, mode="live")

    assert "commit" in call_order, (
        "write_telemetry_row must call conn.commit() after INSERT. "
        f"Call order observed: {call_order}"
    )
    assert "close" in call_order, (
        "write_telemetry_row must call conn.close() after commit. "
        f"Call order observed: {call_order}"
    )
    commit_idx = call_order.index("commit")
    close_idx = call_order.index("close")
    assert commit_idx < close_idx, (
        f"commit() must be called before close(); got order: {call_order}"
    )


# ---------------------------------------------------------------------------
# Finding 5: IntegrityError (NULL into NOT NULL column) swallowed in live mode
# ---------------------------------------------------------------------------


def test_live_mode_swallows_integrity_error_null_column(tmp_path, monkeypatch):
    """Live mode: sqlite3.IntegrityError (NOT NULL violation) is swallowed.

    IntegrityError is a subclass of sqlite3.Error.  The swallow contract
    must cover ALL sqlite3.Error subclasses — a caller that passes
    None for a NOT NULL column (e.g. an insufficient-MC sentinel) must
    not crash the live cycle.

    spec-h4 Finding 5: tests the swallow covers IntegrityError explicitly.

    CC-002: table_name must be in _WRITE_TELEMETRY_TABLES; uses cvar_diagnostics
    (cycle_id is NOT NULL) to force the IntegrityError path.
    """
    db_path = str(tmp_path / "integrity_test.db")
    monkeypatch.setenv("DB_PATH", db_path)
    db.init_db()

    # Create cvar_diagnostics with a NOT NULL cycle_id to force IntegrityError
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cvar_diagnostics (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id  TEXT NOT NULL,
                symphony_id TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    # Pass None for the NOT NULL column — this should cause IntegrityError
    row = {
        "cycle_id": None,  # violates NOT NULL constraint
        "symphony_id": "SYM_INTEGRITY_001",
    }

    # Must not raise — live mode swallows ALL sqlite3.Error subclasses
    result = db.write_telemetry_row("cvar_diagnostics", row, mode="live")

    assert result is None, (
        "write_telemetry_row(mode='live') must return None even when "
        f"swallowing IntegrityError; got {result!r}"
    )


def test_replay_mode_propagates_integrity_error(tmp_path, monkeypatch):
    """Replay mode: sqlite3.IntegrityError propagates — not swallowed.

    A replay that writes None into a NOT NULL column has a schema mismatch —
    it must fail loud so the replay harness can detect the error.

    CC-002: table_name must be in _WRITE_TELEMETRY_TABLES; uses cvar_diagnostics
    (cycle_id is NOT NULL) to force the IntegrityError path.
    """
    db_path = str(tmp_path / "integrity_replay.db")
    monkeypatch.setenv("DB_PATH", db_path)
    db.init_db()

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cvar_diagnostics (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_id  TEXT NOT NULL,
                symphony_id TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    row = {
        "cycle_id": None,  # violates NOT NULL constraint
        "symphony_id": "SYM_INTEGRITY_REPLAY_001",
    }

    with pytest.raises(sqlite3.IntegrityError):
        db.write_telemetry_row("cvar_diagnostics", row, mode="replay")


# ---------------------------------------------------------------------------
# Finding 6 / REQ-7: record_cvar_diagnostic is a thin wrapper
# ---------------------------------------------------------------------------


def test_record_cvar_diagnostic_exists():
    """database.record_cvar_diagnostic must exist as a callable.

    H4 plan deliverable (6): M2's writer is a thin wrapper over write_telemetry_row.
    """
    assert hasattr(db, "record_cvar_diagnostic"), (
        "database.record_cvar_diagnostic does not exist. "
        "H4 plan deliverable (6) requires this thin-wrapper function."
    )
    assert callable(db.record_cvar_diagnostic), "database.record_cvar_diagnostic is not callable."


def test_record_cvar_diagnostic_source_has_no_sqlite3_connect():
    """Source guard: record_cvar_diagnostic must NOT contain sqlite3.connect.

    All write logic is delegated to write_telemetry_row.  A direct
    sqlite3.connect in record_cvar_diagnostic would create a second
    connection path that bypasses the live-swallow / replay-raise contract
    (spec-h4 Finding 6 / rev-h4 REQ-7).
    """
    assert hasattr(db, "record_cvar_diagnostic"), (
        "record_cvar_diagnostic not found — see test_record_cvar_diagnostic_exists"
    )
    source = inspect.getsource(db.record_cvar_diagnostic)
    assert "sqlite3.connect" not in source, (
        "record_cvar_diagnostic must not open its own connection. "
        "All write logic must be delegated to write_telemetry_row. "
        "A direct sqlite3.connect would split the live-swallow contract."
    )


def test_record_cvar_diagnostic_source_has_no_insert_sql():
    """Source guard: record_cvar_diagnostic must NOT contain an INSERT statement.

    An inline INSERT would bypass write_telemetry_row and duplicate the
    connection management logic.  The wrapper must call write_telemetry_row
    and nothing else for the write path (rev-h4 REQ-7).
    """
    assert hasattr(db, "record_cvar_diagnostic"), (
        "record_cvar_diagnostic not found — see test_record_cvar_diagnostic_exists"
    )
    source = inspect.getsource(db.record_cvar_diagnostic)
    assert "INSERT" not in source.upper(), (
        "record_cvar_diagnostic must not contain an INSERT statement. "
        "Delegate all writes to write_telemetry_row."
    )


def test_record_cvar_diagnostic_calls_write_telemetry_row(tmp_path, monkeypatch):
    """Behavioural: record_cvar_diagnostic delegates to write_telemetry_row.

    Patches write_telemetry_row and asserts it is called by
    record_cvar_diagnostic — confirming the delegation chain is live,
    not just source-verified.
    """
    assert hasattr(db, "record_cvar_diagnostic"), (
        "record_cvar_diagnostic not found — see test_record_cvar_diagnostic_exists"
    )

    db_path = str(tmp_path / "wrapper_delegate.db")
    monkeypatch.setenv("DB_PATH", db_path)
    db.init_db()

    with patch.object(db, "write_telemetry_row") as mock_write:
        mock_write.return_value = None
        # Call with representative minimal args — exact signature is impl-defined
        # but mode= must be passed through (no default at the wrapper level either)
        try:
            db.record_cvar_diagnostic(
                cycle_id="CYCLE_WRAPPER_001",
                symphony_id="SYM_WRAPPER",
                cvar_5pct=None,
                cvar_5pct_stderr=None,
                cvar_n_tail=0,
                cvar_5pct_long=None,
                cvar_n_tail_long=None,
                mode="live",
            )
        except TypeError:
            # If the wrapper has a different signature, that is itself a test
            # the implementer needs to fix — but the delegation call must still land.
            pass

    assert mock_write.called, (
        "record_cvar_diagnostic must call write_telemetry_row on its write path. "
        f"write_telemetry_row was not called. "
        "Check that the wrapper delegates rather than implementing a second path."
    )


# ---------------------------------------------------------------------------
# REQ-8: autotuner.py contains no write_telemetry_row call with mode="live"
# ---------------------------------------------------------------------------


def test_autotuner_does_not_call_write_telemetry_row_with_live_mode():
    """Grep/AST test: autotuner.py must not call write_telemetry_row(mode='live').

    autotuner.py is a replay-context file — it runs walk-forward replay cycles
    that must never swallow telemetry errors (replay errors must fail loud).
    Any call in autotuner.py must use mode='replay'.

    live-vs-replay-safety-boundary plan risk R1 / rev-h4 REQ-8.
    """
    autotuner_path = _WORKTREE_ROOT / "autotuner.py"
    if not autotuner_path.exists():
        pytest.skip("autotuner.py not found — skip autotuner call-site scan")

    source = autotuner_path.read_text(encoding="utf-8")

    # Fast grep for the obvious pattern first
    assert (
        'mode="live"' not in source
        or "write_telemetry_row" not in source
        or (
            # If both appear, do a finer AST check
            _autotuner_has_no_live_telemetry_call(source)
        )
    ), (
        "autotuner.py contains a write_telemetry_row call with mode='live'. "
        "autotuner is a replay-context file; all telemetry writes must use "
        "mode='replay' so a write failure fails loud (replay-raise contract). "
        "R1: a mode='live' in autotuner silently swallows replay write failures."
    )


def _autotuner_has_no_live_telemetry_call(source: str) -> bool:
    """Return True if no write_telemetry_row call with mode='live' is found via AST."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return True  # can't parse — let grep result stand

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match write_telemetry_row(...) calls
        func = node.func
        is_telemetry_call = (isinstance(func, ast.Name) and func.id == "write_telemetry_row") or (
            isinstance(func, ast.Attribute) and func.attr == "write_telemetry_row"
        )
        if not is_telemetry_call:
            continue
        # Check keyword arguments for mode="live"
        for kw in node.keywords:
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                if kw.value.value == "live":
                    return False  # found a live call — violation
    return True  # no live call found — safe


def test_autotuner_write_telemetry_calls_use_replay_mode_if_present():
    """If autotuner.py calls write_telemetry_row at all, every call uses mode='replay'.

    This is a stronger version of the above: we enumerate ALL call sites and
    assert each uses mode='replay' (not just that none use mode='live' —
    an explicit assertion catches mode omitted or mode=variable too).
    """
    autotuner_path = _WORKTREE_ROOT / "autotuner.py"
    if not autotuner_path.exists():
        pytest.skip("autotuner.py not found")

    source = autotuner_path.read_text(encoding="utf-8")
    if "write_telemetry_row" not in source:
        # No calls yet — test passes (the scan is a forward-guard, not a retroactive test)
        return

    try:
        tree = ast.parse(source)
    except SyntaxError:
        pytest.skip("autotuner.py has syntax errors — skipping AST scan")

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_telemetry_call = (isinstance(func, ast.Name) and func.id == "write_telemetry_row") or (
            isinstance(func, ast.Attribute) and func.attr == "write_telemetry_row"
        )
        if not is_telemetry_call:
            continue
        # Find the mode= keyword argument
        mode_kwarg = next((kw for kw in node.keywords if kw.arg == "mode"), None)
        if mode_kwarg is None:
            violations.append(
                f"line {node.lineno}: write_telemetry_row called without mode= keyword"
            )
        elif isinstance(mode_kwarg.value, ast.Constant):
            if mode_kwarg.value.value != "replay":
                violations.append(
                    f"line {node.lineno}: write_telemetry_row called with "
                    f"mode={mode_kwarg.value.value!r} (must be 'replay' in autotuner context)"
                )
        else:
            # mode is a variable — flag for review
            violations.append(
                f"line {node.lineno}: write_telemetry_row called with dynamic mode= "
                f"(must be the literal 'replay' in autotuner context)"
            )

    assert not violations, (
        "autotuner.py write_telemetry_row call-site violations (R1 — silent-swallow hazard):\n"
        + "\n".join(f"  {v}" for v in violations)
    )
