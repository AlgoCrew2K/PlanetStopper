# Feature: Data and label truth across History, Performance, Settings and the AI Advisor
Status: planned
Story: none: operator chat request on 2026-10-22, saved verbatim in feature-plans/ux-overhaul/00-overview.source.md
Type: fix
Suite: ux-overhaul 01
Repos: PlanetStopper
Base: main
Ref: PlanetStopper@37c8e983b64c6980c28199a3f9318486b60f3864
Sensitive: money (AC-8)
Depends on: the model-unpin plan merged into this plan's tip before its PR (it unpins Claude model IDs and also edits `templates/ai_advisor.html`, `static/ai_advisor.js` and `app.py`; committed by the PM on branch `model-unpin/fixtures` at 291454eb with its source, audit, review and approach)

## Trigger
The operator found the app outside the dashboard wrong and unreadable (G-3 to G-5), and the app-wide UX audit rendered on a copy of the production state database at the ref recorded the defects this plan fixes; the PM assigned them to plan 01 of the suite (G-17). The source is saved in `feature-plans/ux-overhaul/00-overview.source.md`; the suite overview is `feature-plans/ux-overhaul/00-overview.md`.

What the code does at the ref, per finding:
1. The autotune run card prints the row's `naive_sharpe` under the label "Sortino" (`static/ai_advisor.js:559`). Despite its name, `naive_sharpe` is not a Sharpe ratio: it is the Optuna study's best objective value (`autotuner.py:2776`), the CRRA expected-utility score when the run's spec bundle uses the CRRA utility family and a Sortino ratio otherwise (`autotuner.py:2434-2436`, `:2695-2700`; the column's own docstring says "Raw Optuna best trial Sortino before the selection haircut", `database.py:750`), and the row records which objective scored it through its `spec_bundle_id` (`autotuner.py:3244`), whose `objective_kind` facet, or failing that its `utility_family` facet (CRRA means CRRA-EU), sets the objective (`autotuner.py:2428-2436`); `/api/autotune-runs` does not select that column at the ref (`database.py:860-866`). The audit read the column name, so G-17's "Sortino shows Sharpe" carries the same misreading; Q-8 asks the operator for the label. The card also always renders the Frozen-eval pill, which is empty when the route sets `frozen_eval_verdict` to null (`static/ai_advisor.js:563-565`, `app.py:8951-8957`); audit UX-11 and UX-12, on all 50 cards.
2. The History hero tile's label is the fixed text "$ Saved" (`templates/history.html:297-299`) while its value is a sign-coloured magnitude (`static/history.js:59-75`); at 30d the audit saw a red "$309.40" under "$ SAVED" for a figure the dashboard renders as "$309.40 lost across 108 exits" (UX-16).
3. Performance `fmt(..., 'pp')` already prefixes "+" and `fmtDelta` prefixes "↑ +" again (`static/performance.js:82`, `:94`), rendering "↑ ++1.61pp" (UX-14); the Max drawdown row prints the signed fraction (`static/performance.js:55`, `:78`), so the dashboard's 13.20% reads -13.20% here (audit D3); friction drag prints three decimals (`static/performance.js:831`, UX-23); the two Tier-2 capture rows always render dashes (`static/performance.js:66-72`); and a cold replay cache renders one "replay sample unavailable" row per symphony (`static/performance.js:720-743`, `:771-779`), 12 rows in the audit copy (UX-22, P5).
4. Performance's 1Y and 5Y buttons send 252 and 1260 (`templates/performance.html:421-422`) and `/api/performance` reads `days` as trailing trading-day rows (`app.py:5214`, `app.py:5245-5248`), refusing anything other than an integer or `ytd` with 400 (`app.py:5226-5234`), a dual contract recorded as deliberate (`DECISIONS.md:5825-5827`); the dashboard detail panel also requests `days=60` with the trading-day meaning (`static/index.js:493`); while History's 1Y and 5Y send 365 and 1825 calendar days (`templates/history.html:288-289`) and the dashboard's `1y` token is 365 calendar days (`analytics.py:1983`); Performance opens on 60d (`templates/performance.html:417`) and History on 90d (`templates/history.html:285`, `static/history.js:416`) while the dashboard opens on 30d (`templates/index.html:838`, `app.py:1980`) (UX-15, H5). History's YTD sends the rounded-up count of local days since 1 January (`static/history.js:389-394`), which the route subtracts from today's UTC date (`analytics.py:2003-2004`), so its YTD always starts on or before 31 December of the previous year.
5. Composer hashes or internal lowercase keys label symphonies: the settings modal title is `_currentSymphonyId`, the hash the dashboard card passes (`static/settings-modal.js:592-593`, `templates/index.html:1215`); the Performance preconditions and exit-turnover rows print the hash (`static/performance.js:691-693`, `:727-729`, `:819-821`), 24 rows in the audit copy, although `/api/performance/symphonies` already returns `{id, name}` (`app.py:5378-5395`); each Frontrunner card title prints `p.symphony_id` (`templates/ai_advisor.html:2634`) while `p._incumbent_display_name` is in the context (`templates/ai_advisor.html:2679`); autotune cards print `r.symphony_name || r.symphony_id` (`static/ai_advisor.js:529`), and since the route's select has no `symphony_name` column (`database.py:860-866`) that is always the lowercase key; the observations Symphony cell prints `obs.symphony_id` (`templates/ai_advisor.html:2942`); and the settings detail subtitle prints the lowercase key twice (`static/settings.js:404`) (UX-7, UX-8, UX-25).
6. The History day drilldown builds each exit from `t.get("symphony_id", "")` (`analytics.py:2340-2349`), but the post-mortem producer writes `symphony_name` (`reporting.py:115`), so all 9 rows of the audited day showed a dash, and rows render in file order (`static/history.js:363-383`), not time order (UX-19). The producer writes trigger times as `HH:MM` (`alpha_bot_execution.py:717`, `reporting.py:131`). A drilldown of a day with no valid exits reuses the existing placeholder row "No exit records for this window." (`static/history.js:322-325`), and such a day still gets a bar (`analytics.py:2352`). An earlier plan made each exit inspectable from History (`feature-plans/fix-ops-cluster.md` AC-3, F-020).
7. The Strategy Builder risk banner's headline reads "Logic-change proposals carry the highest overfitting risk." (`templates/ai_advisor.html:2111`), copied from the Logic Changes banner (`templates/ai_advisor.html:1997`) (UX-10); the Correlations caveat prints its first sentence in bold and then `CRISIS_CAVEAT`, which begins with the same sentence (`templates/ai_advisor.html:1810-1811`, `advisors/correlation_diagnostic.py:52-56`) (UX-9); the observations note sends the operator to "the Run Advisor (gear icon on each symphony card)" (`templates/ai_advisor.html:2919-2920`) while the button reads "Run Claude advisor" (`templates/ai_advisor.html:1525`) and the gear opens the settings modal (UX-6); Strategy Builder cards are titled by candidate id (`templates/ai_advisor.html:2309`, `:2474`), the survivor card maps three of the four objective values to labels (`templates/ai_advisor.html:2322-2328`, `advisors/strategy_builder_engine.py:77-85`), and the rejected card prints the raw objective and template values (`templates/ai_advisor.html:2478-2481`) (SB2).
8. "Reset to globals" never gets a click handler: `static/settings.js:457` builds a CSS selector from the raw symphony key; a key with parentheses throws a SyntaxError, and a key with spaces only builds a descendant selector that matches nothing, so `.addEventListener` throws a TypeError (UX-17). The Master "Live execution" toggle on the same page flips its pill to LIVE locally and adds `LIVE_EXECUTION` to the pending save (`static/settings.js:510-528`), which the route refuses with 400 because the key is deliberately outside the allowlist (`app.py:6113-6124`, `:6139-6147`); that refusal also discards every other pending change in the same Save. A pending override that is not a number is sent as text (`static/settings.js:547-548`), and the route writes the globals before it fails with 500 on `float(v)` (`app.py:6150`, `:6160`), a partial write. A CSRF refusal returns HTML (`app.py:379`), so the page shows "Save failed: network error" (`static/settings.js:574`).
9. Each settings list entry counts every key of the symphony's stored strategy parameters as an override (`static/settings.js:362-376`); the route returns the full stored strategy (`app.py:6090-6099`), so every symphony read "8 overrides" with every value equal to the global (audit S3). A stored value equal to the global still pins that symphony: the engine reads each stored key first and falls back to the global only for a missing key (`alpha_bot_execution.py:1251-1252`), and every strategy row is created holding all default values (`database.py:554`), so a later global edit does not reach a symphony that stores the old value.
10. The Prism verification verdict pill uses the pass (green) class for verdict `clean` (`app.py:6582-6598`) and the producer's verdict is `clean` whenever no check is flagged or overridden and at least one passes, even with unverifiable checks or checks of no recognised class, which count only in `n_checks` (`advisors/prism_numeric_verifier.py:273-293`); the audit saw a green "clean" with 14 of 32 checks unverifiable (audit O5).

Found during this spec's investigation, outside the audit: the per-symphony settings modal reads and writes the strategy row keyed by the lowercased Composer hash (`app.py:6177-6183`, `app.py:6231-6275`) while the engine reads the row keyed by the lowercased display name (`alpha_bot_execution.py:1242-1248`); Q-2 and NB-9 carry it.

