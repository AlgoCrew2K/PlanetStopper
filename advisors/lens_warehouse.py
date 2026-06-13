"""Nightly lens data warehouse for Planet Stopper.

Owns a SEPARATE SQLite database (path from env WAREHOUSE_DB_PATH, default
alphabot_warehouse.db) used to accumulate per-lens nightly snapshots.

Design invariants:
  - DISTINCT from the state DB and optimization DB — never cross-joined.
  - Append-only: persist_lens_snapshot only INSERTs, never UPDATEs.
  - WAL mode for concurrent readers.
  - Secret-stripping: any key matching api[_-]?key / token / secret / password
    / webhook (case-insensitive, any nesting depth) has its value replaced with
    "<redacted>" before JSON serialisation.
  - D-1 error contract: errors surface type(exc).__name__ only (never raw str(exc)
    containing potential credentials or stack frames to callers).
  - Off-execution-path: this module is NEVER imported on the 1-minute engine loop.

Public surface:
  init_warehouse()                                       -> None
  persist_lens_snapshot(lens, symbol, source,
                        available, raw_payload,
                        fetch_ts=None)                   -> int   (new row id)
  get_lens_snapshots(lens, symbol=None, since=None)      -> list[dict]
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Warehouse DB path — resolved at call time so env overrides applied in tests
# (via monkeypatch.setenv) are honoured after module import.
_WAREHOUSE_DB_DEFAULT = "alphabot_warehouse.db"

# Regex for secret-key detection — applied case-insensitively at any nesting level.
_SECRET_KEY_PATTERN = re.compile(
    r"api[_-]?key|token|secret|password|webhook",
    re.IGNORECASE,
)

_REDACTED = "<redacted>"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _warehouse_db_path() -> str:
    """Return the warehouse DB file path, honouring WAREHOUSE_DB_PATH env."""
    return os.environ.get("WAREHOUSE_DB_PATH", _WAREHOUSE_DB_DEFAULT)


def _get_connection() -> sqlite3.Connection:
    """Open a writable connection to the warehouse DB."""
    path = _warehouse_db_path()
    return sqlite3.connect(path, timeout=10.0)


def _strip_secrets(obj: Any) -> Any:
    """Recursively replace secret-key values with '<redacted>'.

    Traverses dicts and lists at any nesting depth.  Strings, numbers, and
    other scalar types pass through unchanged.  The replacement is done on a
    deep copy so the caller's original dict is never mutated.
    """
    if isinstance(obj, dict):
        return {
            k: _REDACTED if _SECRET_KEY_PATTERN.search(k) else _strip_secrets(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_strip_secrets(item) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def init_warehouse() -> None:
    """Create the warehouse schema idempotently.

    Safe to call multiple times (CREATE TABLE/INDEX IF NOT EXISTS).
    Enables WAL journal_mode for concurrent Flask reads + single daemon writer.
    """
    conn = _get_connection()
    try:
        cur = conn.cursor()

        # WAL mode — preferred for concurrent reader + single writer.
        # Idempotent: SQLite returns the current mode silently if already set.
        cur.execute("PRAGMA journal_mode=WAL")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS lens_snapshots (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                lens       TEXT    NOT NULL,
                symbol     TEXT    NULL,
                fetch_ts   TEXT    NOT NULL,
                source     TEXT    NOT NULL,
                available  INTEGER NOT NULL,
                raw_json   TEXT    NOT NULL,
                created_at TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # accelerates get_lens_snapshots — ORDER BY fetch_ts, id with lens/symbol filter
        cur.execute("""
            CREATE INDEX IF NOT EXISTS ix_lens_snapshots_lens_symbol_fetch_ts
            ON lens_snapshots (lens, symbol, fetch_ts)
        """)

        conn.commit()
    finally:
        conn.close()


def persist_lens_snapshot(
    lens: str,
    symbol: str | None,
    source: str,
    available: int,
    raw_payload: dict | list,
    fetch_ts: str | None = None,
) -> int:
    """Append one lens snapshot row to the warehouse.

    Parameters
    ----------
    lens:
        Lens identifier (e.g., "sentiment", "technicals", "macro").
    symbol:
        Per-ticker symbol string, or None for market-wide lenses.
    source:
        Data source identifier (e.g., "gdelt", "talib", "fred").
    available:
        1 if data was fetched successfully, 0 if the lens was unavailable.
    raw_payload:
        Dict or list — JSON-serialisable.  Secret keys are stripped before
        storage (see _strip_secrets).
    fetch_ts:
        ISO-format UTC timestamp string.  Defaults to current UTC datetime
        when not supplied.

    Returns
    -------
    int
        The ROWID of the newly inserted row (always > 0).
    """
    if fetch_ts is None:
        fetch_ts = datetime.now(UTC).isoformat()

    # Strip secrets before serialisation — never persist credentials.
    clean_payload = _strip_secrets(raw_payload)
    raw_json = json.dumps(clean_payload, default=str)

    conn = _get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO lens_snapshots (lens, symbol, fetch_ts, source, available, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (lens, symbol, fetch_ts, source, int(available), raw_json),
        )
        conn.commit()
        return cur.lastrowid  # type: ignore[return-value]
    finally:
        conn.close()


def get_lens_snapshots(
    lens: str,
    symbol: str | None = None,
    since: str | None = None,
) -> list[dict]:
    """Return snapshots for `lens`, optionally filtered by symbol and since.

    Parameters
    ----------
    lens:
        Lens identifier to filter on.
    symbol:
        When provided, filter rows to this symbol only.
    since:
        ISO-format UTC timestamp string.  When provided, only rows with
        fetch_ts >= since are returned.

    Returns
    -------
    list[dict]
        Ordered by (fetch_ts ASC, id ASC).  Each dict carries all column
        values plus a 'raw' key holding the deserialised raw_json object.
        Returns [] when no rows match.
    """
    clauses: list[str] = ["lens = ?"]
    params: list[Any] = [lens]

    if symbol is not None:
        clauses.append("symbol = ?")
        params.append(symbol)

    if since is not None:
        clauses.append("fetch_ts >= ?")
        params.append(since)

    where = " AND ".join(clauses)
    sql = f"""
        SELECT id, lens, symbol, fetch_ts, source, available, raw_json, created_at
        FROM lens_snapshots
        WHERE {where}
        ORDER BY fetch_ts ASC, id ASC
    """  # noqa: S608 — parameterized; no user-interpolated identifiers

    conn = _get_connection()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    result: list[dict] = []
    for row in rows:
        d = dict(row)
        d["raw"] = json.loads(d["raw_json"])
        result.append(d)

    return result
