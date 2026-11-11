# Feature: Aligned tables and quiet copy
Status: gate-1
Story: none: the request is quoted in Trigger and saved verbatim in feature-plans/aligned-tables-and-quiet-copy.source.md
Type: fix
Suite: none
Repos: PlanetStopper
Base: main at c0760e70
Ref: PlanetStopper@c0760e70eb6f8c6620002570b493bb9ca4ec6b1e
Sensitive: none: copy and layout only; no figure, control, route, JSON body, order path, engine or database change, and AC-7 pins the protected controls and the zero-order guarantee unchanged
Depends on: none (the ref is origin/main, which carries page-explainers-and-density)

## Trigger
The operator wrote, about a screenshot of the AI Advisor Overview tab on the droplet at c0760e70 (about 2000 px wide), the four sentences quoted in Requirements as Given, saved verbatim in feature-plans/aligned-tables-and-quiet-copy.source.md. The PM's intake rulings K-1 to K-11 are quoted in Requirements as Given as G-6 to G-16.

Root cause (the PM's, checked against the code at the ref): the previous slice, feature-plans/page-explainers-and-density.md, put a visible description of one to two sentences under every section and a "What the terms mean" disclosure beside most sections (its G-6, G-7, AC-1, AC-2), and its AC-6 (no empty region of 240 by 80 px at 1440) was met by moving tables and text to fill space: the autotune rows flow into side-by-side column groups under one header (`templates/ai_advisor.html:356-363`), and the page description sits beside the tab bar from 1440 px (`templates/ai_advisor.html:1301-1311`).

Evidence used: the PM's full-page screenshots of every page and all eight Advisor tabs at 1920 by 1080, light theme, market closed, served read-only from c0760e70 over a production database copy of 2026-11-10. They are untracked review files; what this plan relies on is summarised below, each item verified at the ref. Ids EV-O1 to EV-P2 are the PM's inventory items O-1 to P-2 (the PM's ids, renamed so they do not collide with this plan's R-n, S-n and D-n ids; G-8's "O-8" is EV-O8); EV-X1, EV-X2, EV-O10 and EV-R6 were found while verifying it.

- EV-O1 Autotune runs: once the run time, outcome, score and t-stat are lifted into the shared line, the two remaining columns (Symphony, Runs without adopting a tuned config) flow into a grid of up to five side-by-side groups under one header that sits in the first group only (`templates/ai_advisor.html:356-363`; `static/ai_advisor.js:839-890`). Production shape (the PM's read-only query of the copy taken 2026-11-10T02:20Z): the newest 12 of the 50 newest runs share run_timestamp 2026-11-07T00:08:14Z (19:08 ET on Nov 6), all with baseline_decision "Reverted to Fallback" (shown as "Fallback", with "Frozen-eval not evaluated") and naive_sharpe and selection_tstat NULL (shown "N/A"); the next 2 runs, from 2026-10-30T22:33Z, have the same decision and NULLs.
- EV-O2 The shared line reads "Same for every run: " followed by each lifted header and its value joined by "; " (`static/ai_advisor.js:853-872`), a list of headers and values, not a sentence.
- EV-O3 The page caption A01 and its "What the terms mean" sit to the right of the tab bar from 1440 px (`templates/ai_advisor.html:1301-1311`, `:1341-1346`).
- EV-O4 Three statements of one idea above and in the Overview toolbar: O01 and O03 (`templates/ai_advisor.html:1442-1443`) and the placeholder "Select a symphony and click Run Claude advisor to get suggestions." (`:1490-1492`), plus A01's "a suggestion changes nothing until you accept".
- EV-O5 The Retirement pointer link sits at the far right of the toolbar row (`templates/ai_advisor.html:275-287`, `:1454`).
- EV-O6 Five "What the terms mean" disclosures on the Overview screen (A01 `:1342`, O03 `:1444`, O08 `:1501`, O15 `:1568`, O27 `:1794`).
- EV-O7 The numeric verification line says the count twice: the pill "15 of 18 verified" (`app.py:10441-10471`) beside the count line "15 verified · 3 unverifiable" (`app.py:10378-10402`, rendered at `templates/ai_advisor.html:1722-1729`).
- EV-O10 The same block's disclosure summary repeats the check count: "Show all 18 checks" (`templates/ai_advisor.html:1740`).
- EV-O8 Advisor observations: a fixed four-sentence paragraph (`templates/ai_advisor.html:1808-1818`) under the section description; at 1240 px and up the five producer-line columns take fractions of the full panel width (`:1174-1176`), leaving about 400 px between a producer's name and its verdict at 1920.
- EV-O9 The older-runs summary reads "38 older runs (covers the 50 most recent runs)" (`static/ai_advisor.js:897-905`).
- EV-R1 Every eligible tenure card lists "missing: redundancy (comparison decides nothing for: " followed by every undecided peer by name with its aligned days (the template line `templates/ai_advisor.html:2467-2471` prints the stored reason built at `advisors/tenure_review.py:1500-1540`), then repeats the same list in the Redundancy cell's summary when the item keeps a shown pair (`templates/ai_advisor.html:2130-2140`; `advisors/tenure_review.py:1576-1588`) and again in its "not used in the verdict" line inside the cell's closed disclosure (`:2189-2191`). Production shape (the latest stored tenure review, created 2026-11-10 03:51:48 UTC): 11 cards; 10 have missing_items ["redundancy"] (one of them ["redundancy", "backtest"]) and 1 is not yet eligible (missing_items empty); for the 10 the redundancy item is unavailable and its reason is the undecided-peer list, the only place the per-peer list exists, naming 6 to 10 peers (the PM's count; 6 or 9 in this plan's own read) with 153 to 170 aligned days; 9 of the 10 keep a shown pair, so their summary repeats the list, and 1 has no pair, so its summary reads only "unavailable"; no card has a peer on another basis; each eligible card's Marginal cell names 1 symphony left out.
- EV-R6 The Redundancy cell lists, inside its disclosure, each peer on another basis by name as "not compared: {name}, bases differ" (`templates/ai_advisor.html:2192-2197`; `advisors/tenure_review.py:1446-1452`).
- EV-R2 The six evidence cells of a tenure card flow in a wrapping flex row (`templates/ai_advisor.html:1123`), and Redundancy is moved last by `order: 1` (`:1126`), so Performance, Composite score, Backtest, Marginal contribution, Long VIX hedge and Redundancy sit at different x positions from card to card.
- EV-R3 The run-stats strip wraps: "15 Blocked" and its breakdown fall to a second line (`templates/ai_advisor.html:1082-1093`).
- EV-R4 The tenure run line ("as of trading day", "run completed", "trigger: nightly", "threshold in effect") is spread across the full width (`templates/ai_advisor.html:1072`, `:2422-2435`).
- EV-R5 Each Retirement subsection carries a two to three line description, a disclosure and a hint (`templates/ai_advisor.html:2218-2231`, `:2378-2380`, `:2387-2405`); the hint and the R12 definition both say Run recommender is refused while the market is open.
- EV-S1 Strategy Builder (the same pattern on Asset Swaps, Logic Changes and Frontrunner Builder) carries four texts that each restate that candidates are generated, backtested and gated: the model line, B00, the risk banner and the B03 run note (`templates/ai_advisor.html:2711-2733`, `:2802-2808`).
- EV-D1 The dashboard carries a page caption (`templates/index.html:1241`), a description per panel and six "What the terms mean" disclosures (`:1254`, `:1339`, `:1629`, `:1682`, `:1997`, `:2065`).
- EV-D2 The dollar-saved panel lays its four figures in auto-fit grid columns of at least 16rem (`templates/index.html:500-511`), so they spread with uneven gaps and the second and third wrap onto two lines at 1920.
- EV-D3 Tracked, Armed and Triggered are spread to the panel's two ends (`templates/index.html:457`, `justify-content: space-between`), about 350 px apart at 1920.
- EV-D4 The hero description (`templates/index.html:1253`) and the descriptor under the headline (`:1279`) both say what the guard effect is.
- EV-P1 Performance and History put a description and a disclosure under every section; History's first panel puts the window picker, a caption and the data stamp on one row with the stamp pushed to the far end (`templates/history.html:405-428`; `templates/performance.html:83`, `margin-left: auto`).
- EV-P2 The exit turnover table right-aligns its numeric columns below 1240 px but left-aligns them, header included, from 1240 px (`templates/performance.html:341-342` against `:392-393`).
- EV-X1 The Performance risk metrics table flows its 10 body rows into two side-by-side groups of five, each with its own header row, from 1240 px (`templates/performance.html:378-383`, repeated headers `:692-694`).
- EV-X2 The stop-justification preconditions table stretches its seven columns across the full panel, about 270 px between Sample and ρ at 1920 (`templates/performance.html:755-765`).

## Summary
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

## Requirements as Given
[G-1] [Image #1] Are you fucking serious right now? aligned-tables-and-quiet-copy.source.md:1
[G-2] THIS is what you call fucking effective and delightful UI? aligned-tables-and-quiet-copy.source.md:1
[G-3] THE TABLES THAT DON'T ACTUALLY ALIGN, RANDOM FU CKING TEXT EVERYWHERE? aligned-tables-and-quiet-copy.source.md:1
[G-4] THIS IS FUCKING BULLSHIT aligned-tables-and-quiet-copy.source.md:1
[G-5] (The PM's note on the image, in the source) "(Image #1: the operator's screenshot of the AI Advisor Overview tab on the droplet at c0760e70, about 2000 px wide, saved by the operator at [a file path on the operator's machine, omitted here because a plan names no machine-local path].)" aligned-tables-and-quiet-copy.source.md:3
G-1 to G-4 are the operator's source, verbatim. G-5 is the PM's annotation of the attached image, verbatim apart from the omitted path. G-6 to G-16 are the PM's intake rulings K-1 to K-11, G-17 the PM brief's Not Building line, and G-18 to G-26 to G-31 the PM's rulings on the auditor's findings and questions; each is [PM-ASSUMED] and recorded as the PM gave it, and together they supersede feature-plans/page-explainers-and-density.md where they differ.
[G-6] (PM ruling K-1; [PM-ASSUMED]) "Tables align. In every table, each column's header sits directly above that column, and every row's cells line up under it, at every width of 1001 px and up (narrower widths keep the existing stacked-row form). A table never flows its rows into side-by-side groups. Numeric columns are right-aligned, header included, on every table. Repeated cards that show the same fields (tenure review cards, retirement evidence) place each field at the same x position on every card."
[G-7] (PM ruling K-2; [PM-ASSUMED]) "Alignment beats filling space. page-explainers-and-density AC-6 (no 240 by 80 px empty region at 1440) is withdrawn. A table or line may end before its panel's right edge. No layout moves text beside the tab bar, to the far end of a toolbar, or into another section's column to fill space. Columns sit at the width their content needs, not stretched to the panel edge with wide gaps between a label and its value."
[G-8] (PM ruling K-3; [PM-ASSUMED]) "One description per section, at most one sentence of at most 25 words, directly under its heading, saying what the section is and why the operator cares. No page-level description beside or above the Advisor tab bar; no tab-level description when the tab's first section says the same. A fact appears once per screen: no hint, caption, banner or run note restates a description. Fixed boilerplate paragraphs (O-8's) are removed or folded into the glossary."
[G-9] (PM ruling K-4; [PM-ASSUMED]) "Term definitions move out of the sections. No per-section "What the terms mean" disclosure. Each page, and each Advisor tab, ends with one closed "Glossary" disclosure that holds every definition for that page or tab. A column header or label that names a defined term also carries that definition as its title attribute. Definitions keep their current text unless K-3 or K-5 forces a change."
[G-10] (PM ruling K-5; [PM-ASSUMED]) "Status lines are one plain statement with no count said twice. The autotune shared line becomes a sentence (for the current data, of the form "Latest run Nov 6, 7:08 PM ET: every symphony kept its previous settings."); a column whose value is N/A for every row is not shown and not mentioned."
[G-11] (PM ruling K-6; [PM-ASSUMED]) "Retirement tenure cards list each missing item once and never list other symphonies by name for a comparison that could not be made; they state the count and the reason instead."
[G-12] (PM ruling K-7; [PM-ASSUMED]) "The Overview toolbar holds only the symphony picker, Run and its result; the Retirement pointer moves out of the toolbar (the Retirement tab remains one click away in the tab bar)."
[G-13] (PM ruling K-8; [PM-ASSUMED]) "Surfaces: Dashboard (/), Performance, History, Settings, the per-symphony settings modal, and AI Advisor with all 8 tabs. Login out of scope."
[G-14] (PM ruling K-9; [PM-ASSUMED]) "Unchanged: every figure, control, route, JSON body, banner meaning and honesty text (the Strategy Builder overfitting banner keeps its meaning, shortened per K-3). Engine, database, migrations zero diff."
[G-15] (PM ruling K-10; [PM-ASSUMED]) "The operator's earlier ask still holds: each section still says what it is and why it matters (one sentence), and each jargon term still has a plain definition on its page."
[G-16] (PM ruling K-11; [PM-ASSUMED]) "Deploy gate adds a PM visual check: the PM looks at full-page screenshots of every page and every Advisor tab at 1920, 1440 and 390 px before deploy and lists, per screenshot, any header not above its column, any sentence that restates another on the same screen, and any element placed beside the tab bar or at a toolbar's far end. Any hit blocks the deploy."
[G-17] (PM brief to the spec writer, "Not building (PM answers)"; [PM-ASSUMED]) "no figure, control, route, JSON body, engine, database or migration change (K-9); Login page out (K-8); no new visual direction or token change: the restyle's look (studio tokens, type sizes) stays; no dark-theme-only work beyond keeping both themes legible."

[G-18] (PM ruling on audit finding 9, the tenure card; its line wording is replaced by G-29; [PM-ASSUMED]) "the card states the missing redundancy item ONCE, in one short line saying it was not compared because there are too few shared trading days with the other symphonies, and naming the required count if a configured value exists for it (verify where it lives). The full stored reason stays reachable behind a closed disclosure on that card, never shown by default. No producer change in advisors/tenure_review.py, and no stored row is rewritten (K-9). The WHAT must cover the card as rendered from the stored rows above; leave the HOW to Gate 2."
[G-19] (PM ruling 1, controls and text; [PM-ASSUMED]) "Controls vs text (the 24 px / far-end rule): a control is a button, select, input, checkbox or segmented picker. A control MAY sit at the end of a heading row or a form row: the dashboard window picker, Settings Save/Discard and Run buttons stay where they are. Text, links, status words and stamps may NOT be placed beside the Advisor tab bar or at the far end of a toolbar row. K-11's PM check applies to text, links and status only."
[G-20] (PM ruling 2, widths; [PM-ASSUMED]) "Widths: from the stylesheets at the ref, enumerate every breakpoint from 1001 px up, and measure one width inside each resulting layout band plus 2560. Bands so far: 1001-1239, 1240-1339, 1340-1358, 1359-1439, 1440+. K-11 stays 1920, 1440 and 390 as the PM's own visual check."
[G-21] (PM ruling 3, column edge; [PM-ASSUMED]) "Column edge = the bounding box of the text (or the control) inside the cell, excluding cell padding. A left-aligned column compares left edges and a right-aligned column compares right edges, header text included, within 2 px."
[G-22] (PM ruling 4, header rows; [PM-ASSUMED]) "Header rows: a row group that is NOT a table (dashboard comparison rows, observation lines) is not held to the header rule. The plan must classify every repeated row group on every in-scope surface as table or not-table, give the reason, and apply K-1's same-x rule to the not-table groups that repeat fields."
[G-23] (PM ruling 5, Retirement subsections; [PM-ASSUMED]) "Retirement subsections: the script-driven side-by-side pairing (both subsections at 320 px or less) is a fill-space rule and is withdrawn under K-2. Correlated pairs and Symphony tenure review stack full width."
[G-24] (PM ruling 6, Retirement pointer; [PM-ASSUMED]) "Retirement pointer: one placement only. It leaves the Overview toolbar and becomes the last row of the Advisor observations table: name "Retirement", verdict text from the same retirement_pointer context the page already reads (latest run time, recommendations, pending), and a link to the Retirement tab where the other rows have "+ History". No new request."
[G-25] (PM ruling 7, tenure naming; [PM-ASSUMED]) "Tenure naming vs NB-8: K-6 covers only the tenure card's list of symphonies a comparison could not be made with. The Retirement last-run "left out" lines, which name excluded symphonies with their reasons, stay as they are."
[G-26] (PM ruling 8, Glossary and S-3.3; [PM-ASSUMED]) "Glossary vs S-3.3: definitions moved word for word into a Glossary are allowed. S-3.3 must forbid only a definition that stays in, or is restated in, a section body."

[G-27] (PM ruling on re-check finding 32, the paired sibling; [PM-ASSUMED]) "a tenure card names its paired sibling only when the pair carries at least one computed figure (in production, redundancy.pair.correlation.value is set, 0.75 with a CI). When none of the pair's figures was computed, the card does not name the sibling, and the Redundancy cell reads "unavailable" like any other missing item."
[G-28] (PM rulings on Q-10 to Q-13; [PM-ASSUMED]) "Q-10: yes. The fact is said once per screen, so S01 drops the clause that S02 states. Q-11: yes as defaulted. A control at a row's end is not a hit; only text, links, status words and stamps are (D-7). Q-12: yes as defaulted. The Retirement subsections stack; CVaR and Managed sleeves keep sharing their row, which is a static two-panel row, not a script-measured fill. Q-13: yes, as recorded."

[G-29] (PM ruling on re-check finding 42; [PM-ASSUMED]) "use your proposed wording, recorded as a PM ruling [PM-ASSUMED]: "missing: redundancy (not decided: too few shared trading days with the other symphonies; 181 are needed)", with N from the cited derivation, not a literal. Change AC-6, S-6.1, S-6.6, the worked example and the Copy tables wherever the "not compared: too few shared trading days" form appears. The pair kept under G-27 still shows its computed correlation."
[G-30] (PM ruling on re-check finding 41; [PM-ASSUMED]) "the "not compared ... bases differ" lines are covered by K-6. Render them in count form only: "not compared: {k} other symphony, bases differ" or "{k} other symphonies, bases differ". Never name them, in the closed disclosure either. Fix NB-8, S-6.18 and R-42 to match."
[G-31] (PM rulings on re-check findings 43 to 46; [PM-ASSUMED]) "43: add the 1401-1439 band, measured at 1420, to Table W. 44: add the omitted repeated groups to Table T, each classed with its reason: the Overview suggestion cards, the Asset Swaps result cards, the Logic Changes proposal cards, and the detail panel's Symphony variables rows. Fix TT-16's citation to the server-rendered Risk math rows. 45: you're right that the N row is complete, so it stays as is. 46: the status stays gate-1."

## Requirement Inventory
| R-n | Requirement item | Source (G ids) | Maps to (AC ids, or NB-n) | Scope (full, or narrowed: what is left out) |
|---|---|---|---|---|
| R-1 | The pages stop reading as careless: tables align and text stops repeating | G-1, G-2, G-3, G-4, G-5 | AC-1, AC-2, AC-3, AC-4, AC-5, AC-6 | narrowed: no new visual direction (NB-3); delight is judged by the operator, not by a test |
| R-2 | In every table each column's header sits directly above that column | G-3, G-6, G-22 | AC-1 | narrowed: a row group classed not-table in Table T (the comparison rows, the observation lines and the other groups whose cells name themselves) gets the same-x rule, not a header row (G-22) |
| R-3 | Every row's cells line up under their header at 1001 px and up | G-6, G-20 | AC-1 | narrowed: measured at one width inside each layout band of Table W and at 2560 px, not at every pixel (G-20) |
| R-4 | Below 1001 px the existing stacked-row forms stay | G-6 | AC-8 | full |
| R-5 | A table never flows its rows into side-by-side groups | G-6 | AC-1 | full |
| R-6 | Numeric columns are right-aligned, header included, on every table | G-6 | AC-1 | narrowed: the correlation matrix keeps its centred tiles under centred headers (Q-4) |
| R-7 | Repeated cards with the same fields place each field at the same x on every card | G-6 | AC-1 | full |
| R-8 | The earlier 240 by 80 px empty-region criterion is withdrawn | G-7 | AC-2 | full |
| R-9 | A table or line may end before its panel's right edge | G-7 | AC-2 | full |
| R-10 | No text beside the tab bar, at the far end of a toolbar or in another section's column to fill space | G-7, G-16 | AC-2 | full |
| R-11 | Columns sit at the width their content needs, with no wide gap between a label and its value | G-7 | AC-2 | full |
| R-12 | One description per section: one sentence of at most 25 words, directly under its heading, saying what and why | G-8, G-15 | AC-3 | full |
| R-13 | No page-level description beside or above the Advisor tab bar | G-8 | AC-3 | full |
| R-14 | No tab-level description when the tab's first section says the same | G-8 | AC-3 | full |
| R-15 | A fact appears once per screen: no hint, caption, banner or run note restates a description | G-3, G-8 | AC-3 | narrowed: the closed Glossary is a reference and may define a term a visible line implies (D-3) |
| R-16 | Fixed boilerplate paragraphs are removed or folded into the Glossary | G-8 | AC-3, AC-4 | full |
| R-17 | No per-section "What the terms mean" disclosure | G-9 | AC-4 | full |
| R-18 | Each page and each Advisor tab ends with one closed Glossary holding every definition for it | G-9, G-15 | AC-4 | full |
| R-19 | A column header or label that names a defined term carries the definition as its title | G-9 | AC-4 | narrowed: a label that already carries a title at the ref keeps that title (D-6); a label matches by the Glossary line's lead term or by Table G3, so a term named only inside a line (for example "Variant" after "Baseline") is reached through Table G3 alone |
| R-20 | Definitions keep their text unless K-3 or K-5 forces a change | G-9 | AC-4 | full |
| R-21 | Status lines are one plain statement with no count said twice | G-10 | AC-5 | full |
| R-22 | The autotune shared line becomes a sentence | G-10 | AC-5 | narrowed: its time is printed in the page's existing Eastern stamp format, not "Nov 6, 7:08 PM ET" (Q-1) |
| R-23 | A column whose value is N/A in every row is not shown and not mentioned | G-10 | AC-5 | full |
| R-24 | Tenure cards list each missing item once | G-11 | AC-6 | full |
| R-25 | Tenure cards never name other symphonies for a comparison that could not be made; they state the count and the reason | G-11, G-18 | AC-6 | narrowed: the stored list stays reachable, word for word, behind one closed disclosure on the card (G-18); the line states the required aligned-day count, not the number of peers (G-18) |
| R-26 | The Overview toolbar holds only the symphony picker, Run and its result | G-12 | AC-2 | full |
| R-27 | The Retirement pointer moves out of the toolbar; the Retirement tab stays one click away | G-12, G-24 | AC-2 | full |
| R-28 | Surfaces: Dashboard, Performance, History, Settings, the settings modal and all 8 Advisor tabs; Login out | G-13, G-17 | AC-1, AC-2, AC-3, AC-4, AC-8, NB-1 | full |
| R-29 | Every figure, control, route, JSON body, banner meaning and honesty text is unchanged | G-14, G-17 | AC-7 | full |
| R-30 | The Strategy Builder overfitting banner keeps its meaning, shortened | G-14 | AC-3 | full |
| R-31 | Engine, database and migrations have zero diff | G-14, G-17 | AC-7 | full |
| R-32 | Each jargon term still has a plain definition on its page | G-15 | AC-4 | full |
| R-33 | The deploy gate adds the PM's visual check at 1920, 1440 and 390 px of every page and Advisor tab, listing the three kinds of hit per screenshot, any hit blocking the deploy | G-16, G-19 | AC-9 | narrowed: a control at a row's end (Settings Save and Discard, the dashboard window picker, Run buttons) is not a hit; the check judges text, links and status only (G-19) |
| R-34 | No new visual direction or token change; both themes stay legible | G-17 | AC-7, NB-3 | full |
| R-35 | A tenure card states the missing redundancy once, in one short line: not compared for too few shared trading days, with the required count when a value for it exists; the stored reason stays behind one closed disclosure; no producer or stored row changes | G-18 | AC-6, AC-7 | full |
| R-36 | A control is a button, select, input, checkbox or segmented picker and may sit at a heading or form row's end; text, links, status words and stamps may not sit beside the tab bar or at a toolbar row's far end | G-19 | AC-2, AC-9 | full |
| R-37 | Every breakpoint from 1001 px is enumerated and one width inside each band, plus 2560, is measured; the PM check stays at 1920, 1440 and 390 | G-20 | AC-1, AC-2, AC-9 | full |
| R-38 | A column edge is the bounding box of the text or control in the cell, padding excluded; left edges for left-aligned and right edges for right-aligned columns, header included, within 2 px | G-21 | AC-1 | full |
| R-39 | Every repeated row group on every in-scope surface is classed table or not-table with its reason; not-table groups that repeat fields get the same-x rule | G-22 | AC-1 | full |
| R-40 | Correlated pairs and Symphony tenure review stack full width; the side-by-side pairing is withdrawn | G-23 | AC-2 | full |
| R-41 | The Retirement pointer becomes the last row of the Advisor observations list, named Retirement, with the pointer's text and a link to the tab in the History place, and no new request | G-24 | AC-2 | full |
| R-42 | G-11 covers the tenure card's undecided peers and its peers on another basis, which render in count form only, never named, in a disclosure either; the left-out lines and the excluded list stay | G-25, G-30 | AC-6, NB-8 | full |
| R-43 | A definition moved word for word into a Glossary is allowed; one that stays in or is restated in a section body is not | G-26 | AC-3 | full |
| R-44 | A tenure card names its paired sibling only when the pair carries at least one computed figure; otherwise it names no sibling and the Redundancy cell reads "unavailable" | G-27 | AC-6 | full |
| R-45 | S01 drops the clause S02 states; a control at a row's end is not a hit; the subsections stack and CVaR and sleeves keep their static row; G-17 stands as recorded | G-28 | AC-2, AC-3, AC-9 | full |
| R-46 | The tenure line reads "not decided", with N from its derivation, never a literal | G-29 | AC-6 | full |
| R-47 | Table W measures the 1401 to 1439 band at 1420; Table T classes the suggestion, swap result, logic-change proposal and detail variable groups; TT-16 cites the server-rendered rows | G-31 | AC-1, AC-2 | full |

## User Tasks
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

## Earlier criteria on the same surface
Searched every `feature-plans/*.md` with the `.completed.md` and `.obsolete.md` families and the suite folders (`ux-overhaul/`, `refresh-timing/`, `decision-science.completed/`, `approach/`), `DECISIONS.md`, `docs/generated/`, `docs/audit/` and `tests/ui/`, `tests/app/`, `tests/ai_advisor/` for the touched templates, `data-explainer`, `data-terms`, "What the terms mean", "Same for every run", `count_line`, "covers the", "decides nothing", `retirement-pointer`, `metrics-th-repeat`, `guardrail-uniform-note`, "of 18 verified" and the touched section headings.

| Earlier criterion (plan and AC) | Surface | Disposition (kept, changed or contradicted) | Why |
|---|---|---|---|
| feature-plans/page-explainers-and-density.md AC-1 (a visible description of one or two sentences under every section, page and tab, from its Copy table A) | every in-scope host | changed | One sentence of at most 25 words per section, from this plan's Copy table A; the page-level A01 and D01, the tab-level O01 and the restating B03, X05 and D20 are removed (AC-3) |
| feature-plans/page-explainers-and-density.md AC-2 (each term defined at first appearance, visible in the description or in a "What the terms mean" disclosure under its section) | every in-scope host | contradicted | Every definition moves to one closed Glossary at the end of its host, and labels that name a term carry it as a title (AC-4, G-9) |
| feature-plans/page-explainers-and-density.md AC-3 (jargon labels reworded identically by server and scripts) | every in-scope host | kept | No Copy table C row changes except MU5, retired with the B03 note it lived in (Copy table X, CX-10) |
| feature-plans/page-explainers-and-density.md AC-4 (copy naming a configured value prints it from its authority, else a fallback phrase) | Retirement, dashboard, Performance | kept | Same authorities and fallback rule; the R02 and R22 phrases change wording (Values table) |
| feature-plans/page-explainers-and-density.md AC-5 (no figure, control, banner, contract or honesty text changes) | every in-scope host | changed | Kept as AC-7, except that the Strategy Builder banner drops one sentence (G-14) and the guardrail paragraph's sentences move into the Overview Glossary word for word |
| feature-plans/page-explainers-and-density.md AC-6 (no empty region of 240 by 80 px at 1440; CVaR with sleeves and the two Retirement subsections share a row; figure rows span 90 percent of their width) | every in-scope host | contradicted | Withdrawn by G-7; figure rows sit at the width their content needs with gaps of at most 48 px (AC-2) |
| feature-plans/page-explainers-and-density.md AC-7 (at 375 px a page grows only by the added copy; the Advisor tab strip shows that more tabs exist) | every host at narrow width | changed | The height bound is not carried (the copy shrinks); the no-sideways-scroll rule and the tab strip cue are kept at 390 px (AC-8) |
| feature-plans/page-explainers-and-density.md AC-8 (a column identical in every autotune row is stated once in a "Same for every run" line; frontrunner titles carry the proposal number) | Overview autotune, Frontrunner cards | changed | The line becomes one sentence about run time and outcome only; N/A columns are hidden; score and t-stat are never lifted (AC-5); the frontrunner titles are kept |
| feature-plans/ux-overhaul/02-advisor-overview.md AC-1 (observations as one status line per producer) | Overview observations | kept | One line per producer stays; its columns sit at content width and align (AC-1, AC-2) |
| feature-plans/ux-overhaul/02-advisor-overview.md AC-2 (one autotune card per symphony, older runs behind an expander whose label states it covers the route's 50 most recent runs) | Overview autotune | changed | The label wording changes to "{n} older runs from the 50 most recent" and still states the coverage (AC-5) |
| feature-plans/ux-overhaul/02-advisor-overview.md AC-5 (one Retirement pointer line that opens the Retirement tab) | Overview | changed | Its text (less the "Retirement: " prefix) and the link to the tab are kept; it becomes the last row of the observations list (AC-2, G-24) |
| feature-plans/ux-overhaul/02-advisor-overview.md AC-6 (Overview order: pointer, controls with suggestions and autotune, Market Prism, observations) | Overview | contradicted | The pointer no longer leads the tab; it is the observations list's last row (G-24); the other sections keep their order (S-7.13) |
| feature-plans/ux-overhaul/01-data-truth.md AC-1 (autotune run cards label their numbers truly) | Overview autotune | changed | Labels are unchanged; a score or t-stat column that is N/A in every row is no longer shown (AC-5) |
| feature-plans/ux-overhaul/01-data-truth.md AC-7 (banners, notes and Strategy Builder cards say true things once) | Advisor | kept | This plan applies the same rule more widely (AC-3) |
| feature-plans/ux-overhaul/01-data-truth.md AC-10 (the verification pill reads "N of M verified" when some checks did not pass) | Overview | kept | The pill is unchanged; only the count line beside it drops the count the pill states (AC-5) |
| feature-plans/ux-overhaul/06-review-fixes.md AC-7, first bullet ("FALLBACK N/A repeats collapse per the plan's limit") | Overview autotune | changed | The collapse is the AC-5 sentence and the N/A column rule |
| feature-plans/ux-overhaul/06-review-fixes.md AC-7, Retirement and Performance risk table bullets | Retirement, Performance | kept | The two labelled run buttons and the risk table's delta convention and header unit stay; the risk table becomes one column group (AC-1) |
| feature-plans/ux-overhaul/06-review-fixes.md AC-6 (words on screen: one mapping for enums, the pointer reads as a link) | Advisor | kept | No enum or link wording changes |
| feature-plans/ux-overhaul/04-app-shell.md AC-2 (panels, cards, labels, stat rows and numbers use the dashboard's primitives) and AC-5 (wide content fits narrow screens) | every host | kept | The Glossary reuses `.hp-more`; AC-8 keeps no sideways scroll |
| feature-plans/ux-overhaul/02-advisor-overview.md AC-4 (Retirement is its own tab built from the dashboard's primitives: `.mini-stats` tiles, a `.legend-as-of` stamp, the market-hours hint, `.sym-card` cards) | Retirement | kept | The same primitives stay; the tiles sit on one row (S-2.9) and the subsections stack (S-2.18) |
| feature-plans/ux-overhaul/02-advisor-overview.md AC-7 (exclusion reasons read as sentences through one callable) | Retirement last-run block | kept | Exclusion lines are unchanged |
| feature-plans/ux-overhaul/02-advisor-overview.md AC-8 (Overview times in ET in the dashboard's form) | Overview | kept | The AC-5 sentence's T is the same ET stamp (Q-1) |
| feature-plans/ux-overhaul/03-correlations-realized.md AC-3 (each matrix cell shows its pair, correlation, aligned days and window on hover or focus) and AC-7 (rows and columns carry numbers and full names) | Correlations | kept | TT-10 aligns the matrix without changing its cells or labels; C01 is reworded only |
| feature-plans/ux-overhaul/04-app-shell.md AC-1 (page headers: no title card, the basis or description line stays a `.legend-as-of` caption, Settings' Save and Discard in the first panel) | History, Performance, Settings, AI Advisor | kept | No title card returns; captions keep their form; Save and Discard keep their place (G-19); the Advisor page caption A01 is removed under G-8 |
| feature-plans/ux-overhaul/06-review-fixes.md AC-2 (dashboard figures read one way at first paint and after a refresh) and AC-4 (phone width: no colliding text, no sideways scroll) | dashboard, every host at narrow width | kept | Figures are unchanged (S-7.1); AC-8 keeps the phone rules |
| feature-plans/ux-overhaul/07-final-gate-fixes.md AC-7 (a window label sits only beside its own value) | dashboard hero and comparison rows | kept | AC-1 and AC-2 change only where the rows' columns sit, not which value a label sits beside (S-2.13) |
| feature-plans/ux-overhaul/07-final-gate-fixes.md AC-8 (a missing figure is a dash; tenure drawdowns print as magnitudes) | dashboard, tenure cards | kept | S-7.11 keeps the dash; AC-6 changes only the missing line and the reason text in the Redundancy and missing-item cells |
| static/ai_advisor.js retirement pairing (`static/ai_advisor.js:1156-1160`, the page-explainers AC-6 rule that the two Retirement subsections share a row while each is 320 px or less) | Retirement | contradicted | Withdrawn under G-7; the subsections stack (S-2.18, D-10) |
| feature-plans/symphony-tenure-review.md AC-3 (redundancy evidence; "no redundant sibling" only when it counts as evidence) and AC-8 (one verdict and the decision lifecycle) | Retirement tenure cards | kept | The producer and its stored reason are byte-unchanged; only the card's rendering of an undecided-peer reason and of not-compared peers changes (AC-6) |
| feature-plans/retirement-visibility.md AC-6 (every run shows its blocked count per gate and the stress-gate minimum) | Retirement last-run tiles | kept | Tile text is unchanged; the tiles sit on one row (AC-2) |
| feature-plans/refresh-timing/04-pages-and-header.md AC-3 and AC-4 (History and Performance show their data age) | History, Performance | kept | The stamps stay; they no longer sit at a toolbar's far end (AC-2) |
| DECISIONS.md "AC-14 Guardrail honesty" (the `guardrail-uniform-note` copy names each guardrail's real scope) | Overview observations | changed | Its first three sentences move word for word into the Overview Glossary as O31a to O31c; the fourth, which restates O03, is removed (Copy table X, CX-3) |
| tests/ui/test_page_explainers_*.py, tests/ui/test_page_copy_*.py, tests/ui/test_hp_more_shared_rule.py, tests/ui/test_advisor_density_contract.py, tests/ui/test_advisor_autotune_browser.py, tests/ui/test_dashboard_density_browser.py, tests/ui/test_dashboard_density_contract.py, tests/ui/test_history_performance_density_browser.py, tests/ui/test_history_performance_density_contract.py, tests/ai_advisor/test_r1_guardrail_honesty.py, tests/ai_advisor/test_advisor_honest_uniform_framing.py, tests/app/test_ai_advisor_tab_verification_redesign.py and tests/fixtures/page_explainers/copy.json | the pins of the behaviour above | changed | Each pin of a changed or contradicted criterion above is rewritten to this plan's criteria; a pin of a kept criterion stays (Definition of Done) |

## Acceptance Criteria (Building)

### AC-1: Every table lines up under one header
Requirement items: R-1, R-2, R-3, R-5, R-6, R-7, R-28, R-37, R-38, R-39, R-47
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
| S-1.12 | B | the seeded dashboard with 11 Active cards | `/` renders | on the comparison rows the label, Bot, Held and α fields each sit at one offset across the four rows; on the cards each ledger column (session, Cum, Max DD) starts at the same offset on every card |
| S-1.20 | B | eight pending Frontrunner proposals in the tab's card grid, and the Algorithm parameters cards on Settings | each host renders | each card field (title, chips, figures table, actions; name, input row, help) starts at the same offset from its card's left edge on every card |
| S-1.21 | B | the detail panel of a card, and the settings modal of a symphony | each opens | the Risk math rows' label, value and bar, the event rows' time and text, and the modal's parameter rows' key and value (value right edge) each sit at one offset on every row |
| S-1.19 | B | a swap result and a logic-change result | each tab renders its result | in each Baseline and Variant column every label starts at one x and every value ends at one x |
| S-1.13 | N1 | a symphony name long enough to wrap to two lines | each host renders | the name wraps inside its own cell; the header and the other columns stay aligned |
| S-1.14 | N3 | no autotune runs, no turnover rows, no tenure review and no proposals | each host renders | each empty state shows as at the ref; no table renders a header row with no body rows unless it does so at the ref |
| S-1.15 | N5 | the dashboard poll and the Overview 15 s refresh each run three times | the hosts are measured again | the rule still holds; no header row has been added twice |
| S-1.16 | N6 | `/api/autotune-runs` fails on a refresh | the Overview is read | the last rendered table and its notice stay, still aligned |
| S-1.17 | N7 | every Table T group | each host renders | every cell's text and every figure value is as at the ref (AC-7), apart from the N/A columns AC-5 removes |
| S-1.18 | N8 | an observation history or a tenure evidence disclosure is open | the hosts are measured | the other lines and cards keep their field positions; the open one's fields keep theirs |

Negative coverage: N1 S-1.3, S-1.4, S-1.13 · N2 n/a: layout refuses nothing · N3 S-1.14 · N4 n/a: every host sits behind the unchanged global auth hook, pinned by S-7.8 · N5 S-1.15 · N6 S-1.16 · N7 S-1.17 · N8 S-1.18
Questions: Q-4, Q-9

#### Table W: the widths AC-1 and AC-2 measure
Breakpoints at the ref from 1001 px up: `min-width: 1001px` (`templates/ai_advisor.html:356`), `min-width: 1240px` (`templates/performance.html:344`, `templates/history.html:305`, `templates/index.html:736`, `templates/ai_advisor.html:1174`, `:1237`, `:1272`, `:1284`; with `max-width: 1239px` at `:1260`), `min-width: 1340px` (`templates/performance.html:396`), `max-width: 1359px` (`templates/ai_advisor.html:313`), `min-width: 1440px` (`templates/ai_advisor.html:1301`) `min-width: 2560px` (`templates/settings.html:464`, `:911`), and the header's `max-width: 1400px` (`static/tokens.css:169`, `templates/_chrome.html:124`) and `min-width: 1401px` to `max-width: 1500px` (`templates/_chrome.html:121`), which change only the header. A `max-width: 1359px` rule applies at 1359 itself, so the band edge is 1360, not 1359 as G-20 lists it.

| Band | Width measured |
|---|---|
| 1001 to 1239 | 1001 (the boundary) and 1120 |
| 1240 to 1339 | 1290 |
| 1340 to 1359 | 1350 |
| 1360 to 1400 | 1400 |
| 1401 to 1439 | 1420 |
| 1440 to 1500 | 1440 |
| 1501 to 2559 | 1920 |
| 2560 and up | 2560 |

#### Table T: every repeated row group on the in-scope hosts
Kind table: one header row and the column rule. Kind repeated fields: not a table, so no header row (G-22); the same-offset rule applies to the fields named. Kind single field or no repeated fields: not a table and no rule applies, for the reason given. A group the page script writes says so.

| T-id | Host | Row group at the ref | Kind | Why | Columns, or fields that share an offset | Numeric (right edge) |
|---|---|---|---|---|---|---|
| TT-1 | `/` | comparison rows `#portfolio-strip .vs-row` (`templates/index.html:1425`, `:1466`, `:1499`, `:1545`) | repeated fields | each row is a different measure whose figures name themselves ("Bot +6.09%") | label, Bot, Held, α | Bot, Held, α |
| TT-2 | `/` | symphony cards in Active and Standby (`templates/index.html:1706`, `:1821`; rewritten by the page script on each poll) | repeated fields | a card per symphony, not a grid of cells | name block; session column; Cum column; Max DD column; ring; sparkline; settings button | the figures of the three ledger columns |
| TT-15 | `/` | sleeve rule rows (`templates/index.html:1917`) | repeated fields | a row per rule with its own controls | rule name; mode; actions | none |
| TT-16 | detail panel | Risk math rows (server rendered, `templates/index.html:2186-2248`, for example `:2197`; refilled by the page script) | repeated fields | a label, a value and a bar per row, each row a different figure | label; value; bar | value |
| TT-17 | detail panel | event rows (written by the page script, `static/index.js:1678`) | repeated fields | a time and a sentence per event | time; text | none |
| TT-29 | detail panel | symphony variable rows (written by the page script, `static/index.js:1718-1721`) | repeated fields | a name and its value per variable | name (with its locked mark); value | value |
| TT-3 | Performance | risk metrics table (`templates/performance.html:684`) | table | a metric per row under shared column meanings | Metric, If held, Bot (shadow), Delta | If held, Bot (shadow), Delta |
| TT-4 | Performance | stop-justification preconditions (`templates/performance.html:755`; rows written by the page script) | table | a symphony per row under shared columns | Symphony, Sample, ρ, 95% CI, Daily Sharpe, N, Verdict | ρ, 95% CI, Daily Sharpe, N |
| TT-5 | Performance | exit turnover (`templates/performance.html:799`; rows written by the page script) | table | a symphony per row under shared columns | Symphony, 30d, 90d, 365d, Est. annual trading-cost drag | 30d, 90d, 365d, drag |
| TT-6 | History | by exit reason (`templates/history.html:496`; rows written by the page script) | table | a reason per row under shared columns | Reason and its six figure columns | every column but Reason |
| TT-7 | History | exits (`templates/history.html:566`; rows written by the page script) | table | an exit per row under shared columns | Time, Symphony, Reason, Detail | Detail |
| TT-22 | Settings | Algorithm parameters cards (written by the page script, `static/settings.js:205-215`) | repeated fields | a card per parameter in a grid | name; input row; help | none |
| TT-23 | Settings | per-symphony override rows (written by the page script, `static/settings.js:604-615`) | repeated fields | a row per parameter with its own input | header; global line; input row | none |
| TT-24 | Settings | Master controls ledger (`templates/settings.html:1001`) | no repeated fields | two different cards (the live card and the start time), not repeats | none | none |
| TT-25 | settings modal | locked-variable entries and autotuner parameter rows (written by the page script, `static/settings-modal.js:136-158`) | repeated fields | a key and its value per entry | key; value | value |
| TT-30 | Overview | suggestion cards after Run Claude advisor (written by the page script, `static/ai_advisor.js:186-400`) | repeated fields | a card per suggestion | title; gate dots; figures; accept and dismiss buttons | none |
| TT-8 | Overview | autotune runs and older runs (written by the page script, `static/ai_advisor.js:839-905`) | table | a run per row under shared columns | the columns AC-5 leaves | Runs without adopting a tuned config, the score, Sel t-stat |
| TT-9 | Overview | observation lines (`templates/ai_advisor.html:1826-1835`) and the Retirement row (G-24) | repeated fields | a producer per line whose words name themselves | label; verdict; time; count; last cell | count |
| TT-9h | Overview | each producer's history table (`templates/ai_advisor.html:1838`) | table | an observation per row under shared columns | its columns | its figure columns |
| TT-18 | Overview | Market Prism lens columns (`templates/ai_advisor.html:1589`) | no repeated rows | five side-by-side columns of prose, one per lens | none | none |
| TT-19 | Overview | Market Prism source cards (`templates/ai_advisor.html:1644-1660`) | no repeated rows | one scrolled row of cards per lens | none | none |
| TT-20 | Overview | numeric verification checks (`templates/ai_advisor.html:1701`) | single field | one badge text per check | none | none |
| TT-10 | Correlations | correlation matrix (`templates/ai_advisor.html:1941`) | table | a symphony pair per cell under shared row and column names | one column per symphony | none: centred tiles under centred headers (Q-4) |
| TT-12 | Retirement | recommendation cards (`templates/ai_advisor.html:2249-2293`) | repeated fields | a card per recommendation | pair line; figures line; gates line; lifecycle block | none |
| TT-21 | Retirement | the last run's excluded list (`templates/ai_advisor.html:2349`) | single field | one sentence per excluded symphony | none | none |
| TT-11 | Retirement | tenure review cards (`templates/ai_advisor.html:2447-2482`) | repeated fields | a card per symphony with the same six evidence items | head (name, verdict chip); the six evidence cells in the S-1.8 order | none |
| TT-14 | Asset Swaps, Logic Changes | a result's Baseline and Variant figures (`.stat-row` label and value rows, `templates/ai_advisor.html:655`; written by the page script, `static/ai_advisor_asset_swaps.js:155-159`) | repeated fields | each row names its own figure | in each column: label; value | value |
| TT-31 | Asset Swaps | swap result cards (written by the page script, `static/ai_advisor_asset_swaps.js:112-117`) | repeated fields | a card per swap | title; verdict; the TT-14 figures; notes | none |
| TT-32 | Logic Changes | proposal cards (written by the page script, `static/ai_advisor_logic_changes.js:296`) | repeated fields | a card per proposal | objective line; heading; the TT-14 figures; notes | none |
| TT-13 | Strategy Builder, Frontrunner Builder | each stored card's `stats-table` (`templates/ai_advisor.html:2975`, `:3124`, `:3297`) | table | a metric per row under shared columns | metric and its figure columns | every figure column |
| TT-26 | Strategy Builder, Frontrunner Builder | the stored survivor, rejected, backtest-failed and pending-proposal cards (`templates/ai_advisor.html:2942-3104`, `:3111-3116`, `:3265-3340`) | repeated fields | a card per candidate | title; chips; figures table; notes; actions | none |
| TT-27 | Strategy Builder | the cards of a live run (written by the page script, `static/ai_advisor.js:1550-1551`) | repeated fields | a card per candidate with a title, an objective strip and guidance only | title; strip; guidance | none |
| TT-28 | Chat | the conversation (written by the page script, static/ai_advisor_chat.js) | no repeated fields | alternating messages of free text | none | none |

### AC-2: Nothing is moved to fill space
Requirement items: R-1, R-8, R-9, R-10, R-11, R-26, R-27, R-28, R-36, R-37, R-40, R-41, R-45, R-47
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

Negative coverage: N1 S-2.2 (1001 px boundary) · N2 n/a: layout refuses nothing · N3 S-2.11, S-2.12, S-2.20 · N4 n/a: unchanged auth hook (S-7.8) · N5 S-2.13 · N6 S-2.14 · N7 S-2.15, S-2.16 · N8 S-2.17
Questions: Q-14

#### Table L: toolbar rows
| L-id | Host | Row at the ref |
|---|---|---|
| TL-1 | Overview | the advisor grid's first row: controls and the suggestions placeholder; the Retirement pointer leaves it (`templates/ai_advisor.html:1452-1493`) |
| TL-2 | Retirement | the Run recommender row, its status and its hint (`templates/ai_advisor.html:2362-2381`) |
| TL-3 | Retirement | the tenure header with Run tenure review and its message (`templates/ai_advisor.html:2406-2413`) |
| TL-4 | Performance | both toolbar rows: scope, picker and window; the captions and the data stamp (`templates/performance.html:576-609`) |
| TL-5 | History | the window picker, caption and data stamp row (`templates/history.html:416-430`) |
| TL-6 | Strategy Builder, Frontrunner Builder | the run-controls rows (`templates/ai_advisor.html:2753-2795`, `:3228-3236`) |
| TL-7 | Asset Swaps, Logic Changes | the try-a-swap and try-a-tweak form rows (`templates/ai_advisor.html:2541-2570`, `:2621-2659`) |
| TL-8 | `/` | the hero top: the headline block and the window selector (`templates/index.html:1250-1299`) |
| TL-9 | Settings | the actions panel: caption, Discard and Save (`templates/settings.html:964-970`); Discard and Save are controls, so their place at the row's end is not a hit (D-7) |
| TL-10 | Overview | the Market Prism header: title, chip, model badge and as-of stamp (`templates/ai_advisor.html:1544-1564`, stamp pushed right at `:847`) |

#### Table F: figure and status lines
| F-id | Host | Line at the ref |
|---|---|---|
| TF-1 | `/` | Tracked, Armed, Triggered (`templates/index.html:1603-1616`) |
| TF-2 | `/` | the dollar lead and its three lines (`templates/index.html:1637-1669`) |
| TF-3 | Performance | the headline strip (`templates/performance.html:621-639`) |
| TF-4 | History | the hero figures (`templates/history.html:434-451`) |
| TF-5 | Retirement | the five last-run tiles (`templates/ai_advisor.html:2316-2324`) |
| TF-6 | Retirement | the tenure run line (`templates/ai_advisor.html:2422-2435`) |
| TF-7 | Retirement | each tenure card's head and lifecycle rows (`templates/ai_advisor.html:1112-1113`, `:2448-2451`) |
| TF-8 | `/` | each sleeve card's line (`templates/index.html:1158`, `:1941`) |

### AC-3: One short description per section, said once
Requirement items: R-1, R-12, R-13, R-14, R-15, R-16, R-28, R-30, R-43, R-45
Rule: When an in-scope host renders, each section of Copy table A shall show, directly under its heading (or at the top of a section or panel with no heading), exactly the row's new text as its only description, or no description where the row reads none; each new text shall be one sentence of at most 25 words with the slot filled or its fallback phrase in place; no element of the Advisor navigation panel shall carry a description; each row of Copy table X shall show its new text or be absent as the row says; and the old text of every removed or shortened row shall be absent from the host.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | the production-shape state | each host renders | every Copy table A row for that host shows its exact new text, visible, once, directly under its heading |
| S-3.2 | B | the same state | each host renders | every Copy table X row shows its exact new text, or is absent where it reads removed |
| S-3.3 | N7 | every row whose disposition is removed or shortened | each host renders, all eight Advisor tab panels in the page | its old text, and each old sentence the new text drops, appears in no section body, hidden panels included; a sentence Table G2 moves into the Glossary word for word appears there once (G-26) |
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

Negative coverage: N1 S-3.4 · N2 n/a: a description refuses nothing · N3 S-3.7, S-3.8, S-3.9 · N4 n/a: unchanged auth hook (S-7.8) · N5 S-3.10 · N6 S-3.11 · N7 S-3.3, S-3.12 · N8 S-3.13
Questions: none

#### Copy table A: section descriptions
Dispositions: kept (the ref text stays, already one sentence of at most 25 words with no dash), shortened (the new text replaces it), removed (no description; the reason names where its facts now live), moved (it becomes a Glossary line). Keys are those of `tests/fixtures/page_explainers/copy.json` at the ref. A slot in braces is filled by the Values table. Every "True of" citation is at the ref.

| Key | Host, section and place at the ref | Written by | Disposition | New text (exact) | True of |
|---|---|---|---|---|---|
| D01 | `/`, the page line above the hero (`templates/index.html:1241`) | server render | removed: a page-level line; the hero and Active descriptions say what it says | none | n/a |
| dash.hero | `/`, under "Guard effect on portfolio" (`templates/index.html:1253`) | server render | shortened; its definition sentence becomes Glossary D02 | Above zero the bot's exits did better than holding every symphony through; below zero, holding would have done better. | `analytics.py:2113-2126`; `templates/index.html:1272-1276` |
| dash.rows | `/`, top of the comparison rows (`templates/index.html:1338`) | server render | shortened; its α sentence is Glossary D12 | The same bot-against-holding comparison as single figures: today, the Composer lifetime, the window's deepest drop and its daily swings. | `templates/index.html:1425`, `:1466`, `:1499`, `:1545` |
| D20 | `/`, above Tracked, Armed and Triggered (`templates/index.html:1598`) | server render | moved: it defines three labels, so it becomes Glossary D20t | none | n/a |
| D21 | `/`, top of the dollar-saved panel (`templates/index.html:1628`) | server render | shortened; the other three lines are Glossary D24 to D26 | The dollars the exits saved or lost against holding, added across the exits in the window you pick. | `reporting.py:85-95`; `templates/index.html:1637-1669` |
| D28 | `/`, under the Active header (`templates/index.html:1681`) | server render | shortened | Symphonies with a stop armed and any the bot has exited; the engine checks armed stops every minute while the market is open. | `app.py:3752-3756`, `:3072` |
| D40 | `/`, under the Standby header (`templates/index.html:1818`) | server render | shortened | Symphonies the bot watches with no stop armed; one moves up to Active when a stop arms or the bot exits it. | `app.py:3757-3761` |
| D41 | `/`, under the CVaR title (`templates/index.html:1996`) | server render | shortened; its not-a-signal clause is said by the two CVaR notes | The average of the first symphony's worst outcomes, a measure of how bad its bad days could get. | `math_engine.py:1699-1701`; `templates/index.html:1993-2050` |
| D47 | `/`, under "Managed sleeves" (`templates/index.html:2064`) | server render | shortened; the Arm Paper clause becomes Glossary D50e | Slices of your Alpaca account run by rules you write; a new rule starts in shadow, which places no order. | `database.py:4217-4221`; `templates/index.html:1929` |
| D48 | `/`, the sleeves empty line (`templates/index.html:2075`) | server render | shortened to one sentence | No sleeves configured yet, and this page has no form for creating one. | `templates/index.html:2075` |
| dp.lead | detail panel, under "Symphony detail" (`templates/index.html:2105`) | server render | kept | (unchanged) | `templates/index.html:2095-2270` |
| D57 | detail panel, under "Intraday tape" (`templates/index.html:2152`) | server render | shortened; the toggle sentence is Glossary D58c and D58d | The symphony's return minute by minute through the session, drawn against its stop and breakeven lines. | `templates/index.html:2141-2178` |
| dp.events | detail panel, under the events heading (`templates/index.html:2180`) | server render | kept | (unchanged) | static/index.js event list |
| D59 | detail panel, under "Risk math" (`templates/index.html:2189`) | server render | shortened; the hint sentence restates the hints | The figures the engine weighs when it decides whether to exit this symphony, from its latest check. | `templates/index.html:2186-2248` |
| D66 | detail panel, under "Risk Profile" (`templates/index.html:2252`) | server render | kept | (unchanged) | `templates/index.html:2250-2256` |
| D67 | detail panel, under "Symphony variables" (`templates/index.html:2263`) | server render | shortened; "locked" is Glossary D67t | The stop settings this symphony runs with. | `templates/index.html:2258-2270` |
| F01 | Performance, top of the first panel (`templates/performance.html:566`) | server render | shortened | Risk-adjusted results of the bot's exits against holding, for the whole portfolio or one symphony, over the window you pick. | `templates/performance.html:570-640` |
| F11 | Performance, under "Cumulative returns" (`templates/performance.html:658`) | server render | shortened | The two return paths day by day: the solid line is the bot with its exits, the dashed line is holding through. | `templates/performance.html:646-664` |
| F13 | Performance, under "Risk metrics" (`templates/performance.html:668-672`) | server render | shortened; its delta sentences become Glossary F15h | Each risk figure for holding and for the bot over the window, side by side with their delta. | `templates/performance.html:684-730` |
| F16 | Performance, under "Stop-justification preconditions" (`templates/performance.html:739`) | server render | shortened; its condition becomes Glossary F17h and its Replay and Shadow honesty sentence moves into Glossary F17a word for word | Whether each symphony's daily returns behave in the way that makes a trailing stop pay off on average (Kaminski and Lo, 2014). | `guard_preconditions.py:150-158`, `:166-190` |
| F21 | Performance, under the turnover heading (`templates/performance.html:792`) | server render | shortened; "retained" is Glossary F23 | How often each symphony's exits fired over three windows, and an estimate of the yearly return those exits cost in trading. | `autotuner.py:1444`; `templates/performance.html:799-810` |
| Y01 | History, top of the first panel (`templates/history.html:407`) | server render | shortened | Whether the bot's exits paid for themselves against holding over the window you pick, and which rules did the work. | `analytics.py:2586-2690` |
| Y10 | History, under the daily chart heading (`templates/history.html:477`) | server render | shortened | Each bar is one day's exit alpha added up; above zero the exits beat holding that day. | `analytics.py:2640-2660` |
| Y12 | History, under "By exit reason" (`templates/history.html:490`) | server render | shortened | The same totals split by the rule that fired each exit, so you can see which rules earn their keep. | `analytics.py:2652-2660` |
| Y15 | History, under the exits heading (`templates/history.html:564`) | server render | shortened; Detail becomes Glossary Y16 | Each exit of the day in the heading: when it fired, the symphony and the rule. | static/history.js triggers rows |
| S01 | Settings, the actions caption (`templates/settings.html:965`) | server render | shortened; its next-minute clause is said by the side-nav note S02 (`:989`) | Global settings and per-symphony overrides | `templates/settings.html:985-1150` |
| S03 | Settings, Master controls (`templates/settings.html:999`) | server render | shortened | The mode and the start time that govern real-money execution, so treat both with care. | `templates/settings.html:1001-1033` |
| S08 | Settings, Algorithm parameters (`templates/settings.html:1039`) | server render | shortened | Global defaults for every symphony; a per-symphony override in the next section takes precedence. | static/settings.js algorithm grid |
| S11 | Settings, Credentials (`templates/settings.html:1046`) | server render | shortened; the not-saved fact is said by the credentials warning S12r | The keys, account ids and webhook address the bot uses for Composer, Alpaca, Discord and Anthropic, shown masked. | `app.py:9684`, `:9749-9752` |
| S14 | Settings, Per-symphony overrides (`templates/settings.html:1132`) | server render | shortened; its storage and pinned-value sentences become Glossary S14s and S14p | Override a global setting or lock a parameter against autotune changes, for one symphony at a time. | static/settings.js symphony detail |
| S17 | Settings, Maintenance (`templates/settings.html:1143`) | server render | shortened | Data operations that cannot be undone, so use each with care. | `templates/settings.html:1145-1188` |
| M04 | settings modal, Execution mode (`static/settings-modal.js:471`) | page script | shortened to one sentence | Whether this symphony may send real sell orders; it trades live only when this switch and the server's global live switch are both on. | `static/settings-modal.js:471-472`; `alpha_bot_execution.py:76` |
| M05 | settings modal, Locked tuning variables (`static/settings-modal.js:483`) | page script | kept | (unchanged) | `autotuner.py:3104-3121` |
| M06 | settings modal, Autotuner parameters (`static/settings-modal.js:490`) | page script | shortened to one sentence | The values the autotune set for this symphony, read-only here. | `static/settings-modal.js:490` |
| M07 | settings modal, AI advisor (`static/settings-modal.js:494-495`) | page script | shortened to one sentence | What the advisor said about this symphony; to act on it, lock a variable above or accept a suggestion on the Advisor page. | `static/settings-modal.js:494-500` |
| A01 | Advisor, the caption beside the tab bar (`templates/ai_advisor.html:1341`) | server render | removed: a page-level description beside the tab bar; its autotune fact moves into O08 and its tab-dot and model definitions into the Glossaries (A03, A04) | none | n/a |
| O01 | Overview, top of the tab (`templates/ai_advisor.html:1442`) | server render | removed: a tab-level line that lists what each section below says | none | n/a |
| O03 | Overview, above the toolbar (`templates/ai_advisor.html:1443`) | server render | shortened | Run Claude advisor suggests stop-setting changes for one symphony; you can accept one only if it holds up on history the tuning never saw. | `ai_advisor.py:99-105`, `:2080` |
| O08 | Overview, under "Recent autotune runs" (`templates/ai_advisor.html:1500`) | server render | shortened; takes A01's autotune fact | Each symphony's latest weekly autotune, the one advisor tool that applies new stop settings itself, only when they beat the current ones on held-back data. | `autotuner.py:3085-3121`; `alpha_bot_execution.py:1179-1180` |
| O15 | Overview, under "Market Prism" (`templates/ai_advisor.html:1567`) | server render | shortened; the lens names are Glossary O19 | An overnight read of the market through five lenses; it is background for your decisions and changes nothing by itself. | `templates/ai_advisor.html:1545-1760` |
| O27 | Overview, under "Advisor observations" (`templates/ai_advisor.html:1793`) | server render | shortened | The latest verdict from each advisor check and the last retirement run; each checks something different, so a Clear covers only its own check. | `templates/ai_advisor.html:1808-1815`, `:1820-1880`; the Retirement row of AC-2 |
| C01 | Correlations, top of the tab (`templates/ai_advisor.html:1898`) | server render | shortened | How closely your symphonies' daily returns move together, from the latest successful retirement run; the Retirement tab uses it to suggest a symphony to drop. | `advisors/retirement_recommender.py:58`; `templates/ai_advisor.html:1939-1990` |
| R01 | Retirement, under "Retirement recommendations" (`templates/ai_advisor.html:2211`) | server render | shortened; the wind-down checklist is Glossary R20 | Suggestions to retire a symphony that duplicates another live symphony and scores lower; approving only records your decision. | `advisors/retirement_recommender.py:316`; `advisors/tenure_review.py:1179`; `app.py:12378` (`_dispatch_retirement_decision`, a status-only write) |
| R02 | Retirement, under "Correlated pairs" (`templates/ai_advisor.html:2218`) | server render | shortened; the gates are defined in Glossary R19a and R19b, the arrow in R17 | Flags the lower-scoring symphony of a pair whose returns stay correlated at {screen_threshold} or more, even at the estimate's low end and on worst days. | `advisors/retirement_recommender.py:58`, `:65-69`, `:316`, `:376-378`; `advisors/tenure_review.py:1436-1440` |
| R22 | Retirement, under "Symphony tenure review" (`templates/ai_advisor.html:2387`) | server render | shortened | Once invested for {tenure_threshold} trading days, a symphony gets keep, watch, retire-candidate or no verdict from its redundancy, its backtest and its portfolio effect. | `advisors/tenure_review.py:39-44`, `:228`, `:1155-1214` |
| W01 | Asset Swaps, top of the tab (`templates/ai_advisor.html:2512`) | server render | shortened; the gate is Glossary W02, the manual step the advise-only banner | Test whether swapping one ticker in a symphony for another would have helped; leave both tickers blank and the advisor proposes swaps. | `app.py:11182` (`ai_advisor_asset_swaps_evaluate`, both-blank reasoned mode) |
| L01 | Logic Changes, top of the tab (`templates/ai_advisor.html:2589`) | server render | shortened; the model line already says it reasons over the current rules and the FDR banner names the gate | Test a change to one of a symphony's rules, such as a different lookback, before you make it yourself in Composer. | `app.py:11537` (`ai_advisor_logic_changes_evaluate`, advisory, no Composer write) |
| T02 | Chat, the default body (`templates/ai_advisor.html:2683`) | server render | shortened | Chat explains and changes nothing; the Discuss this proposal and Chat about this buttons on proposal and suggestion cards open it. | `templates/ai_advisor.html:2671-2690`; `static/ai_advisor.js` `openChatWithArtifact` |
| B00 | Strategy Builder, under the model line (`templates/ai_advisor.html:2712`) | server render | shortened; takes the one-per-run fact of the removed B03 note | Ask for new strategies toward one goal; the strongest candidate that clears the gate, at most one per run, is promoted as a suggestion. | `templates/ai_advisor.html:2802-2808` (the ref's own note); `advisors/strategy_builder_engine.py` (`propose_strategies`) |
| B03 | Strategy Builder, the run note under "Propose new strategies" (`templates/ai_advisor.html:2802-2808`) | server render | removed: it restates the model line, B00 and the banner; MU5 retires with it | none | n/a |
| B07 | Strategy Builder, under "Strategy proposals" (`templates/ai_advisor.html:2945`) | server render | shortened | Strategies that cleared the gate; to use one, build it in Composer yourself from the rules on its card. | `templates/ai_advisor.html:2942-3086` |
| B10 | Strategy Builder, under "Backtest failures" (`templates/ai_advisor.html:3090`) | server render | shortened to one sentence | Candidates Composer could not backtest, with the error it returned; the gate never judged them. | `templates/ai_advisor.html:3088-3104` |
| X01 | Frontrunner Builder, top of the tab (`templates/ai_advisor.html:3191`) | server render | shortened; the overlay is Glossary X06 | Proposes copies of your symphonies with one frontrunner overlay added, for you to approve or reject. | `advisors/frontrunner_builder.py:667`, `:816-827` |
| X03 | Frontrunner Builder, the run note (`templates/ai_advisor.html:3237-3241`) | server render | shortened to one sentence of at most 25 words with no dash | Runs over every live symphony in the background, which can take several minutes, and also runs on the weekly schedule; reload this tab afterward. | `templates/ai_advisor.html:3237-3241` (the ref's own note); `advisors/strategy_builder_scheduler.py` (`run_weekly_build`) |
| X05 | Frontrunner Builder, under "Pending approval (N)" (`templates/ai_advisor.html:3265`) | server render | removed: the risk banner says approving creates an undeployed symphony and to review the deltas first | none | n/a |
| none at ref | Correlations "Pairwise return correlation", Overview "Sources" and "Numeric verification", Asset Swaps "Try a swap", Logic Changes "Try a logic tweak", Chat "Explain" | server render | none: each sits under a tab or section description that already says what it is | none | n/a |

#### Values table: slot phrases that change
Every other slot of the earlier plan's Values table (`stress_fraction_pct`, `watch_trail_pp`, `friction_pct` in D25 and F24, `shadow_retention_days`, `min_history_days`) keeps its phrase, fallback and authority (`app.py:2682-2760`), now inside a Glossary line.

| Slot | Copy row | Phrase | Fallback phrase | Authority (unchanged) |
|---|---|---|---|---|
| screen_threshold | R02 | at {screen_threshold} or more | past the screen | `app.py:2698` (`_copy_screen_threshold`) |
| tenure_threshold | R22 | Once invested for {tenure_threshold} trading days, | Once past the configured minimum tenure, | `app.py:2682` (`_copy_tenure_threshold`) |

#### Copy table X: other text that changes
| Key | Host and place at the ref | Written by | Disposition | New text (exact) | Why |
|---|---|---|---|---|---|
| CX-1 | Overview, the suggestions placeholder (`templates/ai_advisor.html:1490-1492`) | server render | reworded | No suggestions yet. | the ref text restates O03 |
| CX-2 | Overview, the Retirement pointer (`templates/ai_advisor.html:1454`) | server render | moved to the last row of Advisor observations | label "Retirement"; verdict cell: the pointer text without "Retirement: "; link "Open tab" | G-12, G-24 |
| CX-3 | Overview, the guardrail paragraph (`templates/ai_advisor.html:1808-1818`) | server render | removed; its first three sentences become Glossary O31a, O31b and O31c word for word | none | G-8 (EV-O8); its fourth sentence restates O03 |
| CX-4 | Overview, the verification count line (`app.py:10378-10402`, shown at `templates/ai_advisor.html:1726-1728`) | server render | reworded per AC-5 | per AC-5 | G-10 |
| CX-5 | Overview, the verification disclosure summary (`templates/ai_advisor.html:1740`) | server render | reworded | Show all checks | the count is on the pill or the count line |
| CX-6 | Overview, the autotune shared line (`static/ai_advisor.js:869-872`) | page script | reworded per AC-5 | per AC-5 | G-10 |
| CX-7 | Overview, the older-runs summary (`static/ai_advisor.js:895-905`) | page script | reworded per AC-5 | per AC-5 | EV-O9 |
| CX-8 | History, the caption Y02 (`templates/history.html:426-428`) | server render | shortened to its basis | Totals from the bot's daily exit records | its second clause restates Y01 |
| CX-9 | Strategy Builder, the risk banner text (`templates/ai_advisor.html:2725-2731`) | server render | shortened: the multiple-testing sentence is dropped (it is Glossary B02); the bold lead, the sound-but-unprovable sentence and the from-scratch sentence stay word for word | Strategy Builder proposals carry the highest overfitting risk. Survivors are sound-but-unprovable: a clean gate pass is not proof of a live edge. Strategy Builder proposals are built entirely from scratch, maximising selection-bias exposure. | G-14 (meaning kept, shortened) |
| CX-10 | Strategy Builder, the model sentence of the run note (copy.json row MU5) | server render | removed with B03 | none | B03 is removed; MU4 still names the model |
| CX-11 | Frontrunner Builder, the empty-state body (`templates/ai_advisor.html:3257-3259`) | server render | reworded | Proposals appear here after a build. | the ref text restates X03's weekly schedule |
| CX-12 | Retirement, Run recommender hint (`templates/ai_advisor.html:2378-2380`) | server render | kept; Glossary R12 no longer restates it | (unchanged) | G-8 |
| CX-13 | `/`, the hero descriptor (`templates/index.html:1279`) | server render | kept: honesty text (window, basis and cost basis); dash.hero no longer restates it | (unchanged) | G-14 |
| CX-14 | every host, each "What the terms mean" disclosure | server render and page scripts | removed; its lines move to the host's Glossary | none | G-9 |

### AC-4: Every definition is in one Glossary at the end of its host
Requirement items: R-1, R-16, R-17, R-18, R-19, R-20, R-28, R-32
Rule: When an in-scope host renders, it shall carry exactly one closed disclosure whose summary reads "Glossary", as the last element of the host (Table G), holding one paragraph per Table G line for that host in Table G order with exactly the text Table G gives; no host shall carry a "What the terms mean" disclosure; and each label element (a table header cell, a figure or tile label, a tenure evidence heading, a comparison row label, or an element whose whole text is a term followed by ":") whose text, trimmed and without a trailing ":", equals the lead term of a Glossary line of that host (the text before the line's first ": "), and each element Table G3 lists, shall carry as its `title` the full text of that Glossary line, unless it carries a `title` at the ref.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | B | the production-shape state | each host renders and its Glossary is opened | the lines are exactly Table G's for that host, in order |
| S-4.2 | B | each host | it renders | its Glossary is the last element of the page content, the tab panel, the detail panel or the modal body, and it is closed |
| S-4.3 | N7 | every host, all eight tab panels | it renders | no `summary` reads "What the terms mean"; no element carries `data-terms` other than the Glossary |
| S-4.4 | N1 | a Glossary | it is reached by keyboard | it has the accessible name "Glossary", opens and closes with Enter and Space, and is closed on load |
| S-4.5 | B | the Table G2 lines | their hosts render | each changed and new line reads exactly its Table G2 text, with slots filled per the Values table |
| S-4.6 | B | the Overview Glossary | it is opened | O31a, O31b and O31c read the first three sentences of `templates/ai_advisor.html:1809-1815` word for word |
| S-4.7 | N7 | every unchanged Table G line | its host renders | its text equals the `new_text` of its row in `tests/fixtures/page_explainers/copy.json` at the ref, with slots filled |
| S-4.8 | B | each Table G3 element and each label element whose text matches a lead term, for example the recommendation card's "Composite gap:" (`templates/ai_advisor.html:2275`, line R18) | its host renders, the page scripts' rows included (TG3-5, TG3-6, TG3-14) | its `title` equals the full text of its Glossary line |
| S-4.9 | N7 | each element that carries a `title` at the ref (the info marks, the score header, the tab dots, the symphony-name titles, R12's button) | its host renders | its `title` is unchanged |
| S-4.10 | N5 | a Glossary opened on `/`, the Overview or Performance | the page's poll or panel refresh runs | it stays open and its lines are unchanged |
| S-4.11 | N3 | a host whose sections do not all render (CVaR hidden, no proposals, no tenure review) | the host renders | its Glossary still holds every Table G line for the host |
| S-4.12 | N6 | a host's script fails to load | the server render is read | every template-built Glossary is present, closed, with its lines |
| S-4.13 | N8 | a pending live toggle in the settings modal | the modal Glossary is opened and closed | the pending toggle and its confirm dialog behave as at the ref |
| S-4.14 | N6 | the modal's settings fetch fails | the error state shows | the modal shows its error text as at the ref and no Glossary |
| S-4.15 | N7 | the header on every page | it renders | the header carries its definitions as `title` or accessible description exactly as at the ref (copy.json rows H02, H07, H08, H09, H10) and gains no Glossary |

Negative coverage: N1 S-4.4 · N2 n/a: a definition refuses nothing · N3 S-4.11 · N4 n/a: unchanged auth hook (S-7.8) · N5 S-4.10 · N6 S-4.12, S-4.14 · N7 S-4.3, S-4.7, S-4.9, S-4.15 · N8 S-4.13
Questions: Q-3, Q-5, Q-8

#### Table G: Glossary lines per host, in order
A key stands for the `new_text` of that row in `tests/fixtures/page_explainers/copy.json` at the ref, slots filled; a key marked * reads its Table G2 text instead. The Glossary sits last in: the dashboard page content (`templates/index.html`, before `:2088`); the detail panel; Performance and History page content; the Settings page content, after the five sections (visible whichever section is chosen); the settings modal body; each Advisor tab panel.

| Host | Glossary lines in order |
|---|---|
| `/` | D02*, D04, D06a, D06b, D07, D05, D03, D10, D12, D13, D14, D15, D16, D17, D18, D19, D20t*, D22, D23, D24, D25, D26, D28a, D28b, D29a, D29b, D29c, D29d, D29e, D29f, D29g, D30, D31, D32, D33, D34, D35, D36, D37, D38, D39, D42, D43, D44, D46, D49, D50a, D50b, D50c, D50d, D50e* |
| detail panel | D53, D54, D55, D56, D58a, D58b, D58c, D58d, D63, D64, D65, D67t |
| Performance | F03, F04t, F07, F09, F08, F15a, F15b, F15c, F15d, F15e, F15f, F15g, F15h*, F17a*, F17b, F17c, F17d, F17e, F17h*, F19a, F19b, F19c, F19d, F19e, F20, F23, F24 |
| History | Y03, Y04, Y08, Y05, Y07, Y13, Y16* |
| Settings | S13a, S13b, S13c, S14s*, S14p*, S15, S16, S18 |
| settings modal | M01, M08 |
| Overview | A03, A04, O04, O07a, O07b, O06, O09a, O09b, O09c, O10, O11, O12, O16, O19, O24, O28, O29, O30, O31a*, O31b*, O31c* |
| Correlations | C05, C03, C07 |
| Retirement | R04, R06, R07, R08, R09, R12*, R17, R18, R19a, R19b, R20, R23, R26, R27a, R27b, R27c, R27d, R28, R30, R29a, R29b, R29c, R29d, R29e, R29f |
| Asset Swaps | A04, W02, W06, W07, W09 |
| Logic Changes | A04, L02a, L02b, L03, L05, L06 |
| Chat | A04 |
| Strategy Builder | A04, B01, B02, B03o, B03u, B08, B07t, B12 |
| Frontrunner Builder | X06, X08, X09, X11, X12 |

Removed definition rows: D40t (the Standby description itself says what Standby is) and the earlier plan's V placement of D02, D20t and Y16 (each is now a Glossary line).

#### Table G2: Glossary lines whose text changes or is new
| Key | Host | Disposition | Text (exact) | Why, and true of |
|---|---|---|---|---|
| D02 | `/` | changed: K-3 forces it, the sign reading is now the dash.hero description | Guard effect: how the bot's exits compare with holding every symphony through, over the window you pick. | `analytics.py:2113-2126` |
| D20t | `/` | changed: a Glossary line names its term | Tracked, Armed, Triggered: Tracked counts every symphony the bot watches. Armed counts those with a stop set and waiting, and Triggered counts those the bot has exited. | `app.py:3956-3966` |
| D50e | `/` | new: the Arm Paper clause of the ref's D47 | Arm Paper: moves a rule from shadow to the paper account. | `templates/index.html:1929`; the ref's D47 text at `:2064` |
| F15h | Performance | new: the delta sentences of the ref's F13 | Delta: bot minus baseline. Positive deltas on risk metrics (Sharpe, Sortino, drawdown reduction) mean the bot improved risk-adjusted outcomes vs holding through; drawdown and volatility deltas are in %. | the ref's F13 text at `templates/performance.html:669-671`, kept word for word |
| F17a | Performance | changed: K-3 forces it ("as the note above says" no longer holds) and it takes the ref's honesty sentence | Sample: which daily series was checked, Replay or Shadow. Replay = simulated if-held daily series from the last autotune/replay run; Shadow = live if-held daily series across all position epochs of the symphony. | the ref's F16 text at `templates/performance.html:739`; `guard_preconditions.py:166-190` |
| F17h | Performance | new: the condition sentence of the ref's F16 | What is checked: whether a day's move tends to carry into the next by more than the day's risk-adjusted return. | `guard_preconditions.py:150-158` |
| Y16 | History | changed: a Glossary line names its term | Detail: the exit's exit alpha, in pts. | `app.py:8719-8725` (`_guard_alpha_detail`) |
| S14s | Settings | new: the storage sentence of the ref's S14 | Where overrides live: they are stored in alphabot_state.db, keyed on the normalized symphony name. | the ref's S14 text at `templates/settings.html:1132` |
| S14p | Settings | new: the pinned-value sentence of the ref's S14 | Pinned value: a value stored for the symphony that equals the global today; it will not follow a later global change. | the ref's S14 text at `templates/settings.html:1132` |
| O31a | Overview | new: the first guardrail sentence, word for word | the first sentence of `templates/ai_advisor.html:1809-1810`, prefixed "Spec Critic: " with its own leading "Spec Critic " dropped | DECISIONS.md "AC-14 Guardrail honesty" |
| O31b | Overview | new: the second guardrail sentence, word for word | the sentence of `templates/ai_advisor.html:1811-1813`, prefixed "Overfitting Conscience: " with its own leading "Overfitting Conscience " dropped | DECISIONS.md "AC-14 Guardrail honesty" |
| O31c | Overview | new: the third guardrail sentence, word for word | the sentence of `templates/ai_advisor.html:1814-1815`, prefixed "Divergence Explainer: " with its own leading "Divergence Explainer " dropped | DECISIONS.md "AC-14 Guardrail honesty" |
| R12 | Retirement | changed: K-3 forces it, the CX-12 hint says the market-hours refusal | Run recommender: runs the nightly analysis now; it takes a minute or two. | `app.py:12451` (`ai_advisor_retirement_run`) |

#### Table G3: labels that carry a Glossary line as their title
These are the labels the lead-term match of the AC-4 rule does not reach by itself, or that sit in a page script's rows; the match covers every other label.
| Key | Host | Element (a header or label whose text names the term) | Glossary line |
|---|---|---|---|
| TG3-1 | `/` | the guard-effect label `.guard-alpha-label` (`templates/index.html:1252`) | D02 |
| TG3-2 | `/` | the comparison row labels: "Cumulative · lifetime" keeps its info-mark title; "Max DD" (`:1499`), "Ann. vol" (`:1545`) | D17, D18 |
| TG3-3 | `/` | the mini-stat labels Tracked, Armed and Triggered (`:1606`, `:1610`, `:1614`) | D20t |
| TG3-4 | `/` | "Account · all-time" (`:1286`) and "Lifetime Max DD · since inception" (`:1600`) | D05, D19 |
| TG3-5 | Overview | the autotune headers "Runs without adopting a tuned config" and "Sel t-stat" (static/ai_advisor.js:873-880); the score header keeps its own title | O09a, O09c |
| TG3-6 | Overview | the outcome words "Fallback" and "Frozen-eval" in the outcome column (static/ai_advisor.js:802-806) | O10, O11 |
| TG3-7 | Overview | the "Numeric verification" header (`templates/ai_advisor.html:1714`) and the Market Prism sentiment chip (`:1558-1559`) | O24, O16 |
| TG3-8 | Performance | headline strip labels "Sharpe improvement", "Max DD reduction", "Sortino improvement" (`templates/performance.html:628-636`) | F07, F08, F09 |
| TG3-9 | Performance | risk metric row labels Max DD, Calmar, Annualized volatility, Volatility reduction, Total return, Annualized return (CAGR), Up days (`:708-729`); the Delta header (`:690`) | F15a to F15g; F15h |
| TG3-10 | Performance | precondition headers Sample, ρ, 95% CI, Daily Sharpe, N (`:759-763`) and each verdict chip word | F17a, F17b, F17c, F17d, F17e; F19a to F19e |
| TG3-11 | Performance | turnover header "Est. annual trading-cost drag" (`:806`) | F24 |
| TG3-12 | History | "Dollar result" and "Win rate" hero labels (`templates/history.html:440`, `:447`); the Detail header (`:572`); the "Wins" and "Avg per exit" headers (`:503`, `:505`) | Y05, Y07, Y16, Y13 |
| TG3-13 | Retirement | the tile labels Analysed, History, Pairs screened, Blocked (`templates/ai_advisor.html:2320`); the six evidence headings (`:2152`) | R06, R07, R08, R09; R29a to R29f |
| TG3-14 | Settings | the override badge words "lock" and "override", and "No longer held" (static/settings.js) | S15, S16 |
| TG3-15 | Strategy Builder, Frontrunner Builder | the "Live Baseline" column header of the stats tables; the Calmar, CAGR, Max DD and Node count change labels on Frontrunner cards (`templates/ai_advisor.html:3297-3330`) | B07t; X11, X12 |
| TG3-16 | Asset Swaps, Logic Changes | the Baseline and Variant labels of a result (static/ai_advisor_asset_swaps.js, static/ai_advisor_logic_changes.js) | W06; L05 |

### AC-5: Status lines read as one plain statement
Requirement items: R-1, R-21, R-22, R-23
Rule: When the Overview renders its autotune runs, its numeric verification or its older runs, each status line shall be one plain statement in which no count appears twice: the autotune runs shall show the AC-5 sentence in place of the "Same for every run" line, a score or Sel t-stat column whose cell reads "N/A" in every row of a table shall be absent from that table with its header and named nowhere, and only the run time and outcome columns shall ever be lifted out of the rows; the verification count line shall omit the verified count when the pill states it and omit every zero count; the verification disclosure summary shall read "Show all checks"; and the older-runs summary shall read "{n} older run" or "{n} older runs" followed by " from the {limit} most recent", where {limit} is the route's row limit.

The autotune sentence, for a main table of two or more rows. T is the run time cell text when it is the same in every row (the page's Eastern stamp, for example "2026-11-06 19:08 ET"). The outcome is shared when the outcome cell text is the same in every row; its clause is "every symphony kept its previous settings" for `fallback`, "every symphony took new tuned settings" for "Adopted AI", "every symphony was reset to the global default settings" for "Reset to Global Default", and "every run's outcome was {label}" for any other label; when the shared frozen-eval word is not "not evaluated", " (frozen-eval {verdict})" follows the clause.
- T and outcome shared: "Latest run {T}: {clause}."
- T shared only: "Latest run {T} for every symphony."
- Outcome shared only: "{Clause, first letter upper case} in its latest run."
- Neither shared, or one row: no sentence, and the run time and outcome stay in the rows.

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
| S-5.13 | N1 | verdict flags-detected, n_pass 23, n_unverifiable 2, n_flagged 1, n_overridden 0 | the Overview renders | the count line reads "23 verified · 2 unverifiable · 1 flagged" |
| S-5.14 | N1 | verdict overrides-detected, n_pass 23, n_unverifiable 0, n_flagged 0, n_overridden 1 | the Overview renders | the count line reads "23 verified · 1 overridden" |
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

Negative coverage: N1 S-5.4 to S-5.9, S-5.12 to S-5.14 · N2 n/a: no status line refuses anything · N3 S-5.15, S-5.16, S-5.17, S-5.22 to S-5.26 · N4 n/a: unchanged auth hook (S-7.8) · N5 S-5.18 · N6 S-5.19 · N7 S-5.20, S-5.21 · N8 n/a: no edit is held open on the Overview's status lines
Questions: Q-1

### AC-6: Tenure cards state the missing redundancy once, without names
Requirement items: R-1, R-24, R-25, R-35, R-42, R-44, R-46
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

Negative coverage: N1 S-6.4, S-6.5, S-6.16, S-6.17, S-6.22, S-6.24, S-6.26 · N2 n/a: no refusal is added; Run tenure review's refusal is unchanged (S-7.5) · N3 S-6.7, S-6.8, S-6.9, S-6.10, S-6.19, S-6.20, S-6.21, S-6.23 · N4 n/a: unchanged auth hook (S-7.8) · N5 S-6.13 · N6 S-6.14 · N7 S-6.11, S-6.12, S-6.18, S-6.27 · N8 S-6.15
Questions: none

### AC-7: No figure, control, contract or protected surface changes
Requirement items: R-29, R-31, R-34, R-35
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

Negative coverage: N1 S-7.15 · N2 S-7.5 · N3 S-7.11 · N4 S-7.8 · N5 S-7.13 · N6 S-7.14 · N7 S-7.1 to S-7.4, S-7.6, S-7.7, S-7.9, S-7.10 · N8 S-7.12
Questions: none

### AC-8: Narrow widths keep the stacked forms
Requirement items: R-4, R-28
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

Negative coverage: N1 S-8.4, S-8.5 · N2 n/a: layout refuses nothing · N3 S-8.6 · N4 n/a: unchanged auth hook (S-7.8) · N5 S-8.7 · N6 S-8.8 · N7 S-8.9 · N8 S-8.10
Questions: none

### AC-9: The PM's visual check gates the deploy
Requirement items: R-33, R-36, R-37, R-45
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

Negative coverage: N1 S-9.5 · N2 S-9.2, S-9.3, S-9.4 · N3 S-9.6 · N4 n/a: the check is run by the PM on a read-only local server · N5 n/a: a second run replaces the record for the same SHA · N6 n/a: a server that cannot start leaves no screenshots and so no deploy · N7 S-9.7 · N8 n/a: no edit state
Questions: Q-7

## Rule-by-Entry-Point Matrix
| Rule | Server first paint | Page script refresh or poll | Host the page script builds (Settings overrides, settings modal) | Below 1001 px |
|---|---|---|---|---|
| AC-1 tables align | S-1.1 | S-1.15 | n/a: those hosts carry no Table T entry | S-1.4 |
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
- NB-11: A proper table for the dashboard detail panel's Risk Profile, which static/index.js fills with rows that are not a table element; it is outside Table T (proposed: a follow-up if the PM's visual check finds it misaligned).

Protected surfaces consumed but not changed:
- NB-12: `alpha_bot_execution.py`, `math_engine.py`, `database.py`, `advisors/tenure_review.py` and `migrations/` (S-7.6).
- NB-13: The no-trade boundary: the panic-sell route `/api/sell_account` is the only dashboard trade path; the proposal-approve route alone reaches Composer symphony creation and creates an undeployed symphony; the Managed Sleeves arming ladder; `LIVE_EXECUTION` and credentials stay out of the settings allowlist. `tests/app/test_dashboard_no_order_path.py` and every `tests/security/*no_trade_boundary*.py` run unchanged (S-7.7).

## Defect Classes
| DC-n | Class (what goes wrong) | Trigger (B or N1-N8) | Sibling search (tool, query, scope) | Scenarios | Raised in review as |
|---|---|---|---|---|---|
| DC-1 | A rule holds on the server render and is lost when a script re-renders the same element | N5 | grep `innerHTML\|textContent\|createElement` in static/ai_advisor.js, static/index.js, static/performance.js, static/history.js, static/settings.js, static/settings-modal.js against every Table T, Table F and Copy table row | S-1.15, S-3.10, S-4.10, S-5.18, S-6.13 | none yet |
| DC-2 | The fix lands on one host or tab and misses a sibling | N7 | grep `What the terms mean`, `data-terms`, `data-explainer`, `<table`, `role="table"`, `stats-table`, `justify-content: space-between`, `margin-left: auto` in templates/ and static/ | S-1.1, S-2.2, S-4.3 | none yet |
| DC-3 | A narrow assertion licenses an unlisted sibling of a family (zero and non-zero counts; one, several and equal peer counts; each outcome label) | N1 | the AC-5 and AC-6 scenario families listed whole | S-5.2 to S-5.16, S-6.3 to S-6.5 | none yet |
| DC-4 | A shortened sentence says something the code does not do | B | for each Copy table A and G2 row, read its "True of" citation at the ref | S-3.1, S-4.5 | none yet |
| DC-5 | Old text survives in a hidden tab panel or a second copy of a string | N7 | grep each removed sentence's distinctive words in templates/, static/ and app.py | S-3.3 | none yet |
| DC-6 | A layout fixed at one width breaks at another media-query band | N1 | list every `@media` in templates/*.html, static/layout.css and static/tokens.css and measure at each Table W width | S-1.3, S-2.2, S-8.4 | none yet |
| DC-7 | A script-built header lacks the title its template sibling carries | B | grep `columnheader` and `<th` in static/*.js against Table G3 | S-4.8 | none yet |
| DC-8 | Reading aligned days from a stored text misreads a name that holds a comma, parentheses or the words "aligned days" | N1 | the stored reason format at `advisors/tenure_review.py:1500-1540` | S-6.22 | none yet |

## Worked Example
The Overview of the production database copy at 1920 px. The tab bar stands alone in its row. Under the tab bar the Overview shows O03, then the toolbar with the symphony picker, Run Claude advisor and "No suggestions yet." (S-2.3). Under "Recent autotune runs" one sentence reads "Latest run 2026-11-06 19:08 ET: every symphony kept its previous settings." and one table follows with the header "Symphony | Runs without adopting a tuned config" and 12 rows below it, such as "Example Northwind Rotation | 4" (S-5.1, S-1.5); "38 older runs from the 50 most recent" closes it (S-5.10). Market Prism's verification reads "15 of 18 verified" and "3 unverifiable", then "Show all checks" (S-5.11). Advisor observations ends with the row "Retirement | last run 2026-11-09 22:51 ET, 0 recommendations, 0 pending | Open tab" (S-2.4). The tab ends with "Glossary", closed (S-4.2).

The main failure case: the Retirement tab, where a keep card for an invented symphony "Example Harbor Ladder" has redundancy undecided against 6 peers. The ref shows the 6 names twice; after the change its missing line reads "missing: redundancy (not decided: too few shared trading days with the other symphonies; 181 are needed)", the Redundancy cell reads "Redundancy unavailable", and the 6 names show only when "Comparisons that decided nothing" is opened (S-6.1, S-6.2, S-6.3).

## Fail-Closed Behaviour
none: no input is judged and nothing refuses; a value the copy cannot read falls back to its whole fallback phrase (Values table), and the existing 409 refusals of Run recommender and Run tenure review are unchanged (S-7.5).

## Authorities Touched
| Value | Authority used | What it replaced | What proves no second source |
|---|---|---|---|
| the R02 screen threshold | the stored ok run's `screen_threshold`, else `advisors/retirement_recommender.py:58`, read through `app.py:2698` (`_copy_screen_threshold`) | the same authority; only the phrase changes | S-3.9 and the earlier plan's AC-4 pins; the Pairs tile prints the same value |
| the R22 tenure threshold | the stored review's `threshold_in_effect`, else the configured minimum, read through `app.py:2682` (`_copy_tenure_threshold`) | the same authority; only the phrase changes | S-3.9; the tenure run line prints the same value |
| the stress fraction, watch margin, exit friction, shadow retention and minimum history slots | `app.py:2711-2760` | unchanged; the lines move into the Glossary | S-4.5, S-4.7 |
| the autotune row limit in the older-runs summary | the route's row limit, 50 (`static/ai_advisor.js:725-728`) | the same value | S-5.10 |
| N, the fewest aligned days the tenure review's redundancy comparison needs | the review's own floor, `max(_structural_needs_days(), retirement_recommender.MIN_OBS_FLOOR)` (`advisors/tenure_review.py:1533`), derived from the named constants `MIN_OBS_FLOOR`, `STRESS_MIN_OBS` and `STRESS_WINDOW_FRACTION` (`advisors/retirement_recommender.py:65`, `:88`, `:97`); 181 at the ref (feature-plans/symphony-tenure-review.md S-3.2); not in the page context at the ref, so Gate 2 says how it reaches the page; it is not stored in the review row | the printed list of names | S-6.1, S-6.7; KU-1 |
| each listed peer's aligned days and gate suffix | the stored redundancy reason (`advisors/tenure_review.py:1500-1540`) | the same text, now read rather than printed | S-6.4, S-6.5, S-6.22 |
| the Retirement row's verdict text | the pointer context the page already reads (`app.py:7298`, `_build_retirement_pointer`) | the toolbar link | S-2.4, S-2.11, S-2.12 |

## Contract Changes and Consumers
none: no route, JSON body or stored record changes (S-7.3, S-6.12); the verification count line is page text only.

## Sibling Repos and Landing Order
none: one repository (Repos header).

## Migrations Required
none: no schema or data change (S-7.6).

## Configuration and Enablement
none: no configuration key is added or changed; the copy reads the existing keys through their existing authorities.

## Breaking Changes
none: no consumer of a route, record or configuration changes; the visible changes are the copy and layout the operator asked for.

## Security Considerations
- Input validation and injection: no new input. The tenure count is derived from stored text and rendered through the template's autoescape; no `| safe` is added (S-6.16).
- Authorization and access control: every host stays behind the unchanged auth hook (S-7.8); no route is added.
- Data exposure: fewer symphony names are shown (AC-6); no new data reaches a page; the settings credentials stay masked (S11).
- Client-side rendering: script-built text keeps `textContent` or the existing escaping (`escHtml`); a Glossary line is static text.
- Abuse or rate limits: no new request; the pointer issues none (S-2.5).
This agrees with `Sensitive: none`.

## Prerequisites
none: no runtime, service or provider is added; the browser tests use the existing harness and the AC-9 check a read-only local server over a production database copy.

## Design-System Mapping
| Component | Element | Required primitive or token | Notes |
|---|---|---|---|
| Description | one line under a heading | the host's existing note class (`.row-note`, `.dash-panel-note`, `.adv-explainer`, `.metrics-caption`, `.hp-note`, `.sec-subtitle`, `.ssm-sec-cap`) | no new class needed for the look |
| Glossary | the closed disclosure | `.hp-more` (`static/layout.css`), summary text "Glossary" | one per host, last |
| Tables | header row and cells | existing `.metrics-tbl`, `.hp-tbl`, `.stats-table`, `.obs-table`, autotune row classes | numeric cells use the existing tabular-figure setting |
| Figure lines | Table F | existing `.mini-stats`, `.hp-figs`, `.adv-figs`, dollar line classes | gaps from existing spacing values |
| Retirement row | the last row of Advisor observations | the existing observation-line classes, with the existing link styling for "Open tab" | placement only (G-24) |

## Definition of Done (Verification)
| AC | Scenarios | Test level | Real-entrypoint test |
|---|---|---|---|
| AC-1 | S-1.1, S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8, S-1.9, S-1.10, S-1.11, S-1.12, S-1.13, S-1.14, S-1.15, S-1.16, S-1.17, S-1.18, S-1.19, S-1.20, S-1.21 | browser layout tests on rendered pages | each host requested through the Flask app and measured in the browser |
| AC-2 | S-2.1, S-2.2, S-2.3, S-2.4, S-2.5, S-2.6, S-2.7, S-2.8, S-2.9, S-2.10, S-2.11, S-2.12, S-2.13, S-2.14, S-2.15, S-2.16, S-2.17, S-2.18, S-2.19, S-2.20 | browser layout tests | the same |
| AC-3 | S-3.1, S-3.2, S-3.3, S-3.4, S-3.5, S-3.6, S-3.7, S-3.8, S-3.9, S-3.10, S-3.11, S-3.12, S-3.13, S-3.14 | template render tests and browser tests | `GET` of each host through the Flask test client and in the browser |
| AC-4 | S-4.1, S-4.2, S-4.3, S-4.4, S-4.5, S-4.6, S-4.7, S-4.8, S-4.9, S-4.10, S-4.11, S-4.12, S-4.13, S-4.14, S-4.15 | template render tests and browser tests | the same, with the modal and detail panel opened by their real controls |
| AC-5 | S-5.1, S-5.2, S-5.3, S-5.4, S-5.5, S-5.6, S-5.7, S-5.8, S-5.9, S-5.10, S-5.11, S-5.12, S-5.13, S-5.14, S-5.15, S-5.16, S-5.17, S-5.18, S-5.19, S-5.20, S-5.21, S-5.22, S-5.23, S-5.24, S-5.25, S-5.26 | script tests on static/ai_advisor.js and render tests of the verification line | the Overview in the browser fed by `GET /api/autotune-runs` |
| AC-6 | S-6.1, S-6.2, S-6.3, S-6.4, S-6.5, S-6.6, S-6.7, S-6.8, S-6.9, S-6.10, S-6.11, S-6.12, S-6.13, S-6.14, S-6.15, S-6.16, S-6.17, S-6.18, S-6.19, S-6.20, S-6.21, S-6.22, S-6.23, S-6.24, S-6.25, S-6.26, S-6.27 | template render tests | `GET /ai-advisor` with a stored tenure review |
| AC-7 | S-7.1, S-7.2, S-7.3, S-7.4, S-7.5, S-7.6, S-7.7, S-7.8, S-7.9, S-7.10, S-7.11, S-7.12, S-7.13, S-7.14, S-7.15 | byte-equality, hash and diff tests | the route requests of S-7.3 |
| AC-8 | S-8.1, S-8.2, S-8.3, S-8.4, S-8.5, S-8.6, S-8.7, S-8.8, S-8.9, S-8.10 | browser tests at 390, 320 and 1000 | each host in the browser |
| AC-9 | S-9.1, S-9.2, S-9.3, S-9.4, S-9.5, S-9.6, S-9.7 | PM manual check | the PM's screenshots of the deploy SHA |

Gates: `ruff format --check`, `ruff check`, a scoped pytest run over the touched test folders (never `-n auto` and never more than 4 workers), CI green on the exact SHA, then the PM's live functional check after the code review, then AC-9. Tests-green is necessary and never sufficient.

Fixture provenance: the production-shape states come from the read-only production database copy of 2026-11-10, one captured record per case the code distinguishes (the 12-row autotune list and its older runs; one verification summary per verdict shape; one tenure review with a keep card holding the undecided-peer reason, a card with a second missing item, a not-yet-eligible card and a card with not-compared peers), with each symphony display name replaced by an invented name of similar length and the rest verbatim; cases the copy does not hold (Adopted AI, Reset to Global Default, a gate suffix, a name with a comma) are built from the producer's own format strings at `autotuner.py:3105-3121` and `advisors/tenure_review.py:1500-1512`, and a test pins that each built record parses as the producer's output does. Existing pins of the changed and contradicted criteria (Earlier criteria table) are rewritten, not loosened; `tests/fixtures/page_explainers/copy.json` is regenerated so its rows match Copy tables A, X and G.

UI evidence: for each changed host, a screenshot at 1920, 1440 and 390 in light theme and at 1440 in dark theme, compared against this plan's rules, beside the AC-9 record.

## Success Measures
The operator reads each page without finding a header off its column or a sentence said twice; read in the AC-9 record of each deploy and in the operator's next message about the pages.

## Known-Unconfirmed and Standing Risk
- KU-1: AC-6 reads each listed peer's aligned days from stored text; a display name that itself contains a parenthesised "N aligned days" could be misread. The production copy holds no such name; S-6.22 pins the case, and a misread can only move the line between its two true forms.
- KU-2: A correlation that is undefined over N or more aligned days also lands in the undecided list; the card then reads "no comparison with the other symphonies reached a result", which stays true, without saying why.
- KU-3: How the pages read on the operator's own screens is not confirmable from code; AC-9 is the check.
- KU-4: Row counts, history depth and the autotune outcome mix are production facts read from the copy of 2026-11-10; they change with time. The read-only queries that settle the shapes: `SELECT symphony_id, baseline_decision, naive_sharpe, selection_tstat, run_timestamp FROM autotune_runs ORDER BY id DESC LIMIT 50`; `SELECT raw_response FROM advisor_observations WHERE advisor_role = 'TENURE_REVIEW_RUN' ORDER BY id DESC LIMIT 1`, then per entry its `missing_items`, `evidence.redundancy.reason`, whether `evidence.redundancy.pair` is present, the length of `evidence.redundancy.not_compared` and of `evidence.marginal.excluded`; and `SELECT raw_response FROM advisor_observations WHERE advisor_role = 'MARKET_PRISM_VERIFICATION' ORDER BY id DESC LIMIT 1` for its `summary` counts. On the copy of 2026-11-10 these gave the shapes the Trigger states.

## Open Questions
| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | G-10 gives the form "Latest run Nov 6, 7:08 PM ET"; every other stamp on the page prints as "2026-11-06 19:08 ET" through one formatter (`static/ai_advisor.js:87`, `analytics.format_et_stamp`). Which format does the sentence use? | AC-5 | the page's existing stamp, "Latest run 2026-11-06 19:08 ET: ..." so one screen has one time format | |
| Q-2 | removed: settled by PM ruling (G-24) | plan | n/a | |
| Q-3 | Do the dashboard detail panel and the settings modal each end with their own Glossary, since they cover the page while open? | AC-4 | yes, each its own | |
| Q-4 | Does the right-aligned numeric rule apply to the correlation matrix, whose cells are colour tiles? | AC-1 | no: its headers and tiles share a centre within 1 px instead | |
| Q-5 | On Settings, where only one of five sections shows at a time, where is the Glossary? | AC-4 | after the five sections in the page content, shown whichever section is chosen | |
| Q-6 | removed: settled by PM ruling (G-25) | plan | n/a | |
| Q-7 | Does the AC-9 check include the open detail panel and settings modal, which G-16 does not name but G-13 lists as surfaces? | AC-9 | yes | |
| Q-8 | Does the "Model not known" definition (A04) go in every tab whose model line can read "not known"? | AC-4 | yes: Overview, Asset Swaps, Logic Changes, Chat and Strategy Builder | |
| Q-9 | Which columns count as numeric? | AC-1 | a column whose body cells each hold a number, a percent, a signed figure, a count or a dash, its header included | |
| Q-14 | What does the link in the Retirement row's last cell read? | AC-2 | "Open tab" | |
| Q-10 | removed: settled by PM ruling (G-28: S01 drops the clause S02 states) | plan | n/a | |
| Q-11 | removed: settled by PM rulings (G-19, G-28) | plan | n/a | |
| Q-12 | removed: settled by PM rulings (G-23, G-28) | plan | n/a | |
| Q-13 | removed: settled by PM ruling (G-28: G-17 stands as recorded) | plan | n/a | |

## Decisions
| ID | Decision | Rationale |
|---|---|---|
| D-1 | The guardrail paragraph's three scope sentences move to the Overview Glossary word for word, not reworded | they are recorded honesty text (DECISIONS.md "AC-14 Guardrail honesty"); G-8 allows folding |
| D-2 | Honesty text kept on screen is never shortened to fit 25 words; only descriptions are | G-14 keeps honesty text; G-8 bounds descriptions |
| D-3 | "Restates" is judged on visible text; the closed Glossary may define a term a visible line implies | a closed reference does not repeat itself to the reader; it keeps G-15's "a plain definition on its page" |
| D-4 | A numeric column is one whose cells hold numbers, percents, signed figures, counts or dashes (Q-9) | gives the right-align rule one test |
| D-5 | Only run time and outcome are ever lifted out of the autotune rows | G-10 makes them a sentence; a lifted score would be a header and value, the EV-O2 defect |
| D-6 | A label that carries a title at the ref keeps it | those titles are already definitions (S-4.9) |
| D-7 | A control is a button, a select, an input, a checkbox or a segmented picker; text, links, status words, stamps, captions, hints and notes are not | G-19 |
| D-8 | A tenure card names its shown pair's sibling only when the pair carries at least one computed figure (correlation, an interval bound, stressed correlation or holdings overlap with a number); otherwise it names no sibling | G-27: the shown pair can be a screen hit with too few aligned days (`advisors/tenure_review.py:1430-1434`, gates "insufficient data" or "undefined" at `:243-249`), which is a comparison that could not be made (G-11); in production the pair's correlation is computed (0.75 with an interval) |
| D-9 | Row groups classed not-table get the same-offset rule, not a header row | G-22 |
| D-10 | The Retirement subsections stack; the CVaR and Managed sleeves panels keep sharing a row | G-23 withdraws the script-measured pairing; CVaR and sleeves are two whole panels in their own boxes, placed side by side by the page grid, not by a measured fill rule |
| D-11 | The tenure line's count is N, the aligned days required, not the number of peers | G-18 asks for the required count; the peer count needs a parse of names, and the stored list stays in the disclosure |

## Docs to Update
- `docs/generated/page_explainers.md` (the copy, the Glossary and the withdrawn density rule), `docs/generated/app.md` (the verification count line), `docs/generated/static_ai_advisor_js.md` or the file that documents static/ai_advisor.js (the autotune sentence and columns), and one file per other touched module.
- `docs/generated/INDEX.md`.
- `README.md` where it describes page descriptions or "What the terms mean".
- A `DECISIONS.md` entry appended at ship by the doc-writer.
- A drafted `.claude/CLAUDE.md` Key Files update to the `templates/ai_advisor.html`, `templates/index.html` and `static/ai_advisor.js` rows for PM approval.

## Follow-Ups
- NB-11 (detail panel Risk Profile as a table): tracked as a backlog line if the AC-9 check flags it.
