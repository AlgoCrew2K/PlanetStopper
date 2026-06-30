# TDD Handoff — DE-AUTOTUNE-OOM (replay n_jobs bounding)

**Cycle:** DE-AUTOTUNE-OOM — autotuner replay parallelism bounding
**Branch:** `fix/autotune-oom-memory-bound`
**Phase:** green
**RED commit SHA:** `2ea5216`
**Worktree:** `C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/autotune-oom`
**Feature plan:** `feature-plans/autotune-oom-memory-bound.md`

---

## What is broken (the OOM root cause)

`synthetic_history.py:670`:
```python
results = Parallel(n_jobs=_resolve_replay_n_jobs())(...)
```
`_resolve_replay_n_jobs()` returns `-1` (all cores) when `ALPHABOT_MAX_JOBS` is
unset. On the 2-core / MemoryMax=3.0 GiB droplet this forks 2 tick-data-copying
workers → parent ~2 GB + 2 copies > 3 GB → cgroup OOM-kills the autotuner before
`save_autotune_run` is ever reached (AC-1 empirical profile, 2026-06-29).

---

## Fix scope (AC-1 verdict — LOCKED)

**AC-3 chunking is OUT OF SCOPE.** `n_jobs=1` alone keeps peak at 2.03 GiB (990 MB
headroom). The fix is two changes only:

### Change 1 — `synthetic_history.py`

Add `n_jobs` keyword argument to `generate_synthetic_history`:

```python
def generate_synthetic_history(bot_state, current_date_str, *, n_jobs=None):
    ...
    # At the Parallel call site (currently line 670):
    effective_n_jobs = n_jobs if n_jobs is not None else _resolve_replay_n_jobs()
    results = Parallel(n_jobs=effective_n_jobs)(
        delayed(process_day)(d) for d in intraday_dates
    )
```

- `n_jobs=None` default → ALL OTHER CALLERS are untouched (their `None` resolves
  via `_resolve_replay_n_jobs()` as before).
- Do NOT change `_resolve_replay_n_jobs()` — its global default of `-1` must stay
  `-1` (Guard G6 asserts this; changing it is out of scope and breaks other callers).

### Change 2 — `autotuner.py`

Add a named module-level constant AND thread it into the `generate_synthetic_history`
call at line 2158:

```python
# Bound the intraday-replay parallelism on the autotune path to prevent OOM on the
# 2-core / MemoryMax=3.0 GiB droplet. n_jobs=1 → joblib sequential backend (no fork),
# peak 2.03 GiB vs 3.0 GiB cap (AC-1 empirical profile 2026-06-29, DE-AUTOTUNE-OOM).
# Other generate_synthetic_history callers are untouched (their default resolves via
# _resolve_replay_n_jobs()). AC-6 sets ALPHABOT_MAX_JOBS=1 in .env as defense-in-depth.
_AUTOTUNE_REPLAY_N_JOBS = 1

# Then at the call site (autotuner.py:2158):
history_125d = synthetic_history.generate_synthetic_history(
    bot_state, current_date_str, n_jobs=_AUTOTUNE_REPLAY_N_JOBS
)
```

The constant name must contain at least one of: `JOBS`, `N_JOBS`, `REPLAY`
(T2 checks by value=1 AND name pattern).

---

## RED tests — what you must make GREEN

**File:** `tests/autotuner/test_replay_n_jobs_autotune_bound.py`
**Fixture:** `tests/fixtures/autotuner/replay_n_jobs_bound_contract.json`