## Summary
The operator reads true numbers under correct labels, symphony display names instead of Composer hashes or internal keys, and working Settings controls on History, Performance, Settings, the per-symphony settings modal and the AI Advisor tabs.
- The autotune card labels its optimizer score by what it is and never shows an empty Frozen-eval pill (AC-1).
- History's dollar total reads saved or lost like the dashboard (AC-2).
- Performance prints one sign per delta, Max DD as a positive magnitude, two-decimal friction drag, and no dead rows (AC-3).
- Performance and History windows mean the dashboard's calendar windows; History opens on 30d and Performance on 60d, its shortest window that can meet its 30-observation minimum (AC-4).
- Every symphony label shows the display name, with the hash only in a tooltip (AC-5).
- The History day drilldown names each symphony and lists exits in time order (AC-6).
- Banners, notes and Strategy Builder cards say true things once (AC-7).
- The Settings controls do what they show: Reset to globals works, the live toggle is read-only, and a save that would fail is caught before it is sent (AC-8).
- Override counts ignore values equal to the global and say when such values are pinned (AC-9).
- The Prism verification chip is never a green "clean" while a check is unverifiable (AC-10).

## Requirements as Given
[G-3] holy fuck this looks like shit, you've done literally 0 UX screenshot checking. 00-overview.source.md:8
[G-4] Your whole job for the rest of the day is an overhaul of the entire application, ensuring it fucking looks right on top of functions properly. 00-overview.source.md:8
[G-7] PM Step-2 answers ([PM-ASSUMED]; the operator is not available for questions; project directive: record assumptions, do not stop) 00-overview.source.md:11
[G-8] Building: (1) every page and tab of the dashboard app renders correctly, honestly and consistently with the main dashboard `/`, which is the operator's reference standard, and every control on it works. 00-overview.source.md:13
[G-9] (2) Users: the operator, through `/`, `/history`, `/performance`, `/settings` (+ the per-symphony settings modal), `/login`, `/ai-advisor` (tabs Overview, Correlations, Asset Swaps, Logic Changes, Chat, Strategy Builder, Frontrunner Builder) and the shared header/nav. 00-overview.source.md:14
[G-10] (3) Flows: as the audit records per surface; the workflow files under `.claude/workflows/` if any exist. 00-overview.source.md:15
[G-11] (4) Negatives: per the audit (empty states, unverifiable states, unavailable data, narrow widths, dark theme). 00-overview.source.md:16
[G-17] 01-data-truth: wrong numbers and labels (Sortino shows Sharpe; "$ SAVED" over a loss; "++1.61pp"; Performance 1Y = 252 days), Composer hashes shown where display names belong (settings modal title, Performance rows, Frontrunner cards), honest copy (Strategy Builder banner says "Logic-change", Correlations caveat repeats, gear-icon note), dead controls (`settings.js` SyntaxError / Reset to globals, empty Frozen-eval pill), Prism verification chip, overrides count. 00-overview.source.md:24
[G-24] PM rulings on the audit's decisions (record as decisions in the overview; each a default the operator may override) 00-overview.source.md:30
[G-31] D-8 no green "clean" while any check is unverifiable (show verified count of total); 00-overview.source.md:38
[G-32] D-9 dashboard conventions everywhere: Max DD as a positive magnitude labelled Max DD, 1Y = 365 calendar days everywhere, 30d default window on every page; 00-overview.source.md:39
[G-34] D-11 an override equal to the global value does not count; 00-overview.source.md:41
[G-38] Pushback 4 (D-9): revise D-9: the 30d default applies on every page EXCEPT Performance, whose default is the shortest window token that clears its minimum-history rule (state which, from the code); 1Y = 365 days and Max DD as a positive magnitude stay app-wide. 00-overview.source.md:51

## Requirement Inventory
These are the overview's R ids. Overview items R-21, R-70 and R-71 (plan numbering, the Base chain, landing after model-unpin) are met by this plan's Suite, Base and Depends on headers.

| R-n | Requirement item | Source (G ids) | Maps to (AC ids, or NB-n) | Scope (full, or narrowed: what is left out) |
|---|---|---|---|---|
| R-4 | Every screen this plan changes is checked by rendered screenshots before it ships | G-3 | AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9, AC-10 | full |
| R-5 | Every page and tab looks right | G-4, G-8 | AC-1, AC-2, AC-3, AC-5, AC-7 | narrowed: this plan's part is numbers, labels and copy; layout and primitives are plan 04's, new layouts are Claude Design's |
| R-6 | Every control on every page functions | G-4, G-8 | AC-4, AC-8 | narrowed: this plan's part is the window buttons and the Settings controls (Reset to globals, the live toggle, Save); other controls are plans 02, 04 and 05 |
| R-9 | Open questions are answered by recorded defaults, never by stopping | G-7 | AC-1, AC-2, AC-4, AC-6, AC-8, AC-9 | full |
| R-10 | Every page and tab renders honestly | G-8 | AC-1, AC-2, AC-3, AC-5, AC-6, AC-7, AC-10 | narrowed: this plan's surfaces only |
| R-11 | The overhaul covers every page, the modal, the Advisor tabs and the header | G-9 | AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9, AC-10 | narrowed: this plan touches History, Performance, Settings, the settings modal, and the Advisor Overview, Correlations, Strategy Builder and Frontrunner Builder tabs |
| R-12 | Flows follow the audit per surface, and workflow files where they exist | G-10 | AC-2, AC-4, AC-6, AC-8 | full |
| R-13 | Empty, unverifiable and unavailable states are honest on every surface this plan touches | G-11 | AC-1, AC-3, AC-5, AC-6, AC-10 | full |
| R-22 | The autotune card's value now labelled "Sortino" carries a label that names what it is | G-17 | AC-1 | narrowed: G-17 says the value is a Sharpe ratio; the code shows it is the optimizer's best objective score, so it is not labelled Sharpe (Q-8) |
| R-23 | A History dollar figure over a loss reads lost, over a gain saved | G-17 | AC-2 | full |
| R-24 | Performance deltas carry exactly one sign | G-17 | AC-3 | full |
| R-25 | Performance 1Y means 365 calendar days and 5Y 1825 | G-17, G-32 | AC-4 | full |
| R-26 | The settings modal title is the symphony's display name | G-17 | AC-5 | full |
| R-27 | Performance preconditions and exit-turnover rows name each symphony by display name | G-17 | AC-5 | full |
| R-28 | Frontrunner proposal card titles name the incumbent by display name | G-17 | AC-5 | full |
| R-29 | The Strategy Builder risk banner names Strategy Builder | G-17 | AC-7 | full |
| R-30 | The Correlations caveat sentence appears once | G-17 | AC-7 | full |
| R-31 | The observations note names the real advisor control, not the gear icon | G-17 | AC-7 | full |
| R-32 | Settings "Reset to globals" works for every symphony | G-17 | AC-8 | full |
| R-33 | The Frozen-eval line never shows an empty pill | G-17 | AC-1 | full |
| R-34 | The Prism verification chip is never a green "clean" while a check is unverifiable, and shows the verified count of the total | G-17, G-31 | AC-10 | full |
| R-35 | An override equal to the global value is not counted | G-17, G-34 | AC-9 | full |
| R-36 | Max DD is a positive magnitude labelled Max DD on every page | G-32 | AC-3 | full |
| R-37 | Every page with a window picker opens on 30d, except Performance, which opens on the shortest window token that clears its minimum-history rule | G-32, G-38 | AC-4 | full |
| R-38 | The History day drilldown names each exit's symphony and lists exits in time order | G-10 | AC-6 | full |
| R-39 | Internal lowercase keys are replaced by display names | G-10 | AC-5 | narrowed: the dashboard detail-panel title is plan 05's |
| R-40 | Performance shows no all-dash metric rows, one panel note for unavailable replay samples, and two-decimal friction drag | G-10, G-11 | AC-3 | narrowed: a computed metric with no value for the chosen window keeps its row and dash, because the row says the metric exists and lacks data; only rows with no producer at all are removed |
| R-41 | Strategy Builder cards show human objective and source labels and a human title | G-10 | AC-7 | full |
| R-69 | The PM's rulings are recorded as decisions, each a default the operator may override | G-24 | AC-3, AC-4, AC-9, AC-10 | full |

## User Tasks
Workflow (History): no workflow file: this repository has no `.claude/workflows/` directory at the ref.

### T-1: Read the dollar result for a window
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Open `/history` | none | The 30d button is active and the hero tiles show the 30-day window | S-4.4 |
| 2 | Read the dollar tile | a window whose post-mortem dollars sum to a loss | The value reads the magnitude followed by "lost" | S-2.2 |

### T-2: Inspect one day's exits
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Click a day's bar in the Daily Guard alpha strip | a day with several exits | The exits table lists that day's exits, earliest first, each with its symphony's name | S-6.1 |

Workflow (Performance): no workflow file: this repository has no `.claude/workflows/` directory at the ref.

### T-3: Compare a one-year window across pages
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Open `/performance` | none | The 60d button is active | S-4.5 |
| 2 | Click 1Y | none | The series and metrics cover the last 365 calendar days, the same span History's 1Y covers | S-4.1 |

### T-4: Read the risk table and the symphony panels
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Read the risk table | a window with live and shadow metrics | Deltas carry one sign; Max DD reads as a positive percentage | S-3.1, S-3.4 |
| 2 | Read the preconditions and exit-turnover panels | the live roster | Each row names a symphony by display name; friction drag has two decimals; missing replay samples are one note | S-5.2, S-3.7, S-3.9 |

