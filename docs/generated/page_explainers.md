# Page explainers and denser layouts

> Descriptions, one Glossary per page, aligned column tables and quiet status copy on the dashboard, History, Performance, Settings (page and per-symphony modal) and AI Advisor pages, plus the server resolvers that print configured values into that copy.

**Source:** `app.py`, `templates/index.html`, `templates/performance.html`, `templates/history.html`, `templates/settings.html`, `templates/ai_advisor.html`, `templates/_chrome.html`, `templates/_glossary.html`, `static/glossary.js`, `static/layout.css`, `static/settings.js`, `static/settings-modal.js`
**Last updated:** 2026-11-13

## Overview

The slice is copy and layout only. No route, JSON body, figure, control, order path or honesty text changed, and `alpha_bot_execution.py`, `math_engine.py`, `database.py` and the migrations carry no diff against the production base. Every section heading has one short visible description under it and each page, Advisor tab, detail panel and settings modal ends in one closed Glossary that holds every definition. Tables share one header row and one set of columns (see Aligned columns), and a status line states a fact once. The per-section "What the terms mean" disclosures of the earlier slice are gone; the Glossary replaced them.

The exact expected text of every description, definition and relabel is held in `tests/fixtures/page_explainers/copy.json`, extracted from the feature plan's copy tables by `tests/fixtures/page_explainers/extract_copy.py`. The tests read that fixture; no test types a row's text. See `tests/fixtures/page_explainers/PROVENANCE.md`.

## Markup convention

- A description is a `<p>` or `<div>` in the host page's existing note class and carries `data-explainer="<key>"`, directly under its heading.
- Definitions live in one closed Glossary per host, written by the `glossary(host)` macro in `templates/_glossary.html`: `<details class="hp-more" data-terms="glossary" data-testid="glossary" data-glossary-host="<host>">` with the summary "Glossary" and one `<p data-term="<key>">` per term, as the last element of the host, with no `open` attribute. The hosts are the dashboard, its detail panel, History, Performance, Settings, the settings modal and each of the eight Advisor tabs. Settings builds the modal's Glossary in script (`_glossary()` in `static/settings-modal.js`); `ensureSymphonyTerms()` and `_termsDisclosure()` no longer exist. No host carries a "What the terms mean" or "What the exit reasons mean" disclosure.
- A label that names a term shows the term's Glossary line as its `title`: `static/glossary.js` (`window.PSGlossary.apply`) writes the `title` attribute only, never markup. A table header, a figure or tile label, a tenure evidence heading or an element with `data-glossary="<key>"` matches its host's nearest Glossary by the line's lead term (the text before the first ": ") or by key. An element that already carries its own `title` keeps it. The script rescans on DOM changes (one `requestAnimationFrame` per burst), so labels the page scripts write get titles too.
- The `.hp-more` rules live in `static/layout.css`, which every page links. A definition is never carried by a tooltip alone: the title repeats a line the Glossary holds.
- No description or disclosure sits inside a container a script rewrites with `innerHTML`; it sits between the heading and that container.
- Header controls carry tooltips and an `aria-description` (`templates/_chrome.html`; `ENGINE_STATE_DESCRIPTION` and `MODE_INDICATORS[...].title` in `static/chrome.js` keep a poll's rewrite equal to the server's first paint).
- The AI Advisor tab strip shows a cue at each edge while a tab lies past that edge (`updateTabCues` in `static/ai_advisor.js`).

## API Reference

### `_plain_number(value, *, decimals) → str | None`
Rounds `value` to `decimals` places, strips trailing zeros and a bare point. `None` when `value` is not a finite real number; a `bool` is not a number; an `int` is returned as its own text.

### `_page_copy_values(slots, *, tenure_review=None, retirement_last_run=None) → dict`
Returns a dict from slot name to a display string or `None`. Each slot is resolved in its own `try`, so one failing authority yields `None` for that slot only. A template never prints a slot alone: each phrase that holds a slot is written in two forms, one for a string and one fallback phrase for `None`.

