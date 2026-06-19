"""Multi-source market-news corpus builder.

Produces a two-facet sentiment/news result:
  Facet A — GDELT aggregate tone scalar (independent, always-valid, unranked).
  Facet B — ranked, cross-source-deduped, topic-tagged article corpus drawn from
             GDELT artlist + free RSS + .gov feeds.

Public entry point
------------------
``build_news_corpus() -> dict``

Return shape::

    {
        "available": bool,      # True iff tone is not None OR corpus is non-empty
        "tone":      float | None,  # GDELT AvgTone normalised to [-1,1] (Facet A)
        "corpus":    list[dict],    # ranked articles (Facet B); [] when none fetched
        "reason":    str | None,    # D-1: type(exc).__name__ or named reason; None on success
    }

Design invariants
-----------------
D-1:          every error path logs/returns type(exc).__name__ only — never str(exc).
Per-feed:     one feed failing (403/timeout/parse) degrades THAT feed only.
Never-raises: build_news_corpus() catches all exceptions; callers never see a throw.
Off-path:     CC-2 — never imported at module level by alpha_bot_execution.py.
No magic:     every weight/threshold is a named module-level constant with source comment.

Contract reference: feature-plans/lens-news-events-upgrade.md AC-1..AC-7.
"""

from __future__ import annotations

import calendar
import datetime
import logging
import math
import urllib.parse
from typing import Any

import feedparser
import requests

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scoring weights (sum to 1.0) — from feature-plans/news-sources-reference.md
# ---------------------------------------------------------------------------

# Recency weight: exp(-Δt_hours / TAU_HOURS)
W_RECENCY: float = 0.40

# Relevance weight: min(1.0, keyword_hit_count / 3)
W_RELEVANCE: float = 0.35

# Authority weight: SOURCE_AUTHORITY[domain] or 0.4 (unknown)
W_AUTHORITY: float = 0.25

# Recency decay half-life (hours) — from feature-plans/news-sources-reference.md
TAU_HOURS: float = 24.0

# Maximum corpus size after scoring and sorting — from feature-plans/news-sources-reference.md AC-3
TOP_K: int = 25

# Title token-set Jaccard threshold for same-story dedup — AC-3
DEDUP_JACCARD_THRESHOLD: float = 0.85

# Maximum articles per domain in the final corpus — AC-3
_PER_DOMAIN_CAP: int = 3

# ---------------------------------------------------------------------------
# Authority table — from feature-plans/news-sources-reference.md
# ---------------------------------------------------------------------------

SOURCE_AUTHORITY: dict[str, float] = {
    # .gov sources — primary data, highest authority
    "federalreserve.gov": 1.0,
    "sec.gov": 1.0,
    "bls.gov": 1.0,
    "bea.gov": 1.0,
    "apps.bea.gov": 1.0,
    # Wire services — near-primary
    "reuters.com": 0.9,
    "apnews.com": 0.9,
    # Tier-1 financial media
    "cnbc.com": 0.7,
    "marketwatch.com": 0.7,
    # Aggregators / secondary
    "yahoo.com": 0.6,
    "finance.yahoo.com": 0.6,
    "nasdaq.com": 0.6,
    # unknown domains default to 0.4 (see _authority() helper)
}

# ---------------------------------------------------------------------------
# User-Agent constants
# ---------------------------------------------------------------------------

# .gov feeds (SEC, Fed, BLS, BEA) require a descriptive contact UA; they 403 on bare requests UA.
_UA_GOV: str = "PlanetStopper/1.0 paulmgreaney@gmail.com"

# Standard UA for non-.gov feeds
_UA_STD: str = "PlanetStopper/1.0 (market-news lens; paulmgreaney@gmail.com)"

# ---------------------------------------------------------------------------
# Feed list: (name, url, ua_constant)
# All keyless, free, fetched serially with explicit UA + timeout.
# ---------------------------------------------------------------------------

