# Feature Plan — Advisor Suite Fixes (post-audit)

**Status: ready**
**Branch:** `fix/advisor-suite` (off origin/main `5f9fa942`)
**Ships:** advisory-only (no LIVE_EXECUTION / no trade path) → DIRECT to origin/main after the PM live-UI gate, NO PR. Then droplet deploy (git pull + daemon restart).
**Origin:** `ADVISOR-AUDIT-VERDICT.md` (2026-07-13 audit) — operator caught the PM over-claiming a comprehensive audit. Every AC below is a PM-verified defect. **Every fix must be proven FROM THE RENDERED UI (a Playwright screenshot the PM reads) — never DB/unit-test-only.**

## Acceptance Criteria
- **AC-1 (Strategy Builder renders its result in-place):** After the operator runs the on-demand Strategy Builder (Strategy Builder tab → select symphony → Run), the tab shows the run's OUTCOME without navigating away: survivors (if any) as cards, the rejected candidates (count + expandable), the n_candidates evaluated, and the FDR threshold. A 0-survivor run shows an explicit honest status ("Evaluated N candidates — 0 passed the gate", NOT a blank/redirect). An engine error shows a message (the route already returns `{"error": "strategy-builder-error"}`). **The unconditional `window.location.href='/ai-advisor'` at `static/ai_advisor.js:749` is removed** — the response JSON is rendered, not discarded.
- **AC-2 (Strategy Builder run identifiability):** the operator can tell THIS run's results from prior history — cards/results are scoped to the just-completed run (via run-id/timestamp returned by the route, or by rendering the route's own response directly rather than re-fetching a limit-50 history slice at `database.py:1205` / `templates/ai_advisor.html:1968`).
- **AC-3 (Asset Swaps "Chat about this" button works):** `static/ai_advisor_asset_swaps.js:256-257` — the `onclick` handler must not contain unescaped double-quotes inside the double-quoted attribute (they truncate it). Match the correctly-escaped pattern used in `ai_advisor.js:298-301` / `ai_advisor_logic_changes.js`. The button opens the chat panel / routes to chat with the artifact. Verify via a live click.
- **AC-4 (Fundamentals uses the latest reporting period incl. 10-Q — OPERATOR-APPROVED 2026-07-13):** `ai_advisor.py:1041-1043` currently filters `form=="10-K"` BEFORE the sort-by-(end desc, filed desc). Change so the selection considers 10-K AND 10-Q entries and picks the most recent period. AAPL must resolve to the ~2026-03 10-Q period, not the 2025-09 10-K. Keep the union-of-candidate-tags logic. (This REVERSES the deliberate scope-out in `lens-fundamentals-vintage-fix.completed.md` — operator approved the reversal.) Existing `tests/ai_advisor/test_fundamentals_vintage.py` must be updated to the new contract (not weakened).
- **AC-5 (Badge icon is design-consistent):** the header candidate-alert indicator in `templates/_chrome.html` must use a monochrome inline SVG bell with `stroke="currentColor"` (inheriting `--studio-ink-dim`, matching the clock/engine-status) — NOT the `&#x1F514;` 🔔 emoji. Keep the red `--studio-neg` count-pill. Legible on `--studio-paper` in BOTH light + dark themes. No raw hex/emoji for the icon.
- **AC-6 (GDELT retries transient network errors):** `advisors/lens_gdelt.py:184-228` retries timeouts + connection errors (not only HTTP 429), mirroring `ai_advisor.py:406-486 _fetch_with_backoff`. Bounded retry; D-1 never-raises preserved.
- **AC-7 (VERIFY the untouched surfaces — fix only if broken):** drive guard-alpha, the weekly-scheduled suggestion surfacing (distinct from on-demand), and whether the Overview observations feed filters non-ADOPT_CANDIDATE rows — end-to-end on the running app. Report WORKS/BROKEN with a screenshot each. If broken, add an AC + fix.

## Architecture / Scope
- Frontend: `static/ai_advisor.js` (AC-1/AC-2 render), `static/ai_advisor_asset_swaps.js` (AC-3), `templates/_chrome.html` (AC-5), possibly `templates/ai_advisor.html` (AC-1 render target). The route `POST /ai-advisor/strategy-builder/run` (app.py:4589) already returns the full JSON — AC-1 is mostly frontend rendering of an existing response.
- Advisor/lens: `ai_advisor.py` (AC-4 fundamentals selection), `advisors/lens_gdelt.py` (AC-6 retry).
- Tests assert DESIGN CONTRACT not computed values (no raw-color assertions; assert SVG+currentColor + token usage; assert the response is rendered not discarded; assert latest-period selection on a fixture with both 10-K + 10-Q).
- Do NOT `ruff format` .json fixtures. Do NOT add per-file `node --check` (extend tests/js_syntax). Bounded `-n0` tests only (box crashes on -n>4).

## Testing / Gate
- TDD RED-first for AC-1/AC-3/AC-4/AC-6. AC-2/AC-5 design-contract tests. Bounded `-n0` + ruff.
- **PM LIVE-UI GATE (non-negotiable):** a ux-expert drives the running app (local instance + droplet DB copy) and screenshots EACH fixed surface; the PM personally Reads the screenshots and confirms before ship. Never DB/green-only.
- Ship advisory → direct origin/main (SHA-guard) → droplet git pull + daemon restart → operator sees it.

## Scope Boundaries
- Fundamentals "levels-only, no valuation/PE" is a KNOWN thin-lens limitation — NOT in scope here (separate enhancement); this cycle only fixes the STALENESS (AC-4).
- No trade-path changes; advisory-only throughout.
