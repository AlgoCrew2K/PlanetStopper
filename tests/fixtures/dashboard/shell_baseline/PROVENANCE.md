# Provenance: the shell work's base capture of the pages

Used by `tests/ui/test_dashboard_primitives_shared.py`, `tests/ui/test_shell_header_browser.py`,
`tests/ui/test_page_skeleton_browser.py` and `tests/ui/test_narrow_close_browser.py`. One file of data,
`baseline.json`; the script that wrote it, `capture_shell_baseline.py`, is committed beside it.

## Record: `baseline.json` (cases: the dashboard outside the header; the header height and table scroll bars at 1440; Settings' Save request)

- Source: the real `GET /` route rendered by the Flask test client, and the stylesheets the returned markup
  links or inlines, read in document order (`tests/ui/_dashboard_primitives_capture.py`,
  `tests/ui/_shell_capture.py`); and the five pages loaded in Firefox headless at 1440x900 from the
  in-process server (`tests/ui/_browser_server.py`), every request to anything but that server refused.
- Producer call: `python tests/fixtures/dashboard/shell_baseline/capture_shell_baseline.py` with `DB_PATH`
  and `ATLAS_CACHE_DB_PATH` set to scratch files. The script refuses to run when anything under
  `templates/`, `static/` or `app.py` differs from `HEAD`, and renders `GET /` twice and refuses to write
  when the element structure differs between the two.
- Base SHA: `f9cb367668872b16d22877e6b7490c0950a12cba` (branch `lane/ux-04` before any edit of the shell
  work; also written into the file as `base_sha`). Time: see `captured_at_utc` in the file. Browser:
  Firefox (version per page in the file).
- Input state (the same seeding the checking tests use, `tests/ui/_shell_support.seed_every_page`):
  every record of `tests/fixtures/prod_droplet/bot_state_positions.json` verbatim through
  `database.save_state`; six captured post-mortem days of `tests/fixtures/prod_droplet/post_mortems/`,
  each trigger carrying the one added key `if_held_source` the read-time validity guard requires (the
  derived edit already recorded, with its sign-off, in `tests/fixtures/ux_data_truth/PROVENANCE.md`);
  one pending Frontrunner proposal through `database.insert_frontrunner_proposal` over the captured
  incumbent tree `tests/fixtures/advisors/frontrunner/real_tree_07_iaSOOUsmnCJHiZvbrWfs.json`; one
  weekly-suggestion survivor row through `database.insert_advisor_observation`.
- Contents: `dashboard.structure_outside_top_bar` (tag and sorted class tokens of every body element of
  `GET /` outside the shared header), `dashboard.resolved` (declarations per `<at-rule>||<selector>` for the
  selectors naming a dashboard primitive), `pages_1440` (per page: header height, per table in document order
  whether its holding box scrolls sideways and whether it is wider than the viewport; for `/settings` the
  request Save sent after the first algorithm parameter was edited).
- Scenarios served: the dashboard-unchanged rows (S-1.7, S-2.6 rules half), the one-row header at 1440
  (S-3.5), Save's request (S-1.5), no new scroll bar at 1440 (S-5.10).
- Edits: none to the capture. Two input records are not real producer output and are labelled:
  - the post-mortem triggers carry the added `if_held_source` key (derived, recorded elsewhere, signed off);
  - the pending Frontrunner proposal's `metrics_json` is a typed stand-in with the keys
    `_run_build_for_symphony` persists (synthetic, the same shape as the dict
    `tests/app/test_frontrunner_proposal_identity_template.py` uses; there is no real captured queue row:
    producing one needs live Composer calls). It only decides whether the Frontrunner card renders. Sign-off:
    pending, the PM signs off.
- Sign-off: pending for the synthetic metrics stand-in; not needed for the rest.

## Revision: `dashboard.structure_outside_top_bar` re-captured for the render-follow templates

The dashboard grew from 965 to 1014 elements outside the header when the refresh-timing 03 templates landed (`e972b179`), so the structure pin was re-captured; `base_sha`, `pages_1440` (the header heights, the table scroll boxes, Save's request) and `dashboard.resolved` are the shell work's base and are untouched.

- Producer call: `python tests/fixtures/dashboard/shell_baseline/capture_shell_baseline.py --structure-only` with `DB_PATH` and `ATLAS_CACHE_DB_PATH` set to scratch files, at head `cdcba823020f44e4ba10026a6b9f40611680f6f8` (templates, static and `app.py` clean). The flag runs the script's own render and reading code (twice, as always, and stops if the two renders differ), replaces only the structure list, and records `structure_revision` (head, time, element counts) in the file. It stops without writing when the resolved primitive rules differ from the recorded ones: they do not (23 selectors, unchanged).
- Net change, 965 to 1014: 50 elements added, 1 removed.
- Removed (1): the `<script src="tweaks.js">` that stood first in the body, outside the header. `e972b179` moved it to the page head so the stored theme is applied before the first paint and again on every poll.
- Added, always rendered where the page used to render one of two alternatives, so a poll can show one and hide the other (`hidden`): the Max DD row's insufficient-history label (`span`) and alpha delta (`span` `vs-delta`); one `cfg-alpha-badge` span on each of the 11 symphony cards (the card's insufficient-history label and badge are both in the markup now); the hero chart's `hero-window-no-data` span (`legend-as-of`).
- Added, the Sleeves panel's markup: the two inert `<template>` elements `sleeve-card-template` and `sleeve-rule-row-template` with their contents (a card and a rule row rendered from empty values, so the poll's new rows come from the same markup as the page's), and 11 elements (6 `span`, 4 `div`, 1 `section`) in the CVaR diagnostic and Sleeves panels after the standby grid (the record holds tag and class tokens only, so the diff cannot say which element of the two panels each one is). One more added entry, a `cards-grid` `div`, is an alignment shift of the existing grid, not a new grid. The label above is a bare `span`, which the position-based diff aligns near the hero legend.
- Each added element carries no class that any primitive selector resolves differently: the resolved-rule record is unchanged.

## Revision: `dashboard.resolved` for the phone layout (UX review fixes)

The UX review fixes change two primitives on purpose, so `dashboard.resolved` was re-captured; `base_sha`, `dashboard.structure_outside_top_bar` (the body structure is checked against it with the changes the review fixes made listed in the test) and `pages_1440` are the shell work's base and are untouched.

- Producer call: `python tests/fixtures/dashboard/shell_baseline/capture_shell_baseline.py --resolved-only` with `DB_PATH` and `ATLAS_CACHE_DB_PATH` set to scratch files, at head `38b0cbe8007ec7442cfb127114a8ff58d79b2a24` (templates, static and `app.py` clean). The flag runs the script's own render and reading code (twice, as always), replaces only `dashboard.resolved`, and records `resolved_revision` (head, time, selector counts before and after: 23 to 24).
- What differs from the base, read from `git diff` of the file: a new rule `@media (max-width: 700px) .hero-section { padding: 18px 16px 16px; margin-bottom: 16px }`, and `.status-pill` gains `white-space: nowrap`. No other selector's declarations changed.
- Sign-off: not needed (producer output; the two differences are the fixes' own).
