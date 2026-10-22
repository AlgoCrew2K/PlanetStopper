# Feature: One page skeleton and a reachable header on every page
Status: planned
Story: none: operator chat request on 2026-10-22, saved verbatim in feature-plans/ux-overhaul/00-overview.source.md
Type: fix
Suite: ux-overhaul 04
Repos: PlanetStopper
Base: 03
Ref: PlanetStopper@37c8e983b64c6980c28199a3f9318486b60f3864
Sensitive: money (AC-4, AC-9)
Depends on: plan 03 (and through plan 02, plan 05 and the shared dashboard primitives plan 02 extracts into `static/layout.css`); the model-unpin plan (branch `model-unpin/fixtures`) merged into this plan's tip before its PR

## Trigger
The operator said every page but the dashboard looks wrong (G-3 to G-5). The app-wide UX audit rendered at the ref found five page-header patterns (the dashboard has no title card; AI Advisor a 36 px title card with an accent bar and a back link; History 48 px; Performance 43 px; Settings a 22 px grey band holding Save and Discard), two panel systems (every non-dashboard panel radius 16 px with a 0.8 px rule border and 20 by 24 px padding against the dashboard's 14 px, borderless, 28 by 32 px), four section-label styles, monospace table values where the dashboard uses Manrope tabular figures (audit G4, G5, P6, H5, S4, A3); at 375 px the nav row runs 961 to 1,186 px wide on every page but login, putting History, Settings, the mode pill, Force run now and Emergency Liquidate off-screen with the whole document scrolling sideways, and tables, the settings modal grid, the Chat side panel and Frontrunner card buttons clipped (G1, H1, P1, S2, M1, CH1, F1); login loads neither the app font nor the stored theme (UX-18, L1, L2); and five Advisor chip tints are raw light-theme colour literals (UX-3, A4). The PM assigned these to this plan (G-20) and ruled D-4 and D-10 (G-27, G-33), with Emergency Liquidate and the mode pill kept reachable and unchanged (G-14). The source is saved in `feature-plans/ux-overhaul/00-overview.source.md`.

What the code does at the ref:
1. The shared nav is one flex row with no wrap and `flex-shrink:0` on its links and right-rail buttons (`templates/_chrome.html:4-108`; links at `:12-42`, right rail at `:93-106`); only the ET clock and workspace switcher hide below 1,400 px (`static/tokens.css:166-172`).
2. History, Performance and AI Advisor open with a title header and a back link (`templates/history.html:269-277`, `templates/performance.html:393-424`, `templates/ai_advisor.html:1203-1218`); Settings has no `.page-wrap` and a header band holding Save and Discard (`templates/settings.html:836-848`). Each page's description line carries basis wording (for example Performance's "Live (if held) vs Planet Stopper-exited (shadow) · daily portfolio series, value-weighted", `templates/performance.html:396-401`).
3. The dashboard's primitives (`.hero-section`, `.sym-card`, `.section-header`, `.mini-stats`, `.status-pill`, `.legend-as-of`, `.card-footer-grid`) are defined only in `templates/index.html`'s own style block (`templates/index.html:27`, `:174`, `:277-305`, `:307`, `:323`, `:352`, `:697-750`); `static/layout.css` shares only the page inset. Plan 02 moves the primitives it needs into one shared definition (overview SD-6); this plan applies that definition to every other page and removes the page-local copies.
4. Performance table values use `font-family: monospace` (`templates/performance.html:293`).
5. `templates/login.html:1-9` links `tokens.css` and `layout.css` only; the font stylesheet is linked by `templates/_chrome.html:1-3`, and the stored theme is applied by `static/tweaks.js`, which login does not load.
6. Chip tints at `templates/ai_advisor.html:518`, `:649`, `:687`, `:777` and `:1009` are fixed `rgba(...)` values that do not follow the dark theme.
7. Force run now posts `/api/trigger` and shows "Triggered!" on any 2xx (`static/chrome.js:13-34`), but the route is disabled by design and answers with a success status whose message says manual triggering is disabled and runs come from the scheduler (`app.py:4931-4935`), so the button reports a run that never happens. This was found during this spec's investigation, outside the audit.

## Summary
The operator gets every page built on the dashboard's skeleton (no title cards or back links, the dashboard's panel, card, label and stat primitives), a header whose every control stays reachable at 375 px, a login page in the app's font and theme, theme-following chip tints, and a Force run button that tells the truth.
- Page headers conform to the dashboard's, keeping each page's basis caption and Settings' Save and Discard (AC-1).
- Panels, cards, section labels, stat rows and number faces conform to the dashboard's primitives (AC-2).
- At 375 px the header wraps, every control is reachable, and no page scrolls sideways (AC-3).
- Emergency Liquidate and the mode pill work and read exactly as before at every width (AC-4).
- Wide tables, the settings modal, the Chat panel and Frontrunner cards fit narrow screens (AC-5).
- Login loads the app font and the stored theme (AC-6).
- Advisor chip tints come from tokens (AC-7).
- Force run now reports the route's answer (AC-8).
- Emergency Liquidate shows the server's real answer when a request is refused (AC-9).

