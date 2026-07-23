# Feature: Panic-Stop Liquidation Confirmation (F-003)
Status: ready
Created: 2026-07-20

## Summary
The panic-stop liquidation background thread `perform_account_liquidation` (`app.py:3392`, spawned by `POST /api/sell_account`) logs `f"Liquidated {name} (HTTP {sell_resp.status_code})"` **unconditionally** — a 429/400/422/500 reject still reads as a false "Liquidated" success, with no status check (contrast `alpha_bot_execution.py:execute_sell_to_cash` which checks `status_code in (200,201,202)`). Worse, the per-symphony sell POST has **no own try/except**: the outer `except` (`app.py:3398`) wraps the WHOLE loop, so one symphony's raise silently abandons every remaining symphony's liquidation. During a real emergency the console/journal print is the operator's ONLY per-symphony ground truth for "did the account actually go to cash" — a false "Liquidated" under a plausible Composer 429 (market-stress rate-limiting is likely in exactly the scenario that triggers a panic-stop) could make the operator stand down while real money is still exposed. **CONDITIONAL-CRITICAL / must-fix-before-live** (currently unreachable — the route returns the dry-run branch before spawning the thread when `LIVE_EXECUTION` is False; the droplet is `LIVE_EXECUTION='False'`). This cycle fixes the confirmation + error-isolation ONLY — it does NOT change which symphonies liquidate, the sell endpoint/payload, or the live-mode gating.

## Acceptance Criteria
- [ ] AC-1: a non-2xx sell response (429/400/422/500) logs an explicit **failure** line (`"LIQUIDATION FAILED {name} — HTTP {code} — {text[:200]}"`), NOT "Liquidated". Only `status_code in (200,201,202)` logs the success line.
- [ ] AC-2: each per-symphony sell attempt is wrapped in its OWN try/except — one symphony's failure (an exception OR a non-2xx status) does NOT abort the remaining symphonies; **every** symphony in the queue is attempted regardless of a prior failure.
- [ ] AC-3: the function captures a STRUCTURED per-symphony outcome (name → {ok: bool, status/reason}) so the operator has complete per-symphony ground truth even under partial failure — never a single opaque "Liquidation Error: {e}" that hides which symphonies did/didn't liquidate.
- [ ] AC-4: on a MIX of success + failure, the successes still log success AND each failure is individually reported — no all-or-nothing.
- [ ] AC-5 (no-behavior-change guard — HARD): the fix changes ONLY confirmation/logging/error-isolation. The SET of symphonies liquidated, the sell endpoint + payload, and the `live_mode` gating (a real dry-run still no-ops before the thread spawns) are byte-unchanged. NO change to trade behavior.
- [ ] AC-6 (regression guard): a happy-path all-2xx liquidation logs the success line per symphony exactly as before.

## Architecture
- `app.py:3392-3399` `perform_account_liquidation(account_id, key, secret, live_mode)` — the per-symphony sell loop + the unconditional `print` + the loop-wide `except`. Restructure: per-symphony try/except; status-code branch (success vs FAILED) around the log; accumulate a structured outcome list.
- The sell POST client call (Alpaca/Composer) is UNCHANGED — only its response (`status_code`, `text`) is now inspected. Reference the existing `execute_sell_to_cash` status-check + `text[:200]` truncation pattern for consistency.
- Test seam: mock the sell-response object (`status_code` + `text`) and the client call; assert on the captured log lines + the structured outcome.

## Edge Cases
- 429 on one symphony mid-loop (rest must still be attempted).
- 500 / 4xx on one symphony; a raised exception (network/timeout) on one symphony.
- ALL symphonies fail; ALL succeed; a mix.
- empty symphony list (no-op, no crash).
- `text` longer than 200 chars → truncated to `text[:200]` (no unbounded log).

## Security Considerations
- Truncate response bodies to `text[:200]`; NEVER log API keys/secrets or full response bodies. No new external input (the sell path is unchanged). The structured outcome must not carry secrets.

## Testing Strategy
- **RED (quant-test-writer, adversarial):** mock the sell client so a given symphony returns 200 / 429 / 500 / raises. Tests: (1) non-2xx → FAILED line, not "Liquidated" (must fail on origin/main — currently unconditional); (2) one failure does NOT abort the rest (all symphonies attempted); (3) structured outcome captures every symphony's result; (4) mixed success/failure both reported; (5) AC-5 no-behavior-change (same symphonies attempted, same endpoint/payload — assert the client was called once per symphony with unchanged args); (6) golden all-success. Fixtures = the real sell-response shape (status_code + text). NO live orders, NO live API, NO live DB; `-n0` only.
- Both ruff gates (`ruff format --check` + `ruff check`) before GREEN.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Fix confirmation + isolation + structured outcome; DEFER a dashboard UI to display it | The corrected per-symphony LOG line already restores the operator's ground truth (the report calls the console print the operator's only ground truth); a full dashboard surface for the outcome is a separable follow-up, not the CONDITIONAL-CRITICAL. |
| No retry/backoff added this cycle | The fix spec requires correct-detection + isolation, not new retry logic; a panic-stop retry/backoff on 429 is a separable robustness enhancement (noted, not required). |
| composer-alpaca-integration implements | It owns the sell/liquidation external-API response semantics + the `execute_sell_to_cash` status-check reference pattern. |

## Scope Boundaries
- **IN:** status-code check before the success log (AC-1); per-symphony try/except so one failure doesn't abort the queue (AC-2); structured per-symphony outcome capture (AC-3/4); the no-behavior-change guard (AC-5).
- **OUT:** a dashboard UI element to DISPLAY the per-symphony liquidation outcome (separable follow-up); adding retry/backoff; ANY change to trade behavior / which symphonies liquidate / the sell endpoint or payload / the `LIVE_EXECUTION` gating; F-013/F-023/other findings.