| Slot | Authority |
|------|-----------|
| `tenure_threshold` | `tenure_review["threshold_in_effect"]` when it is a whole number, else the configured value parsed by `advisors.tenure_review.parse_threshold` (trading days). Never `None`. |
| `screen_threshold` | `screen_threshold` of an ok `retirement_last_run`, else `advisors.retirement_recommender.CORRELATION_SCREEN_THRESHOLD`. |
| `redundancy_needs_days` | `_copy_redundancy_needs_days`: the larger of `advisors.tenure_review._structural_needs_days()` (the fewest aligned days the review's structural gate can pass) and `advisors.retirement_recommender.MIN_OBS_FLOOR`, as a whole-day text. `None` when the search finds no such day count. The count is derived, never typed in a template. |
| `stress_fraction_pct` | `STRESS_WINDOW_FRACTION` times 100, one decimal, a percentage of aligned days. |
| `watch_trail_pp` | `advisors.tenure_review.BACKTEST_TRAIL_WATCH_PP`, percentage points. |
| `friction_pct` | `autotuner.SIM_EXIT_FRICTION_PCT`, percent per exit. |
| `min_history_days` | `_PERFORMANCE_MIN_HISTORY_DAYS`, the constant the insufficient-history rule uses. |
| `shadow_retention_days` | `SHADOW_HISTORY_RETENTION_DAYS` from the settings file only, default 180, read as the nightly prune reads it. `None` when the stored value is not a whole number. |

The nightly prune keeps its own inline read of the retention value; this resolver does not feed it.

The resolvers are `_copy_tenure_threshold`, `_copy_screen_threshold`, `_copy_redundancy_needs_days`, `_copy_stress_fraction_pct`, `_copy_watch_trail_pp`, `_copy_friction_pct`, `_copy_min_history_days`, `_copy_shadow_retention_days`, plus the helper `_whole_number_or_none`.

### Call sites
- `_render_dashboard`: `copy_values` with `shadow_retention_days`, `friction_pct`.
- `performance_page`: `copy_values` with `friction_pct`, `min_history_days`.
- `ai_advisor_tab`: `page_copy` with `tenure_threshold`, `screen_threshold`, `redundancy_needs_days`, `stress_fraction_pct`, `watch_trail_pp`; also `advisor_suggestion_model_known` and `advisor_synthesis_model_known` (false when the model choice's `source` is `"unavailable"`), so a tab names a model only when one is known.

## Wording changes with a server and a script copy

The server paints first and the script rewrites on a poll, so each of these has one copy in each builder and the two must stay equal:

- Card verdict (`app._first_paint_verdict`, `verdictText` in `static/index.js`): "Exit beat holding by {m} pts", "Exit trailed holding by {m} pts", and "Exit matched holding" when the magnitude rounds to `0.0`. The magnitude is the guard alpha difference in percentage points, one decimal (`analytics.js_to_fixed` mirrors `toFixed`). It replaces "Good call · saved +{m}%α" and "Early exit · gave up {m}%α".
- Retirement last-run tiles (`_build_retirement_summary_tiles`): the four gate labels read "too uncertain", "too few worst days", "apart on worst days", "no score to compare"; the thin-window detail reads "The worst-days check needs at least {N} days on which both symphonies have a return." The count and key names are unchanged.
- Strategy Builder degraded-run notice (`ai_advisor_strategy_builder_run`): names a model only when the run's provenance names one and the suggestion knob's choice is known; otherwise it reads "Built-new generation produced 0 plans". The provenance line of the Strategy Builder, Asset Swaps and Logic Changes scripts reads "Model: not known" when no model is named (was "model unavailable").
- History reason names (`REASON_DISPLAY` in `static/history.js`): the stored and API keys stay `Take-Profit`, `Trailing Stop`, `VWAP Breakdown`, `VWAP Bleed Cut`; the shown names are "Take-profit", "Trailing stop", "VWAP breakdown", "VWAP bleed", and the count column reads "Exits". The Dashboard timeline's VWAP exit chip reads "VWAP breakdown".
- Performance (`static/performance.js`): the chart series read "If held" and "Bot (shadow)"; the `win_rate` row reads "Up days"; the exit-turnover cells no longer set an inline right alignment.
- Dashboard hero legend while the market is open reads "If held · live".

## Layout changes

Layout changes are in the templates and `static/layout.css`; none changes a figure.

- AI Advisor: the Retirement tab's recommender and tenure subsections stack in one column at every width (the measured side-by-side pairing, `refreshRetirementPair` and its two constants, is removed); tenure evidence cells keep one visible order and one set of columns (see Aligned columns); Frontrunner cards use columns that leave no empty cell.
- AI Advisor autotune table (`autotuneTableHtml(rows, withSentence)`): a run time or an outcome that is the same and not empty in every one of two or more rows is stated once in a plain sentence above the rows (`autotuneSentence`, clauses from `AUTOTUNE_OUTCOME_CLAUSES`, for example "Latest run <time>: every symphony kept its previous settings.") and left out of each row; no other column is ever lifted, so a missing streak never reads as "no streak". A score or Sel t-stat column that reads N/A in every row of a table is left out of that table with its header. `autotuneRunCells` returns the six cells per run as `{open, inner, text, absent}`; `autotuneRunRowHtml(cells, omitted)` leaves out an omitted column. The older-runs summary reads "{n} older run(s) from the {limit} most recent".
- Frontrunner proposal cards carry "proposal #<id>" in the card title.
- Dashboard, Performance, History and Settings: the CVaR and Managed sleeves panels share one row (a lone panel takes the full width), the hero figures and dollar-saved panel, the Performance risk table and the History exits and reasons tables use their width.

## Aligned columns and the Glossary (aligned-tables-and-quiet-copy)

Display only: no figure, control, route, JSON body, banner meaning or honesty text changed, and `alpha_bot_execution.py`, `math_engine.py`, `database.py`, `advisors/` and the migrations carry no diff against the slice base.

**Columns primitive (`static/layout.css`).** From 1001 px up, a host sets `--cols` on a `.cols-table` element and each row is a `.cols-row` that takes the table's tracks through `subgrid`, so every column is as wide as its widest cell and a header sits over its cells. `.cols-num` right-aligns a numeric column (a cell that holds a number, percent, signed figure, count or dash). Below 1001 px the primitive is not applied and each table keeps its stacked-row form. The `.cols-table` hosts are the autotune table and the Overview observation lines. The tenure evidence cells use their own six-track grid from 1001 px (Performance, Composite score, Backtest, Marginal contribution, Long VIX hedge, Redundancy, in that visible order on every card). The Performance and History tables size their columns to content from 1001 px in their own page styles, and the result cards use the stacked grid below.

- Overview observation lines: one columns table of five tracks (producer, verdict, time, count, last cell). The Retirement line is a row of the same table, its status written by `_build_retirement_pointer`, which now returns `{"status": ...}` (the text without the leading "Retirement: ") through `_retirement_pointer_result`; it no longer returns `text`. The open observation history spans the section; below 1001 px it is an ordinary table with no status dot hung outside the cell.
- Result cards (Asset Swaps, Logic Changes): the Baseline and Variant blocks sit side by side while both fit. When the page finds them stacked, `syncStackedStatsGrids()` (`static/ai_advisor.js`, also exposed as `window.syncStackedStatsGrids`) marks the grid `stats-grid--stacked` so the two blocks share one label track and one value track. It runs after each result is written, on window resize and when a tab is shown; a grid on a hidden tab is left as it was because it has no layout to measure.
- Header (`templates/_chrome.html`): between 1401 and 1500 px the gaps and padding tighten so the engine label, clock and chip fit and the header never makes a page scroll sideways.
- History: the reason descriptions disclosure and `REASON_DESCRIPTIONS` are removed; the four reason names are Glossary lines.

**Glossary (`templates/_glossary.html`, `static/glossary.js`).** See Markup convention. Settings controls and the Performance metric rows and precondition verdict chips name their line with `data-glossary` keys (`METRIC_GLOSSARY_KEY` and `PRECOND_VERDICT_GLOSSARY_KEY` in `static/performance.js`).

**Status copy.**
- `_build_verification_count_line(summary, verdict=None)`: a count is printed only when it is an integer above zero (a `bool` is not one); the verified segment is left out when the verdict pill already states "N of M verified" or cannot state the verified count (an unreadable count); the check count absent, null or zero gives `""`. The verification disclosure summary reads "Show all checks".
- Logic Changes result card: the apply line shows "To apply:" once; a stored apply guidance that begins with its own "To apply:" has it stripped on render.
- The tenure run line carries one fleet line when at least one card is "too few days": "Redundancy not decided for {k} of {n} symphonies: too few shared trading days with the other symphonies; {N} are needed." k is the number of cards whose stored redundancy reason is the review's list of undecided comparisons (it begins `advisors.tenure_review._UNDECIDED_PREFIX`) in which every listed peer has fewer aligned days than N and none carries a gate suffix; n is the number of tenure cards on the tab; N is the `redundancy_needs_days` slot. The classification is the template filter `tenure_redundancy_missing(reason, needed)` in `app.py`: it returns the card's missing-line phrase, the "no comparison ... reached a result" phrase for any other undecided list, and `None` for a reason that is not the undecided list. The templates read the phrase's "not decided" prefix to count cards, so the filter's wording is also its classification. A card that is too-few-days leaves redundancy out of its own missing line; the stored undecided-peer list is shown once, only inside the Redundancy cell's closed disclosure. When N cannot be read the filter returns the second phrase for every undecided list, so no card is counted and the fleet line does not show (the no-number form is a tracked backlog item).
- A tenure evidence summary headlines the item's own figure; the Composite summary states its value, "0 to 1", the population count and "normalized" once, dropping the stored unit's ", fleet-normalized" qualifier at render.
- The tenure run's not-sent text reads "Click Run tenure review again." (`static/ai_advisor.js`).

**Not confirmed from code.** The header at real font widths (the header test substitutes a wide font by ruling) and how each page reads at 1920, 1440 and 390 px in the operator's browser are the PM's deploy-gate visual check, not this page.

## Settings copy

The page's secret-field status reads "Not saved from here: set it in .env on the host" while a value is typed, because `LIVE_EXECUTION` and credentials are never settings-writable. The per-symphony modal gives each section a description and ends in one closed Glossary (`_glossary()`); the lock and override labels on the Settings page carry their Glossary line as a title (`data-glossary="S15"`).

## Types

No new types. Slot names above are the whole contract between `app.py` and the templates.

## Internal Dependencies

- `app` ([app.md](app.md)): resolvers and render context.
- `advisors.tenure_review`, `advisors.retirement_recommender`, `autotuner`: constants read lazily.
- `model_config`: `peek(...).source` for the model-known flags ([model_config.md](model_config.md)).
- [ux_overhaul.md](ux_overhaul.md): behaviour of each page; this page covers the copy and layout added after it.

## Not confirmed from code

Whether each description reads well at 375 px on a real device and how the pages look in the operator's browsers are covered by the slice's browser tests against a production database copy, not by this page.
