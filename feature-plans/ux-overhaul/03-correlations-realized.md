# Feature: Correlations matrix on the realized Composer series
Status: planned
Story: none: operator chat request on 2026-10-22, saved verbatim in feature-plans/ux-overhaul/00-overview.source.md
Type: fix
Suite: ux-overhaul 03
Repos: PlanetStopper
Base: plan 02's tip with `ux-overhaul/fixtures` (7c0b7432: the real series captures and a real `RETIREMENT_RUN_SUMMARY` row) merged in
Ref: PlanetStopper@37c8e983b64c6980c28199a3f9318486b60f3864
Sensitive: none: the matrix is an advisory display; the retirement recommender's gates, thresholds, composite and candidate selection do not change (NB-4), and no Composer request is added to page rendering
Depends on: plan 02 (it also provides the shared dashboard primitives this plan's Design-System Mapping uses); the model-unpin plan (branch `model-unpin/fixtures`) merged into this plan's tip before its PR; the real captures on `ux-overhaul/fixtures` (Prerequisites)

## Trigger
The operator said the correlations he can see on the droplet are "still only just a list" (G-1, G-2). The app-wide UX audit rendered at the ref found the 12 by 12 grid with no cell fill, each cell a coloured number plus "n=13 thin" in 9 px warn text (3.63:1 contrast on paper), every one of the 132 off-diagonal cells marked thin with n from 0 to 28 (median 16), column headers truncated to 12 characters and row labels to 18 so that several names collide, a raw ISO as-of with microseconds, colour by absolute value so a diversifying -0.75 pair is as red as +0.97, and a legend marker no cell carries (audit C1 to C6, UX-4, UX-5, UX-24). The PM assigned the rebuild to this plan (G-19) and ruled D-2 and D-7 (G-25, G-30). The source is saved in `feature-plans/ux-overhaul/00-overview.source.md`.

What the code does at the ref:
1. The matrix reads at most 60 days of post-mortem files (`analytics.get_history_with_cache_invalidation`, default `days=60`, `analytics.py:1570`) and correlates each symphony's post-mortem `live_ret` values aligned by date (`app.py:6678-6699`, `analytics.py:305-330`); these are trigger-day returns, not a continuous series. The brief and the audit call this basis shadow history; the code shows post-mortem trigger-day returns.
2. The as-of is the render time (`as_of=datetime.now(_ET).isoformat()`, `app.py:7182`), not the data's last date.
3. Column headers use `| truncate(12)` and row labels `| truncate(18)` (`templates/ai_advisor.html:1855`, `:1863`).
4. Colour classes are chosen by `|r|` at 0.70 and 0.40 (`templates/ai_advisor.html:1873-1877`) and colour only the number's text (`templates/ai_advisor.html:1883`).
5. Each cell prints "n=" and the count, plus "thin" below 30 (`templates/ai_advisor.html:1885-1888`, `advisors/correlation_diagnostic.py:43`), and the legend prints a dagger no cell carries (`templates/ai_advisor.html:1906-1907`).
6. The retirement recommender's production tick reads each live symphony's realized deposit-adjusted daily return series from Composer (`advisors/retirement_recommender.py:579-623`, `advisors/composer_series_client.py`), computes every pairwise correlation over that series (`advisors/retirement_recommender.py:663-665`), and persists a run summary with the basis label "realized daily returns (Composer, deposit-adjusted)" (`advisors/retirement_recommender.py:145`, `:525-554`), the series depth range, the evaluated pair count, the excluded symphonies with reasons (`advisors/retirement_recommender.py:189-192`, `:600-620`), the screen threshold 0.65 and the stress-gate minimum (`advisors/retirement_recommender.py:180`), but not the pairs themselves. Its uncertainty gate needs at least 30 aligned days (`MIN_OBS_FLOOR`, `advisors/retirement_recommender.py:65`) and its stress gate at least 181 (`STRESS_GATE_MIN_ALIGNED_DAYS`, derived at `:164-180`). In the audit copy that run analysed 11 of 12 symphonies with 29 to 250 days of history.
7. The series client labels each point by its epoch's America/New_York date (`advisors/composer_series_client.py:169-171`, `:198`) and drops dates on or after today's ET date (`:202`). The PM's read-only capture on the droplet on 2026-10-22 (`GET /api/v0.1/portfolio/accounts/{account}/symphonies/{id}`) found every `epoch_ms` point stamped at 00:00 UTC (158 of 158 and 150 of 150 points), which in New York is the previous calendar day, so every return is labelled one day early (a return labelled Sunday 2026-10-18 appeared, and the latest point labelled 2026-10-20 is Composer's 2026-10-21). Pairing across symphonies is unaffected, because every series shifts alike, but every date the operator would see is wrong by one day and the partial-day cut-off compares a shifted date. `SeriesResult.first_date` and `last_date` are computed (`:222-223`) and read by nothing (finding CR-2 of the review after PR #145). The client is shared with the retirement recommender (`advisors/retirement_recommender.py:579-623`).
8. Composer's rate limit is shared with the live engine, which is why Run now is refused during market hours (`feature-plans/retirement-visibility.md` AC-7b); page rendering at the ref makes no Composer request for the matrix.

## Summary
The operator reads a colour-filled correlation heatmap of the live symphonies on the Correlations tab, computed from the same realized Composer daily series and the same run as the Retirement tab, with each cell's depth, a thin marker tied to the recommender's gates, full names, the excluded symphonies and an as-of that names its zone.
- The matrix shows the pairs the latest successful retirement run evaluated, under the same basis label, with no Composer call at render (AC-1).
- The as-of names the last trading date and the run time in ET (AC-2).
- Every realized-series date is the calendar date Composer stamps, read in UTC, and each symphony's first and last series dates are shown (AC-9).
- Each cell exposes its aligned days and date window (AC-3).
- One thin marker, tied to the 30 and 181 day gates, in both cells and legend (AC-4).
- Excluded symphonies are listed with sentence reasons (AC-5).
- Cells are filled by signed colour per D-7 (AC-6).
- Rows and columns carry distinct full names (AC-7).
- No run, a failed run and too few symphonies each have an honest state (AC-8).

## Requirements as Given
[G-1] and yet, here i am, not seeing any fucking retirement or correlation shit on the droplet page I'm viewing. 00-overview.source.md:7
[G-2] Correlations are still only just a list... 00-overview.source.md:7
[G-3] holy fuck this looks like shit, you've done literally 0 UX screenshot checking. 00-overview.source.md:8
[G-4] Your whole job for the rest of the day is an overhaul of the entire application, ensuring it fucking looks right on top of functions properly. 00-overview.source.md:8
[G-7] PM Step-2 answers ([PM-ASSUMED]; the operator is not available for questions; project directive: record assumptions, do not stop) 00-overview.source.md:11
[G-8] Building: (1) every page and tab of the dashboard app renders correctly, honestly and consistently with the main dashboard `/`, which is the operator's reference standard, and every control on it works. 00-overview.source.md:13
[G-9] (2) Users: the operator, through `/`, `/history`, `/performance`, `/settings` (+ the per-symphony settings modal), `/login`, `/ai-advisor` (tabs Overview, Correlations, Asset Swaps, Logic Changes, Chat, Strategy Builder, Frontrunner Builder) and the shared header/nav. 00-overview.source.md:14
[G-10] (3) Flows: as the audit records per surface; the workflow files under `.claude/workflows/` if any exist. 00-overview.source.md:15
[G-11] (4) Negatives: per the audit (empty states, unverifiable states, unavailable data, narrow widths, dark theme). 00-overview.source.md:16
[G-12] (5) Data/contracts: display and route JSON only, plus ONE data-basis change: the Correlations matrix moves from shadow history to the same realized Composer daily series the retirement recommender reads (`advisors/composer_series_client.py`, DE-RETIRE-VISIBILITY-001). 00-overview.source.md:17
[G-19] 03-correlations-realized: matrix on the realized Composer series with depth per cell, as-of with timezone, thin threshold tied to the recommender's 30/181-day gates, excluded symphonies with reasons, signed colour fill, full names on both axes, the same basis label as the Retirement panel. 00-overview.source.md:26
[G-24] PM rulings on the audit's decisions (record as decisions in the overview; each a default the operator may override) 00-overview.source.md:30
[G-25] D-2 matrix moves to the realized series; 00-overview.source.md:32
[G-30] D-7 signed colour: high positive correlation = warn/danger, near zero neutral, negative = diversifying (pos), never red for negative; 00-overview.source.md:37
[G-43] Plan 03: add a criterion (with N-kind scenarios) that every realized-series date is the calendar date Composer stamps, read in UTC. 00-overview.source.md:57
[G-44] Evidence (PM live capture 2026-10-22 on the droplet, read-only GET /api/v0.1/portfolio/accounts/{account}/symphonies/{id}): every `epoch_ms` point is stamped at 00:00 UTC (158 of 158 and 150 of 150 points); `advisors/composer_series_client.py` `_epoch_ms_to_et_date` maps each to America/New_York, which is the PREVIOUS calendar day, so returns are labelled one day early (a return labelled Sunday 2026-10-18 appeared; the latest point labelled 2026-10-20 is Composer's 2026-10-21). 00-overview.source.md:58
[G-45] The pairing across symphonies is unaffected (every series shifts alike), but every date the operator sees (as-of, depth window, first/last date) is wrong by one day, and the `d < today_et` cut-off compares a shifted date. 00-overview.source.md:59
[G-46] The fix belongs to `composer_series_client` (shared with the retirement recommender, so the retirement summary's dates change too; say so in Contract Changes). 00-overview.source.md:60
[G-47] Plan 03 also renders the series' first and last dates, which consumes `SeriesResult.first_date`/`last_date` (closing the #145 post-merge review's CR-2 dead-field finding). 00-overview.source.md:61
[G-48] Real captures for every live symphony will be committed by the PM on branch `ux-overhaul/fixtures` under `tests/fixtures/composer/series_captured/` with a PROVENANCE.md; cite that branch in 03's Fixture provenance. 00-overview.source.md:62

## Requirement Inventory
These are the overview's R ids. Overview items R-21, R-70 and R-71 (plan numbering, the Base chain, landing after model-unpin) are met by this plan's Suite, Base and Depends on headers.

| R-n | Requirement item | Source (G ids) | Maps to (AC ids, or NB-n) | Scope (full, or narrowed: what is left out) |
|---|---|---|---|---|
| R-2 | The operator sees the correlation analysis on the AI Advisor page on the droplet | G-1 | AC-1, AC-8 | full |
| R-3 | The Correlations view reads as a colour-coded matrix (heatmap), not a list of numbers | G-2, G-19, G-30 | AC-6 | narrowed: the combined Correlations and Retirement layout (Claude Design B2) is left out |
| R-4 | Every screen this plan changes is checked by rendered screenshots before it ships | G-3 | AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7, AC-8 | full |
| R-5 | Every page and tab looks right | G-4, G-8 | AC-6, AC-7 | narrowed: this plan's part is the Correlations tab |
| R-9 | Open questions are answered by recorded defaults, never by stopping | G-7 | AC-1, AC-6 | full |
| R-10 | Every page and tab renders honestly | G-8 | AC-1, AC-2, AC-3, AC-4 | narrowed: the Correlations tab |
| R-11 | The overhaul covers every page, the modal, the Advisor tabs and the header | G-9 | AC-1 | narrowed: the Correlations tab |
| R-12 | Flows follow the audit per surface, and workflow files where they exist | G-10 | AC-3, AC-7 | full |
| R-13 | Empty, unverifiable and unavailable states are honest on every surface this plan touches | G-11 | AC-4, AC-8 | full |
| R-16 | Only display and route JSON change, plus the one Correlations data-basis change | G-12 | AC-1 | full |
| R-50 | Exclusion reasons render as sentences | G-10 | AC-5 | narrowed: the Correlations part; the Retirement tab's part is plan 02's |
| R-52 | The Correlations matrix is computed from the realized Composer daily series the retirement recommender reads | G-12, G-19, G-25 | AC-1 | full |
| R-53 | Each matrix cell shows its depth | G-19 | AC-3 | full |
| R-54 | The matrix as-of names its timezone | G-19 | AC-2 | full |
| R-55 | The thin threshold is tied to the recommender's 30 and 181 day gates | G-19 | AC-4 | full |
| R-56 | Excluded symphonies are listed with reasons | G-19 | AC-5 | full |
| R-57 | Matrix cells are filled by signed colour per D-7 | G-19, G-30 | AC-6 | full |
| R-58 | Full names on both matrix axes | G-19 | AC-7 | full |
| R-59 | The matrix carries the same basis label as the Retirement panel | G-19 | AC-1 | full |
| R-75 | Every realized-series date is the calendar date Composer stamps, read in UTC, in the shared series client, for the matrix and the retirement recommender alike | G-43, G-44, G-45, G-46 | AC-9 | full |
| R-76 | The Correlations tab shows each symphony's first and last series dates from the series client | G-47 | AC-9 | full |
| R-77 | Fixtures use the PM's real captures for every live symphony from branch `ux-overhaul/fixtures` | G-48 | AC-9 | full |
| R-69 | The PM's rulings are recorded as decisions, each a default the operator may override | G-24 | AC-1, AC-6 | full |

## User Tasks
Workflow (AI Advisor Correlations tab): no workflow file: this repository has no `.claude/workflows/` directory at the ref.

### T-1: Read which symphonies move together
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Open `/ai-advisor` and click Correlations | a successful retirement run | A filled matrix with the basis label and an as-of in ET | S-1.1, S-2.1, S-6.1 |
| 2 | Hover or focus a cell | a pair with 120 aligned days | The cell's aligned days and date window show, and it carries the marker for pairs below the stress gate's minimum | S-3.1, S-4.2 |
| 3 | Read the legend and the excluded list | one excluded symphony | The legend's markers and bands match the cells; the excluded symphony is named with a sentence | S-4.1, S-5.1 |

## Earlier criteria on the same surface
| Earlier criterion (plan and AC) | Surface | Disposition (kept, changed or contradicted) | Why |
|---|---|---|---|
| `feature-plans/ai-advisor.obsolete.md` AC-1.1 (pairwise return correlation from a return series) | Correlations matrix | changed | The series becomes the realized Composer daily series (D-2) instead of post-mortem trigger-day returns |
| `feature-plans/ai-advisor.obsolete.md` AC-1.2 (each figure shows its sample basis) | Correlations cells | kept | AC-3 shows depth and window per cell, replacing the "n=" text |
| `feature-plans/ai-advisor.obsolete.md` AC-1.3 (an insufficient-data state, never a fabricated number) | Correlations empty state | kept | AC-8 keeps it and adds the no-run and failed-run states |
| `feature-plans/ai-advisor.obsolete.md` AC-1.4 (no backtest, no gate; crisis caveat always shown) | Correlations tab | kept | The matrix still runs no gate; the caveat stays (plan 01 removes its duplicate sentence) |
| `feature-plans/retirement-visibility.md` AC-7b (no Composer fetch loop during market hours) | Composer rate limit | kept | AC-1 renders from the run's stored results and makes no Composer request (S-1.6) |
| `feature-plans/retirement-visibility.md` scope boundary "No change to the Correlations tab or `advisors/correlation_diagnostic.py`" | Correlations tab | changed | That boundary bounded Cycle 2e; this plan changes the tab by the PM's ruling D-2, and `advisors/correlation_diagnostic.py`'s computation is unchanged |
| `feature-plans/retirement-recommender-core.md` (gates, thresholds, screen 0.65) | Retirement recommender | kept | The matrix reads the recommender's numbers and never changes them |
| `feature-plans/retirement-visibility.md` AC-1 (`SeriesResult.returns` keyed by America/New_York calendar date) | Series client date labels | contradicted | Composer stamps each point at 00:00 UTC of its date (PM capture, G-44), so the New York reading labels every return a day early; AC-9 reads the stamp in UTC |

## Acceptance Criteria (Building)
### AC-1: The matrix shows the latest successful retirement run's pairs on its basis
Requirement items: R-2, R-4, R-9, R-10, R-11, R-16, R-52, R-59, R-69
Rule: When the Correlations tab renders, the matrix shall show, for every pair of symphonies the latest successful retirement run evaluated, the Pearson correlation of their aligned realized daily returns and their aligned-day count exactly as that run computed them, under that run's basis label verbatim, without any Composer request during the page render.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-1.1 | B | a successful run that analysed 11 symphonies and evaluated 55 pairs | the operator opens Correlations | the matrix has 11 rows and 11 columns, 110 off-diagonal cells, and each cell's correlation and aligned days equal the run's values for that pair |
| S-1.2 | B | the same run | the tab renders | the basis line reads "realized daily returns (Composer, deposit-adjusted)", the same string the Retirement tab prints |
| S-1.3 | N3 | a pair the run evaluated whose correlation is undefined (zero variance or fewer than 2 aligned days) | the tab renders | the cell reads "undefined", carries no fill, and shows its aligned days |
| S-1.4 | B | a later successful run that analysed 12 symphonies | the tab renders after it | the matrix shows 12 rows and columns from that later run |
| S-1.5 | N7 | any run | the tab renders | the run summary, the recommendations and the recommender's numbers are unchanged, and the Asset Swaps and Logic Changes symphony lists are unchanged |
| S-1.6 | N7 | the market is open | the operator opens and reloads Correlations three times | no request leaves for Composer during any render |
| S-1.7 | N5 | a run finishes while the tab is open | the operator reloads | the matrix shows the new run's pairs |
| S-1.8 | N6 | reading the stored run fails | the tab renders | the AC-8 unavailable state shows and the rest of the page renders |
| S-1.9 | B | a successful run that stored its evaluated pairs | the operator requests `GET /api/retirement-recommendations` | the `last_run` and `cards_run` objects carry the stored pairs (both Composer hashes, the correlation, the aligned-day count and the window), equal to those the tab renders |

Negative coverage: N1 n/a: the matrix takes no operator input · N2 n/a: display only · N3 S-1.3 · N4 n/a: the page is behind the global auth hook, unchanged · N5 S-1.7 · N6 S-1.8 · N7 S-1.5, S-1.6 · N8 n/a: no edit state
Questions: Q-1

### AC-2: The as-of names the last trading date and the run time in ET
Requirement items: R-4, R-10, R-54
Rule: When the matrix renders, its as-of shall state the last trading date its series reach and the run's time in ET, in the dashboard's stamp form, without fractional seconds.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | the nightly run at 2026-10-22T03:45:00Z (03:45 UTC on the droplet's clock, 23:45 ET on 2026-10-21) after Composer posted the 2026-10-21 close | the tab renders | the as-of reads "Series through 2026-10-21 · run 2026-10-21 23:45 ET", naming that day's close |
| S-2.2 | N1 | series whose last dates differ by symphony | the tab renders | the as-of names the latest date, and each cell's window (AC-3) states its own last date |
| S-2.3 | N3 | a run with no recorded run time | the tab renders | the run part reads "run time not recorded" |
| S-2.4 | N7 | any render | the tab renders | the as-of no longer reflects the render time |

Negative coverage: N1 S-2.2 · N2 n/a: display only · N3 S-2.3 · N4 n/a: page behind the global auth hook, unchanged · N5 n/a: the as-of is a property of the run, the same on every render · N6 n/a: a failed read is AC-8's state · N7 S-2.4 · N8 n/a: no edit state
Questions: none

### AC-3: Each cell exposes its aligned days and date window
Requirement items: R-4, R-10, R-12, R-53
Rule: When the operator hovers or focuses a matrix cell, the cell shall show the pair's two display names, the correlation, the aligned-day count and the first and last aligned dates, and no cell shall print "n=" or "thin" as text.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | a pair with 120 aligned days from 2026-04-02 to 2026-10-21 | the operator hovers its cell | the cell shows both names, the correlation to two decimals, "120 aligned days" and "2026-04-02 to 2026-10-21" |
| S-3.2 | B | the same pair | the operator focuses the cell with the keyboard | the same text is available to assistive technology |
| S-3.3 | N1 | a pair with 0 aligned days | the operator hovers | it shows "0 aligned days" and no window |
| S-3.4 | N7 | any cell | it renders | the visible text in the cell is the correlation only |

Negative coverage: N1 S-3.3 · N2 n/a: display only · N3 n/a: undefined correlations are AC-1's case (S-1.3) · N4 n/a: page behind the global auth hook, unchanged · N5 n/a: hover is read-only · N6 n/a: no request · N7 S-3.4 · N8 n/a: no edit state
Questions: none

### AC-4: One thin marker, tied to the recommender's gates
Requirement items: R-4, R-10, R-13, R-55
Rule: When a pair has fewer aligned days than the recommender's stress-gate minimum, its cell shall carry a thin marker (a stronger one below the uncertainty gate's minimum), drawn by reduced fill or hatching rather than words, and the legend shall show the same markers with both minimums read from the run summary and the recommender, each with the reason a pair below it cannot be recommended.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | B | a run whose summary states a stress-gate minimum of 181 and the uncertainty minimum 30 | the tab renders | the legend shows the two markers with "fewer than 30 aligned days: the uncertainty gate cannot pass" and "fewer than 181 aligned days: the stress gate cannot pass" |
| S-4.2 | B | a pair with 120 aligned days | the tab renders | its cell carries the stress-gate marker only |
| S-4.3 | N1 | pairs with exactly 29, 30, 180 and 181 aligned days | the tab renders | 29 carries the stronger marker, 30 and 180 the stress-gate marker, 181 no marker |
| S-4.4 | B | a pair with 250 aligned days | the tab renders | its cell carries no thin marker |
| S-4.5 | N7 | a retuned stress constant in the recommender | the next run's summary renders | the legend's number follows the summary, never a number typed in the template |
| S-4.6 | N3 | no cell is thin | the tab renders | the legend still shows both markers, so the absence is readable |

Negative coverage: N1 S-4.3 · N2 n/a: display only · N3 S-4.6 · N4 n/a: page behind the global auth hook, unchanged · N5 n/a: markers derive from stored counts, the same on every render · N6 n/a: a failed read is AC-8's state · N7 S-4.5 · N8 n/a: no edit state
Questions: none

### AC-5: Excluded symphonies are listed with sentence reasons
Requirement items: R-4, R-50, R-56
Rule: When the run excluded symphonies, the Correlations tab shall list each by display name with its reason as a sentence from the mapping shared with the Retirement tab (plan 02 AC-7).

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | the run excluded "Quiet Meadow Bonds" for `series_too_short` | the tab renders | below the matrix: "Not in the matrix: Quiet Meadow Bonds. Its Composer series has fewer than 2 daily returns." |
| S-5.2 | N1 | exclusions for `no_account` and `fetch_error: HTTP 503` | the tab renders | each reads its sentence from the shared mapping, the second naming HTTP 503 |
| S-5.3 | N3 | no exclusions | the tab renders | no excluded list shows |
| S-5.4 | N3 | an unmapped reason | the tab renders | the shared mapping's fallback ("Excluded: " and the raw reason) shows |
| S-5.5 | N7 | the same run | both tabs render | the Correlations and Retirement tabs show the same sentence for the same symphony |

Negative coverage: N1 S-5.2 · N2 n/a: display only · N3 S-5.3, S-5.4 · N4 n/a: page behind the global auth hook, unchanged · N5 n/a: server-rendered once per load · N6 n/a: a failed read is AC-8's state · N7 S-5.5 · N8 n/a: no edit state
Questions: none

### AC-6: Cells are filled by signed colour
Requirement items: R-3, R-4, R-5, R-9, R-57, R-69
Rule: When a cell's correlation is defined, its background shall be filled from existing tokens by sign and band: at or above the run's screen threshold the danger token (`--studio-neg`), from 0.40 up to the threshold the warn token (`--studio-warn`), between -0.40 and 0.40 neutral, and at or below -0.40 the diversifying token (`--studio-pos`), with intensity rising with `|r|`, the band decided on the same two-decimal value the cell prints, the number staying in the ink token, and a negative correlation never filled with the danger token.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | pairs at +0.97, +0.52, +0.10, -0.12 and -0.75, screen threshold 0.65 | the tab renders | +0.97 fills with the danger token, +0.52 with the warn token, +0.10 and -0.12 stay neutral, -0.75 fills with the diversifying token; every number is in the ink token |
| S-6.2 | N1 | pairs at exactly 0.65, 0.40, -0.40, 0.6449 and a stored 0.6499 | the tab renders | 0.65 danger, 0.40 warn, -0.40 diversifying, 0.6449 prints +0.64 and fills warn, the stored 0.6499 prints +0.65 and fills danger |
| S-6.3 | N1 | pairs at +0.70 and +0.95 | the tab renders | the +0.95 fill is more intense than the +0.70 fill, both from the danger token |
| S-6.4 | N7 | -1.00 | the tab renders | the fill uses the diversifying token, never the danger token |
| S-6.5 | B | the legend | it renders | it names the four bands with their bounds and the threshold read from the run summary |
| S-6.6 | N3 | the diagonal | it renders | diagonal cells read 1.00 with no band fill |
| S-6.7 | N7 | the dark theme | the tab renders | fills use the same tokens, which the dark theme defines; no raw colour value is in the template |

Negative coverage: N1 S-6.2, S-6.3 · N2 n/a: display only · N3 S-6.6 · N4 n/a: page behind the global auth hook, unchanged · N5 n/a: server-rendered once per load · N6 n/a: a failed read is AC-8's state · N7 S-6.4, S-6.7 · N8 n/a: no edit state
Questions: Q-2

### AC-7: Rows and columns carry distinct full names
Requirement items: R-4, R-5, R-12, R-58
Rule: When the matrix renders, each row shall carry a number and the symphony's full display name, each column header shall carry the matching number, and no label shall be an ellipsis-truncated duplicate.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | live symphonies "Harbor Lights Momentum", "Harbor Lights Momentum v2" and "Harbor Lights Momentum v3" | the tab renders | each row shows its number and full name, each column header shows the matching number, the three columns differ from each other, and each label exposes its full name |
| S-7.2 | N1 | two live symphonies with the identical display name | the tab renders | their labels still differ |
| S-7.3 | N1 | a name with an HTML bold tag and an ampersand | the tab renders | the characters show literally |
| S-7.4 | N3 | a symphony in the run that is no longer in the live state | the tab renders | its labels show the Composer hash |
| S-7.5 | N7 | the cells | labels change | each cell still maps to the same pair |

Negative coverage: N1 S-7.2, S-7.3 · N2 n/a: display only · N3 S-7.4 · N4 n/a: page behind the global auth hook, unchanged · N5 n/a: server-rendered once per load · N6 n/a: names come from the live state already read for the page · N7 S-7.5 · N8 n/a: no edit state
Questions: Q-3

### AC-8: No run, a failed run and too few symphonies each have an honest state
Requirement items: R-2, R-4, R-13
Rule: If no successful retirement run with evaluated pairs exists, then the Correlations tab shall say so, name the latest run's state when one exists (`series_unavailable`, `insufficient_symphonies` or `internal_error`) and name when the next run happens; and if the latest run was not successful while an earlier one was, the tab shall show the earlier run's matrix with a note naming that run's date.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-8.1 | N3 | no retirement run has completed | the tab renders | it reads "No realized series yet: the matrix fills after the next retirement run (nightly at 03:45 UTC, or Run now on the Retirement tab)." and no matrix |
| S-8.2 | N3 | the latest run is `internal_error` and an earlier successful run exists | the tab renders | the earlier run's matrix shows with a note naming that run's date in ET |
| S-8.3 | N3 | the only runs are `series_unavailable` or `insufficient_symphonies` | the tab renders | the tab names that state with the analysed count from the summary (for example "The last run found fewer than two symphonies with a usable series."), states that no matrix exists yet, and names the next run |
| S-8.7 | N3 | the only run is `internal_error` | the tab renders | the tab names the failed run and its time in ET and the next run |
| S-8.4 | N3 | the real successful run summary row committed on `ux-overhaul/fixtures`, which predates this plan and records no pairs | the tab renders | the no-run text of S-8.1 shows, naming that the matrix needs a newer run |
| S-8.5 | B | a successful run with pairs | the tab renders | no unavailable state shows |
| S-8.6 | N7 | any state | the tab renders | the crisis caveat renders |

Negative coverage: N1 n/a: no operator input · N2 n/a: display only · N3 S-8.1, S-8.2, S-8.3, S-8.4, S-8.7 · N4 n/a: page behind the global auth hook, unchanged · N5 n/a: server-rendered once per load · N6 n/a: a failed read is covered by S-1.8 · N7 S-8.6 · N8 n/a: no edit state
Questions: none

### AC-9: Every realized-series date is the calendar date Composer stamps
Requirement items: R-75, R-76, R-77
Rule: When the series client reads a Composer daily-series point, it shall label the point with the calendar date of its `epoch_ms` read in UTC, the date Composer stamps, keep a point whose date is before today's ET date, or equals today's ET date when the ET clock is at or past 16:00 (the session has closed, so the point is that day's final close), and drop it otherwise, and each symphony's label on the Correlations tab shall show the window the matrix actually uses (its last `RETIREMENT_LOOKBACK_DAYS` returns, 250, `advisors/retirement_recommender.py:103`) with its first and last dates, and the full series depth from `SeriesResult.first_date` and `last_date` and its point count as secondary text, all read from the same labels.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-9.1 | B | a real captured series whose points are stamped 00:00 UTC, the last at 2026-10-21T00:00:00Z, read on 2026-10-22 ET | the client parses it | the last return is labelled 2026-10-21, never 2026-10-20 |
| S-9.2 | N1 | the same capture, whose stamps fall only on trading days | the client parses it | no return is labelled a Saturday or a Sunday |
| S-9.3 | N1 | a point stamped 2026-10-22T00:00:00Z read at 15:59 ET on 2026-10-22, during the session | the client parses it | that point is dropped as today's partial day, and 2026-10-21 is the last date |
| S-9.11 | N1 | the same point read at 16:00 ET on 2026-10-22, the close | the client parses it | the point is kept as that day's final close, and 2026-10-22 is the last date |
| S-9.12 | N1 | a point stamped 2026-10-21T00:00:00Z read by the nightly run at 23:45 ET on 2026-10-21 (03:45 UTC on 2026-10-22) | the client parses it | the point is kept, and 2026-10-21 is the last date |
| S-9.4 | N1 | two points stamped within one UTC date | the client parses them | one value per date, the later point winning, as at the ref |
| S-9.5 | N1 | a point stamped at 2026-10-21T20:00:00Z | the client parses it | it is labelled 2026-10-21 |
| S-9.6 | N7 | the same capture parsed before and after the change | the recommender evaluates two symphonies | every return value, every aligned-day count, every correlation and every gate result is equal; only the date labels move one day later |
| S-9.7 | B | a run with two symphonies whose series run 2025-03-03 to 2026-10-21 and 2026-06-02 to 2026-10-21 | the operator hovers or focuses each symphony's label on the Correlations tab | each shows the matrix window it uses (the first symphony's last 250 returns, ending 2026-10-21; the second symphony's whole series, being shorter) and, as secondary text, its full series dates and point count read from the run's stored `first_date` and `last_date` |
| S-9.8 | N3 | a symphony whose series yields no returns | the tab renders | it is in the excluded list (AC-5) and no series dates are shown for it |
| S-9.9 | N7 | the Retirement tab and the Correlations as-of for the same run | both render | the one line this plan adds to the Retirement tab ("Series through" and the date, built after plan 02) and the Correlations as-of name the same last series date |
| S-9.10 | N6 | a fetch error for one symphony | the run completes | that symphony is excluded with its reason, as at the ref |

Negative coverage: N1 S-9.2, S-9.3, S-9.4, S-9.5, S-9.11, S-9.12 · N2 n/a: parsing refuses nothing new; malformed bodies keep their existing errors · N3 S-9.8 · N4 n/a: the client is called only by the recommender, off any request path · N5 n/a: parsing the same body twice gives the same labels · N6 S-9.10 · N7 S-9.6, S-9.9 · N8 n/a: no edit state
Questions: none

## Not Building
Non-goals:
- NB-1: The combined Correlations and Retirement view and a heatmap layout beyond these criteria (overview NB-5, Claude Design B2).
- NB-2: Linking a recommendation to its matrix cell (part of Claude Design B2) (approved by the PM, [PM-ASSUMED], operator may override).
- NB-3: A Discuss entry point on correlation cells (plan 02 NB-3) (approved by the PM, [PM-ASSUMED], operator may override).

Behaviour that stays exactly as it is:
- NB-4: The retirement recommender's series collection (apart from the date labels AC-9 corrects in the shared client), gates, thresholds, composite weights, candidate selection and recommendations; `advisors/correlation_diagnostic.py`'s computation; the nightly tick's schedule and the Run-now route.
- NB-5: The post-mortem history reads the Asset Swaps and Logic Changes symphony lists use (`app.py:6716-6722`).

Deferred:
- NB-6: Holdings-level correlation, which the original diagnostic allowed where data exists (approved by the PM, [PM-ASSUMED], operator may override).

Protected surfaces consumed, not changed:
- NB-7: `advisors/composer_series_client.py`'s request (GET only, the same URL, headers, retries and timeouts), the retirement no-trade boundary tests, and Composer's rate limit: no request is added to any page render; only its date labelling changes (AC-9).

## Defect Classes
| DC-n | Class (what goes wrong) | Trigger (B or N1-N8) | Sibling search (tool, query, scope) | Scenarios | Raised in review as |
|---|---|---|---|---|---|
| DC-1 | A threshold or label typed a second time instead of read from its source | B | grep `0\.65\|0\.70\|0\.40\|181\|\b30\b\|realized daily returns` in `templates/ai_advisor.html`, `static/ai_advisor.js` and `app.py`, PlanetStopper | S-1.2, S-4.1, S-4.5, S-6.5 | - |
| DC-2 | An undefined value rendered as a number or colour | N3 | grep `correlation is not none\|abs_corr\|or 0` in `templates/ai_advisor.html`, PlanetStopper | S-1.3, S-6.6 | - |
| DC-3 | A page render reaching a rate-limited external API | N7 | grep `composer_series_client\|fetch_symphony_daily_returns\|requests.get` reachable from `ai_advisor_tab`, PlanetStopper | S-1.6 | - |
| DC-4 | Ambiguous truncated labels | B | grep `truncate(` in `templates/`, PlanetStopper | S-7.1, S-7.2 | - |
| DC-5 | A sign family covered on one member only | N1 | read: every colour-band test; each of strong positive, moderate positive, neutral, strong negative, boundary and undefined has a case | S-6.1, S-6.2, S-6.4 | - |
| DC-7 | A timestamp read in the wrong timezone, shifting every date label | N1 | grep `fromtimestamp\|tz=_ET\|today_et\|isoformat()` in `advisors/composer_series_client.py`, `advisors/retirement_recommender.py` and `app.py`, PlanetStopper | S-9.1, S-9.3, S-9.5 | - |
| DC-6 | Two tabs explaining the same exclusion differently | B | grep each reason value in `templates/` and `app.py`, PlanetStopper | S-5.5 | - |

## Worked Example
The nightly run at 2026-10-22T03:45:00Z (03:45 UTC) analysed 11 symphonies with 29 to 250 days of realized history and excluded "Quiet Meadow Bonds" for `series_too_short`. The Correlations tab shows an 11 by 11 matrix under "realized daily returns (Composer, deposit-adjusted)" and "Series through 2026-10-21 · run 2026-10-21 23:45 ET", the close Composer posted that day. "Copper Ridge Rotation" against "Harbor Lights Momentum" reads +0.82 in a strong danger fill with the stress-gate marker; hovering shows "120 aligned days, 2026-04-02 to 2026-10-21". "Copper Ridge Rotation" against "Harbor Lights Momentum v2" reads -0.61 in a diversifying fill. Below the matrix: "Not in the matrix: Quiet Meadow Bonds. Its Composer series has fewer than 2 daily returns." (S-1.1, S-2.1, S-3.1, S-4.2, S-5.1, S-6.1).

Failure case: the next night's run ended `internal_error`. The tab shows the earlier run's matrix and the note "From the run of 2026-10-21 23:45 ET; the latest run did not complete." (S-8.2).

## Contract Changes and Consumers
- `advisors/composer_series_client.SeriesResult`: every `returns` key, `first_date` and `last_date` moves one calendar day later (the date Composer stamps, read in UTC); return values are unchanged; the partial-day cut-off keeps a same-day point once the ET clock is at or past 16:00, so a run after the close (the 03:45 UTC nightly run, or Run now after 16:00 ET) includes that day's close if Composer has posted it. Consumers: the retirement recommender, which gains the same rule through the shared client, so the retirement run summary's dates and every pair's aligned-date window move one day later for the same data, while counts, correlations and gate results stay equal (S-9.6); `first_date` and `last_date` gain their first reader, the Correlations tab (S-9.7), closing CR-2. A summary row written before the change keeps its old labels until the next run.
- The retirement run's stored output gains every evaluated pair (both Composer hashes, the correlation, the aligned-day count and the aligned date window), additively and without a schema change; consumers: the Correlations tab (this plan) and `GET /api/retirement-recommendations`, whose `last_run` and `cards_run` objects then carry the pairs. The route's three top-level keys are unchanged.
- The template context for `/ai-advisor` replaces the render-time `as_of` with the run's as-of; the post-mortem-based `correlation_matrix` is no longer computed for the tab.

## Prerequisites
Real responses from Composer's per-symphony daily-series endpoint (`GET /api/v0.1/portfolio/accounts/{account}/symphonies/{id}`), captured read-only by the PM on the droplet for every live symphony and committed on branch `ux-overhaul/fixtures` (7c0b7432) under `tests/fixtures/composer/series_captured/` with a `PROVENANCE.md`, together with a real `RETIREMENT_RUN_SUMMARY` row. The bodies are raw: real names and Composer ids, nothing edited, and no account ids. Correlation pair records in any fixture are computed only from those real captures, never from the synthetic-from-docs fixture `tests/fixtures/composer/symphony_daily_series_v1.json`, which stays for parser-shape tests only. Without the captures, RED for AC-1, AC-3, AC-4 and AC-6 does not start.

## Migrations Required
none: the pairs are stored inside the existing JSON `raw_response` of the run's `advisor_observations` row; no table or column changes (overview NB-2).

## Security Considerations
- Input validation and injection: display names and reasons render through autoescaping; no `| safe`.
- Authorization and access control: no route or guard changes.
- Data exposure: the pairs added to `GET /api/retirement-recommendations` are derived returns already shown on the authenticated page.
- Client-side rendering: hover text is set through attributes or `textContent`, never `innerHTML` with a name.
- Abuse or rate limits: rendering makes no Composer request (S-1.6); the run's fetch pacing (1 request per second) is unchanged.

## Design-System Mapping
| Component | Element | Required primitive or token | Notes |
|---|---|---|---|
| Matrix cell | fill | `color-mix` over `--studio-neg`, `--studio-warn`, `--studio-pos` with transparent | intensity by `|r|`; no new token |
| Matrix cell | number | `--studio-ink` | tabular figures |
| Matrix cell | thin marker | reduced fill or a hatch built from `--studio-ink-faint` | one marker style per gate |
| Matrix header | basis line and as-of | `.legend-as-of` | the shared definition plan 02 provides (overview SD-6) |
| Matrix | labels | `.section-header` scale for the axis labels | full names on hover and focus |
| Legend | bands and markers | the same fills and markers as the cells | threshold numbers read from the summary |
| Excluded list | names and sentences | body text in `--studio-ink-dim` | |

## Definition of Done (Verification)
| AC | Scenarios | Test level | Real-entrypoint test |
|---|---|---|---|
| AC-1 | S-1.1, S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8, S-1.9 | the tick's stored pairs as a unit against `compute_pairwise_correlations`; template render | `GET /ai-advisor` with a captured run row, with outbound requests blocked to prove S-1.6 |
| AC-2 | S-2.1, S-2.2, S-2.3, S-2.4 | template render | `GET /ai-advisor` |
| AC-3 | S-3.1, S-3.2, S-3.3, S-3.4 | template render; live hover in a browser | live render |
| AC-4 | S-4.1, S-4.2, S-4.3, S-4.4, S-4.5, S-4.6 | template render with pair depths at each boundary | `GET /ai-advisor` |
| AC-5 | S-5.1, S-5.2, S-5.3, S-5.4, S-5.5 | shared mapping unit; template render of both tabs | `GET /ai-advisor` |
| AC-6 | S-6.1, S-6.2, S-6.3, S-6.4, S-6.5, S-6.6, S-6.7 | band function as a unit; template asserts token use and no raw colour | `GET /ai-advisor` |
| AC-7 | S-7.1, S-7.2, S-7.3, S-7.4, S-7.5 | template render | `GET /ai-advisor` |
| AC-8 | S-8.1, S-8.2, S-8.3, S-8.4, S-8.5, S-8.6, S-8.7 | template render with captured run rows per state | `GET /ai-advisor` |
| AC-9 | S-9.1, S-9.2, S-9.3, S-9.4, S-9.5, S-9.6, S-9.7, S-9.8, S-9.9, S-9.10, S-9.11, S-9.12 | `_parse_series_body` as a unit over the real captures; recommender run over the same captures before and after; template render of the date tooltips | `GET /ai-advisor` after a run over the captures; the Retirement tab's dates for the same run |

Gates: `ruff format --check`; `ruff check`; a scoped pytest run of `tests/ai_advisor`, `tests/app`, `tests/advisors`, `tests/security` and `tests/js_syntax` with at most 4 workers, never `-n auto`; CI green on the exact SHA; after `/review`, the PM's live check on the droplet after the next retirement run, of T-1.
Fixture provenance: the series captures and the `RETIREMENT_RUN_SUMMARY` row on `ux-overhaul/fixtures` (7c0b7432) are raw bodies with real names and Composer ids, nothing edited, and no account ids. Summary rows for the other statuses are derived from that real row with only the status field and its dependent counts changed, signed off by the PM as producer owner (overview SD-9). Series and pair records come only from the PM's real Composer captures on branch `ux-overhaul/fixtures` (`tests/fixtures/composer/series_captured/` with its `PROVENANCE.md`), run through the recommender (Prerequisites), one record per case the code distinguishes (a full-depth pair, a pair under 181 aligned days, a pair under 30, an undefined pair), never typed by hand and never computed from the synthetic-from-docs fixture `tests/fixtures/composer/symphony_daily_series_v1.json`.
Screenshots: the Correlations tab at 1440x900 and 375x812, light and dark, compared with the audit's captures (A-CO series).

## Known-Unconfirmed and Standing Risk
- `deposit_adjusted_series` semantics are unverified in Composer's documentation, and no real-response fixture has been captured (`DE-RETIRE-VISIBILITY-001`); the matrix inherits whatever that series means.
- The matrix fills only after the first run that stores pairs; until the nightly run after deploy, or a Run now outside market hours, the tab shows S-8.4's state. The PM's live check runs after that run.
- The production run's depth (29 to 250 days in the audit copy) means many pairs sit below 181 aligned days, so most cells will carry the stress-gate marker for some time.

## Open Questions
| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | Does the matrix use the latest successful retirement run's series, or fetch its own copy from Composer? | AC-1 | The run's series: one fetch per night, no Composer call at render, and the two tabs describe the same data | default approved by the PM, [PM-ASSUMED], operator may override |
| Q-2 | Should the high band start at the recommender's screen threshold (0.65) instead of the old 0.70? | AC-6 | Yes, so a danger cell is a pair the recommender screens | default approved by the PM, [PM-ASSUMED], operator may override |
| Q-3 | removed: decided by the PM (PM-5): numbered column headers with full names on rows | AC-7 | none | none needed: removed |

## Decisions
| ID | Decision | Rationale |
|---|---|---|
| DEC-1 | The matrix's numbers are the retirement run's numbers | The Correlations and Retirement tabs then cannot disagree about the same pair |
| DEC-2 | The neutral band is -0.40 to 0.40, keeping the old moderate bound | The audit's legend used 0.40; D-7 fixes the sign, not the bound |
| PM-1 | Fixture provenance matches the committed captures: raw bodies, real names and ids, nothing edited, no account ids | PM ruling on the Gate-2 approach |
| PM-2 | `GET /api/retirement-recommendations` carries the stored pairs (S-1.9) | PM ruling on the Gate-2 approach |
| PM-3 | Each symphony label shows the matrix's actual window (the last 250 returns) with full series depth as secondary text | PM ruling on the Gate-2 approach |
| PM-4 | A cell's colour band follows the two-decimal value it prints | PM ruling on the Gate-2 approach; the colour and the number cannot disagree |
| PM-5 | Numbered column headers, full names on rows | PM ruling on the Gate-2 approach; settles Q-3 |
| PM-6 | The Retirement tab's "Series through" line is owned by this plan and built after plan 02 | PM ruling on the Gate-2 approach |
| PM-7 | S-8.4 uses the committed real summary row | PM ruling on the Gate-2 approach |
| PM-8 | The nightly run is labelled 03:45 UTC (overview SD-10) | PM ruling: the droplet's clock is UTC |
| PM-9 | A point is kept when its UTC-stamped date is before today's ET date, or equals it and the ET clock is at or past 16:00 | PM ruling: a run after the close keeps that day's final close; a run during the session drops the partial day |
| PM-A | Plan 03 approved at Step 7 under the project's standing autonomy directive (operator unavailable), with its Gate-2 approach `feature-plans/ux-overhaul/approach/03-correlations-realized.md` and the suite approach `feature-plans/ux-overhaul/approach/00-suite.md`; every open question takes its default or its recorded ruling, and every Not Building entry is approved | [PM-ASSUMED]; the operator may override |

## Docs to Update
- `docs/generated/app.md` (the Advisor page's Correlations section), `docs/generated/advisors_retirement_recommender.md` (stored pairs), `docs/generated/INDEX.md`.
- A `DECISIONS.md` entry appended at ship by the doc writer.
- A drafted `.claude/CLAUDE.md` Key Files row update for `advisors/retirement_recommender.py` and `templates/ai_advisor.html`, for PM approval.

## Follow-Ups
- The combined view and cell links (NB-1, NB-2): Claude Design B2, overview NB-5.
- A real Composer series fixture: the `DE-RETIRE-VISIBILITY-001` follow-up.