| Test | What it asserts | Currently |
|------|----------------|-----------|
| T1 `test_generate_synthetic_history_api_has_n_jobs_kwarg` | `n_jobs` in `inspect.signature(generate_synthetic_history).parameters` | FAIL — no such param |
| T2 `test_autotuner_exposes_named_autotune_replay_n_jobs_constant` | module-level int constant value==1, name contains JOBS/N_JOBS/REPLAY | FAIL — no such constant |
| T3 `test_autotune_path_passes_bounded_n_jobs_when_alphabot_max_jobs_unset` | spy captures `n_jobs=1` kwarg when `ALPHABOT_MAX_JOBS` unset | FAIL — no kwarg passed |
| T4 `test_autotune_path_passes_bounded_n_jobs_even_when_env_set_to_minus_one` | spy captures `n_jobs=1` kwarg even with env=-1 | FAIL — no kwarg passed |
| T5 `test_generate_synthetic_history_passes_n_jobs_kwarg_to_parallel` | `Parallel` receives `n_jobs=1` when kwarg passed | FAIL — TypeError (no param) |
| G6 `test_resolve_replay_n_jobs_global_default_unchanged_when_env_unset` | `_resolve_replay_n_jobs()` returns -1 (unchanged) | PASS — must stay GREEN |
| G7 `test_mem_cap_default_not_raised_above_contract_ceiling` | `DEFAULT_CAP_GB <= 24` | PASS — must stay GREEN |

---

## Status Log

- [2026-06-29] implementer: GREEN complete — 7/7 tests passing (5 RED → GREEN, G6 + G7 guards held). AC-8 autotuner regression suite: 717/717 passed. No test bugs documented. Typecheck N/A (Python). Lint pending.

## Test File Issues (for test-writer to fix)

None.

## Implementation Notes

- `synthetic_history.generate_synthetic_history`: added `*, n_jobs=None` as keyword-only param. Used `effective_n_jobs = n_jobs if n_jobs is not None else _resolve_replay_n_jobs()` at the `Parallel` call site. Default `None` → existing env-driven behavior unchanged for all non-autotune callers. Global `_resolve_replay_n_jobs()` body untouched (G6 confirmed green).
- `autotuner.py`: added `_AUTOTUNE_REPLAY_N_JOBS = 1` module-level constant with a multi-line comment citing the AC-1 empirical result (2.03 GiB / 990 MB headroom, 2026-06-29, DE-AUTOTUNE-OOM). Constant name contains `AUTOTUNE`, `REPLAY`, `N_JOBS` — satisfies T2's name-pattern check. Passed at `run_autotuner`'s `generate_synthetic_history` call site; no other callers touched.
- No gold-plating: chunking (AC-3), Optuna n_jobs, memory caps, and `.env` AC-6 config are all out of scope and untouched.

---

## Hard rules for at-impl

1. **Write ONLY the minimum code to make the 5 RED tests GREEN** — no gold-plating,
   no AC-3 chunking, no changes beyond `synthetic_history.py` and `autotuner.py`.
2. **G6 and G7 must stay GREEN** — do not change `_resolve_replay_n_jobs()` default,
   do not raise `DEFAULT_CAP_GB`.
3. **NEVER merge, never checkout main.** Signal at-test + PM with your GREEN commit SHA.
4. Run the bounded suite only:
   ```
   pytest tests/autotuner/test_replay_n_jobs_autotune_bound.py -n0 -x
   ```
   with `ALPHABOT_TEST_MEM_CAP_GB=24`. Do NOT run the full tree.
5. Existing tests must not break (AC-8). Also run:
   ```
   pytest tests/autotuner/ -n0 --ignore=tests/autotuner/test_replay_n_jobs_autotune_bound.py
   ```
   to confirm no regressions in the autotuner suite.

---

## Scope boundaries (strict)

- **IN scope:** `synthetic_history.generate_synthetic_history` signature + Parallel call;
  `autotuner.run_autotuner` generate_synthetic_history call site + named constant.
- **OUT of scope:** `_resolve_replay_n_jobs()` body; any other callers of
  `generate_synthetic_history`; Optuna n_jobs; per-symphony memory release (AC-3);
  droplet `.env` (AC-6 — PM handles); systemd MemoryMax; any cap constant.
