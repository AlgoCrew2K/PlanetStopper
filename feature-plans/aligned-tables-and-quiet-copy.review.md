# Approval packet: Aligned tables and quiet copy

## What is being built

Every table on every in-scope page lines up under its own header, and every page says each thing once in one short sentence per section with all term definitions in one Glossary at its end, for the operator reading the dashboard, Performance, History, Settings and the eight AI Advisor tabs, without changing any figure, control or contract.
- Tables align: one header row above each column, cells under it, numbers right-aligned, repeated cards with each field at the same x (AC-1).
- Nothing is moved to fill space: no text beside the Advisor tab bar or at a toolbar's far end, columns and figure rows at the width their content needs, the Retirement pointer out of the Overview toolbar; the earlier 240 by 80 px empty-region criterion is withdrawn (AC-2).
- One description sentence of at most 25 words per section, from Copy table A, and no hint, caption, banner or run note that restates one, from Copy table X (AC-3).
- One closed Glossary per page, Advisor tab, detail panel and settings modal holding every definition, from Table G; no per-section disclosures; labels that name a term carry its definition as a title (AC-4).
- Status lines read as one plain statement: the autotune line, columns that are N/A in every row, the numeric verification line and the older-runs summary (AC-5).
- Tenure cards list each missing item once and state a count and a reason instead of naming symphonies a comparison could not decide (AC-6).
- No figure, control, route, JSON body, banner meaning, honesty text, engine or database change (AC-7).
- Below 1001 px the stacked forms stay, and the copy and Glossary rules hold at 390 px (AC-8).
- The PM's deploy-gate visual check at 1920, 1440 and 390 px (AC-9).

## Acceptance criteria and scenarios

### AC-1: Every table lines up under one header
Rule: When an in-scope host renders at each width of Table W, every Table T group of kind table shall show exactly one header row, above all its body rows, whose body rows neither overlap vertically nor sit side by side, and in each of its columns the bounding box of the text or control inside every cell, cell padding excluded, header included, shall share its left edge (a text column) or its right edge (a numeric column, D-4) within 2 px; and in every Table T group of kind repeated fields, each named field's bounding box shall start (or, for a numeric field, end) at the same offset from its row's or card's left edge on every row or card, within 2 px.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-1.1 | B | the production-shape state of the Definition of Done | each host renders at 1920 and 2560 | every Table T group meets the rule |
| S-1.2 | B | the same state | each host renders at 1120, 1290, 1350, 1400, 1420 and 1440 | every Table T group meets the rule; the risk metrics table is one column group and the turnover numbers end at their right edge in every band (EV-X1, EV-P2) |
| S-1.3 | N1 | the same state, at 1001 px, the narrowest width the rule covers | each host renders | every Table T group meets the rule |
| S-1.4 | N1 | the same state at 1000 px | each host renders | each table shows the stacked-row form it shows at the ref at 1000 px (AC-8) |
| S-1.5 | B | 12 autotune rows with one shared run time and outcome and N/A scores, and 38 older runs (rows the page script writes on each refresh) | the Overview renders | the main table shows one header row naming Symphony and Runs without adopting a tuned config, then 12 body rows one under another, each as wide as the table; the older-runs table, opened, does the same |
| S-1.6 | B | Performance with all 10 risk metric rows | `/performance` renders | one header row (Metric, If held, Bot (shadow), Delta) above 10 body rows in one column group; no header cell repeats |
| S-1.7 | B | 11 exit turnover rows | `/performance` renders | the 30d, 90d, 365d and drag cells and their headers end at their column's right edge; Symphony starts at its left edge |
| S-1.8 | B | 11 tenure cards, one of them not yet eligible | the Retirement tab renders | the six evidence cells keep the ref's visible order (Performance, Composite score, Backtest, Marginal contribution, Long VIX hedge, Redundancy) and each starts at the same x on every card, including the not-yet-eligible card |
| S-1.9 | B | five pending frontrunner proposals and three stored Strategy Builder survivors | each tab renders | each card's figures table has one header row, its figure columns right-aligned, and each column starts at the same x on every card of that tab |
| S-1.10 | B | five observation producers and the Retirement row | the Overview renders | each line's label, verdict, time, count and last cell start at the same offset on every line, the Retirement row included; the count ends at one right edge |
| S-1.11 | B | History with exits of all four reasons | `/history` renders | the by-reason and exits tables meet the rule; Detail and every figure column are right-aligned, header included |
| S-1.12 | B | the seeded dashboard with 11 Active cards | `/` renders | on the comparison rows the label, Bot, Held and \u03b1 fields each sit at one offset across the four rows; on the cards each ledger column (session, Cum, Max DD) starts at the same offset on every card |
| S-1.20 | B | eight pending Frontrunner proposals in the tab's card grid, and the Algorithm parameters cards on Settings | each host renders | each card field (title, chips, figures table, actions; name, input row, help) starts at the same offset from its card's left edge on every card |
| S-1.21 | B | the detail panel of a card, and the settings modal of a symphony | each opens | the Risk math rows' label, value and bar, the event rows' time and text, and the modal's parameter rows' key and value (value right edge) each sit at one offset on every row |
| S-1.19 | B | a swap result and a logic-change result | each tab renders its result | in each Baseline and Variant column every label starts at one x and every value ends at one x |
| S-1.13 | N1 | a symphony name long enough to wrap to two lines | each host renders | the name wraps inside its own cell; the header and the other columns stay aligned |
| S-1.14 | N3 | no autotune runs, no turnover rows, no tenure review and no proposals | each host renders | each empty state shows as at the ref; no table renders a header row with no body rows unless it does so at the ref |
| S-1.15 | N5 | the dashboard poll and the Overview 15 s refresh each run three times | the hosts are measured again | the rule still holds; no header row has been added twice |
| S-1.16 | N6 | `/api/autotune-runs` fails on a refresh | the Overview is read | the last rendered table and its notice stay, still aligned |
| S-1.17 | N7 | every Table T group | each host renders | every cell's text and every figure value is as at the ref (AC-7), apart from the N/A columns AC-5 removes |
| S-1.18 | N8 | an observation history or a tenure evidence disclosure is open | the hosts are measured | the other lines and cards keep their field positions; the open one's fields keep theirs |

