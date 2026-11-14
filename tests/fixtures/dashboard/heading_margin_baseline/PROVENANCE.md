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

## Revision: re-recorded at the page-explainers-and-density fix head

The fix round re-arranged the dashboard hero, the History chart note and the Advisor Overview, Retirement and
Frontrunner layouts, so the element set of 20 comparison cases changed on purpose and `baseline.json` was
written again.

- `base_sha` in the file, `BASE_SHA` in the producer and the producer's refusal now name
  `6b63f53e0d718e230878df900a4aabc72c5b67ed`, the last production commit of the fix round; the producer refuses
  unless `git diff --quiet` against it over `static templates app.py` exits 0 and those paths are clean (it ran in
  a detached worktree with no `.env` at head `64dec7c95d19481686a6ac76cf47d94239833d0d`, whose `static`,
  `templates` and `app.py` equal that commit; the later commits are test files). Run twice in separate
  processes: the two records were equal apart from `captured_at_utc`.
- Producer call and Firefox (`148.0.2`) as above; 76 views, 674 elements (was 686).
- Compared with the previous record, view by view: the 76 view keys are the same; 20 views differ (Advisor 12,
  Dashboard 4, History 4), all in their element set or an element's text or computed style; in every one of the 76
  views the section headings (`.section-header`) are equal in tag, classes, margins and text to the previous
  record, and the margins of the elements the shared rule matches are equal.
