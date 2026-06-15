# Stub — implementation pending (TDD RED phase)
"""GDELT tone/sentiment producer stub.

Real implementation goes here. This file exists only so test imports resolve.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Constants — named at their PINNED values (contract §5).
# The implementer MUST use these exact names and MUST NOT change the values.
# Tests assert the values are correct (they are load-bearing contract pins).
# ---------------------------------------------------------------------------

_GDELT_MAX_ATTEMPTS: int = 3
_GDELT_BACKOFF_BASE_S: float = 5.0
_GDELT_BACKOFF_CAP_S: float = 30.0
_GDELT_TIMEOUT_S: float = 15.0


def _fetch_gdelt_sentiment(universe: list[str]) -> dict:  # type: ignore[return]
    """Fetch GDELT tone/sentiment for the configured universe.

    Returns a dict with keys: available, tone, per_ticker, source, sources, reason.
    See .claude/gdelt-contract.md for the full contract.
    """
    raise NotImplementedError("_fetch_gdelt_sentiment not implemented — TDD RED phase")