## Requirements as Given
[G-3] holy fuck this looks like shit, you've done literally 0 UX screenshot checking. 00-overview.source.md:8
[G-4] Your whole job for the rest of the day is an overhaul of the entire application, ensuring it fucking looks right on top of functions properly. 00-overview.source.md:8
[G-7] PM Step-2 answers ([PM-ASSUMED]; the operator is not available for questions; project directive: record assumptions, do not stop) 00-overview.source.md:11
[G-8] Building: (1) every page and tab of the dashboard app renders correctly, honestly and consistently with the main dashboard `/`, which is the operator's reference standard, and every control on it works. 00-overview.source.md:13
[G-9] (2) Users: the operator, through `/`, `/history`, `/performance`, `/settings` (+ the per-symphony settings modal), `/login`, `/ai-advisor` (tabs Overview, Correlations, Asset Swaps, Logic Changes, Chat, Strategy Builder, Frontrunner Builder) and the shared header/nav. 00-overview.source.md:14
[G-10] (3) Flows: as the audit records per surface; the workflow files under `.claude/workflows/` if any exist. 00-overview.source.md:15
[G-11] (4) Negatives: per the audit (empty states, unverifiable states, unavailable data, narrow widths, dark theme). 00-overview.source.md:16
[G-14] (7) Not sensitive, except plan 04 must keep Emergency Liquidate and the mode pill reachable and must not change their behavior. 00-overview.source.md:19
[G-20] 04-app-shell: every page header and panel conforms to the dashboard's; an interim narrow-width nav that wraps so every control (Settings, mode pill, Force run, Emergency Liquidate) stays reachable at 375 px without new design; login loads the app font and theme. 00-overview.source.md:27
[G-24] PM rulings on the audit's decisions (record as decisions in the overview; each a default the operator may override) 00-overview.source.md:30
[G-27] D-4 interim wrap now, real mobile layout after B1; 00-overview.source.md:34
[G-29] D-6 token changes wait for B5 (no token values changed by the team); 00-overview.source.md:36
[G-33] D-10 conform every page to the dashboard's header and panel primitives; 00-overview.source.md:40
[G-39] NB-16 (Emergency Liquidate fails silently on a rejected request): NOT a follow-up. 00-overview.source.md:52
[G-40] My G-14 brief meant the liquidation request, its confirmation steps (account id, phrase), the route and its server-side checks must not change; showing the operator the server's real answer when the request is rejected is required honesty, like Force run's real answer in 04 AC-8. 00-overview.source.md:52
[G-41] Add it to plan 04 as a criterion with scenarios for each of the five 400 reasons and the 403 CSRF case, marking plan 04 Sensitive; the request payload and flow stay byte-identical. 00-overview.source.md:52

## Requirement Inventory
These are the overview's R ids. Overview items R-21 and R-70 (plan numbering, the Base chain) are met by this plan's Suite, Base and Depends on headers.

| R-n | Requirement item | Source (G ids) | Maps to (AC ids, or NB-n) | Scope (full, or narrowed: what is left out) |
|---|---|---|---|---|
| R-4 | Every screen this plan changes is checked by rendered screenshots before it ships | G-3 | AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9 | full |
| R-5 | Every page and tab looks right | G-4, G-8 | AC-1, AC-2, AC-6, AC-7 | narrowed: this plan's part is page structure and primitives; new layouts and token values are Claude Design's (B1 to B5) |
| R-6 | Every control on every page functions | G-4, G-8 | AC-3, AC-4, AC-8 | narrowed: this plan's part is the header's controls; the workspace switcher outside `/` is NB-3 |
| R-9 | Open questions are answered by recorded defaults, never by stopping | G-7 | AC-1, AC-8 | full |
| R-11 | The overhaul covers every page, the modal, the Advisor tabs and the header | G-9 | AC-1, AC-2, AC-3, AC-5, AC-6 | full |
| R-12 | Flows follow the audit per surface, and workflow files where they exist | G-10 | AC-3, AC-5 | full |
| R-14 | Every page is usable at narrow widths | G-11, G-27 | AC-3, AC-5 | narrowed: an interim wrap and contained overflow only; the real mobile layout (B1) is left out |
| R-15 | Every page renders in the dark theme | G-11 | AC-6, AC-7 | narrowed: login follows the stored theme and chip tints follow tokens; the dark accent override and faint-ink contrast (B5) are left out |
| R-18 | Emergency Liquidate and the mode pill stay reachable on every page and their behaviour is unchanged | G-14 | AC-3, AC-4, AC-9 | full |
| R-60 | Every page header conforms to the dashboard's | G-20, G-33 | AC-1 | full |
| R-61 | Every page's panels, cards and section labels conform to the dashboard's | G-20, G-33 | AC-2 | full |
| R-62 | An interim narrow-width nav wraps so Settings, the mode pill, Force run and Emergency Liquidate stay reachable at 375 px | G-20, G-27 | AC-3 | full |
| R-64 | Login loads the app font and the stored theme | G-20 | AC-6 | full |
| R-65 | Chip tints use existing tokens, never raw colour literals | G-10, G-29 | AC-7 | full |
| R-69 | The PM's rulings are recorded as decisions, each a default the operator may override | G-24 | AC-1, AC-3 | full |
| R-73 | A refused Emergency Liquidate request shows the server's real answer, for each of the five 400 reasons and the 403 CSRF case, with the request and its confirmation flow unchanged | G-39, G-40, G-41 | AC-9 | full |

## User Tasks
Workflow (shared header on every page): no workflow file: this repository has no `.claude/workflows/` directory at the ref.

### T-1: Reach every header control on a phone
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Open any page at 375 px wide | none | The header wraps; Settings, the mode pill, Force run now and Emergency Liquidate are visible; the page does not scroll sideways | S-3.1, S-3.2 |
| 2 | Tap Emergency Liquidate, then Cancel | none | The confirm modal opens and closes exactly as at the ref; nothing is sent | S-4.1, S-4.3 |

