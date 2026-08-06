# Feature: Render Account-Basis Honesty Markers + Fix Freshness Stamp (BL-4)
Status: shipped (pending merge)
Created: 2026-08-04
Shipped: 2026-08-05 (`DE-AUDIT-BL4-001`, HEAD `b82de309`, branch `fix/audit-bl4-account-basis-render`)
Source: `docs/audit/TWO-WEEK-REVIEW-2026-08-04.md` §4 Findings D1/D2/D4, §6 Backlog BL-4 (commit `ca7f2beb`)

## Summary
The backend already computes and serializes honest account-basis markers —
`portfolio_strip["basis"] = "value_weighted"` (Tier-2 floor, fires when either TC or
CR has no basis at all: `app.py:1749-1750` live, `:2382-2383` frozen) and
`portfolio_strip["account_basis_stale"] = True` +
`portfolio_strip["account_basis_as_of"]` (Tier-1 stale-last-good stamp:
`app.py:1754-1758` live, `:2385-2387` frozen) — but they have **zero consumers**
anywhere in `static/index.js` (confirmed by direct grep: the ONLY reads of
`data.portfolio_strip` fields in the comparison-row render path are
`ps.today_change`/`ps.cumulative_return` at `static/index.js:938/943`, inside
`updateComparisonRows(data)`). The dashboard's ONLY freshness stamp —
`#hero-data-as-of` — is populated from `portfolio.data_as_of`
(`static/index.js:1249`), which traces back to `_data_as_of`
(`app.py:1699-1718`), itself derived from `last_successful_cycle_at` (the ENGINE
cycle timestamp, `app.py:1703`) — NOT the account-fetch time. So on an account-fetch
timeout, the operator sees a stale account today-change/return figure sitting under
a fresh-looking engine-cycle timestamp with zero indicator that anything is stale —
this is the operator's actual reported "+0.16 bot vs -2.02 account" confusion.

