"""
Regression tests — CC-002: SQL injection hardening for write_telemetry_row.

Sprint-1 audit finding CC-002 (CRITICAL): write_telemetry_row constructed SQL
via f-string with an unvalidated table_name and unvalidated column names from
row_dict.keys().  Only the VALUES tuple was parameterized.

Fix:
  - _WRITE_TELEMETRY_TABLES frozenset allowlist: table_name must be a member
    before any f-string interpolation.
  - _IDENTIFIER_RE column-name check: every key in row_dict must match
    ^[a-z_][a-z0-9_]*$ before interpolation.
  - ValueError raised on any violation; live-mode callers swallow it
    (cycle must never fail on telemetry); replay-mode callers let it propagate.

These tests exercise the injection guards directly — they do NOT test the
live-swallow / replay-raise contract (that lives in test_telemetry_helper_*).
"""

from __future__ import annotations

import sqlite3

import pytest

import database as db


# ---------------------------------------------------------------------------
# Helpers: minimal DB + cvar_diagnostics table for success-path tests
# ---------------------------------------------------------------------------


def _setup_cvar_table(tmp_path, monkeypatch) -> str:
    """Return a DB path pointing at an isolated DB with cvar_diagnostics."""
    db_path = str(tmp_path / "cc002_test.db")
    monkeypatch.setenv("DB_PATH", db_path)
    db.init_db()
    conn = sqlite3.connect(db_path)
    try:
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
    finally:
        conn.close()
    return db_path


_VALID_ROW = {
    "cycle_id": "CYCLE_CC002_001",
    "symphony_id": "SYM_CC002",
    "cvar_5pct": -0.042,
    "cvar_5pct_stderr": 0.003,
    "cvar_n_tail": 8,
    "cvar_5pct_long": None,
    "cvar_n_tail_long": None,
}


# ---------------------------------------------------------------------------
# 1. Success path: allowlisted table + valid column names succeeds
# ---------------------------------------------------------------------------


def test_allowlisted_table_and_valid_columns_succeeds(tmp_path, monkeypatch):
    """write_telemetry_row with a whitelisted table and safe column names must
    insert one row without raising.

    This is the baseline: the injection guards must not break the legitimate
    production call path.
    """
    _setup_cvar_table(tmp_path, monkeypatch)

    # Must not raise — a ValueError here would break record_cvar_diagnostic
    db.write_telemetry_row("cvar_diagnostics", dict(_VALID_ROW), mode="replay")

    db_path = str(tmp_path / "cc002_test.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM cvar_diagnostics WHERE cycle_id = ?",
            (_VALID_ROW["cycle_id"],),
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 1, (
        f"Expected 1 inserted row; found {len(rows)}. "
        "Allowlist guard must not block legitimate writes."
    )


# ---------------------------------------------------------------------------
# 2. table_name not in allowlist → ValueError
# ---------------------------------------------------------------------------


def test_table_name_not_in_allowlist_raises_value_error(tmp_path, monkeypatch):
    """A table_name not in _WRITE_TELEMETRY_TABLES must raise ValueError.

    This is the primary CC-002 guard: an arbitrary caller cannot inject a
    table name into the f-string.  ValueError is raised before any SQL is
    built so no DB I/O occurs.
    """
    _setup_cvar_table(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="telemetry allowlist"):
        db.write_telemetry_row("users", dict(_VALID_ROW), mode="replay")


def test_table_name_sql_injection_payload_raises_value_error(tmp_path, monkeypatch):
    """A table_name containing a SQL injection payload is rejected as not in
    the allowlist — the allowlist check fires before any identifier validation.
    """
    _setup_cvar_table(tmp_path, monkeypatch)

    injection = "cvar_diagnostics; DROP TABLE cvar_diagnostics; --"

    with pytest.raises(ValueError):
        db.write_telemetry_row(injection, dict(_VALID_ROW), mode="replay")


# ---------------------------------------------------------------------------
# 3. col_name containing "; DROP TABLE ..." → ValueError
# ---------------------------------------------------------------------------


def test_col_name_with_drop_table_injection_raises_value_error(tmp_path, monkeypatch):
    """A column name containing a SQL injection payload must raise ValueError.

    The identifier regex ^[a-z_][a-z0-9_]*$ rejects semicolons, spaces, and
    SQL keywords — the f-string interpolation path is never reached.
    """
    _setup_cvar_table(tmp_path, monkeypatch)

    row = {
        "cycle_id": "CYCLE_CC002_INJ_001",
        "col; DROP TABLE cvar_diagnostics; --": "injected",
    }

    with pytest.raises(ValueError, match="valid.*SQLite identifier|SQLite identifier"):
        db.write_telemetry_row("cvar_diagnostics", row, mode="replay")


# ---------------------------------------------------------------------------
# 4. col_name containing a single quote → ValueError
# ---------------------------------------------------------------------------


def test_col_name_with_single_quote_raises_value_error(tmp_path, monkeypatch):
    """A column name containing a single quote must raise ValueError.

    Single quotes in identifiers are a classic SQL injection vector; the
    identifier regex rejects them before any f-string interpolation.
    """
    _setup_cvar_table(tmp_path, monkeypatch)

    row = {
        "cycle_id": "CYCLE_CC002_QUOTE_001",
        "col'injection": "injected",
    }

    with pytest.raises(ValueError, match="valid.*SQLite identifier|SQLite identifier"):
        db.write_telemetry_row("cvar_diagnostics", row, mode="replay")


# ---------------------------------------------------------------------------
# 5. col_name containing a hyphen → ValueError
# ---------------------------------------------------------------------------


def test_col_name_with_hyphen_raises_value_error(tmp_path, monkeypatch):
    """A column name containing a hyphen must raise ValueError.

    Hyphens are not valid SQLite unquoted identifier characters; allowing them
    would open a path where `col-name` is interpreted as `col` minus `name`.
    The identifier regex ^[a-z_][a-z0-9_]*$ rejects hyphens.
    """
    _setup_cvar_table(tmp_path, monkeypatch)

    row = {
        "cycle_id": "CYCLE_CC002_HYPHEN_001",
        "col-with-hyphen": "value",
    }

    with pytest.raises(ValueError, match="valid.*SQLite identifier|SQLite identifier"):
        db.write_telemetry_row("cvar_diagnostics", row, mode="replay")
