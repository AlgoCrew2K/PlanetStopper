# Feature: Page explainers and denser layouts
Status: ready
Story: none: the request is quoted in Trigger and saved verbatim in feature-plans/page-explainers-and-density.source.md
Type: feat
Suite: none
Repos: PlanetStopper
Base: feat/security-and-controls at 1988ce5f (the security-and-controls head, which ships first)
Ref: PlanetStopper@1988ce5f15c85de8d3f4bba8ed815ad7b1c01b76
Sensitive: none: copy and layout only; no route, data, order path or control behavior changes, and AC-5 pins the protected controls and the zero-order guarantee unchanged
Depends on: feature-plans/security-and-controls.md (it removes the per-card Cash Now control and adds the tenure refusal line that these pages carry at the ref)

## Trigger
The operator wrote, after the restyle (the "unslop" density and polish pass, merged at 758f7f95 and included in the ref): "Continue. I like the restyle but I feel like there's still a ton of empty space and whatnot. I also think you need more descriptions in these pages, e.g. "Symphony Tenure" - what the fuck is it and why do I care?" Saved verbatim in feature-plans/page-explainers-and-density.source.md. The PM's intake rulings are quoted in Requirements as Given.

Evidence used: a pre-spec audit by the ux-expert at 1988ce5f, from 24 screenshots of every page at 1440 px and 375 px (light and dark) taken on a production database copy of 2026-10-31 with the market closed. It holds 18 empty-space findings and 274 on-screen items, of which 203 need an explanation: 27 sections with no purpose line, 99 undefined terms and 81 existing explanations in jargon. The audit and its screenshots are untracked review files; the parts this plan relies on are summarised in the rows that cite them. Item keys of the form D01, F01, Y01, S01, M01, A01, O01, C01, R01, W01, L01, T01, B01 and X01 are that audit's item ids. The space findings are A1 to A15 (1440) and P1 to P3 (375):

- A1: Retirement panel, the "Correlated pairs" and "Symphony tenure review" subsections stacked full width; the tenure part uses 24 percent of its width over 110 px (about 185k px squared blank).
- A2: Frontrunner Builder, 5 cards in a 3-column grid leave a 451 by 343 px hole.
- A3: Performance risk metrics table, about 500 px of blank between the labels and the figures over 10 rows.
- A4: History exits table, a 540 px blank channel between Symphony and Reason over 8 rows.
- A5: Dashboard foot, the CVaR panel (28 percent of its width used over 100 px) and the Managed sleeves panel (a 1024 px gap) stacked full width.
- A6: Advisor observations, a 520 px blank band across the 5 producer rows.
- A7, A8: Chat and Correlations tabs end at 343 and 320 px of a 900 px viewport, one short card each.
- A9: Settings Master card, blank to the right of a 960 px ledger and between text and controls (258 by 100 px at most).
- A10: Market Prism rationale capped at about 870 px, 463 by 125 px blank beside it.
- A11: Dashboard dollar panel, two blank columns (250 by 80 px and 300 by 80 px).
- A12: Frontrunner "Run frontrunner build" card, a 35 px tall button row 90 percent blank.
- A13: Dashboard hero, a 333 by 115 px block blank below the right column.
- A14: Stop-justification table, a 170 px channel over 11 rows.
- A15: Asset Swaps and Logic Changes, one form card each; the Logic Changes button row is blank across 1172 by 35 px.
- P1: Advisor Overview at 375, 12 near-identical autotune rows over 1140 px. P2: Frontrunner at 375, five cards with the same title over 1900 px. P3: short advisor tabs at 375.
- At 375 the dashboard (4547 px), Performance (3064), History (1880), Settings (943) and Retirement (945) have no blank band of 100 px or more inside a card.

## Summary
Every page and Advisor tab tells the operator, in plain visible words under each heading, what each section is and why it matters, defines each jargon term where it first appears, and stops leaving large empty regions at 1440 px, without changing any figure, control or contract.
- A visible description under every section heading and at the top of every page and tab, from the exact copy in Copy table A (AC-1).
- A plain definition for each jargon term at its first appearance on each page, visible or in a closed "What the terms mean" disclosure, never in a tooltip alone, from Copy table B (AC-2).
- Jargon labels and status words reworded, with the same text from the server render and from the script that refreshes it, per Copy table C (AC-3).
- Every threshold, window or count the copy names rendered from its configured value, never a literal (AC-4).
- No figure, control, banner, route, JSON body or honesty text changes (AC-5).
- At 1440 px no panel or card holds an empty region of 240 by 80 px or more, the named short sections share a row, and figure rows use their width (AC-6).
- At 375 px each page grows only by the height of the added copy and never scrolls sideways, and the Advisor tab strip shows that more tabs exist (AC-7).
- Autotune status text shared by every row is stated once, and frontrunner cards for one symphony carry their proposal number in the title (AC-8).

