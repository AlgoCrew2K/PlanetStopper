# advisors/lens_warehouse

> Nightly lens data warehouse: a SEPARATE SQLite database that accumulates per-lens snapshots in an append-only, WAL-mode store with secret-stripping before persistence.

**Source:** `advisors/lens_warehouse.py`
**Last updated:** 2026-06-13

## Overview

`advisors/lens_warehouse.py` owns a dedicated SQLite database (path from env `WAREHOUSE_DB_PATH`, default `alphabot_warehouse.db`) used to accumulate per-lens nightly snapshots. It is distinct from both the state DB (`alphabot_state.db`) and the optimization DB — never cross-joined with either (Architecture Constraint 3).

The module is off-execution-path: it is never imported on the 1-minute engine loop and carries no Flask dependency.

**Append-only invariant:** `persist_lens_snapshot` only INSERTs — there is no UPDATE or DELETE surface. The full snapshot history is preserved for trend analysis and debugging.

**Secret-stripping:** before any payload reaches SQLite, `_strip_secrets` traverses the full dict/list tree at any nesting depth and replaces the value of any key matching `api[_-]?key|token|secret|password|webhook` (case-insensitive) with `<redacted>`. This is a best-effort guard applied by key-name pattern; it does not scan scalar string values for credential-shaped content.

**D-1 error contract:** callers never see `str(exc)`, stack frames, or file paths from this module. Only `type(exc).__name__` may appear in any error surface.

## Public API

### `init_warehouse() → None`

Create the warehouse schema idempotently. Safe to call multiple times — uses `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS`. Enables WAL journal mode for concurrent Flask reads alongside a single daemon writer.

**Parameters:** none

**Returns:** `None`

**Side effects:** creates `lens_snapshots` table and `ix_lens_snapshots_lens_symbol_fetch_ts` index in the warehouse DB if they do not already exist.

**Never raises** — but will propagate `sqlite3.OperationalError` if the DB path is not writable (expected: the caller controls the path via `WAREHOUSE_DB_PATH`).

---

### `persist_lens_snapshot(lens, symbol, source, available, raw_payload, fetch_ts=None) → int`

Append one lens snapshot row to the warehouse. Secret keys in `raw_payload` are stripped before JSON serialisation.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `lens` | `str` | Lens identifier, e.g. `"sentiment"`, `"technicals"`, `"macro"`. |
| `symbol` | `str \| None` | Per-ticker symbol, or `None` for market-wide lenses. |
| `source` | `str` | Data source identifier, e.g. `"gdelt"`, `"talib"`, `"fred"`. |
| `available` | `int` | `1` if data was fetched successfully; `0` if the lens was unavailable. |
| `raw_payload` | `dict \| list` | JSON-serialisable payload. Secret keys are stripped before storage. |
| `fetch_ts` | `str \| None` | ISO-format UTC timestamp. Defaults to `datetime.now(UTC).isoformat()` when not supplied. |

**Returns:** `int` — the ROWID of the newly inserted row (always > 0).

**Example:**
```python
row_id = persist_lens_snapshot(
    lens="sentiment",
    symbol=None,
    source="gdelt",
    available=1,
    raw_payload={"score": 0.72, "api_key": "sk-secret"},  # api_key → <redacted>
)
```

---

### `get_lens_snapshots(lens, symbol=None, since=None) → list[dict]`

Return snapshots for `lens`, optionally filtered by symbol and time window.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `lens` | `str` | Lens identifier to filter on. |
| `symbol` | `str \| None` | When provided, restrict to rows for this symbol only. |
| `since` | `str \| None` | ISO-format UTC timestamp. When provided, only rows with `fetch_ts >= since` are returned. |

**Returns:** `list[dict]` — ordered by `(fetch_ts ASC, id ASC)`. Each dict carries all column values (`id`, `lens`, `symbol`, `fetch_ts`, `source`, `available`, `raw_json`, `created_at`) plus a `raw` key holding the deserialised JSON object. Returns `[]` when no rows match.

**Example:**
```python
rows = get_lens_snapshots("technicals", since="2026-06-13T00:00:00+00:00")
for row in rows:
    print(row["fetch_ts"], row["available"], row["raw"])
```

## Schema

```sql
CREATE TABLE IF NOT EXISTS lens_snapshots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    lens       TEXT    NOT NULL,
    symbol     TEXT    NULL,
    fetch_ts   TEXT    NOT NULL,
    source     TEXT    NOT NULL,
    available  INTEGER NOT NULL,
    raw_json   TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_lens_snapshots_lens_symbol_fetch_ts
    ON lens_snapshots (lens, symbol, fetch_ts);
```

The index accelerates `get_lens_snapshots` queries ordered by `fetch_ts` with `lens`/`symbol` filters.

## Design Invariants

| Code | Invariant |
|------|-----------|
| WARCH-1 | Separate DB — never cross-joined with state DB or optimization DB. Path controlled by `WAREHOUSE_DB_PATH` env. |
| WARCH-2 | Append-only — no UPDATE or DELETE accessor exists. Full snapshot history is preserved. |
| WARCH-3 | WAL journal mode — enables concurrent Flask reads alongside the single nightly daemon writer. |
| WARCH-4 | Secret-stripping by key-name pattern (`api[_-]?key\|token\|secret\|password\|webhook`, case-insensitive, any nesting depth). Applied before JSON serialisation. Best-effort: does not scan scalar string values. |
| WARCH-5 | D-1 error contract — `type(exc).__name__` only in any error surface; no raw `str(exc)`, no stack frames, no file paths. |
| WARCH-6 | Off-execution-path — never imported on the 1-minute engine loop; no Flask dependency. |

## Known Limits

- **Secret-stripping is best-effort.** The guard matches on dict key names via regex. It does NOT scan string scalar values for credential-shaped content (e.g., a payload like `{"data": "Bearer sk-abc123"}` is stored verbatim). Callers must not pass credentials in non-key positions.
- **No production caller yet.** As of 2026-06-13, `advisors/lens_warehouse.py` has no caller in production code — it is scaffolded infrastructure for the warehouse phase. Tests drive it via `WAREHOUSE_DB_PATH` env override.
- **No read-only connection path.** `_get_connection()` opens a writable connection. Flask dashboard reads should use a read-only SQLite URI if the warehouse is ever queried from a template context.
- **No schema migration system.** Schema is created idempotently via `init_warehouse()`. If the schema changes, a manual migration or a new `ALTER TABLE` step is needed; the numbered migration system in `database.py` does not cover the warehouse DB.

## Internal Dependencies

- `json` — payload serialisation
- `os` — `WAREHOUSE_DB_PATH` env lookup
- `re` — `_SECRET_KEY_PATTERN` compilation
- `sqlite3` — WAL-mode warehouse connections
- `datetime` — UTC timestamp default in `persist_lens_snapshot`

No imports from `database`, `app`, `ai_advisor`, or any other project module. The warehouse is intentionally self-contained.
