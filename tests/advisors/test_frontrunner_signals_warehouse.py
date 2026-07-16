"""RED tests — AC-2 (signal snapshot persistence) for
advisors/frontrunner_signals.py.

Module under test: advisors.frontrunner_signals (NEW module, NOT-STARTED as of
this commit). Implementer: fr-data (sqlite-specialist).

CONTRACT SOURCE
---------------
AC-2 (feature-plans/frontrunner-signals.md): "Every non-cache-hit pull is
persisted to the warehouse DB (lens_warehouse third-DB pattern: append-only,
parameterized, _strip_secrets, pytest sentinel honoring temp db_path). Per-signal
rows carry at minimum: fr_key, ticker, window, threshold, comparator, rsi_live,
rsi_live_at, cagr, sharpe, sortino, calmar, max_drawdown, n_points,
vix_destination_json, total_strategy_count, fetch_ts. A read accessor returns the
latest snapshot's rows. No cross-DB joins in app code."

fr-data's posted persistence mechanics (2026-07-16, team-lead-ratified):
persistence is CONDITIONAL — triggered from INSIDE load_frontrunner_signals
itself (a closure flag around _bounded_fetch_fn detects cache-miss vs cache-hit,
mirrors community_strats._timeout_fired). A cache-MISS (fresh Atlas pull) writes
N rows sharing one fetch_ts; a cache-HIT within the TTL window writes ZERO new
rows. `get_latest_signal_rows(*, db_path=None) -> list[dict]` returns the rows
sharing the latest fetch_ts. Pytest sentinel mirrors
lens_warehouse._warehouse_db_file exactly (RuntimeError, production-basename
message, only fires when db_path=None under pytest).

DE-PRODUCTIZATION (2026-07-16, feature-plans/frontrunner-signals.md ADDENDUM 2
AC-R2, operator directive): the classification-row + run-marker persistence
layer this file used to also cover (persist_classification_run,
get_latest_classifications, get_latest_run_marker, the
frontrunner_classification_snapshots + frontrunner_run_metadata tables) was
REMOVED — it productized a one-time PM cull-analysis deliverable that was
never asked for as a product feature. The builder still classifies every run
in memory (advisors/frontrunner_builder.py); it no longer persists or
renders the result. This file now covers ONLY the AC-2 signal-snapshot
persistence (load_frontrunner_signals' own warehouse write), which AC-R3
explicitly retains.

Pytest sentinel: mirrors lens_warehouse._warehouse_db_file — db_path=None
under pytest raises RuntimeError naming the production basename
(alphabot_warehouse.db); an explicit temp db_path is always used in this file.
"""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def mod():
    from advisors import frontrunner_signals

    return frontrunner_signals


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "test_frontrunner_warehouse.db")


def _raw_atlas_doc(fr_key: str = "TEST:10:50") -> dict:
    ticker, window, threshold = fr_key.split(":")
    return {
        "fr_key": fr_key,
        "comparator": "gt",
        "ticker": ticker,
        "window": int(window),
        "threshold": float(threshold),
        "rsi_live": 55.5,
        "rsi_live_at": "2026-07-16 07:01:28.970000",
        "total_strategy_count": 10,
        "vix_destination_summary": {"VIXY": 5, "UVXY": 5},
        "backtest": {
            "summary": {
                "cagr": 0.05,
                "sharpe": 0.5,
                "sortino": 1.0,
                "calmar": 0.4,
                "max_drawdown": 0.1,
                "volatility": 0.08,
                "cumulative_return": 1.5,
                "first_day": "2006-01-01",
                "last_day": "2026-07-15",
                "n_points": 5000,
            },
            "computedAt": "2026-07-16 07:02:00.000000",
            "true_ticker": "VIXY",
            "false_ticker": "BIL",
        },
    }


# ---------------------------------------------------------------------------
# Pytest sentinel — signal snapshots table
# ---------------------------------------------------------------------------


def test_signal_snapshots_sentinel_refuses_production_db_path_under_pytest(mod):
    """Mirrors lens_warehouse._warehouse_db_file — db_path=None under pytest
    must raise RuntimeError naming the production basename, never silently
    open the real alphabot_warehouse.db."""
    with pytest.raises(RuntimeError, match="alphabot_warehouse"):
        mod.get_latest_signal_rows(db_path=None)


def test_init_frontrunner_signal_snapshots_db_is_idempotent(mod, db_path):
    """Calling init twice on the same path must not raise (IF NOT EXISTS DDL)."""
    mod.init_frontrunner_signal_snapshots_db(db_path)
    mod.init_frontrunner_signal_snapshots_db(db_path)  # must not raise


# ---------------------------------------------------------------------------
# AC-2: cache-miss persists, cache-hit does not duplicate
# ---------------------------------------------------------------------------


