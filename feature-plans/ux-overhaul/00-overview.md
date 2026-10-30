# Epic: UX overhaul of the PlanetStopper dashboard app
Status: planned (approved by the PM at Step 7 with approach/00-suite.md, [PM-ASSUMED], operator may override)
Repos: PlanetStopper
Ref: PlanetStopper@37c8e983b64c6980c28199a3f9318486b60f3864

## North Star
Every page and tab of the PlanetStopper dashboard app (`/history`, `/performance`, `/settings` with the per-symphony settings modal, `/login`, and the seven AI Advisor tabs) shows the operator true numbers under correct labels, names symphonies by their display names, keeps every control working, and is built from the same header, panel, card, label and stamp primitives as the main dashboard `/`, which the operator named the only page that looks right; the Retirement analysis and a realized-basis Correlations heatmap are visible on the droplet without scrolling past unrelated content, and every control in the shared header stays reachable at phone width. The evidence is the app-wide rendered UX audit at the ref: 25 assertable findings (UX-1 to UX-25, 12 blocking), per-surface findings (G1 to G7, D1 to D3, M1 to M4, H1 to H5, P1 to P7, S1 to S4, L1 and L2, A1 to A4, O1 to O8, C1 to C6, CH1 to CH3, SB1 and SB2, F1 to F4) and decisions D-1 to D-12, captured on a copy of the production state database in Firefox at 1440 px and 375 px, light and dark. The work is split into five plans because it spans five surfaces with different files, owners and risks: data and label truth (01), the Advisor Overview and a Retirement tab (02), the Correlations matrix on a new data basis (03), the shared shell and every page header (04), and the dashboard's own charts (05); one approval per plan keeps each PR small enough for a reviewer while the stack lands as one unit. New layouts the audit says need design (Claude Design brief items B1 to B5) are follow-ups, not part of this suite.

