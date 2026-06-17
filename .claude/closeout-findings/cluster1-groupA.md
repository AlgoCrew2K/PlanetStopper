# Cluster 1 — Group A Findings: Lens Producers + Infra (F1–F8)

**Auditor:** closeout-audit-prism
**Worktree HEAD:** b1b6227 (branch: audit/ai-council-closeout-e2e, tracking origin/main 73dc603)
**Date:** 2026-06-17
**Market status:** OPEN (RTH); all live calls bounded (one per external API); no LIVE_EXECUTION contact; state DB read-only.

---

## F1 — Technicals lens

**[PASS]**

**Success arm — live direct call:**
```
ai_advisor._build_technicals_section()
→ available: True
→ lens: 'technicals'
→ payload keys: ['ma_posture', 'breadth', 'momentum']
→ breadth: 0.8  (80% of proxy basket above SMA50 — numeric, 0..1)
→ ma_posture sample: {'AGG': {'above_sma50': True, 'above_sma200': None}, 'EFA': {'above_sma50': True, 'above_sma200': None}}
→ momentum sample: {'AGG': 0.014, 'EFA': 0.031}
→ sources: 0  (expected — technicals has no clickable external sources per design)
→ reason: None
```

**Failure arm — static cite:**
`ai_advisor.py:511–514`:
```python
try:
    result = lens_technicals._fetch_technicals(universe)
except Exception as exc:
    result = {"available": False, "reason": type(exc).__name__}
```
`ai_advisor.py:527–534`:
```python
return {
    "lens": "technicals",
    "available": False,
    "reason": result.get("reason", "unavailable"),
    "payload": None,
    "sources": [],
}
```
D-1 contract: `type(exc).__name__` only. No `str(exc)`, no traceback.

**Note:** `above_sma200: None` for some tickers (insufficient 200-day history) — consistent with CLAUDE.md "excludes tickers with insufficient history". This is correct behaviour, not a defect.

---

## F2 — Sentiment/GDELT lens

**[PASS — with observation on per-source isolation behaviour]**

**Success arm — live direct call:**
```
ai_advisor._build_sentiment_section()
→ available: True
→ lens: 'sentiment'
→ payload keys: ['article_count', 'tone_summary', 'tone_score']
→ tone_score: 0.002837882352941176  (numeric — GDELT timelinetone succeeded)
→ tone_summary: None  (hardcoded placeholder at ai_advisor.py:651 — by design)
→ article_count: 0   (artlist endpoint returned 0 articles this call)
→ sources: 0 entries  (no artlist sources — tone-only path)
→ reason: None
```

**Per-source isolation confirmed:** tone succeeded (score present), artlist returned 0 articles. `available=True` is correct — per `ai_advisor.py:586–588`: "available=True if either tone OR artlist gave us something". This is the tone-only arm working as designed.

**Failure arm — static cite:**
`ai_advisor.py:560–566` (tone arm):
```python
except Exception as exc:
    tone_result = {"available": False, "tone": None, "reason": type(exc).__name__}
```
`ai_advisor.py:581–583` (artlist arm):
```python
except Exception as exc:
    artlist_reason = type(exc).__name__  # D-1: never str(exc)
```
`ai_advisor.py:588–608` (full-failure return, both arms down):
```python
if not tone_available and not artlist_available:
    reason = artlist_reason or tone_result.get("reason") or "gdelt_fetch_failed"
    ...
    return {"lens": _lens, "available": False, "reason": reason, ...}
```
D-1 contract: `type(exc).__name__` on both arms. No `str(exc)`.

**[OBSERVATION]** `tone_summary` is hardcoded `None` at `ai_advisor.py:651`. The payload field exists but is always `None` — it is a reserved-for-future-use field, not a populated signal. This is not a defect (the tone signal lives in `tone_score`) but should be confirmed not to mislead the synthesizer. [Non-blocking — not a D-1 or availability issue.]

---

## F3 — Derivatives lens + freshness guard

**[PASS]**

**Success arm — live direct call:**
```
ai_advisor._build_derivatives_section()
→ available: True
→ lens: 'derivatives'
→ payload keys: ['vix_level', 'vix_term_structure', 'risk_read', 'as_of_date']
→ vix_level: 16.41
→ vix_term_structure.regime: 'contango'
→ as_of_date: '2026-06-16'
→ risk_read: 'neutral'
→ sources: 1 entry
→ reason: None
```

`as_of_date = 2026-06-16` is 1 calendar day old (run date 2026-06-17). The freshness guard threshold is `_OPTIONS_PROXY_MAX_STALENESS_DAYS` (10 days per `lens_options_proxy.py:358`). 1 < 10 → data is fresh → `available=True` is correct.

