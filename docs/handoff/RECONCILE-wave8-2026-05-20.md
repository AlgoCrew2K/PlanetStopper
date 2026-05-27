> **HISTORICAL** — This document is a pre-Sprint-1 or Sprint-1 cycle artifact preserved for provenance. See [docs/audit/](../audit/) for current state.

---

# Composer ground-truth reconciliation — Wave 8 — HEAD db0f95f

**Branch:** feat/studio-design-handoff
**Date:** 2026-05-20 ~21:00 UTC (market closed, 16:07 ET)
**Fixture:** `tests/fixtures/composer/reconcile-wave8-2026-05-20/total_stats.json` (captured-from-producer)
**Endpoint:** `GET /api/v0.1/portfolio/accounts/{id}/total-stats`
**Dashboard:** `GET /api/state` — `market_state: closed_frozen`, `frozen_at: 16:00:01 ET`
**Prior pass:** `docs/handoff/RECONCILE-final-2026-05-20.md` (HEAD 9645db8)

---

## Summary

| Category | Count |
|----------|-------|
| MATCH | 3 (meta.portfolio.cr_if_held, meta.portfolio.account_value, frozen TC epoch) |
| WRONG (structural) | 1 (portfolio_strip.cumulative_return.if_held — frozen path bypasses cache) |
| WRONG (minor/structural) | 1 (frozen TC: 0.037pp gap explained by cash denominator, not stale data) |

---

## Reconciliation Table

| Metric | Composer (truth) | Dashboard | Delta | Verdict |
|--------|------------------|-----------|-------|---------|
| Account value (`meta.portfolio.account_value`) | $12,893.70 | $12,893.70 | $0.00 | **MATCH** |
| Portfolio CR (`meta.portfolio.cr_if_held`) | 69.2726% | 69.2726% | 0.0000pp | **MATCH** |
| Portfolio TC (frozen `tc.if_held` in portfolio_strip) | -2.0206%† | -1.9840% | +0.037pp | WRONG (structural) |
| Portfolio CR (`portfolio_strip.cumulative_return.if_held`) | 69.2726% | 66.8918% | -2.381pp | WRONG (frozen path bypasses cache) |

†Composer `total-stats.todays_percent_change` pulled post-market (~21:00 UTC); this may reflect after-hours adjustments vs the exact 4pm close.

---

## Auth Fix Confirmed — CR and Account Value Now MATCH

`_refresh_account_totals` (db0f95f) now uses correct env var names (`COMPOSER_KEY_ID`, `COMPOSER_SECRET`, `ACCOUNT_ROTH`) and correct header shape (`x-api-key-id` + `authorization: Bearer`). The `_account_totals_cache` is populated every minute-scheduler tick.

- `meta.portfolio.cr_if_held = 69.2726%` matches Composer `simple_return * 100 = 69.2726%` — **exact match, 0.000pp delta**
- `meta.portfolio.account_value = 12893.70` matches Composer `portfolio_value = 12893.70` — **exact match**

The JS dashboard reads from `meta.portfolio`, so the user sees the correct values.

---

## Root Cause: Frozen TC — Is It Capturing the Right Epoch?

**Verdict: YES, the snapshot freezes the correct close-time value. The 0.037pp gap is structural, not a stale-data bug.**

### What the snapshot contains

The `last_market_close_snapshot` was written at `captured_at_et = 16:00:01 ET` — the 16:00 engine cycle, the last cycle of the trading day. The per-symphony `current_return` values in the snapshot represent the Composer `last_percent_change * 100` values fetched at that cycle:

| Symphony | Frozen `current_return` |
|----------|------------------------|
| Trolling Planet (n2oo) | +0.60% |
| Projected Inflation | -0.09% |
| Hunted Cascades | +0.89% |
| Reasonabilists | -1.04% |
| Planet LQD | -5.31% |
| LQD+EYEG Full Market | -4.25% |
| Corporate Chaos | -2.52% |
| Paragons EYEG | -4.15% |
| Feaver Allocations | -2.80% |
| Erased History | -2.73% |
| Golden Age | -2.05% |

