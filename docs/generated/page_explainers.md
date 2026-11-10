# Page explainers and denser layouts

> Descriptions, plain definitions and denser layouts on the dashboard, History, Performance, Settings (page and per-symphony modal) and AI Advisor pages, plus the server resolvers that print configured values into that copy.

**Source:** `app.py`, `templates/index.html`, `templates/performance.html`, `templates/history.html`, `templates/settings.html`, `templates/ai_advisor.html`, `templates/_chrome.html`, `static/layout.css`, `static/settings.js`, `static/settings-modal.js`
**Last updated:** 2026-11-10

## Overview

The slice is copy and layout only. No route, JSON body, figure, control, order path or honesty text changed, and `alpha_bot_execution.py`, `math_engine.py`, `database.py` and the migrations carry no diff against the production base. Every section heading has a visible description under it, each jargon term is defined where it first appears on a page, and panels that left large empty regions share rows or lay their content out in columns.

The exact expected text of every description, definition and relabel is held in `tests/fixtures/page_explainers/copy.json`, extracted from the feature plan's copy tables by `tests/fixtures/page_explainers/extract_copy.py`. The tests read that fixture; no test types a row's text. See `tests/fixtures/page_explainers/PROVENANCE.md`.

## Markup convention

- A description is a `<p>` or `<div>` in the host page's existing note class and carries `data-explainer="<key>"`, directly under its heading.
- A closed `<details class="hp-more" data-terms="<key of the description it follows>">` with the summary "What the terms mean" holds the definitions as `<p data-term="<key>">`, one per term, with no `open` attribute. Settings builds the same markup in script (`ensureSymphonyTerms()` in `static/settings.js`, `_termsDisclosure()` in `static/settings-modal.js`).
- The `.hp-more` rules live in `static/layout.css`, which every page links; History's own copy of them moved there. A definition is never carried by a tooltip alone.
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
| `stress_fraction_pct` | `STRESS_WINDOW_FRACTION` times 100, one decimal, a percentage of aligned days. |
| `watch_trail_pp` | `advisors.tenure_review.BACKTEST_TRAIL_WATCH_PP`, percentage points. |
| `friction_pct` | `autotuner.SIM_EXIT_FRICTION_PCT`, percent per exit. |
| `min_history_days` | `_PERFORMANCE_MIN_HISTORY_DAYS`, the constant the insufficient-history rule uses. |
| `shadow_retention_days` | `SHADOW_HISTORY_RETENTION_DAYS` from the settings file only, default 180, read as the nightly prune reads it. `None` when the stored value is not a whole number. |

The nightly prune keeps its own inline read of the retention value; this resolver does not feed it.

The resolvers are `_copy_tenure_threshold`, `_copy_screen_threshold`, `_copy_stress_fraction_pct`, `_copy_watch_trail_pp`, `_copy_friction_pct`, `_copy_min_history_days`, `_copy_shadow_retention_days`, plus the helper `_whole_number_or_none`.

### Call sites
- `_render_dashboard`: `copy_values` with `shadow_retention_days`, `friction_pct`.
- `performance_page`: `copy_values` with `friction_pct`, `min_history_days`.
- `ai_advisor_tab`: `page_copy` with `tenure_threshold`, `screen_threshold`, `stress_fraction_pct`, `watch_trail_pp`; also `advisor_suggestion_model_known` and `advisor_synthesis_model_known` (false when the model choice's `source` is `"unavailable"`), so a tab names a model only when one is known.

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

- AI Advisor: the Retirement tab's recommender and tenure subsections share a row only while each is no taller than `RETIREMENT_PAIR_MAX_HEIGHT_PX = 320` laid out in one column, and always stack at or below `RETIREMENT_PAIR_STACK_MAX_WIDTH_PX = 700` (`refreshRetirementPair`); tenure evidence and last-run tiles lay out in columns; Frontrunner cards use columns that leave no empty cell.
- AI Advisor autotune table (`autotuneTableHtml(rows, withSharedLine)`): a column whose cell text is the same and not empty in every one of two or more rows is stated once in a "Same for every run" line above the rows and left out of each row. The streak column is never lifted, because a row's missing streak must not read as "no streak". `autotuneRunCells` returns the six cells per run as `{open, inner, text}`.
- Frontrunner proposal cards carry "proposal #<id>" in the card title.
- Dashboard, Performance, History and Settings: the CVaR and Managed sleeves panels share one row (a lone panel takes the full width), the hero figures and dollar-saved panel, the Performance risk table and the History exits and reasons tables use their width.

## Settings copy

The page's secret-field status reads "Not saved from here: set it in .env on the host" while a value is typed, because `LIVE_EXECUTION` and credentials are never settings-writable. The per-symphony modal gives each section a description and a closed terms disclosure (execution mode, locked variables, autotuner parameters, advisor).

## Types

No new types. Slot names above are the whole contract between `app.py` and the templates.

## Internal Dependencies

- `app` ([app.md](app.md)): resolvers and render context.
- `advisors.tenure_review`, `advisors.retirement_recommender`, `autotuner`: constants read lazily.
- `model_config`: `peek(...).source` for the model-known flags ([model_config.md](model_config.md)).
- [ux_overhaul.md](ux_overhaul.md): behaviour of each page; this page covers the copy and layout added after it.

## Not confirmed from code

Whether each description reads well at 375 px on a real device and how the pages look in the operator's browsers are covered by the slice's browser tests against a production database copy, not by this page.
