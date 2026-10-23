"""Shared loaders and temporary-database builders for the capture script's tests.

The temporary databases are built from the repository's own migration SQL, so the
column shapes are the producer's; the rows are synthetic and exist only to exercise
the capture tooling. No test here reads a production database.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

TOOL_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOL_DIR.parents[2]
MIGRATIONS = REPO_ROOT / "migrations"

# Table-creating and column-adding migrations, in the order the producer applies them.
_SHADOW_MIGRATIONS = ("008_shadow_history.sql", "015_shadow_history_position_epoch.sql")
_EXIT_MIGRATIONS = (
    "005_exit_triggers.sql",
    "011_exit_triggers_port.sql",
    "029_exit_triggers_also_true.sql",
)
_BOT_STATE_DDL = "CREATE TABLE IF NOT EXISTS bot_state (id INTEGER PRIMARY KEY, data TEXT)"


def load_capture_module(name: str = "rt_capture_under_test", path: Path | None = None):
    """Import capture.py by path under `name`; the script resolves helpers via sys.modules."""
    source = path or TOOL_DIR / "capture.py"
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def capture():
    module = load_capture_module()
    yield module
    sys.modules.pop("rt_capture_under_test", None)


def build_state_db(path: Path, *, wal: bool = False) -> sqlite3.Connection:
    """Create the three tables the cases read, from the real migrations; return a writer."""
    conn = sqlite3.connect(path)
    if wal:
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(_BOT_STATE_DDL)
    for name in (*_SHADOW_MIGRATIONS, *_EXIT_MIGRATIONS):
        conn.executescript((MIGRATIONS / name).read_text(encoding="utf-8"))
    conn.commit()
    return conn


def insert_shadow(
    conn,
    *,
    symphony_id,
    trading_day,
    cycle_id,
    ts_utc,
    ts_et=None,
    current_return=0.0,
    shadow_return=0.0,
    is_post_trigger=0,
    trigger_id=None,
    position_epoch=None,
):
    conn.execute(
        "INSERT INTO shadow_history (ts_utc, ts_et, trading_day, symphony_id, account_id, cycle_id, "
        "current_return, shadow_return, is_post_trigger, trigger_id, position_epoch) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ts_utc,
            ts_et or ts_utc.replace("Z", ""),
            trading_day,
            symphony_id,
            "acct-1",
            cycle_id,
            current_return,
            shadow_return,
            is_post_trigger,
            trigger_id,
            position_epoch,
        ),
    )


def insert_exit(
    conn,
    *,
    symphony_id,
    ts_utc,
    ts_et=None,
    reason="Trailing Stop",
    at_return=-1.5,
    gate_state_json='{"hwm": 3.2}',
    cycle_id=None,
):
    conn.execute(
        "INSERT INTO exit_triggers (ts_utc, ts_et, symphony_id, account_id, triggered_reason, at_return, "
        "gate_state_json, cycle_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ts_utc,
            ts_et or ts_utc.replace("Z", ""),
            symphony_id,
            "acct-1",
            reason,
            at_return,
            gate_state_json,
            cycle_id,
        ),
    )


@pytest.fixture
def db_builder():
    """Builders for a temporary state database: .build(path), .shadow(conn, ...), .exit(conn, ...)."""
    return SimpleNamespace(build=build_state_db, shadow=insert_shadow, exit=insert_exit)
