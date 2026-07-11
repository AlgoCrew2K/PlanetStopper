"""Community strategies loader.

Pulls strategy documents from the captplanet MongoDB Atlas collection via the
weekly Atlas cache (`advisors.atlas_cache`), validates each document via
symphony_schema, deduplicates by composition, and returns a well-formed result
dict.

Off-execution-path. Advisory-only. No Flask routes, no execution flags.
pymongo is lazy-imported inside the fetch_fn closure only.

D-1 contract: reason fields are type(exc).__name__ for general exceptions,
or a fixed sentinel string for known failure modes (e.g., 'AtlasFetchTimeout')
— never the exception message or any secret value (MONGO_URI, hostname, credential).
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import math
import os
from typing import Any

from advisors import atlas_cache, symphony_schema

logger = logging.getLogger(__name__)

# Wall-clock bound for the live Atlas fetch leg. serverSelectionTimeoutMS /
# connectTimeoutMS do NOT cover mongodb+srv:// SRV/TXT DNS resolution (confirmed:
# hangs >50s with those set). 45s is intentionally generous: the bound exists to
# catch a genuine SRV/DNS hang (DE-CS-002), NOT to be tight. A cold connect +
# server-side sharpe-sort over ~11k docs was live-observed to exceed 12s on the
# droplet; the weekly cache (atlas_cache, ~7-day TTL) means this fetch runs at
# most once per week, so a 45s bound is acceptable. 45s still terminates a true
# hung SRV DNS resolution well before it would block the caller indefinitely.
# (DE-ATLAS-CACHE-001 / AC-5)
_ATLAS_FETCH_TIMEOUT_S: float = 45.0


class _AtlasFetchTimeout(Exception):
    """Raised when the bounded Atlas fetch exceeds _ATLAS_FETCH_TIMEOUT_S."""


# ---------------------------------------------------------------------------
# Mongo query constants
# ---------------------------------------------------------------------------

# Inclusion projection: fetch only the fields the loader reads.
# Omits 'backtest' and 'quantstats_metrics' (multi-MB arrays per doc).
# "_id": 0 explicitly suppresses the BSON ObjectId field. pymongo includes
# _id by default even when not in the inclusion list — ObjectId is not
# JSON-serializable and would cause atlas_cache's json.dumps to raise
# TypeError, silently dropping the cache row on every write attempt.
_PROJECTION: dict = {
    "_id": 0,
    "sid": 1,
    "name": 1,
    "edn_string": 1,
    "oos_metrics": 1,
}

# Client-side-ranked fetch cap: top-N docs by oos_metrics.Sharpe desc.
# Bounds memory and latency — captplanet.strategies holds ~11k docs; fetching
# all would OOM the 4GB droplet (multi-MB edn_string fields × 11k = several GB).
# Reduced 500 -> 100 (DE-ATLAS-SLOW-QUERY-001 amendment): edn_string is
# ~153KB/doc live, so even an indexed _id fetch of 500 full docs was
# live-observed to dominate the 45s timeout budget on its own, independent of
# sort/index. Tightened further 100 -> 50 (droplet-headroom margin,
# DE-ATLAS-SHARPE-FIELD-001 follow-up): the 100-cap live gate left only ~11s
# of margin under the 45s bound (33.95s observed). 50 roughly halves
# fetch+parse cost with zero functional impact — still covers
# MAX_COMMUNITY_CANDIDATES_PER_RUN=20 with 2.5x headroom for dedup/validation
# loss. The public `limit` param (post-fetch, post-dedup) is a separate,
# independent caller-level control — not the same as this cap.
_MAX_FETCH_DOCS: int = 50

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


def _parse_sharpe(oos_metrics: Any) -> float | None:
    """Parse oos_metrics['Sharpe'] (capital-S, string-valued on live
    captplanet.strategies docs — DE-ATLAS-SHARPE-FIELD-001) defensively to a float.

    Returns None — never raises — when the field is missing, or the value is
    non-numeric (e.g. 'N/A'), percent-formatted (e.g. '12.3%'), or NaN/Infinity.
    Python's bare float() accepts 'nan'/'inf' as valid floats, but neither is a
    valid Sharpe ratio, so both are explicitly rejected here.

    Shared by all 3 sharpe-consumption sites (client-side selection ranking via
    _oos_sharpe, the dedup tie-break via _oos_sharpe, and the min_oos_sharpe
    filter) so a single parse contract governs every read of this field.
    """
    if not isinstance(oos_metrics, dict):
        return None
    raw = oos_metrics.get("Sharpe")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return value


def _oos_sharpe(doc: dict) -> float:
    """Return the OOS sharpe from a doc's oos_metrics, or -inf if absent/invalid.

    A missing or unparseable sharpe is never 'better' than any present, valid
    sharpe; -inf ensures such docs always lose dedup ties and sort to the
    bottom of the client-side selection ranking (both consumers of this fn).
    """
    parsed = _parse_sharpe(doc.get("oos_metrics"))
    return parsed if parsed is not None else float("-inf")


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
        If not None, exclude docs whose oos_metrics['Sharpe'] is below this
        floor. Docs that lack oos_metrics, lack the 'Sharpe' key, or whose
        Sharpe value is unparseable (non-numeric/percent-formatted/NaN/Infinity)
        are KEPT — the floor only ever excludes a genuinely-parsed low value.
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

    # Closure-captured flag: set to True inside _bounded_fetch_fn when the
    # wall-clock timeout fires. checked after cached_pull returns None to
    # distinguish AtlasFetchTimeout from AtlasCacheUnavailable.
    # cached_pull catches all exceptions from fetch_fn (its never-raises contract),
    # so _AtlasFetchTimeout cannot propagate through it; the flag is the only
    # reliable signal available to the outer scope.
    _timeout_fired: list[bool] = [False]

    try:
        # Build a fetch_fn closure: lazy pymongo import inside so the module
        # is importable without pymongo installed.
        def _fetch_fn() -> list:
            """Connect to Atlas and return the projected strategy documents.

            Two-step fetch (DE-ATLAS-SLOW-QUERY-001, amended after a live-Atlas
            gate failure): captplanet.strategies has no usable index besides
            _id, so ANY server-side sort — even over a lightweight projection,
            which is what the pre-amendment version did — is an unindexed
            COLLSCAN across the full ~11,227-doc corpus and still blows through
            _ATLAS_FETCH_TIMEOUT_S. edn_string is also ~153KB/doc, so even an
            indexed _id fetch of too many full docs dominates the timeout
            budget on its own (independent of sort/index) — see _MAX_FETCH_DOCS.

            Step 1 — lightweight, UNSORTED, uncapped selection: pull only
            {_id, oos_metrics.Sharpe} for the whole collection. No sort=, no
            .limit() — no server-side sort of any kind runs against Mongo.
            Step 2 — client-side rank + bound: parse each doc's Sharpe
            defensively (via _oos_sharpe, missing/unparseable -> -inf, sorts to
            the bottom, never raises), sort descending in Python, slice to the
            top _MAX_FETCH_DOCS ids.
            Step 3 — targeted full-document fetch by the ids selected in step
            2, an indexed _id lookup, so it needs no server-side sort.
            """
            import pymongo  # noqa: PLC0415

            mongo_client = pymongo.MongoClient(
                os.environ["MONGO_URI"],
                serverSelectionTimeoutMS=10_000,
                connectTimeoutMS=10_000,
            )
            collection = mongo_client["captplanet"]["strategies"]

            selection_cursor = collection.find({}, {"_id": 1, "oos_metrics.Sharpe": 1})
            selection_docs = list(selection_cursor)
            selection_docs.sort(key=_oos_sharpe, reverse=True)
            top_ids = [doc["_id"] for doc in selection_docs[:_MAX_FETCH_DOCS]]

            full_cursor = collection.find({"_id": {"$in": top_ids}}, _PROJECTION)
            return list(full_cursor)

        def _bounded_fetch_fn() -> list:
            """Wrap _fetch_fn with a wall-clock timeout.

            Uses ThreadPoolExecutor with shutdown(wait=False) so a hung worker thread
            (e.g., blocked on SRV/TXT DNS resolution) does not block the caller on exit.
            The orphan thread is allowed to linger; MongoClient eventually errors.
            Sets _timeout_fired[0] = True before raising so the outer scope can
            distinguish a timeout from other cached_pull failures.
            """
            ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            fut = ex.submit(_fetch_fn)
            try:
                return fut.result(timeout=_ATLAS_FETCH_TIMEOUT_S)
            except concurrent.futures.TimeoutError:
                _timeout_fired[0] = True  # signal before cached_pull swallows
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
        # Check the closure flag to distinguish a wall-clock timeout from other
        # Atlas / cache failures (e.g., connectivity error, corrupt cache).
        if raw_docs is None:
            reason = "AtlasFetchTimeout" if _timeout_fired[0] else "AtlasCacheUnavailable"
            return {
                "available": False,
                "reason": reason,
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

    except Exception as exc:  # noqa: BLE001
        # When the timeout wrapper raises _AtlasFetchTimeout (which propagates if
        # cached_pull is mocked to call fn() directly), treat it identically to the
        # closure-flag path — both must surface reason="AtlasFetchTimeout".
        reason = "AtlasFetchTimeout" if _timeout_fired[0] else type(exc).__name__
        return {
            "available": False,
            "reason": reason,
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

        # Sharpe filter — docs with a missing/unparseable Sharpe are KEPT
        # regardless of floor; only a genuinely-parsed low value is excluded.
        oos_metrics = doc.get("oos_metrics")
        if min_oos_sharpe is not None:
            parsed_sharpe = _parse_sharpe(oos_metrics)
            if parsed_sharpe is not None and parsed_sharpe < min_oos_sharpe:
                sharpe_filtered += 1
                continue

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
