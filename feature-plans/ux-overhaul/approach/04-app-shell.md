# Approach: One page skeleton and a reachable header on every page
Spec: feature-plans/ux-overhaul/04-app-shell.md
Ref: PlanetStopper@37c8e983b64c6980c28199a3f9318486b60f3864

## Architecture
Every citation below is at the ref. Shared contracts are cited by their id in `feature-plans/ux-overhaul/approach/00-suite.md` (SC-1 to SC-10) and not restated. The plan's base is the `ux-overhaul/03` tip (SC-9), which carries plans 01, 02, 03 and 05; those plans edit the same templates first, so each cycle's test-writer re-runs the plan's DC-1, DC-2, DC-4 and DC-6 sibling searches at the base SHA before writing RED and carries any class or line they moved.

No model, persistence, validator, route, migration or dependency changes. `app.py`, `alpha_bot_execution.py`, `math_engine.py`, `static/tokens.css` and `migrations/` carry zero diff. The work is templates, static JavaScript and inline styles only.

Adopted, not invented:
- Page skeleton: `.page-wrap` in `static/layout.css:32-37`.
- Panel, card, label, stat, pill and caption primitives: SC-5. At this plan's base they live at the end of `static/layout.css`, already linked by every template, so this plan adds no `<link>` and defines none of those selectors anywhere. Page style blocks add only layout properties beside a primitive, plus the `cursor` override on a non-clickable `.sym-card` that SC-5 names, because `.sym-card` sets `cursor: pointer` (`templates/index.html:328`).
- Number face: the page sans with `font-variant-numeric: tabular-nums`, the property the dashboard's own figures use (`.cfg-val`, `templates/index.html:746-750`). No new class.
- Narrow breakpoints: the nav's existing `@media (max-width: 1400px)` breakpoint (`static/tokens.css:166-172`) for the header wrap, and the dashboard's existing `@media (max-width: 700px)` stacking breakpoint (`templates/index.html:40-44`) for page containment. No new breakpoint value.
- Fluid card grid: `repeat(auto-fill, minmax(min(100%, <width>), 1fr))`, the pattern of `.cards-grid` (`templates/index.html:316-320`) and `.studio-fluid-grid` (`static/tokens.css:156-160`).
- Theme-following tints: `color-mix(in srgb, var(--studio-*) N%, transparent)`, the pattern of the `.status-pill` modifiers (`templates/index.html:363-371`) and `settings-modal.js` `_badge` (`static/settings-modal.js:610-622`).
- Theme and typeface application: `static/tweaks.js:52-66` (`applyAll`) and `:110-118` (seed, apply at DOMContentLoaded), loaded the way every page loads it (`templates/history.html:264`).
- Advisor tab addressing: SC-6. The tab bar moves into the first `.hero-section` and keeps every `role="tab"`, `aria-controls` and `data-tab` attribute the switcher reads.

Header (`templates/_chrome.html`): the nav row (`:4`), its link group (`:13`) and its right-rail status cluster (`:49`) move their layout properties only (display, flex, gap, margin, min-width, overflow) from inline `style` into classes in the chrome's existing style block (`:111-116`), with identical values, so the computed header at 1440 px is unchanged. Inside the nav's existing 1400 px breakpoint the nav and the link group take `flex-wrap: wrap` and the link group may shrink, so the row wraps instead of running to 961 to 1,186 px (audit G1). The wordmark, the candidate-alert indicator (`:65-72`), the mode pill (`:93-95`), Force run (`:98-100`), Emergency Liquidate (`:103-106`) and the panic modal (`:110-144`) carry zero diff.

Fonts: `templates/_chrome.html:1-3` move verbatim into a new partial `templates/_fonts.html`, included by `_chrome.html` at the same position (same rendered bytes) and by `templates/login.html` in its head after `:9`. New because `templates/` holds no head partial (only `_chrome.html` and `table_partial.html`), and copying the three lines into login would make a second font source. Login also loads `static/tweaks.js` as the first child of its body, as `templates/history.html:264` does.

Client reporting (`static/chrome.js`): `forceRun` (`:13-34`) and `submitPanicLiquidation` (`:182-197`) change only how the response is read. The `fetch` calls (`:18` and `:189-192`: URL, method, headers, body and its key order), the phrase gate (`:188`, and the `oninput` gate at `templates/_chrome.html:132`), `openPanicModal` (`:152-179`), `closePanicModal` (`:146-149`) and the Escape handler (`:201-203`) are byte-identical.
- `forceRun` reads the JSON body when the response is JSON and shows its `message` in the button for the existing 2,000 ms timer; with no message it keeps the ref's `r.ok ? 'Triggered!' : 'Error'`; a non-JSON response or a rejected fetch shows `Error`.
- `submitPanicLiquidation` reads the status and Content-Type first. A 2xx keeps the ref's alert expression (`:195`) verbatim. A non-2xx JSON body with a `message` alerts that message (the five 400s, as at the ref). A 403 alerts that the session check refused the request and nothing was liquidated. Any other refusal (the auth hook's 401 `unauthenticated` and 503 `misconfigured` JSON bodies at `app.py:213-235`, which carry `error` and no `message`, and a 500) alerts the HTTP status and that nothing was liquidated. A rejected fetch closes the modal and alerts that the request could not be sent and nothing was liquidated.
- All text is set through `textContent` or `alert`, never `innerHTML`. No in-flight guard is added to Confirm, because the ref sends one request per click (S-4.8).