## Requirements as Given
[G-1] and yet, here i am, not seeing any fucking retirement or correlation shit on the droplet page I'm viewing. 00-overview.source.md:7
[G-2] Correlations are still only just a list... 00-overview.source.md:7
[G-3] holy fuck this looks like shit, you've done literally 0 UX screenshot checking. 00-overview.source.md:8
[G-4] Your whole job for the rest of the day is an overhaul of the entire application, ensuring it fucking looks right on top of functions properly. 00-overview.source.md:8
[G-5] The only piece of this application that even looks slightly good is the fucking dashboard. 00-overview.source.md:8
[G-6] (about pinned versions; context only, owned by another plan: feature-plans/model-unpin.md) "AND STOP PINNING SPECIFIC FUCKING VERSIONS OF SHIT LIKE THIS" 00-overview.source.md:9
[G-7] PM Step-2 answers ([PM-ASSUMED]; the operator is not available for questions; project directive: record assumptions, do not stop) 00-overview.source.md:11
[G-8] Building: (1) every page and tab of the dashboard app renders correctly, honestly and consistently with the main dashboard `/`, which is the operator's reference standard, and every control on it works. 00-overview.source.md:13
[G-9] (2) Users: the operator, through `/`, `/history`, `/performance`, `/settings` (+ the per-symphony settings modal), `/login`, `/ai-advisor` (tabs Overview, Correlations, Asset Swaps, Logic Changes, Chat, Strategy Builder, Frontrunner Builder) and the shared header/nav. 00-overview.source.md:14
[G-10] (3) Flows: as the audit records per surface; the workflow files under `.claude/workflows/` if any exist. 00-overview.source.md:15
[G-11] (4) Negatives: per the audit (empty states, unverifiable states, unavailable data, narrow widths, dark theme). 00-overview.source.md:16
[G-12] (5) Data/contracts: display and route JSON only, plus ONE data-basis change: the Correlations matrix moves from shadow history to the same realized Composer daily series the retirement recommender reads (`advisors/composer_series_client.py`, DE-RETIRE-VISIBILITY-001). 00-overview.source.md:17
[G-13] (6) PlanetStopper only. 00-overview.source.md:18
[G-14] (7) Not sensitive, except plan 04 must keep Emergency Liquidate and the mode pill reachable and must not change their behavior. 00-overview.source.md:19
[G-15] Not building: no change to `alpha_bot_execution.py`, `math_engine.py`, trading or liquidation behavior, the DB schema; no new token values or new layouts invented by the team (those are Claude Design brief items B1-B5, tracked as follow-ups in the overview; the operator runs them in Claude Design); no model-version work (model-unpin plan owns it). 00-overview.source.md:20
[G-16] The split (agreed by the PM; plan numbers fixed) 00-overview.source.md:22
[G-17] 01-data-truth: wrong numbers and labels (Sortino shows Sharpe; "$ SAVED" over a loss; "++1.61pp"; Performance 1Y = 252 days), Composer hashes shown where display names belong (settings modal title, Performance rows, Frontrunner cards), honest copy (Strategy Builder banner says "Logic-change", Correlations caveat repeats, gear-icon note), dead controls (`settings.js` SyntaxError / Reset to globals, empty Frozen-eval pill), Prism verification chip, overrides count. 00-overview.source.md:24
[G-18] 02-advisor-overview: Overview order and bounds (autotune list of 50 identical cards, observations table outside the layout under every tab), Retirement as its own tab (interim, from existing primitives `.mini-stats`, `.legend-as-of`, `.sym-card`, `.status-pill`), Chart.js loaded on the Advisor page. 00-overview.source.md:25
[G-19] 03-correlations-realized: matrix on the realized Composer series with depth per cell, as-of with timezone, thin threshold tied to the recommender's 30/181-day gates, excluded symphonies with reasons, signed colour fill, full names on both axes, the same basis label as the Retirement panel. 00-overview.source.md:26
[G-20] 04-app-shell: every page header and panel conforms to the dashboard's; an interim narrow-width nav that wraps so every control (Settings, mode pill, Force run, Emergency Liquidate) stays reachable at 375 px without new design; login loads the app font and theme. 00-overview.source.md:27
[G-21] 05-dashboard-charts: the hero chart and card sparklines blank after the client refresh (audit D-5). 00-overview.source.md:28
[G-22] A separate read-only diagnosis is running now; its report will be at .claude/review/37c8e983b64c/dashboard-charts-diagnosis.md. 00-overview.source.md:28
[G-23] Write plan 05 from it if it exists when you reach 05; otherwise write the criteria on the observable behavior and list the cause under Known-Unconfirmed. 00-overview.source.md:28
[G-24] PM rulings on the audit's decisions (record as decisions in the overview; each a default the operator may override) 00-overview.source.md:30
[G-25] D-2 matrix moves to the realized series; 00-overview.source.md:32
[G-26] D-3 Retirement its own tab now, combined Correlations+Retirement view after Claude Design B2; 00-overview.source.md:33
[G-27] D-4 interim wrap now, real mobile layout after B1; 00-overview.source.md:34
[G-28] D-5 per diagnosis; 00-overview.source.md:35
[G-29] D-6 token changes wait for B5 (no token values changed by the team); 00-overview.source.md:36
[G-30] D-7 signed colour: high positive correlation = warn/danger, near zero neutral, negative = diversifying (pos), never red for negative; 00-overview.source.md:37
[G-31] D-8 no green "clean" while any check is unverifiable (show verified count of total); 00-overview.source.md:38
[G-32] D-9 dashboard conventions everywhere: Max DD as a positive magnitude labelled Max DD, 1Y = 365 calendar days everywhere, 30d default window on every page; 00-overview.source.md:39
[G-33] D-10 conform every page to the dashboard's header and panel primitives; 00-overview.source.md:40
[G-34] D-11 an override equal to the global value does not count; 00-overview.source.md:41
[G-35] D-12 observations collapse to one status line per producer with history behind an expander. 00-overview.source.md:42
[G-36] Stack: plans touch overlapping templates; order the Base chain so no two concurrently built plans edit the same file where avoidable: 05 and 01 first (different files), then 02, 03, then 04 last (it touches every template header). 00-overview.source.md:46
[G-37] Note in the overview that model-unpin also edits `templates/ai_advisor.html`, `static/ai_advisor.js` and `app.py` and lands first. 00-overview.source.md:47
[G-38] Pushback 4 (D-9): revise D-9: the 30d default applies on every page EXCEPT Performance, whose default is the shortest window token that clears its minimum-history rule (state which, from the code); 1Y = 365 days and Max DD as a positive magnitude stay app-wide. 00-overview.source.md:51
[G-39] NB-16 (Emergency Liquidate fails silently on a rejected request): NOT a follow-up. 00-overview.source.md:52
[G-40] My G-14 brief meant the liquidation request, its confirmation steps (account id, phrase), the route and its server-side checks must not change; showing the operator the server's real answer when the request is rejected is required honesty, like Force run's real answer in 04 AC-8. 00-overview.source.md:52
[G-41] Add it to plan 04 as a criterion with scenarios for each of the five 400 reasons and the 403 CSRF case, marking plan 04 Sensitive; the request payload and flow stay byte-identical. 00-overview.source.md:52
[G-42] NB-14 (settings modal reads/writes the strategy row by Composer hash while the engine reads by display name): keep it out of this suite as a proposed separate SENSITIVE plan, but state in the overview that it is the highest-priority follow-up and that until it ships the modal's live-mode toggle and locks may not reach the engine. 00-overview.source.md:53
[G-43] Plan 03: add a criterion (with N-kind scenarios) that every realized-series date is the calendar date Composer stamps, read in UTC. 00-overview.source.md:57
[G-44] Evidence (PM live capture 2026-10-22 on the droplet, read-only GET /api/v0.1/portfolio/accounts/{account}/symphonies/{id}): every `epoch_ms` point is stamped at 00:00 UTC (158 of 158 and 150 of 150 points); `advisors/composer_series_client.py` `_epoch_ms_to_et_date` maps each to America/New_York, which is the PREVIOUS calendar day, so returns are labelled one day early (a return labelled Sunday 2026-10-18 appeared; the latest point labelled 2026-10-20 is Composer's 2026-10-21). 00-overview.source.md:58
[G-45] The pairing across symphonies is unaffected (every series shifts alike), but every date the operator sees (as-of, depth window, first/last date) is wrong by one day, and the `d < today_et` cut-off compares a shifted date. 00-overview.source.md:59
[G-46] The fix belongs to `composer_series_client` (shared with the retirement recommender, so the retirement summary's dates change too; say so in Contract Changes). 00-overview.source.md:60
[G-47] Plan 03 also renders the series' first and last dates, which consumes `SeriesResult.first_date`/`last_date` (closing the #145 post-merge review's CR-2 dead-field finding). 00-overview.source.md:61
[G-48] Real captures for every live symphony will be committed by the PM on branch `ux-overhaul/fixtures` under `tests/fixtures/composer/series_captured/` with a PROVENANCE.md; cite that branch in 03's Fixture provenance. 00-overview.source.md:62

## Requirement Inventory
Child plans keep these R ids for the items they own. An item owned by several plans is delivered in part by each, and each plan's inventory states its part.

| R-n | Requirement item | Source (G ids) | Maps to (plan NN, or NB-n) | Scope (full, or narrowed: what is left out) |
|---|---|---|---|---|
| R-1 | The operator sees the retirement analysis on the AI Advisor page on the droplet without scrolling past unrelated content | G-1, G-18, G-26 | 02 | full |
| R-2 | The operator sees the correlation analysis on the AI Advisor page on the droplet | G-1 | 03 | full |
| R-3 | The Correlations view reads as a colour-coded matrix (heatmap), not a list of numbers | G-2, G-19, G-30 | 03 | narrowed: the combined Correlations and Retirement layout (Claude Design B2) is left out |
| R-4 | Every screen a plan changes is checked by rendered screenshots before it ships | G-3 | 01, 02, 03, 04, 05 | full |
| R-5 | Every page and tab looks right | G-4, G-8 | 01, 02, 03, 04, 05 | narrowed: new layouts and token values (Claude Design B1 to B5) are left out |
| R-6 | Every control on every page functions | G-4, G-8 | 01, 02, 04, 05 | narrowed: the Asset Swaps, Logic Changes, Strategy Builder and Frontrunner Builder run forms are re-checked live on the droplet, not changed (NB-7) |
| R-7 | The dashboard `/` is the reference standard and keeps its look | G-5, G-8 | 05 | narrowed: `/` changes only its charts' loading and refresh, its first-paint verdict and dial, its banners, its account refresh and its detail-panel title; its contrast-token findings wait for B5 (NB-10) |
| R-8 | No model version is pinned | G-6, G-15 | NB-1 | full |
| R-9 | Open questions are answered by recorded defaults, never by stopping | G-7 | 01, 02, 03, 04, 05 | full |
| R-10 | Every page and tab renders honestly | G-8 | 01, 02, 03, 05 | full |
| R-11 | The overhaul covers `/`, `/history`, `/performance`, `/settings` with the per-symphony modal, `/login`, all seven `/ai-advisor` tabs and the shared header and nav | G-9 | 01, 02, 03, 04, 05 | full |
| R-12 | Flows follow the audit per surface, and workflow files where they exist | G-10 | 01, 02, 03, 04, 05 | full |
| R-13 | Empty, unverifiable and unavailable states are honest on every surface a plan touches | G-11 | 01, 02, 03, 05 | full |
| R-14 | Every page is usable at narrow widths | G-11, G-27 | 04 | narrowed: an interim wrap and contained overflow only; the real mobile layout (B1) is left out |
| R-15 | Every page renders in the dark theme | G-11 | 04, 05 | narrowed: login follows the stored theme and the dashboard charts follow a theme change; the dark accent override and faint-ink contrast (B5) are left out |
| R-16 | Only display and route JSON change, plus the one Correlations data-basis change | G-12 | 03, NB-2 | full |
| R-17 | PlanetStopper is the only repository changed | G-13 | NB-3 | full |
| R-18 | Emergency Liquidate and the mode pill stay reachable on every page and their behaviour is unchanged | G-14 | 04 | full |
| R-19 | No change to the engine, the math engine, trading or liquidation behaviour, or the database schema | G-15 | NB-2 | full |
| R-20 | No new token values or new layouts; B1 to B5 are tracked as follow-ups here | G-15, G-29 | NB-4 | full |
| R-21 | The five plans and their numbers are as agreed | G-16 | 01, 02, 03, 04, 05 | full |
| R-22 | The autotune card's value now labelled "Sortino" carries a label that names what it is | G-17 | 01 | narrowed: G-17 calls the value a Sharpe ratio; the code shows it is the optimizer's best objective score (CRRA expected utility or Sortino), so it is not labelled Sharpe (plan 01 Q-8) |
| R-23 | A History dollar figure over a loss reads lost, over a gain saved | G-17 | 01 | full |
| R-24 | Performance deltas carry exactly one sign | G-17 | 01 | full |
| R-25 | Performance 1Y means 365 calendar days and 5Y 1825 | G-17, G-32 | 01 | full |
| R-26 | The settings modal title is the symphony's display name | G-17 | 01 | full |
| R-27 | Performance preconditions and exit-turnover rows name each symphony by display name | G-17 | 01 | full |
| R-28 | Frontrunner proposal card titles name the incumbent by display name | G-17 | 01 | full |
| R-29 | The Strategy Builder risk banner names Strategy Builder | G-17 | 01 | full |
| R-30 | The Correlations caveat sentence appears once | G-17 | 01 | full |
| R-31 | The observations note names the real advisor control, not the gear icon | G-17 | 01 | full |
| R-32 | Settings "Reset to globals" works for every symphony | G-17 | 01 | full |
| R-33 | The Frozen-eval line never shows an empty pill | G-17 | 01 | full |
| R-34 | The Prism verification chip is never a green "clean" while a check is unverifiable, and shows the verified count of the total | G-17, G-31 | 01 | full |
| R-35 | An override equal to the global value is not counted | G-17, G-34 | 01 | full |
| R-36 | Max DD is a positive magnitude labelled Max DD on every page | G-32 | 01 | full |
| R-37 | Every page with a window picker opens on 30d, except Performance, which opens on the shortest window token that clears its minimum-history rule (60d) | G-32, G-38 | 01 | full |
| R-38 | The History day drilldown names each exit's symphony and lists exits in time order | G-10 | 01 | full |
| R-39 | Internal lowercase keys are replaced by display names (autotune cards, observations, settings subtitle, dashboard detail panel) | G-10 | 01, 05 | full |
| R-40 | Performance shows no all-dash metric rows, one panel note for unavailable replay samples, and two-decimal friction drag | G-10, G-11 | 01 | full |
| R-41 | Strategy Builder cards show human objective and source labels and a human title | G-10 | 01 | full |
| R-42 | The observations table sits inside the page layout and only on the Overview tab | G-18 | 02 | full |
| R-43 | Observations collapse to one status line per producer with history behind an expander | G-35 | 02 | full |
| R-44 | The autotune list is bounded | G-18 | 02 | full |
| R-45 | The Overview puts actionable items before reference material | G-18 | 02 | narrowed: the Overview information architecture design (B3) and the Prism five-lens layout are left out (NB-8) |
| R-46 | Retirement is its own Advisor tab, built from existing dashboard primitives | G-18, G-26 | 02 | full |
| R-47 | A combined Correlations and Retirement view after Claude Design B2 | G-26 | NB-5 | full |
| R-48 | The Advisor page loads Chart.js so its autotune sparkline draws | G-18 | 02 | full |
| R-49 | Overview timestamps name their timezone without fractional seconds, pre-run zero chips are gone, and the symphony label is linked to its control | G-10, G-11 | 02 | full |
| R-50 | Exclusion reasons render as sentences | G-10 | 02, 03 | full |
| R-51 | Chat example prompts name only flows the page can start | G-10 | 02 | full |
| R-52 | The Correlations matrix is computed from the realized Composer daily series the retirement recommender reads | G-12, G-19, G-25 | 03 | full |
| R-53 | Each matrix cell shows its depth | G-19 | 03 | full |
| R-54 | The matrix as-of names its timezone | G-19 | 03 | full |
| R-55 | The thin threshold is tied to the recommender's 30 and 181 day gates | G-19 | 03 | full |
| R-56 | Excluded symphonies are listed with reasons | G-19 | 03 | full |
| R-57 | Matrix cells are filled by signed colour per D-7 | G-19, G-30 | 03 | full |
| R-58 | Full names on both matrix axes | G-19 | 03 | full |
| R-59 | The matrix carries the same basis label as the Retirement panel | G-19 | 03 | full |
| R-60 | Every page header conforms to the dashboard's | G-20, G-33 | 04 | full |
| R-61 | Every page's panels, cards and section labels conform to the dashboard's | G-20, G-33 | 04 | full |
| R-62 | An interim narrow-width nav wraps so Settings, the mode pill, Force run and Emergency Liquidate stay reachable at 375 px | G-20, G-27 | 04 | full |
| R-63 | A real mobile layout after Claude Design B1 | G-27 | NB-6 | full |
| R-64 | Login loads the app font and the stored theme | G-20 | 04 | full |
| R-65 | Chip tints use existing tokens, never raw colour literals | G-10, G-29 | 04 | full |
| R-66 | The hero chart and card sparklines stay drawn after the client refresh | G-21, G-28 | 05 | full |
| R-67 | Plan 05 is written from the diagnosis report when it exists, else on observable behaviour with the cause as Known-Unconfirmed | G-22, G-23 | 05 | full |
| R-68 | The card verdict line and MC dial agree between first paint and refresh | G-10 | 05 | full |
| R-69 | The PM's rulings are recorded as decisions, each a default the operator may override | G-24 | 01, 02, 03, 04, 05 | full |
| R-70 | The Base chain runs 05 and 01 first, then 02, 03, and 04 last | G-36 | 01, 02, 03, 04, 05 | full |
| R-71 | The model-unpin plan edits overlapping files and lands first | G-37 | 01, 02, 03, 05 | full |
| R-72 | The dashboard hero chart covers the selected calendar window after every refresh | G-32 | 05 | full |
| R-73 | A refused Emergency Liquidate request shows the server's real answer, for each of the five 400 reasons and the 403 CSRF case, with the request and its confirmation flow unchanged | G-39, G-40, G-41 | 04 | full |
| R-74 | The settings modal keying defect stays out of this suite as a separate sensitive plan, stated as the highest-priority follow-up, with its live-trading consequence | G-42 | NB-14 | full |
| R-75 | Every realized-series date is the calendar date Composer stamps, read in UTC, in the shared series client, for the matrix and the retirement recommender alike | G-43, G-44, G-45, G-46 | 03 | full |
| R-76 | The Correlations tab shows each symphony's first and last series dates from the series client | G-47 | 03 | full |
| R-77 | Fixtures use the PM's real captures for every live symphony from branch `ux-overhaul/fixtures` | G-48 | 03 | full |

## Not Building
Non-goals:
- NB-1: Model-version work; the model-unpin plan owns it (branch `model-unpin/fixtures`; excludes R-8; approved by the PM, [PM-ASSUMED]).
- NB-3: Any repository other than PlanetStopper (excludes R-17; approved by the PM, [PM-ASSUMED]).
- NB-4: New token values, new layouts or new primitives invented by the teams; Claude Design brief items B1 (mobile and narrow layout), B2 (combined Correlations and Retirement view), B3 (Advisor Overview information architecture), B4 (Frontrunner proposal rules diff) and B5 (text-safe faint ink and dark-theme accent) are follow-ups the operator runs in Claude Design (excludes R-20; approved by the PM, [PM-ASSUMED]).

Behaviour that stays exactly as it is:
- NB-2: `alpha_bot_execution.py`, `math_engine.py`, every trading and liquidation behaviour, and the database schema carry zero diff; the retirement recommender's gates, thresholds, composite weights and candidate selection are unchanged (excludes R-19; approved by the PM, [PM-ASSUMED]).
- NB-11: The dashboard `/` keeps its layout, primitives and every value it renders, except the plan 05 fixes (its charts' loading and refresh, first-paint verdicts and dials, banners, account refresh and detail-panel title); it is the reference the other pages conform to.

Deferred:
- NB-5: The combined Correlations and Retirement view, after Claude Design B2 (excludes R-47; approved by the PM, [PM-ASSUMED]).
- NB-6: A real mobile layout and a narrow-table pattern, after Claude Design B1 (excludes R-63; approved by the PM, [PM-ASSUMED]).
- NB-7: The Asset Swaps, Logic Changes, Strategy Builder and Frontrunner Builder run forms; the audit copy had no Composer or Anthropic keys, so the forms were not rendered; they are re-checked in the Suite Gate's live test on the droplet and any defect found there opens its own plan (narrows R-6; approved by the PM, [PM-ASSUMED]).
- NB-8: The Advisor Overview information architecture and the Prism five-lens layout, after Claude Design B3 (narrows R-45; approved by the PM, [PM-ASSUMED]).
- NB-9: A rules-diff review view for Frontrunner proposals, after Claude Design B4 (audit F3; approved by the PM, [PM-ASSUMED]).
- NB-10: The faint-ink contrast and dark-theme accent override, after Claude Design B5 (audit G2, G3; narrows R-7 and R-15; approved by the PM, [PM-ASSUMED]).
- NB-12: Human parameter labels on the settings page and modal (audit M3, S4): the code has no label vocabulary, only a key, help text and unit per parameter (`app.py:6014-6024`); deferred until one is agreed (approved by the PM, [PM-ASSUMED], operator may override).
- NB-13: Polish items left for a later pass: the floating tweaks trigger over content (G6), the tweaks panel covering its toggle (G7), source-card titles and the sentiment carousel overflow (O8), the off-palette chat emoji (CH3), uneven Advisor tab heights and hover-only tab dots (A3), and the History daily bars' missing axis (H4) (approved by the PM, [PM-ASSUMED], operator may override).
- NB-14: The per-symphony settings modal reads and writes the strategy row keyed by the lowercased Composer hash (`app.py:6177-6183`, `app.py:6231-6275`), while the engine reads the row keyed by the lowercased display name (`alpha_bot_execution.py:1242-1248`), so the modal's live-mode toggle and lock edits may not reach the engine, and its GET writes a default row under the hash on first open (`database.py:554`) although its docstring says it never writes; this is a live-trading control defect found during this spec's investigation, outside the audit. It is the suite's highest-priority follow-up, a separate sensitive plan (PM ruling, G-42); until it ships, the modal's live-mode toggle and locks may not reach the engine (excludes R-74's fix; approved by the PM, [PM-ASSUMED]).

Protected surfaces (consumed, never changed):
- NB-16: removed: a refused Emergency Liquidate request is now plan 04 AC-9 (PM ruling, G-39 to G-41).
- NB-15: The no-trade boundary: `POST /ai-advisor/proposal/approve` (the only route that reaches Composer symphony creation, which stays undeployed), the panic-sell route behind Emergency Liquidate (`POST /api/sell_account`), the Managed Sleeves arming ladder, `LIVE_EXECUTION` with each symphony's `live_mode`, the settings write allowlist, and the retirement approve, reject and run routes; their boundary tests (`tests/security/test_frontrunner_no_trade_boundary.py`, `tests/security/test_retirement_action_no_trade_boundary.py`, `tests/security/test_retirement_recommender_no_trade_boundary.py`, `tests/app/test_sell_account_panic_confirm.py`, `tests/app/test_f003_liquidation_confirmation.py`) stay green on the assembled stack.

## Plans and Dependency Graph
| # | Plan | Repos | Base | Contract exposed upward | Status | Depends on | Requirement items |
|---|---|---|---|---|---|---|---|
| 01 | [Data and label truth](01-data-truth.md) | PlanetStopper | main | Display-name rule for every symphony label; Performance window buttons resolve as calendar windows; the symphony-settings and history payloads carry display names | planned | model-unpin merged into this plan's tip before its PR | R-4, R-5, R-6, R-9, R-10, R-11, R-12, R-13, R-21, R-22, R-23, R-24, R-25, R-26, R-27, R-28, R-29, R-30, R-31, R-32, R-33, R-34, R-35, R-36, R-37, R-38, R-39, R-40, R-41, R-69, R-70, R-71 |
| 02 | [Advisor Overview and Retirement tab](02-advisor-overview.md) | PlanetStopper | main, its PR opened once 05 and 01 are on main (cycles C1 to C3 from `ux-overhaul/05`) | A Retirement tab panel on `/ai-advisor`; observations inside the Overview tab; Chart.js on the Advisor page; the dashboard primitives as one shared definition (SD-6) | planned | 05 (cycles C1 to C3); 01, merged into 02's branch before cycles C4 and C5 | R-1, R-4, R-5, R-6, R-9, R-10, R-11, R-12, R-13, R-21, R-42, R-43, R-44, R-45, R-46, R-48, R-49, R-50, R-51, R-69, R-70, R-71 |
| 03 | [Correlations on the realized series](03-correlations-realized.md) | PlanetStopper | plan 02's tip with `ux-overhaul/fixtures` (7c0b7432) merged in | The Correlations matrix computed from the latest successful retirement run's realized series, with depth, thin marker, exclusions and basis label | planned | 02 | R-2, R-3, R-4, R-5, R-9, R-10, R-11, R-12, R-13, R-16, R-21, R-50, R-52, R-53, R-54, R-55, R-56, R-57, R-58, R-59, R-69, R-70, R-71, R-75, R-76, R-77 |
| 04 | [App shell and page conformance](04-app-shell.md) | PlanetStopper | 03 | One page skeleton and shared header, panel, card and label primitives on every page; a wrapping nav at narrow widths; an honest Force run | planned | 03 | R-4, R-5, R-6, R-9, R-11, R-12, R-14, R-15, R-18, R-21, R-60, R-61, R-62, R-64, R-65, R-69, R-70, R-73 |
| 05 | [Dashboard charts after refresh](05-dashboard-charts.md) | PlanetStopper | main | The dashboard draws its charts, verdict lines and MC dials from first paint and keeps them, in the selected window, through every refresh | planned | model-unpin merged into this plan's tip before its PR | R-4, R-5, R-6, R-7, R-9, R-10, R-11, R-12, R-13, R-15, R-21, R-39, R-66, R-67, R-68, R-69, R-70, R-71, R-72 |

## Stack and Merge Rules
- The model-unpin plan also edits `templates/ai_advisor.html`, `static/ai_advisor.js` and `app.py`; it merges into each plan's tip before that plan's PR (PM ruling). The PM committed the model-unpin plan and its approach on branch `model-unpin/fixtures` (291454eb); the Symphony Tenure Review plan is committed on branch `feat/tenure-review-c1` (another session's work). Neither is on main at the ref.
- Two roots, built at the same time: 01 (History, Performance, Settings, the settings modal and Advisor files, and the `/api/performance`, `/api/history`, `/api/symphony-settings` routes and the verification display in `app.py`), based on main, and 05 (the dashboard: `static/index.js`, `templates/index.html`, and `dashboard()` in `app.py`), whose three cycles assemble on `ux-overhaul/05`. Both edit `app.py`, in different functions; that overlap is resolved by a merge commit.
- 02 has no base branch of its own: its PR targets main once 05 and 01 are on main. Its cycles C1 to C3 branch from `ux-overhaul/05`; its cycles C4 and C5 edit the same code as plan 01 (`loadRecentRuns` and the observations rows), so they branch only after plan 01 is merged into 02's branch; C6 follows. 02 moves the dashboard's primitives out of `templates/index.html` into `static/layout.css` (SD-6). 03's base is 02's tip with `ux-overhaul/fixtures` merged in (7c0b7432: the real series captures and a real `RETIREMENT_RUN_SUMMARY` row). 04 stacks on 03 and is last because it edits every template header and the shared chrome. Each plan lands as one PR, and every merge across the stack is a merge commit, never a squash. No branch is rebased and no branch is force-pushed.
- Each PR is verified against its own base: `ruff format --check`, `ruff check`, a scoped pytest run of the test directories its files touch (never `-n auto`, never more than 4 workers), CI on the exact SHA, and the plan's own screenshot comparison.
- Each plan lands as one PR to `main`, in stack order (05, 01, 02, 03, 04). Under the project's merge rule, the PM merges each PR after its own reviews (`/review`), its UX screenshot gate and the PM's live functional check on the droplet. The Suite Gate's cross-plan items run once, at plan 04's PR, against `main` plus 04, and are summarised in 04's PR body.
- The Symphony Tenure Review plan (status ready, branch `feat/tenure-review-c1`) targets the same Retirement panel, its API and its run action; whichever lands second carries the merge, and plan 02 Q-1 records the question of order.

## Shared Decisions
Each PM ruling is a default the operator may override at approval.

| ID | Decision | Rationale |
|---|---|---|
| D-2 | The Correlations matrix reads the realized Composer daily series the retirement recommender reads | The current matrix sits on at most 60 days of post-mortem trigger-day returns (`app.py:6683-6695`, `analytics.py:1570`), so every off-diagonal cell in the audit copy was thin (n from 0 to 28, median 16) while the Retirement panel on the same page reported 29 to 250 days of realized history |
| D-3 | Retirement is its own Advisor tab now; the combined view waits for Claude Design B2 | The panel sat 6,832 px down the Overview (audit O4); a tab is reachable from the first screen with existing primitives |
| D-4 | An interim wrapping nav now; the real mobile layout waits for B1 | At 375 px the nav row runs 961 to 1,186 px wide on every page but login, putting Emergency Liquidate off-screen (audit G1) |
| D-5 | Plan 05 follows the read-only charts diagnosis | The audit could not confirm the cause of the blank charts (audit D1) |
| D-6 | No token values change in this suite | Token decisions belong to Claude Design B5 |
| D-7 | Signed colour: high positive correlation is warn or danger, near zero is neutral, negative is diversifying (the positive token), never red for negative | Colour by absolute value showed a diversifying -0.75 pair in the same red as +0.97 (audit C4) |
| D-8 | No green "clean" while any verification check is unverifiable; the chip shows the verified count of the total | The chip read "clean" with 14 of 32 checks unverifiable (audit O5) |
| D-9 | Dashboard conventions everywhere: Max DD a positive magnitude labelled Max DD and 1Y equal to 365 calendar days; every page opens on 30d except Performance, which opens on 60d, its shortest token whose span can hold the 30 observations its minimum-history rule needs (`app.py:44`, `:5322`) (revised by the PM, G-38) | The dashboard shows 13.20% where Performance showed -13.20%, Performance sent 252 for 1Y, and default windows were 30d, 90d and 60d (audit D3, P4, H5) |
| D-10 | Every page conforms to the dashboard's header and panel primitives | Five header patterns and two panel systems (audit G4, G5) |
| D-11 | An override equal to the global value does not count | Every symphony read "8 overrides" with every value equal to the global (audit S3) |
| D-12 | Observations collapse to one status line per producer, history behind an expander | 50 rows of one identical sentence filled 4,734 px under every tab (audit A1, A2) |
| SD-1 | A symphony is labelled by its display name everywhere; its Composer hash appears at most in a `title` attribute | Composer hashes or internal lowercase keys labelled 24 Performance rows, 5 Frontrunner cards, the settings modal and the dashboard detail panel (UX-7, UX-8) |
| SD-2 | A shown time names its zone and has no fractional seconds; a time of day reads like the dashboard's `.legend-as-of` stamp ("14:53 ET") and a dated stamp adds the date | Raw ISO stamps with microseconds and no zone appeared on four Advisor surfaces (UX-5) |
| SD-3 | A dollar figure carries its magnitude and the word saved or lost, never a bare sign under a fixed label | The operator-locked convention the dashboard already renders (`analytics.format_dollar_saved`, `DE-GAS-COHERENCE-001`) |
| SD-4 | An exclusion reason renders as a sentence from one mapping shared by the Retirement tab and the Correlations matrix | The raw enum `series_too_short` reached the screen (UX-20), and two tabs must explain the same exclusion the same way |
| SD-5 | Every test fixture is one captured record per case from the real producer (the production database copy the audit used, or a captured API response), never hand-built to match the parser | A fixture built with its parser only proves the parser agrees with itself |
| SD-6 | Plan 02 moves the dashboard primitives (`.hero-section`, `.sym-card`, `.card-name`, `.section-header`, `.mini-stats` and its parts, `.status-pill`, `.legend-as-of`) out of `templates/index.html` into `static/layout.css` with identical values; plans 03 and 04 use that definition and plan 04 deletes every page-local copy | Plans 02 and 03 render these primitives on `/ai-advisor` before plan 04 lands, and a copied definition would be the second source DC-5 forbids |
| SD-8 | Browser-only tests carry a `browser` pytest marker, are deselected by default and run at the merge gate, because CI has no Playwright; the zero-skip rule holds | PM ruling on the Gate-2 approaches |
| SD-12 | The overview and plans 01 to 05 are approved at Step 7 with their approaches (`approach/00-suite.md` and `approach/01-*.md` to `approach/05-*.md`) under the project's standing autonomy directive; every open question takes its default or its recorded ruling, and every Not Building entry is approved | [PM-ASSUMED]; the operator is unavailable and may override |
| SD-11 | Fixtures may keep real symphony names and Composer ids, and need no anonymizing edit; no fixture holds an account id. A plan that already uses invented names (plan 05) keeps them as a signed-off derived edit (SD-9) | Suite ruling (`feature-plans/ux-overhaul/approach/00-suite.md`): the repository is private, and an edited name would no longer be a captured record |
| SD-9 | A fixture for a case no real capture holds is derived from a real captured record with only the named field changed, and the PM signs it off as producer owner | PM ruling; it keeps SD-5's provenance for edge cases |
| SD-10 | The droplet's clock is UTC (`timedatectl` reports Etc/UTC), so every schedule label for the nightly retirement run reads 03:45 UTC; run times shown to the operator still display in ET (SD-2) | PM ruling; the scheduler fires on the host clock |
| SD-7 | The autotune `naive_sharpe` value is named as the optimizer's best objective score, never a Sharpe or Sortino ratio | It is the Optuna study's best value under the run's objective (`autotuner.py:2434-2436`, `:2776`); the audit and G-17 read the column name |

## Shared Defect Classes
| DC-n | Class | Trigger | Sibling search (tool, query, scope across every plan's repos) |
|---|---|---|---|
| DC-1 | An internal identifier (Composer hash, lowercase key, raw enum) shown where a display name or sentence belongs | B | grep `symphony_id\|normalized_name\|_currentSymphonyId\|sym\.id\|candidate_id\|rejection_reason\|reason }}` in `templates/` and `static/`, PlanetStopper; stop when a widening finds only sites already judged |
| DC-2 | An absent or not-computed value rendered as a default (empty pill, zero chip, dash row, "clean") | N3 | grep `\|\| ''\|or ''\|'0 \|toFixed(` and every `escHtml(` of a nullable field in `static/` and `templates/`, PlanetStopper |
| DC-3 | One label meaning different values on different pages (1Y, 30d default, Max DD sign, saved or lost) | B | grep `data-value=\|data-window=\|currentWindow\|max_drawdown\|Saved` in `templates/` and `static/`, PlanetStopper |
| DC-4 | A rule applied on one page and missing on a sibling page or tab | N2 | read: each page's render of the rule (header, panel, label, timestamp, display name), all templates, PlanetStopper |
| DC-5 | A raw colour, size or font value bypassing the shared tokens and primitives | B | grep `rgba(\|#[0-9a-fA-F]\{3,6\}\|font-family:\|border-radius:` outside `var(--studio-` in `templates/` and `static/`, PlanetStopper |
| DC-6 | A changed template id, class or route field not carried to every consumer, including tests that pin the old string | B | grep each changed `data-testid`, class, route field and copy string in `templates/`, `static/`, `app.py` and `tests/`, PlanetStopper |
| DC-7 | A DOM selector or id built from a raw data value | N1 | grep `querySelector(.*+\|getElementById(.*+\|id="[^"]*\${` in `static/`, PlanetStopper |

## Suite Gate
The cross-plan items below run once, at plan 04's PR, against `main` (which by then holds 05, 01, 02 and 03) plus plan 04, and are summarised in 04's PR body; each earlier plan's PR has already passed its own reviews, screenshot gate and live check:
1. `ruff format --check` and `ruff check` clean; a pytest run of the full tree in batches of at most 4 workers, never `-n auto`, with zero failures and zero errors, quoting the head SHA; CI green on that exact SHA.
2. `git diff main...HEAD -- alpha_bot_execution.py math_engine.py migrations/`, run on the stack tip, is empty, and the five boundary test files named in NB-15 pass.
3. A screenshot comparison of every page and tab at 1440x900 and 375x812, in light and dark, against the dashboard reference captures, by the UX reviewer; each audit finding id (UX-1 to UX-25 and the per-surface ids) is closed with a screenshot or traced to an NB entry, and the comparison is saved under the review folder for the stack SHA.
4. The PM's live behavioural test on the droplet after deploy, after `/review`: on `/ai-advisor` the Retirement tab is visible in the tab bar on the first screen and opens to the last run's summary; the Correlations tab shows a colour-filled matrix with the realized basis label, an as-of with a timezone and depth per cell; the hero chart and every card sparkline on `/` stay drawn across at least three refreshes; every control the plans name is clicked once (Reset to globals then Discard, the read-only Live execution card on `/settings`, every window button on History and Performance, Run now outside market hours, the observations expander, the settings modal open and close, Force run, Emergency Liquidate opened and cancelled without confirming, and a refused liquidation provoked on a dry-run instance to see its message); and the Asset Swaps, Logic Changes, Strategy Builder and Frontrunner Builder forms are opened and their result or refusal recorded (NB-7).
5. At 375 px wide, on every page, the header shows Settings, the mode pill, Force run and Emergency Liquidate without horizontal page scroll.

## Assembly and Close
The suite is complete when every plans-table row reads merged, the Suite Gate's five items are recorded with plan 04's PR head SHA, and the operator has seen the screenshot comparison and the live-test record. The evidence lives in the review folder named for the stack's head SHA under `.claude/review/`, with the UX comparison, the pytest and CI records and the PM's live-test notes, and is summarised in the PR body of plan 04. The PM performs each merge to main after that PR's `/review` and live functional test, as the project's merge rule requires (approval of the suite recorded [PM-ASSUMED] under the standing autonomy directive; the operator may override); after it, the doc writer appends the `DECISIONS.md` entries and the `.claude/CLAUDE.md` Key Files rows each plan drafts, and the Claude Design brief items B1 to B5 stay open as follow-ups.
