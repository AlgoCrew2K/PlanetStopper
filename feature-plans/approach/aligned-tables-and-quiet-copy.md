# Approach: Aligned tables and quiet copy
Spec: feature-plans/aligned-tables-and-quiet-copy.md
Ref: PlanetStopper@c0760e70eb6f8c6620002570b493bb9ca4ec6b1e

## Architecture
Every file:line below is read at c0760e70. The plan commit ac4563cb adds only the plan, its source file, its audit record and three plan amendments over c0760e70 (`git diff --stat c0760e70 ac4563cb`: three files under `feature-plans/`), so every production and test citation holds at the branch base. The design covers the plan as amended at ac4563cb (G-29 to G-33, R-46 to R-49, S-6.25 to S-6.27, the 1420 width, TT-29 to TT-32, the AC-1 matrix cell naming S-1.20 and S-1.21).

**1. One source for the copy.** The expected text stays in one committed fixture, `tests/fixtures/page_explainers/copy.json`, regenerated in place as the plan's Definition of Done asks. C1 extends `tests/fixtures/page_explainers/extract_copy.py` (plan path at `:39`, table columns at `:42-61`) with a second plan source: this plan, read through `git show ac4563cb:feature-plans/aligned-tables-and-quiet-copy.md` (or the approved commit, if a later one), never from the working tree. The regenerated file holds:
- `rows`: Copy table A and Copy table X with their dispositions and exact new texts; a row reading "(unchanged)" takes the `new_text` of the same key in `git show c0760e70:tests/fixtures/page_explainers/copy.json`;
- `glossary`: Table G per host, in order; a key marked * takes its Table G2 text, every other key the ref fixture's `new_text` with its slots; O31a to O31c are resolved by the extractor from `git show c0760e70:templates/ai_advisor.html` lines 1809 to 1815 (whitespace collapsed, then the G2 and G-33 rule: the term's own leading name and a following "is" or "are" dropped after the "Term: " prefix, every other word kept, so O31a reads "Spec Critic: an active guardrail ..." and O31c "Divergence Explainer: disabled by default ...");
- `titles`: Table G3;
- `values`: the ref's slot rows with the two Values table changes (R02, R22);
- `ref_rows`: the ref fixture's rows unchanged, so `tests/ui/_page_copy.py` (`load` at `:28`, `row` at `:38`, `text` at `:46`) keeps serving the superseded rows until each cycle re-pins its host's tests; Copy table C rows stay as they are except MU5 (NB-6, CX-10).

`tests/ui/_page_copy.py` gains `glossary(host)`, `x_row(key)` and `title_rows()` beside the existing accessors. `PROVENANCE.md` in the same folder names both plan commits.

**2. A reference capture at c0760e70.** AC-7 compares against c0760e70, while `tests/fixtures/page_explainers/ref_capture.json` was captured at 1988ce5f (`produce_ref_capture.py:44`). C1's RED author reruns `tests/fixtures/page_explainers/produce_ref_capture.py` with the reference set to c0760e70, inside the shared worktree, before any production file changes. The refusal at `produce_ref_capture.py:72-75` proves the production paths equal the reference, and `ALLOWED_TO_DIFFER` (`:58`) becomes empty because `database.py` is equal at c0760e70. The capture keeps its worlds (`seed_world`, `tests/ui/_page_explainers.py:462`) and its route bodies, banners, login and logout pages, Discord payload, engine hashes and route table. It drops `heights_375`, because the earlier height bound is not carried (Earlier criteria row for AC-7 of the earlier plan). It records figures and controls at 1920, 1440 and 390 for S-7.1 and S-7.2, and the stacked-form signature of every Table T table at 1000, 390 and 320 for S-1.4, S-8.1 and S-8.4. It adds the LF-normalised hashes of `advisors/tenure_review.py`, `tests/app/test_dashboard_no_order_path.py` and every `tests/security/*no_trade_boundary*.py` (five files at the ref). `tests/fixtures/page_explainers/produce_ref_token_inventory.py` is rerun at c0760e70 in the same step (`REFERENCE_COMMIT` at `:30`).

**3. The Glossary primitive.** Search: no shared glossary partial exists (`grep -n "{% macro\|{% include" templates/*.html` finds only `_chrome.html`, `_fonts.html` and template-local macros), and the closed-disclosure look is `.hp-more` (`static/layout.css:89-99`), adopted unchanged. New `templates/_glossary.html` holds one call macro, `glossary(host)`, rendering `<details class="hp-more" data-terms="glossary" data-testid="glossary" data-glossary-host="{{ host }}"><summary>Glossary</summary>{{ caller() }}</details>`. Each host moves its `<p data-term="KEY">` lines into one call block, in Table G order, as the last element of:
- the dashboard page content, before `templates/index.html:2088`;
- the detail panel, at the end of its body (`templates/index.html:2095-2270`);
- Performance and History page content, before the scripts at `templates/performance.html:823` and `templates/history.html:588`;
- Settings, after the five sections and before `templates/settings.html:1204`;
- each Advisor tab panel, before its closing tag (Overview `templates/ai_advisor.html:1883`).

The settings modal is written by `static/settings-modal.js`. Its `_termsDisclosure` (`:727-734`) is replaced by `_glossary(lines)`, which emits the same markup once at the end of the modal body (`:470-504`); a test compares the two markups. The existing `<p data-term>` paragraphs keep their keys, so the DOM marks every Glossary line.

**4. Titles from the Glossary.** Search: no shared title helper exists (`setAttribute('title'` appears only per site in `static/*.js`). New `static/glossary.js` exposes `window.PSGlossary.apply(root)`, following the shared-helper pattern of `window.PSRequest` (`static/request_guard.js`). For each Glossary host (`[data-glossary-host]`, scoped to the nearest detail panel, modal, tab panel or page), it reads each line's lead term (the text before the first ": "). It sets `title` to the full line on every AC-4 label element whose trimmed text, without a trailing ":", equals a lead term, and on every element carrying `data-glossary="KEY"`; templates and scripts put that attribute on the Table G3 elements. It never touches an element whose `title` it did not set itself, marked by `data-glossary-titled` (D-6, S-4.9). One `MutationObserver` (childList, subtree), batched per animation frame, re-applies it to rows the page scripts rewrite (DC-1). It writes attributes only, never markup. Every in-scope template loads it beside its page script; `tests/js_syntax/test_js_syntax.py` discovers the file by glob.

**5. Aligned columns.** Search: the repo already uses `grid-template-rows: subgrid` (`templates/index.html:651`) and `display: contents` (`templates/history.html:329`, `templates/settings.html:592`). C1 adds a columns primitive to `static/layout.css`, between the `.hp-more` block (`:89-99`) and the first primitive rule (`:101`), because `tests/ui/test_hp_more_shared_rule.py:88-104` and `tests/ui/test_dashboard_primitives_shared.py` pin that order:
- `.cols-table`: a grid with `grid-template-columns: repeat(var(--cols), auto)`, `width: max-content`, `max-width: 100%`, column gap 24 px;
- `.cols-row`: `grid-column: 1 / -1`, `display: grid`, `grid-template-columns: subgrid`;
- `.cols-num`: `justify-self: end`, `text-align: right`.

Each rule applies only from 1001 px, so every narrower width keeps the ref's stacked rules (AC-8). The grouping per Table T entry:

| T-id | Change | Cycle |
|---|---|---|
| TT-8 | `autotuneTableHtml` (`static/ai_advisor.js:841-890`) writes `.cols-table` with `--cols` set to the shown column count; the lift modifiers (`templates/ai_advisor.html:337-343`) and the flowed grid (`:356-363`) are deleted | C2 |
| TT-30 | the suggestion cards `static/ai_advisor.js:186-400` writes keep their markup; each field starts at one offset because the cards share the column's width; measured, and given fixed tracks only if a field moves | C2 |
| TT-9 | the observation lines (`:1174-1176`, `:1826-1835`) and the Retirement row share one column template through `.cols-table` on the list and subgrid on each producer, its `details` and its `summary` | C2 |
| TT-9h | the fixed share widths (`:1285-1290`) are deleted, so columns take their content width (R-11) | C2 |
| TT-18, TT-19, TT-20 | no rule applies (Table T); the Prism as-of `margin-left: auto` (`:847`) and the rationale-beside rules (`:1276-1279`) are deleted under AC-2 | C2 |
| TT-11 | from 1001 px the evidence row is `repeat(6, minmax(0, 1fr))`, so every card's cells start at the same x (cards are equal width); `order: 1` (`:1126`) and the flex bases (`:1123-1130`) go, and the loop at `:2473` takes the visible order of S-1.8 | C3 |
| TT-12, TT-21 | unchanged markup; the card head and lifecycle rows lose `justify-content: space-between` (`:1112-1113`) | C3 |
| TT-13 | `.stats-table` numeric cells and headers right-aligned (`:796-800`); `table-layout: fixed` of the fill modifier (`:757`) deleted | C4 |
| TT-14 | `.stat-row` (`:655`) becomes two content-width tracks, label then value, value right-aligned, so each column's values end at one x | C4 |
| TT-31, TT-32 | the swap result cards (`static/ai_advisor_asset_swaps.js:112-117`) and logic-change proposal cards (`static/ai_advisor_logic_changes.js:296`) sit in the equal-track `.swap-cards` grid (`templates/ai_advisor.html:647`); their figures follow TT-14 | C4 |
| TT-26, TT-27 | the Frontrunner `.proposal-cards--fill` flex wrap (`:739-757`, markup `:3282`) is removed, so cards sit in the equal-track `.proposal-cards` grid (`:737`) and each field starts at one offset | C4 |
| TT-10 | unchanged: centred tiles under centred headers (Q-4) | C4 |
| TT-1 | `#portfolio-strip` (`templates/index.html:1418`) shares one column template across its four `.vs-row`s through `.cols-table` | C5 |
| TT-2, TT-15, TT-16, TT-17, TT-29 | fixed tracks inside each card or row (cards are equal width) in `templates/index.html` (Risk math rows server rendered at `:2186-2248`) and the rows `static/index.js` writes or refills (`:1290`, `:1678`, the variable rows at `:1718-1721`) | C5 |
| TT-3 | the two-group grid (`templates/performance.html:362-391`) and the repeated header cells (`:690-693`, `.metrics-th-repeat` at `:339`) are deleted; one table at content width | C5 |
| TT-4 | `.metrics-tbl--ledger-pre` (`:755`) takes `width: auto` from 1001 px | C5 |
| TT-5 | the left-align override (`:392-393`) is deleted | C5 |
| TT-6, TT-7 | numeric headers and cells right-aligned (`templates/history.html:496`, `:566-572`; rows from `static/history.js`) | C5 |
| TT-22, TT-23, TT-25 | fixed tracks in the cards and rows `static/settings.js` (`:205-215`, `:604-615`) and `static/settings-modal.js` (`:136-158`) write | C5 |
| TT-24, TT-28 | no rule applies (Table T) | n/a |

**6. Fill-space rules removed (AC-2).** The nav grid (`templates/ai_advisor.html:1301-1311`) and the A01 caption with its disclosure (`:1341-1346`) go, so the nav panel holds only `.cap-nav-wrap`. The empty-panel beside-the-description grids (`:1235-1269`) go. The Retirement pairing goes: `.retirement-pair--side` and `--never-run` (`:1003-1010`, `:1068`, `:1088-1091`, `:1294-1297`), the class expression at `:2213`, and `refreshRetirementPair` with its constants (`static/ai_advisor.js:1141-1162`) and its calls (`:1187`, `:1223`, `:1228`, `:1232`). Also removed: the tenure run line `space-between` (`:1072`), the data stamps' `margin-left: auto` (`templates/performance.html:83`; the History toolbar `templates/history.html:416-430`), the Performance captions grid (`templates/performance.html:343-357`), Tracked, Armed and Triggered `space-between` (`templates/index.html:457`), and the dollar-panel auto-fit grid (`:500-511`, replaced by a flex row with one gap and `white-space: nowrap` on labels and values from 1440 px). The CVaR and Managed sleeves row stays (D-10).

