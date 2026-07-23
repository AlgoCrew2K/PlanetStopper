# Integration Layer Post-Merge Audit (2026-05-17)

Read-only audit of Composer.trade, Alpaca (data), and Discord webhook integration paths in AlphaBot v3 at main `0228a37`. No live API calls performed; no code changes.

Findings are presented per integration point with status:
- **VALIDATED** — matches contract / policy
- **ISSUE (Low/Med/High)** — defect observed, severity scaled
- **DRIFT-SUSPECTED** — observable mismatch hint; needs researcher follow-up

---

## 1. Composer.trade

### 1.1 Auth flow (`x-api-key-id` + `Bearer` secret)
- **VALIDATED.** `alpha_bot_execution.py:82-87` (`get_composer_headers`) constructs the canonical Composer header pair from `COMPOSER_KEY_ID` / `COMPOSER_SECRET` env vars, loaded once at import via `python-dotenv` from `.env`. `.gitignore:2` confirms `.env` is untracked; `git ls-files | grep ^\.env` returns empty (verified). `symphony_logic.py:19` and `app.py:691` reuse the same header contract; no header drift across call sites.

### 1.2 `symphony-stats-meta` (GET, polling)
- **VALIDATED.** `alpha_bot_execution.py:92-106` (`fetch_symphony_stats`). Explicit `timeout=15`, response-JSON `ValueError` wrapped (task #31 fix at line 100-102 confirmed), `RequestException` returns `[]` rather than crashing the cycle. Polling failure is recoverable (next-minute retry by scheduler).
- **Fixture present:** `tests/fixtures/composer/symphony_stats_meta.json`.

### 1.3 `go-to-cash` (POST, sell-to-cash side-effect)
- **VALIDATED.** `alpha_bot_execution.py:116-153` (`execute_sell_to_cash`). Explicit `timeout=10`. Bounded backoff with named-list `backoff_intervals = [1, 2, 4, 10]` (line 118) → **max total wait ≈ 17s** for transient `5xx` / network. `429` honors `Retry-After` (line 130) with 60s fallback. Non-retryable error branch returns `False` and skips state mutation at the caller (line 1258-1259).
- **ISSUE (Low) — Retry budget constant is unnamed.** Per project Prime Directive, the max total wait must be a named constant in code; `backoff_intervals` is a list, not a named ceiling. Spec compliance is implicit.
- **ISSUE (Low) — No idempotency key on retried writes.** Project Prime Directive requires every retried write to carry an idempotency key; Composer `go-to-cash` POSTs send `json={}` only. If Composer's endpoint is naturally idempotent per `(account_id, symphony_id)` this is acceptable — flag for `composer-api-researcher`.

### 1.4 `/symphonies/{id}/score` (GET, symphony logic — P2 / task #80)
- **VALIDATED.** `symphony_logic.py:35-63` (`fetch_symphony_score`). Explicit `timeout=15` via `_SCORE_FETCH_TIMEOUT` (named constant — model citation for the rest of the codebase). JSON `ValueError` caught; transient failures return `{}` safely. Process-lifetime cache (`_CONDENSED_CACHE`) with explicit `clear_cache()` hook.
- **Fixtures present:** `tests/fixtures/symphony_logic/sample_score_{large,small}.json` plus a live drift-sensor test at `tests/symphony_logic/test_live_composer_score.py` (marked `@pytest.mark.live`).

### 1.5 Rate-limit handling
- **VALIDATED.** 429 path in `execute_sell_to_cash` parses `Retry-After` (int seconds) with 60s default. Polling path (`fetch_symphony_stats`) sleeps `1.5s` after each request to stay under the 1 req/sec Composer baseline (per `docs/runbooks/composer-rejection-diagnostic.md`).

### 1.6 Dashboard liquidation surface
- **ISSUE (Med) — Dashboard is an action surface.** `app.py:705-733` (`/api/sell_account`) and `app.py:689-703` (`perform_account_liquidation`) write to Composer `go-to-cash` from the Flask process. Project CLAUDE.md Architecture Constraint #2 explicitly states "Dashboard is a read-only operator surface — never an action surface for live trades." A `LIVE_EXECUTION` gate (line 715) returns a dry-run no-op, partially mitigating, but the codepath still exists.
- **ISSUE (Med) — broad `except Exception` swallows.** `app.py:702` catches `Exception` and prints. Violates project Prime Directive "Never catch broad `except Exception` around an API call and swallow it." `perform_account_liquidation` also has **no retry / no idempotency / no 429 handling** — inferior to the engine path.
- **ISSUE (Low) — No fixture for the liquidation flow.** `tests/fixtures/composer/` has only `symphony_stats_meta.json`. The dashboard liquidation flow has no fixture-derived test.

### 1.7 Secrets hygiene in error logs
- **VALIDATED for `alpha_bot_execution.py`.** Hardened per cycle #27 (`tests/error_handling/test_response_text_scrub.py` pins zero `response.text` occurrences). Status codes only.

---

## 2. Alpaca

### 2.1 Historical bars (3y daily MC)
- **VALIDATED.** `alpha_bot_execution.py:156-242` (`fetch_alpaca_history`). Explicit `timeout=30` (line 192). 3-attempt retry with linear backoff (`2*(attempt+1)`s → max 6s — bounded). Disk cache `history_cache.json` keyed on date+ticker list. **Read-only data endpoint (`data.alpaca.markets`)** — no broker / trading API used anywhere; paper-vs-live distinction is N/A for AlphaBot's Alpaca usage (Composer is the broker).

> **Superseded on this point (2026-07-07):** "no broker / trading API used anywhere" and "Composer is the broker" are no longer true of the codebase as a whole. Managed Sleeves P1 added `sleeves/alpaca_orders.py`, a second, independent order-capable module that submits orders directly to Alpaca's Trading API (paper today; live once the operator provisions live keys). The historical-bars finding immediately above (read-only `data.alpaca.markets` usage in `alpha_bot_execution.py`/`synthetic_history.py`) is still accurate on its own terms -- those specific call sites remain read-only. See [`docs/generated/sleeves.md`](../../generated/sleeves.md) for the current trade-path architecture and the whole-repo containment invariant that keeps order-placing code confined to that one module.

### 2.2 Intraday VWAP feed
- **VALIDATED.** `alpha_bot_execution.py:245-280` (`fetch_intraday_vwaps`). Explicit `timeout=15`. Feed pinned to `feed=iex` (covered by `tests/alpaca/test_feed_pinning.py`).

### 2.3 Synthetic history (125-day parallel fetcher for autotuner)
- **VALIDATED on contract.** `synthetic_history.py:24-73` (`fetch_bars`). Explicit `timeout=30`. 10-retry rate-limit loop (15s sleep on 429), 5s on other errors — **max total wait ~150s per page** (bounded but generous; acceptable for offline autotuner replay only).
- **ISSUE (Med) — `synthetic_history.py:51` echoes `response.text` to stdout.** This is the same anti-pattern the cycle-#27 hardening removed from `alpha_bot_execution.py`. Alpaca error bodies are less likely to leak Composer account-ids, but the policy ("never log raw API responses verbatim") is project-wide.
- **ISSUE (Med) — `synthetic_history.py:53` has a bare `except Exception` swallow.** Violates Prime Directive. Should be the narrow `(requests.RequestException, ValueError, KeyError, TypeError)` union used elsewhere.
- **ISSUE (Low) — No Alpaca fixtures.** `tests/fixtures/alpaca/` directory is empty. `tests/alpaca/test_feed_pinning.py` is a structural test only.

### 2.4 Paper-vs-live key separation
- **N/A (VALIDATED by absence).** AlphaBot uses only `data.alpaca.markets` (read-only). No `paper-api` / `api.alpaca.markets` (broker) calls exist (`grep -i 'paper|api\.alpaca|trading\.alpaca|broker'` returned no matches across `.py` files). The `LIVE_EXECUTION` flag only gates Composer writes.

> **Superseded on this point (2026-07-07):** this "N/A by absence" finding no longer holds. `sleeves/alpaca_orders.py` (Managed Sleeves P1) now calls both `paper-api.alpaca.markets` (paper, the P1 floor) and, once the operator provisions distinct `ALPACA_LIVE_KEY`/`ALPACA_LIVE_SECRET` env vars, `api.alpaca.markets` (live) -- gated through a single `resolve_host()` function, never the bare `LIVE_EXECUTION` flag this finding references (that flag continues to gate only Composer writes, unchanged). See [`docs/generated/sleeves.md`](../../generated/sleeves.md) for the host-gating architecture.

### 2.5 Autotuner replay path
- **VALIDATED.** `autotuner.py` has zero `requests.` references (verified via grep). Replays from `synthetic_history.generate_synthetic_history` cache → no live API on the backtest path.

---

## 3. Discord

### 3.1 Per-trigger `send_discord_alert`
- **VALIDATED for non-blocking semantics.** `reporting.py:427-488`. Explicit `timeout=10` (line 488). Best-effort: no return value inspected by callers. The `is_live` parameter (line 428, 452) is **propagated explicitly** from `LIVE_EXECUTION` at `alpha_bot_execution.py:1251` — no default. Color/title differ on dry-run vs live.
- **ISSUE (Low) — `reporting.py:488` lacks exception handling.** `requests.post(...)` is unwrapped; a network blip in `send_discord_alert` will propagate out of the trigger loop. Other Discord sites (`send_eod_discord_post:424`) wrap a broad `except Exception` — at least non-fatal but anti-pattern.

### 3.2 EOD `send_eod_discord_post`
- **ISSUE (Med) — Broad `except Exception` swallowing (3 sites).** `reporting.py:259`, `:325`, `:424`. Each swallows arbitrary errors during EOD chart/Discord assembly with a `print(...)`. Violates Prime Directive.
- **VALIDATED:** Explicit timeouts on `requests.post` (`:257` to QuickChart, `:414`/`:420` to Discord). Discord 10-embed batch limit honored (`:411`, `:417`). 1.5s sleep between batches respects rate limit.

### 3.3 QuickChart embed integrity
- **ISSUE (Low) — QuickChart response not validated.** `reporting.py:258` does `resp.json().get('url')` with no status check; a 5xx HTML page would `ValueError` and be swallowed by the broad `except Exception` at line 259. End user sees "QuickChart failed:"; chart silently absent from Discord embed. Low impact (graceful degradation).

### 3.4 O2 deflated-Sharpe surfacing
- **DRIFT-SUSPECTED (Low) — O2 / deflated-Sharpe NOT surfaced in EOD Discord.** Grep `deflated|O2|sharpe_def` against `reporting.py` returns zero matches. O2 logic exists in `autotuner.py` and `tests/autotuner/test_o2_deflated_sharpe.py`. The EOD Discord embed (`reporting.py:373-402`) shows only `(old → new)` parameter deltas and `_baseline_chosen`. If O2 deflation is expected to appear per the audit prompt, this is either a missing wire-up or scope was deferred. Recommend a researcher / spec confirm whether O2 surfacing is in the EOD scope.

---

## 4. Live-vs-Replay Safety Boundary

### 4.1 `is_live` is explicit, never default
- **VALIDATED.** `LIVE_EXECUTION` env var defaults `"False"` (`alpha_bot_execution.py:43`). The only writes to Composer guarded by it: `execute_sell_to_cash` call site at `alpha_bot_execution.py:1139` and dashboard panic-stop at `app.py:715`. `reporting.send_discord_alert(is_live=...)` is positional and explicit — no `is_live=True` default anywhere.

### 4.2 Zero live calls in default test tier
- **VALIDATED with one config-hardening recommendation.** Live tests (`test_live_*.py`, plus `pytestmark = pytest.mark.live` files) exist:
  - `tests/symphony_logic/test_live_composer_score.py`
  - `tests/ai_advisor/test_live_claude_advisor.py`
  - `tests/analytics/test_live_m1_helpers.py`
  - `pyproject.toml` registers the `live` marker but does **not** include `-m 'not live'` in `addopts`. Exclusion relies on the `/run-tests` skill wrapper and naming convention.
- **ISSUE (Low) — Default-exclusion is convention, not config.** A direct `python -m pytest` run will execute the live drift-sensor tests if Composer creds are present (they self-skip when creds absent — partial safety). Recommend adding `-m 'not live'` to `addopts` in `pyproject.toml` so the default-exclude is a config invariant, not skill-script behavior.

### 4.3 Autotuner replays never hit live endpoints
- **VALIDATED.** Confirmed above (§2.5).

---

## 5. Engine 1-min Cadence Safety

- **VALIDATED.** Per-cycle Composer calls are bounded:
  - `fetch_symphony_stats`: 15s timeout × N accounts (≤3) + 1.5s sleep each → ≤ ~50s worst case.
  - `execute_sell_to_cash`: 10s × 5 attempts + backoff (1+2+4+10) = ~67s absolute worst case (only on full-failure path).
  - Alpaca `fetch_intraday_vwaps`: 15s × N batches.
- Schema-migration writes (H1 `record_exit_trigger`, M1F `record_shadow_observation`) are SQLite-only, opening their own connection and swallowing failures — **never block on Composer / Alpaca** (verified `database.py:555-598`, `:608-655`).

---

## 6. Schema Migration Integration Touch

### 6.1 H1 telemetry write
- **VALIDATED.** `database.record_exit_trigger` (`database.py:555-598`) opens its own `sqlite3.connect`, does not join `save_state` transaction, swallows on error — **never calls Composer**.

### 6.2 M1F shadow_history write
- **VALIDATED.** `database.record_shadow_observation` (`database.py:608-655`) opens its own connection, swallows on error — **never calls Alpaca**.

---

## 7. DM Market-State Interaction (Composer Post-Trigger Ambiguity OD-4)

- **VALIDATED.** `market_calendar.get_market_state` (`market_calendar.py:17-45`) returns `closed_frozen` after 16:00 ET; `app.py:223-242` serves a frozen `last_market_close_snapshot` instead of re-querying live state. This freezes the dashboard at close, mitigating the OD-4 post-trigger ambiguity (Composer's "did the rebalance succeed?" race).

---

## 8. Pre-Existing Drift Signals

| Signal | Where | Action |
|---|---|---|
| Composer `go-to-cash` idempotency unknown | `alpha_bot_execution.py:117` | `composer-api-researcher`: confirm whether the endpoint dedupes on `(account, symphony)` or needs a client idempotency key |
| `YAHOO_FINANCE_BASE_URL` dead constant | `alpha_bot_execution.py:77` | Codebase-internal cleanup — `grep YAHOO_FINANCE_BASE_URL *.py` returns only the definition; never read. Likely vestigial after a prior data-source migration. Suggest removal or revival with a contract test. |

---

## Summary of Findings

**Critical / High severity:** 0
**Med:** 4 (dashboard action surface + missing exception narrowing; synthetic_history.py `response.text` echo + bare `except`; EOD reporting broad excepts)
**Low:** 6 (retry-budget constant unnamed; missing idempotency on Composer write; no fixture for liquidation flow; no Alpaca fixtures; default test-exclude is convention-only; QuickChart unchecked + `send_discord_alert` unwrapped)
**Drift-suspected:** 1 (O2 deflated-Sharpe not surfaced in EOD Discord; verify scope)
**Validated:** 11 integration points

No live API calls were made during this audit. No production endpoints were exercised. All findings derive from static analysis of the repo at `0228a37`.
