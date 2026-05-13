# Runbook: Composer API Rejection Diagnostic

**When to use:** Operator sees a log line containing `[COMPOSER REJECTED]: HTTP {status_code}` or `[COMPOSER ERROR HTTP {status_code}]` from `alpha_bot_execution.py`.

**Background:** Per security cycle #27 (commit `88198e5`), the response body is no longer echoed in error logs (it may contain account-identifying or auth-debug strings). Only the HTTP status code is logged. This runbook documents the diagnostic escalation path when the status code alone is insufficient.

---

## Step 1 — Identify the call site

`[COMPOSER REJECTED]: HTTP {code}` originates from **one function only** in `alpha_bot_execution.py`:

| Call site | Function | Purpose |
|---|---|---|
| `execute_sell_to_cash` (line ~124) | POST `/deploy/accounts/{account-id}/symphonies/{symphony-id}/go-to-cash` | Forced exit on Guard Alpha trigger |

Exit-execution failures are operationally critical — capital is not exited.

**Note — polling failures use a different log pattern.** `fetch_symphony_stats` (lines ~82–96) does NOT emit `[COMPOSER REJECTED]`. Its failure format is:

```
Error fetching account {account_id}: HTTP {status_code}
```

(line ~93). Polling failures are recoverable — the next minute's poll retries automatically. If you see the polling-failure format, triage with the same Step 2 status-code table below, but the urgency is lower.

---

## Step 2 — Map HTTP status code to root cause

Per the Composer API baseline (`docs/research/composer/baseline__2026-05-12.md`), Composer documents these error codes:

| Status | Meaning (per Composer docs) | Common cause | Action |
|---|---|---|---|
| 400 | Invalid params | Malformed request body or query — bug in AlphaBot code | File bug; do NOT retry |
| 401 | Auth | Expired or rotated API key | Check `COMPOSER_KEY_ID` / `COMPOSER_SECRET` env vars on host; rotate if needed (single active key only — generating a new key revokes the old immediately per Composer's auth model) |
| 403 | Unauthorized market data access | Account lacks subscription tier needed | Check Composer plan tier on the account |
| 404 | Resource not found | Account ID or symphony ID typo, or symphony deleted | Verify IDs in env config; check Composer dashboard |
| 415 | Unsupported media type | Wrong Content-Type header | Bug in AlphaBot — file issue |
| 429 | Rate limit | Standard 1 req/sec exceeded (or 500 req/sec for backtest) | Wait + retry (AlphaBot handles this automatically per the 60s default `Retry-After` fallback) |
| 500 | Server error | Transient Composer-side issue | Auto-retries with backoff (1s, 2s, 4s, 10s); if persistent, check Composer status page |

---

## Step 3 — Verify via Composer dashboard

For rejections that aren't obvious from status code alone (e.g., a 4xx that doesn't map to one of the above, or repeated 5xx that don't auto-recover):

1. Log into `app.composer.trade` with the operator account
2. Navigate to the account that triggered the rejection (account ID is in the log line)
3. Check:
   - **Symphony status** — is it currently rebalancing? In a deploy window? Already in cash?
   - **Recent activity** — any other deploys queued or pending?
   - **Account-level constraints** — PDT flag? Margin restriction? Insufficient cash for a buy?
4. Composer's UI shows full error details that the AlphaBot logs no longer expose

---

## Step 4 — Common rejection scenarios (from production observations)

These are the most common `[COMPOSER REJECTED]` causes in practice. **None of them are AlphaBot bugs**; they're Composer-side state issues:

- **Symphony in rebalance window** — Composer rejects deploys (including `go-to-cash` and `liquidate`) while a scheduled rebalance is in flight. Wait for rebalance to complete; AlphaBot will retry on next tick.
- **Symphony already liquidated** — if `go-to-cash` was triggered while the symphony was already exiting from a previous tick. Verify via `symphony-stats-meta` polling; this is usually self-resolving.
- **Account suspended / under review** — Composer flag at the account level. Requires Composer support to resolve.
- **Stale account ID in env** — `COMPOSER_ACCOUNT_UUID` env var points to an account that no longer exists on Composer. Rotate env config.

---

## Step 5 — Escalation

If steps 1-4 don't resolve:

1. Collect the full minute's log output (the rejection line + surrounding context: which symphony, which Guard Alpha trigger fired, recent MC probabilities)
2. Cross-reference with Composer dashboard timeline for the same minute window
3. If still unexplained, contact Composer support via their dashboard; provide the timestamp + account ID + HTTP status code

---

## Why the body isn't logged

Per the [Composer API baseline](../research/composer/baseline__2026-05-12.md), Composer error response bodies have been observed to contain auth-debug fragments and account-identifying strings. Echoing `response.text` to stdout (which lands in operator logs, potentially shared workstation history, and any log-aggregation pipeline) was a secrets-hygiene risk.

Cycle #27 (merge `88198e5`) scrubbed these echoes file-wide. The diagnostic loss is real but acceptable:
- The status code is sufficient to triage 90%+ of rejections
- Operators have direct Composer dashboard access for the remaining cases
- The dashboard provides better detail than the API response body anyway

---

## Related code references

- `[COMPOSER REJECTED]` log format: `alpha_bot_execution.py:124` (`execute_sell_to_cash`)
- Polling error log format: `alpha_bot_execution.py:93` (`fetch_symphony_stats`)
- Status code mapping (Composer doc): `docs/research/composer/baseline__2026-05-12.md` § "Rate Limits & Throttling"
- Auth header construction (single-active-key model): `alpha_bot_execution.py:72-76` + `docs/research/composer/baseline__2026-05-12.md` § 2

---

## Related runbooks

- [`tzdata-missing-on-host.md`](tzdata-missing-on-host.md) — handles the `ZoneInfoNotFoundError` fallback documented in cycle #28 corrective
- [`optuna-recalibration.md`](optuna-recalibration.md) — handles the `optuna_studies.db` rename procedure after a calibration-shifting change (canonical trigger: VWAP fix task #24)