Negative coverage: N1 S-1.3, S-1.4, S-1.13 | N2 n/a: layout refuses nothing | N3 S-1.14 | N4 n/a: every host sits behind the unchanged global auth hook, pinned by S-7.8 | N5 S-1.15 | N6 S-1.16 | N7 S-1.17 | N8 S-1.18

### AC-2: Nothing is moved to fill space
Rule: When an in-scope host renders at each width of Table W, the Advisor navigation panel shall hold only the tab bar; in each row of Table L every element that is not a control (D-7) shall start no more than 24 px after the right edge of the element before it on the same line, or begin a new line at the row's left edge, while a control may sit at the row's end; in each TT-1, TT-3, TT-4, TT-5, TT-7, TT-8, TT-9 and TT-14 group whose content is narrower than its panel, the space between the right edge of the widest text in one column and the left edge of the nearest text in the next column shall be at most 48 px; in each line of Table F the gaps between adjacent items shall be equal within 2 px and each at most 48 px, and no figure's label or value shall break across lines at 1440 and wider; the two Retirement subsections shall each take the panel's full content width, one above the other, whatever their heights; and the Retirement pointer shall leave the Overview toolbar and render only as the last row of the Advisor observations list: label "Retirement", verdict cell the pointer text without its leading "Retirement: ", empty time and count cells, and in the last cell a link "Open tab" that opens the Retirement tab, with no request added.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | `/ai-advisor` at 1920 and 1440 | the page renders | the navigation panel's only rendered child is the tab bar; no text, disclosure or link sits to the right of the last tab or above the tab bar |
| S-2.2 | B | the production-shape state | each Table L row renders at each width of the rule | every non-control element meets the 24 px rule; History's and Performance's data stamps sit directly after the element before them or on their own line at the left |
| S-2.3 | B | the Overview | it renders | the toolbar row holds the symphony picker, Run, the error slot and the suggestions placeholder or results, and nothing else |
| S-2.4 | B | a stored ok retirement run at 2026-11-09 22:51 ET with 0 recommendations and 0 pending | the Overview renders | the observations list's last row reads "Retirement", then "last run 2026-11-09 22:51 ET, 0 recommendations, 0 pending", then empty time and count cells, then "Open tab"; no other Retirement pointer is on the Overview |
| S-2.5 | B | the Retirement row's "Open tab" link | it is chosen by click and by Enter | the Retirement tab opens; the page makes no request it does not make at the ref |
| S-2.6 | B | the production-shape state | Performance, History and the Overview render at 1920 | in TT-3, TT-4, TT-5, TT-7, TT-8 and TT-9 no gap between adjacent columns' text exceeds 48 px; in TT-1 the gap between the label and the Bot figure is at most 48 px |
| S-2.7 | B | the seeded dashboard | `/` renders at 1920 and 1440 | Tracked, Armed and Triggered sit with equal gaps of at most 48 px; the four dollar figures sit on one line each with equal gaps of at most 48 px |
| S-2.8 | B | Performance and History | each renders at 1920 and 1440 | the headline strip and the History hero figures sit with equal gaps of at most 48 px and no label or value wraps |
| S-2.9 | B | a stored ok retirement run | the Retirement tab renders at 1920 and 1440 | the five tiles' values start at tops within 2 px of each other on one line with equal gaps of at most 48 px; the Blocked breakdown sits on its own line below the tiles |
| S-2.10 | B | a stored tenure review | the Retirement tab renders | the tenure run line's items sit with equal gaps of at most 48 px, starting at the row's left edge |
| S-2.11 | N3 | no retirement run | the Overview renders | the Retirement row's verdict cell reads "not run yet (use Run recommender)" |
| S-2.12 | N3 | a failed latest retirement run | the Overview renders | the verdict cell names the failed state and its time, as the ref's pointer does |
| S-2.20 | N3 | no observations, and observations that could not be loaded | the Overview renders | the empty or unavailable line shows as at the ref and the Retirement row follows it |
| S-2.13 | N5 | the dashboard poll runs and the window picker moves from 30d to 1y and back | `/` is measured again | S-2.7 still holds |
| S-2.14 | N6 | `/api/guard-alpha-summary` answers 500 | `/` shows its dashes | the dollar figures keep equal gaps of at most 48 px |
| S-2.15 | N7 | every host | it renders | no rule asserts an empty-region bound; the earlier 240 by 80 px tests are removed, not loosened |
| S-2.16 | N7 | every host at 1920 and 1440 | it renders | no description, note or stamp from one section is placed inside another section's box |
| S-2.18 | B | a stored retirement run and a stored tenure review whose subsections are each 320 px tall or less | the Retirement tab renders at 1920 | Correlated pairs and Symphony tenure review each take the panel's full content width, Correlated pairs above (EV-R2, EV-R3; the ref pairs them side by side, `static/ai_advisor.js:1156-1160`) |
| S-2.19 | B | Market Prism, a sleeve card and a tenure card | each renders at 1920 | the Prism as-of stamp starts within 24 px of the item before it; a sleeve line's items and a tenure card's head and lifecycle items sit with gaps of at most 48 px (`templates/ai_advisor.html:847`, `:1112-1113`; `templates/index.html:1158`) |
| S-2.17 | N8 | a symphony picked but Run not yet pressed, and a Run in progress | the Overview toolbar is measured | S-2.3 holds in both states |

Negative coverage: N1 S-2.2 (1001 px boundary) | N2 n/a: layout refuses nothing | N3 S-2.11, S-2.12, S-2.20 | N4 n/a: unchanged auth hook (S-7.8) | N5 S-2.13 | N6 S-2.14 | N7 S-2.15, S-2.16 | N8 S-2.17