## Requirements as Given
[G-1] Continue. page-explainers-and-density.source.md:1
[G-2] I like the restyle but I feel like there's still a ton of empty space and whatnot. page-explainers-and-density.source.md:1
[G-3] I also think you need more descriptions in these pages, e.g. "Symphony Tenure" - what the fuck is it and why do I care? page-explainers-and-density.source.md:1
G-1 to G-3 are the operator's source, verbatim. G-4 to G-26 are PM rulings from the PM's briefs to the spec writer, not operator quotes; each is recorded as the PM gave it.
[G-4] (PM intake answer 1, what this makes true; [PM-ASSUMED]) "every page tells the operator in plain words what each section is and why they should care (what decision it informs, or what to do when it looks bad). Pages stop wasting space at 1440 px."
[G-5] (PM intake answer 2, surfaces; [PM-ASSUMED]) "the Dashboard (/), Performance, History and Settings pages, the per-symphony settings modal, and AI Advisor with all 8 tabs. Login is out."
[G-6] (PM intake answer 3, explanations; [PM-ASSUMED]) "Every section gets a VISIBLE description under its heading, one to two plain sentences: what it is, and why it matters to the operator. It must be visible without hover, because hover is invisible on a phone."
[G-7] (PM intake answer 3, explanations; [PM-ASSUMED]) "Each jargon term gets a plain definition at its first appearance on a page. A term definition may sit in a disclosure, a title attribute or the visible line; you choose per term and state the rule. The terms are the ones pe-ux lists, for example VWAP bleed, α, Max DD, CVaR, guard effect, If-held, Bot · frozen, stress window, uncertainty gate, tenure, CRRA-EU, PBO, FDR, OOS and Calmar."
[G-8] (PM intake answer 3, explanations; [PM-ASSUMED]) "The plan carries the EXACT copy for every description and definition, in a table with one row per item, so tests can assert it and qa can check it."
[G-9] (PM intake answer 3, explanations; [PM-ASSUMED]) "Every sentence must be true of the code at 1988ce5f. Cite the file:line of the behavior it describes, for example advisors/tenure_review.py for what the tenure review does and when a symphony qualifies."
[G-10] (PM intake answer 3, explanations; [PM-ASSUMED]) "Copy that names a configurable value (thresholds, windows, day counts) must render the configured value from template context, never a literal copy. Find whether each value is already in context; where it is not, the plan states the requirement and leaves the HOW to Gate 2."
[G-11] (PM intake answer 3, explanations; [PM-ASSUMED]) "Tone: plain, direct, second person allowed, no marketing words, no exclamation marks, no emoji."
[G-12] (PM intake answer 4, density; [PM-ASSUMED]) "Density. Use pe-ux's audit file; it will message you its path. WAIT for that message before you finalise the density criteria. You may draft the explanations meanwhile."
[G-13] (PM intake answer 4, density; [PM-ASSUMED]) "At 1440, no card's content may leave large empty regions. Short sections sit side by side instead of stacked full-width. Stat rows use their width."
[G-14] (PM intake answer 4, density; [PM-ASSUMED]) "At 375, phone page heights grow only by what the added copy needs."
[G-15] (PM intake answer 4, density; [PM-ASSUMED]) "State measurable criteria (for example, a content-width share, or the absence of empty bands of N px) that a browser test can check."
[G-16] (PM intake answer 5, data and contracts; [PM-ASSUMED]) "none. The change is render-only (templates, CSS, static JS). There is no route, JSON shape, DB, scheduler or engine change, and alpha_bot_execution.py and math_engine.py have zero diff."
[G-17] (PM intake answer 6, repos; [PM-ASSUMED]) "this one only."
[G-18] (PM intake answer 7, sensitive; [PM-ASSUMED]) "no. Copy and layout only. Still, all of these are unchanged in behavior and visibility: Force run now, Emergency Liquidate, the risk banners, Run recommender, Run tenure review, approve/reject, and the live toggles. The dashboard's zero-order guarantee holds."
[G-19] (PM ruling, Not Building; [PM-ASSUMED]) "no new data, metric, route or computation, and no figure changes value; no change to any control's behavior; no new visual language (keep the restyle's ledger rows, dot-and-word states, one type scale and studio tokens; no new colors, icons or illustrations); no help or docs page and no onboarding tour; the honesty text (bases, windows, as-of stamps, caveats, exclusion reasons) may be reworded for plainness but never removed; the login page and Discord are unchanged."
[G-20] (PM note at plan approval; [PM-ASSUMED]) "The copy table must also cover status words and figure labels that are themselves jargon, such as the retirement gate counts ("uncertainty 4, too thin 4, ..."), "Bot · frozen" and "Good call · saved +x%α". Rewording a label counts as a copy row: give the old text and the new text."
[G-21] (PM note at plan approval; [PM-ASSUMED]) "For every row whose text is built by static/*.js rather than by a template, say so in the row, because the test seam differs."
[G-22] (PM ruling on the audit's notes; [PM-ASSUMED]) "Naming splits: one name per concept across every page. You choose the name and list every old-to-new rename as a copy row. Examples: "If held" vs "Live (if held)", "exits" vs "Triggers", "VWAP bleed" vs "VWAP Bleed Cut"."
[G-23] (PM ruling on the audit's notes; [PM-ASSUMED]) "Tooltip-only explanations: any explanation that exists today only as a title or info-icon tooltip must also meet the visible rule, where it is a section's what-and-why."
[G-24] (PM ruling on the audit's notes; [PM-ASSUMED]) ""model unavailable" pasted into a sentence ("via model unavailable") is IN scope as a copy defect. The sentence must read correctly whether or not a model name resolves. Render-only."
[G-25] (PM ruling on the audit's notes; [PM-ASSUMED]) "At 375, the advisor tab strip must show that more tabs exist, and every tab must be reachable. State the WHAT only; no new visual language."
[G-26] (PM ruling on the audit's notes; [PM-ASSUMED]) "Phone repetition (P1 autotune rows, P2 identical Frontrunner titles) is IN scope. Repeated identical status text may be stated once. Cards that would carry identical titles must be told apart by data already on the card (for example the proposal number or the overlay summary). Render-only, and no data is dropped."
[G-27] (PM ruling on Q-1; [PM-ASSUMED]) The shared header's terms are in scope as definitions only, with no layout or visual change to the header: "Dry run" (no real orders are sent; global live execution is off), the market state word, the account selector, Force run now and Emergency Liquidate (what each does, and that liquidate sells the whole account). A title attribute or accessible description is enough, because these are terms and controls, not sections. The chrome look pins stay untouched.
[G-28] (PM ruling on NB-5; [PM-ASSUMED]) A control with no handler in any script ("Go to cash" and "View logs" in the detail panel) is not rendered. This is render-only, and no working control changes; it is the one stated exception to the nothing-else-changes criterion.
[G-29] (PM ruling on KU-6; [PM-ASSUMED]) The code keeps refusing credential keys by design, but the copy must be true: the Settings Credentials text says the dashboard does not save credentials and that they are set in the .env file on the host, and a save path that cannot succeed does not claim it will. The backend behavior is unchanged.

## Requirement Inventory
| R-n | Requirement item | Source (G ids) | Maps to (AC ids, or NB-n) | Scope (full, or narrowed: what is left out) |
|---|---|---|---|---|
| R-1 | Work continues from the restyle and keeps what the operator likes in it | G-1, G-2 | AC-5, AC-6 | full |
| R-2 | Large empty space is removed at 1440 px | G-2, G-4, G-13 | AC-6 | narrowed: regions smaller than 240 by 80 px stay allowed, so A12, A14 and the 35 px button row of A15 are not required to change (NB-10) |
| R-3 | Short sections sit side by side instead of stacked full width | G-13 | AC-6 | narrowed: required for the two pairs the audit found stacked (CVaR with Managed sleeves; the two Retirement subsections), not for every short section on every page |
| R-4 | Rows of stat figures use their width | G-13 | AC-6 | full |
| R-5 | Phone page heights grow only by what the added copy needs | G-14 | AC-7 | full |
| R-6 | The density criteria are measurable by a browser test | G-15 | AC-6, AC-7 | full |
| R-7 | Every page and section says, in plain words, what it is and why the operator should care | G-3, G-4, G-6 | AC-1 | narrowed: the shared header is not covered (NB-2, Q-1) |
| R-8 | Each section description is visible without hover | G-6 | AC-1 | full |
| R-9 | Each jargon term gets a plain definition at its first appearance on a page, by a stated placement rule | G-3, G-7 | AC-2, NB-2, NB-3, NB-4 | narrowed: the audit's terms less the header's wordmark tag, clock, countdown, bell and Tweaks button, model-written lens text and text a route returns in JSON |
| R-10 | The plan carries the exact copy, one row per item, for tests and qa | G-8, G-20 | AC-1, AC-2, AC-3 | full |
| R-11 | Every sentence is true of the code at 1988ce5f, with the file:line it describes | G-9 | AC-1, AC-2, AC-3 | full |
| R-12 | Copy that names a configured value renders it from its authority, never a literal | G-10 | AC-4 | full |
| R-13 | Tone: plain, direct, no marketing words, no exclamation marks, no emoji | G-11 | AC-1, AC-2, AC-3 | full |
| R-14 | Surfaces: Dashboard, Performance, History, Settings, the settings modal, all 8 Advisor tabs; login out | G-5 | AC-1, AC-2, AC-6, AC-7, NB-15 | full |
| R-15 | The change is render-only: no route, JSON shape, database, scheduler or engine change; engine files have zero diff | G-16 | AC-5 | narrowed: server-built display text for two labels and the template context may change (D-6, Q-5) |
| R-16 | Only this repository changes | G-17 | AC-5 | full |
| R-17 | The named controls are unchanged in behavior and visibility and the dashboard places no order | G-18, G-19 | AC-5 | full |
| R-18 | No new data, metric, route or computation; no figure changes value | G-19 | AC-5 | full |
| R-19 | No new visual language: the restyle's rows, dot-and-word states, type scale and tokens only | G-19 | AC-1, AC-6, NB-17 | full |
| R-20 | No help or docs page and no onboarding tour | G-19 | NB-1 | full |
| R-21 | Honesty text may be reworded for plainness but is never removed | G-19 | AC-5 | full |
| R-22 | The login page and Discord are unchanged | G-19 | NB-15 | full |
| R-23 | Status words and figure labels that are jargon are covered; a reworded label gives its old and new text | G-20 | AC-3 | full |
| R-24 | Every copy row whose text a script builds says so | G-21 | AC-1, AC-2, AC-3 | full |
| R-25 | The density criteria come from the ux-expert's audit | G-12 | AC-6 | full |
| R-26 | One name per concept on every page, each rename a copy row | G-22 | AC-3 | full |
| R-27 | A tooltip-only explanation of a section's what and why is also visible | G-23 | AC-1 | full |
| R-28 | A sentence that embeds the model name reads correctly whether or not a model resolves | G-24 | AC-3 | full |
| R-29 | At 375 the Advisor tab strip shows that more tabs exist and every tab is reachable | G-25 | AC-7 | full |
| R-30 | Repeated identical autotune status text is stated once, and same-title frontrunner cards are told apart by data on the card, with no data dropped | G-26 | AC-8 | full |
| R-31 | The header's mode pill, market state word, account selector, Force run now and Emergency Liquidate carry definitions, with no change to the header's layout or look | G-27 | AC-2 | full |
| R-32 | A control with no handler in any script is not rendered | G-28 | AC-5 | full |
| R-33 | The Settings Credentials copy says the dashboard does not save credentials and that they are set in .env on the host, and no copy claims a save that cannot succeed | G-29 | AC-3 | full |

## User Tasks
Workflow (AI Advisor Retirement tab): no workflow file: `.claude/workflows/` does not exist at the ref; the task is the operator's own example (G-3)
Workflow (dashboard `/`): no workflow file: `.claude/workflows/` does not exist at the ref
Workflow (Performance and History): no workflow file: `.claude/workflows/` does not exist at the ref
Workflow (Settings and the settings modal): no workflow file: `.claude/workflows/` does not exist at the ref
Workflow (AI Advisor Overview, Correlations, Asset Swaps, Logic Changes, Chat, Strategy Builder, Frontrunner Builder): no workflow file: `.claude/workflows/` does not exist at the ref

### T-1: Find out what the symphony tenure review is and why it matters
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Open `/ai-advisor` and choose the Retirement tab | none | The tab's description and the panel description say what retirement suggestions are and that nothing is retired for them | S-1.9 |
| 2 | Read under "Symphony tenure review" | none | The visible line says when a symphony qualifies (the configured trading days), what the review compares, the four outcomes, and that approving records a decision and changes nothing in Composer | S-1.9, S-4.1 |
| 3 | Open "What the terms mean" in that section | a click or Enter | The definitions of keep, watch, retire-candidate, tenure evidence items and Run tenure review show | S-2.9 |

### T-2: Read a symphony card on the dashboard
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Open `/` | none | The page line and the Active section line say what the page and section are | S-1.1 |
| 2 | Open "What the terms mean" in Active | a click | Each status word, Bot · frozen, the ring and Cum are defined | S-2.1 |
| 3 | Look at a triggered card | none | Its verdict reads "Exit beat holding by 1.2 pts" or "Exit trailed holding by 0.4 pts" with its basis, on first paint and after the next poll | S-3.1, S-3.2 |

### T-3: Scan a page at 1440 px
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Open each page and tab at 1440 by 900 | none | No panel or card shows an empty region of 240 by 80 px or more; CVaR and Managed sleeves share a row; the Retirement subsections share a row while both are short | S-6.1, S-6.2, S-6.3 |

## Earlier criteria on the same surface
Searched every `feature-plans/*.md` (the `.completed.md` and `.obsolete.md` families and the suite folders `ux-overhaul/`, `refresh-timing/`, `decision-science.completed/` and `approach/`), `DECISIONS.md`, `docs/generated/`, `docs/audit/` and the density and look pins under `tests/ui/` for the touched templates, the section headings, `title=`, `density`, `scrollHeight`, `Good call`, `Triggers`, `VWAP Bleed Cut`, `Planet Stopper-exited`, `blocked_`, `reasons over your live tree` and the restyle commits (758f7f95 and its merges). The restyle has no plan file; its criteria live in the `tests/ui/` pins listed below.

| Earlier criterion (plan and AC) | Surface | Disposition (kept, changed or contradicted) | Why |
|---|---|---|---|
| feature-plans/ux-overhaul/01-data-truth.md AC-7 (banners, notes and Strategy Builder cards say true things once; notes name "Run Claude advisor") | Advisor banners and notes | kept | Banners are byte-identical (S-5.3); the Overview line O03 names Run Claude advisor |
| feature-plans/ux-overhaul/01-data-truth.md AC-1 (autotune run cards label their numbers truly) | Overview autotune rows | kept | Labels are unchanged; definitions are added beside them (S-2.6) |
| feature-plans/ux-overhaul/01-data-truth.md AC-2 (History's dollar total says saved or lost) | History hero | kept | The figure and its word are unchanged (S-5.1) |
| feature-plans/ux-overhaul/02-advisor-overview.md AC-6 (the Overview's first screen shows actionable items first) | Overview | kept | Order is unchanged; each section gains one or two lines (S-5.5) |
| feature-plans/ux-overhaul/02-advisor-overview.md AC-9 (Chat's example prompts name only flows the page can start) | Chat tab | kept | The reworded Chat body names the real buttons (Copy table C, T02) |
| feature-plans/ux-overhaul/03-correlations-realized.md AC-8 (no run, a failed run and too few symphonies each have an honest state) | Correlations | kept | State copy is unchanged; a tab description is added above it |
| feature-plans/ux-overhaul/04-app-shell.md AC-2 (panels, cards, labels, stat rows and numbers use the dashboard's primitives) | every page | kept | Descriptions and disclosures reuse existing primitives (Design-System Mapping, S-6.9) |
| feature-plans/ux-overhaul/04-app-shell.md AC-5 (wide content fits narrow screens) | every page at 375 | kept | AC-7 requires no sideways scroll (S-7.2) |
| feature-plans/ux-overhaul/05-dashboard-charts.md AC-2 (first-paint verdicts and dials match what a refresh shows) | Dashboard cards | changed | The verdict words change (Copy table C); first paint and poll still print the same text (S-3.1, S-3.2) |
| feature-plans/ux-overhaul/06-review-fixes.md AC-6 (words on screen: raw enums read as words through one mapping) | Advisor | kept | No raw enum is introduced; new words come from the copy tables |
| feature-plans/ux-overhaul/06-review-fixes.md AC-7 (advisor layout: two labelled run buttons in their own sections; autotune block takes the width when suggestions are empty) | Retirement, Overview | kept | The two subsections stay separate sections with their buttons; they share a row while short (S-6.3) |
| feature-plans/retirement-visibility.md AC-6 (the panel shows every run, with the blocked count per gate and the stress-gate minimum from the summary) | Retirement last-run tiles | changed | The four gate words and the stress-window sentence are reworded (Copy table C, R09); the counts, the minimum and the summary keys are unchanged (S-3.7) |
| feature-plans/symphony-tenure-review.md AC-8 (one verdict per eligible symphony and the decision lifecycle) | Retirement tenure entries | kept | Verdicts and the lifecycle are unchanged; their words are defined (Copy table B, R27) |
| feature-plans/security-and-controls.md AC-6 (the per-symphony Cash Now control is removed) | Dashboard cards | kept | Nothing re-adds it; the freed card width is not counted as waste |
| feature-plans/security-and-controls.md AC-7 (Run tenure review is refused while the market is open, with its message) | Retirement | kept | The refusal line is unchanged; the R23 definition states the refusal |
| feature-plans/mdd-window-truth.md AC-2 (the Composer lifetime scalar is its own clearly labelled figure, "Lifetime Max Drawdown · since inception") | Dashboard hero | changed | The label becomes "Lifetime Max DD · since inception" (NM1); it stays its own figure outside the comparison rows |
| feature-plans/refresh-timing/01-server-state-truth.md S-9.3 (a card's verdict switches between "Early exit · gave up" and "Good call · saved" on the poll) | Dashboard cards | changed | The words become "Exit trailed holding by", "Exit beat holding by" and "Exit matched holding" (D30a to D30c); the switch on the poll is unchanged (S-3.2) |
| tests/ui/test_dashboard_density_browser.py (each card row at most 112 px and the dollar-saved panel at most 160 px at 1440; no text under 11 px at 375) | Dashboard | changed | The card bound and the 11 px floor are kept; the dollar panel bound becomes 160 px plus the height of its added description and disclosure summary lines (S-6.8, Q-7) |
| tests/ui/test_history_performance_density_browser.py (History at most five screens at 375; each reason row at most 40 px at 1440; no upper case, no weight above 500) | History, Performance | kept | S-7.3 and S-6.9 keep them |
| tests/ui/test_advisor_density_contract.py (tenure evidence items are closed disclosures; Market Prism is compact) | Advisor | kept | Not changed; the new disclosures are closed by default too |
| tests/ui/test_advisor_density_contract.py `test_a_row_carries_the_six_columns_the_header_names`, `test_a_run_that_was_never_frozen_evaluated_says_so_in_visible_dim_ink` and `test_a_phone_row_names_its_score_t_stat_and_streak_on_screen`; tests/ui/test_advisor_autotune_browser.py (the seeded three-row checks of the frozen-eval text, the phone score label and the tail width); the restyle judge's item B1 (frozen-eval and the score label on every row) | Overview autotune rows | changed | Under AC-8 a column whose text is identical in every row is stated once in the "Same for every run" line instead of in each row; a column that differs stays in every row, and every text stays visible, in dim ink where it was (PM ruling G-26) |
| tests/ui/test_chrome_look_contract.py and tests/ui/test_dashboard_density_contract.py (two weights, sentence case, a status is a dot and a word, hairline cards) | every page | kept | S-6.9 |
| tests/ui/test_retirement_copy_names_the_real_control_and_tenure_chip.py (copy names Run recommender; nothing says Run now) | Retirement | kept | The new copy names Run recommender and Run tenure review only |

## Acceptance Criteria (Building)

### AC-1: Every page, tab and section says what it is and why it matters
Requirement items: R-7, R-8, R-10, R-11, R-13, R-14, R-19, R-24, R-27
Rule: When a page, Advisor tab, the dashboard detail panel or the settings modal renders, each row of Copy table A for that host shall show its new text exactly, as visible text directly under the section's heading (or at the top of a page, tab or section that has no heading), in the host's existing note style, at 1440 px and at 375 px, and a section that is not rendered shall not render its description.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-1.1 | B | the seeded dashboard with active, standby and triggered symphonies, a CVaR diagnostic and no sleeves | `/` renders at 1440 | every Copy table A row for host `/` shows its exact text, visible (a non-zero box, not hidden, not inside a closed disclosure, not only in an attribute) |
| S-1.2 | B | a card on `/` | the card is clicked and the detail panel fills | every Copy table A row for host detail panel shows its exact text, visible |
| S-1.3 | B | seeded Performance data | `/performance` renders | every Copy table A row for host Performance shows its exact text, visible |
| S-1.4 | B | seeded History exits | `/history` renders | every Copy table A row for host History shows its exact text, visible |
| S-1.5 | B | `/settings` | each of the five side-nav sections is chosen in turn | every Copy table A row for host Settings shows its exact text in its section, visible |
| S-1.6 | B | a symphony on `/` | its settings modal opens | every Copy table A row for host settings modal shows its exact text, visible (built by static/settings-modal.js) |
| S-1.7 | B | `/ai-advisor` | the Overview tab shows | the A01 row and every Overview row show their exact text, visible |
| S-1.8 | B | a stored retirement run with pairs | the Correlations tab is chosen | the C01 row shows its exact text, visible |
| S-1.9 | B | a stored retirement run and a stored tenure review | the Retirement tab is chosen | R01, R02 and R22 show their exact text, visible |
| S-1.10 | B | an API key configured | the Asset Swaps tab is chosen | W01 shows its exact text, visible |
| S-1.11 | B | an API key configured | the Logic Changes tab is chosen | L01 shows its exact text, visible |
| S-1.12 | B | none | the Chat tab is chosen | the T02 row shows its exact text, visible |
| S-1.13 | B | stored survivor and backtest-failed candidates | the Strategy Builder tab is chosen | B00, B07 and B10 show their exact text, visible |
| S-1.14 | B | pending frontrunner proposals | the Frontrunner Builder tab is chosen | X01 and X05 show their exact text, visible |
| S-1.15 | N3 | no symphonies, no CVaR diagnostic, no retirement run, no tenure review, no Strategy Builder observations, no frontrunner proposals, no autotune runs | each host renders | every section that renders shows its description; the CVaR panel stays hidden with its description inside it; B07, B10 and X05 are absent with their sections |
| S-1.16 | N3 | no API key configured | the Asset Swaps, Logic Changes and Strategy Builder tabs are chosen | W01, L01 and B00 still show (they are tab lines); the try panels are replaced by the unavailable state as today and carry no orphaned description |
| S-1.17 | N1 | the 375 by 812 viewport | each host renders | every description shows in full, not cut off or ellipsed, and the page does not scroll sideways |
| S-1.18 | N1 | every new text in Copy tables A, B and C | the texts are scanned | none contains "!", a character in the emoji ranges, U+2014 or U+2013, or any of the words "powerful", "seamless", "effortless", "revolutionary", "cutting-edge", "unlock", "supercharge", "amazing", "simply the best"; each description row is one or two sentences, apart from honesty text the row keeps |
| S-1.19 | N7 | any Copy table A row | the page renders | the text is not carried only by a `title` or `aria-label` attribute |
| S-1.20 | N6 | `/api/state`, `/api/performance`, `/api/history/N` or `/api/retirement-recommendations` answers 500 | the page's script runs its refresh | every description already shown stays, unchanged |
| S-1.21 | N5 | the dashboard poll runs three times, or a tab is chosen, left and chosen again | the page is read | each description shows once per section, never duplicated |
| S-1.22 | N8 | an unsaved edit on `/settings`, or a pending live-mode toggle in the modal | the operator switches section or reads the modal | descriptions are unchanged and the pending edit is kept |
| S-1.23 | N7 | each Advisor tab whose tab dot carries a `title` at the ref (`templates/ai_advisor.html:1135-1197`: "Summary & autotune runs", "Low risk", "Low risk when gated", "Higher overfitting risk", "Explain-only", "Highest overfitting risk", "Real Composer write, approval required") | the tab is chosen | its visible tab description or its risk banner states the same what and why, by the mapping D-8; the `title` stays |

Negative coverage: N1 S-1.17, S-1.18 · N2 n/a: a description refuses nothing and no route is added · N3 S-1.15, S-1.16 · N4 n/a: every host sits behind the unchanged global auth hook, and a description carries no data (pinned by S-5.8) · N5 S-1.21 · N6 S-1.20 · N7 S-1.19, S-1.23 · N8 S-1.22
Questions: Q-1, Q-8

### AC-2: Each jargon term is defined where it first appears on a page
Requirement items: R-9, R-10, R-11, R-13, R-14, R-24, R-31
Rule: When a host renders, each row of Copy table B for that host shall be readable at the term's first appearance on that host by the row's placement: V (inside the visible description it names) or D (a line "Term: definition", in table order, inside one closed disclosure with the summary "What the terms mean" placed directly under the description of the section it names); no term shall be defined by a `title` attribute alone, except that the header rows (placement A) are carried by the control's `title` or its accessible description, with no visible change to the header (D-12); and each Advisor tab, the dashboard detail panel and the settings modal count as hosts of their own (D-4).

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | the seeded dashboard | `/` renders and each "What the terms mean" is opened | every Copy table B row for host `/` shows its exact definition at its placement |
| S-2.2 | B | an open detail panel | each disclosure in it is opened | every row for host detail panel shows exactly |
| S-2.3 | B | `/performance` | each disclosure is opened | every row for host Performance shows exactly |
| S-2.4 | B | `/history` | each disclosure is opened | every row for host History shows exactly |
| S-2.5 | B | `/settings` and the modal | each disclosure is opened | every row for hosts Settings and settings modal shows exactly (the S15, S16, M01 and M08 rows are built by static/settings.js and static/settings-modal.js) |
| S-2.6 | B | the Overview tab, with autotune runs and a Market Prism row | each disclosure is opened | every row for host Overview, and the nav rows A03 and A04, show exactly |
| S-2.7 | B | the Correlations tab with a matrix | its disclosure is opened | C05 and C07 show exactly |
| S-2.8 | B | the Asset Swaps and Logic Changes tabs | each disclosure is opened | every row for those hosts shows exactly |
| S-2.9 | B | the Retirement tab with a run and a review | each disclosure is opened | every row for host Retirement shows exactly |
| S-2.10 | B | the Strategy Builder and Frontrunner Builder tabs with stored results | each disclosure is opened | every row for those hosts shows exactly |
| S-2.11 | N1 | a term that appears in several sections of one host | the host renders | it is defined once, in the section the row names, and nowhere else on that host |
| S-2.12 | N1 | a disclosure | it is reached by keyboard | it has the accessible name "What the terms mean", opens and closes with Enter and Space, and is closed on load |
| S-2.13 | N7 | a term defined by a `title` at the ref (D10, D16, O09, R12, S16, A03) | the host renders | the `title` stays as it was and the same meaning is also in a visible or disclosure line |
| S-2.14 | N5 | a disclosure opened on `/`, Overview or Performance | the page's poll or panel refresh runs | the disclosure stays open and its lines unchanged |
| S-2.15 | N3 | the section a row names does not render (CVaR hidden, no frontrunner proposals) | the host renders | its disclosure is absent with it, and the term is not shown elsewhere on that host |
| S-2.16 | N8 | a pending live-mode toggle in the modal | the M01 disclosure is opened and closed | the pending toggle and the confirm dialog behave as at the ref |
| S-2.17 | N6 | the modal's settings fetch fails | the error state shows | no definitions show and the error text is unchanged |
| S-2.19 | B | any page at 1440 and 375 | the header renders, and the engine state moves through each state static/chrome.js can write | the mode pill, the engine status element, the account selector, Force run now and Emergency Liquidate each carry their Copy table B header definition, exactly, as a `title` or an accessible description; the status element's definition is the one for the state it shows |
| S-2.20 | N7 | the header | it renders | its layout, sizes, colors and visible text are as at the ref; `tests/ui/test_chrome_look_contract.py` and `tests/ui/test_chrome_look_browser.py` pass unchanged; the existing `title` text of the engine states (the degraded reasons included) is kept |
| S-2.18 | N6 | a host's script fails to load | the server render is read | every template-built disclosure is present, closed, with its definitions exactly as Copy table B gives them |

Negative coverage: N1 S-2.11, S-2.12 · N2 n/a: a definition refuses nothing · N3 S-2.15 · N4 n/a: the hosts sit behind the unchanged auth hook (S-5.8) · N5 S-2.14 · N6 S-2.17, S-2.18 · N7 S-2.13, S-2.20 · N8 S-2.16
Questions: Q-8

### AC-3: Jargon labels are reworded the same way by the server and the scripts
Requirement items: R-10, R-11, R-13, R-23, R-24, R-26, R-28, R-33
Rule: When a label in Copy table C renders, the host shall show its new text in place of its old text, identically whether the server rendered it or a script wrote it on a poll or after a run, and the old text shall no longer appear on that host.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | a triggered symphony whose exit return is 1.24 points above its held return | `/` first paints | its verdict reads "Exit beat holding by 1.2 pts" followed by its basis label |
| S-3.2 | B | the same symphony | the poll rewrites the card (static/index.js) | the verdict text is identical to the first paint |
| S-3.3 | B | a triggered symphony whose exit return is 0.42 points below its held return | first paint and poll | both read "Exit trailed holding by 0.4 pts" |
| S-3.4 | N1 | an exit gap that prints as 0.0 at one decimal (exactly 0, 0.04 or -0.04) | first paint and poll | both read "Exit matched holding" |
| S-3.5 | N3 | a triggered symphony with no guard figure (absent, null, not finite) | first paint and poll | both show a dash, as at the ref |
| S-3.6 | B | a stored ok retirement run with blocked counts | the Retirement tab renders | the Blocked tile detail uses the four new gate words and the new stress-window sentence with the stored minimum |
| S-3.7 | N7 | any reworded label | the host renders | every `data-testid`, `data-*` value, CSS class, JSON key (including `blocked_uncertainty`, `_verdict_text`, `exit_reason`) and route body is unchanged |
| S-3.8 | B | History with exits of all four reasons | `/history` renders and refreshes | the reason names and the reason definitions read the new text in both the server placeholders and the script-built rows |
| S-3.9 | B | Performance with data | `/performance` renders and draws its chart (static/performance.js) | the caption, the legend, the chart dataset labels and the table headers read the new series names, each still marking the bot series as the shadow record |
| S-3.10 | N5 | the dashboard poll and the Overview 15 s refresh run five times | the hosts are read | no reworded label ever shows its old text |
| S-3.11 | N6 | a host's script fails to load | the server render is read | every template-built row of Copy table C shows its new text |
| S-3.12 | B | every other row of Copy table C | its host renders | the new text shows and the old text is absent from that host |
| S-3.16 | B | the Credentials section of `/settings` | it renders and a value is typed into a credential field (static/settings.js) | the S11, S12r and S12s rows show; no text on the page says a credential will be saved or written to .env |
| S-3.17 | N7 | a save that carries a typed credential | Save is chosen | `POST /api/settings` still answers 400 and writes nothing, as at the ref; the page's error text is unchanged |
| S-3.13 | B | the suggestion and synthesis models resolve to display names | `/ai-advisor` renders | each model sentence of Copy table C (rows MU1 to MU5) reads its resolved form with the name in place |
| S-3.14 | N3 | the models do not resolve (`model_config.py:66-73` gives "model unavailable") | `/ai-advisor` renders | each model sentence reads its unresolved form, and the words "model unavailable" appear nowhere on the page |
| S-3.15 | N7 | every concept that had two names at the ref (the Bot series, If held, exits, each exit reason, max drawdown, exit alpha, Performance's share of up days) | every host renders | each shows only the name Copy table C gives it, on every host |

Negative coverage: N1 S-3.4 · N2 n/a: no label refuses anything · N3 S-3.5, S-3.14 · N4 n/a: unchanged auth hook (S-5.8) · N5 S-3.10 · N6 S-3.11 · N7 S-3.7, S-3.15, S-3.17 · N8 n/a: a typed credential is an unsaved edit, and S-3.16 shows its status text is the new one
Questions: Q-5, Q-6

### AC-4: Copy that names a configured value shows the configured value
Requirement items: R-12
Rule: When a row of Copy table A or B holds a slot from the Values table, the host shall print the value that slot's authority holds when the page renders, in the slot's format, and never a number typed into a template or script; if the authority's value cannot be read, the host shall replace the whole phrase that holds the slot with that row's fallback phrase from the Values table.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | B | `TENURE_REVIEW_MIN_TRADING_DAYS=90` in the env file, the process environment holding 120, and no stored review | the Retirement tab renders | R22 reads "invested for 90 trading days" (the env file wins, as the review reads it) |
| S-4.2 | B | a stored review whose `threshold_in_effect` is 126, the environment set to 90 | the tab renders | R22 reads "126 trading days" (the stored review's value, Q-2) |
| S-4.3 | N1 | `TENURE_REVIEW_MIN_TRADING_DAYS` absent, empty, "abc", "0", "-5" or "12.5", and no stored review | the tab renders | R22 prints the value `advisors/tenure_review.py` `parse_threshold` resolves for that input (`advisors/tenure_review.py:430`), as the next review would use |
| S-4.4 | B | `CORRELATION_SCREEN_THRESHOLD` patched to 0.7 and no stored run | the tab renders | R02 reads "a correlation of 0.7 or more" |
| S-4.11 | B | a stored ok run whose `screen_threshold` is 0.65, the constant patched to 0.7 | the tab renders | R02 reads "0.65", the same value as the Pairs tile |
| S-4.12 | B | `SHADOW_HISTORY_RETENTION_DAYS=90` in the env file | `/` renders and its hero disclosure opens | D07 reads "kept for the last 90 days" |
| S-4.5 | B | `STRESS_WINDOW_FRACTION` patched to 0.1 | the tab's disclosure opens | R19b reads "the worst 10 percent of days" |
| S-4.6 | B | `BACKTEST_TRAIL_WATCH_PP` patched to 7.5 | the tab's disclosure opens | R27 watch reads "by more than 7.5 percentage points" |
| S-4.7 | B | `SIM_EXIT_FRICTION_PCT` patched to 0.25 | `/` and `/performance` disclosures open | D25 and F24 read "0.25 percentage points" |
| S-4.8 | N7 | `_PERFORMANCE_MIN_HISTORY_DAYS` | `/performance` renders the insufficient banner | it prints `min_history_days` from context as at the ref, now with the F10 wording |
| S-4.9 | N3 | each slot's authority raises or yields None, one at a time | the host renders | each copy row in the Values table prints its fallback phrase in place of the phrase holding the slot, the sentence around it unchanged, no number shows, and the rest of the page renders |
| S-4.10 | N7 | the templates and scripts after the change | they are searched for the slot values next to the new copy | no slot value is typed as a literal; each comes from context or from a value the server writes into the page |

Negative coverage: N1 S-4.3 · N2 n/a: no input is judged · N3 S-4.9 · N4 n/a: the values are non-secret module constants and two non-secret environment keys; no credential reaches a page · N5 n/a: each render reads the authority once and a refresh changes no copy · N6 S-4.9 · N7 S-4.8, S-4.10 · N8 n/a: no slot sits on an editable form
Questions: Q-2

### AC-5: No figure, control, banner, contract or honesty text changes
Requirement items: R-1, R-15, R-16, R-17, R-18, R-21, R-32
Rule: If this change is applied, then every figure, control, banner, route, JSON body, database read and write and honesty text shall stay as it was at 1988ce5f, apart from the rows of the three copy tables and the layout of AC-6, with one stated exception: a control that no script handles is no longer rendered, which at the ref is the detail panel's View logs, Go to cash and Edit variables buttons (`templates/index.html:1991-1992`, `:2132`).

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | the same seeded state before and after | every host renders | the text of every figure element (each element whose `data-testid` or `id` carries a value) is identical |
| S-5.2 | N7 | each host at 1440 and 375 | it renders | Force run now, Emergency Liquidate with its modal and the typed LIQUIDATE, Run recommender, Run tenure review, every approve and reject button, the live switches on Settings and in the modal, Arm Paper, Disarm, Delete, Sync & Flush, Run Claude advisor, Evaluate swap, Evaluate logic change, Run analysis and Run build are present, visible, enabled or disabled as at the ref, and send the same requests |
| S-5.3 | N7 | the risk banners: the crisis caveat, the advise-only banner, the logic-change FDR banner, the Strategy Builder and Frontrunner risk banners, the Settings live warning, credential warning and maintenance note, the two CVaR notes, the fleet banner and its modal | each renders | each is byte-identical to the ref |
| S-5.4 | N7 | the honesty text: every basis label, window label, as-of stamp, caveat and exclusion reason | each host renders | each is present, with its words changed only where Copy table C says |
| S-5.5 | N7 | each host | it renders | its sections appear in the same order as at the ref |
| S-5.6 | N7 | the same seeded state | `GET /api/state`, `/api/performance`, `/api/history/N`, `/api/retirement-recommendations`, `/api/guard-alpha-summary`, `/api/autotune-runs` and `/api/settings` are requested | each body is byte-identical to the ref |
| S-5.7 | N7 | the branch against its base | the diff is read | `alpha_bot_execution.py`, `math_engine.py`, `database.py`, `migrations/`, every route signature and every scheduler slot are unchanged; `tests/app/test_dashboard_no_order_path.py` and every `tests/security/*no_trade_boundary*.py` pass unchanged |
| S-5.8 | N4 | no session | each host is requested | the HTML request redirects to `/login` and an XHR or `/api/` request gets 401, as at the ref |
| S-5.9 | N7 | the login page, the logout confirm page and a Discord post built by `reporting.py` | each is produced | each is byte-identical to the ref |
| S-5.10 | N3 | a figure that is absent, null or not finite | its host renders | it shows the same dash as at the ref |
| S-5.11 | N2 | Run recommender or Run tenure review chosen while the market is open | the route answers 409 | the refusal text is the same as at the ref |
| S-5.12 | N8 | a pending live toggle in the modal | Save is chosen | the "Confirm live trading" dialog shows as at the ref and nothing is saved before it |
| S-5.14 | B | a card on `/` | the detail panel opens at 1440 and 375 | no View logs, Go to cash or Edit variables button is in the panel; the close button and the overlay toggles are present and work |
| S-5.15 | N7 | every page | it renders | every other control is rendered as at the ref, and a search of `static/` and `templates/` finds no other rendered button without a handler, an `onclick` or a form |
| S-5.13 | N7 | `alpha_bot_execution.py`, `math_engine.py` and `database.py` | a test reads each file with line endings normalized to LF and hashes it | each SHA-256 equals the value recorded from the file at 1988ce5f |

Negative coverage: N1 n/a: this criterion judges no input · N2 S-5.11 · N3 S-5.10 · N4 S-5.8 · N5 n/a: no action is added; repeated actions keep their existing guards, pinned by their existing tests · N6 n/a: no dependency is added; existing failure states are pinned by S-5.4 · N7 S-5.2, S-5.3, S-5.4, S-5.5, S-5.6, S-5.7, S-5.9, S-5.13, S-5.15 · N8 S-5.12
Questions: Q-5

### AC-6: At 1440 px no panel holds a large empty region
Requirement items: R-1, R-2, R-3, R-4, R-6, R-14, R-19, R-25
Rule: When a host renders at 1440 by 900 px in any seeded state the Definition of Done names, no panel or card (`.hero-section`, `.dash-panel`, `.hp-panel`, `.adv-panel`, `.adv-card`, `.sym-card`, `.settings-section`, the detail panel and the modal surface) shall contain, inside its content box, an empty rectangle 240 px wide and 80 px tall or larger that overlaps no content box, where content boxes are the rendered glyph boxes of text (`Range.getClientRects` over its text nodes, not the element box), form controls, canvases, SVGs, images and filled bars, and borders and rules are not content; the CVaR panel and the Managed sleeves panel shall share one row when both render; the "Correlated pairs" and "Symphony tenure review" subsections shall share one row while each is no taller than 320 px in a single column; and every row of figures (`.mini-stats`, `.hp-figs`, `.adv-figs`) shall spread its figures across at least 90 percent of its container's content width.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | the seeded dashboard of S-1.1 | `/` renders at 1440 | no panel holds an empty rectangle of 240 by 80 px or larger (the A5, A11 and A13 regions included) |
| S-6.2 | B | the same, with a CVaR diagnostic and with no sleeves | `/` renders | the CVaR and Managed sleeves panels share one row (their tops within 8 px, their boxes side by side) |
| S-6.3 | B | a retirement run whose last-run block holds exclusions and no cards, and no tenure review | the Retirement tab renders | the two subsections share one row and no empty rectangle of 240 by 80 px is in the panel (A1) |
| S-6.4 | N1 | a run with three recommendation cards and a review with 11 entries | the Retirement tab renders | each subsection is taller than 320 px, they stack, and neither holds an empty rectangle of 240 by 80 px |
| S-6.5 | B | five pending frontrunner proposals | the Frontrunner tab renders | the card grid leaves no empty cell of 240 by 80 px (A2) |
| S-6.6 | B | Performance with 10 metric rows and 11 precondition rows (the row rules do not count as content); History with 8 exits; Overview with 5 producer lines and a Market Prism row; the Chat and Correlations tabs in their default states; Settings Master | each renders | no panel holds an empty rectangle of 240 by 80 px (A3, A4, A6, A7, A8, A9, A10) |
| S-6.7 | B | the dashboard hero, History hero, Performance headline and Retirement tiles | each renders | each figure row spans at least 90 percent of its container's content width |
| S-6.8 | N7 | the seeded dashboard | `/` renders at 1440 | each symphony card row is at most 112 px tall and the dollar-saved panel is at most 160 px plus the height of its D21 description and its disclosure summary line |
| S-6.9 | N7 | every host | it renders | only existing tokens, the two font weights 400 and 500, sentence case, dot-and-word states and hairline cards are used; no new color, icon, illustration or type size appears; History reason rows stay at most 40 px |
| S-6.10 | N1 | the viewport at 1240 by 900, the narrowest desktop layout | each host renders | S-6.1 to S-6.7 hold |
| S-6.11 | N3 | the empty states of S-1.15 | each host renders at 1440 | no panel holds an empty rectangle of 240 by 80 px (a lone Managed sleeves panel with "No sleeves configured yet." included) |
| S-6.12 | N5 | the dashboard poll runs and the window picker moves from 30d to 1y and back | `/` is measured again | S-6.1 and S-6.2 still hold |
| S-6.13 | N6 | `/api/exit-turnover` or `/api/guard-alpha-preconditions` answers 500 | `/performance` shows its empty or error state | no panel holds an empty rectangle of 240 by 80 px |
| S-6.14 | N7 | every host at 1440 | it renders | the document is no wider than the viewport |

Negative coverage: N1 S-6.4, S-6.10 · N2 n/a: layout refuses nothing · N3 S-6.11 · N4 n/a: unchanged auth hook (S-5.8) · N5 S-6.12 · N6 S-6.13 · N7 S-6.8, S-6.9, S-6.14 · N8 n/a: the layout does not depend on an edit state; the modal and the Settings form keep their ref behavior (S-1.22)
Questions: Q-4, Q-7

### AC-7: At 375 px a page grows only by the added copy
Requirement items: R-5, R-6, R-14, R-29
Rule: When a host renders at 375 by 812 px in a seeded state of the Definition of Done, its document height shall be no more than its height at 1988ce5f for the same state plus the summed heights of the elements this change adds (description lines, closed disclosure summaries and reworded labels' growth) plus 16 px, the document shall be no wider than the viewport, and no added text shall be smaller than 11 px; and the Advisor tab strip shall either show all eight tabs whole or show, at each edge beyond which a tab is out of view, a visible cue that more tabs exist, with every tab reachable by touch scrolling, by a tap and by keyboard.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | each seeded state of S-1.1 to S-1.14 | each host renders at 375 | its height is within the bound against the baseline recorded at 1988ce5f |
| S-7.2 | N7 | every host at 375 and 320 px | it renders | `scrollWidth` equals the viewport width |
| S-7.3 | N7 | the seeded History page | it renders at 375 | it is at most five screens (5 times 812 px) tall |
| S-7.4 | N1 | every added text | it renders at 375 | its computed font size is at least 11 px |
| S-7.5 | N3 | the empty states of S-1.15 | each host renders at 375 | the bound of S-7.1 holds against the empty-state baselines |
| S-7.6 | N5 | every disclosure opened and closed again | the height is measured | it returns to the closed height within 1 px |
| S-7.7 | B | `/ai-advisor` at 375 by 812 | the page loads | either every tab button lies wholly inside the viewport, or a cue is visible at each edge of the strip beyond which a tab lies out of view; no tab label is cut by the strip's edge without that cue |
| S-7.8 | N1 | `/ai-advisor` at 320 by 812 | each of the eight tabs is reached by touch scroll and chosen by tap, then by Tab and Enter | each tab panel opens and the chosen tab is in view |
| S-7.9 | N5 | the strip scrolled to its end, then back | the cues are read | a cue shows only on an edge that has a tab out of view, and none when no tab is out of view |
| S-7.10 | N7 | the strip | it renders | it uses existing tokens and shapes only; no new color, icon or illustration |

Negative coverage: N1 S-7.4, S-7.8 · N2 n/a: layout refuses nothing · N3 S-7.5 · N4 n/a: unchanged auth hook (S-5.8) · N5 S-7.6, S-7.9 · N6 n/a: a failed fetch shows the ref's error state, which this change does not resize (S-1.20) · N7 S-7.2, S-7.3, S-7.10 · N8 n/a: no edit state changes the height
Questions: none

### AC-8: Repeated autotune text is stated once and frontrunner cards are told apart
Requirement items: R-30
Rule: When the Recent autotune runs table renders two or more rows and a column's cell text is identical in every row, the panel shall print that column once, above the rows, in one line that begins "Same for every run: " and lists each such column as its header followed by its cell text, joined by "; ", and the rows shall leave those cells out; and when the Frontrunner Builder tab renders pending cards, each card's title shall read its incumbent's name (or its hash where no name resolves, as today) followed by " · proposal #" and the proposal's id.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-8.1 | B | 12 autotune rows whose Outcome cell reads "Fallback" with "Frozen-eval not evaluated", and whose score and t-stat cells read "N/A", in every row | the Overview renders at 1440 and at 375 (built by static/ai_advisor.js) | one line reads "Same for every run: Outcome Fallback, Frozen-eval not evaluated; Best CRRA-EU score N/A; Sel t-stat N/A" and no row repeats those cells; each row keeps its symphony, run time and streak |
| S-8.2 | N1 | rows whose Outcome differs in one row | the table renders | Outcome stays in every row; only the columns identical in every row move to the line |
| S-8.3 | N1 | exactly one row | the table renders | no "Same for every run" line; the row shows every cell as at the ref |
| S-8.4 | N3 | no autotune runs | the panel renders | its empty state is unchanged and no line shows |
| S-8.5 | N7 | any table state | the panel renders | every value a row showed at the ref is still on screen, in its row or in the line; the older-runs disclosure keeps its own rows unchanged |
| S-8.6 | N5 | the 15 s refresh brings a run whose Outcome differs | the table re-renders | Outcome returns to every row and leaves the line |
| S-8.7 | N6 | the autotune refresh request fails | the table is read | the last rendered table and line stay, as the table does at the ref |
| S-8.8 | B | five pending proposals for one incumbent with ids 31 to 35 | the Frontrunner tab renders | the titles read "{name} · proposal #31" to "{name} · proposal #35", all different |
| S-8.9 | N3 | an incumbent whose name does not resolve | the tab renders | the title reads its hash followed by " · proposal #" and the id |
| S-8.10 | N7 | each card | it renders | its `title` attribute (the hash), `data-testid` values, Approve and Reject controls and every figure are unchanged |

Negative coverage: N1 S-8.2, S-8.3 · N2 n/a: nothing is refused · N3 S-8.4, S-8.9 · N4 n/a: unchanged auth hook (S-5.8) · N5 S-8.6 · N6 S-8.7 · N7 S-8.5, S-8.10 · N8 n/a: no edit state on either surface
Questions: none

#### Copy table A: page, tab and section descriptions
Columns: Key, the host and where the line sits at the ref, who builds it, the kind (new, kept or reworded), the old text, the exact new text, and what it is true of. A slot in braces is filled per AC-4. "kept" means the existing line already meets AC-1 and is the section's description, unchanged.

| Key | Host and place at the ref | Built by | Kind | Old text | New text (exact) | True of |
|---|---|---|---|---|---|---|
| D01 | `/`, top of the page, above the hero (`templates/index.html:1178-1215`) | template | new | none | This page shows how the bot's stops are guarding each symphony and how its exits compare with simply holding. Start with the guard effect, then check Active for anything armed or exited. | `app.py:3599-3608`; `analytics.py:2113-2126` |
| dash.hero | `/`, under "Guard effect on portfolio" (`templates/index.html:1223`), above the descriptor at `:1240`, which stays | template | new | none | The guard effect is how the bot's exits compare with holding every symphony through, over the window you pick. Above zero the exits did better than holding; below zero holding would have done better. | `analytics.py:2113-2126`; `templates/index.html:1226-1240` |
| dash.rows | `/`, top of the comparison rows `#portfolio-strip` (`templates/index.html:1366`) | template | new | none | The same comparison as single figures: today, the Composer lifetime, the deepest drop in the window and how widely daily returns swing. On every row α is the bot's edge over holding, so a positive α is good. | `templates/index.html:1319-1323`, `:1352-1362`, `:1485` |
| D20 | `/`, above Tracked, Armed and Triggered (`templates/index.html:1551`) | template | new | none | Tracked counts every symphony the bot watches. Armed counts those with a stop set and waiting, and Triggered counts those the bot has exited. | `app.py:3803-3813` |
| D21 | `/`, top of the dollar-saved panel (`templates/index.html:1575`) | template | new | none | The dollars the exits saved or lost against holding, added across the exits in the window you pick. The lines beside it give the same total after trading costs and at each exit's end-of-day prices. | `reporting.py:85-95`; `app.py:6489`, `:6581`; `static/index.js:2869-2878` |
| D28 | `/`, under the Active section header (`templates/index.html:1611-1612`) | template | new | none | Symphonies with a stop armed, and any the bot has already exited. The engine checks armed stops every minute while the market is open, so these are the ones to watch. | `app.py:3599-3603`, `:2924` |
| D40 | `/`, under the Standby header (`templates/index.html:1729-1730`) | template | new | none | Symphonies the bot is watching with no stop armed. A symphony moves up to Active when a stop arms or the bot exits it. | `app.py:3604-3608` |
| D41 | `/`, under the CVaR title (`templates/index.html:1907-1909`) | template | new | none | The average of the worst outcomes for the first symphony, a measure of how bad its bad days could get. It is context only; the two notes below say why it is not a trading signal. | `templates/index.html:1896-1940`; `math_engine.py:1699-1701` |
| D47 | `/`, under "Managed sleeves" (`templates/index.html:1956-1958`) | template | new | none | Slices of your Alpaca account run by rules you write. A new rule starts in shadow, which records what it would have done and places no order; Arm Paper moves it to the paper account. | `database.py:4176-4182`; `templates/index.html:1836-1844` |
| D48 | `/`, the sleeves empty line (`templates/index.html:1970`) | template | reworded | No sleeves configured yet. | No sleeves configured yet. This page has no form for creating one. | `templates/index.html:1943-1949` |
| dp.lead | detail panel, under "Symphony detail" (`templates/index.html:1988-1989`) | template | new | none | One symphony up close: where its stop sits, what the engine recorded in the session and how its risk compares with holding. | `templates/index.html:2001-2137` |
| D57 | detail panel, under "Intraday tape" and its time line (`templates/index.html:2029-2030`) | template | new | none | The symphony's return minute by minute through the session, drawn against its stop and breakeven lines. The toggles add the VWAP line and the Monte Carlo reading. | `templates/index.html:2032-2049` |
| dp.events | detail panel, under the events heading (`templates/index.html:2058`) | template | new | none | Each event the engine recorded for this symphony in the session, so you can see what it did. | `static/index.js:1639-1680` |
| D59 | detail panel, under "Risk math" (`templates/index.html:2066`) | template | new | none | The figures the engine weighs when it decides whether to exit this symphony, from its latest check. The hint under each line says how to read it. | `templates/index.html:2067-2115`; `static/index.js:1380-1392` |
| D66 | detail panel, under "Risk Profile" (`templates/index.html:2122`) | template | new | none | This symphony's risk figures with the bot's exits and when held through, the same figures the Performance page shows for one symphony. | `templates/index.html:2119-2120` |
| D67 | detail panel, under "Symphony variables" (`templates/index.html:2131`) | template | new | none | The stop settings this symphony runs with. A variable marked locked keeps its value when the weekly autotune runs. | `static/index.js:1688-1716`; `app.py:3236-3246`; `alpha_bot_execution.py:1179` |
| F01 | Performance, top of the first panel (`templates/performance.html:502`) | template | new | none | Risk-adjusted results of the bot's exits against holding, for the whole portfolio or one symphony, over the window you pick. On every figure in this row, above zero favors the bot; the guard effect's note says when it matches the dashboard's figure. | `static/performance.js:452-460`, `:485-502` |
| F11 | Performance, under "Cumulative returns" (`templates/performance.html:574`) | template | new | none | The two return paths day by day: the solid line is the bot with its exits, the dashed line is holding through. Where the solid line is above the dashed one, the exits were ahead. | `templates/performance.html:575-584` |
| F13 | Performance, the caption under "Risk metrics" (`templates/performance.html:595-599`) | template | kept | the caption at `templates/performance.html:595-599` | unchanged | `static/performance.js:485-502` |
| F16 | Performance, the caption under "Stop-justification preconditions" (`templates/performance.html:652-658`) | template | reworded | the caption at `templates/performance.html:652-658` | Checks whether each symphony's daily returns behave in the way that makes a trailing stop pay off on average: a day's move tends to carry into the next by more than the day's risk-adjusted return. Not met means this sample does not show that, not that the stop is wrong (Kaminski and Lo, 2014). Replay = simulated if-held daily series from the last autotune/replay run; Shadow = live if-held daily series across all position epochs of the symphony. | `guard_preconditions.py:150-158`, `:166-190` (the last sentence is the ref's own honesty text, kept) |
| F21 | Performance, the caption under the turnover heading (`templates/performance.html:697-702`) | template | reworded | the caption at `templates/performance.html:697-702` | How often each symphony's exits fired over the three windows, counted only over the days of history still kept, and an estimate of the yearly return those exits cost, from the simulated trading cost per exit. A high drag is a cost the exits have to earn back. | `database.py:3922-3990`; `autotuner.py:1444` |
| Y01 | History, top of the first panel (`templates/history.html:371`), above the caption at `:383-385` | template | new | none | How the bot's exits scored against holding, added up over the window you pick. Use it to see whether the exits pay for themselves and which rules do the work. | `analytics.py:2586-2690` |
| Y10 | History, under "Daily sum of exit alpha (pts, unweighted)" (`templates/history.html:425`) | template | new | none | Each bar is one day's exit alpha added up. A positive day is one where the exits beat holding; a negative day is one where holding would have done better. | `analytics.py:2640-2660` |
| Y12 | History, under "By exit reason" (`templates/history.html:446`) | template | new | none | The same totals split by the rule that fired each exit, so you can see which rules earn their keep and which give gains back. | `analytics.py:2652-2660` |
| Y15 | History, under the exits heading (`templates/history.html:512`) | template | new | none | Each exit of the day in the heading: when it fired, the symphony, the rule, and in Detail its exit alpha. | `static/history.js:368-380` |
| S01 | Settings, the page subtitle (`templates/settings.html:963`) | template | reworded | .env globals + SQLite-isolated symphony strategies · live edits, no restart | Global settings and per-symphony overrides · saved changes apply from the next minute, no restart | `templates/settings.html:986-989` |
| S03 | Settings, Master controls subtitle (`templates/settings.html:998`) | template | kept | The mode and the start time that govern real-money execution. Treat both with care. | unchanged | `templates/settings.html:1001-1033` |
| S08 | Settings, Algorithm parameters subtitle (`templates/settings.html:1040`) | template | kept | Global defaults applied to every symphony. Per-symphony overrides in the next section take precedence. | unchanged | `static/settings.js:197-220` |
| S11 | Settings, Credentials subtitle (`templates/settings.html:1047`) | template | reworded | the subtitle at `templates/settings.html:1047` | The keys, account ids and webhook address the bot uses for Composer, Alpaca, Discord and Anthropic. The dashboard does not save credentials: they are set in the .env file on the host, and the server shows only masked placeholders here. | `app.py:9513-9525`, `:9651-9656`, `:9708-9729` |
| S14 | Settings, Per-symphony overrides subtitle (`templates/settings.html:1128`) | template | kept | the subtitle at `templates/settings.html:1128` | unchanged | `static/settings.js:490-591` |
| S17 | Settings, Maintenance subtitle (`templates/settings.html:1139`) | template | kept | the subtitle at `templates/settings.html:1139` | unchanged | `templates/settings.html:1141-1188` |
| M04 | settings modal, the "Execution mode" header's empty sub line (`static/settings-modal.js:471`) | static/settings-modal.js | new | none | Whether this symphony may send real sell orders. It trades live only when this switch and the server's global live switch are both on. | `static/settings-modal.js:369-391` |
| M05 | settings modal, "Locked tuning variables" sub line (`static/settings-modal.js:482`) | static/settings-modal.js | reworded | Checked variables the autotuner may not change | Checked variables keep their value when the weekly autotune runs; the autotune may change the rest. | `autotuner.py:3104-3121`; `alpha_bot_execution.py:1179` |
| M06 | settings modal, "Autotuner parameters" sub line (`static/settings-modal.js:489`) | static/settings-modal.js | reworded | Read-only, autotuner-owned. | The values the autotune set for this symphony. They are read-only here. | `static/settings-modal.js:430-440` |
| M07 | settings modal, "AI advisor" sub line (`static/settings-modal.js:495`) | static/settings-modal.js | reworded | Display-only. Act by locking a variable and letting the optimizer converge. | What the advisor said about this symphony, for reading only. To act on it, lock a variable above or accept a suggestion on the Advisor page. | `static/settings-modal.js:442-447` |
| A01 | Advisor, the caption above the tab bar (`templates/ai_advisor.html:1122-1124`) | template | reworded | the caption at `templates/ai_advisor.html:1122-1124` | Advisory tools: a suggestion changes nothing until you accept or approve it. The weekly autotune is the one exception: it applies new stop settings by itself when they pass its checks. | `autotuner.py:3093-3152`; `alpha_bot_execution.py:1180`; `app.py:12206-12208` |
| O01 | Overview tab, top of the panel (`templates/ai_advisor.html:1205-1216`) | template | new | none | A summary of what the advisor tools have found: suggestions for one symphony on demand, the weekly autotune results, the overnight market read and the latest automated checks. Nothing on this tab changes a symphony until you accept a suggestion. | `templates/ai_advisor.html:1216-1625` |
| O03 | Overview, above the symphony picker and Run Claude advisor (`templates/ai_advisor.html:1221`) | template | new | none | Pick a symphony and choose Run Claude advisor for suggested changes to its stop settings. You can accept a suggestion only if it also holds up on recent history the tuning never saw. | `ai_advisor.py:99-105`, `:2080`; `static/ai_advisor.js:362-372` |
| O08 | Overview, under "Recent autotune runs" (`templates/ai_advisor.html:1261`) | template | new | none | Each symphony's latest weekly autotune, which searches past data for better stop settings. New settings are applied only when they also beat the current ones on data the search did not use; Outcome says what each run did. | `autotuner.py:3085-3121`; `alpha_bot_execution.py:1179` |
| O15 | Overview, under the "Market Prism" title (`templates/ai_advisor.html:1299`) | template | new | none | An overnight read of the market through five lenses: technicals, sentiment, derivatives, macro and fundamentals. It is background for your decisions and changes nothing by itself. | `templates/ai_advisor.html:1283-1286`; `app.py:2933` |
| O27 | Overview, under "Advisor observations" (`templates/ai_advisor.html:1537`), above the note at `:1546-1556`, which stays | template | new | none | The latest verdict from each advisor that records observations: the checks on the tuning process, Market Prism, and the weekly swap and rule-change searches. Each covers something different, so read the note below before you take a Clear as an all-clear. | `templates/ai_advisor.html:1515-1556`, `:1568` |
| C01 | Correlations tab, top of the panel, under the caveat (`templates/ai_advisor.html:1636-1646`) | template | new | none | How closely your symphonies' daily returns move together, from the latest successful retirement run; this tab only measures. Two symphonies that move as one add risk without adding diversification; the Retirement tab uses this to suggest which one to drop. | `templates/ai_advisor.html:1644-1646`; `advisors/retirement_recommender.py:58` |
| R01 | Retirement tab, under "Retirement recommendations" (`templates/ai_advisor.html:1940`) | template | new | none | Suggestions to retire a symphony that duplicates another or no longer earns its place. Nothing is retired for you: approving records your decision and gives you a checklist to wind the symphony down yourself in Composer. | `templates/ai_advisor.html:1775-1823`; `app.py:12206` |
| R02 | Retirement, under "Correlated pairs" (`templates/ai_advisor.html:1944`) | template | new | none | A symphony is flagged when, over enough shared days, its daily returns track another live symphony's at a correlation of {screen_threshold} or more, even at the low end of the estimate's range and on the worst days, and it has the lower combined return and risk score. Each card names the symphony to retire, then the one to keep after the arrow. | `advisors/retirement_recommender.py:58`, `:65-69`, `:82-97`, `:108-112`, `:316`, `:376-378`, `:760`; `templates/ai_advisor.html:1973-1978` |
| R22 | Retirement, under "Symphony tenure review" (`templates/ai_advisor.html:2094`) | template | new | none | Once a symphony has been invested for {tenure_threshold} trading days, this review asks whether it still earns its place: its real results against its own backtest, its effect on the whole portfolio, and whether it duplicates another symphony. Each eligible symphony gets keep, watch, retire-candidate or no verdict; the review only advises, and approving a retirement records your decision without changing anything in Composer. | `advisors/tenure_review.py:39-44`, `:1155-1214`, `:1902-1907`; `app.py:12206-12208` |
| W01 | Asset Swaps tab, under the model line (`templates/ai_advisor.html:2200`) | template | new | none | Test whether replacing one ticker in a symphony with another would have helped, against a statistical gate built to reject lucky results. Leave both tickers blank to let the advisor propose swaps; you make any change yourself in Composer. | `app.py:11013-11040`; `advisors/backtest_gate_engine.py` (`evaluate_candidate_batch`) |
| L01 | Logic Changes tab, under the model line (`templates/ai_advisor.html:2269`) | template | new | none | Test a change to a symphony's rules, such as a different lookback, against the same statistical gate: the advisor edits your current rule tree, backtests it and judges the result. Rule changes are the easiest to overfit, so read any pass as a lead to check. | `app.py:11368-11385`, `:11444-11449` |
| T02 | Chat tab, the body under the title (`templates/ai_advisor.html:2355-2358`) | template | reworded | the body at `templates/ai_advisor.html:2355-2358` | Chat explains what the advisor found and why. Use Discuss this proposal or Chat about this on a proposal or suggestion card to start a conversation about it; Chat explains and changes nothing. | `templates/ai_advisor.html:2725-2739`; `static/ai_advisor.js:370-372` |
| B00 | Strategy Builder tab, under the model line (`templates/ai_advisor.html:2386`) | template | new | none | Ask the advisor for new strategies for one goal: it designs some from scratch, adds ranked community strategies, tests them all and keeps only the strongest that clears the gate. A survivor is a suggestion you would build yourself in Composer. | `templates/ai_advisor.html:2476-2481`, `:2725-2730` |
| B03 | Strategy Builder, the note under "Propose new strategies" (`templates/ai_advisor.html:2476-2481`) | template | kept | the note at `templates/ai_advisor.html:2476-2481` | unchanged | `advisors/strategy_builder_engine.py` (`propose_strategies`) |
| B07 | Strategy Builder, under "Strategy proposals" (`templates/ai_advisor.html:2610`) | template | new | none | Strategies that cleared the statistical gate. To use one, build it in Composer yourself from the rules on its card. | `templates/ai_advisor.html:2725-2730` |
| B10 | Strategy Builder, under "Backtest failures" (`templates/ai_advisor.html:2749`) | template | new | none | Candidates Composer could not backtest, with the error it returned. The gate never judged them. | `templates/ai_advisor.html:2753-2760` |
| X01 | Frontrunner Builder tab, above the risk banner (`templates/ai_advisor.html:2844-2856`) | template | new | none | Proposes copies of your symphonies with one frontrunner overlay added: a rule placed ahead of the symphony's own logic that switches into VIX-linked funds when its conditions are met, for example a watched ticker's RSI rising above a set level. You approve or reject each copy. | `advisors/frontrunner_builder.py:667`, `:816-827`; `advisors/frontrunner_detector.py:5-9` |
| X03 | Frontrunner, the note under "Run frontrunner build" (`templates/ai_advisor.html:2913-2917`) | template | kept | the note at `templates/ai_advisor.html:2913-2917` | unchanged | `advisors/strategy_builder_scheduler.py` (`run_weekly_build`) |
| X05 | Frontrunner, under "Pending approval (N)" (`templates/ai_advisor.html:2925`) | template | new | none | Copies waiting for your decision. Compare each copy's Calmar, CAGR and drawdown with the original before you approve; approving creates a new undeployed symphony and trades nothing. | `templates/ai_advisor.html:2861-2867`; `advisors/frontrunner_builder.py:15-21` |

#### Copy table B: term definitions
Audit items covered by other rows: D11 by D06a and D10; D27 by D25; D60 by D58d; F06 by F01; F18 by F17a; F22 by F21 and F22r; Y11 by Y10; A02 by the tab rows O01, C01, R01, W01, L01, T02, B00 and X01; O17 by MU1; R24 by R22; B04 by B01 and B02; X13 by X06. W08 and S09 are excluded by NB-4, H02, H03, H07, H08, H09 and H10 by the header rows, H01, H04, H05, H06 and H11 by NB-2, O21 by NB-3 and D52 by NB-5.

Placement V means the definition is the visible description named; D means a line in the "What the terms mean" disclosure under that section's description; A means the control's `title` or accessible description, used for the header rows only (D-12). Each disclosure is built by whatever builds its section (the template unless the row says a script).

| Key | Host | Term as shown | Placement | Built by | Definition (exact) | True of |
|---|---|---|---|---|---|---|
| D02 | `/` | Guard effect on portfolio | V dash.hero | template | (the dash.hero description) | `analytics.py:2113-2126` |
| D04 | `/` | 30d 60d 90d 125d YTD 1Y All time | D dash.hero | template | Windows: 30d to 1Y are calendar days counted back from the session shown. YTD starts on 1 January; All time uses every recorded day. | `analytics.py:2103`, `:2129-2152` |
| D06a | `/` | Planet Stopper (dry-run), Bot | D dash.hero | template | Bot: the return with the bot's exits applied. While live trading is off it is a simulation that places no order, and the headline then says Simulated. | `templates/index.html:1239-1240`, `:1279` |
| D06b | `/` | If held, Held | D dash.hero | template | If held: what the same symphonies would have returned with no exit, held as Composer runs them. | `analytics.py:1019-1031` |
| D07 | `/` | shadow-history basis | D dash.hero | template | Shadow history: the bot's own daily record of both paths, kept for the last {shadow_retention_days} days. | `app.py:1792-1793`, `:2930` |
| D05 | `/` | Account · all-time | D dash.hero | template | Account · all-time: Composer's own total return for the whole account since it was opened; it does not change with the window. | `app.py:4858-4860`; `templates/index.html:1241-1243` |
| D03 | `/` | All symphonies at their latest values, before trading costs | D dash.hero | template | At their latest values: each symphony is weighted by its current value; before trading costs: no cost of trading is taken off. | `analytics.py:1826-1830` |
| D10 | `/` | the info mark on the first row | D dash.rows | template | The first row's Held figure is for the whole account, uninvested cash included; a card's Held figure leaves cash out. | `templates/index.html:1369-1373` |
| D12 | `/` | α | D dash.rows | template | α: the bot's edge over holding on that row. Positive is good for the bot: a higher return, a shallower fall or calmer swings. | `templates/index.html:1352-1362`, `:1485` |
| D13 | `/` | the Bot and Held bars | D dash.rows | template | Bars: the highlighted bar and label mark the better side on that row. | `templates/index.html:1346-1360`, `:1386-1394`, `:1486-1488` |
| D14 | `/` | Value-weighted floor | D dash.rows | template | Value-weighted floor: Composer's account totals could not be read, so the row weights each symphony by its value instead. | `app.py:4852-4854`; `static/index.js:1844` |
| D15 | `/` | Stale account data | D dash.rows | template | Stale account data: Composer's account totals could not be refreshed, so the row uses the last totals read, from the time shown. | `app.py:4855-4857`; `static/index.js:1841` |
| D16 | `/` | Cumulative · lifetime | D dash.rows | template | Cumulative · lifetime: Composer's Total return since each symphony was invested, which counts deposits and withdrawals; it is not a time-weighted return. | `templates/index.html:1405-1414`; `app.py:1943` |
| D17 | `/` | Max DD, needs 30 trading days | D dash.rows | template | Max DD: maximum drawdown, the largest fall from a high to a later low in the window, shown as a size. The row waits until the window holds enough trading days for a stable figure. | `templates/index.html:1443-1447`; `app.py:60-69` |
| D18 | `/` | Ann. vol | D dash.rows | template | Ann. vol: annualized volatility, how widely daily returns swing, scaled to a year; lower is calmer. | `templates/index.html:1471-1488` |
| D19 | `/` | Lifetime Max DD · since inception | D dash.rows | template | Lifetime Max DD · since inception: the deepest fall of your symphonies held through, from Composer's records since each was invested, weighted by value. | `analytics.py:1433-1436` |
| D20t | `/` | Tracked, Armed, Triggered | V D20 | template | (the D20 description) | `app.py:3803-3813` |
| D22 | `/` | exits, saved, lost | D D21 | template | Exit: one time the bot sold a symphony out. Saved or lost: the symphony's value times the gap between its exit return and its held return, added over the exits. | `reporting.py:85-95`; `app.py:6489` |
| D23 | `/` | snapshot-time basis | D D21 | template | Snapshot-time basis: each exit valued at the bot's afternoon check of that day. | `reporting.py:60-124` |
| D24 | `/` | gross of trading costs | D D21 | template | Gross of trading costs: before any cost of trading the exit. | `app.py:6489-6581` |
| D25 | `/` | net of trading costs | D D21 | template | Net of trading costs: after taking {friction_pct} percentage points off every exit, the cost the autotune assumes for each exit. With many exits the net result sits well below the gross one: fewer dollars saved, or more lost. | `autotuner.py:1444`; `app.py:6524-6581` |
| D26 | `/` | realized (marks basis) | D D21 | template | Realized (marks basis): each exit valued at its day's end-of-day prices instead of the afternoon check; the count says how many exits have those prices. | `app.py:6534`; `reporting.py` (Stage 2 realized fields) |
| D28a | `/` | N locked | D D28 | template | N locked: how many tuning variables are locked across your symphonies; the weekly autotune leaves those alone. | `app.py:3236-3246` |
| D28b | `/` | a session date after the heading | D D28 | template | A date after the heading: the figures below belong to that session, not today. | `templates/index.html:1617-1618` |
| D29a | `/` | Trailing stop | D D28 | template | Trailing stop: the exit fired because the symphony fell further below its peak than its volatility-scaled stop allows. | `math_engine.py:452-518`; `static/history.js:221` |
| D29b | `/` | Take-profit | D D28 | template | Take-profit: the exit fired to keep a gain the Monte Carlo check judged likely to fade. | `math_engine.py:796-860`; `static/history.js:220` |
| D29c | `/` | VWAP breakdown | D D28 | template | VWAP breakdown: the exit fired after the symphony had gained enough to arm this check, its holdings then traded below their volume-weighted average price, and its return stayed under the session high for several checks in a row. | `math_engine.py:995-1004`, `:956` |
| D29d | `/` | VWAP bleed | D D28 | template | VWAP bleed: the exit fired after the symphony's own return stayed at or below a volatility-scaled loss line for several checks in a row; despite the name, this check does not look at the volume-weighted average price. | `math_engine.py:982-990`, `:1035-1043`, `:945-950` |
| D29e | `/` | Armed | D D28 | template | Armed: the trailing stop is set and following the symphony's peak. | `math_engine.py:303-340` |
| D29f | `/` | TP-armed | D D28 | template | TP-armed: the take-profit is set and waiting for its confirmation. | `math_engine.py:796-860` |
| D29g | `/` | Para-armed | D D28 | template | Para-armed: the symphony rose fast enough that its stop has been pulled in closer. | `math_engine.py:452-518` |
| D30 | `/` | Exit beat holding, Exit trailed holding | D D28 | template | Exit beat or trailed holding: the exit's return minus the symphony's held return. | `templates/index.html:1660-1663`; `app.py:3343-3355` |
| D31 | `/` | vs held now, at close | D D28 | template | vs held now: compared with the held return at this minute, so the figure moves while the market is open. At close: compared at the session's close. | `templates/index.html:1660-1663`; `app.py:3651` |
| D32 | `/` | Lifetime Max DD | D D28 | template | Lifetime Max DD: this symphony's deepest fall since it was invested, from Composer's records, held through. | `analytics.py:1170-1172` |
| D33 | `/` | the first ledger column, a date or Today with α | D D28 | template | The first column is the session's return; its α is the bot's edge over holding for that session. | `templates/index.html:1681-1686` |
| D34 | `/` | Bot · frozen | D D28 | template | Bot · frozen: after an exit the Bot figure stays at the exit's return while If held keeps moving. | `templates/index.html:1688`; `analytics.py:1026-1028` |
| D35 | `/` | If held · live | D D28 | template | If held · live: the held return, still moving with the market after the exit. | `templates/index.html:1692` |
| D36 | `/` | Cum | D D28 | template | Cum: return since the symphony was invested, from Composer; the Bot side adds the exits' effect over the shadow history. | `analytics.py:1019-1034` |
| D37 | `/` | Max DD on a card | D D28 | template | Max DD on a card: each side's deepest fall over the shadow history; a positive α means the bot's fall was shallower. | `analytics.py:1158-1163`; `templates/index.html:1643` |
| D38 | `/` | the ring with a percent | D D28 | template | The ring: the Monte Carlo reading, the share of similar past market days on which this symphony's holdings did better than they are doing now. A low reading means today is unusually strong; a dash means no reading. | `math_engine.py:1386-1394`; `app.py:3358-3370` |
| D39 | `/` | the sparkline and its badges | D D28 | template | Sparkline: the session's return; a TP, VWAP or Stop badge marks the minute of an exit. | `static/index.js:609-640` |
| D40t | `/` | Standby | V D40 | template | (the D40 description) | `app.py:3604-3608` |
| D42 | `/` | CVaR (5%) | D D41 | template | CVaR: expected shortfall, the average of the outcomes in the worst tail the heading names. | `math_engine.py:1699-1705` |
| D43 | `/` | stderr | D D41 | template | stderr: the standard error, how far the figure could be off by chance alone. | `app.py:3173-3190` |
| D44 | `/` | Tail obs | D D41 | template | Tail obs: how many outcomes fell in that tail; a small count means a rough figure. | `math_engine.py:1705-1708` |
| D46 | `/` | lower bound on tail severity | D D41 | template | Lower bound: the method tends to understate how bad the tail is, so the real figure is likely worse. | `templates/index.html:1937-1939` |
| D49 | `/` | Atlas cache | D D47 | template | Atlas cache: the age of the weekly saved copy of the community strategy catalog the builders read. | `app.py:3253-3270` |
| D50a | `/` | Capital | D D47 | template | Capital: the money assigned to the sleeve. | `docs/generated/sleeves.md` (ledger conservation law) |
| D50b | `/` | Ledger cash | D D47 | template | Ledger cash: the sleeve's uninvested cash by its own books. | `docs/generated/sleeves.md` (ledger conservation law) |
| D50c | `/` | Realized | D D47 | template | Realized: gains or losses already closed out; n/a means the books could not be read. | `templates/index.html:1868-1869` |
| D50d | `/` | a rule line | D D47 | template | Rule line: the rule's mode in brackets, then how often it fired today and over its life, and what those fires realized. | `templates/index.html:1832-1835` |
| D53 | detail panel | Return, Live return, frozen at exit, at close, real-time | D dp.lead | template | Return: the symphony's return in the session; frozen at exit after an exit, at close once the market has closed, real-time while it is open. | `static/index.js:1177` |
| D54 | detail panel | Stop level, breakeven lock, vol-scaled | D dp.lead | template | Stop level: the return at which the bot exits; breakeven lock means the stop has been raised to protect the gain, vol-scaled means it follows the symphony's volatility. | `static/index.js:1182`; `math_engine.py:519-580` |
| D55 | detail panel | Peak, HWM | D dp.lead | template | Peak (HWM): the highest return the symphony itself reached in the session; until an exit, the stop trails this peak, and after an exit the figure keeps following the held return. | `alpha_bot_execution.py:1343-1353`, `:1505` |
| D56 | detail panel | Exit α | D dp.lead | template | Exit α: the exit's return minus the held return, for this symphony. | `static/index.js:1190-1196` |
| D58a | detail panel | Bot (booked) | D D57 | template | Bot (booked): the bot's path; it moves with the symphony until an exit and then stays at the exit's return. | `analytics.py:1026-1028`; `templates/index.html:2045` |
| D58b | detail panel | Breakeven lock | D D57 | template | Breakeven lock: once the gain is large enough, the stop is raised so that the exit cannot fall below breakeven. | `math_engine.py:519-580` |
| D58c | detail panel | VWAP | D D57 | template | VWAP: the day's volume-weighted average price. | `math_engine.py:863-920` |
| D58d | detail panel | MC probability | D D57 | template | MC probability: the share of similar past market days on which this symphony's holdings did better than they are doing now; lower means today is unusually strong. | `math_engine.py:1386-1394` |
| D63 | detail panel | Parabolic velocity | D D59 | template | Parabolic velocity: how fast the return is climbing; past the threshold, the stop is pulled in closer. | `math_engine.py:452-518` |
| D64 | detail panel | Active · floor | D D59 | template | Active · floor: the breakeven lock has fired and the stop cannot go below the floor shown. | `static/index.js:1389`; `math_engine.py:561-580` |
| D65 | detail panel | VWAP defense | D D59 | template | VWAP defense: whether the symphony's holdings trade above or below their volume-weighted average price, and by how much; below it after a gain is what the VWAP breakdown exit watches for. | `static/index.js:1594-1603`; `math_engine.py:995-1004` |
| D67t | detail panel | locked | D D67 | template | locked: the weekly autotune may not change this variable. | `static/index.js:1714` |
| F03 | Performance | 30d to 1Y, YTD, 5Y | D F01 | template | Windows: calendar days counted back from the session shown; YTD starts on 1 January and 5Y covers five years. | `analytics.py:2103`, `:2129-2152` |
| F04t | Performance | If held, Bot (shadow) | D F01 | template | If held: the same symphonies held with no exit. Bot (shadow): the bot's shadow record, the return with its exits applied; while live trading is off it is a simulation that places no order. | `analytics.py:1810-1830`; `templates/index.html:1239-1240` |
| F07 | Performance | Sharpe | D F01 | template | Sharpe: return per unit of overall volatility; higher is better. | `analytics.py:373-470` |
| F09 | Performance | Sortino | D F01 | template | Sortino: return per unit of downside volatility only; higher is better. | `analytics.py:373-470` |
| F08 | Performance | Max DD reduction | D F01 | template | Max DD reduction: how much shallower the bot's deepest fall was than holding's, in percentage points. | `static/performance.js:494-497` |
| F15a | Performance | Max DD | D F13 | template | Max DD: the largest fall from a high to a later low over the window, shown as a size. | `analytics.py:373-470` |
| F15b | Performance | Calmar | D F13 | template | Calmar: yearly return divided by the deepest fall; higher is better. | `analytics.py:373-470` |
| F15c | Performance | Annualized volatility | D F13 | template | Annualized volatility: how widely daily returns swing, scaled to a year. | `analytics.py:373-470` |
| F15d | Performance | Volatility reduction | D F13 | template | Volatility reduction: how much calmer the bot's returns were than holding's, in percentage points. | `static/performance.js:53-64` |
| F15e | Performance | Total return | D F13 | template | Total return: the whole window's return, compounded. | `analytics.py:373-470` |
| F15f | Performance | Annualized return (CAGR) | D F13 | template | Annualized return (CAGR): the yearly rate that compounds to the total return. | `analytics.py:373-470` |
| F15g | Performance | Up days | D F13 | template | Up days: the share of days with a gain. | `analytics.py:467-468` |
| F17a | Performance | Sample | D F16 | template | Sample: which daily series was checked, Replay or Shadow, as the note above says. | `templates/performance.html:655-657` |
| F17b | Performance | ρ | D F16 | template | ρ: lag-1 autocorrelation, how much one day's return tends to carry into the next. | `guard_preconditions.py:19-80` |
| F17c | Performance | 95% CI | D F16 | template | CI: the confidence range that ρ likely sits in. | `guard_preconditions.py:19-80` |
| F17d | Performance | Daily Sharpe | D F16 | template | Daily Sharpe: the average daily return divided by its volatility, not annualized. | `guard_preconditions.py:19-80` |
| F17e | Performance | N | D F16 | template | N: how many daily returns the sample holds. | `guard_preconditions.py:19-80` |
| F19a | Performance | Sufficient met | D F16 | template | Sufficient met: even at the low end of its range, the day-to-day carry beats the daily Sharpe ratio, which is enough to justify the stop on average. | `guard_preconditions.py:150-171` |
| F19b | Performance | Sufficient likely | D F16 | template | Sufficient likely: the carry beats the daily Sharpe ratio at its central estimate, but its range overlaps it. | `guard_preconditions.py:173-177` |
| F19c | Performance | Not met | D F16 | template | Not met: this sample does not show enough carry; that is not proof the stop is wrong, and it says nothing about drawdown protection. | `guard_preconditions.py:179-190` |
| F19d | Performance | Negative edge | D F16 | template | Negative edge: the daily Sharpe ratio is zero or below, so no carry can justify the stop on return grounds. | `guard_preconditions.py:191-196` |
| F19e | Performance | Insufficient data | D F16 | template | Insufficient data: too few days to judge either way. | `guard_preconditions.py:19`, `:197-201` |
| F20 | Performance | autotune | D F16 | template | Autotune: the weekly search for better stop settings; its last run leaves the replay series this table reads. | `autotuner.py:1556`; `alpha_bot_execution.py:1179` |
| F23 | Performance | retained, in 30d | D F21 | template | Retained: the days of exit history still kept; a count covers only those days. | `static/performance.js:915-928`; `database.py:3922-3970` |
| F24 | Performance | Est. annual trading-cost drag | D F21 | template | Est. annual trading-cost drag: an estimate of the yearly return lost to trading costs, at {friction_pct} percentage points per exit times the exits per year. | `database.py:3974-3990`; `autotuner.py:1444` |
| Y03 | History | 30d to 1Y, YTD, 5Y | D Y01 | template | Windows: calendar days counted back from the session shown; YTD starts on 1 January and 5Y covers five years. | `templates/history.html:374-381`; `analytics.py:2586-2600` |
| Y04 | History | exit alpha, pts | D Y01 | template | Exit alpha: an exit's return minus its held return, in pts, which are percentage points. | `templates/history.html:409`; `analytics.py:2643` |
| Y08 | History | the time in the exit-alpha note | D Y01 | template | The time in the note: when the bot freezes each exit's result for the day's record, the same afternoon check the dashboard calls snapshot-time. | `templates/history.html:409`; `reporting.py:60-124` |
| Y05 | History | Dollar result | D Y01 | template | Dollar result: the dollars the exits saved or lost against holding, on the same snapshot basis as the dashboard. | `analytics.py:2643-2647`; `app.py:6653` |
| Y07 | History | Win rate | D Y01 | template | Win rate: the share of exits whose exit alpha was above zero. | `analytics.py:2650-2651`, `:2683` |
| Y13 | History | Wins, Avg per exit | D Y12 | template | Wins: exits whose exit alpha was above zero. Avg per exit: the reason's sum of exit alpha divided by its number of exits. | `analytics.py:2659`; `static/history.js:320-321` |
| Y16 | History | Detail | V Y15 | template | (the Y15 description) | `static/history.js:368-380` |
| S13a | Settings | Composer group | D S11 | template | Composer: the key and secret the bot reads your symphonies and sends sell orders with, and your account ids. | `alpha_bot_execution.py` (`get_composer_headers`); `app.py:3815-3830` |
| S13b | Settings | Alpaca · price data | D S11 | template | Alpaca · price data: the keys the bot reads market prices with. | `synthetic_history.py` (`fetch_bars`) |
| S13c | Settings | Discord + Anthropic | D S11 | template | Discord + Anthropic: the webhook for alerts and the key the advisor uses. | `reporting.py`; `ai_advisor.py:1871` |
| S15 | Settings | uses globals, N overrides, lock, override | D S14 | static/settings.js | uses globals: no value set for this symphony. N overrides: values set for this symphony. lock: stop the autotune changing a value. override: a value that replaces the global one. | `static/settings.js:522-591` |
| S16 | Settings | No longer held | D S14 | static/settings.js | No longer held: Composer no longer reports this symphony and the engine writes it no rows; it returns here by itself if its rows resume. | `static/settings.js:496-502` |
| S18 | Settings | synthetic post-mortem, re-syncs account totals | D S17 | template | Synthetic post-mortem: a daily exit record that was generated or seeded rather than captured live. Re-syncs account totals: reads the account totals from Composer again. | `templates/settings.html:1150-1155`; `static/settings.js:350-353` |
| M01 | settings modal | Dry-run (global off), Live, Dry-run | D M04 | static/settings-modal.js | Dry-run (global off): the server's live switch is off, so nothing trades live. Live: this symphony sends real sell orders. Dry-run: this symphony is watched but sends no orders. | `static/settings-modal.js:369-376` |
| M08 | settings modal | Clear, Watch, Breach | D M07 | static/settings-modal.js | Clear: the check passed. Watch: worth a look, not a failure. Breach: the check failed. | `app.py:113-123` |
| H08 | header, every page | the mode pill, Dry run or Live | A | template | Dry run: global live execution is off, so the bot sends no real orders. Live: global live execution is on; a symphony trades live only when its own live switch is on too. | `app.py:3905`; `templates/_chrome.html:57-59`; `static/settings-modal.js:369-376` |
| H02 | header, every page | the engine state word | A | template and static/chrome.js | Live: the market is open and the engine is checking stops every minute. Closed: the market is closed. Rebalance pause: the scheduled pause before the close; stops resume at the close. Opening: the session's first cycle is running. Stale: no recent engine cycle, so positions may not be guarded. Not updating: the page has not received data for a while. Degraded: the server reports a problem, named in the tooltip. Connection Lost: the last refresh failed, and the values shown are from the last data received. Each state carries only its own sentence. | `static/chrome.js:283-303`, `:351-377`; `templates/_chrome.html:20-26`; `app.py:2924` |
| H07 | header, every page | the account selector | A | template | Account: the Composer account the dashboard shows figures for; choosing another reloads the dashboard for it. The short code is the start of the account's id. | `static/chrome.js:59-77`, `:90-150`; `app.py:8351-8361` |
| H09 | header, every page | Force run now | A | template | Force run now: asks the server for an engine run; the server declines and answers that runs come only from the scheduler, so pressing it starts nothing. | `app.py:8345-8349`; `static/chrome.js:1-50` |
| H10 | header, every page | Emergency Liquidate | A | template | Emergency Liquidate: after you choose an account and type LIQUIDATE, asks Composer to sell every symphony in that whole account to cash. While global live execution is off it only logs and sends the alert, and sells nothing. | `app.py:8970-9030`, `:8923-8935`; `static/chrome.js:170-245` |
| A03 | Advisor, every tab | the dot beside a tab name | D A01 | template | Tab dots: green marks low risk; amber marks a tab to read with more care: higher overfitting risk, a real Composer write, or explanations only. | `templates/ai_advisor.html:476-485`, `:1135-1197` |
| A04 | Advisor, every tab | model not known | D A01 | template | Model not known: the page has no Claude model name to show, because the lookup has not run yet, failed, or found no model of that family. | `model_config.py:38`, `:66-73`, `:184`, `:207-212`; `app.py:10953-10954` |
| O04 | Overview | OOS | D O03 | template | OOS: out of sample, recent history the tuning never saw, kept back to check that a result is not fitted to noise. | `ai_advisor.py:99-105` (`_OOS_HOLDOUT_FRACTION`) |
| O07a | Overview | confidence on a suggestion card | D O03 | template | Confidence: the model's own rating of a suggestion, not a statistical test. | `ai_advisor.py:251` |
| O07b | Overview | allowlist, risk direction, oos frozen eval, locked vars | D O03 | template | Gates: allowlist checks the setting is one the advisor may suggest; risk direction checks that the direction the model claims for its change, looser or tighter, matches the direction the engine works out; oos frozen eval checks the change holds up on held-back history; locked vars checks the setting is not locked. | `ai_advisor.py:2072`; `app.py:12371-12381`; `static/ai_advisor.js:312-320` |
| O06 | Overview | Fallback OOS (on an empty run, built by static/ai_advisor.js) | D O03 | template | Fallback OOS: the same held-back score for the settings the symphony had before the latest autotune. | `static/ai_advisor.js:240-247`; `autotuner.py:2947` |
| O09a | Overview | Runs without adopting a tuned config | D O08 | template | Runs without adopting a tuned config: how many runs in a row kept the old settings. | `analytics.py:2748` |
| O09b | Overview | Best CRRA-EU score | D O08 | template | CRRA-EU score: the autotune's score for a set of settings, an average outcome in which losses weigh more than gains of the same size; higher is better. | `math_engine.py:1880` |
| O09c | Overview | Sel t-stat | D O08 | template | Sel t-stat: how clearly the winning settings stand out after correcting for how many settings were tried; higher is stronger. | `static/ai_advisor.js:768` |
| O10 | Overview | Fallback | D O08 | template | Fallback: the new settings failed the check on held-back data, so the symphony keeps the settings it had before the run. | `autotuner.py:2947`, `:3106-3113` |
| O11 | Overview | Frozen-eval | D O08 | template | Frozen-eval: a final score of the run's best settings on held-back data, under the same objective; not evaluated means the run recorded no such score. | `autotuner.py:3122-3130` |
| O12 | Overview | N/A | D O08 | template | N/A: the run recorded no figure for that column. | `static/ai_advisor.js:796-799` |
| O16 | Overview | risk-on, risk-off, neutral, limited-inputs | D O15 | template | risk-on: conditions favor taking risk. risk-off: they favor caution. neutral: no lean either way. limited-inputs: too few lenses had data for a read. | `templates/ai_advisor.html:1300-1312`; `advisors/lens_pipeline.py` (`run_pipeline`) |
| O19 | Overview | Technicals, Sentiment, Derivatives, Macro, Fundamentals | D O15 | template | Lenses: technicals reads price trends and breadth, sentiment reads news tone, derivatives reads volatility measures, macro reads rates, inflation and jobs data, and fundamentals reads company filings. | `advisors/lens_technicals.py`; `advisors/news_corpus.py`; `ai_advisor.py` (`_build_macro_section`, `_build_fundamentals_section`) |
| O24 | Overview | Numeric verification, verified, unverifiable | D O15 | template | Numeric verification: each number the read lists as a cited figure is checked against lens data fetched after the read; verified means it matched, and unverifiable means there was nothing to check it against, not that it is wrong. Numbers that appear only in the prose are not checked. | `advisors/prism_numeric_verifier.py:300-307` |
| O28 | Overview | THEORY spec, backtest-selection degrees of freedom | D O27 | template | THEORY spec: the frozen set of objective settings every tuning run shares, such as its risk aversion and utility family. Backtest-selection degrees of freedom: choices made by looking at backtest results, each of which adds overfitting risk. | `advisors/spec_critic.py:1-16` |
| O29 | Overview | Asset Swap, Logic Change | D O27 | template | Asset Swap and Logic Change: the weekly searches for a better ticker or rule in each symphony. | `advisors/weekly_suggestions_scheduler.py` (`run_weekly_suggestions`) |
| O30 | Overview | Clear, Watch, Breach, Adopt candidate, Keep incumbent | D O27 | template | Clear: the check passed. Watch: worth a look, not a failure. Breach: the check failed. Adopt candidate: a candidate passed the gate and beat the current symphony. Keep incumbent: no candidate beat the current symphony. | `app.py:113-123` |
| C05 | Correlations | correlation, the matrix figures | D C01 | template | Correlation: a number from minus 1 to 1 for how closely two return series move together; 1 is in lockstep and 0 is unrelated. | `advisors/correlation_diagnostic.py` (`_pearson_r`) |
| C03 | Correlations | stored no pairs | D C01 | template | Stored no pairs: the run analysed the symphonies but saved no pair figures, so there is nothing to draw until a newer run saves them. | `app.py:7650-7665` |
| C07 | Correlations | the legend bands and thin markers | D C01 | template | Fill and markers: the fill deepens as the correlation gets stronger; a thin marker means the pair shares few days, so its figure is rough. | `templates/ai_advisor.html:1716-1729` |
| R04 | Retirement | deposit-adjusted | D R02 | template | Deposit-adjusted: the series Composer names deposit-adjusted, which the recommender uses instead of the raw account value series. | `advisors/composer_series_client.py` (parses `deposit_adjusted_series`, never `series`) |
| R06 | Retirement | Analysed | D R02 | template | Analysed: symphonies with a usable daily series, of all live symphonies. | `app.py:7118-7122` |
| R07 | Retirement | History | D R02 | template | History: the shortest and longest daily series, in days. | `app.py:7123-7127` |
| R08 | Retirement | Pairs screened | D R02 | template | Pairs screened: pairs whose correlation reached the screen, of all pairs compared. | `app.py:7094-7097`, `:7128-7132` |
| R09 | Retirement | Blocked | D R02 | template | Blocked: screened pairs that did not become a recommendation, counted by the check that stopped them. | `app.py:7099-7116`; `advisors/retirement_recommender.py:775-786` |
| R12 | Retirement | Run recommender | D R02 | template | Run recommender: runs the nightly analysis now; it takes a minute or two and is refused while the market is open. | `templates/ai_advisor.html:2076`, `:2087-2089` |
| R17 | Retirement | kept | D R02 | template | kept: the symphony of the pair that stays; the one before the arrow is the one suggested for retirement. | `templates/ai_advisor.html:1973-1978` |
| R18 | Retirement | Composite gap | D R02 | template | Composite gap: how far the kept symphony's combined return and risk score is ahead of the flagged one's. | `templates/ai_advisor.html:1986-1992`; `advisors/retirement_recommender.py:108-112`, `:269` |
| R19a | Retirement | Uncertainty gate | D R02 | template | Uncertainty gate: passes only if the correlation is still at or above the screen at the low end of its confidence range, so a high reading from a short history does not count. | `advisors/retirement_recommender.py:69`, `:376` |
| R19b | Retirement | Structural-redundancy gate, stress window | D R02 | template | Structural-redundancy gate: passes only if the two still move together on the worst {stress_fraction_pct} percent of days, when diversification matters most. | `advisors/retirement_recommender.py:82-97` |
| R20 | Retirement | Approve retirement | D R02 | template | Approve retirement: records your decision and shows a wind-down checklist; it sells nothing and changes nothing in Composer. | `app.py:12206`; `templates/ai_advisor.html:1800-1823` |
| R23 | Retirement | Run tenure review | D R22 | template | Run tenure review: runs the review now instead of waiting for the nightly run; it is refused while the market is open. | `static/ai_advisor.js:1894`; `app.py:2940-2943` (the nightly 03:45 tick) |
| R26 | Retirement | trigger, threshold in effect | D R22 | template | trigger: whether the run was the nightly one or one you started. threshold in effect: the trading days a symphony needs before it gets a verdict. | `templates/ai_advisor.html:2118-2121`; `advisors/tenure_review.py:430` |
| R27a | Retirement | keep | D R22 | template | keep: no evidence against it. | `advisors/tenure_review.py:1208` |
| R27b | Retirement | watch | D R22 | template | watch: it trails its own backtest by more than {watch_trail_pp} percentage points of total return, or the portfolio would have a higher return and no deeper drawdown without it. | `advisors/tenure_review.py:49`, `:1187-1208` |
| R27c | Retirement | retire-candidate | D R22 | template | retire-candidate: it duplicates another live symphony and scores lower, as under Correlated pairs. | `advisors/tenure_review.py:1179-1185` |
| R27d | Retirement | no verdict | D R22 | template | no verdict: there is not yet enough evidence to judge. | `advisors/tenure_review.py:1168-1178` |
| R28 | Retirement | basis, missing | D R22 | template | basis: which series the figures use, Composer's realized one or the bot's shadow history. missing: evidence items that could not be computed, with the reason. | `templates/ai_advisor.html:2153-2160`; `advisors/tenure_review.py:84-105` |
| R30 | Retirement | not applicable, paired with, normalized across N symphonies | D R22 | template | not applicable: the item does not apply to this symphony, for the reason shown. paired with: the live symphony this one is compared against. normalized across N symphonies: the score is ranked among that many symphonies. | `templates/ai_advisor.html:1828-1876` |
| R29a | Retirement | Performance | D R22 | template | Performance: the symphony's realized return and risk over its tenure. | `advisors/tenure_review.py:312` |
| R29b | Retirement | Composite score | D R22 | template | Composite score: its return and risk score ranked against the other live symphonies. | `advisors/tenure_review.py:315` |
| R29c | Retirement | Redundancy | D R22 | template | Redundancy: its return correlation with the other live symphonies. | `advisors/tenure_review.py:318` |
| R29d | Retirement | Backtest | D R22 | template | Backtest: Composer's backtest of its current rules, against what it really earned. | `advisors/tenure_review.py:90`, `:324` |
| R29e | Retirement | Marginal contribution | D R22 | template | Marginal contribution: the portfolio of eligible symphonies with and without it. | `advisors/tenure_review.py:93`, `:330` |
| R29f | Retirement | Long VIX hedge | D R22 | template | Long VIX hedge: its long-volatility holdings beside the rest of the portfolio, shown for context and not used in the verdict. | `advisors/tenure_review.py:96`, `:333` |
| W02 | Asset Swaps | the gate | D W01 | template | The gate: a candidate must beat the current symphony on history held back from the test, beat SPY over the same period, pass the overfitting check, and survive a correction for how many candidates were tested. | `advisors/backtest_gate_engine.py` (`evaluate_candidate_batch` stage order) |
| W06 | Asset Swaps | Baseline, Variant | D W01 | template | Baseline: the symphony as it is. Variant: the symphony with the change. | `static/ai_advisor_asset_swaps.js:125-190` |
| W07 | Asset Swaps | PBO | D W01 | template | PBO: probability of backtest overfitting, the chance the best-looking candidate looked best by luck; a high PBO vetoes the candidate. | `advisors/backtest_gate_engine.py` (batch PBO veto) |
| W09 | Asset Swaps | Model, Context, Run | D W01 | template | Model and context: which model reasoned and which evidence it was given. | `static/ai_advisor_asset_swaps.js:325-338` |
| L02a | Logic Changes | FDR/Yekutieli correction | D L01 | template | FDR/Yekutieli correction: raises the bar each candidate must clear as more candidates are tested, so one lucky result among many tries is not taken for skill. | `advisors/backtest_gate_engine.py` (BHY correction over the batch) |
| L02b | Logic Changes | sound-but-unprovable | D L01 | template | Sound-but-unprovable: passed every check, which still does not prove it will work live. | `templates/ai_advisor.html:2276-2279` |
| L03 | Logic Changes | Reduce drawdown, Lift risk-adjusted return, Reduce turnover | D L01 | template | Objectives: reduce drawdown aims for shallower falls, lift risk-adjusted return aims for more return per unit of risk, and reduce turnover aims for fewer trades. | `templates/ai_advisor.html:2306-2312` |
| L05 | Logic Changes | Baseline, Variant, Ann. return | D L01 | template | Baseline: the symphony as it is. Variant: the symphony with the change. Ann. return: yearly return. | `static/ai_advisor_logic_changes.js:143-162` |
| L06 | Logic Changes | adjusted threshold α, p | D L01 | template | Adjusted threshold α: the p-value bar after the correction for how many candidates were tested. p: the candidate's p-value, which must be under the bar to pass. | `static/ai_advisor_logic_changes.js:204-213` |
| B01 | Strategy Builder | community | D B03 | template | Community: strategies from the Atlas library, a public catalog of Composer strategies, ranked for your goal. | `advisors/community_strats.py`; `advisors/build_plan_generator.py` (`admit_community_candidates`) |
| B02 | Strategy Builder | FDR/Yekutieli, BHY/Yekutieli FDR gate | D B03 | template | FDR/Yekutieli correction: raises the bar each candidate must clear as more candidates are tested, so one lucky result among many tries is not taken for skill. | `advisors/backtest_gate_engine.py` (BHY correction over the batch) |
| B03o | Strategy Builder | Diversify, Cut drawdown, Lift risk-adjusted return | D B03 | template | Objectives: diversify favors strategies that share few holdings with yours, cut drawdown favors shallow falls, and lift risk-adjusted return favors a higher Sharpe ratio. | `advisors/build_plan_generator.py` (`admit_community_candidates`) |
| B03u | Strategy Builder | Universe (tickers) | D B03 | template | Universe (tickers): the tickers the designed strategies may use; leave it empty to allow every tradeable US-listed stock and fund. | `static/ai_advisor.js:1294-1307`; `advisors/strategy_builder_engine.py:412-414`; `advisors/universe_provider.py` (`get_tradeable_set`) |
| B08 | Strategy Builder | PBO | D B03 | template | PBO: probability of backtest overfitting, the chance the best-looking candidate looked best by luck; a high PBO vetoes the candidate. | `advisors/backtest_gate_engine.py` (batch PBO veto) |
| B07t | Strategy Builder | Live Baseline | D B07 | template | Live Baseline: the same figures for your live symphony over the same days as the candidate's backtest. | `advisors/strategy_builder_engine.py:716-719` |
| B12 | Strategy Builder | incubating, promoted, failed, expired | D B07 | template | Incubating: paper-tracked forward before it is shown as a recommendation. Promoted: its forward results held up. Failed: they did not, or its fall went too deep. Expired: tracking stopped. | `advisors/incubation.py` (`evaluate_promotion`, `run_incubation_tick`) |
| X06 | Frontrunner | incumbent, frontrunner overlay | D X05 | template | Incumbent: your current symphony. Frontrunner overlay: a rule placed ahead of the symphony's own logic that switches into VIX-linked funds when its conditions are met, for example a watched ticker's RSI rising above a set level. | `advisors/frontrunner_builder.py:667`, `:816-827` |
| X08 | Frontrunner | cards with the same title | D X05 | template | Same title: several cards can copy the same symphony; they differ in their overlay and figures, which View the added overlay shows. | `templates/ai_advisor.html:2945`, `:2994` |
| X09 | Frontrunner | Strategy Builder (retrofit) | D X05 | template | Strategy Builder (retrofit): a from-scratch Strategy Builder design offered for this symphony; it does not contain the incumbent's logic. | `templates/ai_advisor.html:2946`, `:2996-3000` |
| X11 | Frontrunner | Calmar, CAGR, Max DD | D X05 | template | Calmar: yearly return divided by the deepest fall; higher is better. CAGR: the yearly growth rate. Max DD: the deepest fall from a high to a later low. | `advisors/frontrunner_acceptance.py` (`evaluate_calmar_acceptance`) |
| X12 | Frontrunner | Node count change | D X05 | template | Node count change: how many more or fewer steps the copy's rule tree has than the original; fewer is simpler. | `templates/ai_advisor.html:2985-2987` |

#### Copy table C: reworded labels
| Key | Host and place at the ref | Built by | Old text | New text (exact) | True of |
|---|---|---|---|---|---|
| D30a | `/`, a triggered card's verdict, gap above zero | server first paint (`app.py:3354`) and `static/index.js:1037-1041` (poll) | Good call · saved +{m}%α | Exit beat holding by {m} pts | `app.py:3343-3355` |
| D30b | `/`, a triggered card's verdict, gap below zero | `app.py:3354` and `static/index.js:1037-1041` | Early exit · gave up {m}%α | Exit trailed holding by {m} pts | `app.py:3343-3355` |
| D30c | `/`, a triggered card's verdict when {m} prints as 0.0 | `app.py:3354` and `static/index.js:1037-1041` | Early exit · gave up 0.0%α | Exit matched holding | `app.py:3352-3354` |
| D29v | `/`, the pill for a VWAP breakdown exit | `templates/index.html:1653` and `static/index.js:2011-2012` | VWAP | VWAP breakdown | `math_engine.py:959-967` |
| D68 | detail panel footer (`templates/index.html:2144`) | template | shadow_hwm · stop · mc_prob updated every 1m | Peak, stop and Monte Carlo reading update every minute | `app.py:2924` |
| F04 | Performance caption (`templates/performance.html:530-532`) | template | Live (if held) vs Planet Stopper-exited (shadow) · daily portfolio series, value-weighted | If held vs Bot (shadow record of the bot's exits) · daily portfolio series, value-weighted | `analytics.py:1810-1830` |
| F12a | Performance chart legend and dataset (`templates/performance.html:578`; `static/performance.js:243`) | template and static/performance.js | Planet Stopper-exited (shadow); Planet Stopper-Exited (shadow) | Bot (shadow) | `analytics.py:1810-1830` |
| F12b | Performance chart legend and dataset (`templates/performance.html:582`; `static/performance.js:232`) | template and static/performance.js | Live (if held) | If held | `analytics.py:1810` |
| F14a | Performance table header (`templates/performance.html:605`) | template | If-held baseline | If held | `static/performance.js:485-502` |
| F14b | Performance table header (`templates/performance.html:606`) | template | Bot (Planet Stopper) | Bot (shadow) | `static/performance.js:485-502` |
| F10 | Performance insufficient banner (`templates/performance.html:542-543`) | template | At least {min_history_days} trading days of portfolio history are needed for stable quantstats metrics. Showing what's available so far. | At least {min_history_days} trading days of portfolio history are needed for stable risk metrics. Showing what's available so far. | `app.py:52-58` |
| D03r | `/`, the hero descriptor (`templates/index.html:1240`) | template | All symphonies at their latest values, before exit friction. | All symphonies at their latest values, before trading costs. | `analytics.py:1826-1830` |
| F22r | Performance turnover header and heading (`templates/performance.html:696`, `:711`) | template | Exit turnover & estimated friction drag; Est. annual friction drag | Exit turnover & estimated trading-cost drag; Est. annual trading-cost drag | `database.py:3974-3990` |
| S02 | Settings side-nav note (`templates/settings.html:986-989`) | template | All changes are written to .env or alphabot_state.db immediately on save. The minute scheduler picks them up on the next tick. | Saved changes go straight to the server's settings file or its database. The engine picks them up at its next minute. | `app.py:9651-9713`; `app.py:2924` |
| D06c | `/`, hero chart legend (`templates/index.html:1279`) | template | Planet Stopper ({mode}) | Bot ({mode}) | `templates/index.html:1279` |
| Y02 | History caption (`templates/history.html:383-385`) | template | Rollup of post-mortem snapshots · how the bot's exits actually scored vs the if-held series | Totals from the bot's daily exit records · how the bot's exits actually scored vs the if-held series | `analytics.py:2586-2600` |
| Y06 | History hero label and table header and cell label (`templates/history.html:401`, `:454`, the `data-label` cells; `static/history.js:329`) | template and static/history.js | Triggers | Exits | `analytics.py:2648` |
| Y14a | History reason names (`templates/history.html:464-491`; `static/history.js:193-216` display values) | template and static/history.js | Take-Profit, Trailing Stop, VWAP Breakdown, VWAP Bleed Cut | Take-profit, Trailing stop, VWAP breakdown, VWAP bleed | `static/history.js:193-216` |
| Y14b | History reason definitions (`static/history.js:219-224`) | static/history.js | the four definitions at `static/history.js:220-223` | the D29a, D29b, D29c and D29d definitions of Copy table B, each without its leading "Name: " | `math_engine.py:452-967` |
| R09a | Retirement Blocked tile detail (`app.py:7101-7105`) | server-built tile text (`app.py:7101-7105`) | uncertainty | too uncertain | `advisors/retirement_recommender.py:376` |
| R09b | the same | `app.py` | too thin | too few worst days | `advisors/retirement_recommender.py:775-779` |
| R09c | the same | `app.py` | below threshold | apart on worst days | `advisors/retirement_recommender.py:780-781` |
| R09d | the same | `app.py` | no candidate | no score to compare | `advisors/retirement_recommender.py:316`, `:784-786` |
| R09e | the same, the stress sentence (`app.py:7111-7115`) | `app.py` | . Stress window needs at least {n} aligned days. | . The worst-days check needs at least {n} days on which both symphonies have a return. | `advisors/retirement_recommender.py:180` |
| S05 | Settings Live execution body (`templates/settings.html:1009-1011`) | template | When Live, the bot fires real POST /go-to-cash requests to Composer the moment a stop is hit. | When Live, the bot sends Composer a real sell-to-cash order the moment a stop is hit. | `templates/settings.html:1008-1013` |
| S07 | Settings start time hint (`templates/settings.html:1032`) | template | The minute the scheduler begins monitoring stops. Market opens 09:30; earlier values stage state pre-open. | The minute the scheduler begins monitoring stops. Market opens 09:30; an earlier time lets the engine prepare its state before the open. | `alpha_bot_execution.py:124` |
| A05 | Asset Swaps and Logic Changes model line (`templates/ai_advisor.html:2200`, `:2269`) | template | the model line at `templates/ai_advisor.html:2200` and `:2269` | {model} · reasons over your symphony's current rules | `ai_advisor.py` (`build_reasoning_context`) |
| T05 | Chat unavailable body (`templates/ai_advisor.html:3071-3075`) | template | Set ANTHROPIC_API_KEY to enable explanations. | Chat needs an Anthropic API key in the server's .env file; the daemon reads it when it starts. | `app.py:10413` |
| X14 | Frontrunner overlay disclosure summary (`templates/ai_advisor.html:2994`) | template | View overlay delta | View the added overlay | `templates/ai_advisor.html:2994` |
| MU1 | Overview, Market Prism model badge (`templates/ai_advisor.html:1314`) | template | Synthesized by {model}, which reads "Synthesized by model unavailable" when no model resolves | resolved: Synthesized by {model}; unresolved: Synthesizing model not known | `app.py:10954`; `model_config.py:66-73` |
| MU2 | Asset Swaps and Logic Changes model line (`templates/ai_advisor.html:2200`, `:2269`), together with A05 | template | {model} with A05's old text | resolved: {model} · reasons over your symphony's current rules; unresolved: Reasons over your symphony's current rules; model not known | `app.py:10953`; `model_config.py:66-73` |
| MU3 | Chat model line (`templates/ai_advisor.html:2349`) | template | {model} | resolved: {model}; unresolved: Chat model not known | `app.py:10954`; `model_config.py:66-73` |
| MU4 | Strategy Builder model line (`templates/ai_advisor.html:2386`) | template | {model} + community, statistically gated | resolved: {model} and community strategies, statistically gated; unresolved: Claude and community strategies, statistically gated; model not known | `app.py:10953`; `advisors/build_plan_generator.py` (`load_atlas_candidates`) |
| MU5 | Strategy Builder run note (`templates/ai_advisor.html:2477`) | template | The advisor generates candidate strategies via {model} (the rest of the note unchanged) | resolved: as at the ref; unresolved: The advisor generates candidate strategies with its Claude model (the rest of the note unchanged) | `app.py:10953`; `model_config.py:66-73` |
| NM1 | `/`, hero right column (`templates/index.html:1538`) | template | Lifetime max drawdown · since inception: | Lifetime Max DD · since inception: | `analytics.py:1433-1436` |
| NM2 | `/`, every card (`templates/index.html:1670`, `:1765`) | template | Lifetime MDD: | Lifetime Max DD: | `analytics.py:1170-1172` |
| NM3 | detail panel stat label (`templates/index.html:2019`) | template | Guard α | Exit α | `static/index.js:1190-1196` |
| NM4 | detail panel stat label (`templates/index.html:2014`) | template | Shadow peak | Peak | `alpha_bot_execution.py:1349-1350` |
| NM5 | detail panel legend (`templates/index.html:2045`) | template | Shadow (booked) | Bot (booked) | `analytics.py:1026-1028` |
| NM6 | detail panel legend while the market is open (`templates/index.html:2044`; `static/index.js:2378`) | template and static/index.js | Live (if held) | If held · live | `templates/index.html:1692` |
| NM7 | Performance metric row (`templates/performance.html:642`; `static/performance.js:64`) | template and static/performance.js | Win rate | Up days | `analytics.py:467-468` |
| NM8 | Frontrunner card title (`templates/ai_advisor.html:2945`) | template | {name} | {name} · proposal #{id} (AC-8) | `templates/ai_advisor.html:2938-2940` |
| X04 | Frontrunner unavailable body (`templates/ai_advisor.html:2876-2879`) | template | the body at `templates/ai_advisor.html:2876-2879` | The Frontrunner build needs an Anthropic API key in the server's .env file to generate candidates. Proposals already queued still show below. | `templates/ai_advisor.html:2870-2880` |
| S12r | Settings, Credentials warning (`templates/settings.html:1049-1053`) | template | Heads up. Saving any of these fields writes the new value to .env immediately. Blank fields are ignored (existing value preserved). | Heads up. The dashboard cannot save these fields, and a save that includes one is refused with nothing written. To change a credential, edit the .env file on the host; some keys take effect only after the daemon restarts. | `app.py:9708-9729`; `app.py:10413` |
| S12s | Settings, a credential field's status after typing (`static/settings.js:244`) | static/settings.js | Ready to save | Not saved from here: set it in .env on the host | `app.py:9708-9729` |
| X15 | Frontrunner full preview summary (`templates/ai_advisor.html:3004`) | template | Full spliced symphony (preview) | Full symphony with the overlay added (preview) | `advisors/frontrunner_builder.py` (`splice_candidate_into_symphony`) |

#### Values table (AC-4)
One row per copy row that holds a slot. When the slot's authority can be read, the phrase is printed with the value in the slot's format; when it cannot, the whole phrase is replaced by the fallback phrase, so the sentence still reads correctly. The {min_history_days} value is already in context at the ref, so its fallback is a guard only.

| Slot | Copy row | Phrase holding the slot (exact) | Fallback phrase (exact) | Authority at the ref | In template context at the ref | Format |
|---|---|---|---|---|---|---|
| {tenure_threshold} | R22 | Once a symphony has been invested for {tenure_threshold} trading days, | Once a symphony has been invested for the configured minimum number of trading days, | the stored review's `threshold_in_effect` when a review exists (`templates/ai_advisor.html:2121`); otherwise `TENURE_REVIEW_MIN_TRADING_DAYS` read the way the review reads it, from the env file first and from the process environment only when the file has no value (`app.py:2616-2623`), resolved by `advisors/tenure_review.py:430` (`parse_threshold`, default `DEFAULT_MIN_TENURE_TRADING_DAYS` at `:41`) | yes, as `tenure_review.threshold_in_effect`, only when a review exists; no otherwise (Gate 2 states how the configured value reaches the page) | whole number |
| {screen_threshold} | R02 | at a correlation of {screen_threshold} or more, | at or above the correlation screen, | the stored ok run's `screen_threshold` when one exists, so R02 and the Pairs tile print the same value (`app.py:7094-7097`); otherwise `advisors/retirement_recommender.py:58` (`CORRELATION_SCREEN_THRESHOLD`) | only inside `retirement_last_run.screen_threshold` for an ok run; not when no run exists | as the Pairs tile prints it (`app.py:7097`) |
| {stress_fraction_pct} | R19b | on the worst {stress_fraction_pct} percent of days, | on their worst days, | `advisors/retirement_recommender.py:97` (`STRESS_WINDOW_FRACTION`) times 100 | no | whole number when whole, else one decimal |
| {watch_trail_pp} | R27b | by more than {watch_trail_pp} percentage points of total return, | by more than the set margin of total return, | `advisors/tenure_review.py:49` (`BACKTEST_TRAIL_WATCH_PP`) | no | the number with no trailing ".0" |
| {friction_pct} | D25 | after taking {friction_pct} percentage points off every exit, | after taking a fixed trading cost off every exit, | `autotuner.py:1444` (`SIM_EXIT_FRICTION_PCT`) | no | the number with no trailing zeros |
| {friction_pct} | F24 | at {friction_pct} percentage points per exit times the exits per year. | at a fixed cost per exit times the exits per year. | `autotuner.py:1444` (`SIM_EXIT_FRICTION_PCT`) | no | the number with no trailing zeros |
| {shadow_retention_days} | D07 | kept for the last {shadow_retention_days} days. | kept for a limited number of days. | `SHADOW_HISTORY_RETENTION_DAYS`, read from the env file as the 02:00 prune reads it, default 180 (`app.py:1792-1793`, `:2930`) | no | whole number |
| {min_history_days} | F10 | At least {min_history_days} trading days of portfolio history are needed | More trading days of portfolio history are needed | `app.py:58` (`_PERFORMANCE_MIN_HISTORY_DAYS`) | yes (`templates/performance.html:542`) | whole number |

## Rule-by-Entry-Point Matrix
| Rule | `/` | detail panel | Performance | History | Settings | settings modal | Overview | Correlations | Retirement | Asset Swaps | Logic Changes | Chat | Strategy Builder | Frontrunner Builder |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AC-1 descriptions | S-1.1 | S-1.2 | S-1.3 | S-1.4 | S-1.5 | S-1.6 | S-1.7 | S-1.8 | S-1.9 | S-1.10 | S-1.11 | S-1.12 | S-1.13 | S-1.14 |
| AC-2 definitions | S-2.1 | S-2.2 | S-2.3 | S-2.4 | S-2.5 | S-2.5 | S-2.6 | S-2.7 | S-2.9 | S-2.8 | S-2.8 | n/a: Copy table B has no Chat rows | S-2.10 | S-2.10 |
| AC-3 relabels | S-3.1, S-3.2, S-3.15 | S-3.12, S-3.15 | S-3.9, S-3.15 | S-3.8, S-3.15 | S-3.12 | S-3.12 | S-3.13, S-3.14 | n/a: no Correlations row in Copy table C | S-3.6 | S-3.13, S-3.14 | S-3.13, S-3.14 | S-3.13, S-3.14 | S-3.13, S-3.14 | S-3.12 |
| AC-6 density at 1440 | S-6.1, S-6.2 | S-6.6 | S-6.6 | S-6.6 | S-6.6 | S-6.6 | S-6.6 | S-6.6 | S-6.3 | S-6.6 | S-6.6 | S-6.6 | S-6.6 | S-6.5 |
| AC-7 growth at 375 | S-7.1 | S-7.1 | S-7.1 | S-7.1, S-7.3 | S-7.1 | S-7.1 | S-7.1 | S-7.1 | S-7.1 | S-7.1 | S-7.1 | S-7.1 | S-7.1 | S-7.1 |

| Rule | header, every page |
|---|---|
| AC-2 definitions (placement A) | S-2.19, S-2.20 |

| Rule | template render | script refresh or run (static/*.js) | page served with the script failed |
|---|---|---|---|
| AC-3 the same text from both builders | S-3.1, S-3.6 | S-3.2, S-3.8, S-3.9, S-3.10 | S-3.11 |
| AC-2 disclosures survive a refresh | S-2.1 | S-2.14 | S-2.18 |

## Not Building
Non-goals a reader could expect but will not get:
- NB-1: A help or docs page, a glossary page or an onboarding tour (approved by the PM, [PM-ASSUMED], G-19).
- NB-2: Section descriptions in the shared header, any change to its layout or look, and definitions for the "guard" tag, the clock, the countdown, the bell and the Tweaks button (audit items H01, H04, H05, H06, H11; excludes part of R-7 and part of R-9; approved by the PM, [PM-ASSUMED], narrowed by G-27).
- NB-3: A glossary for the model-written Market Prism text (lens summaries and the rationale, audit item O21); it is model output whose words change nightly and cannot be asserted (excludes part of R-9; approved by the PM, [PM-ASSUMED]).
- NB-4: Rewording or defining text a route returns in a JSON body: the precondition verdict sentences (`guard_preconditions.py:166-201`; their verdict words are defined by F19a to F19e), the Settings parameter names and help (`app.py:9536-9574`, audit item S09), the autotune decision words stored in the database ("Adopted AI", "Reset to Global Default") and the raw data warnings on swap results (audit item W08) (excludes part of R-9). Changing them changes a JSON value (G-16); the parameter names are the keys the operator sets and are left as the route returns them (approved by the PM, [PM-ASSUMED]).
- NB-5: removed: flipped in by the PM ruling G-28; AC-5 states the exception and S-5.14 tests it.
- NB-6: removed: the Universe input's path is confirmed at the ref and B03u defines it.
- NB-7: removed: brought into scope by the PM ruling G-26 (AC-8).
- NB-8: removed: brought into scope by the PM ruling G-25 (AC-7).
- NB-9: removed: brought into scope by the PM ruling G-24 (AC-3, rows MU1 to MU5).
- NB-10: Empty regions under the AC-6 size: the Stop-justification channel (A14, about 170 px wide), the frontrunner run card's button row (A12, 35 px tall) and the Logic Changes button row (A15, 35 px tall) (excludes part of R-2; approved by the PM, [PM-ASSUMED]).

Behavior that stays exactly as it is:
- NB-11: Every figure's value, every route and JSON body, the database, every migration, the scheduler, `alpha_bot_execution.py` and `math_engine.py` (approved by the PM, [PM-ASSUMED], G-16, G-19).
- NB-12: The behavior, visibility and enabled state of Force run now, Emergency Liquidate and its typed-LIQUIDATE modal, Run recommender, Run tenure review, every approve and reject control, the live switches on Settings and in the modal, Arm Paper, Disarm, Delete, Sync & Flush, Run Claude advisor, Evaluate swap, Evaluate logic change, Run analysis and Run build (approved by the PM, [PM-ASSUMED], G-18).
- NB-13: The risk banners listed in S-5.3, byte for byte (approved by the PM, [PM-ASSUMED], G-18).
- NB-14: The honesty text (bases, windows, as-of stamps, caveats, exclusion reasons), reworded only where Copy table C says (approved by the PM, [PM-ASSUMED], G-19).
- NB-15: The login page, the logout confirm page and Discord output (approved by the PM, [PM-ASSUMED], G-5, G-19).
- NB-16: Existing `title` attributes; this plan adds visible or disclosure definitions beside them and removes none (approved by the PM, [PM-ASSUMED]).
- NB-17: No new color, icon, illustration, font weight or type size; only the restyle's primitives and studio tokens (approved by the PM, [PM-ASSUMED], G-19).

Deferred:
- NB-18: Definitions for the header items NB-2 leaves out (Follow-Ups).
- NB-19: removed: the inert buttons are no longer rendered (G-28, AC-5).

Protected surfaces this consumes but does not change:
- NB-20: `tests/app/test_dashboard_no_order_path.py`, every `tests/security/*no_trade_boundary*.py`, the auth and CSRF hooks in `app.py`, `static/request_guard.js` (`PSRequest`) and the panic modal in `templates/_chrome.html` and `static/chrome.js`.

## Defect Classes
| DC-n | Class (what goes wrong) | Trigger (B or N1-N8) | Sibling search (tool, query, scope) | Scenarios | Raised in review as |
|---|---|---|---|---|---|
| DC-1 | Server render and script disagree: the first paint shows the new label and the poll writes the old one, or the reverse | N5 | grep: every old text of Copy table C in `templates/`, `static/`, `app.py`; scope PlanetStopper | S-3.1, S-3.2, S-3.8, S-3.9, S-3.10 | - |
| DC-2 | An explanation reaches the operator only on hover, so a phone never shows it | N7 | grep: `title=` and `.title =` in `templates/` and `static/` against the Copy table B terms; scope PlanetStopper | S-1.19, S-2.13 | - |
| DC-3 | A threshold, window or count typed into copy as a literal, so it lies when the configuration changes | N1 | grep: `0.65`, `126`, `10.0`, `0.5`, `5 percent`, `181` next to the new copy in `templates/` and `static/`; scope PlanetStopper | S-4.1 to S-4.10 | - |
| DC-4 | A banner or honesty line removed or altered while text around it is reworded | N7 | diff: the S-5.3 banners and the honesty list of S-5.4 at the ref against the head; scope PlanetStopper | S-5.3, S-5.4 | - |
| DC-5 | A label change leaks into a contract: a `data-testid`, a JSON key or a mapping key renamed with its text (for example the History reason keys or `blocked_*`) | N7 | grep: `Triggers`, `VWAP Bleed Cut`, `Take-Profit`, `blocked_`, `_verdict_text` in `static/`, `templates/`, `app.py`, `tests/`; scope PlanetStopper | S-3.7, S-5.6 | - |
| DC-6 | A narrow assertion: one host, one state or one viewport tested and the rest assumed | N1 | read: each test against the Rule-by-Entry-Point Matrix and the state families of S-1.15, S-6.10, S-6.11, S-7.5 | S-1.1 to S-1.14, S-6.1 to S-6.13, S-7.1 to S-7.6 | - |
| DC-7 | A layout fix for 1440 breaks 375, or the reverse | N1 | read: each CSS change's media queries in `templates/*.html`, `static/layout.css` and `static/tweaks.css` | S-6.10, S-7.1, S-7.2 | - |
| DC-8 | A script re-render closes an opened disclosure or duplicates a description | N5 | grep: `innerHTML` writes to section containers in `static/index.js`, `static/ai_advisor.js`, `static/performance.js`, `static/history.js`, `static/settings-modal.js`; scope PlanetStopper | S-1.21, S-2.14 | - |
| DC-9 | A copy sentence claims something the code does not do | B | read: each row's True-of citation at the ref, and its siblings in the same module | S-1.1 to S-1.14, S-2.1 to S-2.10, S-3.12 | - |

## Worked Example
The Retirement tab on a database with one ok recommender run (11 of 12 symphonies analysed, 8 of 55 pairs screened at 0.65, 8 blocked: 4 by the uncertainty gate and 4 because the worst-days window was too thin, minimum 181 days) and a stored tenure review whose threshold in effect is 126 trading days (S-1.9, S-2.9, S-3.6, S-4.2).

Under "Symphony tenure review" the operator now reads: "Once a symphony has been invested for 126 trading days, this review asks whether it still earns its place: its real results against its own backtest, its effect on the whole portfolio, and whether it duplicates another symphony. Each eligible symphony gets keep, watch, retire-candidate or no verdict; the review only advises, and approving a retirement records your decision without changing anything in Composer." Opening "What the terms mean" there shows, among others, "watch: it trails its own backtest by more than 10 percentage points of total return, or the portfolio would have a higher return and no deeper drawdown without it." The Blocked tile's detail reads "too uncertain 4, too few worst days 4, apart on worst days 0, no score to compare 0. The worst-days check needs at least 181 days on which both symphonies have a return." At 1440 the two subsections sit side by side, because each is shorter than 320 px.

Main failure case (S-4.9): the tenure threshold cannot be read and no review is stored. The line then reads "Once a symphony has been invested for the configured minimum number of trading days, this review asks ..." with no number, and the rest of the tab renders.

## Fail-Closed Behaviour
none: the feature sends no input to any write route and adds no refusal; the existing refusals it shows are pinned unchanged by S-5.11.

## Authorities Touched
| Value | Authority used | What it replaced | What proves no second source |
|---|---|---|---|
| tenure threshold | the stored review's `threshold_in_effect`, else the env file then the process environment, through `parse_threshold` | a literal in copy | grep of `TENURE_REVIEW_MIN_TRADING_DAYS` and `DEFAULT_MIN_TENURE_TRADING_DAYS` in `templates/`, `static/`, `app.py`: only the review's reader and the new context value |
| correlation screen | the stored ok run's `screen_threshold`, else `CORRELATION_SCREEN_THRESHOLD` | a literal in copy | grep of `0.65` and `screen_threshold` in `templates/` and `static/`: only the context value |
| stress window fraction | `STRESS_WINDOW_FRACTION` | a literal in copy | grep of `STRESS_WINDOW_FRACTION` and "5 percent" in `templates/` and `static/` |
| watch trail | `BACKTEST_TRAIL_WATCH_PP` | a literal in copy | grep of `BACKTEST_TRAIL_WATCH_PP` and `10.0` next to the copy |
| trading cost per exit | `SIM_EXIT_FRICTION_PCT` | a literal in copy | grep of `SIM_EXIT_FRICTION_PCT` and `0.5` next to the copy |
| shadow retention | `SHADOW_HISTORY_RETENTION_DAYS` read as the 02:00 prune reads it | a literal in copy | grep of `SHADOW_HISTORY_RETENTION_DAYS` in `app.py`, `templates/`, `static/` |
| minimum history days | `_PERFORMANCE_MIN_HISTORY_DAYS`, already in context | unchanged | already single-sourced at `templates/performance.html:542` |

## Contract Changes and Consumers
none: no route, JSON body, `data-testid` or stored value changes (S-3.7, S-5.6); only template context gains the values of the Authorities Touched table.

## Sibling Repos and Landing Order
none: this repository only (G-17).

## Migrations Required
none: no schema or data change.

## Configuration and Enablement
none: no new key; the existing keys `TENURE_REVIEW_MIN_TRADING_DAYS` and `SHADOW_HISTORY_RETENTION_DAYS` are read, not added, and nothing ships dark.

## Breaking Changes
none for any consumer: the reworded labels change only on-screen words; tests that pin the old words are listed in Earlier criteria and are updated by the RED author.

## Security Considerations
- Input validation and injection: no input is added. The slot values of AC-4 are non-secret numbers from module constants and two environment keys (`TENURE_REVIEW_MIN_TRADING_DAYS`, `SHADOW_HISTORY_RETENTION_DAYS`); they are inserted with the template's autoescape or a script's `textContent` (S-4.10).
- Authorization and access control: unchanged; every host stays behind the global auth hook (S-5.8).
- Data exposure: no new value reaches a page beyond the six new values of the Values table (the seventh, the minimum history days, is already in context); no credential, account id or key is named in any copy row.
- Client-side rendering: the script-built rows (Copy tables A to C rows that name a `static/*.js` builder) are written with `textContent` or escaped text, never by interpolating stored data into HTML.
- Abuse or rate limits: none; no request is added.
This agrees with the Sensitive header: none.

## Prerequisites
none: the change needs no new service, version or provider.

## Design-System Mapping
No declared design system; the restyle's conventions and the `--studio-*` tokens in `static/tokens.css` apply. No new primitive is created.

| Component | Element | Required primitive or token | Notes |
|---|---|---|---|
| Dashboard | page line, section descriptions | the dashboard's note style `.dash-panel-note` (`templates/index.html:1121`), `--studio-ink-dim` | sentence case, weight 400 |
| Detail panel | section descriptions | `.detail-section-sub` (`templates/index.html:2030`) | |
| Performance, History | descriptions | `.metrics-caption` and `.hp-note` (`templates/performance.html:595`, `templates/history.html:383`) | |
| Settings | descriptions | `.sec-subtitle` (`templates/settings.html:998`) | |
| Settings modal | descriptions | the `_sectionHeader` sub line (`static/settings-modal.js:471`) | |
| Advisor | tab and section descriptions | `.adv-note` (`templates/ai_advisor.html:1122`) | |
| Every host | "What the terms mean" disclosure | the History `.hp-more` details pattern (`templates/history.html:503-506`), restyled with each page's existing tokens; summary 12 px or larger, hairline `--studio-rule` | closed on load; one per section that has D rows |
| Dashboard, Retirement | side-by-side sections | the existing grid patterns `.hero-grid` and `.cards-grid` (`templates/index.html:1216`, `:1620`) | |

## Definition of Done (Verification)
| AC | Scenarios | Test level | Real-entrypoint test |
|---|---|---|---|
| AC-1 | S-1.1, S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8, S-1.9, S-1.10, S-1.11, S-1.12, S-1.13, S-1.14, S-1.15, S-1.16, S-1.17, S-1.18, S-1.19, S-1.20, S-1.21, S-1.22, S-1.23 | render tests through the Flask test client for template rows; browser tests (the `browser` marker, Firefox) for visibility and every script-built row; a text scan of the three copy tables for S-1.18 | `GET /`, `/performance`, `/history`, `/settings`, `/ai-advisor` on the test server; the modal opened from a card |
| AC-2 | S-2.1, S-2.2, S-2.3, S-2.4, S-2.5, S-2.6, S-2.7, S-2.8, S-2.9, S-2.10, S-2.11, S-2.12, S-2.13, S-2.14, S-2.15, S-2.16, S-2.17, S-2.18, S-2.19, S-2.20 | render tests for the disclosure text; browser tests for open, keyboard and refresh survival | the same pages, each disclosure opened by click and by keyboard |
| AC-3 | S-3.1, S-3.2, S-3.3, S-3.4, S-3.5, S-3.6, S-3.7, S-3.8, S-3.9, S-3.10, S-3.11, S-3.12, S-3.13, S-3.14, S-3.15, S-3.16, S-3.17 | render tests of the first-paint verdict and the Blocked tile text; node tests of the poll and chart label builders | `GET /` with a triggered symphony, then one poll; `GET /ai-advisor` with a stored run |
| AC-4 | S-4.1, S-4.2, S-4.3, S-4.4, S-4.5, S-4.6, S-4.7, S-4.8, S-4.9, S-4.10, S-4.11, S-4.12 | render tests with each authority patched; a source scan for literals | `GET /ai-advisor`, `/` and `/performance` with patched values |
| AC-5 | S-5.1, S-5.2, S-5.3, S-5.4, S-5.5, S-5.6, S-5.7, S-5.8, S-5.9, S-5.10, S-5.11, S-5.12, S-5.13, S-5.14, S-5.15 | before-and-after render comparison on one seeded state; route body comparison; the existing no-order and no-trade suites | every listed route on the test client; live: the PM's check |
| AC-6 | S-6.1, S-6.2, S-6.3, S-6.4, S-6.5, S-6.6, S-6.7, S-6.8, S-6.9, S-6.10, S-6.11, S-6.12, S-6.13, S-6.14 | browser tests at 1440 by 900 and 1240 by 900 measuring empty rectangles inside each panel's content box and figure-row spans | each page on the test server, and the PM's live look at the droplet copy |
| AC-7 | S-7.1, S-7.2, S-7.3, S-7.4, S-7.5, S-7.6, S-7.7, S-7.8, S-7.9, S-7.10 | browser tests at 375 by 812 and 320 by 812 | each page on the test server; the tab strip by touch, tap and keyboard |
| AC-8 | S-8.1, S-8.2, S-8.3, S-8.4, S-8.5, S-8.6, S-8.7, S-8.8, S-8.9, S-8.10 | node tests of the autotune table builder in static/ai_advisor.js; render tests of the frontrunner cards; a browser test at 375 | `GET /ai-advisor` with seeded autotune runs and pending proposals, then one 15 s refresh |

Gates that must pass:
- `ruff format --check` and `ruff check`.
- A scoped pytest run over `tests/ui/`, `tests/app/`, `tests/dashboard/`, `tests/js_syntax/` and `tests/security/`, with the browser marker selected for the browser tests, never with `-n auto` and never with more than 4 workers.
- CI green on the exact SHA.
- `git diff` of `alpha_bot_execution.py`, `math_engine.py`, `database.py` and `migrations/` against the base is empty.
- The PM's live functional check after the PR review: every page and tab at 1440 and 375 on the running daemon with the droplet database copy; each disclosure opened; a triggered card's verdict before and after a poll.
- A screenshot comparison per host at 1440 and 375, light and dark, against the audit's captures at the ref, by the behavioral-evidence capturer, judged by the ux-expert against AC-6 and AC-7.

Fixture provenance, one source per seeded state:
- Dashboard symphonies, shadow rows and post-mortems: the droplet captures in `tests/fixtures/prod_droplet/` (`bot_state_positions.json`, `shadow_last_rows.json`, `exit_triggers.json`, `post_mortems/`), captured by its `capture_from_droplet.py`.
- Autotune rows: `tests/fixtures/ai_advisor/autotune_run_reverted_to_fallback.json` and `autotune_run_adopted_ai.json`, repeated per symphony.
- Market Prism: `tests/fixtures/ai_advisor/market_prism_summary_full.json` and `market_prism_summary_limited_inputs.json`. Observations: `tests/fixtures/ai_advisor/ux_overhaul_02/observations.json`.
- Retirement run: `tests/fixtures/retirement/run_summary_captured.json`.
- States with no captured record at the ref (pending frontrunner proposals, a stored tenure review, Strategy Builder survivor and backtest-failure rows, a CVaR diagnostic, sleeves): written at test time through the real producer and accessor code. These are `database.insert_frontrunner_proposal` with trees from `tests/fixtures/advisors/frontrunner/real_tree_*.json`, the tenure review computed by `advisors/tenure_review.py` over the `prod_droplet` shadow rows and stored the way `advisors/tenure_review_runner.py` stores it, `advisors/strategy_builder_engine.py` `_persist_survivor` and `_persist_rejected`, `database.record_cvar_diagnostic`, and `database.create_sleeve` with `database.create_sleeve_rule`. Each accessor's own validation is the runtime check on the shape.
None is hand-built to match a test. The AC-7 baseline heights are recorded from the pages at 1988ce5f by the same browser harness on the same seeded states and saved with a provenance note naming the commit and the command. Copy assertions read the exact text from the plan's tables, not from the rendered page.

## Success Measures
none: judged by the live check and the screenshot comparison in Definition of Done.

## Known-Unconfirmed and Standing Risk
- KU-1: What Composer's `deposit_adjusted_series` means is documented by name only (the client's own notes mark its semantics unverified). R04 therefore says only that Composer names it so and that the recommender uses it instead of the raw series. Settle by a captured real response and Composer's documentation; until then the row stays worded as a label.
- KU-2: removed: settled at the ref (`static/ai_advisor.js:1294-1307` sends the input; `advisors/strategy_builder_engine.py:412-414` uses it when it is not empty).
- KU-3: The density thresholds (240 by 80 px; 320 px for the Retirement pair; 90 percent figure span) are set from the audit's measurements on one production copy (2026-10-31, market closed). Production states with longer names or more rows may create regions the seeded tests do not; the PM's live check at 1440 is where this surfaces.
- KU-4: The schedule times in existing copy (03:00 for Market Prism, 03:45 UTC for the recommender) are scheduler literals and host-clock dependent; this plan adds no new clock time and does not change those lines.
- KU-6: The Settings save refuses every credential key with a 400 by design (`app.py:9708-9729`) while the Credentials fields stay on the page. The backend is unchanged (PM ruling G-29); S11, S12r and S12s now say the dashboard does not save credentials, and S-3.17 pins the refusal as it is.
- KU-7: The exit named "VWAP bleed" (and "VWAP_BLEED_MULTIPLIER", "VWAP_BLEED_TICKS") never reads the volume-weighted average price: it counts checks on which the symphony's own return sits at or below a volatility-scaled loss line (`math_engine.py:982-990`, `:1035-1043`). D29d states the mechanism and keeps the name; renaming the exit is out of scope (PM ruling) and is a follow-up.
- KU-5: The suggestion card's confidence is read as the model's own field (`ai_advisor.py:251`); if a later change derives it statistically, O07a becomes false. The row cites the field.

## Open Questions
| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | Is the shared header (status words, clock, bell, account chip, mode pill, Force run now) in scope for descriptions and definitions? | AC-1, AC-2 | no: the header is not one of the surfaces G-5 names; it becomes a follow-up (NB-2, NB-18) | overridden by the PM (G-27, D-12): the header's mode pill, engine state word, account selector, Force run now and Emergency Liquidate get definitions only; the rest stays out (NB-2) |
| Q-2 | When a stored tenure review exists, does R22 name the review's `threshold_in_effect` or the configured value? | AC-4 | the stored review's value, so the line matches the verdicts shown; the configured value only when no review exists | default approved by the PM, [PM-ASSUMED] |
| Q-3 | removed: answered by the PM ruling G-22 (D-1, D-2, D-3, D-9) | plan | none | ruled by the PM |
| Q-4 | Claude Design is recorded as the design system of record for new screens. Does this layout change need a Claude Design brief? | AC-6 | no: it creates no new screen and reuses the restyle's primitives, and the restyle itself was built in-team; the ux-expert judges the result | default approved by the PM, [PM-ASSUMED] |
| Q-5 | May the display text the server builds for the page (the triggered verdict on first paint, the Blocked tile text) and the template context change, given G-16 names templates, CSS and static JS only? | AC-3, AC-4, AC-5 | yes, limited to that display text and the AC-4 values; no route, JSON body or stored value changes (D-6) | default approved by the PM, [PM-ASSUMED] |
| Q-6 | Should a verdict gap that prints as 0.0 read "Exit matched holding" instead of "Exit trailed holding by 0.0 pts"? | AC-3 | yes | default approved by the PM, [PM-ASSUMED] |
| Q-7 | May the dollar-saved panel's 160 px height pin grow by its added description and disclosure summary lines? | AC-6 | yes | default approved by the PM, [PM-ASSUMED] |
| Q-9 | removed: ruled by the PM (D-11): Sensitive stays none | plan | none | ruled by the PM |
| Q-8 | Do each Advisor tab, the detail panel and the settings modal count as their own page for "first appearance"? | AC-1, AC-2 | yes: each hides the others, so a definition elsewhere is out of sight (D-4) | default approved by the PM, [PM-ASSUMED] |

## Decisions
| ID | Decision | Rationale |
|---|---|---|
| D-1 | (PM ruling G-22) Series names: Bot and If held on every page; Performance's "Planet Stopper-exited (shadow)" and "Live (if held)" become "Bot (shadow)" and "If held", keeping the shadow marker as honesty text; the dashboard legend becomes "Bot (dry-run)" or "Bot (live)" | The audit found the same two series named differently on two pages; one definition cannot fix a naming split |
| D-2 | (PM ruling G-22) History's "Triggers" becomes "Exits" | The dashboard counts the same events as exits |
| D-3 | (PM ruling G-22) Exit reason names: Take-profit, Trailing stop, VWAP breakdown, VWAP bleed, on the dashboard pills and on History | The pills and History named the same reasons differently; "VWAP" alone was ambiguous beside "VWAP defense" |
| D-4 | Each Advisor tab, the detail panel and the settings modal are hosts of their own for first appearance | Each hides the others, so a definition elsewhere is never on screen with the term |
| D-5 | Placement: a term that names what a section is about is defined in that section's visible description (V); every other term goes in the section's closed "What the terms mean" disclosure (D); no term relies on a `title` alone | A `title` never shows on a phone (G-6); a closed disclosure keeps the page dense (G-13) and is one tap on a phone |
| D-6 | Python changes are limited to the display text the server builds for the page (a triggered card's verdict on first paint, the Blocked tile's detail) and the template context AC-4 needs; Gate 2 names the code | Two of the labels G-20 names are built server-side; G-16's intent is that no contract or computation changes, and both reach only template context, never a JSON body |
| D-8 | Each tab dot's `title` meaning is carried visibly: Overview by O01, Correlations by C01 ("this tab only measures"), Asset Swaps by W01 and its advise-only banner, Logic Changes by L01 and its FDR banner, Chat by T02 ("Chat explains and changes nothing"), Strategy Builder by its risk banner, Frontrunner Builder by its risk banner and X05; Retirement has no dot | PM ruling G-23 |
| D-9 | Further one-name renames under G-22: max drawdown is "Max DD" everywhere (NM1, NM2); a single exit's return minus its held return is "exit alpha" (NM3, with History's "exit alpha"); the bot's path is "Bot" (NM5); the session high of the symphony's own return is "Peak", because after an exit it follows the held return and is no longer the bot's (NM4); trading cost is "trading costs" everywhere (D03r, F21, F22r, F24); the open-market held line is "If held · live" (NM6); Performance's share of up days becomes "Up days", so "Win rate" means only History's share of exits ahead of holding (NM7) | PM ruling G-22, applied to every name the audit and this plan found used for one concept on two hosts |
| D-10 | Same-title frontrunner cards are told apart by the proposal id each card already carries (`templates/ai_advisor.html:2940`), not by the overlay summary, because a legacy card may have no recorded overlay | PM ruling G-26 |
| D-11 | (PM ruling) Sensitive stays none: the change is display-only and drives no decision path; the risk that a false explanation steers a real-money retire or trade call is covered by the truth pass in the Gate-1 audit and by qa's copy check at review | PM ruling on Q-9 |
| D-12 | (PM ruling G-27) The header's five items are defined by `title` or accessible description (placement A), with no visible change to the header and its look pins untouched; the engine state's definition follows the state static/chrome.js writes | They are terms and controls, not sections, and the header's look is pinned |
| D-13 | (PM ruling G-28) The detail panel's View logs, Go to cash and Edit variables buttons, which no script handles, are not rendered; Edit variables is included because it has no handler either (`templates/index.html:2132`; no reference in `static/`) | A control that does nothing misleads; removing an inert control changes no working behavior |
| D-14 | (PM ruling G-29) The Credentials copy states that the dashboard does not save credentials; the refusal itself is unchanged | Copy must be true of the code; the backend refuses by design |
| D-15 | (PM ruling under the operator's full-autonomy directive, [PM-ASSUMED]) The plan at 6e0eea80 is approved as written, together with the Gate-2 approach `feature-plans/approach/page-explainers-and-density.md`; every open question takes its recorded ruling or default and every Not Building entry is approved | The operator runs this project fully autonomously, and the PM approves on the operator's behalf |
| D-7 | The empty-region size is 240 by 80 px | It catches the audit's findings A1 to A11 and A13 (each at least 250 by 80 px) and leaves A12, A14 and the 35 px rows of A15, which the audit ranked lowest (NB-10) |

## Docs to Update
- `docs/generated/static_index_js.md`, `docs/generated/static_ai_advisor_js.md`, `docs/generated/static_performance_js.md`, `docs/generated/static_history_js.md`, `docs/generated/app.md` (the two display helpers), `docs/generated/ux_overhaul.md` and `docs/generated/INDEX.md`.
- `README.md` if it quotes any reworded label.
- A `DECISIONS.md` entry, minted at ship by the doc writer.
- A drafted `.claude/CLAUDE.md` Key Files note for `templates/index.html`, `templates/ai_advisor.html` and `static/history.js`, for PM approval.

## Follow-Ups
- NB-2 and NB-18: definitions for the header's wordmark tag, clock, countdown, bell and Tweaks button; tracked as a backlog line in `feature-plans/BACKLOG.md`.
- KU-7: the "VWAP bleed" exit's name does not match its mechanism; a rename is its own plan; backlog line.
