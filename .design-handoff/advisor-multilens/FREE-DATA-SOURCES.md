# FREE / No-Cost Data Sources — Exhaustive Catalogue (Retail Quant Advisor)

**Researcher:** quant data researcher (Explore/research role)
**Date:** 2026-06-10
**Complements:** `.design-handoff/advisor-multilens/DATA-SOURCES.md` (Alpaca-centric; under-swept the FREE landscape)
**Operator constraint:** "Go as free as possible — data is already costing me." This catalogue maximizes **$0** coverage across all 7 lenses.

> **Confidence summary:** High confidence on the government/open-data tier (SEC EDGAR, FRED, BLS, BEA, Treasury, World Bank, OECD, ECB, EIA, CBOE, OCC) and GDELT — these are authoritative, free, and largely auth-free. Medium confidence on free-tier *commercial* quotas (Alpha Vantage, Tiingo, Twelve Data, Finnhub, FMP, Marketaux, Polygon/Massive, EODHD, Nasdaq Data Link) — quotas drift and several pricing pages are JS-rendered / 403 to fetchers; each quota is date-stamped and flagged where I could only corroborate via Tier-3/4 sources. Unofficial/scraped sources (yfinance, Stooq, pytrends) carry reliability + ToS risk, flagged inline.

> **Methodology note / staleness caveat:** Every factual claim below carries an inline source URL from a live search/fetch this session (2026-06-10). The official SEC pages (`sec.gov/...`) returned **HTTP 403 to the WebFetch tool** (bot-blocking), so SEC limits/headers are corroborated via the SEC's own quoted text surfaced in search + independent Tier-3/4 guides — flagged `[corroborated, not direct-fetched]` where that applies. Commercial free-tier quotas are the highest staleness risk; treat each as **needs live re-check at build time**.

---

## How to read the tables

Columns per lens row:

| Source | Lens(es) | What it provides | Access method | Auth | Free rate limit / quota (2026-06-10) | Citation/article-URL + timestamp? | Freshness | ToS / legal caveat |

