# Approach: Retire the dead state-poll table render and define the shared heading margin rule once
Spec: feature-plans/dead-table-partial-and-header-css.md
Ref: AlphaBotPM@758f7f9551ff3a15a292debb7bfd4f00b3ecf1e7

## Architecture

Every `path:line` below is at AlphaBotPM@758f7f95. The design follows the plan's proposed defaults for Q-1 to Q-7, which the PM approved, and G-15 as the writer gave it. No model, migration, route, configuration key or dependency is added; the change is two deletions and one move, plus the test harness that proves nothing else moved.

### State poll (`GET /api/state`, `app.py:5428-5967`)

Close-snapshot branch (`app.py:5492-5650`):
- Delete the sort parameters and the status and exit-return ranks (`app.py:5513-5578`: `_sort_col`, `_sort_dir`, `_is_desc`, `_frozen_status_rank` at `:5518`, `_frozen_exit_ret` at `:5531`, the six non-name branches). Keep one in-place name sort over the same lists: `for _syms in _snap_accounts_map.values(): _syms.sort(key=lambda s: (s.get("name") or s.get("id", "")).lower())`. The key is the ref's default branch (`app.py:5576-5578`) with `reverse` fixed at False, which is what the ref computes when no `sortDir=desc` is sent. The sort stays in place, on the snapshot's own lists, because the view is held per snapshot and shared with `GET /` (`app.py:4706-4732`; `_snapshot_without_symphonies` keeps the snapshot's own entry dicts, `app.py:3905-3921`), so moving it to a copy would change what a later `GET /` reads (S-3.7, DC-2). It stays before `_inject_guard_alpha(_state)` and `_normalize_frozen_symphonies(snapshot)` (`app.py:5595-5597`), as at the ref; the key reads no defaulted field, so the order of those calls does not change the result.
- Delete the account-label read (`app.py:5582-5592`), the table render and its fallback string (`app.py:5599-5610`) and the `"html"` key (`app.py:5640`). The labels fed only the template; the same label rule survives in its one other home, `advisors/tenure_review_runner.configured_account_labels` (pinned by `tests/advisors/test_tenure_review_runner.py:546-580`), so this removal also removes a copy.

Live branch (`app.py:5700-5955`):
- Keep the name-guarded `symphony_keys` list (`app.py:5722-5724`) and the loop that stamps `id` and `normalized_name` on each entry (`app.py:5726-5733`, PD-2), with the `accounts_map` grouping removed from it: the loop becomes `for k in symphony_keys: sym = state_data[k]; sym["id"] = k; sym["normalized_name"] = database.normalize_name(sym.get("name", ""))`. The live `accounts_map` is never returned (`app.py:5928-5955`), so its grouping and sort are dead.
- Delete the account labels (`app.py:5735-5745`), the sort (`app.py:5747-5809`), the render and its fallback (`app.py:5891-5901`) and the `"html"` key (`app.py:5938`). Keep `env_vars` (`app.py:5709`) for `live_mode` and `execution_start_time`, and keep `data_as_of` (`app.py:5889`), which the body serves.
- Comments that name the table are reworded to what the code does: `app.py:5701` ("cards (html)"), `app.py:5718` ("Render HTML for UI"), `app.py:5855` ("so the template can render them": the figures are on the served entries and cards), and the defaults' comment `app.py:4439-4440` (the defaults now exist for the served entries and cards; the values are unchanged, PD-1, and NB-16 tracks the fabricated zeros).

Every other branch and return path (`app.py:5654-5699` waiting, `app.py:5956-5959` error, the auth hook `app.py:280-316`) is untouched. No new helper is added: the one surviving sort is a single statement, and a search for an existing name-order helper (`grep -n "sort(\|sorted(" app.py` filtered for name keys) finds only unrelated keys (`app.py:7484`, `:8915`, `:9625`, `:9638`).

### Dead template and its pins
- Delete `templates/table_partial.html`. No other file under `app.py`, `static/` or `templates/` names it except the two `render_template` calls removed above and the header comment `static/settings-modal.js:4-5`, which is reworded to name only the card gear (`templates/index.html:1711-1714`).
- Tests are retired or rewritten under Q-1's default; the per-test disposition is in Per-Cycle Detail C1.

### Heading margin rule
- Add `.hero-section > .section-header:first-child { margin-top: 0; }` to `static/layout.css` immediately after the `.section-header` rule (`static/layout.css:150-155`), with a one-line comment that it is the zero top margin of a panel's first heading, shared by every page. That position is inside the primitive suffix (`tests/ui/test_dashboard_primitives_shared.py:241-250`) and after the rule it refines, so source order agrees with specificity (0,3,0 over 0,1,0).
- Delete the four inline copies: `templates/ai_advisor.html:50`, `templates/history.html:40`, `templates/performance.html:44`, `templates/settings.html:33`. The comments above them (`templates/history.html:37-39`, `templates/performance.html:41-43`, `templates/settings.html:29-32`) still describe the page-scoped classes that follow and stay.
- The cascade analysis the spec audit recorded (spec-audit section F) holds at the ref: no rule on any page with specificity 0,3,0 or higher sets a margin on these headings, every page links `static/layout.css` before its inline block, and the dashboard and login render no element the selector matches.

### Test harness (new, test-only)
- `tests/app/_state_poll_worlds.py` (new): one named builder per scenario world, composing the existing builders instead of writing new seeding: `seeded_world` and `build_world` (`tests/app/_refresh_timing_state_world.py:360`, `:390`), the stalled-morning world (`tests/app/test_open_market_stalled_figures_are_the_last_sessions.py:254` `fresh_world`), the close-gap held view (`tests/app/test_close_gap_serves_session_that_closed.py:39` `monday_before_the_close`, read inside the gap as at `:111-114`), and the no-longer-held entry (`tests/app/test_no_longer_held_surfaces.py:42` `_seed_older_sessions`, `:55` `_add_stale_entry`). Fault worlds patch exactly one read the route makes (`database.get_triggers`, one `analytics.get_symphony_*` call for one id, `app._dashboard_panels_payload`, `database.get_guard_alpha_by_symphony`). A `WORLDS` mapping names each, so the golden producer and the tests build the same worlds through one implementation. Every builder fixes the route clock through the existing `frozen_datetime_class` (`tests/app/_refresh_timing_state_world.py:119`) or the virtual host clock of `_closed_session_world.install_clock` (`tests/app/_closed_session_world.py:99`), so `meta.clock_et`, `meta.next_run` and `next_run_seconds` (`app.py:3653`, `:3728`, `:3735`) are equal by construction.
- The two browser captures reuse the in-process browser harness (`tests/ui/_shell_support.py`: `firefox`, `open_probe` with `theme="dark"` at `:203-241`, `open_page` at `:244-276`, `WIDE`/`NARROW` at `:54-56`, `seed_every_page` at `:334`; the server `tests/ui/_browser_server.py` `running_server`), not the `UX_LAUNCHER_URL` launcher (`tests/ui/test_dashboard_charts_browser.py:55-61`). The launcher serves a production-database copy whose content changes between runs, so a capture committed from it could not be compared again; the in-process server reads the per-test database the worlds seed, as `tests/fixtures/dashboard/shell_baseline/PROVENANCE.md` already does for the shell baseline. The browser clock is fixed with `context.clock.install` (pattern `tests/ui/test_dashboard_recovery_browser.py:51`, `:83-93`).

## Criterion Thread

| AC | R items | Design element (file:line, or "new: <reason and search>") | Test seam (test file:line; fixture and provenance; harness pattern) | Cycle |
|---|---|---|---|---|
| AC-1 | R-6, R-7, R-8, R-13, R-14, R-15 | `app.py:5599-5610`, `:5640` (close-snapshot render and key removed); `app.py:5891-5901`, `:5938` (live render and key removed); waiting, error and auth paths unchanged (`app.py:5654-5699`, `:5956-5959`, `:280-316`) | new `tests/app/test_api_state_without_table.py` over `tests/app/_state_poll_worlds.py` (Flask test client, real temp DB, worlds from the committed records of `tests/fixtures/refresh_timing/01-c2/`, captured on the droplet); `tests/app/test_app_routes.py:187` (500 path); `tests/app/test_dashboard_auth.py:189`, `:547`, `:588` (401 and 503 with `_auth_check_enabled` re-enabled) | C1 |
| AC-2 | R-1, R-12 | `app.py:5726-5733` (stamping loop kept, grouping removed); `app.py:5735-5809` (labels and sort removed); every other statement of the live branch unchanged | `tests/app/test_api_state_without_table.py`; goldens `tests/fixtures/state_poll_no_table/golden_ref_<world>.json` written by the committed `produce_golden_ref.py` at 758f7f95 (pattern `tests/fixtures/refresh_timing/01-c2/produce_golden_ref.py:24-33`); derived live-state records from `live_state_two_cycles.json` and `tests/fixtures/prod_droplet/bot_state_positions.json` (both captured) | C1 |
| AC-3 | R-1, R-12 | `app.py:5513-5578` replaced by one in-place name sort; `app.py:5582-5592` removed; `_inject_guard_alpha` and `_normalize_frozen_symphonies` calls (`app.py:5595-5597`) and `_FROZEN_SYM_DEFAULTS` (`app.py:4441-4455`) unchanged | `tests/app/test_api_state_without_table.py`; goldens as AC-2; derived snapshot records from `tests/fixtures/refresh_timing/01-c2/snapshot_two_symphonies.json` (R8, PM-signed) and `bot_state_positions.json` | C1 |
| AC-4 | R-2, R-8, R-16 | `app.py:5513-5515`, `:5747-5750` (the only readers of `sortCol`/`sortDir`) removed | `tests/app/test_api_state_without_table.py` comparing two requests in one world under one fixed clock; no golden needed | C1 |
| AC-5 | R-1, R-10, R-11, R-14 | `static/index.js:2757-2761` (`isStateBody`) and `:2788-2810` (`loadState`), both unchanged (NB-19): the page reads no `html`, so AC-1's body change is what it must not notice; no production edit | new `tests/ui/test_state_poll_page_unchanged_browser.py` (marker `browser`) over the in-process server and the AC-2/AC-3 worlds; baseline `tests/fixtures/dashboard/state_poll_page_baseline/` written at 758f7f95 by its committed producer; shared reader `tests/ui/_state_poll_page_capture.py` (new); the node harness `tests/dashboard/_index_js_harness.py` suites still pass | C1 |
| AC-6 | R-2, R-3, R-5 | delete `templates/table_partial.html`; `static/settings-modal.js:4-5` reworded; comments at `app.py:4439-4440`, `:5701`, `:5718`, `:5855` reworded | new `tests/dashboard/test_table_partial_retired.py` (tree scans of `app.py`, `static/`, `templates/`, `tests/`, `tests/fixtures/dashboard/frozen_html/`); the census rewrites in Per-Cycle Detail C1; S-6.8 as a merge check over `git diff 758f7f95` | C1 |
| AC-7 | R-1, R-2, R-9, R-10, R-11 | `static/layout.css` after `:155` (one added rule); `templates/ai_advisor.html:50`, `templates/history.html:40`, `templates/performance.html:44`, `templates/settings.html:33` (deleted) | `tests/ui/test_dashboard_primitives_shared.py:236-262` (S-7.1 and S-7.10, the two pins edited to allow exactly the one added rule; `primitives_baseline/baseline.json` untouched); new `tests/ui/test_heading_margin_shared_browser.py` (marker `browser`) against baseline `tests/fixtures/dashboard/heading_margin_baseline/` written at 758f7f95 by its committed producer; shared reader `tests/ui/_heading_margin_capture.py` (new) | C2 |

## Execution Model

A real Agent Team, Toxic Pair TDD, per `.claude/CLAUDE.md` §Agent Team Composition. Each cycle has its own fresh `test-writer` (RED author, writes `.claude/review/<RED-sha12>/handoff.md` and the coverage record under its `records/`) and its own fresh `implementer` (GREEN, blind to the plan, works from the handoff), in its own worktree on its own branch, because two pairs committing in one worktree race on HEAD. The PM records both teams, branches and worktrees in `.claude/PM-ACTIVE-WORK.md`.

- C1 (state poll, dead template, dashboard page): `test-writer`, `implementer`. Worktree `.claude/worktrees/dtp` on `cleanup/dead-table-partial`.
- C2 (heading margin rule): `test-writer`, `implementer`. Worktree `.claude/worktrees/dtp-css` on `cleanup/dead-table-partial-css`, created by the PM from 758f7f95.
- One review round, on the one final head (C2's branch merged into `cleanup/dead-table-partial` by the PM with a merge commit after both cycle-completes), dispatched in parallel against that head: `qa`, `code-reviewer`, `ux-expert` with `behavioral-evidence-capturer` (the diff changes `templates/` and `static/`, and the plan's Screens line names the capturer), `flask-dashboard-specialist` (the domain specialist for `app.py` routes, templates and `static/`, which also covers the API surface `api-expert` would review), and `integration-auditor` (the diff spans the API body and the page that consumes it, AC-5). After every verdict reads PASS, one task walkthrough by `ux-expert` with its capturer runs on that head (ORCHESTRATION §The Mandate, task walkthrough), then one `doc-gen` for the slice. `quant-code-reviewer` is not dispatched: no math-layer file changes and `tests/test_mdd_window_truth_zero_diff_guard.py` pins the engine.
- Sensitive is none, so no hidden-mode `qa`.
- Every local pytest run is `-n0`, named files only, never the full tree and never `-o addopts=""`; browser files run with `-m browser -n0`; CI on GitHub Actions is the full-suite gate (`.github/workflows/tests.yml`).

Team size: 12 = 2 test-writer + 2 implementer + 1 qa + 1 code-reviewer + 1 ux-expert + 1 behavioral-evidence-capturer + 1 flask-dashboard-specialist + 1 integration-auditor + 1 doc-gen

## Dispatch Ordering

| Cycle | Consumes (cycle: committed artifact) | Branch base |
|---|---|---|
| C1 |  | cleanup/dead-table-partial @ 758f7f9551ff3a15a292debb7bfd4f00b3ecf1e7 |
| C2 |  | 758f7f9551ff3a15a292debb7bfd4f00b3ecf1e7 (branch cleanup/dead-table-partial-css) |

C1 and C2 start at once: they share no file. C1 changes `app.py`, `templates/table_partial.html`, `static/settings-modal.js` and tests under `tests/app/`, `tests/dashboard/`, `tests/ui/` (other than `test_dashboard_primitives_shared.py` and `_shell_support.py`), `tests/reporting/`, `tests/shadow/`. C2 changes `static/layout.css`, the four templates, `tests/ui/test_dashboard_primitives_shared.py`, `tests/ui/_shell_support.py` and its new files. The PM merges C2's cycle-complete branch into `cleanup/dead-table-partial` once both cycles are complete; that merge head is the one head the review round reads.

## Per-Cycle Detail

### C1: State poll without the table

Satisfies AC-1, AC-2, AC-3, AC-4, AC-5, AC-6.

Production files modified (GREEN, implementer): `app.py` (the edits in Architecture, State poll); `static/settings-modal.js:4-5` (comment). Production file deleted: `templates/table_partial.html`. Primitives imported: none new; `database.normalize_name` stays in its existing call (`app.py:5732`).

Test files added (RED, test-writer):
- `tests/app/_state_poll_worlds.py`: the `WORLDS` mapping of Architecture, Test harness.
- `tests/app/test_api_state_without_table.py`: AC-1 to AC-4 route tests.
- `tests/fixtures/state_poll_no_table/`: `derive_records.py` (stdlib, run with `python -I`, writes the derived records with sorted keys so a test can rerun it and compare bytes, pattern `tests/fixtures/refresh_timing/01-c2/derive_records.py`), the derived records below, `produce_golden_ref.py`, the goldens, `PROVENANCE.md`.
- `tests/ui/_state_poll_page_capture.py`, `tests/ui/test_state_poll_page_unchanged_browser.py`, `tests/fixtures/dashboard/state_poll_page_baseline/` (`produce_page_baseline.py`, `baseline.json`, `PROVENANCE.md`).
- `tests/dashboard/test_table_partial_retired.py`: AC-6 tree scans.

Derived records (one per case the code distinguishes; each changes only the named fields; PM sign-off as producer owner recorded per row in `PROVENANCE.md`, pattern `tests/fixtures/refresh_timing/01-c2/PROVENANCE.md` "Derived records"):
- `snapshot_two_accounts.json`: R8 (`snapshot_two_symphonies.json`) with its account list stored in reverse name order (`Land of Feaver'd Allocations: Intelligent Novas`, then `Corporate Chaos 5 ways `), plus the captured entries `Gpaw3IhZghQPRE6AdEKx` and `iaSOOUsmnCJHiZvbrWfs` from `tests/fixtures/prod_droplet/bot_state_positions.json` (each with `id` added) under a second account id in the captured key's format (a lower-case UUID v4 string, not a placeholder). Cases: two accounts and four symphonies (S-3.1, S-5.2), stored order not name order (S-3.5, S-4.1, S-4.5). Both R8 entries are `triggered: true` (01-c2 PROVENANCE, Cautions), so their status ranks tie and the ref's stable `?sortCol=status&sortDir=desc` keeps the stored order: S-4.1 can fail at the ref.
- `live_state_two_accounts.json`: cycle `m` of `live_state_two_cycles.json` plus the captured `Gpaw3IhZghQPRE6AdEKx` entry under the second account. Case: three symphonies in two accounts (S-2.1, S-2.4, S-2.5, S-4.2, S-4.6, S-5.1).
- `snapshot_missing_defaults.json`: `snapshot_two_accounts.json` with all 13 `_FROZEN_SYM_DEFAULTS` keys (`app.py:4441-4455`) removed from one entry (S-3.2, S-3.7).
- `snapshot_null_defaults.json`: the same entry with each of the 13 keys set to null (S-3.3).
- `snapshot_unnamed_entry.json`: one entry's `name` removed (S-3.6).
- `snapshot_no_accounts_map.json`: R8 with `accounts_map` removed (S-3.13).
- `live_state_prev_return_cases.json`: `live_state_two_accounts.json` with `prev_return` removed from one entry and `current_return` from a second (S-2.8).

Goldens: `produce_golden_ref.py` (pytest-run, not collected, `GOLDEN_OUT=<dir> python -m pytest -n0 tests/fixtures/state_poll_no_table/produce_golden_ref.py`) parametrizes over the `WORLDS` whose scenarios compare with the ref, pops `html`, and writes `golden_ref_<world>.json` with `indent=2, sort_keys=True` and LF line ends (the CRLF golden trap, `newline="\n"` as at `produce_golden_ref.py:32-33`). It refuses to write unless `git diff --quiet 758f7f9551ff3a15a292debb7bfd4f00b3ecf1e7 -- app.py templates static analytics.py database.py market_calendar.py` exits 0 and `git status --porcelain` on the same paths is empty (refusal pattern `tests/fixtures/dashboard/shell_baseline/capture_shell_baseline.py:104-110`). The RED author runs it on the RED tree before any GREEN commit, where those paths equal the ref; `PROVENANCE.md` records the command, the ref, the time and each golden's sha256, and states how to regenerate later from a throwaway worktree at 758f7f95 (the existing producer's docstring, `produce_golden_ref.py:1-13`).

