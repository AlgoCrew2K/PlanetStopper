# Tenure-Review Composer Fixtures

## Provenance

captured-from-producer, 2026-10-20 (18:28 to 18:32 UTC). Credentialed requests to the live Composer API
(`https://api.composer.trade/api/v0.1`) for the Roth account (11 live symphonies). The statistics, series,
holdings and definition requests are reads. The four `POST /backtest` requests are compute only (no account in
the path, no state change). 26 requests in total: 22 GET and 4 POST; statuses 200 x20, 400, 401 x2, 404, 413,
500; no 429.

Requests came from existing wrappers where one covers the call, otherwise from a one-off harness that reuses
`alpha_bot_execution.COMPOSER_BASE_URL` and `get_composer_headers`:

- Statistics: the request `alpha_bot_execution.fetch_symphony_stats` sends (same URL and headers). The wrapper
  discards the status and any key besides `symphonies`, so the capture kept them.
- Definitions: `symphony_logic.fetch_symphony_score`.
- Series, holdings, dated backtest, 413 and error probes: harness requests.

Each `.json` file is one record with these keys: `_fixture_provenance`, `_case`, `_capture_date`,
`_response_date_header`, `_endpoint`, `_request` (method, URL, query, body parameters, credential kind),
`_trimmed` (what was kept and dropped), `response_status`, `response_content_type`, and `response_body`
(`response_body_text` when the body is not JSON). Credential values are never recorded.

## Redaction

- Account id: every occurrence (URLs, bodies, file names) is the placeholder `ACCOUNT_ID_REDACTED`. The id
  appeared in request URLs only; no response body contained it.
- User id: `errors[0].meta.user_id` in `case7a_series_nonexistent_account_404.json` is `USER_ID_REDACTED`.
- Kept as returned: Composer symphony ids and names (not account identifiers).
- `00000000-0000-0000-0000-000000000000` in the 404 record is a deliberately fake account id sent on purpose,
  not a redaction.
- A scan of every file for the key id, secret, account id and user id values found none.

## Records

| File | Case | Endpoint | Request parameters | Status | Kept |
|---|---|---|---|---|---|
| `case1_statistics_roth.json` | 1 | `GET /portfolio/accounts/{account-id}/symphony-stats-meta` | none | 200 | 6 of the 11 symphonies (see Trims); fields `id`, `name`, `invested_since`, `asset_class`, `value`, `deposit_adjusted_value`; the other 34 per-entry keys are listed in `_trimmed` |
| `case2_series_n2ooAZTvBRN6ZzpMmWmU.json` | 2 | `GET /portfolio/accounts/{account-id}/symphonies/{symphony-id}` | none | 200 | complete series (oldest live symphony, 570 points) |
| `case2_series_q7WQHwLzt49cMgFBzHsX.json` | 2 | same | none | 200 | complete series (youngest live symphony, 28 points) |
| `case2_series_nOyb55RMGVCKPiYXv7TI.json` | 2 extra | same | none | 200 | complete series (148 points); subject of cases 4 and 5 |
| `case2_series_8FAXAnQmYi1INDubazeC.json` | 2 extra | same | none | 200 | complete series (233 points); `series` and `deposit_adjusted_series` differ on every point |
| `case3_holdings_recent_2026-09-20_2026-10-19.json` | 3 | `GET /portfolio/accounts/{account-id}/symphony-historical-holdings` | `start_date=2026-09-20`, `end_date=2026-10-19`, `include_aggregate_account_holdings=false` | 200 | 6 symphonies on 6 of the 30 days (see Trims); per holding `symbol` and `value` (`quantity` dropped) |
| `case3_holdings_older_2026-04-06_2026-05-05.json` | 3 | same | `start_date=2026-04-06`, `end_date=2026-05-05`, `include_aggregate_account_holdings=false` | 200 | 5 symphonies on 4 of the 30 days (see Trims); same per-holding trimming |
| `case4_definition_nOyb55RMGVCKPiYXv7TI.json` | 4 | `GET /symphonies/{symphony-id}/score` | none | 200 | complete body of the smallest live definition (100,906 bytes as received) |
| `case4_definition_sizes.json` | 4 | derived from 11 `/score` responses | none | n/a | measured byte sizes of all 11 live definitions; only the smallest definition is stored |
| `case5_backtest_dated_compact_nOyb55RMGVCKPiYXv7TI.json` | 5 | `POST /backtest` | body: `start_date=2026-07-20`, `end_date=2026-10-19`, `capital=10000.0`, `apply_reg_fee=true`, `apply_taf_fee=true`, `slippage_percent=0.005`, `broker=alpaca`, `backtest_version=v2`; compact separators | 200 | `first_day`, `last_market_day`, `data_warnings`, `costs`, `dvm_capital`, `stats`; six other keys dropped (sizes in `_trimmed`) |
| `case6_backtest_413_too_large.json` | 6 | `POST /backtest` | same body shape, largest live definition, default separators, 1,066,003 bytes | 413 | complete HTML body |
| `case7a_series_nonexistent_symphony_200_empty.json` | 7a | series | symphony id `ZZZZZZZZZZZZZZZZZZZZ` | 200 | complete body (three empty arrays) |
| `case7a_series_nonexistent_account_404.json` | 7a | series | account id `00000000-0000-0000-0000-000000000000` | 404 | complete body |
| `case7b_statistics_invalid_credentials_401.json` | 7b | statistics | fake key id and secret | 401 | complete body |
| `case7b_series_invalid_credentials_401.json` | 7b | series | fake key id and secret | 401 | complete body |
| `case7c_backtest_malformed_structure_400.json` | 7c | `POST /backtest` | root node whose `children` is the string `"x"` | 400 | complete body |
| `case7c_backtest_unknown_ticker_500.json` | 7c | `POST /backtest` | well-formed tree with one asset `ZZZZNOTATICKER1` | 500 | complete body |
| `synthetic_non_json_200.txt` | n/a | n/a | n/a | n/a | SYNTHETIC, see below |