- sha256 of `baseline.json` (LF line ends): `64c8f9ec615696324a3d3c2fd0898f49b68333c4b6d63727d46e1f327952668e`.
- PM sign-off: not needed (producer output; the only differences are the fix round's own re-arranged elements).

## Revision: re-recorded at the aligned-tables-and-quiet-copy head

The slice removed and re-arranged elements on every page (the captions grids, the two-group risk table, the far-end
stamps, the one Glossary per page, the aligned observation lines), so the element set of the History, Performance,
Advisor and Settings comparison cases changed on purpose and `baseline.json` was written again.

- `base_sha` in the file, `BASE_SHA` in the producer and the producer's refusal now name
  `b8a49292015d4377a7c8fcfacd72acaac6fabf29`, the last production commit of the slice; the producer refuses unless
  `git diff --quiet` against it over `static templates app.py` exits 0 and those paths are clean (it ran in the
  slice's worktree at head `585617cacc1467973cae42076dd9fda4b7926559`, whose `static`, `templates` and `app.py` equal
  that commit; the later commit is a test file). The worktree holds a `.env`; the producer removes the credential
  variables and the module values the import set (see "Re-capture without `.env`"), so the record does not read it.
  Run twice in separate processes: the two records were equal apart from `captured_at_utc`.
- Producer call and Firefox (`148.0.2`) as above; 76 views, 546 elements (was 674).
- Compared with the previous record, view by view: the 76 view keys are the same; 64 views differ (Advisor 36,
  Settings 16, Dashboard 4, History 4, Performance 4), all in their element set or an element's text or computed
  style. Every heading of the previous record is present with the same tag, classes and four margins; the Advisor
  Overview views (load and the Overview tab, 8 views) hold one more `section-header` (the Retirement producer
  label, margins the same as the other producer labels) and no view lost one. The margins of the elements the
  shared rule matches are equal in every view.
- sha256 of `baseline.json` (LF line ends): `37655d9961540428270676d8a41a7960e5dab417eb435b380d37cfa53961d434`.
- PM sign-off: not needed (producer output; the only differences are the slice's own added and re-arranged elements).

## Revision: re-recorded at the aligned-tables-and-quiet-copy fix round R1 merged head

The fix round's two tracks (Advisor pages and copy; dashboard, Performance, History and header layout) re-arranged
elements on four pages, so the element set or computed style of 28 comparison cases changed on purpose and
`baseline.json` was written again.

- `base_sha` in the file, `BASE_SHA` in the producer and the producer's refusal now name
  `f869256c56da2fd4c5273c34a34f5c90803af628`, the merge of the two tracks, which is the last commit that touched
  `static`, `templates` or `app.py` (the producer ran at that very head, so the diff over those paths is empty and
  the paths are clean; the commit that records this file is a fixture-only commit). The worktree holds a `.env`;
  the producer removes the credential variables and the module values the import set, so the record does not read
  it. Run twice in separate processes: the two records were equal apart from `captured_at_utc`.
- Producer call and Firefox (`148.0.2`) as above; 76 views, 542 elements (was 546).
- Compared with the previous record, view by view: the 76 view keys are the same; 28 views differ (Advisor 20,
  History 4, Dashboard 2, Performance 2), all in their element set or an element's text, margin or computed style.
  No heading was lost: every `.section-header` of the previous record is present with the same tag, classes, four
  margins and text, and the margins of the elements the shared rule matches are equal in every view.
- Element-set changes, each by R1 fix: Dashboard load gains the `dollar-lines` element (the stacked dollar lines,
  track B `abe01b19`); History load gains the `hp-head` of "Today's exits" and loses the `hp-more` exit-reasons
  disclosure (track B, the exit reasons text moved out of a disclosure); Advisor Chat loses the
  `chat-default-title` element (track A `2747483b`, the contained result cards and stylesheet cleanup). The rest
  are computed-style changes with the same element set: the Advisor `cap-nav-wrap` and `obs-lines` rows (track A
  stylesheet cleanup), the History `hp-*` toolbar, scroll and note rows, the Performance `hp-figs`, `hp-toolbar`
  and `metrics-caption` rows, and two margin changes outside the shared rule's reach (Performance `perf-captions`
  bottom 4px to 12px, `insufficient-banner` bottom 8px to 16px, track B layout).
- sha256 of `baseline.json` (LF line ends): `a2dc6dceb9c5744b7915d29d5318c2c86968e03b67575bd07da81933c1bddfe3`.
- PM sign-off: not needed (producer output; the only differences are the fix round's own re-arranged elements).

## Revision: re-recorded at the aligned-tables-and-quiet-copy fix round R2 head

The final fix round (observation history columns, stacked result-card tracks, dead shared rules removed) changed
`templates/ai_advisor.html`, `history.html`, `performance.html`, `static/layout.css` and `static/ai_advisor.js`, so
`baseline.json` was written again to pin the record to the production state it is read against.

- `base_sha` in the file, `BASE_SHA` in the producer and the producer's refusal now name
  `08e28b452ba6d3d2396c57873c26b29de380d21a`, the last commit that touched `static`, `templates` or `app.py` (the
  producer ran at the merge head `b442890f8997e28b08fd441af952df82f1dd966c`, whose `static`, `templates` and `app.py`
  equal that commit and are clean). The worktree holds a `.env`; the producer removes the credential variables and
  the module values the import set, so the record does not read it. Run twice in separate processes: the two records
  were equal apart from `captured_at_utc`.
- Producer call and Firefox (`148.0.2`) as above; 76 views, 542 elements (as before).
- Compared with the previous record, view by view: the 76 view keys are the same and no view differs in any element,
  text, margin or style digest; only `base_sha` and `captured_at_utc` changed. No heading was lost and the shared
  rule's margins are equal in every view. The round's changes lie in elements this record does not read (the opened
  observation History table, the result cards, the dead rules).
- sha256 of `baseline.json` (LF line ends): `0e549263af97c2c4cda46e207490219777489c6ec23024685333fed2c1cfc877`.
- PM sign-off: not needed (producer output; no element differs).

## Regenerating

A different Firefox version, or a deliberate change of a page that moves the elements, needs a new capture,
which is taken from a throwaway worktree at `758f7f9551ff3a15a292debb7bfd4f00b3ecf1e7` (never from the
tree under test, since the point of the file is what the base rendered). Check that worktree out, copy the
producer and `tests/ui/_heading_margin_capture.py` into it if they are newer there, run the producer call
above, and commit the resulting file. The primitives and shell baselines are not touched by this file.