### AC-3: One short description per section, said once
Rule: When an in-scope host renders, each section of Copy table A shall show, directly under its heading (or at the top of a section or panel with no heading), exactly the row's new text as its only description, or no description where the row reads none; each new text shall be one sentence of at most 25 words with the slot filled or its fallback phrase in place; no element of the Advisor navigation panel shall carry a description; each row of Copy table X shall show its new text or be absent as the row says; and the old text of every removed or shortened row shall be absent from the host.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | the production-shape state | each host renders | every Copy table A row for that host shows its exact new text, visible, once, directly under its heading |
| S-3.2 | B | the same state | each host renders | every Copy table X row shows its exact new text, or is absent where it reads removed |
| S-3.3 | N7 | every row whose disposition is removed or shortened | each host renders, all eight Advisor tab panels in the page | its old text, and each old sentence the new text drops, appears in no section body, hidden panels included; a sentence Table G2 moves into the Glossary appears there once, word for word apart from the term's own name and a leading "is" or "are", which the line may drop after its "Term: " prefix (G-26, G-33) |
| S-3.4 | N1 | every new text in Copy table A, with each slot filled by its longest authority value and, in turn, by its fallback phrase | the texts are counted | each is one sentence of at most 25 words; none contains "!", an emoji, U+2014 or U+2013 |
| S-3.5 | B | `/ai-advisor` | it renders | the navigation panel holds no `data-explainer` element and no disclosure |
| S-3.6 | B | the Strategy Builder tab | it renders | the risk banner reads exactly the CX-9 text; the model line and B00 show; no run note shows under Propose new strategies |
| S-3.7 | N3 | no API key configured | the Asset Swaps, Logic Changes and Strategy Builder tabs are chosen | W01, L01 and B00 still show; the unavailable state shows as at the ref |
| S-3.8 | N3 | sections that do not render (CVaR hidden, no frontrunner proposals, no survivors, no backtest failures) | each host renders | their descriptions are absent with them |
| S-3.9 | N3 | each slot's authority fails in turn | the host renders | the row's fallback phrase replaces the slot phrase and the sentence still has at most 25 words |
| S-3.10 | N5 | the dashboard poll runs three times; a tab is chosen, left and chosen again | the page is read | each description shows once per section |
| S-3.11 | N6 | a host's script fails to load | the server render is read | every template-built row shows its new text |
| S-3.12 | N7 | every banner other than the Strategy Builder banner: the crisis caveat, the advise-only banner, the logic-change FDR banner, the Frontrunner risk banner, the Settings warnings and maintenance note, the two CVaR notes and the fleet banner | each renders | each is byte-identical to the ref |
| S-3.13 | N8 | an unsaved edit on `/settings` or a pending live toggle in the modal | the operator switches section or reads the modal | the descriptions are the Copy table A text and the pending edit is kept |
| S-3.14 | B | the settings modal opens (the page script writes it on open) | it renders | M04, M05, M06 and M07 show their exact text |

Negative coverage: N1 S-3.4 | N2 n/a: a description refuses nothing | N3 S-3.7, S-3.8, S-3.9 | N4 n/a: unchanged auth hook (S-7.8) | N5 S-3.10 | N6 S-3.11 | N7 S-3.3, S-3.12 | N8 S-3.13