Page baseline (AC-5): `produce_page_baseline.py` (marker `browser`, same refusal) runs each AC-5 world through `tests/ui/_state_poll_page_capture.py`, which both the producer and the test call. Per world it opens `/` with the browser clock installed at the world's instant, waits for `body[data-dashboard-ready="1"]` (`tests/ui/_shell_support.py:58`), and records: the normalized visible text of `main` and of the detail panel, the `#dp-footer-id` text after opening the first card's detail panel (`static/index.js:1365`, `:1691-1695`), and the ordered request paths with queries from the start of one poll to the next increment of `body[data-refresh-count]`. The poll is driven by `context.clock.run_for(30_000)` (the 30 s fallback, `static/index.js:28`) and, for S-5.3, by one `("cycle-complete", <payload with "changed": true>)` tuple put on each queue in `app._sse_clients` under `app._sse_clients_lock`: the fan-out step of `_notify_cycle_complete` (`app.py:1250-1258`) without its cache masking and Composer refresh (`app.py:1240-1248`), which would otherwise call out of the process. S-5.5 and S-5.6 answer the second poll from `page.route("**/api/state", ...)` with 500 and 401.

Test census dispositions (all in RED, by the test-writer; Q-1 default):

| Test (at the ref) | Disposition |
|---|---|
| `tests/app/test_f011_state_field_availability.py:218` | rewrite the line to assert `"html" not in body` on every branch; the rest of the test stays |
| `tests/dashboard/test_cards_live_refresh.py:484-503` | drop `"html"` from `_EXISTING_KEYS` and assert it is absent |
| `tests/dashboard/test_frozen_html_rendering.py:88-151`, `:155-192`, `:281-332`, `:335-392`, `:394-428`, `:430-474` | delete (AC-H1, AC-H2, AC-H4 to AC-H9); module docstring updated |
| `tests/dashboard/test_frozen_html_rendering.py:197-275` | keep unchanged (AC-H3, S-6.4) |
| `tests/fixtures/dashboard/frozen_html/frozen_html_missing_shadow_hwm.json` | delete (its only reader is `:444`); `frozen_html_with_symphonies.json` stays, read by `:207` |
| `tests/app/test_dashboard_m2.py:319-392`, `:453-579`, `:590-644`, `:645-720`, `:789-812` | delete (the table column, header, shadow-peak cell, sort context and freshness tests, with their helpers `_get_rendered_html` at `:319` and `_get_html` at `:469`) |
| `tests/app/test_dashboard_m2.py:394-452` | keep unchanged (S-6.4) |
| `tests/app/test_dashboard_m2.py:985-1043` | rewrite: capture the dict the route passes to `app.jsonify` (monkeypatched to record and delegate) and assert the two symphonies' `_tc` fallback dicts in `bot_state` are distinct objects; the identity check is the live assertion (S-6.4) |
| `tests/app/test_app_routes.py:107-163` | delete (status sort); docstring lines `:15-17` updated |
| `tests/app/test_app_routes.py:87-105` (`EXPECTED_RANKS` and its section comment) with `:167-175` (`test_api_state_status_rank_values_are_distinct_and_span_zero_to_five`) | delete (PM ruling, Item 3): both pin the status rank of the sort C1 removes (`app.py:5518-5529`, `:5752-5763`); nothing in them pins a behaviour that stays |
| `tests/app/test_dashboard_m2fix.py:32`, `:382-580` | delete the constant and `TestPerSymphonyIfHeldFormat`; docstring `:10` updated; the `index.html` classes stay |
| `tests/app/test_dashboard_vertical_fill.py:27`, `:154-170` | delete the constant and `TestTablePartialMaxHeightRemoved`; docstring `:15` updated |
| `tests/dashboard/test_f7_ac3_tooltip_first_sentence.py:43-68` | delete the constant and `test_mc_prob_tooltip_matches_locked_exact_text`; `test_no_beats_spy_claim_anywhere_under_templates_or_static` (`:71-98`) stays |
| `tests/dashboard/test_r15_shadow_names.py:46`, `:497-554` | delete the constant and `TestMainTableRowAttribute`; docstring `:17` updated |
| `tests/shadow/test_shadow_history.py:1118-1134` | delete |
| `tests/reporting/test_inv_cov_4_stop_consumers_handle_decrease.py:149-186` | delete `TestDashboardSortHandlesDecreasingStops` (PM ruling, Item 3) with its section comment (`:143-146`): an inline copy of the `stop_level` sort key C1 removes (`app.py:5782-5792`); it calls no production code, so no live part stays; docstring `:13` updated |
| `tests/reporting/test_inv_cov_4_stop_consumers_handle_decrease.py:189-258` | delete `TestTemplateRendersDecreasingStop`; docstring `:14` updated; `TestDiscordAlertRendersDecreasingStop` (`:43-140`) and `TestJsHasNoMonotonicUpStopClamp` (`:264-291`) stay |
| `tests/ui/test_dash_advisor_fixes.py:315-440` | delete `TestAC3bMddColumnNotInflated`; docstring `:8` updated |
| `tests/ui/test_persymph_settings_modal.py:1504-1583` | delete the two table-row gear tests (`:1515`, `:1555`) and their section comment; the card gear tests (`:149-208`) stay |
| `tests/dashboard/test_cards_live_updates.py:326-391`, `tests/dashboard/test_card_consistency_liveness.py:733-745`, `tests/dashboard/test_cards_live_refresh.py:405-419`, `tests/dashboard/test_symph_autoupdate.py:227-247` | keep unchanged (S-6.3) |

