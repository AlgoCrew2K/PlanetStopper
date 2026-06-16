# TDD Handoff — lens-data-warehouse
Plan: feature-plans/lens-data-warehouse.md
Branch: feat/lens-warehouse
Phase: green

## Test Files
- `tests/database/test_lens_warehouse.py` — 49 tests total

## Import Stubs Created
- `advisors/lens_warehouse.py` — minimal stub; exports `init_warehouse_db`,
  `persist_lens_snapshot`, `get_lens_snapshots` (all raise `NotImplementedError`
  after sentinel check); contains NO real logic; sentinel raises `RuntimeError`
  when called without `db_path` under pytest (mirrors database._db_file() guard);
  no `database` import at module level; no `update_*`/`delete_*` exports.

## A/C Coverage Matrix

| A/C | Description | Test Class | Test Name(s) | Status |
|-----|-------------|------------|--------------|--------|
| AC-1 | Module importable | TestWarehouseInit | test_module_is_importable | RED (passes on stub) |
| AC-1 | init_warehouse_db callable | TestWarehouseInit | test_init_warehouse_db_is_callable | RED (passes on stub) |
| AC-1 | init creates SQLite file | TestWarehouseInit | test_init_creates_sqlite_file | RED (NotImplementedError) |
| AC-1 | lens_snapshots table exists | TestWarehouseInit | test_lens_snapshots_table_exists_after_init | RED |
| AC-1 | All required columns present | TestWarehouseInit | test_lens_snapshots_has_required_columns | RED |
| AC-1 | Index on (lens,symbol,fetch_ts) | TestWarehouseInit | test_index_on_lens_symbol_fetch_ts_exists | RED |
| AC-1 | init is idempotent | TestWarehouseInit | test_init_warehouse_db_is_idempotent | RED |
| AC-2 | persist_lens_snapshot exported | TestPersistLensSnapshot | test_persist_lens_snapshot_is_exported | RED (passes on stub) |
| AC-2 | Returns positive int | TestPersistLensSnapshot | test_returns_positive_int | RED (fixture ERROR) |
| AC-2 | Returned id is actual rowid | TestPersistLensSnapshot | test_returned_id_matches_actual_rowid | RED |
| AC-2 | raw_json is JSON-deserializable | TestPersistLensSnapshot | test_raw_json_is_json_deserializable | RED |
| AC-2 | Append-only (re-insert ≠ overwrite) | TestPersistLensSnapshot | test_append_only_re_insert_creates_new_row | RED |
| AC-2 | available stored as 0/1 | TestPersistLensSnapshot | test_available_stored_as_integer | RED |
| AC-2 | symbol=None stored as SQL NULL | TestPersistLensSnapshot | test_null_symbol_stored_as_null | RED |
| AC-2 | fetch_ts round-trips verbatim | TestPersistLensSnapshot | test_fetch_ts_stored_verbatim | RED |
| AC-2 | Secret keys stripped (5 param cases) | TestPersistLensSnapshot | test_secret_key_stripped_from_stored_json[*] | RED |
| AC-2 | Non-secret keys preserved | TestPersistLensSnapshot | test_non_secret_keys_preserved_in_stored_json | RED |
| AC-2 | D-1: no raise on valid call | TestPersistLensSnapshot | test_persist_never_raises_on_valid_call | RED |
| AC-3 | get_lens_snapshots exported | TestGetLensSnapshots | test_get_lens_snapshots_is_exported | RED (passes on stub) |
| AC-3 | [] for unknown lens | TestGetLensSnapshots | test_returns_empty_list_for_unknown_lens | RED (fixture ERROR) |
| AC-3 | Returns list of dicts | TestGetLensSnapshots | test_returns_list_of_dicts | RED |
| AC-3 | Dicts have required keys | TestGetLensSnapshots | test_returned_dicts_have_required_keys | RED |
| AC-3 | Ordered by fetch_ts asc | TestGetLensSnapshots | test_ordered_by_fetch_ts_ascending | RED |
| AC-3 | No symbol filter → all symbols | TestGetLensSnapshots | test_no_symbol_filter_returns_all_symbols | RED |
| AC-3 | symbol filter limits results | TestGetLensSnapshots | test_symbol_filter_limits_to_matching_symbol | RED |
| AC-3 | since filter includes threshold row | TestGetLensSnapshots | test_since_filter_includes_rows_at_and_after_threshold | RED |
| AC-3 | since filter excludes older rows | TestGetLensSnapshots | test_since_filter_excludes_rows_before_threshold | RED |
| AC-4 | No database import at module level | TestSeparateDbIsolation | test_module_does_not_import_database_at_module_level | RED (passes — negative invariant) |
| AC-4 | No `import database as db` | TestSeparateDbIsolation | test_module_does_not_import_database_as_db | RED (passes — negative invariant) |
| AC-4 | No get_connection/init_db in source | TestSeparateDbIsolation | test_no_get_connection_reference_in_warehouse_source | RED (passes — negative invariant) |
| AC-4 | persist writes to warehouse path only | TestSeparateDbIsolation | test_persist_uses_warehouse_path_not_state_db | RED (fixture ERROR) |
| AC-5 | Fixture path is not production basename | TestWarehouseSentinel | test_temp_fixture_path_is_not_alphabot_warehouse | RED (passes — invariant check) |
| AC-5 | init without path raises under pytest | TestWarehouseSentinel | test_init_without_path_raises_under_pytest | RED (currently passes via stub sentinel) |
| AC-5 | persist without path raises under pytest | TestWarehouseSentinel | test_persist_without_path_raises_under_pytest | RED (currently passes via stub sentinel) |
| AC-5 | get without path raises under pytest | TestWarehouseSentinel | test_get_without_path_raises_under_pytest | RED (currently passes via stub sentinel) |
| AC-6 | Two inserts → two rows | TestAppendOnlyAndHonestAvailability | test_two_inserts_create_two_rows | RED (fixture ERROR) |
| AC-6 | Both rows present with diff fetch_ts | TestAppendOnlyAndHonestAvailability | test_both_rows_present_with_different_fetch_ts | RED |
| AC-6 | available=False accepted without error | TestAppendOnlyAndHonestAvailability | test_available_false_row_accepted_without_error | RED |
| AC-6 | available=False stored as 0 | TestAppendOnlyAndHonestAvailability | test_available_false_stored_as_zero | RED |
| AC-6 | available=False payload stored verbatim | TestAppendOnlyAndHonestAvailability | test_available_false_payload_stored_verbatim | RED |
| AC-6 | No update_lens_snapshot exported | TestAppendOnlyAndHonestAvailability | test_no_update_lens_snapshot_exported | RED (passes — negative invariant) |
| AC-6 | No delete_lens_snapshot exported | TestAppendOnlyAndHonestAvailability | test_no_delete_lens_snapshot_exported | RED (passes — negative invariant) |
| AC-2+6 | SQL injection shaped payload round-trips | TestAppendOnlyAndHonestAvailability | test_sql_injection_shaped_payload_stored_verbatim[*] | RED |

