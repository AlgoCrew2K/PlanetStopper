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

_GDELT_MAX_ATTEMPTS: int = 4           # initial + 3 retries; AMENDMENT 1
_GDELT_BACKOFF_BASE_S: float = 20.0   # first retry waits 20s — margin above GDELT's 5s window; AMENDMENT 1
_GDELT_BACKOFF_CAP_S: float = 60.0    # ramp ceiling; AMENDMENT 1: 20s→40s→60s
_GDELT_TIMEOUT_S: float = 15.0        # per-request socket timeout
_GDELT_INTER_REQUEST_S: float = 6.0   # spacing between tone GET and artlist GET; AMENDMENT 1


def _fetch_gdelt_sentiment(universe: list[str]) -> dict:  # type: ignore[return]
    """Fetch GDELT tone/sentiment for the configured universe.

    Returns a dict with keys: available, tone, per_ticker, source, sources, reason.
    See .claude/gdelt-contract.md for the full contract.
    """
    raise NotImplementedError("_fetch_gdelt_sentiment not implemented — TDD RED phase")
