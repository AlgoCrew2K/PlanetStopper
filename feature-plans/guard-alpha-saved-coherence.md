# Feature: Guard-Alpha $-Saved Display Coherence — Sign Convention + Windowing
Status: draft — pending PM approval (gate 1/2, this doc)
Created: 2026-07-29

## Provenance note
The operator-locked AC, as relayed by the team lead: "coherent green-saved/red-lost
magnitude convention everywhere + same-range-same-number windowing + window-disclosed."
Everything below AC-1..AC-8 is this doc-writer's own code-verified translation of that
one-line directive into concrete, file:line-backed acceptance criteria — NOT independently
confirmed against the operator's exact words. Flag to PM/operator if the intended scope was
narrower or aimed at a different surface than the two defects found below.

## Summary
Two concrete, verified coherence defects live in the guard-alpha $-saved display layer,
found by auditing every "saved"/"lost"/guard-alpha-labeled numeric render site across
`static/index.js`, `static/history.js`, `static/performance.js`, and their templates:

**(A) Sign/color convention violation — History tab.** Every other saved/lost figure in the
app (`guard-alpha-headline`, `dollar-saved-headline`, `dollar-saved-realized-headline`,
History's own `val-total-alpha`, and the by-reason `alphaColor`) colors green when the value
is `>= 0` and red when `< 0`. The History tab's `val-total-saved` headline
(`static/history.js:74-77`, `renderHero()`) is hardcoded to `--studio-pos` (green)
UNCONDITIONALLY. `analytics.get_history_summary`'s `total_saved` is a real signed sum of
per-trigger `saved_dollars` (`analytics.py:2017,2020`) and CAN go negative — the
`guard-alpha-saved-diagnosis.md` fixture rows (`.claude/guard-alpha-saved-diagnosis.md:35-46`)
show real negative per-trigger `saved_dollars` (e.g. -0.44, -0.22, -1.18). A net-loss window
today renders in the "everything is fine" green. The by-reason card's cumulative-dollar
caption (`static/history.js:248-250,279`) has the SAME defect class twice over: it is
rendered with NO color at all (plain caption-grey text, unlike the alpha figure directly
above it which IS sign-colored), and its string-building (`'$' + s.dollars.toLocaleString(...)`)
lacks the `-$`/`$` sign-then-magnitude idiom the rest of the app uses
(`(v<0?'-$':'$')+Math.abs(v).toFixed(2)` — see `index.js:1434,1456`), so a negative value
renders the malformed `"$-500.00"` instead of `"-$500.00"`, still captioned "saved."

**(B) Windowing incoherence — History tab picker.** The History tab's window-picker
(`templates/history.html:282-289`) offers a button labeled "1Y" with `data-window="252"` and
a button labeled "5Y" with `data-window="1260"`. `static/history.js:375-381`
(`windowDays()`) passes that value straight through as a literal calendar-day count to
`GET /api/history/<days>` → `analytics.get_history_summary(days=days)`, which computes
`start_date = end_date - timedelta(days=days)` (`analytics.py:1986-1987`) — i.e. 252 and 1260
CALENDAR days, not trading days and not actual years. Meanwhile the rest of the app's "1y"
window token (hero strip/chart, `/api/performance`'s `ytd`-adjacent windowing) resolves via
`analytics._window_cutoff_date`/`_WINDOW_TRAILING_DAYS = {"30d":30,...,"1y":365}`
(`analytics.py:1714,1720-1744`) — i.e. 365 calendar days. So "1 year" means 252 calendar days
(~8.3 months) on the History tab and 365 calendar days everywhere else — the SAME nominal
label produces a DIFFERENT actual span and therefore an incomparable number. Separately,
neither the picker nor the rendered hero stats disclose WHICH date range backs the number on
screen at all (no "as of" / resolved-range text anywhere on the page) — a user has no way to
know "1Y" is actually ~8.3 months without reading source. The 30d/60d/90d/125d buttons are
already numerically correct (their `data-window` values already match
`_WINDOW_TRAILING_DAYS`'s 30/60/90/125 literally) — only "1Y"/"5Y" and the missing disclosure
are broken.

Both defects are pure display/reporting-layer bugs — zero touch to `alpha_bot_execution.py`
or `math_engine.py`, zero change to any trade/execution path.

## Acceptance Criteria
- [ ] AC-1: `static/history.js`'s `val-total-saved` headline colors `--studio-pos` when
      `total_saved >= 0` and `--studio-neg` when `total_saved < 0` (same `>=0` idiom as
      `val-total-alpha` in the same function, `dollar-saved-headline`, and
      `dollar-saved-realized-headline`) — no hardcoded color. Also add the same
      `typeof payload.total_saved === 'number'` guard `val-total-alpha`'s coloring already
      uses (currently absent for `total_saved`), so a non-number value never gets colored.
- [ ] AC-2: the by-reason card's cumulative-dollar caption (`renderReasonCards()`,
      `static/history.js:~248-279`) colors by the SAME sign rule as its own alpha figure
      directly above it, instead of rendering uncolored regardless of sign.
- [ ] AC-3: the by-reason card's dollar string uses the SAME `(v<0?'-$':'$') +
      Math.abs(v).toFixed(2)` sign-then-magnitude idiom as `dollar-saved-headline` /
      `val-total-saved`, replacing `'$' + s.dollars.toLocaleString(...)` (which currently
      emits the malformed `"$-500.00"` for a negative figure).
- [ ] AC-4: the by-reason caption's static word "saved" reflects the actual sign of
      `s.dollars` — proposed antonym "lost" when `s.dollars < 0` (exact copy is a product
      call, see Decisions — confirm before the implementer locks the literal string).
- [ ] AC-5 (regression guard): one source-scan test enumerates every dollar/percent
      guard-alpha-or-$-saved-labeled render site across `index.js`/`history.js`
      (`guard-alpha-headline`, `dollar-saved-headline`, `dollar-saved-realized-headline`,
      `val-total-alpha`, `val-total-saved`, by-reason alpha, by-reason dollars) and asserts
      each site's color assignment is gated on a `>=0`/`<0` (or equivalent) comparison, never
      a bare `--studio-pos`/`--studio-neg` literal independent of a sign check — a structural
      guard so a future addition can't reintroduce a hardcoded-color regression.
- [ ] AC-6 (windowing — label/value parity): the History tab's "1Y" button sends
      `data-window="365"` (was `"252"`) and "5Y" sends `data-window="1825"` (5×365, was
      `"1260"`) — matching `analytics._WINDOW_TRAILING_DAYS["1y"]=365`'s app-wide meaning of
      "1 year," so picking "1Y" anywhere in the app spans the same actual calendar range.
- [ ] AC-7 (windowing — disclosure): the History tab renders the RESOLVED date range backing
      the currently-selected window (e.g. "Jan 15, 2026 – Jul 29, 2026") next to the hero
      stats/picker, sourced from additive `start_date`/`end_date` ISO fields on
      `analytics.get_history_summary`'s return dict (the function already computes both
      locally, `analytics.py:1986-1987` — purely additive, no new query) — never a
      client-guessed range. Suppressed alongside the other hero elements in the existing
      empty-state branch (`isEmpty`, `history.js:45-55`) so no stale range shows with zero
      data. Mirrors the exit-turnover panel's existing `coverage_days`-per-window disclosure
      precedent (`templates/performance.html:577-582`, "RULING C").
- [ ] AC-8 (no-regression): History's 30/60/90/125/ytd buttons, `get_history_summary`'s
      calendar-day arithmetic itself, and `GET /api/history/<days>`'s existing
      `window_days` field are BYTE-UNCHANGED — only the "1Y"/"5Y" button values (AC-6) and
      the additive date-range fields (AC-7) change.

## Architecture
- `static/history.js`:
  - `renderHero()` (~:44-83) — sign-gate `val-total-saved`'s color (AC-1); render the
    resolved date-range text (AC-7), suppressed under the existing `isEmpty` branch.
  - `renderReasonCards()` (~:228-298) — sign-gate + reformat + rewordthe by-reason dollar
    caption (AC-2/3/4).
- `templates/history.html`:
  - Window-picker buttons (~:282-289) — `data-window` value fix for "1Y"/"5Y" (AC-6).
  - New element near the hero/picker (e.g. `data-testid="history-window-range"`) for the
    disclosed range (AC-7).
- `analytics.py`:
  - `get_history_summary()` (~:1973-2040+) — additive `start_date`/`end_date` ISO keys on the
    already-returned `stats` dict, sourced from the function's own existing local
    `start_date`/`end_date` variables (~:1986-1987). No change to the windowing arithmetic.
- `app.py`:
  - `get_history()` route (~:3869-3872) — no route-logic change; the new keys ride through
    the existing `stats` dict the same way `stats["window_days"] = days` already does.

## Edge Cases
- `total_saved` / `s.dollars` exactly `0.0` → colors "pos" (green), consistent with the
  `>=0` convention already used by every other sign-colored element in this app.
- `total_saved` / `s.dollars` non-number (defensive; `get_history_summary` always returns a
  float via accumulation, so this should not occur in practice) → render neutral/`--`, never
  a colored zero — same defensive posture `val-total-alpha`'s existing type guard already has.
- "ytd" button — untouched. `windowDays('ytd')` already computes a client-side
  days-since-Jan-1 value (`history.js:376-379`) consistent with the hero's own
  `_window_cutoff_date("ytd")` = Jan 1 semantics (`analytics.py:1739-1740`) — already
  coherent, no fix needed, called out here only so it isn't mistaken for an oversight.
- Empty-state window (zero exits) — the new disclosed-range element must hide alongside the
  other hero elements (`heroEl`/`dailyEl`/`reasonEl` all toggle on `isEmpty`) so a
  zero-data window never shows a misleadingly precise date range next to "No exits."
- Server local-time vs ET: `get_history_summary`'s `end_date = datetime.now()` uses naive
  server-local time, not ET — a PRE-EXISTING, OUT-OF-SCOPE quirk. Not touched by this fix;
  called out explicitly so it is not silently conflated with AC-6/AC-7 (which fix the
  button-value mismatch and add disclosure, not timezone correctness).

## Security Considerations
- No new user input surface — window-picker values are fixed template literals (a constant
  edit, 252→365 / 1260→1825), not a new free-form input path.
- The disclosed date-range string is server-computed (plain ISO date arithmetic already
  performed by `get_history_summary`, no user-controlled format string) and rendered via
  `textContent`/DOM APIs, consistent with `history.js`'s existing XSS-hygiene pattern
  (`escHtml` / `createElement` throughout the file, no `innerHTML` with interpolated
  external-origin strings).
- No new DB access pattern — the date range is derived from values `get_history_summary`
  already computes in-memory; no new query, no new secret exposure.

## Testing Strategy
This repo's established JS-behavior-testing idiom (no jsdom harness): source-text assertions
via `pathlib.read_text()` + string/regex checks, mirroring
`tests/dashboard/test_window_picker_wiring.py`'s pattern, PLUS the existing parametrized
`node --check` syntax gate (`tests/js_syntax/test_js_syntax.py`) — extend it, do not add a
new per-file JS syntax test (per the JS-syntax-coverage entry in project CLAUDE.md). These
are "necessary, not sufficient" — the PM owns the live render gate.

- **RED (quant-test-writer):**
  1. `templates/history.html` source assertion: `data-window="365"` present (associated with
     the "1Y" label) and `data-window="1825"` present (the "5Y" label); the OLD
     `data-window="252"` / `data-window="1260"` values are ABSENT — pin both the new
     presence AND the old absence, not just a bare "365 appears somewhere" check.
  2. `static/history.js` source assertion: the exact old hardcoded line
     (`savedEl.style.color = cssVar('--studio-pos');` with no conditional) is GONE, and the
     replacement is conditioned on a `total_saved` sign comparison mirroring `alphaEl`'s
     existing pattern in the same function.
  3. Same source-assertion style for the by-reason dollar coloring/format/wording
     (AC-2/3/4) — assert the old sign-blind `'$' + s.dollars.toLocaleString(...)`
     concatenation is gone, replaced by a sign-aware format matching the headline idiom.
  4. `analytics.py` unit test (real Python/pytest — this piece is NOT source-text-only):
     `get_history_summary(days=N, base_dir=<fixture dir>)` returns additive `start_date`/
     `end_date` ISO keys whose span is consistent with `N`.
  5. `app.py` route test (`tests/app/`): `GET /api/history/<days>` response JSON carries the
     new `start_date`/`end_date` keys pass-through; `window_days` behavior unchanged
     (AC-8) — grep `tests/app/test_history_*.py` +
     `tests/analytics/test_history_daily_drilldown.py` for existing consumers of
     `get_history_summary`'s dict shape BEFORE extending it (consumer-suite-discovery
     before sufficiency — a house lesson from a prior CI-bounce class).
  6. AC-5's coherence-audit guard: one parametrized source-scan test over the 7 identified
     render sites, asserting each is sign-gated (no bare color literal).
- Both ruff gates (`ruff format --check .` && `ruff check .`) + the existing parametrized
  `node --check` test must stay green.
- **PM's LIVE functional gate** (Merge Workflow step 4): render the History tab (Playwright,
  against a seeded DB/fixture carrying at least one net-negative window — the diagnosis
  doc's own fixture rows already prove real negative `saved_dollars` values occur) to
  eyeball the red $-figure and the disclosed date range live, not just green tests.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Constant-edit only for AC-6, no rewiring of `get_history_summary` to call `analytics._window_cutoff_date` | Verified `get_history_summary`'s `end_date - timedelta(days=days)` (`analytics.py:1986-1987`) is ALREADY arithmetically identical to `_window_cutoff_date`'s bare-int branch (`today - timedelta(days=window)`, `analytics.py:1734-1735`). The divergence lives entirely in the TEMPLATE's mislabeled button values, not in two different date-math implementations. Rewiring would be a no-op refactor with real risk to a working function's calling contract, for zero behavior gain — rejected as scope creep. |
| 5Y target = 1825 (5×365) | No other view in the app currently defines a "5y" token to align WITH — 1825 is the only internally-coherent choice (5× the app's own existing "1y"=365 definition). [PM-ASSUMED — flag to operator if a different 5Y definition (e.g. trading days) was intended.] |
| Reason-card wording "lost" (AC-4) | Plain antonym of "saved." [PM-ASSUMED copy — confirm before the implementer locks the literal string; this is a product-copy call, not a code-correctness fix.] |
| Scope limited to the History tab | Audited `dollar-saved-headline`/`dollar-saved-realized-headline` (index.js) and the exit-turnover panel (performance.js) — BOTH already sign-coherent / already window-disclosed (RULING C's `coverage_days`). No changes needed there; AC-5's regression guard covers them so a future edit can't quietly regress that existing correctness. |

## Scope Boundaries
- **IN:** History tab hero `total_saved` color (AC-1); by-reason dollars color+format+wording
  (AC-2/3/4); the cross-file coherence-audit regression guard (AC-5); History window-picker
  "1Y"/"5Y" value correction (AC-6); resolved-date-range disclosure (AC-7); explicit
  no-regression to 30/60/90/125/ytd and the underlying calendar-day arithmetic (AC-8).
- **OUT:** `guard_alpha_summary`'s cumulative (non-windowed, all-time) basis and its existing
  `basis_label` disclosure — already correct per `DE-GUARD-ALPHA-SAVED-001` /
  `DE-EXIT-FRICTION-REALIZED-001`, untouched. The exit-turnover panel — already coherent,
  untouched (covered only by the new regression guard, not modified). Any engine/trade-math
  change — pure display/reporting-layer fix, zero touch to `alpha_bot_execution.py` /
  `math_engine.py`. The server-render-clock "data as of" staleness gap and the
  fetch-error-silent-catch gap documented in `.claude/live-dashboard-reality-audit.md` — a
  DIFFERENT, already-known, not-yet-remediated defect class; out of scope here, not to be
  conflated with this cycle. Adding a genuinely NEW window token (a real "5y" entry in the
  shared `_WINDOW_TRAILING_DAYS` table, or any OTHER tab's window menu) — this cycle only
  fixes the EXISTING History-tab picker's mislabeled values; it does not expand any other
  tab's window options.
