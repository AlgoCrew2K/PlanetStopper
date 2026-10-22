# Approval packet: Data and label truth across History, Performance, Settings and the AI Advisor

## What is being built

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

## Acceptance criteria and scenarios

### AC-1: Autotune run cards label their numbers truly
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

Negative coverage: N1 S-1.5 | N2 n/a: the card is read-only and nothing is refused | N3 S-1.2, S-1.3, S-1.4, S-1.6, S-1.11 | N4 n/a: the route is behind the global auth hook, unchanged, and the card has no write | N5 S-1.7 | N6 S-1.8 | N7 S-1.9 | N8 n/a: the card has no edit state

### AC-2: History's dollar total says saved or lost
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

Negative coverage: N1 S-2.2, S-2.3 | N2 n/a: display only | N3 S-2.4, S-2.6 | N4 n/a: the route is behind the global auth hook, unchanged | N5 S-2.7 | N6 S-2.8 | N7 S-2.5 | N8 n/a: no edit state on the page

### AC-3: Performance numbers carry one sign, Max DD a magnitude, and no dead rows
Rule: When the Performance page renders its risk table, preconditions panel and exit-turnover panel, each delta shall carry exactly one sign character, the drawdown row shall be labelled "Max DD" and show each leg as a positive magnitude, friction drag shall show two decimals, no row shall render for a metric that has no producer, and a cold replay sample shall be reported once at panel level.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | live and shadow `max_drawdown` whose reduction is positive | the risk table renders | the delta cell starts with "\u2191 +" and contains exactly one "+" character |
| S-3.2 | N1 | a delta of exactly 0 | the risk table renders | the cell contains no arrow and at most one sign character |
| S-3.3 | N1 | a negative delta | the risk table renders | the cell starts with "\u2193 " and contains exactly one "-" and no "+" |
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

Negative coverage: N1 S-3.2, S-3.3, S-3.6 | N2 n/a: display only | N3 S-3.5, S-3.9, S-3.10, S-3.11, S-3.15 | N4 n/a: the routes are behind the global auth hook, unchanged | N5 S-3.13 | N6 S-3.14 | N7 S-3.12 | N8 n/a: no edit state on the page

### AC-4: Window buttons mean the dashboard's calendar windows, with a default that can be met
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

Negative coverage: N1 S-4.6, S-4.7, S-4.13, S-4.15 | N2 S-4.10 | N3 S-4.8 | N4 n/a: the routes are behind the global auth hook, unchanged | N5 S-4.11 | N6 S-4.12 | N7 S-4.9, S-4.14 | N8 n/a: a window click has no pending edit

### AC-5: Every symphony label shows the display name
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

Negative coverage: N1 S-5.10, S-5.11 | N2 n/a: labels only, nothing is refused | N3 S-5.8, S-5.9 | N4 n/a: every route is behind the global auth hook, unchanged | N5 S-5.14 | N6 S-5.12 | N7 S-5.13 | N8 n/a: a label has no edit state; the modal's pending edits are untouched by the title change and covered by S-5.13

### AC-6: The History day drilldown names each symphony in time order
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

Negative coverage: N1 S-6.4, S-6.8 | N2 n/a: display only | N3 S-6.2, S-6.3, S-6.5 | N4 n/a: the route is behind the global auth hook, unchanged | N5 S-6.7 | N6 n/a: the drilldown reads the already-fetched payload, with no request of its own | N7 S-6.6 | N8 n/a: no edit state

### AC-7: Banners, notes and Strategy Builder cards say true things once
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

Negative coverage: N1 S-7.5 | N2 n/a: copy only | N3 S-7.6, S-7.7, S-7.10 | N4 n/a: the page is behind the global auth hook, unchanged | N5 n/a: static server-rendered copy, the same on every load | N6 n/a: the copy depends on no request | N7 S-7.8, S-7.9 | N8 n/a: no edit state

### AC-8: The Settings controls do what they show
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

Negative coverage: N1 S-8.2, S-8.13, S-8.14 | N2 S-8.6 | N3 n/a: the reset and the toggle read only the loaded settings, with no computed value | N4 n/a: the page and the POST are behind the global auth and CSRF hooks, unchanged | N5 S-8.7 | N6 S-8.10 | N7 S-8.8, S-8.15 | N8 S-8.3, S-8.5, S-8.9

### AC-9: An override equal to the global is not counted
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

Negative coverage: N1 S-9.2, S-9.3, S-9.6 | N2 n/a: display only | N3 S-9.5 | N4 n/a: the page is behind the global auth hook, unchanged | N5 n/a: counting is a pure function of the loaded data | N6 n/a: counting runs only after a successful load, covered by S-8.10 | N7 S-9.9 | N8 S-9.8

### AC-10: The verification chip is never a green "clean" over unverifiable checks
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

Negative coverage: N1 S-10.8 | N2 n/a: display only | N3 S-10.3, S-10.4, S-10.5, S-10.6, S-10.9 | N4 n/a: the page is behind the global auth hook, unchanged | N5 n/a: the pill is server-rendered once per page load | N6 n/a: a failed verification fetch already renders no block, covered by S-10.6 | N7 S-10.7 | N8 n/a: no edit state

## Rule-by-entry-point matrix

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

## Narrowed mappings

R-5: Every page and tab looks right; maps to AC-1, AC-2, AC-3, AC-5, AC-7; left out: this plan's part is numbers, labels and copy; layout and primitives are plan 04's, new layouts are Claude Design's
R-6: Every control on every page functions; maps to AC-4, AC-8; left out: this plan's part is the window buttons and the Settings controls (Reset to globals, the live toggle, Save); other controls are plans 02, 04 and 05
R-10: Every page and tab renders honestly; maps to AC-1, AC-2, AC-3, AC-5, AC-6, AC-7, AC-10; left out: this plan's surfaces only
R-11: The overhaul covers every page, the modal, the Advisor tabs and the header; maps to AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9, AC-10; left out: this plan touches History, Performance, Settings, the settings modal, and the Advisor Overview, Correlations, Strategy Builder and Frontrunner Builder tabs
R-22: The autotune card's value now labelled "Sortino" carries a label that names what it is; maps to AC-1; left out: G-17 says the value is a Sharpe ratio; the code shows it is the optimizer's best objective score, so it is not labelled Sharpe (Q-8)
R-39: Internal lowercase keys are replaced by display names; maps to AC-5; left out: the dashboard detail-panel title is plan 05's
R-40: Performance shows no all-dash metric rows, one panel note for unavailable replay samples, and two-decimal friction drag; maps to AC-3; left out: a computed metric with no value for the chosen window keeps its row and dash, because the row says the metric exists and lacks data; only rows with no producer at all are removed

## User tasks

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

## Open questions

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

Sensitive: money (AC-8). qa writes hidden scenarios for the named criteria after approval; they are not shown to the builders.