## Test Run Command

```
pytest tests/database/test_lens_warehouse.py -p no:xdist -o addopts= -m "not live and not slow and not perf" -q
```

(Run from the worktree root. The global conftest's pytest_configure sets DB_PATH
before collection; if running standalone, set DB_PATH to a temp file first.)

## What the implementer must build

The implementer READS THIS HANDOFF ONLY (not the plan). New module `advisors/lens_warehouse.py`:

### 1. Module-level sentinel constant and helper

```python
_WAREHOUSE_DB_BASENAME = "alphabot_warehouse.db"

def _warehouse_db_file(path=None):
    # If no path given, resolve to production basename.
    # Under pytest, raise RuntimeError to mirror database._db_file().
    if path is None:
        resolved = _WAREHOUSE_DB_BASENAME
        if "pytest" in sys.modules:
            raise RuntimeError(
                f"test attempted to open the production warehouse DB "
                f"({_WAREHOUSE_DB_BASENAME}) — set db_path to a temp file"
            )
        return resolved
    return str(path)
```

### 2. `init_warehouse_db(path=None)`

- Calls `_warehouse_db_file(path)` for sentinel.
- Opens a SQLite connection to the resolved path.
- Executes `CREATE TABLE IF NOT EXISTS lens_snapshots (...)` with all 8 columns:
  - `id INTEGER PRIMARY KEY AUTOINCREMENT`
  - `lens TEXT NOT NULL`
  - `symbol TEXT`   (NULL-able — for index-level lenses)
  - `fetch_ts TEXT NOT NULL`
  - `source TEXT NOT NULL`
  - `available INTEGER NOT NULL`
  - `raw_json TEXT NOT NULL`
  - `created_at TEXT DEFAULT (datetime('now'))`
