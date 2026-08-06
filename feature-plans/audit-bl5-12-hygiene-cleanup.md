# Feature: Two-Week Audit Hygiene Cleanup Bundle (BL-5..BL-12)
Status: shipped (pending merge)
Created: 2026-08-04
Source: `docs/audit/TWO-WEEK-REVIEW-2026-08-04.md` §4 Findings D3/M3/D6/T3/M4/T5/T6/D7, §6 Backlog BL-5..BL-12 (commit `ca7f2beb`)

## Summary
Eight low-severity (P3 hygiene / P4 info-cleanup) findings from the two-week audit,
bundled into one plan per the PM's dispatch. Each is independently small
(XS-S effort) and independently scoped — none depends on another shipping first.
All are display/documentation/defensive fixes; none touches trade-execution math.

1. **BL-5 (D3, window-cutoff asymmetry):** `/api/guard-alpha-summary?window=`
   resolves its cutoff via `analytics._window_cutoff_date` — a UTC-DATE cutoff,
   INCLUSIVE of the boundary day (`app.py:3315-3316/3352`, `analytics.py:1720-1744`
   — `today = _dt.now(UTC).date()`, string comparison `date_str < _cutoff_iso`
   excludes only STRICTLY-older dates). `analytics.get_history_summary`
   (`analytics.py:1987-2022`, backing `/api/history/<days>`) instead uses NAIVE
   LOCAL `_dt.now()` (no UTC) carrying time-of-day, compared via
   `start_date <= file_date <= end_date` — a full-datetime comparison, not a pure
   date comparison. At a boundary-dated post-mortem file, the two surfaces can
   disagree about whether that file is "in window."
2. **BL-6 (M3, ET fallback):** `analytics.get_symphony_today_change`
   (`analytics.py:513-552`) falls back to `datetime.now(UTC)` (`:543`) ONLY when a
   caller omits `trading_day`. Both current production call sites pass an explicit
   ET-derived `trading_day`, so this branch is dead code today — but a future
   forgetful caller after ~20:00 ET would query TOMORROW's UTC date against
   ET-stamped rows, silently returning empty results.
3. **BL-7 (D6, stale comment):** `app.py:7406-7410`'s comment claims
   "[DIVERGENCE_EXPLAINER] is permanently rejected but still writes one per
   autotune run" — false since AC-14.
   `advisors/divergence_explainer.py:150-181`'s `run_divergence_explainer` is
   explicit: "§B off: writes NOTHING and returns None (AC-14 Mechanism 2)."
4. **BL-8 (T3, silent-never-tuned signal):** `fallback_oos_alpha ==
   default_oos_alpha` byte-exact in 40/40 observed `autotune_runs` rows, `0/40`
   "Adopted AI," `oos_alpha = -inf` in 40/40 rows — Planet Stopper has run on
   100% stock-default risk parameters for its entire visible history. The gate is
   correctly implemented (an honest statistical null per the audit's own T3
   finding), but the operator currently has NO signal distinguishing "tuning tried
   this week and reverted" from "tuning has never once engaged."
5. **BL-9 (M4, basket-reconstruction footgun):** the "TRUE SHADOW RETURN OVERRIDE"
   block (`alpha_bot_execution.py:1246-1261`) reconstructs a basket-based
   `current_return` for TRIGGERED symphonies (from `trigger_prices` + live VWAPs)
   and writes it, unconditionally, into `bot_state[symphony_id]["current_return"]`
   at `:1635` — the THIRD of three write sites, distinct from the two CLEAN
   per-tick writes at `:886` and `:1037` (both `sym.get("last_percent_change", 0.0)
   * 100`, untriggered-safe). The read path is already clean today —
   `reporting.py`/`analytics.py` source $-saved exclusively from `shadow_history`,
   never `bot_state["current_return"]` for a triggered symphony — but the
   reconstructed-value footgun remains live code with no structural fence
   preventing a FUTURE reader from silently picking it up.