### T-2: Move between pages
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Open History, Performance, Settings and AI Advisor in turn | none | Each opens on its first panel with no title card or back link; the nav marks the page | S-1.1, S-1.2 |
| 2 | Edit a setting and scroll | an unsaved edit | Save and Discard stay reachable and enabled | S-1.5 |

Workflow (login): no workflow file: this repository has no `.claude/workflows/` directory at the ref.

### T-3: Sign in with the dark theme stored
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Open `/login` | dark theme stored in the browser | The page renders dark in Manrope | S-6.1 |

## Earlier criteria on the same surface
| Earlier criterion (plan and AC) | Surface | Disposition (kept, changed or contradicted) | Why |
|---|---|---|---|
| `feature-plans/advisor-remediation-r1.md` AC-1 (the Advisor page's neutral description replaces the "Claude-powered" subtitle) | Advisor header | changed | The title card goes; the description stays as a caption (S-1.3) |
| `feature-plans/candidate-alert.md` AC-1 (a header indicator on every authenticated screen) | Header | kept | The indicator stays in the wrapped header (S-3.4) |
| `feature-plans/dashboard-auth.completed.md` AC-3 (`GET /login` renders a password form reachable without auth) | Login | kept | Only the font and theme change; the form, CSRF token and route are unchanged (S-6.4) |
| `feature-plans/fix-f003-panic-stop.md` and the panic-confirm tests (`tests/app/test_sell_account_panic_confirm.py`) | Emergency Liquidate | kept | AC-4 pins the modal and request unchanged |
| `feature-plans/studio-design-handoff.completed.md` (the Studio page layout, `static/layout.css` shared inset) | Page skeleton | kept | AC-1 and AC-2 extend the dashboard's primitives to every page; the inset is unchanged |
| `feature-plans/guard-alpha-preconditions.md` (follow house dashboard conventions for the Performance panel) | Performance panels | kept | AC-2 makes those conventions the dashboard's actual primitives |
| `feature-plans/ai-advisor-design-prompt.completed.md` (Advisor title card with an accent bar) | Advisor header | contradicted | D-10 removes the title card on every page |

## Acceptance Criteria (Building)
### AC-1: Page headers conform to the dashboard's
Requirement items: R-4, R-5, R-9, R-11, R-60, R-69
Rule: When History, Performance, Settings or AI Advisor renders, the page shall have no title card and no back link, its content shall start inside the shared `.page-wrap` with its first panel, its basis or description line shall stay as a caption in the `.legend-as-of` form, and Settings' Save and Discard buttons shall stay at the top of the settings content with their behaviour unchanged.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-1.1 | B | none | the operator opens `/history`, `/performance`, `/settings` and `/ai-advisor` | none of the four pages has an `h1` title card or a "Dashboard" back link; each page's first element after the header is inside `.page-wrap` |
| S-1.2 | B | none | each page renders | the nav link for that page carries `aria-current="page"` |
| S-1.3 | B | none | Performance and AI Advisor render | Performance's basis sentence and observation caption and the Advisor's advisory description show as captions in the first panel |
| S-1.4 | B | none | `/settings` renders | its content sits inside `.page-wrap`; Save and Discard show at the top of the settings content |
| S-1.5 | N8 | an unsaved edit on `/settings` | the operator scrolls to the bottom of a long section | Save and Discard stay reachable and enabled, and Save writes the pending edit as at the ref |
| S-1.6 | N8 | no unsaved edit | the page renders | Save and Discard are disabled, as at the ref |
| S-1.7 | N7 | the dashboard `/` | it renders | its structure outside the shared header subtree is unchanged |
| S-1.8 | N3 | Performance's observation caption has no count yet | the page renders | the caption reads the dash, as at the ref |

Negative coverage: N1 n/a: headers take no input · N2 n/a: nothing is refused; Save's refusals are unchanged and covered by plan 01 S-8.6 · N3 S-1.8 · N4 n/a: every page is behind the global auth hook, unchanged · N5 n/a: a static structure, the same on every load · N6 n/a: the header depends on no request · N7 S-1.7 · N8 S-1.5, S-1.6
Questions: Q-1

### AC-2: Panels, cards, labels, stat rows and numbers use the dashboard's primitives
Requirement items: R-4, R-5, R-11, R-61
Rule: When History, Performance, Settings or AI Advisor renders, each top-level panel shall use the dashboard's panel primitive (`.hero-section`), each card the `.sym-card` primitive, each section label `.section-header`, each row of headline figures `.mini-stats`, and each table number the dashboard's number face with tabular figures, with no page-local radius, border, padding, font or colour value standing in for one of them.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | none | History renders | its daily-strip and by-reason panels use the panel primitive, its section labels `.section-header`, its hero figures `.mini-stats` |
| S-2.2 | B | none | Performance renders | its headline strip uses `.mini-stats`, its chart, preconditions, exit-turnover and risk panels the panel primitive, their titles `.section-header`, and no table cell uses a monospace face |
| S-2.3 | B | none | Settings renders | its sections use the panel primitive and their titles `.section-header` |
| S-2.4 | B | none | each AI Advisor tab renders | its top-level panels use the panel primitive, its proposal and Frontrunner cards `.sym-card` with the name in `.card-name`, its section labels `.section-header` |
| S-2.5 | N7 | the primitives | each page's stylesheets are scanned | the five primitives are defined once and shared; no page redefines one with different values |
| S-2.6 | N7 | the dashboard `/` | it renders | it uses the same shared primitive definitions as the other pages, and its screenshots match a capture of `/` taken on this plan's base (the stack after plans 05 and 03), the full page at 1440 px light and dark and the area below the header at 375 px (the 375 px header changes by design), both taken after the readiness signal (`Chart.getChart` on the hero canvas truthy and every triggered card's verdict text present), never the audit's captures |
| S-2.7 | N7 | the dark theme | each page renders | each primitive takes its colours from tokens only |
| S-2.8 | N3 | an empty state on any page (for example History with no exits) | it renders | the empty state sits inside the panel primitive |

