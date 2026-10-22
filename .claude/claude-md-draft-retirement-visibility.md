# CLAUDE.md key-files draft — Retirement Recommender Cycle 2e (DE-RETIRE-VISIBILITY-001, 2026-10-22)

This is a proposal only. The doc-writer does not edit the project `.claude/CLAUDE.md` directly; the PM applies or amends these blocks on merge. Each block is either a drop-in addition to the named row's existing text in the `## Key Files` table or a short annotation of a now-stale clause. Every fact below was checked against the cycle diff (`git diff de45213..<cycle HEAD>`), not taken from the plan.

---

## New row — `advisors/composer_series_client.py` (insert after the `advisors/retirement_{recommender,explainer,checklist}.py` row)

| `advisors/composer_series_client.py` | Read-only Composer per-symphony daily-series client (Retirement Cycle 2e, `DE-RETIRE-VISIBILITY-001` AC-1/AC-10). `fetch_symphony_daily_returns(account_id, symphony_id) -> SeriesResult` (`returns` ISO ET date → simple daily return in **percent**, plus `points`/`first_date`/`last_date`/`error`). It sends `GET {COMPOSER_BASE_URL}/portfolio/accounts/{account}/symphonies/{symphony}` and parses `epoch_ms` + `deposit_adjusted_series`, never the raw `series`, which jumps on deposits. Points map to America/New_York dates; the last point per date wins; today and later dates are dropped. A non-positive or non-finite endpoint skips that return, and epoch-ms below `1e11` rejects the body. Network handling: `REQUEST_TIMEOUT_SECONDS=30`, `MAX_ATTEMPTS=3` on 429/5xx/transport errors, `Retry-After` clamped to `[0, RETRY_AFTER_CAP_SECONDS=30]`, and `MAX_TOTAL_RETRY_WAIT_SECONDS=60`. It never raises (D-1: `error` is an HTTP status, an exception type or a fixed phrase; never a body, header or message). GET-only; the only `alpha_bot_execution` import is `COMPOSER_BASE_URL`/`get_composer_headers` (the `composer_backtest_client` precedent), enforced by no-trade Group G. **`deposit_adjusted_series` semantics are [Unverified]** (name-only in Composer's docs). **No live Composer call was possible from the build container**, so the client is verified against mocks plus the `synthetic-from-docs` fixture `tests/fixtures/composer/symphony_daily_series_v1.json`. Recapturing a real fixture with `/api-fixture` is a tracked follow-up. Lazily imported by the recommender's `source="composer"` path only. See `docs/generated/advisors_composer_series_client.md`. |

---

## `advisors/retirement_{recommender,explainer,checklist}.py` row — append at the end of the row

**Cycle 2e — real data depth + visible nightly runs (`DE-RETIRE-VISIBILITY-001`, 2026-10-22):**

- **Root cause.** Two verified gaps meant the recommender had never surfaced a card on the droplet. First, `shadow_history` begins at the 2026-06-22 go-live floor, about 87 trading days on 2026-10-22. That is below the structural-redundancy gate's floor of `STRESS_GATE_MIN_ALIGNED_DAYS=181`, so every pair failed closed. Second, a night that blocked every pair wrote nothing, so the UI could not tell "ran and blocked" from "crashed" or "never fired".
- **New `source` keyword.** `build_recommendations` gains `source` (`"shadow"` default, output unchanged, or `"composer"`). The production tick uses `"composer"`: realized deposit-adjusted daily returns via `advisors/composer_series_client.py`, with `bot_state[sym]["account"]` as the account id. Fetches are spaced `COMPOSER_FETCH_MIN_INTERVAL_SECONDS=1.0` apart. Each run uses one basis: a symphony without an account, with a failed fetch, or with fewer than 2 returns is excluded with a reason and never backfilled from shadow. The composer basis label is `"realized daily returns (Composer, deposit-adjusted)"`.
- **Why the basis change.** It is a `[PM-ASSUMED]` reading of Cycle 2a's AC-2: the droplet runs `LIVE_EXECUTION=False`, so the shadow bot leg is a simulation. Because no live exits fire, the realized series measures each symphony as actually held, without Guard Alpha's exits.
- **New `build_recommendations_with_summary()`.** It returns `(recs, summary)`. The summary has status `ok` / `insufficient_symphonies` / `series_unavailable` / `internal_error`. Its per-pair gate counts reconcile with `pairs_screened`: `blocked_uncertainty`, `blocked_stress_too_thin`, `blocked_stress_below_threshold`, `blocked_no_candidate`. It also records exclusions, series depth, and `stress_gate_min_aligned_days`. That value is derived at import from the stress constants (never a literal) with a bounded search (`cc11f53`).
- **Unchanged.** Gates, thresholds, composite weights and candidate selection.
- **AC-9.** The Cycle-2b golden sha256 pin on `retirement_recommender.py` is replaced by an AST no-LLM-import scan in `tests/app/test_retirement_producer_explainer_wiring.py`. That scan covers module-level, lazy, relative and string-constant dynamic imports of `anthropic`, the explainer, `model_config`, `advisor_chat` and `ai_advisor`. It checks this file's own imports only, not transitive ones.
- **Byte-unchanged this cycle:** `retirement_explainer.py`, `retirement_checklist.py`, `correlation_diagnostic.py`, `database.py`, `analytics.py`, the engine.

See `docs/generated/advisors_retirement_recommender.md`'s "Cycle 2e" section and `DE-RETIRE-VISIBILITY-001` in `DECISIONS.md`.

**Annotate (do not delete) the three now-stale "byte-unchanged" clauses in the same row.** Append `*[superseded by Cycle 2e AC-9 — the module changed legitimately; LLM-free is now enforced by an AST import scan]*` immediately after each of:

> `` `retirement_recommender.py` itself is byte-unchanged this cycle (golden-hash-pinned). ``

> `` `retirement_recommender.py`/`retirement_explainer.py`/`retirement_checklist.py` are all BYTE-UNCHANGED this cycle (verified zero diff) ``

> `**Cycle 2d (`DE-RETIRE-POLISH-001`, 2026-08-28) — all 3 modules here remain BYTE-UNCHANGED**`

**Why:** each of these was true of its own cycle and is preserved as history. A future reader who takes "golden-hash-pinned" as the current invariant would look for a test that no longer exists.

---

## `app.py` row — append after the Cycle 2d (`DE-RETIRE-POLISH-001`) clause, before the MDD none-guard clause

**Retirement Cycle 2e — visible nightly runs + Run now (`DE-RETIRE-VISIBILITY-001`, 2026-10-22):**

- **Tick worker.** `_retirement_recommender_tick_worker` now builds with `build_recommendations_with_summary(source="composer")` and stamps the run's `run_id` on every rec before the enrichment/explain pass. It persists the recs, then always writes exactly ONE `RETIREMENT_RUN_SUMMARY` advisor_observations row via `_persist_retirement_run_summary`, after the recs, so the Run-now reload never lands before the cards. That row is written even on 0 recs, a failed build (`_retirement_internal_error_summary`: unknown counts are `None`, never `0`) or a failed persist, and a post-build failure downgrades `ok` to `internal_error`. `recommendations` is overwritten with the rows actually persisted. `RETIREMENT_RUN_SUMMARY` is NOT in `_ADVISOR_ROLES`.
- **Reuse snapshot.** `run_id` is excluded from `_canonical_evidence_snapshot`, so a stable pair still reuses its explanation and no new nightly LLM spend is added.
- **Run-scoped cards.** New `_fetch_retirement_last_run()` returns the latest run of ANY status and feeds the last-run block. New `_fetch_retirement_cards_run(last_run)` (AC-5b, a mid-cycle PM ruling on ret5-review finding 1) returns the newest `status=="ok"` summary with a `run_id`. It takes a fast path when `last_run` is itself `ok` and otherwise scans `_RETIREMENT_CARDS_RUN_SCAN_LIMIT=365` rows. Both helpers raise on DB failure, and callers degrade. `_fetch_retirement_recommendations` gains keyword `cards_run` (sentinel default; an explicit `None` means the legacy path with no re-read) and returns only rows whose `run_id` matches. **A failed night never hides earlier or approved cards, while an `ok` run that found nothing does empty the panel.** With no `ok` run, it keeps the latest-calendar-date filter.
- **API.** `GET /api/retirement-recommendations` returns exactly `{recommendations, last_run, cards_run}`, from one shared snapshot. `_json_safe` now recurses into lists.
- **Panel.** `ai_advisor_tab()` passes `retirement_last_run`, `retirement_cards_run` and `retirement_last_run_excluded`. The last of these is resolved by `_retirement_resolve_excluded_symphonies` via the shared memoized `bot_state` closure.
- **Run now.** New `POST /ai-advisor/retirement/run` (`ai_advisor_retirement_run`), with responses checked in this order:
  - `409 {"status":"market_open"}` while `get_market_state(now ET)=="open"` (AC-7b, mid-cycle PM ruling, because Composer's rate limit is shared with the live engine). This is checked BEFORE the lock: no lock taken, no thread started.
  - `409 {"status":"already_running"}` if the non-blocking `_RETIREMENT_RUN_LOCK` is held. That lock is shared with the 03:45 tick, whose thread body `_retirement_scheduled_tick_body` skips with an INFO log when a run is in flight; the 03:45 tick is never market-gated.
  - `202 {"status":"started"}` otherwise. The daemon thread `_retirement_run_now_body` releases the lock in a `finally`; if `Thread.start()` fails, the route releases the lock itself and returns `500`.
- **Security.** CSRF and auth come from the global hooks. The route is not in `_SETTINGS_WRITE_ALLOWLIST`, has no `LIVE_EXECUTION`, and is advisory-only.
- **No-trade test.** Group E gains the route, `_fetch_retirement_last_run` and the tick wrapper. Its walker now follows `Thread(target=<name>)`, a ret5-doc finding: three new helpers were unscanned until this fix. Group H pins the market gate to the route only. A new Group I name-pattern sweep covers every module-level `*retirement*` function (19 at `5ad13a8`).
- **Accepted limitations (PM, 2026-10-22):**
  - (a) The market gate is checked only at run start. A run can overlap 09:30: about 17 min worst case with all-timeout fetches for 11 symphonies, with a strict theoretical bound of about 28 min. Run now is also not gated against the 16:00 EOD stage-2 calls, which are reporting-only.
  - (b) The lock is process-local, which is correct for the single-process daemon.
  - (c) A partial persist leaves rows stamped with a non-`ok` run. Because of AC-5b they are hidden whenever an earlier `ok` run exists, while the summary honestly reads `internal_error`.

See `docs/generated/app.md`'s Cycle-2e sections.

---

## `templates/ai_advisor.html` row — append

**Retirement Cycle 2e (`DE-RETIRE-VISIBILITY-001`, 2026-10-22):** the Overview-tab Retirement panel gains four pieces above the cards. All are autoescaped, with no `| safe` anywhere in the panel, which is test-pinned.

1. An always-present `data-testid="retirement-last-run"` block. It shows the run time (UTC), source/basis, symphonies analysed vs roster, series depth, pairs screened at the threshold, and the blocked count per gate. When `blocked_stress_too_thin > 0` it adds the stress-gate minimum, read from the summary rather than re-declared. It also shows the recommendation count and the excluded symphonies by display name with reasons. Each of never-run, `series_unavailable`, `insufficient_symphonies` and `internal_error` gets distinct honest copy.
2. A `retirement-run-now-btn` button with a `role="status"` message slot.
3. A static AC-7b hint: "Run now is available outside market hours only".
4. (AC-5b) A `data-testid="retirement-cards-from-earlier-run"` note just above the cards when the latest run failed and the cards come from an earlier successful run (its `run_at` date, in UTC).

---

## `static/ai_advisor.js` row — append

**Retirement Cycle 2e (`DE-RETIRE-VISIBILITY-001`, 2026-10-22):** new `window.retirementRunNow(btnEl)`.

- It sends a CSRF POST to `/ai-advisor/retirement/run`.
- `market_open` is checked before the generic 409: it shows a `textContent` reason and returns without polling.
- `already_running` waits for that run; `202` polls.
- The poll reads `GET /api/retirement-recommendations` every `RETIREMENT_POLL_INTERVAL_MS=5000` for up to `RETIREMENT_POLL_TIMEOUT_MS=180000`, until `last_run.run_id` changes, then calls `location.reload()`. On timeout it shows "still going — reload later".
- It writes `textContent` only. The poll is click-initiated and bounded, so it is not an unattended auto-refresh; the rationale is in a source comment.

---

## Project Identity — "Built capabilities" bullet, optional append to the Composer-series clause

After `...verified 538 points back to 2024-07-12) on credentials already in use — see DE-PERF-WINDOW-TRUTH-001 and docs/audit/COMPOSER-HISTORICAL-SERIES-2026-09-03.md).`, append:

> **First consumer shipped 2026-10-22:** the Retirement Recommender's production tick reads this series through `advisors/composer_series_client.py` (`DE-RETIRE-VISIBILITY-001`). It reads `deposit_adjusted_series`, whose semantics are still [Unverified], and no real-response fixture has been captured yet.

**Why:** the bullet currently reads as "available, unused", which became false this cycle.
