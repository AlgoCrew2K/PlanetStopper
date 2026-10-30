# Source: UX overhaul suite (verbatim)

Recorded 2026-10-22 from the PM's brief to the spec writer. Each numbered answer starts its own line; a machine-local absolute path in the brief is written here repository-relative; nothing else is changed.

## Operator source (2026-10-22, three messages, in order)

1. "and yet, here i am, not seeing any fucking retirement or correlation shit on the droplet page I'm viewing. Correlations are still only just a list..."
2. "holy fuck this looks like shit, you've done literally 0 UX screenshot checking. Your whole job for the rest of the day is an overhaul of the entire application, ensuring it fucking looks right on top of functions properly. The only piece of this application that even looks slightly good is the fucking dashboard."
3. (about pinned versions; context only, owned by another plan: feature-plans/model-unpin.md) "AND STOP PINNING SPECIFIC FUCKING VERSIONS OF SHIT LIKE THIS"

## PM Step-2 answers ([PM-ASSUMED]; the operator is not available for questions; project directive: record assumptions, do not stop)

Building: (1) every page and tab of the dashboard app renders correctly, honestly and consistently with the main dashboard `/`, which is the operator's reference standard, and every control on it works.
(2) Users: the operator, through `/`, `/history`, `/performance`, `/settings` (+ the per-symphony settings modal), `/login`, `/ai-advisor` (tabs Overview, Correlations, Asset Swaps, Logic Changes, Chat, Strategy Builder, Frontrunner Builder) and the shared header/nav.
(3) Flows: as the audit records per surface; the workflow files under `.claude/workflows/` if any exist.
(4) Negatives: per the audit (empty states, unverifiable states, unavailable data, narrow widths, dark theme).
(5) Data/contracts: display and route JSON only, plus ONE data-basis change: the Correlations matrix moves from shadow history to the same realized Composer daily series the retirement recommender reads (`advisors/composer_series_client.py`, DE-RETIRE-VISIBILITY-001).
(6) PlanetStopper only.
(7) Not sensitive, except plan 04 must keep Emergency Liquidate and the mode pill reachable and must not change their behavior.
Not building: no change to `alpha_bot_execution.py`, `math_engine.py`, trading or liquidation behavior, the DB schema; no new token values or new layouts invented by the team (those are Claude Design brief items B1-B5, tracked as follow-ups in the overview; the operator runs them in Claude Design); no model-version work (model-unpin plan owns it).

## The split (agreed by the PM; plan numbers fixed)

- 01-data-truth: wrong numbers and labels (Sortino shows Sharpe; "$ SAVED" over a loss; "++1.61pp"; Performance 1Y = 252 days), Composer hashes shown where display names belong (settings modal title, Performance rows, Frontrunner cards), honest copy (Strategy Builder banner says "Logic-change", Correlations caveat repeats, gear-icon note), dead controls (`settings.js` SyntaxError / Reset to globals, empty Frozen-eval pill), Prism verification chip, overrides count.
- 02-advisor-overview: Overview order and bounds (autotune list of 50 identical cards, observations table outside the layout under every tab), Retirement as its own tab (interim, from existing primitives `.mini-stats`, `.legend-as-of`, `.sym-card`, `.status-pill`), Chart.js loaded on the Advisor page.
- 03-correlations-realized: matrix on the realized Composer series with depth per cell, as-of with timezone, thin threshold tied to the recommender's 30/181-day gates, excluded symphonies with reasons, signed colour fill, full names on both axes, the same basis label as the Retirement panel.
- 04-app-shell: every page header and panel conforms to the dashboard's; an interim narrow-width nav that wraps so every control (Settings, mode pill, Force run, Emergency Liquidate) stays reachable at 375 px without new design; login loads the app font and theme.
- 05-dashboard-charts: the hero chart and card sparklines blank after the client refresh (audit D-5). A separate read-only diagnosis is running now; its report will be at .claude/review/37c8e983b64c/dashboard-charts-diagnosis.md. Write plan 05 from it if it exists when you reach 05; otherwise write the criteria on the observable behavior and list the cause under Known-Unconfirmed.

