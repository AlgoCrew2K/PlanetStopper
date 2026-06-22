# Planet Stopper — Security Review (read-only audit)

**Date:** 2026-05-31
**Auditor:** security-auditor (read-only; no code changed, no daemon run, no trade placed)
**Scope:** current `main` (HEAD `04db2e9`, "merge: M4 — AI Advisor logic-change proposals"). AI Advisor M1–M4 merged.
**Excluded from scan:** `.claude/worktrees/`, `.claude/audit-worktrees/` (stale forks).
**Secret redaction:** no secret value is reproduced in this report. Where a committed value is real, it is referenced by class + length only.

> **FOLLOW-UP REQUIRED — M5 advisor CHAT (NOT audited here).** The advisor chat (`advisors/advisor_chat.py` + a chat Flask route) is a NEW Anthropic LLM surface in-flight on the unmerged `advisor-m5` branch (`.claude/worktrees/advisor-m5/`). It introduces free-text operator input flowing to Anthropic — a materially larger prompt-injection / data-leakage surface than the structured config advisor. **A dedicated security pass against M5 is mandatory once it merges to main.** It is deliberately out of scope for this review.

---

## Executive Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 0 |
| HIGH | 2 |
| MEDIUM | 3 |
| LOW | 3 |
| INFO | 4 |

### Top risks (one line each)

- **HIGH — Real Discord webhook URL + account UUIDs committed to git history** (`.env`, 10 commits `c0ec631`→`ca48ea3`; untracked at `0228a37`). Composer/Alpaca/Anthropic API keys were NEVER real in history (always placeholder words). CONFIRMED.
- **HIGH — Unauthenticated `POST /api/settings` writes ANY key to `.env` with no allowlist** (`app.py:2079-2080`), incl. `LIVE_EXECUTION` and credential keys. Localhost-bound, so exploitable only by a local/proxied client. CONFIRMED.

There are **no hardcoded live API keys in source, tests, or fixtures**; credential loading, SQL parameterization, the LLM context allowlist, the advise-only API invariant, and log-redaction are all sound and test-guarded. The two HIGH items are git-hygiene and a missing write-side allowlist, not code-execution or key-exfiltration vulnerabilities.

---

## How the AI Advisor uses each key (key-usage trace — first priority)

### COMPOSER key — every advisor codepath

Credential source: `alpha_bot_execution.py:57-58` (`COMPOSER_KEY_ID`, `COMPOSER_SECRET` via `os.getenv`). Header builder: `get_composer_headers()` at `alpha_bot_execution.py:160-165` → `{"x-api-key-id": KEY_ID, "authorization": "Bearer <SECRET>", ...}`.

| Advisor codepath | Composer endpoint hit | Method | Scope | Evidence |
|---|---|---|---|---|
| M2 backtest client | `{COMPOSER_BASE_URL}/backtest` | POST | **READ-ONLY** (stateless simulation) | `advisors/composer_backtest_client.py:271,281,298` |
| M2 backtest (alt module) | `{COMPOSER_BASE_URL}/backtest` | POST | **READ-ONLY** | `composer_backtest.py:298,310,324` |
| M3 asset-swap engine | via `run_backtest` only | POST | **READ-ONLY** | `advisors/asset_swap_engine.py:50,551,556,727` |
| M4 logic-change engine | via `run_backtest` only | POST | **READ-ONLY** | `advisors/logic_change_engine.py:66,894,898,1095` |
| `ai_advisor.py` (config advisor) | **none** — never calls Composer | — | n/a | reads only state DB + symphony_logic |
| `symphony_logic.py` | `GET .../score` (read) | GET | READ-ONLY | (condensed-logic read) |

**Advise-only invariant — HOLDS at the API-call level (CONFIRMED).** Every advisor Composer call routes through `run_backtest` → `POST /backtest`, a stateless simulation endpoint. The only MUTATING Composer endpoint in the codebase is `execute_sell_to_cash` → `POST /deploy/accounts/{id}/symphonies/{id}/go-to-cash` (`alpha_bot_execution.py:258,263`). That function is **not reachable from any advisor module** (no advisor imports it; grep-confirmed no `/deploy`/`go-to-cash` in `advisors/`). It is gated by `is_live` and only invoked from the LIQUIDATE-confirmed panic route. So no advisor path can place a trade or mutate a live symphony.

