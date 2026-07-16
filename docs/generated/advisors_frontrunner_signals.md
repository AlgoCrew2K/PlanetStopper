# advisors/frontrunner_signals

> Live Atlas signal ingestion (daily-cached) + AC-4 edge classification + the PM-ruling classification/run-marker persistence extension, all in one module — the durable machinery that keeps the Frontrunner Builder's cull recommendations current against real backtested edge data.

**Source:** `advisors/frontrunner_signals.py`
**Last updated:** 2026-07-16 (GREEN at `bf6f026b` — AC-1/AC-2 landed first at `212f41a5`, AC-4 + the PM-ruling classification/run-marker tables landed at `bf6f026b`)

## Overview

`advisors/frontrunner_signals.py` is the new module for feature-plans/frontrunner-signals.md — it closes the gap the shipped Frontrunner Builder (PR #96, `docs/generated/advisors_frontrunner_builder.md`) had from day one: the builder detected cascade shapes and generated replacements but never read the live signal data the operator pointed at. This module pulls that data (Atlas collection `captplanet.frontrunners`, ~3,402 docs, one per `TICKER:WINDOW:THRESHOLD` RSI-frontrunner check), persists it, classifies extracted FR-checks against it, and persists the classification results for read-only dashboard rendering.

The module has three layers, all in this one file per the Architecture doc:

1. **AC-1/AC-2 — ingest + persist.** `load_frontrunner_signals` pulls the collection through the daily `atlas_cache` seam (dedicated cache key, `ttl_days=1` — distinct from the weekly `strategies` cache used elsewhere) and persists every non-cache-hit pull into the warehouse third-DB. `get_latest_signal_rows` reads the most recent snapshot batch. **Fully wired and production-live.**
2. **AC-4 — edge classification.** `classify_fr_checks` joins extracted FR-checks (from `advisors.frontrunner_detector.extract_fr_checks`) to the persisted signal rows by exact `fr_key`, guards the comparator/fn mismatch traps, and classifies `remove`/`prune`/`keep`/`no_edge_data`. **Correct and unit-tested; not yet called from any production path as of this writing — see the caveat below.**
3. **PM-ruling extension — classification + run-marker persistence.** AC-7 (the dashboard tab) renders PERSISTED rows only, never live-computes in a Flask request thread (extraction needs `/score` fetches — network I/O, banned on the dashboard path). `persist_classification_run` / `get_latest_classifications` / `get_latest_run_marker` are the write/read pair for a second table set in the same warehouse DB file. **The READ side (`get_latest_classifications`/`get_latest_run_marker`, consumed by the AI Advisor Frontrunner tab) is wired and live. The WRITE side (`persist_classification_run`) is NOT yet called from production** — fr-review's Cluster-D pass (`bf6f026b`) found `advisors/frontrunner_builder.py`'s background compute path does not yet call `classify_fr_checks` or `persist_classification_run`; wiring is in progress as a separate item. Until it lands, the classification tables have no production rows and the tab's "Live Signal Classification" subsection renders its honest empty state. See `DE-FR-SIGNALS-001` for the current status.

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

**Returns:** one dict per input check: `{fr_key, fn, comparator, branch_path, classification, rsi_live, rsi_live_at, cagr, sharpe, sortino, calmar, max_drawdown, signal_fetch_ts}`. A `no_edge_data` row never carries a borrowed/mismatched edge stat — every stat field is `None`. Pure function (no I/O). Never raises (D-1) — malformed/empty inputs degrade to `[]`. **Correct and unit-tested at the function level; not yet called from any production path — see the Overview caveat.**

---

### `persist_classification_run(symphony_id: str, classification_rows: list[dict], *, signals_unavailable: bool = False, reason: str | None = None, computed_at: str | None = None, db_path=None) -> None`

PM-ruling extension. Writes one `frontrunner_classification_snapshots` row per `fr_key` plus exactly one `frontrunner_run_metadata` row, sharing a single `computed_at` (explicit if passed, else generated once — never per-row). Valid with `classification_rows=[]` (the AC-5 degraded case) — the run marker is still written so the tab can render an honest degraded state. **Correct and unit-tested; not yet called from any production path — see the Overview caveat. The classification tables are empty in production until this is wired.**

---

### `get_latest_classifications(symphony_id=None, *, db_path=None) -> list[dict]`

`symphony_id` given: that symphony's own latest batch (greatest `computed_at` for that `symphony_id`). `symphony_id=None`: EVERY symphony's own latest batch — a per-symphony greatest-n-per-group, never a single global max that would silently drop older symphonies' rows. `branch_path` is deserialized here (returned as a native list) — callers never `json.loads()` it themselves. **Wired and live** — called by `app.py::ai_advisor_tab()`; currently returns `[]` in production since nothing writes to the underlying table yet (see `persist_classification_run` above).

---

### `get_latest_run_marker(symphony_id=None, *, db_path=None) -> dict | None`

`symphony_id` given: that symphony's own latest marker row. `symphony_id=None`: the single most-recently-computed row across the WHOLE table, arbitrary which symphony that happens to be — deliberately NO aggregation (no any-symphony-degraded-implies-True logic). `None` when no row has ever been written for the requested scope. **Wired and live** — called by `app.py::ai_advisor_tab()`; currently returns `None` in production for the same reason as `get_latest_classifications` above.

---

### `init_frontrunner_signal_snapshots_db(path=None) -> None`

Creates the `frontrunner_signal_snapshots` schema at `path` (idempotent). Production callers never need to pass `db_path` on any of the above — every function self-sufficiently ensures its own schema on demand (mirrors `atlas_cache.cached_pull`'s self-sufficiency precedent).

## Schema

### `frontrunner_signal_snapshots` (AC-2)

Append-only. `id, fr_key, ticker, "window", threshold, comparator, rsi_live, rsi_live_at, cagr, sharpe, sortino, calmar, max_drawdown, n_points, vix_destination_json, total_strategy_count, true_ticker, false_ticker, fetch_ts, created_at`. `"window"` is quoted throughout — SQLite reserves `WINDOW` for window-function syntax; unquoted usage happens to parse today but quoting is defensive. Indexed on `fetch_ts` (accelerates `get_latest_signal_rows`'s `MAX(fetch_ts)` lookup) and on `(fr_key, fetch_ts)` (accelerates `classify_fr_checks`'s per-`fr_key` join against the latest snapshot).

### `frontrunner_classification_snapshots` (PM-ruling extension)

Append-only. `id, symphony_id, fr_key, fn, comparator, branch_path, rsi_live, rsi_live_at, cagr, sharpe, sortino, calmar, max_drawdown, classification, signal_fetch_ts, computed_at, created_at`. `fr_key` stays `NOT NULL` — a crossover/vs check's `FRCheck.fr_key` is `None` in the pure dataclass, but `advisors/frontrunner_builder.py`'s intended persist call site (not yet wired, see Overview) formats a deterministic display-identity string into this column before the insert (see that module's AC-5 section). Indexed on `(symphony_id, computed_at)`. Empty in production as of this writing — see the write-side caveat above.

### `frontrunner_run_metadata` (PM-ruling extension)

Append-only. `id, symphony_id, signals_unavailable, reason, computed_at, created_at`. Indexed on `(symphony_id, computed_at)`. Empty in production as of this writing — see the write-side caveat above.

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
- `tests/advisors/test_frontrunner_signals_warehouse.py` — classification/run-marker persistence + accessor round-trips
- 110/110 across the full RED batch on this branch as of `bf6f026b` (includes fr-data's AC-1/AC-2 tests, fr-engine's AC-4 tests, and fr-fe's AC-7 tab tests). **These are function/unit-level tests.** No test in this battery proves `frontrunner_builder`'s background compute path actually calls `classify_fr_checks`/`persist_classification_run` in production, because as of this writing it does not — see the Overview caveat and `DE-FR-SIGNALS-001` in `DECISIONS.md` for the current wiring status and the full verification record once fr-test/fr-review's final counts land.

## Internal Dependencies

- `advisors.atlas_cache` — `cached_pull` (module-level import)
- `pymongo` — lazy-imported inside `_fetch_fn` only (CC-2); the module stays importable without it installed
- `sqlite3`, `json`, `concurrent.futures`, `datetime`, `logging`, `os`, `sys` — stdlib only otherwise

**Reverse dependencies (who calls into this module):**
- `advisors/frontrunner_builder.py::resolve_signals_unavailable_marker` — defined to call `load_frontrunner_signals()` per-symphony (CC-2 lazy import) to resolve the AC-5 degraded marker, but `resolve_signals_unavailable_marker` itself has no production caller yet (see that module's own doc, AC-5 status caveat)
- `app.py::ai_advisor_tab()` — calls `get_latest_classifications()` / `get_latest_run_marker(symphony_id=<id>)` (once per unique `symphony_id`, never bare) to render the AC-7 Frontrunner tab's "Live Signal Classification" subsection — no live Composer/network I/O in the request thread. **This IS wired and live** — the gap is entirely on the write side (nothing populates the tables yet), not this read path.
- **NOT YET a reverse dependency (tracked gap, fr-review Cluster-D finding, `bf6f026b`):** `advisors/frontrunner_builder.py`'s background compute path (the on-demand run executor + the weekly scheduler) does not currently call `classify_fr_checks` or `persist_classification_run` — this is the intended design per the Architecture doc, being wired as a separate, currently-open item. See `DE-FR-SIGNALS-001` for status.
