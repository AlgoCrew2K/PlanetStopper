"""Weekly Atlas read cache — dedicated local SQLite DB.

Public API
----------
init_atlas_cache() -> None
    Create the cache DB and schema (idempotent). Called once at startup or
    lazily on first cached_pull. WAL mode enabled for concurrent reads.

cached_pull(collection_name, fetch_fn, *, ttl_days=_ENV_DEFAULT, force_refresh=False)
    Return the cached payload when a row exists and its age is strictly less
    than ttl_days; otherwise call fetch_fn(), upsert, and return the result.

    force_refresh=True always calls fetch_fn() regardless of row freshness.

    Never raises — all failure modes degrade gracefully:
      - corrupt/locked read  → live fetch_fn() call
      - write failure        → return the freshly-fetched value
      - fetch_fn raises + stale row  → return stale payload
      - fetch_fn raises + no row     → return None (documented sentinel)

Design invariants
-----------------
- Cache DB is isolated: this module imports no database.py, autotuner, or
  pymongo code; it never cross-joins with the state or optimization DBs.
- MONGO_URI is never read, stored, or logged here.
- One row per collection (TEXT PRIMARY KEY); last-writer-wins upsert.
- TTL boundary: age strictly < ttl_days is FRESH; age >= ttl_days is STALE.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# Sentinel used to detect "caller did not supply ttl_days"; env var is read then.
_ENV_DEFAULT = object()

# Default TTL in days when neither kwarg nor env var is supplied.
_DEFAULT_TTL_DAYS = 7


def _atlas_cache_db() -> str:
    """Resolve the cache DB file path from ATLAS_CACHE_DB_PATH env (default alphabot_atlas_cache.db)."""  # noqa: E501  # un-wrappable long line
    return os.environ.get("ATLAS_CACHE_DB_PATH", "alphabot_atlas_cache.db")


def _ensure_atlas_cache_schema(conn: sqlite3.Connection) -> None:
    """Idempotently create the atlas_cache table + enable WAL on an open connection.

    Shared by init_atlas_cache() and cached_pull() so cached_pull is
    self-sufficient — it must not depend on a caller having invoked
    init_atlas_cache() first (community_strats.py never does).
    """
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS atlas_cache "
        "(collection TEXT PRIMARY KEY, fetched_at TEXT, payload TEXT)"
    )
    conn.commit()


def init_atlas_cache() -> None:
    """Create the atlas_cache table and enable WAL mode (idempotent)."""
    db_path = _atlas_cache_db()
    conn = sqlite3.connect(db_path)
    try:
        _ensure_atlas_cache_schema(conn)
    finally:
        conn.close()


def cached_pull(
    collection_name: str,
    fetch_fn,
    *,
    ttl_days=_ENV_DEFAULT,
    force_refresh: bool = False,
):
    """Return cached payload if fresh, else call fetch_fn() and upsert.

    Parameters
    ----------
    collection_name:
        Key identifying the Atlas collection (one row in the cache table).
    fetch_fn:
        Zero-argument callable that returns the live payload when called.
    ttl_days:
        Maximum age (in days) before a row is considered stale. Defaults to
        the ATLAS_CACHE_TTL_DAYS env var, then 7 days.
    force_refresh:
        When True, always call fetch_fn() and refresh the row, even if fresh.

    Returns
    -------
    The cached or freshly-fetched payload, or None if fetch_fn raises and no
    row (fresh or stale) is available.

    Never raises.
    """
    # Resolve effective TTL.
    if ttl_days is _ENV_DEFAULT:
        try:
            ttl_days = int(os.environ.get("ATLAS_CACHE_TTL_DAYS", _DEFAULT_TTL_DAYS))
        except (ValueError, TypeError):
            ttl_days = _DEFAULT_TTL_DAYS

    ttl_seconds = ttl_days * 86400
    db_path = _atlas_cache_db()

    # --- Attempt to read the cached row ---
    cached_row = None  # (fetched_at_str, payload_obj) or None
    try:
        conn = sqlite3.connect(db_path)
        try:
            # Self-sufficient: ensure schema exists before the SELECT so this
            # call never depends on a prior init_atlas_cache() (community_strats.py
            # calls cached_pull directly and never calls init_atlas_cache()).
            _ensure_atlas_cache_schema(conn)
            row = conn.execute(
                "SELECT fetched_at, payload FROM atlas_cache WHERE collection=?",
                (collection_name,),
            ).fetchone()
        finally:
            conn.close()

        if row is not None:
            fetched_at_str, payload_str = row
            try:
                payload_obj = json.loads(payload_str)
                cached_row = (fetched_at_str, payload_obj)
            except (json.JSONDecodeError, TypeError, ValueError):
                # Corrupt payload — treat as a read failure, fall through to live fetch.
                logger.warning(
                    "atlas_cache: corrupt payload for collection=%r; falling through to live fetch",
                    collection_name,
                )
    except Exception as exc:
        # DB read failure (locked, missing, etc.) — fall through to live fetch.
        logger.warning(
            "atlas_cache: read error for collection=%r (%s); falling through to live fetch",
            collection_name,
            type(exc).__name__,
        )

    # --- Determine freshness ---
    is_fresh = False
    if cached_row is not None and not force_refresh:
        fetched_at_str, _ = cached_row
        try:
            fetched_at = datetime.fromisoformat(fetched_at_str)
            # Normalise to UTC-aware for comparison.
            now = datetime.now(UTC)
            if fetched_at.tzinfo is None:
                # Stored without timezone info — treat as UTC.
                fetched_at = fetched_at.replace(tzinfo=UTC)
            age_seconds = (now - fetched_at).total_seconds()
            is_fresh = age_seconds < ttl_seconds  # strict: < is fresh, >= is stale
        except Exception:
            is_fresh = False  # Cannot parse timestamp — treat as stale

    # --- HIT: return cached payload without calling fetch_fn ---
    if is_fresh:
        return cached_row[1]

    # --- MISS or force_refresh: call fetch_fn ---
    fetched_payload = None
    fetch_succeeded = False
    try:
        fetched_payload = fetch_fn()
        fetch_succeeded = True
    except Exception as exc:
        logger.warning(
            "atlas_cache: fetch_fn raised for collection=%r (%s); degrading gracefully",
            collection_name,
            type(exc).__name__,
        )

    if not fetch_succeeded:
        # Degrade gracefully: return stale row if available, else None sentinel.
        if cached_row is not None:
            return cached_row[1]
        return None

    # --- Upsert the freshly fetched payload ---
    try:
        now_iso = datetime.now(UTC).isoformat()
        # default=str converts non-JSON-native values (BSON ObjectId, datetime,
        # Decimal128) to their str() representation rather than raising TypeError.
        # Prevents silent row-drop when docs carry un-projected BSON _id fields.
        serialised = json.dumps(fetched_payload, default=str)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO atlas_cache (collection, fetched_at, payload) "
                "VALUES (?, ?, ?)",
                (collection_name, now_iso, serialised),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        # Write failure — return the fetched payload anyway, no raise.
        logger.warning(
            "atlas_cache: write error for collection=%r (%s); returning payload without caching",
            collection_name,
            type(exc).__name__,
        )

    return fetched_payload
