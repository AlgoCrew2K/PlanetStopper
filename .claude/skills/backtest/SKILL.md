---
name: backtest
description: Replay the Planet Stopper risk engine over a historical date range using saved SQLite state, producing a P&L summary and exit-decision log.
allowed-tools:
  - Read
  - Glob
  - Grep
  - Bash
  - Write
agent: general-purpose
---

# /backtest skill

## Purpose

Replay Planet Stopper's risk-engine decisions over a historical date range from saved SQLite state. Never touches live APIs or production data.

## Dynamic Context

Before doing anything else, orient yourself:

```
Project root: !`git rev-parse --show-toplevel`
DB files present: !`ls *.db 2>/dev/null || echo "no db files"`
```

## Arguments

```
/backtest <start-date> <end-date> [<symphony-id>]
```

- `start-date` / `end-date`: YYYY-MM-DD format; end must be >= start
- `symphony-id`: optional filter; omit to replay all symphonies

## Steps

### 1. Validate Arguments

- Confirm both dates match `YYYY-MM-DD` exactly. Reject malformed input immediately.
- Confirm `end-date >= start-date`. Reject if not.
- If `symphony-id` is provided, verify it is a non-empty string.

### 2. Confirm State DB Coverage

Use `Bash` with `sqlite3` (or `python -c`) to check that the state DB has rows covering the requested range:

```bash
sqlite3 <state_db_path> "SELECT MIN(date), MAX(date), COUNT(*) FROM <state_table>;"
```

If no rows exist for the range, emit a clear error and stop. Do not proceed with an empty replay.

### 3. Copy DB to /tmp (Read-Only Guard)

Copy the state DB to `/tmp/alphabot_backtest_<timestamp>.db` before opening it. Open the copy read-only. Never open the production DB for writes.

```bash
cp <state_db_path> /tmp/alphabot_backtest_$(date +%s).db
```

### 4. Run the Replay

Write a self-contained Python replay script to `/tmp/backtest_run.py` that:

- Imports `math_engine` from the project root (add root to `sys.path`)
- Reads state rows from the copied DB filtered by the date range (and `symphony-id` if provided)
- Replays risk decisions deterministically row-by-row
- Captures per-trade: `(symbol, date, decision, computed_stops, would_have_exited_at, pnl_delta)`

Execute it with `Bash`. Capture stdout/stderr. Fail loudly on import errors or DB errors.

### 5. Aggregate Results

From captured trade records compute:

- Total trades
- Win rate (%)
- Total P&L
- Average exit timing (bars/days to exit)
- "Alpha saved vs hold" delta (sum of `pnl_delta` where exit beat hold)

### 6. Emit Markdown Report

Write the report to `/tmp/backtest_<start>_<end>.md` and print it to stdout. Include:

- Run metadata: dates, symphony filter, DB path used, git SHA
- ASCII P&L summary table
- Exit-decision log (top 20 rows; full log path if truncated)

Example table:

```
| Metric              | Value     |
|---------------------|-----------|
| Trades              | 142       |
| Win Rate            | 61.3%     |
| Total P&L           | +$18,450  |
| Avg Exit (days)     | 4.2       |
| Alpha vs Hold       | +$3,210   |
```

## What You Must NOT Do

- **Never call live Composer or Alpaca APIs.** This is a replay-only operation. No HTTP calls to external brokers or data providers.
- **Never write to production SQLite DBs.** Always work on a `/tmp` copy. Open it read-only where possible.
- **Never place trades, even in dry-run modes that communicate with real brokers.** No order submission of any kind.
- **Never mutate `alpha_bot_execution.py` or `math_engine.py`** — import and call, do not edit.

## Walk-Through: `/backtest 2025-01-01 2025-03-01`

1. Validate: both dates well-formed; 2025-03-01 >= 2025-01-01. Pass.
2. Check DB: query confirms rows between 2025-01-01 and 2025-03-01 exist.
3. Copy DB to `/tmp/alphabot_backtest_<ts>.db`.
4. Write + run `/tmp/backtest_run.py`: imports `math_engine`, filters rows to Jan–Mar 2025, replays decisions, emits trade records.
5. Aggregate: compute win rate, total P&L, avg exit, alpha delta.
6. Write `/tmp/backtest_2025-01-01_2025-03-01.md` and print to stdout.
