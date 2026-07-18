# scripts/objective_variance_probe

> R3-a pre-retune checklist deliverable (a): proves the production Optuna walk-forward objective is genuinely sensitive to every tuned search-space dimension, with a non-vacuity control that can actually fail.

**Source:** `scripts/objective_variance_probe.py`
**Last updated:** 2026-07-18

## Overview

`objective_variance_probe` is an off-execution-path, D-1 (never-raises-by-design), bounded and deterministic probe consumed only by `tests/autotuner/test_r3a_walkforward_variance_all_dims.py` — never imported by `alpha_bot_execution.py` or any live codepath. It exists to close a gap the math-remediation program's R1 phase deliberately deferred: `DE-MATH-R1-001`'s AC-7 proved `TAKE_PROFIT_MC_PCT` objective-sensitive at the real walk-forward level, but the two parabolic dims (`PARABOLIC_VELOCITY_THRESHOLD`, `MAX_PARABOLIC_SQUEEZE`) were proven inert-free only at the wiring level. This module extends walk-forward objective-variance coverage to **all six** dims in `autotuner.OPTUNA_SEARCH_SPACE_KEYS` — including the three VWAP dims, which had never been walk-forward-tested at all (the pre-existing `test_ac7_inert_dims_objective_variance_smoke.py` fixture always sets `vwap == close`, which structurally never exercises them).

Every fixture scores through the REAL replay pipeline — `synthetic_history.build_replay_day` → `autotuner.run_simulation` (never the full `autotuner.run_autotuner` study; never the 500-trial production floor). Determinism is inherited for free: `build_replay_day`'s Monte-Carlo seed derives deterministically from `sym_id`+`date_str` (`math_engine.derive_cycle_mc_seed`), and every per-tick decision primitive is pure.

**Source-derived enumeration, not hardcoded (AC-1):** `production_tuned_dims()` reads `autotuner.OPTUNA_SEARCH_SPACE_KEYS` directly — the production authority — so a future dim added to the search space without a matching sweep fixture makes the consuming test's `test_swept_dims_equals_production_search_space` FAIL, not silently pass. A second, independent AST-based drift-guard (`suggest_names_in_run_autotuner_objective`) reads the REAL `trial.suggest_*(...)` calls out of `autotuner.run_autotuner`'s objective closure source, structurally scoped to that one function (a sibling function like `run_calibration_sweep` is never visited) — so `OPTUNA_SEARCH_SPACE_KEYS` itself is checked against what the optimizer actually suggests, not merely trusted as a hand-maintained constant. This closes a belt-and-suspenders gap flagged during RED-review: the constant is a validation contract that could in principle drift from the real `suggest_*` calls it's meant to mirror. `TRIGGER_THRESHOLD_PCT` is explicitly confirmed absent from both the constant and the real suggest-call set — it is a frozen, non-tuned default (`autotuner.py:1173`, `p.get("TRIGGER_THRESHOLD_PCT", 15.0)`), never a `trial.suggest_*` call.

**Config-robustness (the fixture-timing finding):** every fixture pads `_NEUTRAL_PAD_TICKS` (30) neutral ticks before its discriminating ticks. RED-review (`a0e3bec1`) found the original sensitivity proof was an artifact of the test-suite's pinned `EXECUTION_START_TIME=09:30` — at the droplet-production value (`9:35`, the config the R3-d retune actually runs `run_autotuner` under), all 6 dims went dead (span=0, fires=0), because the discriminating ticks sat before the action-phase gate opened. The fix (`db164fb8`) is timing-only, zero mechanism change, zero production diff — every fixture's discriminating ticks now start comfortably past both the action-phase gate and the 15-minute VWAP grace window at 09:30, at 9:35, and with headroom for other plausible operator start-times.

