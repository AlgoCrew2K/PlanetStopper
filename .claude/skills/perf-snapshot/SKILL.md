---
name: perf-snapshot
description: Capture a point-in-time snapshot comparing live Planet Stopper performance against the counterfactual (no-Planet Stopper hold) for current/recent positions. Computes Guard Alpha = P&L delta from Planet Stopper early exits vs holding.
allowed-tools: Read, Glob, Grep, Bash, Write
agent: general-purpose
---

# /perf-snapshot [<lookback>] [--symphony <id>]

Capture Guard Alpha: what Planet Stopper's exits actually saved vs. holding each position.

## Dynamic Context

Current databases: `!`ls *.db 2>/dev/null``
Snapshot timestamp: `!`date -u +%Y-%m-%dT%H:%MZ``

## Arguments

| Arg | Format | Default |
|-----|--------|---------|
| `<lookback>` | `^\d+[dwmy]$` or `^ytd$` | `1d` (last 24 h) |
| `--symphony <id>` | any string | all symphonies |

## Steps

### 1. Validate lookback

Regex must match `^\d+[dwmy]$|^ytd$`. If it does not, print:
```
Error: invalid lookback "<value>". Use e.g. 7d, 4w, 3m, 1y, ytd.
```
Then exit without touching any DB or writing any file.

### 2. Pull exits from state DB

Read-only query against the state DB(s) found in step 0. Retrieve all exits within the lookback window:
- `symbol`, `exit_price`, `exit_time`, `would_have_held_until`, `position_size`, `direction` (long/short)

If `--symphony <id>` is supplied, filter to that symphony only.

### 3. Fetch counterfactual prices from Alpaca

For each trade: fetch the closing price at `would_have_held_until` (or current market price if that timestamp is in the future / position still open).
Use READ-ONLY Alpaca endpoints only (historical bars or latest quote).

### 4. Compute per-trade Guard Alpha

Formula (print this formula in the report — math-transparency is mandatory):

```
alpha_saved = (exit_price - hold_price) × position_size   # long
alpha_saved = (hold_price - exit_price) × position_size   # short
```

`hold_price` = counterfactual price from step 3.

### 5. Aggregate

Compute and display — **show the formula for each metric**:

- `total_alpha_saved  = sum(alpha_saved)`
- `pct_positive       = count(alpha_saved > 0) / n × 100`
- `worst_decision     = min(alpha_saved)` (most negative trade)
- `n                  = total trade count in window`

If `n < 5`: do NOT report alpha figures. Print:
```
n too small (n=<count>): need at least 5 trades to report Guard Alpha.
```
Then exit without writing a report file.

### 6. Write markdown report

Path: `reports/perf-snapshot__<timestamp>.md`
Timestamp format: `YYYYMMDDTHHMMZ` (UTC).

Report must include: lookback, symphony filter (or "all"), n, the formula block from step 4, per-trade table, and all aggregates from step 5.

### 7. Print inline summary + report path

```
Guard Alpha snapshot — <lookback> | symphony: <id or "all">
  Trades analysed : <n>
  Total α saved   : <total_alpha_saved>
  % positive      : <pct_positive>%
  Worst decision  : <worst_decision>

Full report: reports/perf-snapshot__<timestamp>.md
```

## What You Must NOT Do

- Never call Alpaca write endpoints (orders, account changes, etc.)
- Never modify the state DB (no INSERT, UPDATE, DELETE, DROP)
- Never report alpha figures when `n < 5` — print the "n too small" message instead
- Never omit the formula block from the report — math-transparency is non-negotiable

## Examples

**`/perf-snapshot`**
Last 24 h, all symphonies. Validates default lookback `1d`, pulls exits, fetches counterfactual prices, computes Guard Alpha, writes `reports/perf-snapshot__<ts>.md`, prints inline summary.

**`/perf-snapshot 7d`**
Last 7 days, all symphonies. Same flow with a 7-day window.

**`/perf-snapshot 30d --symphony SYMPH-001`**
Last 30 days, filtered to symphony `SYMPH-001`. Per-trade table and aggregates cover only that symphony's exits.