Workflow (Settings): no workflow file: this repository has no `.claude/workflows/` directory at the ref.

### T-5: Reset a symphony to the global parameters
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Select a symphony in Symphony overrides | a symphony whose key has spaces and parentheses | The list entry reads its real override count | S-9.1 |
| 2 | Click "Reset to globals" | none | Every override field clears, Save and Discard enable, nothing is written | S-8.1 |
| 3 | Click Discard, or Save | none | Discard restores the saved values; Save writes the cleared overrides | S-8.3, S-8.4 |
| 4 | Look at the Live execution card | none | It shows the current mode read-only and says where live trading is armed | S-8.11 |

Workflow (Dashboard, per-symphony settings modal): no workflow file: this repository has no `.claude/workflows/` directory at the ref.

### T-6: Open a symphony's settings from its card
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Click the gear on a dashboard card | a live symphony | The modal title shows the symphony's display name | S-5.1 |

Workflow (AI Advisor Overview, Correlations, Strategy Builder, Frontrunner Builder): no workflow file: this repository has no `.claude/workflows/` directory at the ref.

### T-7: Read the advisor's run history, verification and notes
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Read an autotune run card | a run with no frozen-eval result | The card shows its best objective score and "not evaluated" | S-1.1, S-1.2 |
| 2 | Read the Numeric Verification block | a run with unverifiable checks | The pill reads the verified count of the total and is not green | S-10.2 |
| 3 | Read the observations note | none | It names the "Run Claude advisor" button | S-7.3 |
| 4 | Open the Correlations tab and read the caveat | none | The stress sentence appears once | S-7.2 |
| 5 | Open Strategy Builder and Frontrunner Builder | stored candidates and proposals | The banner names Strategy Builder; cards carry objective labels; Frontrunner cards carry the incumbent's name | S-7.1, S-7.4, S-5.4 |

