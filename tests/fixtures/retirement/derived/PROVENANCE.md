# Derived retirement run records

Each record here, or each helper named below, changes only the listed fields of ONE real record and
leaves every other field verbatim (suite fixture rule 4). The real records are:

* the stored run summary `tests/fixtures/retirement/run_summary_captured.json` (row 4025, exported
  from production on 2026-10-22), and
* the output of the real producer, `advisors.retirement_recommender.build_recommendations_with_summary`
  (`source="composer"`) over the PM's real Composer captures in
  `tests/fixtures/composer/series_captured/`, run through `tests/advisors/_series_captures.py`
  (`run_over_captures`).

PM sign-off (producer owner): ruled on the Gate-2 approach for plan 03 on 2026-10-22 and confirmed in
the RED dispatch brief of 2026-10-30 ("Derived records: PROVENANCE entry, I sign off").

## Committed files (derived from the captured row)

Written once by a throwaway script that loaded the captured row's `raw_response`, applied the
listed changes and dumped the result; the file is the whole summary. A conformance test in
`tests/app/test_correlations_derived_records.py` rebuilds each from the captured row with exactly
the listed changes and fails on any other difference.

| File | Source record | Changed fields | Why | Scenario |
|---|---|---|---|---|
| `summary_series_unavailable.json` | captured row | `status` -> `series_unavailable`; `symphonies_with_series` -> 1; `series_days_min` and `series_days_max` -> 250; `pairs_evaluated`, `pairs_screened`, `blocked_uncertainty`, `blocked_stress_too_thin`, `blocked_stress_below_threshold`, `blocked_no_candidate` -> 0 | a run that found fewer than two usable series has no pairs | S-8.3 |
| `summary_insufficient_symphonies.json` | captured row | `status` -> `insufficient_symphonies`; `roster_size` -> 1; `symphonies_with_series` -> 0; `symphonies_without_series` -> `[]`; `series_days_min` and `series_days_max` -> null; the six pair and gate counts -> 0 | a roster of one symphony has nothing to compare | S-8.3 |
| `summary_internal_error.json` | captured row | `status` -> `internal_error`; `error_type` -> `TimeoutError`; `roster_size`, `symphonies_with_series`, `series_days_min`, `series_days_max` and the six pair and gate counts -> null; `symphonies_without_series` -> `[]` | a run that failed part-way knows none of its counts | S-8.2, S-8.7 |

## Derived at test time (helpers in `tests/app/_correlations_view_support.py` and `tests/advisors/_series_captures.py`)

| Helper or argument | Source record | Changed fields | Why | Scenario |
|---|---|---|---|---|
| `with_run_at_removed` | a `run_over_captures` summary | `run_at` removed | a run with no recorded time | S-2.3 |
| `with_run_at` | a `run_over_captures` summary | `run_at` -> `2026-10-22T03:45:00Z` (the nightly slot's instant) | a run whose time is known, so the Eastern reading is checkable | S-2.1 |
| `with_unmapped_exclusion_reason` | a `run_over_captures` summary | `symphonies_without_series[0].reason` -> `retired_by_operator` | a reason the shared mapping does not know | S-5.4 |
| `with_pair_correlations` | a `run_over_captures` summary | the named pairs' `correlation` replaced (-0.75, -1.0, -0.12; 0.65, 0.40, -0.40, 0.6449, 0.6499; 0.95, 0.70) | no captured pair is negative and none sits on a band boundary | S-6.1, S-6.2, S-6.3, S-6.4 |
| `produced["screen_threshold"] = 0.72` (one test) | a `run_over_captures` summary | `screen_threshold` -> 0.72 | the legend must read the bound from the summary, not from a typed number | S-6.5 |
| `name_override_duplicate` | the captures' index | the display name of `qF5ZU7ALjrlhxrGEwsyJ` set to that of `q7WQHwLzt49cMgFBzHsX` | two live symphonies with one display name | S-7.2 |
| `name_override_markup` | the captures' index | one display name gains ` <b>bold</b> & Co "q"` | a name carrying markup, an ampersand and a double quote | S-7.3 |
| `name_override_swap` | the captures' index | the display names of the first and last symphony (by name) exchanged | labels change while the pairs stay put | S-7.5 |
| `per_symphony_now_et` for `hvPiGP1O7AHfutHE3Fjy` at 2026-03-01 12:00 ET | the capture, read by the real parser at another instant | the instant the capture is read at, so its series ends 2026-02-27 | a pair with no common day with `q7WQHwLzt49cMgFBzHsX` (begins 2026-09-10) | S-1.3, S-3.3 |
| `per_symphony_now_et` for `Gpaw3IhZghQPRE6AdEKx` at 2026-10-15 12:00 ET | the capture, read by the real parser at another instant | the instant the capture is read at, so its last date is 2026-10-14 | a symphony whose series ends before the others | S-2.2 |
| `accounts={...: None}` | the seeded roster | one roster entry written without `account` | the recommender's real `no_account` exclusion | S-5.2 |
| `errors={...: "HTTP 503"}` | the fetch seam | the client's own failure result for one symphony | the recommender's real `fetch_error` exclusion | S-5.2, S-9.10 |
| bot_state re-saved without `qF5ZU7ALjrlhxrGEwsyJ` (one test) | the seeded roster | one roster entry removed | a symphony in the run that is no longer live | S-7.4 |
