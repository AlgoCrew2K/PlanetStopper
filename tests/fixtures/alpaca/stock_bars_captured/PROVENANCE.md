# Provenance: Alpaca `/v2/stocks/bars` captures

Captured 2026-11-04T02:51:16Z by the PM (session 8055360f) for `feature-plans/regime-label-producer.md`, from the operator's Alpaca credentials in the repository `.env`. Every request was a read-only `GET https://data.alpaca.markets/v2/stocks/bars`. The credentials were sent as headers and are not recorded. No body contained a key matching `(key|token|secret|account.?id|auth)` other than `next_page_token`, a pagination cursor, which is kept.

Each file holds `request` (method, url, params), `captured_at_utc`, `status` and the raw response `body`. These are raw HTTP responses, not the output of a code wrapper: the existing wrapper (`synthetic_history.fetch_bars`) fixes `feed=iex` and drops status codes, and the regime job's tests need both. `END` below is the capture time minus 20 minutes (2026-11-04T02:31:16Z); `END5` is the capture time minus 5 minutes.

| File | Request | Result | Case it stands for |
|---|---|---|---|
| `sip_1day__20261104_025116.json` | `feed=sip`, `timeframe=1Day`, `adjustment=split`, `symbols=QQQ,DULL,STPZ`, `start=2026-10-01`, `end=END` | 200; 24 bars per symbol, last bar 2026-11-03; no `next_page_token` | consolidated daily closes the job reads |
| `sip_1day_paged__20261104_025116.json` | as above with `limit=5` | 200; 5 bars (DULL); `next_page_token` set | a response that must be paged |
| `sip_1day_recent_refused__20261104_025116.json` | `feed=sip`, `1Day`, `symbols=QQQ,DULL`, `start=2026-10-27`, `end=END5` | 403 `subscription does not permit querying recent SIP data` | the job's request ending inside the last 15 minutes |
| `sip_1day_missing_symbol__20261104_025116.json` | `feed=sip`, `1Day`, `symbols=QQQ,ZZZQX`, `start=2026-10-01`, `end=END` | 200; QQQ only; ZZZQX absent with no error | a 200 that omits a requested symbol |
| `iex_1min__20261104_025116.json` | `feed=iex`, `timeframe=1Min`, `symbols=QQQ`, 2026-11-03 14:30Z to 21:00Z | 200; 389 bars | the replay's minute basis, for comparison |
| `iex_1day__20261104_025116.json` | `feed=iex`, `1Day`, `symbols=QQQ,DULL`, `start=2026-10-01`, `end=END` | 200; 24 bars per symbol | the replay's daily basis, for comparison with SIP |

Re-capture: run the same requests read-only with an end bound at least 15 minutes before the request time; never edit a captured file in place.