## Earlier criteria on the same surface
| Earlier criterion (plan and AC) | Surface | Disposition (kept, changed or contradicted) | Why |
|---|---|---|---|
| `feature-plans/guard-alpha-saved-coherence.md` AC-6 (History 1Y and 5Y send 365 and 1825) | History window picker | kept | AC-4 keeps those values and makes 30d History's default |
| `feature-plans/guard-alpha-saved-coherence.md` AC-7 (History hero dollar tile coloured by sign, magnitude only) | History hero tile | changed | Colour alone left "$ Saved" over a loss; AC-2 adds the saved or lost word and a direction-neutral label |
| `feature-plans/guard-alpha-saved-coherence.md` AC-8 (reason cards magnitude plus word) | History reason cards | kept | Already follows the convention (`static/history.js:254-257`); S-2.5 pins it unchanged |
| `feature-plans/math-r0.md` AC-5 (one calendar window semantic per picker click on every page, Performance included) and its shipped ruling that Performance's numeric `days` stay trading-day counts (`DECISIONS.md:5825-5827`) | Performance window picker and `/api/performance` | contradicted | D-9 rules 1Y = 365 calendar days everywhere; AC-4 sends calendar tokens from the Performance page and keeps integer `days` as trading days for other callers |
| `feature-plans/mdd-window-truth.md` AC-4, AC-5 (Performance states its actual coverage; `window_days` stays the requested value with honest `actual_days`) | Performance coverage caption and route | kept | AC-4 changes which dates a window covers, not how coverage is reported |
| `feature-plans/fix-f023-perf-view.md` AC-1, AC-2 (`/api/performance/symphonies` returns `{id, name}`; the picker sends the hash and shows the name) | Performance picker | kept | AC-5 reuses the same `{id, name}` list to name the panel rows |
| `feature-plans/guard-alpha-preconditions.md` AC-7 (per-symphony verdict table with a panel-level empty state) | Performance preconditions panel | changed | A cold replay cache now reports once at panel level instead of one row per symphony (AC-3) |
| `feature-plans/frontrunner-proposal-identity.md` AC-4 (identity line naming the incumbent) | Frontrunner card | kept | AC-5 also puts the incumbent's name in the card title |
| `feature-plans/fix-ops-cluster.md` AC-3 (F-020: each exit inspectable from History from existing payload fields) | History day drilldown | kept | AC-6 adds the recorded symphony name to the same payload and orders the rows |
| `feature-plans/autotuner-statistics.completed.md` D3 (the autotune objective stays Sortino) and the later CRRA-EU objective (`autotuner.py:2434-2436`) | Autotune card value | kept | AC-1 changes only the label, which must name the objective score, not a ratio it is not |
| `feature-plans/prism-numeric-verifier.completed.md` AC-10 (render overlay with verdict badges) | Overview Numeric Verification | changed | The 2026-07 redesign's verdict pill showed green "clean" with unverifiable checks; AC-10 applies D-8 |
| `feature-plans/advisor-remediation-r1.md` AC-14 (observations note names each producer's real scope) | Overview observations note | kept | AC-7 changes only the note's last sentence, which named the wrong control |
| `feature-plans/ai-advisor-design-prompt.completed.md` tab-level risk warning text (the Logic Changes headline) | Strategy Builder banner | contradicted | That headline was written for Logic Changes and copied onto Strategy Builder; AC-7 gives Strategy Builder its own |
| `feature-plans/strategy-builder-phase3-contract.completed.md` A1 to A4 (non-dismissible Strategy Builder risk banner) | Strategy Builder banner | kept | The banner stays non-dismissible; only its headline changes |

## Acceptance Criteria (Building)
### AC-1: Autotune run cards label their numbers truly
Requirement items: R-4, R-5, R-9, R-10, R-11, R-13, R-22, R-33
Rule: When the AI Advisor Overview renders an autotune run card from `GET /api/autotune-runs`, the card shall show the row's `naive_sharpe` under a label naming the run's objective as its spec bundle sets it ("Best CRRA-EU score" or "Best Sortino score") with a tooltip stating it is the optimizer's best trial score before the selection haircut, falling back to "Best objective score" with a tooltip naming both objectives when the row's bundle or its facets cannot be read, with no bare label "Sortino" or "Sharpe" on it, and when the row's `frozen_eval_verdict` is null the Frozen-eval line shall read "Frozen-eval not evaluated" with no pill element.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-1.1 | B | a run row with `naive_sharpe` set, a spec bundle whose `utility_family` is CRRA, and `frozen_eval_verdict` "passed" | the Overview loads the autotune list | the card reads "Best CRRA-EU score" followed by `fmtSharpe(naive_sharpe)`; its tooltip names the score as the optimizer's best trial before the haircut; the Frozen-eval pill reads "passed" in the positive token colour |
| S-1.10 | B | a run row whose bundle carries `objective_kind` `sortino_loss_aversion` | the list renders | the card reads "Best Sortino score" |
| S-1.11 | N3 | a run row whose `spec_bundle_id` is null or names no stored bundle | the list renders | the card reads "Best objective score" with a tooltip naming both possible objectives |
| S-1.2 | N3 | a run row with `frozen_eval_verdict` null | the list renders | the card reads "Frozen-eval not evaluated"; the card contains no `frozen-eval-pill` element |
| S-1.3 | N3 | a run row with `frozen_eval_verdict` absent from the row | the list renders | same as S-1.2 |
| S-1.4 | N3 | a run row with `naive_sharpe` null | the list renders | the card reads "Best objective score N/A" |
| S-1.5 | N1 | a run row with `naive_sharpe` 0 | the list renders | the card reads "Best objective score" followed by `fmtSharpe(0)`, never "N/A" |
| S-1.6 | N3 | a run row with `frozen_eval_verdict` "failed" | the list renders | the pill reads "failed" in the negative token colour |
| S-1.7 | N5 | the list has rendered once | the 15 s poll re-renders it twice | each card carries exactly one objective score and one Frozen-eval line |
| S-1.8 | N6 | `GET /api/autotune-runs` fails | the list loads | the existing "Failed to load runs" message shows and no card renders |
| S-1.9 | N7 | any run row | the list renders | the decision pill, the selection t-stat and the adoption-streak line render exactly as at the ref |

Negative coverage: N1 S-1.5 · N2 n/a: the card is read-only and nothing is refused · N3 S-1.2, S-1.3, S-1.4, S-1.6, S-1.11 · N4 n/a: the route is behind the global auth hook, unchanged, and the card has no write · N5 S-1.7 · N6 S-1.8 · N7 S-1.9 · N8 n/a: the card has no edit state
Questions: Q-8

### AC-2: History's dollar total says saved or lost
Requirement items: R-4, R-5, R-9, R-10, R-11, R-12, R-23
Rule: When the History page renders its hero dollar tile for a window, the tile shall show the window's `total_saved` as a magnitude without a sign character followed by "saved" when the total is zero or positive and "lost" when it is negative, the format `analytics.format_dollar_saved` produces, under a label that names no direction.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | a window whose `total_saved` is positive | the hero renders | the tile value equals `format_dollar_saved(total_saved)` (two decimals, thousands separators, "saved"); the tile label is "Dollar result" |
| S-2.2 | N1 | a window whose `total_saved` is negative | the hero renders | the value is the magnitude followed by "lost", with no minus sign and no "Saved" anywhere in the tile |
| S-2.3 | N1 | a window whose `total_saved` is exactly 0 with at least one trigger | the hero renders | the value reads "$0.00 saved" |
| S-2.4 | N3 | `total_saved` absent from the payload while triggers exist | the hero renders | the value reads "--" and no word follows it |
| S-2.5 | N7 | any window | the hero and reason cards render | each reason card's dollar line, the alpha tile and the win-rate tile render exactly as at the ref |
| S-2.6 | N3 | a window with no triggers | the page loads | the existing empty state shows and the hero tiles are hidden |
| S-2.7 | N5 | the 30d window is shown | the operator clicks 90d, then 30d | the tile shows the 30d result again, with one word |
| S-2.8 | N6 | the History request (`GET /api/history/30`) fails | the page loads | the tiles keep their prior content and no word is appended twice |

Negative coverage: N1 S-2.2, S-2.3 · N2 n/a: display only · N3 S-2.4, S-2.6 · N4 n/a: the route is behind the global auth hook, unchanged · N5 S-2.7 · N6 S-2.8 · N7 S-2.5 · N8 n/a: no edit state on the page
Questions: Q-4

### AC-3: Performance numbers carry one sign, Max DD a magnitude, and no dead rows
Requirement items: R-4, R-5, R-10, R-11, R-13, R-24, R-36, R-40, R-69
Rule: When the Performance page renders its risk table, preconditions panel and exit-turnover panel, each delta shall carry exactly one sign character, the drawdown row shall be labelled "Max DD" and show each leg as a positive magnitude, friction drag shall show two decimals, no row shall render for a metric that has no producer, and a cold replay sample shall be reported once at panel level.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | live and shadow `max_drawdown` whose reduction is positive | the risk table renders | the delta cell starts with "↑ +" and contains exactly one "+" character |
| S-3.2 | N1 | a delta of exactly 0 | the risk table renders | the cell contains no arrow and at most one sign character |
| S-3.3 | N1 | a negative delta | the risk table renders | the cell starts with "↓ " and contains exactly one "-" and no "+" |
| S-3.4 | B | live `max_drawdown` -0.132 and shadow -0.128 (fractions as the route returns them) | the risk table renders | the row label reads "Max DD" and the cells read the absolute values as percentages with two decimals and no minus sign |
| S-3.5 | N3 | `max_drawdown` null on one leg | the risk table renders | that cell reads the dash and the other leg still renders |
| S-3.6 | N1 | `max_drawdown` exactly 0 | the risk table renders | the cell reads "0.00%" |
| S-3.7 | B | an exit-turnover entry with `est_annual_friction_drag_pct` set | the panel renders | the drag cell shows the value with exactly two decimals followed by "%" |
| S-3.8 | B | any payload | the risk table renders | no "Upside capture vs SPY" or "Downside capture vs SPY" row exists |
| S-3.9 | N3 | every symphony's replay sample is cold (`verdict` INSUFFICIENT_DATA and `n_obs` 0) and at least one shadow sample is usable | the preconditions panel renders | one panel note states that replay samples are unavailable until the next autotune run; no row carries the replay-unavailable message; shadow rows render |
| S-3.10 | N3 | some symphonies have a cold replay sample and others a usable one | the panel renders | usable replay rows render; the panel note names how many symphonies lack a replay sample; no per-row unavailable message |
| S-3.11 | N3 | a computed metric (for example Sortino) has no value on either leg for the chosen window | the risk table renders | its row renders with dashes |
| S-3.12 | N7 | any payload | the page renders | the headline "Max DD reduction" value, the guard alpha headline, and every metric value other than the drawdown legs' sign equal the ref's |
| S-3.13 | N5 | the page is open | the 60 s poll refreshes three times | the panel note appears once and the table has one row per metric |
| S-3.14 | N6 | `GET /api/guard-alpha-preconditions` fails | the poll runs | the panel keeps its last rendered state |
| S-3.15 | N3 | no symphony has any usable sample | the panel renders | the existing panel-level empty state shows and no panel note duplicates it |

Negative coverage: N1 S-3.2, S-3.3, S-3.6 · N2 n/a: display only · N3 S-3.5, S-3.9, S-3.10, S-3.11, S-3.15 · N4 n/a: the routes are behind the global auth hook, unchanged · N5 S-3.13 · N6 S-3.14 · N7 S-3.12 · N8 n/a: no edit state on the page
Questions: none

### AC-4: Window buttons mean the dashboard's calendar windows, with a default that can be met
Requirement items: R-4, R-6, R-9, R-11, R-12, R-25, R-37, R-69
Rule: When the operator selects a window on Performance or History, the page shall show exactly the calendar span the dashboard's same-labelled token covers (30d, 60d, 90d and 125d as that many calendar days back from today inclusive, YTD from 1 January, 1Y as 365 days, 5Y as 1825 days), the Performance page shall request it from `/api/performance` with the window token (`30d`, `60d`, `90d`, `125d`, `ytd`, `1y`, `5y`) while an integer `days` keeps its trading-day meaning for other callers, and History shall open with 30d selected, and Performance with 60d, the shortest token whose calendar span can hold `_PERFORMANCE_MIN_HISTORY_DAYS` (30) daily observations (`app.py:44`, `:5322`): 30 calendar days hold at most 22 trading days, 60 hold about 41.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | B | daily portfolio rows on every trading day of the last two years | the operator clicks 1Y on Performance | the page requests `days=1y`; the returned series' first date is the first trading day on or after today minus 365 calendar days, and every row on or after that date is included |
| S-4.2 | B | the same rows | the operator clicks 1Y on History | the window covers the same first date as S-4.1 |
| S-4.3 | B | the same rows | the operator clicks 30d, 60d, 90d or 125d on Performance | each series starts at the first trading day on or after today minus that many calendar days |
| S-4.4 | B | none | the operator opens `/history` | the 30d button is active and the first request asks for the 30-day window |
| S-4.5 | B | none | the operator opens `/performance` | the 60d button is active and the first request asks for `days=60d` |
| S-4.6 | N1 | rows that start 200 calendar days ago | the operator clicks 5Y on Performance | the series starts at the first row, and the coverage caption states the actual span |
| S-4.7 | N1 | today is 2 January | the operator clicks YTD | the series holds only rows dated 2 January or later |
| S-4.8 | N3 | fewer than 30 observations in the 60d window, because the history itself is shorter | Performance opens | the existing insufficient-history banner shows with what is available |
| S-4.15 | N1 | the operator clicks 30d on Performance | the response lands | the 30d window shows, with the insufficient-history banner, because 30 calendar days cannot hold 30 observations |
| S-4.9 | N7 | the dashboard detail panel requests `/api/performance?scope=symphony&...&days=60` (`static/index.js:493`) | the panel opens | the returned series is the same as at the ref |
| S-4.10 | N2 | a request to `/api/performance` with `days=abc` or `days=2w` | the route answers | it refuses with 400 and the message names the accepted integers and tokens |
| S-4.13 | N1 | today is 2 January | the operator clicks YTD on History | the window holds only post-mortem days dated 1 January or later |
| S-4.14 | N7 | a caller sends `days=252` | the route answers | the series is the trailing 252 trading-day rows, as at the ref |
| S-4.11 | N5 | the operator clicks 1Y twice | the second click lands | one request per click; the page shows the 1Y result |
| S-4.12 | N6 | `/api/performance` fails | the operator clicks 1Y | the page shows its existing error handling and the 1Y button stays active |

Negative coverage: N1 S-4.6, S-4.7, S-4.13, S-4.15 · N2 S-4.10 · N3 S-4.8 · N4 n/a: the routes are behind the global auth hook, unchanged · N5 S-4.11 · N6 S-4.12 · N7 S-4.9, S-4.14 · N8 n/a: a window click has no pending edit
Questions: Q-5

### AC-5: Every symphony label shows the display name
Requirement items: R-4, R-5, R-10, R-11, R-13, R-26, R-27, R-28, R-39
Rule: When the settings modal, the Performance preconditions and exit-turnover panels, a Frontrunner proposal card, an autotune run card, an observations row or the settings detail header labels a symphony, it shall show the symphony's display name from the live state and keep the Composer hash or internal key at most in a `title` attribute, and when the symphony is not in the live state it shall show the stored identifier unchanged.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | live symphony "Copper Ridge Rotation" with Composer hash Hb3Kd9Pq2Rt7Vw5Xy1Za | the operator clicks its card's gear on `/` | the modal title reads "Copper Ridge Rotation"; the hash appears only in the title element's `title` attribute |
| S-5.2 | B | the same symphony in the preconditions response | the Performance preconditions panel renders | its rows' first cell reads "Copper Ridge Rotation" |
| S-5.3 | B | the same symphony in the exit-turnover data | the exit-turnover panel renders | its row's first cell reads "Copper Ridge Rotation" |
| S-5.4 | B | a pending Frontrunner proposal whose incumbent is that hash | the Frontrunner Builder tab renders | the card title reads "Copper Ridge Rotation" |
| S-5.5 | B | an autotune run for normalized key "copper ridge rotation" | the Overview loads the autotune list | the card name reads "Copper Ridge Rotation" |
| S-5.6 | B | an observation with `symphony_id` "copper ridge rotation" | the Overview renders the observations | the Symphony cell reads "Copper Ridge Rotation" |
| S-5.7 | B | the symphony selected on `/settings` | the detail header renders | the header reads the display name, and the internal key appears at most once, in a `title` attribute |
| S-5.8 | N3 | a Frontrunner proposal whose incumbent hash is no longer in the live state | the card renders | the title reads the hash, as the identity line already does |
| S-5.9 | N3 | an observation whose key matches no live symphony | the row renders | the Symphony cell reads the stored key; an empty key reads the dash |
| S-5.10 | N1 | a display name containing an HTML bold tag and an ampersand | each surface renders it | the characters show literally; no markup is interpreted |
| S-5.11 | N1 | two live symphonies "Harbor Lights Momentum" and "Harbor Lights Momentum v2" | the preconditions panel renders | each row carries its own full name |
| S-5.12 | N6 | `GET /api/performance/symphonies` fails | the Performance panels render | rows fall back to the identifier the panel already has; the panels still render |
| S-5.13 | N7 | the modal is open | the operator saves a lock change | the modal sends the same request to `/api/symphony-settings/Hb3Kd9Pq2Rt7Vw5Xy1Za` as at the ref |
| S-5.14 | N5 | the modal was opened for one symphony and closed | the operator opens it for another | the title shows the second symphony's name |

Negative coverage: N1 S-5.10, S-5.11 · N2 n/a: labels only, nothing is refused · N3 S-5.8, S-5.9 · N4 n/a: every route is behind the global auth hook, unchanged · N5 S-5.14 · N6 S-5.12 · N7 S-5.13 · N8 n/a: a label has no edit state; the modal's pending edits are untouched by the title change and covered by S-5.13
Questions: Q-2

### AC-6: The History day drilldown names each symphony in time order
Requirement items: R-4, R-9, R-10, R-11, R-12, R-13, R-38
Rule: When the operator clicks a day's bar on the History page, the exits table shall list that day's exits ordered by trigger time, earliest first, each naming its symphony by the name the post-mortem recorded.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | a post-mortem day with exits at `time_triggered` 11:07, 11:33 and 10:12, each with a `symphony_name` | the operator clicks that day's bar | three rows in the order 10:12, 11:07, 11:33, each showing its symphony name |
| S-6.2 | N3 | an exit with no `symphony_name` | the drilldown renders | that row's Symphony cell reads "name not recorded" |
| S-6.3 | N3 | an exit with no trigger time | the drilldown renders | it is listed after every timed exit |
| S-6.4 | N1 | two exits with the same trigger time | the drilldown renders | both are listed, in file order |
| S-6.5 | N3 | a day with a bar but no valid exits | the operator clicks its bar | the heading reads that date with "(0)" and the table shows the existing placeholder row, as at the ref |
| S-6.6 | N7 | any window | the payload is returned | `todays_exits` is the same as at the ref, and a `daily_exits` entry carries every field it carried at the ref |
| S-6.7 | N5 | one day is drilled into | the operator clicks another day | the table shows only the second day's exits |
| S-6.8 | N1 | a trigger whose `if_held_source` is not recognised | the payload is built | the exit is excluded as at the ref (`analytics.is_valid_post_mortem_entry`) |

Negative coverage: N1 S-6.4, S-6.8 · N2 n/a: display only · N3 S-6.2, S-6.3, S-6.5 · N4 n/a: the route is behind the global auth hook, unchanged · N5 S-6.7 · N6 n/a: the drilldown reads the already-fetched payload, with no request of its own · N7 S-6.6 · N8 n/a: no edit state
Questions: Q-6

### AC-7: Banners, notes and Strategy Builder cards say true things once
Requirement items: R-4, R-5, R-10, R-11, R-29, R-30, R-31, R-41
Rule: When the AI Advisor page renders, the Strategy Builder risk banner shall name Strategy Builder, the Correlations caveat shall state "Correlations destabilize toward 1.0 in market stress." exactly once, every note and placeholder shall name the suggestion control by its on-screen label with no reference to a gear icon, and each Strategy Builder card, whether rendered from stored observations or by a live run (`static/ai_advisor.js:928-929`), shall show a human objective label, a human source label and a title that leads with the objective.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | none | the Strategy Builder tab renders | the banner's headline names Strategy Builder; the banner contains no "Logic-change" |
| S-7.2 | B | at least two symphonies with series | the Correlations tab renders | the caveat's visible text contains the stress sentence exactly once and still contains the rest of `CRISIS_CAVEAT` |
| S-7.3 | B | none | the Overview renders | the observations note and the suggestions placeholder name "Run Claude advisor" and contain no "gear icon" |
| S-7.4 | B | a rejected candidate with objective `volatility_mitigation` and template `built-new` | the Strategy Builder tab renders | the card reads "Objective: Volatility mitigation" and "Source: Built new"; its title leads with "Volatility mitigation" and keeps the candidate id as a secondary label |
| S-7.5 | N1 | candidates with each of the four objective values and both template values | the tab renders | every card shows a label from the same mapping on survivor and rejected cards; none shows an underscore |
| S-7.6 | N3 | a candidate whose objective is absent or not one of the four | the tab renders | the card reads "Objective: not recorded" |
| S-7.7 | N3 | a candidate whose template is absent | the tab renders | the card reads "Source: not recorded" |
| S-7.8 | N7 | the Logic Changes tab | it renders | its banner text is the same as at the ref |
| S-7.9 | N7 | the Strategy Builder banner | it renders | it stays non-dismissible and keeps its body sentences about the FDR correction and selection bias |
| S-7.10 | N3 | no correlation pairs exist | the Correlations tab renders | the caveat still renders, with the stress sentence once |
| S-7.11 | B | a live Strategy Builder run returns a rejected candidate with objective `cut_drawdown` and template `atlas-suggested` | the run's cards render in place | the card reads "Objective: Cut drawdown" and "Source: Atlas community", its title leads with "Cut drawdown" and keeps the candidate id secondary, the same labels a stored card shows |

Negative coverage: N1 S-7.5 · N2 n/a: copy only · N3 S-7.6, S-7.7, S-7.10 · N4 n/a: the page is behind the global auth hook, unchanged · N5 n/a: static server-rendered copy, the same on every load · N6 n/a: the copy depends on no request · N7 S-7.8, S-7.9 · N8 n/a: no edit state
Questions: none

### AC-8: The Settings controls do what they show
Requirement items: R-4, R-6, R-9, R-11, R-12, R-32
Rule: When the operator uses `/settings`, "Reset to globals" shall clear that symphony's pending parameter overrides and locks, show every override field empty with its global value beside it, enable Save and Discard, and send no request until Save; the Live execution toggle shall show the current mode read-only with the reason the dashboard cannot change it; and if a pending override is not a number, then Save shall name the field and send nothing.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-8.1 | B | a symphony whose key is "(alpha) copper + ridge 5 ways" with two stored overrides | the operator selects it and clicks Reset to globals | no console error; every override input is empty; Save and Discard are enabled; no request is sent |
| S-8.2 | N1 | symphony keys containing parentheses, plus signs, a quote, a hash sign and non-ASCII letters | the operator selects each and clicks Reset to globals | each reset behaves as S-8.1 |
| S-8.3 | N8 | a reset is pending | the operator clicks Discard | the stored overrides and locks show again and Save is disabled |
| S-8.4 | B | a reset is pending | the operator clicks Save | one `POST /api/settings` carries that symphony with empty params and no locks, and the list entry reads "uses globals" |
| S-8.5 | N8 | the operator edited symphony A's override to a number and has not saved | the operator resets symphony B | A's pending edit is kept; Save sends both |
| S-8.6 | N2 | the CSRF token is unavailable | the operator saves after a reset | the route refuses with 403 and nothing is written; the page's failure alert shows; the pending reset stays |
| S-8.7 | N5 | the operator clicks Reset to globals twice | the second click lands | the state equals one reset; still no request |
| S-8.8 | N7 | a reset is pending for one symphony | the operator inspects the others | their overrides, locks and counts are unchanged |
| S-8.9 | N8 | an override input holds an unsaved value | the operator clicks Reset to globals for that symphony | the input clears and the pending value is dropped, and Discard restores the stored value |
| S-8.10 | N6 | `GET /api/settings` fails on load | the page loads | the existing load failure handling shows and no Reset button is bound to stale data |
| S-8.11 | B | `LIVE_EXECUTION` is False in `.env` | the operator opens Master controls and clicks the Live execution toggle | the card reads DRY RUN and states that live trading is armed in the server's `.env`, not from the dashboard; the click changes nothing, marks nothing dirty and adds nothing to Save |
| S-8.12 | B | `LIVE_EXECUTION` is True in `.env` | the page renders | the card reads LIVE, read-only, with the same statement |
| S-8.13 | N1 | a pending override holds "abc" | the operator clicks Save | the page names that field as not a number and sends no request; the globals in `.env` are unchanged |
| S-8.14 | N1 | a symphony key containing spaces only | the operator selects it and clicks Reset to globals | the reset behaves as S-8.1 with no TypeError |
| S-8.15 | N7 | the save path | any Save is sent | `POST /api/settings` receives the same body shape as at the ref and never a `LIVE_EXECUTION` key |

Negative coverage: N1 S-8.2, S-8.13, S-8.14 · N2 S-8.6 · N3 n/a: the reset and the toggle read only the loaded settings, with no computed value · N4 n/a: the page and the POST are behind the global auth and CSRF hooks, unchanged · N5 S-8.7 · N6 S-8.10 · N7 S-8.8, S-8.15 · N8 S-8.3, S-8.5, S-8.9
Questions: Q-1, Q-9

### AC-9: An override equal to the global is not counted
Requirement items: R-4, R-9, R-11, R-35, R-69
Rule: When `/settings` lists the tracked symphonies, each entry shall count only parameters whose stored per-symphony value differs from the global value, read "uses globals" only when the symphony stores no parameter, state how many stored values equal the global and stay pinned when it stores some, and mark a detail row as overridden only when its value differs.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-9.1 | B | a symphony storing 8 parameters, 2 of which differ from the globals | the list renders | the entry reads "2 overrides" |
| S-9.2 | N1 | a stored value "0.5" and a global value 0.5 | the list renders | the parameter is not counted |
| S-9.3 | N1 | a stored value 0.50 and a global "0.5" | the list renders | the parameter is not counted |
| S-9.4 | B | 8 stored values, every one equal to its global | the list renders | the entry reads "0 overrides" and "8 pinned at the global value", with a tooltip saying those values will not follow a later global change; its lock count still shows |
| S-9.10 | B | a symphony storing no parameters | the list renders | the entry reads "uses globals" |
| S-9.5 | N3 | a parameter with no global value | the list renders | the parameter counts as an override when the symphony stores a value for it |
| S-9.6 | N1 | a non-numeric stored value equal as text to the global | the list renders | the parameter is not counted |
| S-9.7 | B | a symphony with one differing parameter | the operator opens its detail | only that row carries the overridden marker |
| S-9.8 | N8 | the operator types a value equal to the global into an empty override input | the list meta refreshes | the entry's count does not change |
| S-9.9 | N7 | any symphony | the list renders | the stored parameters and the payload Save sends are unchanged by counting |

Negative coverage: N1 S-9.2, S-9.3, S-9.6 · N2 n/a: display only · N3 S-9.5 · N4 n/a: the page is behind the global auth hook, unchanged · N5 n/a: counting is a pure function of the loaded data · N6 n/a: counting runs only after a successful load, covered by S-8.10 · N7 S-9.9 · N8 S-9.8
Questions: Q-7

### AC-10: The verification chip is never a green "clean" over unverifiable checks
Requirement items: R-4, R-10, R-11, R-13, R-34, R-69
Rule: If the Market Prism numeric verification's verdict is `clean` and its passing count is below its check count, then the Overview's verdict pill shall not use the pass style and shall read the passing count, "of", the check count and "verified" (for example "18 of 32 verified"), and only when every check passes shall it read "clean" in the pass style.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-10.1 | B | verdict `clean`, `n_checks` 32, `n_pass` 32, `n_unverifiable` 0 | the Overview renders | the pill reads "clean" in the pass style |
| S-10.2 | B | verdict `clean`, `n_checks` 32, `n_pass` 18, `n_unverifiable` 14 | the Overview renders | the pill reads "18 of 32 verified" in the unverifiable style |
| S-10.3 | N3 | verdict `clean` with `n_pass` or `n_checks` absent from the summary | the Overview renders | the pill reads "verified count unavailable" in the unverifiable style |
| S-10.4 | N3 | verdict `flags-detected` with unverifiable checks | the Overview renders | the pill keeps the flagged label and style, as at the ref |
| S-10.5 | N3 | verdict `no-numeric-claims` | the Overview renders | the pill reads as at the ref |
| S-10.6 | N3 | no verification row for the run | the Overview renders | no verification block renders, as at the ref |
| S-10.7 | N7 | any verification | the Overview renders | the count line, the actionable check badges and the "Show all" list render as at the ref |
| S-10.8 | N1 | verdict `clean`, `n_checks` 10, `n_pass` 9, `n_unverifiable` 0 (one check of no recognised class) | the Overview renders | the pill reads "9 of 10 verified" in the unverifiable style |
| S-10.9 | N3 | verdict `no-verifiable-claims` (checks exist, none passed) | the Overview renders | the pill reads as at the ref |

Negative coverage: N1 S-10.8 · N2 n/a: display only · N3 S-10.3, S-10.4, S-10.5, S-10.6, S-10.9 · N4 n/a: the page is behind the global auth hook, unchanged · N5 n/a: the pill is server-rendered once per page load · N6 n/a: a failed verification fetch already renders no block, covered by S-10.6 · N7 S-10.7 · N8 n/a: no edit state
Questions: none

## Rule-by-Entry-Point Matrix
| Rule | Settings modal (`/`) | Performance preconditions | Performance exit turnover | Frontrunner card | Autotune card | Observations row | Settings detail header |
|---|---|---|---|---|---|---|---|
| AC-5 display name, hash only in `title` | S-5.1 | S-5.2 | S-5.3 | S-5.4 | S-5.5 | S-5.6 | S-5.7 |
| AC-5 identifier fallback when not in live state | n/a: the modal opens only from a live card | S-5.12 | S-5.12 | S-5.8 | n/a: autotune rows exist only for live symphonies, and S-5.9's rule covers a stale key | S-5.9 | n/a: the list is built from the live state |

| Rule | Performance window picker | History window picker | Dashboard detail panel request |
|---|---|---|---|
| AC-4 calendar window per token | S-4.1, S-4.3, S-4.7 | S-4.2, S-4.13 | S-4.9, S-4.14 |
| AC-4 opens on its default (Performance 60d, History 30d) | S-4.5 | S-4.4 | n/a: the panel has no window picker |

## Not Building
Non-goals:
- NB-1: Human labels for the algorithm parameters on the settings page and modal (audit M3, S4); no label vocabulary exists in the code (`app.py:6014-6024` holds a help text and unit per key) (excludes nothing; approved by the PM, [PM-ASSUMED]; overview NB-12).
- NB-2: A Sortino ratio on autotune cards: the only Sortino-named column the route returns, `sortino_sentinel_pct` (`database.py:865`), is one of the never-written `autotune_runs` columns documented in `DE-AUDIT-BL5-12-001`, so no Sortino ratio exists to show (approved by the PM, [PM-ASSUMED], operator may override).
- NB-11: The server-side partial write of `POST /api/settings` (globals written before a symphony parameter fails `float`, `app.py:6150-6167`); AC-8 stops the page from sending a non-numeric value, and the route's ordering is a backlog item (approved by the PM, [PM-ASSUMED], operator may override).
- NB-12: The settings modal's own Live toggle and its route (`POST /api/symphony-settings/`), which belong to the keying defect in NB-9.
- NB-3: Changing which strategy row the settings modal reads or writes (see NB-9).

Behaviour that stays exactly as it is:
- NB-4: Every value computed by `analytics.py`, `reporting.py`, the post-mortem files and the routes, except the window cutoff of `/api/performance` (AC-4) and the added `symphony_name` on History drilldown entries (AC-6).
- NB-5: The settings save path: `POST /api/settings` writes the same keys through the same allowlist (`app.py:6127-6167`); Reset only prepares a pending edit.
- NB-6: The Logic Changes banner, the Strategy Builder banner's body and its non-dismissibility, and the Frontrunner identity, explainer and overlay lines.

Deferred:
- NB-7: The History daily bars' missing date axis and value scale (audit H4) (approved by the PM, [PM-ASSUMED]; overview NB-13).
- NB-8: Re-checking the Strategy Builder and Frontrunner run forms with keys (approved by the PM, [PM-ASSUMED]; overview NB-7).

Protected surfaces consumed, not changed:
- NB-9: The per-symphony modal's strategy and live-mode keying (`app.py:6170-6290`), the engine's strategy read (`alpha_bot_execution.py:1242-1248`), `LIVE_EXECUTION` and the settings write allowlist (`app.py:6113-6124`, `_SETTINGS_WRITE_ALLOWLIST` at `app.py:6118`); the modal title change reads the display name only; the keying defect goes to its own sensitive plan (approved by the PM, [PM-ASSUMED]; overview NB-14).
- NB-10: `alpha_bot_execution.py` and `math_engine.py` carry zero diff.

## Defect Classes
| DC-n | Class (what goes wrong) | Trigger (B or N1-N8) | Sibling search (tool, query, scope) | Scenarios | Raised in review as |
|---|---|---|---|---|---|
| DC-1 | An internal identifier shown where a display name belongs | B | grep `symphony_id\|_currentSymphonyId\|normalized_name\|sym\.id\|candidate_id` in `templates/` and `static/`, PlanetStopper; stop when a widening finds only sites already judged | S-5.1, S-5.2, S-5.3, S-5.4, S-5.5, S-5.6, S-5.7, S-6.1, S-7.4 | - |
| DC-2 | An absent value rendered as a plausible default (empty pill, "Saved" over a loss, dash rows with no producer) | N3 | grep `escHtml(.*\|\| '')\|placeholderRow\|TIER2_PLACEHOLDER` in `static/`, and every nullable field this plan renders, PlanetStopper | S-1.2, S-1.3, S-2.4, S-3.8, S-3.9, S-10.3 | - |
| DC-3 | One label meaning a different value on another page | B | grep `data-value=\|data-window=\|currentWindow\|max_drawdown` in `templates/` and `static/`, PlanetStopper | S-4.1, S-4.2, S-4.3, S-3.4 | - |
| DC-4 | A DOM selector built from a raw data value | N1 | grep `querySelector(.*+\|getElementById(.*+` in `static/`, PlanetStopper | S-8.1, S-8.2 | - |
| DC-5 | A pending edit lost or applied to the wrong symphony | N8 | grep `_pendingOverrides\|markDirty\|clearDirty` in `static/settings.js`, PlanetStopper | S-8.3, S-8.5, S-8.9 | - |
| DC-6 | A changed string, value or field not carried to a consumer, including tests pinning the old one | B | grep `252\|1260\|Logic-change proposals carry\|gear icon\|Run Advisor\|\$ Saved\|currentWindow = '90'\|daily_exits\|upside_capture\|data-unavail\|TIER2\|naive_sharpe\|Sortino\|live-toggle\|uses globals` in `templates/`, `static/`, `app.py` and `tests/`, PlanetStopper | S-4.9, S-6.6, S-7.3 | - |
| DC-7 | A sign family asserted on one member only | N1 | read: every test of `fmtDelta`, `format_dollar_saved` and the drawdown cell; each of positive, zero, negative and null has a case | S-3.1, S-3.2, S-3.3, S-2.1, S-2.2, S-2.3 | - |

## Worked Example
Performance, 30d window, live `max_drawdown` -0.1320 and shadow -0.1282, live `sharpe` 0.84 and shadow 0.99: the drawdown row reads "Max DD | 13.20% | 12.82%", the Max DD reduction delta reads "↑ +0.38pp" with one plus sign, and the Sharpe delta reads "↑ +0.150" (S-3.1, S-3.4). On History at 30d with post-mortem dollars summing to -309.40 over 108 exits, the dollar tile reads "Dollar result" over "$309.40 lost" (S-2.2). On `/settings`, symphony "(alpha) copper + ridge 5 ways" stores 8 parameters equal to the globals: its entry reads "0 overrides" and "8 pinned at the global value"; clicking Reset to globals empties its overrides and enables Save without a request, and after Save the entry reads "uses globals" (S-9.4, S-8.1, S-8.4). On the Overview, a verification with 18 of 32 checks passing and 14 unverifiable shows the pill "18 of 32 verified" in the neutral style (S-10.2).

Failure case: the Composer hash Hb3Kd9Pq2Rt7Vw5Xy1Za of a pending Frontrunner proposal is no longer in the live state, so the card title reads Hb3Kd9Pq2Rt7Vw5Xy1Za, as its identity line already does (S-5.8); and a verification row whose summary lacks `n_pass` or `n_checks` shows "verified count unavailable", never a green "clean" (S-10.3).

## Fail-Closed Behaviour
Refusals on the paths this plan touches, each unchanged at the route:
- `POST /api/settings`, no valid CSRF token: 403 HTML from the global hook (`app.py:379`, `_csrf_before_request`); the page shows its failure alert and keeps the pending edits (S-8.6).
- `POST /api/settings`, a globals key outside `_SETTINGS_WRITE_ALLOWLIST`: 400 "Rejected keys not in settings allowlist" (`app.py:6139-6147`); after AC-8 the page never sends `LIVE_EXECUTION`, so this refusal is not reached from the page (S-8.11, S-8.15).
- `POST /api/settings`, a non-numeric symphony parameter: 500 "An internal error occurred" after the globals are written (`app.py:6150`, `:6160`, `:6165-6167`); after AC-8 the page refuses the Save before sending (S-8.13), and the route's ordering stays a backlog item (NB-11).
- `GET /api/performance`, a `days` value that is neither an integer nor an accepted token: 400 (`app.py:5226-5234`), with the accepted tokens added by AC-4 (S-4.10).

## Authorities Touched
| Value | Authority used | What it replaced | What proves no second source |
|---|---|---|---|
| A symphony's display name | the `name` field of the symphony's `bot_state` entry through `advisors.frontrunner_builder.resolve_incumbent_display_name` (hash keys) or the `{id, name}` list of `/api/performance/symphonies`; for normalized keys, the live state's names normalised with `database.normalize_name` | The hash or the lowercase key | A grep for `_currentSymphonyId`, `symphony_id` and `normalized_name` in visible text returns only `title` attributes and fallbacks (DC-1) |
| A window's start date | `analytics._window_cutoff_date` | Trailing trading-day row counts in `/api/performance` | The Performance and History requests for 1Y return the same first date (S-4.1, S-4.2) |
| The dollar saved or lost text | `analytics.format_dollar_saved` and its existing JavaScript twin on the dashboard (`static/index.js`, `dollar-saved-verb`) | A fixed "$ Saved" label | The History tile text equals `format_dollar_saved(total_saved)` (S-2.1) |
| A parameter's global value | `GET /api/settings` `globals` | The presence of a stored key | The count changes only when a value differs (S-9.1 to S-9.6) |

## Contract Changes and Consumers
- `GET /api/performance`: before, `days` accepted an integer (trailing trading-day rows, `app.py:5245-5248`) or `ytd`; after, it also accepts the tokens `30d`, `60d`, `90d`, `125d`, `1y` and `5y` as calendar windows, an integer keeps its trading-day meaning, and anything else is refused with 400. Consumers: `static/performance.js` (sends tokens, updated by this plan) and the dashboard detail panel's `days=60` request (`static/index.js:493`), which sees the same series as at the ref (S-4.9, S-4.14). Backward compatible.
- `GET /api/autotune-runs`: each row gains the objective its spec bundle sets and the symphony's display name as `symphony_name` (both additive; the response stays a bare JSON array). Consumer: `static/ai_advisor.js`.
- `GET /api/performance`: the response gains `window_start`, the first calendar date of the window it served (additive). Consumer: `static/performance.js`.
- `GET /api/history/ytd`: a new route form, so History's YTD resolves from 1 January on the server; the integer form is unchanged. Consumer: `static/history.js`.
- `analytics._WINDOW_TRAILING_DAYS` gains `5y` (1825 calendar days), additive; the existing tokens keep their values.
- `analytics.format_dollar_saved` adopts the suite's shared rounding rule, ties rounding the way JavaScript rounds, so the Python and page renderings of one figure agree. Consumers: `reporting.py`'s Discord "Total" and Managed Sleeves gain/loss lines, whose text changes on half-cent ties.
- `GET /api/symphony-settings/` followed by a symphony id: gains the symphony's display name (additive). Consumer: `static/settings-modal.js`.
- `GET /api/history/` followed by a day count: each `daily_exits` entry gains `symphony_name` (additive). Consumer: `static/history.js`.

## Breaking Changes
The meaning of Performance's window buttons changes (the route's integer `days` does not): 1Y covers 365 calendar days instead of 252 trading days, 5Y 1825 calendar days instead of 1260 trading days, and 30d to 125d calendar days instead of trading days; every metric on the page moves for the same button. Performance still opens on 60d (now 60 calendar days); History opens on 30d instead of 90d. Discord's dollar lines, rendered through `analytics.format_dollar_saved`, can differ by one cent on a half-cent tie, because the shared rounding rule rounds ties the way JavaScript does.