6. **BL-10 (T5, dead autotune columns):** `autotune_runs.deflated_sharpe` is
   intentionally, already-documented dead (`tests/autotuner/
   test_c4_dsr_machinery_removed.py`, Decision D3). Six OTHER columns —
   `ce_metric`/`cvar_feasible`/`lambda_budget`/`sortino_sentinel_pct`/`fold_role`/
   `account_id` — exist in the schema (`database.py:192-195`/`:240`-region) but
   have NO parameter in `save_autotune_run`'s signature at all
   (`database.py:692-715`) — a never-implemented-for-this-table schema, not a
   caller bug (note: `account_id`/`sortino_sentinel_pct`/`fold_role` ARE real,
   actively-used columns/params on a DIFFERENT table's accessor family — the
   port-level `port_state`/`read_port_state` cluster and a separate port-level
   save function around `database.py:823-919` — this finding is scoped
   specifically to their presence as unimplemented `autotune_runs` columns).
7. **BL-11 (T6, unannotated sum convention):** `autotuner.py`'s
   `total_guard_alpha` (accumulation begins `:1928`+, inside `run_simulation`)
   SUMS guard-alpha across every triggered OOS day; `avg_oos_alpha = oos_alpha /
   test_days_count` (`autotuner.py:3036`) exists specifically to un-inflate it for
   human reading. This convention is why raw `oos_alpha` values like -743%/-581%
   appear in the DB/logs — a sum artifact, not corruption — but it is easy to
   misread without a comment at the point of definition.
8. **BL-12 (D7, undisclosed CR basis):** `_account_totals_cache["portfolio_cr"] =
   data["simple_return"] * 100.0` (`app.py:844`, deliberately matching Composer's
   own displayed "Total return" per the existing code comment at `:840-843` —
   cash-flow-sensitive, diverges from time-weighted-return by ~5pp when cash flows
   exist). The dashboard's "Cumulative · lifetime" label
   (`templates/index.html:946`) does not disclose this basis, so an operator
   benchmarking against a TWR figure elsewhere could be misled.

## Acceptance Criteria

