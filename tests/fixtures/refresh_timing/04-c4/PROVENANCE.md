# 04-c4 fixtures: provenance

Plan: refresh-timing 04, cycle C4 (`/performance`).

## Record: `requests_per_tick_at_base.json` (case: the data requests the page sends on its 60-second tick)

- Source: the real `static/performance.js` evaluated unedited in `tests/ui/_page_js_harness` over the page the
  real app renders, every answer taken from the real routes over the captured production records
  (`tests/ui/_performance_page.py`: bot state, `shadow_last_rows.json` and `exit_triggers.json` through the
  real writers), with the page clock advanced 60 seconds; the data requests the tick sent, sorted.
- Producer call: `python tests/fixtures/refresh_timing/04-c4/capture_requests_per_tick.py` with `DB_PATH` and
  `ATLAS_CACHE_DB_PATH` set to scratch files. The script refuses to run when `static/performance.js` or
  `templates/performance.html` differ from `HEAD`.
- Base SHA: `7d8db3cd123397c1d013b693d7921c115e00ef07`; captured at `2026-10-31T09:25:50Z`. 14 requests.
- Scenario served: S-4.6. Edits: none. Sign-off: not needed.

## Derived records

| Builder | Source record | Changed fields | Reason | Scenarios | PM sign-off |
|---|---|---|---|---|---|
| `test_one_failed_request_keeps_that_row_and_redraws_the_others` | the real exit-turnover body of one symphony | `windows["30"].exit_count` + 7 | a second tick whose figures differ for one symphony | S-4.2 | requested |
