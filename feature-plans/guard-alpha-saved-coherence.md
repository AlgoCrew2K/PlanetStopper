# Feature: Guard-Alpha $-Saved Display Coherence — Sign Convention + Windowing
Status: ready
Created: 2026-07-29
Revised: 2026-07-29 (reconciled to the full operator-locked contract + gas-test's committed RED tests — see Revision note)

## Revision note (why this doc changed shape)
The first draft of this plan (commit `a2ab0805`) scoped the fix to the History tab only —
narrower than the operator-locked contract. gas-review correctly held the gate pending
reconciliation. This revision is built from two sources of truth, not a re-guess: (1) the
team lead's explicit ruling on the missing surfaces (dashboard panel windowing + both
index.js headlines + a shared formatter), and (2) a direct read of gas-test's own committed/
staged RED tests (`tests/app/test_guard_alpha_summary_windowed.py`,
`tests/analytics/test_format_dollar_saved.py`,
`tests/app/test_dollar_saved_panel_sign_coherence.py`, plus additions to
`tests/app/test_sleeves_digest_extension.py`, `tests/ui/test_cycle_5_history.py`, and
`tests/reporting/test_reporting.py`) — which pin THREE surfaces beyond what either the
original draft or the team lead's punch-list named: the History "Recent triggers" Detail
column color, the Discord EOD "Total Saved" embed line, and the QuickChart "Daily Saved ($)"
bar coloring. This doc is the lagging artifact; it now matches the RED tests, not the other
way around. All ACs below are cross-referenced to the actual test file/class that pins them.

## Summary
Every dollar figure and several color/window surfaces tied to guard-alpha "$-saved" violate
the operator-locked convention: **render the ABS magnitude with NO sign character; color +
a WORD (not a `+`/`-`) convey direction — the positive word ("saved" by default) for
`value >= 0`, the negative word ("lost" by default) for `value < 0`.** Separately, the
dashboard's $-saved panel is ALWAYS all-time while the History tab's $-saved is calendar-
windowed — for a given nominal window ("1Y") the two surfaces can show two unrelated,
unreconcilable numbers, and neither surface discloses which window backs its own number
without reading source. Nine concrete defects, four files, one new Python helper, one new
shared JS helper, one new route parameter:

1. **`static/history.js` `renderHero()`** hardcodes `val-total-saved` to green
   (`--studio-pos`) unconditionally, and its VALUE string carries a naked leading `-` for a
   negative `total_saved` — no sign-based color, no abs magnitude.
2. **`static/history.js` `renderReasonCards()`** — the by-reason dollar caption is uncolored
   regardless of sign, its string-building lacks abs-magnitude handling (a negative value
   renders the malformed `"$-500.00"`), and its trailing word is the unconditional literal
   "saved."
3. **`static/history.js` `renderTriggers()`** — the "Recent triggers" table's Detail column
   (`rec.detail`, a signed guard-alpha %) is hardcoded `--studio-ink-dim` (neutral gray)
   regardless of sign, inconsistent with every other alpha/dollar figure in the same file.
4. **`templates/history.html`'s window picker** — "1Y"/"5Y" buttons send literal calendar-day
   counts `252`/`1260` (a 252-trading-days/year artifact), while the app-wide `1y` token
   (`analytics._WINDOW_TRAILING_DAYS["1y"]==365`) means 365 calendar days — "1 year" means
   two different spans depending which surface you're on.
5. **`static/index.js` `fetchGuardAlphaSummary()`** (both the snapshot headline AND the
   realized-basis sibling) prepends a literal `-$`/`$` sign prefix, paired with a STATIC
   caption ("saved"/"realized") that never changes — a losing window renders
   `"-$50.00 saved across 3 exits"`, the exact naked-minus-under-a-saved-label pattern the
   ruling forbids.
6. **`GET /api/guard-alpha-summary`** has no window parameter at all — it is always the
   all-time cumulative sum, while History's own $-saved is calendar-windowed from the SAME
   `saved_dollars` field in the SAME `post_mortem_*.json` files — two numbers, no shared
   token, no way to reconcile them.
7. **No shared dollar-formatting helper exists anywhere** — every surface (dashboard panel,
   History hero/by-reason, the Managed Sleeves EOD digest, the Discord EOD embed) does its
   own ad-hoc sign handling, which is why the SAME bug (naked sign character under an
   unconditional word) is independently present in at least five places.
