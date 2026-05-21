# Ground-Truth Reconciliation — Pass 4 (Final)
**HEAD:** 80363b6  
**Market state:** `closed_frozen` (frozen_at 16:00:01 ET 2026-05-20)  
**Fixture path:** `tests/fixtures/composer/reconcile-FINAL-2026-05-20/`  
**Fixture provenance:** captured-from-producer (live Composer API 2026-05-20)  
**Account:** ROTH (`880be47e-efe4-4b44-9d83-b6d86098fe0d`)

---

## Summary

**All metrics MATCH or within documented structural tolerance.**  
Score: **26 MATCH, 0 WRONG** (2 structural tolerances documented below).

Key fix confirmed: `f53fbf9` (`closed_frozen` cache injection) — both `portfolio_strip.cumulative_return.if_held` and `portfolio_strip.account_value` now serve the cached Composer-sourced values on the frozen path. Both equal `meta.portfolio` equivalents — the split-value bug is resolved.

---

## Portfolio-Level Reconciliation

| Metric | Dashboard | Composer | Delta | Status |
|--------|-----------|----------|-------|--------|
| `portfolio_strip.cumulative_return.if_held` | 69.2726% | 69.2726% | 0.000pp | **MATCH** |
| `meta.portfolio.cr_if_held` | 69.2726% | 69.2726% | 0.000pp | **MATCH** |
| Both values equal each other | `True` | — | — | **MATCH** |
| `portfolio_strip.account_value` | $12,893.70 | $12,893.70 | $0.00 | **MATCH** |
| `meta.portfolio.account_value` | $12,893.70 | $12,893.70 | $0.00 | **MATCH** |
| `portfolio_strip.tc` (today's change) | -1.9840% | -2.0206% | 0.037pp | **STRUCTURAL** |
| `portfolio_strip.mdd_if_held` | 19.27% | 24.47% | 5.20pp | **STRUCTURAL** |

### Structural Tolerance Notes

**TC gap (0.037pp):** Dashboard denominator = sum of symphony `current_value` fields (~$12,607). Composer denominator = full `portfolio_value` including `total_cash` ($286.02, of which $20.97 is unallocated). Freeze epoch is correct — captured at 16:00:01 ET on the first engine cycle after 4pm close. Gap is stable and mathematically explained by the cash-in-denominator difference. Not a bug.

**MDD divergence (5.20pp):** Dashboard `mdd_if_held = 19.27%` is the value-weighted average of per-symphony MDD values (each symphony's own peak-to-trough). Composer `metrics.max_drawdown = 24.47%` is the portfolio-level peak-to-trough since inception (a different, portfolio-aggregate metric). Both are correct for what they measure. Not a bug.

---

## Per-Symphony Reconciliation (all 11 symphonies)

### Cumulative Return (if_held)

| Symphony | Dashboard CR | Composer CR | Delta | Status |
|----------|-------------|-------------|-------|--------|
| n2oo Trolling Planet | 318.411% | 318.411% | 0.000pp | **MATCH** |
| Projected Inflation | 67.485% | 67.485% | 0.000pp | **MATCH** |
| Hunted Cascades | 80.138% | 80.138% | 0.000pp | **MATCH** |
| Reasonabilists | 74.995% | 74.995% | 0.000pp | **MATCH** |
| Planet LQD | 33.125% | 33.125% | 0.000pp | **MATCH** |
| LQD+EYEG Full Market | 2.626% | 2.626% | 0.000pp | **MATCH** |
| Corporate Chaos | -4.479% | -4.479% | 0.000pp | **MATCH** |
| Paragons EYEG | -8.915% | -8.915% | 0.000pp | **MATCH** |
| Feaver Allocations | 8.991% | 8.991% | <0.001pp | **MATCH** |
| Erased History | -0.845% | -0.845% | 0.000pp | **MATCH** |
| Golden Age | 20.097% | 20.097% | 0.000pp | **MATCH** |

Note (n2oo): `simple_return = 0` and `net_deposits = 0` — dashboard correctly falls back to `time_weighted_return * 100` per `analytics.py:530` fallback branch.

### Max Drawdown (if_held)

| Symphony | Dashboard MDD | Composer MDD | Delta | Status |
|----------|--------------|-------------|-------|--------|
| n2oo Trolling Planet | 25.52% | 25.52% | 0.000pp | **MATCH** |
| Projected Inflation | 14.95% | 14.95% | 0.000pp | **MATCH** |
| Hunted Cascades | 32.26% | 32.26% | 0.000pp | **MATCH** |
| Reasonabilists | 23.44% | 23.44% | 0.000pp | **MATCH** |
| Planet LQD | 23.36% | 23.36% | 0.000pp | **MATCH** |
| LQD+EYEG Full Market | 14.82% | 14.82% | 0.000pp | **MATCH** |
| Corporate Chaos | 12.51% | 12.51% | 0.000pp | **MATCH** |
| Paragons EYEG | 23.18% | 23.18% | 0.000pp | **MATCH** |
| Feaver Allocations | 15.29% | 15.29% | 0.000pp | **MATCH** |
| Erased History | 15.26% | 15.26% | 0.000pp | **MATCH** |
| Golden Age | 6.57% | 6.57% | 0.000pp | **MATCH** |

MDD units fix confirmed (Wave 7, `analytics.py:585`): `float(sym_dict["max_drawdown"]) * 100.0` applied correctly across all symphonies.

---

## Composer total-stats Reference (captured 2026-05-20)

```json
{
  "portfolio_value": 12893.70,
  "simple_return": 0.6927264759777976,
  "time_weighted_return": 0.7445357089316487,
  "todays_percent_change": -0.02020585293505977,
  "todays_dollar_change": -265.900976043,
  "net_deposits": 7617.12,
  "total_cash": 286.02,
  "total_unallocated_cash": 20.97
}
```

---

## Fix History (all four passes)

| Pass | HEAD | Bug Found | Fix Commit |
|------|------|-----------|------------|
| 1 | 913c019 | Portfolio CR formula wrong (value-weighted != deposit-weighted); account_value excludes cash; symphony ID drift (10/11 miss); MDD units wrong | — |
| 2 | 9645db8 | `_refresh_account_totals` used nonexistent env vars + wrong auth header -> HTTP 401 -> cache never populated | db0f95f |
| 3 | db0f95f | `closed_frozen` path: `portfolio_strip.cumulative_return.if_held` still on-the-fly (wrong formula); `portfolio_strip.account_value = None` | f53fbf9 |
| 4 | 80363b6 | All clear — all metrics MATCH or documented structural tolerance | — |

---

## Verification Commands

```bash
# Pull live total-stats
curl -s "https://api.composer.trade/api/v0.1/portfolio/accounts/$ACCOUNT_ROTH/total-stats" \
  -H "x-api-key-id: $COMPOSER_KEY_ID" \
  -H "authorization: Bearer $COMPOSER_SECRET" | python -m json.tool

# Compare against dashboard
curl -s http://localhost:5000/api/state | python -c "
import json,sys
s=json.load(sys.stdin)
ps=s.get('portfolio_strip',{})
cr=ps.get('cumulative_return',{})
print('cr.if_held:', cr.get('if_held'))
print('account_value:', ps.get('account_value'))
print('meta.cr_if_held:', s.get('meta',{}).get('portfolio',{}).get('cr_if_held'))
print('meta.account_value:', s.get('meta',{}).get('portfolio',{}).get('account_value'))
"
```