Pages (`templates/history.html`, `templates/performance.html`, `templates/settings.html`, `templates/ai_advisor.html`, with the card renderers in `static/history.js`, `static/ai_advisor.js`, `static/ai_advisor_logic_changes.js`, `static/ai_advisor_asset_swaps.js`): each title card and back link is deleted with its CSS; each page's description or basis line becomes a `.legend-as-of` caption with its own test id inside the first `.hero-section`, which also holds the page's own controls (window picker, scope control, Settings' Save and Discard, the Advisor tab bar); each top-level panel takes `.hero-section`, each card `.sym-card` with its name in `.card-name`, each section label `.section-header`, each headline row `.mini-stats`; every page-local copy of those values and the Advisor's own `.section-header` (`templates/ai_advisor.html:595-599`) is deleted. Classes that JavaScript hooks on (`.settings-section` at `static/settings.js:45`, `#btn-save` and `#btn-discard` at `:51-62` and `:599-604`, every `id` and `data-testid`) are kept beside the primitive. State modifiers that carry meaning (`.proposal-card--survivor`, `--rejected`, `--backtest-failed`, `.live-card[data-live="true"]`) keep their token-only colour declarations.

Narrow containment: page-local layout rules only (wrap, stacking, min-width, overflow), under the 700 px breakpoint, never radius, border, padding, font or colour values, because overview NB-4 forbids new primitives and plan NB-1 forbids a narrow-table design.

Browser tests follow SC-7 (`browser` marker, Playwright imported inside a fixture, no skip, Firefox headless, `-m browser -n 0` on the PM host); those that need the production copy read `UX_LAUNCHER_URL` and name their SC-8 mode. `/` captures wait on SC-1. Fixtures follow SC-10.

## Criterion Thread

| AC | R items | Design element (file:line, or "new: <reason and search>") | Test seam (test file:line; fixture and provenance; harness pattern) | Cycle |
|---|---|---|---|---|
| AC-1 | R-4, R-5, R-9, R-11, R-60, R-69 | `templates/history.html:268-277`, `templates/performance.html:393-424`, `templates/ai_advisor.html:1203-1218`, `templates/settings.html:836-848` (headers deleted; captions to `.legend-as-of`, SC-5); `/` structure seam of SC-5 | `tests/ui/test_cycle_5_history.py:225`, `tests/ui/test_cycle_3_performance.py:171`, `tests/ui/test_cycle_4_advisor.py:263`, `tests/ui/test_cycle_6_settings.py:630`, `tests/ui/test_cycle_1_foundation.py:390`; Flask test-client render of the real routes; SC-7 browser module in SC-8 Mode A for S-1.5; S-1.7 on SC-5's `tests/ui/test_dashboard_primitives_shared.py` against a base capture taken at this plan's base | C3 |
| AC-2 | R-4, R-5, R-11, R-61 | page style blocks `templates/history.html:12-260`, `templates/performance.html:12-384`, `templates/settings.html:12-830`, `templates/ai_advisor.html:12-1193` and `:2808-2882`; SC-5 primitives in `static/layout.css`; `static/history.js:232-305`, `static/ai_advisor.js:551`, `:928`, `static/ai_advisor_logic_changes.js:97-100`, `:296` | `tests/ui/test_page_wrapper_contract.py:326` (rule scan with `_iter_css_rules` at `:145`); render scans per page via the clients above; SC-7 browser module in Mode A, dark theme set through the stored tweak value | C3 |
| AC-3 | R-4, R-6, R-11, R-12, R-14, R-18, R-62, R-69 | `templates/_chrome.html:4`, `:13`, `:49` layout properties to classes in `:111-116` with the wrap rules inside the `static/tokens.css:166-172` breakpoint | `tests/ui/test_cycle_1_foundation.py:67` (render), `tests/app/test_candidate_alert_indicator_render.py:87`; SC-7 browser modules in Mode A (pages) and Mode B (`/login`) | C1 |
| AC-4 | R-4, R-6, R-18 | `static/chrome.js:146-198` request and modal paths kept byte-identical; `templates/_chrome.html:92-144` zero diff | `tests/app/test_sell_account_panic_confirm.py` and `tests/app/test_f003_liquidation_confirmation.py` run unchanged; new `tests/app/test_chrome_header_honesty.py` running the whole real `static/chrome.js` under `node -e` with a stub DOM, pattern `tests/app/test_dollar_saved_panel_friction_disclosure.py:315-358`; base request record `tests/fixtures/chrome/panic_request_at_base.json` produced by that harness over `static/chrome.js` at the plan base SHA (SC-10 rule 1, real producer) | C2 |
| AC-5 | R-4, R-11, R-12, R-14 | `templates/performance.html:262-271`, `templates/history.html:375`, `templates/settings.html:84-108`, `static/settings-modal.js:371`, `:583-605`, `templates/ai_advisor.html:546-548`, `:602`, `:2705` | SC-7 browser module in Mode A at 375, 320 and 1440 px; request capture with `page.route` for S-5.9 | C4 |
| AC-6 | R-4, R-5, R-11, R-15, R-64 | new: `templates/_fonts.html` (no head partial exists; `templates/` holds `_chrome.html` and `table_partial.html` only) from `templates/_chrome.html:1-3`; `templates/login.html:3-9` and body start; `static/tweaks.js:52-66`, `:110-118` | `tests/app/test_dashboard_auth.py:198`, `:205`, `:292`, `:645`, `:814` with the `auth_client` fixture at `:87`; SC-7 browser module in Mode B with the stored tweak value preset and the font host aborted | C1 |
| AC-7 | R-4, R-5, R-15, R-65 | `templates/ai_advisor.html:516-519`, `:646-650`, `:687`, `:774-778`, `:1006-1010` to `color-mix` over `--studio-cyan` and `--studio-pos` | `tests/ui/test_cycle_4_advisor.py:750` (extends its bare-hex scan to `rgba(` in chip, pill and notice rules); LF-normalized SHA-256 pin of `static/tokens.css` at the ref, pattern `tests/test_mdd_window_truth_zero_diff_guard.py:55-80`; SC-7 browser module in Mode A, dark | C4 |
| AC-8 | R-4, R-6, R-9 | `static/chrome.js:13-34` (response read only); `app.py:4931-4935` unchanged | `tests/app/test_chrome_header_honesty.py` (new, above); route answers taken at test time from the real `/api/trigger` through the Flask test client; 403 from the real CSRF hook (`app.py:489-493`) with `_csrf_check_enabled` set true, pattern `tests/app/test_dashboard_auth.py:26` | C2 |
| AC-9 | R-4, R-18, R-73 | `static/chrome.js:182-197` (response read and a rejection branch only); `app.py:5447-5528` unchanged | `tests/app/test_chrome_header_honesty.py` (new); every answer taken at test time from the real app through the Flask test client: the five 400s and the 200 dry-run body with the payloads and `dotenv_values` patches of `tests/app/test_sell_account_panic_confirm.py:100-212`, the 403 HTML from the CSRF hook, the 401 and 503 JSON from the auth hook (`auth_client`, `tests/app/test_dashboard_auth.py:87`), the 500 from Flask's own handler with `PROPAGATE_EXCEPTIONS` off; base request record above; live refusals in SC-8 Mode B | C2 |

## Execution Model
- C1 (header wrap, login font and theme): a Toxic Pair in its own worktree; renders UI, so `qa`, `code-reviewer` and the `ux-expert` with `behavioral-evidence-capturer` gate it in SC-8 Mode A, and Mode B for the login rows.
- C2 (Force run and Emergency Liquidate reporting): a Toxic Pair in its own worktree; the plan's money-sensitive cycle. `qa`, `code-reviewer`, the ux pair (button text and alerts render) and the project's `security-auditor` (PM ruling: the security auditor reviews the liquidation-messaging cycle).
- C3 (page skeleton and primitives): a Toxic Pair in its own worktree; renders UI; same reviewer set as C1, Mode A.
- C4 (narrow containment and Advisor tints): a Toxic Pair in its own worktree on C3's branch; renders UI; same reviewer set as C1, Mode A.
- C5 (cross-page close): a Toxic Pair on the plan branch after C1, C2 and C4 merge; its scenarios are whole-stack facts (document width on every page at 375 and 320 px, `/` against the base, the token file). RED may arrive green where earlier cycles already satisfied it; the implementer acts only on failures. Its `code-reviewer` is dispatched only if its diff changes code; the ux pair always, because every C5 scenario is a render.
- One `qa` dispatch in hidden mode for the plan, because the plan is Sensitive (money).
- One doc-gen for the slice after every head passes.
- Isolation: each pair works in its own worktree under `.claude/worktrees/`, never the main checkout, with `.env` copied in and `git rev-parse --show-toplevel` and `git rev-parse --abbrev-ref HEAD` checked before the first edit (`.claude/CLAUDE.md` §Agent Team Composition; `00-suite.md` Execution Model). A pair's worktree is never served in Mode B, because it carries the real `.env` (SC-8 Mode B item 4). One pytest invocation at a time on the PM host, never `-n auto` or more than 4 workers; browser modules run with `-n 0`.

Team size: 21 = 5 test-writer + 5 implementer + 2 qa + 1 qa hidden + 2 code-reviewer + 2 ux-expert + 2 behavioral-evidence-capturer + 1 security-auditor + 1 doc-gen

## Dispatch Ordering

| Cycle | Consumes (cycle: committed artifact) | Branch base |
|---|---|---|
| C1 | | the `ux-overhaul/03` tip at the SHA the PM records at dispatch (SC-9; plan 03 is unbuilt at the ref) |
| C2 | | the `ux-overhaul/03` tip at the SHA the PM records at dispatch |
| C3 | | the `ux-overhaul/03` tip at the SHA the PM records at dispatch |
| C4 | C3: panel, card and scroll-wrapper markup in `templates/performance.html`, `templates/history.html` and `templates/ai_advisor.html`, and the `.sym-card` proposal-card markup in `static/ai_advisor.js` and `static/ai_advisor_logic_changes.js` | C3's branch at its cycle-complete SHA |
| C5 | C1: nav classes and wrap rules in `templates/_chrome.html`; C2: response handling in `static/chrome.js`; C4: page containment rules and C3's page skeleton it carries | `ux-overhaul/04-app-shell` after C1, C2 and C4 are merged into it with merge commits |

Every cycle merges into `ux-overhaul/04-app-shell` by a merge commit; no cycle head is a PR of its own, no branch is rebased or force-pushed, and a new `ux-overhaul/03` tip enters `ux-overhaul/04-app-shell` by a merge commit (SC-9 rules 2 and 3).

## Per-Cycle Detail

### C1: header wrap, login font and theme
ACs: AC-3 (S-3.1, S-3.4, S-3.5, S-3.6, S-3.7), AC-6.
Files modified:
- `templates/_chrome.html`: `:1-3` replaced by `{% include '_fonts.html' %}`; nav (`:4`), link group (`:13`) and status cluster (`:49`) carry classes for their layout properties; the style block (`:111-116`) gains those classes and, inside `@media (max-width: 1400px)`, `flex-wrap: wrap` on the nav and the link group with the link group allowed to shrink. Lines `:92-144` zero diff.
- `templates/login.html`: include `_fonts.html` in the head after `:9`; `<script src="{{ url_for('static', filename='tweaks.js') }}">` as the body's first child; the form (`:121-137`), CSRF field (`:123`), error block (`:117-119`) and styles unchanged.
Files added: `templates/_fonts.html` (the three lines of `templates/_chrome.html:1-3`, verbatim); the C1 browser module.
Primitives imported: `static/tweaks.js`, the 1400 px breakpoint of `static/tokens.css:166-172`.
First production uses: `tweaks.js` and the font stylesheet on `/login`.
Renders UI: yes.

### C2: Force run and Emergency Liquidate report the server's answer
ACs: AC-4, AC-8, AC-9.
Files modified: `static/chrome.js` only: `forceRun` response handling (`:19-33`) and `submitPanicLiquidation` response handling plus a rejection branch (`:193-196`). Request construction at `:18` and `:189-192` and every other function byte-identical.
Files added: `tests/fixtures/chrome/panic_request_at_base.json` with its `PROVENANCE.md` (SC-10), one record per case: the single-confirm request (URL, method, headers with a real-format token, body string) and the two-click request count, produced by running the C2 harness over `static/chrome.js` at the plan base SHA before any C2 edit; the harness module and the C2 browser module.
Per-page coverage: every page renders the same `_chrome.html` include and loads the same `chrome.js` (`tests/app/test_candidate_alert_indicator_render.py:70`), so the harness result holds on each matrix column; one render assertion per page pins that each page loads that script and that modal markup.
First production uses: none.
Renders UI: yes (button text, alerts). Sensitive.

### C3: page skeleton and primitives
ACs: AC-1 (all but S-1.7), AC-2 (all but S-2.6).
Files modified:
- `templates/history.html`: delete the header (`:268-277`) and its CSS (`:37-82`); first `.hero-section` holds the caption (text of `:272-274`, `.legend-as-of`, `data-testid="history-caption"`), the window picker (`:281-291`) and the hero stats as `.mini-stats` (`:294-312`, CSS `:128-156` deleted); `.section-card` (`:325`, `:344`, `:373`; CSS `:159-165`) to `.hero-section`; `.section-title` (`:326`, `:345`, `:374`; CSS `:167-174`) to `.section-header`; the empty state (`:314-324`) sits in a `.hero-section` without its inline border, radius and padding; reason cards (`:351-370`, CSS `:220-227`) to `.sym-card` with `.card-name`. The `--studio-page-max-width` override stays (`tests/ui/test_page_wrapper_contract.py:362`).
- `static/history.js`: `renderReasonCards` (`:232-305`) emits `.sym-card`/`.card-name` and drops its inline radius and border (`:271`).
- `templates/performance.html`: delete the header CSS (`:41-86`) and the title (`:395`); first `.hero-section` holds the basis caption (`:396-398`), the observation caption (`:401`, id and test id kept, class `.legend-as-of`, initial `--` kept), the scope and window controls (`:404-423`), the insufficient banner and the headline strip as `.mini-stats` (`:437-452`; CSS `:137-170` deleted); `.chart-block` and the three `.metrics-section` panels (`:457`, `:478`, `:543`, `:584`; CSS `:172-197`, `:233-238`) to `.hero-section` with their inline `margin-top` removed; `.chart-block-title` and `.metrics-title` to `.section-header` (CSS `:190-197`, `:240-246` deleted); `.metrics-tbl tbody` (`:292-295`) drops `font-family: monospace` and takes `font-variant-numeric: tabular-nums`.
- `templates/settings.html`: content wrapped in `.page-wrap`; the header band (`:839-848`, CSS `:37-48`) becomes an actions row (caption `.legend-as-of` with `data-testid="settings-caption"`, then Discard and Save with ids and classes unchanged) as the first child of `.page-wrap`, above `.settings-body` and outside `.settings-content`'s scroller (`:141-145`), so the buttons keep whatever scroll behaviour they had at the ref; each `.settings-section` (`:870`, `:909`, `:916`, `:997`, `:1008`) also carries `.hero-section`; `.sec-title` (`:871`, `:910`, `:917`, `:998`, `:1009`; CSS `:150-156`) to `.section-header`; `.live-card` (`:874`, CSS `:166-175`) carries `.sym-card` and keeps its `data-live` tint modifier.
- `templates/ai_advisor.html`: delete the header (`:1203-1218`) and CSS (`:33-78`); first `.hero-section` holds the caption (text of `:1213-1215`, `.legend-as-of`, `data-testid="advisor-caption"`) and the tab bar (`.cap-nav`, `:1221`, every tab attribute kept, SC-6); delete the local `.section-header` (`:595-599`); panels `.autotune-panel` (`:197`), `.matrix-card` (`:394`), `.try-swap-panel`/`.try-tweak-panel` (`:437`), `.run-controls-panel` (`:568`), `.prism-block` (`:719`), `.obs-section` (`:2809`) and every further panel class the DC-1 search finds at the base (the Retirement tab and the Correlations matrix plans 02 and 03 add) take `.hero-section`, with the heatmap table kept inside an `overflow-x: auto` wrapper so matrix scrolling does not regress when `.matrix-card`'s own overflow (`:396`) goes; their header classes (`.matrix-card-header` `:398`, `.autotune-header` `:205`, `.prism-block-title` `:736`, `.run-controls-header` `:572`, `.try-swap-header` `:441`) to `.section-header`; `.proposal-card` base values (`:603-606`) deleted and every proposal, Frontrunner (`:2630`), retirement (`.retirement-rec-card` `:952`) and autotune run card (`.autotune-run-card` `:214`) carries `.sym-card`; `.card-candidate-id` (`:620`) to `.card-name`; `.mono-bold` (`:249-255`), if it survives plan 02, to the tabular sans face.
- `static/ai_advisor.js` (`:551`, `:928`), `static/ai_advisor_logic_changes.js` (`:97-108`, `:296`), `static/ai_advisor_asset_swaps.js` (`:367`, `:386`): emit `.sym-card` and `.card-name`.
- No `<link>` is added: SC-5's stylesheet is already linked on every page.
- Test pins that encode the old headers are updated (DC-6): `tests/ui/test_cycle_5_history.py:225-246`, `tests/ui/test_cycle_3_performance.py:171-190`, `tests/ui/test_cycle_4_advisor.py:263-287`, `tests/ai_advisor/test_advisor_observations_ui.py:191-206`, `tests/ai_advisor/test_r1_attribution_honesty.py:91`, and `tests/app/test_retirement_recommendations_panel.py:308-336` if the `matrix-card` pin survives plan 02.
Files added: the C3 browser module.
Renders UI: yes.

### C4: narrow containment and Advisor tints
ACs: AC-5 (all but S-5.8, S-5.10), AC-7 (S-7.1, S-7.3, S-7.4).
Files modified:
- `templates/performance.html`: inside the 700 px breakpoint, `.metrics-tbl` (`:266-271`) keeps its intrinsic column widths (`table-layout: auto`, content width, at least the container's width) so `.metrics-overflow` (`:262-264`) scrolls instead of overlapping columns (audit P1); the `.mini-stats` row wraps two tiles per line.
- `templates/history.html`: the exits table container (`:375`) is the scroller; the `.mini-stats` row wraps as on Performance.
- `templates/settings.html`: inside the 700 px breakpoint `.settings-body` (`:84-90`) stacks the side nav above the content, the side nav takes full width (`:92-108`) and inputs take at most the content width, the dashboard's own stacking precedent (`templates/index.html:40-44`); this delivers S-5.11 (audit S2).
- `static/settings-modal.js`: the modal body (`:604`) takes `min-height: 0` so the flex child can scroll inside the capped surface (`:583-596`) and the footer stays reachable (audit M4); the locked-variables grid (`:371`) uses `repeat(2, minmax(0, 1fr))` so long keys ellipsize instead of clipping the right column (audit M1).
- `templates/ai_advisor.html`: `.proposal-cards` (`:602`) uses `minmax(min(100%, 22rem), 1fr)`; the Frontrunner actions row (`:2705`) wraps; the chat panel's narrow rule (`:546-548`) is checked live and changed only if S-5.5 fails after the nav fix; the five tints become `color-mix(in srgb, var(--studio-cyan) 8%, transparent)` with a 25% border (`:516-519`), `color-mix(in srgb, var(--studio-pos) 12%, transparent)` (`:649`, `:1009`) and `7%` (`:687`, `:777`), keeping each literal's alpha.
Files added: the C4 browser module.
Renders UI: yes.

### C5: cross-page close
ACs: AC-1 (S-1.7), AC-2 (S-2.6), AC-3 (S-3.2, S-3.3), AC-5 (S-5.8, S-5.10), AC-7 (S-7.2).
Files modified: none planned; any page whose document is still wider than 375 or 320 px, or any table that gained a scroll bar at 1440 px, is fixed in that page's template with a page-local layout rule.
Files added: the tokens pin test, the base capture for SC-5's `/` seam taken at this plan's base (with its `PROVENANCE.md` naming that SHA), and the C5 browser module.
Renders UI: yes (verification of every page).

## Per-Cycle Test Plan
Scope by cycle: C1 server render, route tests and browser; C2 node harness over the real `chrome.js`, route tests and browser; C3 server render, stylesheet scans and browser; C4 browser and stylesheet scans; C5 browser, SC-5's `/` seam and a file pin.

Browser modules are SC-7 modules, one per cycle so parallel branches never edit one test file: marked `browser`, Playwright imported inside a fixture, Firefox headless, no `importorskip` and no `skipif`, base URL from `UX_LAUNCHER_URL` with the SC-8 mode named per row (Mode A unless the row says Mode B). A selected test with Playwright or its server missing fails naming what is missing. `/` is captured or measured only after SC-1 readiness (`body[data-dashboard-ready="1"]`); other pages wait on their own selectors, never on network idle: History on `#triggers-tbody` holding no "Loading" row, Performance on `#guard-alpha-value` no longer `--`, Settings on a rendered `.param-input`, the Advisor on `[data-tab]` buttons present after `DOMContentLoaded`. Themes are set through the stored tweak value (`static/tweaks.js:17-30`). Route answers are taken at test time from the real app through the Flask test client, never hand-written (SC-10 rule 1). Mode B cannot show the 200 dry-run answer (SC-8 Mode B item 6), so S-9.7 is harness-only; live refusals in Mode B are the CSRF 403 (blank page token), the missing-credentials 400 (an account chosen) and, on an instance with no account configured, the empty-account 400.

No persisted or verified field is added or changed, so the census has no new writer; Save and the modal's save are listed because AC-1 and AC-5 scenarios drive them. The page routes are `/` at `app.py:1665`, `/history` at `app.py:5149`, `/performance` at `app.py:5160`, `/settings` at `app.py:5977`, `/ai-advisor` at `app.py:6618` and `/login` (GET and POST) at `app.py:240`.

| Entry point | Rules |
|---|---|
| `/` | AC-1, AC-2, AC-3, AC-4, AC-8, AC-9 |
| `/history` | AC-1, AC-2, AC-3, AC-4, AC-5, AC-8, AC-9 |
| `/performance` | AC-1, AC-2, AC-3, AC-4, AC-5, AC-8, AC-9 |
| `/settings` | AC-1, AC-2, AC-3, AC-4, AC-5, AC-8, AC-9 |
| `/ai-advisor` | AC-1, AC-2, AC-3, AC-4, AC-5, AC-7, AC-8, AC-9 |
| `/login` | AC-3, AC-6 |
| `POST /api/sell_account` (`app.py:5447`) | AC-4, AC-9 |
| `POST /api/trigger` (`app.py:4931`) | AC-8 |
| `GET /api/accounts` (`app.py:4938`) | AC-4 |
| `GET /api/csrf-token` (`app.py:382`) | AC-4, AC-8, AC-9 |
| `_auth_before_request` hook (`app.py:193`) | AC-9 |
| `_csrf_before_request` hook (`app.py:489`) | AC-8, AC-9 |
| `POST /api/settings` (`app.py:6127`) | AC-1 |
| `GET /api/symphony-settings/<symphony_name>` (`app.py:6170`) | AC-5 |
| `POST /api/symphony-settings/<symphony_name>` (`app.py:6219`) | AC-5 |

### C1
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-3.1 | AC-3 | browser 375x812, every page (Mode A); server render of the nav classes | `/`, `/history`, `/performance`, `/settings`, `/ai-advisor` | `tests/ui/test_cycle_1_foundation.py:67`; browser part in `tests/ui/test_shell_header_browser.py` (new) | Mode A launcher; `/` after SC-1 readiness |
| S-3.4 | AC-3 | server render plus browser 375 (Mode A) | every page | `tests/app/test_candidate_alert_indicator_render.py:87`; browser part in `tests/ui/test_shell_header_browser.py` | badge count read from the launcher's `/api/candidate-alert` at run time, never a literal |
| S-3.5 | AC-3 | browser 1440x900 (Mode A): the nav is one row, its height equal to the base capture's | every page | new: `tests/ui/test_shell_header_browser.py` (C1's SC-7 module; no browser module covers the header) | base capture of the same pages at the plan base SHA (SC-10 rule 1) |
| S-3.6 | AC-3 | browser resize 1440 to 375 to 1440 (Mode A) | every page | new: `tests/ui/test_shell_header_browser.py` (C1's SC-7 module) | Mode A launcher |
| S-3.7 | AC-3 | browser 375 (Mode B) | `/login` | new: `tests/ui/test_shell_header_browser.py` (C1's SC-7 module) | Mode B launcher |
| S-6.1 | AC-6 | server render of the login head (font link identical to the chrome's, `tweaks.js` loaded) plus browser `data-theme` and computed font (Mode B) | `/login` | `tests/app/test_dashboard_auth.py:198`; browser part in `tests/ui/test_shell_header_browser.py` | stored tweak value with `theme: "dark"` in the shape `static/tweaks.js:17-30` reads |
| S-6.2 | AC-6 | browser with no stored tweak value (Mode B) | `/login` | new: `tests/ui/test_shell_header_browser.py` (C1's SC-7 module) | none |
| S-6.3 | AC-6 | server render (font URL keeps `display=swap`, login font stack ends in a generic family) plus browser with the font host aborted by `page.route` (Mode B) | `/login` | `tests/app/test_dashboard_auth.py:205`; browser part in `tests/ui/test_shell_header_browser.py` | none |
| S-6.4 | AC-6 | route tests unchanged plus a head render | `/login` POST | `tests/app/test_dashboard_auth.py:645`, `:242`, `:814` | `auth_client` fixture `tests/app/test_dashboard_auth.py:87` |
| S-6.5 | AC-6 | route test plus browser dark render of the error (Mode B, fewer than ten failed logins, SC-8 Mode B item 8) | `/login` POST | `tests/app/test_dashboard_auth.py:292`; browser part in `tests/ui/test_shell_header_browser.py` | the route's real wrong-password response |

### C2
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-4.1 | AC-4 | browser 1440 and 375 on every page, open only (Mode A); harness for `openPanicModal` state; per-page render of the shared script and modal | every page | new: `tests/ui/test_panic_flow_browser.py` (C2's SC-7 module); harness part in `tests/app/test_chrome_header_honesty.py` (new) | Mode A launcher with accounts configured; `/api/accounts` body from the real route |
| S-4.2 | AC-4 | server render: the `oninput` gate at `_chrome.html:132` equals the base record; browser typing (Mode A) | every page | new: `tests/app/test_chrome_header_honesty.py` (no test runs `static/chrome.js` under node, and S-4.7 keeps the two panic test files unchanged); browser part in `tests/ui/test_panic_flow_browser.py` | `tests/fixtures/chrome/panic_request_at_base.json` (harness over base `chrome.js`) |
| S-4.3 | AC-4 | harness: Cancel after the phrase, zero `fetch` calls to `/api/sell_account` | every page | new: `tests/app/test_chrome_header_honesty.py` (as above) | none |
| S-4.4 | AC-4 | harness request capture compared with the base record | `POST /api/sell_account` | new: `tests/app/test_chrome_header_honesty.py` (as above) | base record; token from `app._CSRF_TOKEN` (real format); account id in real UUID form |
| S-4.5 | AC-4 | harness with the real 400 bodies | `POST /api/sell_account` | new: `tests/app/test_chrome_header_honesty.py` (as above) | route answers via test client, payloads of `tests/app/test_sell_account_panic_confirm.py:133-212` |
| S-4.6 | AC-4 | server render of the pill with `LIVE_EXECUTION` true and false | every page | `tests/ui/test_cycle_1_foundation.py:292` (patch pattern) | `dotenv.dotenv_values` patch as at `:292-330` |
| S-4.7 | AC-4 | the two panic files run with zero diff | `POST /api/sell_account` | `tests/app/test_sell_account_panic_confirm.py:63` and `tests/app/test_f003_liquidation_confirmation.py:126`, run with zero diff | their own fixtures |
| S-4.8 | AC-4 | harness: two Confirm calls, request count equals the base record's | `POST /api/sell_account` | new: `tests/app/test_chrome_header_honesty.py` (as above) | base record |
| S-4.9 | AC-4 | harness: the `/api/accounts` fetch rejects; the select shows the failure option, Confirm disabled | `GET /api/accounts` | new: `tests/app/test_chrome_header_honesty.py` (as above) | none |
| S-8.1 | AC-8 | harness with the real `/api/trigger` body; the button shows its message, then the label | `POST /api/trigger` | new: `tests/app/test_chrome_header_honesty.py` (as above) | route answer via test client |
| S-8.2 | AC-8 | harness with the real 403 HTML | `_csrf_before_request` hook | new: `tests/app/test_chrome_header_honesty.py` (as above) | 403 via test client with `_csrf_check_enabled` true, pattern `tests/app/test_dashboard_auth.py:26` |
| S-8.3 | AC-8 | harness, `fetch` rejects | `POST /api/trigger` | new: `tests/app/test_chrome_header_honesty.py` (as above) | none |
| S-8.4 | AC-8 | harness, two clicks while pending, one request | `POST /api/trigger` | new: `tests/app/test_chrome_header_honesty.py` (as above) | none |
| S-8.5 | AC-8 | route test: the POST spawns nothing | `POST /api/trigger` | new: `tests/app/test_chrome_header_honesty.py` (as above) | `app.trigger_alpha_bot` (`app.py:776`) and `subprocess.Popen` patched and asserted not called |
| S-9.1 | AC-9 | harness with the real 400 body; browser on a Mode B instance with no account configured | `POST /api/sell_account` | new: `tests/app/test_chrome_header_honesty.py` (as above); browser part in `tests/ui/test_panic_flow_browser.py` | route answer, payload of `tests/app/test_sell_account_panic_confirm.py:133` |
| S-9.2 | AC-9 | harness with the real 400 body | `POST /api/sell_account` | new: `tests/app/test_chrome_header_honesty.py` (as above) | payload of `tests/app/test_sell_account_panic_confirm.py:150` |
| S-9.3 | AC-9 | harness with the real 400 body | `POST /api/sell_account` | new: `tests/app/test_chrome_header_honesty.py` (as above) | payload of `tests/app/test_sell_account_panic_confirm.py:169` |
| S-9.4 | AC-9 | harness with the real 400 body (the wrong-phrase case the client blocks, sent by calling the request path directly) | `POST /api/sell_account` | new: `tests/app/test_chrome_header_honesty.py` (as above) | payload of `tests/app/test_sell_account_panic_confirm.py:186` |
| S-9.5 | AC-9 | harness with the real 400 body; browser in Mode B with an account chosen (SC-8 Mode B item 6) | `POST /api/sell_account` | new: `tests/app/test_chrome_header_honesty.py` (as above); browser part in `tests/ui/test_panic_flow_browser.py` | valid payload with `dotenv_values` patched without `COMPOSER_KEY_ID` |
| S-9.6 | AC-9 | harness with the real 403 HTML; browser in Mode B with the page token blanked | `_csrf_before_request` hook | new: `tests/app/test_chrome_header_honesty.py` (as above); browser part in `tests/ui/test_panic_flow_browser.py` | 403 via test client with `_csrf_check_enabled` true |
| S-9.7 | AC-9 | harness with the real 200 dry-run body (not reachable in Mode B) | `POST /api/sell_account` | new: `tests/app/test_chrome_header_honesty.py` (as above) | route answer with `LIVE_EXECUTION` false and `requests.post` patched, pattern `tests/app/test_sell_account_panic_confirm.py:100` |
| S-9.8 | AC-9 | harness request capture for every S-9 case compared with the base record | `POST /api/sell_account` | new: `tests/app/test_chrome_header_honesty.py` (as above) | base record |
| S-9.9 | AC-9 | harness, `fetch` rejects | `POST /api/sell_account` | new: `tests/app/test_chrome_header_honesty.py` (as above) | none |
| S-9.10 | AC-9 | harness, a refusal then reopen and confirm: one new request, its own answer | `POST /api/sell_account` | new: `tests/app/test_chrome_header_honesty.py` (as above) | two route answers via test client |
| S-9.11 | AC-9 | harness with the real 401 JSON; the alert shows 401 and that nothing was liquidated, never the dry-run text | `_auth_before_request` hook | new: `tests/app/test_chrome_header_honesty.py` (as above) | 401 via test client with auth enabled and no session, `auth_client` (`tests/app/test_dashboard_auth.py:87`) |
| S-9.12 | AC-9 | harness with the real 503 JSON | `_auth_before_request` hook | new: `tests/app/test_chrome_header_honesty.py` (as above) | 503 via test client with auth enabled and the dashboard credential removed from the environment (`app.py:221-228`) |
| S-9.13 | AC-9 | harness with the real 500 body | `POST /api/sell_account` | new: `tests/app/test_chrome_header_honesty.py` (as above) | 500 via test client with `app.config["PROPAGATE_EXCEPTIONS"]` false and `app.dotenv_values` patched to raise inside the route (`app.py:5468`) |

### C3
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-1.1 | AC-1 | server render of each page: no `h1` title card, no back link, first element after the chrome inside `.page-wrap`, caption present (History and Settings captions included) | `/history`, `/performance`, `/settings`, `/ai-advisor` | `tests/ui/test_cycle_5_history.py:225`, `tests/ui/test_cycle_3_performance.py:171`, `tests/ui/test_cycle_4_advisor.py:263`, `tests/ui/test_cycle_6_settings.py:308` | page clients `hist_client` (`tests/ui/test_cycle_5_history.py:93`), `perf_client` (`tests/ui/test_cycle_3_performance.py:108`), `adv_client` (`tests/ui/test_cycle_4_advisor.py:157`), settings `client` with `mock_db_empty` (`tests/ui/test_cycle_6_settings.py:71-100`) |
| S-1.2 | AC-1 | server render: the link whose `href` is the page's own carries `aria-current="page"` | every page | `tests/ui/test_cycle_1_foundation.py:390`, `tests/ui/test_cycle_6_settings.py:542` | same clients |
| S-1.3 | AC-1 | server render: Performance basis and observation captions and the Advisor caption inside the first `.hero-section` | `/performance`, `/ai-advisor` | `tests/ui/test_cycle_3_performance.py:180`, `tests/ui/test_cycle_4_advisor.py:276`, `tests/ai_advisor/test_r1_attribution_honesty.py:91` | same clients |
| S-1.4 | AC-1 | server render | `/settings` | `tests/ui/test_cycle_6_settings.py:630`, `:641` | settings client |
| S-1.5 | AC-1 | browser (Mode A): edit, scroll to the bottom of the longest section, Save and Discard reachable and enabled, Save's POST body captured by `page.route` equals the base capture | `POST /api/settings` | new: `tests/ui/test_page_skeleton_browser.py` (C3's SC-7 module) | Mode A launcher; base capture of the same steps at the plan base SHA; the POST is intercepted in the browser, nothing written |
| S-1.6 | AC-1 | server render: both buttons `disabled` | `/settings` | `tests/ui/test_cycle_6_settings.py:630` | settings client |
| S-1.8 | AC-1 | server render: `obs-caption` text is the dash | `/performance` | `tests/ui/test_cycle_3_performance.py:306` | `perf_client` |
| S-2.1 | AC-2 | server render class usage, plus a scan of `renderReasonCards` output under node | `/history` | `tests/ui/test_cycle_5_history.py:312`, `:344`, `:277` | `hist_client`; history payload from the real `/api/history/<days>` route |
| S-2.2 | AC-2 | server render class usage plus a rule scan for `monospace` | `/performance` | `tests/ui/test_cycle_3_performance.py:257`, `:327`, `:379` | `perf_client` |
| S-2.3 | AC-2 | server render class usage | `/settings` | `tests/ui/test_cycle_6_settings.py:336` | settings client |
| S-2.4 | AC-2 | server render of each tab panel; source scan of the card renderers in `static/ai_advisor*.js` | `/ai-advisor` | `tests/ui/test_cycle_4_advisor.py:359` | `adv_client` with `_patch_advisor` (`tests/ui/test_cycle_4_advisor.py:102`) |
| S-2.5 | AC-2 | rule scan: each SC-5 selector defined only in `static/layout.css`, no page style block redefines one | all page templates | `tests/ui/test_page_wrapper_contract.py:326` (helpers `:128-170`) | the templates and `static/layout.css` at HEAD |
| S-2.7 | AC-2 | rule scan: every colour value in the primitive rules is a `var(--studio-*)`; browser dark render (Mode A) | every page | `tests/ui/test_page_wrapper_contract.py:326`; browser part in `tests/ui/test_page_skeleton_browser.py` | Mode A launcher, dark through the stored tweak value |
| S-2.8 | AC-2 | server render of the empty-state container inside `.hero-section`; browser with an empty window (Mode A) | `/history` | `tests/ui/test_cycle_5_history.py:277`; browser part in `tests/ui/test_page_skeleton_browser.py` | `hist_client`; a launcher window with no exits |

### C4
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-5.1 | AC-5 | browser 375 (Mode A): the exits table's scroller has `scrollWidth` over `clientWidth` and its last column is reachable by scrolling it | `/history` | new: `tests/ui/test_narrow_containment_browser.py` (C4's SC-7 module) | Mode A launcher; exits populated through the day drilldown (`static/history.js:363`), because today's exits on a snapshot copy can be empty |
| S-5.2 | AC-5 | browser 375 (Mode A): three tables scroll in their containers; headline labels not ellipsized | `/performance` | new: `tests/ui/test_narrow_containment_browser.py` (C4's SC-7 module) | Mode A launcher |
| S-5.3 | AC-5 | browser 375 (Mode A): the modal grid within the surface, the footer reachable by scrolling the body | `GET /api/symphony-settings/<symphony_name>` | new: `tests/ui/test_narrow_containment_browser.py` (C4's SC-7 module) | Mode A launcher, opened from a card on `/` after SC-1 readiness |
| S-5.4 | AC-5 | browser 1440x900 (Mode A): the footer reachable | `GET /api/symphony-settings/<symphony_name>` | new: `tests/ui/test_narrow_containment_browser.py` (C4's SC-7 module) | Mode A launcher |
| S-5.5 | AC-5 | browser 375 (Mode A): the panel's left edge at or right of 0 after a Discuss click | `/ai-advisor` | new: `tests/ui/test_narrow_containment_browser.py` (C4's SC-7 module) | Mode A launcher with a Discuss entry point rendered |
| S-5.6 | AC-5 | browser 375 (Mode A): Approve and Reject inside the viewport | `/ai-advisor` | new: `tests/ui/test_narrow_containment_browser.py` (C4's SC-7 module) | Mode A launcher with a pending Frontrunner proposal (audit A-FR-3 shows one) |
| S-5.7 | AC-5 | browser 375 (Mode A): the matrix wrapper scrolls, reached as `/ai-advisor#correlations` (SC-6) | `/ai-advisor` | new: `tests/ui/test_narrow_containment_browser.py` (C4's SC-7 module) | Mode A launcher with plan 03's matrix data |
| S-5.9 | AC-5 | browser 375 (Mode A): edit, scroll the body, the edit and Save's enabled state kept; Save's POST intercepted | `POST /api/symphony-settings/<symphony_name>` | new: `tests/ui/test_narrow_containment_browser.py` (C4's SC-7 module) | Mode A launcher; the POST intercepted in the browser, nothing written |
| S-5.11 | AC-5 | browser 375 (Mode A): every label and input inside the viewport and unclipped by its container; the side nav above the content | `/settings` | new: `tests/ui/test_narrow_containment_browser.py` (C4's SC-7 module) | Mode A launcher |
| S-7.1 | AC-7 | rule scan of the five rules | `/ai-advisor` | `tests/ui/test_cycle_4_advisor.py:750` | `templates/ai_advisor.html` at HEAD |
| S-7.3 | AC-7 | browser dark render of the five tints (Mode A) | `/ai-advisor` | new: `tests/ui/test_narrow_containment_browser.py` (C4's SC-7 module) | Mode A launcher, dark through the stored tweak value |
| S-7.4 | AC-7 | scan of every chip, pill and notice rule for `rgba(` or hex outside a `var(--studio-...)` fallback | `/ai-advisor` | `tests/ui/test_cycle_4_advisor.py:750` | `templates/ai_advisor.html` at HEAD |

### C5
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-1.7 | AC-1 | server-render `/` structure and classes compared with this plan's base, excluding the `top-bar` subtree (PM-1) | `/` | new: SC-5's `tests/ui/test_dashboard_primitives_shared.py`, committed by 02 C1 and present at this plan's base; no seam compares `/` with a base at the ref | base capture of `GET /` at this plan's base, committed under `tests/fixtures/dashboard/` with a `PROVENANCE.md` naming that SHA |
| S-2.6 | AC-2 | the same seam for the SC-5 rules resolving on `/`; capturer screenshots after SC-1 readiness, full page at 1440 light and dark and below the header at 375 light (PM-1), against this plan's base capture | `/` | new: SC-5's `tests/ui/test_dashboard_primitives_shared.py` (as above); browser part in `tests/ui/test_narrow_close_browser.py` | base capture at this plan's base, never the audit's captures |
| S-3.2 | AC-3 | browser 375x812 (Mode A): `document.documentElement.scrollWidth` equals the viewport width on every page | every page | new: `tests/ui/test_narrow_close_browser.py` (C5's SC-7 module) | Mode A launcher; `/` after SC-1 readiness |
| S-3.3 | AC-3 | browser 320 px (Mode A): the S-3.1 and S-3.2 conditions | every page | new: `tests/ui/test_narrow_close_browser.py` (C5's SC-7 module) | Mode A launcher |
| S-5.8 | AC-5 | browser 320 px (Mode A): the S-5.1 to S-5.7 and S-5.11 conditions | `/history`, `/performance`, `/settings`, `/ai-advisor`, `/` | new: `tests/ui/test_narrow_close_browser.py` (C5's SC-7 module) | Mode A launcher |
| S-5.10 | AC-5 | browser 1440 (Mode A): per table container, `scrollWidth > clientWidth` equals the base capture's | every page | new: `tests/ui/test_narrow_close_browser.py` (C5's SC-7 module) | base capture at this plan's base |
| S-7.2 | AC-7 | LF-normalized SHA-256 of `static/tokens.css` equals the ref's | none (file) | new pin test, pattern `tests/test_mdd_window_truth_zero_diff_guard.py:55-80` | hash of `git show 37c8e983:static/tokens.css`, LF-normalized, recorded with its command |

## Per-Cycle Risk Register

| Cycle | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| C1 | The wrap rule changes the header at widths between 1,186 and 1,400 px | Low | Medium | The audit measured the nav's minimum width at 961 to 1,186 px (G1), so wrapping cannot trigger above that; the rule sits inside the existing 1400 px breakpoint (`static/tokens.css:166-172`) and S-3.5 compares 1440 with the base |
| C1 | The active-link underline is absolutely positioned 14 px below the link (`templates/_chrome.html:18`) and overlaps the next wrapped row | Medium | Low | The ux gate judges the 375 render; a position change inside the narrow breakpoint is a layout rule only |
| C1 | Moving inline layout styles to classes changes computed values on `/` | Low | High | Values move verbatim; S-3.5 and C5's S-1.7 and S-2.6 compare with the base |
| C1 | `tweaks.js` on login also writes the stored accent inline (`static/tweaks.js:62`), so login inherits audit G3's dark-accent issue | High | Low | Same behaviour as every other page; G3 is Claude Design B5 (plan NB-2) |
| C1 | The login throttle locks the browser module out after ten failed logins (`app.py:126-129`) | Medium | Medium | SC-8 Mode B item 8: S-6.5 sends one wrong password per run |
| C2 | At the ref a 403 makes `r.json()` reject with no catch (`static/chrome.js:193-196`), so the modal stays open and nothing is shown | Certain at ref | High | The status-first read and the rejection branch; S-9.6 and S-9.9 |
| C2 | A 401 or 503 `{"error": ...}` from the auth hook (`app.py:213-235`) or a 500 shows "Dry-run: no action taken." at the ref (`static/chrome.js:195`) | Medium | High | The generic non-2xx branch; S-9.11 to S-9.13 |
| C2 | Served from a pair's worktree in Mode B, the code loads the real `.env` beside `app.py` (`app.py:69`, `:78`) and `sell_account` reads `.env` from the working directory (`app.py:5468`), so a confirmed request could post to Discord and log a liquidation (`app.py:5476-5498`) | Medium | High | SC-8 Mode B items 4 to 7: blank credentials in both files, network refused, never a pair's worktree; S-9.7 is harness-only |
| C2 | The wrong-phrase refusal cannot be provoked through the UI, because the client gate returns first (`static/chrome.js:188`) | Certain | Low | S-9.4 calls the request path directly in the harness (PM-4) |
| C2 | A changed request shape slips through review | Low | High | S-4.4 and S-9.8 compare with a record produced from the base `chrome.js`; S-4.7 runs the panic route tests unchanged; `security-auditor` reviews the head |
| C2 | Force run's message (`app.py:4934`) widens the button at 1440 while shown | Medium | Low | The status cluster already shrinks (`templates/_chrome.html:49`); the ux gate captures the header during the message |
| C2 | The test client propagates route exceptions in testing mode, so no real 500 body is produced | High | Medium | S-9.13 turns `PROPAGATE_EXCEPTIONS` off for its request so Flask's own 500 handler answers |
| C3 | Plans 01, 02 and 03 move or rename the cited classes and lines in `templates/ai_advisor.html`, `static/history.js` and `templates/performance.html` | High | Medium | The test-writer re-runs DC-1, DC-4 and DC-6 at the base SHA and carries every class found |
| C3 | Converting `.matrix-card` to `.hero-section` drops its `overflow-x: auto` (`templates/ai_advisor.html:396`) | High | Medium | C3 keeps the table inside an overflow wrapper; C4's S-5.7 verifies at 375 |
| C3 | Moving the tab bar into a panel breaks the tab switcher or the fragment (SC-6, `static/ai_advisor.js:646-686` at the ref) | Low | Medium | Every tab attribute kept; S-5.7 reaches the Correlations tab through `#correlations` |
| C3 | Non-clickable cards on `.sym-card` show a pointer cursor and hover shadow (`templates/index.html:328-336`) | High | Low | The `cursor` override SC-5 names; the ux gate judges the hover |
| C3 | Settings' Save and Discard change scroll behaviour when the band goes | Medium | Medium | The actions row stays outside `.settings-content`'s scroller (`templates/settings.html:141-145`); S-1.5 compares with the base capture |
| C3 | Tests pinning old headers fail (`tests/ui/test_cycle_5_history.py:225-246` and the others listed in C3) | Certain | Low | Updated in C3 under DC-6 |
| C4 | `.metrics-tbl` keeps `table-layout: fixed` at 100% width (`templates/performance.html:266-271`), so columns overlap instead of scrolling | Certain at ref | Medium | Intrinsic widths inside the 700 px breakpoint only, so 1440 is unchanged (S-5.10) |
| C4 | The modal body cannot scroll because a flex child keeps `min-height: auto` (`static/settings-modal.js:583-605`) | High | Medium | `min-height: 0` on the body; S-5.3 and S-5.4 |
| C4 | `.proposal-cards`' minimum of 22rem exceeds the 375 px content width (`templates/ai_advisor.html:602`) | Certain at ref | Medium | The `min(100%, <width>)` pattern of `templates/index.html:318` |
| C4 | The settings body hides overflow beside a fixed-width side nav (`templates/settings.html:84-108`) and clips inputs at 375 (audit S2) | Certain at ref | Medium | Stacking inside the 700 px breakpoint; S-5.11 |
| C5 | A page keeps a wide element unrelated to this plan's surfaces, so S-3.2 fails on that page | Medium | Medium | C5's implementer fixes it with a page-local layout rule; on `/` (plan 05's file, overview NB-11) the PM rules before any change |
| All | Browser rows need data the launcher copy may lack (today's exits, a pending Frontrunner proposal, a Discuss entry point) | Medium | Medium | Each row names its data source; seeding only through SC-8 Mode A item 7; a row the launcher cannot feed is a held row with its reason, carried to the PM's droplet check |
| All | A module-level Playwright import or a skip breaks CI collection or the zero-skip mandate (`tests/meta/test_zero_skip_xfail_close.py`) | Medium | Medium | SC-7: lazy import in a fixture, no skip; 01 C6's meta test |

## Cycle-Close Criteria
- The suite's Cycle-Close Criteria (`00-suite.md`), including the coverage record `records/coverage.json` with a test or a held row with its reason for every scenario the cycle owns and `plan-trace check-coverage` exiting 0.
- RED commit precedes GREEN commit; both SHAs and the coverage map path are in the cycle-complete message.
- `ruff format --check` and `ruff check` clean on the head.
- The cycle's own test files, plus `tests/js_syntax/test_js_syntax.py` and every existing test file the cycle edited or whose pins it updated, pass with at most 4 workers; the cycle's browser module passes with `-m browser -n 0` where its host has Playwright and the named launcher mode.
- C2: `tests/app/test_sell_account_panic_confirm.py` and `tests/app/test_f003_liquidation_confirmation.py` pass with zero diff; `templates/_chrome.html` has zero diff.
- C1 and C2: `templates/_chrome.html:92-144` has zero diff from the base.
- No diff to `alpha_bot_execution.py`, `math_engine.py`, `app.py`, `static/tokens.css` or `migrations/` from the base.
- No raw colour literal added outside a `var(--studio-...)` fallback (DC-2 grep on the cycle's diff); no SC-5 selector defined outside `static/layout.css`.
- `qa`, `code-reviewer` (when dispatched) and `ux-expert` verdict files read PASS for the head, and C2's `security-auditor` reads PASS.

## Merge Criteria
One PR for the plan, from `ux-overhaul/04-app-shell` into `ux-overhaul/03` (SC-9 rule 3), on the exact head being merged:
- The suite's Merge Criteria (`00-suite.md`): the head contains the model-unpin merge commit, every consumed tip entered by a merge commit, no rebase and no force push.
- `ruff format --check` and `ruff check` clean.
- Scoped pytest of `tests/app`, `tests/dashboard`, `tests/ui`, `tests/ai_advisor`, `tests/security` and `tests/js_syntax` with at most 4 workers, never `-n auto`, zero failures and zero errors, quoting the head SHA; CI green on that exact SHA.
- On the PM host, `python -m pytest -m browser -n 0` over every browser module of this plan, with `UX_LAUNCHER_URL` set to the SC-8 mode each module names (Mode A, and Mode B for the login and liquidation-refusal rows), results saved under `.claude/review/<head-sha12>/`.
- `git diff ux-overhaul/03...HEAD -- alpha_bot_execution.py math_engine.py migrations/ app.py static/tokens.css` is empty, and the five boundary test files of overview NB-15 pass.
- The screenshot gate per the suite protocol: every page and the settings modal at 1440x900, 375x812 and 320x640, light and dark, compared with the dashboard reference and the audit's captures by the ux-expert, `/` compared with this plan's own base capture after SC-1 readiness (full page at 1440, below the header at 375), every capture with SC-8's record.
- The `/review` skill run on the PR, findings resolved or accepted in writing.
- The PM's live functional check on the droplet after `/review`: T-1 to T-3 at 375 and 1440, Force run clicked once, Emergency Liquidate opened and cancelled without confirming, one refused liquidation provoked on a dry-run instance by a blank CSRF token or the empty account option, and `/login` in the dark theme; tests-green alone never satisfies this.
- The operator merges the stack to main after the Suite Gate, in SC-9 rule 4's order, with merge commits, never a squash.

## Items Raised to the PM
- Census entry points that are not columns of the plan's matrix but reach its rules: `POST /api/sell_account` (`app.py:5447`, AC-4, AC-9), `POST /api/trigger` (`app.py:4931`, AC-8), `GET /api/accounts` (`app.py:4938`, AC-4), `GET /api/csrf-token` (`app.py:382`, AC-4, AC-8, AC-9), `_auth_before_request` hook (`app.py:193`, AC-9), `_csrf_before_request` hook (`app.py:489`, AC-8, AC-9), `POST /api/settings` (`app.py:6127`, AC-1), `GET /api/symphony-settings/<symphony_name>` (`app.py:6170`, AC-5) and `POST /api/symphony-settings/<symphony_name>` (`app.py:6219`, AC-5). Each is tested at its own level in the cycle tables; the matrix may add them as columns.
- S-9.1's live part needs a Mode B instance with no account configured, because the empty account option renders only when `/api/accounts` returns none (`static/chrome.js:168-173`); with accounts configured, Mode B's live refusals are S-9.5 and S-9.6 and S-9.1 stays harness-only. The PM picks the instance's account setting when it prepares Mode B.