**Correction to the earlier PM lead framing (D2, already resolved by the audit —
included here for the implementer's context):** the "timeout falls to the
value-weighted floor" mechanism is only reachable BETWEEN a daemon restart and the
first successful account fetch. `_account_totals_last_good` is a plain dict
(`app.py:574`), reassigned only on success (`app.py:864`), never cleared — so after
the daemon's first success (droplet typically up for days), a single subsequent
timeout falls to Tier-1 STALE (last-good values + `account_basis_stale=True`), not
the Tier-2 VW floor. The "sticky last-good" mechanism is ALREADY implemented
correctly on the backend — the ONLY missing piece is rendering it. This plan is
purely additive/frontend; no backend computation changes.

**D4 (compounding factor):** the "Bot" column in the Today/Cumulative comparison
rows (`templates/index.html:921/947`, `static/index.js`'s `updateComparisonRows`)
is a shadow/dry-run simulation figure, rendered with no qualifier distinguishing it
from a live account figure — compounding the D1 confusion when a stale-but-unmarked
account figure sits next to it.

## Acceptance Criteria
- [x] **AC-1 — render the stale/VW chip on the account-basis comparison rows.**
      `updateComparisonRows(data)` (`static/index.js:932-...`) reads
      `ps.basis`/`ps.account_basis_stale`/`ps.account_basis_as_of` (currently ZERO
      references — confirmed by grep) and, when `ps.account_basis_stale` is truthy
      OR `ps.basis === "value_weighted"`, renders a visible chip/badge on the Today
      and Cumulative rows ONLY — the two rows whose values derive from the
      account-basis fields (`ps.today_change`/`ps.cumulative_return`). The Max DD
      row (`ps.max_drawdown`) is NOT account-basis-derived and must never render
      this chip.
- [x] **AC-2 — two distinct honest labels, never conflated.** The chip's
      text/tooltip distinguishes: (a) STALE — `ps.account_basis_stale` true,
      discloses the as-of time from `ps.account_basis_as_of`; from (b)
      VALUE-WEIGHTED FLOOR — `ps.basis === "value_weighted"` with
      `account_basis_stale` NOT set (the narrow daemon-restart-window case per D2),
      which has no as-of timestamp to disclose and must not fabricate one.
- [x] **AC-3 — dedicated account-fetch freshness indicator.** A NEW element
      (distinct from `#hero-data-as-of`, which stays anchored to the engine-cycle
      timestamp per its existing, unrelated contract) reflects
      `ps.account_basis_as_of` when the account basis is stale, so an operator can
      see "account data as of HH:MM" separately from "engine cycle as of HH:MM"
      when the two diverge.
- [x] **AC-4 — "Bot" simulation qualifier.** The "Bot" label
      (`templates/index.html:921/947`'s literal `"Bot "` prefix, rendered
      server-side; also referenced in `static/index.js`'s comparison-row builder)
      gains a qualifier disclosing it is a simulated/dry-run figure (a `title=`
      tooltip attribute or an adjacent small-text qualifier — implementer's choice
      of exact presentation) — text/attribute change only, zero change to which
      underlying value (`dry_run`) is displayed.
- [x] **AC-5 — zero backend computation change.** `_compute_portfolio_strip()`'s and
      the frozen `get_state()` closed-branch's computation of `basis` /
      `account_basis_stale` / `account_basis_as_of` (`app.py:1749-1758` live,
      `:2382-2387` frozen) are byte-unchanged — per D2, this mechanism is ALREADY
      correct; this fix is render-only.
- [x] **AC-6 (optional, operator-gated) — reduce stale-flicker frequency.**
      `_ACCOUNT_TOTALS_HTTP_TIMEOUT_S` (`app.py:579`, currently `10`) MAY be raised
      toward `30` to reduce the frequency of transient timeout-driven staleness
      (observed ~21 timeouts/day, ~1.5% of per-minute fetch attempts, 0 in the most
      recent 24h at audit time) — flagged explicitly OPTIONAL per the audit's own
      "optionally" framing; not required for AC-1 through AC-5 to close the finding.

## Architecture
- **`static/index.js`** — `updateComparisonRows(data)` (`~:932-1000`) already
  destructures `var ps = (data && data.portfolio_strip) || {}` at its top; extend
  it to read the 3 currently-unused fields and toggle chip visibility/text per row.
  A new small render helper (e.g. `renderAccountBasisChip(row, ps)`) keeps the
  existing function readable, following this file's established pattern of small
  named render helpers (`setPosNeg`, etc.).
- **`templates/index.html`** — new (initially-hidden or JS-populated-only) chip
  elements adjacent to the Today/Cumulative rows (`~:920-949`), following the
  existing `data-testid` convention used throughout this template; a new
  freshness-indicator element near the comparison-rows section, structurally
  distinct from `#hero-data-as-of` (which lives in the hero-chart legend region,
  `~:1054` area, per `static/index.js:1247-1250`).
- **`app.py`** — no route/computation change (AC-5). Verify at implementation time
  that `portfolio_strip` (containing `basis`/`account_basis_stale`/
  `account_basis_as_of`) already reaches the client via the SAME `/api/state`
  response `updateComparisonRows` already consumes (`data.portfolio_strip` per the
  existing `var ps = ...` line) — no new route or field-plumbing should be needed
  since these fields are already serialized into the strip dict returned today.

## Edge Cases
- `basis === "value_weighted"` on a fresh (non-stale) daemon-restart window (D2's
  narrow reachable case) — chip renders "value-weighted basis" wording, NOT
  "stale," since no `account_basis_as_of` timestamp exists yet for this case.
- `account_basis_stale` true with the backend's own as-of fallback
  (`app.py:1756-1758`'s `datetime.now(_ET)` fallback when
  `_account_totals_last_success_at` was never set) — the chip renders whatever
  timestamp the backend already provides; no client-side special-casing needed,
  the backend already handles its own fallback.
- Neither flag present (the common, healthy-fetch case) — no chip renders at all;
  zero visual change from today's dashboard.
- The frozen/closed-market `get_state()` branch (`app.py:2382-2387`) — the SAME
  chip logic applies since `updateComparisonRows` consumes whichever
  `portfolio_strip` shape `/api/state` returns, live or frozen, without needing to
  distinguish the two branches client-side (they already serialize the same field
  names).

## Security Considerations
- No new input surface — this renders already-server-computed, already-
  authenticated-route fields (`GET /api/state` is covered by the global auth hook).
  No `innerHTML` with interpolated external-origin strings — use `textContent`/DOM
  APIs per this codebase's established XSS-hygiene convention (see
  `DE-GAS-COHERENCE-001`'s Security Considerations for the precedent this repo
  follows for similar display-layer JS work).
- AC-6 (the optional timeout bump) changes only how long the daemon waits on a
  Composer GET before falling back to last-good/floor — no credential, auth, or
  write-path change.

## Testing Strategy
- JS body-extraction test (this repo's no-jsdom idiom, mirroring
  `tests/dashboard/test_window_picker_wiring.py` and
  `tests/app/test_dollar_saved_panel_sign_coherence.py`'s established pattern) —
  assert `updateComparisonRows`'s source text references
  `ps.account_basis_stale`/`ps.basis`/`ps.account_basis_as_of` and toggles a
  chip/element accordingly; a SEPARATE test for the "Bot" qualifier text/attribute.
- Template test asserting the new chip + freshness-indicator elements exist with
  correct `data-testid`s and are guarded/hidden by default (no chip when neither
  flag is set).
- Non-vacuity control (house standard): a planted-positive fixture (stale=True with
  a known as-of timestamp) must produce a DIFFERENT rendered chip than a
  planted-negative fixture (no flags) — the test must actually distinguish the two,
  not merely check the code path exists.
- Consumer-suite discovery (house lesson): grep `tests/dashboard/`, `tests/ui/`,
  and `tests/app/` for existing assertions on `updateComparisonRows`'s current
  DOM/behavior shape before extending it, to avoid a stale conflicting assertion.
- Both ruff gates + the parametrized `node --check` JS-syntax test stay green.
- PM's LIVE functional gate (Merge Workflow step 4) — seed a stale/value-weighted
  `portfolio_strip` state (e.g. via a test fixture DB or a mocked Composer timeout
  against a running daemon) and visually confirm (Playwright or manual) the chip
  and freshness indicator render correctly on the real page — matching the
  precedent this project sets for display-coherence work (see
  `guard-alpha-saved-coherence.md`'s Testing Strategy).

## Decisions
| Decision | Rationale |
|----------|-----------|
| Frontend-only fix, zero backend computation change | Per D2's correction, the backend's stale/VW mechanism (`_account_totals_last_good`, the two-tier fallback) is ALREADY implemented correctly — the audit's own framing is explicit: "Backend already emits everything — frontend-only." |
| A NEW freshness indicator, not repurposing `#hero-data-as-of` | `#hero-data-as-of` has an existing, unrelated contract (anchored to `last_successful_cycle_at`, the engine cycle) established by `showConnectionLost()` (AC-8, 2026-06-23) and other consumers — overloading it to sometimes mean "account fetch time" would silently break that existing semantic for a different feature. |
| AC-6 (timeout bump) is optional, not a hard requirement | The audit itself frames it as "optionally... to reduce stale flicker" — it treats a symptom's FREQUENCY, not the underlying honesty gap AC-1 through AC-5 close; making it mandatory would conflate a nice-to-have tuning knob with the actual fix. |

## Scope Boundaries
- **IN:** client-side rendering of the 3 already-computed account-basis markers on
  the Today/Cumulative comparison rows; a dedicated account-fetch freshness
  indicator; a "Bot" simulation qualifier; the optional timeout constant bump.
- **OUT:** any change to HOW `basis`/`account_basis_stale`/`account_basis_as_of`
  are computed (`app.py:1749-1758`/`:2382-2387` — already correct per D2); the
  existing `#hero-data-as-of` element's engine-cycle semantics (untouched); the Max
  DD comparison row (not account-basis-derived, must never gain this chip); any
  Composer API client change beyond the optional timeout constant; the server-render-
  clock staleness gap and fetch-error-silent-catch gap documented separately in
  `.claude/live-dashboard-reality-audit.md` (a different, already-tracked defect
  class per that document, not conflated with this cycle).

## Shipped

Shipped 2026-08-05 as `DE-AUDIT-BL4-001` (branch `fix/audit-bl4-account-basis-render`, worktree `.claude/worktrees/audit-bl4`, base `origin/main` @ `f2c1ebfe` / #122). Commit chain: `8d89f421` (RED, 32 tests) -> `d91caf0b` (GREEN) -> `b82de309` (sufficiency-review pin, +2 tests). 34/34 tests green (`-n0`, this file only), both ruff gates green, per `b82de309`'s own commit message.

**AC-1 through AC-4** shipped exactly as specified: `renderAccountBasisChip()`/`renderAccountBasisFreshness()` in `static/index.js`, called from `updateComparisonRows()` on every poll; two new hidden `data-testid="comp-{today,cumulative}-basis-chip"` spans plus a hidden `#account-basis-as-of` element in `templates/index.html`; a `title=` qualifier on both "Bot" spans. Priority rule for the both-flags-true edge case (STALE wins) was approved by the team lead during RED-plan review, not left to implementer discretion.

**AC-5** — the plan's own architecture section anticipated no backend change would be needed; confirmed true. `TestBackendComputationByteUnchanged` (6 tests) pins the 6 known assignment lines (live + frozen) as a regression guard.

**AC-6 — [PM-ASSUMED] ruling:** the plan explicitly framed this as optional/operator-gated ("MAY be raised... not required for AC-1 through AC-5 to close the finding"). Under the project's full-autonomy directive, the PM ruled it in-scope for this cycle (a one-line constant-value change, no new codepath) rather than deferring it to a separate operator-gated cycle. `quant-code-reviewer` flagged a tradeoff during review — the timeout gates the background scheduler thread (`_refresh_account_totals()`), not the 1-minute engine dispatch path, but a slow Composer call can now block that thread up to 30s instead of 10s. This tradeoff is recorded, not disputed; the audit's own observed timeout frequency (~21/day, 0/24h at audit time) motivated the change.

**Sufficiency-review addition beyond the plan:** `TestStaleToHealthyTransitionResetsChipsAndFreshness` (2 tests, `b82de309`) — not in the original A/C list, added because `bl4test`'s post-GREEN adversarial pass identified that every RED-phase test called `updateComparisonRows` exactly once per scenario, which could not distinguish "renders correctly" from "renders correctly but never resets on a later healthy poll" (the DOM persists across polls in production, unlike a fresh per-test render). Authorized directly by the team lead per the same precedent BL-3 set (`97b900ff`).

**Verdicts:** `quant-code-reviewer` — APPROVE (as reported to the team lead; see `DECISIONS.md` for the full record and the caveat that this doc-writer did not independently re-run the review). Test-writer sufficiency verdict not yet formally relayed to this doc-writer as of this doc pass.

See `DE-AUDIT-BL4-001` in `DECISIONS.md` for the full record.