Negative coverage: N1 n/a: no input · N2 n/a: display only · N3 S-2.8 · N4 n/a: pages behind the global auth hook, unchanged · N5 n/a: static structure · N6 n/a: structure depends on no request · N7 S-2.5, S-2.6, S-2.7 · N8 n/a: no edit state changes
Questions: none

### AC-3: At 375 px the header wraps and every control is reachable
Requirement items: R-4, R-6, R-11, R-12, R-14, R-18, R-62, R-69
Rule: While the viewport is 375 px wide, the shared header on every page shall wrap onto as many rows as it needs so every nav link, the engine status, the candidate-alert indicator, the mode pill, Force run now and Emergency Liquidate are visible and operable, and no page's document shall be wider than the viewport.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | a 375 by 812 viewport | the operator opens each of `/`, `/history`, `/performance`, `/settings` and `/ai-advisor` | Settings, the mode pill, Force run now and Emergency Liquidate are each inside the viewport without scrolling sideways |
| S-3.2 | B | the same viewport | each page loads | the document's scroll width equals the viewport width |
| S-3.3 | N1 | a 320 px viewport | each page loads | S-3.1 and S-3.2 still hold |
| S-3.4 | B | the same viewport | the header renders | the candidate-alert indicator and its badge stay visible |
| S-3.5 | N7 | a 1440 by 900 viewport | each page loads | the header is one row, as at the ref |
| S-3.6 | N5 | the operator resizes from 1440 to 375 and back | each resize settles | the header wraps and unwraps without losing a control |
| S-3.7 | N7 | `/login` | it loads at 375 px | it still fits, as at the ref |

Negative coverage: N1 S-3.3 · N2 n/a: nothing is refused · N3 n/a: the header holds no data-dependent state that can be unavailable beyond the existing engine status · N4 n/a: header behind the global auth hook, unchanged · N5 S-3.6 · N6 n/a: wrapping depends on no request · N7 S-3.5, S-3.7 · N8 n/a: no edit state
Questions: none

### AC-4: Emergency Liquidate and the mode pill work and read exactly as before at every width
Requirement items: R-4, R-6, R-18
Rule: When the operator opens Emergency Liquidate on any page at any width, the confirm modal shall open with the same account list, the same LIQUIDATE phrase gate and the same `POST /api/sell_account` request as at the ref, and the mode pill shall show the same text and state as at the ref.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | B | each page at 1440 px and at 375 px | the operator clicks Emergency Liquidate | the modal opens with the accounts list and the Confirm button disabled |
| S-4.2 | N1 | the modal is open | the operator types "liquidate" in lower case | Confirm stays disabled |
| S-4.3 | N8 | the modal is open with "LIQUIDATE" typed | the operator clicks Cancel | the modal closes and no request is sent |
| S-4.4 | B | the modal is open with an account chosen and "LIQUIDATE" typed | the operator clicks Confirm | one `POST /api/sell_account` is sent with the same body and headers as at the ref |
| S-4.5 | N2 | the route refuses (wrong phrase or missing account id) | the request returns | the existing refusal is shown and nothing else changes |
| S-4.6 | B | `LIVE_EXECUTION` true | any page renders | the mode pill reads LIVE with the negative token fill; with it false the pill reads DRY RUN, as at the ref |
| S-4.7 | N7 | any page | the shell changes | `tests/app/test_sell_account_panic_confirm.py` and `tests/app/test_f003_liquidation_confirmation.py` pass unchanged |
| S-4.8 | N5 | the operator clicks Confirm twice | both clicks land | the request count equals the ref's for the same clicks |
| S-4.9 | N6 | `/api/accounts` fails when the modal opens | the modal renders | the existing account-loading failure text shows and Confirm stays disabled |

Negative coverage: N1 S-4.2 · N2 S-4.5 · N3 n/a: the pill's mode is always defined by `_build_meta` · N4 n/a: the route's auth and CSRF hooks are unchanged and covered by its existing tests · N5 S-4.8 · N6 S-4.9 · N7 S-4.7 · N8 S-4.3
Questions: none

### AC-5: Wide content fits narrow screens
Requirement items: R-4, R-11, R-12, R-14
Rule: While the viewport is 375 px wide, each wide table (History exits, Performance risk, preconditions and exit-turnover tables, the Correlations matrix) shall scroll inside its own container, the settings page's labels and inputs and the settings modal's grid shall not be clipped, the settings modal's Cancel and Save shall be reachable, the Chat side panel shall open inside the viewport, and each Frontrunner card's Approve and Reject shall be visible.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | a 375 px viewport | the operator opens History with exits | the exits table scrolls inside its container and every column is reachable by that scroll |
| S-5.2 | B | the same | Performance renders | the risk, preconditions and exit-turnover tables scroll inside their containers; headline labels are not truncated |
| S-5.3 | B | the same | the settings modal opens from a card | the locked-variables grid is not clipped, and Cancel and Save are reachable by scrolling the modal body |
| S-5.4 | B | a 1440 by 900 viewport | the settings modal opens | Cancel and Save are reachable |
| S-5.5 | B | a 375 px viewport | the operator opens Chat from a Discuss button | the side panel's left edge is at or inside the viewport's left edge |
| S-5.6 | B | the same | the Frontrunner tab renders a pending card | Approve and Reject are visible |
| S-5.7 | B | the same | the Correlations tab renders | the matrix scrolls inside its container |
| S-5.8 | N1 | a 320 px viewport | the same pages render | the same holds |
| S-5.9 | N8 | an unsaved edit in the settings modal | the operator scrolls the modal body | the edit and the Save button's state are kept |
| S-5.10 | N7 | a 1440 px viewport | every page renders | no table gains a scroll bar it did not have at the ref |
| S-5.11 | B | a 375 px viewport | the operator opens `/settings` | every label and input is visible without clipping, each input fits the viewport, and the side navigation does not push the content off-screen |

