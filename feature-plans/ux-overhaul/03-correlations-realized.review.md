# Approval packet: Correlations matrix on the realized Composer series

## What is being built

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

## Acceptance criteria and scenarios

### AC-1: The matrix shows the latest successful retirement run's pairs on its basis
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

Negative coverage: N1 n/a: the matrix takes no operator input | N2 n/a: display only | N3 S-1.3 | N4 n/a: the page is behind the global auth hook, unchanged | N5 S-1.7 | N6 S-1.8 | N7 S-1.5, S-1.6 | N8 n/a: no edit state

### AC-2: The as-of names the last trading date and the run time in ET
Rule: When the matrix renders, its as-of shall state the last trading date its series reach and the run's time in ET, in the dashboard's stamp form, without fractional seconds.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | the nightly run at 2026-10-22T03:45:00Z (03:45 UTC on the droplet's clock, 23:45 ET on 2026-10-21) after Composer posted the 2026-10-21 close | the tab renders | the as-of reads "Series through 2026-10-21 | run 2026-10-21 23:45 ET", naming that day's close |
| S-2.2 | N1 | series whose last dates differ by symphony | the tab renders | the as-of names the latest date, and each cell's window (AC-3) states its own last date |
| S-2.3 | N3 | a run with no recorded run time | the tab renders | the run part reads "run time not recorded" |
| S-2.4 | N7 | any render | the tab renders | the as-of no longer reflects the render time |

Negative coverage: N1 S-2.2 | N2 n/a: display only | N3 S-2.3 | N4 n/a: page behind the global auth hook, unchanged | N5 n/a: the as-of is a property of the run, the same on every render | N6 n/a: a failed read is AC-8's state | N7 S-2.4 | N8 n/a: no edit state

### AC-3: Each cell exposes its aligned days and date window
Rule: When the operator hovers or focuses a matrix cell, the cell shall show the pair's two display names, the correlation, the aligned-day count and the first and last aligned dates, and no cell shall print "n=" or "thin" as text.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | a pair with 120 aligned days from 2026-04-02 to 2026-10-21 | the operator hovers its cell | the cell shows both names, the correlation to two decimals, "120 aligned days" and "2026-04-02 to 2026-10-21" |
| S-3.2 | B | the same pair | the operator focuses the cell with the keyboard | the same text is available to assistive technology |
| S-3.3 | N1 | a pair with 0 aligned days | the operator hovers | it shows "0 aligned days" and no window |
| S-3.4 | N7 | any cell | it renders | the visible text in the cell is the correlation only |

Negative coverage: N1 S-3.3 | N2 n/a: display only | N3 n/a: undefined correlations are AC-1's case (S-1.3) | N4 n/a: page behind the global auth hook, unchanged | N5 n/a: hover is read-only | N6 n/a: no request | N7 S-3.4 | N8 n/a: no edit state

### AC-4: One thin marker, tied to the recommender's gates
Rule: When a pair has fewer aligned days than the recommender's stress-gate minimum, its cell shall carry a thin marker (a stronger one below the uncertainty gate's minimum), drawn by reduced fill or hatching rather than words, and the legend shall show the same markers with both minimums read from the run summary and the recommender, each with the reason a pair below it cannot be recommended.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | B | a run whose summary states a stress-gate minimum of 181 and the uncertainty minimum 30 | the tab renders | the legend shows the two markers with "fewer than 30 aligned days: the uncertainty gate cannot pass" and "fewer than 181 aligned days: the stress gate cannot pass" |
| S-4.2 | B | a pair with 120 aligned days | the tab renders | its cell carries the stress-gate marker only |
| S-4.3 | N1 | pairs with exactly 29, 30, 180 and 181 aligned days | the tab renders | 29 carries the stronger marker, 30 and 180 the stress-gate marker, 181 no marker |
| S-4.4 | B | a pair with 250 aligned days | the tab renders | its cell carries no thin marker |
| S-4.5 | N7 | a retuned stress constant in the recommender | the next run's summary renders | the legend's number follows the summary, never a number typed in the template |
| S-4.6 | N3 | no cell is thin | the tab renders | the legend still shows both markers, so the absence is readable |

Negative coverage: N1 S-4.3 | N2 n/a: display only | N3 S-4.6 | N4 n/a: page behind the global auth hook, unchanged | N5 n/a: markers derive from stored counts, the same on every render | N6 n/a: a failed read is AC-8's state | N7 S-4.5 | N8 n/a: no edit state

### AC-5: Excluded symphonies are listed with sentence reasons
Rule: When the run excluded symphonies, the Correlations tab shall list each by display name with its reason as a sentence from the mapping shared with the Retirement tab (plan 02 AC-7).

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | the run excluded "Quiet Meadow Bonds" for `series_too_short` | the tab renders | below the matrix: "Not in the matrix: Quiet Meadow Bonds. Its Composer series has fewer than 2 daily returns." |
| S-5.2 | N1 | exclusions for `no_account` and `fetch_error: HTTP 503` | the tab renders | each reads its sentence from the shared mapping, the second naming HTTP 503 |
| S-5.3 | N3 | no exclusions | the tab renders | no excluded list shows |
| S-5.4 | N3 | an unmapped reason | the tab renders | the shared mapping's fallback ("Excluded: " and the raw reason) shows |
| S-5.5 | N7 | the same run | both tabs render | the Correlations and Retirement tabs show the same sentence for the same symphony |

