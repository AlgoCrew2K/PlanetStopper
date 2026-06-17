"""Community strategies loader.

Pulls strategy documents from the captplanet MongoDB Atlas collection via the
weekly Atlas cache (`advisors.atlas_cache`), validates each document via
symphony_schema, deduplicates by composition, and returns a well-formed result
dict.

Off-execution-path. Advisory-only. No Flask routes, no execution flags.
pymongo is lazy-imported inside the fetch_fn closure only.

D-1 contract: reason fields are always type(exc).__name__ — never the
exception message or any secret value (MONGO_URI, hostname, credential).
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import os
from typing import Any

from advisors import atlas_cache
from advisors import symphony_schema

logger = logging.getLogger(__name__)

# Wall-clock bound for the live Atlas fetch leg. serverSelectionTimeoutMS /
# connectTimeoutMS do NOT cover mongodb+srv:// SRV/TXT DNS resolution (confirmed:
# hangs >50s with those set). Chosen > 10s serverSelectionTimeoutMS so a
# reachable-but-slow Atlas still completes server selection.
_ATLAS_FETCH_TIMEOUT_S: float = 12.0


class _AtlasFetchTimeout(Exception):
    """Raised when the bounded Atlas fetch exceeds _ATLAS_FETCH_TIMEOUT_S."""


# ---------------------------------------------------------------------------
# Mongo query constants
# ---------------------------------------------------------------------------

# Inclusion projection: fetch only the fields the loader reads.
# Omits 'backtest' and 'quantstats_metrics' (multi-MB arrays per doc).
_PROJECTION: dict = {
    "sid": 1,
    "name": 1,
    "edn_string": 1,
    "oos_metrics": 1,
}

# Atlas collection identifier — key used in the atlas_cache table.
_COLLECTION_NAME = "captplanet.strategies"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _strip_ids(obj: Any) -> Any:
    """Return a deep copy of obj with all 'id' keys stripped (recursive).

    Used to produce a composition hash that is independent of the uuid4 node
    ids emitted by symphony_schema constructors, so two structurally identical
    trees always produce the same hash regardless of their node ids.
    """
    if isinstance(obj, dict):
        return {k: _strip_ids(v) for k, v in obj.items() if k != "id"}
    if isinstance(obj, list):
        return [_strip_ids(item) for item in obj]
    return obj


def _composition_hash(tree: dict) -> str:
    """Return a deterministic SHA-256 hex digest of the tree's structure.

    Volatile 'id' fields are stripped before hashing so two structurally
    identical trees (same logic, same tickers, same weights) always produce
    the same hash regardless of their uuid4 node ids.

    This is intentionally a tree-structural hash — NOT database.compute_composition_hash
    (which takes list[str] of symphony IDs and is used for portfolio-set identity,
    not individual strategy structure).
    """
    canonical = json.dumps(_strip_ids(tree), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _oos_sharpe(doc: dict) -> float:
    """Return the OOS sharpe from a doc's oos_metrics, or -inf if absent.

    A missing sharpe is never 'better' than any present sharpe; -inf ensures
    docs with a present sharpe always win dedup ties.
    """
    oos = doc.get("oos_metrics")
    if oos and isinstance(oos, dict) and "sharpe" in oos:
        try:
            return float(oos["sharpe"])
        except (TypeError, ValueError):
            pass
    return float("-inf")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_community_strategies(
    *,
    limit: int | None = None,
    min_oos_sharpe: float | None = None,
    client=None,  # kept for interface compatibility; not used directly
    force_refresh: bool = False,
) -> dict:
    """Load and validate community strategies from the captplanet Atlas collection.

    Parameters
    ----------
    limit:
        If not None, cap the number of returned candidates (applied after
        dedup and sharpe filtering).
    min_oos_sharpe:
        If not None, exclude docs whose oos_metrics['sharpe'] is below this
        floor. Docs that lack oos_metrics or lack the 'sharpe' key are KEPT.
    client:
        Reserved for interface compatibility. Not used in this implementation.
    force_refresh:
        When True, bypass the atlas_cache TTL and re-fetch from Mongo.

    Returns
    -------
    dict with keys:
        available (bool), candidates (list), stats (dict), source (str),
        and optionally reason (str) when available is False.

    Never raises. All failure modes return available=False + D-1 reason.
    """
    _EMPTY_STATS: dict[str, int] = {
        "pulled": 0,
        "valid": 0,
        "missing_edn_string": 0,
        "parse_failed": 0,
        "validate_rejected": 0,
        "sharpe_filtered": 0,
        "deduped": 0,
    }

    try:
        # Build a fetch_fn closure: lazy pymongo import inside so the module
        # is importable without pymongo installed.
        def _fetch_fn() -> list:
            """Connect to Atlas and return the projected strategy documents."""
            import pymongo  # noqa: PLC0415

            mongo_client = pymongo.MongoClient(
                os.environ["MONGO_URI"],
                serverSelectionTimeoutMS=10_000,
                connectTimeoutMS=10_000,
            )
            collection = mongo_client["captplanet"]["strategies"]
            cursor = collection.find({}, _PROJECTION)
            return list(cursor)

        def _bounded_fetch_fn() -> list:
            """Wrap _fetch_fn with a wall-clock timeout.

            Uses ThreadPoolExecutor with shutdown(wait=False) so a hung worker thread
            (e.g., blocked on SRV/TXT DNS resolution) does not block the caller on exit.
            The orphan thread is allowed to linger; MongoClient eventually errors.
            """
            ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            fut = ex.submit(_fetch_fn)
            try:
                return fut.result(timeout=_ATLAS_FETCH_TIMEOUT_S)
            except concurrent.futures.TimeoutError:
                raise _AtlasFetchTimeout("Atlas SRV/DNS fetch timed out")
            finally:
                ex.shutdown(wait=False, cancel_futures=True)  # NEVER wait=True

        # Route the Atlas read through the weekly cache. Only the raw projected
        # docs are cached; validation/dedup run on every call (cheap in-process).
        raw_docs = atlas_cache.cached_pull(
            _COLLECTION_NAME,
            _bounded_fetch_fn,
            force_refresh=force_refresh,
        )

        # cached_pull returns None when fetch failed and no stale row exists.
        if raw_docs is None:
            return {
                "available": False,
                "reason": "AtlasCacheUnavailable",
                "candidates": [],
                "stats": dict(_EMPTY_STATS),
                "source": "captplanet",
            }

        # Guard against a non-list payload (corrupt cache, unexpected shape).
        if not isinstance(raw_docs, list):
            return {
                "available": False,
                "reason": "TypeError",
                "candidates": [],
                "stats": dict(_EMPTY_STATS),
                "source": "captplanet",
            }

    except _AtlasFetchTimeout:
        return {
            "available": False,
            "reason": "AtlasFetchTimeout",
            "candidates": [],
            "stats": dict(_EMPTY_STATS),
            "source": "captplanet",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "reason": type(exc).__name__,
            "candidates": [],
            "stats": dict(_EMPTY_STATS),
            "source": "captplanet",
        }

    # --- Parse and validate each document ------------------------------------
    pulled = len(raw_docs)
    missing_edn_string = 0
    parse_failed = 0
    validate_rejected = 0
    sharpe_filtered = 0
    valid_candidates: list[dict] = []

    for doc in raw_docs:
        # Missing or empty edn_string — no payload to parse.
        edn = doc.get("edn_string")
        if not edn:
            missing_edn_string += 1
            continue

        # Parse edn_string (wire format: JSON-encoded tree dict).
        try:
            tree = json.loads(edn)
        except Exception:  # noqa: BLE001
            parse_failed += 1
            continue

        # Structural validation — HARD errors only ([] = keep).
        try:
            errors = symphony_schema.validate_tree(tree)
        except Exception:  # noqa: BLE001
            validate_rejected += 1
            continue

        if errors:
            validate_rejected += 1
            continue

        # Ticker extraction (excludes '%' placeholder).
        try:
            tickers = symphony_schema.extract_tickers(tree)
        except Exception:  # noqa: BLE001
            validate_rejected += 1
            continue

        # Sharpe filter — docs LACKING sharpe are KEPT regardless of floor.
        oos_metrics = doc.get("oos_metrics")
        if min_oos_sharpe is not None:
            oos = oos_metrics if isinstance(oos_metrics, dict) else {}
            if "sharpe" in oos:
                try:
                    if float(oos["sharpe"]) < min_oos_sharpe:
                        sharpe_filtered += 1
                        continue
                except (TypeError, ValueError):
                    pass  # unparseable sharpe → keep the doc

        # Composition hash: tree-structural (strips uuid4 'id' keys so identical
        # logic always hashes identically, regardless of node id generation).
        comp_hash = _composition_hash(tree)

        valid_candidates.append(
            {
                "sid": doc.get("sid", ""),
                "name": doc.get("name", ""),
                "tree": tree,
                "tickers": tickers,
                "oos_metrics": oos_metrics,
                "composition_hash": comp_hash,
                # Internal key for dedup quality comparison; stripped after dedup.
                "_sharpe": _oos_sharpe(doc),
            }
        )

    # --- Deduplication by composition hash ------------------------------------
    # For each hash group, retain the candidate with the highest OOS sharpe.
    best_by_hash: dict[str, dict] = {}
    deduped_count = 0

    for cand in valid_candidates:
        h = cand["composition_hash"]
        if h not in best_by_hash:
            best_by_hash[h] = cand
        else:
            if cand["_sharpe"] > best_by_hash[h]["_sharpe"]:
                best_by_hash[h] = cand
            deduped_count += 1

    # Strip the internal _sharpe key before returning.
    final_candidates = [
        {k: v for k, v in c.items() if k != "_sharpe"} for c in best_by_hash.values()
    ]

    # Apply limit after dedup/filter.
    if limit is not None:
        final_candidates = final_candidates[:limit]

    return {
        "available": True,
        "candidates": final_candidates,
        "stats": {
            "pulled": pulled,
            "valid": len(final_candidates),
            "missing_edn_string": missing_edn_string,
            "parse_failed": parse_failed,
            "validate_rejected": validate_rejected,
            "sharpe_filtered": sharpe_filtered,
            "deduped": deduped_count,
        },
        "source": "captplanet",
    }