Negative coverage: N1 S-5.8 · N2 n/a: nothing is refused · N3 n/a: containment does not depend on data availability; empty tables are AC-2's S-2.8 · N4 n/a: pages behind the global auth hook, unchanged · N5 n/a: scrolling is not an action that repeats state · N6 n/a: containment depends on no request · N7 S-5.10 · N8 S-5.9
Questions: none

### AC-6: Login loads the app font and the stored theme
Requirement items: R-4, R-5, R-11, R-15, R-64
Rule: When `/login` renders, it shall load the same font stylesheet and the same theme script as the other pages, so it renders in the stored theme and the app's typeface.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | the dark theme stored in the browser | the operator opens `/login` | the root element's `data-theme` is "dark" and the page's computed font family is the app's typeface |
| S-6.2 | N3 | no stored theme | the operator opens `/login` | it renders light, as other pages do |
| S-6.3 | N6 | the font stylesheet cannot load | the operator opens `/login` | the form renders in the fallback face and works |
| S-6.4 | N7 | any theme | the operator signs in | the form, its CSRF token, the throttle and the redirect behave as at the ref |
| S-6.5 | N2 | a wrong password | the operator submits | the existing generic error shows in the stored theme |

Negative coverage: N1 n/a: the theme comes from a fixed set of stored values · N2 S-6.5 · N3 S-6.2 · N4 n/a: `/login` is the auth-exempt page by design (`_AUTH_EXEMPT_ENDPOINTS`), unchanged · N5 n/a: loading the page twice reads the same stored theme · N6 S-6.3 · N7 S-6.4 · N8 n/a: no edit state
Questions: none

### AC-7: Advisor chip tints come from tokens
Requirement items: R-4, R-5, R-15, R-65
Rule: When the AI Advisor page renders a chip, pill or notice tint, the tint shall derive from an existing `--studio-*` token, never a raw colour literal, and no token value shall change.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | the explain-only notice, the promoted incubation chip, the passed gate pill, the risk-on sentiment chip and the approved retirement status | the page's styles are read | each tint references a `--studio-*` token |
| S-7.2 | N7 | `static/tokens.css` | the plan lands | its token values are byte-identical to the ref |
| S-7.3 | B | the dark theme | the page renders | each of the five tints follows the dark theme's token values |
| S-7.4 | N1 | `templates/ai_advisor.html` | it is scanned | no `rgba(` or hex colour appears outside a `var(--studio-...)` fallback in a chip, pill or notice rule |

Negative coverage: N1 S-7.4 · N2 n/a: styling only · N3 n/a: tokens are always defined · N4 n/a: styling only · N5 n/a: static · N6 n/a: no request · N7 S-7.2 · N8 n/a: no edit state
Questions: none

### AC-8: Force run now reports the route's answer
Requirement items: R-4, R-6, R-9
Rule: When the operator clicks Force run now on any page, the button shall show the answer `/api/trigger` returns, and shall not show "Triggered!" for a request the route declines.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-8.1 | B | the route answers with its disabled message | the operator clicks Force run now | the button area shows that message (that runs come from the scheduler) for a few seconds, then returns to its label |
| S-8.2 | N2 | the route refuses with 403 (no CSRF token) | the operator clicks | the button shows "Error" |
| S-8.3 | N6 | the network fails | the operator clicks | the button shows "Error" |
| S-8.4 | N5 | the operator clicks twice quickly | both land | one request is sent while the button is disabled, as at the ref |
| S-8.5 | N7 | the route | the button is clicked | `/api/trigger` still starts no engine run (`app.py:4931-4935`) |

Negative coverage: N1 n/a: the button takes no input · N2 S-8.2 · N3 n/a: the route always answers with a message or an error · N4 n/a: the route sits behind the global auth and CSRF hooks, unchanged · N5 S-8.4 · N6 S-8.3 · N7 S-8.5 · N8 n/a: no edit state
Questions: Q-2