Negative coverage: N1 S-5.2 | N2 n/a: display only | N3 S-5.3, S-5.4 | N4 n/a: page behind the global auth hook, unchanged | N5 n/a: server-rendered once per load | N6 n/a: a failed read is AC-8's state | N7 S-5.5 | N8 n/a: no edit state

### AC-6: Cells are filled by signed colour
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

Negative coverage: N1 S-6.2, S-6.3 | N2 n/a: display only | N3 S-6.6 | N4 n/a: page behind the global auth hook, unchanged | N5 n/a: server-rendered once per load | N6 n/a: a failed read is AC-8's state | N7 S-6.4, S-6.7 | N8 n/a: no edit state

### AC-7: Rows and columns carry distinct full names
Rule: When the matrix renders, each row shall carry a number and the symphony's full display name, each column header shall carry the matching number, and no label shall be an ellipsis-truncated duplicate.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | live symphonies "Harbor Lights Momentum", "Harbor Lights Momentum v2" and "Harbor Lights Momentum v3" | the tab renders | each row shows its number and full name, each column header shows the matching number, the three columns differ from each other, and each label exposes its full name |
| S-7.2 | N1 | two live symphonies with the identical display name | the tab renders | their labels still differ |
| S-7.3 | N1 | a name with an HTML bold tag and an ampersand | the tab renders | the characters show literally |
| S-7.4 | N3 | a symphony in the run that is no longer in the live state | the tab renders | its labels show the Composer hash |
| S-7.5 | N7 | the cells | labels change | each cell still maps to the same pair |

Negative coverage: N1 S-7.2, S-7.3 | N2 n/a: display only | N3 S-7.4 | N4 n/a: page behind the global auth hook, unchanged | N5 n/a: server-rendered once per load | N6 n/a: names come from the live state already read for the page | N7 S-7.5 | N8 n/a: no edit state

### AC-8: No run, a failed run and too few symphonies each have an honest state
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

Negative coverage: N1 n/a: no operator input | N2 n/a: display only | N3 S-8.1, S-8.2, S-8.3, S-8.4, S-8.7 | N4 n/a: page behind the global auth hook, unchanged | N5 n/a: server-rendered once per load | N6 n/a: a failed read is covered by S-1.8 | N7 S-8.6 | N8 n/a: no edit state

### AC-9: Every realized-series date is the calendar date Composer stamps
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

Negative coverage: N1 S-9.2, S-9.3, S-9.4, S-9.5, S-9.11, S-9.12 | N2 n/a: parsing refuses nothing new; malformed bodies keep their existing errors | N3 S-9.8 | N4 n/a: the client is called only by the recommender, off any request path | N5 n/a: parsing the same body twice gives the same labels | N6 S-9.10 | N7 S-9.6, S-9.9 | N8 n/a: no edit state

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

## Narrowed mappings

R-3: The Correlations view reads as a colour-coded matrix (heatmap), not a list of numbers; maps to AC-6; left out: the combined Correlations and Retirement layout (Claude Design B2) is left out
R-5: Every page and tab looks right; maps to AC-6, AC-7; left out: this plan's part is the Correlations tab
R-10: Every page and tab renders honestly; maps to AC-1, AC-2, AC-3, AC-4; left out: the Correlations tab
R-11: The overhaul covers every page, the modal, the Advisor tabs and the header; maps to AC-1; left out: the Correlations tab
R-50: Exclusion reasons render as sentences; maps to AC-5; left out: the Correlations part; the Retirement tab's part is plan 02's

## User tasks

Workflow (AI Advisor Correlations tab): no workflow file: this repository has no `.claude/workflows/` directory at the ref.

### T-1: Read which symphonies move together
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Open `/ai-advisor` and click Correlations | a successful retirement run | A filled matrix with the basis label and an as-of in ET | S-1.1, S-2.1, S-6.1 |
| 2 | Hover or focus a cell | a pair with 120 aligned days | The cell's aligned days and date window show, and it carries the marker for pairs below the stress gate's minimum | S-3.1, S-4.2 |
| 3 | Read the legend and the excluded list | one excluded symphony | The legend's markers and bands match the cells; the excluded symphony is named with a sentence | S-4.1, S-5.1 |

## Open questions

| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | Does the matrix use the latest successful retirement run's series, or fetch its own copy from Composer? | AC-1 | The run's series: one fetch per night, no Composer call at render, and the two tabs describe the same data | default approved by the PM, [PM-ASSUMED], operator may override |
| Q-2 | Should the high band start at the recommender's screen threshold (0.65) instead of the old 0.70? | AC-6 | Yes, so a danger cell is a pair the recommender screens | default approved by the PM, [PM-ASSUMED], operator may override |
| Q-3 | removed: decided by the PM (PM-5): numbered column headers with full names on rows | AC-7 | none | none needed: removed |