### AC-4: Every definition is in one Glossary at the end of its host
Rule: When an in-scope host renders, it shall carry exactly one closed disclosure whose summary reads "Glossary", as the last element of the host (Table G), holding one paragraph per Table G line for that host in Table G order with exactly the text Table G gives; no host shall carry a "What the terms mean" disclosure; and each label element (a table header cell, a figure or tile label, a tenure evidence heading, a comparison row label, or an element whose whole text is a term followed by ":") whose text, trimmed and without a trailing ":", equals the lead term of a Glossary line of that host (the text before the line's first ": "), and each element Table G3 lists, shall carry as its `title` the full text of that Glossary line, unless it carries a `title` at the ref.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | B | the production-shape state | each host renders and its Glossary is opened | the lines are exactly Table G's for that host, in order |
| S-4.2 | B | each host | it renders | its Glossary is the last element of the page content, the tab panel, the detail panel or the modal body, and it is closed |
| S-4.3 | N7 | every host, all eight tab panels | it renders | no `summary` reads "What the terms mean"; no element carries `data-terms` other than the Glossary |
| S-4.4 | N1 | a Glossary | it is reached by keyboard | it has the accessible name "Glossary", opens and closes with Enter and Space, and is closed on load |
| S-4.5 | B | the Table G2 lines | their hosts render | each changed and new line reads exactly its Table G2 text, with slots filled per the Values table |
| S-4.6 | B | the Overview Glossary | it is opened | O31a, O31b and O31c read the first three sentences of `templates/ai_advisor.html:1809-1815` word for word after their "Term: " prefix, each without its own leading term name and, for O31a and O31c, without the "is" that follows it (G-33) |
| S-4.7 | N7 | every unchanged Table G line | its host renders | its text equals the `new_text` of its row in `tests/fixtures/page_explainers/copy.json` at the ref, with slots filled |
| S-4.8 | B | each Table G3 element and each label element whose text matches a lead term, for example the recommendation card's "Composite gap:" (`templates/ai_advisor.html:2275`, line R18) | its host renders, the page scripts' rows included (TG3-5, TG3-6, TG3-14) | its `title` equals the full text of its Glossary line |
| S-4.9 | N7 | each element that carries a `title` at the ref (the info marks, the score header, the tab dots, the symphony-name titles, R12's button) | its host renders | its `title` is unchanged |
| S-4.10 | N5 | a Glossary opened on `/`, the Overview or Performance | the page's poll or panel refresh runs | it stays open and its lines are unchanged |
| S-4.11 | N3 | a host whose sections do not all render (CVaR hidden, no proposals, no tenure review) | the host renders | its Glossary still holds every Table G line for the host |
| S-4.12 | N6 | a host's script fails to load | the server render is read | every template-built Glossary is present, closed, with its lines |
| S-4.13 | N8 | a pending live toggle in the settings modal | the modal Glossary is opened and closed | the pending toggle and its confirm dialog behave as at the ref |
| S-4.14 | N6 | the modal's settings fetch fails | the error state shows | the modal shows its error text as at the ref and no Glossary |
| S-4.15 | N7 | the header on every page | it renders | the header carries its definitions as `title` or accessible description exactly as at the ref (copy.json rows H02, H07, H08, H09, H10) and gains no Glossary |

Negative coverage: N1 S-4.4 | N2 n/a: a definition refuses nothing | N3 S-4.11 | N4 n/a: unchanged auth hook (S-7.8) | N5 S-4.10 | N6 S-4.12, S-4.14 | N7 S-4.3, S-4.7, S-4.9, S-4.15 | N8 S-4.13

### AC-5: Status lines read as one plain statement
Rule: When the Overview renders its autotune runs, its numeric verification or its older runs, each status line shall be one plain statement in which no count appears twice: the autotune runs shall show the AC-5 sentence in place of the "Same for every run" line, a score or Sel t-stat column whose cell reads "N/A" in every row of a table shall be absent from that table with its header and named nowhere, and only the run time and outcome columns shall ever be lifted out of the rows; the verification count line shall omit the verified count when the pill states it and omit every zero count; the verification disclosure summary shall read "Show all checks"; and the older-runs summary shall read "{n} older run" or "{n} older runs" followed by " from the {limit} most recent", where {limit} is the route's row limit.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | the production shape: 12 rows, the same run time 2026-11-06 19:08 ET, outcome Fallback with Frozen-eval not evaluated, score N/A and t-stat N/A in every row (rows the page script writes on each refresh) | the Overview renders | the line reads exactly "Latest run 2026-11-06 19:08 ET: every symphony kept its previous settings."; the table shows only Symphony and Runs without adopting a tuned config; the words "N/A", "Best CRRA-EU score", "Sel t-stat" and "Same for every run" appear nowhere in the panel |
| S-5.2 | B | 12 rows sharing a run time, all "Adopted AI" with frozen-eval passed | the Overview renders | the line reads "Latest run {T}: every symphony took new tuned settings (frozen-eval passed)." |
| S-5.3 | B | 12 rows sharing a run time, all "Reset to Global Default" | the Overview renders | the line reads "Latest run {T}: every symphony was reset to the global default settings." |
| S-5.4 | N1 | rows sharing a run time whose outcomes differ | the Overview renders | the line reads "Latest run {T} for every symphony."; the outcome stays in every row |
| S-5.5 | N1 | rows with different run times, all Fallback | the Overview renders | the line reads "Every symphony kept its previous settings in its latest run."; the run time stays in every row |
| S-5.6 | N1 | rows with different run times and outcomes | the Overview renders | no sentence; every column that is not N/A in every row stays |
| S-5.7 | N1 | exactly one row, with N/A score and t-stat | the Overview renders | no sentence; its row shows Symphony, run time, outcome and streak; no score or t-stat column |
| S-5.8 | N1 | 12 rows where one row has a score and the rest read N/A | the Overview renders | the score column shows in every row with its header; the t-stat column follows the rule on its own |
| S-5.9 | N1 | a shared outcome label the mapping does not name, for example "Hold" | the Overview renders | the clause reads "every run's outcome was Hold" |
| S-5.10 | B | 38 older runs with N/A scores | the older-runs disclosure is read and opened | the summary reads "38 older runs from the 50 most recent"; its table has no score or t-stat column; one older run reads "1 older run from the 50 most recent" |
| S-5.11 | B | verdict clean, n_checks 18, n_pass 15, n_unverifiable 3, no flagged or overridden | the Overview renders | the pill reads "15 of 18 verified"; the count line reads exactly "3 unverifiable"; the disclosure summary reads "Show all checks" |
| S-5.12 | N1 | verdict clean, n_checks 18, n_pass 18, all other counts 0 | the Overview renders | the pill reads "clean"; the count line reads "18 verified" |
| S-5.13 | N1 | verdict flags-detected, n_pass 23, n_unverifiable 2, n_flagged 1, n_overridden 0 | the Overview renders | the count line reads "23 verified | 2 unverifiable | 1 flagged" |
| S-5.14 | N1 | verdict overrides-detected, n_pass 23, n_unverifiable 0, n_flagged 0, n_overridden 1 | the Overview renders | the count line reads "23 verified | 1 overridden" |
| S-5.15 | N3 | n_checks 0 (no-numeric-claims) | the Overview renders | no count line, as at the ref |
| S-5.16 | N3 | verdict clean, n_checks absent | the Overview renders | the pill reads "verified count unavailable"; no count line |
| S-5.22 | N3 | verdict clean, n_checks null | the Overview renders | the pill reads "verified count unavailable"; no count line |
| S-5.23 | N3 | verdict clean, n_checks the string "18" | the Overview renders | the pill reads "verified count unavailable"; the count line prints only the other counts that are integers above zero, and nothing when none is |
| S-5.24 | N3 | verdict clean, n_checks 18, n_pass absent | the Overview renders | the pill reads "verified count unavailable"; the count line prints the counts that are integers above zero and no verified count |
| S-5.25 | N3 | verdict clean, n_checks 18, n_pass null | the Overview renders | as S-5.24 |
| S-5.26 | N3 | verdict clean, n_checks 18, n_pass the string "15" | the Overview renders | as S-5.24; the string is not printed |
| S-5.17 | N3 | no autotune runs | the panel renders | "No tuning runs recorded yet." as at the ref, and no sentence |
| S-5.18 | N5 | the 15 s refresh brings a run whose outcome differs | the table re-renders | the sentence changes to the S-5.4 form and the outcome returns to every row |
| S-5.19 | N6 | the autotune refresh fails | the panel is read | the last sentence and table stay, with the ref's notice |
| S-5.20 | N7 | every value a row showed at the ref other than N/A | the panel renders | it is still on screen, in its row or in the sentence |
| S-5.21 | N7 | `GET /api/autotune-runs` and the verification summary | they are requested and read | the route body and the stored row are byte-identical to the ref |

Negative coverage: N1 S-5.4 to S-5.9, S-5.12 to S-5.14 | N2 n/a: no status line refuses anything | N3 S-5.15, S-5.16, S-5.17, S-5.22 to S-5.26 | N4 n/a: unchanged auth hook (S-7.8) | N5 S-5.18 | N6 S-5.19 | N7 S-5.20, S-5.21 | N8 n/a: no edit is held open on the Overview's status lines

### AC-6: Tenure cards state the missing redundancy once, without names
Rule: When the Retirement tab renders a tenure card, its missing line shall name each missing item once; when the redundancy item's stored reason is the undecided-peer list (it begins "comparison decides nothing for: "), its entry in the missing line shall read "redundancy (not decided: too few shared trading days with the other symphonies; {N} are needed)" if N can be read, every listed peer's aligned-day count is below N and no listed peer carries a gate suffix, and "redundancy (no comparison with the other symphonies reached a result)" otherwise, where N is the fewest aligned days the review's redundancy comparison needs, printed from its derivation and never typed as a literal (Authorities Touched); that stored reason shall be shown, word for word, only inside one closed disclosure on the card, directly after the missing line, whose summary reads "Comparisons that decided nothing"; every evidence cell of an item in the missing list shall read "unavailable" or "not applicable" in its summary with no reason, whether or not the item keeps a shown pair, and its "not used in the verdict" line shall carry no reason; the peers on another basis, listed inside the Redundancy cell's disclosure, shall render as one line "not compared: {k} other symphony, bases differ" or "not compared: {k} other symphonies, bases differ"; and the Redundancy cell shall name its shown pair's sibling ("paired with:" and the pair's figures) only when at least one of the pair's correlation, interval bounds, stressed correlation or holdings overlap holds a number, and otherwise shall name no sibling (D-8).

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | the production shape: a keep card whose only missing item is redundancy, whose redundancy item keeps a shown pair, with 6 undecided peers of 153 to 170 aligned days and no gate suffix, N being 181 | the tab renders | the missing line reads exactly "missing: redundancy (not decided: too few shared trading days with the other symphonies; 181 are needed)" |
| S-6.2 | B | the same card | the tab renders | the Redundancy cell's summary reads "Redundancy" then "unavailable" with no reason (the ref prints the 6 names there); inside, "not used in the verdict" carries no reason, and because the pair's correlation holds a number (0.75 with its interval in production) "paired with:" names the shown sibling with its figures as at the ref (D-8); with every disclosure closed none of the 6 peer names is visible on the card |
| S-6.23 | N3 | a redundancy item that keeps a shown pair whose correlation, interval bounds, stressed correlation and holdings overlap all hold no number | the tab renders | the Redundancy cell's summary reads "unavailable" with no reason; no "paired with:" line and no sibling name appear anywhere in the cell; its aligned days and gate lines name no symphony |
| S-6.24 | N1 | a shown pair with only one computed figure, for example holdings overlap with a number and every correlation figure without one | the tab renders | "paired with:" names the sibling, with that one figure |
| S-6.3 | B | the same card | "Comparisons that decided nothing" is opened by click, by Enter and by Space | it shows the stored reason word for word; it is closed on load and after a reload |
| S-6.4 | N1 | a listed peer with exactly N aligned days | the tab renders | the line reads "missing: redundancy (no comparison with the other symphonies reached a result)" |
| S-6.5 | N1 | a listed peer whose stored text carries a gate suffix (", uncertainty gate undefined" or ", structural gate undefined") | the tab renders | the line reads the S-6.4 form |
| S-6.16 | N1 | exactly one listed peer, with 170 aligned days | the tab renders | the S-6.1 form, unchanged |
| S-6.22 | N1 | a listed peer whose display name holds a comma, parentheses or the words "aligned days" | the tab renders | the line reads the S-6.1 form when every listed count is below N, else the S-6.4 form; the name shows only inside the disclosure |
| S-6.6 | B | the production card with missing items redundancy and backtest, no shown pair, the backtest reason "backtest dates do not match the realized window" | the tab renders | "missing: redundancy (not decided: too few shared trading days with the other symphonies; 181 are needed); backtest (backtest dates do not match the realized window)"; the Backtest cell reads "unavailable" with no reason |
| S-6.7 | N3 | N cannot be read | the tab renders | the line reads the S-6.4 form, with no number |
| S-6.27 | N7 | the templates and scripts after the change | they are searched next to the tenure line | the number 181 is not typed as a literal; the line prints N from its derivation |
| S-6.8 | N3 | a redundancy reason that is not the undecided-peer list ("this symphony has no composite", "no same-basis peer has a composite", "no other symphonies to compare") | the tab renders | the reason prints once, in the missing line, as at the ref; no "Comparisons that decided nothing" disclosure; the cell reads "unavailable" or "not applicable" |
| S-6.9 | N3 | missing_items absent | the tab renders | no missing line, as at the ref |
| S-6.19 | N3 | missing_items an empty list | the tab renders | no missing line, as at the ref |
| S-6.20 | N3 | missing_items null | the tab renders | "missing: not recorded", as at the ref |
| S-6.21 | N3 | missing_items a string or a mapping | the tab renders | "missing: not recorded", as at the ref |
| S-6.10 | N3 | an item in the missing list whose cell has no stored reason | the tab renders | the missing line prints "not recorded" for it, as at the ref |
| S-6.17 | N1 | the production card whose redundancy item keeps no shown pair | the tab renders | its summary reads "unavailable" with no reason, as at the ref; its "not used in the verdict" line carries no reason (the ref prints the peer list there) |
| S-6.11 | N7 | an evidence item that is not in the missing list | the tab renders | its cell's summary and body are as at the ref |
| S-6.18 | N7 | a card whose Marginal contribution cell lists a symphony left out, and the last run's excluded list | each disclosure opens | the "left out" and excluded lines are as at the ref (NB-8, G-25) |
| S-6.25 | B | a card whose Redundancy cell lists 3 peers on another basis (production holds none) | its disclosure opens | one line reads "not compared: 3 other symphonies, bases differ"; no peer name anywhere on the card, the closed and open disclosures included (the ref prints one line per name, `templates/ai_advisor.html:2192-2197`) |
| S-6.26 | N1 | exactly one peer on another basis | its disclosure opens | "not compared: 1 other symphony, bases differ" |
| S-6.12 | N7 | the stored tenure review and `GET /api/retirement-recommendations` | they are read | the stored JSON and the route body are byte-identical to the ref; `advisors/tenure_review.py` has zero diff |
| S-6.13 | N5 | Run tenure review completes and the tab reloads | the cards are read | each card follows the rule; each new disclosure is closed |
| S-6.14 | N6 | the stored review cannot be read | the tab renders | "Review unavailable: the stored review could not be read." as at the ref |
| S-6.15 | N8 | an approve or reject on a retire-candidate card is in progress | the card is read | the missing line, the disclosure and the cells follow the rule; the lifecycle block behaves as at the ref |

Negative coverage: N1 S-6.4, S-6.5, S-6.16, S-6.17, S-6.22, S-6.24, S-6.26 | N2 n/a: no refusal is added; Run tenure review's refusal is unchanged (S-7.5) | N3 S-6.7, S-6.8, S-6.9, S-6.10, S-6.19, S-6.20, S-6.21, S-6.23 | N4 n/a: unchanged auth hook (S-7.8) | N5 S-6.13 | N6 S-6.14 | N7 S-6.11, S-6.12, S-6.18, S-6.27 | N8 S-6.15

### AC-7: No figure, control, contract or protected surface changes
Rule: If this change is applied, then every figure, control, route, JSON body, database read and write, banner meaning, honesty text, engine file and migration shall stay as it is at c0760e70, apart from the copy and layout this plan's tables and AC-5 and AC-6 name.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | the same seeded state before and after | every host renders | the text of every figure element (each element whose `data-testid` or `id` carries a value) is identical, apart from the elements AC-5 and AC-6 rewrite (the autotune line and table, the verification count line and disclosure summary, the older-runs summary, the tenure missing line and evidence cells) |
| S-7.2 | N7 | each host at 1920, 1440 and 390 | it renders | Force run now, Emergency Liquidate with its modal and the typed LIQUIDATE, Run recommender, Run tenure review, every approve and reject button, the live switches, Arm Paper, Disarm, Delete, Sync & Flush, Run Claude advisor, Evaluate swap, Evaluate logic change, Run analysis and Run build are present, enabled or disabled as at the ref, and send the same requests |
| S-7.3 | N7 | the same state | `GET /api/state`, `/api/performance`, `/api/history/N`, `/api/retirement-recommendations`, `/api/guard-alpha-summary`, `/api/autotune-runs`, `/api/settings` and `/health` are requested | each body is byte-identical to the ref |
| S-7.4 | N7 | the honesty text: every basis label, window label, as-of stamp, caveat and exclusion reason | each host renders | each is present, word for word, in its place or, for the F16 and guardrail sentences, in the Glossary |
| S-7.5 | N2 | Run recommender or Run tenure review chosen while the market is open | the route answers 409 | the refusal text is the same as at the ref |
| S-7.6 | N7 | `alpha_bot_execution.py`, `math_engine.py`, `database.py`, `advisors/tenure_review.py`, `migrations/` | a test hashes each file with line endings normalized to LF | each SHA-256 equals its value at c0760e70; no route signature or scheduler slot changes |
| S-7.7 | N7 | `tests/app/test_dashboard_no_order_path.py` and every `tests/security/*no_trade_boundary*.py` | they run | they pass unchanged |
| S-7.8 | N4 | no session | each host is requested | the HTML request redirects to `/login` and an `/api/` or XHR request gets 401, as at the ref |
| S-7.9 | N7 | every host in light and dark themes | it renders | only existing tokens, the two font weights 400 and 500 and the existing type sizes are used; every text keeps the contrast the ref's contrast tests require |
| S-7.10 | N7 | the login page, the logout confirm page and a Discord post | each is produced | each is byte-identical to the ref |
| S-7.11 | N3 | a figure that is absent, null or not finite | its host renders | it shows the same dash as at the ref |
| S-7.12 | N8 | a pending live toggle in the modal | Save is chosen | the "Confirm live trading" dialog shows as at the ref and nothing is saved before it |
| S-7.13 | N5 | each section's order | each host renders | sections appear in the same order as at the ref, except that the Retirement pointer leaves the Overview's top for the observations list (G-24) |
| S-7.14 | N6 | `/api/state`, `/api/performance` or `/api/history/N` answers 500 | the page's refresh runs | the page shows the ref's error state |
| S-7.15 | N1 | figures at the edge of their formats: a negative, a zero, the longest value in the copy, and a dash | each host renders | each prints as at the ref, in its aligned column |

Negative coverage: N1 S-7.15 | N2 S-7.5 | N3 S-7.11 | N4 S-7.8 | N5 S-7.13 | N6 S-7.14 | N7 S-7.1 to S-7.4, S-7.6, S-7.7, S-7.9, S-7.10 | N8 S-7.12

### AC-8: Narrow widths keep the stacked forms
Rule: When an in-scope host renders below 1001 px wide, each Table T table shall keep the stacked-row form it has at the ref for that width, the document shall be no wider than the viewport, the rules of AC-3, AC-4, AC-5 and AC-6 shall hold, and the Advisor tab strip shall show every tab whole or a cue at each edge beyond which a tab is out of view, with every tab reachable by touch, tap and keyboard.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-8.1 | B | each host at 390 by 844 | it renders | `scrollWidth` equals the viewport width; each table's form matches the ref's form at 390 |
| S-8.2 | B | each host at 390 | it renders | the Copy table A texts, Copy table X texts and the Glossary show as AC-3 and AC-4 say |
| S-8.3 | B | `/ai-advisor` at 390 | it loads | either every tab lies wholly inside the viewport or a cue shows at each edge with a tab out of view |
| S-8.4 | N1 | 320 px and 1000 px | each host renders | S-8.1 holds; at 1000 the stacked forms of the ref at 1000 show |
| S-8.5 | N1 | `/ai-advisor` at 320 | each tab is reached by touch scroll, tap, then Tab and Enter | each tab panel opens and its tab is in view |
| S-8.6 | N3 | the empty states of S-1.14 | each host renders at 390 | S-8.1 holds |
| S-8.7 | N5 | a Glossary opened and closed | the height is measured | it returns to the closed height within 1 px |
| S-8.8 | N6 | a refresh fails at 390 | the page is read | the ref's error state shows and S-8.1 holds |
| S-8.9 | N7 | every added or changed text at 390 | it renders | its computed font size is at least 11 px |
| S-8.10 | N8 | an unsaved Settings edit at 390 | the section is switched | the edit is kept and S-8.1 holds |

Negative coverage: N1 S-8.4, S-8.5 | N2 n/a: layout refuses nothing | N3 S-8.6 | N4 n/a: unchanged auth hook (S-7.8) | N5 S-8.7 | N6 S-8.8 | N7 S-8.9 | N8 S-8.10

### AC-9: The PM's visual check gates the deploy
Rule: When the branch is ready to deploy, the PM shall take full-page screenshots of each in-scope page, each Advisor tab, the open detail panel and the open settings modal at 1920, 1440 and 390 px wide, light theme, from the exact SHA to deploy over a production database copy, and record per screenshot every header not above its column, every sentence that restates another on the same screen and every text, link, status word or stamp (not a control, D-7) beside the Advisor tab bar or at a toolbar row's far end, or "none"; and if any screenshot records a hit, then the deploy shall not proceed.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-9.1 | B | the green, reviewed SHA | the PM runs the check | a record lists 14 hosts (dashboard, detail panel, Performance, History, Settings, settings modal, 8 Advisor tabs) by 3 widths, 42 screenshots, each with its three lists or "none", and the SHA and database copy date |
| S-9.2 | N2 | one screenshot records a header not above its column | the deploy is attempted | it does not proceed; the hit becomes a finding and a new round |
| S-9.3 | N2 | one screenshot records a restated sentence | the deploy is attempted | it does not proceed |
| S-9.4 | N2 | one screenshot records an element beside the tab bar or at a toolbar's far end | the deploy is attempted | it does not proceed |
| S-9.5 | N1 | the SHA screenshotted differs from the SHA to deploy | the record is read | the check is void and is run again at the deploy SHA |
| S-9.6 | N3 | a host shows an empty state in the database copy | the PM runs the check | the screenshot is still taken and judged |
| S-9.7 | N7 | the record | it is read | it is kept with the review files of the slice and names no symphony |

Negative coverage: N1 S-9.5 | N2 S-9.2, S-9.3, S-9.4 | N3 S-9.6 | N4 n/a: the check is run by the PM on a read-only local server | N5 n/a: a second run replaces the record for the same SHA | N6 n/a: a server that cannot start leaves no screenshots and so no deploy | N7 S-9.7 | N8 n/a: no edit state

## Rule-by-entry-point matrix

| Rule | Server first paint | Page script refresh or poll | Host the page script builds (Settings overrides, settings modal) | Below 1001 px |
|---|---|---|---|---|
| AC-1 tables align | S-1.1 | S-1.15 | S-1.20, S-1.21 (TT-22, TT-23, TT-25) | S-1.4 |
| AC-2 nothing moved to fill space | S-2.2 | S-2.13 | n/a: those hosts carry no Table L row or Table F line | n/a: AC-2 covers 1001 px and up (G-6); below it AC-8 keeps the stacked forms |
| AC-3 one description per section | S-3.1 | S-3.10 | S-3.14 | S-8.2 |
| AC-4 one Glossary per host | S-4.1 | S-4.10 | S-4.13 | S-8.2 |
| AC-5 autotune sentence and N/A columns | n/a: the page script alone writes the table | S-5.1, S-5.18 | n/a: those hosts carry no autotune rows | S-8.2 |
| AC-5 verification count line | S-5.11 | n/a: the Overview does not re-render it | n/a: those hosts carry no verification line | S-8.2 |
| AC-6 tenure counts | S-6.1 | S-6.13 | n/a: those hosts carry no tenure cards | S-8.2 |

## Not Building

Non-goals:
- NB-1: The login page, the logout confirm page and Discord posts (G-13).
- NB-2: No new figure, metric, route, JSON field or computation; no figure changes value (G-14).
- NB-3: No new visual direction: the restyle's studio tokens, type sizes, weights, dot-and-word states and hairline cards stay; no new colour, icon or illustration; no dark-theme-only work beyond keeping both themes legible (G-17).
- NB-4: No help page, onboarding tour or tooltip-only explanation.
- NB-5: No change to how the stored tenure review words its reasons; the card renders the stored text differently (AC-6).

Behaviour that stays exactly as it is:
- NB-6: Every Copy table C label of the earlier plan except MU5 (CX-10).
- NB-7: The header's definitions, carried as titles (copy.json rows H02, H07 to H10).
- NB-8: The "left out" lines of the Marginal contribution cell and the last run's excluded list, which name symphonies with their reasons; G-25 keeps them (S-6.18). The "not compared" lines are covered by AC-6 (S-6.25).
- NB-9: The order of sections on every host, and the CVaR and Managed sleeves panels sharing one row where the ref places them: each is a whole panel in its own box, so no text sits in another section's column (D-10). The Retirement subsections stop sharing a row (AC-2, S-2.18).
- NB-10: The correlation matrix's centred tiles (Q-4).

Deferred:
- NB-11: A proper table for the dashboard detail panel's Risk Profile, which static/index.js fills with rows that are not a table element; it is outside Table T (approved by the PM [PM-ASSUMED]: a follow-up if the PM's visual check finds it misaligned).

