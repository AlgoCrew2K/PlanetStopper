# Derived Composer series bodies

Each file changes only the named `epoch_ms` entries of one real captured body in
`tests/fixtures/composer/series_captured/` and leaves every other value verbatim (`series` and
`deposit_adjusted_series` are untouched). A derived body exists because no real capture holds the
case: every real point is stamped 00:00 UTC (`series_captured/PROVENANCE.md`).

PM sign-off (suite fixture rule 4, producer owner): ruled on the Gate-2 approach for plan 03 on
2026-10-22 and confirmed in the RED dispatch brief of 2026-10-30. Until that sign-off, the scenario
rows stay held.

| File | Source record | Changed fields | Why | Scenario |
|---|---|---|---|---|
| `q7WQHwLzt49cMgFBzHsX_two_points_one_utc_date.json` | `series_captured/q7WQHwLzt49cMgFBzHsX.json` (30 points) | `epoch_ms[28]` set to 1792540800000 (2026-10-21T00:00:00Z); `epoch_ms[29]` set to 1792612800000 (2026-10-21T20:00:00Z) | two points inside one UTC date, so the later point must win | S-9.4 |
| `q7WQHwLzt49cMgFBzHsX_last_point_in_utc_evening.json` | `series_captured/q7WQHwLzt49cMgFBzHsX.json` (30 points) | `epoch_ms[29]` set to 1792612800000 (2026-10-21T20:00:00Z) | a point stamped in the UTC evening of its date | S-9.5 |

The files were written once by a throwaway script that loaded the capture, replaced the listed
entries and dumped the result with two-space indentation; the same change is checked by
`tests/advisors/test_composer_series_client.py` (`TestDerivedBodies`), which diffs each file against
its source and fails on any other difference.
