# Approval packet: Page explainers and denser layouts

## What is being built

Every page and Advisor tab tells the operator, in plain visible words under each heading, what each section is and why it matters, defines each jargon term where it first appears, and stops leaving large empty regions at 1440 px, without changing any figure, control or contract.
- A visible description under every section heading and at the top of every page and tab, from the exact copy in Copy table A (AC-1).
- A plain definition for each jargon term at its first appearance on each page, visible or in a closed "What the terms mean" disclosure, never in a tooltip alone, from Copy table B (AC-2).
- Jargon labels and status words reworded, with the same text from the server render and from the script that refreshes it, per Copy table C (AC-3).
- Every threshold, window or count the copy names rendered from its configured value, never a literal (AC-4).
- No figure, control, banner, route, JSON body or honesty text changes (AC-5).
- At 1440 px no panel or card holds an empty region of 240 by 80 px or more, the named short sections share a row, and figure rows use their width (AC-6).
- At 375 px each page grows only by the height of the added copy and never scrolls sideways, and the Advisor tab strip shows that more tabs exist (AC-7).
- Autotune status text shared by every row is stated once, and frontrunner cards for one symphony carry their proposal number in the title (AC-8).

## Acceptance criteria and scenarios

### AC-1: Every page, tab and section says what it is and why it matters
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

Negative coverage: N1 S-1.17, S-1.18 | N2 n/a: a description refuses nothing and no route is added | N3 S-1.15, S-1.16 | N4 n/a: every host sits behind the unchanged global auth hook, and a description carries no data (pinned by S-5.8) | N5 S-1.21 | N6 S-1.20 | N7 S-1.19, S-1.23 | N8 S-1.22

### AC-2: Each jargon term is defined where it first appears on a page
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

Negative coverage: N1 S-2.11, S-2.12 | N2 n/a: a definition refuses nothing | N3 S-2.15 | N4 n/a: the hosts sit behind the unchanged auth hook (S-5.8) | N5 S-2.14 | N6 S-2.17, S-2.18 | N7 S-2.13, S-2.20 | N8 S-2.16

### AC-3: Jargon labels are reworded the same way by the server and the scripts
Rule: When a label in Copy table C renders, the host shall show its new text in place of its old text, identically whether the server rendered it or a script wrote it on a poll or after a run, and the old text shall no longer appear on that host.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | a triggered symphony whose exit return is 1.24 points above its held return | `/` first paints | its verdict reads "Exit beat holding by 1.2%" followed by its basis label |
| S-3.2 | B | the same symphony | the poll rewrites the card (static/index.js) | the verdict text is identical to the first paint |
| S-3.3 | B | a triggered symphony whose exit return is 0.42 points below its held return | first paint and poll | both read "Exit trailed holding by 0.4%" |
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

Negative coverage: N1 S-3.4 | N2 n/a: no label refuses anything | N3 S-3.5, S-3.14 | N4 n/a: unchanged auth hook (S-5.8) | N5 S-3.10 | N6 S-3.11 | N7 S-3.7, S-3.15, S-3.17 | N8 n/a: a typed credential is an unsaved edit, and S-3.16 shows its status text is the new one

### AC-4: Copy that names a configured value shows the configured value
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

Negative coverage: N1 S-4.3 | N2 n/a: no input is judged | N3 S-4.9 | N4 n/a: the values are non-secret module constants and two non-secret environment keys; no credential reaches a page | N5 n/a: each render reads the authority once and a refresh changes no copy | N6 S-4.9 | N7 S-4.8, S-4.10 | N8 n/a: no slot sits on an editable form

### AC-5: No figure, control, banner, contract or honesty text changes
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

