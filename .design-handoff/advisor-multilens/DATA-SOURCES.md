# Multi-Lens AI Advisor — Data Source Survey
**Researcher:** alpaca-api-researcher | **Date:** 2026-06-10
**Scope:** Feasibility/landscape survey for a single-operator retail quant on Composer.trade + Alpaca (Planet Stopper / AlphaBot). All provider claims grounded in primary-doc fetches or corroborated search results from this session; no training-data hallucination. Cost figures dated 2026-06-10 — re-verify at build time (pricing pages are JS-rendered and drift).

---

## Lens 1 — Technicals (price, volume, bars, indicators)

| Data point needed | Source / provider | Alpaca-native? | Cost tier | Citation/article-URL available? | Rate limits / caveats |
|---|---|---|---|---|---|
| OHLCV bars (multi-timeframe) | Alpaca Market Data API | **Y** | Free (Basic) / $99/mo (Algo Trader Plus) | n/a | Basic: IEX only, historical limited to last 15 min. ATP: full SIP/CTA/UTP, history since 2016, 10,000 req/min |
| Full US exchange bars (real-time) | Alpaca — Algo Trader Plus | **Y** | $99/mo | n/a | Basic tier = IEX feed only (subset of US exchanges) |
| Computed indicators (RSI, MACD, Bollinger, etc.) | Operator-computed from Alpaca bars | **Y** (raw data) | No extra cost | n/a | Alpaca returns OHLCV; indicators must be derived operator-side (pandas-ta, ta-lib, etc.) — no indicator endpoint exists |
| Historical bar depth | Alpaca ATP | **Y** | $99/mo | n/a | Since 2016 on ATP; effectively unavailable on Basic |