- **Data through the Composer key:** the full symphony decision-tree (`raw_value`), capital (default $10k), fee/slippage/broker flags, `symphony_id`. No account credentials are in the request body; auth is header-only.
- **Rate-limit + timeout:** bounded exponential backoff `1→2→4→8s` clamped to `max_retries` (default 4), 429 honors `Retry-After`, per-request `timeout=_BACKTEST_REQUEST_TIMEOUT` (`composer_backtest_client.py:23-26,293,299,315-318`). No unbounded loops.
- **Exfiltration/logging:** `get_composer_headers()` return value (carrying the Bearer token) is NEVER logged — grep for header/authorization logging in production source returned empty. Debug logs emit URL + `symphony_id` + capital only (`composer_backtest_client.py:283-290`, `composer_backtest.py:312-319`). **No leak.**

### ANTHROPIC key — `ai_advisor.py` (config-knob advisor on main)

Credential source: `ai_advisor.py:426` (`os.getenv("ANTHROPIC_API_KEY")`); client built in `_build_client()` (`:415-436`), raises cleanly if unset. Model: `claude-opus-4-7` (`:55`), `max_tokens=2048` (`:56`), explicit `timeout=30.0s` (`:58,496`).

- **EXACTLY what is placed in the prompt (`assemble_advisor_context`, `:343-407`; `_build_messages`, `:439-455`):** a curated dict of — the 9-item param allowlist (definition + valid range + current live value + locked flag), Optuna OOS/train alpha evidence, volatility-regime numbers, the 125-day data-window note, risk invariants, role framing, and condensed symphony logic. **No secret, API key, account UUID, `.env` value, `LIVE_EXECUTION`, or PII enters the prompt.** Live values come from the `symphony_strategies` DB row, NEVER `os.environ` (`_read_current_strategy`, `:306-340`; docstring contract `:8-11,359-362`). This is an allowlist, not a denylist — anything not enumerated is structurally absent. CONFIRMED.
- **PII/holdings:** the condensed symphony logic is strategy structure (tickers/weights), not account balances or position dollar values. No account identifiers.
- **Response parsing/validation:** structured-output via Pydantic `ConfigSuggestionsResponse` (`:197-204,491-553`); malformed output degrades to `(None, error_msg)` and never raises (`:458-475`).
- **Can the LLM response drive an action? NO (CONFIRMED).** Defense-in-depth gates before any config write: (1) `enforce_suggestion_allowlist` structurally rejects any `config_key` not in the 9-item set — credentials, `LIVE_EXECUTION`, account UUIDs, methodology knobs cannot pass (`:599-625`); (2) engine recomputes risk direction, never trusts the model's self-report (`:628-687`); (3) `revalidate_suggestion_oos` runs the autotuner OOS gate, strict `>` (`:690-797`); (4) locked-var guard at the route (`app.py:2794`). Only after all gates does `/ai-advisor/accept` write — and only to an allowlisted param (`app.py:2751-2801`). The model emits suggestions; a human accepts; the OOS gate validates. No direct action.
- **Token/cost exposure:** capped at 2048 output tokens; on-demand (operator click), not on the 1-min cycle; failure is "no suggestion this click," zero engine impact.

---

## Findings by category

### 1. Secrets & Credentials (TOP PRIORITY)

**S-1 [HIGH, CONFIRMED] — Real Discord webhook URL + Composer account UUIDs committed to git history.**
`.env` was tracked across **10 commits** (`c0ec631` "Add files via upload" → … → `ca48ea3`), untracked at `0228a37` ("chore(security): untrack .env"). It is correctly absent from the working tree now (`git ls-files` shows no `.env`; `.gitignore:2` covers it). Across every historical revision:
- `DISCORD_WEBHOOK_URL` = a real `https://discord.com/...` value (length 36, consistent across all 10 commits) — **a live credential**: anyone who pulls history can post to the operator's Discord channel.
- `ACCOUNT_UUIDS` = real-looking Composer account identifiers (length 14).
- `COMPOSER_KEY_ID` / `COMPOSER_SECRET` / `ALPACA_KEY` / `ALPACA_SECRET` = the literal placeholder words `key`/`secret` (length 3/6) in **every** revision — **never real keys**. (Verified by classifying value content across all 10 SHAs, no secret printed.)
- `ANTHROPIC_API_KEY` = **never** present in committed `.env` (`git log -p -- .env` → 0 occurrences; the advisor postdates the untrack).

