---
name: db-inspect
description: Read-only SQLite query helper for Planet Stopper state + optimization databases. Lists tables, samples rows, or runs a user-supplied SELECT.
allowed-tools:
  - Bash
  - Read
  - Glob
---

# /db-inspect

Inspect Planet Stopper's SQLite databases without booting the dashboard.

## Dynamic Context

Current DB files in project root:
`!`ls -la *.db 2>/dev/null``

## Modes

| Invocation | Behavior |
|---|---|
| `/db-inspect` | List all tables in each DB with row counts |
| `/db-inspect tables <state\|optimization>` | Print CREATE TABLE schema for one DB |
| `/db-inspect sample <table> [<n>]` | SELECT * LIMIT n (default 10, hard cap 100) |
| `/db-inspect query "<SQL>"` | Run a single SELECT statement |

## Steps

1. **Locate DBs.** Glob `*.db` from the project root. Identify state DB vs optimization DB by filename (state DB contains "state"; optimization DB contains "optuna" or "optimization"). If ambiguous, read `database.py` to confirm paths.

2. **Validate mode.** Parse the arg string to select one of the four modes above. Default to list mode when no args are given.

3. **Safety check for `query` mode.** Strip leading/trailing whitespace and normalize to uppercase. Reject if the statement matches:
   ```
   /(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|PRAGMA\s+\w+\s*=|ATTACH)/i
   ```
   Return an error message explaining the rejection — do not execute.

4. **Execute via sqlite3 read-only.**
   - Always open with: `sqlite3 -readonly <db_file>`
   - For list mode: `.tables` then `SELECT COUNT(*) FROM <table>` per table
   - For tables mode: `.schema`
   - For sample/query mode: run the SQL with a mandatory `LIMIT` appended if absent (default 100)

5. **Format output.**
   - Schema output: raw text (fenced code block)
   - Row output: markdown table (header row from column names, one data row per result row)
   - Error output: plain text prefixed with `ERROR:`

## What You Must NOT Do

- Never run INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, or PRAGMA write statements
- Never open a DB without `-readonly` flag
- Never dump an entire table — enforce `LIMIT 100` maximum at all times
- Never modify the DB file path or copy DB files

## Examples

**List all tables:**
```
/db-inspect
```
Runs `.tables` + row counts on both DBs, formats as markdown.

**Inspect state DB schema:**
```
/db-inspect tables state
```
Runs `sqlite3 -readonly state.db .schema` and returns raw DDL.

**Run a custom SELECT:**
```
/db-inspect query "SELECT * FROM trades LIMIT 5"
```
Validates no write keywords, opens DB readonly, returns markdown table.
