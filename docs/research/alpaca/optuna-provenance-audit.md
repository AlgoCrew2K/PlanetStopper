# Optuna Calibration Data-Source Audit — Resolution of Alpaca Fix Gate 10

**Date:** 2026-05-12
**Auditor:** Explore agent (very thorough, read-only)
**Trigger:** quant-code-reviewer Gate 10 flag on commit `ce043fd` (`Merge: pin feed=iex on Alpaca market-data call sites`)
**Question:** Did any Optuna walk-forward study calibrate against pre-fix SIP-default data from `alpha_bot_execution.py`, or did all studies use IEX-pinned `synthetic_history.py` data?

## Verdict

**CLEAR.** All Optuna calibration data flows exclusively from `synthetic_history.py` (IEX). No live SIP data ever entered the optimization pipeline. The just-merged feed=iex fix (`ce043fd`) does NOT retroactively invalidate any Optuna-calibrated parameters.

## Architectural Reason This Is Clean

1. **`autotuner.py`'s only tick-data input is `synthetic_history.generate_synthetic_history()`** (`autotuner.py:81`). That function always called Alpaca with `feed=iex` (`synthetic_history.py:36`) — this was true both before and after `ce043fd`.

2. **`alpha_bot_execution.py`'s `fetch_alpaca_history`** (pre-fix: SIP, post-fix: IEX) feeds only the live intraday decision engine (`main()` at line 418). It is **never** imported by, called by, or written to any path that `autotuner.py` reads.

3. **`history_cache.json`** (the only SIP-exposed persisted artifact pre-fix) is on a dead-end branch: written and read exclusively within `alpha_bot_execution.fetch_alpaca_history`, never consumed by the optimization pipeline.

4. **`.gitignore`** entry for `optuna_studies.db` combined with the file's absence on disk in this working tree means there are no pre-existing studies to audit for SIP contamination in this environment.

## Data Flow Trace

### `autotuner.py` per-tick simulation data source
- `autotuner.py:81` — `history_125d = synthetic_history.generate_synthetic_history(bot_state, current_date_str)`
- Returns dict `{sym_id: {date_str: [tick_dicts]}}`. Every tick (`return`, `mc_prob`, `vol`, `vwap_diff`, `base_atr_pct`) computed entirely inside `generate_synthetic_history`.
- No second tick-data source exists. Autotuner does NOT call `fetch_alpaca_history`, does NOT read `history_cache.json`, does NOT access `chart_archive` or any optimization-DB historical records for tick data.
- Other autotuner data inputs (all out-of-scope for tick-stream calibration):
  - `post_mortem_*.json` files (lines 33–51) — execution-deviation penalty constants only
  - `database.load_chart_history()` / `database.save_chart_archive()` (lines 75–78) — 60-day rolling chart archive
  - `database.get_symphony_strategy()` / `database.save_symphony_strategy()` (lines 119, 361) — OUTPUT of optimization, not input

### `synthetic_history.py` output destination
- Filesystem cache at `synthetic_history.py:98` — `cache/synthetic_history_{date}_{hash}.json` (`.gitignore`d, transient)
- Returns `history_125d` dict in-memory directly to `autotuner.py:81`
- No DB write occurs in `synthetic_history.py`
- Both Alpaca calls use `feed=iex` at `synthetic_history.py:36` — pre-fix and post-fix

### `history_cache.json` role
- Writer: `alpha_bot_execution.py:208-210` (`fetch_alpaca_history()` after 3-year daily-bars download for Monte Carlo)
- Reader: `alpha_bot_execution.py:139-147` (same `fetch_alpaca_history()` on next call same trading day)
- **NOT** read by `autotuner.py` or `synthetic_history.py`
- Pre-fix: SIP-default. Post-fix: IEX. Any pre-fix file is stale-dated; date-guard forces regeneration at next-day boundary

### Optuna study storage
- `autotuner.py:294-299`: `sqlite:///optuna_studies.db` — relative path, project-root
- `.gitignore:8` confirms file is not in repo
- File does not exist in current working tree

### Live-data persistence pathway
- `fetch_alpaca_history` writes only `history_cache.json` — not read by autotuner
- `fetch_intraday_vwaps` writes nothing to disk
- `chart_archive` table written by `autotuner.py:78` contains chart display data, not tick streams; only read by `database.get_rolling_60day_chart()` for Discord
- **No bridge between live-captured Alpaca data and Optuna calibration input.**

## Recommended PM Actions (verbatim from auditor)

1. **No re-calibration required.** The Optuna pipeline was always IEX-clean. Existing `symphony_strategies` parameters in `alphabot_state.db` are not compromised by the SIP/IEX mismatch that existed on the live execution path.

2. **Rotate `history_cache.json`.** If a `history_cache.json` was written by a pre-fix run (before `ce043fd`), delete it before the next live trading day so `fetch_alpaca_history` regenerates from IEX. The date-guard (`cache.get("date") == current_date_str`) will force regeneration at the next day boundary automatically, but a manual delete eliminates any same-day residual risk if the fix is deployed mid-day.

3. **Confirm `optuna_studies.db` does not exist on the production host.** The file is absent in this repo checkout but may exist on the server where `alpha_bot_execution.py` runs. If it does, its trial history was generated against IEX (from `synthetic_history.py`), so no action is needed — but confirming gives you provenance certainty. Operational question: "Was `autotuner.py` ever run before the math_engine refactor series that began at commit `8a9bab5`?" If yes, those early studies used slightly different internal formulas (pre-extraction duplicates vs. canonical `math_engine` functions), but the feed was always IEX.

4. **The four feed-pinning regression tests** (in `tests/alpaca/test_feed_pinning.py`) now permanently guard both call sites in `alpha_bot_execution.py` AND `synthetic_history.py` against future drift. No additional manual audit cadence is needed; the test suite is the gate.

## Closure

Task #26 (Gate 10 follow-up) is resolved by this audit. The Alpaca feed=iex fix at `ce043fd` is fully cleared for live deploy — no parameter re-calibration prerequisite.