**Freshness guard (stale arm) — static cite:**
`advisors/lens_options_proxy.py:347–368`:
```python
# Freshness guard: reject stale observations before computing any values.
# D-1: reason is a sentinel string, not type(exc).__name__,
# because staleness is a data quality decision, not an exception.
if obs_date < _today() - timedelta(days=_OPTIONS_PROXY_MAX_STALENESS_DAYS):
    logger.warning("Derivatives lens: VIXCLS observation %s is stale...", as_of_date, ...)
    return {
        "available": False,
        "reason": "stale_data",
        "source": _SOURCE_CITATION,
    }
```
Stale path returns `reason="stale_data"` — a curated sentinel, not `str(exc)` — consistent with D-1 (data quality decision, not an exception). PR #37 fix is confirmed in code.

**Outer failure arm — static cite:**
`ai_advisor.py:684–696`:
```python
except Exception as exc:
    exc_type = type(exc).__name__
    logger.warning("Derivatives lens unavailable (caller guard): %s", exc_type)
    return {"lens": _lens, "available": False, "reason": exc_type, "payload": None, "sources": []}
```

---

## F4 — Macro lens

**[PASS — live; FINDING: doc mislabel (stale "stub" claim in docs/generated/)]**

**Success arm — live direct call:**
```
ai_advisor._build_macro_section()
→ available: True
→ lens: 'macro'
→ payload: {'series': <dict of 4 FRED series>}
→ series['DGS10']: {'label': '10-Year Treasury...', 'value': '4.47', 'date': '2026-06-15'}
→ series['UNRATE']: {'label': 'Unemployment Rate', 'value': '4.3', 'date': '2026-05-01'}
→ series['CPIAUCSL']: {'label': 'Consumer Price Index (CPI-U)', 'value': '333.979', 'date': '2026-05-01'}
→ series['FEDFUNDS']: {'label': 'Federal Funds Effective Rate', 'value': '3.63', 'date': '2026-05-01'}
→ sources: 4 entries (one clickable FRED release URL per series)
→ sources[0]: {'title': '10-Year Treasury...', 'url': 'https://fred.stlouisfed.org/series/DGS10', 'published': '2026-06-15', 'lens': 'macro'}
→ reason: None
```
Macro is a LIVE FRED producer — NOT a stub. All 4 FRED series resolve with values, dates, and sources.

**Key-absent degradation — static cite:**
`ai_advisor.py:760–778`:
```python
fred_key = os.environ.get("FRED_API_KEY", "").strip()
...
    return {
        "lens": _lens,
        "available": False,
        "reason": "FRED_API_KEY not configured — register free at fred.stlouisfed.org",
        ...
    }
```
Key-absent returns `available=False` with an informative reason (not `type(exc).__name__` — this is a config-guard, not an exception). D-1 note: FRED embeds the API key in the URL, so `str(exc)` is explicitly avoided (see `ai_advisor.py:749–750` docstring).

---

### FINDING F4-DOC-1 — [FAIL] macro stub mislabel in docs/generated/

**Severity: HIGH** (AC-10 closeout fail — doc contradicts verified live behavior)

**Evidence:**
- `docs/generated/ai_advisor.md` line (grep confirmed): `"| _build_macro_section() | "macro" | Stub — available=False | FRED / US Treasury XML (not yet connected) |"`
- `docs/generated/ai_advisor.md` module header: `"...multi-lens pipeline (technicals wired; sentiment wired; derivatives wired with freshness guard; fundamentals wired with portfolio fan-out; macro stub)."`
- `docs/generated/INDEX.md` entry: `"...macro stub only"`

**Actual behavior:** `_build_macro_section()` is a live FRED producer returning `available=True` with 4 real FRED series (DGS10, UNRATE, CPIAUCSL, FEDFUNDS) and 4 clickable sources. It has been live since at minimum `ai_advisor.py:739` exists in the codebase — implemented as a full FRED fetcher.

**Required correction:** `docs/generated/ai_advisor.md` and `docs/generated/INDEX.md` must replace every `macro stub` claim with the accurate description of the live macro FRED producer. Tag for `closeout-doc`.

---

## F5 — Fundamentals lens (portfolio fan-out + single-ticker path)

**[PASS]**

