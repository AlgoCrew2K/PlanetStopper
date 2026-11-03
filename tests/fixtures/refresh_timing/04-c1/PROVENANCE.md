# 04-c1 fixtures: provenance

Plan: refresh-timing 04 (pages and header), cycle C1 (the header on the four other pages).

## Record: `header_markup_at_base.json` (case: the header markup each page renders at the base)

- Source: the real app's `<nav data-testid="studio-nav">` element for `/`, `/history`, `/performance`, `/ai-advisor` and
  `/settings`, rendered through its test client with the host clock frozen on a Saturday (market closed) and the
  developer `.env` unread, by `tests/ui/_header_status_answers.nav_markup`.
- Producer call: `python tests/fixtures/refresh_timing/04-c1/capture_header_markup.py` with `DB_PATH` and
  `ATLAS_CACHE_DB_PATH` set to scratch files. The script refuses to run when `templates/`, `app.py` or
  `static/chrome.js` differ from `HEAD`.
- Base SHA: `7d8db3cd123397c1d013b693d7921c115e00ef07` (also written into the file as `base_sha`); captured at `2026-10-31T09:38:23Z`.
- Scenario served: the header-layout row (a page header markup equals the base markup except the one attribute).
- Edits: none. Sign-off: not needed (no derived record).

## Revision: `header_markup_at_base.json` re-captured for the unslop pass (shared chrome)

The unslop pass rewrote the header markup on purpose (classes instead of inline styles, a plain wordmark, an icon bell, "Next mm:ss", a chevron on the account chip; the mode tag, Force run now and Emergency Liquidate keep their recorded markup). The record now holds the header as the pass left it, taken with the poll attribute on four pages; `tests/ui/test_header_refresh_client.py` reads both sides without that attribute.

- Producer call: `python tests/fixtures/refresh_timing/04-c1/capture_header_markup.py` with `DB_PATH` and `ATLAS_CACHE_DB_PATH` set to scratch files; the script refuses to run when `templates/`, `app.py` or `static/chrome.js` differ from `HEAD`. Base SHA now `efa817096f942f122129e1921e521513e3396966` (the file's `base_sha`), captured `2026-11-03T17:02:27Z`.
- Sign-off: not needed (producer output).

## Derived at test time (not stored)

| Builder | Source | Changed field | Reason | Scenarios | PM sign-off |
|---|---|---|---|---|---|
| `header_answer(...)` arguments | the real `GET /api/header-status` body at a named server instant | `last_successful_cycle_at` set N seconds before that instant, `_health_status` reporting the named reasons, `LIVE_EXECUTION` and account ids from a patched `.env` reader, `_DAEMON_STARTED_AT` set | each argument produces the state a scenario names (a stale cycle, a degraded daemon, a live flag, a restart) through the route own inputs | S-1.1 to S-1.11, S-2.14 | requested |