Protected surfaces consumed but not changed:
- NB-12: `alpha_bot_execution.py`, `math_engine.py`, `database.py`, `advisors/tenure_review.py` and `migrations/` (S-7.6).
- NB-13: The no-trade boundary: the panic-sell route `/api/sell_account` is the only dashboard trade path; the proposal-approve route alone reaches Composer symphony creation and creates an undeployed symphony; the Managed Sleeves arming ladder; `LIVE_EXECUTION` and credentials stay out of the settings allowlist. `tests/app/test_dashboard_no_order_path.py` and every `tests/security/*no_trade_boundary*.py` run unchanged (S-7.7).

## Narrowed mappings

R-1: The pages stop reading as careless: tables align and text stops repeating; maps to AC-1, AC-2, AC-3, AC-4, AC-5, AC-6; left out: no new visual direction (NB-3); delight is judged by the operator, not by a test
R-2: In every table each column's header sits directly above that column; maps to AC-1; left out: a row group classed not-table in Table T (the comparison rows, the observation lines and the other groups whose cells name themselves) gets the same-x rule, not a header row (G-22)
R-3: Every row's cells line up under their header at 1001 px and up; maps to AC-1; left out: measured at one width inside each layout band of Table W and at 2560 px, not at every pixel (G-20)
R-6: Numeric columns are right-aligned, header included, on every table; maps to AC-1; left out: the correlation matrix keeps its centred tiles under centred headers (Q-4)
R-15: A fact appears once per screen: no hint, caption, banner or run note restates a description; maps to AC-3; left out: the closed Glossary is a reference and may define a term a visible line implies (D-3)
R-19: A column header or label that names a defined term carries the definition as its title; maps to AC-4; left out: a label that already carries a title at the ref keeps that title (D-6); a label matches by the Glossary line's lead term or by Table G3, so a term named only inside a line (for example "Variant" after "Baseline") is reached through Table G3 alone
R-22: The autotune shared line becomes a sentence; maps to AC-5; left out: its time is printed in the page's existing Eastern stamp format, not "Nov 6, 7:08 PM ET" (Q-1)
R-25: Tenure cards never name other symphonies for a comparison that could not be made; they state the count and the reason; maps to AC-6; left out: the stored list stays reachable, word for word, behind one closed disclosure on the card (G-18); the line states the required aligned-day count, not the number of peers (G-18)
R-33: The deploy gate adds the PM's visual check at 1920, 1440 and 390 px of every page and Advisor tab, listing the three kinds of hit per screenshot, any hit blocking the deploy; maps to AC-9; left out: a control at a row's end (Settings Save and Discard, the dashboard window picker, Run buttons) is not a hit; the check judges text, links and status only (G-19)

