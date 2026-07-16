"""Frontrunner Signals — live Atlas signal ingestion + warehouse persistence.

Pulls the `captplanet.frontrunners` Atlas collection (one doc per
`TICKER:WINDOW:THRESHOLD` RSI-frontrunner check) through the daily atlas_cache
seam, normalizes each doc into a typed row, and persists non-cache-hit pulls
into the warehouse third-DB (`alphabot_warehouse.db` — separate from the state
DB and the optimization DB; no cross-DB joins).

This module also owns the write/read accessors for the PM-ruling classification
extension (per-symphony FR-check classification rows + the AC-5
`signals_unavailable` run marker) — a second table pair in the SAME warehouse
DB file, written by advisors/frontrunner_builder.py's compute path via
`persist_classification_run`, read by the AI Advisor Frontrunner tab via
`get_latest_classifications` / `get_latest_run_marker`.

`classify_fr_checks` (the AC-4 pure edge-classifier) is a SEPARATE function
implemented by fr-engine in this same file — not implemented here.

Public surface
--------------
load_frontrunner_signals(*, force_refresh=False, db_path=None) -> dict
init_frontrunner_signal_snapshots_db(path=None) -> None
get_latest_signal_rows(*, db_path=None) -> list[dict]
persist_classification_run(symphony_id, classification_rows, *,
    signals_unavailable=False, reason=None, computed_at=None, db_path=None) -> None
get_latest_classifications(symphony_id=None, *, db_path=None) -> list[dict]
get_latest_run_marker(symphony_id=None, *, db_path=None) -> dict | None

Design invariants
-----------------
- Off-execution-path: never imported by alpha_bot_execution.py / the minute engine.
- pymongo is lazy-imported inside the fetch closure only (CC-2).
- D-1 never-raises on load_frontrunner_signals: reason = "AtlasFetchTimeout" |
  type(exc).__name__ | "AtlasCacheUnavailable".
- Warehouse writes never raise past their public entry point; a persist
  failure (including the pytest sentinel firing with db_path=None) is
  logged and swallowed inside load_frontrunner_signals — the caller always
  gets a valid result dict back.
- Pytest sentinel mirrors lens_warehouse._warehouse_db_file exactly:
  db_path=None under pytest raises RuntimeError naming the production
  basename; production callers never need to pass db_path.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import sqlite3
import sys
from datetime import UTC, datetime

from advisors import atlas_cache

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Dedicated atlas_cache collection key — a label decoupled from the real Mongo
# collection queried in _fetch_fn (mirrors advisors/universe_provider.py's
# _CACHE_COLLECTION precedent). Chosen to byte-match the worktree's existing
# untracked dev-cache row (captured 2026-07-16) so manual dev-time
# verification reuses that cache rather than re-pulling Atlas.
_COLLECTION_NAME = "captplanet.frontrunners.fr_checks"

# Daily cache — AC-1: a week-old RSI value must never be served as today's.
_CACHE_TTL_DAYS = 1

# Wall-clock bound for the live Atlas fetch leg (DE-CS-002 SRV/DNS-hang
# pattern, mirrored from advisors/community_strats.py's _ATLAS_FETCH_TIMEOUT_S).
# Lighter than community_strats.py's 45.0s: this collection is ~3,402 docs
# with no edn_string-scale field (observed ~776 bytes/doc in the dev-cache
# pull) and no unindexed server-side sort, so a single full find() comfortably
# fits well inside 30s while the bound still catches a genuine SRV/DNS hang.
_FETCH_TIMEOUT_S: float = 30.0

# Exclusion-style projection: AC-1's own wording is "excludes
# backtest.equity_curve", not an inclusion allowlist — an inclusion list
# risks silently dropping a field AC-2 needs (e.g. total_strategy_count) if
# it goes stale. "_id": 0 suppresses the non-JSON-serializable BSON ObjectId
# (DE-ATLAS-CACHE-001); backtest.equity_curve is the only known large
# per-doc field.
_PROJECTION: dict = {"_id": 0, "backtest.equity_curve": 0}

# Same warehouse DB file as advisors/lens_warehouse.py — a THIRD, separate
# SQLite file from the state DB and the optimization DB. No cross-DB joins.
_WAREHOUSE_DB_BASENAME = "alphabot_warehouse.db"


class _AtlasFetchTimeout(Exception):
    """Raised internally when the bounded Atlas fetch exceeds _FETCH_TIMEOUT_S."""


# ---------------------------------------------------------------------------
# Warehouse connection helpers (mirrors advisors/lens_warehouse.py exactly)
# ---------------------------------------------------------------------------


def _warehouse_db_file(path=None) -> str:
    """Resolve the warehouse DB path and enforce the pytest sentinel.

    Mirrors lens_warehouse._warehouse_db_file: under pytest, an explicit path
    is required — omitting it raises RuntimeError naming the production
    basename rather than silently opening the real alphabot_warehouse.db.
    """
    if path is None:
        if "pytest" in sys.modules:
            raise RuntimeError(
                f"test attempted to open the production warehouse DB "
                f"({_WAREHOUSE_DB_BASENAME}) — set db_path to a temp file in tests"
            )
        return _WAREHOUSE_DB_BASENAME
    return str(path)


def _connect(resolved_path: str) -> sqlite3.Connection:
    """Open a WAL-mode connection to the warehouse DB with row access by name."""
    conn = sqlite3.connect(resolved_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Schema — frontrunner_signal_snapshots (AC-2)
# ---------------------------------------------------------------------------

# "window" is quoted throughout — SQLite reserves WINDOW for window-function
# syntax; unquoted usage happens to parse today but quoting is defensive.
_CREATE_SIGNAL_SNAPSHOTS_SQL = """
CREATE TABLE IF NOT EXISTS frontrunner_signal_snapshots (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    fr_key                TEXT    NOT NULL,
    ticker                TEXT    NOT NULL,
    "window"              INTEGER,
    threshold             REAL,
    comparator            TEXT,
    rsi_live              REAL,
    rsi_live_at           TEXT,
    cagr                  REAL,
    sharpe                REAL,
    sortino               REAL,
    calmar                REAL,
    max_drawdown          REAL,
    n_points              INTEGER,
    vix_destination_json  TEXT,
    total_strategy_count  INTEGER,
    true_ticker           TEXT,
    false_ticker          TEXT,
    fetch_ts              TEXT    NOT NULL,
    created_at            TEXT    DEFAULT (datetime('now'))
)
"""

# accelerates get_latest_signal_rows's MAX(fetch_ts) lookup and WHERE fetch_ts=? scan
_CREATE_SIGNAL_SNAPSHOTS_FETCH_TS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_frontrunner_signal_snapshots_fetch_ts
    ON frontrunner_signal_snapshots (fetch_ts)
"""