**7. Status lines (AC-5).**
- In `static/ai_advisor.js`, `autotuneTableHtml` replaces the shared line (`:853-872`) with `autotuneSentence(cellRows, rows)`, which writes the AC-5 sentence into the same element. The element keeps its position and gains `data-testid="autotune-sentence"`; `autotune-shared-line` is retired. Only the run time (column 1) and outcome (column 2) are ever lifted (D-5).
- The outcome clause is chosen from the decision token the route sends (`app.py:13111-13115`, `_BASELINE_DECISION_TOKENS`: `fallback`, then the verbatim `Adopted AI` and `Reset to Global Default` the producer writes at `autotuner.py:3105`, `:3121`). Any other value takes `decisionLabel` (`static/ai_advisor.js:675`). The frozen-eval suffix is read from `frozen_eval_verdict`.
- A score or t-stat column is dropped from a table when `fmtSharpe` (`:45-48`) returns "N/A" for every row of that table, in both the main table and the older-runs table (`renderOlderAutotuneRuns`, `:895-905`, whose summary becomes CX-7).
- In `app.py`, `_build_verification_count_line` (`:10378-10402`) takes the verdict as a second parameter and uses `_readable_verified_counts` (`:10421`) to decide whether the pill states the count. It omits the verified count then, omits every count that is not an integer above zero (a bool is not one), and keeps the ref's empty result when `n_checks` is absent, null or zero. The caller at `:10693` passes `_ver_verdict`. The summary text "Show all checks" (`templates/ai_advisor.html:1740`) is static.

**8. The Retirement row (G-24, AC-2).** `_build_retirement_pointer` (`app.py:7298-7338`) adds a `status` key holding its text without the leading "Retirement: ". `text` is kept, so no consumer changes. The toolbar anchor (`templates/ai_advisor.html:1454`) and the `.adv-pointer` grid placements (`:275-287`, `:319`) go. After the producer loop and after both empty states (`:1878-1880`), the observations section renders one `.obs-producer` row, `data-role="RETIREMENT"`, with the label "Retirement", the verdict `retirement_pointer.status`, empty time and count cells, and in the last cell `<a href="#retirement" data-testid="retirement-pointer">Open tab</a>`. The existing hashchange handler (`static/ai_advisor.js:1104`) opens the tab, so no request is added.

**9. Tenure cards (AC-6).**
- N reaches the page through the existing page-copy seam, with no producer change, no stored-row change and no request. A new resolver `_copy_redundancy_needs_days()` sits beside `_copy_tenure_threshold` (`app.py:2682`) in the `_page_copy_values` resolver map (`:2750-2770`) under the slot `redundancy_needs_days`, and `ai_advisor_tab` adds the slot to its tuple (`:11116-11120`).
- The resolver lazy-imports `advisors.tenure_review` and `advisors.retirement_recommender` and returns `max(tenure_review._structural_needs_days(), retirement_recommender.MIN_OBS_FLOOR)` as a string, the expression of `advisors/tenure_review.py:1533`. It returns None when the search gives `sys.maxsize` (`:684-685`) or raises; `_page_copy_values` already turns a raise into None. A test pins that assignment's AST at `:1533`, and S-7.6 pins the file's hash.
- A new Jinja filter `tenure_redundancy_missing(reason, n)`, registered beside `tenure_text` (`app.py:8026-8073`), returns None unless the reason starts with `_UNDECIDED_PREFIX` (`advisors/tenure_review.py:289`). Otherwise it returns the S-6.1 form ("not decided", G-29) or the S-6.4 form. The number in the S-6.1 form is the `redundancy_needs_days` slot; no template, script or `app.py` text holds the figure (S-6.27). It counts every match of `\((\d+) aligned days(, (uncertainty|structural) gate undefined)?\)` in the reason, the format `_undecided_text` writes (`:1495-1508`). It returns the S-6.1 form only when N is readable, at least one match exists, every count is below N and no match carries a suffix. A display name can only add matches, so it can only move the line toward the S-6.4 form (KU-1, S-6.22).
- The missing line (`templates/ai_advisor.html:2467-2471`) prints the filter's text for redundancy when it is not None, else the ref's text. A new closed `<details data-testid="tenure-review-undecided">` with the summary "Comparisons that decided nothing" follows the line only then, holding the stored reason through `tenure_text`.
- `tenure_evidence_item` (`:2122-2195`): an item in the missing list prints "unavailable" or "not applicable" with no reason in its headline (`:2134-2139`), and "not used in the verdict" (`:2189-2191`) carries no reason. "paired with:" (`:2128`, `:2152`, `:2174-2176`) prints only when one of the pair's five figures holds a finite number (D-8); otherwise the pair's figure lines still print with their stored reasons and name no symphony.
- The per-peer "not compared" loop (`:2192-2197`) becomes one line per distinct stored reason, "not compared: {k} other symphony, {reason}" or "{k} other symphonies, {reason}", counting the mapping entries of `not_compared`. The producer writes only `_BASES_DIFFER_REASON` ("bases differ", `advisors/tenure_review.py:267`, written at `:1446-1452`), so the line reads the G-30 form; grouping by the stored reason keeps the line true if another reason ever appears. No peer name is printed anywhere in the cell (S-6.25).

**10. What does not change (AC-7).** No route, JSON body, stored row, migration or engine file changes. The render context gains one `page_copy` slot and one `retirement_pointer` key; no context key is removed. No `| safe` is added; every new text goes through autoescape or `textContent`. Every new layout rule sits inside `@media (min-width: 1001px)`, so the stacked forms below it are the ref's (AC-8).