**Non-vacuity crux (`force_inert`):** `walkforward_dim_sweep(dim, force_inert=True)` pins the swept dim to one fixed baseline value for every sweep point — the swept value never reaches `params[dim]` at all, a genuine "ignore the swept param" path, not a special-cased short-circuit. The consuming test asserts a two-clause contract per (dim, EXECUTION_START_TIME): (1) the live sweep MUST vary (there is variance to attribute — guards against a dead fixture making the next clause trivially pass), and (2) `force_inert` MUST collapse that variance to byte-identical objectives (the variance is the dim's, not a fixture artifact). This crux is itself parametrized across `{09:30, 9:35}` (RED-review, `c8615201`, PM addition) — the same config-robustness bar the fixture-timing finding established, so "the variance is dim-driven" is proven at the retune's real config, not just the test-pinned default.

## API Reference

### `production_tuned_dims() -> frozenset[str]`

AC-1: the tuned-dim enumeration authority — returns `frozenset(autotuner.OPTUNA_SEARCH_SPACE_KEYS)` directly, never a hardcoded literal set.

**Returns:** `frozenset[str]` — the 6 production-tuned dims.

---

### `suggest_names_in_run_autotuner_objective() -> frozenset[str]`

AC-1 drift-guard: reads `autotuner.py`'s own live source via `inspect.getsource`, extracts every `trial.suggest_*("<NAME>", ...)` string literal scoped to `run_autotuner`'s AST subtree only, and returns the real set the optimizer actually suggests over. Independent of `OPTUNA_SEARCH_SPACE_KEYS` — used to catch drift between the constant and the real objective closure.

**Returns:** `frozenset[str]` — dim names read from source, not from the constant.

---

### `_extract_suggest_names_from_source(source: str, enclosing_func: str) -> frozenset[str]`

Pure AST seam underlying the drift-guard above. Walks the target function's subtree for `ast.Call` nodes whose callee is a `suggest_*` attribute access with a string-literal first argument. Structural scoping means a sibling function's `suggest_*` calls are never collected.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `source` | `str` | Python source text to parse |
| `enclosing_func` | `str` | Name of the function whose AST subtree is walked |

**Returns:** `frozenset[str]` — suggest-call names found, or empty if `enclosing_func` is not found.

---

### `walkforward_dim_sweep(dim: str, *, force_inert: bool = False) -> DimSweepResult`

AC-2/AC-3: sweeps `dim` over its registered bar-derived fixture (`SWEEP_VALUES_PER_DIM = 2` values), scoring each swept point via `autotuner.run_simulation` and independently re-deriving the per-tick decision trace via `autotuner.replay_exit_sequence` (the same `_replay_exit_tick` core `run_simulation` scores with) to count codepath fires.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `dim` | `str` | One of the 6 registered dims (`_FIXTURE_BUILDERS` keys) |
| `force_inert` | `bool` | If `True`, pins `dim` to its fixture's single baseline value for every swept point — the non-vacuity control |

**Returns:** `DimSweepResult` — `.objectives` (swept value → walk-forward objective) and `.codepath_fires` (count of ticks where the dim's decision codepath engaged, across every scoring date and swept value).

**Raises:** `ValueError` if `dim` has no registered fixture.

## Types

### `DimSweepResult` (frozen dataclass)

| Field | Type | Description |
|-------|------|-------------|
| `objectives` | `dict[float \| int, float]` | swept value → `autotuner.run_simulation` objective |
| `codepath_fires` | `int` | count of ticks (across every scoring date and swept value) where the dim's decision codepath actually engaged |

### `_DimFixture` (frozen dataclass, internal)

Everything one dim needs to score + fire-check: `sym_id`, `dates`, `bars_by_date` ((close, vwap) pairs), `hist_data_up_to_yesterday`, `yesterday_close`, `spy_today`, `baseline_params`, `sweep_values`, `baseline_value`, `fire_predicate`. One builder per dim in `_FIXTURE_BUILDERS` (`TAKE_PROFIT_MC_PCT`, `PARABOLIC_VELOCITY_THRESHOLD`, `MAX_PARABOLIC_SQUEEZE`, `VWAP_CROSS_HWM_PCT`, `VWAP_BLEED_TICKS`, `VWAP_BLEED_MULTIPLIER`).

## Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `SWEEP_VALUES_PER_DIM` | 2 | Values swept per dim (AC-5: a bounded smoke, never the production trial floor) |
| `SWEEP_MAX_DAYS` | 3 | Max scoring dates for any one fixture (`TAKE_PROFIT_MC_PCT`'s AC-7-derived 3-day fixture is the max; every other dim uses a single day) |
| `_NEUTRAL_PAD_TICKS` | 30 | Neutral ticks every fixture pads before its discriminating ticks — clears the action-phase gate + VWAP grace window at both `09:30` and `9:35` |
| `_INERT_BASELINE_PARAMS` | dict | "Never trips" default value per dim, used for every dim not currently under test in a given fixture |

## Internal Dependencies

- `autotuner` — `OPTUNA_SEARCH_SPACE_KEYS` (AC-1 enumeration authority), `run_simulation` (scoring), `replay_exit_sequence` (fire-trace), `_replay_grace_minutes`
- `synthetic_history` — `build_replay_day` (real per-tick replay history construction)
- `math_engine` — `derive_cycle_mc_seed` (transitively, via `build_replay_day`)
- stdlib only beyond the above: `ast`, `inspect`, `dataclasses`, `collections.abc.Callable`

## Consumers

- `tests/autotuner/test_r3a_walkforward_variance_all_dims.py` — the sole consumer. Drives `walkforward_dim_sweep` per dim (variance, force_inert collapse, codepath-fire, determinism) and the two enumeration functions (AC-1 source-derivation + drift-guard), parametrized across `EXECUTION_START_TIME ∈ {09:30, 9:35}` for the variance/force_inert/fire assertions.