Evidence: `git log --all -- .env`; per-SHA value-class scan. **Remediation:** (1) **Rotate/delete the leaked Discord webhook** at Discord (it is in history forever short of a history rewrite); treat the account UUIDs as exposed. (2) If this repo is or will be public/shared, scrub `.env` from history (`git filter-repo`) — coordinate, as this is a force-push-class operation requiring explicit user go-ahead. (3) Add a pre-commit secret scanner.

**S-2 [LOW, CONFIRMED] — No `.env.example` / template committed.** Only a live (gitignored) `.env` exists; there is no checked-in template documenting required keys with placeholder values. Increases the chance of a future careless `git add` of the real file. **Remediation:** commit `.env.example` with placeholder values and document it in the README.

**S-3 [INFO] — Credential loading is sound.** All three classes load from env/`.env` via `python-dotenv` (`alpha_bot_execution.py:55-66`, `ai_advisor.py:426`, `synthetic_history.py:16-19`). No credential is hardcoded in any source module. `get_alpaca_headers`/`get_composer_headers` read module-level env constants. **Clean.**

**S-4 [INFO] — No hardcoded secrets in source/tests/fixtures.** Grep for `sk-`, `api_key`, `Bearer`, `token`, `password`, `secret`, account-ID patterns across non-`.claude` files returned only: (a) deliberate test **sentinels** designed to detect leakage (`sk-ant-LEAK_SENTINEL_xyz` `tests/ai_advisor/test_ai_advisor.py:389`; `FAKESECRET_…` `tests/integration/test_composer_alpaca_client_log_redaction.py:337`; `dummy-key-for-attr-check` `tests/ai_advisor/test_sdk_contract.py:332`); (b) placeholder values in fixtures. None are real. **Clean.**

**S-5 [INFO] — Fixture secrets are placeholders; backtest fixture has no PII.** `tests/fixtures/app/settings_env_snapshot.json` uses explicit placeholders (`test-…-placeholder`, `discord.com/api/webhooks/000000000000/test-placeholder-token`, `ACCT-001-INDIVIDUAL`). `tests/fixtures/composer/backtest_inline_v1.json` is labeled `_fixture_provenance: captured-from-producer`; its 46 UUIDs are **Composer internal symphony-node/asset identifiers used as JSON map keys** to numeric allocation weights (e.g. `"50c35084-…": 0.005` at line 737) — NOT account IDs, auth tokens, or PII. No `account_id`/`bearer`/`authorization`/`token` keys present. **Clean.**

**S-6 [INFO] — Settings GET masks all credentials.** `GET /api/settings` runs every credential key (incl. `ANTHROPIC_API_KEY`, Discord webhook, all three account UUIDs) through `_mask_secret()` which returns `""` regardless of value (`app.py:1951-1968,2035`). Account UUIDs are explicitly masked to defeat enumeration. Test-pinned (`tests/app/test_r8_settings_secrets_mask.py`). **Clean.**

### 2. External API boundary (Composer / Alpaca / Anthropic)

**B-1 [LOW, CONFIRMED] — URLs are constructed from a fixed `COMPOSER_BASE_URL` constant + path params; no SSRF surface.** Endpoints are f-strings over a constant base + IDs (`alpha_bot_execution.py:173,258`; `composer_backtest_client.py:271`). No user-supplied URL/host is ever fetched. Alpaca base URL comes from env (`synthetic_history.py:20`), operator-controlled, not request-controlled. TLS is via `requests` defaults (cert verification on). **No SSRF.** Minor: base URLs are not asserted to be `https://` in code — relies on the `.env` value being correct.

**B-2 [INFO] — Timeouts + bounded retries everywhere.** Composer: `timeout=10/15` + capped backoff + 429 `Retry-After` (`alpha_bot_execution.py:175,263,270-281`); backtest client/​module: explicit timeout + `1→2→4→8s` bounded backoff. Anthropic: explicit `timeout=30.0` (`ai_advisor.py:496`). Discord: `timeout=5/10` (`reporting.py:455,461,534`, `app.py:1893`). **No unbounded waits found.** Input validation of external JSON: parse failures are caught and degrade (`ValueError` guards at `alpha_bot_execution.py:180,311,353`). **Clean.**

### 3. LLM surface (`ai_advisor.py`)

**L-1 [INFO] — No data leakage to Anthropic.** Covered in the key-usage section: the prompt is a curated allowlist; no credential/account/PII/`.env` value reaches the model. CONFIRMED via `assemble_advisor_context` + `_read_current_strategy` + the module docstring contract.

