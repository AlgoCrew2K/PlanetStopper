# Feature: Nightly Lens Data Warehouse
Status: ready
Created: 2026-06-13

## Summary
Persist every nightly lens data pull (options/IV/greeks, GDELT tone, FRED macro, SEC fundamentals, technicals) to a proprietary append-only store so Planet Stopper accumulates its OWN historical data corpus at $0 — enabling future options/lens-based backtesting without buying years of vendor history. Operator decision 2026-06-13: record-everything-from-free-sources instead of paying ~$80–99/mo for vendor history at small scale.

## Acceptance Criteria
- [ ] AC-1: A separate append-only warehouse DB (`alphabot_warehouse.db` [PM-ASSUMED]), distinct from the state and optimization DBs, with a `lens_snapshots` table: `id` PK, `lens` TEXT NOT NULL, `symbol` TEXT NULL (null for index-level lenses), `fetch_ts` TEXT NOT NULL (UTC ISO), `source` TEXT NOT NULL, `available` INTEGER NOT NULL, `raw_json` TEXT NOT NULL (verbatim payload), `created_at` TEXT DEFAULT. Index on (`lens`,`symbol`,`fetch_ts`). Init/migration creates it idempotently.
- [ ] AC-2: `persist_lens_snapshot(lens, symbol, source, available, raw_payload) -> int` — parameterized, append-only (never overwrites), returns row id; serializes `raw_payload` to JSON; strips known secret keys before storing.
- [ ] AC-3: `get_lens_snapshots(lens, symbol=None, since=None) -> list[dict]` — ordered by `fetch_ts`; typed dicts; `[]` when none. For future backtest consumption.
- [ ] AC-4: A producer/Prism integration point: the existing FRED/macro + GDELT/sentiment pulls (and future technicals/options producers) call `persist_lens_snapshot` after each fetch. (Retrofit of existing producers may be a follow-up cycle; the store + helper land here.)
- [ ] AC-5: Warehouse DB is fully separate — no cross-DB joins in app code; pytest sentinel respected (tests use a temp warehouse path, never a real `alphabot_warehouse.db`).
- [ ] AC-6: Append-only + safe re-runs: re-running a night appends new rows (dedupe at read by latest `fetch_ts` per `lens`+`symbol`+date); never corrupts/loses prior rows. Only persists what was actually fetched (mark `available=0` for a down source; never fabricate payloads).

## Architecture
New module `advisors/lens_warehouse.py` (or `database.py` accessors) owning the warehouse DB connection + the two accessors. Separate SQLite file. Off-execution-path, advisory-only. Designed so the storage engine can migrate to DuckDB/parquet if volume grows (raw_json keeps payloads engine-agnostic). Producers import and call `persist_lens_snapshot` after their lens fetch; the Prism run persists per-lens snapshots for its `run_id`.

## Design-System Mapping
N/A — backend feature, no UI surface.

## Edge Cases
- Large payloads (full option chains) → `raw_json` is TEXT (no cap); revisit storage engine at scale.
- Index-level lens (no symbol) → `symbol` NULL.
- Source down → persist a row with `available=0` and a reason, NOT a fabricated payload.
- Re-run same night → append (dedupe at read), never overwrite.
- Growth over time → separate DB keeps the live state DB lean; monitor size, migrate to DuckDB/parquet if needed.
- Secrets in payloads → strip API keys/tokens before storing (D-1).

## Security Considerations
- Parameterized writes only (no injection). Local store only — no external egress, no Flask route.
- Strip credentials from raw payloads before persisting; never store API keys.
- D-1 error contract (type-only). Off-execution-path; never touches `LIVE_EXECUTION`.
- pytest sentinel: tests must use a temp warehouse path.

## Testing Strategy
- `tests/database/test_lens_warehouse.py` — init/migration idempotent; insert+read round-trip; append-only (re-insert doesn't overwrite); ordering by fetch_ts; `[]` for unknown lens; separate-DB isolation (warehouse ≠ state DB); secret-stripping; no hardcoded payload values (assert shape/presence).
- Run protocol: temp warehouse path before pytest, `-n0` (`-o addopts= -p no:xdist`), targeted, one pytest at a time.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Separate warehouse DB (not state DB) | Historical/append-only/large; keep live state DB lean; preserve two-DB clarity |
| Raw JSON payload + normalized cols | Engine-agnostic; lets us reconstruct anything later + migrate stores |
| Append-only, dedupe at read | Never lose a night's pull; safe re-runs |
| Free sources only ($0) | Build proprietary history without vendor history cost at small scale |

## Scope Boundaries
- **IN**: the warehouse DB + `persist_lens_snapshot` + `get_lens_snapshots`; wiring at least the live FRED/GDELT pulls to persist.
- **OUT**: the backtest engine that consumes the warehouse (later); DuckDB/parquet migration (later); paid vendor data; any UI. Retrofitting ALL producers may be a fast-follow once each producer exists.