## User tasks

Workflow (AI Advisor, all eight tabs): no workflow file: `.claude/workflows/` does not exist at the ref
Workflow (dashboard `/` and its detail panel): no workflow file: `.claude/workflows/` does not exist at the ref
Workflow (Performance and History): no workflow file: `.claude/workflows/` does not exist at the ref
Workflow (Settings and the settings modal): no workflow file: `.claude/workflows/` does not exist at the ref

### T-1: Read the AI Advisor Overview (the operator's screenshot)
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Open `/ai-advisor` at 1920 px | none | The tab bar stands alone in its row; nothing sits to its right | S-2.1 |
| 2 | Read the top of the Overview | none | O03 sits above the toolbar, which holds only the picker, Run and "No suggestions yet." | S-2.3, S-3.1 |
| 3 | Read Recent autotune runs | none | One sentence states the shared run time and outcome; one table with one header row lists each symphony and its count, rows one under another | S-5.1, S-1.5 |
| 4 | Read Numeric verification | none | "15 of 18 verified" then "3 unverifiable", and "Show all checks" | S-5.11 |
| 5 | Read the last row of Advisor observations | none | "Retirement" with the last run's time, recommendations and pending count, and an "Open tab" link that opens the Retirement tab | S-2.4, S-2.5 |
| 6 | Open the Glossary at the end of the tab | a click or Enter | Every Overview definition shows, including the three guardrail sentences | S-4.1, S-4.6 |