## Security Considerations
- Input validation and injection: display names are operator-entered in Composer and reach six new text sites; every one renders through autoescaping or `textContent`, never `innerHTML` with an unescaped name or `| safe` (S-5.10). The reset selector no longer interpolates raw keys into a CSS selector (DC-4, S-8.2).
- Authorization and access control: no route gains or loses a guard; every route stays behind `_auth_before_request` and every write behind `_csrf_before_request`.
- Data exposure: the Composer hash moves from visible text to a `title` attribute on the same authenticated pages; nothing new is exposed.
- Client-side rendering: the History drilldown's new name field renders through the existing escaping helper.
- Abuse or rate limits: no new request loop; Reset sends nothing until Save.
- Money: Save after Reset writes empty per-symphony parameters that the engine then reads in place of the stored ones (`alpha_bot_execution.py:1242-1249`), so AC-8 is marked money in the Sensitive header (Q-1).

## Design-System Mapping
| Component | Element | Required primitive or token | Notes |
|---|---|---|---|
| Autotune run card | objective-named score label, tooltip and value | existing `.mono-bold` value span | label text only |
| Autotune run card | "Frozen-eval not evaluated" | `--studio-ink-dim` text | replaces the empty pill |
| History hero | dollar tile label and word | existing `.hero-stat-label` and `.hero-stat-value`; word coloured `--studio-pos` or `--studio-neg` | matches the dashboard's `dollar-saved-verb` |
| Performance risk table | "Max DD" row, delta cells | existing table classes; `--studio-neg` for drawdown legs | no new class |
| Performance preconditions | panel note | existing `.precond-row-unavailable` text style moved to one note line | no per-row note |
| Frontrunner card | title | existing `.card-candidate-id` slot showing the name | hash in `title` |
| Verification pill | the "18 of 32 verified" form | existing `prism-verify-badge--unverifiable` | no new token |
| Strategy Builder card | objective, source and title text | existing `.objective-strip` and `.card-candidate-id` | label mapping only |
| Settings list entry | "uses globals", the override count and the pinned count | existing `.sym-uses-globals` and `.sym-has-override` | |

