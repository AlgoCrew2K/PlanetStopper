# Provenance: the computed style of every heading and panel child at the base

Used by `tests/ui/test_heading_margin_shared_browser.py`. One file of data, `baseline.json`; the script
that wrote it, `produce_heading_margin_baseline.py`, is committed beside it.

## Re-capture without `.env` (changed field: the Composer-key state of three Advisor tabs)

The record above was first written on a machine whose `.env` held a Composer key: importing the app
loads that file, `alpha_bot_execution.COMPOSER_KEY_ID` and its neighbours keep what it held, and the
Asset Swaps, Logic Changes and Strategy Builder tabs test those values (`_has_composer_key()` in each
engine), so they rendered their key-present form. The producer removed the environment variables but
not those module values, so the record, and the check that reads the head with none, disagreed in a
checkout with no `.env` (four Advisor cases of `test_heading_margin_shared_browser.py` failed there).
`tests/ui/_advisor_browser_support.restore_credential_free_module_state` now puts those values back
to what an import without `.env` gives; the producer calls it with `setattr` after it removes the
variables, and `advisor_server` calls it with `monkeypatch.setattr`. The test that finds the full
set of such values by behaviour is `tests/ui/test_advisor_browser_credential_free_state.py`.

The record was written again by the fixed producer, in a throwaway worktree at the base commit (the
producer refuses anywhere else) with the fixed producer and `tests/ui/_advisor_browser_support.py`,
`tests/ui/_heading_margin_capture.py` and `tests/ui/_shell_support.py` copied in from the branch (the
base predates the last two and their `/login` readiness rule). Run twice, in a worktree with no `.env`
and in one with a `.env` holding invented credential values (synthetic, none real): the two records were
equal apart from `captured_at_utc`, and the first of them is the committed file. It differs from the
previous record in 12 views (the three tabs above at 1440 and 375, light and dark) and four elements
(540 to 536): the tabs now show their `no-api-key-title` and `no-api-key-body` notice in place of the
key-present form, the state the check reads. No other view differs. The Time and sha256 above are the
new record's.

## Record: `baseline.json` (cases: every page the shared heading rule can reach, plus the two it cannot)

- Source: the real pages loaded in Firefox headless from the in-process server
  (`tests/ui/_browser_server.py`), every request to anything but that server refused, read with
  `getComputedStyle` by `tests/ui/_heading_margin_capture.py`, the module the checking test also calls.
- Ref (`base_sha` in the file): `758f7f9551ff3a15a292debb7bfd4f00b3ecf1e7`. The producer refuses to write
  unless `git diff --quiet 758f7f9551ff3a15a292debb7bfd4f00b3ecf1e7 -- static templates app.py` exits 0 and
  `git status --porcelain -- static templates app.py` is empty.
- Producer call, from the repository root of a tree at that state:
  `DB_PATH=<scratch>/state.db ATLAS_CACHE_DB_PATH=<scratch>/atlas.db python tests/fixtures/dashboard/heading_margin_baseline/produce_heading_margin_baseline.py`.
- Time: `2026-11-04T16:45:32Z` (also `captured_at_utc` in the file); re-captured (see "Re-capture without `.env`" below). Browser: Firefox `148.0.2`
  (`browser_version` in the file; the check refuses to compare on a different version).
- sha256 of `baseline.json` (LF line ends, written with `newline="\n"`):
  `df8f35482e9a291108a21b1fe45002978a9df15ebe316fc3723cb246b3663690`.
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
  theme: 76 views, 536 elements.
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

## Revision: re-recorded at the page-explainers-and-density head (AC-1 and AC-6)

The page descriptions, term disclosures and shared-line text (AC-1, AC-2, AC-8) and the denser layout
(AC-6) add and re-arrange elements on every page, so the element set of 21 comparison cases changed on
purpose and `baseline.json` was written again.

- `base_sha` in the file, `BASE_SHA` in the producer and the producer's refusal now name
  `ff188e270efab9b4e1a39103c27fc20e2eb5dcf5`, the last production commit of the Advisor cycle; the producer
  refuses unless `git diff --quiet` against it over `static templates app.py` exits 0 and those paths are
  clean (it ran at head `e3c82774c28bb80edcf69714494d3b0b27e48a6e`, whose `static`, `templates` and `app.py` equal
  that commit; the later commits are test files).
- Producer call and Firefox (`148.0.2`) as above; 76 views, 686 elements (was 536).
- Compared with the previous record, view by view: the 76 view keys are the same; 72 views differ, all in
  their element set or an element's text or computed style (Advisor 36, Settings 24, Dashboard 4, History 4,
  Performance 4); in every one of the 76 views the section headings (`.section-header`) are equal in tag,
  classes, margins and text to the previous record, and the margins of the elements the shared rule matches
  are equal. So the shared first-heading rule moved nothing; only the added and re-arranged elements differ.
- sha256 of `baseline.json` (LF line ends): `cde6821ffa2a6c12b535bbb77e3f8387aaf02889fe71ddf9ba4b274e3e8009dc`.
- PM sign-off: not needed (producer output; the only differences are the plan's own added and re-arranged elements).

## Regenerating

A different Firefox version, or a deliberate change of a page that moves the elements, needs a new capture,
which is taken from a throwaway worktree at `758f7f9551ff3a15a292debb7bfd4f00b3ecf1e7` (never from the
tree under test, since the point of the file is what the base rendered). Check that worktree out, copy the
producer and `tests/ui/_heading_margin_capture.py` into it if they are newer there, run the producer call
above, and commit the resulting file. The primitives and shell baselines are not touched by this file.