### T-2: Read a tenure review card
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Choose the Retirement tab | a click | The tenure run line is one compact line; the run-stats tiles sit on one row | S-2.9, S-2.10 |
| 2 | Read a keep card | none | One missing line, "missing: redundancy (not decided: too few shared trading days with the other symphonies; 181 are needed)"; no peer is named on the closed card; the Redundancy cell reads "unavailable" | S-6.1, S-6.2 |
| 3 | Open "Comparisons that decided nothing" | a click or Enter | The stored reason shows word for word | S-6.3 |
| 4 | Scan down the cards | none | Performance, Composite score, Backtest, Marginal contribution, Long VIX hedge and Redundancy sit at the same x on every card | S-1.8 |

### T-3: Compare a table's numbers
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Open `/performance` at 1440 px | none | Risk metrics is one table with one header row; turnover counts and the drag are right-aligned under right-aligned headers | S-1.6, S-1.7 |
| 2 | Read the preconditions table | none | No column gap wider than 48 px between the content of adjacent columns | S-2.6 |

### T-4: Look up a term
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Hover the "Calmar" header of a Frontrunner card's figures | a pointer hover | The title shows the X11 definition | S-4.8 |
| 2 | Open the Frontrunner tab's Glossary | a click | The X06, X08, X09, X11 and X12 lines show, in that order | S-4.1 |

