# Approach: Page explainers and denser layouts
Spec: feature-plans/page-explainers-and-density.md
Ref: PlanetStopper@7e6afd02a7e9fe2de181ec333635deafce671cfd

## Architecture
Every file:line below is read at 7e6afd02. That commit equals the plan's ref 1988ce5f in every production path: `git diff --stat 1988ce5f 7e6afd02` lists only the plan and its source file.

**Effect of the security merge on these citations.** The build base is this branch after main merges the shipped security head. That head is 7c237616 at the time of writing. `git diff --stat 1988ce5f 7c237616` shows no change under `templates/` or `static/`, so every template and script citation holds after the merge. It does change `app.py`, in six hunks (`git diff 1988ce5f 7c237616 -- app.py`, hunk heads at 229, 277, 293, 306, 506 and 5420): session revocation code, `login()` and `sse_events()`. None of the functions this design touches is in those hunks. Their line numbers do shift. An `app.py` line L cited here maps to L+33 after the merge when 513 <= L < 5430, and to L+34 when L >= 5430. Lines below 229 do not move. It also changes `database.py` by 40 lines and adds migration 041. That affects S-5.13 (Items Raised).

**1. Where the copy lives (one source).** The expected text of every row is held in one committed fixture, `tests/fixtures/page_explainers/copy.json` (ruling 8). C0 writes it once, by running `tests/fixtures/page_explainers/extract_copy.py` over the plan's Copy tables A, B and C and its Values table (`feature-plans/page-explainers-and-density.md:372-675` at the ref; the revised Values table of ruling 3 when it lands). `PROVENANCE.md` beside it names the plan's path and the commit SHA the plan was read at. No test reads `feature-plans/` at run time, so revising or archiving the plan breaks no test. The test loader `tests/ui/_page_copy.py` reads only `copy.json`. Each row record holds key, table, host, place, builder, kind, old text, new text, placement and true-of, and each slot row of the Values table holds its authority, format and whole-phrase fallback. The extractor resolves every cross reference to text at extraction time:
- "(the dash.hero description)";
- Y14b's "the D29a to D29d definitions without the leading name";
- the MU rows' "resolved: ...; unresolved: ..." pairs;
- the per-state sentences of H02 and H08;
- the R09 fragments;
- the "unchanged" text of kept rows, taken from the ref capture (item 8).

A row the extractor cannot parse stops the extraction with its key, so no row is ever skipped without a failure. `extract_copy.py --check <sha>` re-extracts from the plan at a named commit (through `git show`) and reports any difference from `copy.json`; qa runs it at review. No test types a row's text. A slot is filled by the test from the patched authority, using the Values table's Format column. The test never calls the production formatter, so the expected text never just repeats what the code produced.

Production keeps one copy per builder. Template-built rows are literal text in their template. Script-built rows are literal text in their script. Rows built by both the server and a script (D30a to D30c, D29v, NM6, F12a, F12b, Y06, Y14a) keep one copy in each builder, because the existing design paints server text first and has the script rewrite it (`app.py:3343-3355` with `static/index.js:1036-1040`). Tests that read the plan's single text through both seams keep them equal (DC-1).

**2. Markup convention for added copy.**
- Copy table A: a description is `<p>` or `<div>` in the host's existing note class from the plan's Design-System Mapping. Those classes are:
  - `.dash-panel-note` (`templates/index.html:1121`);
  - `.detail-section-sub` (`templates/index.html:935`);
  - `.metrics-caption` (`templates/performance.html:220`);
  - `.hp-note` (`templates/history.html:72`);
  - `.sec-subtitle` (`templates/settings.html:158`);
  - the modal's `_sectionHeader` caption (`static/settings-modal.js:708`);
  - `.adv-note` (`templates/ai_advisor.html:87`).

  The element carries `data-explainer="<Key>"` and sits directly under its heading. A kept or reworded row adds the same attribute to the element that exists at the ref and adds no new element.
- Copy table B, placement D: one `<details class="hp-more" data-terms="<Copy A key>">` with `<summary>What the terms mean</summary>`, then one `<p data-term="<Copy B key>">` per row, in table order. It sits directly after the `[data-explainer]` element it names, and it has no `open` attribute.
- Copy table B, placement V: no element; the row is carried by its Copy A element.
- Copy table B, placement A (header): see item 5.
- Copy table C: the existing element's text is replaced, and no attribute or class changes. The loader maps each row key to a CSS selector that exists both at the ref and at the head, in `RELABEL_SELECTORS` in `tests/ui/_page_copy.py` (a selector map in test code, not copy). The S-5.1 golden and the AC-7 growth allowance use the same map.
- Placement rule against script rewrites (DC-8): no description or disclosure is placed inside a container a script rewrites with `innerHTML`:
  - `#detail-risk-profile` (`static/index.js:1296`);
  - `#events-timeline` (`:1663`);
  - `#vars-section` (`:1709-1712`);
  - `#autotune-older-list` (`static/ai_advisor.js:841`);
  - the History rows filled by `static/history.js`.

  Every description and disclosure goes between the heading and that container.

**3. The disclosure rule is promoted, not invented.** The History rules for `.hp-more` (`templates/history.html:115-118` and `:290-296`) move to `static/layout.css`, which every page links (`templates/index.html:10`, `templates/history.html:11`, `templates/performance.html:11`, `templates/settings.html:11`, `templates/ai_advisor.html:11`). The class name `.hp-more` is kept, so History's existing element (`templates/history.html:503`) keeps its class (S-3.7). The rules are inserted after the shared focus rule (`static/layout.css:84-87`) and before the first primitive rule, `.hero-section` (`static/layout.css:97`). That position is required: `tests/ui/test_dashboard_primitives_shared.py:261` requires the primitive rules to be a suffix of the sheet, and `.hp-more` is not in `PRIMITIVE_CLASSES` (`tests/ui/_dashboard_primitives_capture.py:37-48`). The shell baseline's `dashboard.resolved` therefore does not change. Search: `grep -n "<details" templates/*.html` finds disclosures only in `ai_advisor.html` (11) and `history.html` (1), and none outside History is a "more" disclosure with a shared look. So no other primitive exists to adopt.

**4. Configured values reach the page through three existing render functions.** C0 adds one helper block to `app.py` after `_tenure_review_settings()` (`app.py:2616-2627`):
- `_plain_number(value, *, decimals)` returns a string, or None when the value is not a finite real number (booleans count as not a number). It rounds to `decimals` places and strips trailing zeros and a bare point.
- `_page_copy_values(slots, *, tenure_review=None, retirement_last_run=None)` returns a dict from slot to string or None. Each slot is resolved in its own `try`, and a failure yields None (S-4.9). Ruling 3: a fallback replaces the whole phrase that holds the slot, never the slot alone. Each template therefore writes the phrase in both forms, `{% if copy_values.<slot> is not none %}<phrase with the value>{% else %}<fallback phrase>{% endif %}`, with both forms taken from the revised Values table through `copy.json`. Each slot reads the authority the Values table names:
  - `tenure_threshold`: `tenure_review.threshold_in_effect` when it is a whole number. Otherwise `tenure_review.parse_threshold(_tenure_review_settings().get(_TENURE_REVIEW_THRESHOLD_KEY)).value`, the exact read at `app.py:2744-2746`, with the module imported lazily.
  - `screen_threshold`: `f"{threshold}"` from an ok `retirement_last_run`, the same expression as the Pairs tile (`app.py:7094-7097`). Otherwise `f"{CORRELATION_SCREEN_THRESHOLD}"` from `advisors/retirement_recommender.py:58`, imported lazily.
  - `stress_fraction_pct`: `_plain_number(STRESS_WINDOW_FRACTION * 100, decimals=1)`, from `advisors/retirement_recommender.py:97`.
  - `watch_trail_pp`: `_plain_number(BACKTEST_TRAIL_WATCH_PP, decimals=6)`, from `advisors/tenure_review.py:49`.
  - `friction_pct`: `_plain_number(autotuner.SIM_EXIT_FRICTION_PCT, decimals=6)`, with the same lazy import as `app.py:6550` (`autotuner.py:1444`).
  - `shadow_retention_days`: `str(int(dotenv_values(ENV_FILE_PATH).get("SHADOW_HISTORY_RETENTION_DAYS", "180")))`, the read the 02:00 prune makes (`app.py:1792`), or None where that read would raise.

  The prune keeps its own inline read, because the plan allows no scheduler change and the prune's failure behavior differs (it raises).

The call sites, each owned by one cycle:
- `_render_dashboard` (`app.py:3642`, C1) passes `copy_values` with `shadow_retention_days` and `friction_pct`.
- `performance_page` (`app.py:8619`, C2) passes `friction_pct`. `min_history_days` is already in context there (`app.py:8621`).
- `ai_advisor_tab` (`app.py:10956`, C4) passes `tenure_threshold`, `screen_threshold`, `stress_fraction_pct` and `watch_trail_pp`.

`ai_advisor_tab` also passes `advisor_suggestion_model_known` and `advisor_synthesis_model_known`, which are `ModelChoice.source != "unavailable"` from the two `model_config.peek` calls at `app.py:10953-10954` (`model_config.py:59-66`). The MU rows branch on these flags instead of printing `MODEL_UNAVAILABLE_TEXT` (`model_config.py:38`).

None of these reads adds a route, a JSON key or a stored value. Values are inserted with Jinja autoescape.

**5. Server-built labels (D-6).**
- `_first_paint_verdict` (`app.py:3343-3355`, C1) takes the D30a to D30c wording. The magnitude is `analytics.js_to_fixed(abs(ga), 1)` as now. The text is "matched" exactly when that magnitude is `"0.0"`, the same test the script makes on `Math.abs(ga).toFixed(1)` in `verdictText` (`static/index.js:1036-1040`).
- `_build_retirement_summary_tiles` (`app.py:7099-7116`, C4) takes the R09a to R09e words. Only the first member of each `gate_keys` pair changes, plus the stress sentence. The summary keys `blocked_*` stay as they are (S-3.7).

Both outputs reach only the template (`app.py:3595`, `app.py:10838`). Neither reaches a JSON body: `/api/state` carries `guard_alpha` and no verdict text (`tests/app/test_api_state_frozen_cards.py:267-292` builds the text on the test side).

**6. Script-built rows.**
- `static/index.js`: the verdict (`:1036-1040`), the VWAP pill (`:2011-2012`), the detail legend (`:2376-2379`).
- `static/chrome.js` (header):
  - `_engineLabelState` (`:351-377`) gains a `description` per state, which `renderEngineLabel` writes to `#engine-status` as `aria-description`. The existing `title` text is untouched (S-2.20). The server render writes the same attribute for the state it renders (`templates/_chrome.html:22-25`).
  - `MODE_INDICATORS` (`:446-449`) gains a `title` per mode, which `updateModeIndicators` (`:452-465`) writes. The template writes the same title (`templates/_chrome.html:57-59`).
  - The account selector, Force run now and Emergency Liquidate get static `title` attributes in `templates/_chrome.html`, at the `workspace-switcher` button and the two buttons that follow the mode pill.