# accelerates classify_fr_checks' per-fr_key exact-match join against the latest snapshot
_CREATE_SIGNAL_SNAPSHOTS_FR_KEY_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_frontrunner_signal_snapshots_fr_key
    ON frontrunner_signal_snapshots (fr_key, fetch_ts)
"""


def _ensure_signal_snapshots_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_CREATE_SIGNAL_SNAPSHOTS_SQL)
    conn.execute(_CREATE_SIGNAL_SNAPSHOTS_FETCH_TS_INDEX_SQL)
    conn.execute(_CREATE_SIGNAL_SNAPSHOTS_FR_KEY_INDEX_SQL)
    conn.commit()


def init_frontrunner_signal_snapshots_db(path=None) -> None:
    """Create the frontrunner_signal_snapshots schema at *path* (idempotent)."""
    resolved = _warehouse_db_file(path)
    conn = _connect(resolved)
    try:
        _ensure_signal_snapshots_schema(conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema — frontrunner_classification_snapshots + frontrunner_run_metadata
# (PM-ruling extension: AC-3/4/5 output, persisted by fr-engine's builder
# compute path via persist_classification_run)
# ---------------------------------------------------------------------------

_CREATE_CLASSIFICATION_SNAPSHOTS_SQL = """
CREATE TABLE IF NOT EXISTS frontrunner_classification_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symphony_id     TEXT    NOT NULL,
    fr_key          TEXT    NOT NULL,
    fn              TEXT,
    comparator      TEXT,
    branch_path     TEXT,
    rsi_live        REAL,
    rsi_live_at     TEXT,
    cagr            REAL,
    sharpe          REAL,
    sortino         REAL,
    calmar          REAL,
    max_drawdown    REAL,
    classification  TEXT    NOT NULL,
    signal_fetch_ts TEXT,
    computed_at     TEXT    NOT NULL,
    created_at      TEXT    DEFAULT (datetime('now'))
)
"""

# accelerates get_latest_classifications' per-symphony MAX(computed_at) lookup
_CREATE_CLASSIFICATION_SYMPHONY_COMPUTED_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_frontrunner_classification_symphony_computed
    ON frontrunner_classification_snapshots (symphony_id, computed_at)
"""