## Definition of Done (Verification)
| AC | Scenarios | Test level | Real-entrypoint test |
|---|---|---|---|
| AC-1 | S-1.1, S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8, S-1.9, S-1.10, S-1.11 | JavaScript renderer under node with captured `/api/autotune-runs` rows; route test that each row carries its bundle's objective | the rendered `/ai-advisor` page with the route stubbed by a captured payload |
| AC-2 | S-2.1, S-2.2, S-2.3, S-2.4, S-2.5, S-2.6, S-2.7, S-2.8 | `renderHero` under node; template render | `GET /history` render plus `GET /api/history/30` |
| AC-3 | S-3.1, S-3.2, S-3.3, S-3.4, S-3.5, S-3.6, S-3.7, S-3.8, S-3.9, S-3.10, S-3.11, S-3.12, S-3.13, S-3.14, S-3.15 | `fmt`, `fmtDelta`, row builders under node | `GET /performance` render with captured route payloads |
| AC-4 | S-4.1, S-4.2, S-4.3, S-4.4, S-4.5, S-4.6, S-4.7, S-4.8, S-4.9, S-4.10, S-4.11, S-4.12, S-4.13, S-4.14, S-4.15 | route test of `/api/performance` windows; template default | `GET /api/performance?scope=aggregate&...` for each window, `GET /history`, `GET /performance` |
| AC-5 | S-5.1, S-5.2, S-5.3, S-5.4, S-5.5, S-5.6, S-5.7, S-5.8, S-5.9, S-5.10, S-5.11, S-5.12, S-5.13, S-5.14 | renderers under node; template render; route test | `GET /ai-advisor`, `GET /performance`, `GET /api/symphony-settings/Hb3Kd9Pq2Rt7Vw5Xy1Za`, `GET /settings` |
| AC-6 | S-6.1, S-6.2, S-6.3, S-6.4, S-6.5, S-6.6, S-6.7, S-6.8 | `analytics.get_history_summary` unit; `renderDayDrilldown` under node | `GET /api/history/30` with captured post-mortem files |
| AC-7 | S-7.1, S-7.2, S-7.3, S-7.4, S-7.5, S-7.6, S-7.7, S-7.8, S-7.9, S-7.10, S-7.11 | template render; the live-run card renderer under node with a captured run response | `GET /ai-advisor`; a live Strategy Builder run on an instance with keys |
| AC-8 | S-8.1, S-8.2, S-8.3, S-8.4, S-8.5, S-8.6, S-8.7, S-8.8, S-8.9, S-8.10, S-8.11, S-8.12, S-8.13, S-8.14, S-8.15 | `renderSymphonyDetail`, `initLiveToggle` and the save builder under a real DOM (jsdom or the browser), not a stub; route test of `POST /api/settings` refusals | live render of `/settings` with clicks on Reset, Discard, Save and the live toggle |
| AC-9 | S-9.1, S-9.2, S-9.3, S-9.4, S-9.5, S-9.6, S-9.7, S-9.8, S-9.9, S-9.10 | `renderSymphonies` under node | `GET /settings` plus `GET /api/settings` |
| AC-10 | S-10.1, S-10.2, S-10.3, S-10.4, S-10.5, S-10.6, S-10.7, S-10.8, S-10.9 | `_build_verdict_display` and template render | `GET /ai-advisor` with a captured verification row |

