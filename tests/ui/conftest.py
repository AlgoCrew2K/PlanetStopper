"""
Shared conftest for tests/ui/.

Stubs get_api_state_dict exactly as tests/app/conftest.py does, so Flask
routes can be exercised without a live DB or engine process.

Also provides a known-state minimal SQLite fixture (minimal_state_db) for
tests that need deterministic DB data (FIX-1 through FIX-4 hist array assertions).
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest

_API_STATE_STUB = {
    "bot_state": {},
    "is_locked": False,
    "port_state": {},
    "exit_authority": {},
    "daemon_started_at": None,
}


# Minimal bot_state stub for the advisor route's name->hash resolution (RC-6).
# Routes now call database.load_state() to resolve a display NAME to a Composer
# hash before calling fetch_symphony_score; without a mapping, RC-6 fails loudly.
# Pre-RC-6 tests that use symphony_id="sym-test" need this mapping so they bypass
# the RC-6 guard and reach their mocked fetch_symphony_score/propose_* calls.
# Tests that explicitly mock database.load_state themselves override this stub.
_ADVISOR_ROUTE_BOT_STATE_STUB = {
    "sym-test": {"name": "sym-test", "account_uuid": "acc-test"},
}


@pytest.fixture(autouse=True)
def _stub_get_api_state_dict():
    import app as app_module

    with patch.object(app_module, "get_api_state_dict", return_value=_API_STATE_STUB):
        yield


@pytest.fixture(autouse=True)
def _stub_database_load_state_for_advisor_routes():
    """Stub database.load_state to include 'sym-test' so advisor route tests that
    predate RC-6 (which added name->hash resolution) can still reach their mocked
    fetch_symphony_score / propose_* call sites.

    Tests that supply their own database.load_state mock override this autouse stub.
    """
    with patch("database.load_state", return_value=_ADVISOR_ROUTE_BOT_STATE_STUB):
        yield


@pytest.fixture
def client():
    """Flask test client bound to the in-process app instance."""
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Known-state SQLite fixture
# PM requirement: without a known-state fixture, len>0 assertions on hist arrays
# are meaningless against a live DB that may or may not have data.
# ---------------------------------------------------------------------------

_KNOWN_BOT_STATE = {
    "sym_alpha": {
        "id": "sym_alpha",
        "name": "Alpha Symphony",
        "position": 0.85,
        "simple_return": 0.042,
        "net_deposits": 50000.0,
        "time_weighted_return": 0.038,
        "triggered": False,
        "armed": True,
        "tp_armed": False,
        "para_armed": False,
        "current_return": 4.2,
        "mc_prob": 0.73,
        "guard_alpha": 0.0,
    },
    "sym_beta": {
        "id": "sym_beta",
        "name": "Beta Symphony",
        "position": 0.60,
        "simple_return": 0.021,
        "net_deposits": 30000.0,
        "time_weighted_return": 0.018,
        "triggered": True,
        "triggered_at_return": 8.5,
        "triggered_reason": "Take-Profit",
        "current_return": 8.2,
        "mc_prob": 0.51,
        "guard_alpha": 0.3,
    },
}

_KNOWN_CHART_HISTORY = {
    "symphonies": {
        "sym_alpha": [
            {"date": f"2026-04-{d:02d}", "bot": round(d * 0.1, 2), "held": round(d * 0.08, 2)}
            for d in range(1, 32)
        ],
        "sym_beta": [
            {"date": f"2026-04-{d:02d}", "bot": round(d * 0.15, 2), "held": round(d * 0.12, 2)}
            for d in range(1, 32)
        ],
    }
}

# 35-day chart_archive: covers April 1–28 + May 1–7 = 35 days
_KNOWN_CHART_ARCHIVE_ROWS = []
for _i in range(28):
    _date = f"2026-04-{_i + 1:02d}"
    for _sym in ("sym_alpha", "sym_beta"):
        _KNOWN_CHART_ARCHIVE_ROWS.append(
            (
                _date,
                _sym,
                json.dumps(
                    {
                        "live_ret": round((_i + 1) * 0.001, 4),
                        "f_ret": round((_i + 1) * 0.0012, 4),
                        "weight": 1.0,
                    }
                ),
            )
        )
for _i in range(7):
    _date = f"2026-05-{_i + 1:02d}"
    for _sym in ("sym_alpha", "sym_beta"):
        _KNOWN_CHART_ARCHIVE_ROWS.append(
            (
                _date,
                _sym,
                json.dumps(
                    {
                        "live_ret": round((_i + 29) * 0.001, 4),
                        "f_ret": round((_i + 29) * 0.0012, 4),
                        "weight": 1.0,
                    }
                ),
            )
        )


@pytest.fixture
def minimal_state_db(tmp_path):
    """Create a minimal SQLite DB with known state.

    Provides:
    - bot_state table: 2 symphonies (sym_alpha armed, sym_beta triggered)
    - chart_history table: 31 days of per-symphony bot/held series
    - chart_archive table: 35 days × 2 symphonies of live_ret/f_ret data
    - execution_lock table: not locked
    - strategies table: default strategy for each symphony

    Returns the path to the temporary DB file.
    Usage in tests: set DB_PATH env var or pass to database functions directly.
    """
    db_path = str(tmp_path / "test_minimal_state.db")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Core tables
    cur.execute("CREATE TABLE bot_state (id INTEGER PRIMARY KEY, data TEXT)")
    cur.execute("INSERT INTO bot_state VALUES (1, ?)", (json.dumps(_KNOWN_BOT_STATE),))

    cur.execute("CREATE TABLE chart_history (id INTEGER PRIMARY KEY, data TEXT)")
    cur.execute("INSERT INTO chart_history VALUES (1, ?)", (json.dumps(_KNOWN_CHART_HISTORY),))

    cur.execute(
        "CREATE TABLE chart_archive (date TEXT, symphony_id TEXT, data TEXT, "
        "UNIQUE(date, symphony_id))"
    )
    cur.executemany(
        "INSERT OR IGNORE INTO chart_archive VALUES (?, ?, ?)",
        _KNOWN_CHART_ARCHIVE_ROWS,
    )

    cur.execute(
        "CREATE TABLE execution_lock (id INTEGER PRIMARY KEY, is_locked INTEGER, timestamp REAL)"
    )
    cur.execute("INSERT INTO execution_lock VALUES (1, 0, 0.0)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS strategies (
            symphony_id TEXT PRIMARY KEY,
            params TEXT,
            locked_vars TEXT
        )
    """)
    for sym_id in ("sym_alpha", "sym_beta"):
        cur.execute(
            "INSERT INTO strategies VALUES (?, ?, ?)",
            (
                sym_id,
                json.dumps(
                    {
                        "TRIGGER_THRESHOLD_PCT": 15.0,
                        "TAKE_PROFIT_MC_PCT": 5.0,
                        "MAX_SQUEEZE_FLOOR": 0.20,
                        "VWAP_CROSS_HWM_PCT": 1.0,
                        "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
                        "MAX_PARABOLIC_SQUEEZE": 0.50,
                        "VWAP_BLEED_MULTIPLIER": 1.5,
                        "VWAP_BLEED_TICKS": 10,
                    }
                ),
                json.dumps(["TRIGGER_THRESHOLD_PCT"]),
            ),
        )

    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def db_patched_to_minimal(minimal_state_db, monkeypatch):
    """Patch database module to use the known-state minimal DB.

    Sets DB_PATH env var so database.get_connection() / get_ro_connection()
    point at the deterministic test DB instead of the live alphabot_state.db.
    """
    monkeypatch.setenv("DB_PATH", minimal_state_db)
    # Also patch the module-level DB_FILE so existing open connections redirect
    import database as db_module

    monkeypatch.setattr(db_module, "DB_FILE", minimal_state_db)
    return minimal_state_db
