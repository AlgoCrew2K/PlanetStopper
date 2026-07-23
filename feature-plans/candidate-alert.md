# Feature Plan — Header Candidate-Alert Indicator

**Status: ready**
**Branch:** `feature/candidate-alert` (off unified `main` `1a40467c`)
**Ships:** DIRECT to origin/main after PM live E2E gate (advisory-only UI — no `LIVE_EXECUTION`, no trade path — NO PR per operator rule).
**Origin:** Operator request (2026-07-12): "some sort of alerting system on the actual UI, probably in the header somewhere so it's always visible regardless of the screen I'm on, that indicates there's valid new candidates to view and routes me appropriately when I click on the alert, otherwise I'll never actually know if this is working."

## Summary
The weekly suggestions job (`advisors/weekly_suggestions_scheduler`) produces advisory candidate suggestions (asset-swap / logic-change / strategy-builder), gated by the strict FDR/PBO/SPY-OOS overfitting discipline. Most candidates are correctly rejected (no benefit); survivors are rare and valuable. Today those persist as `advisor_observations` that only appear if the operator happens to open the AI Advisor tab — so a week that produces a real winner is easy to miss, and a week that ran-but-rejected-everything is invisible (looks broken). This feature adds an always-visible header indicator that (a) badges when there are NEW VALID (survivor) candidates worth viewing, (b) makes the weekly run's status visible even at zero survivors (so the operator knows it's alive and working), and (c) routes to the candidates on click.

## Acceptance Criteria
- **AC-1 (persistent, all screens):** A header indicator visible on EVERY authenticated dashboard screen — main dashboard (`index.html`), AI Advisor (`ai_advisor.html`), History (`history.html`), Performance (`performance.html`). [HOW is the team's call: extract a shared base template / header partial, or inject the same partial into each page's header. Prefer a single shared partial over per-page duplication.]
- **AC-2 (valid-candidate badge):** The indicator shows a numeric badge = count of NEW, UNVIEWED, VALID candidate suggestions. "VALID" = a weekly-suggestion `advisor_observation` (roles `ASSET_SWAP`/`LOGIC_CHANGE`/`STRATEGY_BUILDER`, `is_advisory_only=1`) that SURVIVED the gate — i.e. its verdict is NOT a rejection (`verdict != 'REJECT_VETO_FAILED'` and `rejection_reason` is null/absent). Rejected-for-no-benefit candidates do NOT count (they are the expected common case; alerting on them would be noise). Badge hidden/zero when count is 0.
- **AC-3 (run-status visibility — the "know it's working" case):** The indicator also surfaces the LATEST weekly-run status: ran-when (timestamp of the most recent weekly-suggestion batch), N candidates evaluated, N survivors. A run that produced 0 survivors is still visible here (e.g. tooltip/dropdown on the indicator: "Weekly run <date>: 42 evaluated, 0 passed the gate"). Honest empty-state when no weekly run has ever occurred ("no weekly run yet").
- **AC-4 (click routes):** Clicking the indicator routes to the AI Advisor view showing the weekly suggestions, scrolled/filtered to the NEW valid candidates (reuse the existing surfacing from advisor-rewire AC-A2 — `_ADVISOR_ROLES` Overview feed / the relevant tab). No new bespoke candidate page required.
- **AC-5 (viewed-marker):** "New/unviewed" is tracked so the badge CLEARS once the operator has viewed the new candidates. Mechanism (team's call, keep minimal): e.g. a "candidate-alert last-viewed" marker (timestamp or last-seen observation id) persisted in state; the badge counts survivors newer than the marker; viewing the AI Advisor candidates (or an explicit acknowledge) advances the marker. The mark-viewed write is CSRF-protected and advisory-only (NOT in `_SETTINGS_WRITE_ALLOWLIST`, no `LIVE_EXECUTION`).
- **AC-6 (honest degradation):** No weekly run → honest "no run yet" state, no error. Empty/malformed observation data → indicator degrades to no-badge, never raises, never blocks page render. Dashboard read-only-on-non-write-path invariant preserved.

## Architecture
- **Backend:** a read-only `GET` endpoint (e.g. `GET /api/candidate-alert`) returning `{new_valid_count, last_run: {ran_at, evaluated, survivors} | null}` — queries `advisor_observations` for the weekly-suggestion roles: survivor count (verdict not a rejection) newer than the viewed-marker, plus the latest run's aggregate (batch by created_at / a run marker). READ-ONLY SQLite on this path. A CSRF-protected `POST` mark-viewed advances the viewed-marker (a small state write via a new `database` accessor + optionally a migration for the marker, additive/NULLable).
- **Frontend:** a header partial (shared across the 4 screens) with the indicator; `static/*.js` fetches `/api/candidate-alert` (401-guarded like the existing `fetchGuardAlphaSummary` pattern), renders the badge + run-status, wires click → route to AI Advisor + mark-viewed. Reuse the existing SSE/poll cadence if present; otherwise a light poll.
- Mirror existing dashboard patterns (guard-alpha-panel / dollar-saved-headline fetch pattern in `static/index.js`; auth-gate + CSRF infrastructure in `app.py`).

## Edge Cases
- No weekly run ever → "no run yet", no badge, no error.
- Latest run all-rejected (0 survivors) → no badge, but run-status shows "N evaluated, 0 survivors".
- Viewed-marker unset (first ever) → treat all current survivors as new (or none — team pins via RED); advancing on view must be idempotent.
- Multiple screens open → badge state consistent (server-derived, not per-tab).
- Survivor observation with missing/odd verdict field → conservatively NOT counted as valid (fail-closed: don't badge a non-survivor).

## Security Considerations
- Advisory-only: no `LIVE_EXECUTION`, no trade path. The mark-viewed write is CSRF-protected, NOT in `_SETTINGS_WRITE_ALLOWLIST`, and touches only the viewed-marker (not settings/trades).
- The count endpoint is read-only (dashboard read-only-on-non-write-path invariant). Auth-gated by the existing `_auth_before_request` hook. No secret exposure.

## Testing Strategy
- TDD RED-first (Toxic Pair). Hermetic — no live network; seed `advisor_observations` fixtures (survivor + rejected rows) to assert counts. Bounded `-n0` through `ALPHABOT_TEST_MEM_CAP_GB` — NEVER full/-n>4. `ruff format` + `ruff check` before commit. Do NOT add per-file `node --check` (extend the parametrized JS-syntax module). Do NOT `ruff format` any `.json` fixtures (corrupts them).
- RED pins: (AC-2) survivor vs rejected classification — a rejected row does NOT increment the badge; a survivor row does; (AC-3) run-status aggregates the latest batch incl. the 0-survivor case; (AC-5) mark-viewed advances the marker and clears the badge; (AC-6) no-run and malformed-data honest states, never raises; (AC-1) the indicator renders on all 4 screens (Flask test client per screen).
- **PM live E2E gate (real data):** seed/observe a real survivor observation → the badge appears with the right count + routes; a rejected-only run → no badge but run-status visible; mark-viewed clears it. Verify on the reconciled code (both dashboard + advisor present).

## Scope Boundaries
- No new bespoke candidate-review page — click routes to the EXISTING AI Advisor surfacing.
- No accept/apply action on candidates from the alert (advisory-only; consistent with the existing read-only + chat-discuss pattern).
- No push/email/Discord alerting — in-dashboard header indicator only (Discord digest is a separate existing surface).
- [PM-ASSUMED] Trigger = survivors-only for the badge + run-status for the alive-signal; if the operator wants a badge on every completed run regardless of survivors, that's a one-line trigger change.
