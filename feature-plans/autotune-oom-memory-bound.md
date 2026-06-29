# Feature: Autotuner Memory Bounding — keep the weekly walk-forward under the 3 GiB cap (DE-AUTOTUNE-OOM)
Status: ready
Created: 2026-06-29

## Summary
The AI Advisor shows "Optuna has not yet run … OOS alpha: N/A" for every symphony because the `autotune_runs` table is **empty** — the WEEKLY (Fri EOD) walk-forward autotuner is **OOM-killed** on the droplet before it ever reaches `save_autotune_run`. Root cause (recon + PM-verified, supersedes the earlier "Optuna n_jobs" theory): **Optuna's own `n_jobs` already defaults to 1** (deliberate — both study sites use a shared SQLite RDBStorage whose writer lock can't take concurrent writes, `autotuner.py:243-261`). The actual memory driver is **`synthetic_history.py:670` `Parallel(n_jobs=_resolve_replay_n_jobs())`** for the intraday tick replay, whose `n_jobs` defaults to **-1 (all cores)** via the `ALPHABOT_MAX_JOBS` env knob — and that knob is **UNSET in the droplet `.env`** (PM-confirmed). On the 2-core / `MemoryMax=3221225472` (3.0 GiB) droplet, joblib forks 2 worker processes each copying tick data, on top of the parent holding `history_125d` (ALL active symphonies' 250-day history, loaded once at `autotuner.py:2158` and held for the whole run) + per-symphony CPCV path histories (`:2267-2273`) + the growing 500-trial Optuna study → peak RSS exceeds 3 GiB → the cgroup OOM-kills it (`oom_memcg=/system.slice/planetstopper.service`).

The bounding knob already exists; the fix is to make the autotune path **never rely on an unset env var to avoid the all-core fan-out** (the `-1` default is documented in-code as having "crashed the host"), bound the replay parallelism, and — only if profiling proves it necessary — release per-symphony memory between symphonies so the whole walk-forward completes under 3 GiB. **The `MemoryMax=3 GiB` cgroup cap is a hard host-safety boundary and MUST NOT be raised** (operator directive: raising it hard-reboots the box).

**Verified premises (droplet, 2026-06-29):** `.env` sets neither `ALPHABOT_MAX_JOBS` nor `OPTUNA_N_JOBS`; `nproc=2`; `systemctl show planetstopper.service -p MemoryMax` = `3221225472`. The `synthetic_history` knob (`_MAX_JOBS_ENV="ALPHABOT_MAX_JOBS"`, `_resolve_replay_n_jobs()`, applied at the `Parallel(...)` call) and the test-time setting `ALPHABOT_MAX_JOBS=1` (`tests/conftest.py`) already exist.

## Acceptance Criteria
- [ ] AC-1 (empirical, drives the rest): A **cgroup-bounded** memory profile of a representative walk-forward run determines whether `ALPHABOT_MAX_JOBS=1` ALONE keeps peak RSS under 3 GiB, or whether per-symphony memory release (AC-3) is ALSO required. The run MUST be executed inside a `MemoryMax=3G` scope (e.g. `systemd-run --scope -p MemoryMax=3G -p MemorySwapMax=0`) against a **/tmp copy** of the state DB (NEVER the live DB, NEVER a second live engine) so it is contained and cannot crash the host. Peak RSS + outcome (completes vs OOM-killed) recorded in the Decisions table.
- [ ] AC-2 (default-hardening — the core fix): the autotuner's `synthetic_history` replay calls resolve to a **bounded** `n_jobs` that does NOT fan out to all cores **even when `ALPHABOT_MAX_JOBS` is unset**. The `-1` default (documented as host-crashing) must be unreachable on the autotune path — the autotuner explicitly passes a bounded degree (preferred: scope the bound to the autotune path; do NOT silently change other callers' `-1` default unless AC-1 shows it necessary). Tested without relying on the droplet `.env`.
- [ ] AC-3 (conditional on AC-1): IF `n_jobs=1` alone exceeds 3 GiB, the per-symphony loop (`autotuner.py:2240-2748`) RELEASES large in-memory structures (per-symphony `history_125d` slices, CPCV path histories, the completed Optuna study) before the next symphony so peak RSS stays under 3 GiB. If AC-1 shows config alone suffices, AC-3 is explicitly out of scope (documented), not silently dropped.
- [ ] AC-4: a representative walk-forward run completes **under 3 GiB without OOM** and reaches `save_autotune_run` — i.e. writes ≥1 `autotune_runs` row. (Behavioral, droplet, cgroup-bounded; reduced trial/symphony count acceptable for the verify as long as it exercises the real `synthetic_history` replay + a real study + the save.)
- [ ] AC-5: **No memory-cap is raised anywhere** — no change to `planetstopper.service` `MemoryMax`, no systemd edits, no `ALPHABOT_TEST_MEM_CAP_GB` change. (Guard: the diff touches no systemd unit / no cap constant upward.)
- [ ] AC-6: the droplet `.env` sets `ALPHABOT_MAX_JOBS` to a bounded value (config; part of the deploy steps, documented) as defense-in-depth on top of AC-2's code-level default-hardening.
- [ ] AC-7: **reproducibility-neutral** — bounding parallelism (and any memory release) does NOT change autotuner numerical outputs. The replay is already documented as reproducibility-neutral w.r.t. `n_jobs` (`synthetic_history.py:35`); a golden-fixture / determinism test confirms trial results are unchanged vs the pre-fix path at the same `n_jobs`.
- [ ] AC-8: no change to the engine's live per-cycle execution / trading path; the autotuner stays invoked only from the existing weekly (`alpha_bot_execution.py:1105`/`:1109`) + `force_run` entry points. Existing autotuner + synthetic_history tests still pass.