First production use: none; the poll's body loses one key and the page's code is unchanged.

### C2: Shared heading margin rule

Satisfies AC-7.

Production files modified (GREEN): `static/layout.css` (one rule added after `:155`); `templates/ai_advisor.html:50`, `templates/history.html:40`, `templates/performance.html:44`, `templates/settings.html:33` (one line each deleted). Primitives imported: none.

Test files modified (RED): `tests/ui/test_dashboard_primitives_shared.py`. A module constant names the one added rule (`"||.hero-section > .section-header:first-child"` with declarations `[("margin-top", "0")]` in the record shape `cap.ordered_primitive_rules` returns). `test_layout_css_holds_the_base_rules_byte_for_byte_in_order` (`:237-239`) compares with `BASELINE["ordered"]` with that record inserted immediately after the `.section-header` record. `test_each_primitive_selector_is_defined_once_in_layout_css` (`:252-262`) compares the key set with `set(BASELINE["resolved"]) | {added key}`. A new test in `TestOneDefinition` (S-7.1) asserts the rule appears once in `static/layout.css` and in no template's inline block. The baseline file is not edited (Q-4, NB-21). `tests/ui/_shell_support.py:244-276`: `open_page` gains a `/login` readiness rule (wait for `#password`, `templates/login.html:130`).

