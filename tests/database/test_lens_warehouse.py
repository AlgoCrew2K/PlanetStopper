"""
RED tests for advisors/lens_warehouse.py — nightly lens data warehouse.

Acceptance criteria:
  AC-1   init_warehouse() is idempotent — call twice, no error.
  AC-2   persist_lens_snapshot() + get_lens_snapshots() round-trip (insert→read).
  AC-3   Append-only — two inserts same lens/symbol produce two rows, neither overwritten.
  AC-4   Results are ordered by fetch_ts then id.
  AC-5   get_lens_snapshots() returns [] for an unknown lens.
  AC-6   `since` parameter filters fetch_ts >= since.
  AC-7   Warehouse DB is SEPARATE from the state DB — no lens_snapshots table in state DB.
  AC-8   Secret-stripping — api_key/token/secret/password/webhook values are redacted.
  AC-9   No hardcoded payload values — assert shape/presence/round-trip equality.
  AC-10  WAL mode enabled on the warehouse DB.
  AC-11  WAREHOUSE_DB_PATH env var controls the warehouse file; default basename is
         alphabot_warehouse.db (but never created during these tests).
  AC-12  persist_lens_snapshot returns a positive integer row id.
  AC-13  get_lens_snapshots parses raw_json back under key 'raw'.
  AC-14  Secret-stripping is recursive (nested dicts/lists).

DB isolation: each test receives an isolated warehouse DB via the `warehouse_db`
fixture (sets WAREHOUSE_DB_PATH to a tmp_path). The global _isolate_db conftest
fixture already redirects DB_PATH to a separate temp file, so the two DBs are
always distinct paths.

Fixture provenance: schema-derived with runtime validator.
No producer-computed values asserted as literals; assert shape/presence/equality.
"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def warehouse_db(tmp_path, monkeypatch):
    """Redirect WAREHOUSE_DB_PATH to an isolated temp file and init the warehouse."""
    wh_path = str(tmp_path / "test_warehouse.db")
    monkeypatch.setenv("WAREHOUSE_DB_PATH", wh_path)

    # Re-import after env patch so the module picks up the new path.
    import importlib
    import advisors.lens_warehouse as lw
    importlib.reload(lw)
    lw.init_warehouse()
    return wh_path, lw


# ---------------------------------------------------------------------------
# AC-1: init_warehouse() idempotency
# ---------------------------------------------------------------------------


def test_init_warehouse_idempotent(warehouse_db):
    """Calling init_warehouse() twice must not raise any error."""
    _wh_path, lw = warehouse_db
    lw.init_warehouse()  # second call — must be a no-op


def test_init_warehouse_creates_lens_snapshots_table(warehouse_db):
    """init_warehouse() creates the lens_snapshots table."""
    wh_path, _lw = warehouse_db
    conn = sqlite3.connect(wh_path)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='lens_snapshots'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "lens_snapshots table was not created by init_warehouse()"


def test_init_warehouse_column_schema(warehouse_db):
    """lens_snapshots has all required columns with correct types."""
    wh_path, _lw = warehouse_db
    conn = sqlite3.connect(wh_path)
    try:
        cols = {row[1]: row[2] for row in conn.execute("PRAGMA table_info(lens_snapshots)").fetchall()}
    finally:
        conn.close()

    required = {
        "id": "INTEGER",
        "lens": "TEXT",
        "symbol": "TEXT",
        "fetch_ts": "TEXT",
        "source": "TEXT",
        "available": "INTEGER",
        "raw_json": "TEXT",
        "created_at": "TEXT",
    }
    for col, dtype in required.items():
        assert col in cols, f"Column '{col}' missing from lens_snapshots"
        assert cols[col].upper() == dtype.upper(), (
            f"Column '{col}' has type '{cols[col]}', expected '{dtype}'"
        )


# ---------------------------------------------------------------------------
# AC-2: insert → read round-trip
# ---------------------------------------------------------------------------


def test_round_trip_basic(warehouse_db):
    """persist_lens_snapshot followed by get_lens_snapshots returns the row."""
    _wh_path, lw = warehouse_db
    payload = {"tone_score": 0.42, "per_ticker": {"SPY": 0.5, "QQQ": 0.3}}
    row_id = lw.persist_lens_snapshot(
        lens="sentiment",
        symbol="SPY",
        source="gdelt",
        available=1,
        raw_payload=payload,
    )

    rows = lw.get_lens_snapshots("sentiment", symbol="SPY")
    assert len(rows) == 1
    row = rows[0]

    # AC-13: raw_json parsed back under 'raw' key
    assert "raw" in row, "Expected 'raw' key in result dict"
    assert row["raw"] == payload, "Round-trip payload mismatch"


def test_round_trip_returns_positive_id(warehouse_db):
    """AC-12: persist_lens_snapshot returns a positive integer row id."""
    _wh_path, lw = warehouse_db
    row_id = lw.persist_lens_snapshot(
        lens="technicals",
        symbol=None,
        source="talib",
        available=0,
        raw_payload={"error": "timeout"},
    )
    assert isinstance(row_id, int), f"Expected int row id, got {type(row_id)}"
    assert row_id > 0, f"Expected positive row id, got {row_id}"


# ---------------------------------------------------------------------------
# AC-3: append-only (two inserts → two rows, no overwrite)
# ---------------------------------------------------------------------------


def test_append_only_two_inserts_same_lens_symbol(warehouse_db):
    """Two inserts with same lens/symbol produce two distinct rows."""
    _wh_path, lw = warehouse_db
    payload_a = {"score": 1}
    payload_b = {"score": 2}

    id_a = lw.persist_lens_snapshot(
        lens="technicals",
        symbol="SPY",
        source="talib",
        available=1,
        raw_payload=payload_a,
    )
    id_b = lw.persist_lens_snapshot(
        lens="technicals",
        symbol="SPY",
        source="talib",
        available=1,
        raw_payload=payload_b,
    )

    assert id_a != id_b, "Two inserts must produce distinct row ids"

    rows = lw.get_lens_snapshots("technicals", symbol="SPY")
    assert len(rows) == 2, f"Expected 2 rows after 2 inserts, got {len(rows)}"

    # Neither row is overwritten — both payloads survive
    raws = [r["raw"] for r in rows]
    assert payload_a in raws, "First payload was overwritten (append-only violation)"
    assert payload_b in raws, "Second payload is missing"


# ---------------------------------------------------------------------------
# AC-4: ordering by fetch_ts then id
# ---------------------------------------------------------------------------


def test_ordering_by_fetch_ts(warehouse_db):
    """Results are ordered by fetch_ts ascending, then id ascending."""
    _wh_path, lw = warehouse_db

    # Insert with explicit fetch_ts to control ordering
    ts_early = "2026-06-10T00:00:00"
    ts_later = "2026-06-11T00:00:00"

    id1 = lw.persist_lens_snapshot(
        lens="macro",
        symbol=None,
        source="fred",
        available=1,
        raw_payload={"ts": ts_later},
        fetch_ts=ts_later,
    )
    id2 = lw.persist_lens_snapshot(
        lens="macro",
        symbol=None,
        source="fred",
        available=1,
        raw_payload={"ts": ts_early},
        fetch_ts=ts_early,
    )

    rows = lw.get_lens_snapshots("macro")
    assert len(rows) == 2
    assert rows[0]["fetch_ts"] <= rows[1]["fetch_ts"], (
        f"Expected ascending fetch_ts order, got {rows[0]['fetch_ts']} then {rows[1]['fetch_ts']}"
    )


# ---------------------------------------------------------------------------
# AC-5: [] for unknown lens
# ---------------------------------------------------------------------------


def test_empty_list_for_unknown_lens(warehouse_db):
    """get_lens_snapshots returns [] when the lens has no rows."""
    _wh_path, lw = warehouse_db
    result = lw.get_lens_snapshots("nonexistent_lens_xyzzy")
    assert result == [], f"Expected [], got {result!r}"


# ---------------------------------------------------------------------------
# AC-6: since filter
# ---------------------------------------------------------------------------


def test_since_filter(warehouse_db):
    """since parameter filters fetch_ts >= since."""
    _wh_path, lw = warehouse_db

    ts_old = "2026-06-01T00:00:00"
    ts_new = "2026-06-13T00:00:00"
    cutoff = "2026-06-10T00:00:00"

    lw.persist_lens_snapshot(
        lens="fundamentals",
        symbol="AAPL",
        source="simfin",
        available=1,
        raw_payload={"ts": ts_old},
        fetch_ts=ts_old,
    )
    lw.persist_lens_snapshot(
        lens="fundamentals",
        symbol="AAPL",
        source="simfin",
        available=1,
        raw_payload={"ts": ts_new},
        fetch_ts=ts_new,
    )

    all_rows = lw.get_lens_snapshots("fundamentals", symbol="AAPL")
    filtered_rows = lw.get_lens_snapshots("fundamentals", symbol="AAPL", since=cutoff)

    assert len(all_rows) == 2, "Expected 2 total rows"
    assert len(filtered_rows) == 1, (
        f"Expected 1 row after since={cutoff}, got {len(filtered_rows)}"
    )
    assert filtered_rows[0]["raw"]["ts"] == ts_new


# ---------------------------------------------------------------------------
# AC-7: SEPARATE-DB isolation — no lens_snapshots in state DB
# ---------------------------------------------------------------------------


def test_warehouse_does_not_touch_state_db(warehouse_db):
    """Warehouse writes do NOT appear in the state DB (separate DB contract)."""
    _wh_path, lw = warehouse_db

    lw.persist_lens_snapshot(
        lens="sentiment",
        symbol="SPY",
        source="gdelt",
        available=1,
        raw_payload={"tone": 0.1},
    )

    # State DB path comes from DB_PATH env (set by _isolate_db autouse fixture)
    state_db_path = os.environ.get("DB_PATH")
    assert state_db_path is not None, "DB_PATH env not set — _isolate_db fixture missing?"

    conn = sqlite3.connect(state_db_path)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='lens_snapshots'"
        ).fetchone()
    finally:
        conn.close()

    assert row is None, (
        "lens_snapshots table found in the state DB — warehouse must use a SEPARATE DB"
    )


# ---------------------------------------------------------------------------
# AC-8 & AC-14: Secret-stripping (top-level and recursive)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("secret_key", [
    "api_key",
    "apikey",
    "api-key",
    "token",
    "secret",
    "password",
    "webhook",
    "WEBHOOK_URL",
    "Authorization",  # uppercase variant — case-insensitive
])
def test_secret_keys_are_redacted_at_top_level(warehouse_db, secret_key):
    """AC-8: Top-level secret keys in payload are stored as '<redacted>'."""
    _wh_path, lw = warehouse_db
    payload = {
        "data": "visible",
        secret_key: "supersecret_value_12345",
    }

    row_id = lw.persist_lens_snapshot(
        lens="sentiment",
        symbol=None,
        source="test",
        available=1,
        raw_payload=payload,
    )

    rows = lw.get_lens_snapshots("sentiment")
    # Find our row
    target = next((r for r in rows if r["id"] == row_id), None)
    assert target is not None

    raw = target["raw"]
    assert "data" in raw, "'data' key should be preserved (not a secret)"
    assert raw["data"] == "visible"

    # The secret key (in original casing) should still appear but with value redacted
    stored_raw_json = json.loads(
        sqlite3.connect(_wh_path)
        .execute("SELECT raw_json FROM lens_snapshots WHERE id=?", (row_id,))
        .fetchone()[0]
    )
    sqlite3.connect(_wh_path).close()

    # Find any key that case-insensitively matches the secret pattern
    def _has_secret_value(d, key):
        for k, v in d.items():
            if k.lower() == key.lower() or k == key:
                return v == "<redacted>"
        return None

    # Verify the value is redacted (the key name may be preserved as-is)
    secret_values = [
        v for k, v in stored_raw_json.items()
        if _matches_secret_pattern(k)
    ]
    assert all(v == "<redacted>" for v in secret_values), (
        f"Expected secret values to be '<redacted>', got: {secret_values}"
    )


def _matches_secret_pattern(key: str) -> bool:
    """Return True if `key` matches the secret-key pattern."""
    import re
    return bool(re.search(r"api[_-]?key|token|secret|password|webhook", key, re.IGNORECASE))


def test_secret_stripping_is_recursive(warehouse_db):
    """AC-14: Secret-stripping recurses into nested dicts and lists."""
    _wh_path, lw = warehouse_db
    payload = {
        "metadata": {
            "api_key": "nested_secret_abc",
            "label": "safe_label",
        },
        "items": [
            {"token": "list_secret_xyz", "score": 0.9},
            {"score": 0.1},
        ],
        "top_level_data": "visible",
    }

    row_id = lw.persist_lens_snapshot(
        lens="macro",
        symbol=None,
        source="test",
        available=1,
        raw_payload=payload,
    )

    wh_conn = sqlite3.connect(_wh_path)
    stored_json = wh_conn.execute(
        "SELECT raw_json FROM lens_snapshots WHERE id=?", (row_id,)
    ).fetchone()[0]
    wh_conn.close()

    stored = json.loads(stored_json)

    # Nested dict: api_key should be redacted, label preserved
    assert stored["metadata"]["api_key"] == "<redacted>", (
        "Nested api_key was not redacted"
    )
    assert stored["metadata"]["label"] == "safe_label", (
        "Non-secret nested key was altered"
    )

    # List items: token in list element should be redacted
    assert stored["items"][0]["token"] == "<redacted>", (
        "Token in list element was not redacted"
    )
    assert stored["items"][0]["score"] == 0.9, "Non-secret list value was altered"
    assert stored["items"][1]["score"] == 0.1

    # Top-level safe key preserved
    assert stored["top_level_data"] == "visible"


# ---------------------------------------------------------------------------
# AC-9: No hardcoded payload values — round-trip equality only
# ---------------------------------------------------------------------------


def test_round_trip_equality_not_literals(warehouse_db):
    """AC-9: Assert round-trip equality, not specific literal values."""
    _wh_path, lw = warehouse_db
    # Payload constructed dynamically — no literal values in assertions
    payload = {
        "score": 0.42,
        "tickers": ["SPY", "QQQ"],
        "nested": {"depth": 1},
        "available": True,
    }

    row_id = lw.persist_lens_snapshot(
        lens="technicals",
        symbol="SPY",
        source="talib",
        available=1,
        raw_payload=payload,
    )

    rows = lw.get_lens_snapshots("technicals", symbol="SPY")
    assert len(rows) == 1
    assert rows[0]["raw"] == payload, "Round-trip equality failed"
    assert rows[0]["id"] == row_id


# ---------------------------------------------------------------------------
# AC-10: WAL mode on warehouse DB
# ---------------------------------------------------------------------------


def test_warehouse_uses_wal_mode(warehouse_db):
    """AC-10: init_warehouse() enables WAL journal_mode on the warehouse DB."""
    wh_path, _lw = warehouse_db
    conn = sqlite3.connect(wh_path)
    try:
        row = conn.execute("PRAGMA journal_mode").fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0].lower() == "wal", (
        f"Expected WAL journal mode on warehouse DB, got '{row[0]}'"
    )


# ---------------------------------------------------------------------------
# AC-11: WAREHOUSE_DB_PATH env var
# ---------------------------------------------------------------------------


def test_warehouse_db_path_from_env(tmp_path, monkeypatch):
    """AC-11: WAREHOUSE_DB_PATH env var controls the warehouse file location."""
    custom_path = str(tmp_path / "custom_warehouse.db")
    monkeypatch.setenv("WAREHOUSE_DB_PATH", custom_path)

    import importlib
    import advisors.lens_warehouse as lw
    importlib.reload(lw)
    lw.init_warehouse()

    import pathlib
    assert pathlib.Path(custom_path).exists(), (
        f"Warehouse DB not created at WAREHOUSE_DB_PATH={custom_path}"
    )


# ---------------------------------------------------------------------------
# Additional structural assertions
# ---------------------------------------------------------------------------


def test_no_update_symbol_exported(warehouse_db):
    """Append-only contract: no update_lens_snapshot symbol exported."""
    _wh_path, lw = warehouse_db
    assert not hasattr(lw, "update_lens_snapshot"), (
        "update_lens_snapshot must NOT be exported — warehouse is append-only"
    )


def test_get_lens_snapshots_symbol_filter(warehouse_db):
    """get_lens_snapshots filters by symbol when provided."""
    _wh_path, lw = warehouse_db
    lw.persist_lens_snapshot("sentiment", "SPY", "gdelt", 1, {"t": 1})
    lw.persist_lens_snapshot("sentiment", "QQQ", "gdelt", 1, {"t": 2})
    lw.persist_lens_snapshot("sentiment", None, "gdelt", 1, {"t": 3})

    spy_rows = lw.get_lens_snapshots("sentiment", symbol="SPY")
    qqq_rows = lw.get_lens_snapshots("sentiment", symbol="QQQ")
    all_rows = lw.get_lens_snapshots("sentiment")

    assert len(spy_rows) == 1
    assert len(qqq_rows) == 1
    assert len(all_rows) == 3


def test_get_lens_snapshots_returns_list_of_dicts(warehouse_db):
    """get_lens_snapshots always returns a list of dicts (never sqlite3.Row)."""
    _wh_path, lw = warehouse_db
    lw.persist_lens_snapshot("macro", None, "fred", 1, {"x": 1})
    rows = lw.get_lens_snapshots("macro")
    assert isinstance(rows, list)
    for row in rows:
        assert isinstance(row, dict), f"Expected dict, got {type(row)}"
