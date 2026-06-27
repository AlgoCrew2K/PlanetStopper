# today-change-account-basis

**Status:** ready

## Summary

The dashboard hero "Today's Change" bot-vs-held shows phantom divergence even when no guard
has fired. Root cause: `if_held` is sourced from Composer's account-level
`todays_percent_change` (cash-inclusive denominator, `portfolio_tc` in
`_account_totals_cache`) while `dry_run` is sourced from
`analytics.get_portfolio_today_change` (value-weighted over symphony values — cash
excluded). Different denominators → phantom alpha from arithmetic, not guard events.

The cumulative-return path already has the B-1 fix
(`analytics.get_portfolio_cumulative_return_account_basis`, `analytics.py:1024`). This
feature adds the mirror fix for today-change: `get_portfolio_today_change_account_basis`.

Verified on the live droplet: all 11 symphonies untriggered
(`shadow_history.current_return == shadow_return`, `is_post_trigger=0`) yet the hero
showed bot ≠ held (e.g. +0.54 vs +0.46).

## Acceptance Criteria

- **AC-1:** `analytics.get_portfolio_today_change_account_basis(vw_tc, account_if_held_tc, account_value, symphony_value_sum)` exists with identical signature shape to `get_portfolio_cumulative_return_account_basis`.
- **AC-2:** With zero guard divergence (`vw_tc["dry_run"] == vw_tc["if_held"]`), `result["dry_run"]` equals `account_if_held_tc` exactly (no phantom alpha regardless of uninvested cash).
- **AC-3 (real divergence):** With `guard_delta_vw = vw_tc["dry_run"] - vw_tc["if_held"]` ≠ 0, `result["dry_run"] == account_if_held_tc + guard_delta_vw * (symphony_value_sum / account_value)`. Magnitude sanity: with cash present (`invested_frac < 1.0`), the account-basis guard alpha is strictly less than the VW guard delta.
- **AC-4 (cash basis):** The scaling correctly handles `account_value > symphony_value_sum` (uninvested cash); guard delta is attenuated, not inflated.
- **AC-5 (division guard):** `account_value <= 0` or `symphony_value_sum <= 0` → return `vw_tc` unchanged (no `ZeroDivisionError`).
- **AC-6 (None propagation):** `vw_tc["dry_run"] is None` or `vw_tc["if_held"] is None` → `{"if_held": account_if_held_tc, "dry_run": None}`.
- **AC-7 (strip integration):** `_compute_portfolio_strip` uses the new helper when `_account_totals_cache["portfolio_tc"]` is warm. With untriggered symphonies (zero VW guard divergence), `today_change["dry_run"] == today_change["if_held"]` (both on account basis).
- **AC-8 (cold-cache fallback):** When `portfolio_tc` is absent, the VW-both fallback path (`analytics.get_portfolio_today_change`, both bot and held on VW basis) is unchanged and still yields `dry_run == if_held` for untriggered symphonies.
- **AC-9 (cumulative consistency):** The B-1 cumulative path also yields `dry_run == if_held` when untriggered (regression guard; if it fails, flag it — do NOT silently carry a pre-existing failure).

## Architecture

### New function (analytics.py, mirrors `get_portfolio_cumulative_return_account_basis`)

```
get_portfolio_today_change_account_basis(
    vw_tc:              {"if_held": float, "dry_run": float | None}
    account_if_held_tc: float   — portfolio_tc from _account_totals_cache (Composer todays_percent_change * 100)
    account_value:      float   — total account value including cash
    symphony_value_sum: float   — sum of invested symphony values (cash excluded)
) -> {"if_held": account_if_held_tc, "dry_run": account-basis dry_run | None}
```

Formula (identical to B-1):
```
invested_frac  = symphony_value_sum / account_value
guard_delta_vw = vw_tc["dry_run"] - vw_tc["if_held"]   # pure guard effect, VW basis
dry_run_acct   = account_if_held_tc + guard_delta_vw * invested_frac
```

### Wire-in (app.py, around line 1195)

Replace the current:
```python
today_change = {"if_held": _cached_tc, "dry_run": analytics.get_portfolio_today_change(...).get("dry_run")}
```
With:
```python
_vw_tc = analytics.get_portfolio_today_change(symphonies_list, bot_state, trading_day=trading_day)
today_change = analytics.get_portfolio_today_change_account_basis(
    _vw_tc, _cached_tc, account_value, _symphony_value_sum,
)
```

`_symphony_value_sum` is already computed earlier in `_compute_portfolio_strip` for the cumulative-return path (app.py:1177). Reuse it — do not recompute.

## Edge Cases

- `account_value` or `symphony_value_sum` ≤ 0: return `vw_tc` unchanged.
- `vw_tc["dry_run"]` is `None`: return `{"if_held": account_if_held_tc, "dry_run": None}`.
- `vw_tc["if_held"]` is `None`: same as `dry_run=None` path (can't compute guard delta).
- Cold cache (`portfolio_tc` absent from `_account_totals_cache`): fallback path unchanged.
- `account_value == symphony_value_sum` (zero cash): `invested_frac = 1.0`; formula reduces to `dry_run_acct = account_if_held_tc + guard_delta_vw` — accounts share the same denominator in that edge case.

## Security Considerations

Analytics-only; no network, no DB write, no auth surface. Read-only math.

## Testing Strategy

- Unit tests for `get_portfolio_today_change_account_basis` with golden fixture
  `tests/fixtures/math/today_change_account_basis_basic.json` (inputs from captured
  Composer fixture, derived expected — never hardcoded producer-computed value).
- Integration test for `_compute_portfolio_strip`: mock `analytics.get_portfolio_today_change`
  to return a VW today-change that differs from the cached account-level `portfolio_tc`
  (simulating real cash-dilution arithmetic), then assert `today_change["dry_run"] ==
  today_change["if_held"]` after fix.
- Regression guard for cumulative B-1 (AC-9): confirm it still yields zero phantom alpha.
- All tests `-n0`, bounded, no live API.

## Scope Boundaries

- Only `analytics.py` (new helper) and `app.py` (wire-in at `_compute_portfolio_strip`).
- The VW fallback path (cold-cache `else` branch) is unchanged.
- Engine execution path (`alpha_bot_execution.py`) is not touched.
- No new DB schema, no new API endpoints.
