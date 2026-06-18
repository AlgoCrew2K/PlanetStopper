# TDD Handoff — lens-news-events upgrade

**For:** nl-implementer (blind to the feature plan — read ONLY this file)
**Branch:** feat/lens-news-events
**Worktree:** C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/lens-news-events

## Your job

Make 14 RED tests pass in `tests/ai_advisor/test_lens_gdelt.py` by modifying:
1. `advisors/lens_gdelt.py` — the GDELT producer
2. `ai_advisor.py` — the consumer (`_build_sentiment_section` + its `_GDELT_ARTLIST_URL` constant)

Do NOT modify the test file. Do NOT modify `advisors/lens_pipeline.py` (out of scope here).

Run after every change:
```
python -m pytest tests/ai_advisor/test_lens_gdelt.py -o addopts= -p no:cacheprovider -q
```
Target: 0 FAILED, 68 passed (54 existing + 14 new).

---

## Contract 1 — `advisors/lens_gdelt.py`

### 1a. Add English-language filter to both URL constants

Both `_GDELT_TONE_URL` and `_GDELT_ARTLIST_URL` must contain `sourcelang:eng` in the query string.

Before (current):
```
?query=stock+market+finance&mode=timelinetone&format=json
?query=stock+market+finance&mode=artlist&format=json&maxrecords=10
```

After (required):
```
?query=stock+market+finance+sourcelang:eng&mode=timelinetone&format=json
?query=stock+market+finance+sourcelang:eng&mode=artlist&format=json&maxrecords=10
```

GDELT accepts `sourcelang:eng` as a query operator. Append it after the existing query terms with a `+` separator (GDELT uses `+` as a URL-safe space).

### 1b. Add `_GDELT_MAX_EVENTS` named constant

```python
# Maximum number of events to surface from the artlist (named for prompt-budget control).
# Source: feature-plans/lens-news-events-upgrade.md AC-2 — ~5-8 events.
_GDELT_MAX_EVENTS: int = 7
```

Any value in [3, 10] is acceptable. 7 is the recommended default.

### 1c. Add `events` key to ALL return paths

The function currently returns:
```python
{"available", "tone", "per_ticker", "source", "sources", "reason"}
```

Add an `events` key to ALL return paths (success, unavailable, and the `_unavailable()` helper).

**`_unavailable()` helper** — add `"events": []` to its return dict:
```python
def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "tone": None,
        "per_ticker": None,
        "source": _GDELT_SOURCE,
        "sources": None,
        "reason": reason,
        "events": [],  # NEW
    }
```

### 1d. New helper `_extract_events`

Add this helper before `_fetch_gdelt_sentiment`:

```python
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
            deduped.append({
                "title": art.get("title", ""),
                "domain": domain,
                "seendate": art.get("seendate", ""),
            })

    # 4. Cap at _GDELT_MAX_EVENTS
    return deduped[:_GDELT_MAX_EVENTS]
```

### 1e. Call `_extract_events` after the artlist fetch (Step 4)

In `_fetch_gdelt_sentiment`, after building `sources` from the artlist response,
also call `_extract_events`:

```python
# After Step 4 artlist fetch, add:
articles_raw = articles  # the list of raw article dicts from artlist_data
events = _extract_events(articles_raw)
```

Pass the raw `artlist_data.get("articles", [])` list (before mapping to sources)
to `_extract_events`. Store as `events`.

### 1f. New availability rule — events-OR-tone

Replace the current unconditional Step 5 `available=True` return with:

```python
# available=True iff at least one signal is present (events OR tone)
has_events = bool(events)
has_tone = tone is not None

if not has_events and not has_tone:
    return {
        "available": False,
        "tone": None,
        "per_ticker": None,
        "source": _GDELT_SOURCE,
        "sources": None,
        "reason": "no_news_events",
        "events": [],
    }

# At least one signal present — return success with both
return {
    "available": True,
    "tone": tone,
    "per_ticker": None,
    "source": _GDELT_SOURCE,
    "sources": sources,
    "reason": None,
    "events": events,  # NEW
}
```

---

## Contract 2 — `ai_advisor.py`

### 2a. Fix `_GDELT_ARTLIST_URL` constant (around line 317)

The constant currently reads:
```python
_GDELT_ARTLIST_URL: str = (
    "https://api.gdeltproject.org/api/v2/doc/doc"
    "?query=stock+market+finance"
    "&mode=artlist"
    "&maxrecords=10"
    "&format=json"
    "&timespan=1440"
)
```

Add `+sourcelang:eng` to the query:
```python
_GDELT_ARTLIST_URL: str = (
    "https://api.gdeltproject.org/api/v2/doc/doc"
    "?query=stock+market+finance+sourcelang:eng"
    "&mode=artlist"
    "&maxrecords=10"
    "&format=json"
    "&timespan=1440"
)
```

### 2b. Add `events` to `_build_sentiment_section` payload

The current success-path return (around line 660) is:
```python
return {
    "lens": _lens,
    "available": True,
    "payload": {
        "article_count": len(articles),
        "tone_summary": None,
        "tone_score": tone_score,
    },
    "sources": sources,
}
```

Add `"events"` to the payload. Read it from `tone_result` (the producer result
already carries events after the lens_gdelt upgrade):

```python
return {
    "lens": _lens,
    "available": True,
    "payload": {
        "article_count": len(articles),
        "tone_summary": None,
        "tone_score": tone_score,
        "events": tone_result.get("events", []),  # NEW — from lens_gdelt producer
    },
    "sources": sources,
}
```

---

## Fixtures used by the RED tests

- `tests/fixtures/math/gdelt_artlist_events_captured.json` — English-filtered artlist (schema-derived)
- `tests/fixtures/math/gdelt_artlist_response.json` — existing artlist (used by prior tests)
- `tests/fixtures/math/gdelt_timelinetone_response.json` — existing tone response

---

## Invariants that must NOT regress (54 existing PASSING tests)

- Bounded retry: `_GDELT_MAX_ATTEMPTS == 4`, `_GDELT_BACKOFF_BASE_S == 20.0`, `_GDELT_BACKOFF_CAP_S == 60.0`, `_GDELT_INTER_REQUEST_S == 6.0`
- D-1: `reason = type(exc).__name__` only (never `str(exc)`)
- `available=True` => tone is float OR events is non-empty (after refactor)
- `_unavailable()` returns `available=False, tone=None, sources=None`
- Non-429 HTTP -> `reason='gdelt_fetch_failed'` (not `'HTTPError'`)
- `_GDELT_INTER_REQUEST_S` sleep called between tone GET and artlist GET

---

## When GREEN

Commit path-scoped (do NOT use git add -A):
```
git -C C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/lens-news-events \
  add advisors/lens_gdelt.py ai_advisor.py
```

Commit message: `fix(gdelt): news-events upgrade — English filter, events extraction, honest availability`

Quote SHA and counts: `N passed / 0 failed on <sha>`

SendMessage to nl-test-writer with: SHA + counts.