### AC-9: Emergency Liquidate shows the server's real answer when a request is refused
Requirement items: R-4, R-18, R-73
Rule: If `POST /api/sell_account` refuses a liquidation request or cannot be reached, then the panic flow shall tell the operator the server's real answer (a 400's own message, as at the ref; for a 403 a statement that the session check refused the request and nothing was liquidated; for any other refusal without a message, such as 401, 503 or 500, the HTTP status and that nothing was liquidated) and never stay silent or claim a liquidation, while the request body, its headers, the confirmation steps (account choice and the LIQUIDATE phrase), the modal's open and close behaviour, and the route's checks stay byte-identical to the ref.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-9.1 | N2 | the route answers 400 "confirm_account_id is required" | the operator confirms | the modal closes and the alert shows that message, as at the ref |
| S-9.2 | N2 | the route answers 400 "confirm_account_id does not match account_id" | the operator confirms | the modal closes and the alert shows that message, as at the ref |
| S-9.3 | N2 | the route answers 400 "confirm_phrase is required" | the operator confirms | the modal closes and the alert shows that message, as at the ref |
| S-9.4 | N2 | the route answers 400 "confirm_phrase must be exactly LIQUIDATE" | the operator confirms | the modal closes and the alert shows that message, as at the ref |
| S-9.5 | N2 | the route answers 400 "Missing credentials or account ID." | the operator confirms | the modal closes and the alert shows that message, as at the ref |
| S-9.6 | N2 | the CSRF token is missing or invalid, so the route answers 403 HTML | the operator confirms | the modal closes and the alert states that the session check refused the request and nothing was liquidated; no unhandled error is raised |
| S-9.7 | B | the route accepts the request | the operator confirms | the modal closes and shows the acceptance as at the ref |
| S-9.8 | N7 | any of S-9.1 to S-9.7 | the request is sent | its URL, method, headers and body are byte-identical to the ref's for the same inputs |
| S-9.9 | N6 | the network fails | the operator confirms | the modal closes and the alert states the request could not be sent and nothing was liquidated |
| S-9.10 | N5 | a refusal was shown | the operator reopens Emergency Liquidate and confirms again | exactly one new request is sent and its own answer is shown |
| S-9.11 | N4 | the session has expired, so the route answers 401 `{"error": "unauthenticated"}` | the operator confirms | the alert shows HTTP 401 and that nothing was liquidated, never "Dry-run: no action taken." |
| S-9.12 | N6 | the deployment is misconfigured, so the route answers 503 `{"error": "misconfigured"}` | the operator confirms | the alert shows HTTP 503 and that nothing was liquidated |
| S-9.13 | N6 | the route fails with 500 | the operator confirms | the alert shows HTTP 500 and that nothing was liquidated |

Negative coverage: N1 n/a: the inputs are the existing account choice and phrase, whose client checks are unchanged (S-4.2) · N2 S-9.1, S-9.2, S-9.3, S-9.4, S-9.5, S-9.6 · N3 n/a: every refusal carries a status; the network failure is S-9.9 · N4 S-9.11 · N5 S-9.10 · N6 S-9.9, S-9.12, S-9.13 · N7 S-9.8 · N8 n/a: the phrase gate is the existing pending state, covered by S-4.3
Questions: none

## Rule-by-Entry-Point Matrix
| Rule | `/` | `/history` | `/performance` | `/settings` | `/ai-advisor` | `/login` |
|---|---|---|---|---|---|---|
| AC-1 no title card, content in `.page-wrap` | S-1.7 | S-1.1 | S-1.1 | S-1.4 | S-1.1 | n/a: login is a single card with no page header by design |
| AC-2 dashboard primitives | S-2.6 | S-2.1 | S-2.2 | S-2.3 | S-2.4 | n/a: login has one card and no panels |
| AC-3 header reachable at 375 px | S-3.1 | S-3.1 | S-3.1 | S-3.1 | S-3.1 | S-3.7 |
| AC-4 Emergency Liquidate unchanged | S-4.1 | S-4.1 | S-4.1 | S-4.1 | S-4.1 | n/a: login has no header |
| AC-8 Force run reports the answer | S-8.1 | S-8.1 | S-8.1 | S-8.1 | S-8.1 | n/a: login has no header |
| AC-9 refusals shown, request unchanged | S-9.1, S-9.6 | S-9.1, S-9.6 | S-9.1, S-9.6 | S-9.1, S-9.6 | S-9.1, S-9.6 | n/a: login has no header |

## Not Building
Non-goals:
- NB-1: A mobile navigation design or a narrow-table pattern (overview NB-6, Claude Design B1).
- NB-2: Any token value change, including the faint-ink contrast and the dark accent override (overview NB-10, Claude Design B5).
- NB-3: Making the workspace switcher filter pages other than `/`; plan 05 AC-6 makes it filter the dashboard, and elsewhere it keeps its behaviour at the ref (approved by the PM, [PM-ASSUMED], operator may override).

Behaviour that stays exactly as it is:
- NB-4: Every page's data, numbers, copy and controls other than the header structure, Force run's message and the five tints.
- NB-5: `POST /api/sell_account`'s request, its confirm phrase and account gate, the route's checks, and the mode pill's source (`_build_meta`); only the modal's display of a refusal changes (AC-9).

Deferred:
- NB-6: The floating tweaks trigger over content and the open tweaks panel covering its toggle (overview NB-13).

Protected surfaces consumed, not changed:
- NB-7: `POST /api/sell_account`, `POST /api/trigger`'s disabled behaviour, `_AUTH_EXEMPT_ENDPOINTS`, the login throttle and CSRF hooks, `alpha_bot_execution.py` and `math_engine.py`.