## Criterion Thread
| AC | R items | Design element (file:line, or "new: <reason and search>") | Test seam (test file:line; fixture and provenance; harness pattern) | Cycle |
|---|---|---|---|---|
| AC-1 | R-1, R-2, R-3, R-5, R-6, R-7, R-28, R-37, R-38, R-39, R-47, R-48 | static/layout.css:99 (the columns primitive inserted after the `.hp-more` block); static/ai_advisor.js:841 (TT-8); templates/ai_advisor.html:1174 (TT-9), :1123 (TT-11), :796 (TT-13), :655 (TT-14), :739 (TT-26); templates/performance.html:362 (TT-3), :392 (TT-5); templates/index.html:1418 (TT-1) | new tests/ui/_alignment.py (Table W widths, Table T registry, column-edge and field-offset scripts on the text-box technique of tests/ui/_page_explainers.py:301-327) driven through tests/ui/_shell_support.py:202 `open_probe` and :244 `open_page`; worlds from tests/ui/_page_explainers.py:462 `seed_world` (captured droplet records through the real writers); browser marker, `-n 0` | C6 |
| AC-2 | R-1, R-8, R-9, R-10, R-11, R-26, R-27, R-28, R-36, R-37, R-40, R-41, R-45, R-47 | templates/ai_advisor.html:1301 (nav grid deleted), :1454 (pointer leaves the toolbar), :1878 (Retirement row added), :1003 (pairing deleted); static/ai_advisor.js:1141 (`refreshRetirementPair` deleted); app.py:7298 (`_build_retirement_pointer` gains `status`); templates/index.html:457, :500; templates/performance.html:83 | tests/ui/_alignment.py row-start, gap and line-gap scripts; tests/ui/test_page_explainers_advisor_browser.py:348 (pairing tests re-pinned to stacking); tests/app/test_advisor_overview_order.py:98 (pointer order re-pinned); fixture tests/fixtures/retirement/run_summary_captured.json (captured RETIREMENT_RUN_SUMMARY row) | C6 |
| AC-3 | R-1, R-12, R-13, R-14, R-15, R-16, R-28, R-30, R-43, R-45, R-49 | templates/index.html:1241, templates/performance.html:566, templates/history.html:407, templates/settings.html:965, templates/ai_advisor.html:1341, :1442, :2725, :2802; static/settings-modal.js:471 | tests/ui/test_page_copy_advisor.py:150, tests/ui/test_page_copy_dashboard.py:160, tests/ui/test_page_copy_performance_history.py:183, tests/ui/test_page_copy_settings.py:166 re-pinned to the regenerated tests/fixtures/page_explainers/copy.json (extracted from the plan at ac4563cb by extract_copy.py); render through the Flask test client | C6 |
| AC-4 | R-1, R-16, R-17, R-18, R-19, R-20, R-28, R-32, R-49 | new: templates/_glossary.html, the one Glossary wrapper for 13 template hosts (search: no shared partial in templates/, `.hp-more` at static/layout.css:89 adopted for the look); new: static/glossary.js, title stamping from Glossary lines (search: no shared title helper in static/*.js); static/settings-modal.js:727 (`_termsDisclosure` replaced) | tests/ui/test_page_explainers_cross_host.py:159 (one definition per host) and :201 (ref titles) re-pinned to the Glossary; tests/ui/test_page_explainers_cross_host_browser.py:491 (closed, named, Enter and Space); fixture: copy.json `glossary` and `titles` | C6 |
| AC-5 | R-1, R-21, R-22, R-23 | static/ai_advisor.js:841 (`autotuneTableHtml`), :853 (shared line replaced by the sentence), :895 (`renderOlderAutotuneRuns`); app.py:10378 (`_build_verification_count_line`), :10693 (caller) | tests/ui/test_advisor_refresh_client.py:283 (real script over the real page through tests/ui/_page_js_harness.py `render_page`/`run_page`); tests/ui/test_advisor_density_contract.py:299; tests/app/test_ai_advisor_tab_verification_redesign.py:255 and :726; fixtures: runs through database.save_autotune_run from tests/ui/_page_explainers_advisor.py:401 `save_run_like` (captured autotune fixtures), verification rows from advisors.prism_numeric_verifier over tests/fixtures/prism_verifier/verify_cited_numbers_mixed_classifications.json | C2 |
| AC-6 | R-1, R-24, R-25, R-35, R-42, R-44, R-46 | app.py:2682 (new `_copy_redundancy_needs_days` beside it), :2750 (`_page_copy_values` map), :8026 (new `tenure_redundancy_missing` filter beside `tenure_text`), :11116 (slot tuple); templates/ai_advisor.html:2467 (missing line), :2122 (`tenure_evidence_item`) | tests/app/test_retirement_panel_render.py:2209 and :4079 re-pinned; fixture: records built by the real producer through tests/app/_tenure_review_panel_builders.py:337 `_record_of` (`tr.build_review` over constructed series), new undecided-peer rosters added there | C3 |
| AC-7 | R-29, R-31, R-34, R-35 | tests/fixtures/page_explainers/produce_ref_capture.py:44 (reference moved to c0760e70); no production design element: AC-7 is a set of pins on files this slice does not change | tests/ui/test_page_explainers_unchanged_surfaces.py:200 (route bodies), :249 (hashes), :230 (login and logout), :236 (Discord); tests/ui/test_page_explainers_unchanged_browser.py:174 (figures and controls); fixture: ref_capture.json regenerated at c0760e70 | C6 |
| AC-8 | R-4, R-28 | static/layout.css:99 (every new rule inside `@media (min-width: 1001px)`); static/ai_advisor.js:1129 (`updateTabCues`, unchanged) | tests/ui/test_narrow_close_browser.py:59; tests/ui/test_page_explainers_advisor_browser.py:602 and :680 (tab cues and reach at 375 and 320, re-pointed to 390 and 320); stacked-form signatures in ref_capture.json at 1000, 390 and 320 | C6 |
| AC-9 | R-33, R-36, R-37, R-45 | new: the PM's visual-check record `.claude/review/<deploy-sha12>/visual-check.md`, a deploy gate with no code (search: no screenshot gate exists in scripts/ or tests/) | held coverage rows in C6's record, reason: the PM's deploy gate after Merge Criteria; screenshots from a read-only local server over the production database copy | C6 |

## Execution Model
One repository and six cycles, run in sequence by one Toxic Pair at a time in the shared worktree `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\.claude\worktrees\atq` on branch `feat/aligned-tables-and-quiet-copy`, as the brief sets. Each brief requires `cd` into the worktree, then `git rev-parse --abbrev-ref HEAD` and `git rev-parse HEAD` checked against the brief before the first edit, because a teammate inherits a non-worktree cwd. The project environment rules apply: the pytest memory cap (`tests/_mem_cap.py`), never `-n auto` and never more than 4 workers, browser modules with `-m browser -n 0`. The browser server empties the environment, so browser modules need no `.env`; a cycle whose non-browser suites read credentials gets the `.env` copy the project rule names.

| Cycle | Pair (agent names) | Why this pair exists |
|---|---|---|
| C1 shared primitives and harness | atq-c1-tw (test-writer, closer), atq-c1-impl (implementer) | Owns every file later cycles read: the regenerated copy fixture, the c0760e70 reference capture, the alignment harness (test side); `templates/_glossary.html`, `static/glossary.js` and the columns primitive in `static/layout.css` (production side); the AC-7 pins every later head keeps green; the removal of the withdrawn 240 by 80 px and figure-span tests |
| C2 Advisor navigation and Overview | atq-c2-tw, atq-c2-impl | The Overview and the navigation panel of `templates/ai_advisor.html`, the autotune code of `static/ai_advisor.js`, and `app.py` `_build_verification_count_line` and `_build_retirement_pointer` |
| C3 Retirement | atq-c3-tw, atq-c3-impl | The Retirement panel of `templates/ai_advisor.html`, the pairing code of `static/ai_advisor.js`, and the `app.py` N resolver and missing-line filter |
| C4 the other Advisor tabs | atq-c4-tw, atq-c4-impl | Correlations, Asset Swaps, Logic Changes, Chat, Strategy Builder and Frontrunner Builder in `templates/ai_advisor.html`; `static/ai_advisor_asset_swaps.js` and `static/ai_advisor_logic_changes.js` |
| C5 dashboard, detail panel, Performance, History, Settings, settings modal | atq-c5-tw, atq-c5-impl | `templates/index.html`, `static/index.js`, `templates/performance.html`, `static/performance.js`, `templates/history.html`, `static/history.js`, `templates/settings.html`, `static/settings.js`, `static/settings-modal.js` |
| C6 cross-host sweep | atq-c6-tw, atq-c6-impl | Scenarios that span every host or every width and hold only once all hosts changed; re-captures the page baselines earlier slices pinned. atq-c6-impl fixes whichever file a failing sweep names |

Reviewers: one slice-end review of the merged C6 head, by the standing rule of one review per plan at the end (PM ruling, item 5). No cycle head is reviewed on its own; each cycle closer still runs its own census and fault checks before its cycle-complete.

| Role | Head it reviews | Why it is dispatched |
|---|---|---|
| qa (atq-qa) | the C6 head | The head bound for main |
| code-reviewer (atq-cr) | the C6 head | The slice changes code in `app.py`, templates and scripts |
| ux-expert (atq-ux) | the C6 head | The slice renders UI on every page and Advisor tab |
| behavioral-evidence-capturer (atq-cap) | paired with atq-ux | Observes the rendered pages in the shared browser, over the read-only capture server the PM runs |
| flask-dashboard-specialist (atq-flask) | the C6 head | Covers the `app.py` render code and Jinja filters of C2 and C3 |
| doc-gen (atq-docgen) | the slice tail | After every reviewer passes the C6 head |

Team size: 18 = 6 test-writer + 6 implementer + 1 qa + 1 code-reviewer + 1 ux-expert + 1 behavioral-evidence-capturer + 1 flask-dashboard-specialist + 1 doc-gen

## Dispatch Ordering
| Cycle | Consumes (cycle: committed artifact) | Branch base |
|---|---|---|
| C1 |  | feat/aligned-tables-and-quiet-copy at the commit holding the approved plan and this approach (after ac4563cb) |
| C2 | C1: tests/fixtures/page_explainers/copy.json; C1: tests/fixtures/page_explainers/ref_capture.json; C1: tests/ui/_alignment.py; C1: tests/ui/_page_copy.py; C1: templates/_glossary.html; C1: static/glossary.js; C1: static/layout.css columns primitive | feat/aligned-tables-and-quiet-copy at C1's cycle-complete SHA |
| C3 | C1: tests/fixtures/page_explainers/copy.json; C1: tests/ui/_alignment.py; C1: templates/_glossary.html; C1: static/layout.css columns primitive; C2: templates/ai_advisor.html (C3 edits the same `<style>` block and tab markup C2 edits); C2: static/ai_advisor.js (C3 deletes the pairing calls inside the tab-activation code C2's tests drive) | feat/aligned-tables-and-quiet-copy at C2's cycle-complete SHA |
| C4 | C1: tests/fixtures/page_explainers/copy.json; C1: templates/_glossary.html; C1: static/glossary.js; C3: templates/ai_advisor.html; C3: tests/ui/test_page_explainers_advisor_browser.py (C4 re-pins the Frontrunner cases in the module C2 and C3 edit) | feat/aligned-tables-and-quiet-copy at C3's cycle-complete SHA |
| C5 | C1: tests/fixtures/page_explainers/copy.json; C1: tests/ui/_alignment.py; C1: templates/_glossary.html; C1: static/glossary.js; C1: static/layout.css columns primitive; C4: tests/ui/test_page_explainers_cross_host.py; C4: tests/ui/test_page_explainers_cross_host_browser.py (C5 re-pins the non-Advisor host cases in the cross-host modules C2 to C4 edit) | feat/aligned-tables-and-quiet-copy at C4's cycle-complete SHA |
| C6 | C4: templates/ai_advisor.html; C5: templates/index.html; C5: templates/performance.html; C5: templates/history.html; C5: templates/settings.html; C5: static/settings-modal.js; C5: tests/ui/test_page_explainers_cross_host.py | feat/aligned-tables-and-quiet-copy at C5's cycle-complete SHA |

## Per-Cycle Detail
**C1: shared primitives and harness.** Satisfies AC-2 (S-2.15), AC-3 (S-3.4) and AC-7 (S-7.3, S-7.6, S-7.7, S-7.8, S-7.10); provides the seams of AC-1, AC-3, AC-4 and AC-7 for later cycles.
- Files modified (test side, RED author): `tests/fixtures/page_explainers/extract_copy.py` (second plan source, Architecture 1); `copy.json` regenerated; `PROVENANCE.md`; `produce_ref_capture.py` and `ref_capture.json` (Architecture 2); `produce_ref_token_inventory.py` and `ref_token_inventory.json`; `tests/ui/_page_copy.py:24-76` (new accessors); `tests/ui/_page_explainers.py`:
  - `EMPTY_REGION_JS` (`:149`), `FIGURE_SPAN_JS` (`:301`) and `HEIGHT_FACTS_JS` (`:330`) deleted with every test that asserts them: `tests/ui/test_page_explainers_measure_browser.py` whole; `tests/ui/test_dashboard_density_browser.py:183`; `tests/ui/test_page_explainers_cross_host_browser.py:327`, `:347`, `:404`; `tests/ui/test_page_explainers_performance_history_browser.py:310`, `:328`, `:351`, `:370`; `tests/ui/test_page_explainers_settings_browser.py:321`;
  - in `tests/ui/test_page_explainers_production_shapes_browser.py:139`, `:238`, `:298` and `tests/ui/test_page_explainers_advisor_browser.py:348`, `:368`, `:402`, `:451`, only the empty-region assertion is deleted; their other assertions are re-pinned by C2 to C4.
- Files modified (production, implementer): `static/layout.css`, the columns primitive after `:99`.
- Files added (test side): `tests/ui/_alignment.py`, holding:
  - `WIDTHS` = 1001, 1120, 1290, 1350, 1400, 1420, 1440, 1920, 2560 (Table W as amended, the 1420 band of G-31 included) and `NARROW` = 1000, 390, 320;
  - `TABLE_T`, a dict per T-id: host, how to open it (tab, card, modal), group, header and row selectors, column cell selectors, numeric columns; `TABLE_L` and `TABLE_F` likewise;
  - the scripts `COLUMN_EDGES_JS`, `FIELD_OFFSETS_JS`, `COLUMN_GAPS_JS`, `LINE_GAPS_JS`, `ROW_START_JS` (the 24 px rule, D-7 controls `button, select, input, [role="radiogroup"], [role="tablist"]`) and `TABLE_FORM_JS` (the stacked-form signature).
- Files added (test side): `tests/ui/test_alignment_measure_browser.py` (each script shown reporting a fault and reporting none on small built pages, the pattern of the deleted `test_page_explainers_measure_browser.py:53-366`); `tests/ui/test_withdrawn_density_rules.py` (S-2.15).
- Files added (production): `templates/_glossary.html`; `static/glossary.js`.
- Primitives imported: `.hp-more` (`static/layout.css:90-98`); the subgrid precedent (`templates/index.html:651`); `window.PSRequest`'s helper shape (`static/request_guard.js`).
- First production uses: none in C1; C2 is the first host of the macro, the script and the primitive. Supporting tests in C1 drive the primitive through a page built with `page.set_content` that links `static/layout.css` and `static/glossary.js`.

**C2: Advisor navigation and Overview.** Satisfies AC-1 (S-1.5, S-1.10, S-1.16), AC-2 (S-2.1, S-2.3, S-2.4, S-2.5, S-2.11, S-2.12, S-2.17, S-2.20), AC-3 (S-3.5), AC-4 (S-4.6) and all of AC-5.
- `templates/ai_advisor.html`:
  - navigation: `:1333-1346` (A01 and its disclosure out), `:1301-1311` (grid out);
  - Overview top: `:1442` (O01 out), `:1443` (O03), `:1444-1450` (to the Glossary), `:1454` (pointer out), `:1490-1492` (CX-1);
  - autotune: `:1500-1509` (O08 and the Glossary), `:333-343`, `:356-363` (TT-8), `:1235-1269` (empty-panel grids out);
  - Market Prism: `:1567-1573` (O15 and the Glossary), `:847`, `:1276-1279`; verification: `:1740` (CX-5);
  - observations: `:1793-1799` (O27 and the Glossary), `:1808-1818` (CX-3), `:1174-1176` and `:1285-1290` (TT-9, TT-9h), the Retirement row after `:1878-1880`;
  - the Overview Glossary before `:1883`; `data-glossary` on the verification header (`:1714`) and the sentiment chip (`:1558-1559`) (TG3-7); `static/glossary.js` loaded.
- `static/ai_advisor.js`: `:750-835` (`autotuneRunCells` emits `data-glossary` on the outcome words, TG3-6), `:841-890` (sentence, N/A columns, `data-glossary` on the two headers, TG3-5, `.cols-table`), `:895-905` (CX-7).
- `app.py`: `:10378-10402` and `:10693` (AC-5 count line); `:7298-7338` (`status` key).
- Tests re-pinned (superseded behaviour):
  - autotune: `tests/ui/test_advisor_density_contract.py:113-342`; `tests/ui/test_advisor_refresh_client.py:283-416`; `tests/app/test_advisor_autotune_panel_bounds.py:293` and its older-summary expectation (`:12`); `tests/ui/test_advisor_autotune_browser.py:199-226`; `tests/ui/test_page_explainers_advisor_browser.py:753`; `tests/ui/test_page_explainers_production_shapes_browser.py:261`;
  - navigation and descriptions: `tests/ui/test_page_explainers_advisor_browser.py:793`; `tests/ui/test_page_explainers_production_shapes_browser.py:238`;
  - verification: `tests/app/test_ai_advisor_tab_verification_redesign.py:255-325`, `:365`, `:493`, `:812`;
  - guardrail note: `tests/ai_advisor/test_r1_guardrail_honesty.py:176-263`, `tests/ai_advisor/test_advisor_honest_uniform_framing.py:98-153` and `tests/app/test_advisor_observation_status_lines.py:200`, now read Glossary lines O31a to O31c;
  - pointer: `tests/app/test_advisor_overview_order.py:31`, `:98`; `tests/ui/test_advisor_overview_order_browser.py:45-104`; `tests/app/test_retirement_tab_render.py:632`; `tests/app/test_retirement_tab_client.py:46`, `:559`; `tests/ui/test_review_fixes_browser.py:498`, `:542`;
  - the page caption: `tests/ui/test_cycle_4_advisor.py:272`, `:382`; `tests/ai_advisor/test_r1_attribution_honesty.py:91`; `tests/ai_advisor/test_advisor_observations_ui.py:202`; `tests/ui/test_page_skeleton_render.py:280`;
  - the Overview rows of `tests/ui/test_page_copy_advisor.py:150-287`;
  - the removed tokens of `tests/ui/test_page_explainers_cross_host.py:313` (an explicit list, each naming its plan id);
  - `tests/fixtures/ai_advisor/ux_overhaul_02/prism_block_source_baseline.txt`, re-captured by its producer `capture_prism_block_source.py`.
- Supporting tests for the Overview's Copy table A, Copy table X and Glossary rows go in `tests/ui/test_page_copy_advisor.py` (recorded by C6 under S-3.1, S-3.2, S-4.1 and S-4.5).
- First production uses: `templates/_glossary.html`, `static/glossary.js` and `.cols-table`.

**C3: Retirement.** Satisfies AC-1 (S-1.8, S-1.18), AC-2 (S-2.9, S-2.10, S-2.18), all of AC-6, and AC-7 (S-7.5).
- `templates/ai_advisor.html`:
  - copy: `:2211` (R01), `:2218` (R02 with the Values phrase), `:2219-2231` (to the Glossary, R12 per G2), `:2387` (R22), `:2388-2405` (to the Glossary);
  - layout: `:2213` (class expression out), `:1003-1010`, `:1068`, `:1088-1091`, `:1294-1297`; `:1082-1093` (TF-5: the five tiles on one row, the Blocked breakdown on its own line below); `:1072` (TF-6); `:1112-1113` (TF-7); `:1123-1130` (TT-11); the loop at `:2473` (order);
  - tenure: `:2467-2471` (missing line), the new `tenure-review-undecided` disclosure, `tenure_evidence_item` `:2122-2195`, its "not compared" loop `:2192-2197` in count form (G-30); `data-glossary` on the tiles (`:2320`) and the evidence headings (`:2152`) (TG3-13); the Retirement Glossary at the panel end.
- `static/ai_advisor.js`: `:1141-1162`, `:1187`, `:1223`, `:1228`, `:1232` deleted.
- `app.py`: the resolver after `:2696`; the resolver map `:2759-2767`; the filter after `:8031`; the slot tuple `:11117`.
- Fixture added: in `tests/app/_tenure_review_panel_builders.py`, rosters built through `_record_of` (`:337`, the real `tr.build_review` and `run_record`):
  - `undecided_record`, the production shape: 11 symphonies, pairwise aligned days 153 to 170, one entry also missing backtest with the producer's reason, one not yet eligible, one with no shown pair;
  - `undecided_edge_record`: a peer at exactly N days whose correlation is undefined, a gate-suffix peer, a single peer at 170 days, a display name holding a comma, parentheses and "aligned days";
  - `uncomputed_pair_record`: a shown pair with no computed figure, and one with only holdings overlap.
- Tests re-pinned: `tests/app/test_retirement_panel_render.py:2209-2279`, `:4079-4128`; `tests/ui/test_page_explainers_advisor_browser.py:348-447`; `tests/ui/test_page_explainers_fix_contracts.py:111-127`; `tests/ui/test_page_explainers_production_shapes_browser.py:298`, `:318`; the R02 and R22 phrase tests `tests/ui/test_page_copy_advisor.py:538-686`.
- Retired fixture: `tests/ui/_page_explainers_production_shapes.py:80-107`. `production_tenure_record` joins peers with "; " while the producer joins with ", " (`advisors/tenure_review.py:141`), so it is replaced by `undecided_record`.
- First production uses: the `redundancy_needs_days` slot and the `tenure_redundancy_missing` filter.

**C4: the other Advisor tabs.** Satisfies AC-1 (S-1.9, S-1.19) and AC-3 (S-3.6, S-3.7).
- `templates/ai_advisor.html`:
  - Correlations: `:1898-1905` (C01 and its Glossary);
  - Asset Swaps and Logic Changes: `:2512-2520` (W01 and its Glossary), `:2589-2597` (L01 and its Glossary);
  - Chat: `:2683` (T02) and its Glossary (A04);
  - Strategy Builder: `:2712` (B00), `:2725-2731` (CX-9), `:2802-2815` (B03 note and disclosure out, MU5 retired), `:2945-2950` (B07), `:3090` (B10) and its Glossary;
  - Frontrunner Builder: `:3191` (X01), `:3237-3241` (X03), `:3257-3259` (CX-11), `:3265-3270` (X05 out) and its Glossary;
  - layout: `:796-800` (TT-13), `:655` (TT-14), `:737-757` and `:3282` (TT-26); `data-glossary` on the stats-table "Live Baseline" header and the Frontrunner change labels (`:3297-3330`) (TG3-15).
- `static/ai_advisor_asset_swaps.js:155-159` and `static/ai_advisor_logic_changes.js` (the Baseline and Variant labels carry `data-glossary` W06 and L05, TG3-16).
- Tests re-pinned: `tests/ui/test_page_explainers_advisor_browser.py:451`, `:501`, `:530` (the Frontrunner fill layout); `tests/ui/test_page_copy_advisor.py:315-458` (model lines, the B03 run note at `:346`); `tests/ai_advisor/test_advisor_inplace_tabs.py:695` (T02 marker).

**C5: dashboard, detail panel, Performance, History, Settings and settings modal.** Satisfies AC-1 (S-1.6, S-1.7, S-1.11, S-1.12, S-1.20, S-1.21), AC-2 (S-2.6, S-2.7, S-2.8, S-2.13, S-2.14, S-2.19), AC-3 (S-3.13, S-3.14), AC-4 (S-4.13, S-4.14) and AC-7 (S-7.12).
- `templates/index.html`:
  - copy: `:1241` (D01 out), `:1253-1260` (dash.hero and its Glossary), `:1338-1345`, `:1598` (D20 to Glossary D20t), `:1628-1635`, `:1681-1688`, `:1818`, `:1996-2003`, `:2064-2070`, `:2075`; the dashboard Glossary before `:2088`;
  - layout: `:457` (TF-1), `:500-511` (TF-2), `:1418-1545` (TT-1);
  - detail panel: `:2152`, `:2189`, `:2263`, its Glossary at the panel end; `data-glossary` per TG3-1 to TG3-4 (`:1252`, `:1286`, `:1499`, `:1545`, `:1600`, `:1606`, `:1610`, `:1614`).
- `static/index.js` (`:1290`, `:1678`, the card ledger).
- `templates/performance.html`:
  - copy: `:566`, `:658`, `:668-680`, `:739-750`, `:792-797`; the Performance Glossary;
  - layout: `:83`, `:336-394`, `:690-693`, `:755`; `data-glossary` per TG3-8 to TG3-11.
- `static/performance.js` (the verdict chips of TG3-10).
- `templates/history.html`: `:407`, `:416-430` (CX-8 and the stamp), `:434-451` (TF-4), `:477`, `:490`, `:564`, `:572`; the History Glossary; TG3-12.
- `static/history.js` (numeric classes on the rows it writes).
- `templates/settings.html`: `:965`, `:999`, `:1039`, `:1046-1050`, `:1132`, `:1143-1148`; the Settings Glossary after the five sections; `static/glossary.js` loaded.
- `static/settings.js` (`:205-215`, `:604-615`, TG3-14).
- `static/settings-modal.js` (`:471`, `:490`, `:494-495`, `:727-734`, `:136-158`).
- Tests re-pinned:
  - copy: the dashboard, Performance, History and Settings rows of `tests/ui/test_page_copy_dashboard.py:136-300`, `tests/ui/test_page_copy_performance_history.py:148-266`, `tests/ui/test_page_copy_settings.py:149-266`;
  - browser: `tests/ui/test_page_explainers_dashboard_browser.py:220-364`, `tests/ui/test_page_explainers_performance_history_browser.py:191-280`, `tests/ui/test_page_explainers_settings_browser.py:190-400`;
  - density: `tests/ui/test_dashboard_density_contract.py`, `tests/ui/test_history_performance_density_browser.py:108-131`, `tests/ui/test_history_performance_density_contract.py`;
  - the non-Advisor cases of `tests/ui/test_page_explainers_cross_host.py` and `tests/ui/test_page_explainers_cross_host_browser.py`;
  - `tests/fixtures/dashboard/state_poll_page_baseline/baseline.json`, re-produced by `tests/ui/produce_state_poll_page_baseline.py` (consumer `tests/ui/test_state_poll_page_unchanged_browser.py`).
- `tests/ui/test_page_explainers_dashboard_browser.py:540` (CVaR and sleeves share a row) is kept (D-10).

**C6: cross-host sweep.** Satisfies AC-1 (S-1.1, S-1.2, S-1.3, S-1.4, S-1.13, S-1.14, S-1.15, S-1.17), AC-2 (S-2.2, S-2.16), AC-3 (S-3.1, S-3.2, S-3.3, S-3.8 to S-3.12), AC-4 (S-4.1 to S-4.5, S-4.7 to S-4.12, S-4.15), AC-7 (S-7.1, S-7.2, S-7.4, S-7.9, S-7.11, S-7.13 to S-7.15), all of AC-8, and carries AC-9 as held rows.
- Files added: `tests/ui/test_aligned_tables_browser.py` (every Table T group at every Table W width, every Table L row, the S-1.13 long name, S-1.15 three refreshes) and `tests/ui/test_glossary_titles_browser.py` (S-4.8 over every host, page-script rows included).
- Files modified (test side): the cross-host modules for their whole host lists; `tests/fixtures/dashboard/heading_margin_baseline/baseline.json`, re-produced by `produce_heading_margin_baseline.py` at C6's GREEN head, before cycle-complete (consumer `tests/ui/test_heading_margin_shared_browser.py:121-197`).
- Production files: none planned. atq-c6-impl changes only what a failing sweep names (DC-2, DC-5, DC-6), in the files of C2 to C5.

## Per-Cycle Test Plan
No entity is written: the plan changes no route, record or configuration (Contract Changes and Consumers: none). The census therefore lists the read entry points that carry the matrix rules, each in the matrix column it belongs to.

| Entry point | Rules |
|---|---|
| Server first paint: GET / (app.py:3587 `dashboard`) and the detail panel template (templates/index.html:2095) | AC-1, AC-2, AC-3, AC-4, AC-7 |
| Server first paint: GET /performance (app.py:8766 `performance_page`) | AC-1, AC-2, AC-3, AC-4, AC-7 |
| Server first paint: GET /history (app.py:8748 `history_page`) | AC-1, AC-2, AC-3, AC-4, AC-7 |
| Server first paint: GET /settings (app.py:9654 `settings_page`) | AC-1, AC-2, AC-3, AC-4, AC-7 |
| Server first paint: GET /ai-advisor (app.py:10494 `ai_advisor_tab`), all eight tab panels, the verification count line (app.py:10693) and the tenure cards, including the reload after Run tenure review (static/ai_advisor.js:1986 `retRunReview`) | AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7 |
| Page script refresh or poll: static/index.js poll of GET /api/state rewriting the cards and the detail panel rows (static/index.js:1290, :1678) | AC-1, AC-2, AC-3, AC-4 |
| Page script refresh or poll: static/ai_advisor.js 15 s refresh of GET /api/autotune-runs (app.py:13146) through `autotuneTableHtml` (static/ai_advisor.js:841) | AC-1, AC-4, AC-5 |
| Page script refresh or poll: static/performance.js and static/history.js rows and window picks | AC-1, AC-2, AC-4 |
| Page script refresh or poll: static/glossary.js observer re-stamping titles on rewritten labels | AC-4 |
| Host the page script builds (Settings overrides, settings modal): static/settings.js override rows (:604-615) and Algorithm cards (:205-215) | AC-1, AC-3, AC-4 |
| Host the page script builds (Settings overrides, settings modal): static/settings-modal.js modal body (:460-505) | AC-1, AC-3, AC-4, AC-7 |
| Below 1001 px: every host at 1000, 390 and 320 px | AC-1, AC-3, AC-4, AC-5, AC-6, AC-8 |

Fixture provenance used below:
- FX-full: `seed_world("full")` (`tests/ui/_page_explainers.py:462`), captured droplet state and advisor records through the real writers (`tests/fixtures/page_explainers/PROVENANCE.md`).
- FX-runs: runs through `database.save_autotune_run` by `save_run_like` (`tests/ui/_page_explainers_advisor.py:401`, the captured `autotune_run_reverted_to_fallback.json` and `autotune_run_adopted_ai.json`) and `seed_same_batch_autotune_runs` (`tests/ui/_page_explainers_production_shapes.py:64`). The Reset to Global Default case is built from the producer's literal at `autotuner.py:3121`.
- FX-verify: rows from `advisors.prism_numeric_verifier.verify_cited_numbers` over `tests/fixtures/prism_verifier/verify_cited_numbers_mixed_classifications.json` (`tests/app/test_ai_advisor_tab_verification_redesign.py:664-700`); the malformed-count cases edit one stored summary field, named at the call site.
- FX-tenure: records from the real producer through `tests/app/_tenure_review_panel_builders.py:337`, with the C3 rosters.
- FX-retire: `tests/fixtures/retirement/run_summary_captured.json` (captured RETIREMENT_RUN_SUMMARY), stored through the real writer.
- FX-prism: `tests/fixtures/ai_advisor/market_prism_summary_full.json` (captured MARKET_PRISM row).
- FX-sbfr: `seed_strategy_builder` (`tests/ui/_page_explainers_advisor.py:264`) and `seed_frontrunner_proposals` (`:292`), real writers.
- FX-dash: `seed_dashboard_state` (`tests/ui/_shell_support.py:108`), captured droplet state.
- FX-hist: `seed_history_with_all_four_reasons` (`tests/ui/test_page_copy_performance_history.py:641`), captured post-mortems with one named field edit.
- FX-pre: `_usable_preconditions_body` (`tests/ui/test_page_explainers_performance_history_browser.py:94`), the real route answer with one named field edit.
- FX-copy: `copy.json` regenerated from the plan at ac4563cb.
- FX-ref: `ref_capture.json` captured at c0760e70.

### C1
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-2.15 | AC-2 | static AST scan of tests/ | every test module | new: tests/ui/test_withdrawn_density_rules.py, a guard that no test imports or defines the empty-region or figure-span measures or a 240 by 80 bound (no such guard exists) | the repository's own test tree |
| S-3.4 | AC-3 | fixture unit test | the Copy table A rows of FX-copy with slots filled from the app resolvers (app.py:2682-2747) and their fallback phrases | tests/ui/test_page_copy_advisor.py:133 (fixture premises test, extended to every host's A rows) | FX-copy; slot values from `full_world_values` (tests/ui/_page_explainers_advisor.py:362) |
| S-7.3 | AC-7 | route body comparison | GET /api/state, /api/performance, /api/history/N, /api/retirement-recommendations, /api/guard-alpha-summary, /api/autotune-runs, /api/settings, /health | tests/ui/test_page_explainers_unchanged_surfaces.py:200 (re-pointed to the c0760e70 capture; /health added to ROUTE_PATHS at tests/ui/_page_explainers.py:127) | FX-ref over FX-full |
| S-7.6 | AC-7 | content hash | alpha_bot_execution.py, math_engine.py, database.py, advisors/tenure_review.py, migrations/ | tests/ui/test_page_explainers_unchanged_surfaces.py:249 and :263 | FX-ref (LF-normalised SHA-256 at c0760e70) |
| S-7.7 | AC-7 | content hash, and the suites run unchanged | tests/app/test_dashboard_no_order_path.py and the five tests/security/*no_trade_boundary*.py | tests/ui/test_page_explainers_unchanged_surfaces.py:249 (hashes of the six test files added to the capture) | FX-ref |
| S-7.8 | AC-7 | Flask test client with auth enabled | GET of every host and of an /api route, with and without XHR | tests/app/test_dashboard_auth.py:150 (parametrized over /, /performance, /history, /settings, /ai-advisor) | `auth_client` (tests/app/test_dashboard_auth.py:88) |
| S-7.10 | AC-7 | byte comparison | GET /login, the logout confirm page, `reporting.send_eod_discord_post` | tests/ui/test_page_explainers_unchanged_surfaces.py:230 and :236 | FX-ref; the captured post-mortem day 2026-06-30 |

### C2
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-1.5 | AC-1 | browser layout | GET /ai-advisor, Overview, after the 15 s refresh | tests/ui/test_advisor_autotune_browser.py:242 | FX-runs (12 same-batch rows and 38 older) |
| S-1.10 | AC-1 | browser layout | GET /ai-advisor, Overview observations | tests/ui/test_advisor_observations_browser.py:65 | FX-full; FX-retire |
| S-1.16 | AC-1 | real script over the real page | the autotune refresh answering 500 | tests/ui/test_advisor_refresh_client.py:283 | FX-runs; failing answer from tests/ui/_history_answers.py `unavailable_answer` |
| S-2.1 | AC-2 | browser layout at 1920 and 1440 | GET /ai-advisor | tests/ui/test_page_explainers_advisor_browser.py:793 | FX-full |
| S-2.3 | AC-2 | browser layout | GET /ai-advisor, Overview toolbar | tests/ui/test_advisor_overview_order_browser.py:94 | FX-full |
| S-2.4 | AC-2 | template render | GET /ai-advisor | tests/app/test_retirement_tab_render.py:632 | FX-retire with run_at 2026-11-09 22:51 ET, no cards |
| S-2.5 | AC-2 | real script over the real page, click and Enter | the "Open tab" link | tests/app/test_retirement_tab_client.py:559 | FX-retire |
| S-2.11 | AC-2 | template render | GET /ai-advisor with no run | tests/app/test_retirement_tab_render.py:632 | empty state DB (tests/conftest.py per-worker DB_PATH) |
| S-2.12 | AC-2 | template render | GET /ai-advisor with a failed run | tests/app/test_retirement_tab_render.py:632 | FX-retire with status internal_error (one named field edit) |
| S-2.17 | AC-2 | browser layout | Overview toolbar, symphony picked and Run in progress | tests/ui/test_suggestion_buttons_browser.py:86 | FX-full |
| S-2.20 | AC-2 | template render | GET /ai-advisor with no observations and with the read failing | tests/app/test_advisor_observation_status_lines.py:129 | empty DB; `database.get_advisor_observations` patched to raise |
| S-3.5 | AC-3 | template render | GET /ai-advisor navigation panel | tests/ui/test_page_copy_advisor.py:168 | FX-full |
| S-4.6 | AC-4 | template render | GET /ai-advisor Overview Glossary | tests/ai_advisor/test_r1_guardrail_honesty.py:176 | FX-copy O31a to O31c (resolved from templates/ai_advisor.html:1809-1815 at c0760e70 with the G-33 drop) |
| S-5.1 | AC-5 | real script over the real page | the autotune refresh | tests/ui/test_advisor_refresh_client.py:283 | FX-runs, 12 same-batch Fallback rows |
| S-5.2 | AC-5 | real script over the real page | the autotune refresh | tests/ui/test_advisor_refresh_client.py:283 | FX-runs adopted kind, frozen-eval passed |
| S-5.3 | AC-5 | real script over the real page | the autotune refresh | tests/ui/test_advisor_refresh_client.py:283 | FX-runs with the producer literal of autotuner.py:3121 |
| S-5.4 | AC-5 | real script over the real page | the autotune refresh | tests/ui/test_advisor_refresh_client.py:283 | FX-runs, one batch, mixed kinds |
| S-5.5 | AC-5 | real script over the real page | the autotune refresh | tests/ui/test_advisor_refresh_client.py:283 | FX-runs, different stamps, all reverted |
| S-5.6 | AC-5 | real script over the real page | the autotune refresh | tests/ui/test_advisor_refresh_client.py:283 | FX-runs, different stamps and kinds |
| S-5.7 | AC-5 | real script over the real page | the autotune refresh | tests/ui/test_advisor_refresh_client.py:283 | FX-runs, one row |
| S-5.8 | AC-5 | real script over the real page | the autotune refresh | tests/ui/test_advisor_refresh_client.py:283 | FX-runs with one row storing naive_sharpe (save_run_like extended by one named argument) |
| S-5.9 | AC-5 | real script over the real page | the autotune refresh | tests/ui/test_advisor_refresh_client.py:283 | FX-runs with baseline_decision "Hold" (one named field edit) |
| S-5.10 | AC-5 | real script over the real page | the older-runs disclosure | tests/app/test_advisor_autotune_panel_bounds.py:293 | FX-runs, 38 older and 1 older |
| S-5.11 | AC-5 | template render | GET /ai-advisor verification block | tests/app/test_ai_advisor_tab_verification_redesign.py:726 | FX-verify, 15 pass and 3 unverifiable |
| S-5.12 | AC-5 | template render | GET /ai-advisor verification block | tests/app/test_ai_advisor_tab_verification_redesign.py:726 | FX-verify, every check passing |
| S-5.13 | AC-5 | template render | GET /ai-advisor verification block | tests/app/test_ai_advisor_tab_verification_redesign.py:493 | FX-verify, flags-detected |
| S-5.14 | AC-5 | template render | GET /ai-advisor verification block | tests/app/test_ai_advisor_tab_verification_redesign.py:493 | FX-verify, overrides-detected |
| S-5.15 | AC-5 | unit and render | `_build_verification_count_line` and GET /ai-advisor | tests/app/test_ai_advisor_tab_verification_redesign.py:255 | FX-verify, no-numeric-claims |
| S-5.16 | AC-5 | template render | GET /ai-advisor verification block | tests/app/test_ai_advisor_tab_verification_redesign.py:757 | FX-verify with n_checks removed |
| S-5.17 | AC-5 | real script over the real page | the autotune refresh with no runs | tests/ui/test_advisor_refresh_client.py:283 | empty runs list from the real route |
| S-5.18 | AC-5 | real script over the real page | two refreshes, the second bringing a different outcome | tests/ui/test_advisor_refresh_client.py:283 | FX-runs written between the refreshes |
| S-5.19 | AC-5 | real script over the real page | the refresh answering 500 | tests/ui/test_advisor_refresh_client.py:283 | `unavailable_answer` (tests/ui/_history_answers.py) |
| S-5.20 | AC-5 | real script over the real page | the autotune refresh | tests/ui/test_advisor_density_contract.py:299 | FX-runs, values read from the route body |
| S-5.21 | AC-5 | route body and stored row comparison | GET /api/autotune-runs; the stored MARKET_PRISM_VERIFICATION row | tests/ui/test_page_explainers_unchanged_surfaces.py:200 | FX-ref; FX-verify |
| S-5.22 | AC-5 | template render | GET /ai-advisor verification block | tests/app/test_ai_advisor_tab_verification_redesign.py:757 | FX-verify with n_checks null |
| S-5.23 | AC-5 | unit and render | `_build_verification_count_line` and GET /ai-advisor | tests/app/test_ai_advisor_tab_verification_redesign.py:757 | FX-verify with n_checks the string "18" |
| S-5.24 | AC-5 | unit and render | `_build_verification_count_line` and GET /ai-advisor | tests/app/test_ai_advisor_tab_verification_redesign.py:757 | FX-verify with n_pass removed |
| S-5.25 | AC-5 | unit and render | `_build_verification_count_line` and GET /ai-advisor | tests/app/test_ai_advisor_tab_verification_redesign.py:757 | FX-verify with n_pass null |
| S-5.26 | AC-5 | unit and render | `_build_verification_count_line` and GET /ai-advisor | tests/app/test_ai_advisor_tab_verification_redesign.py:757 | FX-verify with n_pass the string "15" |

### C3
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-1.8 | AC-1 | browser layout at every Table W width | GET /ai-advisor, Retirement | tests/ui/test_page_explainers_production_shapes_browser.py:298 | FX-tenure `undecided_record` (11 entries, one not eligible) |
| S-1.18 | AC-1 | browser layout | an open observation history and an open evidence disclosure | tests/ui/test_advisor_observations_browser.py:65 | FX-full; FX-tenure |
| S-2.9 | AC-2 | browser layout at 1920 and 1440 | Retirement last-run tiles | tests/ui/test_page_explainers_advisor_browser.py:348 | FX-retire |
| S-2.10 | AC-2 | browser layout | the tenure run line | tests/ui/test_page_explainers_advisor_browser.py:368 | FX-tenure |
| S-2.18 | AC-2 | browser layout at 1920 | the two Retirement subsections | tests/ui/test_page_explainers_advisor_browser.py:348 | FX-retire with no cards and FX-tenure `small_record` (tests/app/_tenure_review_panel_builders.py:629), each subsection 320 px or less |
| S-6.1 | AC-6 | template render | GET /ai-advisor tenure card | tests/app/test_retirement_panel_render.py:2209 | FX-tenure `undecided_record` keep entry |
| S-6.2 | AC-6 | template render | GET /ai-advisor tenure card | tests/app/test_retirement_panel_render.py:4079 | FX-tenure `undecided_record` keep entry with a shown pair |
| S-6.3 | AC-6 | browser, click, Enter and Space, then reload | the "Comparisons that decided nothing" disclosure | tests/ui/test_page_explainers_advisor_browser.py:248 | FX-tenure `undecided_record` |
| S-6.4 | AC-6 | template render | GET /ai-advisor tenure card | tests/app/test_retirement_panel_render.py:2209 | FX-tenure `undecided_edge_record`, peer at exactly N days |
| S-6.5 | AC-6 | template render | GET /ai-advisor tenure card | tests/app/test_retirement_panel_render.py:2209 | FX-tenure `undecided_edge_record`, both gate-suffix peers |
| S-6.6 | AC-6 | template render | GET /ai-advisor tenure card | tests/app/test_retirement_panel_render.py:2209 | FX-tenure `undecided_record` entry missing redundancy and backtest |
| S-6.7 | AC-6 | template render | GET /ai-advisor with the resolver raising | tests/app/test_retirement_panel_render.py:2209 | FX-tenure; `_structural_needs_days` patched to raise and to return sys.maxsize |
| S-6.8 | AC-6 | template render | GET /ai-advisor tenure card | tests/app/test_retirement_panel_render.py:2209 | FX-tenure `full_record`, `lone_record`, `solo_record` (tests/app/_tenure_review_panel_builders.py:612, :657, :662) carrying the other three reasons |
| S-6.9 | AC-6 | template render | GET /ai-advisor tenure card | tests/app/test_retirement_panel_render.py:3661 | FX-tenure with missing_items removed (named edit) |
| S-6.10 | AC-6 | template render | GET /ai-advisor tenure card | tests/app/test_retirement_panel_render.py:3661 | FX-tenure with one item's reason removed (named edit) |
| S-6.11 | AC-6 | template render | GET /ai-advisor tenure card | tests/app/test_retirement_panel_render.py:2277 | FX-tenure `full_record` |
| S-6.12 | AC-6 | stored row and route body comparison; file hash | the stored TENURE_REVIEW_RUN row; GET /api/retirement-recommendations; advisors/tenure_review.py | tests/ui/test_page_explainers_unchanged_surfaces.py:200 and :249 | FX-ref; FX-tenure |
| S-6.13 | AC-6 | template render after a stored second run | GET /ai-advisor after Run tenure review | tests/app/test_retirement_panel_render.py:2209 | FX-tenure two runs (RUN_ID_FIRST, RUN_ID_SECOND at tests/app/_tenure_review_panel_builders.py:41) |
| S-6.14 | AC-6 | template render | GET /ai-advisor with an unreadable stored review | tests/app/test_retirement_panel_render.py:3718 | a stored row with unparseable raw_response (named edit) |
| S-6.15 | AC-6 | real script over the real page | approve on a retire-candidate card in progress | tests/app/test_retirement_tab_client.py:559 | FX-tenure retire-candidate entry |
| S-6.16 | AC-6 | template render | GET /ai-advisor tenure card | tests/app/test_retirement_panel_render.py:2209 | FX-tenure `undecided_edge_record`, one peer at 170 days |
| S-6.17 | AC-6 | template render | GET /ai-advisor tenure card | tests/app/test_retirement_panel_render.py:4079 | FX-tenure `undecided_record` entry with no shown pair |
| S-6.18 | AC-6 | template render | the Marginal disclosure and the last run's excluded list | tests/app/test_retirement_panel_render.py:2404 | FX-tenure `apart_record` (tests/app/_tenure_review_panel_builders.py:667); FX-retire with exclusions |
| S-6.19 | AC-6 | template render | GET /ai-advisor tenure card | tests/app/test_retirement_panel_render.py:3661 | FX-tenure with missing_items [] (named edit) |
| S-6.20 | AC-6 | template render | GET /ai-advisor tenure card | tests/app/test_retirement_panel_render.py:3661 | FX-tenure with missing_items null (named edit) |
| S-6.21 | AC-6 | template render | GET /ai-advisor tenure card | tests/app/test_retirement_panel_render.py:3661 | FX-tenure with missing_items a string and a mapping (named edits) |
| S-6.22 | AC-6 | template render | GET /ai-advisor tenure card | tests/app/test_retirement_panel_render.py:2209 | FX-tenure `undecided_edge_record` with the hostile display name |
| S-6.23 | AC-6 | template render | GET /ai-advisor tenure card | tests/app/test_retirement_panel_render.py:2233 | FX-tenure `uncomputed_pair_record` |
| S-6.24 | AC-6 | template render | GET /ai-advisor tenure card | tests/app/test_retirement_panel_render.py:2233 | FX-tenure `uncomputed_pair_record`, holdings overlap only |
| S-6.25 | AC-6 | template render, disclosure opened | GET /ai-advisor tenure card | tests/app/test_retirement_panel_render.py:2404 | FX-tenure `apart_record` (tests/app/_tenure_review_panel_builders.py:667, two peers on another basis from the real producer), a C3 variant of that roster with three shadow-basis peers |
| S-6.26 | AC-6 | template render, disclosure opened | GET /ai-advisor tenure card | tests/app/test_retirement_panel_render.py:2404 | FX-tenure from the roster `_roster_peer_without_composite_on_another_basis` (tests/app/_tenure_review_panel_builders.py:520), one peer on another basis |
| S-6.27 | AC-6 | static source scan | templates/ai_advisor.html, static/*.js and app.py near the tenure line | new: tests/app/test_tenure_needs_days_not_typed.py, scans for the floor's value as a literal and pins the AST of the `floor =` assignment at advisors/tenure_review.py:1533 (no such scan exists; the earlier slice's literal scan at tests/ui/test_page_explainers_cross_host.py:566 covers only its own slots) | the repository's own source; the value from the resolver at run time |
| S-7.5 | AC-7 | Flask test client with the market open | POST /ai-advisor/retirement/run and POST /ai-advisor/retirement/review/run | tests/app/test_retirement_tab_render.py:99 (new class beside it) | `get_market_state` patched to "open"; refusal text from FX-ref |

### C4
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-1.9 | AC-1 | browser layout | GET /ai-advisor, Strategy Builder and Frontrunner Builder | tests/ui/test_page_explainers_advisor_browser.py:530 | FX-sbfr, five proposals and three survivors |
| S-1.19 | AC-1 | browser layout | Evaluate swap and Evaluate logic change results | new: tests/ui/test_swap_logic_result_alignment_browser.py (no browser test renders a swap or logic result today; grep of tests/ui for `stat-row`) | the real routes' answers over FX-full with the Composer backtest client patched at its network boundary (tests/fixtures/composer/backtest_response_v1.json, captured) |
| S-3.6 | AC-3 | template render | GET /ai-advisor, Strategy Builder | tests/ui/test_page_copy_advisor.py:346 | FX-copy CX-9, B00, MU4 |
| S-3.7 | AC-3 | template render with no API key | GET /ai-advisor, Asset Swaps, Logic Changes, Strategy Builder | tests/ui/test_page_copy_advisor.py:227 | FX-full with ANTHROPIC_API_KEY removed |

### C5
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-1.6 | AC-1 | browser layout | GET /performance | tests/ui/test_narrow_containment_browser.py:124 (wide widths added) | FX-full post-mortems, the real /api/performance answer |
| S-1.7 | AC-1 | browser layout | GET /performance turnover | tests/ui/test_narrow_containment_browser.py:124 | FX-full, the real /api/exit-turnover answers |
| S-1.11 | AC-1 | browser layout | GET /history | tests/ui/test_history_exits_table_fit_browser.py:141 | FX-hist |
| S-1.12 | AC-1 | browser layout | GET / | tests/ui/test_dashboard_density_browser.py:106 | FX-dash, 11 Active cards |
| S-1.20 | AC-1 | browser layout | Frontrunner card grid and Settings Algorithm parameters | tests/ui/test_settings_controls_browser.py:389 | FX-sbfr eight proposals; the real GET /api/settings |
| S-1.21 | AC-1 | browser layout | the detail panel and the settings modal opened by their controls | tests/ui/test_page_explainers_dashboard_browser.py:474 | FX-dash; the real GET of the modal's settings route |
| S-2.6 | AC-2 | browser layout at 1920 | /performance, /history, /ai-advisor Overview, / | tests/ui/test_page_explainers_performance_history_browser.py:191 | FX-full; FX-pre; FX-hist |
| S-2.7 | AC-2 | browser layout at 1920 and 1440 | GET / | tests/ui/test_page_explainers_dashboard_browser.py:220 | FX-dash |
| S-2.8 | AC-2 | browser layout at 1920 and 1440 | GET /performance and GET /history | tests/ui/test_page_explainers_performance_history_browser.py:191 | FX-full; FX-hist |
| S-2.13 | AC-2 | browser layout after polls and window picks | GET / | tests/ui/test_dashboard_window_browser.py:37 | FX-dash |
| S-2.14 | AC-2 | browser layout | GET / with /api/guard-alpha-summary answering 500 | tests/ui/test_window_pick_failure_browser.py:1 (new test beside the module's cases) | FX-dash; a 500 answer through the probe's route override |
| S-2.19 | AC-2 | browser layout at 1920 | Market Prism header, a sleeve card, a tenure card | tests/ui/test_page_explainers_production_shapes_browser.py:238 | FX-prism; FX-dash with one sleeve (`_seed_sleeve`, tests/ui/_page_explainers.py:407); FX-tenure |
| S-3.13 | AC-3 | browser | an unsaved Settings edit and a pending modal toggle | tests/ui/test_page_explainers_settings_browser.py:265 and :400 | FX-full |
| S-3.14 | AC-3 | browser | the settings modal opened from a card | tests/ui/test_page_explainers_settings_browser.py:383 | FX-dash |
| S-4.13 | AC-4 | browser | the modal Glossary opened and closed with a pending toggle | tests/ui/test_page_explainers_settings_browser.py:400 | FX-dash |
| S-4.14 | AC-4 | browser | the modal with its settings fetch answering 500 | tests/ui/test_narrow_containment_browser.py:177 (new case beside it) | FX-dash; a 500 answer through the probe's route override |
| S-7.12 | AC-7 | browser | Save with a pending live toggle | tests/ui/test_page_explainers_settings_browser.py:400 | FX-dash |

### C6
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-1.1 | AC-1 | browser layout at 1920 and 2560 | every host and Advisor tab | new: tests/ui/test_aligned_tables_browser.py, the one sweep over Table T (no module measures column edges today) | FX-full with FX-runs, FX-tenure, FX-sbfr, FX-hist, FX-pre |
| S-1.2 | AC-1 | browser layout at 1120, 1290, 1350, 1400, 1420, 1440 | every host | new: tests/ui/test_aligned_tables_browser.py (as S-1.1) | as S-1.1 |
| S-1.3 | AC-1 | browser layout at 1001 | every host | new: tests/ui/test_aligned_tables_browser.py (as S-1.1) | as S-1.1 |
| S-1.4 | AC-1 | browser form signature at 1000 | every host | tests/ui/test_narrow_close_browser.py:59 | FX-ref stacked-form signatures |
| S-1.13 | AC-1 | browser layout | every host with a long symphony name | new: tests/ui/test_aligned_tables_browser.py (as S-1.1) | FX-full with one display name lengthened in the live state (named edit) |
| S-1.14 | AC-1 | browser | every host with no runs, no turnover, no review, no proposals | tests/ui/test_page_explainers_cross_host.py:281 | empty world (`seed_world("empty")`) |
| S-1.15 | AC-1 | browser after three polls and three refreshes | GET / and Overview | tests/ui/test_page_explainers_cross_host.py:667 | FX-dash; FX-runs |
| S-1.17 | AC-1 | browser figure inventory | every Table T group | tests/ui/test_page_explainers_unchanged_browser.py:174 | FX-ref |
| S-2.2 | AC-2 | browser layout at every Table W width | every Table L row | new: tests/ui/test_aligned_tables_browser.py (as S-1.1) | FX-full |
| S-2.16 | AC-2 | browser layout at 1920 and 1440 | every host | new: tests/ui/test_aligned_tables_browser.py (as S-1.1) | FX-full |
| S-3.1 | AC-3 | template render | every host | tests/ui/test_page_copy_advisor.py:150, tests/ui/test_page_copy_dashboard.py:160, tests/ui/test_page_copy_performance_history.py:183, tests/ui/test_page_copy_settings.py:166 | FX-copy over FX-full |
| S-3.2 | AC-3 | template render and browser | every host | tests/ui/test_page_explainers_cross_host.py:415 | FX-copy Copy table X over FX-full |
| S-3.3 | AC-3 | template render, all tab panels | every host | tests/ui/test_page_explainers_cross_host.py:430 | FX-copy old texts (`ref_rows`) |
| S-3.8 | AC-3 | template render | hosts with sections absent | tests/ui/test_page_explainers_cross_host.py:249 and :265 | empty world; FX-dash without a CVaR row |
| S-3.9 | AC-3 | template render | each slot's authority patched to fail | tests/ui/test_page_explainers_cross_host.py:497 | FX-full; resolvers at app.py:2682-2747 patched |
| S-3.10 | AC-3 | browser after polls and tab switches | GET / and /ai-advisor | tests/ui/test_page_explainers_cross_host.py:667 and :693 | FX-dash; FX-full |
| S-3.11 | AC-3 | browser with scripts refused | every host | tests/ui/test_page_explainers_cross_host_browser.py:604 | FX-full |
| S-3.12 | AC-3 | markup comparison | every banner of the scenario | tests/ui/test_page_explainers_unchanged_surfaces.py:140 | FX-ref banner markup |
| S-4.1 | AC-4 | template render, Glossary opened | every host | tests/ui/test_page_explainers_cross_host.py:159 | FX-copy `glossary` |
| S-4.2 | AC-4 | template render | every host | tests/ui/test_page_explainers_cross_host.py:159 | FX-copy `glossary` |
| S-4.3 | AC-4 | template render, all tab panels | every host | tests/ui/test_page_explainers_cross_host.py:159 | FX-full |
| S-4.4 | AC-4 | browser keyboard | every Glossary | tests/ui/test_page_explainers_cross_host_browser.py:491 | FX-full |
| S-4.5 | AC-4 | template render | the Table G2 lines | tests/ui/test_page_explainers_cross_host.py:159 | FX-copy G2 rows with slots from `full_world_values` |
| S-4.7 | AC-4 | template render | every unchanged Table G line | tests/ui/test_page_explainers_cross_host.py:159 | FX-copy `glossary` resolved from the c0760e70 copy.json |
| S-4.8 | AC-4 | browser | every Table G3 element and lead-term label, script rows included | new: tests/ui/test_glossary_titles_browser.py (no test reads titles from a Glossary today) | FX-full; FX-runs; settings.js rows from the real GET /api/settings |
| S-4.9 | AC-4 | template render and browser | every element with a title at the ref | tests/ui/test_page_explainers_cross_host.py:201 | FX-ref token inventory |
| S-4.10 | AC-4 | browser after a poll and a window pick | GET /, Overview, /performance | tests/ui/test_page_explainers_cross_host_browser.py:544 and :564 | FX-dash; FX-runs; FX-full |
| S-4.11 | AC-4 | template render | hosts with sections absent | tests/ui/test_page_explainers_cross_host.py:265 | empty world |
| S-4.12 | AC-4 | browser with scripts refused | every host | tests/ui/test_page_explainers_cross_host_browser.py:604 | FX-full |
| S-4.15 | AC-4 | template render | the header on every page | tests/ui/test_page_explainers_cross_host.py:201 | FX-ref; copy.json rows H02, H07 to H10 |
| S-7.1 | AC-7 | browser figure inventory at 1920, 1440 and 390 | every host | tests/ui/test_page_explainers_unchanged_browser.py:174 | FX-ref |
| S-7.2 | AC-7 | browser control inventory and requests | every host | tests/ui/test_page_explainers_unchanged_browser.py:174 | FX-ref |
| S-7.4 | AC-7 | browser | every basis, window, stamp, caveat and exclusion text | tests/ui/test_page_explainers_cross_host_browser.py:664 | FX-ref; FX-copy for the F16 and guardrail sentences |
| S-7.9 | AC-7 | browser computed style, light and dark | every host | tests/ui/test_page_explainers_cross_host_browser.py:385 | FX-full |
| S-7.11 | AC-7 | browser | absent, null and non-finite figures | tests/ui/test_page_explainers_cross_host_browser.py:751 | FX-full with the named edits of that test |
| S-7.13 | AC-7 | template render | every host's section order | tests/ui/test_page_explainers_unchanged_surfaces.py:180 | FX-ref section order, with the Retirement row exception |
| S-7.14 | AC-7 | template render and browser | /api/state, /api/performance, /api/history/N answering 500 | tests/ui/test_page_explainers_cross_host.py:642 | FX-full; 500 answers |
| S-7.15 | AC-7 | browser | negative, zero, longest and dash figures | tests/ui/test_page_explainers_cross_host_browser.py:751 | FX-full with named edits |
| S-8.1 | AC-8 | browser at 390 by 844 | every host | tests/ui/test_narrow_close_browser.py:59 | FX-ref stacked-form signatures |
| S-8.2 | AC-8 | browser at 390 | every host | tests/ui/test_page_explainers_cross_host_browser.py:285 | FX-copy |
| S-8.3 | AC-8 | browser at 390 | /ai-advisor tab strip | tests/ui/test_page_explainers_advisor_browser.py:602 | FX-full |
| S-8.4 | AC-8 | browser at 320 and 1000 | every host | tests/ui/test_narrow_close_browser.py:59 | FX-ref stacked-form signatures |
| S-8.5 | AC-8 | browser at 320, touch, tap, Tab and Enter | /ai-advisor | tests/ui/test_page_explainers_advisor_browser.py:680 and :704 | FX-full |
| S-8.6 | AC-8 | browser at 390 | every host in the empty world | tests/ui/test_narrow_close_browser.py:59 | empty world |
| S-8.7 | AC-8 | browser | each Glossary opened and closed | tests/ui/test_page_explainers_cross_host_browser.py:465 | FX-full |
| S-8.8 | AC-8 | browser at 390 | a refresh answering 500 | tests/ui/test_page_explainers_cross_host.py:642 | FX-full; 500 answers |
| S-8.9 | AC-8 | browser at 390 | every added or changed text | tests/ui/test_page_explainers_cross_host_browser.py:465 | FX-copy |
| S-8.10 | AC-8 | browser at 390 | an unsaved Settings edit and a section switch | tests/ui/test_page_explainers_settings_browser.py:265 | FX-full |
| S-9.1 | AC-9 | PM manual check, held coverage row | the PM's screenshots of the deploy SHA | new: .claude/review/<deploy-sha12>/visual-check.md, the PM's record (no test can judge it) | the production database copy, read only |
| S-9.2 | AC-9 | PM manual check, held coverage row | the deploy attempt | new: .claude/review/<deploy-sha12>/visual-check.md (as S-9.1) | as S-9.1 |
| S-9.3 | AC-9 | PM manual check, held coverage row | the deploy attempt | new: .claude/review/<deploy-sha12>/visual-check.md (as S-9.1) | as S-9.1 |
| S-9.4 | AC-9 | PM manual check, held coverage row | the deploy attempt | new: .claude/review/<deploy-sha12>/visual-check.md (as S-9.1) | as S-9.1 |
| S-9.5 | AC-9 | PM manual check, held coverage row | the record against the deploy SHA | new: .claude/review/<deploy-sha12>/visual-check.md (as S-9.1) | as S-9.1 |
| S-9.6 | AC-9 | PM manual check, held coverage row | hosts in an empty state | new: .claude/review/<deploy-sha12>/visual-check.md (as S-9.1) | as S-9.1 |
| S-9.7 | AC-9 | PM manual check, held coverage row | the record | new: .claude/review/<deploy-sha12>/visual-check.md (as S-9.1) | as S-9.1 |

Host-local supporting tests: C2 to C5 each extend the per-host copy modules (`tests/ui/test_page_copy_*.py`) for every Copy table A, Copy table X and Glossary line of their hosts. Each also extends the host's entries in `TABLE_T`, `TABLE_L` and `TABLE_F` (`tests/ui/_alignment.py`), so its own RED fails on its own host. C6 records S-3.1, S-3.2, S-4.1, S-4.5 and the AC-1 sweeps against those tests plus its own cross-host ones. Live-backend scope: none in any cycle; the PM's functional check is a Merge Criterion. Screenshot scope: the slice-end ux-expert and capturer pass of the C6 head, plus the Definition of Done's UI evidence at C6.

## Per-Cycle Risk Register
| Cycle | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| C1 | The reference capture runs after a production file has changed, so every AC-7 golden agrees with the head | Medium | AC-7 proves nothing | The RED author runs `produce_ref_capture.py` before any implementer commit; the refusal at `produce_ref_capture.py:72-75` with the reference c0760e70 stops it otherwise |
| C1 | Regenerating `copy.json` in place breaks every superseded test that reads the old A and B rows through `tests/ui/_page_copy.py:28-52` before its cycle re-pins it | High if the old rows are dropped | Red suites on hosts no cycle has reached | The file keeps the ref's rows under `ref_rows`, and the old accessors read them until C6 removes the last consumer |
| C1 | The columns primitive placed at the end of `static/layout.css` breaks the order pins | High if appended | CI red | Insert after `:99` and before `:101`; `tests/ui/test_hp_more_shared_rule.py:88-104` and `tests/ui/test_dashboard_primitives_shared.py` run in C1's set |
| C1 | Browser modules are deselected on CI (`pyproject.toml:88`), so a green CI says nothing about AC-1, AC-2 or AC-8 | Certain | A layout regression ships green | Each closer runs its cycle's browser modules with `-m browser -n 0` and records the counts; the Merge Criteria run them again |
| C2 | Subgrid through `details` and `summary` does not lay out in the Firefox the harness pins (`tests/fixtures/page_explainers/PROVENANCE.md`, Firefox 148.0.2), so the observation lines misalign | Medium | S-1.10 fails | The RED measures it at round 1; the fallback is `display: contents` on `.obs-producer`, decided by the same measurement |
| C2 | The 15 s refresh rewrites the autotune table and drops the header titles (DC-1) | High without the observer | S-4.8 fails after a refresh | `static/glossary.js` re-applies on childList mutations; C6's S-4.8 test reads titles after a refresh |
| C2 | `_build_verification_count_line` gains a parameter, and the unit tests at `tests/app/test_ai_advisor_tab_verification_redesign.py:255-325` call it with one argument | Certain | Red unit tests | The verdict parameter is keyword with default None (no pill count), and the RED author re-pins those tests to AC-5 |
| C2 | The guardrail note's three tests (`tests/ai_advisor/test_r1_guardrail_honesty.py:176`, `tests/ai_advisor/test_advisor_honest_uniform_framing.py:98`, `tests/app/test_advisor_observation_status_lines.py:200`) read a removed element | Certain | Red suites | Re-pinned to Glossary lines O31a to O31c, the DECISIONS.md "AC-14 Guardrail honesty" text word for word apart from the G-33 drop (D-1) |
| C3 | The existing production-shape seed joins undecided peers with "; " (`tests/ui/_page_explainers_production_shapes.py:102`) while the producer joins with ", " (`advisors/tenure_review.py:141`), so a parser tuned to it passes on text the producer never writes | High if reused | AC-6 passes on a fixture, fails in production | AC-6 tests use only records from the real producer (`tests/app/_tenure_review_panel_builders.py:337`); the old seed is retired |
| C3 | `_structural_needs_days` returns `sys.maxsize` for a fraction that cannot reach the minimum (`advisors/tenure_review.py:684-685`), and printing it would state a false count | Low | A wrong number on every card | The resolver returns None for that value, so the line reads the S-6.4 form (S-6.7) |
| C3 | `app.py` repeats the floor expression of `advisors/tenure_review.py:1533`, so the two can drift | Low in this slice | The card states a count the review does not use | S-7.6 pins the file's hash; an AST test pins the `floor =` assignment's expression; the resolver imports both names, never their values |
| C3 | A display name that contains "(N aligned days" is read as a count (KU-1) | Low | The line moves between its two true forms | The filter counts every match, so a name can only add matches and push toward the S-6.4 form; S-6.22 pins it |
| C4 | Dropping `.proposal-cards--fill` changes the Frontrunner card layout that `tests/ui/test_page_explainers_advisor_browser.py:451-530` pins | Certain | Red browser suite | Re-pinned in C4's RED to S-1.9 and S-1.20 |
| C5 | Settings shows one section at a time; a Glossary placed inside a section disappears with it | Medium | S-4.1 fails on Settings | The Glossary sits after the five sections (`templates/settings.html`, before `:1204`), outside every section the script hides |
| C5 | The modal's Glossary markup, written in JavaScript, drifts from the macro's | Medium | S-4.4 passes on pages and fails in the modal | A test compares the element, attributes and summary the macro renders with the markup `_glossary(lines)` writes |
| C5 | The state-poll page baseline (`tests/fixtures/dashboard/state_poll_page_baseline/baseline.json`) records the old dashboard | Certain | `tests/ui/test_state_poll_page_unchanged_browser.py` red | Re-produced by its producer at C5's GREEN head, with the PROVENANCE note naming this slice |
| C6 | The heading-margin baseline (`tests/fixtures/dashboard/heading_margin_baseline/baseline.json`) records every computed style of every page, so every cycle's change breaks it | Certain | `tests/ui/test_heading_margin_shared_browser.py:189-197` red | It is a browser module, so it is not on CI; C6's closer re-produces it at the GREEN head before cycle-complete, and no earlier cycle touches it |
| All | A teammate's bare `git` runs against the primary checkout instead of the worktree | Medium | Commits land on the wrong branch | Every brief requires `cd` into the worktree and a branch and HEAD check before the first edit; the closer checks `git rev-parse HEAD` in the worktree before cycle-complete |
| All | A full or wide pytest run exhausts host memory | Low with the cap | The host reboots | Scoped runs only, at most 4 workers, browser modules with `-n 0`, under `tests/_mem_cap.py` |

## Cycle-Close Criteria
- The RED commit and the GREEN commit or commits are on `feat/aligned-tables-and-quiet-copy` in the shared worktree, and `git rev-parse HEAD` in the worktree equals the SHA the cycle-complete names.
- The coverage record `.claude/review/<HEAD-sha12>/records/coverage.json` holds a row for every scenario this approach assigns to the cycle: a test, or a held row with its reason. In C6 the S-9 rows are held for the PM's deploy gate. Guard scenarios that pass at RED by design (S-2.15, S-7.3, S-7.6, S-7.7, S-7.8, S-7.10, S-7.5, S-5.21, S-6.12) are recorded as guards. `plan-trace check-coverage` exits 0 against this approach and the plan.
- The handoff `.claude/review/<RED-sha12>/handoff.md` names, per test, the behaviour pinned and the worse implementation it rejects.
- Every superseded test this approach lists for the cycle is rewritten to this plan's criteria or deleted with the reason recorded; none is loosened or skipped.
- The cycle's own test files pass: `review-ledger run-set`, `census-run` and `review-ledger close` for the default suites, plus the cycle's browser modules with `-m browser -n 0`, with counts and the HEAD SHA in the cycle-complete.
- The closer runs its own fault checks on the cycle's tests (a planted fault in each new production line the tests must catch), because no reviewer reads a cycle head before the slice-end review.
- `ruff format --check` and `ruff check` pass on the touched Python files (line length 100).
- `tests/js_syntax/test_js_syntax.py` passes for every touched or added script.
- In C1, the regenerated `copy.json` and `ref_capture.json` carry the plan commit and the reference commit in `PROVENANCE.md`, and `extract_copy.py --check ac4563cb` (or the approved plan commit) reports no difference.

## Merge Criteria
The slice merges into main without a PR, as the project's merge rule requires: a merge commit pushed to origin/main as a fast forward, never a force push. The head being merged passes:
- every verdict file of the one slice-end review of the C6 head PASS, and the slice doc-gen's docs-done;
- `ruff format --check`, `ruff check`, and CI green on the exact SHA;
- the browser modules of every cycle, run with `python -m pytest -m "browser and not live and not slow and not perf" -n 0` over the touched `tests/ui/*_browser.py` modules, at that SHA;
- `plan-trace check-coverage` clean for all six coverage records;
- no migration and no `database.py`, engine or `advisors/tenure_review.py` diff (`git diff --stat c0760e70 <SHA> -- alpha_bot_execution.py math_engine.py database.py advisors/tenure_review.py migrations/` empty), which stands in for a migration proof;
- the PM's live functional check after the review: the app served read-only over the production database copy, every page and all eight Advisor tabs read in the browser at 1920, 1440 and 390 px. The check confirms the Retirement row opens its tab, the autotune sentence reads the copy's latest batch, the tenure cards state "181 are needed" with no peer names on a closed card, and every route body of S-7.3 equals the reference;
- the AC-9 visual check, recorded by the PM at the deploy SHA, with no hit;
- after the push, local main reconciled to origin/main.

## Items Raised to the PM
Each item below is answered by the PM, recorded [PM-ASSUMED], and the design above carries the answer.

1. **Fixture provenance for the production shapes (Definition of Done).** Raised: the plan asked for records captured from the 2026-11-10 production database copy, which is not in the repository. Answered [PM-ASSUMED]: the design is accepted. Tenure cases (S-6.*, S-1.8) use real-producer output through `tests/app/_tenure_review_panel_builders.py:337`; autotune and verification cases use the captured fixtures through the real writers (FX-runs, FX-verify). The production database copy is never committed and is never a test fixture; it is used only by the PM's AC-9 visual check and the slice-end ux capture, on a read-only capture server the PM runs.
2. **Matrix cell for AC-1 against "Host the page script builds".** Raised: the matrix marked it n/a while Table T lists TT-22, TT-23 and TT-25 there. Answered [PM-ASSUMED]: agreed; the plan now names S-1.20 and S-1.21 in that cell (G-32, R-48 at ac4563cb). The census and C5 carry both scenarios, and R-48 is on the AC-1 thread row.
3. **S-1.10 and the count field.** Raised: S-1.10 asked the count both to start at one offset and to end at one right edge. Answered [PM-ASSUMED]: confirmed; a count is a numeric column and is compared by its right edge, as the tests do.
4. **Glossary lines O31a and O31c.** Raised: the G2 rule applied word for word gave "Spec Critic: is an active guardrail ..." and "Divergence Explainer: is disabled ...". Answered [PM-ASSUMED]: a Glossary entry may drop the term's own name and a leading "is" or "are" of the moved sentence, every other word kept, giving "Spec Critic: an active guardrail ..." and "Divergence Explainer: disabled by default ..." (G-33, R-49 at ac4563cb). The extractor applies it (Architecture 1), C2's re-pinned guardrail tests read the lines in that form, and R-49 is on the AC-3 and AC-4 thread rows.
5. **Review budget.** Raised: the team size counted a review per cycle head. Answered [PM-ASSUMED]: by the standing rule of one review per plan, at the end, there is one slice-end review of the merged C6 head (one qa, one code-reviewer, one ux-expert with one capturer, one flask-dashboard-specialist), then doc-gen. Team size is 18 (Execution Model); each closer still runs its own census and fault checks.
6. **Serial cycles.** Raised: C5 could run in parallel only on its own worktree. Answered [PM-ASSUMED]: agreed, no parallel C5; the cycles run in the Dispatch Ordering sequence.
