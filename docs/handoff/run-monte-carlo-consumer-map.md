# run_monte_carlo Consumer Map — Phase-1 Baseline

**Fork-point SHA:** 5f44060  
**Authored by:** quant-test-writer (mc-sentinel-blast-radius cycle)  
**Plan ref:** feature-plans/decision-science/phase-1/mc-sentinel-blast-radius/plan.md (deliverable 1)

This table enumerates every call site for `run_monte_carlo(...)` and every
consumer of its return value (`prob_beating` / `mc_prob`) across the
non-test production codebase as of the Phase-1 baseline. Any Phase-1 PR that
mutates the call signature at any of these sites is out of scope and blocked.

---

## Call sites — `run_monte_carlo(...)` invocations

| File | Line | Variable bound | Context |
|------|------|----------------|---------|
| `alpha_bot_execution.py` | 1131 | `prob_beating` | Per-symphony per-cycle; seed derived from `derive_cycle_mc_seed(current_et.strftime("%Y%m%d_%H%M"))` |
| `synthetic_history.py` | 340 | `mc_prob` | Per-symphony per-day replay; seed derived from `derive_cycle_mc_seed(f"{sym_id}_{date_str}")` |

*(autotuner.py has zero direct `run_monte_carlo` calls — it consumes `mc_prob` from tick dicts pre-computed by `synthetic_history.py`)*

---

## Consumer sites — `prob_beating` / `mc_prob` / `mc_available` readers

### `alpha_bot_execution.py`

| Line | Consumer pattern | Sentinel handling |
|------|-----------------|-------------------|
| 1155 | `mc_available = prob_beating is not None` | Guard — all downstream branches read `mc_available` |
| 1157 | `if mc_available and acc_TAKE_PROFIT_MC_PCT <= prob_beating < acc_TRIGGER_THRESHOLD_PCT` | Arm gate — gated on `mc_available` |
| 1159 | `f"MC Prob {prob_beating:.1f}%"` | Format only reached inside `mc_available` guard |
| 1174–1175 | `if mc_available and prob_beating > (acc_TRIGGER_THRESHOLD_PCT * 2)` | Disarm gate — gated on `mc_available` |
| 1184–1187 | `if mc_available: bot_state[...]["mc_history"].append(prob_beating)` | History append — None excluded by `mc_available` guard |
| 1263 | `prob_beating=prob_beating` (passed to `compute_exit_confirmation`) | `compute_exit_confirmation` accepts `float \| None`; None → MC gate bypassed (fail-safe) |
| 1292–1293 | `mc_available=mc_available, prob_beating=prob_beating` (passed to `compute_tp_confirmation`) | TP function gated on `mc_available` |
| 1307, 1311 | `f"...MC Prob {prob_beating:.1f}%..."` | Format inside TP-arm block, which is inside `mc_available` guard via `compute_tp_confirmation` |
| 1359 | `f"{prob_beating:.1f}%" if mc_available else "N/A"` | Sentinel-safe conditional format |
| 1367 | `bot_state[...]["mc_prob"] = prob_beating` | State write — None is valid; downstream reads check `is not None` |
| 1402 | `mc_available and prob_beating >= acc_TRIGGER_THRESHOLD_PCT` | Sanity gate — gated on `mc_available` |
| 1449 | `"mc_prob": prob_beating` | Snapshot dict — None is valid; readers check `is not None` |
| 1506 | `"prob_beating": prob_beating` | Exit report payload — passed to `reporting.send_exit_alert` |
| 1762 | `"mc_prob": item["prob_beating"]` | Chart history emission — reads prior snapshot |
| 1794 | `item["prob_beating"]` | Passed to `reporting.send_exit_alert` |

### `reporting.py`

| Line | Consumer pattern | Sentinel handling |
|------|-----------------|-------------------|
| 498–500 | `mc_prob_text = f"{prob_beating:.1f}%" if prob_beating is not None else "N/A"` | Sentinel-safe conditional format |
| 507 | `{"name": "MC Probability", "value": mc_prob_text, ...}` | Uses pre-guarded `mc_prob_text` |
| 522 | `f"...{mc_prob_text}..."` | Uses pre-guarded `mc_prob_text` |