_CREATE_RUN_METADATA_SQL = """
CREATE TABLE IF NOT EXISTS frontrunner_run_metadata (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    symphony_id          TEXT    NOT NULL,
    signals_unavailable  INTEGER NOT NULL,
    reason               TEXT,
    computed_at          TEXT    NOT NULL,
    created_at           TEXT    DEFAULT (datetime('now'))
)
"""

# accelerates get_latest_run_marker's per-symphony (or table-wide) MAX(computed_at) lookup
_CREATE_RUN_METADATA_SYMPHONY_COMPUTED_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_frontrunner_run_metadata_symphony_computed
    ON frontrunner_run_metadata (symphony_id, computed_at)
"""


def _ensure_classification_schema(conn: sqlite3.Connection) -> None:
    """Self-sufficient: creates both classification tables on demand, never
    depending on a prior init_frontrunner_signal_snapshots_db() call (mirrors
    atlas_cache.cached_pull's self-sufficiency precedent)."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_CREATE_CLASSIFICATION_SNAPSHOTS_SQL)
    conn.execute(_CREATE_CLASSIFICATION_SYMPHONY_COMPUTED_INDEX_SQL)
    conn.execute(_CREATE_RUN_METADATA_SQL)
    conn.execute(_CREATE_RUN_METADATA_SYMPHONY_COMPUTED_INDEX_SQL)
    conn.commit()


# ---------------------------------------------------------------------------
# AC-1: daily-cached Atlas pull
# ---------------------------------------------------------------------------


def _normalize_doc(doc: dict) -> dict | None:
    """Flatten one raw Atlas doc into a typed row. Returns None (dropped, never
    fabricated) when the doc lacks its identity fields (fr_key/ticker).

    No _strip_secrets call here (unlike lens_warehouse.persist_lens_snapshot) —
    INTENTIONAL, not an oversight: this extracts a fixed, named-field allowlist
    from the raw doc (fr_key/ticker/window/... below) and never persists the
    raw doc payload itself, so there is no arbitrary-shaped dict for a stray
    credential key to hide in. _strip_secrets exists to scrub payloads whose
    shape isn't controlled by the persisting code (lens_warehouse's raw_payload
    is caller-supplied and stored near-verbatim); a strict whitelist extraction
    has no equivalent leak surface by construction.
    """
    fr_key = doc.get("fr_key")
    ticker = doc.get("ticker")
    if not fr_key or not ticker:
        return None
    backtest = doc.get("backtest") or {}
    summary = backtest.get("summary") or {}
    vix_dest = doc.get("vix_destination_summary")
    return {
        "fr_key": fr_key,
        "ticker": ticker,
        "window": doc.get("window"),
        "threshold": doc.get("threshold"),
        "comparator": doc.get("comparator"),
        "rsi_live": doc.get("rsi_live"),
        "rsi_live_at": doc.get("rsi_live_at"),
        "cagr": summary.get("cagr"),
        "sharpe": summary.get("sharpe"),
        "sortino": summary.get("sortino"),
        "calmar": summary.get("calmar"),
        "max_drawdown": summary.get("max_drawdown"),
        "n_points": summary.get("n_points"),
        "vix_destination_json": json.dumps(vix_dest) if vix_dest is not None else None,
        "total_strategy_count": doc.get("total_strategy_count"),
        "true_ticker": backtest.get("true_ticker"),
        "false_ticker": backtest.get("false_ticker"),
    }


def _normalize_raw_docs(raw_docs: list[dict], fetch_ts: str) -> tuple[list[dict], dict]:
    """Normalize every raw doc; a single malformed doc is dropped (counted in
    stats), never aborts the whole batch."""
    pulled = len(raw_docs)
    normalized: list[dict] = []
    normalize_failed = 0
    for doc in raw_docs:
        try:
            row = _normalize_doc(doc)
        except Exception:  # noqa: BLE001 - defensive, one bad doc must not abort the batch
            row = None
        if row is None:
            normalize_failed += 1
            continue
        row["fetch_ts"] = fetch_ts
        normalized.append(row)
    stats = {"pulled": pulled, "normalized": len(normalized), "normalize_failed": normalize_failed}
    return normalized, stats


def _persist_signal_snapshot(rows: list[dict], *, fetch_ts: str, db_path=None) -> None:
    resolved = _warehouse_db_file(db_path)
    conn = _connect(resolved)
    try:
        _ensure_signal_snapshots_schema(conn)
        for row in rows:
            conn.execute(
                """
                INSERT INTO frontrunner_signal_snapshots
                (fr_key, ticker, "window", threshold, comparator, rsi_live, rsi_live_at,
                 cagr, sharpe, sortino, calmar, max_drawdown, n_points,
                 vix_destination_json, total_strategy_count, true_ticker, false_ticker, fetch_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["fr_key"],
                    row["ticker"],
                    row["window"],
                    row["threshold"],
                    row["comparator"],
                    row["rsi_live"],
                    row["rsi_live_at"],
                    row["cagr"],
                    row["sharpe"],
                    row["sortino"],
                    row["calmar"],
                    row["max_drawdown"],
                    row["n_points"],
                    row["vix_destination_json"],
                    row["total_strategy_count"],
                    row["true_ticker"],
                    row["false_ticker"],
                    fetch_ts,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def load_frontrunner_signals(*, force_refresh: bool = False, db_path=None) -> dict:
    """Pull captplanet.frontrunners through the daily atlas_cache seam.

    Returns {available, signals, stats, fetched_at, source, reason?}. Never
    raises (D-1) — every failure mode degrades to available=False with a
    reason drawn from {"AtlasFetchTimeout", type(exc).__name__,
    "AtlasCacheUnavailable"}.

    Persists to the warehouse (AC-2) ONLY when this call actually performed a
    fresh Atlas fetch (cache-miss or force_refresh) — a cache-hit persists
    nothing. A persist failure (including the pytest db_path=None sentinel)
    is logged and swallowed; it never affects the returned result.
    """
    _EMPTY_STATS: dict = {"pulled": 0, "normalized": 0, "normalize_failed": 0}
    fetched_at = datetime.now(UTC).isoformat()

    # Closure-captured flags: mirrors community_strats.py's _timeout_fired
    # idiom. _fetch_occurred distinguishes "cached_pull actually invoked our
    # fetch closure" (a genuine fresh pull, persist-eligible) from a cache hit
    # (fetch_fn never called, must not persist).
    _timeout_fired: list[bool] = [False]
    _fetch_occurred: list[bool] = [False]

    def _fetch_fn() -> list[dict]:
        """Connect to Atlas and return the projected frontrunner documents.
        Lazy pymongo import (CC-2) — the module stays importable without
        pymongo installed."""
        import pymongo  # noqa: PLC0415

        mongo_client = pymongo.MongoClient(
            os.environ["MONGO_URI"],
            serverSelectionTimeoutMS=10_000,
            connectTimeoutMS=10_000,
        )
        collection = mongo_client["captplanet"]["frontrunners"]
        cursor = collection.find({}, _PROJECTION)
        return list(cursor)

    def _bounded_fetch_fn() -> list[dict]:
        """Wrap _fetch_fn with a wall-clock timeout (DE-CS-002 pattern) —
        ThreadPoolExecutor + shutdown(wait=False) so a hung SRV/DNS resolution
        never blocks the caller on exit."""
        _fetch_occurred[0] = True
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(_fetch_fn)
        try:
            return fut.result(timeout=_FETCH_TIMEOUT_S)
        except concurrent.futures.TimeoutError:
            _timeout_fired[0] = True
            raise _AtlasFetchTimeout("Atlas SRV/DNS fetch timed out")  # noqa: B904
        finally:
            ex.shutdown(wait=False, cancel_futures=True)

    try:
        raw_docs = atlas_cache.cached_pull(
            _COLLECTION_NAME,
            _bounded_fetch_fn,
            ttl_days=_CACHE_TTL_DAYS,
            force_refresh=force_refresh,
        )

        if raw_docs is None:
            reason = "AtlasFetchTimeout" if _timeout_fired[0] else "AtlasCacheUnavailable"
            return {
                "available": False,
                "reason": reason,
                "signals": [],
                "stats": dict(_EMPTY_STATS),
                "fetched_at": fetched_at,
                "source": "captplanet",
            }

        if not isinstance(raw_docs, list):
            return {
                "available": False,
                "reason": "TypeError",
                "signals": [],
                "stats": dict(_EMPTY_STATS),
                "fetched_at": fetched_at,
                "source": "captplanet",
            }

    except Exception as exc:  # noqa: BLE001
        # When the timeout wrapper raises _AtlasFetchTimeout (propagates if
        # cached_pull is mocked to call fn() directly, bypassing its own
        # try/except), treat it identically to the closure-flag path.
        reason = "AtlasFetchTimeout" if _timeout_fired[0] else type(exc).__name__
        return {
            "available": False,
            "reason": reason,
            "signals": [],
            "stats": dict(_EMPTY_STATS),
            "fetched_at": fetched_at,
            "source": "captplanet",
        }

    signals, stats = _normalize_raw_docs(raw_docs, fetched_at)

    if _fetch_occurred[0]:
        try:
            _persist_signal_snapshot(signals, fetch_ts=fetched_at, db_path=db_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "frontrunner_signals: warehouse persist failed (%s); continuing",
                type(exc).__name__,
            )

    return {
        "available": True,
        "signals": signals,
        "stats": stats,
        "fetched_at": fetched_at,
        "source": "captplanet",
    }


# ---------------------------------------------------------------------------
# AC-2 accessor
# ---------------------------------------------------------------------------


def get_latest_signal_rows(*, db_path=None) -> list[dict]:
    """Return every row from the most recent signal-snapshot batch (rows
    sharing the max fetch_ts). [] when the table is empty. Never raises
    (production D-1); the pytest sentinel fires only when db_path=None
    under pytest."""
    resolved = _warehouse_db_file(db_path)
    conn = _connect(resolved)
    try:
        _ensure_signal_snapshots_schema(conn)
        rows = conn.execute(
            """
            SELECT * FROM frontrunner_signal_snapshots
            WHERE fetch_ts = (SELECT MAX(fetch_ts) FROM frontrunner_signal_snapshots)
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC-4: edge scoring + keep/prune/remove/no_edge_data classification
# (fr-engine owns this logic; lives in this file per the Architecture doc)
# ---------------------------------------------------------------------------

# Weak-edge floor for the prune tier — a check with cagr>=0 and sharpe>=0
# (so it isn't a remove) but sharpe below this floor is a weak, marginal
# signal, not worth keeping. p10 of the 152 matched live FR-check Sharpe
# distribution (docs/fr-signals-inputs/joined.json, 2026-07-16 join).
# Strict "<" — a check with sharpe exactly AT the threshold is kept, not
# pruned (test_sharpe_exactly_at_weak_threshold_is_not_pruned).
FR_WEAK_SHARPE_THRESHOLD: float = 0.38

# The Atlas frontrunners collection is exclusively RSI-based (fr-key format
# TICKER:WINDOW:THRESHOLD from RSI(t,w) gt thr) — a live check whose OWN fn
# is not RSI-family must never be treated as edge-comparable, even when its
# fr_key string numerically coincides with a real Atlas doc (the
# cumulative-return SPY:1:0 trap).
_RSI_FN = "relative-strength-index"


def classify_fr_checks(fr_checks: list[dict], signal_rows: list[dict]) -> list[dict]:
    """AC-4: join extracted FR-checks to signal rows by exact `fr_key` AND
    the LIVE check's own `comparator=="gt"` — the collection is gt-only, so a
    live `lt`-check must never be scored against a coincidentally-matching
    `gt` Atlas doc (the SPY:10:30 mis-join trap). The live check's own `fn`
    must also be RSI-family — a numeric fr_key coincidence with a non-RSI fn
    (e.g. cumulative-return) must never be treated as edge-comparable either.

    Classification: `remove` (Tier 1) when `cagr < 0 or sharpe < 0`; `prune`
    (Tier 2) when `sharpe < FR_WEAK_SHARPE_THRESHOLD` (strict); `keep`
    otherwise; `no_edge_data` when the check is not edge-comparable at all
    (wrong comparator, wrong fn, or genuinely absent from the collection) —
    never scored against a mismatched backtest, never invented.

    Pure function (no I/O). Never raises (D-1) — malformed/empty inputs
    degrade to `[]` or an honest `no_edge_data` row; a `no_edge_data` row
    never carries a borrowed/mismatched edge stat.
    """
    try:
        signal_by_key: dict[str, dict] = {}
        for row in signal_rows or []:
            key = row.get("fr_key")
            if isinstance(key, str) and key:
                signal_by_key[key] = row

        results: list[dict] = []
        for check in fr_checks or []:
            fr_key = check.get("fr_key")
            comparator = check.get("comparator")
            fn = check.get("fn")
            edge_comparable = (
                isinstance(fr_key, str)
                and bool(fr_key)
                and comparator == "gt"
                and fn == _RSI_FN
                and fr_key in signal_by_key
            )

            base_row = {
                "fr_key": fr_key,
                "fn": fn,
                "comparator": comparator,
                "branch_path": check.get("branch_path"),
            }

            if not edge_comparable:
                results.append(
                    {
                        **base_row,
                        "classification": "no_edge_data",
                        "rsi_live": None,
                        "rsi_live_at": None,
                        "cagr": None,
                        "sharpe": None,
                        "sortino": None,
                        "calmar": None,
                        "max_drawdown": None,
                        "signal_fetch_ts": None,
                    }
                )
                continue

            signal = signal_by_key[fr_key]
            cagr = signal.get("cagr")
            sharpe = signal.get("sharpe")
            if cagr is None or sharpe is None:
                # A matched doc with an incomplete backtest is honestly
                # no_edge_data too — never a fabricated classification.
                results.append(
                    {
                        **base_row,
                        "classification": "no_edge_data",
                        "rsi_live": None,
                        "rsi_live_at": None,
                        "cagr": None,
                        "sharpe": None,
                        "sortino": None,
                        "calmar": None,
                        "max_drawdown": None,
                        "signal_fetch_ts": None,
                    }
                )
                continue

            if cagr < 0 or sharpe < 0:
                classification = "remove"
            elif sharpe < FR_WEAK_SHARPE_THRESHOLD:
                classification = "prune"
            else:
                classification = "keep"

            results.append(
                {
                    **base_row,
                    "classification": classification,
                    "rsi_live": signal.get("rsi_live"),
                    "rsi_live_at": signal.get("rsi_live_at"),
                    "cagr": cagr,
                    "sharpe": sharpe,
                    "sortino": signal.get("sortino"),
                    "calmar": signal.get("calmar"),
                    "max_drawdown": signal.get("max_drawdown"),
                    "signal_fetch_ts": signal.get("fetch_ts"),
                }
            )
        return results
    except Exception:  # noqa: BLE001 - defensive; never-raises contract
        logger.debug("classify_fr_checks: unexpected error", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# PM-ruling extension: classification-row + run-marker persistence/accessors
# ---------------------------------------------------------------------------


def persist_classification_run(
    symphony_id: str,
    classification_rows: list[dict],
    *,
    signals_unavailable: bool = False,
    reason: str | None = None,
    computed_at: str | None = None,
    db_path=None,
) -> None:
    """Write one classification_snapshots row per fr_key + exactly ONE
    run_metadata row, atomically, sharing a single computed_at (explicit if
    passed, else generated once — never per-row). Valid with
    classification_rows=[] (the AC-5 degraded case) — the run marker is still
    written."""
    resolved = _warehouse_db_file(db_path)
    resolved_computed_at = computed_at or datetime.now(UTC).isoformat()

    conn = _connect(resolved)
    try:
        _ensure_classification_schema(conn)
        for row in classification_rows:
            branch_path = row.get("branch_path")
            conn.execute(
                """
                INSERT INTO frontrunner_classification_snapshots
                (symphony_id, fr_key, fn, comparator, branch_path, rsi_live, rsi_live_at,
                 cagr, sharpe, sortino, calmar, max_drawdown, classification,
                 signal_fetch_ts, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symphony_id,
                    row["fr_key"],
                    row.get("fn"),
                    row.get("comparator"),
                    json.dumps(branch_path) if branch_path is not None else None,
                    row.get("rsi_live"),
                    row.get("rsi_live_at"),
                    row.get("cagr"),
                    row.get("sharpe"),
                    row.get("sortino"),
                    row.get("calmar"),
                    row.get("max_drawdown"),
                    row["classification"],
                    row.get("signal_fetch_ts"),
                    resolved_computed_at,
                ),
            )
        conn.execute(
            """
            INSERT INTO frontrunner_run_metadata
            (symphony_id, signals_unavailable, reason, computed_at)
            VALUES (?, ?, ?, ?)
            """,
            (symphony_id, int(bool(signals_unavailable)), reason, resolved_computed_at),
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_classifications(symphony_id=None, *, db_path=None) -> list[dict]:
    """symphony_id given: that symphony's own latest batch (greatest
    computed_at for that symphony_id). symphony_id=None: EVERY symphony's own
    latest batch — a per-symphony greatest-n-per-group, never a single global
    max that would silently drop older symphonies' rows.

    branch_path is returned as a native list (deserialized here) — callers
    never json.loads() it themselves."""
    resolved = _warehouse_db_file(db_path)
    conn = _connect(resolved)
    try:
        _ensure_classification_schema(conn)
        base_query = """
            SELECT * FROM frontrunner_classification_snapshots t
            WHERE t.computed_at = (
                SELECT MAX(computed_at) FROM frontrunner_classification_snapshots t2
                WHERE t2.symphony_id = t.symphony_id
            )
        """
        if symphony_id is not None:
            rows = conn.execute(base_query + " AND t.symphony_id = ?", (symphony_id,)).fetchall()
        else:
            rows = conn.execute(base_query).fetchall()

        result = []
        for r in rows:
            row = dict(r)
            raw_branch_path = row.get("branch_path")
            if raw_branch_path is not None:
                try:
                    row["branch_path"] = json.loads(raw_branch_path)
                except (json.JSONDecodeError, TypeError):
                    row["branch_path"] = None
            result.append(row)
        return result
    finally:
        conn.close()


def get_latest_run_marker(symphony_id=None, *, db_path=None) -> dict | None:
    """symphony_id given: that symphony's own latest marker row. symphony_id=
    None: the single most-recently-computed row across the WHOLE table,
    arbitrary which symphony that happens to be — deliberately NO aggregation
    (no any-symphony-degraded-implies-True logic). None when no row has ever
    been written for the requested scope."""
    resolved = _warehouse_db_file(db_path)
    conn = _connect(resolved)
    try:
        _ensure_classification_schema(conn)
        if symphony_id is not None:
            row = conn.execute(
                "SELECT * FROM frontrunner_run_metadata WHERE symphony_id = ? "
                "ORDER BY computed_at DESC LIMIT 1",
                (symphony_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM frontrunner_run_metadata ORDER BY computed_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["signals_unavailable"] = bool(result["signals_unavailable"])
        return result
    finally:
        conn.close()
