# Approval packet: One page skeleton and a reachable header on every page

## What is being built

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

## Acceptance criteria and scenarios

### AC-1: Page headers conform to the dashboard's
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

Negative coverage: N1 n/a: headers take no input | N2 n/a: nothing is refused; Save's refusals are unchanged and covered by plan 01 S-8.6 | N3 S-1.8 | N4 n/a: every page is behind the global auth hook, unchanged | N5 n/a: a static structure, the same on every load | N6 n/a: the header depends on no request | N7 S-1.7 | N8 S-1.5, S-1.6

### AC-2: Panels, cards, labels, stat rows and numbers use the dashboard's primitives
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

Negative coverage: N1 n/a: no input | N2 n/a: display only | N3 S-2.8 | N4 n/a: pages behind the global auth hook, unchanged | N5 n/a: static structure | N6 n/a: structure depends on no request | N7 S-2.5, S-2.6, S-2.7 | N8 n/a: no edit state changes

### AC-3: At 375 px the header wraps and every control is reachable
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

Negative coverage: N1 S-3.3 | N2 n/a: nothing is refused | N3 n/a: the header holds no data-dependent state that can be unavailable beyond the existing engine status | N4 n/a: header behind the global auth hook, unchanged | N5 S-3.6 | N6 n/a: wrapping depends on no request | N7 S-3.5, S-3.7 | N8 n/a: no edit state

### AC-4: Emergency Liquidate and the mode pill work and read exactly as before at every width
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

Negative coverage: N1 S-4.2 | N2 S-4.5 | N3 n/a: the pill's mode is always defined by `_build_meta` | N4 n/a: the route's auth and CSRF hooks are unchanged and covered by its existing tests | N5 S-4.8 | N6 S-4.9 | N7 S-4.7 | N8 S-4.3

### AC-5: Wide content fits narrow screens
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

Negative coverage: N1 S-5.8 | N2 n/a: nothing is refused | N3 n/a: containment does not depend on data availability; empty tables are AC-2's S-2.8 | N4 n/a: pages behind the global auth hook, unchanged | N5 n/a: scrolling is not an action that repeats state | N6 n/a: containment depends on no request | N7 S-5.10 | N8 S-5.9

### AC-6: Login loads the app font and the stored theme
Rule: When `/login` renders, it shall load the same font stylesheet and the same theme script as the other pages, so it renders in the stored theme and the app's typeface.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | the dark theme stored in the browser | the operator opens `/login` | the root element's `data-theme` is "dark" and the page's computed font family is the app's typeface |
| S-6.2 | N3 | no stored theme | the operator opens `/login` | it renders light, as other pages do |
| S-6.3 | N6 | the font stylesheet cannot load | the operator opens `/login` | the form renders in the fallback face and works |
| S-6.4 | N7 | any theme | the operator signs in | the form, its CSRF token, the throttle and the redirect behave as at the ref |
| S-6.5 | N2 | a wrong password | the operator submits | the existing generic error shows in the stored theme |

Negative coverage: N1 n/a: the theme comes from a fixed set of stored values | N2 S-6.5 | N3 S-6.2 | N4 n/a: `/login` is the auth-exempt page by design (`_AUTH_EXEMPT_ENDPOINTS`), unchanged | N5 n/a: loading the page twice reads the same stored theme | N6 S-6.3 | N7 S-6.4 | N8 n/a: no edit state

### AC-7: Advisor chip tints come from tokens
Rule: When the AI Advisor page renders a chip, pill or notice tint, the tint shall derive from an existing `--studio-*` token, never a raw colour literal, and no token value shall change.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | the explain-only notice, the promoted incubation chip, the passed gate pill, the risk-on sentiment chip and the approved retirement status | the page's styles are read | each tint references a `--studio-*` token |
| S-7.2 | N7 | `static/tokens.css` | the plan lands | its token values are byte-identical to the ref |
| S-7.3 | B | the dark theme | the page renders | each of the five tints follows the dark theme's token values |
| S-7.4 | N1 | `templates/ai_advisor.html` | it is scanned | no `rgba(` or hex colour appears outside a `var(--studio-...)` fallback in a chip, pill or notice rule |

Negative coverage: N1 S-7.4 | N2 n/a: styling only | N3 n/a: tokens are always defined | N4 n/a: styling only | N5 n/a: static | N6 n/a: no request | N7 S-7.2 | N8 n/a: no edit state

### AC-8: Force run now reports the route's answer
Rule: When the operator clicks Force run now on any page, the button shall show the answer `/api/trigger` returns, and shall not show "Triggered!" for a request the route declines.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-8.1 | B | the route answers with its disabled message | the operator clicks Force run now | the button area shows that message (that runs come from the scheduler) for a few seconds, then returns to its label |
| S-8.2 | N2 | the route refuses with 403 (no CSRF token) | the operator clicks | the button shows "Error" |
| S-8.3 | N6 | the network fails | the operator clicks | the button shows "Error" |
| S-8.4 | N5 | the operator clicks twice quickly | both land | one request is sent while the button is disabled, as at the ref |
| S-8.5 | N7 | the route | the button is clicked | `/api/trigger` still starts no engine run (`app.py:4931-4935`) |

Negative coverage: N1 n/a: the button takes no input | N2 S-8.2 | N3 n/a: the route always answers with a message or an error | N4 n/a: the route sits behind the global auth and CSRF hooks, unchanged | N5 S-8.4 | N6 S-8.3 | N7 S-8.5 | N8 n/a: no edit state

### AC-9: Emergency Liquidate shows the server's real answer when a request is refused
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

Negative coverage: N1 n/a: the inputs are the existing account choice and phrase, whose client checks are unchanged (S-4.2) | N2 S-9.1, S-9.2, S-9.3, S-9.4, S-9.5, S-9.6 | N3 n/a: every refusal carries a status; the network failure is S-9.9 | N4 S-9.11 | N5 S-9.10 | N6 S-9.9, S-9.12, S-9.13 | N7 S-9.8 | N8 n/a: the phrase gate is the existing pending state, covered by S-4.3

## Rule-by-entry-point matrix

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

## Narrowed mappings

R-5: Every page and tab looks right; maps to AC-1, AC-2, AC-6, AC-7; left out: this plan's part is page structure and primitives; new layouts and token values are Claude Design's (B1 to B5)
R-6: Every control on every page functions; maps to AC-3, AC-4, AC-8; left out: this plan's part is the header's controls; the workspace switcher outside `/` is NB-3
R-14: Every page is usable at narrow widths; maps to AC-3, AC-5; left out: an interim wrap and contained overflow only; the real mobile layout (B1) is left out
R-15: Every page renders in the dark theme; maps to AC-6, AC-7; left out: login follows the stored theme and chip tints follow tokens; the dark accent override and faint-ink contrast (B5) are left out

## User tasks

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

## Open questions

| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | Each page's description line carries basis wording; keep it as a caption or drop it with the title card? | AC-1 | Keep it as a caption | default approved by the PM, [PM-ASSUMED], operator may override |
| Q-2 | `/api/trigger` is disabled by design (dashboards must not spawn the engine). Keep Force run now with an honest message, or remove the button? | AC-8 | Keep it reachable, as the brief lists it, with the route's message | default approved by the PM, [PM-ASSUMED], operator may override |

Sensitive: money (AC-4, AC-9). qa writes hidden scenarios for the named criteria after approval; they are not shown to the builders.