_FEEDS: list[tuple[str, str, str]] = [
    # RSS/Atom feeds parsed via feedparser.
    # GDELT artlist articles are sourced via lens_gdelt._fetch_gdelt_sentiment in
    # build_news_corpus (not fetched here — no direct GDELT GET in this list).
    (
        "google_news_business",
        "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en",
        _UA_STD,
    ),
    (
        "cnbc_markets",
        "https://www.cnbc.com/id/10000664/device/rss/rss.html",
        _UA_STD,
    ),
    (
        "marketwatch_top",
        "https://feeds.marketwatch.com/marketwatch/topstories/",
        _UA_STD,
    ),
    (
        "yahoo_finance",
        "https://finance.yahoo.com/news/rssindex",
        _UA_STD,
    ),
    (
        "fed_press",
        "https://www.federalreserve.gov/feeds/press_all.xml",
        _UA_GOV,
    ),
    (
        "bls_latest",
        "https://www.bls.gov/feed/bls_latest.rss",
        _UA_GOV,
    ),
    (
        "bea_rss",
        "https://apps.bea.gov/rss/rss.xml",
        _UA_GOV,
    ),
    (
        "sec_8k",
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom&count=10",
        _UA_GOV,
    ),
]

# ---------------------------------------------------------------------------
# Topic keyword vocabulary — AC-4
# ---------------------------------------------------------------------------

_TOPIC_KEYWORDS: dict[str, frozenset[str]] = {
    "macro": frozenset(
        {
            "federal reserve",
            "fed",
            "fomc",
            "rate",
            "inflation",
            "cpi",
            "ppi",
            "gdp",
            "jobs",
            "payroll",
            "treasury",
            "yield",
            "recession",
        }
    ),
    "fundamentals": frozenset(
        {
            "earnings",
            "revenue",
            "guidance",
            "eps",
            "dividend",
            "buyback",
            "8-k",
            "10-k",
            "10-q",
        }
    ),
    "technicals": frozenset(
        {
            "rally",
            "selloff",
            "breakout",
            "support",
            "resistance",
            "moving average",
            "volume",
            "breadth",
        }
    ),
    "derivatives": frozenset(
        {
            "options",
            "vix",
            "futures",
            "gamma",
            "open interest",
            "expiry",
            "hedge",
        }
    ),
}

