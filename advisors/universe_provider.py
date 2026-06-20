"""Tradeable Universe Provider — Component 1 of the real Opus-driven Strategy Builder.

Fetches the full active US-equity tradeable set from the Alpaca PAPER trading
host and caches it weekly.  The result is a plain membership/validation SET —
no ranking, no dollar-volume, no top-N cap.

Public surface
--------------
ALPACA_TRADING_BASE_URL : str
    Paper trading host constant, DISTINCT from synthetic_history.ALPACA_BASE_URL
    (the data host).  Our keys are PAPER keys — the live host api.alpaca.markets
    returns 401, so all calls must target this constant.

fetch_universe(*, force_refresh=False, db_path=None) -> dict
    Returns {available, symbols, reason?}.
    Never raises.  On failure: available=False + reason=ExcClassName only (D-1).

is_tradeable(symbol, *, force_refresh=False) -> bool
    Membership check served from cache.  Never raises.

get_tradeable_set(*, force_refresh=False) -> frozenset[str]
    Full cached membership set.  Never raises.

Design constraints
------------------
- Paper host only: ALPACA_TRADING_BASE_URL is NEVER the live host.
- D-1 contract: reason strings contain only the exception class name — no
  message text, no file path, no credential value.
- No import of database (state DB) or autotuner/optuna (optimization DB).
  The third-DB warehouse (lens_warehouse) is the only persistence layer here.
- Weekly TTL via atlas_cache (bill-protection: <= 7 days).
- Single flat JSON array response — no pagination loop.
"""

from __future__ import annotations

import logging
import os

import requests

from advisors import atlas_cache, lens_warehouse

logger = logging.getLogger(__name__)

# Module-level slot: stores the exception class name from the most recent
# _live_fetch failure so fetch_universe can surface it when cached_pull
# returns None (atlas_cache swallows the exception; we need the class name).
_last_fetch_exc_class: str = "FetchFailed"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Paper trading host — DISTINCT from synthetic_history.ALPACA_BASE_URL
# (data.alpaca.markets/v2).  Paper keys 401 on the live host api.alpaca.markets.
ALPACA_TRADING_BASE_URL = "https://paper-api.alpaca.markets"

# Exact exchange strings that constitute the listed membership set.
# "NYSE ARCA" (with space) is NOT in this set — exact string match only.
ALLOWED_EXCHANGES: frozenset[str] = frozenset({"NASDAQ", "NYSE", "ARCA", "BATS", "AMEX"})

# atlas_cache collection key for the universe snapshot.
_CACHE_COLLECTION = "universe_provider"

# Cache TTL: one week (bill-protection directive, <= 7 days).
_CACHE_TTL_DAYS = 7

# HTTP request timeout in seconds (explicit — never rely on urllib3 default None).
_HTTP_TIMEOUT_S = 30

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _passes_filter(asset: dict) -> bool:
    """Return True when an asset belongs in the tradeable membership set.

    Keeps only records where tradable is exactly True AND exchange is one of
    the five listed exchanges.  Exact string match — 'NYSE ARCA' is not 'ARCA'.
    ETFs, leveraged ETFs, and inverse ETFs are NOT excluded by class.
    """
    return asset.get("tradable") is True and asset.get("exchange") in ALLOWED_EXCHANGES


