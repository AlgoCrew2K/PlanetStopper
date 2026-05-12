---
name: discord-test
description: Send a probe alert through reporting.py to verify the Discord webhook + QuickChart pipeline end-to-end.
allowed-tools:
  - Read
  - Bash
  - Grep
---

# /discord-test [<embed-type>]

Send a probe (non-live) alert through `reporting.py` to confirm the Discord webhook and QuickChart image pipeline are working.

## Dynamic Context

Available send-functions in reporting.py:
```
!`grep -n "^def " reporting.py 2>/dev/null | head -20`
```

## Arguments

| Arg | Required | Description |
|-----|----------|-------------|
| `<embed-type>` | No | Function name from reporting.py (e.g. `exit_alert`, `eod_summary`). Omit for a minimal heartbeat embed. |
| `--force` | No | Bypass market-hours guard (09:30–16:00 ET). |

## Steps

1. **Validate embed-type** — Grep `reporting.py` for `^def ` functions. If the user named a type that does not match, list available functions and stop.

2. **Market-hours guard** — Check current ET time. If 09:30–16:00 ET and `--force` was not passed, abort with:
   ```
   Probe aborted: market hours active (09:30-16:00 ET). Pass --force to override.
   ```

3. **Build dummy data** — Construct minimal placeholder arguments for the chosen function:
   - symbol: `"PROBE"`
   - P&L values: `0.0`
   - timestamps: current UTC datetime
   - Any required list/dict fields: empty or single-element stubs
   - Prepend `[PROBE]` to the embed title inside the call

4. **Invoke** — Run:
   ```bash
   python -c "from reporting import <fn>; <fn>(<dummy_args>)"
   ```

5. **Check result** — Capture stdout/stderr. Confirm Discord returned a 2xx response (look for `204` or no error in output). On failure, surface the raw error.

6. **Confirm** — Print:
   ```
   Probe sent. Check the Discord channel for a probe alert at <UTC timestamp>.
   ```

## What You Must NOT Do

- Never echo the webhook URL in any output or log.
- Never send an embed without `[PROBE]` in the title — live ops must be able to distinguish probes from real alerts.
- Never run during market hours (09:30–16:00 ET) unless `--force` is explicitly passed.
- Never use real position data, real symbols, or real P&L figures.

## Examples

**Default heartbeat:**
```
/discord-test
```
Sends a minimal heartbeat embed tagged `[PROBE]` using placeholder data.

**Specific embed type:**
```
/discord-test exit_alert
```
Greps `reporting.py` for `exit_alert`, builds stub arguments (symbol=`PROBE`, pnl=`0.0`, timestamp=now), invokes `from reporting import exit_alert; exit_alert(...)`, and confirms Discord acknowledged.
