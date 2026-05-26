"""
RED tests — rev-m2 BLOCK-1, BLOCK-2, BLOCK-3 findings on c0bbfb4.

BLOCK-1 (Schema Reversibility + Math Safety):
  record_cvar_diagnostic passes cvar_n_tail=None into row_dict unchanged.
  The cvar_n_tail column is NOT NULL DEFAULT 0. SQLite raises IntegrityError
  on every Phase-1 INSERT that passes None; write_telemetry_row live-mode
  swallows the error silently, dropping the row entirely.
  Fix: record_cvar_diagnostic must coerce None → 0 before building row_dict.

BLOCK-2 (Dashboard Side Effects — functional bug):
  app.py:392-393 calls read_cvar_diagnostic_for_cycle(_first_sym_id, _first_sym_id),
  passing the symphony key as BOTH cycle_id and symphony_id. cycle_id holds
  ISO-8601 timestamps (e.g. "2026-05-25T14:30:00"), not symphony IDs. The query
  WHERE cycle_id = ? AND symphony_id = ? always returns None; the S-3 panel never
  renders. Fix: dashboard must call a "latest row for symphony" read —
  SELECT ... WHERE symphony_id = ? ORDER BY ts_utc DESC LIMIT 1.
  Requires a new read function read_cvar_diagnostic_for_symphony(symphony_id).

BLOCK-3 (Schema Reversibility — missing indexes):
  021_cvar_diagnostics.sql has no CREATE INDEX statements. Plan §4 requires
  idx_cvar_diag_cycle (on cycle_id) and idx_cvar_diag_symphony_ts (on symphony_id,
  ts_utc DESC). idx_cvar_diag_symphony_ts is the index the BLOCK-2 dashboard fix
  will use. Without it, every dashboard render does a full-table scan.
  Fix: add both CREATE INDEX IF NOT EXISTS statements to the migration.

All tests are RED on c0bbfb4. GREEN criteria:
  BLOCK-1: record_cvar_diagnostic(..., cvar_n_tail=None) writes 0, not error.
  BLOCK-2: read_cvar_diagnostic_for_symphony exists and returns the latest row by
           symphony_id; dashboard uses it instead of read_cvar_diagnostic_for_cycle.
  BLOCK-3: both indexes exist in sqlite_master after 021 migration runs.
"""

from __future__ import annotations

import pathlib
import sqlite3
from unittest.mock import patch

import pytest

import database as _db

_MIGRATIONS = pathlib.Path(__file__).parent.parent.parent / "migrations"


def _apply_migration(conn: sqlite3.Connection, filename: str) -> None:
    """Execute a migration file against an open in-memory connection."""
    sql = (_MIGRATIONS / filename).read_text(encoding="utf-8")
    conn.executescript(sql)


def _make_cvar_db() -> sqlite3.Connection:
    """
    Return an in-memory SQLite connection with the cvar_diagnostics schema applied.
    Caller is responsible for closing.
    """
    conn = sqlite3.connect(":memory:")
    _apply_migration(conn, "021_cvar_diagnostics.sql")
    return conn


# ---------------------------------------------------------------------------
# BLOCK-1: record_cvar_diagnostic must coerce cvar_n_tail=None → 0
# ---------------------------------------------------------------------------


