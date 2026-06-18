# TDD Handoff — lens-news-events multi-source upgrade

**For:** nl-implementer (blind to the feature plan — read ONLY this file)
**Branch:** feat/lens-news-events
**Worktree:** C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/lens-news-events
**RED baseline:** 68 PASSED in test_lens_gdelt.py (commit 2649229 — do NOT regress these)

## Your job

Make the RED tests in `tests/ai_advisor/test_news_corpus.py` pass by:
1. Creating `advisors/news_corpus.py` — the new multi-source corpus builder module
2. Updating `ai_advisor.py` — to consume `news_corpus.build_news_corpus()` in `_build_sentiment_section`
3. Bumping `_GDELT_ARTLIST_URL` in `advisors/lens_gdelt.py` — `maxrecords=10 → 50`
4. Adding `feedparser` to `requirements.txt`
5. pip-installing feedparser in the worktree venv: `pip install feedparser`

Do NOT modify `tests/ai_advisor/test_news_corpus.py` or `tests/ai_advisor/test_lens_gdelt.py`.

Run after every change:
```
python -m pytest tests/ai_advisor/test_news_corpus.py tests/ai_advisor/test_lens_gdelt.py -o addopts= -p no:cacheprovider -p no:xdist -q
```
Target: 0 FAILED, 68 + (non-skipped new tests) passed.

The `@pytest.mark.live` test and any `pytest.skip()` are fine to leave skipped — they
are intentionally excluded from the default gate. Skips are not failures.

---

## Contract 1 — Create `advisors/news_corpus.py`

This is a new module. Public entry point: `build_news_corpus() -> dict`.

### Return shape

```python
{
    "available": bool,          # True iff tone is not None OR corpus is non-empty
    "tone": float | None,       # GDELT aggregate tone scalar (Facet A — unranked, independent)
    "corpus": list[dict],       # ranked, deduped, topic-tagged articles (Facet B)
    "reason": str | None,       # D-1: type(exc).__name__ or named reason when available=False; None when available
}
```

The `corpus` list is empty (not None) when no articles were fetched. `tone` is None when
GDELT timelinetone fails; it is still set even if all article feeds fail.

### Named constants (ALL must be module-level, named, with source comment)

```python
# --- Scoring weights (sum to 1.0) — from feature-plans/news-sources-reference.md ---
W_RECENCY: float = 0.40       # recency = exp(-Δt_hours / TAU_HOURS)
W_RELEVANCE: float = 0.35     # relevance = min(1.0, keyword_hit_count / 3)
W_AUTHORITY: float = 0.25     # authority = SOURCE_AUTHORITY[domain] or 0.4 (unknown)

TAU_HOURS: float = 24.0       # recency decay half-life in hours

TOP_K: int = 25               # maximum corpus size after sorting

DEDUP_JACCARD_THRESHOLD: float = 0.85  # title token-set Jaccard ≥ this → same story

# --- Authority table — from feature-plans/news-sources-reference.md ---
SOURCE_AUTHORITY: dict[str, float] = {
    "federalreserve.gov": 1.0,
    "sec.gov": 1.0,
    "bls.gov": 1.0,
    "bea.gov": 1.0,
    "apps.bea.gov": 1.0,
    "reuters.com": 0.9,
    "apnews.com": 0.9,
    "cnbc.com": 0.7,
    "marketwatch.com": 0.7,
    "yahoo.com": 0.6,
    "finance.yahoo.com": 0.6,
    "nasdaq.com": 0.6,
    # unknown domains default to 0.4 (see _authority() helper)
}
```

### User-Agent constants

```python
# .gov feeds (SEC, Fed, BLS, BEA) require a descriptive contact UA; they 403 on bare requests UA.
_UA_GOV: str = "PlanetStopper/1.0 paulmgreaney@gmail.com"
_UA_STD: str = "PlanetStopper/1.0 (market-news lens; paulmgreaney@gmail.com)"
```

### Feed list

Fetch ALL of these feeds (per-feed isolation: one feed failing does NOT stop the rest):

| Name | URL | UA constant |
|---|---|---|
| GDELT artlist | see lens_gdelt._GDELT_ARTLIST_URL | `_UA_STD` |
| Google News BUSINESS | `https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en` | `_UA_STD` |
| CNBC markets | `https://www.cnbc.com/id/10000664/device/rss/rss.html` | `_UA_STD` |
| MarketWatch top | `https://feeds.marketwatch.com/marketwatch/topstories/` | `_UA_STD` |
| Yahoo Finance | `https://finance.yahoo.com/news/rssindex` | `_UA_STD` |
| Federal Reserve press | `https://www.federalreserve.gov/feeds/press_all.xml` | `_UA_GOV` |
| BLS latest | `https://www.bls.gov/feed/bls_latest.rss` | `_UA_GOV` |
| BEA | `https://apps.bea.gov/rss/rss.xml` | `_UA_GOV` |
| SEC 8-K atom | `https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom&count=10` | `_UA_GOV` |