8. **`reporting.py`'s Discord EOD "Total Saved" line** (`f"...${ws['total_saved']:+,.2f}"`)
   forces a literal `+`/`-` sign character under the unconditional label "Total Saved:".
9. **`reporting.py`'s QuickChart "Daily Saved ($)" bar dataset** hardcodes a single amber
   `backgroundColor` string for every bar regardless of that day's sign — a losing day's bar
   is visually identical to a winning day's bar.

Zero touch to `alpha_bot_execution.py`/`math_engine.py`, zero touch to any trade/execution
path, zero change to the actual `saved_dollars` VALUE computation (`reporting.py:91-94`) —
this is a pure display/reporting-layer coherence fix. Percentage-figure surfaces (Avg Guard
Alpha, total_alpha, alphaColor's own %) keep their existing `+`/`-` numeric convention
unchanged — the no-naked-sign rule applies to DOLLAR figures only (team-lead ruling, item 3).

## Acceptance Criteria
Each AC cites the RED test file/class that pins it (all either committed on this branch or
staged in the shared worktree as of this revision).

- [ ] **AC-1 — shared Python formatter.** `analytics.format_dollar_saved(value: float, *,
      positive_word="saved", negative_word="lost") -> str` renders the ABS magnitude with a
      thousands separator and NO sign character ever (not even a redundant `+` for a
      positive value); `value >= 0` (including exactly `0.0`) uses `positive_word`, `value <
      0` uses `negative_word`; both words are keyword-only (a positional third argument
      raises `TypeError`).
      *Pinned by:* `tests/analytics/test_format_dollar_saved.py` (all classes).
- [ ] **AC-2 — Python-side callers route through AC-1.** (a) The Managed Sleeves EOD digest's
      `realized_pnl_usd` line (`reporting.build_sleeves_digest_section`, ~`reporting.py:253-254`)
      uses `format_dollar_saved(value, positive_word="gain", negative_word="loss")`; the
      pre-existing None/non-numeric → `"n/a"` degrade path is preserved unchanged (the helper
      is only reached for a genuine numeric value). (b) The Discord EOD "Total Saved" embed
      line (~`reporting.py:581`) uses `format_dollar_saved(value)` (default saved/lost words).
      (c) Blast-radius: grep the whole of `reporting.py` for any OTHER `:+,.2f`/`:+.2f`-style
      forced-sign format spec applied to a saved/lost-semantic dollar figure before calling
      this AC complete — do not assume only the two known sites exist.
      *Pinned by:* `tests/app/test_sleeves_digest_extension.py::TestBuildSleevesDigestSectionRealizedPnlSignCoherence`;
      `tests/reporting/test_reporting.py::TestDiscordTotalSavedSignCoherence`.
- [ ] **AC-3 — QuickChart bar coloring by sign.** The "Daily Saved ($)" dataset's
      `backgroundColor` becomes a per-index list (Chart.js-supported), the same length as
      `data`, colored by each day's OWN sign — differing-sign days get differing colors,
      same-sign days (even non-adjacent ones) share the identical color.
      *Pinned by:* `tests/reporting/test_reporting.py::TestQuickChartDailySavedBarColorBySign`.
- [ ] **AC-4 — windowed `GET /api/guard-alpha-summary`.** The route gains an optional
      `?window=<token>` param using the SAME token vocabulary the hero-strip picker already
      emits and `/api/strip/<window>` already accepts (`{"30d","60d","90d","125d","ytd","1y","all"}`),
      resolved via the SAME `analytics._window_cutoff_date` the strip route already uses —
      never a new cutoff scheme. Response gains `window` (echoes the resolved token, mirrors
      `/api/strip/<window>`'s own echo pattern) and `date_range` that brackets ONLY the
      in-window files' own dates (not the cutoff bound, not the all-time earliest/latest).
      Omitting `window` preserves today's all-time default byte-for-byte (regression guard for
      existing callers — the Discord aggregation context and
      `tests/app/test_guard_alpha_summary_route.py`). An unrecognized token degrades to the
      lifetime/all-time sum with a 200, never a 404/500 (read-only advisory route).
      *Pinned by:* `tests/app/test_guard_alpha_summary_windowed.py`
      (`TestWindowedSumMatchesFixture`, `TestWindowedDateRangeDisclosure`).
- [ ] **AC-5 — byte-parity proof (the literal "same range → same number").** For any shared
      day-count token, the windowed `guard_alpha_summary` sum equals
      `analytics.get_history_summary(days=N)["total_saved"]` computed over the IDENTICAL
      post-mortem files, to the cent — including at `1y`, which BOTH sides must resolve to
      365 days (never History's legacy 252) for the proof to hold.
      *Pinned by:* `tests/app/test_guard_alpha_summary_windowed.py::TestByteParityWithHistorySummary`.
- [ ] **AC-6 — History's own window-picker values corrected.** `templates/history.html`'s
      "1Y" button carries `data-window="365"` (was `"252"`) and "5Y" carries
      `data-window="1825"` (5×365, was `"1260"`); the legacy `252`/`1260` values are removed
      from BOTH the template and `static/history.js` (no residual literal anywhere) — this is
      what makes AC-5's byte-parity hold at the 1-year token by construction, and makes
      picking "1Y" anywhere in the app span the identical calendar range.
      *Pinned by:* `tests/app/test_guard_alpha_summary_windowed.py::TestHistoryWindowPickerAppWideDayCounts`.
- [ ] **AC-7 — History hero total-saved: sign color + abs magnitude.**
      `static/history.js`'s `renderHero()` colors `val-total-saved` `--studio-pos` when
      `total_saved >= 0`, `--studio-neg` when `< 0` (supersedes the prior "always green"
      test/behavior) — same idiom as `val-total-alpha` directly above it in the same
      function. The formatted VALUE applies `Math.abs()` so a negative figure never leaks a
      naked minus. (No dedicated verb element required here — the History hero's caption is
      a static stat LABEL, not a sentence-shaped clause; color + abs magnitude is the full
      contract per the RED tests below.)
      *Pinned by:* `tests/ui/test_cycle_5_history.py::test_history_js_render_hero_total_saved_colored_by_sign`,
      `::test_history_js_render_hero_total_saved_value_has_no_naked_minus`.
- [ ] **AC-8 — History by-reason card: abs magnitude + sign-conditional word.**
      `renderReasonCards()`'s dollar caption applies `Math.abs(s.dollars)` to the magnitude
      and swaps its trailing word "saved"/"lost" by sign, with the word living in the SAME
      render block as the dollars figure (not merely present elsewhere in the function).
      *Pinned by:* `tests/ui/test_cycle_5_history.py::test_history_js_reason_card_dollars_uses_abs_magnitude`,
      `::test_history_js_reason_card_dollars_word_is_sign_conditional`.
- [ ] **AC-9 — History Detail column colored by sign.** `renderTriggers()`'s Detail cell
      (`rec.detail`) is colored `--studio-pos`/`--studio-neg` by sign, replacing today's
      unconditional `--studio-ink-dim` — matching `renderHero`'s alpha and
      `renderReasonCards`' `alphaColor` convention.
      *Pinned by:* `tests/ui/test_cycle_5_history.py::test_history_js_detail_column_colored_by_sign_not_hardcoded_dim`.
- [ ] **AC-10 — dashboard $-saved panel: verb elements + no naked sign.**
      `templates/index.html` gains `id="dollar-saved-verb"` (snapshot headline) and
      `id="dollar-saved-realized-verb"` (realized-basis headline) — dedicated elements so JS
      can swap "saved"/"lost" independently of each headline's static "across"/"realized"
      text. `static/index.js`'s `fetchGuardAlphaSummary()` sets each verb element by its OWN
      figure's sign and never prepends a literal `-$` for either headline — pure ABS
      magnitude; direction is color + the verb element only.
      *Pinned by:* `tests/app/test_dollar_saved_panel_sign_coherence.py::TestTemplateVerbElementsExist`,
      `::TestSnapshotHeadlineNoNakedMinus`.
- [ ] **AC-11 — dashboard panel windowing wiring.** `fetchGuardAlphaSummary` accepts a
      window-token parameter and includes `window=<token>` in its `/api/guard-alpha-summary`
      fetch URL (AC-4's new param). The existing hero window-picker click handler (which
      already re-fetches `/api/hero-chart` + the windowed strip on click) ALSO calls
      `fetchGuardAlphaSummary(token)` on the same click, so the $-saved panel re-windows in
      lockstep with the rest of the hero instead of staying on all-time forever. `_heroWindow`
      initializes as the STRING `'30d'` (was the bare number `30`) to match the shape the
      click handler assigns and the actively-highlighted 30d button.
      *Pinned by:* `tests/app/test_dollar_saved_panel_sign_coherence.py::TestDollarSavedPanelJoinsWindowPicker`.
- [ ] **AC-12 — no-regression.** Existing all-time-only callers of
      `GET /api/guard-alpha-summary` (Discord aggregation context,
      `tests/app/test_guard_alpha_summary_route.py`) keep getting the all-time sum when
      `window` is omitted; History's 30/60/90/125/ytd buttons and `get_history_summary`'s
      calendar-day arithmetic are otherwise byte-unchanged; `analytics._window_cutoff_date` /
      `compute_windowed_symphony_guard_alpha` are REUSED, never forked or reimplemented; the
      actual `saved_dollars` VALUE computation (`reporting.py:91-94`) and every
      PERCENTAGE-figure surface (Avg Guard Alpha, total_alpha, etc.) are untouched — the
      no-naked-sign convention governs DOLLAR figures only.

## Architecture
- **`analytics.py`** — new `format_dollar_saved(value, *, positive_word="saved",
  negative_word="lost") -> str` (AC-1). Pure function, no I/O, alongside the existing
  `get_history_summary`/`_window_cutoff_date` in the same module.
- **`app.py`** — `guard_alpha_summary()` (~`app.py:2172`) gains the `?window=` param (AC-4),
  resolved via `analytics._window_cutoff_date` (reuse — see Decisions), filtering the same
  `post_mortem_*.json` glob to in-window files before summing; response gains `window` +
  a windowed `date_range`.
- **`analytics.py`** — `get_history_summary()` unchanged in its calendar-day arithmetic
  itself (AC-12) — only the CALLER-supplied day-counts change (AC-6, a template/JS constant
  fix, not a function-body change).
- **`static/history.js`** — `renderHero()` (AC-7), `renderReasonCards()` (AC-8),
  `renderTriggers()` (AC-9).
- **`templates/history.html`** — window-picker `data-window` values (AC-6); no new elements
  needed here (AC-7's contract is color+abs only).
- **`templates/index.html`** — two new verb elements (AC-10).
- **`static/index.js`** — `fetchGuardAlphaSummary()` gains a window-token param + verb-element
  sign logic (AC-10/AC-11); the window-picker click handler (`var windowTokenMap = {...}`)
  gains one more call (AC-11); `_heroWindow`'s initial value becomes a string (AC-11).
- **`static/format_saved.js` (NEW, team-lead directive, item 5)** — ONE shared JS helper
  mirroring `format_dollar_saved`'s contract, consumed by BOTH `index.js` and `history.js` —
  no per-surface JS reimplementation. **Reconciliation note (flagged, not silently decided):**
  the ALREADY-COMMITTED RED tests in `test_dollar_saved_panel_sign_coherence.py` and
  `test_cycle_5_history.py` assert literal tokens (`'lost'`, `Math.abs`) are present INSIDE
  each calling function's own source-text window (`_js_block`/brace-matched body extraction —
  this codebase's no-jsdom idiom). A shared helper is compatible with those assertions ONLY
  if each call site passes its words EXPLICITLY (e.g. `formatSaved(value, 'saved', 'lost')`,
  mirroring the Python helper's keyword-explicit style) rather than relying on silent
  in-module defaults — that keeps the literal word visible at the call site the existing RED
  tests inspect, while still centralizing the abs/sign logic in one file. gas-impl should
  follow this pattern; if gas-test finds it insufficient, the JS RED tests' extraction scope
  (not this plan's AC) is the thing to revise, and that revision should be called out
  explicitly to the team lead/PM before GREEN, not folded in silently.
- **`reporting.py`** — `build_sleeves_digest_section` (~:253-254), the Discord "Total Saved"
  line (~:581), and the QuickChart "Daily Saved ($)" dataset builder — AC-2(a)/(b)/AC-3. The
  actual `saved_dollars`/`realized_pnl_usd` VALUE computations (~:91-94, and wherever
  `realized_pnl_usd` is computed) are untouched — format-layer only.

## Edge Cases
- Exactly `0.0` → the POSITIVE word/color everywhere (the operator ruling's explicit
  zero-boundary case) — `format_dollar_saved(0.0)` → `"$0.00 saved"`; JS mirrors this with
  `>= 0`.
- A tiny negative value that would round-display near zero (e.g. `-0.001`) still renders the
  negative word with NO sign character — never a misleading "$0.00 saved"-looking string with
  a stray `-` (pinned by `test_format_dollar_saved.py`'s dedicated near-zero parametrization).
- `window=all` (or an unrecognized token) on `/api/guard-alpha-summary` → `date_range` spans
  the FULL history (all files' earliest/latest), matching the pre-existing all-time contract.
- A window with exactly one in-window file → `date_range.earliest == date_range.latest`
  (that file's own date) — not the cutoff bound.
- No post-mortem files at all (any window) → the pre-existing "No guard events yet" empty
  state (`guard_event_count == 0`) — untouched by this fix.
- Sleeves digest `realized_pnl_usd is None`/non-numeric → the pre-existing `"n/a"` marker is
  preserved (AC-2(a)'s explicit non-regression case) — `format_dollar_saved` is never called
  on a non-numeric value.
- History's "ytd" button — untouched; it already computes a client-side days-since-Jan-1
  value consistent with the app-wide `ytd` semantics (Jan 1) — not part of the 252/1260
  defect and not touched by AC-6.
- Server local-time vs ET in `get_history_summary`'s `end_date = datetime.now()` — a
  PRE-EXISTING, OUT-OF-SCOPE quirk, not touched by this fix (unchanged from the original
  draft's scoping note).

## Security Considerations
- No new user input surface of consequence — `window` is validated against the existing
  strip-route allowlist pattern (unrecognized → safe degrade to `all`, never a raw string
  interpolated into a query or a path); History's picker values are fixed template literals
  (a constant edit, not a new input path).
- `format_dollar_saved`/the shared JS helper are pure formatting functions over a
  server-computed float — no new DB access, no new secret exposure, no `innerHTML` with
  interpolated external-origin strings (JS changes use `textContent`/DOM APIs per this
  codebase's existing XSS-hygiene pattern).
- The QuickChart per-index color array and the windowed `date_range` are derived entirely
  from values the route/producer already computes in-memory — no new query, no new external
  call.

## Testing Strategy
RED tests for this cycle are ALREADY WRITTEN (committed/staged on this branch) — this plan
documents them rather than prescribing hypothetical ones. This repo's established
JS-behavior-testing idiom (no jsdom): source-text assertions via brace-matched function-body
extraction or `_js_block` windowing, mirroring `tests/dashboard/test_window_picker_wiring.py`'s
pattern; Python-side pieces (the new `analytics.format_dollar_saved`, the windowed route, the
`reporting.py` producers) get real pytest unit/integration tests since those are genuine
Python behavior, not source-text-only.

- `tests/analytics/test_format_dollar_saved.py` — AC-1 (all classes: default words, custom
  words, keyword-only enforcement, magnitude formatting, near-zero boundary).
- `tests/app/test_guard_alpha_summary_windowed.py` — AC-4/AC-5/AC-6
  (`TestWindowedSumMatchesFixture`, `TestByteParityWithHistorySummary`,
  `TestWindowedDateRangeDisclosure`, `TestHistoryWindowPickerAppWideDayCounts`). Fixture
  dates are computed relative to `date.today()` (never hardcoded historical dates, per house
  convention) so the suite stays correct regardless of run date.
- `tests/app/test_dollar_saved_panel_sign_coherence.py` — AC-10/AC-11 (template verb
  elements, no-naked-minus on both headlines, window-picker join wiring, `_heroWindow` string
  init).
- `tests/ui/test_cycle_5_history.py` (extended) — AC-7/AC-8/AC-9. Note: this SUPERSEDES the
  prior `test_history_js_render_hero_total_saved_always_pos` test (deleted/replaced by
  `test_history_js_render_hero_total_saved_colored_by_sign`) — the OLD test encoded the bug
  itself as an assertion; its removal is intentional, not a regression.
- `tests/app/test_sleeves_digest_extension.py` (extended) — AC-2(a)
  (`TestBuildSleevesDigestSectionRealizedPnlSignCoherence`, including the `None` → `"n/a"`
  non-regression case).
- `tests/reporting/test_reporting.py` (extended) — AC-2(b)/AC-3
  (`TestDiscordTotalSavedSignCoherence`, `TestQuickChartDailySavedBarColorBySign`).
- Consumer-suite discovery (house lesson, `feedback_consumer_suite_discovery_before_sufficiency`):
  before GREEN, grep the whole tree for existing consumers of `get_history_summary`'s dict
  shape, `guard_alpha_summary`'s response shape, and any other test asserting the OLD "always
  green"/forced-sign behavior this fix intentionally supersedes — reconcile or update them,
  don't leave a stale duplicate assertion behind.
- Both ruff gates (`ruff format --check .` && `ruff check .`) + the existing parametrized
  `node --check` JS-syntax test (`tests/js_syntax/test_js_syntax.py`) must stay green — no new
  per-file JS syntax test (project CLAUDE.md gotcha).
- **PM's LIVE functional gate** (Merge Workflow step 4): render the dashboard AND the History
  tab (Playwright, seeded DB/fixture carrying at least one net-negative window and one
  net-negative by-reason bucket) to eyeball the red $-figures, the "lost" wording, the
  windowed $-saved panel matching History at a shared token, and the disclosed window — live,
  not just green tests.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Scope reconciled to include the dashboard panel windowing + both index.js headlines + a shared formatter + the Discord/QuickChart/sleeves-digest surfaces | The first draft under-scoped based on the one-line relay alone; gas-test's already-committed RED tests are the authoritative contract (team-lead ruling: "the doc is the lagging artifact, catch it up"). This revision matches the RED tests file-for-file. |
| ONE shared `analytics.format_dollar_saved` (Python) + ONE shared `static/format_saved.js` (JS) | Team-lead directive (item 5) — the same bug (naked sign under an unconditional word) was independently present in 5+ places precisely BECAUSE there was no shared implementation; centralizing removes the class of bug, not just today's instances. |
| Shared JS helper takes words as EXPLICIT call-site arguments, not silent in-module defaults | Reconciles the new shared-module directive with the ALREADY-COMMITTED RED tests, which assert literal `'lost'`/`Math.abs` tokens inside each CALLING function's own source-text window (this codebase's no-jsdom idiom extracts function bodies, not cross-file call graphs). Explicit-argument call sites keep the literal token visible where the existing tests look. Flagged to the team lead as the resolution path, not silently assumed — if gas-test judges this insufficient, revising the JS RED tests' extraction scope is the correct fix, called out explicitly rather than folded in. |
| `get_history_summary`'s calendar-day arithmetic itself stays unchanged; only caller-supplied day-counts (AC-6) change | Unchanged from the original draft's finding: `end_date - timedelta(days=days)` is ALREADY arithmetically identical to `_window_cutoff_date`'s bare-int branch. The divergence was always in the TEMPLATE's mislabeled button values, not in two different date-math implementations. |
| Percentage-figure surfaces keep their existing `+`/`-` convention | Team-lead ruling (item 3): the no-naked-sign convention is scoped to DOLLAR figures. A percentage change conventionally displays a leading sign; this fix does not touch that surface. |
| `reporting.py:91-94` (the `saved_dollars` VALUE formula) stays untouched | Team-lead ruling (item 3) — this is a pure display/formatting fix; the underlying guard-alpha dollar computation is out of scope and already correct per `DE-GUARD-ALPHA-SAVED-001`. |

## Scope Boundaries
- **IN:** the shared Python (`analytics.format_dollar_saved`) and JS (`static/format_saved.js`)
  formatters (AC-1); every Python call site routed through the formatter — sleeves digest,
  Discord Total-Saved line, and any other blast-radius hit (AC-2); QuickChart bar coloring by
  sign (AC-3); the windowed `GET /api/guard-alpha-summary` route + byte-parity proof +
  History's corrected 1Y/5Y values (AC-4/5/6); History hero/by-reason/Detail-column sign
  coloring + abs magnitude (AC-7/8/9); the dashboard panel's verb elements + no-naked-sign +
  windowing wiring (AC-10/11); explicit no-regression guarantees (AC-12).
- **OUT:** the actual `saved_dollars`/`realized_pnl_usd` VALUE computations
  (`reporting.py:91-94` and wherever `realized_pnl_usd` is computed) — untouched, already
  correct. Every PERCENTAGE-figure surface (Avg Guard Alpha, `total_alpha`, `alphaColor`'s own
  %, the windowed `guard-alpha-headline`) — keeps its existing `+`/`-` numeric convention,
  not touched by this cycle. The exit-turnover/friction-drag panel (`performance.html`) —
  already coherent (RULING C's `coverage_days` disclosure), untouched. Any engine/trade-math
  change — pure display/reporting-layer fix, zero touch to `alpha_bot_execution.py` /
  `math_engine.py`. The server-render-clock "data as of" staleness gap and the
  fetch-error-silent-catch gap documented in `.claude/live-dashboard-reality-audit.md` — a
  DIFFERENT, already-known defect class, not conflated with this cycle. Adding a genuinely NEW
  window token (e.g. a real "5y" entry in the shared `_WINDOW_TRAILING_DAYS` table, or any
  OTHER tab's window menu beyond History/the hero strip) — this cycle only fixes the EXISTING
  History-tab picker's mislabeled values and wires the EXISTING hero-strip tokens into the
  $-saved panel; it does not expand any tab's window options.
