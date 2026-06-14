"""Community strategies loader.

Pulls strategy documents from the captplanet MongoDB collection,
validates each via symphony_schema, deduplicates by composition, and
returns a well-formed result dict.

Off-execution-path. Advisory-only. No Flask routes, no execution flags.
pymongo and dns are lazy-imported inside _connect_mongo only.

D-1 contract: reason fields are always type(exc).__name__ — never the
exception message or any secret value.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from advisors import symphony_schema

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

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _strip_ids(tree: Any) -> Any:
    """Return a deep copy of tree with all 'id' keys removed.

    Used to produce a stable composition hash that is independent of the
    uuid4 node ids emitted by symphony_schema constructors.
    """
    if isinstance(tree, dict):
        return {k: _strip_ids(v) for k, v in tree.items() if k != "id"}
    if isinstance(tree, list):
        return [_strip_ids(item) for item in tree]
    return tree


def _composition_hash(tree: dict) -> str:
    """Return a deterministic SHA-256 hex digest of the tree's composition.

    Volatile 'id' fields are stripped before hashing so two structurally
    identical trees always produce the same hash regardless of their uuid4
    node ids.
    """
    canonical = json.dumps(_strip_ids(tree), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _oos_sharpe(candidate: dict) -> float:
    """Extract the OOS sharpe from a candidate dict for dedup quality comparison.

    Returns -inf when the candidate has no oos_metrics or no sharpe key so
    that candidates with metrics always win ties over those without.
    """
    oos = candidate.get("oos_metrics")
    if oos and isinstance(oos, dict) and "sharpe" in oos:
        return float(oos["sharpe"])
    return float("-inf")


# ---------------------------------------------------------------------------
# Internal Mongo connection (lazy import — module must import without pymongo)
# ---------------------------------------------------------------------------


def _connect_mongo():
    """Lazily import pymongo + dns and return the captplanet.strategies collection.

    DNS is overridden to use public resolvers (8.8.8.8 / 1.1.1.1) so SRV
    lookups bypass any local resolver that may be unavailable.

    The MONGO_URI env var is consumed here and NEVER returned or logged.
    """
    import dns.resolver  # noqa: F401 — import triggers dns package init
    from dns.resolver import Resolver  # noqa: PLC0415

    import pymongo  # noqa: PLC0415

    resolver = Resolver(configure=False)
    resolver.nameservers = ["8.8.8.8", "1.1.1.1"]
    dns.resolver.default_resolver = resolver

    # Fail fast (~10s) on a slow or unreachable Atlas cluster instead of
    # blocking for the ~30s urllib3 default.
    client = pymongo.MongoClient(
        os.environ["MONGO_URI"],
        serverSelectionTimeoutMS=10_000,
        connectTimeoutMS=10_000,
    )
    return client["captplanet"]["strategies"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_community_strategies(
    *,
    limit: int | None = None,
    min_oos_sharpe: float | None = None,
    client=None,
) -> dict:
    """Load and validate community strategies from the captplanet MongoDB collection.

    Parameters
    ----------
    limit:
        If not None, cap the number of raw documents fetched (before parse).
    min_oos_sharpe:
        If not None, exclude docs whose oos_metrics['sharpe'] is below this
        threshold.  Docs that lack oos_metrics or lack the 'sharpe' key are
        KEPT regardless.
    client:
        Optional injectable Mongo client.  If provided, the collection is
        extracted as client["captplanet"]["strategies"].  If None, _connect_mongo()
        is called to build a real client.

    Returns
    -------
    dict with keys:
        available (bool), candidates (list), stats (dict), source (str),
        and optionally reason (str) when available is False.
    """
    _EMPTY_STATS = {
        "pulled": 0,
        "valid": 0,
        "missing_edn_string": 0,
        "parse_failed": 0,
        "validate_rejected": 0,
        "deduped": 0,
    }

    # --- Resolve collection ---------------------------------------------------
    if client is not None:
        collection = client["captplanet"]["strategies"]
    else:
        try:
            collection = _connect_mongo()
        except Exception as exc:  # noqa: BLE001
            return {
                "available": False,
                "candidates": [],
                "stats": dict(_EMPTY_STATS),
                "source": "captplanet",
                "reason": type(exc).__name__,
            }

    # --- Fetch documents ------------------------------------------------------
    try:
        cursor = collection.find({}, _PROJECTION)
        if limit is not None:
            # Prefer cursor.limit() (pymongo native — bounds the query server-side).
            # Fall back to slicing for plain-list iterables (e.g. test mocks that
            # return a list directly from find()).
            if hasattr(cursor, "limit"):
                cursor = cursor.limit(limit)
            else:
                cursor = list(cursor)[:limit]
        raw_docs = list(cursor)
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "candidates": [],
            "stats": dict(_EMPTY_STATS),
            "source": "captplanet",
            "reason": type(exc).__name__,
        }

    # --- Empty collection guard -----------------------------------------------
    if not raw_docs:
        return {
            "available": False,
            "candidates": [],
            "stats": dict(_EMPTY_STATS),
            "source": "captplanet",
            "reason": "EmptyCollection",
        }

    pulled = len(raw_docs)
    missing_edn_string = 0
    parse_failed = 0
    validate_rejected = 0
    valid_candidates: list[dict] = []

    # --- Parse and validate each document ------------------------------------
    for doc in raw_docs:
        # Missing or empty edn_string field — no payload to parse.
        edn = doc.get("edn_string")
        if "sid" not in doc or not edn:
            missing_edn_string += 1
            continue

        # Parse edn_string (which is actually JSON per the contract).
        try:
            tree = json.loads(edn)
        except (json.JSONDecodeError, TypeError, ValueError):
            parse_failed += 1
            continue

        # Structural validation — HARD errors only.
        errors = symphony_schema.validate_tree(tree)
        if errors:
            validate_rejected += 1
            continue

        # Ticker extraction
        tickers = symphony_schema.extract_tickers(tree)

        # OOS metrics pass-through
        oos_metrics = doc.get("oos_metrics")

        # min_oos_sharpe filter — docs lacking the metric are KEPT.
        if min_oos_sharpe is not None:
            if (
                oos_metrics is not None
                and isinstance(oos_metrics, dict)
                and "sharpe" in oos_metrics
                and oos_metrics["sharpe"] < min_oos_sharpe
            ):
                # filtered out — NOT a drop-accounting event
                continue

        comp_hash = _composition_hash(tree)

        valid_candidates.append(
            {
                "sid": doc["sid"],
                "name": doc.get("name", ""),
                "tree": tree,
                "tickers": tickers,
                "oos_metrics": oos_metrics,
                "composition_hash": comp_hash,
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
            # Keep the one with the higher OOS sharpe
            if _oos_sharpe(cand) > _oos_sharpe(best_by_hash[h]):
                best_by_hash[h] = cand
            deduped_count += 1

    final_candidates = list(best_by_hash.values())

    # valid counts post-dedup survivors so the sum invariant holds:
    # pulled == valid + deduped + missing_edn_string + parse_failed + validate_rejected
    return {
        "available": True,
        "candidates": final_candidates,
        "stats": {
            "pulled": pulled,
            "valid": len(final_candidates),
            "missing_edn_string": missing_edn_string,
            "parse_failed": parse_failed,
            "validate_rejected": validate_rejected,
            "deduped": deduped_count,
        },
        "source": "captplanet",
    }
