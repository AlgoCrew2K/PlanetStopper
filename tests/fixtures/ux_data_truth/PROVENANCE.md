# Provenance: records the data-truth tests build at run time

This folder holds no data files. The tests build their records at run time from the sources
below, and this file is where each derived record names its source, the fields changed and the
reason (SC-10 rule 4). PM sign-off of every derived record below: granted.

## Sources used unchanged

| Source | Class | Used for |
|---|---|---|
| `tests/fixtures/prod_droplet/bot_state_positions.json` | captured from the droplet (`capture_from_droplet.py`) | live symphony names and Composer hashes; picked by property (trailing space, apostrophe, parentheses), never by typed name |
| `tests/fixtures/advisors/frontrunner/real_tree_07_iaSOOUsmnCJHiZvbrWfs.json` | captured Composer tree | Frontrunner proposal tree; Strategy Builder candidate tree |
| `tests/fixtures/autotuner/dsr_surfacing/autotune_run_legacy_nulls.json` | schema-derived, provenance header in the file | a legacy run with null score columns |
| `tests/fixtures/prism_verifier/verify_cited_numbers_mixed_classifications.json` | schema-derived (numeric values synthetic, shapes from the lens builders); the file says so | council claims and lens sections fed to the real `verify_cited_numbers` |

## Derived records

| Test | Source record | Fields changed | Reason |
|---|---|---|---|
| verification rows (`_producer_row`) | `verify_cited_numbers` output over the fixture above | `cited_numbers` limited to the claims the fixture records as pass, as unverifiable, or as flagged; or emptied | one real producer row per verdict the pill distinguishes |
| damaged verification summary | the pass-plus-unverifiable producer row | `n_pass` or `n_checks` deleted, `n_pass` set to null, `n_checks` set to the string `"32"` | the summary-field-missing and wrong-type edges the pill must survive |
| unclassified check | the producer's pass check | nine copies re-indexed `PASS_0..8`; one copy renamed `UNCLASSIFIED` with `classification` `unclassified`; summary and verdict recomputed with the producer's own `_summarize` and `_derive_verdict` | the plan's "one check of no recognised class" edge, which `_summarize` counts in `n_checks` and in no bucket |
| Strategy Builder cards | rows written by `_persist_survivor` and `_persist_rejected` from authored `CandidateInfo` inputs (objective, template, metrics are inputs, never asserted) | none | stored-card label cases |
| Strategy Builder row, key removed / null / unknown | the producer's row for a base candidate | `objective` or `template_id` deleted, set to null, or set to `legacy_mix` / `community`; `candidate_id` and `subject_id` renamed so the original row stays | the absent-and-unknown edges for "not recorded" |
| Strategy Builder failed-backtest card | the producer's rejected row | `backtest_error` added | no writer sets `backtest_error` on a Strategy Builder row at the Ref (the template branch keys on it), so the only way to render that card is a row with the key |
| autotune runs | rows written by `database.save_autotune_run` | authored scores and decisions (inputs, asserted only as formatted) | one run per label case |
| route row, verdict key deleted | the real `GET /api/autotune-runs` row | `frozen_eval_verdict` deleted | the missing-field edge |
| bot_state with metadata entries | `bot_state_positions.json` | `last_market_close_snapshot` and `post_mortem_run` added as dict entries with no `name` | the real engine writes these top-level non-symphony entries; name resolution must skip them |
| bot_state without the incumbent | `bot_state_positions.json` | one entry removed | the "incumbent no longer held" edge |
| bot_state with a markup display name | `bot_state_positions.json` | one entry's `name` replaced by `<b>bold</b> & Co`, nothing else varied | the markup-in-a-name edge for the three Advisor name sites this cycle renders. The approach (`approach/01-data-truth.md`, Per-Cycle Test Plan, Fixtures) records the PM's sign-off of the changed-name record for this case |

## Held

Scenario S-7.11 (live Strategy Builder run card) is held until the PM commits a
`POST /ai-advisor/strategy-builder/run` capture taken on the droplet after the model-unpin deploy.
No test stands in for it.

# ux_data_truth: derived inputs for the History and Performance page tests