## PM rulings on the audit's decisions (record as decisions in the overview; each a default the operator may override)

D-2 matrix moves to the realized series;
D-3 Retirement its own tab now, combined Correlations+Retirement view after Claude Design B2;
D-4 interim wrap now, real mobile layout after B1;
D-5 per diagnosis;
D-6 token changes wait for B5 (no token values changed by the team);
D-7 signed colour: high positive correlation = warn/danger, near zero neutral, negative = diversifying (pos), never red for negative;
D-8 no green "clean" while any check is unverifiable (show verified count of total);
D-9 dashboard conventions everywhere: Max DD as a positive magnitude labelled Max DD, 1Y = 365 calendar days everywhere, 30d default window on every page;
D-10 conform every page to the dashboard's header and panel primitives;
D-11 an override equal to the global value does not count;
D-12 observations collapse to one status line per producer with history behind an expander.

## Stack

Stack: plans touch overlapping templates; order the Base chain so no two concurrently built plans edit the same file where avoidable: 05 and 01 first (different files), then 02, 03, then 04 last (it touches every template header).
Note in the overview that model-unpin also edits `templates/ai_advisor.html`, `static/ai_advisor.js` and `app.py` and lands first.

## PM follow-up rulings after the Gate-1 audit (2026-10-22, PM's words, verbatim)

Pushback 4 (D-9): revise D-9: the 30d default applies on every page EXCEPT Performance, whose default is the shortest window token that clears its minimum-history rule (state which, from the code); 1Y = 365 days and Max DD as a positive magnitude stay app-wide.
NB-16 (Emergency Liquidate fails silently on a rejected request): NOT a follow-up. My G-14 brief meant the liquidation request, its confirmation steps (account id, phrase), the route and its server-side checks must not change; showing the operator the server's real answer when the request is rejected is required honesty, like Force run's real answer in 04 AC-8. Add it to plan 04 as a criterion with scenarios for each of the five 400 reasons and the 403 CSRF case, marking plan 04 Sensitive; the request payload and flow stay byte-identical.
NB-14 (settings modal reads/writes the strategy row by Composer hash while the engine reads by display name): keep it out of this suite as a proposed separate SENSITIVE plan, but state in the overview that it is the highest-priority follow-up and that until it ships the modal's live-mode toggle and locks may not reach the engine.

## PM final edit to plan 03 (2026-10-22, PM's words, verbatim, one sentence per line)

Plan 03: add a criterion (with N-kind scenarios) that every realized-series date is the calendar date Composer stamps, read in UTC.
Evidence (PM live capture 2026-10-22 on the droplet, read-only GET /api/v0.1/portfolio/accounts/{account}/symphonies/{id}): every `epoch_ms` point is stamped at 00:00 UTC (158 of 158 and 150 of 150 points); `advisors/composer_series_client.py` `_epoch_ms_to_et_date` maps each to America/New_York, which is the PREVIOUS calendar day, so returns are labelled one day early (a return labelled Sunday 2026-10-18 appeared; the latest point labelled 2026-10-20 is Composer's 2026-10-21).
The pairing across symphonies is unaffected (every series shifts alike), but every date the operator sees (as-of, depth window, first/last date) is wrong by one day, and the `d < today_et` cut-off compares a shifted date.
The fix belongs to `composer_series_client` (shared with the retirement recommender, so the retirement summary's dates change too; say so in Contract Changes).
Plan 03 also renders the series' first and last dates, which consumes `SeriesResult.first_date`/`last_date` (closing the #145 post-merge review's CR-2 dead-field finding).
Real captures for every live symphony will be committed by the PM on branch `ux-overhaul/fixtures` under `tests/fixtures/composer/series_captured/` with a PROVENANCE.md; cite that branch in 03's Fixture provenance.