GDELT tone (timelinetone) is fetched separately — see Contract 2 below.

### Per-feed isolation

Wrap every `requests.get(url, headers={"User-Agent": ua}, timeout=12)` + feedparser parse
in its own try/except. On any exception: log `type(exc).__name__` (D-1 — NEVER str(exc)),
skip that feed, continue to the next.

```python
try:
    resp = requests.get(url, headers={"User-Agent": ua}, timeout=12)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    # normalize entries...
except Exception as exc:
    _log.warning("feed %s failed: %s", name, type(exc).__name__)
    continue
```

### Article normalization

Convert every feedparser entry to:

```python
{
    "url": str,           # item.link (strip query params for dedup; keep for display)
    "title": str,         # item.title
    "published": str,     # item.published or item.updated or "" (ISO string, best-effort)
    "domain": str,        # extracted from url (netloc, strip www.)
    "source_feed": str,   # feed name (e.g. "fed_press", "cnbc_markets", "sec_8k")
    "topics": list[str],  # assigned by _tag_topics() — always non-empty (defaults to ["broad-sentiment"])
    "score": float,       # computed by _score_article() — see below
}
```

For GDELT artlist entries (JSON, not feedparser): normalize the same way. Fields: `url`,
`title`, `seendate` (as `published`), `domain`. `source_feed="gdelt_artlist"`.

### Topic-tagging `_tag_topics(title: str) -> list[str]`

Pure stdlib. Case-insensitive keyword scan on title. Multi-label allowed. Never returns
empty — default is `["broad-sentiment"]`.

```
macro keywords:         {"federal reserve", "fed", "fomc", "rate", "inflation", "cpi",
                         "ppi", "gdp", "jobs", "payroll", "treasury", "yield", "recession"}
fundamentals keywords:  {"earnings", "revenue", "guidance", "eps", "dividend", "buyback",
                         "8-k", "10-k", "10-q"}
technicals keywords:    {"rally", "selloff", "breakout", "support", "resistance",
                         "moving average", "volume", "breadth"}
derivatives keywords:   {"options", "vix", "futures", "gamma", "open interest", "expiry", "hedge"}
broad-sentiment:        default (also: "fear", "greed", "risk-on", "risk-off", "volatility")
```

### Cross-source dedup `_dedup(articles: list[dict]) -> list[dict]`

1. **URL dedup:** canonical URL = netloc + path (strip query + fragment). Keep highest
   SOURCE_AUTHORITY on collision.
2. **Title Jaccard dedup:** token-set Jaccard of title.lower().split() between every pair.
   If ≥ `DEDUP_JACCARD_THRESHOLD`, keep the one with the higher SOURCE_AUTHORITY.
3. **Per-domain cap:** ≤ 3 articles per domain in the final output.

### Scoring `_score_article(article: dict, now: datetime) -> float`

```python
import math, datetime

def _authority(domain: str) -> float:
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
    """min(1.0, keyword_hit_count / 3) using the combined macro+fundamentals+technicals+derivatives vocab."""
    hits = sum(1 for kw in _RELEVANCE_KEYWORDS if kw in title.lower())
    return min(1.0, hits / 3)

def _score_article(article: dict, now: datetime.datetime) -> float:
    return (
        W_RECENCY    * _recency(article.get("published", ""), now)
        + W_RELEVANCE * _relevance(article.get("title", ""))
        + W_AUTHORITY * _authority(article.get("domain", ""))
    )
```

### `build_news_corpus()` skeleton

```python
import datetime, logging, math, requests, feedparser

_log = logging.getLogger(__name__)

def build_news_corpus() -> dict:
    """Fetch multi-source news corpus + GDELT tone. Never raises.

    Returns
    -------
    dict with keys: available, tone, corpus, reason.
    available=True iff tone is not None OR corpus is non-empty.
    D-1: reason is type(exc).__name__ or a named reason, never str(exc).
    """
    tone = _fetch_gdelt_tone()
    articles = _fetch_all_feeds()
    articles = _dedup(articles)
    now = datetime.datetime.utcnow()
    for art in articles:
        art["score"] = _score_article(art, now)
    articles.sort(key=lambda a: a["score"], reverse=True)
    corpus = articles[:TOP_K]

    available = tone is not None or bool(corpus)
    reason = None if available else "no_news_events"
    return {"available": available, "tone": tone, "corpus": corpus, "reason": reason}
```

### GDELT tone helper `_fetch_gdelt_tone() -> float | None`

Imports `_GDELT_TONE_URL` from `advisors.lens_gdelt` (the existing constant). Uses `_UA_STD`.
On any error returns `None` (never raises). Parses the JSON timeline and averages the tone values
(same logic as the existing tone extraction in lens_gdelt.py Step 2).