def test_cache_miss_persists_n_rows_sharing_one_fetch_ts(mod, db_path, monkeypatch):
    """A fresh (cache-miss) pull must persist one row per raw doc, all sharing
    a single fetch_ts (not N distinct timestamps)."""
    monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")
    docs = [_raw_atlas_doc("AAA:10:50"), _raw_atlas_doc("BBB:10:60"), _raw_atlas_doc("CCC:10:70")]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "advisors.atlas_cache.cached_pull",
            lambda col, fn, **kw: fn(),
        )
        mp.setattr("pymongo.MongoClient", lambda *a, **kw: _mongo_client_returning(docs))
        mod.load_frontrunner_signals(db_path=db_path)

    rows = mod.get_latest_signal_rows(db_path=db_path)
    assert len(rows) == 3, f"expected 3 persisted rows, got {len(rows)}: {rows}"
    fetch_ts_values = {r["fetch_ts"] for r in rows}
    assert len(fetch_ts_values) == 1, (
        f"all rows from one pull must share ONE fetch_ts, got {fetch_ts_values}"
    )


def _mongo_client_returning(docs):
    from unittest.mock import MagicMock

    mock_cursor = MagicMock()
    mock_cursor.__iter__ = MagicMock(side_effect=lambda: iter(docs))
    mock_cursor.limit = MagicMock(return_value=mock_cursor)
    mock_collection = MagicMock()
    mock_collection.find.return_value = mock_cursor
    mock_db = MagicMock()
    mock_db.__getitem__ = MagicMock(return_value=mock_collection)
    mock_client = MagicMock()
    mock_client.__getitem__ = MagicMock(return_value=mock_db)
    return mock_client


def test_cache_hit_within_ttl_persists_zero_new_rows(mod, db_path, monkeypatch):
    """A second call served from a fresh (within-TTL) cache row must persist
    NOTHING — the warehouse row count stays at the first call's N, not 2N."""
    monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")
    docs = [_raw_atlas_doc("DDD:10:80")]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "advisors.atlas_cache.cached_pull",
            lambda col, fn, **kw: fn(),
        )
        mp.setattr("pymongo.MongoClient", lambda *a, **kw: _mongo_client_returning(docs))
        mod.load_frontrunner_signals(db_path=db_path)  # cache-miss: persists 1 row

    rows_after_first = mod.get_latest_signal_rows(db_path=db_path)
    assert len(rows_after_first) == 1

    # Second call: served from cache (cached_pull returns the cached docs
    # directly, fetch_fn never called) — must NOT persist again.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("advisors.atlas_cache.cached_pull", lambda col, fn, **kw: docs)
        mod.load_frontrunner_signals(db_path=db_path)

    rows_after_second = mod.get_latest_signal_rows(db_path=db_path)
    assert len(rows_after_second) == 1, (
        f"cache-hit must persist zero new rows; expected 1 total, got {len(rows_after_second)}"
    )


def test_get_latest_signal_rows_returns_required_columns(mod, db_path, monkeypatch):
    """AC-2: 'Per-signal rows carry at minimum: fr_key, ticker, window,
    threshold, comparator, rsi_live, rsi_live_at, cagr, sharpe, sortino,
    calmar, max_drawdown, n_points, vix_destination_json,
    total_strategy_count, fetch_ts'."""
    monkeypatch.setenv("MONGO_URI", "mongodb+srv://fake-uri-for-test/db")
    docs = [_raw_atlas_doc("EEE:10:90")]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("advisors.atlas_cache.cached_pull", lambda col, fn, **kw: fn())
        mp.setattr("pymongo.MongoClient", lambda *a, **kw: _mongo_client_returning(docs))
        mod.load_frontrunner_signals(db_path=db_path)

    rows = mod.get_latest_signal_rows(db_path=db_path)
    assert len(rows) == 1
    row = rows[0]
    required = {
        "fr_key",
        "ticker",
        "window",
        "threshold",
        "comparator",
        "rsi_live",
        "rsi_live_at",
        "cagr",
        "sharpe",
        "sortino",
        "calmar",
        "max_drawdown",
        "n_points",
        "vix_destination_json",
        "total_strategy_count",
        "fetch_ts",
    }
    missing = required - set(row.keys())
    assert not missing, f"row missing required AC-2 columns: {sorted(missing)}"
    assert row["fr_key"] == "EEE:10:90"


def test_no_cross_db_join_signal_snapshots_db_is_isolated_from_state_db(mod, db_path):
    """AC-2: 'No cross-DB joins in app code' — the warehouse DB must be a
    standalone SQLite file with no ATTACH/foreign reference to the state or
    optimization DB. Structural check: opening it directly must show only
    frontrunner-owned tables, never database.py's state-DB tables."""
    mod.init_frontrunner_signal_snapshots_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert "bot_state" not in tables, (
        "warehouse DB must never carry state-DB tables (no cross-DB join)"
    )
