"""GDELT tone/sentiment producer.

Produces a normalized market-sentiment tone from the GDELT 2.0 DOC API
(free, no API key required).  The primary signal is mean AvgTone from
the timelinetone endpoint, normalized from the GDELT [-100, 100] scale
to [-1.0, 1.0].  Source citations come from the artlist endpoint.

Public entry point
------------------
``_fetch_gdelt_sentiment(universe: list[str]) -> dict``

Returns a dict with keys:
    available  (bool)           -- True when tone was successfully extracted.
    tone       (float | None)   -- Normalized mean AvgTone in [-1.0, 1.0];
                                   None when unavailable.
    per_ticker (dict | None)    -- v1: ALWAYS None (universe-level only).
    source     (str)            -- Human-readable citation; always present.
    sources    (list | None)    -- Artlist citations; None when unavailable,
                                   [] when tone OK but artlist failed/empty.
    reason     (str | None)     -- Set only when available=False; None on success.

Design invariants
-----------------
Honest availability: tone is None => available is False (the prior bug was
    the reverse — available=True, tone=None — which is forbidden).
D-1: reason is type(exc).__name__ only — never str(exc) or the message.
Bounded retry: on 429 only, exponential backoff, at most _GDELT_MAX_ATTEMPTS
    total calls, each separated by >= _GDELT_BACKOFF_BASE_S seconds.
Off-execution-path: never imported at module level in alpha_bot_execution.py.

Contract reference: .claude/gdelt-contract.md (pinned 2026-06-15).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — named at their PINNED values (contract §5).
# Tests assert these values; they are load-bearing contract pins.
# ---------------------------------------------------------------------------

# Maximum number of HTTP attempts for the tone endpoint (1 initial + 3 retries).
# Source: contract §5 Amendment 1 — finite bound prevents the persistent-429 loop.
_GDELT_MAX_ATTEMPTS: int = 4

# Exponential backoff base (seconds) between 429 retries.
# Source: contract §5 Amendment 1 — 20.0 gives 4x margin above GDELT's 5s/req
# rate-limit window.  The prior value (1.0) caused a persistent-429 PC-crash;
# 5.0 is GDELT's literal floor with zero margin — 20.0 is the load-bearing fix.
_GDELT_BACKOFF_BASE_S: float = 20.0

# Per-attempt sleep ceiling (seconds).
# Source: contract §5 Amendment 1 — exponential schedule min(BASE * 2**i, CAP):
#   attempt 0 -> 20s, attempt 1 -> 40s, attempt 2 -> 60s (capped).
_GDELT_BACKOFF_CAP_S: float = 60.0

# Per-request connect + read timeout (seconds).
# Source: contract §5 PINNED — explicit to avoid urllib3's None default.
_GDELT_TIMEOUT_S: float = 15.0

# Minimum spacing between the tone GET and the artlist GET (seconds).
# Source: contract §5 Amendment 1 — GDELT rate-limits per-IP across modes;
# 6.0 gives margin above the 5s floor.
_GDELT_INTER_REQUEST_S: float = 6.0

# ---------------------------------------------------------------------------
# Endpoint URLs (universe-level, stock+market+finance query — contract §1)
# ---------------------------------------------------------------------------

_GDELT_TONE_URL: str = (
    "https://api.gdeltproject.org/api/v2/doc/doc"
    "?query=stock+market+finance&mode=timelinetone&format=json"
)

_GDELT_ARTLIST_URL: str = (
    "https://api.gdeltproject.org/api/v2/doc/doc"
    "?query=stock+market+finance&mode=artlist&format=json&maxrecords=10"
)

# Source citation string — always present in the return dict (contract §3).
_GDELT_SOURCE: str = (
    "GDELT 2.0 DOC API timelinetone — https://api.gdeltproject.org/"
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _unavailable(reason: str) -> dict[str, Any]:
    """Return the standard unavailable dict (contract §4)."""
    return {
        "available": False,
        "tone": None,
        "per_ticker": None,
        "source": _GDELT_SOURCE,
        "sources": None,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _fetch_gdelt_sentiment(universe: list[str]) -> dict[str, Any]:
    """Fetch GDELT tone/sentiment for the configured universe.

    Makes two HTTP GETs: timelinetone (tone signal) then artlist (citations).
    Retries the tone GET on HTTP 429 only, with exponential backoff bounded
    to _GDELT_MAX_ATTEMPTS total calls.

    Parameters
    ----------
    universe:
        List of ticker symbols.  v1 uses a fixed universe-level query
        (stock+market+finance) regardless of the tickers supplied;
        per_ticker is always None (contract §6).

    Returns
    -------
    dict
        Keys: available, tone, per_ticker, source, sources, reason.
        Never raises — all exceptions yield available=False with D-1 reason.
    """
    # --- Step 1: Fetch tone from timelinetone endpoint (with bounded retry) ---
    tone_data: dict[str, Any] | None = None
    try:
        for attempt in range(_GDELT_MAX_ATTEMPTS):
            resp = requests.get(_GDELT_TONE_URL, timeout=_GDELT_TIMEOUT_S)

            if resp.status_code == 429:
                # Detect 429 by status code only — the body is plaintext, NOT
                # JSON (contract §5: "do NOT parse the 429 body").
                if attempt < _GDELT_MAX_ATTEMPTS - 1:
                    sleep_s = min(
                        _GDELT_BACKOFF_BASE_S * (2 ** attempt),
                        _GDELT_BACKOFF_CAP_S,
                    )
                    logger.debug(
                        "GDELT timelinetone returned 429; retrying in %.1fs "
                        "(attempt %d/%d)",
                        sleep_s,
                        attempt + 1,
                        _GDELT_MAX_ATTEMPTS,
                    )
                    time.sleep(sleep_s)
                    continue
                # Final attempt also 429 — all attempts exhausted
                logger.info(
                    "GDELT timelinetone rate_limited after %d attempts",
                    _GDELT_MAX_ATTEMPTS,
                )
                return _unavailable("rate_limited")

            # Non-429: fail immediately on non-2xx (no retry on 5xx in v1)
            resp.raise_for_status()

            tone_data = resp.json()
            logger.info("GDELT timelinetone: HTTP %d", resp.status_code)
            break

    except Exception as exc:
        # D-1: type(exc).__name__ only — never str(exc)
        exc_type = type(exc).__name__
        logger.warning("GDELT timelinetone exception: %s", exc_type)
        return _unavailable(exc_type)

    # --- Step 2: Extract tone from the nested data field (contract §2) ---
    # Correct field path: timeline[0]["data"][k]["value"]
    # The prior bug read entry.get("value") from the series wrapper object
    # {series, data} — which has no "value" key at that level — so raw was
    # always empty and the producer returned available=True, tone=None (forbidden).
    try:
        timeline = (tone_data or {}).get("timeline", [])
        if not timeline:
            logger.debug("GDELT timelinetone: empty timeline list")
            return _unavailable("no_tone_data")

        series = timeline[0]
        points = series.get("data", [])
        raw = [
            p["value"]
            for p in points
            if isinstance(p.get("value"), (int, float))
        ]

        if not raw:
            logger.debug("GDELT timelinetone: no numeric values in data")
            return _unavailable("no_tone_data")

        mean_tone = sum(raw) / len(raw)
        # Normalize: GDELT AvgTone is [-100, 100]; divide by 100 then clamp.
        tone = float(max(-1.0, min(1.0, mean_tone / 100.0)))

    except Exception as exc:
        exc_type = type(exc).__name__
        logger.warning("GDELT tone extraction exception: %s", exc_type)
        return _unavailable(exc_type)

    # --- Step 3: Fetch sources from artlist endpoint (best-effort) ---
    # Artlist citations are best-effort — a failed artlist call does NOT
    # invalidate the tone signal.  On failure: sources=[] (not None).
    sources: list[dict[str, Any]] = []
    try:
        artlist_resp = requests.get(_GDELT_ARTLIST_URL, timeout=_GDELT_TIMEOUT_S)
        artlist_resp.raise_for_status()
        artlist_data = artlist_resp.json()
        articles = artlist_data.get("articles", [])
        # Map each article to the §3 source shape: {url, seendate, title, domain}.
        # Drop language/sourcecountry — not needed by the lens (contract §3).
        sources = [
            {
                "url": art.get("url", ""),
                "seendate": art.get("seendate", ""),
                "title": art.get("title", ""),
                "domain": art.get("domain", ""),
            }
            for art in articles
        ]
        logger.info(
            "GDELT artlist: HTTP %d, %d sources",
            artlist_resp.status_code,
            len(sources),
        )
    except Exception as exc:
        logger.debug(
            "GDELT artlist best-effort exception: %s (tone still valid)",
            type(exc).__name__,
        )
        sources = []

    # --- Step 4: Return the successful result ---
    # Invariant: available=True => tone is float in [-1,1], reason=None.
    return {
        "available": True,
        "tone": tone,
        "per_ticker": None,  # v1: universe-level only (contract §6)
        "source": _GDELT_SOURCE,
        "sources": sources,
        "reason": None,
    }