Case 2 rows marked "extra" were not in the requested list. They exist because the plan's S-3.1 and S-6.1 need
two series with at least 181 points plus one younger than the 126-day threshold, and S-5.1 needs a realized
series for the same symphony as the dated backtest.

## Trims

The statistics and holdings records are subsets of the captured responses, made by selecting entries of the
captured data and copying them unchanged (no value is typed or edited; each file's `_trimmed` records the
same). The request metadata of a holdings record (`_request`) is that of the full 30-day request the capture
made. A test never asserts a count taken from the capture: every count is derived from the committed record.

Symphonies kept (statistics record, in captured order): `8FAXAnQmYi1INDubazeC`, `lW4ZzWuqR8tEO2DhXbil`,
`q7WQHwLzt49cMgFBzHsX`, `nOyb55RMGVCKPiYXv7TI`, `Gpaw3IhZghQPRE6AdEKx`, `n2ooAZTvBRN6ZzpMmWmU`. Each covers a case:

- `n2ooAZTvBRN6ZzpMmWmU`, `8FAXAnQmYi1INDubazeC`, `nOyb55RMGVCKPiYXv7TI`: the three eligible symphonies with a
  captured series; the oldest holds a long-volatility product on the last days and cash on the first; the
  third is the subject of the definition and backtest records.
- `q7WQHwLzt49cMgFBzHsX`: the youngest symphony (below the default threshold), absent from the older holdings.
- `Gpaw3IhZghQPRE6AdEKx`: a symphony with no series record that holds a long-volatility product on every kept
  recent day (the co-holder), so the empty-series answer is exercised for an old symphony.
- `lW4ZzWuqR8tEO2DhXbil`: a symphony with no series record that holds no long-volatility product on the
  kept recent days.

Dropped: `qF5ZU7ALjrlhxrGEwsyJ`, `iaSOOUsmnCJHiZvbrWfs`, `hvPiGP1O7AHfutHE3Fjy`, `5XjzXjdGnjh99MIsdM97`,
`MoAkUHnavSYw3oONiUxe` (no test distinguishes them), and from the older holdings the one id that is not in the
statistics record (`INfCn3eKsu6i4oTTqdUp`).

Days kept, recent holdings: 2026-09-21 (cash rows for two symphonies), 2026-10-14, 2026-10-15 (the oldest starts
holding a long-volatility product beside the co-holder), 2026-10-16 (every symphony holds one), 2026-10-17 (a
Saturday: a non-session day that carries holdings) and 2026-10-19 (the as-of day). Days kept, older holdings:
2026-04-06, 2026-04-14 (cash rows and long-volatility holdings), 2026-04-23 (a day only the co-holder holds
one) and 2026-04-25 (a Saturday). The two sets share no date.