## Architecture
**Bound the replay parallelism on the autotune path (AC-2):**
- `synthetic_history.py`: `_resolve_replay_n_jobs()` (`:46-49`) returns `ALPHABOT_MAX_JOBS` or **default -1**. The `Parallel(n_jobs=_resolve_replay_n_jobs())` at `:670` is the all-core fan-out. **Preferred fix (least blast radius):** let the autotuner pass an explicit bounded `n_jobs` into its `synthetic_history.generate_synthetic_history(...)` call (`autotuner.py:2158`) — i.e. thread a `max_jobs` parameter through so the autotune path is bounded regardless of env, while leaving other callers' behavior untouched. Alternative (only if simpler/safer per the team): make the prod default itself memory-aware (e.g. `min(2, cpu_count)` capped, or default 1 when a low-memory env marker is set). Decision recorded after AC-1.
- The bound value: on a 2-core box, `n_jobs=1` removes the fork-doubling (the proven OOM contributor). The team picks 1 (vs 2) based on the AC-1 profile.

**Per-symphony memory release (AC-3, conditional):**
- `autotuner.py:2240-2748` per-symphony loop; `history_125d` loaded once at `:2158`; per-symphony CPCV at `:2267-2273`; study at `:2418-2430`. If AC-1 shows `n_jobs=1` alone still peaks >3 GiB, add explicit `del` + `gc.collect()` of the completed study + per-symphony slices at the end of each iteration, and (if `history_125d` for ALL symphonies is itself the driver) restructure to load/release per-symphony history rather than all-at-once.

**Invocation (unchanged):** weekly `alpha_bot_execution.py:1105` (`weekday()>=4 or force_run`) → `:1109` `autotuner.run_autotuner(bot_state, current_date_str, ACCOUNT_UUIDS, is_forced=force_run, spec_bundle_id=...)`. Manual populate = a `force_run=True` invocation, run cgroup-bounded.