## Defect Classes
| DC-n | Class (what goes wrong) | Trigger (B or N1-N8) | Sibling search (tool, query, scope) | Scenarios | Raised in review as |
|---|---|---|---|---|---|
| DC-1 | A page-local value standing in for a shared primitive | B | grep `border-radius:\|padding:\|font-family:\|font-size:` in each template's style block, and each class that duplicates a dashboard primitive, PlanetStopper | S-2.1, S-2.2, S-2.3, S-2.4, S-2.5 | - |
| DC-2 | A raw colour literal instead of a token | B | grep `rgba(\|#[0-9a-fA-F]\{3,6\}` outside `var(--studio-` in `templates/` and `static/*.css`, PlanetStopper | S-7.1, S-7.4 | - |
| DC-3 | A control off-screen or clipped at narrow widths | N1 | read: each page's rendered header and wide element at 375 and 320 px | S-3.1, S-3.3, S-5.1, S-5.3, S-5.5, S-5.6 | - |
| DC-4 | A rule applied on some pages and missing on another | N2 | read: each template's header, panel and label usage against the matrix | S-1.1, S-2.1, S-2.2, S-2.3, S-2.4 | - |
| DC-5 | A control that reports success, or nothing, for a declined request | N2 | grep `r.ok ?\|'Triggered'\|r.json()\|\.then(function (d)` without a status check in `static/`, PlanetStopper | S-8.1, S-8.2, S-9.1, S-9.6 | - |
| DC-6 | A protected request changed by a shell refactor | N7 | grep `sell_account\|panic-confirm-btn\|confirm_phrase\|mode-pill` in `templates/`, `static/` and `tests/`, PlanetStopper | S-4.4, S-4.6, S-4.7 | - |
| DC-7 | A pending edit lost when its controls move | N8 | grep `btn-save\|btn-discard\|markDirty` in `templates/settings.html` and `static/settings.js`, PlanetStopper | S-1.5, S-5.9 | - |

## Worked Example
At 375 by 812 on `/performance`, the header wraps to three rows: the wordmark and nav links, the engine status and candidate indicator, then the mode pill reading DRY RUN, Force run now and Emergency Liquidate; the document scroll width is 375. The page opens on the headline `.mini-stats` row with the basis caption above it, and the risk table scrolls sideways inside its panel (S-3.1, S-3.2, S-1.3, S-5.2). Clicking Force run now shows that runs come from the scheduler (S-8.1). Clicking Emergency Liquidate opens the same modal as at the ref; Cancel closes it with no request (S-4.1, S-4.3).

Failure case: the operator types "liquidate" in the modal on a 375 px screen; Confirm stays disabled exactly as at 1440 px (S-4.2).

## Fail-Closed Behaviour
Refusals on the write paths the header's controls reach, each unchanged by this plan:
- `POST /api/trigger`: answers 200 with its disabled message (`app.py:4931-4935`), which AC-8 now shows (S-8.1); without a CSRF token it is refused with 403 HTML by the global hook (`app.py:379`), shown as "Error" (S-8.2).
- `POST /api/sell_account`: 400 when `confirm_account_id` is missing or does not match, when `confirm_phrase` is missing or is not exactly LIQUIDATE, and when credentials or the account id are missing (`app.py:5452-5472`); without a CSRF token, 403 HTML from the global hook (`app.py:379`). At the ref the modal handles a 403 silently (`static/chrome.js:189-196`); AC-9 shows each refusal's real answer, with the request, the confirmation steps and the route's checks unchanged (S-9.1 to S-9.6).

## Security Considerations
- Input validation and injection: no new input; the header and login keep their escaping.
- Authorization and access control: no route or guard changes; `/login` stays the only auth-exempt page with the same hooks.
- Data exposure: none; the mode pill and account labels show what they show at the ref.
- Client-side rendering: Force run's message is set through `textContent`.
- Abuse or rate limits: Force run's single-request behaviour is unchanged.
- Money: Emergency Liquidate reaches `POST /api/sell_account`, which can liquidate real positions in live mode; AC-4 pins it unchanged at every width, so the plan reads Sensitive money (AC-4) as the intake required.

## Design-System Mapping
| Component | Element | Required primitive or token | Notes |
|---|---|---|---|
| Every page | skeleton | `.page-wrap` (`static/layout.css`) | Settings gains it |
| Every page | top-level panel | `.hero-section` values (paper fill, no border, radius 14 px, padding 28 by 32 px) | shared once, not copied |
| Every page | card | `.sym-card` with `.card-name` | proposal, retirement and Frontrunner cards |
| Every page | section label | `.section-header` | replaces four styles |
| History, Performance | headline figures | `.mini-stats` | |
| Performance | table numbers | the dashboard's sans face with tabular figures | replaces monospace |
| Every page | caption | `.legend-as-of` | keeps each page's basis line |
| Header | wrapped rows | existing nav styles with wrapping | interim only (D-4) |
| Advisor | chip tints | `color-mix` over `--studio-pos`, `--studio-accent` or the `*-tint` tokens | no new token |
| Login | font and theme | the font stylesheet in `templates/_chrome.html:1-3` and `static/tweaks.js` | |

