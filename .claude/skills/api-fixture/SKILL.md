---
name: api-fixture
description: Capture a live API response from Composer or Alpaca to a versioned JSON fixture file under tests/fixtures/. Used to build the test suite without hammering live APIs.
allowed-tools: Read, Glob, Grep, Bash, Write
agent: forked
---

# /api-fixture

Capture a live read-only API response and write it to a versioned fixture file.

## Usage

```
/api-fixture <provider> <endpoint-or-method> [<arg-json>]
```

- `provider` — `composer` or `alpaca`
- `endpoint-or-method` — method name as it appears in the codebase (e.g. `get_portfolio`, `get_latest_bar`)
- `arg-json` — optional JSON object of keyword args (e.g. `{"symbol":"SPY"}`)

## Dynamic Context

```
!`ls tests/fixtures 2>/dev/null || echo "no fixtures dir yet"`
```

## Steps

1. **Load credentials** — read `.env` via python-dotenv. Never echo or log credential values.
2. **Locate wrapper** — grep `*.py` for the method name. If not found, fail:
   > "no wrapper found — name a method that exists in the code"
3. **Safety check** — if the method name contains any of `liquidate`, `submit`, `cancel`, `delete`, `place_order` (case-insensitive), refuse:
   > "refused — this skill is read-only; that method name suggests a write operation"
4. **Invoke** — run a minimal Python harness:
   ```bash
   python -c "
   from dotenv import load_dotenv; load_dotenv()
   from <module> import <method>
   import json, sys
   result = <method>(**<args>)
   print(json.dumps(result if isinstance(result, (dict,list)) else vars(result), sort_keys=True, indent=2))
   "
   ```
5. **Scrub secrets** — scan the JSON for keys matching `/(key|token|secret|account.?id|auth)/i`. Replace each matched value with `"**REDACTED**"`. Print which keys were redacted before writing.
6. **Write fixture** — path: `tests/fixtures/<provider>/<endpoint>__<timestamp>.json` where timestamp is `YYYYMMDD_HHMMSS`. Never overwrite an existing file.
7. **Report** — print the fixture path, file size in bytes, and the first 100 characters of the JSON.

## What You Must NOT Do

- NEVER write API keys, tokens, secrets, or full account-identifier strings to any fixture file.
- NEVER make POST, DELETE, or PATCH requests. This skill is read-only.
- NEVER overwrite an existing fixture file — always use a timestamped filename.
- NEVER echo credential values to the terminal at any point.

## Examples

**Capture a Composer portfolio:**
```
/api-fixture composer get_portfolio
```
Greps `*.py` for `get_portfolio`, invokes it with no args, scrubs secrets, writes to:
`tests/fixtures/composer/get_portfolio__20260512_143022.json`

**Capture an Alpaca bar with a symbol arg:**
```
/api-fixture alpaca get_latest_bar '{"symbol":"SPY"}'
```
Greps `*.py` for `get_latest_bar`, invokes it with `symbol="SPY"`, scrubs secrets, writes to:
`tests/fixtures/alpaca/get_latest_bar__20260512_143105.json`
