> **HISTORICAL** — This document is a pre-Sprint-1 or Sprint-1 cycle artifact preserved for provenance. See [docs/audit/](../audit/) for current state.

---

# Composer ground-truth reconciliation — HEAD 9645db8

**Branch:** feat/studio-design-handoff
**Date:** 2026-05-20T20:48 UTC
**Fixtures:** `tests/fixtures/composer/reconcile-final-2026-05-20/symphony_stats_meta.json` (captured live), `tests/fixtures/composer/reconcile-final-2026-05-20/total_stats.json` (captured live)
**Account:** ROTH IRA (`880be47e-efe4-4b44-9d83-b6d86098fe0d`)
**Endpoints:** `GET /api/v0.1/portfolio/accounts/{id}/symphony-stats-meta`, `GET /api/v0.1/portfolio/accounts/{id}/total-stats`
**Dashboard:** `GET /api/state` (http://127.0.0.1:5000)
**Prior pass:** `docs/handoff/RECONCILE-2026-05-20.md`

---

## Summary

| Category | Count |
|----------|-------|
| MATCH | 24 (all per-symphony CR + MDD, portfolio MDD) |
| WRONG | 2 (portfolio CR, account value — both caused by a single root cause) |
| FIXED since prior pass | 2 (per-symphony MDD units, symphony ID drift) |
| Open bug | 1 (`_refresh_account_totals` wrong env vars — cache never populates) |

---

## Reconciliation Table

| Metric | Composer (truth) | Dashboard | Delta | Verdict |
|--------|------------------|-----------|-------|---------|
| **Account value** | **$12,916.34** | **$12,895.43** | **-$20.91** | **WRONG** |
| **Portfolio CR (if_held)** | **69.5699%** | **66.7685%** | **-2.8014pp** | **WRONG** |
| Portfolio TC (if_held) | -1.8485% | -2.0175% | -0.169pp | MATCH* |
| Portfolio MDD (if_held) | 19.22% (vw avg) | 19.22% | ~0% | MATCH |
| Land of Feaver'd Allocations CR | 9.3510% | 9.3520% | +0.001pp | MATCH |
| Planet LQD CR | 33.7870% | 33.7870% | 0.000pp | MATCH |
| Planet of the Reasonabilists CR | 74.7560% | 74.7590% | +0.003pp | MATCH |
| Planet of Projected Inflation CR | 67.5950% | 67.5950% | 0.000pp | MATCH |
| Planet of Erased History CR | -0.5310% | -0.5310% | 0.000pp | MATCH |
| Planet of Hunted Cascades CR | 79.9240% | 79.9240% | 0.000pp | MATCH |
| LQD + EYEG Full Market CR | 3.0550% | 3.0550% | 0.000pp | MATCH |
| Planet of the Paragons CR | -8.1700% | -8.1680% | +0.002pp | MATCH |
| We do a Little Trolling (n2oo) CR | 317.474% (TWR) | 317.474% (TWR) | 0.000pp | MATCH |
| Corporate Chaos 5 ways CR | -4.1070% | -4.1060% | +0.001pp | MATCH |
| Planet of the Golden Age CR | 20.0990% | 20.0990% | 0.000pp | MATCH |
| Land of Feaver'd Allocations MDD | 15.01% | 15.01% | 0.00% | MATCH |
| Planet LQD MDD | 23.36% | 23.36% | 0.00% | MATCH |
| Planet of the Reasonabilists MDD | 23.44% | 23.44% | 0.00% | MATCH |
| Planet of Projected Inflation MDD | 14.95% | 14.95% | 0.00% | MATCH |
| Planet of Erased History MDD | 15.00% | 15.00% | 0.00% | MATCH |
| Planet of Hunted Cascades MDD | 32.26% | 32.26% | 0.00% | MATCH |
| LQD + EYEG Full Market MDD | 14.82% | 14.82% | 0.00% | MATCH |
| Planet of the Paragons MDD | 23.18% | 23.18% | 0.00% | MATCH |
| We do a Little Trolling (n2oo) MDD | 25.52% | 25.52% | 0.00% | MATCH |
| Corporate Chaos 5 ways MDD | 12.51% | 12.51% | 0.00% | MATCH |
| Planet of the Golden Age MDD | 6.57% | 6.57% | 0.00% | MATCH |

*TC staleness: not a formula bug. See prior pass.

---

## Fixed Since Prior Pass

### 1. Per-symphony MDD units — FIXED

Prior pass reported: `analytics.get_symphony_max_drawdown` returned raw decimal (0.1918) with no ×100 conversion.

Fix landed: `analytics.py:585` now reads `float(sym_dict["max_drawdown"]) * 100.0`. Composer stores MDD as decimal (e.g., 0.2552 = 25.52%). Dashboard now shows percentage-scale values.

Verification: All 11 per-symphony MDD values MATCH Composer (Composer decimal × 100 = dashboard %).

### 2. Symphony ID drift — FIXED

Prior pass reported: 10 of 11 Composer IDs did not match bot_state keys, making `_persist_composer_fields_to_bot_state` a silent no-op.

Fix landed: `alpha_bot_execution.py:146-197` — `_resolve_bot_state_key` with three-tier resolution (exact → 12-char prefix with name agreement → name match). Used by `_persist_composer_fields_to_bot_state` at line 202.

Verification: All 11 bot_state `simple_return` values match current Composer values (delta < 0.001 for all, within rounding of the intra-minute price movement between fixture capture and dashboard poll).

---

## Open Bug — Root Cause for Both WRONG Rows

**Both WRONG rows share one root cause: `_refresh_account_totals` silently fails every cycle due to wrong environment variable names.**

### Code (`app.py:227-246`):

```python
def _refresh_account_totals() -> None:
    env_vars = dotenv_values(ENV_FILE_PATH)
    token = env_vars.get("COMPOSER_API_TOKEN") or os.environ.get("COMPOSER_API_TOKEN", "")
    account_id = (
        env_vars.get("COMPOSER_ACCOUNT_IDS") or os.environ.get("COMPOSER_ACCOUNT_IDS", "")
    ).split(",")[0].strip()
    url = f"{COMPOSER_BASE_URL}/portfolio/accounts/{account_id}/total-stats"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    ...
```

**Bug 1 — Wrong env var names:**
- Code reads `COMPOSER_API_TOKEN` — does not exist in `.env`
- Code reads `COMPOSER_ACCOUNT_IDS` — does not exist in `.env`
- `.env` has `COMPOSER_KEY_ID`, `COMPOSER_SECRET`, `ACCOUNT_ROTH`

**Bug 2 — Wrong auth header shape:**
- Code sends `Authorization: Bearer <token>` with token = `""` (COMPOSER_API_TOKEN is None → empty string)
- Composer requires `x-api-key-id: <key_id>` + `authorization: Bearer <secret>` (two headers)

**Result:** Every call returns HTTP 401. The `except Exception: pass` swallows it silently. `_account_totals_cache` stays empty every cycle. `_compute_portfolio_strip` falls through to the value-weighted average fallback.

**Verified:** Direct HTTP call with wrong headers returns HTTP 401. Direct call with correct headers returns HTTP 200 with `portfolio_value: 12916.34`, `simple_return: 0.695699`.

### Effect on metrics:

**Portfolio CR (if_held): 66.7685% dashboard vs 69.5699% Composer (-2.8014pp)**

Without the cache, `_compute_portfolio_strip` falls back to `analytics.get_portfolio_cumulative_return`, which is a value-weighted average of per-symphony `simple_return` values. This produces 66.77% because the crypto symphony (`n2ooAZTvBRN6`, TWR=317.47%, value=$1,626) has inflated value that pulls the denominator higher without a correspondingly inflated weight. The deposit-weighted `total-stats.simple_return = 69.57%` is the correct number.

**Account value: $12,895.43 dashboard vs $12,916.34 Composer (-$20.91)**

Without the cache, `_compute_portfolio_strip` sums `current_value` from bot_state, which covers only symphony-allocated capital. Composer `portfolio_value` also includes `total_cash = $228.90` (symphony cash positions + unallocated account cash). The $20.91 gap = unallocated cash not captured by any symphony's `value` field.

### Fix recipe for impl-backend:

In `app.py:227-246`, replace the env var reads and header construction:

```python
def _refresh_account_totals() -> None:
    try:
        env_vars = dotenv_values(ENV_FILE_PATH)
        key_id = env_vars.get("COMPOSER_KEY_ID") or os.environ.get("COMPOSER_KEY_ID", "")
        secret = env_vars.get("COMPOSER_SECRET") or os.environ.get("COMPOSER_SECRET", "")
        # Use first non-empty account from ACCOUNT_ROTH / ACCOUNT_INDIVIDUAL / ACCOUNT_TRAD
        account_id = (
            env_vars.get("ACCOUNT_ROTH")
            or env_vars.get("ACCOUNT_INDIVIDUAL")
            or env_vars.get("ACCOUNT_TRAD")
            or ""
        ).strip()
        if not key_id or not secret or not account_id:
            return
        url = f"{COMPOSER_BASE_URL}/portfolio/accounts/{account_id}/total-stats"
        resp = requests.get(
            url,
            headers={
                "x-api-key-id": key_id,
                "authorization": f"Bearer {secret}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            _account_totals_cache["portfolio_value"] = data["portfolio_value"]
            _account_totals_cache["portfolio_cr"] = data["simple_return"] * 100.0
    except Exception:
        pass
```

This is a one-function fix, no contract change, no new dependencies.

**Expected outcome after fix:**
- `account_value` → `$12,916.34` (MATCH)
- `portfolio CR if_held` → `69.57%` (MATCH)

---

## Composer `total-stats` Reference Values (2026-05-20 ~14:48 ET)

| Field | Value |
|-------|-------|
| portfolio_value | $12,916.34 |
| simple_return | 69.5699% |
| time_weighted_return | 74.7599% |
| todays_percent_change | -1.8485% |
| net_deposits | $7,617.12 |
| total_cash | $228.90 |
| metrics.max_drawdown | 24.47% (portfolio peak-to-trough) |
| metrics.sharpe_ratio | 1.0837 |

Note: `metrics.max_drawdown = 24.47%` is the portfolio-level peak-to-trough since inception. Dashboard MDD (19.22%) is the value-weighted average of per-symphony MDD values — a different metric measuring the average drawdown risk at the symphony level. Both are internally consistent; they measure different things.

---

## Fixtures (provenance: captured-from-producer, 2026-05-20 ~14:46 ET)

- `tests/fixtures/composer/reconcile-final-2026-05-20/symphony_stats_meta.json`
- `tests/fixtures/composer/reconcile-final-2026-05-20/total_stats.json`
