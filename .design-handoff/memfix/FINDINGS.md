# Test-Suite Memory-Blowup -- Diagnosis & Fix

Branch: fix/test-memory-blowup
Worktree: C:/Users/paulm/Documents/Projects/POC/alphabot-memfix
Investigator: risk-engine-specialist (memfix). Host: 32 logical cores, Windows 11.
Already committed before this work: a7f2bac -- pytest-xdist cap "-n auto" -> "-n 2".

All suspect tests were executed SINGLE-PROCESS (-p no:xdist, no -n) inside a
psutil watchdog (.design-handoff/memfix/watchdog.py) that polls the whole process
tree RSS every 0.5s and kills it above an 8 GB cap. DB_PATH was pinned to
%TEMP%/memfix.db for every run; the production DB and the :8090 daemon were never
touched. No run exceeded the cap; no second crash occurred.

## Empirical baseline -- there is NO single-test 100 GB allocation

Single-process, watchdog-bounded peak RSS (tree):

| Scope | Result | Peak tree RSS |
|---|---|---|
| tests/math_engine/ (1164 tests) | passed | 0.37 GB |
| tests/autotuner/ (718 tests) | 716 pass / 2 numeric-flake fails | 0.23 GB |
| tests/analytics/ (quantstats) | 228 passed | 0.30 GB |
| tests/app/ (strategy-builder) | 530 passed | 0.31 GB |
| tests/calibration/ (real 100-trial sweeps) | 77 passed | 0.17 GB |
| tests/integration/ | 36 passed | 0.16 GB |
| tests/synthetic_history/ | 70 passed | 0.17 GB |
| heaviest individual tests (bootstrap SE, real run_simulation, real study.optimize canary, MC, cvar perf) | passed | <= 0.20 GB each |
| pytest --collect-only (all 6420 tests, one process) | collected | 0.27 GB |

Static review of every changed/heavy module (math_engine MC bootstrap & vol/ATR
matrices, autotuner PBO/CPCV/BHY, compute_pbo C(8,4)=70 combos, quantstats
compute_quantstats_metrics, advisors correlation/backtest clients) found only
small, well-sized allocations (dimensioned by tickers/dates/paths/configs). The
np.zeros/np.empty calls are all O(days x tickers). No cartesian merge, no
accidental broadcast, no unbounded per-trial accumulation of consequential size.

Conclusion: the ~100 GB single process in the Windows crash log was NOT a single
runtime array allocation. It was the multiplicative process fan-out below; on
Windows the committed (reserved) memory of nested child process pools is
attributed to a controller PID, reading as one process at 99.6 GB.

## FAULT 1 -- recursive pytest subprocesses inherit addopts (-n) -> nested xdist fan-out

File:line: tests/meta/test_zero_skip_xfail_close.py:491 and :424.

Two tests spawn a nested pytest:
- test_full_suite_reports_zero_skips_and_zero_xfails: subprocess.run([sys.executable, -m, pytest, ...]) runs the ENTIRE default suite as a child.
- test_pytest_collect_only_does_not_emit_any_forbidden_node_id: _collected_node_ids() spawns pytest --collect-only.

Mechanism. A pytest child run in this rootdir inherits addopts from pyproject.toml
(-n 2 --dist loadfile). Under the pre-a7f2bac -n auto, the nested run re-spawned one
worker per core (32). Because the parent suite was ALSO -n auto, and one of its 32
workers runs this meta-test, the nesting compounds: 32 outer workers x (1 controller
+ 32 inner workers) x the heavy scientific stack import
(numpy/scipy/pandas/quantstats->matplotlib/seaborn, optuna). Each interpreter ~0.3 GB
resident but reserves far more committed address space -- the >90 GB commit. The
a7f2bac -n 2 cap reduced but did NOT remove the hole: the nested child still inherited
-n 2 and re-spawned a worker pool inside an outer worker, and the full-suite child
re-ran every heavy tier serially with no memory bound.

Fix (this branch). Force every nested pytest invocation single-process: add
-p no:xdist and -o addopts= to both subprocess.run argvs. For the full-suite child,
blanking addopts drops the default marker filter, so -m "not live and not slow and not
perf" is RE-PASSED explicitly to keep the nested run scope identical (it must not
silently start running the excluded heavy tiers). Deselects preserved.

