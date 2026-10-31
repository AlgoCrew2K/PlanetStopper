# Provenance: time values the Advisor page formats

Used by `tests/analytics/test_format_et_stamp.py`, `tests/ai_advisor/test_cycle5_market_prism_surface.py`,
`tests/ai_advisor/test_overview_sources_carousel.py` and `tests/ai_advisor/test_advisor_lens_as_of_render.py`.
`time_values.json` holds one string per stored time shape the formatter reads. Every value is a stored or
produced time, never a typed one; `tests/analytics/test_format_et_stamp.py::TestFixtureValuesAreProducerOutput`
recomputes each from its source on every run, so a value that drifts from its producer fails there.
Time of record: 2026-10-30. Base: `lane/ux-02` at 4bc4400a.

## Records

### sqlite_created_at (case: SQLite `datetime('now')` text, no zone marker, UTC)
- Source: `created_at` of row id 4025 in `tests/fixtures/retirement/run_summary_captured.json` (a real
  `RETIREMENT_RUN_SUMMARY` row exported read-only from the droplet at 2026-10-22 21:08 UTC, nothing edited).
- Edits: none. Sign-off: not needed.

### iso_z_run_at (case: ISO instant with a Z suffix)
- Source: `run_at` inside the `raw_response` of the same captured row. Edits: none. Sign-off: not needed.

### prism_created_at (case: a stored MARKET_PRISM row's `created_at`, a date in summer time)
- Source: `created_at` of `tests/fixtures/ai_advisor/rf1_lens_pipeline_row77.json` (captured read-only from the
  production state database, `advisor_observations` id 77). Edits: none. Sign-off: not needed.

### autotune_run_timestamp (case: ISO with a six-digit fraction and Z, across the ET date boundary)
- Source: the writer's own expression, `datetime.now(UTC).isoformat().replace("+00:00", "Z")`
  (`autotuner.py`, `run_timestamp`), evaluated at the pinned instant 2026-10-17 01:08:12.048917 UTC. The
  instant is the one the UX audit saw on the droplet's autotune card. No database row was typed.
- Validator: the test evaluates the same expression and compares. Edits: none. Sign-off: not needed.

### lens_cache_captured_at (case: ISO with a fraction and a `+00:00` offset)
- Source: the writer's own expression, `datetime.now(UTC).isoformat()`
  (`ai_advisor.persist_market_lens_cache`, `captured_at`), evaluated at the pinned instant
  2026-10-22 18:50:25.123456 UTC.
- Validator: the test evaluates the same expression and compares. Edits: none. Sign-off: not needed.

### rss_published (case: a feed publish time with no zone marker)
- Source: the output of the real producer `advisors.news_corpus._fetch_rss_feed` over the captured Federal
  Reserve RSS body `tests/fixtures/ai_advisor/fed_press.xml` (captured 2026-06-18, status 200, per
  `news_corpus_feeds_provenance.json`), first item, `published` field. The producer reads the item's
  `pubDate` ("Thu, 18 Jun 2026 13:00:00 GMT") and writes it zone-less.
- Validator: the test runs the producer with `requests.get` answering that body and compares. Edits: none.
  Sign-off: not needed.