def _live_fetch() -> list[str]:
    """Call the Alpaca paper /v2/assets endpoint and return a filtered symbol list.

    Raises on any failure so atlas_cache can decide between the stale row and None.
    Records the exception class name in _last_fetch_exc_class before re-raising
    so fetch_universe can surface the right D-1 reason when cached_pull returns None.
    The single flat JSON array is consumed in exactly one HTTP call — no pagination.
    """
    global _last_fetch_exc_class

    url = ALPACA_TRADING_BASE_URL + "/v2/assets"
    headers = {
        "APCA-API-KEY-ID": os.getenv("ALPACA_KEY", ""),
        "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET", ""),
    }
    params = {"status": "active", "asset_class": "us_equity"}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=_HTTP_TIMEOUT_S)
    except Exception as exc:
        _last_fetch_exc_class = type(exc).__name__
        raise

    if not resp.ok:
        _last_fetch_exc_class = "HTTPError"
        # Raise with status code only — no body text that might embed key values.
        raise RuntimeError(f"HTTP {resp.status_code}")

    try:
        assets: list[dict] = resp.json()
    except Exception as exc:
        _last_fetch_exc_class = type(exc).__name__
        raise

    return [a["symbol"] for a in assets if _passes_filter(a)]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_universe(*, force_refresh: bool = False, db_path=None) -> dict:
    """Fetch the full tradeable US-equity membership set.

    Routes through the weekly atlas_cache (TTL=7 days).  On a cache miss (or
    force_refresh=True) calls the Alpaca paper /v2/assets endpoint once.
    Persists a snapshot row to the warehouse third-DB after every live fetch.

    Parameters
    ----------
    force_refresh:
        When True, bypass the cache and issue a fresh HTTP call.
    db_path:
        Explicit warehouse DB path for test isolation.  Production callers
        pass None (lens_warehouse resolves the default).

    Returns
    -------
    dict with keys:
      available : bool   — True on success, False on any failure
      symbols   : frozenset[str]  — membership set (empty frozenset on failure)
      reason    : str    — present and non-empty when available=False;
                          exception class name ONLY (D-1 contract)

    Never raises.
    """
    # Ensure the atlas_cache schema exists — cached_pull does not call init.
    try:
        atlas_cache.init_atlas_cache()
    except Exception:
        pass  # Non-fatal; cached_pull will degrade gracefully if DB is broken.

    try:
        cached = atlas_cache.cached_pull(
            _CACHE_COLLECTION,
            _live_fetch,
            ttl_days=_CACHE_TTL_DAYS,
            force_refresh=force_refresh,
        )
    except Exception as exc:
        logger.warning("universe_provider: atlas_cache raised %s", type(exc).__name__)
        return {"available": False, "symbols": frozenset(), "reason": type(exc).__name__}

    # atlas_cache returns None when _live_fetch raised and no stale row exists.
    # Surface the original exception class name captured in _last_fetch_exc_class.
    if cached is None:
        return {
            "available": False,
            "symbols": frozenset(),
            "reason": _last_fetch_exc_class,
        }

    symbols: frozenset[str] = frozenset(cached)

    if not symbols:
        return {
            "available": False,
            "symbols": frozenset(),
            "reason": "EmptyUniverse",
        }

    # Persist a snapshot to the warehouse (third-DB pattern) after a live fetch.
    # raw_payload carries the symbol list for drift-history diffing, no credentials.
    try:
        lens_warehouse.persist_lens_snapshot(
            lens="universe_provider",
            symbol=None,
            source="alpaca_paper_assets",
            available=True,
            raw_payload={"symbols": sorted(symbols), "symbol_count": len(symbols)},
            db_path=db_path,
        )
    except Exception as exc:
        # Warehouse write failure is non-fatal — log and continue.
        logger.warning(
            "universe_provider: warehouse persist failed (%s); continuing",
            type(exc).__name__,
        )

    return {"available": True, "symbols": symbols}


def is_tradeable(symbol: str, *, force_refresh: bool = False) -> bool:
    """Return True if symbol is in the cached tradeable membership set.

    Served from cache after the first fetch — no additional HTTP call.
    Never raises; returns False on any fetch failure.
    """
    result = fetch_universe(force_refresh=force_refresh)
    return bool(symbol in result["symbols"])


def get_tradeable_set(*, force_refresh: bool = False) -> frozenset:
    """Return the full cached tradeable membership set.

    Never raises; returns an empty frozenset on any fetch failure.
    """
    result = fetch_universe(force_refresh=force_refresh)
    return result["symbols"]
