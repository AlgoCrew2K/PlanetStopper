# Derived Alpaca `/v2/stocks/bars` pages

No real capture holds the cases below (a 20-session series whose returns equal a classifier golden
series; an IEX minute page whose last bar closes at the SIP daily close), so each file is derived. Each
keeps the bar shape of the captured records in `tests/fixtures/alpaca/stock_bars_captured/` (keys
`c h l n o t v vw`; daily bars stamped 00:00 ET rendered in UTC; `bars` keyed by symbol;
`next_page_token` present) and is checked against that shape by
`tests/regime_label_job/test_fixture_shapes.py`, which also re-derives every close from its source.

The files were written once by a throwaway script that read the golden series and the helpers in
`tests/regime_label_job/_sip.py`. Closes are unrounded floats (a captured close has at most 4 decimals), so
that each session's close-to-close return reproduces the source return to better than 1e-12.

All pages cover the same 21 NYSE sessions, 2026-10-06 to 2026-11-03 (20 close-to-close returns). Two tickers
per page, `<P>A` (first close 100.0) and `<P>B` (first close 50.0), carry the SAME return series, so a
0.6/0.4 holding's allocation-weighted return equals the source return and the expected label is the
fixture's own `expected` value.

| File | Source | Derivation | Scenario |
|---|---|---|---|
| `sip_1day_golden_trending.json` (tickers `TRNA`, `TRNB`) | `tests/fixtures/math/regime_classifier/trending_basic.json` | closes rebuilt from the golden returns | S-2.2, S-2.12, S-2.9 |
| `sip_1day_golden_mean_reverting.json` (`MRVA`, `MRVB`) | `.../mean_reverting_basic.json` | as above | S-2.2, S-2.12, S-2.9 |
| `sip_1day_golden_high_vol.json` (`HVLA`, `HVLB`) | `.../high_vol_stressed_basic.json` | as above | S-2.2, S-2.12, S-2.9 |
| `sip_1day_std_0p02.json` (`STDA`, `STDB`) | constructed | returns alternate +a, -a with a = 0.02 * sqrt(19/20), so the sample std (ddof 1) is exactly 0.02 as a fraction | S-2.3 |
| `iex_1day_golden_<label>.json` | the matching `sip_1day_golden_<label>.json` | `feed=iex` 1Day page whose closes EQUAL the SIP closes (the S-2.12 coinciding case) | S-2.12 |
| `iex_1min_golden_<label>.json` | the matching `sip_1day_golden_<label>.json` | `feed=iex` 1Min page: 3 bars per session (09:30 ET, 09:31 ET, 15:59 ET); the last bar closes at the session's SIP daily close, the first two at 0.999 and 1.001 of it | S-2.12 |

## Derivations made inside tests (no file)

These start from a file above or from `stock_bars_captured/` and change it in memory, because each
differs from its source in one value:

| Test | Source | Change | Scenario |
|---|---|---|---|
| `test_label_computation.py` | `sip_1day__20261104_025116.json` | none (closes read as captured) | S-2.1 |
| `test_label_computation.py` | `sip_1day_golden_mean_reverting.json` | one close set to NaN, +inf, 0 or a negative number | S-5.6 |
| `test_label_computation.py` | `_sip.closes_from_returns` over fixed return lists | 25- and 45-session variants; 4 oldest closes changed; B's closes deleted on chosen sessions | S-2.5, S-2.10, S-2.11, S-5.2 |
| `test_sip_fetch.py` | `sip_1day_paged__*.json`, `sip_1day__*.json` | page 2 of a paged response = the captured 24-session body minus the bars page 1 already holds | paging |
| `test_sip_fetch.py` | `sip_1day_golden_mean_reverting.json` | extra bars dated D and after D appended | S-2.4 |
| `test_sip_paging_bound.py` | `sip_1day__20261104_025116.json` (captured) | page 1 is the captured body with only `next_page_token` replaced (a non-empty list, dict or set, an empty list, dict or set, None, an empty string, an integer, a repeated string, or the key removed); the set cases are served as a parsed body, since JSON cannot hold a set | page token handling |
| `test_job_run.py` | `tests/regime_label_job/_sip.page_body` over the golden closes | first page of one batch carries a non-empty list as `next_page_token`; every other page is served normally | an unusable page token fails one batch |