"Citation/article-URL + timestamp?" = **Y** means the source itself returns a clickable filing/article/release URL you can open (the operator's hard click-through requirement), **N** means it returns numbers/series only.

---

## Lens 1 — Technicals (price, volume, bars, indicators)

| Source | Lens(es) | What it provides | Access | Auth | Free quota (2026-06-10) | Click-through URL? | Freshness | ToS / caveat |
|---|---|---|---|---|---|---|---|---|
| **Alpaca Market Data (Basic)** | 1 | OHLCV bars, IEX feed | REST JSON | free key | Already integrated; IEX-only, 15-min delayed history on Basic | N | delayed (IEX) | Already plumbed in `synthetic_history.py`. Per existing survey. |
| **Twelve Data (free Basic)** | 1,4,5 | Real-time + historical quotes/bars, US equities/forex/crypto, some indicators | REST JSON | free key | **8 calls/min, 800 calls/day** | N | realtime (quote) / EOD | Free Basic plan limits per [Twelve Data trial docs](https://support.twelvedata.com/en/articles/5335783-trial) and [MEXC 2026 review](https://www.mexc.com/news/476023). |
| **Tiingo (free tier)** | 1,2,5 | EOD prices, IEX real-time (derived ref price), news, fundamentals | REST JSON | free key | **500 unique symbols/mo, 50 req/hr, 1,000 req/day, 1 GB/mo** | partial (news has URLs) | EOD + IEX realtime (derived) | Since 2025-02-01 IEX requires a signed market-data agreement for the true TOPS feed; Tiingo free returns a *derived* reference price instead. [Tiingo pricing](https://www.tiingo.com/about/pricing), [Tiingo IEX API](https://www.tiingo.com/products/iex-api). |
| **Alpha Vantage (free key)** | 1,2,4,5 | Quotes, bars, 50+ technical indicators (RSI/MACD/BBANDS computed server-side), FX, crypto, some fundamentals + news sentiment | REST JSON | free key | **25 req/day, 5 req/min** (very tight) | partial (NEWS_SENTIMENT returns article URLs) | realtime/EOD | 25/day is the binding constraint as of 2026. [Macroption AV limits](https://www.macroption.com/alpha-vantage-api-limits/), [AlphaLog 2026 guide](https://alphalog.ai/blog/alphavantage-api-complete-guide). Unique value: it *computes* indicators server-side (no pandas-ta needed). |
| **Finnhub (free)** | 1,2,4,5 | Quotes, candles, company news, basic financials, earnings, some macro | REST JSON | free key | **60 calls/min** | Y (company-news `url`) | realtime/EOD | Best free per-minute quota of the commercial hubs. [Finnhub pricing](https://finnhub.io/pricing-stock-api-market-data). |
| **Stooq (CSV)** | 1 | EOD OHLCV for US/PL/DE/JP/HU stocks + indices; bulk historical CSV | **CSV download** (no REST API) | none | No documented limit; bulk zipped CSV | N | EOD | No programmatic API — web/CSV only; courtesy use. [Stooq DB](https://stooq.com/db/), [QuantStart Stooq intro](https://www.quantstart.com/articles/an-introduction-to-stooq-pricing-data/). |
| **yfinance / Yahoo Finance** ⚠️ | 1,3,5 | OHLCV bars, fundamentals, **option chains**, holders | **unofficial scrape** (Python) | none | No official limit; **gets blocked / breaks unpredictably** | partial | realtime/delayed/EOD | UNOFFICIAL — uses Yahoo front-end endpoints; Yahoo ToS = personal use only; library "not affiliated, endorsed or vetted by Yahoo." Reliability risk: endpoints break + IP blocks. [yfinance GitHub](https://github.com/ranaroussi/yfinance), [Yahoo API ToS](https://legal.yahoo.com/us/en/yahoo/terms/product-atos/apiforydn/index.html), [why yfinance gets blocked](https://medium.com/@trading.dude/why-yfinance-keeps-getting-blocked-and-what-to-use-instead-92d84bb2cc01). |
| **EODHD (free tier)** | 1,5 | EOD historical + limited fundamentals (US) | REST JSON | free key | **20 API calls/day**, 1 yr history depth | N | EOD | Tight free tier; mainly for prototyping. [EODHD pricing](https://eodhd.com/pricing), [EODHD limits](https://eodhd.com/financial-apis/api-limits). |
| **Nasdaq Data Link (free datasets)** | 1,4,5 | Selected free datasets (e.g., WIKI legacy, some macro/rates publishers) | REST JSON / CSV | free key | Free key; **some datasets free, most now paid**; per-tier limits not published live | partial | EOD/daily | Free-dataset footprint has shrunk over the years; verify the specific dataset is free before relying. [Nasdaq Data Link docs](https://docs.data.nasdaq.com/), [getting started](https://docs.data.nasdaq.com/docs/getting-started). |

**Indicators note:** Alpha Vantage is the only free source above that *computes* indicators server-side; with everyone else (Alpaca, Tiingo, Twelve Data, Stooq, yfinance) you compute indicators operator-side from bars (pandas-ta / ta-lib). That's a derivation, not a data-acquisition cost.

---

## Lens 2 — Sentiment / News

| Source | Lens(es) | What it provides | Access | Auth | Free quota (2026-06-10) | Click-through URL? | Freshness | ToS / caveat |
|---|---|---|---|---|---|---|---|---|
| **GDELT 2.0 DOC API** ⭐ | 2,7 | Global news article search with **tone score**, theme/entity filters; `url`, `title`, `seendate`, `domain`, `language`, `sourcecountry` per article | REST **JSON** (JSONFeed mode) | **none** | **No API key, no documented hard cap** (be courteous; default search window = last 3 months) | **Y — `url` + `seendate`** | **updates ~every 15 min** | Free + open; huge global coverage. Tone filters (`tone>5`, `tone<-5`, `toneabs`). [GDELT DOC 2.0 debut](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/), [JSONFeed support](https://blog.gdeltproject.org/gdelt-doc-2-0-api-supports-jsonfeed/), [Python client](https://github.com/alex9smith/gdelt-doc-api). |
| **GDELT GKG (Global Knowledge Graph)** | 2 | People/orgs/locations/themes/**emotions**/counts extracted from global news | CSV download + **Google BigQuery** | none (BigQuery has its own free-query tier) | "100% free and open"; updates every 15 min | partial (links back to source docs) | ~15 min | Heavier lift (CSV/BigQuery). [GDELT data page](https://www.gdeltproject.org/data.html). |
| **Alpaca News API (Benzinga-sourced)** | 2 | Headlines + body, `url`, `created_at` | REST JSON | free key | ~130+ articles/day; numeric cap not published (429 + `X-RateLimit-*`) | **Y — `url`** | realtime | Already integrated. Per existing survey. |
| **Marketaux (free)** | 2 | Pre-scored sentiment + entity ranking, `url`, `source`, `published_at` | REST JSON | free key | **100 req/day** | **Y — `url` + `published_at`** | realtime | 5,000+ sources. [Marketaux pricing](https://www.marketaux.com/pricing), [freeapihub listing](https://freeapihub.com/apis/marketaux). |
| **Finnhub company-news + news-sentiment** | 2 | Company news (`url`, `datetime`), aggregate sentiment score, social buzz | REST JSON | free key | **60 calls/min** | **Y on company-news** (N on aggregate sentiment) | realtime | Social-sentiment (Reddit/Twitter) tier gating has shifted historically — `[Low]`, test on a live key. [Finnhub pricing](https://finnhub.io/pricing-stock-api-market-data). |
| **Reddit Data API** | 2,7 | Subreddit posts/comments (r/wallstreetbets, r/stocks, etc.); ticker mention/sentiment raw material | REST JSON | **OAuth (free)** | **OAuth: ~60–100 req/min** (per registered client, 10-min sliding window); **unauth: 10 req/min by IP** | Y (permalink to thread) | realtime | Free tier exists but is the post-2023 commercial-API regime — non-commercial OK; redistribution/commercial use is gated. [Reddit API rate limits 2026](https://painonsocial.com/blog/reddit-api-rate-limits-guide), [Reddit Data API wiki](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki). |
| **StockTwits API** | 2,7 | 30 most-recent messages per symbol stream; cashtag sentiment | REST JSON | none (basic stream) | **~200 calls/hour** (unauthenticated) | Y (message permalinks) | realtime | Unauthenticated symbol stream is free + key-less; deeper/Firestream tiers gated. [StockTwits symbol stream](https://github.com/janlukasschroeder/stocktwits-api), [AlgoFin StockTwits sentiment](https://algofin.substack.com/p/using-stocktwits-to-understand-investor). |
| **Google Trends (pytrends)** ⚠️ | 2,7 | Search-interest time series by keyword/ticker | **unofficial scrape** (Python) | none | **No official limit; aggressively 429-rate-limited**; often needs proxies | N (interest index only) | daily/weekly | UNOFFICIAL — not a supported Google API; brittle, frequent 429s. [pytrends 429 issue](https://github.com/GeneralMills/pytrends/issues/492). |
| **Wikipedia / Wikimedia Pageviews API** | 2,7 | Daily pageview counts per article (e.g., a company page) = attention proxy | REST JSON | none (User-Agent required) | "No fixed limit" but may block if you endanger stability; **User-Agent header mandatory** | Y (article links) | daily | Free + open; courteous use + UA policy. [Wikimedia REST API](https://www.mediawiki.org/wiki/Wikimedia_REST_API), [Pageview API](https://en.wikiversity.org/wiki/MediaWiki_API/Pageview_API), [WMF UA policy](https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy). |
| **Hacker News (Firebase) API** | 2,7 | Tech/startup news + discussion items; `url`, `time` | REST JSON | **none** | **No documented rate limit** | Y (`url` + `time`) | realtime | Fully free, no key. Tech/startup tilt (relevant for tech tickers). [Official HN API](https://github.com/hackernews/api), [HN API guide](https://cotera.co/articles/hacker-news-api-guide). |

⭐ = top free win. ⚠️ = unofficial/scraped reliability risk.

---

## Lens 3 — Derivatives / Options (IV, put/call, greeks, flow)

| Source | Lens(es) | What it provides | Access | Auth | Free quota (2026-06-10) | Click-through URL? | Freshness | ToS / caveat |
|---|---|---|---|---|---|---|---|---|
| **CBOE Daily Market Statistics** ⭐ | 3 | **Daily put/call ratios** (total, equity, index), volume | **CSV download** + web page | none | Free, "for informational purposes only" | Y (page link) | EOD/daily | The canonical free put/call source. Equity & index P/C archives. [CBOE daily stats](https://www.cboe.com/us/options/market_statistics/daily/), [CBOE historical data](https://www.cboe.com/us/options/market_statistics/historical_data/), [index P/C archive CSV](https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/indexpcarchive.csv). |
| **OCC Volume & Open Interest** | 3 | Daily options volume, OI, put/call ratio, volume by exchange/account type | **XML / TXT download** | none | Free; last **30 trading days** of daily reports + historical archive | Y (page link) | EOD/daily | Authoritative cleared-volume data. [OCC daily volume](https://www.theocc.com/market-data/market-data-reports/volume-and-open-interest/daily-volume), [OCC volume download](https://www.theocc.com/webapps/trade-volume-download). |
| **Alpaca options (indicative feed)** | 3 | Option chain, IV, greeks (delta/gamma/theta/vega/rho) | REST JSON | free key | Free = indicative/delayed; OPRA realtime needs ATP ($99/mo) | N | delayed (indicative) | Already plumbed. History since Feb 2024 only (shallow). Per existing survey. |
| **yfinance options chains** ⚠️ | 3 | Full option chain per expiry (strikes, bid/ask, IV, volume, OI) via `Ticker.option_chain()` | **unofficial scrape** (Python) | none | No official limit; **breaks unpredictably** | partial | delayed | UNOFFICIAL. Free chains incl. IV; greeks must be computed operator-side. ToS = personal use; reliability risk. [yfinance options guide (Codearmo)](https://www.codearmo.com/python-tutorial/options-trading-getting-options-data-yahoo-finance), [Macroption yfinance options](https://www.macroption.com/yahoo-finance-options-python/). |
| **FlashAlpha (free tier)** | 3 | Pre-computed GEX/DEX/VEX, max pain, vol surface | REST JSON | free key | **5 req/day** (covers 6,000+ symbols) | N | delayed | Lowest-friction free pre-computed aggregates. Tier-4 sourced; re-verify. (Per existing survey, [FlashAlpha comparison](https://flashalpha.com/articles/best-options-data-apis-2026).) |

⭐ = top free win for put/call. **Free gap:** GEX/skew/vol-surface are only free pre-computed at FlashAlpha's 5/day; otherwise compute operator-side from Alpaca/yfinance chains.

---

## Lens 4 — Macro / Economic (rates, CPI, GDP, employment, spreads)

| Source | Lens(es) | What it provides | Access | Auth | Free quota (2026-06-10) | Click-through URL? | Freshness | ToS / caveat |
|---|---|---|---|---|---|---|---|---|
| **FRED (St. Louis Fed)** ⭐ | 4 | 840,000+ macro time series (CPI, GDP, unemployment, Fed funds, spreads, yields, money supply) from 114 sources | REST JSON | **free key** | **120 req/min** | Y (links to underlying Fed/BLS releases) | daily/as-released | Authoritative; already known. Per existing survey + [FRED API docs](https://fred.stlouisfed.org/docs/api/fred/). |
| **US Treasury Daily Interest Rate XML Feed** ⭐ | 4 | Daily par yield curve, real yield curve, bill rates, long-term rates | **XML feed** | **none** | No key, no documented limit | Y (Treasury page) | daily (EOD) | The yield-curve primary source (better than scraping). [Treasury XML feed](https://home.treasury.gov/treasury-daily-interest-rate-xml-feed), [XML files page](https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/interest-rate-xml-files). |
| **US Treasury FiscalData API** | 4 | Debt, daily Treasury statement, spending, auctions, exchange rates | REST JSON | **none** | **No key, no documented rate limit** | Y (dataset pages) | daily | Fully open. [FiscalData API docs](https://fiscaldata.treasury.gov/api-documentation/). |
| **BLS Public Data API v2** | 4 | CPI, PPI, unemployment, employment (CES), wages | REST JSON | **free key** | **Registered: 500 queries/day, up to 50 series + 20 yrs/req. Unregistered: 25/day** | Y (release links) | as-released | [BLS API features](https://www.bls.gov/bls/api_features.htm), [v2 signatures](https://www.bls.gov/developers/api_signature_v2.htm). |
| **BEA API** | 4 | GDP, personal income & outlays (PCE), trade, regional accounts | REST JSON | **free key** | Free key; **per-minute throttle** (error if exceeded in prior minute) | partial (release schedule links) | as-released | [BEA signup](https://apps.bea.gov/api/signup/), [BEA user guide PDF](https://apps.bea.gov/api/_pdf/bea_web_service_api_user_guide.pdf). |
| **EIA Open Data API** | 4 | Energy: oil/gas/electricity prices, inventories, production | REST JSON | **free key** | **~9,000/hr sustained, 5/sec burst** before throttle; max 5,000 rows/req | partial | daily/weekly | [EIA API docs](https://www.eia.gov/opendata/documentation.php), [EIA register](https://www.eia.gov/opendata/register.php). |
| **World Bank Indicators API** | 4 | ~16,000 global indicators (GDP, population, inflation by country) | REST JSON/XML | **none** | "API keys no longer necessary"; no documented hard limit | partial | annual/quarterly | [World Bank Indicators API](https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api-documentation). |
| **OECD SDMX API** | 4 | OECD member macro stats (CLI, trade, prices) | REST (SDMX; JSON/XML/CSV) | **none** | Free; **rate-limited** (introduced for traffic mgmt) | partial | as-released | [OECD API explainer](https://www.oecd.org/en/data/insights/data-explainers/2024/09/api.html), [OECD SDMX](https://sdmx.oecd.org/public). |
| **ECB Data Portal API** | 4 | Euro-area rates, FX reference rates, monetary/financial stats | REST (SDMX 2.1) | **none** | Free; limits not explicitly published | partial | as-released | EUR/USD ref rate + euro-area macro. [ECB API overview](https://data.ecb.europa.eu/help/api/overview). |
| **IMF** (corroborated) | 4 | IFS, balance of payments, WEO macro | REST (SDMX) | none | Free | partial | as-released | Surfaced as SDMX source in [sdmx1 sources](https://sdmx1.readthedocs.io/en/latest/sources.html); not separately deep-fetched — `[single-source, verify endpoint live]`. |

⭐ = top free macro wins. **FRED + Treasury XML + BLS + BEA cover essentially all US macro the advisor needs at $0.**

---

## Lens 5 — Fundamentals (statements, health, valuation, earnings)

| Source | Lens(es) | What it provides | Access | Auth | Free quota (2026-06-10) | Click-through URL? | Freshness | ToS / caveat |
|---|---|---|---|---|---|---|---|---|
| **SEC EDGAR `companyfacts` (XBRL JSON)** ⭐⭐ | 5,6 | **Every XBRL-tagged financial fact** (income statement, balance sheet, cash flow) across ALL filings for a CIK — standardized, as-reported | REST JSON (`data.sec.gov/api/xbrl/companyfacts/CIK##########.json`) | **none** (User-Agent header required) | **≤10 requests/sec** (fair-access; IP throttle if exceeded) | Y (facts link back to filings) | as-filed (quarterly/annual) | THE free replacement for a paid fundamentals provider. Standardized GAAP facts straight from filings. `[corroborated, not direct-fetched — sec.gov 403s the fetch tool]` [SEC EDGAR API guide (tldrfiling)](https://tldrfiling.com/blog/sec-edgar-api-guide/), [SEC rate-limit best practices](https://tldrfiling.com/blog/sec-edgar-api-rate-limits-best-practices), [SEC EDGAR APIs page](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) (page returns 403 to bots; content quoted via search). |
| **SEC EDGAR `companyconcept`** | 5,6 | One concept's full history (e.g., Revenues over time) for a CIK | REST JSON (`data.sec.gov/api/xbrl/companyconcept/CIK##########/us-gaap/Revenues.json`) | none (UA header) | ≤10 req/sec | Y | as-filed | More efficient than companyfacts when you need one line item. [SEC EDGAR API guide](https://tldrfiling.com/blog/sec-edgar-api-guide/). |
| **SEC EDGAR `frames`** | 5,6 | One concept across ALL companies for one period (cross-sectional) | REST JSON (`data.sec.gov/api/xbrl/frames/...`) | none (UA header) | ≤10 req/sec | Y | as-filed | Powers cross-sectional screening (e.g., everyone's Q revenue). [SEC EDGAR API guide](https://tldrfiling.com/blog/sec-edgar-api-guide/). |
| **Finnhub basic-financials** | 5 | Margins, ratios, per-share metrics, earnings surprises | REST JSON | free key | 60 calls/min | N | EOD | Free raw financials + estimates fallback. [Finnhub pricing](https://finnhub.io/pricing-stock-api-market-data). |
| **FMP (free tier)** | 5 | Statements, profile, 150+ endpoints, EOD history | REST JSON | free key | **250 calls/day** | partial | EOD | Free tier is generous for low-volume; health-score product is paid. [FMP pricing plans](https://site.financialmodelingprep.com/pricing-plans), [FMP free signup](https://site.financialmodelingprep.com/how-to/how-to-sign-up-and-use-a-free-stock-market-data-api). |
| **Tiingo fundamentals (free)** | 5 | Statements + metrics (subject to free-tier symbol/req caps) | REST JSON | free key | 1,000 req/day, 500 symbols/mo (shared with Lens-1 tier) | partial | EOD | Same free-tier envelope as Lens 1. [Tiingo pricing](https://www.tiingo.com/about/pricing). |

⭐⭐ = the single biggest free win — see "Biggest free wins" below.

---

## Lens 6 — SEC filings / corporate events (10-K/10-Q/8-K, Form 4 insider, 13F institutional)

| Source | Lens(es) | What it provides | Access | Auth | Free quota (2026-06-10) | Click-through URL? | Freshness | ToS / caveat |
|---|---|---|---|---|---|---|---|---|
| **SEC EDGAR `submissions` API** ⭐ | 6 | Full filing history per CIK (form type, date, accession #, primary-doc URL) — 10-K, 10-Q, 8-K, Form 3/4/5, 13F, S-1, DEF 14A | REST JSON (`data.sec.gov/submissions/CIK##########.json`) | none (UA header) | ≤10 req/sec | **Y — builds filing-document URLs** | realtime (as filed) | The core filing index. [SEC EDGAR API guide](https://tldrfiling.com/blog/sec-edgar-api-guide/). |
| **SEC EDGAR Full-Text Search API (`efts.sec.gov`)** ⭐ | 6 | Keyword/phrase search across full filing bodies (2001–present); filter by form, CIK, date, SIC, location | REST JSON (`efts.sec.gov/LATEST/search-index?q=...`) | **none** | Free, no auth (fair-access ≤10/sec applies) | **Y — accession # → filing URL** | realtime | Powers event/keyword discovery. [SEC full-text search API guide](https://tldrfiling.com/blog/sec-edgar-full-text-search-api), [SEC EDGAR FTS](https://www.sec.gov/edgar/search/). |
| **SEC EDGAR company filing RSS/Atom feed** ⭐ | 6 | Per-company latest filings as a subscribable feed; filter by form type (incl. Forms 3/4/5 ownership) | **RSS/Atom** (`...cgi-bin/browse-edgar?action=getcompany&CIK=<cik>&type=<form>&output=atom`) | **none** | Free | **Y — feed entries link to filings** | realtime | Cleanest way to poll a watchlist for new 8-K/Form-4/13F. [SEC RSS feeds](https://www.sec.gov/about/rss-feeds) (page 403s the fetcher; URL pattern corroborated via search + [edgar RSS parser](https://github.com/aj0strow/edgar)). |
| **SEC EDGAR "latest filings" / full-index feeds** | 6 | Firehose of all recent filings, or daily/quarterly full-index files | RSS + index files | none | Free | Y | realtime/daily | For broad discovery rather than a watchlist. [SEC RSS feeds](https://www.sec.gov/about/rss-feeds). |
| **SEC Structured Disclosure RSS** | 6 | Structured (XBRL/financial-statement) datasets feed | RSS | none | Free | Y | as-released | [SEC structured-data RSS](https://www.sec.gov/structureddata/rss-feeds) (corroborated via search). |

⭐ = top free wins for filings + insider/institutional events.

**Insider (Form 4) + institutional (13F):** both are ordinary EDGAR form types — reachable for free via the `submissions` API, the company RSS/Atom feed (filter `type=4` or `type=13F`), and full-text search. No paid insider-data vendor is required for the raw signal; only pre-aggregated/cleaned insider dashboards are paid.

---

## Lens 7 — Symphony/strategy & ticker discovery (universe screening signals)

| Source | Lens(es) | What it provides | Access | Auth | Free quota (2026-06-10) | Click-through URL? | Freshness | ToS / caveat |
|---|---|---|---|---|---|---|---|---|
| **SEC EDGAR `frames` (cross-sectional XBRL)** | 7,5 | One financial concept across all companies for one period → fundamental screening universe | REST JSON | none (UA) | ≤10/sec | Y | as-filed | Build deterioration/valuation screens for free. [SEC EDGAR API guide](https://tldrfiling.com/blog/sec-edgar-api-guide/). |
| **SEC full-text search** | 7,6 | Discover companies by keyword/event language (e.g., "going concern", "restatement") | REST JSON | none | free | Y | realtime | Event-driven candidate discovery. [SEC FTS API guide](https://tldrfiling.com/blog/sec-edgar-full-text-search-api). |
| **GDELT DOC API** | 7,2 | Discover tickers/companies trending in global news + tone | REST JSON | none | no key | Y | ~15 min | Cross-references news attention to a watchlist. [GDELT DOC 2.0](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/). |
| **Reddit / StockTwits cashtag streams** | 7,2 | Social mention spikes per ticker → momentum/attention discovery | REST JSON | OAuth / none | Reddit ~60–100/min; StockTwits ~200/hr | Y | realtime | Crowd-attention discovery. [Reddit limits](https://painonsocial.com/blog/reddit-api-rate-limits-guide), [StockTwits stream](https://github.com/janlukasschroeder/stocktwits-api). |
| **Wikipedia pageviews** | 7,2 | Attention spikes on a company's Wikipedia page | REST JSON | none (UA) | courteous | Y | daily | Lagging but clean attention proxy. [Pageview API](https://en.wikiversity.org/wiki/MediaWiki_API/Pageview_API). |
| **FMP / Finnhub / Twelve Data screeners** | 7,5 | Built-in stock screeners (free-tier-limited) | REST JSON | free key | per provider quota above | N | EOD | Convenience screeners within the free quotas already listed. [FMP](https://site.financialmodelingprep.com/pricing-plans), [Finnhub](https://finnhub.io/pricing-stock-api-market-data). |

---

## Section 1 — Biggest free wins we are NOT yet capturing (ranked by value)

1. **SEC EDGAR `companyfacts` / `companyconcept` / `frames` (XBRL JSON) — FREE standardized fundamentals.** ⭐⭐ This is the headline. It returns every XBRL-tagged financial fact (income statement, balance sheet, cash flow) per company, standardized GAAP, straight from filings, with filing links — at ≤10 req/sec, no key, no cost. It can **replace the paid FMP Starter ($22/mo)** for the fundamentals lens. The only thing it doesn't ship is a *pre-computed* health score (Altman-Z/Piotroski) — but those are deterministic formulas the operator can compute from the free facts. [SEC EDGAR API guide](https://tldrfiling.com/blog/sec-edgar-api-guide/), [SEC rate limits](https://tldrfiling.com/blog/sec-edgar-api-rate-limits-best-practices).

2. **GDELT 2.0 DOC API — FREE global news + tone + article URLs.** ⭐ No key, ~15-min freshness, tone scores, and a clickable `url`+`seendate` per article. Massively broadens the news/sentiment lens beyond Alpaca's Benzinga feed and satisfies the click-through requirement. [GDELT DOC 2.0](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/).

3. **SEC EDGAR filing RSS/Atom + Full-Text Search + `submissions` — FREE filings/insider/13F events.** ⭐ A watchlist-driven RSS layer (poll `action=getcompany&type=4`/`8-K`/`13F`) gives real-time insider + institutional + material-event signals with filing click-through, for $0. The existing survey had **no Lens-6 coverage at all**. [SEC RSS](https://www.sec.gov/about/rss-feeds), [SEC FTS API](https://tldrfiling.com/blog/sec-edgar-full-text-search-api).

4. **Macro firehose: FRED + Treasury XML + BLS + BEA + (World Bank/OECD/ECB/EIA).** FRED was already known, but the **Treasury Daily Interest Rate XML feed** (key-less yield curve) and **BLS/BEA APIs** (release-linked CPI/GDP/employment) close the macro lens entirely at $0. [Treasury XML](https://home.treasury.gov/treasury-daily-interest-rate-xml-feed), [BLS API](https://www.bls.gov/bls/api_features.htm), [BEA signup](https://apps.bea.gov/api/signup/).

5. **CBOE + OCC free options statistics — FREE put/call ratio + cleared volume.** Replaces any paid put/call feed; daily CSV/XML, no key. [CBOE daily stats](https://www.cboe.com/us/options/market_statistics/daily/), [OCC volume](https://www.theocc.com/market-data/market-data-reports/volume-and-open-interest/daily-volume).

6. **Finnhub free (60/min) as a multi-lens glue layer** — company news with URLs, basic financials, earnings, some macro — the single most generous free per-minute commercial quota for filling gaps. [Finnhub pricing](https://finnhub.io/pricing-stock-api-market-data).

---

## Section 2 — Free-only stack that maximizes coverage (replaces paid recs)

Smallest set of free sources covering all 7 lenses with click-through where it matters:

| Free source | Lenses covered | Replaces (from DATA-SOURCES.md) |
|---|---|---|
| **Alpaca Basic (already integrated)** | 1 (price/bars, IEX delayed), 2 (news w/ URL) | Keeps the existing plumbing; defers Alpaca ATP $99/mo unless realtime SIP is required |
| **SEC EDGAR companyfacts/concept/frames** | 5 (fundamentals), 7 (screening) | **Replaces FMP Starter ($22/mo)** for fundamentals — free standardized statements |
| **SEC EDGAR submissions + RSS/Atom + Full-Text Search** | 6 (filings, Form 4 insider, 13F) | Fills a lens the paid survey **never covered** ($0) |
| **GDELT 2.0 DOC API** | 2 (news + tone + URL), 7 (discovery) | **Replaces Marketaux** as the primary broad news+tone source (Marketaux free is only 100/day); GDELT is key-less + ~15-min |
| **FRED + Treasury XML + BLS + BEA** | 4 (macro) | Confirms FRED-primary; **adds key-less Treasury yield curve** — no paid macro needed |
| **CBOE + OCC free stats** | 3 (put/call, volume) | **Replaces any paid put/call feed**; for IV/greeks, use Alpaca indicative (free) or yfinance chains (unofficial) |
| **Finnhub free (60/min)** | 2,4,5 glue | Free fallback for company-news URLs, basic financials, earnings — **replaces Finnhub Premium ($50–100/mo)** for low volume |
| *(optional)* **Reddit + StockTwits + Wikipedia pageviews** | 2,7 social/attention | Free social-sentiment layer (no paid social-sentiment vendor) |

**Net effect:** This free-only stack covers all 7 lenses at **$0/mo**, versus the paid survey's **~$121/mo** recommended minimal stack (Alpaca ATP $99 + FMP Starter $22 + FRED $0). The two things you trade away vs. paid:
- **Realtime full-SIP bars** (Alpaca ATP $99/mo) — free stack is IEX-delayed / EOD. Add ATP only if intraday realtime is a hard requirement.
- **Pre-computed health scores & options aggregates** (FMP health score, ORATS/FlashAlpha GEX) — compute operator-side from free EDGAR facts + free option chains.

**Explicit replacements:**
- SEC EDGAR `companyfacts` **replaces FMP Starter $22/mo** (fundamentals).
- GDELT DOC API **replaces Marketaux** as primary news+tone (and its 100/day free cap).
- CBOE/OCC free stats **replace** any paid put/call ratio feed.
- Finnhub free **replaces Finnhub Premium $50–100/mo** at low volume.
- Treasury XML + BLS + BEA **augment FRED** so no paid macro provider is ever needed.

---

## Section 3 — Citation / click-through coverage (the hard requirement)

The operator must be able to **open the source**. Free sources that return a clickable article/filing/release URL + timestamp:

| Source | Returns clickable URL? | Timestamp field | Notes |
|---|---|---|---|
| **GDELT DOC API** | ✅ Y | `seendate` | `url` per article; opens the real news page |
| **SEC EDGAR (submissions / FTS / RSS / companyfacts)** | ✅ Y | filing date / `updated` | Accession # → filing document URL; RSS entries link to filings |
| **Alpaca News** | ✅ Y | `created_at` | `url` → benzinga.com |
| **Marketaux** | ✅ Y | `published_at` | `url` + `source` |
| **Finnhub company-news** | ✅ Y | `datetime` | `url` field |
| **FRED** | ✅ Y | release date | Links to underlying Fed/BLS release pages |
| **Treasury XML / FiscalData** | ✅ Y | release date | Links to Treasury data pages |
| **BLS / BEA** | ✅ Y | release date | Links to news-release pages |
| **CBOE / OCC** | ✅ Y (page-level) | report date | Links to the daily stats page / report file |
| **Reddit / StockTwits / Hacker News / Wikipedia** | ✅ Y | post/edit time | Permalinks to thread/message/article |
| Aggregate sentiment scores (Finnhub `news-sentiment`, GDELT tone-only) | ❌ N (number only) | n/a | Supplementary signal; cannot satisfy click-through alone |

**Confirmed:** GDELT, SEC EDGAR (filing URLs), FRED (release links), and news RSS all qualify for click-through, as expected.

---

## Section 4 — Legal / ToS / reliability caveats

This is **advisory / personal-use, single-operator** context — that materially lowers (but does not eliminate) ToS risk. Per-source flags:

- **Unofficial / scraped — reliability + ToS risk (use with fallbacks, do not redistribute):**
  - **yfinance / Yahoo Finance** — uses Yahoo's front-end endpoints; library is "not affiliated, endorsed, or vetted by Yahoo"; Yahoo ToS = *personal use only*. Endpoints break + IP-block without notice. [yfinance GitHub](https://github.com/ranaroussi/yfinance), [Yahoo ToS](https://legal.yahoo.com/us/en/yahoo/terms/product-atos/apiforydn/index.html). Keep a free *official* fallback (Stooq/Alpaca/Tiingo) for any path that depends on it.
  - **Google Trends (pytrends)** — not a supported API; aggressive 429s, often needs proxies; brittle. [pytrends 429](https://github.com/GeneralMills/pytrends/issues/492).
  - **Stooq** — no official API; CSV/web only; courtesy use, no redistribution. [Stooq DB](https://stooq.com/db/).

- **Free official APIs with usage policies (low risk for personal use):**
  - **SEC EDGAR** — fair-access ≤10 req/sec; **mandatory User-Agent header** (`CompanyName email@domain`) — missing UA is the #1 cause of 403s. [SEC rate-limit best practices](https://tldrfiling.com/blog/sec-edgar-api-rate-limits-best-practices). (Note: SEC also 403s generic bot fetchers like this research tool — your client must send a proper UA.)
  - **Wikimedia/Wikipedia** — mandatory User-Agent policy; "no fixed limit" but may block if you endanger stability. [WMF UA policy](https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy).
  - **Reddit Data API** — post-2023 commercial-API regime; free tier OK for non-commercial; commercial use / redistribution gated. [Reddit Data API wiki](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki).
  - **CBOE / OCC** — "for informational purposes only"; free for personal analysis, not for redistribution as a data product. [CBOE daily stats](https://www.cboe.com/us/options/market_statistics/daily/).
  - **GDELT** — "100% free and open." Lowest-friction. [GDELT data](https://www.gdeltproject.org/data.html).

- **Free-tier commercial APIs (ToS = abide by the free-tier limits; no redistribution):** Alpha Vantage, Tiingo, Twelve Data, Finnhub, FMP, Marketaux, EODHD, Nasdaq Data Link — all free tiers are fine for single-operator advisory use; do not redistribute or exceed quotas. Pricing/limit pages are JS-rendered/403 to fetchers — verify live.

- **Single-operator framing:** personal, non-commercial research is the lowest-risk usage class across all of the above. The one structural risk is **reliability**, not legality: unofficial endpoints (yfinance, pytrends, Stooq) WILL break — never make a live execution path depend on them without a free official fallback.

---

## Section 5 — Staleness (every quota date-stamped 2026-06-10)

| Item | Stamped quota | Confidence | Action |
|---|---|---|---|
| SEC EDGAR ≤10 req/sec + UA header | 2026-06-10 | High (SEC's own wording, via search; page 403s direct fetch) | Verify UA format works against a live request |
| GDELT DOC API — no key, ~15-min, JSONFeed | 2026-06-10 | High | Stable; re-confirm output mode at build |
| FRED 120 req/min | 2026-06-10 (existing survey) | High | Low risk |
| Treasury XML — no key, no documented limit | 2026-06-10 | High | Confirm XML schema (Treasury issued a developer XML-change notice — check current format) |
| FiscalData — no key, no documented limit | 2026-06-10 | High | Low risk |
| BLS 500/day registered (25 unregistered), 50 series/req | 2026-06-10 | High | Re-confirm at [BLS API features](https://www.bls.gov/bls/api_features.htm) |
| BEA free key, per-minute throttle | 2026-06-10 | Medium (throttle is dynamic) | Monitor 429s |
| EIA ~9,000/hr, 5/sec burst, 5,000 rows/req | 2026-06-10 | Medium | [EIA docs](https://www.eia.gov/opendata/documentation.php) |
| World Bank — no key, no hard limit | 2026-06-10 | High | Low risk |
| OECD — free, rate-limited (number unpublished) | 2026-06-10 | Medium — **unverified numeric limit** | Test live |
| ECB — free, limits unpublished | 2026-06-10 | Medium — **unverified numeric limit** | Test live |
| IMF SDMX — free | 2026-06-10 | **Low / single-source** — endpoint not deep-fetched | Verify endpoint live |
| Alpha Vantage 25/day, 5/min | 2026-06-10 | Medium (Tier-3/4 corroboration) | [Macroption](https://www.macroption.com/alpha-vantage-api-limits/) — verify live |
| Tiingo 1,000/day, 50/hr, 500 sym/mo, 1 GB/mo | 2026-06-10 | Medium | [Tiingo pricing](https://www.tiingo.com/about/pricing) — verify |
| Twelve Data 8/min, 800/day | 2026-06-10 | Medium | [Twelve Data trial](https://support.twelvedata.com/en/articles/5335783-trial) |
| Finnhub 60/min | 2026-06-10 | Medium-High | [Finnhub pricing](https://finnhub.io/pricing-stock-api-market-data) |
| FMP 250/day free | 2026-06-10 | Medium | [FMP pricing](https://site.financialmodelingprep.com/pricing-plans) — JS-rendered, verify |
| Marketaux 100/day | 2026-06-10 | Medium-High | [Marketaux pricing](https://www.marketaux.com/pricing) |
| EODHD 20/day, 1-yr depth | 2026-06-10 | Medium | [EODHD pricing](https://eodhd.com/pricing) |
| Polygon/Massive free 5/min | 2026-06-10 (existing survey + search) | Medium — **domain rebranded polygon.io→massive.com Oct 2025** | Re-verify current domain/limits |
| Nasdaq Data Link free datasets | 2026-06-10 | **Low — free footprint shrinking; per-dataset** | Verify the specific dataset is free |
| Reddit ~60–100/min OAuth, 10/min unauth | 2026-06-10 | Medium (Tier-3, two figures cited) | Test on a registered client |
| StockTwits ~200/hr unauth | 2026-06-10 | Medium (Tier-3) | Test live |
| CBOE / OCC free CSV/XML | 2026-06-10 | High | Low risk |
| Hacker News — no limit, no auth | 2026-06-10 | High | Low risk |
| Wikipedia pageviews — UA required, no fixed limit | 2026-06-10 | High | Low risk |
| yfinance / pytrends / Stooq | 2026-06-10 | **Unofficial — breakage, not a quota** | Treat as best-effort with official fallback |

**Unverified items explicitly flagged:** OECD/ECB numeric rate limits, IMF SDMX endpoint, Nasdaq Data Link per-dataset free status — all marked "needs live check."

---

## Sources (all fetched/searched this session, 2026-06-10)

**Government / open-data (Tier 1):**
- SEC EDGAR APIs — [tldrfiling guide](https://tldrfiling.com/blog/sec-edgar-api-guide/), [rate limits](https://tldrfiling.com/blog/sec-edgar-api-rate-limits-best-practices), [full-text search API](https://tldrfiling.com/blog/sec-edgar-full-text-search-api), [SEC EDGAR APIs page](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) (403 to bots; quoted via search), [SEC RSS feeds](https://www.sec.gov/about/rss-feeds), [SEC FTS](https://www.sec.gov/edgar/search/), [SEC structured-data RSS](https://www.sec.gov/structureddata/rss-feeds)
- [FRED API docs](https://fred.stlouisfed.org/docs/api/fred/)
- [Treasury daily-rate XML feed](https://home.treasury.gov/treasury-daily-interest-rate-xml-feed), [Treasury XML files](https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/interest-rate-xml-files), [FiscalData API](https://fiscaldata.treasury.gov/api-documentation/)
- [BLS API features](https://www.bls.gov/bls/api_features.htm), [BLS v2 signatures](https://www.bls.gov/developers/api_signature_v2.htm)
- [BEA signup](https://apps.bea.gov/api/signup/), [BEA user guide](https://apps.bea.gov/api/_pdf/bea_web_service_api_user_guide.pdf)
- [EIA API docs](https://www.eia.gov/opendata/documentation.php), [EIA register](https://www.eia.gov/opendata/register.php)
- [World Bank Indicators API](https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api-documentation)
- [OECD API explainer](https://www.oecd.org/en/data/insights/data-explainers/2024/09/api.html), [OECD SDMX](https://sdmx.oecd.org/public)
- [ECB Data Portal API](https://data.ecb.europa.eu/help/api/overview)
- [CBOE daily stats](https://www.cboe.com/us/options/market_statistics/daily/), [CBOE historical data](https://www.cboe.com/us/options/market_statistics/historical_data/), [index P/C archive CSV](https://cdn.cboe.com/resources/options/volume_and_call_put_ratios/indexpcarchive.csv)
- [OCC daily volume](https://www.theocc.com/market-data/market-data-reports/volume-and-open-interest/daily-volume), [OCC volume download](https://www.theocc.com/webapps/trade-volume-download)

**News / sentiment / social (Tier 1–3):**
- [GDELT DOC 2.0 debut](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/), [JSONFeed support](https://blog.gdeltproject.org/gdelt-doc-2-0-api-supports-jsonfeed/), [GDELT data page](https://www.gdeltproject.org/data.html), [Python client](https://github.com/alex9smith/gdelt-doc-api)
- [Marketaux pricing](https://www.marketaux.com/pricing), [freeapihub Marketaux](https://freeapihub.com/apis/marketaux)
- [Reddit API rate limits 2026](https://painonsocial.com/blog/reddit-api-rate-limits-guide), [Reddit Data API wiki](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki)
- [StockTwits symbol stream](https://github.com/janlukasschroeder/stocktwits-api), [AlgoFin StockTwits](https://algofin.substack.com/p/using-stocktwits-to-understand-investor)
- [Hacker News API](https://github.com/hackernews/api), [HN API guide](https://cotera.co/articles/hacker-news-api-guide)
- [pytrends 429 issue](https://github.com/GeneralMills/pytrends/issues/492)
- [Wikimedia REST API](https://www.mediawiki.org/wiki/Wikimedia_REST_API), [Pageview API](https://en.wikiversity.org/wiki/MediaWiki_API/Pageview_API), [WMF UA policy](https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy)

**Free-tier commercial APIs (Tier 1–4):**
- [Alpha Vantage limits (Macroption)](https://www.macroption.com/alpha-vantage-api-limits/), [AlphaLog 2026 guide](https://alphalog.ai/blog/alphavantage-api-complete-guide)
- [Tiingo pricing](https://www.tiingo.com/about/pricing), [Tiingo IEX API](https://www.tiingo.com/products/iex-api)
- [Twelve Data trial](https://support.twelvedata.com/en/articles/5335783-trial), [MEXC 2026 review](https://www.mexc.com/news/476023)
- [Finnhub pricing](https://finnhub.io/pricing-stock-api-market-data)
- [FMP pricing plans](https://site.financialmodelingprep.com/pricing-plans), [FMP free signup](https://site.financialmodelingprep.com/how-to/how-to-sign-up-and-use-a-free-stock-market-data-api)
- [EODHD pricing](https://eodhd.com/pricing), [EODHD limits](https://eodhd.com/financial-apis/api-limits)
- [Nasdaq Data Link docs](https://docs.data.nasdaq.com/), [getting started](https://docs.data.nasdaq.com/docs/getting-started)
- [Polygon/Massive request-limit KB](https://polygon.io/knowledge-base/article/what-is-the-request-limit-for-polygons-restful-apis)

**Price fallbacks / unofficial:**
- [yfinance GitHub](https://github.com/ranaroussi/yfinance), [Yahoo API ToS](https://legal.yahoo.com/us/en/yahoo/terms/product-atos/apiforydn/index.html), [yfinance options (Codearmo)](https://www.codearmo.com/python-tutorial/options-trading-getting-options-data-yahoo-finance), [Macroption yfinance options](https://www.macroption.com/yahoo-finance-options-python/)
- [Stooq DB](https://stooq.com/db/), [QuantStart Stooq](https://www.quantstart.com/articles/an-introduction-to-stooq-pricing-data/)

---

## Open questions / what remains unverified

- **OECD / ECB numeric rate limits** — both free, both rate-limited, but the exact numbers weren't published in fetched docs. `[needs live check]`
- **IMF SDMX** — surfaced as a free SDMX source but not deep-fetched; endpoint + format unverified. `[single-source]`
- **Nasdaq Data Link** — which specific datasets remain free in 2026 is per-dataset and shrinking; verify the exact series before relying. `[needs live check]`
- **SEC pages 403 the WebFetch tool** — all SEC limits/headers are corroborated via the SEC's own quoted text in search + Tier-3/4 guides, not a direct fetch. The ≤10 req/sec and mandatory UA are consistent across multiple sources (High confidence) but flagged for transparency.
- **Pre-computed financial-health scores (Altman-Z/Piotroski)** — no *free* source ships these as a field; they must be computed operator-side from free EDGAR `companyfacts`. (Interpretation, labeled: this is a derivation cost, not a data-acquisition cost.)
- **Realtime full-SIP equity bars** — no free source provides them; the free stack is IEX-delayed/EOD. Only Alpaca ATP ($99/mo) or equivalent paid feed closes this. Surfaced as a trade-off, not a recommendation.
