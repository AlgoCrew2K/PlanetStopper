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
    "?query=stock+market+finance+sourcelang:eng&mode=timelinetone&format=json"
)

_GDELT_ARTLIST_URL: str = (
    "https://api.gdeltproject.org/api/v2/doc/doc"
    "?query=stock+market+finance+sourcelang:eng&mode=artlist&format=json&maxrecords=50"
)

# Maximum number of events to surface from the artlist (named for prompt-budget control).
# Source: feature-plans/lens-news-events-upgrade.md AC-2 — ~5-8 events.
_GDELT_MAX_EVENTS: int = 7

# Source citation string — always present in the return dict (contract §3).
_GDELT_SOURCE: str = "GDELT 2.0 DOC API timelinetone — https://api.gdeltproject.org/"


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
        "events": [],
    }


def _extract_events(sources_raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract a ranked, domain-deduped list of English-language events.

    Filters non-English articles (language != 'English'), deduplicates by domain
    (most-recent per domain kept), sorts most-recent-first by seendate,
    caps at _GDELT_MAX_EVENTS.

    Parameters
    ----------
    sources_raw:
        Raw article dicts from the GDELT artlist response.

    Returns
    -------
    list of dicts, each with keys: title, domain, seendate.
    Never raises.
    """
    # 1. Filter English-only
    english = [a for a in sources_raw if a.get("language") == "English"]

    # 2. Sort most-recent-first by seendate (GDELT format: YYYYMMDDTHHmmssZ —
    #    lexicographic sort is correct for this zero-padded ISO-like format)
    english.sort(key=lambda a: a.get("seendate", ""), reverse=True)

    # 3. Deduplicate by domain — keep the most-recent article per domain
    seen_domains: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for art in english:
        domain = art.get("domain", "")
        if domain and domain not in seen_domains:
            seen_domains.add(domain)
            deduped.append(
                {
                    "title": art.get("title", ""),
                    "domain": domain,
                    "seendate": art.get("seendate", ""),
                }
            )

    # 4. Cap at _GDELT_MAX_EVENTS
    return deduped[:_GDELT_MAX_EVENTS]


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
                        _GDELT_BACKOFF_BASE_S * (2**attempt),
                        _GDELT_BACKOFF_CAP_S,
                    )
                    logger.debug(
                        "GDELT timelinetone returned 429; retrying in %.1fs (attempt %d/%d)",
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

            # Non-429 non-2xx: named label per contract §4 — do NOT raise
            # (raise_for_status would yield reason="HTTPError" via the outer
            # except, which violates the named-label table in §4).
            if not (200 <= resp.status_code < 300):
                logger.info(
                    "GDELT timelinetone: HTTP %d -> gdelt_fetch_failed",
                    resp.status_code,
                )
                return _unavailable("gdelt_fetch_failed")

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
    #
    # NOTE: empty timeline / no numeric values -> tone=None but we do NOT return
    # early.  We continue to Step 4 (artlist) because events-OR-tone availability
    # means events alone can make available=True.  Only HTTP-level failures on the
    # tone endpoint (rate_limited, gdelt_fetch_failed, exception) short-circuit.
    #
    # _tone_unavail_reason tracks the tone-side failure label; used in Step 5
    # when events are also absent to preserve the 'no_tone_data' contract label
    # (contract §4 requires this label for the empty-timeline path).
    tone: float | None = None
    _tone_unavail_reason: str = "no_news_events"  # overridden below on tone-side failure
    try:
        timeline = (tone_data or {}).get("timeline", [])
        if not timeline:
            logger.debug("GDELT timelinetone: empty timeline list — will check events")
            _tone_unavail_reason = "no_tone_data"
        else:
            series = timeline[0]
            points = series.get("data", [])
            raw = [p["value"] for p in points if isinstance(p.get("value"), (int, float))]

            if not raw:
                logger.debug("GDELT timelinetone: no numeric values in data — will check events")
                _tone_unavail_reason = "no_tone_data"
            else:
                mean_tone = sum(raw) / len(raw)
                # Normalize: GDELT AvgTone is [-100, 100]; divide by 100 then clamp.
                tone = float(max(-1.0, min(1.0, mean_tone / 100.0)))

    except Exception as exc:
        exc_type = type(exc).__name__
        logger.warning("GDELT tone extraction exception: %s — will check events", exc_type)
        _tone_unavail_reason = exc_type
        # tone remains None; continue to artlist

    # --- Step 3: Rate-limit spacing before the artlist GET ---
    # Tone and artlist share GDELT's per-IP window (1 req / 5s).
    # Sleep _GDELT_INTER_REQUEST_S (6.0s) to clear the window before the
    # second call (contract §5 Amendment 1 — _GDELT_INTER_REQUEST_S = 6.0).
    time.sleep(_GDELT_INTER_REQUEST_S)

    # --- Step 4: Fetch sources from artlist endpoint (best-effort) ---
    # Artlist citations are best-effort — a failed artlist call does NOT
    # invalidate the tone signal.  On failure: sources=[], events=[].
    #
    # When artlist is successfully fetched (HTTP 200, no exception), we update
    # _tone_unavail_reason to 'no_news_events' — the artlist endpoint was reached,
    # so a subsequent both-absent result means neither tone nor news events were
    # available (not merely a tone-side parsing failure).
    sources: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    try:
        artlist_resp = requests.get(_GDELT_ARTLIST_URL, timeout=_GDELT_TIMEOUT_S)
        artlist_resp.raise_for_status()
        artlist_data = artlist_resp.json()
        articles_raw = artlist_data.get("articles", [])
        # Map each article to the §3 source shape: {url, seendate, title, domain}.
        # Drop language/sourcecountry — not needed by the lens (contract §3).
        sources = [
            {
                "url": art.get("url", ""),
                "seendate": art.get("seendate", ""),
                "title": art.get("title", ""),
                "domain": art.get("domain", ""),
            }
            for art in articles_raw
        ]
        # Extract ranked, domain-deduped English events as a first-class field.
        events = _extract_events(articles_raw)
        # Artlist was reached and returned a genuine artlist payload (has the
        # 'articles' key, even if empty).  If both signals are absent, report
        # 'no_news_events' rather than the tone-side failure label.
        if "articles" in artlist_data:
            _tone_unavail_reason = "no_news_events"
        logger.info(
            "GDELT artlist: HTTP %d, %d sources, %d events",
            artlist_resp.status_code,
            len(sources),
            len(events),
        )
    except Exception as exc:
        logger.debug(
            "GDELT artlist best-effort exception: %s (tone still valid)",
            type(exc).__name__,
        )
        sources = []
        events = []
        # _tone_unavail_reason keeps its Step-2 value (no_tone_data or exc type)

    # --- Step 5: Return the result — events-OR-tone availability gate ---
    # available=True iff at least one signal is present (events OR tone).
    # Prevents the prior bug: available=True with tone=None and no events.
    #
    # Reason label when both signals absent:
    # - 'no_tone_data'   — tone extraction failed (empty/non-numeric timeline),
    #                      AND events also absent.  Preserves contract §4 label
    #                      for the tone-side-only-failed path.
    # - 'no_news_events' — both tone AND events explicitly exhausted
    #                      (artlist was fetched, returned articles, but none
    #                      survived the English filter or artlist was empty).
    has_events = bool(events)
    has_tone = tone is not None

    if not has_events and not has_tone:
        return {
            "available": False,
            "tone": None,
            "per_ticker": None,
            "source": _GDELT_SOURCE,
            "sources": None,
            "reason": _tone_unavail_reason,
            "events": [],
        }

    # At least one signal present — return success with both.
    return {
        "available": True,
        "tone": tone,
        "per_ticker": None,  # v1: universe-level only (contract §6)
        "source": _GDELT_SOURCE,
        "sources": sources,
        "reason": None,
        "events": events,
    }