- `static/performance.js`: `:232`, `:243` and the metric label at `:64`. The `win_rate` key is unchanged.
- `static/history.js`: `CANONICAL_REASONS` and its maps (`:193-224`) keep their keys, because they are mapping keys (DC-5). A new `REASON_DISPLAY` map gives the Y14a names, and `REASON_DESCRIPTIONS` takes the Y14b text. The ledger cell label at `:329` follows Y06 (Items Raised).
- `static/settings.js`: the typed-credential status (`:244`), and the S14 disclosure written once under the S14 subtitle, idempotently.
- `static/settings-modal.js`: the four `_sectionHeader` captions (`:471-495`), plus the M04 and M07 disclosures built with `_esc`.
- `static/ai_advisor.js`:
  - `autotuneTableHtml` (`:816-835`) takes an option to lift shared columns, used for the main list only. The older list's call in `renderOlderAutotuneRuns` (`:841`) is unchanged.
  - The "Same for every run" line is a `<p data-testid="autotune-same-line" class="adv-note">` written with `textContent`.
  - The tab strip cues and the Retirement pair layout (item 7).

All new script text is set with `textContent`, or passed through the file's existing escaper (`escHtml`, `_esc`) where the surrounding code builds HTML strings.

**7. Layout (AC-6, AC-7).** Changes are in each page's inline style block, using only the restyle's tokens and primitives:
- Dashboard (`templates/index.html`):
  - The CVaR panel and the Managed sleeves panel (`:1901-1975`) are wrapped in one `.dash-foot-row` flex container whose children use `flex: 1 1 0`. The hidden CVaR panel (`[hidden]`, `:1110`) leaves the sleeves panel alone at full width. The row stacks at the existing 700 px breakpoint (`:729`).
  - The hero (A13) and the dollar panel (A11) take the D01, dash.hero and D21 lines and their disclosures in the hero grid (`.hero-grid`, `:118-124`).
  - The detail panel's View logs, Go to cash and Edit variables buttons (`:1991-1992`, `:2132`) are removed. `.detail-btn` rules are removed where nothing else uses them (`grep -c detail-btn` gives 7 in `templates/index.html` and 0 in `static/`).
- Performance: the 10-row risk metrics table (A3, `templates/performance.html:595-650`) is laid out as two side-by-side groups of rows at widths of 1240 px and up.
- History: the exits table (A4, `templates/history.html:512` onward) uses automatic column widths.
- Settings Master (A9): the 960 px cap is removed (`templates/settings.html:468`).
- Advisor (`templates/ai_advisor.html`):
  - Observations (A6, `:1515-1556`) and the Market Prism rationale cap (A10, `.prism-rationale`, `:782`) use their width.
  - Frontrunner cards (A2): `.proposal-cards` (`:696`) becomes a wrapping flex row whose cards use `flex: 1 1 22rem`, so the last row's cards fill the row. A fixed column count cannot do this, because `auto-fill` varies with width.
  - The Retirement subsections (A1, `:1943`, `:2093`) are wrapped in `.retirement-pair`. A load-time measure in `static/ai_advisor.js` adds `retirement-pair--side` when both subsections are no taller than 320 px in one column. The 700 px breakpoint always stacks them.
  - The tab strip (`.cap-nav`, `:439-449`) gains two `aria-hidden` cue spans, `data-testid="cap-nav-cue-start"` and `cap-nav-cue-end`. Each is an edge fade from `var(--studio-paper)` to transparent. `static/ai_advisor.js` toggles their `hidden` attribute from `scrollLeft`, `scrollWidth` and `clientWidth`: on load, on scroll, on resize, and after the `scrollIntoView` that tab switching makes (`:1076-1077`, `:1102`).

**8. Test harness, from C0 and adopted from the repo.**
- Page start and seeding come from `tests/ui/_shell_support.py`: `open_probe` (`:203`), `open_page` (`:244`), `seed_dashboard_state` (`:108`), `seed_history_exits` (`:115`) and `seed_pending_frontrunner_proposal` (`:163`). The server is `tests/ui/_browser_server.py:36` and the credential-free fixture is `advisor_server` (`tests/ui/_advisor_browser_support.py:100`).
- The clock is fixed as in `tests/ui/_heading_margin_capture.py:36-43` (`CAPTURE_INSTANT`, `WINDOW_ANCHOR`).
- Script runs use the node page harness: `render_page` and `run_page` (`tests/ui/_page_js_harness.py:359`, `:446`).
- The ref capture follows the refusal pattern of `tests/ui/produce_state_poll_page_baseline.py:44-61`, with `tests/app/_state_poll_worlds.py:470` patched to the reference commit.

C0 adds `tests/ui/_page_explainers.py`, which holds:
- `EMPTY_REGION_JS`: the exact empty-rectangle search of AC-6 (Per-Cycle Test Plan, measurement method).
- `FIGURE_SPAN_JS`.
- `HEIGHT_FACTS_JS`: the document height, the summed heights of `[data-explainer]` and of `[data-terms] > summary`, and the heights of the `RELABEL_SELECTORS` matches.
- `seed_world(name)`: the seeded states the Definition of Done names, written through the real writers it lists (`database.save_state`, `database.insert_frontrunner_proposal` at `database.py:4859`, `database.record_cvar_diagnostic` at `:3376`, `database.create_sleeve` at `:4064`, `database.create_sleeve_rule` at `:4177`, `database.save_autotune_run` at `:883`, `database.insert_advisor_observation` at `:1221`, `advisors/strategy_builder_engine.py:637` and `:820`, and `advisors/tenure_review_runner.py:298` over the `prod_droplet` shadow rows).

C0 also adds `tests/fixtures/page_explainers/produce_ref_capture.py`, its record `ref_capture.json` and `PROVENANCE.md`. The record is written once, in a throwaway worktree at 7e6afd02, whose production paths equal 1988ce5f. It holds, per host and seeded world:
- the document height at 375 by 812 and the `RELABEL_SELECTORS` heights (AC-7 baseline);
- at 1440: the figure-element texts (S-5.1), the control inventory (S-5.2), the banner markup (S-5.3), the section-heading order (S-5.5), the kept rows' texts;
- the bodies of the S-5.6 routes;
- the login and logout pages and one Discord payload (S-5.9);
- the engine-file hashes (S-5.13);
- the Firefox version.

**No new dependency, route, migration, scheduler slot or engine change.** `alpha_bot_execution.py`, `math_engine.py`, `database.py` and `migrations/` have zero diff.