- Executes `CREATE INDEX IF NOT EXISTS idx_lens_snapshots_lens_symbol_fetch_ts ON lens_snapshots (lens, symbol, fetch_ts)`.
- Commits and closes.
- Must be idempotent (IF NOT EXISTS handles re-runs).

### 3. `persist_lens_snapshot(lens, symbol, source, available, raw_payload, fetch_ts=None, db_path=None) -> int`

- Calls sentinel via `_warehouse_db_file(db_path)`.
- Calls `init_warehouse_db(db_path)` first (ensures schema exists).
- Strips known secret keys from `raw_payload` before serializing:
  `_SECRET_KEY_NAMES = {"api_key", "token", "secret", "password", "Authorization"}`
  — top-level key removal only; nested keys not in scope.
- Serializes the cleaned payload to JSON string via `json.dumps`.
- If `fetch_ts` is None, use `datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")`.
- Inserts row with parameterized SQL (no string formatting — injection-safe).
- Never uses INSERT OR REPLACE / INSERT OR IGNORE — pure INSERT for append-only.
- Returns `cursor.lastrowid` (positive int).
- D-1: never raises on valid input.

### 4. `get_lens_snapshots(lens, symbol=None, since=None, db_path=None) -> list[dict]`

- Calls sentinel via `_warehouse_db_file(db_path)`.
- Calls `init_warehouse_db(db_path)` first.
- Builds parameterized SELECT with optional WHERE clauses:
  - Always: `WHERE lens = ?`
  - If `symbol` is not None: `AND symbol = ?`
  - If `since` is not None: `AND fetch_ts >= ?`
  - `ORDER BY fetch_ts ASC`
- Returns `[]` when no rows match.
- Returns list of dicts with all 8 column names as keys.
- Use `sqlite3.Row` or `dict(zip(col_names, row))` for the mapping.

### 5. Module-level import rules (HARD)
- Do NOT import `database` at module level (no `import database`, no `from database import *`).
- No `database.get_connection()` or `database.init_db()` anywhere.
- The warehouse manages its own SQLite connection independently.
- Only stdlib imports at module level: `json`, `sqlite3`, `sys`, `datetime`, `pathlib`.

### 6. No update/delete accessors
- Export only: `init_warehouse_db`, `persist_lens_snapshot`, `get_lens_snapshots`.
- No `update_lens_snapshot`, no `delete_lens_snapshot`.

## AC-4 Wiring RED — round 2 (routed by PM, 2026-06-16)

Plan AC-4 is the **producer integration point** — GDELT and FRED must call
`persist_lens_snapshot` after each fetch. This is separate from the store
and goes in `ai_advisor.py`. Tests in:
`tests/ai_advisor/test_lens_warehouse_wiring.py` (8 tests: 5 RED + 3 PASS invariants).

### What the implementer must add to `ai_advisor.py`

In `_build_sentiment_section` (after building the return dict, before returning):

```python
    # AC-4: persist this lens snapshot to the warehouse (lazy import, CC-2).
    try:
        from advisors import lens_warehouse  # noqa: PLC0415
        _block_available = not (not tone_available and not artlist_available)
        _wh_payload = (
            {"article_count": len(articles), "tone_score": tone_score}
            if _block_available
            else {"reason": artlist_reason or tone_result.get("reason")}
        )
        lens_warehouse.persist_lens_snapshot(
            lens="sentiment",
            symbol=None,
            source="gdelt",
            available=_block_available,
            raw_payload=_wh_payload,
        )
    except Exception:
        pass  # D-1: warehouse errors never surface to callers
```

In `_build_macro_section` (add before each of the two return statements):

```python
    # AC-4: persist this lens snapshot (lazy import, CC-2).
    try:
        from advisors import lens_warehouse  # noqa: PLC0415
        lens_warehouse.persist_lens_snapshot(
            lens="macro",
            symbol=None,
            source="fred",
            available=<True or False>,
            raw_payload=<series_data dict or reason dict>,
        )
    except Exception:
        pass
```

Constraints:
- LAZY import only — `from advisors import lens_warehouse` INSIDE the function body.
  The two lazy-import tests (`test_persist_lens_snapshot_is_lazy_imported_*`) will
  FAIL if `lens_warehouse` appears as a module-level attribute on `ai_advisor`.