Gates: `ruff format --check`; `ruff check`; a scoped pytest run of `tests/ai_advisor`, `tests/app`, `tests/analytics`, `tests/dashboard`, `tests/ui` and `tests/js_syntax` with at most 4 workers, never `-n auto`; CI green on the exact SHA; after `/review`, the PM's live functional check on the droplet of T-1 to T-7.
Fixture provenance: post-mortem files, autotune rows, verification rows, preconditions and exit-turnover payloads are captured from the production database copy the audit used (one record per case the code distinguishes), with real symphony names and Composer ids kept and no account ids (overview SD-11); the records for S-5.10 (a name with markup) and S-8.2 (keys with special characters) are derived from real records with only the name or key changed, signed off by the PM (overview SD-9); no fixture is written alongside its parser.
Screenshots: History, Performance, Settings, the settings modal, and the Advisor Overview, Correlations, Strategy Builder and Frontrunner Builder tabs at 1440x900 and 375x812, light and dark, compared with the audit's captures of the same screens and with the dashboard reference.

## Known-Unconfirmed and Standing Risk
- Whether production post-mortem files always carry `symphony_name` on every trigger: the producer writes it (`reporting.py:115`); older files are unverified. Read-only check: count triggers lacking the key across `post_mortems/post_mortem_*.json` on the droplet.
- Settled from the code: each autotune row's objective is recoverable from its `spec_bundle_id` (`autotuner.py:3244`) and the bundle's facets (`autotuner.py:2428-2436`); rows written before migration 020 may carry a null bundle, which S-1.11 covers.
- How the per-symphony settings modal's hash-keyed rows differ from the engine's name-keyed rows in production. Read-only query: `SELECT symphony_name, live_mode, parameters FROM symphony_strategies` on the droplet's state database, comparing hash-shaped names with display-name rows (Q-2).