## Criterion Thread
| AC | R items | Design element (file:line, or "new: <reason and search>") | Test seam (test file:line; fixture and provenance; harness pattern) | Cycle |
|---|---|---|---|---|
| AC-1 | R-7, R-10, R-11, R-13, R-19, R-24 | new: `tests/fixtures/page_explainers/copy.json` with its extractor `extract_copy.py` and `PROVENANCE.md`, read by `tests/ui/_page_copy.py`, extracted once from the plan's tables (`feature-plans/page-explainers-and-density.md:372-675`). Search: `grep -rln "feature-plans" tests` finds only `tests/meta/test_logic_l_1_brief_symbol_drift.py:54` reading the plans root and no copy fixture | new `tests/ui/test_page_explainers_harness.py`: a text scan of every row in `copy.json`; fixture provenance is the plan at the SHA its `PROVENANCE.md` names | C0 |
| AC-1 | R-7, R-8, R-14, R-19, R-24, R-27 | `templates/index.html:1178`, `:1223`, `:1366`, `:1551`, `:1575`, `:1611`, `:1729`, `:1907`, `:1956`, `:1970`, `:1988-2131`, with `.dash-panel-note` (`:1121`) and `.detail-section-sub` (`:935`) | new `tests/ui/test_page_copy_dashboard.py` (render through the client at `tests/ui/conftest.py:120`) and new `tests/ui/test_page_explainers_dashboard_browser.py` (`tests/ui/_shell_support.py:203` over `seed_dashboard_state` at `:108`, captured `tests/fixtures/prod_droplet/bot_state_positions.json`) | C1 |
| AC-1 | R-7, R-8, R-14, R-24, R-27 | `templates/performance.html:502`, `:574`, `:595-599`, `:652-658`, `:697-702`; `templates/history.html:371`, `:425`, `:446`, `:512` | new `tests/ui/test_page_copy_performance_history.py`; browser via `tests/ui/test_history_performance_density_browser.py:58`'s pattern over `seed_history_exits` (`tests/ui/_shell_support.py:115`, the captured post-mortem days) | C2 |
| AC-1 | R-7, R-8, R-14, R-24 | `templates/settings.html:963`, `:998`, `:1040`, `:1047`, `:1128`, `:1139`; `static/settings-modal.js:471-495` | new `tests/ui/test_page_copy_settings.py` (render, plus the node harness at `tests/ui/_page_js_harness.py:446` for the modal rows); browser pattern `tests/ui/test_settings_controls_browser.py:388` | C3 |
| AC-1 | R-7, R-8, R-14, R-24, R-27 | `templates/ai_advisor.html:1122-1124`, `:1205-1216`, `:1221`, `:1261`, `:1299`, `:1537`, `:1636-1646`, `:1940`, `:1944`, `:2094`, `:2200`, `:2269`, `:2355-2358`, `:2386`, `:2476-2481`, `:2610`, `:2749`, `:2844-2856`, `:2913-2917`, `:2925` | new `tests/ui/test_page_copy_advisor.py`; browser via `tests/ui/_advisor_browser_support.py:161` (`open_advisor`) with the fixtures the plan names (`tests/fixtures/ai_advisor/`, `tests/fixtures/retirement/run_summary_captured.json`) | C4 |
| AC-1 | R-8, R-14, R-27 | new: cross-host sweeps over every `[data-explainer]` on the merged head; no existing module reads every host at once (searched `tests/ui/*_browser.py` for a loop over `_shell_support.PAGES`, `tests/ui/_shell_support.py:52`; the shell modules read headers only) | new `tests/ui/test_page_explainers_cross_host_browser.py` over every `seed_world` state (`tests/ui/_page_explainers.py`, C0) | C5 |
| AC-2 | R-9, R-10, R-11, R-13, R-14, R-24, R-31 | `static/layout.css:84-97` (`.hp-more` promoted from `templates/history.html:115-118`, `:290-296`); header titles at `templates/_chrome.html:22-25`, `:57-59`; `static/chrome.js:351-377`, `:446-465` | `tests/ui/test_page_copy_dashboard.py` (new) for hosts `/`, detail panel and header; `tests/ui/test_header_refresh_client.py:112` (its base-markup pin, extended); the node harness for chrome.js states (pattern `tests/ui/test_header_refresh_client.py:66`) | C1 |
| AC-2 | R-9, R-14, R-24 | disclosures under F01, F13, F16 and F21 at `templates/performance.html:502`, `:595`, `:652`, `:697`, and under Y01 and Y12 at `templates/history.html:371`, `:446` | `tests/ui/test_page_copy_performance_history.py` (new) | C2 |
| AC-2 | R-9, R-14, R-24 | the S11 and S17 disclosures in `templates/settings.html:1047`, `:1139`; S14 by `static/settings.js:490-591`; M04 and M07 by `static/settings-modal.js:471-495` | `tests/ui/test_page_copy_settings.py` (new), the node harness for the script-built disclosures | C3 |
| AC-2 | R-9, R-14, R-24 | disclosures under A01, O03, O08, O15, O27, C01, R02, R22, W01, L01, B03, B07 and X05 at `templates/ai_advisor.html:1122`, `:1221`, `:1261`, `:1299`, `:1537`, `:1636`, `:1944`, `:2094`, `:2200`, `:2269`, `:2476`, `:2610`, `:2925` | `tests/ui/test_page_copy_advisor.py` (new) | C4 |
| AC-2 | R-9, R-14 | new: keyboard, once-per-host and refresh checks over every `[data-terms]` on the merged head; the only disclosure test at the ref is History's (`tests/ui/test_history_performance_density_browser.py:108`) | `tests/ui/test_page_explainers_cross_host_browser.py` (new) | C5 |
| AC-3 | R-10, R-23, R-24, R-26 | `app.py:3343-3355`; `static/index.js:1036-1040`, `:2011-2012`, `:2376-2379`; `templates/index.html:1653`, `:1670`, `:1765`, `:2014`, `:2019`, `:2044-2045`, `:2144`, `:1240`, `:1279`, `:1538` | `tests/dashboard/test_card_verdict_dial_first_paint.py:338` (extended: plan text, matched case, dash case), seeded by its own world; `tests/dashboard/test_card_verdict_basis_label.py:114` | C1 |
| AC-3 | R-10, R-23, R-24, R-26 | `templates/performance.html:530-532`, `:542-543`, `:578`, `:582`, `:605-606`, `:642`, `:696`, `:711`; `static/performance.js:64`, `:232`, `:243`; `templates/history.html:383-385`, `:401`, `:454`, `:464-497`; `static/history.js:193-224`, `:329` | `tests/ui/test_page_copy_performance_history.py` (new); node harness `tests/ui/test_history_refresh_client.py:68` pattern | C2 |
| AC-3 | R-10, R-13, R-23, R-33 | `templates/settings.html:986-989`, `:1009-1011`, `:1032`, `:1047-1053`; `static/settings.js:244` | `tests/ui/test_page_copy_settings.py` (new); `tests/ui/test_settings_controls_browser.py:388` pattern; `POST /api/settings` refusal pinned through the existing client | C3 |
| AC-3 | R-10, R-23, R-24, R-26, R-28 | `app.py:7099-7116`; `app.py:10953-10954`; `templates/ai_advisor.html:1315`, `:2200`, `:2269`, `:2349`, `:2386`, `:2477`, `:2876-2879`, `:2945`, `:2994`, `:3004`, `:3071-3075` | `tests/app/test_retirement_tab_render.py:95` (extended); `tests/ai_advisor/test_r1_attribution_honesty.py:122` (extended); `tests/app/test_frontrunner_proposal_identity_template.py:279` (extended) | C4 |
| AC-3 | R-10, R-11, R-26 | new: old-text-absent and one-name sweeps over every host; DC-1's grep (`grep -rnF` of each Copy table C old text in `templates/`, `static/`, `app.py`) has no test form at the ref | new `tests/ui/test_page_explainers_cross_host.py` | C5 |
| AC-4 | R-12 | new: `_plain_number` and `_page_copy_values` after `app.py:2627`. Search: `grep -n "def _format\|def _plain\|js_to_fixed" app.py analytics.py` finds `_format_data_stamp` (`app.py:4975`, a time stamp) and `analytics.js_to_fixed` (a fixed-decimal mirror of `toFixed`, which keeps trailing zeros), neither of which strips trailing zeros; the tenure read reuses `app.py:2744-2746` | new `tests/app/test_page_copy_values.py` (each slot with its authority patched; `parse_threshold` inputs from S-4.3) | C0 |
| AC-4 | R-12 | `app.py:3642` render kwargs (`copy_values`); D07 and D25 in `templates/index.html` | `tests/ui/test_page_copy_dashboard.py` (new) with an env file holding `SHADOW_HISTORY_RETENTION_DAYS` (pattern `tests/ui/_shell_support.py:330` writes an env file) | C1 |
| AC-4 | R-12 | `app.py:8619` render kwargs; F10 at `templates/performance.html:542` | `tests/ui/test_page_copy_performance_history.py` (new) | C2 |
| AC-4 | R-12 | `app.py:10956` render kwargs; R02, R19b, R22 and R27b in `templates/ai_advisor.html:1944`, `:2094` | `tests/ui/test_page_copy_advisor.py` (new), with `tests/app/test_tenure_review_run_route.py:98`'s store setup for a stored review | C4 |
| AC-4 | R-12 | new: a source scan for slot literals next to `[data-explainer]` and `[data-term]`, and the S-4.7 patch over `app.py:3642` and `app.py:8619` together | `tests/ui/test_page_explainers_cross_host.py` (new) | C5 |
| AC-5 | R-1, R-15, R-16, R-17, R-18, R-21, R-32 | the ref capture (`tests/fixtures/page_explainers/ref_capture.json`, new), compared with each host; `tests/app/test_hygiene_surface_unchanged.py:80` url-map facts adopted for routes; `tests/ui/test_post_controls_request_bytes.py:68` adopted for requests | new `tests/ui/test_page_explainers_unchanged_surfaces.py` and `tests/ui/test_page_explainers_unchanged_browser.py`; `tests/app/test_dashboard_auth.py:147` (extended over every host) | C0 |
| AC-5 | R-17, R-32 | `templates/index.html:1991-1992`, `:2132` removed | `tests/ui/test_page_explainers_dashboard_browser.py` (new) | C1 |
| AC-5 | R-17 | the modal's confirm path, unchanged (`static/settings-modal.js:369-391`) | `tests/ui/test_persymph_settings_modal.py:148` pattern | C3 |
| AC-5 | R-17 | the 409 refusal text of both run routes, unchanged (`static/ai_advisor.js:1894`) | `tests/app/test_tenure_review_run_route.py:98` pattern | C4 |
| AC-5 | R-21 | new: the honesty-text and dash sweeps over every host against the `ref_capture.json` inventory, at `app.py:3436`, `:8594`, `:8612`, `:9499`, `:10338` | `tests/ui/test_page_explainers_cross_host.py` (new) | C5 |
| AC-6 | R-1, R-2, R-3, R-4, R-6, R-25 | `templates/index.html:1896-1975` (`.dash-foot-row`), `:1216-1600` (hero and dollar panel) | `tests/ui/test_dashboard_density_browser.py:77` (extended) and `tests/ui/test_page_explainers_dashboard_browser.py` (new), both with `EMPTY_REGION_JS` from `tests/ui/_page_explainers.py` (C0) | C1 |
| AC-6 | R-2, R-4, R-6 | `templates/performance.html:595-650`, `templates/history.html:512` onward | `tests/ui/test_page_explainers_performance_history_browser.py` (new) | C2 |
| AC-6 | R-2, R-6, R-14 | `templates/settings.html:468` | `tests/ui/test_page_explainers_settings_browser.py` (new) | C3 |
| AC-6 | R-2, R-3, R-6, R-19 | `templates/ai_advisor.html:696`, `:782`, `:1515-1556`, `:1943-2160`; `static/ai_advisor.js` (pair measure) | `tests/ui/test_page_explainers_advisor_browser.py` (new) | C4 |
| AC-6 | R-4, R-6, R-14, R-19 | new: figure-span, token, 1240 px and width sweeps over `.mini-stats` (`static/layout.css:111`), `.hp-figs` (`templates/history.html:139`, `templates/performance.html:146`) and `.adv-figs` (`templates/ai_advisor.html:90`) | `tests/ui/test_page_explainers_cross_host_browser.py` (new) | C5 |
| AC-7 | R-29 | `templates/ai_advisor.html:439-449` (cue spans); `static/ai_advisor.js:1076-1077`, `:1102` | `tests/ui/test_advisor_active_tab_is_in_view.py:119` (node geometry model, extended) and `tests/ui/test_page_explainers_advisor_browser.py` (new, at 375 and 320) | C4 |
| AC-7 | R-5, R-6, R-14 | new: the height bound per host against `tests/fixtures/page_explainers/ref_capture.json` (C0); no height baseline at the ref covers every host (`tests/ui/test_history_performance_density_browser.py:144` bounds History only) | `tests/ui/test_page_explainers_cross_host_browser.py` (new) with `HEIGHT_FACTS_JS` | C5 |
| AC-7 | R-5, R-14 | the History additions under `templates/history.html:371`, `:425`, `:446`, `:512` | `tests/ui/test_history_performance_density_browser.py:131` (unchanged bound, run at the head) | C2 |
| AC-8 | R-30 | `static/ai_advisor.js:749-835` (`autotuneRunRowHtml`, `autotuneTableHtml`); `templates/ai_advisor.html:2945` (card title) | `tests/ui/test_advisor_density_contract.py:119` (extended), `tests/ui/test_advisor_autotune_browser.py:81` (extended), `tests/ui/test_advisor_refresh_client.py:59` (node harness); autotune rows from `tests/fixtures/ai_advisor/autotune_run_reverted_to_fallback.json` and `autotune_run_adopted_ai.json`; proposals through `database.insert_frontrunner_proposal` | C4 |

## Execution Model
One repository, six cycles. Each cycle is one Toxic Pair working in its own worktree and branch, `feat/page-explainers-and-density-c<n>` at `.claude/worktrees/pe-c<n>`. Each brief names the worktree and requires `cd`, `git rev-parse --abbrev-ref HEAD` and `git rev-parse HEAD` before the first edit. The project environment rules apply: isolated worktrees, the pytest memory cap from `tests/_mem_cap.py`, never `-n auto` and never more than 4 workers, browser modules run with `-n 0`. The browser modules need no `.env`, because `advisor_server` empties the environment (`tests/ui/_advisor_browser_support.py:100`). A cycle whose non-browser suites read credentials gets the `.env` copy the project rule names.

| Cycle | Pair (agent names) | Why this pair exists |
|---|---|---|
| C0 shared seams | pe-c0-tw (test-writer), pe-c0-impl (implementer) | Owns every file more than one cycle needs: the copy loader, the measurement harness and the ref capture (test side); the `app.py` copy-value helpers and the `.hp-more` rule in `static/layout.css` (production side); and the AC-5 golden guards that every later head must keep green |
| C1 dashboard, detail panel, header | pe-c1-tw, pe-c1-impl | `templates/index.html`, `static/index.js`, `templates/_chrome.html`, `static/chrome.js`, and `app.py` functions `_first_paint_verdict` and `_render_dashboard` |
| C2 Performance and History | pe-c2-tw, pe-c2-impl | `templates/performance.html`, `static/performance.js`, `templates/history.html`, `static/history.js`, and `app.py` function `performance_page` |
| C3 Settings and the settings modal | pe-c3-tw, pe-c3-impl | `templates/settings.html`, `static/settings.js`, `static/settings-modal.js` |
| C4 AI Advisor, all eight tabs | pe-c4-tw, pe-c4-impl | `templates/ai_advisor.html`, `static/ai_advisor.js`, and `app.py` functions `_build_retirement_summary_tiles` and `ai_advisor_tab`. It is not split by tab: all eight tabs are one template (3113 lines) and one script, and the tab-strip cue, the Retirement pair measure and AC-8 all live in that script |
| C5 merged-head integration | pe-c5-tw, pe-c5-impl | Cross-host scenarios that only the merged head can hold, plus the recapture of the page baselines earlier slices pinned. pe-c5-impl is dispatched only when pe-c5-tw's RED holds a failing test, and it fixes in whichever file the failure names |