# Combined keyword set used for relevance scoring
_RELEVANCE_KEYWORDS: frozenset[str] = frozenset(
    kw for kws in _TOPIC_KEYWORDS.values() for kw in kws
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _authority(domain: str) -> float:
    """Return the authority score for a domain (default 0.4 for unknown)."""
    return SOURCE_AUTHORITY.get(domain, 0.4)


def _recency(published_str: str, now: datetime.datetime) -> float:
    """exp(-Δt_hours / TAU_HOURS); defaults to 0.5 on unparseable date."""
    try:
        pub = datetime.datetime.fromisoformat(published_str.replace("Z", "+00:00"))
        delta_h = (now - pub.replace(tzinfo=None)).total_seconds() / 3600
        return math.exp(-max(0.0, delta_h) / TAU_HOURS)
    except Exception:
        return 0.5


def _relevance(title: str) -> float:
    """min(1.0, keyword_hit_count / 3) using combined market vocab."""
    lower = title.lower()
    hits = sum(1 for kw in _RELEVANCE_KEYWORDS if kw in lower)
    return min(1.0, hits / 3)


def _score_article(article: dict[str, Any], now: datetime.datetime) -> float:
    """Compute composite score = W_RECENCY*recency + W_RELEVANCE*relevance + W_AUTHORITY*authority."""  # noqa: E501  # un-wrappable long line
    return (
        W_RECENCY * _recency(article.get("published", ""), now)
        + W_RELEVANCE * _relevance(article.get("title", ""))
        + W_AUTHORITY * _authority(article.get("domain", ""))
    )


def _tag_topics(title: str) -> list[str]:
    """Assign topic labels to an article based on title keywords.

    Multi-label allowed. Never returns empty — defaults to ['broad-sentiment'].
    """
    lower = title.lower()
    tags: list[str] = []
    for topic, keywords in _TOPIC_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            tags.append(topic)
    if not tags:
        tags = ["broad-sentiment"]
    return tags


def _extract_domain(url: str) -> str:
    """Extract netloc from a URL, stripping leading 'www.'."""
    try:
        netloc = urllib.parse.urlparse(url).netloc
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""


def _canonical_url(url: str) -> str:
    """Strip query string and fragment for dedup comparison."""
    try:
        parsed = urllib.parse.urlparse(url)
        return urllib.parse.urlunparse(parsed._replace(query="", fragment=""))
    except Exception:
        return url


def _jaccard(title_a: str, title_b: str) -> float:
    """Token-set Jaccard similarity of two title strings."""
    tokens_a = set(title_a.lower().split())
    tokens_b = set(title_b.lower().split())
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def _dedup(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cross-source deduplication:

    1. URL dedup — canonical URL (strip query/fragment); keep highest authority.
    2. Title Jaccard dedup — Jaccard >= DEDUP_JACCARD_THRESHOLD; keep highest authority.
    3. Per-domain cap — at most _PER_DOMAIN_CAP articles per domain.
    """
    # Step 1: URL dedup — keep highest authority per canonical URL
    url_seen: dict[str, dict[str, Any]] = {}
    for art in articles:
        canon = _canonical_url(art.get("url", ""))
        if not canon:
            url_seen[id(art)] = art  # degenerate: keep as-is
            continue
        if canon not in url_seen:
            url_seen[canon] = art
        else:
            existing = url_seen[canon]
            if _authority(art.get("domain", "")) > _authority(existing.get("domain", "")):
                url_seen[canon] = art

    deduped: list[dict[str, Any]] = list(url_seen.values())

    # Step 2: Title Jaccard dedup — O(n²) acceptable for small corpora
    kept: list[dict[str, Any]] = []
    for art in deduped:
        title = art.get("title", "")
        is_dup = False
        for kept_art in kept:
            if _jaccard(title, kept_art.get("title", "")) >= DEDUP_JACCARD_THRESHOLD:
                # Keep the one with higher authority
                if _authority(art.get("domain", "")) > _authority(kept_art.get("domain", "")):
                    # Replace the existing kept entry
                    idx = kept.index(kept_art)
                    kept[idx] = art
                is_dup = True
                break
        if not is_dup:
            kept.append(art)

    # Step 3: Per-domain cap
    domain_counts: dict[str, int] = {}
    capped: list[dict[str, Any]] = []
    for art in kept:
        domain = art.get("domain", "")
        count = domain_counts.get(domain, 0)
        if count < _PER_DOMAIN_CAP:
            capped.append(art)
            domain_counts[domain] = count + 1

    return capped


def _normalize_gdelt_articles(sources_raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize raw GDELT artlist records from lens_gdelt into the common article shape.

    Accepts the ``sources`` field from lens_gdelt._fetch_gdelt_sentiment's return dict —
    each record has {url, seendate, title, domain}. Converts seendate → published and
    assigns source_feed="gdelt_artlist". Pure function — no HTTP.

    Returns a list of normalized article dicts (empty list when sources_raw is empty).
    """
    normalized: list[dict[str, Any]] = []
    for src in sources_raw:
        url = src.get("url", "")
        domain = src.get("domain", "") or _extract_domain(url)
        title = src.get("title", "")
        published = src.get("seendate", "")
        normalized.append(
            {
                "url": url,
                "title": title,
                "published": published,
                "domain": domain,
                "source_feed": "gdelt_artlist",
                "topics": _tag_topics(title),
                "score": 0.0,  # assigned later by _score_article
            }
        )
    _log.info("gdelt_artlist: normalized %d articles from lens_gdelt", len(normalized))
    return normalized


def _fetch_rss_feed(name: str, url: str, ua: str) -> list[dict[str, Any]]:
    """Fetch a single RSS/Atom feed and normalize entries to the common article shape.

    Returns a list of normalized article dicts (empty list on failure).
    Per-feed isolation: any exception logs type(exc).__name__ and returns [].
    Never raises — D-1.
    """
    try:
        resp = requests.get(url, headers={"User-Agent": ua}, timeout=12)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        normalized: list[dict[str, Any]] = []
        for entry in parsed.entries:
            link = getattr(entry, "link", "") or ""
            title = getattr(entry, "title", "") or ""
            # Prefer published_parsed (time.struct_time feedparser always provides);
            # fall back to updated_parsed, then raw string as last resort.
            # RFC-822 strings (e.g. "Thu, 18 Jun 2026 12:01:05 GMT") cannot be parsed
            # by datetime.fromisoformat — use calendar.timegm + tz-aware fromtimestamp instead.
            _pp = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
            if _pp is not None:
                published = (
                    datetime.datetime.fromtimestamp(calendar.timegm(_pp), tz=datetime.UTC)
                    .replace(tzinfo=None)
                    .strftime("%Y-%m-%dT%H:%M:%S")
                )
            else:
                published = (
                    getattr(entry, "published", None) or getattr(entry, "updated", None) or ""
                )
            # Prefer entry.source.href (publisher URL) when present — Google News wraps
            # all article links in news.google.com/rss/articles/... wrapper URLs whose
            # domain is meaningless for authority scoring and cross-source dedup.
            _src_href = getattr(getattr(entry, "source", None), "href", None) or ""
            domain = _extract_domain(_src_href) if _src_href else _extract_domain(link)
            normalized.append(
                {
                    "url": link,
                    "title": title,
                    "published": str(published),
                    "domain": domain,
                    "source_feed": name,
                    "topics": _tag_topics(title),
                    "score": 0.0,  # assigned later
                }
            )
        _log.info("feed %s: fetched %d entries", name, len(normalized))
        return normalized
    except Exception as exc:
        _log.warning("feed %s failed: %s", name, type(exc).__name__)
        return []


def _fetch_all_feeds() -> list[dict[str, Any]]:
    """Fetch all RSS/Atom feeds. Per-feed isolation — never raises.

    GDELT artlist articles are sourced separately via lens_gdelt._fetch_gdelt_sentiment
    in build_news_corpus (single call for both tone + artlist — no direct GDELT GET here).
    """
    articles: list[dict[str, Any]] = []

    # RSS/Atom feeds only — GDELT artlist is handled in build_news_corpus
    for name, url, ua in _FEEDS:
        articles.extend(_fetch_rss_feed(name, url, ua))

    return articles


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_news_corpus() -> dict[str, Any]:
    """Fetch multi-source news corpus + GDELT tone. Never raises.

    Makes a single lens_gdelt._fetch_gdelt_sentiment call for both:
      Facet A — GDELT aggregate tone scalar (from result["tone"])
      GDELT corpus input — artlist articles (from result["sources"])
    This produces exactly 2 GDELT GETs (timelinetone + artlist, properly spaced
    by _GDELT_INTER_REQUEST_S inside lens_gdelt). No direct GDELT requests here.

    Returns
    -------
    dict with keys: available, tone, corpus, reason.
      available: True iff tone is not None OR corpus is non-empty.
      tone:      GDELT aggregate AvgTone normalised to [-1, 1]; None on failure.
      corpus:    ranked, deduped, topic-tagged articles; [] when none fetched.
      reason:    D-1 named reason when available=False; None on success.
    """
    # Single lens_gdelt call — 2 spaced GETs (timelinetone + artlist) inside.
    # Facet A: tone scalar; Facet B input: artlist articles via sources field.
    tone: float | None = None
    gdelt_articles: list[dict[str, Any]] = []
    try:
        from advisors import lens_gdelt  # CC-2: lazy import

        gdelt_result = lens_gdelt._fetch_gdelt_sentiment([])
        raw_tone = gdelt_result.get("tone")
        tone = float(raw_tone) if raw_tone is not None else None
        gdelt_articles = _normalize_gdelt_articles(gdelt_result.get("sources") or [])
    except Exception as exc:
        _log.warning("gdelt sentiment via lens_gdelt failed: %s", type(exc).__name__)

    # RSS/Atom feeds (no GDELT GETs)
    rss_articles = _fetch_all_feeds()

    articles = _dedup(gdelt_articles + rss_articles)

    # Use timezone-aware now to avoid DeprecationWarning (Python 3.12+)
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    for art in articles:
        art["score"] = _score_article(art, now)
    articles.sort(key=lambda a: a["score"], reverse=True)
    corpus = articles[:TOP_K]

    available = tone is not None or bool(corpus)
    reason = None if available else "no_news_events"
    return {
        "available": available,
        "tone": tone,
        "corpus": corpus,
        "reason": reason,
    }
