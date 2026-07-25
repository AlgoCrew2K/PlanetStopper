# synthetic_history

> 250-day Alpaca historical fetcher with parallel bar download, file cache, and eligibility guards — feeds the autotuner walk-forward replay.

**Source:** `synthetic_history.py`
**Last updated:** 2026-07-25 (hygiene cycle, `DE-HYGIENE-R1-001` -- `get_cached_synthetic_history_only`'s backward cache-probe walk now existence-checks each candidate file before calling `load_cached_history`, so a plain missing cache file (the expected case under the weekly autotune cache cadence) is summarized in at most one INFO line per call instead of a misleading per-file WARNING "corrupt or unreadable"; genuine corruption keeps that WARNING verbatim; see the new Cache-Miss Log Classification note below. Zero change to cache semantics, walk-back depth, or return values.) Prior: 2026-07-24 (guard-alpha-preconditions, live-gate correction R2, `DE-GUARD-ALPHA-PRECONDITIONS-001` -- `get_cached_synthetic_history_only` now probes backward up to a new `AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS=10` NYSE trading days instead of exact-today-only, since the cache is written once per weekly autotune cycle and was cold 6 of 7 days by construction; see the updated section + new Cache-Probe Constants table below). Prior: 2026-07-23 (guard-alpha-preconditions, `DE-GUARD-ALPHA-PRECONDITIONS-001` -- shared cache-key derivation DRY refactor: `_resolve_history_cache_key` extracted as the single source of truth for `generate_synthetic_history`'s own key derivation, plus a new cache-only reader `get_cached_synthetic_history_only` for `autotuner.build_if_held_replay_series`'s cache-hit-only replay seam; see the new Cache-Key Derivation section below). Prior: 2026-07-18 (Math Remediation R3-a, `DE-MATH-R3A-001` — pre-retune checklist item (b), MC band-edge arm-decision stability probed; `_MC_REPLAY_SIMULATION_PATHS` value unchanged, see the Monte Carlo Constants row below; prior: 2026-07-17, Math Remediation R1, `DE-MATH-R1-001` AC-1/MA-1 — `build_replay_day` stamps a real per-tick `last_percent_change` into replay holdings before every `run_monte_carlo` call; prior: 2026-06-29)

## Overview

`synthetic_history.py` fetches the trading-day history required by the autotuner's walk-forward replay. It downloads daily and intraday bars from Alpaca for every ticker held across all symphonies, caches results to disk, and builds the structured history dict consumed by `autotuner._collect_sim_returns`.

The fetch window is computed to guarantee at least `_REQUIRED_FETCH_TRADING_DAYS = 250 + 39 + 10 = 299` trading days (replay window + MC warmup + buffer). A widen-and-refetch loop (max 3 attempts, 60-day calendar step) handles upstream data gaps. A persistent shortfall raises `HistoryShortfallError`.

Per-day returns are emitted in **percent** units (`tick["return"] = agg_ret * 100.0` at the producer boundary). This is the canonical return frame for the autotuner; the CRRA-EU branch converts to decimal fractions at its entry boundary via `RETURN_PCT_TO_FRACTION`.

**Math Remediation R1 (`DE-MATH-R1-001` AC-1, MA-1 CRITICAL, 2026-07-17):** `build_replay_day` (see API Reference below) now stamps a REAL per-tick `last_percent_change` into the holdings it passes to `math_engine.run_monte_carlo`. Before this fix, `run_monte_carlo` received holdings carrying only ticker+allocation — no lpc at all — which `math_engine.py:1162-1166`'s (correct, unchanged) lpc-exclusion contract silently dropped from the MC baseline sum, making `mc_prob` constant across an entire replay day regardless of actual price action. This made the Trailing-Stop and Take-Profit exits structurally unreachable in the walk-forward replay, so three of the six Optuna-tuned parameters were objective-inert noise. The fix is confined ENTIRELY to this module — `alpha_bot_execution.py` and `math_engine.py` carry zero diff for this fix (see `DE-MATH-R1-001` for the architecture ruling and why the live `bot_state["current_holdings"]` construction sites were ruled off-limits).

## API Reference

### Main Entry Point

#### `generate_synthetic_history(bot_state: dict, current_date_str: str, *, n_jobs: int | None = None) → dict`

Generates the full synthetic history dict for all symphonies present in `bot_state`. Returns a symphony-keyed dict where each value is a date-keyed dict of tick lists.

This is the single call site for both `autotuner.run_autotuner` (pre-market) and `ai_advisor.revalidate_suggestion_oos` (on-demand).

**Cache-key derivation is shared, not mirrored (guard-alpha-preconditions, `DE-GUARD-ALPHA-PRECONDITIONS-001`, 2026-07-23):** the tickers -> holdings-hash -> versioned-filename derivation (previously inline in this function) is now delegated entirely to `_resolve_history_cache_key` (see the new Cache-Key Derivation section below) -- the SAME function `get_cached_synthetic_history_only`'s cache-only read uses, so a genuine same-day/same-holdings cache write here is always found by that reader. Behavior of this function is unchanged (byte-identical cache filenames); only the derivation's location moved.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `bot_state` | `dict` | Current daemon state dict (all symphonies) |
| `current_date_str` | `str` | ISO-8601 date string for today |
| `n_jobs` | `int \| None` | Keyword-only. When not `None`, overrides env-based parallelism resolution for the intraday-replay `Parallel(...)` call (see Parallelism section below). Default `None` preserves existing env-driven behavior for all callers that do not supply this argument. |

**Replay parallelism resolution:**
```
effective_n_jobs = n_jobs if n_jobs is not None else _resolve_replay_n_jobs()
```
`_resolve_replay_n_jobs()` reads `ALPHABOT_MAX_JOBS` and defaults to `-1` (all cores) when unset. The `autotuner` passes `n_jobs=_AUTOTUNE_REPLAY_N_JOBS` (= 1) to avoid OOM on the 2-core / 3.0 GiB droplet (DE-AUTOTUNE-OOM). All other callers receive `n_jobs=None` and inherit env-driven behavior.

---

### Cache-Key Derivation & Cache-Only Replay Read (guard-alpha-preconditions, `DE-GUARD-ALPHA-PRECONDITIONS-001`, 2026-07-23)

#### `_resolve_history_cache_key(bot_state: dict, current_date_str: str) -> tuple[set, dict, str | None]`

**SINGLE SOURCE OF TRUTH** for the tickers -> holdings-hash -> versioned-filename cache-key derivation (all-tickers set, symphony-holdings dict, and the resolved cache filename). Extracted from `generate_synthetic_history`'s prior inline logic (PM hard DRY requirement) so the network-capable writer (`generate_synthetic_history`, above) and the new cache-only reader (`get_cached_synthetic_history_only`, next) both call ONE function rather than each deriving the key independently -- a mirrored re-implementation could silently drift from this one, making the cache-only reader permanently miss even on a genuine same-day, same-holdings hit, with no test catching it until it happens live. Returns the cache-file component as `None` when `bot_state` has no holdings anywhere (mirrors the old `if not all_tickers: return {}` short-circuit `generate_synthetic_history` used to have inline).

#### `get_cached_synthetic_history_only(bot_state: dict, current_date_str: str) -> dict | None`

CACHE-ONLY read of the synthetic-history file cache -- **NEVER triggers a live fetch** (contrast with `generate_synthetic_history` above, which fetches on a cache miss). Uses `_resolve_history_cache_key` (above) for the identical cache-key derivation `generate_synthetic_history` uses, re-derived once per candidate date (see BACKWARD PROBE below), so this reader and the network-capable writer always agree on which file represents a given date's synthetic history for this `bot_state`. Returns `None` on: no holdings anywhere in `bot_state`, every candidate date in the backward window missing or corrupt/unreadable (delegates to `load_cached_history`, which never raises). Sole consumer: `autotuner.build_if_held_replay_series` (see `docs/generated/autotuner.md`) -- the guard-alpha-preconditions dashboard route's cache-hit-only replay seam, which must never drive Alpaca fetch volume from a GET.

**BACKWARD PROBE (corrected 2026-07-24 at the PM's live gate, `DE-GUARD-ALPHA-PRECONDITIONS-001` R2).** Originally this function checked ONLY `current_date_str`'s exact cache file -- since the synthetic-history cache is written once per weekly autotune cycle, that meant a cold miss on 6 of 7 days by construction (the droplet's newest cache file was 6 calendar days stale when the PM's live gate ran). The fix walks backward from `current_date_str` through up to `AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS=10` NYSE trading days (via `market_calendar.is_trading_day` -- this project's single source of truth for trading-day determination, never a re-derived weekday proxy), re-deriving the cache key via `_resolve_history_cache_key` at EACH candidate date for the CURRENT `bot_state` holdings. `current_date_str` itself (offset 0) is checked first and returned immediately on a hit -- newest-hit-wins falls out of the loop order, not a separate sort step. A holdings change since an aged file was written naturally degrades to a miss for that candidate: the aged file's embedded holdings-hash reflects the OLD holdings and simply won't match a freshly computed hash for current holdings, no special-casing needed. Every candidate is still only ever `load_cached_history` -- zero network calls anywhere in the walk, re-verified (not merely assumed) by `tests/synthetic_history/test_autotune_cache_backward_probe.py`.

**CACHE-MISS LOG CLASSIFICATION (hygiene cycle, `DE-HYGIENE-R1-001`, 2026-07-25).** The backward walk previously called `load_cached_history` unconditionally for every candidate date, which raises `FileNotFoundError` internally on a plain missing file and logs a per-file `WARNING "corrupt or unreadable cache file"` -- misleading, since a miss is the EXPECTED case under the weekly autotune cache cadence (up to 11 misleading WARNINGs per route call). Fixed: each candidate's file is now existence-checked (`os.path.exists`) BEFORE `load_cached_history` is called at all (`synthetic_history.py:577`) -- a missing file is collected into a local accumulator instead of reaching `load_cached_history`, which is therefore only ever called for a candidate whose file genuinely exists on disk, so its own per-file WARNING path/wording is byte-unchanged and still fires verbatim for real corruption (and for its OTHER caller, `generate_synthetic_history`, unaffected by construction -- this fix touches only `get_cached_synthetic_history_only`'s own call site). At most ONE aggregate `logging.info` line, via new `_log_cache_miss_summary` (`synthetic_history.py:548`), is emitted per call -- on a hit, or on walk-back exhaustion -- naming the miss count and date range and stating explicitly this is "expected under the weekly cache cadence, not an anomaly." Walk-back depth, candidate stepping, and all return values are unchanged. See `DE-HYGIENE-R1-001` in `DECISIONS.md`.


---

### Per-Day Tick Builder

#### `build_replay_day(sym_id, date_str, holdings, intraday_by_date, timestamps, hist_data_up_to_yesterday, yesterday_closes, spy_today) → list[dict]`

Builds one symphony's replay tick list for a single day — API-free (consumes pre-fetched bar data, no live Alpaca call), so the replay's Monte-Carlo parity (neighbor_k, insufficient-MC handling, and — as of this cycle — per-tick lpc sensitivity) is testable from fixtures. `generate_synthetic_history`'s `process_day` delegates to this so the tick-building logic has one home. Returns a list of tick dicts with the replay tick schema: `{time, return, mc_prob, vol, vwap_diff, base_atr_pct, valid_vwap_weight}`. `mc_prob` is the `run_monte_carlo` result verbatim, including the `None` sentinel for an insufficient-MC tick (production's fail-safe contract).

**Math Remediation R1 (AC-1/MA-1, `DE-MATH-R1-001`) — per-tick lpc stamping:** for each tick, a per-ticker `tick_lpc: dict[str, float]` is computed from the SAME `(c - y_close) / y_close` fraction already computed for the tick's aggregate return (`agg_ret`) — fraction basis vs. prior session close, confirmed by arithmetic cross-check against a captured Composer fixture (`tests/fixtures/composer/symphony_stats_meta.json`). A fresh, **non-mutating** `priced_holdings = [{**h, "last_percent_change": tick_lpc.get(h["ticker"])} for h in holdings]` list is built and passed to `math_engine.run_monte_carlo` in place of the caller's `holdings` argument, which is never modified — `holdings` is closure-captured and reused across every day of the same symphony's `Parallel(n_jobs=...)` replay, so an in-place stamp would leak a stale lpc value forward into a later day's call. A ticker with no bar for this tick, or a non-positive `y_close` (the two DISTINCT gapped-data branches — see `DE-MATH-R1-001` ADDENDUM 7), gets no `tick_lpc` entry: `last_percent_change` is then `None`, and `math_engine.run_monte_carlo`'s existing lpc-exclusion contract (`math_engine.py:1162-1166`, unchanged) drops it from the baseline sum exactly as it does for a genuinely lpc-less live holding — never a fabricated `0.0`. A missing PRIOR CLOSE is a separate, opposite-outcome branch: the code falls back to the tick's own close (`ret=0.0`), a REAL value that IS included in the sum.

**Effect:** `mc_prob` now varies tick-to-tick within a replay day as lpc varies (previously constant for the entire day) — this is the fix that makes the Trailing-Stop arm band `[5,15)` and the exit gate `>=60` reachable in replay (see `autotuner.md`'s `_replay_exit_tick`).

---

### Fetch Helpers

#### `compute_fetch_window_start(end_date) → date`
Returns the fetch-window start date. Adds `_REQUIRED_FETCH_TRADING_DAYS * _CALENDAR_DAYS_PER_TRADING_DAY` calendar days as a generous early bound; the genuine floor is enforced post-fetch on the returned bar count.

#### `utc_to_eastern(utc_dt) → datetime`
Converts a UTC datetime to US Eastern (DST-aware via `ZoneInfo("America/New_York")`).

---

### Parallelism

The intraday tick-replay step (`Parallel(n_jobs=effective_n_jobs)(delayed(process_day)(d) for d in intraday_dates)`) parallelises across trading days. `effective_n_jobs` is resolved as:

1. **Caller-supplied** (`n_jobs` param is not `None`): use it directly. The autotuner always supplies `n_jobs=1` (DE-AUTOTUNE-OOM).
2. **Env-driven** (`n_jobs` is `None`): `_resolve_replay_n_jobs()` returns `int(ALPHABOT_MAX_JOBS)` when set, else `-1` (all cores). Tests set `ALPHABOT_MAX_JOBS=1` via `tests/conftest.py` to prevent xdist × cores fan-out from crashing the host.

`n_jobs=1` uses joblib's sequential backend (no forked workers). This is reproducibility-neutral (`synthetic_history.py:35`): the replay result is independent of `n_jobs`. **This independence is why `build_replay_day`'s AC-1 lpc stamping had to be non-mutating** — `holdings` is a shared closure captured once per symphony and reused across every parallel day-worker; an in-place stamp would have introduced a `n_jobs`-order-dependent race, breaking this exact guarantee.

---

### Exception

#### `class HistoryShortfallError(Exception)`
Raised when synthetic-history generation cannot meet the trading-day floor after the bounded widen-and-refetch loop. Caught precisely by `autotuner.run_autotuner` to convert to a graceful abort.

## Types

### Fetch-Window Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `_WALK_FORWARD_TRADING_DAYS` | 250 | Autotuner replay window length |
| `_MC_WARMUP_TRADING_DAYS` | 39 | `MC_MIN_HISTORY_DAYS + (MC_VOL_WINDOW_DAYS - 1)` |
| `_FETCH_WINDOW_BUFFER_TRADING_DAYS` | 10 | Holiday / margin safety buffer |
| `_REQUIRED_FETCH_TRADING_DAYS` | 299 | Total trading days the fetch must guarantee |
| `_CALENDAR_DAYS_PER_TRADING_DAY` | 1.5 | Calendar-to-trading-day padding factor |
| `_FETCH_WIDEN_STEP_CALENDAR_DAYS` | 60 | Step size for widen-and-refetch |
| `_MAX_FETCH_WIDEN_ATTEMPTS` | 3 | Hard bound on widen loop |

### Cache-Probe Constants (guard-alpha-preconditions, `DE-GUARD-ALPHA-PRECONDITIONS-001` R2, 2026-07-24)

| Constant | Value | Description |
|----------|-------|-------------|
| `AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS` | 10 | Bound on `get_cached_synthetic_history_only`'s backward date-scan (see that function's BACKWARD PROBE note above). Source: weekly autotune cadence plus a buffer for a missed/aborted run -- the droplet's newest cache file was 6 calendar days stale when the PM's live gate ran, so 10 covers a full missed week with margin. |

### Parallelism Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `_MAX_JOBS_ENV` | `"ALPHABOT_MAX_JOBS"` | Env var name read by `_resolve_replay_n_jobs()` |

### Monte Carlo Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `_MC_REPLAY_SIMULATION_PATHS` | 300 | Path count `build_replay_day` passes to `math_engine.run_monte_carlo` — deliberately lower than `math_engine.MC_DEFAULT_SIMULATION_PATHS` (5000) as a replay-throughput approximation over 250 days × N symphonies. `DE-MATH-R1-001`'s AC-6 parity battery matches this config exactly (rather than the 5000-path default) so the battery tests decision-logic parity, not MC-sampling parity. **Math Remediation R3-a (`DE-MATH-R3A-001`, 2026-07-18) — probed, value unchanged:** `scripts/mc_band_edge_stability_probe.py` measured the arm-decision flip-rate of this 300-path estimate against higher reference counts near the lower arm-band boundary. Finding: instability very close to the boundary (committed headline scenario, 0.3pp inside the boundary) is proximity-driven and NOT reducible by more paths — even a production-parity-vs-parity (5000-vs-5000) self-comparison flips ~28% of the time (see `docs/generated/mc-band-edge-stability.md`) — while instability at larger offsets from the boundary IS reducible by more paths. No single-constant bump target is certified for the committed near-edge scenario, so **this value is unchanged by R3-a**, confirmed by a dedicated scope-guard test (`test_replay_constant_is_currently_the_probed_300`). See `DE-MATH-R3A-001` in `DECISIONS.md` for the full pre-retune checklist record — an input for R3-b/c/d, not itself a live-path change. |

## Internal Dependencies

- `math_engine` — `MC_MIN_HISTORY_DAYS`, `MC_VOL_WINDOW_DAYS` (for `_MC_WARMUP_TRADING_DAYS` computation), `calculate_20d_vol`, `calculate_14d_atr_pct`, `run_monte_carlo` (the sole replay-path call site — see `DE-MATH-R1-001`'s call-site enumeration, confirmed via `tests/integration/test_run_monte_carlo_consumers_enumerated.py`), and the existing lpc-exclusion contract at `math_engine.py:1162-1166` (read-only; this cycle supplies the missing input, never relaxes the contract)
- Alpaca Markets data API (`ALPACA_BASE_URL`, `ALPACA_KEY`, `ALPACA_SECRET`)
- `joblib.Parallel` — parallel intraday tick-day replay (parallelism bounded by caller or env)