Shared files and their owners:
- `app.py` is shared by C0, C1, C2 and C4 at disjoint functions: C0 the new block after `:2627`, C1 `:3343-3355` and `:3642-3680`, C2 `:8612-8625`, C4 `:7073-7135` and `:10956-10986`. Git merges the hunks without overlap.
- `static/layout.css` is C0's alone.
- `tests/fixtures/dashboard/shell_baseline/baseline.json` is C1's alone (the dashboard structure, through `--structure-only`).
- `tests/fixtures/dashboard/heading_margin_baseline/baseline.json` and `tests/fixtures/dashboard/state_poll_page_baseline/baseline.json` are C5's alone, because they record every page (heading margin) or depend on the final dashboard (state poll).

Reviewers run once, on the merged head after C5 closes, as the PM ruled:

| Role | Agent name | Why it is dispatched |
|---|---|---|
| qa | pe-qa | Every head bound for main |
| code-reviewer | pe-cr | The diff changes code in `app.py`, templates and scripts |
| ux-expert | pe-ux-review | The diff renders UI on every page; it judges AC-6, AC-7 and the screenshot comparison of the Definition of Done |
| behavioral-evidence-capturer | pe-capture | ux-expert's evidence member, dispatched in the same round and named in its brief |
| flask-dashboard-specialist | pe-flask | Templates and the three `app.py` render functions are its surface |
| integration-auditor | pe-ia | The server-render and script-rewrite contract of AC-3 (DC-1) spans `app.py`, four templates and five scripts, and the context contract runs from `app.py` into three templates |
| doc-gen | pe-docgen | The slice tail, after every reviewer passes the merged head |

After every reviewer reports PASS, ux-expert runs once more in `Mode: task-walkthrough` (pe-ux-walk, with capturer pe-capture-walk) on the same head, before the doc-gen. Not dispatched:
- `quant-code-reviewer`: no math layer is touched;
- `api-expert`: no route or JSON body changes, and S-5.6 pins them;
- `pipelines-expert`: no scheduler or job changes;
- `security-auditor`: the plan is Sensitive none, so no hidden-mode qa runs either.

Team size: 19 = 6 test-writer + 6 implementer + 1 qa + 1 code-reviewer + 1 ux-expert + 1 behavioral-evidence-capturer + 1 flask-dashboard-specialist + 1 integration-auditor + 1 doc-gen

## Dispatch Ordering
| Cycle | Consumes (cycle: committed artifact) | Branch base |
|---|---|---|
| C0 | | feat/page-explainers-and-density after main merges the shipped security head (7c237616 at writing) into 7e6afd02 |
| C1 | C0: tests/fixtures/page_explainers/copy.json; C0: tests/ui/_page_copy.py; C0: tests/ui/_page_explainers.py; C0: tests/fixtures/page_explainers/ref_capture.json; C0: app.py `_page_copy_values` and `_plain_number`; C0: static/layout.css `.hp-more` rule | feat/page-explainers-and-density-c0 at C0's cycle-complete SHA |
| C2 | C0: tests/fixtures/page_explainers/copy.json; C0: tests/ui/_page_copy.py; C0: tests/ui/_page_explainers.py; C0: tests/fixtures/page_explainers/ref_capture.json; C0: app.py `_page_copy_values` and `_plain_number`; C0: static/layout.css `.hp-more` rule | feat/page-explainers-and-density-c0 at C0's cycle-complete SHA |
| C3 | C0: tests/fixtures/page_explainers/copy.json; C0: tests/ui/_page_copy.py; C0: tests/ui/_page_explainers.py; C0: tests/fixtures/page_explainers/ref_capture.json; C0: static/layout.css `.hp-more` rule | feat/page-explainers-and-density-c0 at C0's cycle-complete SHA |
| C4 | C0: tests/fixtures/page_explainers/copy.json; C0: tests/ui/_page_copy.py; C0: tests/ui/_page_explainers.py; C0: tests/fixtures/page_explainers/ref_capture.json; C0: app.py `_page_copy_values` and `_plain_number`; C0: static/layout.css `.hp-more` rule | feat/page-explainers-and-density-c0 at C0's cycle-complete SHA |
| C5 | C1: templates/index.html, static/index.js, templates/_chrome.html, static/chrome.js; C2: templates/performance.html, static/performance.js, templates/history.html, static/history.js; C3: templates/settings.html, static/settings.js, static/settings-modal.js; C4: templates/ai_advisor.html, static/ai_advisor.js | feat/page-explainers-and-density after main merges C1, C2, C3 and C4 into it, in that order |

C1 to C4 start together on their own pairs as soon as C0's cycle-complete lands. C0's extraction of `copy.json` reads the plan after pe-spec's revision of the Values table (ruling 3), so C0 is dispatched on the approved plan that holds that revision. The reviewers start on C5's cycle-complete SHA.

## Per-Cycle Detail
**C0, shared seams.**
- Satisfies AC-1 (S-1.18), AC-4 (the slot resolvers) and AC-5 (S-5.1 to S-5.3, S-5.5 to S-5.9, S-5.13, S-5.15).
- Files modified:
  - `app.py`: a new helper block after `:2627`, with `_plain_number` and `_page_copy_values`.
  - `static/layout.css`: the `.hp-more` rules, inserted between `:87` and `:89`.
  - `tests/app/test_dashboard_auth.py:147`: parametrized over the five pages.
- Files added:
  - `tests/ui/_page_copy.py`;
  - `tests/ui/_page_explainers.py`;
  - `tests/ui/test_page_explainers_harness.py`;
  - `tests/app/test_page_copy_values.py`;
  - `tests/ui/test_page_explainers_unchanged_surfaces.py`;
  - `tests/ui/test_page_explainers_unchanged_browser.py`;
  - `tests/fixtures/page_explainers/produce_ref_capture.py`, `ref_capture.json`, `extract_copy.py`, `copy.json` and `PROVENANCE.md` (naming the 1988ce5f capture commit and the plan SHA `copy.json` was extracted at).
- Primitives imported:
  - in production: `advisors.tenure_review.parse_threshold` (`advisors/tenure_review.py:430`), `advisors.retirement_recommender.CORRELATION_SCREEN_THRESHOLD` and `STRESS_WINDOW_FRACTION` (`:58`, `:97`), `advisors.tenure_review.BACKTEST_TRAIL_WATCH_PP` (`:49`), `autotuner.SIM_EXIT_FRICTION_PCT` (`autotuner.py:1444`), `_tenure_review_settings` (`app.py:2616`), all lazily imported;
  - in tests: `tests/ui/_shell_support.py`, `tests/ui/_heading_margin_capture.py:36-43`, `tests/app/_state_poll_worlds.py:470`, and `facts.url_map_facts` as used at `tests/app/test_hygiene_surface_unchanged.py:73`.
- First production use: the helper is called at C1, C2 and C4's render sites. At C0's own head it is reached only by its unit tests, and the C0 coverage record says so.

**C1, dashboard, detail panel, header.**
- Satisfies AC-1 (S-1.1, S-1.2), AC-2 (S-2.1, S-2.2, S-2.19, S-2.20), AC-3 (S-3.1 to S-3.5), AC-4 (S-4.12), AC-5 (S-5.14) and AC-6 (S-6.1, S-6.2, S-6.8, S-6.12).
- Copy rows owned:
  - Copy A: D01, dash.hero, dash.rows, D20, D21, D28, D40, D41, D47, D48, dp.lead, D57, dp.events, D59, D66, D67;
  - Copy B: every row whose host is `/`, the detail panel or the header;
  - Copy C: D30a to D30c, D29v, D68, D03r, D06c, NM1 to NM6.
- Files modified:
  - `templates/index.html` (copy, disclosures, `.dash-foot-row`, hero and dollar-panel layout, the three inert buttons removed);
  - `static/index.js` (`:1036-1040`, `:2011-2012`, `:2376-2379`);
  - `templates/_chrome.html` (`:22-25`, `:57-59`, the account and two action buttons);
  - `static/chrome.js` (`:351-377`, `:446-465`);
  - `app.py` (`:3343-3355`, `:3642` kwargs);
  - `tests/fixtures/dashboard/shell_baseline/baseline.json`, rewritten by `capture_shell_baseline.py --structure-only`.
- Pins it updates, each found by `grep -rlF "<old text>" tests --include=*.py` for every Copy table C old text of its rows, rerun by the test-writer and never copied from this list:
  - `tests/dashboard/test_card_verdict_dial_first_paint.py`, `tests/dashboard/test_card_verdict_basis_label.py`, `tests/dashboard/test_card_consistency_liveness.py`;
  - `tests/app/test_api_state_frozen_cards.py:267`;
  - `tests/ui/test_cycle_2_dashboard.py:539`, `tests/ui/test_cycle_2_fix_live_data.py:1271`;
  - `tests/dashboard/test_chart_legend_and_descriptor_follow_the_market_state.py:75`, `tests/dashboard/test_dashboard_figures_one_way.py`, `tests/dashboard/test_dashboard_render_follow.py:201`;
  - `tests/app/test_mdd_window_truth_routes.py:291`;
  - `tests/ui/test_text_carrying_a_figure_clears_contrast.py`;
  - `tests/ui/test_dashboard_density_browser.py:77` (the dollar bound per S-6.8);
  - `tests/ui/test_header_refresh_client.py:112` (base-markup pin: the added header `title` and `aria-description` attributes are named and stripped beside the poll attribute).
- Primitives imported: `_page_copy_values` (C0), `.hp-more` (C0), `analytics.js_to_fixed`.
- First production use: the `aria-description` write in `renderEngineLabel` and the `title` on the mode pill.

**C2, Performance and History.**
- Satisfies AC-1 (S-1.3, S-1.4), AC-2 (S-2.3, S-2.4), AC-3 (S-3.8, S-3.9), AC-4 (S-4.8), AC-6 (S-6.13) and AC-7 (S-7.3).
- Copy rows owned:
  - Copy A: F01, F11, F13, F16, F21, Y01, Y10, Y12, Y15;
  - Copy B: every Performance and History row;
  - Copy C: F04, F10, F12a, F12b, F14a, F14b, F22r, Y02, Y06, Y14a, Y14b, NM7.
- Files modified:
  - `templates/performance.html`;
  - `static/performance.js` (`:64`, `:232`, `:243`);
  - `templates/history.html`, including its `.hp-more` copy at `:115-118` and `:290-296`, which is deleted now that layout.css holds it;
  - `static/history.js` (`:193-224`, `:329`);
  - `app.py` (`:8619` kwargs).
- Pins it updates, by the same grep: `tests/ui/test_cycle_3_performance.py`, `tests/dashboard/test_risk_adjusted_display.py`, `tests/app/test_performance_routes.py`, `tests/app/test_live_dashboard_metrics.py`, `tests/ui/test_history_performance_density_contract.py`, `tests/ui/test_cycle_5_history.py`, `tests/ui/test_f020_history_drilldown.py`, `tests/ui/test_page_skeleton_render.py`, `tests/ui/test_performance_page_data_truth.py`, and the exit-turnover tests that read the page heading.
- First production use: the `REASON_DISPLAY` map in `static/history.js`.

**C3, Settings and the settings modal.**
- Satisfies AC-1 (S-1.5, S-1.6, S-1.22), AC-2 (S-2.5, S-2.16, S-2.17), AC-3 (S-3.16, S-3.17) and AC-5 (S-5.12).
- Copy rows owned:
  - Copy A: S01, S03, S08, S11, S14, S17, M04 to M07;
  - Copy B: every Settings and settings-modal row;
  - Copy C: S02, S05, S07, S12r, S12s.
- Files modified:
  - `templates/settings.html` (copy, and `:468` for A9);
  - `static/settings.js` (`:244`, the S14 disclosure);
  - `static/settings-modal.js` (`:471-495`).