**Portfolio path — live direct call (`ticker=None`):**
```
ai_advisor._build_fundamentals_section()
→ available: True
→ lens: 'fundamentals'
→ payload keys: ['tickers', 'coverage']
→ tickers: dict with 6 entries (AAPL, AMZN, GOOGL, MSFT, NVDA, XOM — from _FUNDAMENTALS_PROXY_UNIVERSE)
→ tickers['AAPL']: {'entity_name': ..., 'cik': ..., 'key_facts': dict with 5 keys}
→ tickers['AMZN']: {'entity_name': ..., 'cik': ..., 'key_facts': dict with 5 keys}
→ coverage: {'available': 6, 'universe': 8}  (6/8 tickers resolved — 2 SEC fetch failures)
→ sources: 6 entries (deduped SEC EDGAR filing URLs)
→ sources[0]: {'title': 'Apple Inc. 10-K (2018-11-05)', 'url': 'https://www.sec.gov/cgi-bin/...', ...}
→ reason: None
```
Fan-out over holdings∪proxy confirmed: `holdings={}` (market hours, flat), proxy floor (`_FUNDAMENTALS_PROXY_UNIVERSE`: 8 company tickers — AAPL/MSFT/GOOGL/AMZN/NVDA/JPM/XOM/JNJ) applied unconditionally. 6/8 resolved = honest per-ticker degradation (2 tickers failed SEC EDGAR fetch silently, others succeed).

**Single-ticker path — live direct call (`ticker="AAPL"`):**
```
ai_advisor._build_fundamentals_section(ticker="AAPL")
→ available: True
→ lens: 'fundamentals'
→ payload keys: ['entity_name', 'cik', 'key_facts']  (single-ticker shape — byte-preserved per PR #38)
→ entity_name: 'Apple Inc.'
→ cik: 320193
→ key_facts: dict with 5 keys
→ sources: 1 entry
```
Single-ticker path produces the original payload shape (`entity_name/cik/key_facts`) unchanged — PR #38 regression guard confirmed.

**All-fail arm — static cite:**
`ai_advisor.py:1185–1192`:
```python
if not per_ticker_results:
    return {
        "lens": _lens,
        "available": False,
        "reason": "no fundamentals available: all tickers failed SEC EDGAR fetch",
        "payload": None,
        "sources": [],
    }
```
Curated reason string (not `str(exc)` — D-1 consistent).

**Per-ticker isolation — static cite:**
`ai_advisor.py:1171–1174`:
```python
except Exception as exc:
    result = {"available": False, "reason": type(exc).__name__}
```
Failed tickers are omitted from `per_ticker_results`; survivors proceed.

---

## F6 — Universe floors

**[PASS]**

**Live `logic_holdings` state (read-only SQL via `database.load_state()`):**
```
Total unique logic_holdings tickers: 0
```
Holdings are empty (market hours, portfolios flat). This is the off-hours / proxy-floor-required scenario.

**Technicals proxy floor — code confirmed live (unconditional merge):**
`ai_advisor.py:504–509` (inside `_build_technicals_section`):
```python
# The proxy is a FLOOR that ensures the nightly Prism pipeline always receives
# a real universe. Live tickers are merged in on top.
tickers.update(lens_technicals._PROXY_UNIVERSE)
universe = sorted(tickers)
```
`advisors/lens_technicals.py:68–79`:
```python
_PROXY_UNIVERSE: list[str] = [
    "SPY", "QQQ", "IWM", "EFA", "AGG", "GLD", "XLF", "XLE", "XLV", "XLI",
]
```
10 tickers — SPY/QQQ/IWM/EFA/AGG/GLD + 4 sector ETFs.

**Fundamentals proxy floor — code confirmed live (unconditional union):**
`ai_advisor.py:368–377`:
```python
_FUNDAMENTALS_PROXY_UNIVERSE: frozenset[str] = frozenset({
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "JPM", "XOM", "JNJ",
})
```
8 large-cap COMPANY tickers (not ETFs — consistent with CLAUDE.md "8 large-cap COMPANY tickers — NOT ETFs, which lack companyfacts").
`ai_advisor.py:1152`: `universe = sorted(holdings_tickers | set(_FUNDAMENTALS_PROXY_UNIVERSE))`

**Live proof:** With `logic_holdings={}`, F1 returned `available=True` with `breadth=0.8` (proxy basket), and F5 returned `available=True` with 6 resolved company tickers. Neither was hollow. DE-TECH-002 floor works.

---

## F7 — Honest-availability contract

**[PASS]**

**5-key dict contract confirmed across all builders:**

All 5 success-arm live results carry: `{lens, available, reason (absent on True), payload (on True), sources (on True)}`. All failure-arm static cites return: `{lens, available=False, reason=<type-name or curated>, payload=None, sources=[]}`.

