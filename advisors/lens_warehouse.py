"""
Nightly Lens Data Warehouse.

Append-only SQLite store for lens snapshots produced by the off-hours lens
pipeline.  Kept strictly separate from the state DB (alphabot_state.db) and
the optimization DB — no cross-DB joins, no shared connections.

Public surface
--------------
init_warehouse_db(path=None)
persist_lens_snapshot(lens, symbol, source, available, raw_payload,
                      fetch_ts=None, db_path=None) -> int
get_lens_snapshots(lens, symbol=None, since=None, db_path=None) -> list[dict]

Pytest sentinel
---------------
Calling any public function without an explicit db_path under pytest raises
RuntimeError — mirrors the pattern in database._db_file() for the state DB.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WAREHOUSE_DB_BASENAME = "alphabot_warehouse.db"

# Top-level keys stripped from raw_payload before storage — never persist
# credentials or secrets, even incidentally.
_SECRET_KEY_NAMES: frozenset[str] = frozenset(
    {"api_key", "token", "secret", "password", "Authorization"}
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _warehouse_db_file(path=None) -> str:
    """Resolve the warehouse DB path and enforce the pytest sentinel.

    If *path* is None, resolves to the production basename.  Under pytest
    that resolution raises RuntimeError to prevent accidental production-DB
    writes — tests must always supply an explicit temp path.
    """
    if path is None:
        if "pytest" in sys.modules:
            raise RuntimeError(
                f"test attempted to open the production warehouse DB "
                f"({_WAREHOUSE_DB_BASENAME}) — set db_path to a temp file in tests"
            )
        return _WAREHOUSE_DB_BASENAME
    return str(path)


def _connect(resolved_path: str) -> sqlite3.Connection:
    """Open a WAL-mode connection to the warehouse DB."""
    conn = sqlite3.connect(resolved_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def _strip_secrets(payload: dict) -> dict:
    """Return a shallow copy of *payload* with secret top-level keys removed."""
    return {k: v for k, v in payload.items() if k not in _SECRET_KEY_NAMES}


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS lens_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lens        TEXT    NOT NULL,
    symbol      TEXT,
    fetch_ts    TEXT    NOT NULL,
    source      TEXT    NOT NULL,
    available   INTEGER NOT NULL,
    raw_json    TEXT    NOT NULL,
    created_at  TEXT    DEFAULT (datetime('now'))
)
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_lens_snapshots_lens_symbol_fetch_ts
    ON lens_snapshots (lens, symbol, fetch_ts)
"""

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def init_warehouse_db(path=None) -> None:
    """Create the warehouse DB schema at *path* (idempotent).

    Uses IF NOT EXISTS guards so re-running on an existing DB is safe.
    Under pytest, *path* must be an explicit temp path — omitting it raises
    RuntimeError via the sentinel.
    """
    resolved = _warehouse_db_file(path)
    conn = _connect(resolved)
    try:
        conn.execute(_CREATE_TABLE_SQL)
        conn.execute(_CREATE_INDEX_SQL)
        conn.commit()
    finally:
        conn.close()


def persist_lens_snapshot(
    lens: str,
    symbol: str | None,
    source: str,
    available: bool,
    raw_payload: dict,
    fetch_ts: str | None = None,
    db_path=None,
) -> int:
    """Append a lens snapshot row and return its row id.

    Parameters
    ----------
    lens:        Logical lens name (e.g. "gdelt", "fred_macro").
    symbol:      Ticker symbol, or None for index-level lenses.
    source:      Data source identifier (e.g. "gdelt_timelinetone").
    available:   True when the source returned data; False when down.
    raw_payload: Arbitrary dict — secret top-level keys are stripped before
                 storage; the value is JSON-serialized.
    fetch_ts:    ISO-8601 UTC timestamp string.  Defaults to utcnow() when
                 omitted.
    db_path:     Explicit path to the warehouse DB.  Required under pytest.

    Returns
    -------
    int: The positive SQLite rowid of the newly inserted row.

    D-1 contract: never raises on a valid call.
    """
    resolved = _warehouse_db_file(db_path)
    init_warehouse_db(db_path)

    if fetch_ts is None:
        fetch_ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    cleaned = _strip_secrets(raw_payload)
    raw_json = json.dumps(cleaned)
    available_int = int(bool(available))

    conn = _connect(resolved)
    try:
        cursor = conn.execute(
            """
            INSERT INTO lens_snapshots (lens, symbol, fetch_ts, source, available, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (lens, symbol, fetch_ts, source, available_int, raw_json),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_lens_snapshots(
    lens: str,
    symbol: str | None = None,
    since: str | None = None,
    db_path=None,
) -> list[dict]:
    """Return lens snapshot rows matching the given filters.

    Parameters
    ----------
    lens:    Required — filters to this logical lens name.
    symbol:  When provided, restricts results to this ticker symbol.
             When omitted, all symbols (including NULL-symbol rows) are returned.
    since:   ISO-8601 timestamp string.  When provided, only rows with
             ``fetch_ts >= since`` are returned (inclusive).
    db_path: Explicit path to the warehouse DB.  Required under pytest.

    Returns
    -------
    list[dict]: Rows ordered by fetch_ts ascending; [] when none match.
    """
    resolved = _warehouse_db_file(db_path)
    init_warehouse_db(db_path)

    sql = (
        "SELECT id, lens, symbol, fetch_ts, source, available, raw_json, created_at"
        " FROM lens_snapshots WHERE lens = ?"
    )
    params: list = [lens]

    if symbol is not None:
        sql += " AND symbol = ?"
        params.append(symbol)

    if since is not None:
        sql += " AND fetch_ts >= ?"
        params.append(since)

    sql += " ORDER BY fetch_ts ASC"

    conn = _connect(resolved)
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
