# Alpaca Research — Source Index

Rolling library of sources used in `docs/research/alpaca/` reports. Tier-tagged per `~/.claude/agents/researcher.md` source-quality hierarchy. Append-only — old entries get staleness flags, never deleted.

## Tier 1 — Primary

| URL | Description | First-cited | Last-verified |
|-----|-------------|-------------|---------------|
| https://pypi.org/project/alpaca-py/ | PyPI metadata for alpaca-py (version, Python classifiers, license) | 2026-05-12 | 2026-05-12 |
| https://api.github.com/repos/alpacahq/alpaca-py/releases | GitHub releases JSON API — authoritative dates + bullet summaries | 2026-05-12 | 2026-05-12 |
| https://github.com/alpacahq/alpaca-py/releases | GitHub releases (HTML rendering — used as cross-check) | 2026-05-12 | 2026-05-12 |
| https://github.com/alpacahq/alpaca-py | alpaca-py repo README — clients listed, Python 3.8+ requirement | 2026-05-12 | 2026-05-12 |
| https://github.com/alpacahq/alpaca-trade-api-python | Legacy SDK repo — deprecation notice + v3.2.0 release date | 2026-05-12 | 2026-05-12 |
| https://docs.alpaca.markets/docs/about-market-data-api | Subscription tiers, prices, rate limits (Basic, Algo Trader Plus, Broker tiers) | 2026-05-12 | 2026-05-12 |
| https://docs.alpaca.markets/reference/stockbars | `/v2/stocks/bars` parameter reference | 2026-05-12 | 2026-05-12 |
| https://docs.alpaca.markets/reference/stocklatestquotes-1 | Latest quotes endpoint reference | 2026-05-12 | 2026-05-12 |
| https://docs.alpaca.markets/reference/getaccount-1 | `/v2/account` endpoint — confirmed paper base URL | 2026-05-12 | 2026-05-12 |
| https://docs.alpaca.markets/docs/real-time-stock-pricing-data | Real-time feed enumeration (sip/iex/delayed_sip/boats/overnight) | 2026-05-12 | 2026-05-12 |
| https://docs.alpaca.markets/docs/paper-trading | Paper-vs-live divergences | 2026-05-12 | 2026-05-12 |

## Tier 2 — Expert / Maintainer

| URL | Description | First-cited | Last-verified |
|-----|-------------|-------------|---------------|
| https://forum.alpaca.markets/t/is-the-unlimited-plan-really-unlimited-or-is-it-a-limited-calls-min-plan/11565 | Community thread on "Unlimited" plan naming — referenced as conflicting/clarifying source | 2026-05-12 | 2026-05-12 |
| https://forum.alpaca.markets/t/keeping-up-with-change-inside-alpacas-documentation/13823 | Community thread confirming Alpaca doc-site drift is a known issue | 2026-05-12 | 2026-05-12 |

## Tier 4 — Secondary (marketing pages — NEVER cited as capability evidence)

| URL | Description | First-cited | Use restriction |
|-----|-------------|-------------|-----------------|
| https://alpaca.markets/data | Marketing page — surfaces "Unlimited" naming conflict | 2026-05-12 | Naming-conflict citation only, NEVER as capability proof |

## 404'd / Unreachable (2026-05-12 — flagged for re-verification)

| URL | Status on 2026-05-12 | Notes |
|-----|----------------------|-------|
| https://docs.alpaca.markets/docs/historical-bars | 404 | Page exists in nav but returns 404 |
| https://docs.alpaca.markets/docs/portfolio-history | 404 | Portfolio history doc page — recheck before depending on `period`/`timeframe` params |
| https://docs.alpaca.markets/docs/api-rate-limit | 404 | Dedicated rate-limit page missing; per-tier limits from about-market-data page only |
| https://github.com/alpacahq/alpaca-py/blob/master/CHANGELOG.md | 404 | Repo uses GitHub Releases as changelog |

## Staleness Policy

- Re-verify any Tier 1 entry > 90 days old before citing in a new report.
- Re-fetch all 404'd pages before next Alpaca research cycle — Alpaca's doc site has known drift.
- Forum threads (Tier 2): re-check at 180 days; older than that, treat as `[STALE]` and require corroboration.