Negative coverage: N1 n/a: this criterion judges no input | N2 S-5.11 | N3 S-5.10 | N4 S-5.8 | N5 n/a: no action is added; repeated actions keep their existing guards, pinned by their existing tests | N6 n/a: no dependency is added; existing failure states are pinned by S-5.4 | N7 S-5.2, S-5.3, S-5.4, S-5.5, S-5.6, S-5.7, S-5.9, S-5.13, S-5.15 | N8 S-5.12

### AC-6: At 1440 px no panel holds a large empty region
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

Negative coverage: N1 S-6.4, S-6.10 | N2 n/a: layout refuses nothing | N3 S-6.11 | N4 n/a: unchanged auth hook (S-5.8) | N5 S-6.12 | N6 S-6.13 | N7 S-6.8, S-6.9, S-6.14 | N8 n/a: the layout does not depend on an edit state; the modal and the Settings form keep their ref behavior (S-1.22)

### AC-7: At 375 px a page grows only by the added copy
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

Negative coverage: N1 S-7.4, S-7.8 | N2 n/a: layout refuses nothing | N3 S-7.5 | N4 n/a: unchanged auth hook (S-5.8) | N5 S-7.6, S-7.9 | N6 n/a: a failed fetch shows the ref's error state, which this change does not resize (S-1.20) | N7 S-7.2, S-7.3, S-7.10 | N8 n/a: no edit state changes the height

### AC-8: Repeated autotune text is stated once and frontrunner cards are told apart
Rule: When the Recent autotune runs table renders two or more rows and a column's cell text is identical in every row, the panel shall print that column once, above the rows, in one line that begins "Same for every run: " and lists each such column as its header followed by its cell text, joined by "; ", and the rows shall leave those cells out; and when the Frontrunner Builder tab renders pending cards, each card's title shall read its incumbent's name (or its hash where no name resolves, as today) followed by " | proposal #" and the proposal's id.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-8.1 | B | 12 autotune rows whose Outcome cell reads "Fallback" with "Frozen-eval not evaluated", and whose score and t-stat cells read "N/A", in every row | the Overview renders at 1440 and at 375 (built by static/ai_advisor.js) | one line reads "Same for every run: Outcome Fallback, Frozen-eval not evaluated; Best CRRA-EU score N/A; Sel t-stat N/A" and no row repeats those cells; each row keeps its symphony, run time and streak |
| S-8.2 | N1 | rows whose Outcome differs in one row | the table renders | Outcome stays in every row; only the columns identical in every row move to the line |
| S-8.3 | N1 | exactly one row | the table renders | no "Same for every run" line; the row shows every cell as at the ref |
| S-8.4 | N3 | no autotune runs | the panel renders | its empty state is unchanged and no line shows |
| S-8.5 | N7 | any table state | the panel renders | every value a row showed at the ref is still on screen, in its row or in the line; the older-runs disclosure keeps its own rows unchanged |
| S-8.6 | N5 | the 15 s refresh brings a run whose Outcome differs | the table re-renders | Outcome returns to every row and leaves the line |
| S-8.7 | N6 | the autotune refresh request fails | the table is read | the last rendered table and line stay, as the table does at the ref |
| S-8.8 | B | five pending proposals for one incumbent with ids 31 to 35 | the Frontrunner tab renders | the titles read "{name} | proposal #31" to "{name} | proposal #35", all different |
| S-8.9 | N3 | an incumbent whose name does not resolve | the tab renders | the title reads its hash followed by " | proposal #" and the id |
| S-8.10 | N7 | each card | it renders | its `title` attribute (the hash), `data-testid` values, Approve and Reject controls and every figure are unchanged |

Negative coverage: N1 S-8.2, S-8.3 | N2 n/a: nothing is refused | N3 S-8.4, S-8.9 | N4 n/a: unchanged auth hook (S-5.8) | N5 S-8.6 | N6 S-8.7 | N7 S-8.5, S-8.10 | N8 n/a: no edit state on either surface

## Rule-by-entry-point matrix

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

## Narrowed mappings