- Pins it updates: `tests/ui/test_settings_controls_browser.py`, `tests/ui/test_settings_unslop_contract.py`, `tests/ui/test_persymph_settings_modal.py`, `tests/ui/test_settings_modal_advisor_section_shows_the_rows_text.py`, `tests/ui/test_cycle_6_settings.py`.
- First production use: the S14 disclosure built by `static/settings.js`.

**C4, AI Advisor.**
- Satisfies AC-1 (S-1.7 to S-1.14, S-1.16, S-1.23), AC-2 (S-2.6 to S-2.10), AC-3 (S-3.6, S-3.13, S-3.14), AC-4 (S-4.1 to S-4.6, S-4.11), AC-5 (S-5.11), AC-6 (S-6.3 to S-6.5), AC-7 (S-7.7 to S-7.10) and AC-8 (S-8.1 to S-8.10).
- Copy rows owned:
  - Copy A: A01, O01, O03, O08, O15, O27, C01, R01, R02, R22, W01, L01, T02, B00, B03, B07, B10, X01, X03, X05;
  - Copy B: every Advisor, Overview, Correlations, Retirement, Asset Swaps, Logic Changes, Strategy Builder and Frontrunner row;
  - Copy C: R09a to R09e, A05, T05, X04, X14, X15, MU1 to MU5, NM8.
- Files modified:
  - `templates/ai_advisor.html`;
  - `static/ai_advisor.js` (`:749-835`, `:1076-1110`, the pair measure);
  - `app.py` (`:7099-7116`, `:10953-10986`).
- Pins it updates, by the same grep:
  - `tests/app/test_retirement_tab_render.py`;
  - `tests/ai_advisor/test_r1_attribution_honesty.py`, `tests/advisors/test_model_unpin_cross_site.py`, `tests/ui/test_asset_swap_panel_generation_reasons.py`;
  - `tests/app/test_frontrunner_proposal_identity_template.py`;
  - `tests/ui/test_advisor_density_contract.py:119`, `tests/ui/test_advisor_autotune_browser.py`, `tests/app/test_bl8_streak_render_and_raw_baseline.py` (the AC-8 changes the plan lists in Earlier criteria);
  - `tests/app/test_advisor_overview_order.py:58` with its source record `tests/fixtures/ai_advisor/ux_overhaul_02/prism_block_source_baseline.txt`, recaptured by its own `capture_prism_block_source.py` (MU1 changes the badge inside that block).
- First production use: the cue toggling and the `retirement-pair--side` measure in `static/ai_advisor.js`.

**C5, merged-head integration.**
- Satisfies the cross-host scenarios listed under C5 in Per-Cycle Test Plan.
- Files added: `tests/ui/test_page_explainers_cross_host.py` and `tests/ui/test_page_explainers_cross_host_browser.py`.
- Files modified:
  - `tests/fixtures/dashboard/heading_margin_baseline/baseline.json` and its `PROVENANCE.md`, recaptured with `produce_heading_margin_baseline.py` at the merged head's production commit, recorded under a new revision note naming this plan's AC-1 and AC-6 as the reason;
  - `tests/fixtures/dashboard/state_poll_page_baseline/baseline.json`, recaptured with `tests/ui/produce_state_poll_page_baseline.py` after its `REFERENCE_COMMIT` (`:44`) is set to that commit;
  - `shell_baseline` again with `--structure-only`, if C5 changes `templates/index.html`.
- A cross-host scenario's per-host test is written in the host's cycle (C1 to C4), as part of that cycle's RED, so each GREEN is driven by a failing test. C5 records the scenario against those tests on the first head that holds every host, and writes only the sweeps that read all hosts at once.
- First production use: none, unless pe-c5-impl fixes a cross-host failure.

## Per-Cycle Test Plan
**Write-path census.** No cycle adds or changes a persisted or verified field, so there is no write path to list. The census below lists every column of the plan's Rule-by-Entry-Point Matrix, plus the code entry points found that reach a matrix rule.

**Measurement method (AC-6).** `EMPTY_REGION_JS` in `tests/ui/_page_explainers.py` (C0) runs this procedure for every panel, card, detail panel and modal surface in the plan's selector list that is displayed.
1. Read the element's content box: `getBoundingClientRect` less its computed border and padding.
2. Collect content rectangles:
   - text glyph boxes, from `Range.getClientRects()` over each non-blank text node (never the element box);
   - the boxes of `input`, `select`, `textarea`, `button`, `canvas`, `svg` and `img`;
   - filled bars: leaf elements with no element children and no text, whose computed background colour has alpha above 0 or whose background image is not `none`.

   Borders and rules are never content. A nested element that is itself in the panel list is measured on its own and also counts as content of its parent, through its own glyph boxes.
3. Clip the rectangles to the content box. Compress the x and y coordinates from the box edges and every rectangle edge.
4. Run the histogram search. For each compressed row band, it accumulates each column's empty height upward. A rectangle of at least 240 by 80 px exists exactly when some row has a run of adjacent columns, each with an accumulated empty height of at least 80, whose summed width is at least 240.

The search is exact: there is no fixed grid and no rounding margin. It returns the largest offending rectangle per panel, so the failure message names it.

`FIGURE_SPAN_JS` measures, for each `.mini-stats`, `.hp-figs` and `.adv-figs`, the extent from the leftmost to the rightmost glyph box of its direct children, divided by the content width of the row's parent element.

**AC-7 method.** The bound is computed per host and world. It is the ref capture's document height, plus the summed heights of `[data-explainer]` and `[data-terms] > summary` at the head, plus, for each `RELABEL_SELECTORS` match, the head height minus the ref height, or 0 when that is negative, plus 16 px.

**Pins this replaces or changes.**
- The dollar-panel bound in `tests/ui/test_dashboard_density_browser.py:89` becomes 160 px plus the measured heights of D21 and its summary line (S-6.8).
- The 112 px card bound (`:38`), the 11 px floor (`:41`) and the History bounds in `tests/ui/test_history_performance_density_browser.py:127` and `:144` stay as they are.
- The AC-8 pins named in the plan's Earlier criteria change in C4.
- The page baselines change in C1 (shell structure) and C5 (heading margin, state poll page).

Measurement runs at 1440 by 900 and 1240 by 900 for AC-6, and at 375 by 812 and 320 by 812 for AC-7. All runs use the light theme. The dark theme is added for S-6.9 and in the capturer's screenshot comparison.