### gdelt_seendate (case: GDELT's compact instant, `YYYYMMDDTHHMMSSZ`)
- Source: `seendate` of the first article in `tests/fixtures/math/gdelt_artlist_events_captured.json`, a
  schema-derived capture (its own provenance note says the live capture was rate limited and the shape is
  the GDELT 2.0 DOC API's). The value is a sentinel in the producer's format. Edits: none.
- Sign-off: not needed (no derived record); the weaker provenance class is the capture's own.

# Retirement tab records

Used by `tests/app/test_retirement_tab_render.py` and `tests/app/test_retirement_tab_client.py` through
`tests/app/_retirement_tab_world.py`. No file of data is stored for these: each record is built at test time
from a capture or a real producer, as follows.

## Captured records (nothing edited)

### Captured `ok` run summary (case: a successful run with one exclusion, 4 pairs too thin)
- Source: `tests/fixtures/retirement/run_summary_captured.json` (see its own PROVENANCE.md): one real
  `RETIREMENT_RUN_SUMMARY` row exported from the droplet. Written back through
  `database.insert_advisor_observation`. Cases served: every tile, the stamp, the basis line, the exclusion
  sentence for `series_too_short`, the earlier-run note's date.
- Roster: `tests/fixtures/prod_droplet/bot_state_positions.json`, verbatim, through `database.save_state`;
  the excluded symphony's live display name comes from it. With the excluded id left out of the roster, the
  entry reads the hash (case: a symphony no longer in the live state). No edit of a record.

## Producer outputs (the real function over real inputs)

### `series_unavailable` summary with `fetch_error: HTTP 429` exclusions
- Producer: `advisors.retirement_recommender.build_recommendations_with_summary(source="composer")` over the
  first three captured symphonies that carry an account, with `requests.get` (the network boundary) answering
  HTTP 429 and `time.sleep` disabled, at the pinned instant 2026-10-22T18:50:08Z (the run clock of the
  recommender is pinned, as the captured row's `run_at` shows).
### `no_account` and `fetch_error` together
- Same producer; one of the three symphonies has its `account` key removed from the roster entry before
  the run (a roster input, not a record edit of a captured summary).
### `insufficient_symphonies`
- Same producer over a roster of one captured symphony.
### `shadow_series_unavailable`
- Same producer with `source="shadow"` over three captured symphonies that have no shadow rows.
### `internal_error`
- `app._retirement_internal_error_summary("ConnectionError")` at the pinned instant above.
### `ok` summary with recommendations (and the cards)
- `app._retirement_recommender_tick_worker()`: the real recommender over the seeded deterministic series of
  `tests/advisors/test_retirement_recommender_composer_source.py` (`_series_cluster_scenario`, three
  symphonies whose composite order gives two candidates), the real explainer on a real
  `anthropic.Anthropic` client over `tests/_anthropic_http_fake.py` answering with a captured reply, and
  the real writers. Approvals and rejections are the real routes `POST /ai-advisor/retirement/approve` and
  `/reject`. Series values are the recommender tests' own labelled constructions (the captured series are not
  correlated enough to produce a recommendation); they flow through every real gate.
### `ok` summary stamped at 2026-10-23T02:30:00Z (case: the ET date differs from the UTC date)
- Same producer over the `_series_pair_scenario` series, run clock pinned to that instant.

## Derived records (one captured record, named fields changed) - signed off by the PM

Each is the captured summary with only the named field changed; every other field is verbatim.
Scenario rows that use one stay as tests.

| Derived record | Source | Changed field(s) | Why | Scenario |
|---|---|---|---|---|
| D-1 | captured summary | `blocked_stress_too_thin` -> 0, `blocked_uncertainty` -> 10 | no pair too thin: the stress minimum is not stated | S-4.14 |
| D-2 | captured summary | one key removed per case: `symphonies_with_series`, `series_days_min`, `pairs_screened`, `recommendations`, `blocked_uncertainty`, `blocked_stress_too_thin`, `blocked_stress_below_threshold`, `blocked_no_candidate` | the missing-field edge of a real record | S-4.13 |
| D-3 | captured summary | the same eight keys set to null | the null edge | S-4.13 |
| D-4 | captured summary | `symphonies_without_series[0].reason` -> "unexpected <b>reason</b> & more" | a reason the mapping lacks, with markup | S-7.3 |
| D-5 | captured summary | `symphonies_without_series[0].reason` -> "", null, 5 or a list | an empty or non-text reason | S-7.4 |
- Sign-off: D-1, D-2, D-3, D-4 and D-5 signed off by the PM (coordinator ruling, 2026-10-30).


# Autotune panel rows

Used by `tests/app/test_advisor_autotune_panel_bounds.py` and `tests/ui/test_advisor_autotune_browser.py`.
No file of data is stored: each row is written at test time by the real writer
`database.save_autotune_run` and read back through the real route `GET /api/autotune-runs`; the
tests assert against that route's own rows (the partition is recomputed from them).

- Symphony keys: `database.normalize_name` of the display names in
  `tests/fixtures/prod_droplet/bot_state_positions.json` (11 captured symphonies; case: up to 11 symphonies
  with a live name). Beyond 11, the plain keys `extra-symphony-<n>` that no live state holds (case: 12 to 15
  symphonies, a card that prints the stored key). These are keys of a TEXT column, not captured values.
- Scores (`naive_sharpe`, `selection_tstat`) are labelled constructions that give each run its own value; no
  assertion copies one.
- Decisions: the writer's own literals `Reverted to Fallback` (never adopted; yields the streak line) and
  `Applied`.
- Time with a fraction: `autotune_run_timestamp` of `time_values.json` above (case: the writer's six-digit
  fraction and Z), the same record the formatter tests recompute from its producer expression.
- Edits: none. Derived records: none. Sign-off: not needed.

# Observation cases

Used by `tests/app/test_advisor_observation_status_lines.py`,
`tests/database/test_017_advisor_observations.py` and `tests/ui/test_advisor_observations_browser.py`,
through `tests/_advisor_observation_records.py` and `observations.json` (cases by name, each a source record
and the fields changed). A row is written by the real writer `database.insert_advisor_observation`, which is
the runtime validator of its shape. The writer stamps `created_at` with the database clock, so the helper
sets it afterwards with one `UPDATE` to a stated increasing sequence from 2026-10-20 14:00:00 (the only
post-writer change; a chosen clock, never an expected value copied from a run).

## Records used verbatim

- `guardrail_clear` (case: an Overfitting Conscience row, verdict CLEAR): `example_rows[0]` of
  `tests/fixtures/ai_advisor/advisor_observations_shape.json` (schema-derived from
  `migrations/017_advisor_observations.sql`; values are sentinels, per that file).
- `guardrail_watch` (case: a Spec Critic row, verdict WATCH): `example_rows[1]` of the same file.
- `feature_off_stub` (case: the Divergence Explainer's feature-off stub): the output of the real producer
  `advisors.divergence_explainer.compute_divergence_explainer_observation({'id': 7}, None,
  second_window_enabled=False)`, called at test time.

## Derived records (one source record, named fields changed) - signed off by the PM

The PM signed these off in the brief for the round that added them (2026-10-30).

| Case | Source | Changed field(s) | Why | Serves |
|---|---|---|---|---|
| `feature_off_stub_under_a_shown_role` | `feature_off_stub` | `advisor_role` -> `OVERFITTING_CONSCIENCE` | the Divergence Explainer role is not on the page; the stub rule is role-blind, so the stub must sit under a shown role to prove it is left out of the line and the list | stub in neither total nor rows |
| `empty_verdict`, `null_verdict` | `guardrail_watch` | `verdict` -> `""`, `null` | a producer row whose verdict is empty or NULL (no captured row of either kind) | "verdict not recorded" |
| `markup_verdict` | `guardrail_watch` | `verdict` -> `<script>alert(1)</script>` | markup in the one new place a verdict is printed | escaping |
| `add_candidate_role` | `guardrail_clear` | `advisor_role` -> `ADD_CANDIDATE` | a role the label table lacks | role labels |
| `non_json_raw_response` | `guardrail_clear` | `raw_response` -> `not json at all` | the writer accepts a pre-serialised string as is | count keeps it |
| `json_array_raw_response` | `guardrail_clear` | `raw_response` -> `[1, 2]` | valid JSON that is not an object | count keeps it |
| `feature_flag_on_stub` | `feature_off_stub` | `raw_response` -> `{"feature_flag": "on"}` | NOT_APPLICABLE with the flag not off | count keeps it |
| `stub_without_flag` | `feature_off_stub` | `raw_response` -> `{}` | NOT_APPLICABLE without the flag | count keeps it |
| `real_verdict_with_flag_off` | `guardrail_clear` | `raw_response` -> `{"feature_flag": "off"}` | the flag with a real verdict | count keeps it |
| `null_verdict_with_flag_off` | `guardrail_watch` | `verdict` -> `null`, `raw_response` -> `{"feature_flag": "off"}` | a NULL verdict must not drop under the stub rule | count keeps it |

# Prism block records

Used by `tests/app/test_advisor_overview_order.py` and `tests/ui/test_advisor_overview_order_browser.py`
through `tests/_advisor_overview_prism.py`.

### prism_block_source_baseline.txt (case: the Market Prism block as it stood before it moved)
- Source: `templates/ai_advisor.html`, from the line holding `data-testid="market-prism-block"` to the closing
  `{# /market-prism-block #}` line, each line stripped, empty lines dropped.
- Capture: `python tests/fixtures/ai_advisor/ux_overhaul_02/capture_prism_block_source.py`, run on the
  unmodified template at lane/ux-02 commit 9f0cc091f109f0b9cb17410f1654663c5af71e55 (the commit before this
  round's RED; its template is that of e854c73c). Time of record: 2026-10-30. Nothing edited.
- Re-captured 2026-10-31 at 6fdc1fb7 (same script, nothing edited) after the app-shell skeleton work (dd532212)
  added shared shell classes to two elements of the block: the block's root `div` gained `hero-section`
  (`class="hero-section prism-block"`) and the title span gained `section-header`
  (`class="section-header prism-block-title"`). A line diff of the old and new baseline shows exactly those
  two lines changed and no text, attribute other than `class`, testid or Jinja expression changed; the
  guard still pins every other line of the block.

### MARKET_PRISM row (case: a stored nightly read the block renders)
- Source: `rf1_lens_pipeline_row77.json` (captured read-only from the production state database,
  `advisor_observations` id 77) for `per_lens_digest` and the time; `overall_sentiment` and
  `sentiment_rationale` are the answer of the real producer function
  `advisors.lens_pipeline._synthesize_via_claude({}, digest, 0)` (its no-available-lens branch), called at test
  time; the other keys follow the producer's `raw_response` schema (`run_ts`, `run_id`, counts, `sources`).
- Derived: the captured row77 holds only the digest; the keys above were added around it. Signed off by the PM
  in the brief for this round (2026-10-30). Written through `database.insert_advisor_observation`.