### BL-5 — window-cutoff unification
- [x] **AC-1:** `analytics.get_history_summary` is re-routed to derive its
      window boundary from `analytics._window_cutoff_date` (the SAME function
      `/api/guard-alpha-summary`/`/api/strip/<window>` already use) instead of its
      own inline `end_date - timedelta(days=days)` / naive-local `_dt.now()`
      arithmetic — one cutoff function, one timezone (UTC, matching
      `_window_cutoff_date`'s existing convention) shared by both surfaces.
- [x] **AC-2:** a boundary-dated post-mortem file (dated exactly at the cutoff
      day) is now included/excluded IDENTICALLY by both
      `/api/guard-alpha-summary?window=<N>d` and `/api/history/<N>` for the same
      `N` — a new test proves byte-parity at the boundary, closing the exact gap
      the `#117`/`DE-GAS-COHERENCE-001` live-parity verification did not exercise.
- [x] **AC-3:** `get_history_summary`'s NON-boundary behavior (day counts already
      proven byte-comparable by `DE-GAS-COHERENCE-001`'s AC-5) stays unchanged —
      this is a boundary-condition fix, not a rewrite of the aggregation logic.

### BL-6 — ET fallback
- [x] **AC-4:** `analytics.get_symphony_today_change`'s `trading_day`-omitted
      fallback (`analytics.py:543`) computes the ET calendar date (matching the
      write-side convention already used at `alpha_bot_execution.py:711` and the
      existing explicit-`trading_day` call sites) instead of `datetime.now(UTC)`.
- [x] **AC-5:** both existing production call sites (verify current line numbers
      at implementation time — cited historically as `app.py:1502`/`:2676`) are
      confirmed to still pass an explicit `trading_day` (this fix does not change
      their behavior — it only corrects the DEFENSIVE fallback branch neither
      currently reaches).
- [x] **AC-6:** a new unit test exercises the fallback branch directly (omitting
      `trading_day`) at a time-of-day where UTC-vs-ET would diverge (e.g. a fixed
      `freezegun`/monkeypatched clock at 21:00 ET / 01:00 UTC-next-day), proving
      the function now returns the ET-correct date instead of tomorrow's UTC date.

### BL-7 — stale comment correction
- [x] **AC-7:** `app.py:7406-7410`'s comment is corrected to describe the CURRENT
      `run_divergence_explainer` contract accurately — the producer, when
      `SECOND_WINDOW_CVAR_ENABLED` is off, writes NOTHING (returns `None`) — and
      clarifies that the filter immediately below the comment exists as legacy-row
      defense for the 22 pre-AC-14 `NOT_APPLICABLE` rows still in the DB, not as an
      ongoing per-run write it needs to suppress.
- [x] **AC-8:** zero functional change — the filter logic itself
      (`app.py:7415-...`) is untouched; this is a comment-only correction.

### BL-8 — "never adopted" operator signal
- [x] **AC-9:** a new computed signal (name/shape at implementer's discretion, but
      must be derivable from existing `autotune_runs` data with NO new schema) —
      e.g. "N consecutive weeks at default/fallback params" or "tuning has not
      adopted an AI proposal since <date|never>" — surfaced somewhere in the
      dashboard or reporting layer (AI Advisor Overview panel, the EOD Discord
      digest, or the dashboard hero — implementer's choice of the most natural
      existing surface), computed from `baseline_decision != "Adopted AI"` streaks
      per symphony (or portfolio-wide) across `autotune_runs` rows ordered by
      `run_timestamp`.
- [x] **AC-10:** the signal is DISTINCT from the existing per-week
      "Reverted to Fallback"/"Reset to Global Default" `baseline_decision` string
      already shown per run — it must communicate the ACCUMULATED pattern across
      runs, not merely repeat the latest single-run outcome.
- [x] **AC-11:** honest degrade — a symphony/portfolio with fewer than 2
      `autotune_runs` rows (insufficient history to establish a streak) renders an
      informative "insufficient history" state, never a fabricated streak count.

### BL-9 — basket-reconstruction footgun hardening
- [x] **AC-12:** the reconstructed value written at `alpha_bot_execution.py:1635`
      for a triggered symphony is structurally distinguished from the two clean
      per-tick writes at `:886`/`:1037` — e.g. via a dedicated in-code marker,
      renamed intermediate variable, or an adjacent structural comment/docstring
      making it unmistakable to a future reader that
      `bot_state[symphony_id]["current_return"]` carries a RECONSTRUCTED value for
      any symphony where `triggered == True`. The exact hardening mechanism
      (comment-only vs. a dedicated shadow key vs. a runtime assertion) is an
      implementer decision, made in consultation with `risk-engine-specialist`
      given this touches the live 1-minute execution path.
- [x] **AC-13:** the override is NOT deleted or functionally altered — it
      continues to feed live exit-decision inputs (holdings/HWM/MC) for
      already-triggered symphonies exactly as today; this is a discoverability/
      documentation hardening, not a behavior change.
- [x] **AC-14:** zero change to `reporting.py`/`analytics.py`'s existing clean
      read path (both already correctly source $-saved from `shadow_history`, never
      from `bot_state["current_return"]` for a triggered symphony) — this AC is a
      regression guard proving the read side stays unaffected by any hardening
      applied on the write side.

### BL-10 — dead column documentation
- [x] **AC-15:** each of the 6 never-wired `autotune_runs` columns
      (`ce_metric`/`cvar_feasible`/`lambda_budget`/`sortino_sentinel_pct`/
      `fold_role`/`account_id`) gains an explicit schema-comment or
      `save_autotune_run` docstring note (in `database.py`, near the existing
      `deflated_sharpe`-is-dead precedent) marking it never-implemented for THIS
      table — distinct wording from `deflated_sharpe`'s already-documented
      intentionally-removed status, since these 6 were never wired in the first
      place rather than removed.
- [x] **AC-16:** no schema migration and no column drop — per the project's
      "additive-first, NULLable + DEFAULT, never destructive in one step" standard
      (project CLAUDE.md Coding Standards), this AC is documentation-only.

### BL-11 — `oos_alpha`-sum annotation
- [x] **AC-17:** a one-line comment is added at (or immediately near) the
      `total_guard_alpha` accumulation start in `run_simulation`
      (`autotuner.py:1928`+ region) explaining that the accumulated value is a
      multi-day SUM across triggered OOS days, not a per-day or annualized figure
      — and cross-references `avg_oos_alpha` (`autotuner.py:3036`) as the
      un-inflated per-day companion.
- [x] **AC-18:** the SAME annotation (or a cross-reference to it) is added near
      the `autotune_runs.oos_alpha` column definition/docstring in `database.py`
      (alongside `save_autotune_run`'s existing per-column docstring block,
      `database.py:716-762`), so a reader inspecting the DB schema directly (not
      just the computation site) also sees the sum-convention warning.
- [x] **AC-19 (best-effort, verify feasibility at implementation time):** if any
      existing operator-facing surface (dashboard, Discord EOD digest) displays
      the RAW `oos_alpha` sum without its `avg_oos_alpha` companion, surface
      `avg_oos_alpha` alongside it there too. If no such surface exists today
      (verify via grep before assuming one does), the comment-only fix (AC-17/18)
      is sufficient and this AC is satisfied vacuously — do not invent a new
      display surface to satisfy it.

### BL-12 — CR-basis disclosure
- [x] **AC-20:** the "Cumulative · lifetime" label
      (`templates/index.html:946`, `class="vs-row-label"`) gains a tooltip (or
      adjacent disclosure text) stating the figure is Composer's own cash-flow-
      sensitive "Total return" convention (`simple_return`), not a time-weighted
      return — matching the existing code comment's own language at `app.py:840-843`
      ("~5 pp when cash flows exist").
- [x] **AC-21:** zero change to the underlying `portfolio_cr` VALUE computation
      (`app.py:844`) — display/disclosure-only, matching the audit's own framing
      that the CURRENT basis choice is deliberate and coherent with Composer's own
      display, only its lack of disclosure is the gap.

## Architecture
- **`analytics.py`** — `get_history_summary` (`:1987-2022`) re-routed through
  `_window_cutoff_date` (BL-5); `get_symphony_today_change`'s fallback branch
  (`:543`) swapped to ET (BL-6).
- **`app.py`** — comment-only fix at `:7406-7410` (BL-7); a new signal computation
  + surface for BL-8 (exact route/template TBD by implementer, likely the AI
  Advisor Overview panel or `reporting.py`'s EOD digest — NOT the 1-minute engine);
  tooltip/disclosure addition in the rendered comparison-row template context
  (BL-12, template-side).
- **`alpha_bot_execution.py`** — discoverability hardening ONLY at the `:1635`
  write site (BL-9); zero behavioral diff.
- **`database.py`** — documentation-only additions near `save_autotune_run`'s
  existing docstring block (BL-10, BL-11 AC-18).
- **`autotuner.py`** — one-line comment near `run_simulation`'s
  `total_guard_alpha` accumulation (BL-11 AC-17).
- **`templates/index.html`** — tooltip/disclosure markup near the
  "Cumulative · lifetime" label (BL-12).

## Edge Cases
- BL-5: a window with ZERO in-window files on either surface — both must still
  agree (both report the same honest empty state); `window=all`/omitted —
  unaffected by the cutoff-unification (both surfaces already agree at "all").
- BL-6: the fallback is dead code on BOTH current production call sites — the new
  test (AC-6) must exercise the fallback branch DIRECTLY (call the function
  without `trading_day`), not rely on discovering a live call site that omits it.
- BL-8: a BRAND NEW symphony with a single `autotune_runs` row — AC-11's
  "insufficient history" degrade applies; must not report "N weeks never adopted"
  for N=1 in a way that reads as an alarming multi-week pattern.
- BL-9: a symphony that TRANSITIONS from untriggered to triggered mid-run — the
  hardening must not interfere with the transition itself (the override's
  triggering condition, `bot_state[symphony_id].get("triggered")`, is unchanged).
- BL-10: a legacy row where one of the 6 columns is somehow non-NULL from a
  historical accessor no longer in use — the documentation note must not claim
  "always NULL," only "never populated by the current `save_autotune_run` path."

## Security Considerations
- All eight items are documentation, comment, or read-only-display changes;
  BL-8's new signal reads existing `autotune_runs` rows via already-approved
  read patterns (no new write path, no new credential/secret exposure).
- BL-9's hardening touches the live 1-minute execution path
  (`alpha_bot_execution.py`) — per Architecture Constraint #1 (no blocking I/O on
  the execution path) and the project's math-layer discipline, any non-comment-only
  hardening mechanism (e.g. a runtime assertion) must be reviewed by
  `risk-engine-specialist` for latency/correctness impact before merge, and must
  carry a golden-fixture test per the project's "every change to math layers
  requires a golden-fixture test" standard if it touches any value used in a
  decision (it should not, per AC-13, but the reviewer must confirm).

## Testing Strategy
- BL-5: new test proving byte-parity at a boundary-dated fixture between
  `/api/guard-alpha-summary?window=` and `/api/history/<days>` for a shared
  day-count token (extends `tests/app/test_guard_alpha_summary_windowed.py`'s
  existing `TestByteParityWithHistorySummary`-style pattern from
  `DE-GAS-COHERENCE-001` to the boundary case specifically).
- BL-6: unit test on `get_symphony_today_change` with `trading_day` omitted at a
  clock time where UTC/ET dates diverge (AC-6).
- BL-7: no test required beyond the existing `divergence_explainer.py` suite
  staying green (comment-only change) — optionally, a docstring/comment lint or a
  simple string-presence test if the team wants machine-enforcement against
  future re-staling (implementer's discretion, not required).
- BL-8: unit tests on the new streak/signal computation — a fixture with N
  consecutive non-adopted runs produces the correct count; a fixture with an
  "Adopted AI" run breaking the streak resets it; the insufficient-history degrade
  (AC-11).
- BL-9: a regression test proving `reporting.py`/`analytics.py`'s existing clean
  read path is unaffected (AC-14) — this should already exist; confirm it still
  passes and, if the hardening mechanism adds a NEW structural marker, add an
  assertion that a triggered symphony's `bot_state["current_return"]` write
  carries that marker.
- BL-10/BL-11: no behavioral test required (documentation-only) — a doc-presence
  check is sufficient if desired, not mandatory.
- BL-12: template-render test confirming the tooltip/disclosure text is present
  and correctly associated with the "Cumulative · lifetime" label.
- Consumer-suite discovery (house lesson,
  `feedback_consumer_suite_discovery_before_sufficiency`): before GREEN on BL-5 in
  particular, grep the whole tree for existing tests asserting
  `get_history_summary`'s CURRENT (pre-fix) boundary behavior — reconcile any that
  encode the old asymmetry as expected behavior.
- Both ruff gates green across all 8 items; full PR gate (CI, `/review`, PM's LIVE
  functional gate) applies to the bundle as a whole before merge, per this
  project's Merge & PR Workflow.

## Decisions
| Decision | Rationale |
|----------|-----------|
| One bundled PR for all 8 items | Each item is independently XS-S effort and P3/P4 severity (hygiene/defensive/info) per the audit's own prioritization — bundling avoids 8 separate PR-review round-trips for changes this small, matching the PM's explicit dispatch instruction. |
| BL-9's exact hardening mechanism left to implementer + risk-engine-specialist | The audit explicitly warns "do NOT naively delete the override — it also feeds live exit-decision inputs" and flags this as touching the live execution path; prescribing a specific mechanism (vs. describing the REQUIRED outcome) risks the plan dictating an unsafe implementation detail without the specialist's input. |
| BL-8's exact display surface left to implementer | The audit's backlog item says "S dashboard/reporting" without naming an exact route/template; forcing a specific surface here risks conflicting with how the AI Advisor Overview panel or EOD digest is already structured — implementer picks the most natural existing fit. |
| No schema migration for BL-10 | Project standard: "Schema migrations: additive-first, NULLable + DEFAULT, never destructive in one step." Dropping unused columns is a destructive schema change with no operational upside proportional to the risk — documentation is the correct fix at this severity. |

## Scope Boundaries
- **IN:** the 8 items exactly as scoped above (BL-5 through BL-12), each grounded
  in the audit's cited `file:line` evidence.
- **OUT:** BL-1 through BL-4 (separately scoped, higher-severity plans — this
  bundle is P3/P4 only); INV-1/INV-2 (open investigations, not code changes);
  BL-9's underlying reconciliation-vs-shadow-history math (already correct, per
  M4's own "read path is clean" finding — this bundle only hardens
  discoverability of the write-side footgun, never touches the math); any change
  to `math_engine.py`, `alpha_bot_execution.py`'s exit-decision logic, or any
  other file outside the 6 files enumerated in Architecture above.

## Shipped

**Status: shipped (pending merge), 2026-08-05.** All 21 acceptance criteria met (AC-1..AC-21, AC-19 satisfied via a documented team-lead-ruled disclosure-relabeling deviation from its literal wording — see below).

**Decision records:** BL-9 (AC-12/13/14, the live-execution-path item) ships as its own dedicated entry, `DE-AUDIT-BL9-001` — it touches `alpha_bot_execution.py` and was shipped under a distinct team-lead-ratification process (single-site proposal refined to a 3-site self-healing design after tracing the full write graph). The remaining 7 items (BL-5/6/7/8/10/11/12, AC-1..11 + AC-15..21) ship as one consolidated entry, `DE-AUDIT-BL5-12-001`, matching the bundle's own "one PR" framing.

**Commit chain (branch `fix/audit-bl5-12-hygiene`, worktree `.claude/worktrees/audit-bl5`):**
- `eca71013` — BL-9 marker shipped directly by `risk-engine-specialist` (bl5risk), ahead of a RED test landing first for this item, once the team lead ratified the refined 3-site mechanism (DE-AUDIT-BL9-001).
- `72e4cc0b` — RED tests for the 7-item bundle (BL-9 excluded, already shipped).
- `201425c4` / `ede1d3d7` — BL-9 retroactive regression tests (test-writer, independently re-verifying rather than accepting the implementer's own claim).
- `9b1f349b` — GREEN for the 7-item bundle.
- `f846bca1` — BL-8 render-completion follow-up, closing a team-lead-flagged AC-9/AC-10 gap (the streak signal was computed and stamped on the route response but not yet rendered anywhere — "the exact defect class this audit program exists to close").
- `b616d424` — sufficiency-review pin (BL-8's raw-baseline invariant + rendered-text-contract tests).

**Reviewer verdict:** `quant-code-reviewer` — APPROVE. **Test-writer sufficiency verdict:** all-SUFFICIENT (BL-5/BL-10/BL-11/BL-12 verified via non-vacuity demonstrations against the shipped GREEN rather than new tests; BL-8 tightened with 2 dedicated sufficiency-review test classes).

**AC-19 deviation, recorded explicitly per the letter's own "verify feasibility at implementation time" framing.** The AC's literal wording called for surfacing `avg_oos_alpha` "alongside" any operator-facing surface displaying the raw `oos_alpha` sum. A grep before assuming found exactly one such surface (`static/ai_advisor.js`'s per-symphony assessment block) but confirmed no `avg_oos_alpha` companion is persisted anywhere to surface alongside it — it is a local `autotuner.py` print-statement variable, never written to any table. Per the AC's own instruction not to invent a new display surface to satisfy it, the letter's intent (disclose the convention where the raw number is shown) is satisfied instead by relabeling the JS literal in place (`'OOS alpha: <code>'` → `'OOS alpha (cumulative sum across triggered days): <code>'`). Team-lead-ruled acceptable; see `DE-AUDIT-BL5-12-001` in `DECISIONS.md` for the full record.

**Consumer-suite discovery caught two pre-commit collisions** (house lesson, `feedback_consumer_suite_discovery_before_sufficiency`): `tests/reporting/test_dsr_surfacing.py::TestAutotuneRunsApiRoute`'s bare-JSON-array pin on `/api/autotune-runs` (informed BL-8's per-row-stamp design over a new envelope object) and `tests/app/test_dollar_saved_display_contract.py::TestCumulativeRowNamesItsBasis`'s no-nested-tags regex on `.vs-row-label` (informed BL-12's sibling-span design). Both caught by `bl5impl`'s pre-GREEN discovery grep, not later by CI.

**Fresh authoritative test count, HEAD `b616d424` (independently re-run by the doc-writer, not re-quoted):**
```
python -m pytest tests/analytics/test_bl5_window_cutoff_unification.py tests/app/test_guard_alpha_summary_windowed.py tests/analytics/test_bl6_today_change_et_fallback.py tests/app/test_bl7_divergence_explainer_comment_accuracy.py tests/app/test_bl12_cumulative_lifetime_cr_disclosure.py tests/autotuner/test_bl11_oos_alpha_sum_convention.py tests/app/test_bl11_ai_advisor_oos_alpha_label_disclosure.py tests/database/test_bl10_dead_autotune_columns_documented.py tests/analytics/test_bl8_never_adopted_streak_signal.py tests/app/test_bl8_streak_render_and_raw_baseline.py tests/execution/test_bl9_shadow_return_override_marker.py tests/js_syntax/test_js_syntax.py -n0
```
— 62 passed. `tests/execution -n0` (BL-9's own broader suite): 426 passed. Both ruff gates clean on all touched Python files.

**Reference:** `DE-AUDIT-BL9-001` and `DE-AUDIT-BL5-12-001` in `DECISIONS.md`; `docs/audit/TWO-WEEK-REVIEW-2026-08-04.md` §4 D3/M3/D6/T3/M4/T5/T6/D7 and §6 BL-5..BL-12 (annotated with "Fixed by" pointers to both entries in the same doc pass).
