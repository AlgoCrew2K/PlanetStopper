# Feature: UX review fixes for the assembled overhaul
Status: planned
Type: bugfix
Suite: ux-overhaul 06
Repos: PlanetStopper
Base: assembly/all at 7d8db3cd
Sources: `.claude/review/6fdc1fb7a43b/ux-expert.json` and `.claude/review/6fdc1fb7a43b/alpha-audit.md` in worktree `.claude/worktrees/ux-review`.

Findings are numbered UXR-n in `ux-expert.json`; each entry there holds the page, viewport, theme, evidence image and location. This file states the required outcome of each. Frozen: `alpha_bot_execution.py`, `math_engine.py`, `reporting.py`, `migrations/`, `static/tokens.css`. No new colours or token values.

## AC-1 One portfolio guard number (UXR-1, UXR-12)
- The dashboard headline is the compounded gap: Simulated Bot return minus If-held return over the window, over all symphonies, the gap between the hero chart's two lines. It equals Performance's figure for the same window (-0.64 pp at 30d, +1.78 at 60d, +2.75 at YTD on the audit's copy) and is computed from the series `compute_windowed_portfolio_strip` already holds. Crypto stays excluded only from the cumulative row's return, for the reason that row states.
- Per-symphony cards keep their epoch-additive figures.
- Labels: Dashboard and Performance read "Guard effect on portfolio" with pp as the unit and the window named; descriptor as in the audit. History's total reads "Sum of exit alpha" in pts with a descriptor stating 15:54 ET, unweighted, not a portfolio return; its strip reads "Daily sum of exit alpha (pts, unweighted)"; by-reason cards use the same words.
- The headline and its label change together on a window click and stay together through later polls.

## AC-2 Dashboard figures read one way (UXR-2, UXR-3, UXR-13, UXR-14, UXR-29, UXR-30)
- Max DD prints the same text at first paint and after a refresh, with no leading plus on a magnitude.
- A card with no Bot figure shows the dash for its alpha at first paint and after a refresh.
- The Max DD row follows the picked window, matching Performance's Max DD for that window. If a window cannot supply it, the row says so; it never shows another window's figure under this label.
- ANN. VOL shows the picked window's bot and if-held volatility, matching Performance; with no value the row is hidden rather than all dashes.
- The section header's lock count badge is labelled ("12 locked"), not a bare number.
- The hero chart shows a tooltip on hover like Performance's; window buttons show a hover state.

## AC-3 Opening a card always fills the detail panel (UXR-4)
Clicking a card before the first state arrives opens the panel showing a loading state, and it fills itself when the state arrives, with the symphony's real title. It never stays on "Symphony Detail" with dashes.

## AC-4 Phone width (UXR-7, UXR-8, UXR-9, UXR-10, UXR-31)
At 375 and 320: card stat grids have no colliding text; the window picker wraps as a row, not a thin column; the headline sentence and legend read normally; no page scrolls sideways, including `/settings` at 320 and with an observation history open at 375. Advisor tabs are one horizontally scrolling row so content is on the first screen. The heatmap's name column stays visible while scrolling its columns.

## AC-5 Settings modal shows what it holds (UXR-5, UXR-6)
Autotuner parameters and AI Advisor sections show their values at both widths, in both themes, with the body scrolling. Lock controls show each variable's full name, wrapping as needed, so no two read alike.

## AC-6 Words on screen (UXR-11, UXR-16, UXR-17, UXR-20, UXR-24, UXR-25, UXR-26, UXR-28)
- Uppercased labels never turn alpha or rho into A or P: write the word ALPHA, or keep the glyph lowercase with `text-transform: none` on it, in one helper.
- Raw enums (`REJECT_VETO_FAILED`, `autotune_run`, lowercase `neutral`) read as words through one mapping.
- The observations note is caption size.
- Max Drawdown is a positive magnitude everywhere on Advisor cards and tenure evidence.
- Correlations copy never says "No realized series yet" when the run analysed series; it states what is true.
- The Overview's Retirement pointer reads as a link to the tab.
- Native buttons and selects use the app font.
- A Sortino row with no value is omitted from a Strategy Builder card.

## AC-7 Advisor layout (UXR-15, UXR-18, UXR-19, UXR-21, UXR-23, UXR-22, UXR-27)
- The Overview has no column of blank beside the autotune cards: when the suggestions column holds only its empty state, the autotune block takes the width. FALLBACK N/A repeats collapse per the plan's limit.
- Discuss opens the Chat tab with its context loaded and the chat in view, on every card.
- Frontrunner proposal cards separate identity, the Approve explainer and the overlay into labelled lines, with the raw overlay under its preview, not in the paragraph.
- Retirement: two labelled run buttons ("Run recommender", "Run tenure review") in their own sections; the actionable card comes first; the basis line says Composer realized daily returns with no internal word.
- Heatmap: colour stays readable under a subtle thin marker on low-depth cells; each cell's depth is visible without hover at desktop width; each symphony's first and last series dates are on the tab.
- Performance: a symphony with no replay sample has no Replay row; one note names how many lack a sample.
- Performance risk table: lower-is-better rows use one delta convention and one unit, stated in the header; no meaningless if-held column on reduction rows.

## Testing
Each assertable item has a failing test first, on the real render or the node harness. Layout-only items are judged by re-capture at the next UX gate; name them in the handoff.