No file in this directory is a fixture. The tests below derive every input at run time from
the verbatim production captures already committed under `tests/fixtures/prod_droplet/`
(captured read-only from the droplet by `tests/fixtures/prod_droplet/capture_from_droplet.py`,
see that script's header for host, SHA and capture time). This file records each derivation.
Real symphony names and Composer ids stay as captured. PM sign-off for every derived record
below: granted.

Tests: `tests/ui/test_history_page_data_truth.py`, `tests/ui/test_performance_page_data_truth.py`,
`tests/app/test_calendar_window_routes.py`, `tests/analytics/test_history_daily_drilldown.py`.

## Verbatim use (not derived)

| Source | Used for |
|---|---|
| `prod_droplet/post_mortems/post_mortem_2026-06-22.json` | a day whose dollars sum positive; three triggers with distinct `time_triggered` |
| `prod_droplet/post_mortems/post_mortem_2026-06-23.json` | a trigger with `saved_dollars` 0.0; triggers sharing `time_triggered` 09:50 under different names |
| `prod_droplet/post_mortems/post_mortem_2026-07-07.json` | a day whose dollars sum negative; a day with a bar and no valid exit (used unstamped, as captured) |
| `prod_droplet/shadow_last_rows.json` (143 rows) | `shadow_history` rows written into the test database; per-symphony series for drawdown relations |
| `prod_droplet/bot_state_positions.json` | `bot_state` written with `database.save_state` (display names, hashes, `current_value` weights) |
| `prod_droplet/exit_triggers.json` (87 rows) | `exit_triggers` rows for `/api/exit-turnover` |

## Derived records

| Id | Source record | Changed field | Reason | Scenarios served |
|---|---|---|---|---|
| D-1 | every trigger taken from a captured post-mortem day | adds `if_held_source: "shadow_history"` (nothing else changes) | the captures predate the stamp, and `analytics.is_valid_post_mortem_entry` excludes every unstamped entry, so no verbatim day can reach a History total or drilldown | S-2.1, S-2.2, S-2.3, S-2.4, S-2.5, S-2.7, S-2.8, S-6.1 to S-6.4, S-6.6, S-6.7, S-4.2, S-4.13 |
| D-2 | a captured day file | file name date moved to a date relative to the run's UTC today (or to a frozen date) | the History route windows by the date in the file name; the captured June and July dates fall outside any window measured from the run date | all History tests |
| D-3 | one captured trigger | removes `symphony_name` | an exit whose producer wrote no name | S-6.2 |
| D-4 | one captured trigger | removes `time_triggered` | an exit with no trigger time | S-6.3 |
| D-5 | the S-2.1 route payload | `total_saved` removed, or replaced by one of 0.125, -0.125, 0.625, 0.004, -0.004, 1234.5, 0.0 (and one `by_reason` `dollars` likewise) | a missing field; SC-2's two-place tie family and the near-zero pair, which no captured day contains | S-2.4, SC-2 parity |
| D-6 | the `/api/performance` payload for one symphony | `live_metrics.max_drawdown` set to null | the route emits both legs null only when it has no rows; one null leg is the page's input contract (`_PERFORMANCE_NONE_METRICS` is the producer of the all-null case) | S-3.5 |
| D-7 | a symphony's captured `current_return` series | repeated end to end up to `guard_preconditions.N_MIN_OBS + 10` values, returned by the patched replay or shadow accessor | the captured history holds 13 days, below the 40 observations the verdict needs; the real route code and the real math layer then produce the sample row | S-3.9, S-3.10, S-3.13, S-3.14, S-5.2, S-5.11, S-5.12 |
| D-8 | `shadow_history` rows for one symphony on every weekday of two years (and 200 days, and 31 December to 2 January) | `current_return` and `shadow_return` both 0.1 | window tests need a row on each trading day around a cutoff; the captures cover 13 days | S-4.1, S-4.3, S-4.6, S-4.7, S-4.14, S-4.15, S-4.2 |

The seeding helper `seed_shadow_rows` runs the statement `database.record_shadow_observation`
runs, in one transaction; `test_shadow_seeding_helper_writes_the_row_the_real_writer_writes`
proves the two write the same row.
