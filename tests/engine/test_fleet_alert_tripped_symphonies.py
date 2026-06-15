"""
Tests for migration 013 — fleet_alert_state.tripped_symphonies column.

Covers:
  - Migration file exists and is registered in _MIGRATION_FILES.
  - write_fleet_alert stores tripped_symphonies as a JSON array.
  - read_fleet_alert returns tripped_symphonies as a parsed list.
  - read_fleet_alert returns [] (not None) when tripped_symphonies is NULL (old rows).
  - Full round-trip: write list → read back same list.
  - /api/state fleet_correlation_alert payload includes tripped_symphonies.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# In-memory DB helper — includes both migration 009 base schema and 013 column
# ---------------------------------------------------------------------------


def _make_in_memory_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fleet_alert_state (
            id               INTEGER PRIMARY KEY CHECK(id = 1),
            tripped_at_et    TEXT NOT NULL,
            triggered_reason TEXT NOT NULL,
            tripped_count    INTEGER NOT NULL,
            active_count     INTEGER NOT NULL,
            dismissed_at_et  TEXT NULL DEFAULT NULL,
            tripped_symphonies TEXT NULL DEFAULT NULL
        )
    """)
    conn.commit()
    return conn


def _make_legacy_conn() -> sqlite3.Connection:
    """Pre-migration 013 schema — no tripped_symphonies column."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fleet_alert_state (
            id               INTEGER PRIMARY KEY CHECK(id = 1),
            tripped_at_et    TEXT NOT NULL,
            triggered_reason TEXT NOT NULL,
            tripped_count    INTEGER NOT NULL,
            active_count     INTEGER NOT NULL,
            dismissed_at_et  TEXT NULL DEFAULT NULL
        )
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Section 1 — Migration 013 file + registration
# ---------------------------------------------------------------------------


class TestMigration013Registration:
    def test_migration_013_file_exists(self):
        migration_path = (
            pathlib.Path(__file__).parent.parent.parent
            / "migrations"
            / "013_fleet_alert_tripped_symphonies.sql"
        )
        assert migration_path.exists(), (
            "migrations/013_fleet_alert_tripped_symphonies.sql must exist — "
            "additive ALTER TABLE to add tripped_symphonies TEXT NULL."
        )

    def test_migration_013_listed_in_migration_files(self):
        import database

        assert "013_fleet_alert_tripped_symphonies.sql" in database._MIGRATION_FILES, (
            "database._MIGRATION_FILES must include '013_fleet_alert_tripped_symphonies.sql'."
        )

    def test_migration_013_is_additive_alter_table(self):
        """Migration SQL must use ALTER TABLE ADD COLUMN — never DROP or recreate."""
        migration_path = (
            pathlib.Path(__file__).parent.parent.parent
            / "migrations"
            / "013_fleet_alert_tripped_symphonies.sql"
        )
        sql = migration_path.read_text().upper()
        assert "ALTER TABLE" in sql, "013 migration must use ALTER TABLE."
        assert "ADD COLUMN" in sql, "013 migration must ADD COLUMN (additive only)."
        assert "DROP TABLE" not in sql, "013 migration must not DROP TABLE."
        assert "DROP COLUMN" not in sql, "013 migration must not DROP COLUMN."

    def test_migration_013_applies_cleanly_to_existing_schema(self):
        """ALTER TABLE ADD COLUMN must apply without error on a schema that already has 009."""
        conn = _make_legacy_conn()
        migration_path = (
            pathlib.Path(__file__).parent.parent.parent
            / "migrations"
            / "013_fleet_alert_tripped_symphonies.sql"
        )
        sql = migration_path.read_text()
        conn.executescript(sql)
        cursor = conn.execute("PRAGMA table_info(fleet_alert_state)")
        cols = {row[1] for row in cursor.fetchall()}
        assert "tripped_symphonies" in cols, (
            "After applying 013 migration, fleet_alert_state must have tripped_symphonies column."
        )
        conn.close()


# ---------------------------------------------------------------------------
# Section 2 — write_fleet_alert / read_fleet_alert round-trip
# ---------------------------------------------------------------------------


class TestTrippedSymphonieRoundTrip:
    """write_fleet_alert stores names; read_fleet_alert returns them as a list."""

    def _patched_helpers(self, conn):
        """Return (write_fn, read_fn) backed by the given in-memory conn."""
        import database

        def _write(payload):
            dismissed_at_et = payload.get("dismissed_at_et")
            names = payload.get("tripped_symphonies") or []
            tripped_json = json.dumps(names) if names else None
            conn.execute(
                "INSERT OR REPLACE INTO fleet_alert_state "
                "(id, tripped_at_et, triggered_reason, tripped_count, active_count, "
                "dismissed_at_et, tripped_symphonies) "
                "VALUES (1, ?, ?, ?, ?, ?, ?)",
                (
                    payload["tripped_at_et"],
                    payload["triggered_reason"],
                    payload["tripped_count"],
                    payload["active_count"],
                    dismissed_at_et,
                    tripped_json,
                ),
            )
            conn.commit()

        def _read():
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT id, tripped_at_et, triggered_reason, tripped_count, active_count, "
                "dismissed_at_et, tripped_symphonies FROM fleet_alert_state WHERE id = 1"
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            raw = result.get("tripped_symphonies")
            try:
                result["tripped_symphonies"] = json.loads(raw) if raw else []
            except (ValueError, TypeError):
                result["tripped_symphonies"] = []
            return result

        return _write, _read

    def test_write_stores_names_as_json_array(self):
        conn = _make_in_memory_conn()
        write, _ = self._patched_helpers(conn)
        names = ["Aggressive Momentum", "Conservative Bond Ladder"]
        write(
            {
                "tripped_at_et": "2026-05-21T10:00:00",
                "triggered_reason": "trailing_stop",
                "tripped_count": 2,
                "active_count": 5,
                "tripped_symphonies": names,
            }
        )
        raw = conn.execute(
            "SELECT tripped_symphonies FROM fleet_alert_state WHERE id = 1"
        ).fetchone()[0]
        assert raw is not None, (
            "tripped_symphonies column must be set (not NULL) when names provided."
        )
        parsed = json.loads(raw)
        assert parsed == names, (
            f"Stored JSON must round-trip to the original list. Expected {names}, got {parsed}."
        )
        conn.close()

    def test_read_returns_names_as_list(self):
        conn = _make_in_memory_conn()
        write, read = self._patched_helpers(conn)
        names = ["Symphony Alpha", "Symphony Beta", "Symphony Gamma"]
        write(
            {
                "tripped_at_et": "2026-05-21T10:00:00",
                "triggered_reason": "trailing_stop",
                "tripped_count": 3,
                "active_count": 8,
                "tripped_symphonies": names,
            }
        )
        result = read()
        assert result is not None
        assert result["tripped_symphonies"] == names, (
            f"read_fleet_alert must return tripped_symphonies as a list. "
            f"Expected {names}, got {result['tripped_symphonies']}."
        )
        conn.close()

    def test_read_returns_empty_list_when_column_is_null(self):
        """Old rows without tripped_symphonies must return [] not None."""
        conn = _make_in_memory_conn()
        # Insert directly without tripped_symphonies (simulates pre-013 row).
        conn.execute(
            "INSERT OR REPLACE INTO fleet_alert_state "
            "(id, tripped_at_et, triggered_reason, tripped_count, active_count, dismissed_at_et) "
            "VALUES (1, '2026-05-20T09:30:00', 'trailing_stop', 2, 5, NULL)"
        )
        conn.commit()
        _, read = self._patched_helpers(conn)
        result = read()
        assert result is not None
        assert result["tripped_symphonies"] == [], (
            "read_fleet_alert must return [] for tripped_symphonies when column is NULL "
            "(backward compatibility with pre-013 rows)."
        )
        conn.close()

    def test_write_with_empty_list_stores_null(self):
        """Empty/absent tripped_symphonies list should store NULL (not '[]')."""
        conn = _make_in_memory_conn()
        write, _ = self._patched_helpers(conn)
        write(
            {
                "tripped_at_et": "2026-05-21T10:00:00",
                "triggered_reason": "trailing_stop",
                "tripped_count": 0,
                "active_count": 5,
                "tripped_symphonies": [],
            }
        )
        raw = conn.execute(
            "SELECT tripped_symphonies FROM fleet_alert_state WHERE id = 1"
        ).fetchone()[0]
        assert raw is None, (
            "write_fleet_alert must store NULL when tripped_symphonies is empty, "
            "not an empty JSON array string."
        )
        conn.close()


# ---------------------------------------------------------------------------
# Section 3 — database module helpers (integration via monkeypatching)
# ---------------------------------------------------------------------------


class TestDatabaseHelpersTrippedSymphonies:
    """Integration tests against real database.py helpers using in-memory DB."""

    @pytest.fixture(autouse=True)
    def _patch_db_connection(self, tmp_path):
        """Redirect database.get_connection() to a fresh in-memory DB per test."""
        import database

        conn = _make_in_memory_conn()
        with patch.object(database, "get_connection", return_value=conn):
            yield
        conn.close()

    def test_round_trip_via_database_helpers(self):
        import database

        names = ["Trend Rider", "Mean Reversion Plus"]
        database.write_fleet_alert(
            {
                "tripped_at_et": "2026-05-21T10:30:00",
                "triggered_reason": "trailing_stop",
                "tripped_count": 2,
                "active_count": 6,
                "tripped_symphonies": names,
            }
        )
        result = database.read_fleet_alert()
        assert result is not None, "read_fleet_alert must return a dict after write."
        assert result["tripped_symphonies"] == names, (
            f"database.read_fleet_alert must return the same list written by write_fleet_alert. "
            f"Expected {names}, got {result['tripped_symphonies']}."
        )

    def test_read_returns_empty_list_for_null_column(self):
        import database

        # Write without tripped_symphonies key.
        database.write_fleet_alert(
            {
                "tripped_at_et": "2026-05-21T10:30:00",
                "triggered_reason": "trailing_stop",
                "tripped_count": 1,
                "active_count": 4,
            }
        )
        result = database.read_fleet_alert()
        assert result is not None
        assert result["tripped_symphonies"] == [], (
            "read_fleet_alert must return [] when tripped_symphonies is absent from payload."
        )

    def test_api_state_fleet_alert_includes_tripped_symphonies(self):
        """The /api/state fleet_correlation_alert object must expose tripped_symphonies list."""
        import app as app_module

        names = ["Portfolio A", "Portfolio B"]
        alert_row = {
            "id": 1,
            "tripped_at_et": "2026-05-21T10:00:00",
            "triggered_reason": "trailing_stop",
            "tripped_count": 2,
            "active_count": 5,
            "dismissed_at_et": None,
            "tripped_symphonies": names,
        }

        client = app_module.app.test_client()
        with (
            patch("database.load_state", return_value={}),
            patch("database.read_fleet_alert", return_value=alert_row),
            patch("database.get_ro_connection") as mock_ro,
            patch("database.get_triggers", return_value=[]),
            patch("database.get_guard_alpha_by_symphony", return_value={}),
            patch("app.get_market_state", return_value="open"),
            patch("app._compute_portfolio_strip", return_value={}),
            patch("app.analytics.get_portfolio_daily_returns_from_shadow", return_value=None),
            patch("app.analytics.get_history_with_cache_invalidation", return_value=None),
        ):
            mock_ro.return_value.__enter__ = lambda s: s
            mock_ro.return_value.__exit__ = lambda s, *a: None
            mock_ro.return_value.execute.return_value.fetchone.return_value = None
            resp = client.get("/api/state")

        assert resp.status_code == 200
        data = resp.get_json()
        alert = data.get("fleet_correlation_alert")
        assert alert is not None, (
            "/api/state must include fleet_correlation_alert when an active alert exists."
        )
        assert "tripped_symphonies" in alert, (
            "fleet_correlation_alert in /api/state must include tripped_symphonies field."
        )
        assert alert["tripped_symphonies"] == names, (
            f"fleet_correlation_alert.tripped_symphonies must equal the list from read_fleet_alert. "
            f"Expected {names}, got {alert['tripped_symphonies']}."
        )
