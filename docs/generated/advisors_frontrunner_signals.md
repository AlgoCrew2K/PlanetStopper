# advisors/frontrunner_signals

> Live Atlas signal ingestion (daily-cached) + AC-4 edge classification — the durable machinery that keeps the Frontrunner Builder's candidate generation current against real backtested edge data. Feeds `advisors/frontrunner_builder.py` IN MEMORY, on every build run; does not persist or render classification results (that layer was built, wired, then de-productized per operator directive — see the note below).

**Source:** `advisors/frontrunner_signals.py`
**Last updated:** 2026-07-16 (AC-1/AC-2 landed first at `212f41a5`, AC-4 landed at `bf6f026b`; the PM-ruling classification/run-marker persistence extension that briefly lived here was landed at `bf6f026b`, wired at `95dac72c`, and REMOVED by the operator's de-productization ruling at `6715d654` (AC-R2) — see `DE-FR-SIGNALS-001` in `DECISIONS.md` for the full account)

## Overview

`advisors/frontrunner_signals.py` is the module that closes the gap the shipped Frontrunner Builder (PR #96, `docs/generated/advisors_frontrunner_builder.md`) had from day one: the builder detected cascade shapes and generated replacements but never read the live signal data the operator pointed at. This module pulls that data (Atlas collection `captplanet.frontrunners`, ~3,402 docs, one per `TICKER:WINDOW:THRESHOLD` RSI-frontrunner check), persists the raw signal snapshot, and classifies extracted FR-checks against it.

The module has two layers, both in this one file per the Architecture doc:

1. **AC-1/AC-2 — ingest + persist.** `load_frontrunner_signals` pulls the collection through the daily `atlas_cache` seam (dedicated cache key, `ttl_days=1` — distinct from the weekly `strategies` cache used elsewhere) and persists every non-cache-hit pull into the warehouse third-DB. `get_latest_signal_rows` reads the most recent snapshot batch. **Fully wired and production-live.**
2. **AC-4 — edge classification.** `classify_fr_checks` joins extracted FR-checks (from `advisors.frontrunner_detector.extract_fr_checks`) to the persisted signal rows by exact `fr_key`, guards the comparator/fn mismatch traps, and classifies `remove`/`prune`/`keep`/`no_edge_data`. **Fully wired and production-live** — called on every `frontrunner_builder._run_build_for_symphony` run via `_build_classification_rows_from_fr_checks` (see that module's doc), gating candidate generation (positive-edge keys, Tier-1 remove veto) and attaching signal provenance to accepted candidates.

**De-productization note (AC-R2, 2026-07-16, commit `6715d654`):** a third layer briefly lived in this file — `persist_classification_run` / `get_latest_classifications` / `get_latest_run_marker`, writing to a `frontrunner_classification_snapshots` + `frontrunner_run_metadata` table pair, read by an AI Advisor "Live Signal Classification" dashboard subsection. It was built (`bf6f026b`), wired into production (`95dac72c`), then removed the same day per the operator's verbatim ruling: *"At no point did I say I wanted the cull work put into planetstopper, it was a one time ask for you, the model. The frontrunner builder was the ask to put into planetstopper given there was no live, real data actually feeding it."* The cull analysis was a one-time PM deliverable, already delivered directly to the operator — productizing it as a standing dashboard feature was never asked for. `classify_fr_checks` itself (AC-4, the actual "builder consumes live signal data" ask) is unaffected and remains wired — only its result's persistence and render were removed. See `DE-FR-SIGNALS-001` in `DECISIONS.md` for the full account, including g2-review's APPROVE verdict on the rip.

Off-execution-path (never imported by `alpha_bot_execution.py`). `pymongo` is lazy-imported inside the fetch closure only (CC-2) — the module stays importable without `pymongo` installed. D-1 never-raises throughout.

## Named Constants

| Name | Value | Purpose |
|------|-------|---------|
| `_COLLECTION_NAME` | `"captplanet.frontrunners.fr_checks"` | Dedicated `atlas_cache` collection key, decoupled from the real Mongo collection queried inside `_fetch_fn` (mirrors `advisors/universe_provider.py`'s `_CACHE_COLLECTION` precedent). Chosen to byte-match the worktree's existing untracked dev-cache row so manual dev-time verification reuses that cache instead of re-pulling Atlas |
| `_CACHE_TTL_DAYS` | `1` | AC-1's daily cache — a week-old RSI value must never be served as today's |
| `_FETCH_TIMEOUT_S` | `30.0` | Wall-clock bound on the live Atlas fetch (`_bounded_fetch_fn`, the DE-CS-002 SRV/DNS-hang pattern mirrored from `advisors/community_strats.py`). Lighter than that module's 45.0s — this collection is ~3,402 docs, ~776 bytes/doc observed, no `edn_string`-scale field, no unindexed server-side sort |
| `_PROJECTION` | `{"_id": 0, "backtest.equity_curve": 0}` | Exclusion-style, not an inclusion allowlist — AC-1's own wording is "excludes `backtest.equity_curve`"; an inclusion list risks silently dropping a field AC-2 needs if it goes stale |
| `_WAREHOUSE_DB_BASENAME` | `"alphabot_warehouse.db"` | Same warehouse DB file as `advisors/lens_warehouse.py` — a THIRD, separate SQLite file from the state DB and the optimization DB. No cross-DB joins |
| `FR_WEAK_SHARPE_THRESHOLD` | `0.38` | AC-4's Tier-2 `prune` floor. Source: p10 of the 152 matched live FR-check Sharpe distribution (`docs/fr-signals-inputs/joined.json`, 2026-07-16 join). Strict `<` — a check with Sharpe exactly at the threshold is `keep`, not `prune` |
| `_RSI_FN` | `"relative-strength-index"` | The Atlas collection is exclusively RSI-based; a live check whose own `fn` is not RSI-family must never be treated as edge-comparable even when its `fr_key` numerically coincides with a real Atlas doc (the cumulative-return `SPY:1:0` trap) |

## Public Types

No dataclasses — every public function returns a plain `dict` or `list[dict]`.

## API Reference

### `load_frontrunner_signals(*, force_refresh: bool = False, db_path=None) -> dict`

AC-1. Pulls `captplanet.frontrunners` through the daily `atlas_cache.cached_pull` seam.

**Returns:** `{available, signals, stats, fetched_at, source, reason?}`. `stats` is `{pulled, normalized, normalize_failed}`. Never raises (D-1) — every failure mode degrades to `available=False` with `reason` drawn from `{"AtlasFetchTimeout", type(exc).__name__, "AtlasCacheUnavailable"}`.

**Persistence side effect:** persists to the warehouse (AC-2) ONLY when this call actually performed a fresh Atlas fetch (cache-miss or `force_refresh=True`) — a cache-hit persists nothing. A persist failure (including the pytest `db_path=None` sentinel firing) is logged and swallowed; it never affects the returned result dict.

---

### `get_latest_signal_rows(*, db_path=None) -> list[dict]`

AC-2 accessor. Returns every row from the most recent signal-snapshot batch (rows sharing the max `fetch_ts`). `[]` when the table is empty. Never raises in production; the pytest sentinel fires only when `db_path=None` under pytest.

---

### `classify_fr_checks(fr_checks: list[dict], signal_rows: list[dict]) -> list[dict]`

AC-4. Joins extracted FR-checks to signal rows by exact `fr_key` AND the live check's own `comparator == "gt"` (the collection is gt-only — a live `lt`-check must never be scored against a coincidentally-matching `gt` Atlas doc, the SPY:10:30 mis-join trap) AND the live check's own `fn == "relative-strength-index"` (a numeric `fr_key` coincidence with a non-RSI `fn`, e.g. cumulative-return, must never be treated as edge-comparable either).

**Classification (in order):** `remove` (Tier 1) when `cagr < 0 or sharpe < 0`; `prune` (Tier 2) when `sharpe < FR_WEAK_SHARPE_THRESHOLD` (strict `<`); `keep` otherwise; `no_edge_data` when the check is not edge-comparable at all (wrong comparator, wrong fn, or genuinely absent from the collection, or the matched doc has incomplete `cagr`/`sharpe`) — never scored against a mismatched backtest, never invented.

**Returns:** one dict per input check: `{fr_key, fn, comparator, branch_path, classification, rsi_live, rsi_live_at, cagr, sharpe, sortino, calmar, max_drawdown, signal_fetch_ts}`. A `no_edge_data` row never carries a borrowed/mismatched edge stat — every stat field is `None`. Pure function (no I/O). Never raises (D-1) — malformed/empty inputs degrade to `[]`. **Called on every build run** via `frontrunner_builder._build_classification_rows_from_fr_checks` — see that module's doc for the wiring.

---

### `init_frontrunner_signal_snapshots_db(path=None) -> None`

Creates the `frontrunner_signal_snapshots` schema at `path` (idempotent). Production callers never need to pass `db_path` on any of the above — every function self-sufficiently ensures its own schema on demand (mirrors `atlas_cache.cached_pull`'s self-sufficiency precedent).

## Schema

### `frontrunner_signal_snapshots` (AC-2)

Append-only. `id, fr_key, ticker, "window", threshold, comparator, rsi_live, rsi_live_at, cagr, sharpe, sortino, calmar, max_drawdown, n_points, vix_destination_json, total_strategy_count, true_ticker, false_ticker, fetch_ts, created_at`. `"window"` is quoted throughout — SQLite reserves `WINDOW` for window-function syntax; unquoted usage happens to parse today but quoting is defensive. Indexed on `fetch_ts` (accelerates `get_latest_signal_rows`'s `MAX(fetch_ts)` lookup) and on `(fr_key, fetch_ts)` (accelerates `classify_fr_checks`'s per-`fr_key` join against the latest snapshot).

**This is the ONLY table this module owns as of `6715d654`.** The `frontrunner_classification_snapshots` and `frontrunner_run_metadata` table pair (PM-ruling extension, built at `bf6f026b`) was removed by AC-R2 — see the de-productization note in the Overview above.

## Internal Mechanics

### `_normalize_doc` — no `_strip_secrets`, intentionally

Unlike `advisors/lens_warehouse.py`'s `persist_lens_snapshot`, `_normalize_doc` does NOT call `_strip_secrets`. This is deliberate, not an oversight (documented in-source): the function extracts a fixed, named-field allowlist from the raw Atlas doc and never persists the raw doc payload itself, so there is no arbitrary-shaped dict for a stray credential key to hide in. `_strip_secrets` exists to scrub payloads whose shape isn't controlled by the persisting code (`lens_warehouse`'s `raw_payload` is caller-supplied and stored near-verbatim); a strict allowlist extraction has no equivalent leak surface by construction.

### Persist-only-on-fresh-fetch

`load_frontrunner_signals` tracks whether `atlas_cache.cached_pull` actually invoked its `_bounded_fetch_fn` closure (via a closure-captured `_fetch_occurred` flag, mirroring `community_strats.py`'s `_timeout_fired` idiom) and persists to the warehouse ONLY on a genuine fresh pull — a cache-hit returns the cached signals but writes nothing new to `frontrunner_signal_snapshots`, since there is nothing new to record.

### `classify_fr_checks` lives here, not in `frontrunner_detector.py`

The AC-4 classifier is implemented in this file per the Architecture doc, even though the FR-checks it classifies are extracted by `advisors.frontrunner_detector.extract_fr_checks` — the two are separate concerns (tree-walk extraction vs. signal-data classification) that happen to be co-located in this module for the ingest+classify pairing, not because they share implementation.

## Testing

- `tests/advisors/test_frontrunner_signals_ingest.py` — AC-1/AC-2 (daily-cache wiring, D-1 degradation paths, warehouse persistence + pytest sentinel)
- `tests/advisors/test_frontrunner_signals_classification.py` — AC-4 (comparator/fn guard traps, classification tiers against the known table, `no_edge_data` honesty)
- `tests/advisors/test_frontrunner_signals_warehouse.py` — 6 tests (AC-2 signal-snapshot persistence + pytest sentinel round-trips only, post-AC-R4 trim). The 12 classification/run-marker persistence tests this file used to carry were deleted along with the removed table pair (AC-R4, commit `059dfa3c`).
- `tests/advisors/test_frontrunner_signals_no_live_api.py` — AC-8: credential-less + with-creds battery proving zero live network in pytest for this module's Mongo/Atlas seam.
- Full frontrunner test surface, fr-doc-verified directly at HEAD `6715d654`: `python -m pytest tests/advisors -k frontrunner -n0 -q` -> 240 passed, 1030 deselected, 1 xfailed (66.90s), zero regressions from either slice (Gate#2 fix or de-productization rip). See `DE-FR-SIGNALS-001` in `DECISIONS.md` for the full verification record.

## Internal Dependencies

- `advisors.atlas_cache` — `cached_pull` (module-level import)
- `pymongo` — lazy-imported inside `_fetch_fn` only (CC-2); the module stays importable without it installed
- `sqlite3`, `json`, `concurrent.futures`, `datetime`, `logging`, `os`, `sys` — stdlib only otherwise

**Reverse dependencies (who calls into this module):**
- `advisors/frontrunner_builder.py::_run_build_for_symphony` — calls `load_frontrunner_signals()` once per symphony run (the AC-5 signals hoist) and, via `_build_classification_rows_from_fr_checks`, calls `classify_fr_checks()` — both on EVERY build run. See that module's doc for the full wiring.
- **No longer a reverse dependency (removed, AC-R2, `6715d654`):** `app.py::ai_advisor_tab()` used to call `get_latest_classifications()` / `get_latest_run_marker()` to render the AI Advisor Frontrunner tab's "Live Signal Classification" subsection — both the accessor functions and their dashboard call site were removed together (AC-R1 UI removal, AC-R2 persistence removal). `advisors/frontrunner_builder.py::resolve_signals_unavailable_marker` (the function that used to call `load_frontrunner_signals()` to resolve a per-symphony degraded marker for persistence) was removed with its only caller.