**L-2 [LOW, POTENTIAL] — Prompt-injection vector via symphony name/logic is low-impact and contained.** The condensed symphony logic and `symphony_name` (operator/Composer-controlled strings) are serialized into the prompt (`_build_messages`, `:439-455`). A crafted symphony name could attempt to make the model emit a directive or a hallucinated `config_key`. **Impact is bounded to near-zero** because: the output is parsed as structured Pydantic (free-text directives are not actionable), `enforce_suggestion_allowlist` drops any non-allowlisted key, the engine recomputes risk direction, and the OOS gate must pass before any write. The model cannot emit a trade directive — there is no trade-execution sink on the advisor path at all. **Remediation (defensive):** none required for safety; optionally note in the prompt that field values are untrusted data. Re-examine seriously for M5 chat, where free-text input is the primary surface.

**L-3 [INFO] — LLM output never drives an action.** CONFIRMED (see key-usage section, gates 1–4).

### 4. Injection (SQL / command / template)

**I-1 [INFO] — SQLite is parameterized; the few f-string SQL sites are structurally safe.** Production f-string SQL in `database.py` interpolates only: (a) `{placeholders}` = computed `?,?,?` strings (`:363,2788-2791`); (b) `{where}` = fixed clause fragments each carrying `?` params, values bound separately (`:2767-2771`); (c) `write_telemetry_row` interpolates `{table_name}`/`{col_names}` ONLY after validating `table_name` against the `_WRITE_TELEMETRY_TABLES` allowlist and every column against `^[a-z_][a-z0-9_]*$` (`:2351-2372`), values always parameterized. No user value is ever concatenated into SQL. (f-string SQL in `tests/` uses hardcoded table-name constants — not a production concern.) **No SQL injection.**

**I-2 [INFO] — No command injection in the engine spawn.** `app.py:215-221` spawns `cmd = [sys.executable, "alpha_bot_execution.py"]` (+ static `--force` flag) via `subprocess.run(cmd, …)` — a fixed argv list, **no `shell=True`**, no user input in argv. No `os.system`/`os.exec`/shell string anywhere in production source. **Clean.**

**I-3 [LOW, POTENTIAL] — `POST /api/settings` symphony params are float-coerced but globals are not validated.** Symphony strategy params are coerced via `float(v)` (`app.py:2090`) — a non-numeric value raises and returns a 500 (no injection, just a coarse error). The `globals` write side has no value validation (see A-2). Jinja autoescaping is Flask-default-on; no `| safe` misuse observed on user data. POST routes have no explicit body-size limit (`MAX_CONTENT_LENGTH` unset) — DoS-grade only, and localhost-bound.

### 5. Authz / read-only boundary

**A-1 [HIGH, CONFIRMED] — `POST /api/settings` writes ANY caller-supplied key into `.env` with no allowlist.** `save_settings` iterates `globals_payload.items()` and calls `set_key(ENV_FILE_PATH, key, str(val))` for every key (`app.py:2078-2080`). An unauthenticated request can therefore set `LIVE_EXECUTION=True` (arming real-money execution), overwrite `COMPOSER_SECRET`/`ALPACA_SECRET`/`DISCORD_WEBHOOK_URL`, or inject arbitrary new `.env` keys. Because there is **no auth layer on any route** (grep for `login_required`/`session`/`csrf`/auth headers → empty), the only mitigation is the **localhost bind** (`app.run(port=…, debug=False, use_reloader=False)` at `app.py:2910` defaults to `127.0.0.1`; no `host=0.0.0.0`). So this is exploitable by any local process or via a reverse proxy / SSRF-from-another-app / CSRF-from-browser-on-the-box scenario, NOT from the open internet as-deployed. **Remediation:** (1) allowlist the keys `save_settings` may write (mirror `_ALGO_PARAM_META` + the 3 safe globals) and reject everything else, especially `LIVE_EXECUTION` and credential keys; (2) add CSRF protection to all POST routes; (3) require the daemon never bind `0.0.0.0` without auth (document + assert).

**A-2 [INFO] — The genuine real-money route IS well-gated.** `POST /api/sell_account` (liquidation) requires `confirm_account_id == account_id` AND `confirm_phrase == "LIQUIDATE"` AND `LIVE_EXECUTION` true before it spawns the liquidation thread; otherwise it returns an explicit `dry_run` no-op (`app.py:1855-1936`). `POST /api/trigger` explicitly refuses to spawn the engine from a route (arch-constraint comment + scheduler is the only spawner, `app.py:1581-1584`). The advisor accept/reject routes only write allowlisted, OOS-validated params (`app.py:2751-2807`). **Per-route boundary is sound; the gap is the un-allowlisted settings write (A-1) and global no-auth.**