## Edge Cases
- `autotune_runs` empty (cold) → after the fix + a force run, ≥1 row exists; the advisor stops showing "Optuna not run".
- Partial run / OOM mid-loop → symphonies completed before any failure must have already persisted their `autotune_runs` rows (per-symphony save, not a single end-of-run save) so a late failure doesn't lose earlier work. Confirm where `save_autotune_run` is called relative to the loop.
- `ALPHABOT_MAX_JOBS` set vs unset → AC-2 makes the autotune path safe even when unset; AC-6 sets it anyway (defense-in-depth).
- Reproducibility — bounding `n_jobs` / releasing memory must not change trial outcomes (AC-7).
- The optimization DB (`optuna_studies.db`) SQLite writer lock — already safe at `OPTUNA_N_JOBS=1`; do not introduce study-level parallelism.
- 2-core box: `n_jobs=2` and `-1` are equivalent here; only `n_jobs=1` actually reduces the fork count.

## Security Considerations
- No new external surface; the autotuner is internal, off the dashboard, off the live-trade path. No user input, no new secrets, no network change. The empirical run uses a /tmp DB copy and a contained cgroup — never the live DB, never a second live engine (the "never two live daemons" rule).

## Testing Strategy
- **Unit:** the autotune path resolves a bounded `n_jobs` (NOT -1) regardless of `ALPHABOT_MAX_JOBS` being unset (patch env empty → assert the value passed to `Parallel`/`generate_synthetic_history` is bounded). Reproducibility/determinism test: trial outputs identical at the bounded `n_jobs` vs a reference (golden-fixture, no hardcoded producer values — assert equality of the two runs, not literals).
- **Behavioral (droplet, cgroup-bounded — PM/team):** `systemd-run --scope -p MemoryMax=3G -p MemorySwapMax=0 --uid=planetstopper env ALPHABOT_MAX_JOBS=1 DB_PATH=/tmp/at_test.db …` running a representative `force_run` autotune; capture peak RSS (`systemd-cgtop`/`/proc/<pid>/status` VmHWM) + confirm it completes without the cgroup OOM-killing it + writes an `autotune_runs` row. This is AC-1 + AC-4.
- **Bounded `-n0` mem-capped local runs only; full cloud CI is the merge gate.** NEVER run the uncapped/full/-n>4 suite locally.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Empirical-first (AC-1 before finalizing AC-3 scope) | Avoid over-engineering: don't add symphony chunking if bounding `n_jobs` alone fits under 3 GiB. The cgroup-bounded profile gives the answer safely. |
| Scope the `n_jobs` bound to the autotune path (thread a param) rather than changing the global `-1` default | Least blast radius — other `synthetic_history` callers that legitimately want all-core speed are untouched; the autotune path (the only one that OOM'd) is hardened. Revisit only if AC-1 demands a global change. |
| NEVER raise `MemoryMax` | Operator hard rule — raising the cgroup cap hard-reboots the 4 GB box. The cap is the host-safety boundary; the fix lives under it. |
| Empirical run is cgroup-contained against a /tmp DB copy | A manual autotune OUTSIDE the service cgroup has no memory limit and could crash the host; running it inside `MemoryMax=3G` replicates prod safely (worst case = a contained OOM-kill, not a host reboot). Never a second live engine. |
| Per-symphony `save_autotune_run` preserved | A late OOM must not discard symphonies already optimized. |

## Scope Boundaries
- **IN:** profile the autotune memory under a 3 GiB cgroup (AC-1); bound the `synthetic_history` replay `n_jobs` on the autotune path so an unset env var can't trigger all-core fan-out (AC-2); conditional per-symphony memory release if profiling requires it (AC-3); a contained force-run that completes under 3 GiB and populates `autotune_runs` (AC-4); set the droplet `.env` knob (AC-6); reproducibility + no-cap-raise guards (AC-5/AC-7).
- **OUT:** raising `MemoryMax` or any memory cap (forbidden); changing trial counts, CPCV folds, or walk-forward methodology; changing other `synthetic_history` callers' parallelism unless AC-1 forces a global default change; the AI-Advisor latency fast-follow backlog (separate); any engine live-trade-path change.