```python
def _fetch_gdelt_tone() -> float | None:
    try:
        from advisors import lens_gdelt
        resp = requests.get(
            lens_gdelt._GDELT_TONE_URL,
            headers={"User-Agent": _UA_STD},
            timeout=12,
        )
        resp.raise_for_status()
        data = resp.json()
        timeline = data.get("timeline", []) or []
        if not timeline:
            return None
        # Timeline entries: [{date, value}]; value is already normalised tone.
        values = [float(e["value"]) for e in timeline if "value" in e]
        return sum(values) / len(values) if values else None
    except Exception as exc:
        _log.warning("gdelt tone failed: %s", type(exc).__name__)
        return None
```

---

## Contract 2 — Update `advisors/lens_gdelt.py`

One change only: bump `maxrecords` in `_GDELT_ARTLIST_URL` from `10` to `50`.

Before:
```
&maxrecords=10
```
After:
```
&maxrecords=50
```

(The test `test_gdelt_artlist_url_maxrecords_is_at_least_50` will verify this.)

---

## Contract 3 — Update `ai_advisor.py` — `_build_sentiment_section`

The existing `_build_sentiment_section` fetches from GDELT directly. After this change it
must delegate to `news_corpus.build_news_corpus()` and surface both facets.

**Lazy import pattern** (CC-2 — never at module level):

```python
def _build_sentiment_section() -> dict:
    """Market sentiment via multi-source news corpus + GDELT tone (lens-news-events upgrade)."""
    try:
        from advisors import news_corpus as _news_corpus
        result = _news_corpus.build_news_corpus()
    except Exception as exc:
        return {"lens": "sentiment", "available": False, "payload": None,
                "sources": [], "reason": type(exc).__name__}

    if not result.get("available"):
        return {"lens": "sentiment", "available": False, "payload": None,
                "sources": [], "reason": result.get("reason")}

    return {
        "lens": "sentiment",
        "available": True,
        "payload": {
            "tone_score": result.get("tone"),
            "corpus": result.get("corpus", []),
        },
        "sources": [],
    }
```

The existing GDELT-specific logic (`_fetch_gdelt_sentiment` call, artlist URL constant usage,
warehouse persistence) can be kept intact in the function — just route the RETURN through
`news_corpus.build_news_corpus()` so the test can patch `news_corpus_mod.build_news_corpus`
and verify the payload shape.

IMPORTANT: The 2 consumer tests (`TestBuildSentimentSectionMultiSource`) attempt to patch
`advisors.news_corpus.build_news_corpus` after importing `advisors.news_corpus`. They skip
if the import fails. After GREEN, they must PASS (not skip).

---

## Contract 4 — `requirements.txt`

Add feedparser to `requirements.txt`:

```
feedparser>=6.0
```

Also pip-install it in the worktree venv so the tests actually run (not importorskip):
```
pip install feedparser
```

---

## Fixture paths

- `tests/fixtures/ai_advisor/fed_press.xml` — live-captured Fed RSS
- `tests/fixtures/ai_advisor/bea_rss.xml` — live-captured BEA RSS
- `tests/fixtures/ai_advisor/google_news_business.xml` — live-captured Google News BUSINESS RSS
- `tests/fixtures/ai_advisor/sec_8k_atom.xml` — live-captured SEC EDGAR Atom
- `tests/fixtures/ai_advisor/gdelt_artlist_maxrecords50.json` — schema-derived GDELT artlist (10 English articles)
- `tests/fixtures/math/gdelt_timelinetone_response.json` — existing GDELT tone fixture

All shape assertions only — never assert specific headline text or tone values.

---

## Invariants that must NOT regress (68 existing tests in test_lens_gdelt.py)

- All 68 tests in `tests/ai_advisor/test_lens_gdelt.py` must remain PASSING after your changes.
- `_GDELT_ARTLIST_URL` bump from 10→50 will make `test_gdelt_artlist_url_maxrecords_is_at_least_50`
  transition from FAIL to PASS (it lives in test_news_corpus.py but tests lens_gdelt's constant).
- The existing test `test_artlist_url_contains_english_language_filter` in test_lens_gdelt.py
  checks for `sourcelang:eng` in `_GDELT_ARTLIST_URL` — this was already added at 2649229,
  do NOT remove it.

---

## When GREEN

1. Confirm branch: `git -C C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/lens-news-events branch --show-current` → must be `feat/lens-news-events` (NEVER main).

2. Commit path-scoped:
```
git -C C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/lens-news-events \
  add advisors/news_corpus.py advisors/lens_gdelt.py ai_advisor.py requirements.txt
```
Commit message: `feat(news-corpus): multi-source ranked corpus + GDELT tone two-facet lens`

3. Quote SHA and counts: `N passed / 0 failed / K skipped on <sha>`

4. SendMessage to nl-test-writer with: SHA + counts.
