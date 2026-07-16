"""RED tests — AC-2 (signal snapshot persistence) + PM-ruling extension
(classification-row + run-marker persistence) for advisors/frontrunner_signals.py.

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

PM-RULING EXTENSION (committed 101df72a — "AC-7 renders PERSISTED rows ONLY"):
a SECOND table pair, same warehouse DB file, no cross-DB join:
    persist_classification_run(symphony_id, classification_rows, *,
        signals_unavailable=False, reason=None, computed_at=None,
        db_path=None) -> None
    get_latest_classifications(symphony_id=None, *, db_path=None) -> list[dict]
    get_latest_run_marker(symphony_id=None, *, db_path=None) -> dict | None

fr-data's FINAL locked mechanics (2026-07-16, multiple confirmation rounds):
  - persist_classification_run writes BOTH tables atomically every call: one
    classification row per fr_key into frontrunner_classification_snapshots,
    plus exactly ONE row into frontrunner_run_metadata, sharing a single
    computed_at (explicit if passed, else generated once, never per-row).
  - This holds even when classification_rows=[] AND signals_unavailable=True
    (the AC-5 degraded case) — the run marker still gets written.
  - classification_rows item shape (caller-supplied, native types):
    {fr_key, fn, comparator, branch_path (native list — fr-data json.dumps
    it internally), rsi_live, rsi_live_at, cagr, sharpe, sortino, calmar,
    max_drawdown, classification, signal_fetch_ts}. symphony_id and
    computed_at are NOT per-row.
  - symphony_id is NOT NULL at the schema level (fr-engine confirmed
    run_frontrunner_build() is a plain per-symphony loop — no fleet-wide
    write ever happens in production).
  - get_latest_classifications(symphony_id=None) = greatest-computed_at-PER-
    symphony_id (one row-set per symphony, NOT a single global max).
  - get_latest_run_marker(symphony_id=None) = the single most-recently-
    computed row across THE WHOLE TABLE, arbitrary which symphony —
    explicitly NO aggregation logic (fr-data, plainly confirmed, twice).
    A RED test must pin this "arbitrary single row, no OR-across-symphonies"
    behavior explicitly so nobody accidentally builds real fleet aggregation
    into this accessor later without deliberately deciding to.
  - get_latest_run_marker with zero rows ever written returns None, not [].

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


def _classification_row(
    fr_key: str = "SPY:10:80",
    classification: str = "keep",
    branch_path: list | None = None,
) -> dict:
    return {
        "fr_key": fr_key,
        "fn": "relative-strength-index",
        "comparator": "gt",
        "branch_path": branch_path if branch_path is not None else ["true"],
        "rsi_live": 59.9,
        "rsi_live_at": "2026-07-16 07:01:28.970000",
        "cagr": 0.06,
        "sharpe": 1.0,
        "sortino": 2.0,
        "calmar": 0.9,
        "max_drawdown": 0.05,
        "classification": classification,
        "signal_fetch_ts": "2026-07-16T15:00:00Z",
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
        "fr_key", "ticker", "window", "threshold", "comparator", "rsi_live",
        "rsi_live_at", "cagr", "sharpe", "sortino", "calmar", "max_drawdown",
        "n_points", "vix_destination_json", "total_strategy_count", "fetch_ts",
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
        tables = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    assert "bot_state" not in tables, "warehouse DB must never carry state-DB tables (no cross-DB join)"


# ---------------------------------------------------------------------------
# PM-ruling extension: classification + run-marker persistence
# ---------------------------------------------------------------------------


def test_classification_sentinel_refuses_production_db_path_under_pytest(mod):
    with pytest.raises(RuntimeError, match="alphabot_warehouse"):
        mod.get_latest_classifications(db_path=None)


def test_run_marker_sentinel_refuses_production_db_path_under_pytest(mod):
    with pytest.raises(RuntimeError, match="alphabot_warehouse"):
        mod.get_latest_run_marker(db_path=None)


def test_persist_classification_run_writes_one_row_per_fr_key(mod, db_path):
    rows = [_classification_row("SPY:10:80", "keep"), _classification_row("SPY:10:31", "remove")]
    mod.persist_classification_run("sym-A", rows, db_path=db_path)

    persisted = mod.get_latest_classifications(symphony_id="sym-A", db_path=db_path)
    assert len(persisted) == 2, f"expected 2 classification rows, got {len(persisted)}"
    assert {r["fr_key"] for r in persisted} == {"SPY:10:80", "SPY:10:31"}


def test_persist_classification_run_writes_exactly_one_run_marker_row(mod, db_path):
    rows = [_classification_row("SPY:10:80", "keep")]
    mod.persist_classification_run("sym-B", rows, signals_unavailable=False, db_path=db_path)

    marker = mod.get_latest_run_marker(symphony_id="sym-B", db_path=db_path)
    assert marker is not None
    assert marker["signals_unavailable"] is False


def test_persist_classification_run_shares_one_computed_at_across_rows_and_marker(mod, db_path):
    """Both classification rows AND the run-marker row must share the SAME
    computed_at value from one persist call — never per-row generation."""
    rows = [_classification_row("A:10:1", "keep"), _classification_row("B:10:2", "prune")]
    mod.persist_classification_run("sym-C", rows, computed_at="2026-07-16T15:00:00Z", db_path=db_path)

    persisted = mod.get_latest_classifications(symphony_id="sym-C", db_path=db_path)
    marker = mod.get_latest_run_marker(symphony_id="sym-C", db_path=db_path)
    computed_at_values = {r["computed_at"] for r in persisted}
    assert computed_at_values == {"2026-07-16T15:00:00Z"}, (
        f"classification rows must share one computed_at, got {computed_at_values}"
    )
    assert marker["computed_at"] == "2026-07-16T15:00:00Z"


def test_degraded_call_with_empty_rows_still_produces_one_run_marker_row(mod, db_path):
    """AC-5 degraded case (fr-data-specified RED requirement): zero
    classification rows is valid input — the run marker must STILL be
    written, recording the degradation + reason."""
    mod.persist_classification_run(
        "sym-D", [], signals_unavailable=True, reason="AtlasFetchTimeout", db_path=db_path
    )

    persisted = mod.get_latest_classifications(symphony_id="sym-D", db_path=db_path)
    marker = mod.get_latest_run_marker(symphony_id="sym-D", db_path=db_path)
    assert persisted == [], "degraded call with zero rows must persist zero classification rows"
    assert marker is not None, "the run marker must still be written even with zero classification rows"
    assert marker["signals_unavailable"] is True
    assert marker["reason"] == "AtlasFetchTimeout"


def test_symphony_id_is_required_not_null_at_schema_level(mod, db_path):
    """fr-data/fr-engine-confirmed: symphony_id is ALWAYS populated on write —
    run_frontrunner_build() is a plain per-symphony loop, no batch-aggregation
    call site exists. The DDL enforces NOT NULL; a direct raw INSERT with a
    NULL symphony_id must fail at the schema level."""
    mod.init_frontrunner_signal_snapshots_db(db_path)  # ensure schema exists
    # Trigger classification-table creation via a real persist call first.
    mod.persist_classification_run("sym-E", [_classification_row()], db_path=db_path)

    conn = sqlite3.connect(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO frontrunner_classification_snapshots "
                "(symphony_id, fr_key, fn, comparator, branch_path, classification, computed_at) "
                "VALUES (NULL, 'X:1:1', 'relative-strength-index', 'gt', '[]', 'keep', '2026-07-16T00:00:00Z')"
            )
            conn.commit()
    finally:
        conn.close()


def test_get_latest_classifications_symphony_id_none_is_per_symphony_latest_not_global_max(
    mod, db_path
):
    """symphony_id=None must return EVERY symphony's own latest batch (one
    row-set per symphony), not a single global-max-computed_at slice that
    would silently drop older symphonies' rows."""
    mod.persist_classification_run(
        "sym-old", [_classification_row("OLD:10:1")], computed_at="2026-07-16T10:00:00Z", db_path=db_path
    )
    mod.persist_classification_run(
        "sym-new", [_classification_row("NEW:10:2")], computed_at="2026-07-16T15:00:00Z", db_path=db_path
    )

    all_rows = mod.get_latest_classifications(symphony_id=None, db_path=db_path)
    symphony_ids_present = {r["symphony_id"] for r in all_rows}
    assert "sym-old" in symphony_ids_present, (
        "symphony_id=None dropped sym-old's rows — must be per-symphony-latest, "
        "not a single global-max-computed_at slice"
    )
    assert "sym-new" in symphony_ids_present


