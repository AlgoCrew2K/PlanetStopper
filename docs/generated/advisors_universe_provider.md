# advisors/universe_provider

> Tradeable US-equity membership provider for the real Opus-driven Strategy Builder (Component 1): fetches the active asset list from the Alpaca PAPER trading host, caches it weekly, and exposes a frozenset for membership checks.

**Source:** `advisors/universe_provider.py`
**Last updated:** 2026-06-20

## Overview

`universe_provider` is the single authoritative source of the tradeable US-equity universe consumed by the Strategy Builder engine. It issues one GET request to the Alpaca PAPER trading host (`/v2/assets?status=active&asset_class=us_equity`), filters by exchange membership, and caches the resulting frozenset for seven days via `advisors.atlas_cache` (weekly TTL, operator bill-protection directive). Every live fetch also persists a snapshot row to the lens warehouse (third-DB) for week-over-week drift history.

The module is membership-only by design: the result is an unordered `frozenset[str]` — no dollar-volume, no top-N cap, no ranking. ETFs and leveraged/inverse ETFs are kept; no class-based exclusion is applied.

## Constants

| Name | Value | Description |
|------|-------|-------------|
| `ALPACA_TRADING_BASE_URL` | `"https://paper-api.alpaca.markets"` | Paper trading host. DISTINCT from `synthetic_history.ALPACA_BASE_URL` (`data.alpaca.markets/v2`). Paper keys return 401 on the live host (`api.alpaca.markets`); all calls must target this constant. |
| `ALLOWED_EXCHANGES` | `frozenset({"NASDAQ", "NYSE", "ARCA", "BATS", "AMEX"})` | Exact string match only. `"NYSE ARCA"` (with space) is NOT in this set. |
| `_CACHE_COLLECTION` | `"universe_provider"` | atlas_cache collection key for the universe snapshot. |
| `_CACHE_TTL_DAYS` | `7` | Weekly cache TTL (bill-protection directive). |
| `_HTTP_TIMEOUT_S` | `30` | Explicit HTTP request timeout in seconds. |
| `_last_fetch_exc_class` | `"FetchFailed"` (default) | Module-level mutable slot. Stores the exception class name from the most recent `_live_fetch` failure so `fetch_universe` can surface the correct D-1 reason when `cached_pull` returns `None` (atlas_cache swallows the exception internally). |

## API Reference

### `fetch_universe(*, force_refresh=False, db_path=None) -> dict`

Fetch the full tradeable US-equity membership set.

Routes through the weekly atlas_cache (TTL=7 days). On a cache miss (or `force_refresh=True`) calls the Alpaca paper `/v2/assets` endpoint once. Persists a snapshot row to the warehouse third-DB after every live fetch.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `force_refresh` | `bool` | When `True`, bypass the cache and issue a fresh HTTP call. Default `False`. |
| `db_path` | `str \| None` | Explicit warehouse DB path for test isolation. Production callers pass `None` (lens_warehouse resolves the default). |

**Returns:** `dict` with keys:

| Key | Type | Description |
|-----|------|-------------|
| `available` | `bool` | `True` on success, `False` on any failure. |
| `symbols` | `frozenset[str]` | Membership set. Empty `frozenset` on failure. |
| `reason` | `str` | Present and non-empty when `available=False`. Exception class name ONLY (D-1 contract) — e.g. `"Timeout"`, `"ConnectionError"`, `"HTTPError"`, `"EmptyUniverse"`. Never contains message text, file paths, or credential values. |

**Never raises.**

**Example:**
```python
result = fetch_universe()
if result["available"]:
    symbols = result["symbols"]  # frozenset[str]
else:
    logger.warning("universe unavailable: %s", result["reason"])
```

---

### `is_tradeable(symbol, *, force_refresh=False) -> bool`

Membership check served from cache after the first fetch — no additional HTTP call.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `symbol` | `str` | Ticker symbol to check. |
| `force_refresh` | `bool` | When `True`, bypass the cache. Default `False`. |

**Returns:** `bool` — `True` if the symbol is in the cached tradeable set. `False` on any fetch failure.

**Never raises.**

---

### `get_tradeable_set(*, force_refresh=False) -> frozenset`

Return the full cached tradeable membership set.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `force_refresh` | `bool` | When `True`, bypass the cache. Default `False`. |

**Returns:** `frozenset[str]` — full membership set. Empty `frozenset` on any fetch failure.

**Never raises.**

## Internal Helpers

### `_passes_filter(asset) -> bool`

Returns `True` when an asset belongs in the tradeable set. Keeps only records where `tradable is True` AND `exchange` is one of `ALLOWED_EXCHANGES`. Exact string match — `"NYSE ARCA"` is not `"ARCA"`. No class-based exclusion of ETFs.

### `_live_fetch() -> list[str]`

Calls the Alpaca paper `/v2/assets` endpoint and returns a filtered symbol list. Raises on any failure (so atlas_cache can decide between a stale row and `None`). Records the exception class name in `_last_fetch_exc_class` before re-raising. Single flat JSON array response — no pagination loop.

## Internal Dependencies

- `advisors.atlas_cache` — weekly read-through cache (bill-protection TTL); `init_atlas_cache()` called before `cached_pull` because `cached_pull` does not init the schema on a fresh DB
- `advisors.lens_warehouse` — persists a `universe_provider` snapshot row after every live fetch (`lens="universe_provider"`, `source="alpaca_paper_assets"`, `raw_payload={"symbols": sorted(symbols), "symbol_count": N}`)

**Explicitly NOT imported:** `database` (state DB), `autotuner` / `optuna` (optimization DB). The only persistence layer here is the warehouse third-DB.

## Design Notes

- **Paper host only.** `ALPACA_TRADING_BASE_URL` is `https://paper-api.alpaca.markets`. The project uses PAPER API keys; the live host `api.alpaca.markets` returns 401. Do not substitute the live host.
- **D-1 error contract.** All `reason` strings contain only the exception class name. No message body, no path, no credential value. `_last_fetch_exc_class` captures the class name before `_live_fetch` re-raises, so the surface stays clean even when `atlas_cache` swallows the exception internally.
- **Warehouse payload is credentials-safe.** `raw_payload` carries only `{"symbols": sorted(symbols), "symbol_count": N}` — no API key values. `lens_warehouse._strip_secrets` provides an additional recursive scrub as a defense-in-depth layer.
- **`init_atlas_cache()` must precede `cached_pull`.** `cached_pull` does not initialize the atlas_cache schema on a fresh DB. `fetch_universe` calls `init_atlas_cache()` first; failure is non-fatal (logged, `cached_pull` degrades gracefully if the DB is broken).