class TestRecordCvarDiagnosticCoercesNoneTailToZero:
    """
    BLOCK-1: alpha_bot_execution.py:1475 writes cvar_n_tail=None on the
    Phase-1 sentinel path. record_cvar_diagnostic passes None directly into
    row_dict → write_telemetry_row → INSERT. The NOT NULL DEFAULT 0 constraint
    fires IntegrityError; live-mode swallows it silently, dropping every
    Phase-1 row.

    Fix: record_cvar_diagnostic must map None → 0 before building row_dict.
    """

    def _write_via_record_cvar_diagnostic(
        self, conn: sqlite3.Connection, cvar_n_tail_value
    ) -> "dict | None":
        """
        Patch write_telemetry_row to use the provided in-memory connection,
        then call record_cvar_diagnostic with the given cvar_n_tail value.
        Returns the row dict if a row was written, None if nothing landed.
        """
        def _direct_insert(table_name: str, row_dict: dict, *, mode: str) -> None:
            # Bypass write_telemetry_row's own connection management and insert
            # directly into the test connection. Raises on IntegrityError (replay
            # semantics — surfaces the bug rather than swallowing it).
            cols = ", ".join(row_dict.keys())
            placeholders = ", ".join("?" for _ in row_dict)
            conn.execute(
                f"INSERT INTO {table_name} ({cols}) VALUES ({placeholders})",
                list(row_dict.values()),
            )
            conn.commit()

        with patch.object(_db, "write_telemetry_row", side_effect=_direct_insert):
            _db.record_cvar_diagnostic(
                cycle_id="2026-05-25T14:30:00",
                symphony_id="sym-test",
                cvar_5pct=-0.05,
                cvar_5pct_stderr=0.004,
                cvar_n_tail=cvar_n_tail_value,
                cvar_5pct_long=None,
                cvar_n_tail_long=None,
                mode="replay",
            )

        row = conn.execute(
            "SELECT * FROM cvar_diagnostics WHERE symphony_id = ?",
            ("sym-test",),
        ).fetchone()
        if row is None:
            return None
        cols = [desc[0] for desc in conn.execute("SELECT * FROM cvar_diagnostics LIMIT 0").description]
        # re-query with column names
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM cvar_diagnostics WHERE symphony_id = ?",
            ("sym-test",),
        ).fetchone()
        return dict(row) if row else None

    def test_record_cvar_diagnostic_none_tail_writes_zero_not_error(self):
        """
        record_cvar_diagnostic(cvar_n_tail=None) must write cvar_n_tail=0,
        not raise IntegrityError or silently drop the row.

        Currently FAILS: None is passed directly into row_dict, causing
        IntegrityError on the NOT NULL DEFAULT 0 column. The write_telemetry_row
        live-mode swallows this; the row is silently lost.

        GREEN: record_cvar_diagnostic coerces None → 0 before building row_dict.
        The row must land with cvar_n_tail=0 (integer).
        """
        conn = _make_cvar_db()
        try:
            row = self._write_via_record_cvar_diagnostic(conn, cvar_n_tail_value=None)
        finally:
            conn.close()

        assert row is not None, (
            "record_cvar_diagnostic(cvar_n_tail=None) produced no row in cvar_diagnostics. "
            "BLOCK-1: None→IntegrityError is swallowed in live mode, silently dropping the row. "
            "Fix: coerce None → 0 in record_cvar_diagnostic before building row_dict."
        )
        assert row["cvar_n_tail"] == 0, (
            f"cvar_n_tail={row['cvar_n_tail']!r}; expected 0. "
            "record_cvar_diagnostic must map Python None to integer 0 — "
            "NOT NULL DEFAULT 0 only substitutes when the column is omitted from the INSERT, "
            "not when Python None is explicitly passed as the value."
        )
        assert isinstance(row["cvar_n_tail"], int), (
            f"cvar_n_tail type={type(row['cvar_n_tail']).__name__}; expected int. "
            "The coercion must produce integer 0, not None or string '0'."
        )

    def test_record_cvar_diagnostic_integer_zero_tail_roundtrips_correctly(self):
        """
        record_cvar_diagnostic(cvar_n_tail=0) must write 0 — regression guard
        that the coercion path does not alter an already-correct 0.
        """
        conn = _make_cvar_db()
        try:
            row = self._write_via_record_cvar_diagnostic(conn, cvar_n_tail_value=0)
        finally:
            conn.close()

        assert row is not None, (
            "record_cvar_diagnostic(cvar_n_tail=0) produced no row."
        )
        assert row["cvar_n_tail"] == 0, (
            f"cvar_n_tail={row['cvar_n_tail']!r}; expected 0 (roundtrip)."
        )


# ---------------------------------------------------------------------------
# BLOCK-2: dashboard must use symphony-based read, not cycle_id-as-symphony
# ---------------------------------------------------------------------------