- available=False on failure paths, available=True on success — mirrors the block.
- D-1 guard: `try/except Exception: pass` always wraps the persist call.
- Tests mock `advisors.lens_warehouse.persist_lens_snapshot` at the source path —
  the lazy import must use exactly `from advisors import lens_warehouse` so the
  mock patch path `advisors.lens_warehouse.persist_lens_snapshot` intercepts it.
- Path-scoped commit: `ai_advisor.py` only.

Test command for round 2:
```
pytest tests/ai_advisor/test_lens_warehouse_wiring.py -p no:xdist -o addopts= -m "not live and not slow and not perf" -q
```
Target: 8 passed, 0 failed.

## Questions for User / PM
- None — the plan + AC are clear and fully specified.

## Notes on "passing" RED tests
13 tests currently pass against the stub. These are all NEGATIVE INVARIANT or
STRUCTURAL checks that must also pass after GREEN:
- Module importability + function existence checks (3 tests)
- `test_module_does_not_import_database_*` (2 tests) — stub has no `database` import
- `test_no_get_connection_reference_in_warehouse_source` — stub source has no cross-DB call
- Append-only guards: `test_no_update_*`, `test_no_delete_*` (2 tests)
- Sentinel tests (3) — stub correctly raises `RuntimeError` when no `db_path` under pytest
- `test_temp_fixture_path_is_not_alphabot_warehouse` — fixture isolation invariant

These are correct — a wrong implementation (one that adds `database` imports or
update/delete exports) would make them FAIL, which is the goal.

## Status Log
- [2026-06-16] wh-test-writer (LEAD): Starting RED phase
- [2026-06-16] wh-test-writer (LEAD): RED complete — 49 tests (5 FAILED + 31 ERROR [fixture cascade] + 13 PASS [negative invariants]); 1 stub created (advisors/lens_warehouse.py)
- [2026-06-16] wh-sqlite-implementer: GREEN complete — 49/49 tests passing @ 6e9b3bb, 0 test bugs documented. Typecheck N/A (stdlib only). Lint OK.
- [2026-06-16] wh-test-writer (LEAD): REVIEW ROUND 2 — PM flagged AC mislabel (plan AC-4=wiring, AC-5=isolation — test file had them swapped) + anti-hollow: added 8 wiring RED tests in tests/ai_advisor/test_lens_warehouse_wiring.py (5 FAILED: persist not yet wired into _build_sentiment_section / _build_macro_section; 3 PASS: lazy-import invariant + key-absent guard). Phase: red (wiring).
- [2026-06-16] wh-sqlite-implementer: GREEN round 2 — 60/60 (49 store + 3 recursive-strip + 8 wiring) @ commit to follow. ai_advisor.py wired; lens_warehouse.py _strip_secrets made recursive. Lint ✓ (pre-existing ai_advisor.py issues are not mine). No test bugs.

## Test File Issues (for test-writer to fix)
None — all 49 tests passed against the implementation as written.

## Implementation Notes
- Replaced the `NotImplementedError` stub in `advisors/lens_warehouse.py` with full production logic.
- `_warehouse_db_file(path)` is the single sentinel helper — raises `RuntimeError("...alphabot_warehouse...")` when `path is None` under pytest, mirroring `database._db_file()`.
- WAL mode enabled on every connection via `PRAGMA journal_mode=WAL` (project standard for concurrent reader + single writer).
- `sqlite3.Row` set as `row_factory`; `dict(row)` converts rows to dicts for `get_lens_snapshots`.
- `available` stored as `int(bool(available))` — Python booleans coerce correctly to 0/1.
- `_SECRET_KEY_NAMES` is a `frozenset` — O(1) membership test; top-level strip only as specified.
- `persist_lens_snapshot` calls `init_warehouse_db(db_path)` first to ensure schema exists (idempotent init handles this without overhead).
- `get_lens_snapshots` also calls `init_warehouse_db(db_path)` first for the same reason.
- Parameterized SQL throughout — no f-string interpolation of user values anywhere.
- Test run required `DB_PATH` env var pointing to an existing Windows temp dir (the global `pytest_configure` hook sets this during normal `pytest` invocation from the project root; running standalone needs `DB_PATH` pre-set because `tests/database/conftest.py` imports `database` at module level before the hook can fire in some runner modes).
- No disputed tests.