Test files added (RED): `tests/ui/_heading_margin_capture.py`, `tests/ui/test_heading_margin_shared_browser.py`, `tests/fixtures/dashboard/heading_margin_baseline/` (`produce_heading_margin_baseline.py`, `baseline.json`, `PROVENANCE.md`).

Capture (one implementation, `tests/ui/_heading_margin_capture.py`, called by the producer and the test):
- Preparation: `seed_dashboard_state()` and `seed_every_page(tmp_path, monkeypatch.setattr)` (`tests/ui/_shell_support.py:108`, `:334`), the app clock frozen at one named instant `CAPTURE_INSTANT` with `frozen_datetime_class` and the browser clock installed at the same instant, so the market state and every clock-derived text are the same on every run.
- Views: `/`, `/history`, `/performance`, `/login` on load; `/ai-advisor` once per tab after clicking each `button[data-tab]` (`templates/ai_advisor.html:1135-1197`); `/settings` once per section after clicking each `button[data-section]` (`templates/settings.html:984-988`). Each view at 1440x900 and 375x812, light and dark (`open_probe(theme="dark")`).
- Elements: every element matching `.section-header, .hero-section > *`, in document order, plus every element of `/login` (the selector matches nothing there, `templates/login.html` uses neither class, so the page is captured whole; it is small). Per element: tag, sorted classes, the first 40 characters of its text, the four margin longhands, and the sha256 of its sorted `property: value` computed-style list with the box-size-resolved properties left out (`width`, `height`, `inline-size`, `block-size`, `top`, `right`, `bottom`, `left`, `inset-*`, `transform-origin`, `perspective-origin`, named once with this reason: their resolved values follow the box's content, not the cascade the move can change). The record also holds the Firefox version.
- Assertions compare the head's capture with the baseline record for the same view and element: equal element keys, equal margins, equal digests, and the same browser version (a different version fails with a message to recapture from a throwaway worktree at 758f7f95). No literal pixel value is typed.

The producer refuses as C1's does, over `static templates app.py`. The RED author runs it in C2's worktree before any GREEN commit.

First production use: the shared rule applies on every page that links `static/layout.css` (all six, `templates/ai_advisor.html:11`, `history.html:11`, `performance.html:11`, `settings.html:11`, `index.html:10`, `login.html:9`).

## Per-Cycle Test Plan

Scope by level. C1: route tests through the Flask test client over a real temp state DB seeded through the real writers (`database.save_state`, `database.record_shadow_observation`), with nothing in `analytics` or `database` mocked except the one read a fault world makes fail; one browser module over the in-process server; tree-scan tests. C2: stylesheet and template scans, and one browser module. Neither cycle calls a live backend: the PM's live check is in Merge Criteria. Fixture provenance: every input record is a captured record or one derived record per case with PM sign-off; every golden and baseline is produced at 758f7f95 by a committed producer that refuses a changed tree.

Census of the entry points that reach a matrix rule (each is a round-1 RED row in its cycle). No persisted field changes; the one in-place write that stays is the name sort on the held snapshot's lists, and its two readers are listed.

| Entry point | Rules |
|---|---|
| Live: open with a cycle: `GET /api/state` live branch (`app.py:5700-5955`) | AC-1, AC-2, AC-4 |
| Live: closed, no snapshot: `GET /api/state` live branch reached by `_session_view` with no snapshot (`app.py:4170-4171`) | AC-1, AC-2, AC-4 |
| Snapshot: `closed_frozen`: `GET /api/state` close-snapshot branch (`app.py:5492-5650`) | AC-1, AC-3, AC-4 |
| Snapshot: `pre_market`: the same branch | AC-1, AC-3, AC-4 |
| Snapshot: stalled open: the same branch, `_stalled_day` set (`app.py:4136-4148`, `:4164-4168`) | AC-1, AC-3, AC-4 |
| Snapshot: held view: the same branch over `_held_close_snapshot` (`app.py:4105-4133`, `:4172-4177`) | AC-1, AC-3, AC-4 |
| Waiting: no state: `GET /api/state` waiting branch (`app.py:5654-5699`) | AC-1, AC-4 |
| Error: 500: `GET /api/state` handler (`app.py:5956-5959`) | AC-1 |
| Unauthenticated: 401: auth hook (`app.py:313-315`) | AC-1 |
| Misconfigured: 503: auth hook, no secret key (`app.py:290-296`) or no credential (`app.py:298-306`) | AC-1 |
| `GET /` (`app.py:3249-3265`) | AC-3 (S-3.7), AC-5: the close-snapshot render (`_render_dashboard`, `app.py:3268-3296`) reads the same held snapshot the poll sorts and fills; no change, NB-9; raised as Item 6 |
| `GET /api/events` (`app.py:5224-5258`) | AC-5 (S-5.3): the SSE refresh trigger the page consumes; no change, NB-18; raised as Item 6 |
| Dashboard page `templates/index.html` | AC-5, AC-7 |
| AI Advisor page `templates/ai_advisor.html` | AC-7 |
| History page `templates/history.html` | AC-7 |
| Performance page `templates/performance.html` | AC-7 |
| Settings page `templates/settings.html` | AC-7 |
| Login page `templates/login.html` | AC-7 |

Guard rows. A scenario whose claim is "as at the ref" passes on the RED tree by construction (the production code there is the ref's). Such a row is still written in RED, recorded in the coverage record as a guard, and the closer's sufficiency pass plants one fault per guard family and records that the family then fails: for AC-2 and AC-3, drop the `id` stamping or sort a copy instead of the held lists; for AC-5, delete `normalized_name` from the body; for AC-7, delete one inline copy without adding the shared rule. The rows that fail at RED are every `html`-absence row (S-1.1 to S-1.8, S-1.11), every golden comparison (the RED body still carries `html` and the golden does not), S-4.1, S-4.2, S-4.5 and S-4.6 (at the ref the parameters change the order or the `html` string), S-6.1, S-6.5, S-6.6, S-6.7, S-7.1 and S-7.10.

### C1
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-1.1 | AC-1 | route | `GET /api/state` live, open | new: `tests/app/test_api_state_without_table.py`, no module holds the branch matrix; pattern `tests/app/test_f011_state_field_availability.py:195-207` | world `open_two_accounts` (`live_state_two_accounts.json`, derived from captured records) |
| S-1.2 | AC-1 | route | `GET /api/state` close snapshot, `closed_frozen` | new: `tests/app/test_api_state_without_table.py`, as above | world `closed_frozen_two_accounts` (`snapshot_two_accounts.json`) |
| S-1.3 | AC-1 | route | `GET /api/state` close snapshot, `pre_market` | new: `tests/app/test_api_state_without_table.py`, as above | world `pre_market` (`seeded_world(..., "pre_market")`, R8) |
| S-1.4 | AC-1 | route | `GET /api/state` stalled open | new: `tests/app/test_api_state_without_table.py`, as above | world `stalled_open` (`fresh_world`, `tests/app/test_open_market_stalled_figures_are_the_last_sessions.py:254`, derived rows labelled in `tests/app/_closed_session_world.py:21-25`) |
| S-1.5 | AC-1 | route | `GET /api/state` held view | new: `tests/app/test_api_state_without_table.py`, as above | world `held_view` (`monday_before_the_close`, `tests/app/test_close_gap_serves_session_that_closed.py:39`, read at Monday 16:00:35 ET) |
| S-1.6 | AC-1 | route | `GET /api/state` live, closed with no snapshot | new: `tests/app/test_api_state_without_table.py`, as above | world `closed_no_snapshot` (`build_world(..., "closed_frozen")`, rows seeded, `commit_state("m")` without snapshot) |
| S-1.7 | AC-1 | route | `GET /api/state` waiting | new: `tests/app/test_api_state_without_table.py`, as above; golden comparison for "every other key as at the ref" | world `no_state` (`build_world`, nothing committed) |
| S-1.8 | AC-1 | route | `GET /api/state?account=<first account id>` | new: `tests/app/test_api_state_without_table.py`, as above | world `open_two_accounts`; the account id read from the record |
| S-1.9 | AC-1 | route | `GET /api/state` error path | `tests/app/test_app_routes.py:187` (`load_state` raising), add the exact body and the absent `html` | `mock_database` fixture of that module |
| S-1.10 | AC-1 | route | auth hook, 401 | `tests/app/test_dashboard_auth.py:189` (XHR `/api/state`), add that no state read runs (spy on `database.load_state`) | `auth_client` fixture `tests/app/test_dashboard_auth.py:88` |
| S-1.11 | AC-1 | route | `GET /api/state` close snapshot, two polls | new: `tests/app/test_api_state_without_table.py` | world `closed_frozen_two_accounts`, one fixed clock |
| S-1.12 | AC-1 | route | auth hook, 503 no secret key | `tests/app/test_dashboard_auth.py:588`, add an XHR `/api/state` asserting 503 `{"error": "misconfigured"}` | that test's monkeypatched environment |
| S-1.13 | AC-1 | route | auth hook, 503 no credential | `tests/app/test_dashboard_auth.py:547`, add the same XHR assertion | that test's monkeypatched environment |
| S-2.1 | AC-2 | route | `GET /api/state` live, open | new: `tests/app/test_api_state_without_table.py` | world `open_two_accounts`; `golden_ref_open_two_accounts.json` (producer at 758f7f95) |
| S-2.2 | AC-2 | route | `GET /api/state` live, open | new: `tests/app/test_api_state_without_table.py`; expected `normalized_name` computed by calling `database.normalize_name` on each fixture name | world `open_two_accounts` |
| S-2.3 | AC-2 | route | `GET /api/state` live, open with a stored snapshot (an unnamed dict entry) | new: `tests/app/test_api_state_without_table.py`; golden comparison plus the absence of `id`/`normalized_name` on `last_market_close_snapshot` | world `open_with_snapshot_key` (`seeded_world(..., "open")` then `commit_state("m", with_snapshot=True)`) and its golden |
| S-2.4 | AC-2 | route | `GET /api/state?account=<first account id>` | new: `tests/app/test_api_state_without_table.py` | world `open_two_accounts`; golden for that query |
| S-2.5 | AC-2 | route | `GET /api/state?account=<an id in no record>` | new: `tests/app/test_api_state_without_table.py` | world `open_two_accounts`; golden for that query; the id in the captured UUID format |
| S-2.6 | AC-2 | route | `GET /api/state`, `database.get_triggers` raising | new: `tests/app/test_api_state_without_table.py` | world `open_triggers_fail` and its golden |
| S-2.7 | AC-2 | route | `GET /api/state`, one `analytics.get_symphony_*` raising `KeyError`, `TypeError` and `ValueError` (one world each) for one id | new: `tests/app/test_api_state_without_table.py`, parametrized over the three exception types and the three helpers | worlds `open_analytics_fail_<type>` and their goldens |
| S-2.8 | AC-2 | route | `GET /api/state` live | new: `tests/app/test_api_state_without_table.py`; the expected difference computed from the record's two fields | world `open_prev_return_cases` (`live_state_prev_return_cases.json`) and its golden |
| S-2.9 | AC-2 | route | `GET /api/state`, `_dashboard_panels_payload` raising | new: `tests/app/test_api_state_without_table.py`; expected payload from `app._panels_unreadable()` | world `open_panels_fail` and its golden |
| S-2.10 | AC-2 | route | `GET /api/state` live, two polls | new: `tests/app/test_api_state_without_table.py` | world `open_two_accounts`, one fixed clock |
| S-2.11 | AC-2 | route | `GET /api/state` live, closed with no snapshot | new: `tests/app/test_api_state_without_table.py` | world `closed_no_snapshot_two_accounts` and its golden |
| S-3.1 | AC-3 | route | `GET /api/state` close snapshot | new: `tests/app/test_api_state_without_table.py` | world `closed_frozen_two_accounts`; golden |
| S-3.2 | AC-3 | route | `GET /api/state` close snapshot | new: `tests/app/test_api_state_without_table.py`, parametrized over the 13 keys of `app._FROZEN_SYM_DEFAULTS` read from the module (DC-5), checking `state`, `bot_state`, `accounts_map` and the card | world `closed_missing_defaults` (`snapshot_missing_defaults.json`); golden |
| S-3.3 | AC-3 | route | `GET /api/state` close snapshot | new: `tests/app/test_api_state_without_table.py`, parametrized as S-3.2 | world `closed_null_defaults` (`snapshot_null_defaults.json`); golden |
| S-3.4 | AC-3 | route | `GET /api/state` close snapshot | new: `tests/app/test_api_state_without_table.py`, parametrized as S-3.2; values read from the record | world `closed_frozen_two_accounts`; golden |
| S-3.5 | AC-3 | route | `GET /api/state` close snapshot | new: `tests/app/test_api_state_without_table.py`; expected order computed by sorting the record's names lower-cased | world `closed_frozen_two_accounts` |
| S-3.6 | AC-3 | route | `GET /api/state` close snapshot | new: `tests/app/test_api_state_without_table.py`; expected position computed from the lower-cased id | world `closed_unnamed_entry` (`snapshot_unnamed_entry.json`); golden |
| S-3.7 | AC-3 | route | `GET /api/state`, then `GET /`, then `GET /api/state` | new: `tests/app/test_api_state_without_table.py` | world `closed_missing_defaults`, one fixed clock; golden |
| S-3.8 | AC-3 | route | `GET /api/state` held view | new: `tests/app/test_api_state_without_table.py` | world `held_view`; golden |
| S-3.9 | AC-3 | route | `GET /api/state` close snapshot with a no-longer-held entry | new: `tests/app/test_api_state_without_table.py` | world `closed_no_longer_held` (`tests/app/test_no_longer_held_surfaces.py:42`, `:55`; records labelled in `tests/fixtures/no_longer_held/PROVENANCE.md`); golden |
| S-3.10 | AC-3 | route | `GET /api/state` close snapshot, `pre_market` | new: `tests/app/test_api_state_without_table.py` | world `pre_market`; golden |
| S-3.11 | AC-3 | route | `GET /api/state` stalled open | new: `tests/app/test_api_state_without_table.py` | world `stalled_open`; golden |
| S-3.12 | AC-3 | route | `GET /api/state` close snapshot, `database.get_guard_alpha_by_symphony` raising | new: `tests/app/test_api_state_without_table.py` | world `closed_guard_alpha_fail` (both R8 entries triggered, so the read runs); golden |
| S-3.13 | AC-3 | route | `GET /api/state` close snapshot | new: `tests/app/test_api_state_without_table.py` | world `closed_no_accounts_map` (`snapshot_no_accounts_map.json`); golden |
| S-4.1 | AC-4 | route | `GET /api/state?sortCol=status&sortDir=desc` | new: `tests/app/test_api_state_without_table.py`; body with equals body without; name order computed from the record | world `closed_frozen_two_accounts` |
| S-4.2 | AC-4 | route | `GET /api/state?sortCol=mc_prob&sortDir=desc` | new: `tests/app/test_api_state_without_table.py` | world `open_two_accounts` |
| S-4.3 | AC-4 | route | `GET /api/state?sortCol=unknown_column` | new: `tests/app/test_api_state_without_table.py` | world `closed_frozen_two_accounts` |
| S-4.4 | AC-4 | route | `GET /api/state?sortCol=&sortDir=` | new: `tests/app/test_api_state_without_table.py` | world `closed_frozen_two_accounts` |
| S-4.5 | AC-4 | route | `GET /api/state?sortDir=desc` | new: `tests/app/test_api_state_without_table.py` | world `closed_frozen_two_accounts` |
| S-4.6 | AC-4 | route | `GET /api/state?account=<first account id>&sortCol=status` | new: `tests/app/test_api_state_without_table.py` | world `open_two_accounts` |
| S-4.7 | AC-4 | route | `GET /api/state` with each value of the ref's sort columns (`name`, `mc_prob`, `status`, `stop_level`, `current_return`, `shadow_hwm`, `shadow`) and both directions | new: `tests/app/test_api_state_without_table.py`, parametrized; 200 and equal to the bare body | world `closed_frozen_two_accounts` |
| S-4.8 | AC-4 | route | `GET /api/state?sortCol=status`, waiting | new: `tests/app/test_api_state_without_table.py` | world `no_state` |
| S-5.1 | AC-5 | browser | `/` at 1440, light, first poll | new: `tests/ui/test_state_poll_page_unchanged_browser.py`, no browser module compares the page with a ref capture; harness `tests/ui/_shell_support.py:203-276` | world `open_two_accounts`; `state_poll_page_baseline/baseline.json` (producer at 758f7f95) |
| S-5.2 | AC-5 | browser | `/` at 1440, light, closed | new: `tests/ui/test_state_poll_page_unchanged_browser.py` | world `closed_frozen_two_accounts`; baseline |
| S-5.3 | AC-5 | browser | `/`, SSE `cycle-complete` | new: `tests/ui/test_state_poll_page_unchanged_browser.py`; event put on `app._sse_clients` queues (`app.py:1250-1258`) | world `open_two_accounts`; baseline |
| S-5.4 | AC-5 | browser | `/`, detail panel open, poll lands | new: `tests/ui/test_state_poll_page_unchanged_browser.py`; `#dp-footer-id` and the Risk Math velocity text | world `open_two_accounts`; baseline |
| S-5.5 | AC-5 | browser | `/`, poll answered 500 | new: `tests/ui/test_state_poll_page_unchanged_browser.py`; `page.route` answers the second poll | world `open_two_accounts`; baseline |
| S-5.6 | AC-5 | browser | `/`, poll answered 401 | new: `tests/ui/test_state_poll_page_unchanged_browser.py`; final URL path | world `open_two_accounts`; baseline |
| S-5.7 | AC-5 | browser | `/` at 375, dark, closed | new: `tests/ui/test_state_poll_page_unchanged_browser.py`; `open_probe(..., width=375, height=812, theme="dark")` | world `closed_frozen_two_accounts`; baseline |
| S-5.8 | AC-5 | browser | `/`, no state | new: `tests/ui/test_state_poll_page_unchanged_browser.py` | world `no_state`; baseline |
| S-5.9 | AC-5 | browser | `/`, one fallback poll | new: `tests/ui/test_state_poll_page_unchanged_browser.py`; requests recorded from `run_for(30_000)` to the next `data-refresh-count` | world `open_two_accounts`; baseline |
| S-6.1 | AC-6 | repository scan | `app.py`, `static/`, `templates/` | new: `tests/dashboard/test_table_partial_retired.py`, no scan of this template exists | the tree |
| S-6.2 | AC-6 | route | `GET /api/state`, every branch | `tests/app/test_api_state_without_table.py` (the S-1.1 to S-1.8 tests) and the census rewrites `tests/app/test_f011_state_field_availability.py:218`, `tests/dashboard/test_cards_live_refresh.py:484-503`, `tests/app/test_dashboard_m2.py:985-1043` | worlds as S-1.1 to S-1.8 |
| S-6.3 | AC-6 | existing tests | `static/index.js` | unchanged: `tests/dashboard/test_cards_live_updates.py:326-391`, `tests/dashboard/test_card_consistency_liveness.py:733-745`, `tests/dashboard/test_cards_live_refresh.py:405-419`, `tests/dashboard/test_symph_autoupdate.py:227-247`; the closer runs them and records the pass | their own fixtures |
| S-6.4 | AC-6 | route | `GET /api/state` | `tests/dashboard/test_frozen_html_rendering.py:197-275` and `tests/app/test_dashboard_m2.py:394-452` kept; `tests/app/test_dashboard_m2.py:985-1043` rewritten through a `jsonify` spy | `tests/fixtures/dashboard/frozen_html/frozen_html_with_symphonies.json` (kept); that module's state dicts |
| S-6.5 | AC-6 | repository scan | `tests/` | new: `tests/dashboard/test_table_partial_retired.py`; allowed hits `tests/test_scope_guard_f7.py:8`, `:58`, `tests/execution/test_cycleat_fix.py:21`, the four S-6.3 files and the scan module itself | the tree |
| S-6.6 | AC-6 | repository scan | `tests/fixtures/dashboard/frozen_html/` | new: `tests/dashboard/test_table_partial_retired.py`; `frozen_html_missing_shadow_hwm.json` absent, `frozen_html_with_symphonies.json` present and named by a test | the tree |
| S-6.7 | AC-6 | repository scan | `static/settings-modal.js` | new: `tests/dashboard/test_table_partial_retired.py`; the header comment names neither the template nor a table row | the tree |
| S-6.8 | AC-6 | merge check | the slice head against 758f7f95 | new: no test file; a permanent test would pin unrelated documents against every later edit, so the PM's merge check runs the two `git diff` commands named in Merge Criteria | `git diff 758f7f9551ff3a15a292debb7bfd4f00b3ecf1e7` |

### C2
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-7.1 | AC-7 | stylesheet scan | `static/layout.css`, the six templates' inline blocks | `tests/ui/test_dashboard_primitives_shared.py:236` (`TestOneDefinition`), new test; `_inline_css` at `:93-99` reads the blocks | the tree |
| S-7.2 | AC-7 | browser | `/history`, 1440, light | new: `tests/ui/test_heading_margin_shared_browser.py`, no browser module captures per-element computed style against a ref; harness `tests/ui/_shell_support.py:203-276` | `heading_margin_baseline/baseline.json` (producer at 758f7f95; state from `bot_state_positions.json` and the captured post-mortems through `seed_every_page`) |
| S-7.3 | AC-7 | browser | `/performance`, 1440, light | new: `tests/ui/test_heading_margin_shared_browser.py` | baseline |
| S-7.4 | AC-7 | browser | `/ai-advisor`, each `button[data-tab]` shown, 1440, light | new: `tests/ui/test_heading_margin_shared_browser.py` | baseline; the seeded Frontrunner proposal and survivor (`tests/ui/_shell_support.py:163`, `:175`; the synthetic metrics stand-in is labelled in `tests/fixtures/dashboard/shell_baseline/PROVENANCE.md`) |
| S-7.5 | AC-7 | browser | `/settings`, each `button[data-section]` shown, 1440, light | new: `tests/ui/test_heading_margin_shared_browser.py` | baseline |
| S-7.6 | AC-7 | browser | `/` and `/login`, 1440 and 375, light and dark | new: `tests/ui/test_heading_margin_shared_browser.py`; `/login` readiness added to `tests/ui/_shell_support.py:244-276` | baseline (login captured whole) |
| S-7.7 | AC-7 | browser | `/history` and `/performance`, the `.section-header` inside `.hp-head` (`templates/history.html:425-427`, `templates/performance.html:573-576`) | new: `tests/ui/test_heading_margin_shared_browser.py` | baseline |
| S-7.8 | AC-7 | browser | the four pages at 375, and in the dark theme | new: `tests/ui/test_heading_margin_shared_browser.py`, parametrized over width and theme | baseline |
| S-7.10 | AC-7 | stylesheet scan | `static/layout.css` | `tests/ui/test_dashboard_primitives_shared.py:237-239`, `:252-262` (edited as Per-Cycle Detail C2 states) | `tests/fixtures/dashboard/primitives_baseline/baseline.json`, unedited |

## Per-Cycle Risk Register

| Cycle | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| C1 | The kept name sort is moved onto a copy, so `GET /` after a poll reads a different account order than at the ref | Medium | Medium | The view is held per snapshot and its entry dicts and lists are shared with `GET /` (`app.py:4706-4718`, `:3905-3921`); Architecture keeps the in-place sort; S-3.7 and the closer's planted fault (sort a copy) |
| C1 | The `id` and `normalized_name` stamping goes with the dead `accounts_map` loop, and the detail panel footer falls back to the bare id (`static/index.js:1693-1695`) | Medium | Medium | The stamping is written only at `app.py:5731-5732` on the poll (PD-2); Architecture keeps the loop; S-2.2, S-5.4 |
| C1 | A golden or baseline is captured from a tree that differs from the ref, so the comparison proves agreement with the new code | Low | High | Both producers refuse unless `git diff --quiet 758f7f95` over the production paths exits 0 (pattern `capture_shell_baseline.py:104-110`); PROVENANCE records the command and sha256 values |
| C1 | Clock-derived fields (`app.py:3653`, `:3728`, `:3735`) or the browser clock make two reads differ | High if unfixed | Medium | Every world fixes the route clock (`tests/app/_refresh_timing_state_world.py:119`, `tests/app/_closed_session_world.py:99`); browser tests install the clock (`tests/ui/test_dashboard_recovery_browser.py:83-93`) |
| C1 | The directory conftests stub `app.get_api_state_dict` and `database.load_state`, so a world reads a stand-in | Medium | High | The state world rebinds the real function (`tests/app/_refresh_timing_state_world.py:49-52`, `:376`); the browser module imports the two overriding autouse fixtures (`tests/ui/_advisor_browser_support.py:50-57`) |
| C1 | A reader of `html` or of the table context is missed and goes red only in CI (DC-1) | Medium | Low | The census in Per-Cycle Detail C1 matches the audit's grep (spec-audit finding 5 and its re-check), including the patched `render_template` captures (`tests/app/test_dashboard_m2.py:704`, `:1042`); CI runs the full suite before main moves |
| C1 | The S-5.3 event, sent through `_notify_cycle_complete`, masks the account totals and starts a Composer refresh from the test process (`app.py:1240-1248`) | Medium | Medium | The test puts the tuple on `app._sse_clients` itself (the fan-out step, `app.py:1250-1258`) |
| C1 | The derived second account id or the two added entries read as fake | Low | Low | The id uses the captured key's UUID format; the added entries are verbatim captured records with `id` added; PM sign-off per row |
| C1 | Golden files written with CRLF on Windows differ on the CI runner | Medium | Low | The producer writes with `newline="\n"` (`tests/fixtures/refresh_timing/01-c2/produce_golden_ref.py:32-33`) |
| C2 | The added rule sits outside the primitive suffix of `layout.css` | Low | Low | `test_the_moved_rules_are_a_suffix_of_layout_css` (`tests/ui/test_dashboard_primitives_shared.py:241-250`); Architecture places it after `:155` |
| C2 | The edited primitive pins accept more than the one added rule | Medium | Medium | The added record is named once and inserted at one position; every other record must equal `BASELINE["ordered"]` byte for byte (Q-4) |
| C2 | A rule of 0,3,0 or higher on some page now wins where the inline copy won by order | Low | Medium | Spec-audit section F found none at the ref; the capture covers every element the selector can reach on all six pages in every view (DC-6) |
| C2 | Firefox version or font differences between the capture and the check change computed values | Medium | Low | The record holds the browser version and the check fails naming a mismatch; box-size-resolved properties are left out of the digest; capture and check run on the same host |
| C1, C2 | Browser modules are deselected in CI (`pyproject.toml:98`), so a browser regression is not caught by CI | High | Medium | Merge Criteria require the PM to run both browser modules with `-m browser -n0` on the final head |
| C1, C2 | The merge of C2 into the slice branch conflicts | Low | Low | The two cycles' file sets are disjoint (Dispatch Ordering) |
| C1 | `alpha_bot_execution.py` or `math_engine.py` is touched | Low | High | `tests/test_mdd_window_truth_zero_diff_guard.py` pins their sha256; run in Cycle-Close |

## Cycle-Close Criteria

- Every scenario in the cycle's table has a test in the coverage record, or a held row with its reason (S-6.8 is a merge check; the plan removed S-7.9, so no cycle carries it); each guard row records the planted fault the closer used and that the family then failed.
- Every row this approach marks as failing at RED was red at the RED commit, shown in the coverage record.
- C1: `derive_records.py` reproduces every derived record byte for byte; `PROVENANCE.md` lists each derived record with its case, changed fields and sign-off line, and each golden and the page baseline with ref, command, time and sha256.
- C2: `heading_margin_baseline/PROVENANCE.md` records ref, command, time, Firefox version, the views, the widths and the themes; `primitives_baseline/baseline.json` and `shell_baseline/baseline.json` are unchanged (`git diff` empty).
- `ruff format --check` and `ruff check` clean (line length 100).
- Local runs, `-n0`, named files only. C1: `tests/app/test_api_state_without_table.py`, every census file in Per-Cycle Detail C1, `tests/dashboard/test_table_partial_retired.py`, the S-6.3 pins, `tests/app/test_f011_state_field_availability.py`, `tests/app/test_api_state_single_computation.py`, `tests/app/test_dashboard_auth.py`, `tests/app/test_app_routes.py`, `tests/reporting/test_inv_cov_4_stop_consumers_handle_decrease.py`, `tests/execution/test_cycleat_fix.py`, `tests/test_scope_guard_f7.py`, `tests/test_mdd_window_truth_zero_diff_guard.py`, plus `tests/ui/test_state_poll_page_unchanged_browser.py` with `-m browser -n0`. C2: `tests/ui/test_dashboard_primitives_shared.py`, `tests/test_mdd_window_truth_zero_diff_guard.py`, plus `tests/ui/test_heading_margin_shared_browser.py`, `tests/ui/test_page_skeleton_browser.py` and `tests/ui/test_narrow_close_browser.py` with `-m browser -n0`.
- JS syntax only through `tests/js_syntax/test_js_syntax.py` (C1 changes a comment in `static/settings-modal.js`).
- No em dash, en dash or spaced hyphen in new prose; no `| safe` added.
- The cycle-complete names the RED SHA, the GREEN SHA, the round and the coverage record path.

## Merge Criteria

The operator's delivery rule (Q-3) replaces the PR: no PR is opened; the PM merges the finished branch into main with a merge commit and pushes main after a behavioral check against the real app.

- The final head is `cleanup/dead-table-partial` with C2's branch merged in. `qa`, `code-reviewer`, `ux-expert` with its capturer, `flask-dashboard-specialist` and `integration-auditor` verdict files all read PASS for that head; the task walkthrough reads PASS; doc-gen has committed the plan's Docs to Update and sent docs-done.
- The PM runs, `-n0`, both browser modules (`tests/ui/test_state_poll_page_unchanged_browser.py`, `tests/ui/test_heading_margin_shared_browser.py`) with `-m browser` on the final head, since CI deselects them.
- CI on the exact SHA that lands on main. The workflow runs on pushes to `main` and `assembly/**` (`.github/workflows/tests.yml:3-6`), so the PM builds the merge commit of the slice into current main on `assembly/dead-table-partial`, pushes it, and moves main to that same SHA only after its CI run is green.
- S-6.8 merge check: `git diff --name-only 758f7f9551ff3a15a292debb7bfd4f00b3ecf1e7 HEAD -- .design-handoff docs/research docs/audit 'feature-plans/*.completed.md' 'feature-plans/*.obsolete.md'` prints nothing, and `git diff 758f7f9551ff3a15a292debb7bfd4f00b3ecf1e7 HEAD -- DECISIONS.md` holds only added lines after the ref's last line.
- Migration proof: no file added under `migrations/`; `database._MIGRATION_FILES` unchanged.
- No persisted field changes, so there is no persistence roundtrip to prove.
- The PM's live functional check after the review, against the real app (mocked-green has diverged from live behaviour in this repo): `/api/state` bodies from the running daemon in a closed and an open market carry no `html` and keep every other key; the uncompressed and gzip sizes of the closed body are read for Success Measures; the six pages are viewed at 1440 and 375 px, light and dark. Production deploy to the droplet only after the Wednesday 2026-11-04 close, with a closed-market check that evening and an open-market proof the next morning (Q-3).

## Items Raised to the PM

Every item below is answered by the PM ([PM-ASSUMED]); none is open.

1. Answered, accepted: review roster. One review round on the merged head with `qa`, `code-reviewer`, `ux-expert` with `behavioral-evidence-capturer`, `flask-dashboard-specialist` (standing in for `api-expert` on the route) and `integration-auditor`; the task walkthrough after every verdict reads PASS; one `doc-gen` at the slice tail. Team size 12 as sized in Execution Model.
2. Answered, accepted: AC-7 (S-7.2 to S-7.8) compares the head's computed styles with the capture of 758f7f95 and types no value. This is read as a refactor-equivalence check, not the value assertion `~/.claude/skills/test-design-contract/SKILL.md` forbids.
3. Answered, in scope under R-2: `tests/app/test_app_routes.py:87-105` (`EXPECTED_RANKS`) with `:167-175`, and `tests/reporting/test_inv_cov_4_stop_consumers_handle_decrease.py:149-186`, join C1's census and are deleted in C1's RED. Both mirror the sort C1 removes and neither pins anything that stays live (rows in the Per-Cycle Detail C1 census table).
4. Answered, follow-up only: in both `GET /api/state` branches the `"live_mode"` value in the body literal (`app.py:5630`, `:5935`) is overwritten by `**_additive`'s own `live_mode` (`app.py:5446-5451`, merged after it at `:5648`, `:5953`), so `_frozen_live_mode` (`app.py:5612-5617`) and the live `live_mode` read (`app.py:5710`) never reach a response. The plan's writer adds it to the plan's Follow-Ups; it is not fixed here.
5. Answered, confirmed: the PM pushes the merge commit to `assembly/dead-table-partial`, waits for CI green on it, then fast-forwards main to that same SHA (Merge Criteria).
6. Answered, accepted: census entry points outside the plan's matrix, `GET /api/events` (`app.py:5224-5258`) and `GET /` (`app.py:3249-3265`). Neither changes (NB-9, NB-18); `GET /api/events` drives the AC-5 refresh (S-5.3) and `GET /` shares AC-3's held snapshot (S-3.7). No new matrix column.
