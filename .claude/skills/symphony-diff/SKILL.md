---
name: symphony-diff
description: Compare two symphonies head-to-head — current walk-forward parameters, recent trade decisions, win-rate, and alpha-saved.
allowed-tools:
  - Read
  - Glob
  - Grep
  - Bash
  - Write
agent: general-purpose
---

# /symphony-diff

**Usage:** `/symphony-diff <symphony-a> <symphony-b> [<lookback>]`

- `symphony-a`, `symphony-b` — required; symphony identifiers to compare
- `lookback` — optional; how far back to pull trade history (default: `30d`)

**Dynamic context:** `!`ls *.db 2>/dev/null``

---

## Steps

**1. Validate both symphonies exist**
Glob for `*.db` files. Query both the state DB and the optimization DB for each symphony ID. If either symphony is absent from both DBs, list all known symphony IDs and exit:
```
Symphony 'SYMPH-XXX' not found in state or optimization DB. Known symphonies:
  SYMPH-001
  SYMPH-002
```

**2. Pull current parameter set for each symphony**
Query the optimization DB for the active (latest committed) parameter set per symphony. Capture all parameter names and values for both.

**3. Pull trade decisions and outcomes within lookback window**
Query the state DB for all trade records within the lookback window for each symphony. Collect: entry/exit timestamps, direction, outcome (win/loss), alpha-saved value, and time-to-exit in minutes.

**4. Compute per-symphony outcome metrics**
For each symphony compute:
- `trade_count` — total trades in window
- `win_rate` — wins / trade_count (as %)
- `alpha_saved` — sum of alpha-saved values
- `avg_time_to_exit` — mean minutes from entry to exit

If either symphony has fewer than 10 trades in the lookback window, flag that symphony with:
```
⚠ SYMPH-XXX: n=<count> — sample too small; outcome metrics unreliable.
```
Still render the metrics, but label the column header `[n too small]`.

**5. Emit markdown comparison report**
Structure:

```
# Symphony Diff: SYMPH-001 vs SYMPH-002 (lookback: 30d)

## Parameters
| Parameter         | SYMPH-001        | SYMPH-002        |
|-------------------|------------------|------------------|
| `lookback_window` | `20`             | `25`             |
| `stop_loss_pct`   | `0.02`           | `0.02`           |

## Outcomes
| Metric              | SYMPH-001 | SYMPH-002 |
|---------------------|-----------|-----------|
| Trade count         | 47        | 31        |
| Win rate            | 61.7%     | 54.8%     |
| Alpha saved         | 4.21      | 2.87      |
| Avg time to exit    | 83 min    | 107 min   |

## Key Differences
- **`lookback_window`**: 20 → 25 (+25% delta) ⚠
- **Win rate**: 61.7% vs 54.8% (>2σ separation) ⚠
```

Param deltas: show every parameter regardless of whether it matches (identical params use plain monospace, no bold). Flag any param where `|%Δ| > 25%` with `⚠`. Flag any outcome metric where the two values are more than 2σ apart (use pooled stddev across both sets) with `⚠`. Collect all flagged items into the "Key Differences" callout.

**6. Write report to disk**
Write to:
```
reports/symphony-diff__<a>_vs_<b>__<timestamp>.md
```
Where `<timestamp>` is `YYYYMMDD_HHMMSS`. Create the `reports/` directory if absent. Confirm the path in terminal output.

---

## Mental walkthroughs

**`/symphony-diff SYMPH-001 SYMPH-002`**
1. Both symphonies confirmed in state + optimization DBs
2. Current params fetched for each
3. Trades from last 30d pulled; SYMPH-001 has 47, SYMPH-002 has 31 — both ≥ 10, no flag
4. Metrics computed for both
5. `lookback_window` flagged (25% delta); win-rate flagged (>2σ)
6. Report written to `reports/symphony-diff__SYMPH-001_vs_SYMPH-002__20260512_143000.md`

**`/symphony-diff SYMPH-001 SYMPH-002 7d`**
1-4. Same as above, but trade window restricted to last 7 days
   - If SYMPH-002 has only 6 trades: flag `⚠ SYMPH-002: n=6 — sample too small` on outcomes column header
5-6. Report written with `7d` noted in the header line

---

## What You Must NOT Do

- **Never write to or modify either DB.** All queries are read-only.
- **Never invent statistics.** If data is absent or `n < 10`, flag it — do not fill in estimated or extrapolated values.
- **Never omit identical parameters.** All params appear in the table; identical ones use plain monospace with no bold or emphasis — they matter for narrative completeness.
