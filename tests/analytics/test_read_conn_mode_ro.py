"""
tests/analytics/test_read_conn_mode_ro.py

Arch constraint 5 enforcement: all read-path sqlite3.connect calls in
analytics.py must open read-only connections (uri=True + ?mode=ro).

Two test classes:
  1. StaticSourceScan  — AST/source-level assertion that none of the six
     read-path connect sites use a plain read-write handle without mode=ro.
  2. BehavioralPin     — a ?mode=ro connection rejects INSERT (OperationalError),
     and the existing _get_shadow_cumulative_trajectory still returns rows
     correctly through the read-only connection.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Schema used by the behavioral pin fixture (mirrors the live table layout
# as defined in tests/analytics/test_shadow_trajectory_position_epoch.py).
# ---------------------------------------------------------------------------
_SHADOW_SCHEMA = """
    CREATE TABLE shadow_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_utc TEXT NOT NULL,
        ts_et TEXT,
        trading_day TEXT NOT NULL,
        symphony_id TEXT NOT NULL,
        account_id TEXT,
        cycle_id TEXT,
        current_return REAL NOT NULL,
        shadow_return REAL NOT NULL,
        is_post_trigger INTEGER NOT NULL DEFAULT 0,
        trigger_id INTEGER,
        position_epoch TEXT
    )
"""

_SYM_ID = "test-ro-pin-symphony"
_EPOCH = "2026-01-10T14:30:00Z"


def _seed_shadow_db(tmp_path: Path) -> str:
    """Seed a two-row shadow_history DB; returns the file path as a str."""
    db_file = str(tmp_path / "shadow_ro_pin.db")
    conn = sqlite3.connect(db_file)
    conn.execute(_SHADOW_SCHEMA)
    rows = [
        (_EPOCH, "2026-01-10", _SYM_ID, 1.5, -0.5),
        (_EPOCH, "2026-01-11", _SYM_ID, 2.3, 0.8),
        (_EPOCH, "2026-01-12", _SYM_ID, 3.1, -0.3),
    ]
    conn.executemany(
        "INSERT INTO shadow_history (ts_utc, trading_day, symphony_id, "
        "current_return, shadow_return, position_epoch) VALUES (?,?,?,?,?,?)",
        [(*r[:2], r[2], r[3], r[4], _EPOCH) for r in rows],
    )
    conn.commit()
    conn.close()
    return db_file


# ---------------------------------------------------------------------------
# Class 1 — static source scan
# ---------------------------------------------------------------------------


class TestStaticSourceScan:
    """Verify that every sqlite3.connect call in analytics.py uses uri=True
    and ?mode=ro in the connection string — no plain read-write handle."""

    _ANALYTICS_SRC = Path(__file__).parent.parent.parent / "analytics.py"

    def _source(self) -> str:
        return self._ANALYTICS_SRC.read_text(encoding="utf-8")

    def test_analytics_py_exists(self):
        assert self._ANALYTICS_SRC.exists(), f"analytics.py not found at {self._ANALYTICS_SRC}"

    def test_no_plain_readwrite_connect(self):
        """None of the sqlite3.connect sites may use a bare variable without
        ?mode=ro.  A plain ``sqlite3.connect(db_file, ...)`` (no 'mode=ro'
        anywhere on that logical connect expression) is a violation."""
        source = self._source()

        # Find every sqlite3.connect(...) call and check it contains mode=ro.
        # Strategy: split on "sqlite3.connect(" and check each fragment that
        # represents a call site (skip the first fragment which is before the
        # first call).
        fragments = source.split("sqlite3.connect(")
        violations: list[str] = []
        for frag in fragments[1:]:
            # Take up to the matching closing paren — grab the argument list.
            # We only need to check whether 'mode=ro' appears in the argument.
            # Grab the first 200 chars (plenty for a single-line call).
            call_text = frag[:200]
            if "mode=ro" not in call_text:
                # Show a snippet for diagnostics
                violations.append(call_text.split("\n")[0].strip())

        assert not violations, (
            f"Found {len(violations)} sqlite3.connect site(s) in analytics.py "
            f"without ?mode=ro:\n" + "\n".join(f"  - {v}" for v in violations)
        )

    def test_all_ro_connects_use_uri_true(self):
        """Every sqlite3.connect that has mode=ro must also have uri=True."""
        source = self._source()
        fragments = source.split("sqlite3.connect(")
        violations: list[str] = []
        for frag in fragments[1:]:
            call_text = frag[:300]
            if "mode=ro" in call_text and "uri=True" not in call_text:
                violations.append(call_text.split("\n")[0].strip())

        assert not violations, (
            f"Found {len(violations)} ?mode=ro connect site(s) missing uri=True:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    def test_expected_connect_site_count(self):
        """There are exactly 6 sqlite3.connect call sites in analytics.py.
        If this count changes, update the test and the review."""
        source = self._source()
        count = source.count("sqlite3.connect(")
        assert count == 6, (
            f"Expected 6 sqlite3.connect sites in analytics.py, found {count}. "
            "Update this test if sites were intentionally added or removed."
        )


# ---------------------------------------------------------------------------
# Class 2 — behavioral pin
# ---------------------------------------------------------------------------


class TestBehavioralPin:
    """Confirm that:
    (a) a ?mode=ro URI connection rejects writes (OperationalError), and
    (b) _get_shadow_cumulative_trajectory reads rows correctly via the
        read-only path (behavioral smoke through one real analytics fn).
    """

    def test_mode_ro_rejects_insert(self, tmp_path):
        """A ?mode=ro connection must raise OperationalError on INSERT."""
        db_file = _seed_shadow_db(tmp_path)
        conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=10.0)
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                "INSERT INTO shadow_history "
                "(ts_utc, trading_day, symphony_id, current_return, shadow_return) "
                "VALUES (?,?,?,?,?)",
                ("2026-01-13T14:30:00Z", "2026-01-13", "x", 1.0, 1.0),
            )
        conn.close()

    def test_shadow_trajectory_reads_rows_via_ro_path(self, tmp_path):
        """_get_shadow_cumulative_trajectory returns the expected row count
        when the underlying DB is opened read-only (the hardened code path)."""
        import database as _db

        db_file = _seed_shadow_db(tmp_path)

        # Clear cache so we get a fresh DB read.
        _db._shadow_cr_cache.clear()

        from analytics import _get_shadow_cumulative_trajectory

        trajectory = _get_shadow_cumulative_trajectory(_SYM_ID, db_file)

        assert trajectory is not None, (
            "_get_shadow_cumulative_trajectory returned None; expected a 3-element "
            "trajectory from the 3-row seeded DB."
        )
        assert len(trajectory) == 3, (
            f"Expected 3 trajectory points, got {len(trajectory)}: {trajectory}"
        )

    def test_mode_ro_allows_select(self, tmp_path):
        """A ?mode=ro connection must return rows on SELECT (sanity check)."""
        db_file = _seed_shadow_db(tmp_path)
        conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=10.0)
        rows = conn.execute("SELECT COUNT(*) FROM shadow_history").fetchone()
        conn.close()
        assert rows[0] == 3, f"Expected 3 rows in seeded DB via read-only connection, got {rows[0]}"
