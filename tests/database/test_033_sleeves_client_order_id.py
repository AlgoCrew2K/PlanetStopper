"""
Regression-pinning tests -- migration 033_sleeves.sql: client_order_id as the
durable pre-broker correlation key on sleeve_orders (+ broker_fill_id dedup
on sleeve_fills), per PM steering (team-lead 2026-07-07 23:25):

    "Add to your RED suite: a schema/accessor contract test pinning
    client_order_id on sleeve_orders -- column exists with UNIQUE
    constraint, insert_sleeve_order requires it, and a lookup-by-
    client_order_id accessor exists ... make sure your order-client RED
    exercises the lost-ack recovery path via client_order_id (submit
    succeeds, response lost, reconciliation finds the order by
    client_order_id)."

Category note: sleeve-db's commit 7126eb7 already implements this schema +
accessor surface (the DB layer is infrastructure-paced independently of the
sleeves/ business-logic RED gate -- see the P1 task graph: task #7 is not
blocked by task #6). These tests are therefore EXPECTED GREEN today, in the
"pin an invariant so it cannot silently regress" category used elsewhere in
this suite (e.g. test_reliability_expansion.py's
test_alpaca_batch_retry_is_bounded_at_max_retries: "RED: while the current
code is correctly bounded, this test pins the invariant..."). The
network-side half of the lost-ack recovery pattern (querying the BROKER by
client_order_id after a lost submit response) is genuinely RED and lives in
tests/sleeves/test_alpaca_orders_lifecycle.py::TestLostAckRecovery --
sleeves.alpaca_orders does not exist yet.

Fixture provenance: tests/fixtures/database/sleeves/sleeves_schema.json is
schema-derived (authored from the plan + the sleeve-db/sleeve-integration-
impl/sleeve-risk-impl interface-agreement thread, cross-checked against
migrations/033_sleeves.sql -- not co-designed against a parser). Used here
as an independent schema-shape oracle.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import database as db_module

_MIGRATION_FILENAME = "033_sleeves.sql"
_MIGRATION_PATH = Path(__file__).parents[2] / "migrations" / _MIGRATION_FILENAME
_SCHEMA_FIXTURE_PATH = (
    Path(__file__).parents[2]
    / "tests"
    / "fixtures"
    / "database"
    / "sleeves"
    / "sleeves_schema.json"
)


# ---------------------------------------------------------------------------
# Helpers (mirrors tests/database/test_032_prism_audit_log.py conventions)
# ---------------------------------------------------------------------------


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone()
    return row is not None


def _column_info(conn: sqlite3.Connection, table_name: str) -> dict[str, dict]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
    return {
        row[1]: {
            "type": row[2],
            "notnull": bool(row[3]),
            "has_default": row[4] is not None,
            "pk": bool(row[5]),
        }
        for row in rows
    }


def _unique_index_names_for_table(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA index_list({table_name})").fetchall()
    # PRAGMA index_list columns: seq, name, unique, origin, partial
    return {row[1] for row in rows if row[2] == 1}


def _index_covers_column(conn: sqlite3.Connection, index_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA index_info({index_name})").fetchall()
    return any(row[2] == column_name for row in rows)


@pytest.fixture(scope="module")
def schema_fixture() -> dict:
    assert _SCHEMA_FIXTURE_PATH.exists(), f"schema fixture missing: {_SCHEMA_FIXTURE_PATH}"
    with open(_SCHEMA_FIXTURE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _make_reserved_order(client_order_id: str, sleeve_id: int = 1, symbol: str = "SPY") -> int:
    return db_module.insert_sleeve_order(
        client_order_id,
        sleeve_id,
        symbol,
        "buy",
        10.0,
    )


# ---------------------------------------------------------------------------
# 1. Migration wiring
# ---------------------------------------------------------------------------


class TestMigrationWiring:
    def test_migration_file_exists(self):
        assert _MIGRATION_PATH.exists(), f"{_MIGRATION_FILENAME} not found under migrations/"

    def test_migration_uses_create_if_not_exists(self):
        sql = _MIGRATION_PATH.read_text(encoding="utf-8")
        assert "IF NOT EXISTS" in sql.upper()

    def test_migration_wired_in_migration_files_after_032(self):
        files = db_module._MIGRATION_FILES
        assert _MIGRATION_FILENAME in files
        idx_032 = files.index("032_prism_audit_log.sql")
        idx_033 = files.index(_MIGRATION_FILENAME)
        assert idx_033 > idx_032

    def test_sleeve_orders_table_exists_after_migrations(self):
        conn = db_module.get_connection()
        try:
            assert _table_exists(conn, "sleeve_orders")
        finally:
            conn.close()

    def test_run_migrations_is_idempotent(self):
        db_module.run_migrations()
        db_module.run_migrations()  # must not raise


# ---------------------------------------------------------------------------
# 2. client_order_id column + UNIQUE constraint (the pinned contract)
# ---------------------------------------------------------------------------


class TestClientOrderIdColumnAndConstraint:
    def test_client_order_id_column_exists_and_not_null(self):
        conn = db_module.get_connection()
        try:
            cols = _column_info(conn, "sleeve_orders")
        finally:
            conn.close()
        assert "client_order_id" in cols, "sleeve_orders is missing client_order_id"
        assert cols["client_order_id"]["notnull"] is True, (
            "client_order_id must be NOT NULL -- every sleeve_orders row is keyed by it "
            "from reserve-time onward, including the pre-ack window."
        )

    def test_client_order_id_has_unique_index(self):
        conn = db_module.get_connection()
        try:
            unique_indexes = _unique_index_names_for_table(conn, "sleeve_orders")
            covering = [
                idx for idx in unique_indexes if _index_covers_column(conn, idx, "client_order_id")
            ]
        finally:
            conn.close()
        assert covering, (
            f"sleeve_orders must have a UNIQUE index covering client_order_id; "
            f"found unique indexes: {unique_indexes}"
        )

    def test_duplicate_client_order_id_raises_integrity_error(self):
        client_order_id = "sleeve-test-dup-coid-001"
        _make_reserved_order(client_order_id)
        with pytest.raises(sqlite3.IntegrityError):
            _make_reserved_order(client_order_id)

    def test_schema_fixture_confirms_client_order_id_shape(self, schema_fixture):
        expected = schema_fixture["sleeve_orders"]["columns"]["client_order_id"]
        conn = db_module.get_connection()
        try:
            cols = _column_info(conn, "sleeve_orders")
        finally:
            conn.close()
        assert cols["client_order_id"]["notnull"] == expected["notnull"]
        assert cols["client_order_id"]["type"].upper() == expected["type_affinity"]


# ---------------------------------------------------------------------------
# 3. insert_sleeve_order requires client_order_id
# ---------------------------------------------------------------------------


class TestInsertSleeveOrderRequiresClientOrderId:
    def test_insert_sleeve_order_is_exported_and_callable(self):
        assert hasattr(db_module, "insert_sleeve_order")
        assert callable(db_module.insert_sleeve_order)

    def test_insert_sleeve_order_first_param_is_client_order_id(self):
        # Structural: client_order_id must be positional-first (or at minimum
        # a required kwarg with no default) -- a caller cannot construct a
        # sleeve_orders row without supplying it.
        import inspect

        sig = inspect.signature(db_module.insert_sleeve_order)
        params = list(sig.parameters.values())
        assert params, "insert_sleeve_order has no parameters"
        first = params[0]
        assert first.name == "client_order_id", (
            f"insert_sleeve_order's first parameter must be client_order_id; got {first.name!r}"
        )
        assert first.default is inspect.Parameter.empty, (
            "client_order_id must be a required parameter (no default) -- "
            "insert_sleeve_order requires it (PM steering)."
        )

    def test_insert_sleeve_order_returns_a_positive_row_id(self):
        row_id = _make_reserved_order("sleeve-test-rowid-001")
        assert isinstance(row_id, int)
        assert row_id > 0

    def test_insert_sleeve_order_defaults_alpaca_order_id_to_none(self):
        # The pre-ack RESERVED window: a row can exist before any broker
        # order id is known -- this is what makes crash-safe reservation
        # possible on a subprocess-per-minute engine.
        row_id = _make_reserved_order("sleeve-test-preack-001")
        row = db_module.get_sleeve_order_by_client_id("sleeve-test-preack-001")
        assert row is not None
        assert row["id"] == row_id
        assert row["alpaca_order_id"] is None
        assert row["status"] == "RESERVED"


# ---------------------------------------------------------------------------
# 4. Lookup-by-client_order_id accessor
# ---------------------------------------------------------------------------


class TestGetSleeveOrderByClientId:
    def test_accessor_is_exported_and_callable(self):
        assert hasattr(db_module, "get_sleeve_order_by_client_id")
        assert callable(db_module.get_sleeve_order_by_client_id)

    def test_returns_none_for_unknown_client_order_id(self):
        assert db_module.get_sleeve_order_by_client_id("sleeve-test-nonexistent-coid") is None

    def test_returns_the_inserted_row(self):
        _make_reserved_order("sleeve-test-lookup-001", symbol="QQQ")
        row = db_module.get_sleeve_order_by_client_id("sleeve-test-lookup-001")
        assert row is not None
        assert row["client_order_id"] == "sleeve-test-lookup-001"
        assert row["symbol"] == "QQQ"

    def test_returned_dict_has_required_keys(self, schema_fixture):
        _make_reserved_order("sleeve-test-keys-001")
        row = db_module.get_sleeve_order_by_client_id("sleeve-test-keys-001")
        expected_columns = set(schema_fixture["sleeve_orders"]["columns"].keys())
        missing = expected_columns - row.keys()
        assert not missing, f"get_sleeve_order_by_client_id result missing keys: {missing}"


# ---------------------------------------------------------------------------
# 5. Lost-ack recovery flow (DB-side half)
# ---------------------------------------------------------------------------


class TestLostAckRecoveryDbSideFlow:
    """
    The full recovery pattern has two halves:
      (a) DB-side (this class): a RESERVED row exists at client_order_id
          BEFORE the broker call; once the broker ack is (re-)obtained --
          whether synchronously or via a later recovery lookup after a lost
          response -- attach_alpaca_order_id() populates the row in place.
      (b) Network-side (genuinely RED): querying the BROKER for an order by
          client_order_id after our own process lost the submit response.
          See tests/sleeves/test_alpaca_orders_lifecycle.py::
          TestLostAckRecovery.
    """

    def test_attach_alpaca_order_id_populates_a_reserved_row(self):
        client_order_id = "sleeve-test-recovery-001"
        _make_reserved_order(client_order_id)

        # Simulate: the broker actually accepted the order and assigned an
        # id, but our process's synchronous read of the submit response was
        # lost (timeout / connection reset AFTER the request was processed
        # server-side). A LATER recovery lookup (by client_order_id, against
        # the broker) discovers the real alpaca_order_id and status.
        recovered_alpaca_order_id = "b3a1c2d4-recovered-0001"
        db_module.attach_alpaca_order_id(
            client_order_id,
            recovered_alpaca_order_id,
            status="new",
            raw_json=json.dumps({"id": recovered_alpaca_order_id, "status": "new"}),
        )

        row = db_module.get_sleeve_order_by_client_id(client_order_id)
        assert row["alpaca_order_id"] == recovered_alpaca_order_id, (
            "attach_alpaca_order_id must populate alpaca_order_id on the pre-existing "
            "RESERVED row found by client_order_id -- this IS the lost-ack recovery path."
        )
        assert row["status"] == "new"

    def test_attach_alpaca_order_id_is_a_noop_for_unknown_client_order_id(self):
        # Per docstring: "No-op (no row touched) if client_order_id is unknown."
        # Must not raise -- callers may legitimately race a reconciliation
        # sweep against an order that hasn't been reserved yet.
        db_module.attach_alpaca_order_id("sleeve-test-never-reserved-001", "some-alpaca-id")
        assert db_module.get_sleeve_order_by_client_id("sleeve-test-never-reserved-001") is None

    def test_get_sleeve_order_by_alpaca_id_finds_the_recovered_row(self):
        client_order_id = "sleeve-test-recovery-002"
        _make_reserved_order(client_order_id)
        db_module.attach_alpaca_order_id(client_order_id, "b3a1c2d4-recovered-0002", status="new")

        row = db_module.get_sleeve_order_by_alpaca_id("b3a1c2d4-recovered-0002")
        assert row is not None
        assert row["client_order_id"] == client_order_id


# ---------------------------------------------------------------------------
# 6. broker_fill_id dedup on sleeve_fills (same schema, flagged alongside
#    client_order_id by sleeve-integration-impl -- pinned for completeness)
# ---------------------------------------------------------------------------


class TestBrokerFillIdDedup:
    def test_broker_fill_id_column_exists(self):
        conn = db_module.get_connection()
        try:
            cols = _column_info(conn, "sleeve_fills")
        finally:
            conn.close()
        assert "broker_fill_id" in cols

    def test_broker_fill_id_has_unique_index(self):
        conn = db_module.get_connection()
        try:
            unique_indexes = _unique_index_names_for_table(conn, "sleeve_fills")
            covering = [
                idx for idx in unique_indexes if _index_covers_column(conn, idx, "broker_fill_id")
            ]
        finally:
            conn.close()
        assert covering, "sleeve_fills must have a UNIQUE index covering broker_fill_id"

    def test_duplicate_broker_fill_id_raises_integrity_error(self):
        order_row_id = _make_reserved_order("sleeve-test-fill-dedup-001")
        db_module.insert_sleeve_fill(
            order_row_id, 501.25, 4.0, "2026-07-07T14:32:10Z", broker_fill_id="activity-dedup-001"
        )
        with pytest.raises(sqlite3.IntegrityError):
            db_module.insert_sleeve_fill(
                order_row_id,
                501.25,
                4.0,
                "2026-07-07T14:32:10Z",
                broker_fill_id="activity-dedup-001",
            )

    def test_null_broker_fill_id_does_not_collide(self):
        # SQLite UNIQUE indexes treat multiple NULLs as distinct -- two fills
        # with no broker_fill_id must both insert successfully.
        order_row_id = _make_reserved_order("sleeve-test-fill-null-001")
        id_1 = db_module.insert_sleeve_fill(order_row_id, 500.0, 2.0, "2026-07-07T14:00:00Z")
        id_2 = db_module.insert_sleeve_fill(order_row_id, 500.5, 2.0, "2026-07-07T14:00:05Z")
        assert id_1 != id_2
