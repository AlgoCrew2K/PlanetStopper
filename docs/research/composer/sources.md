# Composer.trade Research — Source Index

Rolling library of sources used in `docs/research/composer/` reports. Tier-tagged per `~/.claude/agents/researcher.md` source-quality hierarchy. Append-only — old entries get staleness flags, never deleted.

## Tier 1 — Primary (official Composer surfaces)

| URL | Description | First-cited | Last-verified | Observation method |
|-----|-------------|-------------|---------------|-------------------|
| https://api.composer.trade/docs/index.html | OpenAPI 1.0.0 reference (Redocly) — full endpoint inventory, schemas, rate limits, error codes | 2026-05-12 | 2026-05-12 | documented |
| https://help.composer.trade/article/236-getting-started-with-your-composer-api | Auth header shape, rotation rules, security guidance (last-updated 2025-07-16) | 2026-05-12 | 2026-05-12 | documented |
| https://help.composer.trade/article/235-getting-your-api-key | UI flow for obtaining keys (last-updated 2025-12-31) | 2026-05-12 | 2026-05-12 | documented |
| https://help.composer.trade/ | Help center index | 2026-05-12 | 2026-05-12 | documented |
| https://github.com/invest-composer | Official Composer GitHub org — 3 public repos | 2026-05-12 | 2026-05-12 | documented |
| https://github.com/invest-composer/composer-trade-mcp | Official MCP server reference implementation (raw README/server.py 404 to anonymous fetch this session) | 2026-05-12 | 2026-05-12 | repo-listing only |

## Tier 2 — Expert / Official-adjacent

| URL | Description | First-cited | Last-verified | Observation method |
|-----|-------------|-------------|---------------|-------------------|
| https://www.composer.trade/blog/introducing-the-composer-mcp-server | Composer's own blog announcing the MCP server | 2026-05-12 | 2026-05-12 | search-result surface only (403 to direct fetch) |
| https://alpaca.markets/blog/how-composer-is-redefining-algorithmic-trading-with-their-no-code-platform/ | Alpaca's analysis of Composer (Composer clears through Alpaca) | 2026-05-12 | 2026-05-12 | search-result surface only |

## Tier 3 — Community

None confirmed as of 2026-05-12. No active community-maintained Composer SDKs or signed community threads surfaced. Reddit/Stack Overflow targeted searches returned no Composer-specific results. `[Medium]` confidence that none exist; deeper GitHub code-search recommended before declaring final.

## Tier 4 — Secondary

| URL | Description | First-cited | Last-verified | Observation method |
|-----|-------------|-------------|---------------|-------------------|
| https://lobehub.com/mcp/invest-composer-composer-trade-mcp | Third-party MCP server directory describing Composer MCP | 2026-05-12 | 2026-05-12 | search-result surface only |
| https://opentools.ai/tools/composer-trade | Third-party tool directory | 2026-05-12 | 2026-05-12 | search-result surface only |
| https://mcp.so/server/composer-mcp-server/invest-composer | Third-party MCP directory | 2026-05-12 | 2026-05-12 | search-result surface only |

## Unreachable / 403'd (2026-05-12 — flagged for re-verification)

| URL | Status on 2026-05-12 | Notes |
|-----|----------------------|-------|
| https://www.composer.trade/whats-new | HTTP 403 to WebFetch | Marketing "What's New" page — only changelog proxy Composer publishes. Contents `[Unverified]` until re-fetched via authenticated/header-spoofed fetcher or `gh` CLI |
| https://github.com/invest-composer/composer-trade-mcp/blob/main/server.py (and README raw paths) | 404 to anonymous WebFetch | MCP server source unreachable this session — needs `gh` CLI |

## Tier 5 — Unknown / not relied upon

LinkedIn post about MCP trading (Ben Rollert) — listed in search results, not used as evidence.

## Staleness Policy

- Composer API findings default to `[Medium]` absent triangulation — the platform has no public spec versioning.
- Re-verify any Tier 1 entry > 30 days old before citing in a new report (Composer is fast-moving; explicit re-verification cadence per `composer-api-researcher.md` operating rules).
- Re-fetch all 403'd / 404'd URLs at next research cycle.