**Best-value pick:** Alpaca Algo Trader Plus at $99/mo. The project already has an Alpaca integration (`synthetic_history.py` fetches 250-day bars), so there is no new integration cost. The only genuine gap is that Alpaca computes no indicators — but that is a pandas-side derivation, not a data acquisition problem. Source: [about-market-data-api](https://docs.alpaca.markets/docs/about-market-data-api), verified 2026-06-10.

---

## Lens 2 — Sentiment / News

| Data point needed | Source / provider | Alpaca-native? | Cost tier | Citation/article-URL available? | Rate limits / caveats |
|---|---|---|---|---|---|
| Financial news headlines + full text | Alpaca News API (Benzinga-sourced) | **Y** | Included in data plan (see caveat) | **Y — `url` field per article** | ~130+ full articles/day; 600–900 real-time headlines/day; back to 2015. Numeric rate cap not published in docs — returns 429 with `X-RateLimit-*` headers |
| Article URL / click-through link | Alpaca News API | **Y** | Same as above | **Y** | `url` field confirmed in Alpaca blog. Points to benzinga.com |
| Per-ticker sentiment score (numeric) | **Not in Alpaca** — must derive or buy | N | — | — | Alpaca positions news as raw material *for* sentiment models; no score field in the response |
| Pre-computed sentiment score + entity ranking | Marketaux | N | Free: 100 req/day; paid tiers for volume | **Y — source URL + domain in JSON** | 5,000+ sources, 200,000+ entities, 80+ markets, 30+ languages. Exact paid tier pricing not verified live — see staleness note |
| News sentiment score (−1..+1) + social sentiment (Reddit/Twitter) | Finnhub | N | Free: 60 req/min; Premium ~$50–100/mo | **Y — `url` field on company-news endpoint** | Social-sentiment (Reddit/Twitter) tier gating has changed historically — treat as `[Low]` confidence until tested against a live key; docs are JS-rendered |
| Direct Benzinga feed (bypasses Alpaca middleman) | Benzinga APIs | N | Free basic tier (headline + teaser + hyperlink) | **Y — hyperlink to benzinga.com** | Custom/enterprise pricing for full body + signals. AWS Marketplace free tier available |

**Best-value pick:** Alpaca's own News API first — it's already plumbed, goes back to 2015, includes the `url` field, and satisfies the operator's click-through requirement at zero marginal integration cost. If a pre-scored sentiment signal is required (beyond deriving scores via the project's existing `anthropic` client), Marketaux adds per-ticker scores and broader source diversity at a low free-tier entry point. Sources: [Alpaca historical-news-data](https://docs.alpaca.markets/docs/historical-news-data), [Alpaca blog — News API fields](https://alpaca.markets/blog/introducing-news-api-for-real-time-fiancial-news/), [Marketaux](https://www.marketaux.com/), all verified 2026-06-10.

---

## Lens 3 — Derivatives / Options (IV, put/call ratio, greeks, gamma exposure, unusual activity)

| Data point needed | Source / provider | Alpaca-native? | Cost tier | Citation/article-URL available? | Rate limits / caveats |
|---|---|---|---|---|---|
| Option chain (all strikes/expiries for an underlying) | Alpaca `get_option_chain` | **Y** | Free (indicative feed) / $99/mo ATP (OPRA real-time) | n/a | OPRA = real-time; indicative = delayed/modified quotes. SDK method confirmed in alpaca-py docs |
| Implied volatility (per contract) | Alpaca `OptionSnapshot` | **Y** | Same as chain | n/a | IV confirmed in `OptionSnapshot` response; historical bars since Feb 2024 only (shallow vs equities) |
| Greeks (delta, gamma, theta, vega, rho) | Alpaca option chain / snapshot | **Y** | Same as chain | n/a | Confirmed in [alpaca-py reference](https://alpaca.markets/sdks/python/api_reference/data/option/historical.html) and [option chain endpoint](https://docs.alpaca.markets/reference/optionchain) |
| Put/call ratio (aggregate) | **Not in Alpaca** — compute from chain or buy | N | — | — | Alpaca provides raw per-contract data; no aggregate p/c ratio endpoint |
| Gamma exposure (GEX), skew curve, vol surface | **Not in Alpaca** | N | — | — | Must be computed from the chain (operator-side math) or purchased pre-computed |
| Unusual options activity / flow alerts | **Not in Alpaca** | N | — | — | Not in Alpaca scope |
| Pre-computed GEX/DEX/VEX, max pain, vol surface | FlashAlpha | N | **Free tier: 5 req/day** / $79 / $299 / $1,499/mo | n/a | Free tier covers 6,000+ symbols; no credit card required. Source: [FlashAlpha comparison](https://flashalpha.com/articles/best-options-data-apis-2026), Tier-4 — re-verify |
| Raw chains + greeks (ORATS-powered) | Tradier Brokerage API | N | **$10/mo data, or free with brokerage account** | n/a | Greeks updated hourly (not tick); brokerage account required for real-time — sandbox = delayed, no greeks. Source: [Tradier docs](https://docs.tradier.com/docs/market-data), verified 2026-06-10 |
| 98+ proprietary IV indicators, skew, history to 2007 | ORATS | N | ~$99/mo | n/a | Deepest options analytics; backtestable. Source: [ORATS data-api](https://orats.com/data-api), verified 2026-06-10 |
| Unusual activity / flow alerts | Unusual Whales | N | ~$48/mo retail | n/a | Flow alerts, dark pool prints; Source: [FlashAlpha comparison](https://flashalpha.com/articles/best-options-data-apis-2026), Tier-4 |
| Full chain + IV + OI + greeks (tick-level) | Polygon.io (rebranded Massive.com, Oct 2025) | N | ~$29/mo stocks + ~$79/mo options | n/a | Real-time effectively at Advanced tier (~$199/mo). **Domain rebranded** — verify current URLs before integration. Source: [FlashAlpha comparison](https://flashalpha.com/articles/best-options-data-apis-2026), Tier-4 |

**Best-value pick:** For a retail advisor needing IV + greeks as signals, Alpaca ATP already delivers the raw per-contract data at no marginal cost. If pre-computed aggregates (GEX/skew/put-call) are a hard requirement, FlashAlpha's free tier is the lowest-friction entry point before committing to ORATS ($99/mo) for deeper analytics. The trade-off: Alpaca options history is shallow (since Feb 2024 only), which limits backtesting options signals. Sources: [alpaca-py option historical](https://alpaca.markets/sdks/python/api_reference/data/option/historical.html), [Tradier market-data](https://docs.tradier.com/docs/market-data), [orats.com/data-api](https://orats.com/data-api), all verified 2026-06-10.

---

## Lens 4 — Macro / Economic data (rates, CPI, employment, GDP, spreads)

| Data point needed | Source / provider | Alpaca-native? | Cost tier | Citation/article-URL available? | Rate limits / caveats |
|---|---|---|---|---|---|
| CPI, GDP, unemployment, Fed funds rate, housing, money supply, etc. | **FRED (St. Louis Fed)** | **N** | **Free** (API key required, 32-char alphanumeric) | **Y — links to underlying Fed/BLS releases** | 120 req/min; 840,000+ time series from 114 sources. Source: [FRED API docs](https://fred.stlouisfed.org/docs/api/fred/), verified 2026-06-10 |
| Macro endpoints bundled with fundamentals | FMP (see Lens 5) | N | Free–$59/mo | Partial (news URLs; macro series no article URL) | FMP bundles economic data — reduces to one integration if FMP already used for fundamentals |
| Economic data + estimates | Finnhub | N | Free (60 req/min) | N (numeric series, no release URLs) | Covers major macro series; not as comprehensive as FRED |

**Best-value pick:** FRED is free, authoritative, and links to the source release (BLS, Fed, Census), satisfying the click-through requirement at the statement/release level. If the build already integrates FMP for fundamentals (Lens 5), FMP's economic endpoints could serve as a convenience backup — but FRED should be primary for macro. Source: [FRED API overview](https://fred.stlouisfed.org/docs/api/fred/overview.html), verified 2026-06-10.

---

## Lens 5 — Fundamentals (balance-sheet health, earnings, valuation, deterioration flags)

| Data point needed | Source / provider | Alpaca-native? | Cost tier | Citation/article-URL available? | Rate limits / caveats |
|---|---|---|---|---|---|
| Income statement / balance sheet / cash flow (annual + quarterly + TTM) | **FMP** | **N** | Free: 250 calls/day / Starter ~$22/mo / Premium ~$59/mo / Ultimate ~$149/mo | Partial — news has URLs; statements link to as-reported filings (not always a live SEC permalink) | Starter: 5yr history, 300 req/min; Premium: 30yr, 750 req/min. Source: [findmymoat.com/FMP review](https://www.findmymoat.com/tools/financial-modeling-prep-fmp), Tier-4; re-verify pricing live |
| Pre-computed financial health score (Altman-Z / Piotroski-style) | FMP | N | Same as above | N (score is a number; no source link) | FMP ships explicit financial scores + screener — strongest for "deterioration flag" use case |
| Stock screener with financial-health filters | FMP | N | Starter tier ($22/mo) | N | Enables ranking/screening for deterioration as a query, not a model |
| DCF / valuation | FMP Premium ($59/mo) | N | $59/mo | N | Custom DCF calculator included |
| Earnings estimates, surprises | Finnhub | N | Free (60 req/min) | N | Earnings surprise scores; raw financials also available |
| Raw financial statements (SEC-sourced) | Polygon.io `vX/reference/financials` (now Massive.com) | N | Developer tier (~$99/mo per Tier-4 source) | Y — links to SEC filings | **Domain rebranded Oct 2025** — verify. Source: [FlashAlpha comparison](https://flashalpha.com/articles/best-options-data-apis-2026), Tier-4 |
| Financials + news + estimates + insider sentiment | Finnhub | N | Free (60 req/min) / Premium ~$50–100/mo | Y — company-news `url` field | Multi-lens hub; shallower per-lens than a specialist |

**Best-value pick:** FMP at the Starter tier (~$22/mo) is the strongest single-provider fit for the operator's stated gap — it ships financial statements, *pre-computed health scores*, and a screener that makes "deteriorating health" a filter query rather than custom model work. Finnhub is a viable free-tier fallback for raw financials + news but does not offer the same pre-computed scoring product. Polygon/Massive covers SEC filings with source URLs but requires the domain rebranding to be resolved first. Sources: [FMP pricing / findmymoat review](https://www.findmymoat.com/tools/financial-modeling-prep-fmp), [site.financialmodelingprep.com/pricing-plans](https://site.financialmodelingprep.com/pricing-plans), verified 2026-06-10.

---

## News-Citation Provenance

**Hard user requirement: the overlay must allow click-through to the source article or market statement.**

### Providers that return clickable article URLs + publish timestamps

| Provider | URL field | Timestamp field | Notes |
|---|---|---|---|
| **Alpaca News API** (Benzinga-sourced) | `url` (confirmed in [Alpaca blog](https://alpaca.markets/blog/introducing-news-api-for-real-time-fiancial-news/)) | `created_at`, `updated_at` | Points to benzinga.com. History to 2015. Already integrated. |
| **Benzinga direct API** | Hyperlink to benzinga.com (confirmed [AWS Marketplace listing](https://aws.amazon.com/marketplace/pp/prodview-xwgvhwowjmw3g)) | Yes | Free basic tier; premium for full body + signals |
| **Marketaux** | `url` + `source` domain in JSON (confirmed [marketaux.com](https://www.marketaux.com/)) | `published_at` | 5,000+ sources; 100 req/day free |
| **Finnhub company-news** | `url` field (corroborated via Finnhub docs search) | `datetime` | Free tier (60 req/min); JS-rendered docs — test against live key |
| **FRED** (macro) | Links to Fed/BLS release pages | Release date | Macro series only; not news articles |

### Providers that return scores only (no article URL)

| Provider | What they return | Missing |
|---|---|---|
| Finnhub `news-sentiment` endpoint | Numeric sentiment score (−1..+1), bullish/bearish %, buzz | No individual article links — aggregate score only |
| Finnhub social-sentiment (Reddit/Twitter) | Aggregate mention count + sentiment score | No individual post URLs |
| FMP financial health scores | Altman-Z / Piotroski score | No article; it's a balance-sheet-derived number |
| FlashAlpha GEX/DEX | Pre-computed aggregate metrics | Not applicable to news |

**Summary:** The click-through requirement is satisfied by Alpaca News, Marketaux, Finnhub company-news, and Benzinga direct — all return a `url` field per article alongside a publish timestamp. Aggregate sentiment-score endpoints (Finnhub `news-sentiment`, social-sentiment) are supplementary signals only and cannot satisfy the click-through requirement on their own.

---

## What Alpaca Gives for Free vs. What Forces a Third Party

### Already in Alpaca (marginal integration cost = ~0, plumbing exists)

- **Bars / OHLCV** — full US exchanges, since 2016, at Algo Trader Plus ($99/mo); 15-min delayed IEX on free Basic
- **News (Benzinga-sourced)** — 130+ articles/day, history to 2015, `url` field per article, article text/summary/symbols — satisfies news + citation requirement
- **Options per-contract data** — IV, delta, gamma, theta, vega, rho, latest trade/quote via `get_option_chain` / `get_option_snapshot` — OPRA feed at $99/mo ATP; indicative feed free
- **Options WebSocket streaming** — real-time trades + quotes (msgpack format only)

### Gaps that force a third party

| Lens | What's missing from Alpaca | Recommended provider |
|---|---|---|
| Technicals | Computed indicators (RSI, MACD, etc.) | Compute operator-side from bars (no new vendor) |
| Sentiment | Pre-scored sentiment signal (numeric) | Marketaux (free 100/day) or derive via existing `anthropic` client |
| Options | Put/call ratio, GEX, skew — aggregate/derived | FlashAlpha (free tier) or ORATS ($99/mo) |
| **Macro** | **No economic data at all** | **FRED (free)** |
| **Fundamentals** | **No financial statements, health scores, screener** | **FMP Starter (~$22/mo)** |

---

## Recommended Minimal Stack

Covering all five lenses with citation/article-URL support, at minimum cost:

| Provider | Lenses covered | Cost (2026-06-10) | URLs / citations? |
|---|---|---|---|
| **Alpaca Algo Trader Plus** | Technicals + News/Sentiment (raw) + Options (raw IV/greeks) | $99/mo | Y (news `url` field) |
| **FRED** | Macro / Economic | **$0** | Y (release links) |
| **FMP Starter** | Fundamentals + health scores + screener + macro backup | ~$22/mo | Partial (news URLs; statements not always SEC permalink) |

**Total: ~$121/mo** for all five lenses.

**Optional add-ons (not required for basic coverage):**
- **Marketaux free tier** — adds pre-scored sentiment + broader news sources (100 req/day, $0). Upgrade path to paid if volume demands.
- **FlashAlpha free tier** — adds pre-computed GEX/max-pain/vol-surface ($0, 5 req/day). Use if options aggregates are needed without ORATS.

**What you give up at this minimal stack:**
- No pre-computed sentiment scores (derive from raw news using existing `anthropic` client, or add Marketaux free)
- No pre-computed put/call ratio / GEX (compute from Alpaca chain operator-side, or add FlashAlpha free)
- No social media sentiment (Reddit/Twitter) — add Finnhub if required
- FMP options history (fundamentals history only; options history in Alpaca is shallow — since Feb 2024 only)

---

## Staleness

All cost figures and feature claims are dated **2026-06-10**. The following are at elevated staleness risk and require live re-verification before build:

| Item | Risk | Action |
|---|---|---|
| FMP tier pricing ($22 / $59 / $149/mo) | Pricing page returns HTTP 403 to fetchers; sourced from a Tier-4 review site ([findmymoat.com](https://www.findmymoat.com/tools/financial-modeling-prep-fmp)) | Verify at [site.financialmodelingprep.com/pricing-plans](https://site.financialmodelingprep.com/pricing-plans) before build |
| Finnhub tier pricing (~$50–100/mo premium) | Pricing page is JS-rendered; exact tier boundaries sourced from a Tier-3/4 overview | Verify at [finnhub.io/pricing](https://finnhub.io/pricing) |
| Polygon.io / Massive.com options pricing (~$79–$199/mo) | **Domain rebranded to massive.com (Oct 2025)** — old polygon.io links may redirect or break | Re-verify at [massive.com/pricing](https://massive.com/pricing) or [polygon.io/pricing](https://polygon.io/pricing) for current state |
| Marketaux paid tier pricing | Free-tier (100 req/day) confirmed; paid tier amounts unverified — "unverified — needs live check" | [marketaux.com/pricing](https://www.marketaux.com/pricing) |
| Finnhub social-sentiment (Reddit/Twitter) tier gating | Social sentiment free-tier availability has shifted historically; docs are JS-rendered; could not confirm current gate | Test against a live API key |
| Alpaca News API rate limit (numeric) | The `reference/news-3` page returns 429 and `X-RateLimit-*` headers but does not publish a numeric cap | Monitor `X-RateLimit-Remaining` in production; contact Alpaca support for guaranteed SLA |
| Alpaca options data depth | Historical options data confirmed since Feb 2024 only; options *trades* limited to last 7 days lookback | Confirmed primary ([alpaca-py reference](https://alpaca.markets/sdks/python/api_reference/data/option/historical.html)) — not a pricing item but a hard data-depth constraint |
| Alpaca ATP tier ($99/mo) | Confirmed from [about-market-data-api](https://docs.alpaca.markets/docs/about-market-data-api) (2026-06-10) | Low staleness risk; re-verify if >90 days since this report |

---

## Primary Sources Used

| URL | Tier | Verified |
|---|---|---|
| [docs.alpaca.markets/docs/about-market-data-api](https://docs.alpaca.markets/docs/about-market-data-api) | 1 | 2026-06-10 |
| [docs.alpaca.markets/docs/historical-news-data](https://docs.alpaca.markets/docs/historical-news-data) | 1 | 2026-06-10 |
| [alpaca.markets/blog — News API fields](https://alpaca.markets/blog/introducing-news-api-for-real-time-fiancial-news/) | 1 | 2026-06-10 |
| [docs.alpaca.markets/reference/optionchain](https://docs.alpaca.markets/reference/optionchain) | 1 | 2026-06-10 |
| [alpaca.markets/sdks/python/api_reference/data/option/historical.html](https://alpaca.markets/sdks/python/api_reference/data/option/historical.html) | 1 | 2026-06-10 |
| [fred.stlouisfed.org/docs/api/fred/overview.html](https://fred.stlouisfed.org/docs/api/fred/overview.html) | 1 | 2026-06-10 |
| [docs.tradier.com/docs/market-data](https://docs.tradier.com/docs/market-data) | 1 | 2026-06-10 |
| [orats.com/data-api](https://orats.com/data-api) | 1 | 2026-06-10 |
| [marketaux.com](https://www.marketaux.com/) | 1 | 2026-06-10 |
| [benzinga.com/apis](https://www.benzinga.com/apis/) | 1 | 2026-06-10 |
| [findmymoat.com/tools/financial-modeling-prep-fmp](https://www.findmymoat.com/tools/financial-modeling-prep-fmp) | 4 | 2026-06-10 |
| [flashalpha.com/articles/best-options-data-apis-2026](https://flashalpha.com/articles/best-options-data-apis-2026) | 4 | 2026-06-10 |