Guarded-run evidence (now bounded):
- collect-only meta test: passed, peak tree RSS 0.42 GB (parent + single-process child).
- full-suite meta test: whole suite single-process; nested child peaked ~0.4 GB (watchdog-observed), parent tree never approached the 8 GB cap.
- New static guard test_recursive_pytest_subprocesses_force_single_process pins -p no:xdist + -o addopts= on every nested pytest argv.

## FAULT 2 -- nested all-core parallelism amplifier (reproducibility-assessed)

Optuna study.optimize(n_jobs=...) -- already safe, no change. autotuner.py:2313 and
:2860 call study.optimize(..., n_jobs=_n_jobs) where _n_jobs =
_resolve_optuna_n_jobs_from_env() (autotuner.py:227) ALREADY defaults to 1 (SQLite
RDBStorage writer-lock safety). NOT a bare n_jobs=-1. No production change. Belt-and-
suspenders: tests pin OPTUNA_N_JOBS=1 via conftest so a stray .env cannot re-enable it.

synthetic_history.py joblib.Parallel(n_jobs=-1) -- the real all-core site.
File:line: synthetic_history.py:613 (pre-fix). generate_synthetic_history parallelizes
per-day replay (process_day) over ~125 intraday dates with Parallel(n_jobs=-1) (all
cores), nested inside each xdist worker.

Reproducibility assessment (critical). Reproducibility-NEUTRAL. The replay per-day
work is deterministic and order-independent: the only RNG in the path is the Monte-
Carlo bootstrap, seeded by a CONTENT-DERIVED seed
math_engine.derive_cycle_mc_seed(f"{sym_id}_{date_str}") (SHA-256 of sym_id_date_str,
math_engine.py:1022). No call in synthetic_history.py seeds from worker index, task
order, or n_jobs (only RNG ref is that content seed at synthetic_history.py:393).
Therefore the degree of parallelism cannot change numerical results -- verified by
monkeypatch resolver tests and the unchanged tests/synthetic_history/ golden suite
(70 passed) after the edit.

Fix (test-environment throttle, prod byte-identical).
- New synthetic_history._resolve_replay_n_jobs() reads ALPHABOT_MAX_JOBS:
  unset/garbled -> -1 (all cores) = UNCHANGED production behavior; integer N -> N.
- Parallel(n_jobs=-1) -> Parallel(n_jobs=_resolve_replay_n_jobs()).
- tests/conftest.py pytest_configure os.environ.setdefault(ALPHABOT_MAX_JOBS, 1)
  (and OPTUNA_N_JOBS=1) so the TEST environment runs single-process while production
  (vars unset) stays all-core. setdefault preserves an explicit operator override.

Guarded-run evidence: tests/synthetic_history/ 70 passed at 0.17 GB after the change;
new guard tests/synthetic_history/test_bounded_replay_parallelism.py (5 tests) pins
resolver behavior + forbids a bare-literal Parallel(n_jobs=...) + asserts conftest
sets the caps.

## The single safe pytest command going forward

Default command is now memory-safe (xdist capped at 2; conftest bounds nested
joblib/optuna to 1; recursive subprocess tests forced single-process). Run ONE
invocation at a time across the whole fleet, with DB_PATH set:

    $env:DB_PATH = "$env:TEMP/memfix.db"
    python -m pytest

For a single file / targeted run (fastest, zero fan-out):

    $env:DB_PATH = "$env:TEMP/memfix.db"
    python -m pytest <path> -n0

The serial meta-test (spawns the full suite as a child) is run explicitly and is now
bounded:

    python -m pytest tests/meta/test_zero_skip_xfail_close.py -n0

NEVER restore -n auto. NEVER run two pytest invocations concurrently.

## Files changed (this branch, on top of a7f2bac)

- synthetic_history.py -- _resolve_replay_n_jobs() + env-bounded Parallel.
- tests/conftest.py -- pytest_configure sets ALPHABOT_MAX_JOBS=1 + OPTUNA_N_JOBS=1.
- tests/meta/test_zero_skip_xfail_close.py -- recursive subprocesses forced single-process; self-guard added.
- tests/synthetic_history/test_bounded_replay_parallelism.py -- NEW regression guard.
- .design-handoff/memfix/watchdog.py -- sanctioned watchdog runner (tooling).
- .design-handoff/memfix/FINDINGS.md -- this file.
