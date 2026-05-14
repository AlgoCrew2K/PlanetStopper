# Composer Per-Symphony CR + MDD — API Availability (Read-Only Check)

**Date:** 2026-05-14
**Author:** composer-alpaca-integration (diagnosis only — no code, no edits)
**Method:** Doc review + empirical read-only GET against live Composer API
**Account UUID:** `880be47e-...` (redacted)
**Endpoint hit:** `GET /api/v0.1/portfolio/accounts/{account-id}/symphony-stats-meta` (via `fetch_symphony_stats()` in `alpha_bot_execution.py`)

---

## VERDICT

**Composer DOES provide per-symphony CR + MDD via `symphony-stats-meta` — fields `simple_return` (cumulative return, since-inception, deposit-adjusted) and `max_drawdown` (per-symphony MDD).**

No persistence path is needed. The Composer API serves both figures directly, per-symphony, in a single call AlphaBot already makes every cycle (`fetch_symphony_stats`). The internal audit's assumption (CR/MDD only via the post-mortem triggered-symphonies path) is **wrong** — it overlooked that `symphony-stats-meta` returns these for *all* symphonies, not just triggered ones.

---

## Empirical Evidence

Pulled the full per-symphony object for 11 symphonies on the live account; inspected 3 in detail. Every symphony returned populated `simple_return`, `time_weighted_return`, `annualized_rate_of_return`, `max_drawdown`, and `sharpe_ratio` — these are not null/absent.

Redacted sample (3 symphonies, return/drawdown-relevant fields only):

```
--- SYMPHONY 0 ---
  id                        = '<redacted>'
  name                      = '(INVEST:CRYPTO) We do a Little Trolling ...'
  simple_return             = 0.0          # see note below — 0.0 for a fully-withdrawn/recycled symphony
  time_weighted_return      = 3.13443
  annualized_rate_of_return = 1.1695
  last_percent_change       = -0.0194      # today's change only — NOT cumulative
  max_drawdown              = 0.2552
  sharpe_ratio              = 1.5737
  net_deposits              = 0.0
  value                     = 1610.26
  deposit_adjusted_value    = 4134.42
  invested_since            = '2024-07-12'
  as_of                     = '2026-05-14'

--- SYMPHONY 1 ---
  id                        = '<redacted>'
  name                      = '(INVEST) Planet of Projected Inflation: Corporate Chaos 2060'
  simple_return             = 0.6633
  time_weighted_return      = 0.38406
  annualized_rate_of_return = 0.3415
  last_percent_change       = -0.0208
  max_drawdown              = 0.1495
  sharpe_ratio              = 1.1076
  net_deposits              = 658.5
  value                     = 1095.28
  deposit_adjusted_value    = 456.74
  invested_since            = '2025-04-03'
  as_of                     = '2026-05-14'

--- SYMPHONY 2 ---
  id                        = '<redacted>'
  name                      = '(INVEST) Planet of Hunted Cascades - Land of Intelligent Allocations'
  simple_return             = 0.80597
  time_weighted_return      = 0.3149
  annualized_rate_of_return = 0.2912
  last_percent_change       = -0.0196
  max_drawdown              = 0.3226
  sharpe_ratio              = 0.7719
  net_deposits              = 638.04
  value                     = 1152.28
  deposit_adjusted_value    = 1577.87
  invested_since            = '2025-04-04'
  as_of                     = '2026-05-14'
```

---

## Field Semantics — Cumulative Return

Composer exposes **three** distinct per-symphony cumulative-return measures. They are NOT the same number — pick deliberately:

| Field | Type | Window | Semantics |
|---|---|---|---|
| `simple_return` | decimal fraction (`0.6633` = +66.33%) | since `invested_since` | Cumulative simple return on capital, deposit-adjusted. The plain "total % return since inception" most operators expect. |
| `time_weighted_return` | decimal fraction (`3.13443` = +313.4%) | since `invested_since` | Cumulative time-weighted return — removes the distorting effect of deposit/withdrawal timing. Industry-standard for comparing strategy skill independent of cashflow timing. |
| `annualized_rate_of_return` | decimal fraction (`1.1695` = +116.95%/yr) | annualized over holding period | CAGR-style annualized figure, not a raw cumulative. |
| `last_percent_change` | decimal fraction | **single day only** | Today's move. This is what the audit likely mistook for "the only return field." NOT cumulative. AlphaBot already consumes this at `alpha_bot_execution.py:404,461`. |

**Recommendation for the dashboard CR column:** use `simple_return` for the headline "Cumulative Return" (matches naive operator expectation), or `time_weighted_return` if the dashboard's purpose is strategy-skill comparison. Both are first-class API fields. Do NOT derive CR from `last_percent_change`.

**Caveat on `simple_return = 0.0` (Symphony 0):** one symphony returned `0.0` for `simple_return` while showing `time_weighted_return = 3.13443` and a non-zero `value`. This is consistent with a symphony that has been fully withdrawn and re-funded (`net_deposits = 0.0`, `deposit_adjusted_value` ≠ `value`) — `simple_return` can zero out under certain deposit/withdrawal histories where TWR does not. Dashboard scoping should decide whether to fall back to `time_weighted_return` when `simple_return` is `0.0` or anomalous. This is a per-symphony data-quality edge, not an API gap.

## Field Semantics — Max Drawdown

| Field | Type | Window | Semantics |
|---|---|---|---|
| `max_drawdown` | decimal fraction, positive (`0.2552` = 25.52% peak-to-trough loss) | since `invested_since` (full holding history) | Per-symphony maximum drawdown. Populated for every symphony in the response. Value is the magnitude of the worst peak-to-trough decline; positive number, not signed. |

`max_drawdown` is a documented top-level field on the symphony object (confirmed in `reverification__2026-05-13.md` schema snapshot, line 73) and is empirically populated for all 11 symphonies tested.

---

## Cross-Check Against Docs

`reverification__2026-05-13.md` (Schema Snapshot section) lists both `simple_return` and `max_drawdown` as documented top-level fields on the symphony object. The empirical pull confirms the docs — no drift, no discrepancy. The baseline doc's one-line description of `symphony-stats-meta` as a "per-symphony rollup: holdings, returns, sharpe, max_drawdown" is accurate and not an overstatement.

## Other Endpoints (not needed)

`GET .../portfolio/accounts/{account-id}/symphonies/{symphony-id}` returns a per-symphony historical value *series* — useful if the dashboard later wants to render a CR/drawdown *chart over time*. But for a single CR + MDD *figure* per symphony, `symphony-stats-meta` is sufficient and is the cheaper call (one request covers all symphonies vs one-per-symphony).

---

## Build-Scope Implication

- **Skip the persistence path.** No need to snapshot/store CR or MDD in the state DB for the dashboard's per-symphony figures.
- AlphaBot already calls `fetch_symphony_stats()` every cycle; the dashboard feature can read `simple_return` / `time_weighted_return` / `max_drawdown` straight off that existing response (or off a fresh read-only call from the dashboard's read path).
- Only consideration: `fetch_symphony_stats` currently returns the parsed `symphonies` list; the dashboard scope just needs to surface three already-present fields. No new endpoint, no new schema.
- If the dashboard wants CR/MDD *time-series charts* (not just current figures), that is the separate `symphonies/{symphony-id}` endpoint — different scope, still no persistence path.