R-2: Large empty space is removed at 1440 px; maps to AC-6; left out: regions smaller than 240 by 80 px stay allowed, so A12, A14 and the 35 px button row of A15 are not required to change (NB-10)
R-3: Short sections sit side by side instead of stacked full width; maps to AC-6; left out: required for the two pairs the audit found stacked (CVaR with Managed sleeves; the two Retirement subsections), not for every short section on every page
R-7: Every page and section says, in plain words, what it is and why the operator should care; maps to AC-1; left out: the shared header is not covered (NB-2, Q-1)
R-9: Each jargon term gets a plain definition at its first appearance on a page, by a stated placement rule; maps to AC-2, NB-2, NB-3, NB-4; left out: the audit's terms less the header's wordmark tag, clock, countdown, bell and Tweaks button, model-written lens text and text a route returns in JSON
R-15: The change is render-only: no route, JSON shape, database, scheduler or engine change; engine files have zero diff; maps to AC-5; left out: server-built display text for two labels and the template context may change (D-6, Q-5)

## User tasks

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
| 2 | Open "What the terms mean" in Active | a click | Each status word, Bot | frozen, the ring and Cum are defined | S-2.1 |
| 3 | Look at a triggered card | none | Its verdict reads "Exit beat holding by 1.2%" or "Exit trailed holding by 0.4%" with its basis, on first paint and after the next poll | S-3.1, S-3.2 |

### T-3: Scan a page at 1440 px
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Open each page and tab at 1440 by 900 | none | No panel or card shows an empty region of 240 by 80 px or more; CVaR and Managed sleeves share a row; the Retirement subsections share a row while both are short | S-6.1, S-6.2, S-6.3 |

## Open questions

| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | Is the shared header (status words, clock, bell, account chip, mode pill, Force run now) in scope for descriptions and definitions? | AC-1, AC-2 | no: the header is not one of the surfaces G-5 names; it becomes a follow-up (NB-2, NB-18) | overridden by the PM (G-27, D-12): the header's mode pill, engine state word, account selector, Force run now and Emergency Liquidate get definitions only; the rest stays out (NB-2) |
| Q-2 | When a stored tenure review exists, does R22 name the review's `threshold_in_effect` or the configured value? | AC-4 | the stored review's value, so the line matches the verdicts shown; the configured value only when no review exists | default approved by the PM, [PM-ASSUMED] |
| Q-3 | removed: answered by the PM ruling G-22 (D-1, D-2, D-3, D-9) | plan | none | ruled by the PM |
| Q-4 | Claude Design is recorded as the design system of record for new screens. Does this layout change need a Claude Design brief? | AC-6 | no: it creates no new screen and reuses the restyle's primitives, and the restyle itself was built in-team; the ux-expert judges the result | default approved by the PM, [PM-ASSUMED] |
| Q-5 | May the display text the server builds for the page (the triggered verdict on first paint, the Blocked tile text) and the template context change, given G-16 names templates, CSS and static JS only? | AC-3, AC-4, AC-5 | yes, limited to that display text and the AC-4 values; no route, JSON body or stored value changes (D-6) | default approved by the PM, [PM-ASSUMED] |
| Q-6 | Should a verdict gap that prints as 0.0 read "Exit matched holding" instead of "Exit trailed holding by 0.0%"? | AC-3 | yes | default approved by the PM, [PM-ASSUMED] |
| Q-7 | May the dollar-saved panel's 160 px height pin grow by its added description and disclosure summary lines? | AC-6 | yes | default approved by the PM, [PM-ASSUMED] |
| Q-9 | removed: ruled by the PM (D-11): Sensitive stays none | plan | none | ruled by the PM |
| Q-8 | Do each Advisor tab, the detail panel and the settings modal count as their own page for "first appearance"? | AC-1, AC-2 | yes: each hides the others, so a definition elsewhere is out of sight (D-4) | default approved by the PM, [PM-ASSUMED] |
