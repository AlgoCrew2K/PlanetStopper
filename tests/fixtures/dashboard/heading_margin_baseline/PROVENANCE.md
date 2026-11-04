# Provenance: the computed style of every heading and panel child at the base

Used by `tests/ui/test_heading_margin_shared_browser.py`. One file of data, `baseline.json`; the script
that wrote it, `produce_heading_margin_baseline.py`, is committed beside it.

## Record: `baseline.json` (cases: every page the shared heading rule can reach, plus the two it cannot)

- Source: the real pages loaded in Firefox headless from the in-process server
  (`tests/ui/_browser_server.py`), every request to anything but that server refused, read with
  `getComputedStyle` by `tests/ui/_heading_margin_capture.py`, the module the checking test also calls.
- Ref (`base_sha` in the file): `758f7f9551ff3a15a292debb7bfd4f00b3ecf1e7`. The producer refuses to write
  unless `git diff --quiet 758f7f9551ff3a15a292debb7bfd4f00b3ecf1e7 -- static templates app.py` exits 0 and
  `git status --porcelain -- static templates app.py` is empty.
- Producer call, from the repository root of a tree at that state:
  `DB_PATH=<scratch>/state.db ATLAS_CACHE_DB_PATH=<scratch>/atlas.db python tests/fixtures/dashboard/heading_margin_baseline/produce_heading_margin_baseline.py`.
- Time: `2026-11-04T00:17:53Z` (also `captured_at_utc` in the file). Browser: Firefox `148.0.2`
  (`browser_version` in the file; the check refuses to compare on a different version).
- sha256 of `baseline.json` (LF line ends, written with `newline="\n"`):
  `90b68a5e17eb715724cb21ff1f032b0a8110289313242c62b5faff778a0336e9`.
- Determinism: the producer was run repeatedly on the same tree and two consecutive runs gave records equal
  apart from `captured_at_utc`. Two sources of difference were found and removed before this capture: the
  date the History and Performance windows count back from (now pinned, `WINDOW_ANCHOR`), and the wall-clock
  time a stored Advisor observation shows (masked in each element's text, `_WALL_CLOCK`). A third, found when
  the head was first read inside pytest, is the environment the pages read: the producer now removes every
  credential and model override variable after importing the app (the import reloads the developer's `.env`),
  and the check runs with the suite's model seed off (`unresolved_models`), so both read the same state: no
  Anthropic key (the Frontrunner tab shows its no-key notice in place of its run controls) and the model
  label "model unavailable".
- Views: `/`, `/history`, `/performance`, `/login` on load; `/ai-advisor` on load and once per tab after its
  `button[data-tab]` is pressed (8 tabs); `/settings` on load and once per section after its
  `button[data-section]` is pressed (5 sections). Each at 1440x900 and 375x812, in the light and the dark
  theme: 76 views, 540 elements.
- Elements per view: every element matching `.section-header, .hero-section > *` that the view shows, in
  document order; for `/login` every element of the body (the selector matches nothing there, so the page is
  read whole). Per element: tag, sorted classes, the classes of its parent, whether the shared rule's own
  selector (`.hero-section > .section-header:first-child`) matches it, the first 40 characters of its text
  (a date and its hour masked), the four margin longhands, and the sha256 of its computed style: every
  property but the box-size-resolved ones (`width`, `height`, `inline-size`, `block-size`, `top`, `right`,
  `bottom`, `left`, `inset-*`, `transform-origin`, `perspective-origin`), left out because their resolved
  values follow the box's content and not the cascade the move can change.
- Input state (the same seeding the other shell browser modules use, `tests/ui/_shell_support.seed_every_page`):
  every record of `tests/fixtures/prod_droplet/bot_state_positions.json` verbatim through
  `database.save_state`; six captured post-mortem days of `tests/fixtures/prod_droplet/post_mortems/`, each
  trigger carrying the one added key `if_held_source` the read-time validity guard requires (the derived edit
  already recorded, with its sign-off, in `tests/fixtures/ux_data_truth/PROVENANCE.md`); one pending
  Frontrunner proposal through `database.insert_frontrunner_proposal` over the captured incumbent tree
  `tests/fixtures/advisors/frontrunner/real_tree_07_iaSOOUsmnCJHiZvbrWfs.json`; one weekly-suggestion survivor
  row through `database.insert_advisor_observation`.
- Clocks: the app's clock (`app.datetime`) and the browser's (`context.clock.install`) are both fixed at
  `2026-10-21T11:00:00-04:00`, a Wednesday inside the regular session; `analytics._window_cutoff_date` counts
  back from `2026-07-01`, the day after the last captured post-mortem day, so the exit panels render.
- Scenarios served: the heading margin rows of AC-7 (computed style unchanged on all six pages, at both
  widths, in both themes, per tab and per section; the heading inside `.hp-head`; the dashboard and the login
  page as the guard that a rule added to the shared sheet moved nothing on a page that does not use it).
- Edits: none to the capture. Two input records are not real producer output and are labelled:
  - the post-mortem triggers carry the added `if_held_source` key (derived, recorded elsewhere, signed off);
  - the pending Frontrunner proposal's `metrics_json` is a typed stand-in with the keys
    `_run_build_for_symphony` persists (synthetic, as `tests/fixtures/dashboard/shell_baseline/PROVENANCE.md`
    records; there is no captured queue row, since producing one needs live Composer calls). It only decides
    whether the Frontrunner card renders.
- Sign-off: PM sign-off: session 8055360f, 2026-11-04, producer owner, captured at 758f7f95 by
  `tests/fixtures/dashboard/heading_margin_baseline/produce_heading_margin_baseline.py`.

## Regenerating

A different Firefox version, or a deliberate change of a page that moves the elements, needs a new capture,
which is taken from a throwaway worktree at `758f7f9551ff3a15a292debb7bfd4f00b3ecf1e7` (never from the
tree under test, since the point of the file is what the base rendered). Check that worktree out, copy the
producer and `tests/ui/_heading_margin_capture.py` into it if they are newer there, run the producer call
above, and commit the resulting file. The primitives and shell baselines are not touched by this file.