**A-3 [MEDIUM, CONFIRMED] — Dashboard has no authentication and no CSRF.** No route requires auth; no CSRF tokens on the many POST routes (`/api/sell_account`, `/api/settings`, `/api/force_eod`, `/api/resend_discord`, `/ai-advisor/accept`, …). Localhost bind is the sole control. The project CLAUDE.md states the dashboard is a "read-only operator surface" — but mutating POST routes exist (settings, liquidation, force-EOD, config-accept), so "read-only" is aspirational. **Remediation:** add at minimum a shared-secret/session auth + CSRF before any non-localhost deployment; document the localhost-only assumption prominently.

### 6. Data exposure

**D-1 [MEDIUM, CONFIRMED] — Raw exception strings returned to the dashboard client.** Several routes return `str(e)` in the JSON error body: `/api/settings` POST `:2096`, `/api/logs/<id>` `:1427`, `/ai-advisor/suggest` `:2748` (also `exc_info=True` to the daemon log `:2747`). A DB/transport exception can leak SQL fragments, file paths, or internal detail to the browser. No secret has been shown to reach these (the redaction tests cover the Composer/Alpaca client paths specifically), but it is information disclosure. **Remediation:** return a generic message to the client; log detail server-side only.

**D-2 [INFO] — Client/engine response-body leakage is test-guarded and fixed.** `alpha_bot_execution.py` error paths print **only** `HTTP {status_code}` — the previously-flagged `response.text` echo is gone (`:181,183,278,283`). `tests/error_handling/test_response_text_scrub.py` + `test_composer_alpaca_client_log_redaction.py` pin that account IDs, position values, webhook URLs, and Bearer tokens never appear in stdout/stderr on the error path. **Clean.**

**D-3 [INFO] — Flask debug mode is OFF in production.** `app.run(debug=False, …)` (`app.py:2910`) — no Werkzeug interactive debugger / stack-trace page exposed. **Clean.**

### 7. Dependencies

**DEP-1 [MEDIUM, CONFIRMED] — `requirements.txt` is fully unpinned and `anthropic` is an undeclared dependency.** `requirements.txt` lists `requests, numpy, pandas, python-dotenv, Flask, schedule, optuna, joblib, scipy, psutil, pydantic` with **no version constraints** — non-reproducible builds and exposure to any future malicious/broken release (supply-chain). More seriously, **`anthropic` is imported by `ai_advisor.py:433` but is NOT declared** in `requirements.txt` or `pyproject.toml` — a fresh install will not have it; the advisor only works because it happens to be installed in the dev env. **Remediation:** pin all deps (compatible-release `~=` or a lockfile) and add `anthropic` (pinned) to `requirements.txt`. No specific known-CVE flagged here — the issue is the absence of pinning itself.

**DEP-2 [INFO] — No obviously abandoned/risky package observed.** All listed packages are mainstream, actively maintained libraries. (Not an exhaustive CVE scan — flag-only per scope.)

---

## Category clean-bill summary

- **Hardcoded live secrets in code:** NONE (clean).
- **SQL injection:** NONE (parameterized + validated; clean).
- **Command injection:** NONE (fixed argv, no shell; clean).
- **SSRF:** NONE (no user-controlled fetch URL; clean).
- **LLM data leakage:** NONE (curated allowlist; clean).
- **LLM output → action:** NOT POSSIBLE (4 gates; clean).
- **Advisor → live-trade/mutation:** NOT POSSIBLE (backtest-only API path; clean).
- **Flask debug exposure:** NONE (debug=False; clean).
- **Engine response-body secret echo:** FIXED + test-guarded (clean).

## Required follow-up

1. **M5 advisor chat** (`advisors/advisor_chat.py`, unmerged `advisor-m5`) — full security pass once merged; free-text → Anthropic is the highest new-surface risk (prompt injection + data leakage). MANDATORY.
2. **Rotate the leaked Discord webhook** and treat the historical account UUIDs as exposed (S-1).
3. **Allowlist the `POST /api/settings` write surface** and add CSRF/auth before any non-localhost deployment (A-1 / A-3).
4. **Pin dependencies + declare `anthropic`** (DEP-1).
