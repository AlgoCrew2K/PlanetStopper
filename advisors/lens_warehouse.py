# Stub — implementation pending (TDD RED phase)
from __future__ import annotations

import sys


_WAREHOUSE_DB_BASENAME = "alphabot_warehouse.db"


def _sentinel_check(db_path):
    # Mirrors database._db_file() sentinel: under pytest, calling with no path
    # (which would resolve to the production alphabot_warehouse.db) must raise.
    if db_path is None and "pytest" in sys.modules:
        raise RuntimeError(
            f"test attempted to open the production warehouse DB "
            f"({_WAREHOUSE_DB_BASENAME}) — set db_path to a temp file in tests"
        )


def init_warehouse_db(path=None):
    _sentinel_check(path)
    raise NotImplementedError("init_warehouse_db not implemented")


def persist_lens_snapshot(
    lens,
    symbol,
    source,
    available,
    raw_payload,
    fetch_ts=None,
    db_path=None,
):
    _sentinel_check(db_path)
    raise NotImplementedError("persist_lens_snapshot not implemented")


def get_lens_snapshots(lens, symbol=None, since=None, db_path=None):
    _sentinel_check(db_path)
    raise NotImplementedError("get_lens_snapshots not implemented")
