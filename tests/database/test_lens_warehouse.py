"""
RED tests for the Nightly Lens Data Warehouse.

Coverage (all tests MUST FAIL until GREEN implementation lands):

  AC-1  Warehouse DB init + schema:
    1. `advisors/lens_warehouse.py` is importable as a module.
    2. `init_warehouse_db(path)` creates an `alphabot_warehouse.db`-compatible
       SQLite file at the given path.
    3. The `lens_snapshots` table exists after init.
    4. `lens_snapshots` has all required columns:
       id, lens, symbol, fetch_ts, source, available, raw_json, created_at.
    5. An index on (lens, symbol, fetch_ts) exists after init.
    6. `init_warehouse_db` is idempotent — calling it twice on the same path
       must not raise.

  AC-2  `persist_lens_snapshot` accessor:
    7. `persist_lens_snapshot` is exported from `advisors.lens_warehouse`.
    8. It returns a positive integer row id.
    9. The returned id matches the actual SQLite rowid (query-verified).
    10. The stored `raw_json` is a JSON-deserializable string.
    11. Re-inserting the exact same payload appends a NEW row (never overwrites
        the prior one — the append-only contract).
    12. The `available` column stores the boolean as an integer (0 or 1).
    13. A `symbol=None` payload is stored with NULL in the symbol column.
    14. `fetch_ts` is stored verbatim (ISO UTC string round-trips).
    15. Secret keys are stripped from `raw_payload` before storage:
        known secret key names (api_key, token, secret, password, Authorization)
        MUST NOT appear as top-level keys in the stored `raw_json`.
    15b. Secret-key stripping is RECURSIVE — a nested credential (e.g.
        {"headers": {"Authorization": "Bearer sk-xxx"}}) must also be removed.
        Shallow-only stripping is insufficient.
    16. Non-secret keys survive the strip (payload content is preserved minus
        the secret keys — spot-checked by asserting a known non-secret key is
        present in the stored JSON).
    17. `persist_lens_snapshot` never raises on a valid call — D-1 contract.

  AC-3  `get_lens_snapshots` accessor:
    18. `get_lens_snapshots` is exported from `advisors.lens_warehouse`.
    19. Returns `[]` when no rows match (unknown lens, no raise).
    20. Returns a list of dicts.
    21. Each dict contains at minimum:
        id, lens, symbol, fetch_ts, source, available, raw_json, created_at.
    22. Results are ordered by `fetch_ts` ascending.
    23. `symbol=None` filter: when called without `symbol`, returns rows for
        ALL symbols under that lens (including NULL-symbol rows).
    24. `symbol=<str>` filter: returns only rows matching the given symbol.
    25. `since=<iso_ts>` filter: returns only rows with `fetch_ts >= since`.
    26. `since` filter excludes rows strictly older than the threshold.

  AC-4  Separate-DB isolation:
    27. The warehouse DB module does NOT import from `database` at module level
        (no cross-DB coupling at import time).
    28. The warehouse module does NOT call `database.get_connection()` or
        `database.init_db()` anywhere.
    29. `persist_lens_snapshot` and `get_lens_snapshots` open the warehouse path
        directly, not the state DB.

  AC-5  Pytest sentinel + separate-DB isolation at test time:
    30. Tests use a TEMP warehouse path — the module's sentinel function
        `_warehouse_db_file(path=None)` MUST NOT default to a path whose
        basename is `alphabot_state.db` (the state-DB sentinel would catch it).
    31. Under pytest, calling `init_warehouse_db` with no path argument raises
        RuntimeError when `"pytest" in sys.modules` AND the resolved path's
        basename equals `alphabot_warehouse.db` (mirrors state-DB sentinel
        pattern for the warehouse).

  AC-6  Append-only safety + honest availability:
    32. Re-running `persist_lens_snapshot` twice with the same arguments inserts
        TWO rows (dedupe happens at read, never at write — append semantics).
    33. `get_lens_snapshots` returns the most-recent row when two rows share the
        same (lens, symbol) but different `fetch_ts` — both are present.
    34. Storing `available=0` (source down) is accepted without error; the stored
        row has `available == 0`.
    35. A row with `available=0` stores the reason payload verbatim in `raw_json`
        — no fabricated data is substituted.
    36. No `update_lens_snapshot` symbol is exported from `advisors.lens_warehouse`
        (append-only contract: no update path).
    37. No `delete_lens_snapshot` symbol is exported (append-only contract: no
        delete path).

DB isolation: each test receives a fresh temp warehouse path via the
`warehouse_db_path` fixture — never the production `alphabot_warehouse.db`.
The global conftest._isolate_db autouse fixture handles the state DB; this
module provides its own warehouse isolation fixture.

Fixture provenance: schema-derived, shape/presence assertions only.
No hardcoded producer-computed values (rates, prices, percents, floats).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import the module under test (fails until stub exists)
# ---------------------------------------------------------------------------
import advisors.lens_warehouse as wh_module

# ---------------------------------------------------------------------------
# Constants mirroring the expected schema
# ---------------------------------------------------------------------------

_REQUIRED_COLUMNS = {
    "id",
    "lens",
    "symbol",
    "fetch_ts",
    "source",
    "available",
    "raw_json",
    "created_at",
}

# Known secret key names that MUST be stripped from payloads before storage.
_SECRET_KEY_NAMES = ["api_key", "token", "secret", "password", "Authorization"]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def warehouse_db_path(tmp_path) -> Path:
    """Return a temp path for the warehouse DB — never the production file."""
    return tmp_path / "test_alphabot_warehouse.db"


@pytest.fixture()
def initialized_warehouse(warehouse_db_path) -> Path:
    """Return a path to a fully-initialized warehouse DB."""
    wh_module.init_warehouse_db(warehouse_db_path)
    return warehouse_db_path


# ---------------------------------------------------------------------------
# Helper functions (test-internal only — no production-code logic here)
# ---------------------------------------------------------------------------


def _open_wh(path) -> sqlite3.Connection:
    """Open the warehouse DB for direct inspection in assertions."""
    return sqlite3.connect(str(path))


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _column_names(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def _index_names_for_table(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
        (table_name,),
    ).fetchall()
    return {row[0] for row in rows}


def _index_covers_columns(conn: sqlite3.Connection, index_name: str, columns: list[str]) -> bool:
    """Return True if the index covers ALL of the named columns."""
    rows = conn.execute(f"PRAGMA index_info({index_name})").fetchall()
    indexed_cols = {row[2] for row in rows}
    return all(col in indexed_cols for col in columns)


# ---------------------------------------------------------------------------
# AC-1: Warehouse DB init + schema
# ---------------------------------------------------------------------------


class TestWarehouseInit:
    def test_module_is_importable(self):
        # The module must exist and be importable — stub satisfies this.
        assert wh_module is not None, "advisors.lens_warehouse must be importable"

    def test_init_warehouse_db_is_callable(self):
        assert callable(getattr(wh_module, "init_warehouse_db", None)), (
            "advisors.lens_warehouse must export init_warehouse_db"
        )

    def test_init_creates_sqlite_file(self, warehouse_db_path):
        wh_module.init_warehouse_db(warehouse_db_path)
        assert warehouse_db_path.exists(), (
            f"init_warehouse_db must create the DB file at {warehouse_db_path}"
        )

    def test_lens_snapshots_table_exists_after_init(self, warehouse_db_path):
        wh_module.init_warehouse_db(warehouse_db_path)
        conn = _open_wh(warehouse_db_path)
        try:
            assert _table_exists(conn, "lens_snapshots"), (
                "lens_snapshots table must exist after init_warehouse_db()"
            )
        finally:
            conn.close()

    def test_lens_snapshots_has_required_columns(self, warehouse_db_path):
        wh_module.init_warehouse_db(warehouse_db_path)
        conn = _open_wh(warehouse_db_path)
        try:
            cols = _column_names(conn, "lens_snapshots")
            missing = _REQUIRED_COLUMNS - cols
            assert not missing, f"lens_snapshots is missing columns: {sorted(missing)}"
        finally:
            conn.close()

    def test_index_on_lens_symbol_fetch_ts_exists(self, warehouse_db_path):
        wh_module.init_warehouse_db(warehouse_db_path)
        conn = _open_wh(warehouse_db_path)
        try:
            indexes = _index_names_for_table(conn, "lens_snapshots")
            # At least one index must cover all three columns.
            covering = [
                idx
                for idx in indexes
                if _index_covers_columns(conn, idx, ["lens", "symbol", "fetch_ts"])
            ]
            assert covering, (
                "lens_snapshots must have at least one index covering "
                "(lens, symbol, fetch_ts); "
                f"found indexes: {indexes}"
            )
        finally:
            conn.close()

    def test_init_warehouse_db_is_idempotent(self, warehouse_db_path):
        # Calling init twice must not raise — IF NOT EXISTS pattern required.
        wh_module.init_warehouse_db(warehouse_db_path)
        wh_module.init_warehouse_db(warehouse_db_path)  # second call — must be silent


# ---------------------------------------------------------------------------
# AC-2: persist_lens_snapshot
# ---------------------------------------------------------------------------


class TestPersistLensSnapshot:
    def test_persist_lens_snapshot_is_exported(self):
        assert callable(getattr(wh_module, "persist_lens_snapshot", None)), (
            "advisors.lens_warehouse must export persist_lens_snapshot"
        )

    def test_returns_positive_int(self, initialized_warehouse):
        row_id = wh_module.persist_lens_snapshot(
            db_path=initialized_warehouse,
            lens="gdelt",
            symbol="SPY",
            source="gdelt_timelinetone",
            available=True,
            raw_payload={"tone": 0.5, "data_points": 3},
        )
        assert isinstance(row_id, int), (
            f"persist_lens_snapshot must return int; got {type(row_id).__name__}"
        )
        assert row_id > 0, f"persist_lens_snapshot must return a positive row id; got {row_id}"

    def test_returned_id_matches_actual_rowid(self, initialized_warehouse):
        row_id = wh_module.persist_lens_snapshot(
            db_path=initialized_warehouse,
            lens="fred_macro",
            symbol=None,
            source="fred_api",
            available=True,
            raw_payload={"series_count": 5, "status": "ok"},
        )
        conn = _open_wh(initialized_warehouse)
        try:
            row = conn.execute("SELECT id FROM lens_snapshots WHERE id = ?", (row_id,)).fetchone()
        finally:
            conn.close()
        assert row is not None, f"Row id={row_id} returned by persist_lens_snapshot not found in DB"
        assert row[0] == row_id

    def test_raw_json_is_json_deserializable(self, initialized_warehouse):
        payload = {"available": True, "count": 7, "nested": {"k": "v"}}
        row_id = wh_module.persist_lens_snapshot(
            db_path=initialized_warehouse,
            lens="gdelt",
            symbol="QQQ",
            source="gdelt_timelinetone",
            available=True,
            raw_payload=payload,
        )
        conn = _open_wh(initialized_warehouse)
        try:
            row = conn.execute(
                "SELECT raw_json FROM lens_snapshots WHERE id = ?", (row_id,)
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        raw = row[0]
        assert isinstance(raw, str), "raw_json must be stored as a string"
        parsed = json.loads(raw)  # must not raise
        assert isinstance(parsed, dict), "raw_json must deserialize to a dict"

    def test_append_only_re_insert_creates_new_row(self, initialized_warehouse):
        # Two identical calls must produce TWO distinct rows — never an upsert.
        id1 = wh_module.persist_lens_snapshot(
            db_path=initialized_warehouse,
            lens="gdelt",
            symbol="SPY",
            source="gdelt_timelinetone",
            available=True,
            raw_payload={"run": "first"},
        )
        id2 = wh_module.persist_lens_snapshot(
            db_path=initialized_warehouse,
            lens="gdelt",
            symbol="SPY",
            source="gdelt_timelinetone",
            available=True,
            raw_payload={"run": "first"},
        )
        assert id2 != id1, (
            "persist_lens_snapshot must append a new row on re-insert; "
            f"both calls returned id={id1} (upsert violation)"
        )
        conn = _open_wh(initialized_warehouse)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM lens_snapshots WHERE lens=? AND symbol=?",
                ("gdelt", "SPY"),
            ).fetchone()[0]
        finally:
            conn.close()
        assert count == 2, (
            f"Expected 2 rows after two inserts; found {count} (upsert/overwrite detected)"
        )

    def test_available_stored_as_integer(self, initialized_warehouse):
        row_id = wh_module.persist_lens_snapshot(
            db_path=initialized_warehouse,
            lens="fred_macro",
            symbol=None,
            source="fred_api",
            available=True,
            raw_payload={"ok": True},
        )
        conn = _open_wh(initialized_warehouse)
        try:
            row = conn.execute(
                "SELECT available FROM lens_snapshots WHERE id=?", (row_id,)
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        stored = row[0]
        assert isinstance(stored, int), (
            f"available must be stored as int (0/1); got {type(stored).__name__!r}"
        )
        assert stored in (0, 1), f"available must be 0 or 1; got {stored}"

    def test_null_symbol_stored_as_null(self, initialized_warehouse):
        row_id = wh_module.persist_lens_snapshot(
            db_path=initialized_warehouse,
            lens="fred_macro",
            symbol=None,
            source="fred_api",
            available=True,
            raw_payload={"macro": "data"},
        )
        conn = _open_wh(initialized_warehouse)
        try:
            row = conn.execute("SELECT symbol FROM lens_snapshots WHERE id=?", (row_id,)).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[0] is None, f"symbol=None must be stored as SQL NULL; got {row[0]!r}"

    def test_fetch_ts_stored_verbatim(self, initialized_warehouse):
        ts = "2026-06-16T03:00:00Z"
        row_id = wh_module.persist_lens_snapshot(
            db_path=initialized_warehouse,
            lens="gdelt",
            symbol="SPY",
            source="gdelt_timelinetone",
            available=True,
            raw_payload={"items": 1},
            fetch_ts=ts,
        )
        conn = _open_wh(initialized_warehouse)
        try:
            row = conn.execute(
                "SELECT fetch_ts FROM lens_snapshots WHERE id=?", (row_id,)
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[0] == ts, f"fetch_ts not stored verbatim; expected {ts!r}, got {row[0]!r}"

    @pytest.mark.parametrize("secret_key", _SECRET_KEY_NAMES)
    def test_secret_key_stripped_from_stored_json(self, initialized_warehouse, secret_key):
        # A payload containing a secret key must have that key removed in storage.
        payload = {secret_key: "SUPERSECRET_VALUE", "safe_key": "visible_data"}
        row_id = wh_module.persist_lens_snapshot(
            db_path=initialized_warehouse,
            lens="options",
            symbol="SPY",
            source="options_proxy",
            available=True,
            raw_payload=payload,
        )
        conn = _open_wh(initialized_warehouse)
        try:
            row = conn.execute(
                "SELECT raw_json FROM lens_snapshots WHERE id=?", (row_id,)
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        stored = json.loads(row[0])
        assert secret_key not in stored, (
            f"Secret key {secret_key!r} must be stripped before storage; "
            f"found in stored JSON keys: {sorted(stored)}"
        )

    def test_non_secret_keys_preserved_in_stored_json(self, initialized_warehouse):
        # Non-secret payload content must survive the strip.
        payload = {
            "api_key": "STRIP_ME",
            "tone_score": 0.42,
            "source_count": 3,
            "status": "ok",
        }
        row_id = wh_module.persist_lens_snapshot(
            db_path=initialized_warehouse,
            lens="gdelt",
            symbol="SPY",
            source="gdelt_timelinetone",
            available=True,
            raw_payload=payload,
        )
        conn = _open_wh(initialized_warehouse)
        try:
            row = conn.execute(
                "SELECT raw_json FROM lens_snapshots WHERE id=?", (row_id,)
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        stored = json.loads(row[0])
        # These non-secret keys must survive.
        for key in ("tone_score", "source_count", "status"):
            assert key in stored, (
                f"Non-secret key {key!r} must be preserved in stored JSON; "
                f"got keys: {sorted(stored)}"
            )

    @pytest.mark.parametrize(
        "nested_payload,secret_key,path_desc",
        [
            (
                {"headers": {"Authorization": "Bearer sk-live-XXXX"}, "safe": "ok"},
                "Authorization",
                "headers.Authorization",
            ),
            (
                {"meta": {"api_key": "sk-very-secret"}, "data": [1, 2, 3]},
                "api_key",
                "meta.api_key",
            ),
            (
                {"config": {"token": "tok_XXXX", "retry": 3}},
                "token",
                "config.token",
            ),
        ],
        ids=["nested_authorization", "nested_api_key", "nested_token"],
    )
    def test_nested_secret_key_stripped_from_stored_json(
        self, initialized_warehouse, nested_payload, secret_key, path_desc
    ):
        # Secret keys MUST be stripped recursively — a nested credential (e.g.
        # {"headers": {"Authorization": "Bearer sk-xxx"}}) must not survive in
        # the stored raw_json.  Shallow-only stripping fails this test.
        row_id = wh_module.persist_lens_snapshot(
            db_path=initialized_warehouse,
            lens="options",
            symbol="SPY",
            source="options_proxy",
            available=True,
            raw_payload=nested_payload,
        )
        conn = _open_wh(initialized_warehouse)
        try:
            row = conn.execute(
                "SELECT raw_json FROM lens_snapshots WHERE id=?", (row_id,)
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        stored = json.loads(row[0])

        def _find_key_recursive(obj, target_key):
            """Return True if target_key appears anywhere in the nested structure."""
            if isinstance(obj, dict):
                if target_key in obj:
                    return True
                return any(_find_key_recursive(v, target_key) for v in obj.values())
            if isinstance(obj, list):
                return any(_find_key_recursive(item, target_key) for item in obj)
            return False

        assert not _find_key_recursive(stored, secret_key), (
            f"Secret key {secret_key!r} found at nested path {path_desc!r} "
            f"in stored raw_json — strip must be recursive, not top-level only. "
            f"Stored keys (top-level): {sorted(stored)}"
        )

    def test_persist_never_raises_on_valid_call(self, initialized_warehouse):
        # D-1 contract: no exception escapes on a valid call.
        try:
            wh_module.persist_lens_snapshot(
                db_path=initialized_warehouse,
                lens="gdelt",
                symbol="IWM",
                source="gdelt_timelinetone",
                available=False,
                raw_payload={"reason": "no_data"},
            )
        except Exception as exc:
            pytest.fail(f"persist_lens_snapshot raised unexpectedly: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# AC-3: get_lens_snapshots
# ---------------------------------------------------------------------------


class TestGetLensSnapshots:
    def test_get_lens_snapshots_is_exported(self):
        assert callable(getattr(wh_module, "get_lens_snapshots", None)), (
            "advisors.lens_warehouse must export get_lens_snapshots"
        )

    def test_returns_empty_list_for_unknown_lens(self, initialized_warehouse):
        result = wh_module.get_lens_snapshots(
            db_path=initialized_warehouse, lens="nonexistent_lens"
        )
        assert result == [], "get_lens_snapshots must return [] for an unknown lens (no raise)"

    def test_returns_list_of_dicts(self, initialized_warehouse):
        wh_module.persist_lens_snapshot(
            db_path=initialized_warehouse,
            lens="gdelt",
            symbol="SPY",
            source="gdelt_timelinetone",
            available=True,
            raw_payload={"items": 2},
        )
        result = wh_module.get_lens_snapshots(db_path=initialized_warehouse, lens="gdelt")
        assert isinstance(result, list), "get_lens_snapshots must return a list"
        assert len(result) >= 1
        assert isinstance(result[0], dict), "Each entry must be a dict"

    def test_returned_dicts_have_required_keys(self, initialized_warehouse):
        wh_module.persist_lens_snapshot(
            db_path=initialized_warehouse,
            lens="fred_macro",
            symbol=None,
            source="fred_api",
            available=True,
            raw_payload={"ok": True},
        )
        result = wh_module.get_lens_snapshots(db_path=initialized_warehouse, lens="fred_macro")
        assert result, "Expected at least one entry"
        entry = result[0]
        missing = _REQUIRED_COLUMNS - set(entry.keys())
        assert not missing, (
            f"Returned dict is missing keys: {sorted(missing)}; got: {sorted(entry)}"
        )

    def test_ordered_by_fetch_ts_ascending(self, initialized_warehouse):
        # Insert 3 rows with distinct fetch_ts values; results must be ordered asc.
        timestamps = [
            "2026-06-14T03:00:00Z",
            "2026-06-15T03:00:00Z",
            "2026-06-16T03:00:00Z",
        ]
        for ts in timestamps:
            wh_module.persist_lens_snapshot(
                db_path=initialized_warehouse,
                lens="gdelt",
                symbol="SPY",
                source="gdelt_timelinetone",
                available=True,
                raw_payload={"day": ts},
                fetch_ts=ts,
            )
        result = wh_module.get_lens_snapshots(
            db_path=initialized_warehouse, lens="gdelt", symbol="SPY"
        )
        assert len(result) == 3, f"Expected 3 rows; got {len(result)}"
        returned_ts = [r["fetch_ts"] for r in result]
        assert returned_ts == sorted(returned_ts), (
            f"Results not ordered by fetch_ts ascending; got: {returned_ts}"
        )

    def test_no_symbol_filter_returns_all_symbols(self, initialized_warehouse):
        # Without symbol filter, rows for ALL symbols (incl. NULL) are returned.
        for sym in ("SPY", "QQQ", None):
            wh_module.persist_lens_snapshot(
                db_path=initialized_warehouse,
                lens="gdelt",
                symbol=sym,
                source="gdelt_timelinetone",
                available=True,
                raw_payload={"sym": str(sym)},
            )
        result = wh_module.get_lens_snapshots(db_path=initialized_warehouse, lens="gdelt")
        assert len(result) == 3, (
            f"Without symbol filter, all 3 rows should be returned; got {len(result)}"
        )

    def test_symbol_filter_limits_to_matching_symbol(self, initialized_warehouse):
        for sym in ("SPY", "QQQ"):
            wh_module.persist_lens_snapshot(
                db_path=initialized_warehouse,
                lens="gdelt",
                symbol=sym,
                source="gdelt_timelinetone",
                available=True,
                raw_payload={"sym": sym},
            )
        result = wh_module.get_lens_snapshots(
            db_path=initialized_warehouse, lens="gdelt", symbol="SPY"
        )
        assert len(result) == 1, f"symbol='SPY' filter should return 1 row; got {len(result)}"
        assert result[0]["symbol"] == "SPY"

    def test_since_filter_includes_rows_at_and_after_threshold(self, initialized_warehouse):
        old_ts = "2026-06-01T03:00:00Z"
        new_ts = "2026-06-16T03:00:00Z"
        for ts in (old_ts, new_ts):
            wh_module.persist_lens_snapshot(
                db_path=initialized_warehouse,
                lens="fred_macro",
                symbol=None,
                source="fred_api",
                available=True,
                raw_payload={"ts": ts},
                fetch_ts=ts,
            )
        result = wh_module.get_lens_snapshots(
            db_path=initialized_warehouse,
            lens="fred_macro",
            since=new_ts,
        )
        returned_ts = [r["fetch_ts"] for r in result]
        assert new_ts in returned_ts, (
            f"since={new_ts!r}: the matching row must be included; got {returned_ts}"
        )

    def test_since_filter_excludes_rows_before_threshold(self, initialized_warehouse):
        old_ts = "2026-06-01T03:00:00Z"
        new_ts = "2026-06-16T03:00:00Z"
        for ts in (old_ts, new_ts):
            wh_module.persist_lens_snapshot(
                db_path=initialized_warehouse,
                lens="fred_macro",
                symbol=None,
                source="fred_api",
                available=True,
                raw_payload={"ts": ts},
                fetch_ts=ts,
            )
        result = wh_module.get_lens_snapshots(
            db_path=initialized_warehouse,
            lens="fred_macro",
            since=new_ts,
        )
        returned_ts = [r["fetch_ts"] for r in result]
        assert old_ts not in returned_ts, (
            f"since={new_ts!r}: the old row must be excluded; got {returned_ts}"
        )


# ---------------------------------------------------------------------------
# AC-4: Separate-DB isolation (no cross-module dependency)
# ---------------------------------------------------------------------------


class TestSeparateDbIsolation:
    def test_module_does_not_import_database_at_module_level(self):
        # Inspect the source to confirm `import database` or `from database`
        # does NOT appear at module level (outside function bodies).
        # We verify by checking that `database` is NOT in the module's direct
        # imports (i.e., it's not an attribute set at module load time by a top-
        # level import statement).
        # Strategy: if `database` was imported at module level, it would be
        # accessible as `wh_module.database` (Python sets the name in the module
        # namespace on `import database`).
        assert not hasattr(wh_module, "database"), (
            "lens_warehouse must NOT import `database` at module level — "
            "no cross-DB coupling; found `wh_module.database` attribute"
        )

    def test_module_does_not_import_database_as_db(self):
        # Guard the `import database as db` variant.
        assert not hasattr(wh_module, "db"), (
            "lens_warehouse must NOT import `database` at module level — "
            "found `wh_module.db` attribute (likely `import database as db`)"
        )

    def test_no_get_connection_reference_in_warehouse_source(self):
        # Read the source file and assert it does not call database.get_connection
        # or database.init_db (the state-DB accessors).
        src_path = Path(wh_module.__file__)
        src = src_path.read_text(encoding="utf-8")
        # We check for the direct call patterns — not the word "database" in
        # comments, but the function-call pattern that would cross DB boundaries.
        assert "database.get_connection" not in src, (
            "lens_warehouse must NOT call database.get_connection() — "
            "warehouse uses its own SQLite connection"
        )
        assert "database.init_db" not in src, (
            "lens_warehouse must NOT call database.init_db() — "
            "warehouse manages its own schema via init_warehouse_db()"
        )

    def test_persist_uses_warehouse_path_not_state_db(self, initialized_warehouse, tmp_path):
        # After a persist call, the state DB (at a separate path) must NOT
        # contain a lens_snapshots table — writes went to the warehouse path only.
        state_db_path = tmp_path / "check_state.db"
        # Connect to the state DB path — if warehouse used it, it would have
        # a lens_snapshots table. It must not.
        wh_module.persist_lens_snapshot(
            db_path=initialized_warehouse,
            lens="gdelt",
            symbol="SPY",
            source="gdelt_timelinetone",
            available=True,
            raw_payload={"items": 1},
        )
        # The state-DB path was never touched by the persist call.
        assert not state_db_path.exists(), (
            "persist_lens_snapshot must write to the warehouse path only, not to any other DB path"
        )


# ---------------------------------------------------------------------------
# AC-5: Pytest sentinel — warehouse DB path protection
# ---------------------------------------------------------------------------


class TestWarehouseSentinel:
    def test_temp_fixture_path_is_not_alphabot_warehouse(self, warehouse_db_path):
        # The fixture path must not resolve to the production file basename.
        assert warehouse_db_path.name != "alphabot_warehouse.db", (
            "Test fixture must not use the production warehouse DB basename"
        )

    def test_init_without_path_raises_under_pytest(self):
        # Under pytest, calling init_warehouse_db() with no path (defaults to
        # the production alphabot_warehouse.db) MUST raise RuntimeError.
        # This mirrors the state-DB sentinel in database._db_file().
        assert "pytest" in sys.modules, "This test must run under pytest"
        with pytest.raises(RuntimeError, match="alphabot_warehouse"):
            wh_module.init_warehouse_db()  # no path = production path = sentinel fires

    def test_persist_without_path_raises_under_pytest(self):
        # Same sentinel applies to persist_lens_snapshot with no db_path.
        assert "pytest" in sys.modules
        with pytest.raises(RuntimeError, match="alphabot_warehouse"):
            wh_module.persist_lens_snapshot(
                lens="gdelt",
                symbol="SPY",
                source="gdelt_timelinetone",
                available=True,
                raw_payload={"items": 1},
            )

    def test_get_without_path_raises_under_pytest(self):
        # Same sentinel applies to get_lens_snapshots with no db_path.
        assert "pytest" in sys.modules
        with pytest.raises(RuntimeError, match="alphabot_warehouse"):
            wh_module.get_lens_snapshots(lens="gdelt")


# ---------------------------------------------------------------------------
# AC-6: Append-only safety + honest availability
# ---------------------------------------------------------------------------


class TestAppendOnlyAndHonestAvailability:
    def test_two_inserts_create_two_rows(self, initialized_warehouse):
        # Re-running persist with the same (lens, symbol) arguments appends,
        # never updates.
        id1 = wh_module.persist_lens_snapshot(
            db_path=initialized_warehouse,
            lens="gdelt",
            symbol="SPY",
            source="gdelt_timelinetone",
            available=True,
            raw_payload={"run": 1},
        )
        id2 = wh_module.persist_lens_snapshot(
            db_path=initialized_warehouse,
            lens="gdelt",
            symbol="SPY",
            source="gdelt_timelinetone",
            available=True,
            raw_payload={"run": 2},
        )
        conn = _open_wh(initialized_warehouse)
        try:
            rows = conn.execute(
                "SELECT id FROM lens_snapshots WHERE lens=? AND symbol=? ORDER BY id",
                ("gdelt", "SPY"),
            ).fetchall()
        finally:
            conn.close()
        ids = [r[0] for r in rows]
        assert len(ids) == 2, f"Expected 2 rows; got {len(ids)}: {ids}"
        assert id1 in ids and id2 in ids

    def test_both_rows_present_with_different_fetch_ts(self, initialized_warehouse):
        # Two rows with the same (lens, symbol) but different fetch_ts must
        # BOTH be returned by get_lens_snapshots (dedupe at read is done by caller,
        # not by the warehouse).
        ts1 = "2026-06-15T03:00:00Z"
        ts2 = "2026-06-16T03:00:00Z"
        wh_module.persist_lens_snapshot(
            db_path=initialized_warehouse,
            lens="gdelt",
            symbol="SPY",
            source="gdelt_timelinetone",
            available=True,
            raw_payload={"night": 1},
            fetch_ts=ts1,
        )
        wh_module.persist_lens_snapshot(
            db_path=initialized_warehouse,
            lens="gdelt",
            symbol="SPY",
            source="gdelt_timelinetone",
            available=True,
            raw_payload={"night": 2},
            fetch_ts=ts2,
        )
        result = wh_module.get_lens_snapshots(
            db_path=initialized_warehouse, lens="gdelt", symbol="SPY"
        )
        returned_ts = [r["fetch_ts"] for r in result]
        assert ts1 in returned_ts and ts2 in returned_ts, (
            f"Both fetch_ts values must be present; got {returned_ts}"
        )

    def test_available_false_row_accepted_without_error(self, initialized_warehouse):
        # available=0 (source down) must not raise — this is the honest-availability path.
        try:
            row_id = wh_module.persist_lens_snapshot(
                db_path=initialized_warehouse,
                lens="gdelt",
                symbol="SPY",
                source="gdelt_timelinetone",
                available=False,
                raw_payload={"reason": "ConnectionError"},
            )
        except Exception as exc:
            pytest.fail(
                f"persist_lens_snapshot with available=False raised: {type(exc).__name__}: {exc}"
            )
        assert isinstance(row_id, int) and row_id > 0

    def test_available_false_stored_as_zero(self, initialized_warehouse):
        row_id = wh_module.persist_lens_snapshot(
            db_path=initialized_warehouse,
            lens="gdelt",
            symbol="SPY",
            source="gdelt_timelinetone",
            available=False,
            raw_payload={"reason": "timeout"},
        )
        conn = _open_wh(initialized_warehouse)
        try:
            row = conn.execute(
                "SELECT available FROM lens_snapshots WHERE id=?", (row_id,)
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[0] == 0, f"available=False must be stored as integer 0; got {row[0]!r}"

    def test_available_false_payload_stored_verbatim(self, initialized_warehouse):
        # For a down source, the reason payload must be stored as-is — no
        # fabricated data substituted in place of a real payload.
        reason_payload = {"reason": "ConnectionError", "source_down": True}
        row_id = wh_module.persist_lens_snapshot(
            db_path=initialized_warehouse,
            lens="fred_macro",
            symbol=None,
            source="fred_api",
            available=False,
            raw_payload=reason_payload,
        )
        conn = _open_wh(initialized_warehouse)
        try:
            row = conn.execute(
                "SELECT raw_json FROM lens_snapshots WHERE id=?", (row_id,)
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        stored = json.loads(row[0])
        assert stored.get("reason") == "ConnectionError", (
            f"Reason string must be stored verbatim in raw_json; got: {stored!r}"
        )
        assert "source_down" in stored, "Payload keys must be preserved for an available=False row"

    def test_no_update_lens_snapshot_exported(self):
        update_symbols = [
            name for name in dir(wh_module) if name.startswith("update_lens_snapshot")
        ]
        assert not update_symbols, (
            f"lens_warehouse must be append-only; found update symbols: {update_symbols}"
        )

    def test_no_delete_lens_snapshot_exported(self):
        delete_symbols = [
            name for name in dir(wh_module) if name.startswith("delete_lens_snapshot")
        ]
        assert not delete_symbols, (
            f"lens_warehouse must be append-only; found delete symbols: {delete_symbols}"
        )

    @pytest.mark.parametrize(
        "content",
        [
            "'; DROP TABLE lens_snapshots; --",
            '{"inject": "SELECT * FROM lens_snapshots"}',
            "He said \"hello\"; she said 'goodbye'",
        ],
        ids=["sql_injection", "json_injection", "mixed_quotes"],
    )
    def test_sql_injection_shaped_payload_stored_verbatim(self, initialized_warehouse, content):
        # Parameterized writes: injection-shaped strings must round-trip safely.
        payload = {"dangerous_input": content, "safe_key": "ok"}
        row_id = wh_module.persist_lens_snapshot(
            db_path=initialized_warehouse,
            lens="gdelt",
            symbol="SPY",
            source="gdelt_timelinetone",
            available=True,
            raw_payload=payload,
        )
        result = wh_module.get_lens_snapshots(
            db_path=initialized_warehouse, lens="gdelt", symbol="SPY"
        )
        matching = [r for r in result if r["id"] == row_id]
        assert matching, f"Row id={row_id} not found in get result"
        stored = json.loads(matching[0]["raw_json"])
        assert stored.get("dangerous_input") == content, (
            f"Injection-shaped content was not stored verbatim.\n"
            f"  Expected: {content!r}\n"
            f"  Got:      {stored.get('dangerous_input')!r}"
        )