| Builder | Outer exception handler | D-1 confirmed |
|---|---|---|
| technicals | `ai_advisor.py:513–514` — `type(exc).__name__` | YES |
| sentiment (tone) | `ai_advisor.py:560–566` — `type(exc).__name__` | YES |
| sentiment (artlist) | `ai_advisor.py:581–583` — `type(exc).__name__` | YES |
| derivatives | `ai_advisor.py:684–696` — `type(exc).__name__` (D-1 note: FRED key in URL, str(exc) explicitly avoided) | YES |
| macro | `ai_advisor.py:801–804` — `type(exc).__name__` per-series; outer returns `available=False` with curated reason | YES |
| fundamentals | `ai_advisor.py:1171–1174` — `type(exc).__name__` per-ticker; `ai_advisor.py:1185–1192` all-fail curated | YES |

**`_call_lens_section` pipeline D-1 wrapper — static cite:**
`advisors/lens_pipeline.py:86–96`:
```python
except Exception as exc:
    exc_type = type(exc).__name__
    logger.warning("Lens %r failed: %s", lens_name, exc_type)
    return {"lens": lens_name, "available": False, "reason": exc_type, "sources": []}
```
Guarantees D-1 even if an individual builder raises unexpectedly (defense-in-depth).

**No `str(exc)` confirmed:** all handler paths use `type(exc).__name__`. FRED key leak risk specifically noted at `ai_advisor.py:749–750` in docstring — explicitly avoided.

---

## F8 — Citation validation

**[PASS]**

**`build_citation` filter — static cite:**
`ai_advisor.py:1230–1252`: All four fields (`title`, `url`, `published`, `lens`) required as non-empty strings; URL must start with `http://` or `https://`; bare scheme (`http://` with no host) rejected. Returns `None` on any violation; caller drops the `None` result.

**`_validate_and_filter_sources` pipeline filter — static cite:**
`advisors/lens_pipeline.py:140–148`:
```python
for citation in raw_sources:
    validated = ai_advisor.build_citation(citation)
    if validated is not None:
        valid_sources.append(validated)
```
Malformed citations are silently dropped (never surfaced). Only `available=True` lenses contribute sources (`:142–143`).

**Live call corroboration:**
- F3 derivatives: 1 source, keys `{title, url, published, lens}` confirmed.
- F4 macro: 4 sources, all carrying `{title, url: 'https://fred.stlouisfed.org/series/...', published: '...', lens: 'macro'}` — all well-formed.
- F5 fundamentals: 6 sources, all `https://www.sec.gov/...` URLs — all well-formed.
- F2 sentiment: 0 sources (artlist returned 0 articles this run — not a filter failure, just no articles).

---

## Summary — Group A

| Feature | Status | Key Evidence |
|---|---|---|
| F1 Technicals | PASS | Live: `available=True`, `breadth=0.8`, `ma_posture`+`momentum` over proxy; failure arm: `ai_advisor.py:513–534` D-1 |
| F2 Sentiment/GDELT | PASS | Live: `available=True`, `tone_score=0.003` (tone-only arm); artlist 0 articles (no filter failure); failure D-1: `:560–566`/`:581–583` |
| F3 Derivatives + freshness guard | PASS | Live: `available=True`, `vix_level=16.41`, `as_of_date=2026-06-16` (1d old < 10d threshold); stale guard: `lens_options_proxy.py:358–368` `reason="stale_data"` |
| F4 Macro | PASS (live behavior) / FAIL (doc) | Live: `available=True`, 4 FRED series; FINDING F4-DOC-1: `docs/generated/ai_advisor.md` + `INDEX.md` still say "macro stub" — **AC-10 closeout FAIL** |
| F5 Fundamentals fan-out | PASS | Live portfolio: `available=True`, 6/8 tickers, honest per-ticker degradation; single-ticker AAPL: byte-preserved shape; all-fail arm: `:1185–1192` |
| F6 Universe floors | PASS | Live `logic_holdings={}` (empty); technicals `breadth=0.8` and fundamentals 6 tickers both produced over proxy floors; `ai_advisor.py:508`+`:1152` unconditional union |
| F7 Honest-availability | PASS | All 5 builders + `_call_lens_section:86–96` use `type(exc).__name__` only; no `str(exc)` leakage; FRED key risk explicitly avoided |
| F8 Citation validation | PASS | `build_citation:1230–1252` filter confirmed; `_validate_and_filter_sources:140–148` drops malformed; live sources all well-formed |

**AC-10 flag:** FINDING F4-DOC-1 is a closeout FAIL for the macro lens. Correct the two stale-stub claims in `docs/generated/ai_advisor.md` and `docs/generated/INDEX.md` to reflect the live FRED producer. Tag: `closeout-doc`.

**Open observation (non-blocking):** F2 `tone_summary=None` is a hardcoded placeholder at `ai_advisor.py:651` — the actual tone signal is `tone_score`. Not a defect; the synthesizer should be briefed to use `tone_score`, not `tone_summary`.