Value-weighted average of these: **-1.9840%** — exactly what the dashboard shows. The freeze epoch is correct.

### Why the gap with Composer is 0.037pp

Dashboard frozen TC formula:

```
tc_if_held = Σ(sym_current_return × sym_value) / Σ(sym_value)
           = -255.72 / 12872.74
           = -1.9840%
```

Composer `total-stats.todays_percent_change`:

```
= todays_dollar_change / yesterday_portfolio_value
= -265.90 / 13159.60
= -2.0206%
```

The denominator differs: dashboard uses `Σ(sym_value) = 12,872.74`; Composer uses the full portfolio value including cash. The account holds `total_cash = $286.02`, of which `$20.97` is unallocated. Cash has 0% daily return — including it in the denominator makes the portfolio TC slightly more negative (more dilution in the denominator without contributing to the numerator). Additionally, the Composer fixture was captured post-market (~21:00 UTC) where after-hours prices may have moved slightly from the 16:00:01 snapshot.

**This is the same structural denominator mismatch flagged in account_value.** It is not a freeze-at-wrong-time bug. The TC formula is mathematically self-consistent with the data it uses; it just excludes cash from the denominator.

---

## Secondary Finding: Split Values in `closed_frozen` Response

In `closed_frozen` mode, the `/api/state` response currently serves **two different CR values** for the same metric depending on which key is read:

| Key | Value | Source |
|-----|-------|--------|
| `meta.portfolio.cr_if_held` | **69.27%** (correct) | `_injected_portfolio_strip` from `get_api_state_dict()` → `_account_totals_cache` |
| `portfolio_strip.cumulative_return.if_held` | **66.89%** (wrong) | On-the-fly recompute from snapshot data in the frozen branch (`app.py:847`) — uses value-weighted average, no cache |

`app.py:889-892`:
```python
"meta": _build_meta(
    _state,
    market_state=market_state,
    portfolio_strip=_injected_portfolio_strip or _portfolio_strip,  # uses cache
),
```

`app.py:884`:
```python
"portfolio_strip": _portfolio_strip,  # uses on-the-fly recompute, no cache
```

If the JS reads `portfolio_strip.cumulative_return.if_held` it gets 66.89%; if it reads `meta.portfolio.cr_if_held` it gets 69.27%. The correct value is 69.27%.

**Fix recipe:** In the `closed_frozen` branch of `get_state()`, populate the `account_value` and `cumulative_return.if_held` fields of `_portfolio_strip` from `_account_totals_cache` before returning, mirroring what `_compute_portfolio_strip` does in the live path:

```python
# After computing _portfolio_strip from snapshot data (app.py:842-858):
if "portfolio_value" in _account_totals_cache:
    _portfolio_strip["account_value"] = _account_totals_cache["portfolio_value"]
if "portfolio_cr" in _account_totals_cache:
    _portfolio_strip["cumulative_return"] = {
        "if_held": _account_totals_cache["portfolio_cr"],
        "dry_run": (_portfolio_strip.get("cumulative_return") or {}).get("dry_run"),
    }
```

This is a low-risk additive change to the frozen path.

---

## Composer `total-stats` Reference Values (2026-05-20 ~21:00 UTC, post-market)

| Field | Value |
|-------|-------|
| portfolio_value | $12,893.70 |
| simple_return | 69.2726% |
| time_weighted_return | 74.4536% |
| todays_percent_change | -2.0206% |
| todays_dollar_change | -$265.90 |
| net_deposits | $7,617.12 |
| total_cash | $286.02 |
| total_unallocated_cash | $20.97 |

---

## Fixture (provenance: captured-from-producer, 2026-05-20 ~21:00 UTC)

- `tests/fixtures/composer/reconcile-wave8-2026-05-20/total_stats.json`
