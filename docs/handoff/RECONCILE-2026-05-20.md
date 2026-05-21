# Composer ground-truth reconciliation — HEAD 913c0194512e4f62c13a44f4f7920fc3e8f6f35b

**Branch:** feat/studio-design-handoff
**Date:** 2026-05-20
**Fixtures:** `tests/fixtures/composer/reconcile-2026-05-20/symphony_stats_meta.json` (captured live), `tests/fixtures/composer/reconcile-2026-05-20/total_stats.json` (captured live)
**Account:** ROTH IRA (`880be47e-efe4-4b44-9d83-b6d86098fe0d`)
**Endpoints hit:** `GET /api/v0.1/portfolio/accounts/{id}/symphony-stats-meta`, `GET /api/v0.1/portfolio/accounts/{id}/total-stats`
**Dashboard endpoints pulled:** `GET /api/state` (http://127.0.0.1:5000)

---

## Summary

- **6 MATCH** rows (per-symphony CR, per-symphony MDD portfolio aggregate, per-symphony TC values internally consistent)
- **5 WRONG** rows (portfolio-level CR, portfolio-level TC, account value, symphony ID drift, n2oo TWR inflation)
- **1 CRITICAL structural issue** (symphony ID mismatch between Composer API and bot_state — see root cause section)

---

## Reconciliation Table

| Metric | Composer (truth) | Dashboard | Delta | Verdict |
|--------|------------------|-----------|-------|---------|
| Account value | $12,945.18 | $12,922.60 | -$22.58 | WRONG |
| Portfolio CR (if_held) | 69.9486% | 67.0753% | -2.8733% | WRONG |
| Portfolio CR (dry_run) | N/A | -0.9638% | N/A | N/A |
| Portfolio TC (if_held) | -1.6293% | -1.8246% | -0.1953% | WRONG |
| Portfolio TC (dry_run) | N/A | -1.7781% | N/A | N/A |
| Portfolio MDD (if_held) | 19.18% | 19.18% | ~0% | MATCH |
| Land of Feaver'd Allocations CR | 9.6650% | 9.6320% | -0.033% | MATCH |
| Planet LQD CR | 34.2000% | 34.2520% | +0.052% | MATCH |
| Planet of the Reasonabilists CR | 74.9330% | 74.9320% | -0.001% | MATCH |
| Planet of Projected Inflation CR | 67.7630% | 67.7600% | -0.003% | MATCH |
| Planet of Erased History CR | -0.2460% | -0.2750% | -0.029% | MATCH |
| Planet of Hunted Cascades CR | 79.7320% | 79.7320% | 0.000% | MATCH |
| LQD + EYEG 5 ways CR | 3.1900% | 3.1700% | -0.020% | MATCH |
| Planet of the Paragons CR | -7.7000% | -7.7940% | -0.094% | MATCH |
| We do a Little Trolling (n2oo) CR | 318.467% (TWR×100) | 318.467% (TWR×100) | 0.000% | MATCH* |
| Corporate Chaos 5 ways CR | -3.6790% | -3.6990% | -0.020% | MATCH |
| Planet of the Golden Age CR | 20.3850% | 20.3860% | +0.001% | MATCH |

*n2oo MATCH is a TWR fallback for both sides — see anomaly note below.

---

## Root Cause for Each WRONG Row

### 1. Portfolio CR (if_held) — WRONG, dashboard underreports by 2.8733%

**File:line:** `app.py:442` (`analytics.get_portfolio_cumulative_return`) → `analytics.py:690-705` (`get_portfolio_cumulative_return`) → `analytics.py:613-670` (`_value_weighted_portfolio`)

**What the analytics layer does wrong:**
The dashboard computes portfolio CR as a value-weighted _average_ of per-symphony `simple_return` values:

```
portfolio_cr_if_held = Σ(symphony_simple_return * symphony_value) / Σ(symphony_value)
```

Composer's `total-stats` endpoint provides the correct portfolio-level `simple_return` directly:

```
simple_return = (portfolio_value - net_deposits) / net_deposits = 69.9486%
```

The two are not equivalent. Value-weighting by current market value distorts the return because symphonies that performed well have grown in value, increasing their weight in the denominator and pulling the average toward the center. The correct portfolio return is deposit-weighted (or use Composer's pre-computed `simple_return` from `total-stats`).

**Numbers:**
- Composer `total-stats.simple_return`: 69.9486%
- Dashboard value-weighted avg: 67.0753%
- Correct answer: **69.9486%** (from `total-stats`) or **75.1502%** (TWR from `total-stats` — preferred for deposit-adjusted comparison)

**Fix recipe:**
`_compute_portfolio_strip` in `app.py:402-458` should pull `total-stats` from Composer and use its `simple_return` (or `time_weighted_return`) for the portfolio-level CR rather than the value-weighted average. The per-symphony CR values passed to `analytics.get_portfolio_cumulative_return` are correct at the symphony level — only the portfolio aggregate formula is wrong.

Alternative (no extra API call): use the correct formula bottom-up:
```python
cr_if_held = (sum(sym_value) - sum(sym_net_deposits)) / sum(sym_net_deposits) * 100
```
But this excludes account-level cash and will still drift from Composer's figure by ~$21 (account cash not in any symphony). The cleanest fix is to pull `total-stats` and use its `simple_return`.

---

### 2. Portfolio TC (if_held) — WRONG, dashboard shows -1.8246% vs Composer -1.6293%

**File:line:** `app.py:439` (`analytics.get_portfolio_today_change`) → `analytics.py:673-687` → `analytics.py:404-439` (`get_symphony_today_change`) → `analytics.py:613-670`

**What the analytics layer does wrong:**
Dashboard TC uses bot_state `current_return` values from the last execution cycle, which may be stale by minutes relative to when the dashboard is polled. Composer's `total-stats.todays_percent_change` is the live value at time of API call.

More specifically, the dashboard computes:
```
tc_if_held = value_weighted_avg(current_return)
```
where `current_return` = `last_percent_change * 100` from the last engine cycle. The per-symphony `last_percent_change` values in the fixture show a value-weighted aggregate of -1.6052%, and `total-stats.todays_percent_change` = -1.6293%. The dashboard shows -1.8246%, suggesting the bot_state values are from a slightly earlier/different market price point.

**Root cause:** Staleness. The engine runs on a 1-minute cadence; the dashboard poll and the fixture capture are asynchronous. The `iaSOOUsmnCJH` symphony shows `current_return = -5.63%` in bot_state but `last_percent_change * 100 = -3.72%` in the live Composer response. This single symphony accounts for most of the delta (it has $1031 value and a 1.91 percentage point difference).

**Severity:** Low — this is a staleness artifact, not a systematic formula bug. The TC formula itself is correct; the data is just up to 1-2 minutes old.

---

### 3. Account value — WRONG, dashboard underreports by $22.58

**File:line:** `app.py:431-435` (account_value computation in `_compute_portfolio_strip`)

**What the analytics layer does wrong:**
```python
account_value = sum(v.get("current_value") or 0.0 for v in bot_state.values() if isinstance(v, dict))
```
This sums only symphony-allocated capital. Composer's `total-stats.portfolio_value` = $12,945.18 includes:
- Symphony values: $12,923.98
- `total_cash`: $233.02 (symphony cash + account unallocated cash)
- `total_unallocated_cash`: $20.97

The $22.58 delta = account-level cash not allocated to any symphony (`total_cash - (symphony_cash_embedded_in_values)`). The dashboard excludes unallocated cash.

**Fix recipe:** Pull `total-stats` and use `portfolio_value` directly for account_value. Alternatively, add `total_unallocated_cash` from `total-stats` to the symphony sum.

---

### 4. Symphony ID mismatch — structural issue (not visible in metrics, but breaks `_persist_composer_fields_to_bot_state`)

**File:line:** `alpha_bot_execution.py:623-625`, `alpha_bot_execution.py:143-148`

**What the code does:**
```python
for sym in symphonies:
    s_id = sym["id"]      # Composer's current ID, e.g., '5XjzXjdGnjh99MIsdM97'
    if s_id in bot_state: # bot_state has '5XjzXjdGnjh99M7CRUZB1DvNs'
        ...               # NEVER EXECUTES for 10 of 11 symphonies
```

**IDs in Composer live API vs bot_state (2026-05-20):**
| Composer ID | bot_state ID | First 12 chars match? |
|-------------|-------------|----------------------|
| `5XjzXjdGnjh99MIsdM97` | `5XjzXjdGnjh99M7CRUZB1DvNs` | YES — same symphony |
| `8FAXAnQmYi1INDubazeC` | `8FAXAnQmYi1INDjT62OBuTnR8` | YES — same symphony |
| `n2ooAZTvBRN6ZzpMmWmU` | `n2ooAZTvBRN6ZzpMmWmU` | **EXACT MATCH** |
| (9 others) | (9 others) | Same prefix, different suffix |

10 of 11 symphonies have **different IDs** in Composer vs bot_state. Only `n2ooAZTvBRN6ZzpMmWmU` (the crypto Trolling Planet) has an exact ID match.

**Consequence:** `_persist_composer_fields_to_bot_state` is silently a no-op for 10 of 11 symphonies every cycle. The `simple_return`, `net_deposits`, `time_weighted_return`, and `max_drawdown` fields stored in bot_state are from a **prior epoch** when the IDs matched. They happen to still be close to correct (the data hasn't changed much) which is why per-symphony CR values show small deltas rather than catastrophic errors. However, this is fragile — any significant CR movement will leave bot_state with stale inception metrics.

**How the initial seeding happened:** The IDs in bot_state were written when those symphonies were first seen in a Composer response that returned different IDs. Composer may have regenerated IDs at some point (possibly related to symphony edits/copies). The bot does not detect this.

**Fix recipe (impl-backend):**
In `alpha_bot_execution.py:623-625`, the lookup should match on name OR use a prefix-based ID reconciliation. One approach:
```python
# Build prefix-based lookup for existing bot_state entries
_prefix_to_state_key = {
    k[:12]: k for k in bot_state if isinstance(bot_state[k], dict) and 'name' in bot_state[k]
}
# Then when processing a Composer symphony:
s_id = sym["id"]
state_key = s_id if s_id in bot_state else _prefix_to_state_key.get(s_id[:12], s_id)
```
This is a diagnostic recommendation. The correct architectural fix depends on whether Composer ID changes are expected to happen again and whether name-based matching is reliable (names can also change). The safest approach may be to match on name when the exact ID is not found.

---

### 5. n2ooAZTvBRN6 CR anomaly — 318.47% (informational, not a dashboard bug)

**Note:** The `(INVEST:CRYPTO) We do a Little Trolling Planet's Mix` symphony shows `simple_return = 0.0` and `net_deposits = 0.0` from Composer. This triggers the fallback to `time_weighted_return = 3.18467` → **318.47% CR**.

This is correct behavior per the analytics fallback rule (when simple_return==0 AND net_deposits==0, use TWR). The symphony shows zero net_deposits because it was funded via a rebalance or allocation rather than a direct deposit. The 318% TWR reflects the price appreciation since inception (invested_since: 2024-07-12).

Dashboard correctly shows the same 318.47% for this symphony. MATCH.

**Warning:** This symphony has outsized influence on the portfolio value-weighted CR because:
1. It has the largest value ($1,629.91)
2. Its CR (318.47%) is 4-5x higher than any other symphony
When the value-weighted average is taken, this symphony's inflated CR pulls the portfolio aggregate significantly higher than Composer's deposit-weighted `simple_return`. This is the primary driver of the 2.87% discrepancy in issue #1 above.

---

## Composer `total-stats` Portfolio Metrics (Ground Truth)

| Metric | Composer Value | Dashboard Value | Verdict |
|--------|---------------|-----------------|---------|
| portfolio_value | $12,945.18 | $12,922.60 | WRONG (-$22.58) |
| simple_return | 69.9486% | 67.0753% | WRONG (-2.87%) |
| time_weighted_return | 75.1502% | not shown | N/A |
| todays_percent_change | -1.6293% | -1.8246% | WRONG (-0.195%) |
| max_drawdown (total-stats.metrics) | 24.47% | 19.18% | DIFFERENT* |
| net_deposits | $7,617.12 | not surfaced | N/A |

*total-stats.metrics.max_drawdown = 24.47% is the portfolio-level peak-to-trough since inception. The dashboard's 19.18% is the value-weighted average of per-symphony MDD values, which is a different metric (average of individual symphony drawdowns, not the portfolio-level drawdown). Both are internally consistent; they measure different things.

---

## Fix Priority

1. **HIGH — U7-01:** Portfolio CR formula (`app.py:442` → `analytics.py:690`) should use Composer `total-stats.simple_return` (69.9486%) not value-weighted average of symphony SRs (67.0753%). Pull `total-stats` endpoint in `_compute_portfolio_strip`.

2. **HIGH — Account value:** `app.py:431-435` should use `total-stats.portfolio_value` ($12,945.18), not the sum of symphony values ($12,922.60). Missing $22.58 = unallocated cash.

3. **MEDIUM — Symphony ID drift:** `alpha_bot_execution.py:623-625` should reconcile by prefix or name when exact ID lookup fails. Without this, `_persist_composer_fields_to_bot_state` is a silent no-op for 10/11 symphonies.

4. **LOW — TC staleness:** Not a formula bug; a staleness artifact from 1-min cycle cadence. Not fixable without moving to a push/event model or accepting ~1-2 minute lag.

---

## Fixtures (provenance: captured-from-producer, 2026-05-20)

- `tests/fixtures/composer/reconcile-2026-05-20/symphony_stats_meta.json` — live Composer response
- `tests/fixtures/composer/reconcile-2026-05-20/total_stats.json` — live Composer response