### `synthetic_history.py`

| Line | Consumer pattern | Sentinel handling |
|------|-----------------|-------------------|
| 340–346 | `mc_prob = math_engine.run_monte_carlo(...)` | Assigns result verbatim |
| 356 | `"mc_prob": mc_prob` | Written to tick dict — None carried through |

### `autotuner.py`

| Line | Consumer pattern | Sentinel handling |
|------|-----------------|-------------------|
| 463 | `mc = tick.get("mc_prob", 50.0)` | Reads from tick dict — NOTE: `.get(key, default)` returns None when key exists with None value (not the default 50.0); `mc_available = mc is not None` guards downstream |
| 464 | `mc_available = mc is not None` | Guard — all downstream branches read `mc_available` |
| 491 | `if mc_available and take_profit_mc <= mc < trigger_threshold` | Arm gate — gated |
| 495 | `if mc_available and mc > (trigger_threshold * 2) and ret > 0.0` | Disarm gate — gated |
| 535 | `prob_beating=mc` (passed to `compute_exit_confirmation`) | `compute_exit_confirmation` accepts None; fail-safe |
| 547 | `prob_beating=mc` (passed to `compute_tp_confirmation`) | TP function gated via `mc_available` |

### `app.py`

| Line | Consumer pattern | Sentinel handling |
|------|-----------------|-------------------|
| 833–836 | `s.get("mc_prob") if s.get("mc_prob") is not None else -999.0` | Sort key None guard — sentinel mapped to -999.0 for sort stability |
| 932 | `"mc_prob": None` | Initial state — default None for new symphonies (sentinel is the correct initial value) |
| 1171–1173 | `key=lambda s: s.get("mc_prob") if s.get("mc_prob") is not None else -999.0` | Sort key None guard (second sort path) |

### `engine/dual_altitude.py`

| Line | Consumer pattern | Sentinel handling |
|------|-----------------|-------------------|
| 96 | `"mc_prob": None` | Initial state — default None in the dual-altitude engine's initial symphony state |

---

## Summary

| File | Direct `run_monte_carlo` calls | `prob_beating`/`mc_prob` consumer sites |
|------|-------------------------------|-----------------------------------------|
| `alpha_bot_execution.py` | 1 (line 1131) | 15 sites (lines 1155–1794) |
| `synthetic_history.py` | 1 (line 340) | 2 sites (lines 340, 356) |
| `autotuner.py` | 0 (reads from tick dict) | 7 sites (lines 463–547) |
| `reporting.py` | 0 (receives `prob_beating` argument) | 3 sites (lines 498–522) |
| `app.py` | 0 (reads from bot_state dict) | 3 sites (lines 833–1173) |
| `engine/dual_altitude.py` | 0 (initial state only) | 1 site (line 96) |
| **Total** | **2 direct calls** | **31 consumer references** |

**Blast-radius conclusion:** any change to `run_monte_carlo`'s return type or
signature touches 2 direct call sites and propagates through 31 downstream
consumer references across 6 files. The Phase-1 freeze (enforced by
`tests/math_engine/test_run_monte_carlo_signature_frozen.py`) is non-negotiable
until last-symphony Phase-2 cutover.

---

## Invariants this map enforces

1. Every `prob_beating is not None` consumer guard must remain on the production
   path — removing any guard re-exposes the pre-fix `TypeError` that aborted
   live cycles (memory: project_mc_sentinel_consumer_blast_radius).
2. The `mc_available` pattern in `autotuner.py:464` and `alpha_bot_execution.py:1155`
   is the canonical sentinel-check idiom — `mc is not None` — not `.get(key, default)`.
   The `autotuner.py:463` `.get("mc_prob", 50.0)` default is intentionally unreachable
   when the key exists with a None value; `mc_available` correctly catches the None.
3. `compute_exit_confirmation` and `compute_tp_confirmation` accept `prob_beating: float | None`
   and handle the sentinel path internally; callers must not pre-filter None before
   passing it to these functions.
