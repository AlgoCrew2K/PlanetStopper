# Account-Basis Fixture

## Provenance

Derived from `tests/fixtures/composer/v3-audit/total_stats.json` (captured-from-producer 2026-06-04).

All numeric fields are verbatim from the live Composer total-stats API response.
The `_provenance` key in the JSON records the derivation.

## Purpose

Drives tests for `analytics.get_portfolio_cumulative_return_account_basis` (B-1 fix).

Key values:
- `portfolio_value`: 12893.7 (account-level, cash-inclusive)
- `simple_return`: 0.6927264759777976 (basis for `if_held` = 69.27%)
- Sum of per-symphony values from v3-audit `symphony_stats_meta.json`: 12872.73
- Invested fraction: 12872.73 / 12893.7 ≈ 0.9984 (99.8% deployed)

The 20.97 gap (total_unallocated_cash) is what makes Bot/Held incommensurable
without the account-basis scaling.
