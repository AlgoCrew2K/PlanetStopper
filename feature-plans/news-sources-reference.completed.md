# News-Sources Reference (for the multi-source lens cycle) — researcher 2026-06-18

**LOAD-BEARING GOTCHA:** SEC / CNBC / Fed / BLS return **HTTP 403 to a default User-Agent**.
Every feed fetch MUST set an explicit `User-Agent`. For `.gov` (esp. SEC) use a descriptive
contact UA: `User-Agent: PlanetStopper/1.0 paulmgreaney@gmail.com`. GDELT is UA-permissive.

## Endpoint catalog (all free + keyless; live-curl-verify at wiring — feed paths drift, it's 2026)

| Source | Endpoint | Format | Notes |
|---|---|---|---|
| GDELT timelinetone (TONE FACET) | `https://api.gdeltproject.org/api/v2/doc/doc?query=stock+market+finance&mode=timelinetone&format=json` | JSON | baseline tone, UNRANKED, always-valid floor. ~1 req/5s; 429 backoff already in lens_gdelt.py |
| GDELT artlist (CORPUS input) | `...&mode=artlist&format=json&maxrecords=50&sort=date` | JSON | per-article {url,seendate,title,domain}; bump maxrecords 10→~50 |
| Google News search (Reuters/AP proxy) | `https://news.google.com/rss/search?q=<q>+when:24h&hl=en-US&gl=US&ceid=US:en` | RSS | reverse-eng; ops: when:24h, intitle:, allinurl:reuters.com, OR, -term. CANONICAL replacement for dead Reuters/AP RSS. Wrapper URLs need resolving for clean dedup |
| Google News topic BUSINESS | `https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en` | RSS | broad fallback |
| Reuters / AP direct RSS | — | — | **DEAD (~2024)** — do NOT wire; use Google News proxy |
| CNBC | `https://www.cnbc.com/id/<ID>/device/rss/rss.html` | RSS | needs UA. IDs (VERIFY LIVE — sources conflicted): Markets/Finance 10000664, Economy 20910258, Investing 15839069, Earnings 15839135, Top 100727362/10001147 |
| MarketWatch | `https://feeds.marketwatch.com/marketwatch/topstories/` (+ /marketpulse/, /realtimeheadlines/) | RSS | needs UA |
| Yahoo Finance | `https://finance.yahoo.com/news/rssindex` ; per-ticker `https://finance.yahoo.com/rss/headline?s=<T>` | RSS | needs UA; best-effort (Yahoo deprecates quietly) |
| Nasdaq | `https://www.nasdaq.com/feed/rssoutbound` | RSS | needs UA; confirm live |
| SEC EDGAR getcurrent | `https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom&count=40` | Atom | **descriptive UA REQUIRED; 10 req/s HARD cap across all EDGAR (429+~10min IP block)**. Drop &type= for all forms |
| SEC full-text / submissions / companyfacts | `https://efts.sec.gov/LATEST/search-index?q=...` ; `https://data.sec.gov/submissions/CIK<10d>.json` | JSON | same 10 req/s; companyfacts already used by fundamentals lens |
| Federal Reserve press | `https://www.federalreserve.gov/feeds/press_all.xml` (+ press_monetary.xml, speeches.xml, testimony.xml) | RSS | needs UA; stable official |
| US Treasury | (RSS being retired; OFAC RSS gone 2025-01-31) | — | route via Google News `allinurl:home.treasury.gov` |
| BLS | `https://www.bls.gov/feed/bls_latest.rss` | RSS | needs UA; CPI/jobs/PPI/ECI |
| BEA | `https://apps.bea.gov/rss/rss.xml` | RSS | confirmed active 2025; GDP/trade/PCE |

## Ranking (NON-GDELT-tone; GDELT articles ARE in the corpus + ranked like the rest)
- **Normalize** every article (GDELT artlist + RSS via feedparser) → `{url,title,published,domain,source_feed}`.
- **Dedup across the COMBINED pool** (GDELT aggregates the same publishers the RSS feeds carry — collision is frequent): canonical-URL (strip utm_*/fragments; resolve Google News wrapper → publisher URL); title token-set Jaccard ≥ 0.85; ≤3 per domain. On collision keep highest `SOURCE_AUTHORITY[publisher_domain]` (a Reuters story is 0.9 whether via GDELT or Google News).
- **Score** `= 0.4·recency + 0.35·relevance + 0.25·authority`; recency=exp(-Δt_h/24); relevance=min(1, kw_hits/3); authority table: Fed/SEC/BLS/BEA=1.0, Reuters/AP=0.9, CNBC/MarketWatch=0.7, Yahoo/Nasdaq=0.6, unknown=0.4. Sort desc, top-K≈25. (All NAMED CONSTANTS.)
- **Topic-tag** (keyword→topic, multi-label, pure stdlib): macro {fed,fomc,rate,inflation,cpi,ppi,gdp,jobs,payroll,treasury,yield,recession} · fundamentals {earnings,revenue,guidance,eps,dividend,buyback,8-k,10-k,10-q} · technicals {rally,selloff,breakout,support,resistance,moving average,volume,breadth} · derivatives {options,vix,futures,gamma,open interest,expiry,hedge} · broad-sentiment {default / fear,greed,risk-on,risk-off,volatility}.

## Ingestion
- Add **`feedparser`** to requirements (pure-Python, tolerant). Fetch with `requests` (UA+timeout), then `feedparser.parse(resp.content)` — feedparser's own fetch can't set UA/timeout.
- Reuse the GDELT bounded-fetch/backoff discipline per feed.

Full cited report in the cycle transcript (news-sources-researcher). Verify endpoints live at wiring.
