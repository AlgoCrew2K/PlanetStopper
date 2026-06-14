"""Community strategies loader — stub (TDD RED phase).

Implementation pending. See feature-plans/community-strats-loader.md.
"""

from __future__ import annotations


def load_community_strategies(
    *,
    limit=None,
    min_oos_sharpe=None,
    client=None,
    force_refresh: bool = False,
) -> dict:
    """Load validated community strategies from the Atlas cache.

    Stub — not implemented. Returns honest-empty to make every assertion fail.
    """
    return {
        "available": False,
        "reason": "NotImplemented",
        "candidates": [],
        "stats": {
            "pulled": 0,
            "valid": 0,
            "missing_edn_string": 0,
            "parse_failed": 0,
            "validate_rejected": 0,
            "sharpe_filtered": 0,
            "deduped": 0,
        },
        "source": "captplanet",
    }