## Open questions

| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | G-10 gives the form "Latest run Nov 6, 7:08 PM ET"; every other stamp on the page prints as "2026-11-06 19:08 ET" through one formatter (`static/ai_advisor.js:87`, `analytics.format_et_stamp`). Which format does the sentence use? | AC-5 | the page's existing stamp, "Latest run 2026-11-06 19:08 ET: ..." so one screen has one time format | default approved (PM approval under the operator's standing autonomy directive) [PM-ASSUMED] |
| Q-2 | removed: settled by PM ruling (G-24) | plan | n/a | settled by PM ruling before approval [PM-ASSUMED] |
| Q-3 | Do the dashboard detail panel and the settings modal each end with their own Glossary, since they cover the page while open? | AC-4 | yes, each its own | default approved (PM approval under the operator's standing autonomy directive) [PM-ASSUMED] |
| Q-4 | Does the right-aligned numeric rule apply to the correlation matrix, whose cells are colour tiles? | AC-1 | no: its headers and tiles share a centre within 1 px instead | default approved (PM approval under the operator's standing autonomy directive) [PM-ASSUMED] |
| Q-5 | On Settings, where only one of five sections shows at a time, where is the Glossary? | AC-4 | after the five sections in the page content, shown whichever section is chosen | default approved (PM approval under the operator's standing autonomy directive) [PM-ASSUMED] |
| Q-6 | removed: settled by PM ruling (G-25) | plan | n/a | settled by PM ruling before approval [PM-ASSUMED] |
| Q-7 | Does the AC-9 check include the open detail panel and settings modal, which G-16 does not name but G-13 lists as surfaces? | AC-9 | yes | default approved (PM approval under the operator's standing autonomy directive) [PM-ASSUMED] |
| Q-8 | Does the "Model not known" definition (A04) go in every tab whose model line can read "not known"? | AC-4 | yes: Overview, Asset Swaps, Logic Changes, Chat and Strategy Builder | default approved (PM approval under the operator's standing autonomy directive) [PM-ASSUMED] |
| Q-9 | Which columns count as numeric? | AC-1 | a column whose body cells each hold a number, a percent, a signed figure, a count or a dash, its header included | default approved (PM approval under the operator's standing autonomy directive) [PM-ASSUMED] |
| Q-14 | What does the link in the Retirement row's last cell read? | AC-2 | "Open tab" | default approved (PM approval under the operator's standing autonomy directive) [PM-ASSUMED] |
| Q-10 | removed: settled by PM ruling (G-28: S01 drops the clause S02 states) | plan | n/a | settled by PM ruling before approval [PM-ASSUMED] |
| Q-11 | removed: settled by PM rulings (G-19, G-28) | plan | n/a | settled by PM ruling before approval [PM-ASSUMED] |
| Q-12 | removed: settled by PM rulings (G-23, G-28) | plan | n/a | settled by PM ruling before approval [PM-ASSUMED] |
| Q-13 | removed: settled by PM ruling (G-28: G-17 stands as recorded) | plan | n/a | settled by PM ruling before approval [PM-ASSUMED] |
