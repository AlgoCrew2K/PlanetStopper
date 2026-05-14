"""Symphony decision-tree logic: fetch, condense, and cache.

Composer's `/symphonies/{id}/score` endpoint returns the full decision tree for
a symphony — a deeply nested node structure that can exceed 1 MB. That raw tree
is far too large to drop into a Claude advisor prompt. This module fetches the
raw tree, distills it into a small (< 8 KB) structured summary that preserves
the load-bearing signal (traded universe, indicator usage, tree shape, creator
group labels), and caches the condensed result per symphony id.

The Composer client here reuses the existing auth + base-URL contract from
``alpha_bot_execution.py`` rather than reinventing it — the header keys
(``x-api-key-id``, ``authorization``) are the contract.
"""

from collections import Counter

import requests

from alpha_bot_execution import COMPOSER_BASE_URL, get_composer_headers

# Bounded timeout for the /score GET, consistent with fetch_symphony_stats (15s).
_SCORE_FETCH_TIMEOUT = 15

# Safe defaults for optional scalar fields absent from a malformed/empty tree.
_DEFAULT_NAME = ""
_DEFAULT_REBALANCE = ""

# Process-lifetime cache: symphony_id -> condensed summary dict.
# Symphony logic changes rarely (only on creator edits) and the daemon is
# long-lived, so a process-lifetime cache with an explicit clear_cache()
# invalidation hook is sufficient — no TTL needed.
_CONDENSED_CACHE: dict[str, dict] = {}


def fetch_symphony_score(symphony_id: str) -> dict:
    """GET the raw decision-tree score for a symphony from Composer.

    Reuses the project's Composer auth headers and base URL. Returns the raw
    JSON body (the decision-tree dict) on success; returns an empty dict on a
    transport error or non-200 response so callers downstream never crash on a
    transient Composer failure.
    """
    url = f"{COMPOSER_BASE_URL}/symphonies/{symphony_id}/score"
    try:
        response = requests.get(
            url, headers=get_composer_headers(), timeout=_SCORE_FETCH_TIMEOUT
        )
        if response.status_code == 200:
            try:
                return response.json()
            except ValueError as e:
                print(
                    f"Error parsing Composer /score JSON for {symphony_id}: "
                    f"HTTP {response.status_code} - {e}"
                )
                return {}
        print(
            f"Error fetching /score for symphony {symphony_id}: "
            f"HTTP {response.status_code}"
        )
    except requests.RequestException as e:
        print(f"Exception fetching /score for symphony {symphony_id}: {e}")
    return {}


def _scan_condition(cond, indicators: Counter) -> None:
    """Extract indicator fns from a compound/binary `condition` block.

    Indicator predicates inside `condition` blocks live in `lhs.fn` / `rhs.fn`,
    and compound conditions nest further conditions under `conditions`. This
    must recurse — a walk that reads only the flat node-level `lhs-fn`/`rhs-fn`
    keys undercounts indicator usage (the signal-loss bug).
    """
    if not isinstance(cond, dict):
        return
    for side in ("lhs", "rhs"):
        operand = cond.get(side)
        if isinstance(operand, dict) and operand.get("fn"):
            indicators[operand["fn"]] += 1
    for sub in cond.get("conditions", []) or []:
        _scan_condition(sub, indicators)


def condense_symphony_logic(raw_score: dict) -> dict:
    """Distill a raw Composer decision tree into a prompt-sized summary.

    Walks the full tree once, collecting:
      * name / rebalance — top-level scalars (safe defaults if absent)
      * asset_universe   — distinct set of every ticker-bearing node's ticker
      * indicators_used  — indicator-fn -> occurrence count, counting BOTH the
                           flat `lhs-fn`/`rhs-fn` keys AND fns nested inside
                           compound `condition` blocks
      * tree_depth / tree_breadth / total_nodes — structural metrics
      * group_labels     — distinct set of creator-authored group names

    Malformed input (empty dict, missing `children`, junk child entries) is
    tolerated and yields a well-shaped but empty summary — never an exception.
    """
    if not isinstance(raw_score, dict):
        raw_score = {}

    indicators: Counter = Counter()
    tickers: set[str] = set()
    group_labels: set[str] = set()
    max_depth = 0
    total_nodes = 0
    max_breadth = 0

    def walk(node, depth):
        nonlocal max_depth, total_nodes, max_breadth
        # An empty dict is "no tree" — it contributes no node. Any node with
        # actual content (a step, name, children, ...) counts as one node.
        if not isinstance(node, dict) or not node:
            return
        total_nodes += 1
        if depth > max_depth:
            max_depth = depth

        for fn_key in ("lhs-fn", "rhs-fn"):
            if node.get(fn_key):
                indicators[node[fn_key]] += 1

        if isinstance(node.get("condition"), dict):
            _scan_condition(node["condition"], indicators)

        if node.get("ticker"):
            tickers.add(node["ticker"])

        if node.get("step") == "group" and node.get("name"):
            group_labels.add(node["name"])

        children = node.get("children", []) or []
        if len(children) > max_breadth:
            max_breadth = len(children)
        for child in children:
            walk(child, depth + 1)

    walk(raw_score, 0)

    return {
        "name": raw_score.get("name", _DEFAULT_NAME),
        "asset_universe": sorted(tickers),
        "indicators_used": dict(indicators),
        "tree_depth": max_depth,
        "tree_breadth": max_breadth,
        "total_nodes": total_nodes,
        "group_labels": sorted(group_labels),
        "rebalance": raw_score.get("rebalance", _DEFAULT_REBALANCE),
    }


def get_condensed_logic(symphony_id: str, use_cache: bool = True) -> dict:
    """Fetch + condense a symphony's decision-tree logic, with optional caching.

    With ``use_cache=True`` (default), a previously condensed summary for the
    same ``symphony_id`` is returned without re-fetching from Composer. With
    ``use_cache=False`` the score is always re-fetched and re-condensed.
    """
    if use_cache and symphony_id in _CONDENSED_CACHE:
        return _CONDENSED_CACHE[symphony_id]

    raw_score = fetch_symphony_score(symphony_id)
    summary = condense_symphony_logic(raw_score)

    if use_cache:
        _CONDENSED_CACHE[symphony_id] = summary
    return summary


def clear_cache() -> None:
    """Invalidate the entire in-process condensed-logic cache.

    Explicit invalidation hook — required for deterministic testing and useful
    operationally when a creator edits a symphony mid-session.
    """
    _CONDENSED_CACHE.clear()