class TestDashboardUsesSymphonyBasedCvarRead:
    """
    BLOCK-2: app.py calls read_cvar_diagnostic_for_cycle(_first_sym_id, _first_sym_id).
    cycle_id holds ISO-8601 timestamps; passing a symphony key as cycle_id always
    returns None. The S-3 panel never renders.

    Fix requires:
    1. A new function read_cvar_diagnostic_for_symphony(symphony_id) that queries
       WHERE symphony_id = ? ORDER BY ts_utc DESC LIMIT 1.
    2. The dashboard route uses read_cvar_diagnostic_for_symphony, not
       read_cvar_diagnostic_for_cycle(_first_sym_id, _first_sym_id).
    """

    def test_read_cvar_diagnostic_for_symphony_exists_in_database_module(self):
        """
        database module must expose read_cvar_diagnostic_for_symphony(symphony_id).
        Currently FAILS: the function does not exist.
        """
        assert hasattr(_db, "read_cvar_diagnostic_for_symphony"), (
            "database.read_cvar_diagnostic_for_symphony not found. "
            "BLOCK-2: the dashboard needs a symphony-keyed read (latest row by "
            "symphony_id ORDER BY ts_utc DESC) — the cycle-keyed function returns None "
            "when passed the symphony ID as cycle_id."
        )

    def test_read_cvar_diagnostic_for_symphony_returns_latest_row(self, tmp_path):
        """
        read_cvar_diagnostic_for_symphony(symphony_id) must return the most-recent
        cvar_diagnostics row for that symphony, regardless of cycle_id.

        Currently FAILS (function absent — test_read_cvar_diagnostic_for_symphony_exists
        above asserts the prerequisite; this test exercises behavior once it exists).

        When the function exists and the dashboard calls it, the S-3 panel receives
        a real row (not None) and renders the four-part display.
        """
        assert hasattr(_db, "read_cvar_diagnostic_for_symphony"), (
            "database.read_cvar_diagnostic_for_symphony not found — "
            "implement the function before this behavior test can run."
        )

        # Build a real file-based DB so get_ro_connection can open it.
        db_path = tmp_path / "test_state.db"
        conn = sqlite3.connect(str(db_path))
        _apply_migration(conn, "021_cvar_diagnostics.sql")
        # Write two rows for the same symphony, different cycle timestamps.
        conn.execute(
            "INSERT INTO cvar_diagnostics "
            "(cycle_id, ts_utc, symphony_id, cvar_5pct, cvar_5pct_stderr, cvar_n_tail) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("2026-05-25T10:00:00", "2026-05-25 10:00:00", "symphony-abc", -0.05, 0.004, 8),
        )
        conn.execute(
            "INSERT INTO cvar_diagnostics "
            "(cycle_id, ts_utc, symphony_id, cvar_5pct, cvar_5pct_stderr, cvar_n_tail) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("2026-05-25T11:00:00", "2026-05-25 11:00:00", "symphony-abc", -0.04, 0.003, 9),
        )
        conn.commit()
        conn.close()

        with patch.object(_db, "DB_FILE", str(db_path)):
            result = _db.read_cvar_diagnostic_for_symphony("symphony-abc")

        assert result is not None, (
            "read_cvar_diagnostic_for_symphony('symphony-abc') returned None; "
            "expected the latest row (cycle_id='2026-05-25T11:00:00', cvar_n_tail=9)."
        )
        # Must return the LATEST row (ts_utc DESC)
        assert result["cvar_n_tail"] == 9, (
            f"Expected latest row with cvar_n_tail=9; got {result['cvar_n_tail']}. "
            "The function must ORDER BY ts_utc DESC LIMIT 1."
        )
        assert result["cycle_id"] == "2026-05-25T11:00:00", (
            f"Expected latest cycle_id='2026-05-25T11:00:00'; got {result['cycle_id']}."
        )

    def test_dashboard_route_does_not_pass_symphony_id_as_cycle_id(self):
        """
        The dashboard route must NOT call read_cvar_diagnostic_for_cycle with the
        symphony key as both arguments. That call always returns None because
        cycle_id holds ISO-8601 timestamps, not symphony IDs.

        app.py:392-393 currently calls:
            database.read_cvar_diagnostic_for_cycle(_first_sym_id, _first_sym_id)
        where _first_sym_id is the symphony key (e.g. "abc123"). The cycle_id
        column holds timestamps like "2026-05-25T14:30:00". WHERE cycle_id = 'abc123'
        always returns None; the S-3 panel never renders.

        Assert: when read_cvar_diagnostic_for_cycle is called, its first argument
        (cycle_id) must NOT equal the symphony key passed as the second argument.

        Currently FAILS: both arguments are _first_sym_id (the same symbol key).
        GREEN: the call is replaced by read_cvar_diagnostic_for_symphony(symphony_id).
        """
        import app as _app

        cycle_calls: list[tuple[str, str]] = []

        def _capture_cycle_call(cycle_id: str, symphony_id: str):
            cycle_calls.append((cycle_id, symphony_id))
            return None

        sym_key = "test-symphony-key-abc123"
        minimal_bot_state = {sym_key: {"name": "Test Symphony", "id": sym_key}}

        # Canned api_state so the route skips all external I/O before the CVaR read.
        # Keys mirror get_api_state_dict() output so the route doesn't branch into
        # live fallbacks (database.load_state, _compute_portfolio_strip, etc.).
        canned_api_state = {
            "bot_state": minimal_bot_state,
            "portfolio_strip": {},
            "is_locked": False,
            "port_state": {},
            "exit_authority": None,
            "daemon_started_at": None,
            "meta": {
                "portfolio": {},
                "market_state": "closed",
                "strategies": {},
                "is_locked": False,
            },
            "symphonies": list(minimal_bot_state.values()),
        }

        with patch("app.get_api_state_dict", return_value=canned_api_state), \
             patch.object(_db, "read_cvar_diagnostic_for_cycle", side_effect=_capture_cycle_call), \
             patch.object(_db, "read_cvar_diagnostic_for_symphony", return_value=None, create=True), \
             patch("app.database.normalize_name", return_value="test-symphony"), \
             patch("app.database.get_symphony_strategy", return_value=None), \
             patch("app.render_template", return_value="ok"):
            _app.app.config["TESTING"] = True
            with _app.app.test_client() as client:
                client.get("/")

        # If the old call site is still present: cycle_calls contains
        # (sym_key, sym_key) — cycle_id == symphony_id. That is the bug.
        for cycle_id_arg, symphony_id_arg in cycle_calls:
            assert cycle_id_arg != symphony_id_arg, (
                f"read_cvar_diagnostic_for_cycle called with cycle_id={cycle_id_arg!r} == "
                f"symphony_id={symphony_id_arg!r}. "
                "BLOCK-2: app.py passes the symphony key as cycle_id. "
                "cycle_id holds ISO-8601 timestamps; this query always returns None. "
                "Fix: replace with read_cvar_diagnostic_for_symphony(symphony_id)."
            )