def test_get_latest_classifications_returns_the_newest_batch_per_symphony_on_rerun(mod, db_path):
    """A symphony re-run (on-demand executor) must serve its OWN latest batch,
    not a stale earlier one — two persist calls for the same symphony_id,
    get_latest_classifications(symphony_id=...) returns only the second."""
    mod.persist_classification_run(
        "sym-F", [_classification_row("OLD:10:1")], computed_at="2026-07-16T10:00:00Z", db_path=db_path
    )
    mod.persist_classification_run(
        "sym-F", [_classification_row("NEW:10:2")], computed_at="2026-07-16T15:00:00Z", db_path=db_path
    )

    rows = mod.get_latest_classifications(symphony_id="sym-F", db_path=db_path)
    fr_keys = {r["fr_key"] for r in rows}
    assert fr_keys == {"NEW:10:2"}, (
        f"expected only the newest batch's fr_key, got {fr_keys} — stale rows from the "
        "earlier persist call leaked through"
    )


def test_get_latest_run_marker_with_symphony_id_scopes_to_that_symphony(mod, db_path):
    mod.persist_classification_run(
        "sym-G", [], signals_unavailable=True, reason="AtlasFetchTimeout", db_path=db_path
    )
    mod.persist_classification_run("sym-H", [_classification_row()], signals_unavailable=False, db_path=db_path)

    marker_g = mod.get_latest_run_marker(symphony_id="sym-G", db_path=db_path)
    marker_h = mod.get_latest_run_marker(symphony_id="sym-H", db_path=db_path)
    assert marker_g["signals_unavailable"] is True
    assert marker_h["signals_unavailable"] is False


