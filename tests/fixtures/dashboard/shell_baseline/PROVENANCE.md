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