# ---------------------------------------------------------------------------
# BLOCK-3: migration must create both required indexes
# ---------------------------------------------------------------------------


class TestCvarDiagnosticsMigrationIndexes:
    """
    BLOCK-3: 021_cvar_diagnostics.sql has no CREATE INDEX statements.
    Plan §4 requires:
      idx_cvar_diag_cycle — on (cycle_id)
      idx_cvar_diag_symphony_ts — on (symphony_id, ts_utc DESC)

    idx_cvar_diag_symphony_ts is the index used by read_cvar_diagnostic_for_symphony
    (the BLOCK-2 fix). Without it, every dashboard render is a full-table scan.
    """

    def _get_indexes_after_migration(self) -> set[str]:
        conn = _make_cvar_db()
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='cvar_diagnostics'"
            ).fetchall()
        finally:
            conn.close()
        return {row[0] for row in rows}

    def test_migration_creates_idx_cvar_diag_cycle(self):
        """
        021_cvar_diagnostics.sql must CREATE INDEX IF NOT EXISTS idx_cvar_diag_cycle
        on cvar_diagnostics(cycle_id).

        Currently FAILS: the migration has no CREATE INDEX statements.
        """
        indexes = self._get_indexes_after_migration()
        assert "idx_cvar_diag_cycle" in indexes, (
            f"idx_cvar_diag_cycle not found after running 021_cvar_diagnostics.sql. "
            f"Existing indexes: {indexes or 'none'}. "
            "BLOCK-3: add CREATE INDEX IF NOT EXISTS idx_cvar_diag_cycle "
            "ON cvar_diagnostics(cycle_id) to the migration."
        )

    def test_migration_creates_idx_cvar_diag_symphony_ts(self):
        """
        021_cvar_diagnostics.sql must CREATE INDEX IF NOT EXISTS idx_cvar_diag_symphony_ts
        on cvar_diagnostics(symphony_id, ts_utc DESC).

        This index is required by read_cvar_diagnostic_for_symphony (BLOCK-2 fix):
        the dashboard reads the latest row per symphony on every page load.
        Without this index every render hits a full table scan.

        Currently FAILS: the migration has no CREATE INDEX statements.
        """
        indexes = self._get_indexes_after_migration()
        assert "idx_cvar_diag_symphony_ts" in indexes, (
            f"idx_cvar_diag_symphony_ts not found after running 021_cvar_diagnostics.sql. "
            f"Existing indexes: {indexes or 'none'}. "
            "BLOCK-3: add CREATE INDEX IF NOT EXISTS idx_cvar_diag_symphony_ts "
            "ON cvar_diagnostics(symphony_id, ts_utc DESC) to the migration."
        )