## Definition of Done (Verification)
| AC | Scenarios | Test level | Real-entrypoint test |
|---|---|---|---|
| AC-1 | S-1.1, S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8 | server-render structure assertions per page | `GET` each page; live Settings edit and scroll |
| AC-2 | S-2.1, S-2.2, S-2.3, S-2.4, S-2.5, S-2.6, S-2.7, S-2.8 | class-usage and raw-value scans of rendered pages and stylesheets; no computed-value assertion | live render compared with the dashboard reference captures |
| AC-3 | S-3.1, S-3.2, S-3.3, S-3.4, S-3.5, S-3.6, S-3.7 | browser render at 375, 320 and 1440 px | live render of every page |
| AC-4 | S-4.1, S-4.2, S-4.3, S-4.4, S-4.5, S-4.6, S-4.7, S-4.8, S-4.9 | the existing panic route tests; modal under a real DOM | live modal open, phrase, Cancel at 375 and 1440 px; Confirm only against a dry-run instance |
| AC-5 | S-5.1, S-5.2, S-5.3, S-5.4, S-5.5, S-5.6, S-5.7, S-5.8, S-5.9, S-5.10, S-5.11 | browser render | live render of each page and the modal |
| AC-6 | S-6.1, S-6.2, S-6.3, S-6.4, S-6.5 | server-render assertion of `/login`'s head; browser render | live `/login` in both themes |
| AC-7 | S-7.1, S-7.2, S-7.3, S-7.4 | stylesheet scan; token file byte comparison | live render in dark |
| AC-8 | S-8.1, S-8.2, S-8.3, S-8.4, S-8.5 | `forceRun` under node with the route's captured answers | live click on the droplet |
| AC-9 | S-9.1, S-9.2, S-9.3, S-9.4, S-9.5, S-9.6, S-9.7, S-9.8, S-9.9, S-9.10, S-9.11, S-9.12, S-9.13 | `submitPanicLiquidation` under a real DOM with each captured route answer (five 400 bodies, the 403 HTML body, the 401 and 503 JSON bodies, a 500, a 200); the wrong-phrase case, which the client blocks, in the same node harness; request bytes compared with the ref's | live modal on a dry-run instance, refusals provoked with a blank CSRF token and with the empty account option; never confirmed against a live account; the security auditor reviews the liquidation-messaging cycle |

Gates: `ruff format --check`; `ruff check`; a scoped pytest run of `tests/app`, `tests/dashboard`, `tests/ui`, `tests/ai_advisor`, `tests/security` and `tests/js_syntax` with at most 4 workers, never `-n auto`; CI green on the exact SHA; after `/review`, the PM's live check on the droplet of T-1 to T-3, with Emergency Liquidate opened and cancelled, never confirmed.
Visual tests assert class and token usage and the absence of raw values, never a computed colour or size; layout facts (scroll width, a control inside the viewport) are asserted on a live render.
Fixture provenance: none needed beyond the rendered pages; the route answers for Force run are captured from the running app at the ref.
Screenshots: every page and the settings modal at 1440x900, 375x812 and 320x640, light and dark, compared with the dashboard reference and the audit's captures (N-2, D-3, H-3, P-3, S-3, A-OV-3, A-FR-3, M-3, L-1, L-2 series).

## Known-Unconfirmed and Standing Risk
- The audit could not confirm whether the settings modal body scrolls to its footer at 1440x900 (audit M4); S-5.4 tests it.
- Plan 02 moves the dashboard's primitives out of `templates/index.html` and pins `/` unchanged in its own PR (plan 02 S-4.18); this plan applies the shared definition to the other pages, and S-1.7 and S-2.6 pin `/` against this plan's base.
- Tests that pin the old headers (for example `back-link`, `hist-title`, `perf-title`, `adv-title`) need updating; DC-4 and the overview's DC-6 carry the search.

## Open Questions
| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | Each page's description line carries basis wording; keep it as a caption or drop it with the title card? | AC-1 | Keep it as a caption | default approved by the PM, [PM-ASSUMED], operator may override |
| Q-2 | `/api/trigger` is disabled by design (dashboards must not spawn the engine). Keep Force run now with an honest message, or remove the button? | AC-8 | Keep it reachable, as the brief lists it, with the route's message | default approved by the PM, [PM-ASSUMED], operator may override |

## Decisions
| ID | Decision | Rationale |
|---|---|---|
| DEC-1 | Plan 02 makes the dashboard's primitives shared definitions with the same values; this plan applies them to every other page and deletes the page-local copies | D-10; one source keeps pages from drifting again (DC-1), and plans 02 and 03 need the primitives before this plan lands |
| DEC-2 | The narrow header wraps rather than collapses | D-4: no new design before B1 |
| PM-1 | S-2.6 compares full pages at 1440 px and the area below the header at 375 px; S-1.7 excludes the header subtree | PM ruling on the Gate-2 approach: the header changes by design |
| PM-2 | AC-5 covers the settings page's labels and inputs at 375 px (S-5.11) | PM ruling on the Gate-2 approach |
| PM-3 | AC-9 covers 401, 503 and 500 refusals, which today read "Dry-run: no action taken."; the request stays byte-identical | PM ruling on the Gate-2 approach |
| PM-4 | The live check provokes refusals with a blank CSRF token and the empty account option; the wrong-phrase case is blocked in the client and tested in the node harness; the security auditor reviews the liquidation-messaging cycle | PM ruling on the Gate-2 approach |
| PM-A | Plan 04 approved at Step 7 under the project's standing autonomy directive (operator unavailable), with its Gate-2 approach `feature-plans/ux-overhaul/approach/04-app-shell.md` and the suite approach `feature-plans/ux-overhaul/approach/00-suite.md`; every open question takes its default or its recorded ruling, and every Not Building entry is approved | [PM-ASSUMED]; the operator may override |

## Docs to Update
- `docs/generated/` entries for each touched template and `static/chrome.js`, and `docs/generated/INDEX.md`.
- `README.md` where it describes the pages.
- A `DECISIONS.md` entry appended at ship by the doc writer.
- A drafted `.claude/CLAUDE.md` Key Files row update for `templates/_chrome.html`, `static/layout.css` and `templates/login.html`, for PM approval.

## Follow-Ups
- The mobile layout (NB-1): Claude Design B1, overview NB-6.
- Token decisions (NB-2): Claude Design B5, overview NB-10.
- The workspace switcher beyond `/` (NB-3): its own plan.
- The tweaks trigger and panel (NB-6): overview NB-13.