def test_get_latest_run_marker_symphony_id_none_is_arbitrary_single_row_no_aggregation(
    mod, db_path
):
    """fr-data, plainly confirmed twice: symphony_id=None returns the SINGLE
    most-recently-computed row across the WHOLE table, arbitrary which
    symphony that happens to be — NO aggregation logic (no
    "any-symphony-degraded implies True", no OR-across-rows).

    Adversarial construction: two symphonies, one degraded (older
    computed_at) and one healthy (newer computed_at). A real fleet-aggregation
    accessor would report signals_unavailable=True (something IS degraded);
    this accessor must report the NEWEST row's own value — False, misleadingly
    from a fleet-health perspective, but structurally correct per the locked
    contract. This test exists specifically so nobody accidentally builds real
    aggregation into this accessor later without deliberately deciding to."""
    mod.persist_classification_run(
        "sym-degraded",
        [],
        signals_unavailable=True,
        reason="AtlasFetchTimeout",
        computed_at="2026-07-16T10:00:00Z",
        db_path=db_path,
    )
    mod.persist_classification_run(
        "sym-healthy",
        [_classification_row()],
        signals_unavailable=False,
        computed_at="2026-07-16T15:00:00Z",
        db_path=db_path,
    )

    marker = mod.get_latest_run_marker(symphony_id=None, db_path=db_path)
    assert marker is not None
    assert marker["signals_unavailable"] is False, (
        "symphony_id=None must return the single newest-computed row's own value "
        "(healthy, computed later) — NOT an aggregated any-symphony-degraded=True "
        "signal. If this fails with True, real aggregation logic has been added "
        "where the locked contract says there must be none."
    )
    assert marker["computed_at"] == "2026-07-16T15:00:00Z"


def test_get_latest_run_marker_returns_none_when_no_rows_ever_written(mod, db_path):
    """Zero rows ever written -> None, not [] (dict|None return type, not a list)."""
    mod.init_frontrunner_signal_snapshots_db(db_path)  # schema exists, but no persist call made
    marker = mod.get_latest_run_marker(db_path=db_path)
    assert marker is None
