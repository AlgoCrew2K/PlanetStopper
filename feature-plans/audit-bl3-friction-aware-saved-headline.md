# Feature: Friction-Aware $-Saved Headline Disclosure (BL-3)
Status: shipped (pending merge)
Created: 2026-08-04
Source: `docs/audit/TWO-WEEK-REVIEW-2026-08-04.md` §4 Finding M1, §6 Backlog BL-3 (commit `ca7f2beb`)

## Summary
The live `$-saved` dashboard headline is friction-blind on BOTH bases it reports.
`reporting.py:92` (`saved_pct = f_ret - live_ret`, snapshot basis) and
`reporting.py:193` (`saved_pct_realized = f_ret - realized_ret`, realized/marks
basis) both carry no trading-cost term. Meanwhile the team's OWN optimizer already
models `SIM_EXIT_FRICTION_PCT = 0.5` percentage points (`autotuner.py:1463`),
subtracted at 3 replay-only accounting sites (`autotuner.py:1545/1742/2012`) and
structurally FORBIDDEN from ever reaching the live engine
(`tests/autotuner/test_exit_friction_blast_radius.py`'s `_FORBIDDEN_FILES =
{alpha_bot_execution.py, math_engine.py}`). Neither dashboard headline
(`templates/index.html:1057-1081`, populated by `fetchGuardAlphaSummary()` in
`static/index.js:1429-1484`, sourced from `GET /api/guard-alpha-summary` in
`app.py:3267-3484`) discloses "gross of trading costs."

This is not cosmetic: the audit independently re-derived, from live `shadow_daily`
(87 post-trigger symphony-days), that this window's mean GROSS save is **+0.13pp**
— SMALLER than the 0.5pp friction the team's own optimizer already assumes.
Net-of-friction, the window mean flips to **-0.37pp** and the share of positive
symphony-days drops from 54% to 29%. The live $-saved headline currently reports
only the gross (overstated) figure with no disclosure that a cost term exists.

The fix is DISPLAY-LAYER ONLY: `SIM_EXIT_FRICTION_PCT` must never be referenced
from `alpha_bot_execution.py` or `math_engine.py` (enforced by the existing
blast-radius test, which already allows `app.py` as a reference surface alongside
`autotuner.py`/`reporting.py`/`database.py`). No change to the actual
`saved_dollars`/`saved_dollars_realized` VALUE computations in `reporting.py`.

## Acceptance Criteria
- [x] **AC-1 — snapshot-basis net-of-friction figure.** `GET /api/guard-alpha-summary`
      gains `cumulative_saved_dollars_net_of_friction` — for each valid in-window
      trigger entry, computed as
      `symphony_value * (saved_pct_guard_alpha - SIM_EXIT_FRICTION_PCT) / 100.0`
      (the SAME formula shape as `reporting.py:92-95`'s `saved_dollars`, with the
      team's own friction constant subtracted from the percentage BEFORE the
      dollar conversion — never a post-hoc dollar-level subtraction, which would
      not scale correctly per-position), summed across the same valid entries
      `cumulative_saved_dollars` already sums (`app.py:3361-3376`). Additive to,
      never replacing, `cumulative_saved_dollars`. `0.0` in the honest empty state
      (`guard_event_count == 0`).
- [x] **AC-2 — realized-basis net-of-friction sibling.** The same friction
      subtraction applied to the realized/marks basis:
      `saved_dollars_realized_net_of_friction`, using each entry's
      `saved_pct_guard_alpha`-equivalent realized percentage (derive from the
      already-serialized `saved_dollars_realized`/`symphony_value` fields — do not
      require a new percentage field if the existing realized dollar figure can be
      re-expressed with the SAME per-position friction subtraction pattern as AC-1;
      implementer confirms the cleanest derivation against `reporting.py:190-197`'s
      actual field shapes at TDD time). Additive-only, absent (never fabricated)
      for entries with no realized coverage (mirrors the EXISTING
      `realized_coverage` honesty contract at `app.py:3294-3297`/AC-7 in
      `DE-EXIT-FRICTION-REALIZED-001`).
- [x] **AC-3 — day-1 intraday-fallback path nets out friction too.** The
      `exit_triggers`-sourced intraday estimate branch (`app.py:3400-3467`, active
      only before the first post-mortem file exists) applies the SAME friction
      subtraction to its per-row dollar estimate (`(at_return - current_return) -
      SIM_EXIT_FRICTION_PCT) / 100 * position_value`) — consistency across all
      three bases this route can report, never silently friction-free just because
      it's the fallback path.
- [x] **AC-4 — persistent gross-of-cost disclosure.** The dashboard's dollar-saved
      panel (`templates/index.html:1057-1081`) gains a STATIC (never JS-injected,
      so it can never be silently dropped by a JS bug) caveat qualifying the
      existing gross headline(s) as "gross of trading costs" — mirroring the
      existing static "(marks basis)" caption pattern at `:1069-1073`/`:1077`.
- [x] **AC-5 — net-of-friction figure rendered as a third line.**
      `fetchGuardAlphaSummary()` (`static/index.js:1429-1484`) renders
      `cumulative_saved_dollars_net_of_friction` (and its realized sibling) as an
      ADDITIONAL line on the dollar-saved panel — new dedicated DOM elements, never
      overwriting the existing gross-headline elements — reusing the SAME
      `DE-GAS-COHERENCE-001` display contract already established for every
      dollar figure on this panel: ABS magnitude, no naked sign character,
      sign-conditional color + word ("saved"/"lost"), never hardcoded green.
- [x] **AC-6 — single source of truth for the friction constant.** `app.py`
      imports `SIM_EXIT_FRICTION_PCT` from `autotuner` (module already an
      authorized reference surface per `tests/autotuner/test_exit_friction_blast_radius.py`'s
      `_ALLOWED_FILES`, which already includes `app.py`) — never redefines a second
      local constant. A future change to the optimizer's modeled friction value
      must automatically propagate to this display, not silently diverge.
- [x] **AC-7 — zero engine/value-computation touch.** `alpha_bot_execution.py` and
      `math_engine.py` carry zero diff (already enforced by the existing
      blast-radius test's forbidden-files check — no test change needed there
      since `app.py` is already an allowed surface). The actual
      `saved_dollars`/`saved_dollars_realized` VALUE computations
      (`reporting.py:92-95`/`:190-197`) are byte-unchanged — this fix only adds a
      NEW additional aggregate field alongside them.
- [x] **AC-8 — no regression.** `cumulative_saved_dollars`, `saved_dollars_realized`,
      `realized_coverage`, `guard_event_count`, `basis_label`, `date_range`, and the
      `window=` query-param behavior (`DE-GAS-COHERENCE-001`) are all byte-unchanged
      for existing callers; the new net-of-friction fields respect the SAME
      `window=` filtering the gross fields already apply (no separate cutoff
      scheme).

## Architecture
- **`app.py`** — `guard_alpha_summary()` (`:3267-3484`): new accumulator variables
  alongside the existing `cumulative_saved_dollars`/`saved_dollars_realized`,
  computed inside the SAME per-trigger loop (`:3361-3376`) that already reuses
  `t.get("symphony_value")` (present in every trigger entry per
  `reporting.py:113-136`) and `t.get("saved_pct_guard_alpha")`. Module-scope import
  of `SIM_EXIT_FRICTION_PCT` from `autotuner` (AC-6) — follow whatever existing
  autotuner-import convention `app.py` already uses elsewhere in the file (verify
  at implementation time; do not introduce a new import style).
- **`templates/index.html`** — dollar-saved panel (`:1053-1081`) gains a third
  figure block + static caveat, structurally paralleling the existing
  realized-basis block (`:1068-1080`)'s own pattern of a static qualifying caption
  next to a JS-populated figure.
- **`static/index.js`** — `fetchGuardAlphaSummary()` (`:1429-1484`) gains render
  logic for the new field(s), reusing this file's existing per-file-local
  dollar-format contract established by `DE-GAS-COHERENCE-001` (AC-13 of that
  cycle) rather than inventing a new formatting idiom.

## Edge Cases
- A legacy trigger entry predating this fix (no `symphony_value` or
  `saved_pct_guard_alpha` field) — excluded from the net-of-friction sum, same
  non-regression discipline as `DE-EXIT-FRICTION-REALIZED-001`'s
  `realized_coverage` counters; never coerced to a fabricated `0.0`.
- Net-of-friction can be NEGATIVE even when the gross figure is positive (this
  window's own +0.13pp-gross-vs-0.5pp-friction case) — sign-colored
  INDEPENDENTLY of the gross figure's own sign/color, never inherited.
- `window=all` or an omitted `window` param — the net-of-friction fields apply the
  SAME all-time default the gross fields already use.
- Zero guard events (`guard_event_count == 0`) — net-of-friction fields render the
  same honest empty state as the gross fields, not a spurious `$0.00`.

## Security Considerations
- No new input surface — `SIM_EXIT_FRICTION_PCT` is an internal, non-user-supplied
  constant; the route remains read-only, covered by the existing global auth hook,
  not in `_SETTINGS_WRITE_ALLOWLIST`, no `LIVE_EXECUTION` interaction.

## Testing Strategy
- Extend `tests/app/test_guard_alpha_summary_windowed.py` (or add a sibling) —
  AC-1/AC-2/AC-3: a fixture with a known `saved_pct_guard_alpha` + `symphony_value`
  pair asserts the net-of-friction figure equals the hand-computed value; a SECOND
  fixture whose gross save is BELOW `SIM_EXIT_FRICTION_PCT` proves the sign can
  flip (mirrors the audit's real +0.13pp-gross / -0.37pp-net finding) — this is the
  non-vacuity control (a test that would fail under the OLD friction-blind code).
- `tests/autotuner/test_exit_friction_blast_radius.py` stays green unmodified —
  `app.py` is already in `_ALLOWED_FILES`; `alpha_bot_execution.py`/`math_engine.py`
  remain in `_FORBIDDEN_FILES` with zero new references.
- JS body-extraction test (this repo's no-jsdom idiom, mirroring
  `tests/app/test_dollar_saved_panel_sign_coherence.py`) — AC-5: assert the new
  render block exists, reuses the abs-magnitude/no-naked-sign/sign-conditional-word
  contract, and is a genuinely separate element from the existing gross headline.
- Consumer-suite discovery (house lesson): grep `tests/` for any existing assertion
  on the EXACT DOM shape of the dollar-saved panel or the EXACT JSON key set of
  `GET /api/guard-alpha-summary`'s response that a new sibling field/element might
  perturb; reconcile rather than leave a stale duplicate assertion.
- Both ruff gates + the parametrized `node --check` JS-syntax test stay green.
- PM's LIVE functional gate (Merge Workflow step 4): render the dashboard against a
  seeded DB carrying at least one window where gross-positive flips net-negative,
  and visually confirm the disclosure + third figure render correctly — live, not
  just green tests (mirrors the precedent set by `DE-GAS-COHERENCE-001`'s own
  Testing Strategy).

## Decisions
| Decision | Rationale |
|----------|-----------|
| Recommend option (a) — a disclosed third figure, not just a caveat label | The audit's own backlog item recommends "(a) with disclosure" and states the two are "not mutually exclusive" — implementing both the caveat AND the computed figure gives the operator both the honest label and the actual net number, closing the finding more completely than either alone. |
| Scope limited to the dashboard panel (`templates/index.html:1057-1081`), not History tab or Discord | The audit's own evidence citations for BL-3 are `reporting.py:92/193` (the VALUE computation, unchanged) and `templates/index.html:1057-1081` (the specific headline). The History tab and Discord embed read the SAME underlying `saved_dollars` field but were not cited as part of THIS backlog item's fix scope — extending friction disclosure there is a natural follow-up, not silently folded into this cycle. |
| Friction subtracted at the PERCENTAGE level before dollar conversion, never post-hoc on the dollar sum | Matches `reporting.py:92-95`'s own formula shape exactly (percentage delta * position value) — subtracting a flat dollar amount from the aggregate sum instead would not scale correctly across positions of different sizes and would silently diverge from how the optimizer itself applies the SAME constant in its replay accounting. |

## Scope Boundaries
- **IN:** new net-of-friction aggregate fields (snapshot basis, realized basis,
  intraday-fallback basis); a persistent gross-of-cost caveat; dashboard rendering
  of the third figure; the shared-constant import.
- **OUT:** any change to `SIM_EXIT_FRICTION_PCT`'s VALUE or its use inside
  `autotuner.py`'s replay accounting (`:1545/1742/2012`); any change to the actual
  `saved_dollars`/`saved_dollars_realized` computation in `reporting.py`; any
  live-engine change (`alpha_bot_execution.py`/`math_engine.py` — structurally
  forbidden per the existing blast-radius test); History tab
  (`static/history.js`/`analytics.get_history_summary`) or Discord embed
  (`reporting.py`'s EOD post) friction disclosure — not cited by this backlog
  item's evidence, a candidate follow-up only.


## Shipped

Shipped 2026-08-05 as `DE-AUDIT-BL3-001` (see `DECISIONS.md`). Commit chain: `0e001f5b` (RED) -> `ed4eebee` (GREEN) -> `97b900ff` (sufficiency-review RED pin) -> `48f5f149` (bl3review-found empty-state RED, the plan's own "Zero guard events" edge case) -> `40b130e3` (fix). 57 tests green at HEAD `40b130e3` across `tests/app/test_guard_alpha_summary_friction_aware.py`, `tests/app/test_dollar_saved_panel_friction_disclosure.py`, and the unmodified `tests/autotuner/test_exit_friction_blast_radius.py`. `quant-code-reviewer` BLOCK on the initial GREEN (the empty-state gap) -> fix landed -> re-review pending at this doc pass. Test-writer sufficiency verdict: SUFFICIENT. Status here means the Toxic Pair TDD cycle is complete and green, not that the PR has merged to origin (see the project's Merge & PR Workflow hard rule).