## Synthetic file

`synthetic_non_json_200.txt` is hand-written. It is not a Composer response. No read-only request provokes a
non-JSON HTTP 200 from Composer, so the file stands in for that body in S-1.19, S-2.14, S-5.11 and S-7.8. The
file text says it is synthetic.

## Measured facts about the captured data

Statistics (the capture; the committed record keeps 6 entries, see Trims):
- Top-level keys: `symphonies` only. 11 entries, 40 keys each. `invested_since` is non-null on all 11;
  `asset_class` is `EQUITIES` on all 11. Each entry's `tickers[].symbol` uses the `EQUITIES::SYM//USD` form.

Series:
- The first point's date equals the statistics `invested_since` on all four captured series.
- The last point is 2026-10-19; the capture ran on 2026-10-20 at about 14:28 ET and had no point for that day.
- Every `epoch_ms` is 00:00:00 UTC. Every date is an NYSE trading day (`market_calendar.is_trading_day`), and
  no NYSE trading day between a series' first and last point is missing. No null values.
- `series` equals `deposit_adjusted_series` on 8 of 570 points (n2oo), 147 of 148 (nOyb), 27 of 28 (q7WQ) and
  0 of 233 (8FAX). On nOyb and q7WQ the single difference is the first point (`series` 998.28 against
  `deposit_adjusted_series` 1000.0 on nOyb).
- A symphony id that does not exist returns HTTP 200 with three empty arrays, not 404. An account id that does
  not exist returns 404 `broker-account-not-found`.

Holdings (the capture; the committed records keep the days and symphonies listed under Trims):
- With `include_aggregate_account_holdings=false` the response has only `by_symphony`
  (no `total_symphony_holdings` key).
- A 30-inclusive-day span returns 30 date keys per symphony, weekends included; weekend days carry holdings.
- Holding entries have `symbol`, `quantity`, `value`. `symbol` is a bare ticker (`VIXY`); cash is `USD`.
- Recent span: 11 symphonies (all live), 2,921 entries, 12 `USD` entries. `VIXY` is held by at least two
  symphonies on 27 of 30 days. Also present: `UVIX`, `UVXY`, `VXX`, `VIXM`, `SVIX`.
- Older span: 11 symphonies, of which one id (`INfCn3eKsu6i4oTTqdUp`) is not in the current statistics and the
  youngest live symphony (`q7WQHwLzt49cMgFBzHsX`) is absent. 3,421 entries, 29 `USD` entries. `VIXY` is held
  by at least two symphonies on 23 of 30 days.

Dated backtest:
- The request dates were honored: `first_day` 20654 is 2026-07-20 and `last_market_day` 20745 is 2026-10-19.
- `dvm_capital` has one key (the symphony id) with 65 day-integer points; `stats.size` is 64.
- The 101,110-byte compact body returned 200.

Definition sizes (bytes, UTF-8):

| Definition | Tree, default separators | Tree, compact | Backtest body, default | Backtest body, compact |
|---|---|---|---|---|
| Largest (`hvPiGP1O7AHfutHE3Fjy`) | 1,065,781 | 988,016 | 1,066,003 | 988,220 |
| Smallest (`nOyb55RMGVCKPiYXv7TI`) | 109,051 | 100,906 | 109,273 | 101,110 |

The other nine are in `case4_definition_sizes.json`. The 1,066,003-byte default-separator body of the largest
definition returned 413 (nginx/1.28.1 behind Cloudflare, `text/html`, 656 bytes). The script block in that page
holds per-request Cloudflare challenge values, not credentials. A compact-separator request of the largest
definition was not sent.

Error bodies:
- Invalid credentials: 401, `{"errors":[{"status":401,"code":"invalid-api-key","title":"Invalid or missing API key secret"}]}`.
- Malformed definition: 400 with a `reitit.coercion/request-coercion` (malli) body that echoes the request
  `value`.
- Unknown ticker in a well-formed tree: 500 `internal-server-error`, observed once.

## Not captured

- HTTP 403: no read-only request tried produced one.
- A backtest tradeability-400 envelope (`... not tradable`): the unknown-ticker probe returned 500 instead.
- A fresh 422: reuse `tests/fixtures/strategy_builder/wt_marketcap_deprecated_envelope.json`.
- 429, other 5xx, timeouts and transport errors: none occurred or can be provoked on request.