## Open Questions
| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | The intake marks this plan not sensitive, but a Save after Reset to globals changes the parameters the live engine reads for that symphony. Mark AC-8 money? | AC-8 | Yes: Sensitive reads money (AC-8), so qa writes hidden scenarios for it | approved by the PM (pushback accepted): plan 01 is Sensitive money (AC-8), [PM-ASSUMED] |
| Q-2 | The settings modal reads and writes the strategy row keyed by the Composer hash while the engine reads the row keyed by the display name, so its live-mode toggle and locks may never reach the engine. Fix here or in its own plan? | AC-5 | Its own sensitive plan; this plan changes only the modal title (NB-9) | approved by the PM: its own sensitive plan, the suite's highest-priority follow-up (overview NB-14), [PM-ASSUMED] |
| Q-3 | removed: answered by the PM's revised D-9 (G-38): Performance opens on 60d | AC-4 | none | none needed: removed |
| Q-4 | What label does the History dollar tile take once the direction moves onto the value? | AC-2 | "Dollar result" | default approved by the PM, [PM-ASSUMED], operator may override |
| Q-5 | Do Performance's 30d, 60d, 90d and 125d also become calendar windows, not only 1Y and 5Y? | AC-4 | Yes, every button means the dashboard's token | default approved by the PM, [PM-ASSUMED], operator may override |
| Q-6 | Earliest-first or latest-first in the History day drilldown? | AC-6 | Earliest first | default approved by the PM, [PM-ASSUMED], operator may override |
| Q-7 | How are a stored value and a global compared, and what does the entry say about values equal to the global, which still pin the symphony? | AC-9 | As numbers when both parse as finite numbers, else as exact text; the entry reads "0 overrides" and "N pinned at the global value" | default approved by the PM, [PM-ASSUMED], operator may override |
| Q-8 | The value the card labels "Sortino" is the optimizer's best objective score (CRRA expected utility for Phase-1 bundles, Sortino otherwise), not a Sharpe ratio, so G-17's "Sortino shows Sharpe" misreads the column name. What should the card call it? | AC-1 | Name each row's actual objective, read from its spec bundle ("Best CRRA-EU score" or "Best Sortino score"), with a tooltip saying it is the best trial before the selection haircut; "Best objective score" naming both only when the bundle cannot be read | approved by the PM: name each row's actual objective from its spec bundle, with the fallback as stated, [PM-ASSUMED] |
| Q-9 | The Live execution toggle on `/settings` can never be saved, because `LIVE_EXECUTION` is outside the write allowlist by design. Make it read-only with its reason, or remove it? | AC-8 | Read-only, showing the current mode and that live trading is armed in the server's `.env` | default approved by the PM, [PM-ASSUMED], operator may override |

## Decisions
| ID | Decision | Rationale |
|---|---|---|
| DEC-1 | The card names `naive_sharpe` as the best score of the run's own objective, read from its spec bundle, never as a Sharpe ratio | The value is the Optuna study's best objective (`autotuner.py:2776`), whose kind depends on the run's spec bundle |
| DEC-2 | A cold replay sample is reported once at panel level, counting the symphonies affected | One note carries the same fact the 12 interleaved rows carried |
| DEC-3 | The settings detail header keeps the internal key only in a `title` attribute | The key is useful for support and is not a display name (SD-1) |
| PM-1 | The objective rule (an explicit `objective_kind` facet, else CRRA `utility_family` means CRRA-EU, else Sortino) moves from `autotuner.py:2428-2436` into `database.objective_kind_for_facets` unchanged, pinned by a golden test of autotuner's objective selection | PM ruling on the Gate-2 approach; the route and the autotuner then read one rule |
| PM-2 | The four additive contract items (`symphony_name` on autotune rows, `window_start`, `GET /api/history/ytd`, the `5y` token) are part of this plan | PM ruling on the Gate-2 approach |
| PM-3 | AC-7 covers the cards a live Strategy Builder run renders as well as stored ones | PM ruling on the Gate-2 approach |
| PM-A | Plan 01 approved at Step 7 under the project's standing autonomy directive (operator unavailable), with its Gate-2 approach `feature-plans/ux-overhaul/approach/01-data-truth.md` and the suite approach `feature-plans/ux-overhaul/approach/00-suite.md`; every open question takes its default or its recorded ruling, and every Not Building entry is approved | [PM-ASSUMED]; the operator may override |

## Docs to Update
- `docs/generated/static_performance_js.md`, `docs/generated/static_history_js.md`, `docs/generated/app.md`, `docs/generated/analytics.md` (one per touched module), and `docs/generated/INDEX.md`.
- `README.md` where it describes the Performance windows.
- A `DECISIONS.md` entry appended at ship by the doc writer.
- A drafted `.claude/CLAUDE.md` Key Files row update for `static/performance.js`, `static/history.js`, `static/settings.js` and `static/settings-modal.js`, for PM approval.

## Follow-Ups
- Human parameter labels (NB-1): overview NB-12.
- The History daily bar axis (NB-7): overview NB-13.
- The settings modal keying defect (NB-9, Q-2): overview NB-14, its own sensitive plan.
- Run-form re-check with keys (NB-8): overview NB-7, the Suite Gate's live test.