| Entry point | Rules |
|---|---|
| template render: GET / (`app.py:3436`, `_render_dashboard` at `:3453`, render at `:3642`) | AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7 |
| detail panel: `openDetailPanel` in `static/index.js` over the aside at `templates/index.html:1986` | AC-1, AC-2, AC-3, AC-5, AC-6, AC-7 |
| Performance: GET /performance (`app.py:8612`) | AC-1, AC-2, AC-3, AC-4, AC-6, AC-7 |
| History: GET /history (`app.py:8594`) | AC-1, AC-2, AC-3, AC-6, AC-7 |
| Settings: GET /settings (`app.py:9499`) and POST /api/settings (its credential refusal) | AC-1, AC-2, AC-3, AC-5, AC-6, AC-7 |
| settings modal: `static/settings-modal.js` opened from a dashboard card | AC-1, AC-2, AC-3, AC-5, AC-6, AC-7 |
| Overview: GET /ai-advisor (`app.py:10338`), Overview panel (`templates/ai_advisor.html:1206`) | AC-1, AC-2, AC-3, AC-4, AC-6, AC-7, AC-8 |
| Correlations: tab panel at `templates/ai_advisor.html:1631` | AC-1, AC-2, AC-6, AC-7 |
| Retirement: tab panel at `templates/ai_advisor.html:1751`, plus POST /ai-advisor/retirement/run and POST /ai-advisor/retirement/review/run (their 409 refusal) | AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7 |
| Asset Swaps: tab panel at `templates/ai_advisor.html:2195` | AC-1, AC-2, AC-3, AC-6, AC-7 |
| Logic Changes: tab panel at `templates/ai_advisor.html:2264` | AC-1, AC-2, AC-3, AC-6, AC-7 |
| Chat: tab panel in `templates/ai_advisor.html` (`:2340-2360`) | AC-1, AC-3, AC-6, AC-7 |
| Strategy Builder: tab panel in `templates/ai_advisor.html` (`:2380-2760`) | AC-1, AC-2, AC-3, AC-6, AC-7 |
| Frontrunner Builder: tab panel in `templates/ai_advisor.html` (`:2840-3010`) | AC-1, AC-2, AC-3, AC-6, AC-7, AC-8 |
| header, every page: `templates/_chrome.html` and `static/chrome.js` | AC-2, AC-5 |
| script refresh or run (static/*.js): the /api/state poll in `static/index.js`, /api/performance in `static/performance.js`, /api/history/N in `static/history.js`, the 15 s /api/autotune-runs refresh and the post-run reload in `static/ai_advisor.js`, /api/header-status in `static/chrome.js` | AC-1, AC-2, AC-3, AC-5, AC-8 |
| page served with the script failed: each host with its page script refused | AC-2, AC-3 |
| template render: GET /login and the logout confirm page (`app.py:444-522`), unchanged | AC-5 |
| Discord post built by reporting.py | AC-5 |

### C0
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-1.18 | AC-1 | unit, a text scan | every new text in `tests/fixtures/page_explainers/copy.json`, read by `tests/ui/_page_copy.py` | new: `tests/ui/test_page_explainers_harness.py`; no copy fixture exists (`tests/meta/test_logic_l_1_brief_symbol_drift.py:54` only lists the plans root) | `copy.json`, extracted once from the operator-approved plan tables at the SHA named in `tests/fixtures/page_explainers/PROVENANCE.md` |
| S-5.1 | AC-5 | browser | every host at 1440 in each `seed_world` state | new: `tests/ui/test_page_explainers_unchanged_browser.py`, after the golden pattern of `tests/ui/test_state_poll_page_unchanged_browser.py:45` | `tests/fixtures/page_explainers/ref_capture.json`, written at 1988ce5f by `produce_ref_capture.py` through the real writers over the `tests/fixtures/prod_droplet/` captures; Copy table C selectors are excluded through `RELABEL_SELECTORS` |
| S-5.2 | AC-5 | browser, plus the existing request pin | every host and the header controls | new: `tests/ui/test_page_explainers_unchanged_browser.py`; requests through `tests/ui/test_post_controls_request_bytes.py:68`, unchanged | `ref_capture.json` control inventory without the three controls AC-5 excepts; `tests/fixtures/refresh_timing/04-c9/post_requests_at_base.json` (existing record) |
| S-5.3 | AC-5 | render | each banner's host through the Flask client | new: `tests/ui/test_page_explainers_unchanged_surfaces.py`, after `tests/ui/test_page_skeleton_render.py:197` | the banner markup in `ref_capture.json`, captured at the ref |
| S-5.5 | AC-5 | render | each host | new: `tests/ui/test_page_explainers_unchanged_surfaces.py` | the section-heading order in `ref_capture.json` |
| S-5.6 | AC-5 | integration, Flask client | GET /api/state, /api/performance, /api/history/N, /api/retirement-recommendations, /api/guard-alpha-summary, /api/autotune-runs, /api/settings | new: `tests/ui/test_page_explainers_unchanged_surfaces.py` | the bodies in `ref_capture.json`, from the same seeded world with the clock fixed as in `tests/ui/_heading_margin_capture.py:36-43` |
| S-5.7 | AC-5 | unit, git diff plus AST | the branch against the build base | new: `tests/ui/test_page_explainers_unchanged_surfaces.py`; routes through the url-map facts of `tests/app/test_hygiene_surface_unchanged.py:80`; the `run_scheduler` slot calls compared by AST at base and head | git objects at the build base SHA |
| S-5.8 | AC-5 | integration | every host with auth re-enabled | `tests/app/test_dashboard_auth.py:147` | its existing `auth_client` fixture |
| S-5.9 | AC-5 | render plus unit | GET /login, the logout confirm page, the payload `reporting.send_eod_discord_post` builds | new: `tests/ui/test_page_explainers_unchanged_surfaces.py` | `ref_capture.json`; the Discord payload over the captured day `tests/fixtures/prod_droplet/post_mortems/post_mortem_2026-06-30.json` |
| S-5.13 | AC-5 | unit | `alpha_bot_execution.py`, `math_engine.py`, `database.py`, read with LF endings | new: `tests/ui/test_page_explainers_unchanged_surfaces.py` | hashes recorded in `ref_capture.json`: the engine files at 1988ce5f, `database.py` at the build base (Items Raised) |
| S-5.15 | AC-5 | unit (a scan of `templates/` and `static/`) plus browser inventory | every page | new: `tests/ui/test_page_explainers_unchanged_surfaces.py` | the `ref_capture.json` control inventory |

### C1
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-1.1 | AC-1 | render for text, browser for visibility | GET / | new: `tests/ui/test_page_copy_dashboard.py` (data-driven over the loader's host `/` rows) and `tests/ui/test_page_explainers_dashboard_browser.py` (pattern `tests/ui/test_dashboard_density_browser.py:77`) | `seed_world("dashboard_full")`: the captured `bot_state_positions.json` through `database.save_state`, a CVaR row through `database.record_cvar_diagnostic`, no sleeves |
| S-1.2 | AC-1 | browser | a card click, then the detail panel | new: `tests/ui/test_page_explainers_dashboard_browser.py` | same world |
| S-2.1 | AC-2 | render for text, browser for opening | GET /, each `[data-terms]` opened | new: `tests/ui/test_page_copy_dashboard.py` | same world; D07 and D25 filled from the defaults the Values table names |
| S-2.2 | AC-2 | browser | the detail panel's disclosures | new: `tests/ui/test_page_explainers_dashboard_browser.py` | same world |
| S-2.19 | AC-2 | node harness for each chrome.js state; render for the server states | every page's header | `tests/ui/test_header_refresh_client.py:66` (scenarios per state through `header_answer` and `server_error_answer`) | the header answers that file already builds from the real route shapes |
| S-2.20 | AC-2 | unit plus browser | the header | `tests/ui/test_header_refresh_client.py:112`, with its base-markup comparison stripping only the named added attributes; `tests/ui/test_chrome_look_contract.py` and `tests/ui/test_chrome_look_browser.py:75`, run unchanged | `tests/fixtures/refresh_timing/04-c1/header_markup_at_base.json` (existing record) |
| S-3.1 | AC-3 | render | GET / with a triggered symphony | `tests/dashboard/test_card_verdict_dial_first_paint.py:305` | that file's own world (its triggered card); the gap value comes from the world, never typed |
| S-3.2 | AC-3 | node harness | one /api/state poll | `tests/dashboard/test_card_verdict_dial_first_paint.py:338` | same world |
| S-3.3 | AC-3 | render plus node harness | first paint and poll with a negative gap | `tests/dashboard/test_card_verdict_dial_first_paint.py:338` (parametrized) | same world with the gap set through the state writer |
| S-3.4 | AC-3 | render plus node harness | gaps of 0, 0.04 and -0.04 | `tests/dashboard/test_card_verdict_dial_first_paint.py:338` (parametrized) | same |
| S-3.5 | AC-3 | render plus node harness | absent, null and non-finite guard figures | `tests/dashboard/test_card_verdict_dial_first_paint.py:338` (parametrized) | same |
| S-4.12 | AC-4 | render | GET / with an env file holding `SHADOW_HISTORY_RETENTION_DAYS=90` | new: `tests/ui/test_page_copy_dashboard.py` | an env file written by the test, as `tests/ui/_shell_support.py:330` does |
| S-5.14 | AC-5 | browser | the detail panel at 1440 and 375 | new: `tests/ui/test_page_explainers_dashboard_browser.py` | `seed_world("dashboard_full")` |
| S-6.1 | AC-6 | browser | GET / at 1440 | `tests/ui/test_dashboard_density_browser.py:77` | `seed_world("dashboard_full")` |
| S-6.2 | AC-6 | browser | GET / with CVaR and no sleeves | new: `tests/ui/test_page_explainers_dashboard_browser.py` | same |
| S-6.8 | AC-6 | browser | GET / at 1440 | `tests/ui/test_dashboard_density_browser.py:77` | `seed_dashboard_state` (`tests/ui/_shell_support.py:108`) |
| S-6.12 | AC-6 | browser | poll, then the window picker 30d, 1y, 30d | new: `tests/ui/test_page_explainers_dashboard_browser.py`, after `tests/ui/test_dashboard_window_browser.py:37` | `seed_world("dashboard_full")` |

### C2
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-1.3 | AC-1 | render plus browser | GET /performance | new: `tests/ui/test_page_copy_performance_history.py` and `tests/ui/test_page_explainers_performance_history_browser.py` (pattern `tests/ui/test_history_performance_density_browser.py:58`) | `seed_history_exits` (`tests/ui/_shell_support.py:115`), the captured post-mortem days |
| S-1.4 | AC-1 | render plus browser | GET /history | new: same two modules | same |
| S-2.3 | AC-2 | render plus browser | Performance disclosures | new: `tests/ui/test_page_copy_performance_history.py` | same; F24 filled from `SIM_EXIT_FRICTION_PCT` |
| S-2.4 | AC-2 | render plus browser | History disclosures | new: `tests/ui/test_page_copy_performance_history.py` | same |
| S-3.8 | AC-3 | node harness | `static/history.js` over /api/history/N with all four reasons | `tests/ui/test_history_refresh_client.py:68` | the captured days holding the four reasons (`tests/fixtures/prod_droplet/post_mortems/`) |
| S-3.9 | AC-3 | node harness plus render | `static/performance.js` chart and table build | `tests/ui/test_performance_refresh_client.py:74` | the answer that file builds from the route's real shape |
| S-4.8 | AC-4 | render | GET /performance with the insufficient banner | `tests/app/test_performance_routes.py:139` | its `mock_analytics` fixture |
| S-6.13 | AC-6 | browser | /api/exit-turnover or /api/guard-alpha-preconditions answering 500 | `tests/ui/test_exit_turnover_failure_shows_no_zeros.py:121` | its existing failure world |
| S-7.3 | AC-7 | browser | GET /history at 375 | `tests/ui/test_history_performance_density_browser.py:131` | `seed_history_exits` |

### C3
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-1.5 | AC-1 | render plus browser | GET /settings, each side-nav section | new: `tests/ui/test_page_copy_settings.py` and `tests/ui/test_page_explainers_settings_browser.py` (pattern `tests/ui/test_settings_controls_browser.py:388`) | `seed_dashboard_state` and the strategies the settings route reads, through `database.save_state` |
| S-1.6 | AC-1 | node harness plus browser | the modal opened from a card | new: `tests/ui/test_page_copy_settings.py` (run over `render_page("/")`, `tests/ui/_page_js_harness.py:359`) | the modal answer from `tests/ui/test_persymph_settings_modal.py:209`'s route shape |
| S-1.22 | AC-1 | browser | an unsaved edit, then a section switch; a pending modal toggle | new: `tests/ui/test_page_explainers_settings_browser.py`; edit helper `tests/ui/_shell_support.py:400` | same |
| S-2.5 | AC-2 | render plus node harness | Settings and modal disclosures | new: `tests/ui/test_page_copy_settings.py` | same |
| S-2.16 | AC-2 | node harness | the M01 disclosure opened and closed with a pending toggle | `tests/ui/test_settings_modal_ordering_client.py:94` | its symphonies fixture |
| S-2.17 | AC-2 | node harness | the modal settings fetch failing | `tests/ui/test_settings_modal_ordering_client.py:94` | its failure answer |
| S-3.16 | AC-3 | browser | typing a credential | `tests/ui/test_settings_controls_browser.py:388` | its probe fixture |
| S-3.17 | AC-3 | integration | POST /api/settings with a credential | new: `tests/ui/test_page_copy_settings.py`, through the client at `tests/ui/conftest.py:120` | a typed key under the masked-placeholder rules of `tests/app/test_r8_settings_secrets_mask.py` |
| S-5.12 | AC-5 | browser | Save with a pending live toggle | `tests/ui/test_persymph_settings_modal.py:148` | its `mock_db_one_symphony` fixture |

### C4
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-1.7 | AC-1 | render plus browser | GET /ai-advisor, Overview | new: `tests/ui/test_page_copy_advisor.py` and `tests/ui/test_page_explainers_advisor_browser.py` (pattern `tests/ui/_advisor_browser_support.py:161`) | autotune rows from `tests/fixtures/ai_advisor/autotune_run_reverted_to_fallback.json` through `database.save_autotune_run`; `market_prism_summary_full.json`; `ux_overhaul_02/observations.json` |
| S-1.8 | AC-1 | render plus browser | the Correlations tab | new: same | `tests/fixtures/retirement/run_summary_captured.json` through `database.insert_advisor_observation` |
| S-1.9 | AC-1 | render plus browser | the Retirement tab | new: same | the same run, plus a tenure review computed by `advisors/tenure_review_runner.py:298` over `tests/fixtures/prod_droplet/shadow_last_rows.json` |
| S-1.10 | AC-1 | render | the Asset Swaps tab with a key | new: `tests/ui/test_page_copy_advisor.py` | a synthetic key value set by the test, as `tests/fixtures/dashboard/heading_margin_baseline/PROVENANCE.md` describes |
| S-1.11 | AC-1 | render | the Logic Changes tab | new: same | same |
| S-1.12 | AC-1 | render | the Chat tab | new: same | none needed |
| S-1.13 | AC-1 | render | the Strategy Builder tab | new: same | survivors and failures written by `advisors/strategy_builder_engine.py:637` and `:820` |
| S-1.14 | AC-1 | render | the Frontrunner tab | new: same | `seed_pending_frontrunner_proposal` (`tests/ui/_shell_support.py:163`) |
| S-1.16 | AC-1 | render | the three tabs with no key | new: same | the credential-free state of `tests/ui/_advisor_browser_support.py:75` |
| S-1.23 | AC-1 | render | each tab with a dot `title` | new: same | the full advisor world |
| S-2.6 | AC-2 | render plus browser | Overview disclosures and the nav rows A03 and A04 | new: same | the S-1.7 world |
| S-2.7 | AC-2 | render | the Correlations disclosure | new: same | the S-1.8 world |
| S-2.8 | AC-2 | render | Asset Swaps and Logic Changes disclosures | new: same | the S-1.10 world |
| S-2.9 | AC-2 | render | Retirement disclosures | new: same | the S-1.9 world |
| S-2.10 | AC-2 | render | Strategy Builder and Frontrunner disclosures | new: same | the S-1.13 and S-1.14 worlds |
| S-3.6 | AC-3 | render | the Retirement Blocked tile | `tests/app/test_retirement_tab_render.py:95` | `tests/fixtures/retirement/run_summary_captured.json` |
| S-3.13 | AC-3 | render | GET /ai-advisor with resolved models | `tests/ai_advisor/test_r1_attribution_honesty.py:122` | the captured Models page of `tests/llm_request/conftest.py` (`fake`) |
| S-3.14 | AC-3 | render | GET /ai-advisor with unresolved models | `tests/ai_advisor/test_r1_attribution_honesty.py:122` | the empty registry state of `model_config` (`_reset_registry`) |
| S-4.1 | AC-4 | render | the Retirement tab, env file 90 and process 120 | new: `tests/ui/test_page_copy_advisor.py` | an env file written by the test; `app.ENV_FILE_PATH` patched |
| S-4.2 | AC-4 | render | the tab with a stored review at 126 | new: same | a review stored as `advisors/tenure_review_runner.py:298` stores it |
| S-4.3 | AC-4 | render | the tab with each S-4.3 input and no review | new: same (parametrized) | the env inputs the scenario names |
| S-4.4 | AC-4 | render | `CORRELATION_SCREEN_THRESHOLD` patched, no run | new: same | none beyond the patch |
| S-4.5 | AC-4 | render | `STRESS_WINDOW_FRACTION` patched | new: same | same |
| S-4.6 | AC-4 | render | `BACKTEST_TRAIL_WATCH_PP` patched | new: same | same |
| S-4.11 | AC-4 | render | an ok run with `screen_threshold` and the constant patched | new: same | `run_summary_captured.json` |
| S-5.11 | AC-5 | integration plus node harness | the two run routes answering 409 | `tests/app/test_tenure_review_run_route.py:98` and `tests/ui/test_advisor_controls_refusal_client.py:128` | their existing stores and answers |
| S-6.3 | AC-6 | browser | the Retirement tab with exclusions and no cards, no review | new: `tests/ui/test_page_explainers_advisor_browser.py` | a run summary built from `run_summary_captured.json` with its cards removed |
| S-6.4 | AC-6 | browser | three cards and an 11-entry review | new: same | `run_summary_captured.json` plus the review over the captured shadow rows |
| S-6.5 | AC-6 | browser | five pending proposals | new: same | five `database.insert_frontrunner_proposal` writes over `tests/fixtures/advisors/frontrunner/real_tree_07_iaSOOUsmnCJHiZvbrWfs.json` |
| S-7.7 | AC-7 | browser | /ai-advisor at 375 | new: same | the full advisor world |
| S-7.8 | AC-7 | browser | /ai-advisor at 320, scroll, tap and keyboard | new: same; geometry model `tests/ui/test_advisor_active_tab_is_in_view.py:119` | same |
| S-7.9 | AC-7 | node harness plus browser | the strip scrolled to its end and back | `tests/ui/test_advisor_active_tab_is_in_view.py:119` | the harness geometry that file defines |
| S-7.10 | AC-7 | unit (template scan) | the strip's style rules | new: `tests/ui/test_page_copy_advisor.py`, after `tests/ui/test_chrome_look_contract.py:96` | the template source |
| S-8.1 | AC-8 | node harness plus browser | the Overview autotune table | `tests/ui/test_advisor_refresh_client.py:59` and `tests/ui/test_advisor_autotune_browser.py:81` | 12 rows from `autotune_run_reverted_to_fallback.json`, one per captured symphony |
| S-8.2 | AC-8 | node harness | one row's Outcome differs | `tests/ui/test_advisor_refresh_client.py:59` | the same rows plus one from `autotune_run_adopted_ai.json` |
| S-8.3 | AC-8 | node harness | exactly one row | `tests/ui/test_advisor_refresh_client.py:59` | one row from `autotune_run_reverted_to_fallback.json` |
| S-8.4 | AC-8 | node harness | no rows | `tests/ui/test_advisor_refresh_client.py:59` | an empty run list from the route's real shape |
| S-8.5 | AC-8 | node harness | every table state, plus the older-runs list | `tests/ui/test_advisor_density_contract.py:119` | the S-8.1 and S-8.2 rows |
| S-8.6 | AC-8 | node harness | the 15 s refresh bringing a differing row | `tests/ui/test_advisor_refresh_client.py:59` | the same rows |
| S-8.7 | AC-8 | node harness | the refresh failing | `tests/ui/test_advisor_refresh_client.py:59` | that module's failure answer |
| S-8.8 | AC-8 | render | five proposals, ids 31 to 35 | `tests/app/test_frontrunner_proposal_identity_template.py:280` | proposals written through `database.insert_frontrunner_proposal`; ids from the writer |
| S-8.9 | AC-8 | render | an incumbent with no resolved name | `tests/app/test_frontrunner_proposal_identity_template.py:290` | same writer |
| S-8.10 | AC-8 | render | each card's attributes, controls and figures | `tests/ui/test_frontrunner_card_labels_its_figures_as_backtest.py:101` | same writer |

### C5
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-1.15 | AC-1 | render plus browser | every host in its empty state | new: `tests/ui/test_page_explainers_cross_host_browser.py`, recording the per-host empty tests of C1 to C4 | `seed_world("empty")`: an initialised database with no rows |
| S-1.17 | AC-1 | browser | every host at 375 by 812 | new: same | each `seed_world` state |
| S-1.19 | AC-1 | browser | every `[data-explainer]` | new: same | same |
| S-1.20 | AC-1 | node harness | the four routes answering 500 | new: `tests/ui/test_page_explainers_cross_host.py`, pattern `tests/ui/test_dashboard_recovery_browser.py:56` | the failure answers of the existing refresh-client modules |
| S-1.21 | AC-1 | node harness | three polls; a tab chosen, left and chosen again | new: same | the S-1.1 and S-1.7 worlds |
| S-2.11 | AC-2 | render | every host | new: same | each world |
| S-2.12 | AC-2 | browser | every `[data-terms]` by keyboard | new: `tests/ui/test_page_explainers_cross_host_browser.py` | each world |
| S-2.13 | AC-2 | render | the hosts of D10, D16, O09, R12, S16 and A03 | new: `tests/ui/test_page_explainers_cross_host.py` | the C1, C3 and C4 worlds |
| S-2.14 | AC-2 | browser | an opened disclosure on /, Overview and Performance through a refresh | new: `tests/ui/test_page_explainers_cross_host_browser.py`, after `tests/ui/test_advisor_observations_browser.py:65` | same |
| S-2.15 | AC-2 | render | CVaR hidden, no proposals | new: `tests/ui/test_page_explainers_cross_host.py` | `seed_world("empty")` |
| S-2.18 | AC-2 | browser | each host with its script refused | new: `tests/ui/test_page_explainers_cross_host_browser.py` (route refusal as in `tests/ui/_shell_support.py:224`) | each world |
| S-3.7 | AC-3 | unit (git diff plus scan) | every reworded label | new: `tests/ui/test_page_explainers_cross_host.py` | the ref capture's attribute and class inventory |
| S-3.10 | AC-3 | node harness | five dashboard polls and five Overview refreshes | new: same | the S-1.1 and S-1.7 worlds |
| S-3.11 | AC-3 | browser | each host with its script refused | new: `tests/ui/test_page_explainers_cross_host_browser.py` | each world |
| S-3.12 | AC-3 | render plus browser | every Copy table C row on its host; old text absent | new: `tests/ui/test_page_explainers_cross_host.py`, recording the per-host modules of C1 to C4 | each world |
| S-3.15 | AC-3 | render plus browser | every host | new: same | each world |
| S-4.7 | AC-4 | render | / and /performance with `SIM_EXIT_FRICTION_PCT` patched | new: same | the C1 and C2 worlds |
| S-4.9 | AC-4 | render | each slot's authority raising or yielding None | new: same | each world, with the authority patched |
| S-4.10 | AC-4 | unit (source scan) | `templates/` and `static/` near `[data-explainer]` and `[data-term]` | new: same | the source tree at the head |
| S-5.4 | AC-5 | render | every host | new: same | the honesty inventory in `ref_capture.json` |
| S-5.10 | AC-5 | render | absent, null and non-finite figures on every host | new: same | worlds with those figures set through the writers |
| S-6.6 | AC-6 | browser | Performance, History, Overview, Chat, Correlations, Settings Master | new: `tests/ui/test_page_explainers_cross_host_browser.py`, recording the per-host modules of C2, C3 and C4 | the worlds the scenario names |
| S-6.7 | AC-6 | browser | dashboard hero, History hero, Performance headline, Retirement tiles | new: same | the C1, C2 and C4 worlds |
| S-6.9 | AC-6 | browser plus unit | every host, light and dark | new: same, after `tests/ui/test_history_performance_density_browser.py:58` | each world |
| S-6.10 | AC-6 | browser | every host at 1240 by 900 | new: same | each world |
| S-6.11 | AC-6 | browser | the empty states at 1440 | new: same | `seed_world("empty")` |
| S-6.14 | AC-6 | browser | every host at 1440 | new: same | each world |
| S-7.1 | AC-7 | browser | every host at 375 | new: same | the `ref_capture.json` heights at 1988ce5f |
| S-7.2 | AC-7 | browser | every host at 375 and 320 | new: same, after `tests/ui/test_narrow_containment_browser.py:124` | each world |
| S-7.4 | AC-7 | browser | every added text at 375 | new: same, with the small-text scan of `tests/ui/test_dashboard_density_browser.py:50` | each world |
| S-7.5 | AC-7 | browser | the empty states at 375 | new: same | the `ref_capture.json` empty-state heights |
| S-7.6 | AC-7 | browser | every disclosure opened and closed | new: same | each world |

## Per-Cycle Risk Register
| Cycle | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| C0 | The `.hp-more` rules appended at the end of `static/layout.css` break the suffix pin `tests/ui/test_dashboard_primitives_shared.py:261` and the one-definition pin `:272` | High if appended | CI red on every later head | Insert them before the first primitive rule (`static/layout.css:97`), after the focus rule (`:84-87`); `.hp-more` is not in `PRIMITIVE_CLASSES` (`tests/ui/_dashboard_primitives_capture.py:37-48`) |
| C0 | `copy.json` drifts from the plan once the plan is revised, so tests assert text the operator no longer approved | Medium | A wrong row passes review | `PROVENANCE.md` names the plan SHA; `extract_copy.py --check <sha>` reports any difference; qa runs it against the approved plan at review (ruling 8); a plan revision after approval is a new dispatch that re-extracts |
| C0 | The ref capture is taken from a tree that is not the ref, so goldens agree with the head | Medium | Every AC-5 golden proves nothing | `produce_ref_capture.py` uses the refusal of `tests/app/_state_poll_worlds.py:470` with the reference set to 1988ce5f, and runs only in a throwaway worktree at 7e6afd02 |
| C0 | The pages' clock-derived texts move between capture and check | Medium | False S-5.1 failures | Reuse `CAPTURE_INSTANT` and `WINDOW_ANCHOR` (`tests/ui/_heading_margin_capture.py:36-43`) and record the Firefox version, failing with a recapture message on a mismatch (the heading-margin pattern) |
| C0 | S-5.13 cannot hold for `database.py` against 1988ce5f once the security head is merged (it adds 40 lines to `database.py`) | Certain | S-5.13 red by construction | Record `database.py`'s hash from the build base; raised to the PM |
| C1 | Title attributes on the header controls fail `tests/ui/test_header_refresh_client.py:112`, which compares the nav with a base record | Certain | A pin outside the plan's Earlier criteria goes red | C1's RED changes that comparison to strip the named added attributes only; raised to the PM as an unlisted pin |
| C1 | Adding descriptions changes the dashboard's element structure, failing the CI-gated `tests/ui/test_dashboard_primitives_shared.py:360` and the shell baseline | Certain | CI red | C1 rewrites `structure_outside_top_bar` with `capture_shell_baseline.py --structure-only`, the path that script provides for a page that grew elements |
| C1 | The poll writes the old verdict or pill text after a first paint with the new one (DC-1) | Medium | S-3.2 fails in production while render tests pass | `tests/dashboard/test_card_verdict_dial_first_paint.py:338` runs the real `static/index.js` over the real page and compares with the plan text, with the matched and dash cases parametrized |
| C1 | `aria-description` is not read by the capturer's accessibility tree in Firefox | Low | S-2.19 unproven at the browser level | The node harness asserts the attribute per state; the capturer reads the attribute directly |
| C2 | Renaming History's reason keys breaks the mapping maps (`static/history.js:193-224`) and the stored `exit_reason` values (DC-5) | Medium | Wrong colours or descriptions, or a JSON change | Keys stay; a `REASON_DISPLAY` map carries the Y14a names |
| C2 | The ledger cell label `data-label="Triggers"` renders visibly through `content: attr(data-label)` (`templates/history.html:347`), and Y06 renames it while S-3.7 says every `data-*` value stays | Certain | A contradiction in the plan | The design follows Y06 for `data-label` only; raised to the PM |
| C2 | A narrower metrics table moves the blank channel to the panel's right edge, so the empty-region check still fails (A3) | High if done that way | S-6.6 red | Two side-by-side row groups at 1240 px and up, measured by `EMPTY_REGION_JS` in C2's own browser module before cycle-complete |
| C3 | The S14 disclosure built by `static/settings.js` is appended on each re-render (DC-8) | Medium | S-1.21 and S-2.14 duplicate lines | The builder looks for `[data-terms="S14"]` before it creates one |
| C4 | Lifting a column whose text is the visually hidden header label (`static/ai_advisor.js:746-748`, `:796-799`) drops the label a phone row relies on | Medium | S-8.5 fails at 375 | Compare per column on the visible cell value; the lifted line carries the header text; the older list keeps its full rows (`:841`) |
| C4 | The MU rows keep printing `MODEL_UNAVAILABLE_TEXT` (`model_config.py:38`) through `badge_text` | High without the flag | S-3.14 red | Branch on `advisor_*_model_known` from `ModelChoice.source` (`model_config.py:59-66`) |
| C4 | `tests/app/test_advisor_overview_order.py:58` compares the Market Prism block's source with a recorded copy, and MU1 changes that block | Certain | A pin goes red | Recapture with `tests/fixtures/ai_advisor/ux_overhaul_02/capture_prism_block_source.py` in C4 |
| C4 | Playwright's Firefox has no mobile emulation (`isMobile`), so "touch scrolling" in S-7.8 is proven by native overflow scrolling, programmatic scroll and click instead of a real gesture | Medium | Touch behaviour unproven in CI | The strip keeps `overflow-x: auto` (`templates/ai_advisor.html:447`); the PM's live phone check covers the gesture |
| C4 | The Retirement pair measure runs before fonts load and misjudges height | Low | S-6.3 or S-6.4 flips | Measure after `document.fonts.ready` and again on resize |
| C5 | The heading-margin and state-poll page baselines are recaptured over a head that still has a defect, freezing it | Medium | A later refactor check protects a defect | Recapture only after every other C5 test is green, at the production commit the reviewers will judge, with a revision note naming the plan |
| C5 | Production states with longer names or more rows open regions the seeded worlds do not (plan KU-3) | Medium | A live page fails AC-6 | The PM's live look at 1440 on the droplet copy; the capturer's screenshot comparison |
| C1, C2, C4 | Hunks in the shared `app.py` conflict at merge | Low | A merge stall before C5 | Disjoint functions (`:2627` block C0, `:3343` and `:3642` C1, `:8612` C2, `:7073` and `:10956` C4); main merges C1, C2, C3, C4 in that order |

## Cycle-Close Criteria
- Every scenario assigned to the cycle has a test row in `.claude/review/<HEAD-sha12>/records/coverage.json`, or a held row with its reason. `plan-trace check-coverage` passes for that record with `--approach` naming this document.
- The cycle's RED commit and GREEN commit or commits are named in its cycle-complete, and nothing is left uncommitted.
- `ruff format --check` and `ruff check` are clean.
- The non-browser suites of the touched directories pass with the memory cap and at most 4 workers: `tests/ui/`, `tests/app/`, `tests/dashboard/`, `tests/js_syntax/`, and, for C4, `tests/ai_advisor/`.
- The cycle's own browser modules pass with `-m browser -n 0`. C0's AC-5 guard modules (`tests/ui/test_page_explainers_unchanged_surfaces.py`, `tests/ui/test_page_explainers_unchanged_browser.py`) pass at the cycle head, apart from the Copy table C selectors the harness excludes.
- C1 to C4 also run the C0 harness's `EMPTY_REGION_JS` and the AC-7 height bound over their own hosts and pass, so no cross-host scenario first fails in C5.
- The page baselines that only C5 recaptures (heading margin and state poll page) are listed in C1 to C4's coverage records as held, with this document's C5 as the reason. Every other pin a cycle's change touches is updated in that cycle's RED.
- `git diff` of `alpha_bot_execution.py`, `math_engine.py`, `database.py` and `migrations/` against the build base is empty.

## Merge Criteria
These apply to the one merge of `feat/page-explainers-and-density` into main, at the head the reviewers judged. The merge goes by pull request or direct push, by the PM's standing project rule.
- CI is green on that exact SHA: ruff, and the full pytest under the default addopts.
- The browser modules of this plan, the heading-margin, state-poll page and density browser modules, and the chrome look browser module pass with `-m browser -n 0` on that SHA.
- `qa`, `code-reviewer`, `ux-expert`, `flask-dashboard-specialist` and `integration-auditor` verdict files read PASS for that SHA, and the task walkthrough is PASS.
- The doc-gen record is present. It covers the Docs to Update list of the plan.
- `git diff` of `alpha_bot_execution.py`, `math_engine.py`, `database.py` and `migrations/` against the build base is empty, and `tests/app/test_dashboard_no_order_path.py` and every `tests/security/*no_trade_boundary*.py` pass unchanged.
- No migration, so no migration proof; no persisted field, so no persistence roundtrip.
- The PM's live functional check after the review runs on the real environment: the running daemon with the droplet database copy. Every page and tab is opened at 1440 and at 375, each "What the terms mean" disclosure is opened, a triggered card's verdict is read before and after a poll, and the Advisor tab strip is scrolled on a real phone.
- The capturer's per-host screenshot comparison at 1440 and 375, light and dark, against the audit's captures, is judged by `ux-expert` against AC-6 and AC-7.

## Items Raised to the PM
1. **Y06 against S-3.7.** Y06 renames "Triggers" to "Exits" in History's `data-label` cells (`templates/history.html:467`, `:476`, `:485`, `:494`; `static/history.js:329`). Those attributes render as visible text on a phone through `content: attr(data-label)` (`templates/history.html:347`). S-3.7 says every `data-*` value stays unchanged. The design follows Y06 for `data-label` only. To settle: confirm that S-3.7's `data-*` clause excludes a `data-label` whose only use is visible text, or rule that Y06 drops those cells.
   Ruled: Y06 wins for `data-label` only, because it is visible text on a phone. S-3.7's "every data-* value unchanged" gains that one stated exception; the C5 S-3.7 test excludes exactly the Y06 `data-label` cells.
2. **S-5.13 for `database.py`.** S-5.13 requires the SHA-256 of `database.py` to equal its value at 1988ce5f. The build base merges the security head, which adds 40 lines to `database.py` (`git diff --stat 1988ce5f 7c237616`). The design records `database.py`'s hash from the build base and the two engine files' hashes from 1988ce5f, where they are unchanged. To settle: confirm the build base as the reference for `database.py`.
   Ruled: approved. The `database.py` hash comes from the build base after the security merge, and the engine-file hashes from 1988ce5f.
3. **Fallback words read as broken sentences when put in the slot as written.** The Values table's fallback words are inserted in place of the slot. For `{shadow_retention_days}`, `{friction_pct}`, `{watch_trail_pp}` and `{screen_threshold}` that gives "kept for the last a set number of days", "after taking a fixed cost of percentage points off", "by more than a set number of percentage points" and "a correlation of the screen or more". To settle: confirm, or give whole-phrase fallbacks as the table already does for `{stress_fraction_pct}`.
   Ruled: not as written. Every fallback replaces the whole phrase that holds the slot, so the sentence reads correctly; pe-spec revises the Values table to give one whole fallback phrase per slot. The design is against that revision: Architecture item 4 writes each phrase in both forms, `copy.json` carries both, and C0 extracts after the revision lands.
4. **An AC-8 column that is empty in every row.** When a column's text is empty in every row (the streak cell for runs with no streak, `static/ai_advisor.js:776-783`), the design leaves that column in the rows and does not list it in the "Same for every run" line, because there is no text to list. To settle: confirm.
   Ruled: confirmed. A column empty in every row is not listed in "Same for every run".
5. **Pins outside the plan's Earlier criteria that this design changes.** Every one is changed by the RED author named, with the reason in its record:
   - `tests/ui/test_header_refresh_client.py:112`, the header markup at base (C1);
   - `tests/fixtures/dashboard/shell_baseline/baseline.json`, the structure only (C1);
   - `tests/app/test_advisor_overview_order.py:58` with `prism_block_source_baseline.txt` (C4);
   - `tests/fixtures/dashboard/heading_margin_baseline/baseline.json` and `tests/fixtures/dashboard/state_poll_page_baseline/baseline.json`, recaptured at the merged head (C5).

   To settle: confirm that refactor-equivalence baselines recorded by earlier slices are recaptured at this plan's head.
   Ruled: confirmed. The named RED authors change or recapture those pins and baselines, with the reason and provenance in each record.
6. **Census entry point outside the matrix.** "Discord post built by reporting.py" reaches AC-5 through S-5.9 and has no column in the plan's Rule-by-Entry-Point Matrix. It is carried in C0 (S-5.9) as a pinned-unchanged surface. To settle: no action needed unless the matrix should gain the column.
   Ruled: no action needed.
7. **C5 runs after the four parallel cycles and before review.** The cross-host scenarios need every host on one head. C5's implementer is dispatched only if C5's RED fails. To settle: confirm this integration cycle ahead of the single review round you ruled.
   Ruled: confirmed. C5 runs after C1 to C4 and before the single review round.
8. **Where the expected copy is read from (raised by the PM).**
   Ruled: tests never parse the plan file at run time, because plans are revised and archived. C0 extracts the three copy tables and the Values table once into the committed fixture `tests/fixtures/page_explainers/copy.json`, with a provenance note naming the plan SHA, and every test reads the fixture; qa checks the fixture against the plan at review. Carried in Architecture item 1, the C0 rows of the Criterion Thread and the test plan, Dispatch Ordering and the C0 risk rows.
