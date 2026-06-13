# xdist Test-Isolation Fix

**Epic:** C — platform polish · **Status:** 🔴 not started.

## Problem

Running the suite under pytest-xdist (`-n auto` / `-n 2`) produces **spurious test-isolation
failures** that do NOT reproduce at `-n0`. Evidence: the "85 failures" scare on Cycle-4 main
were xdist artifacts — the same tree passed `125/0` at `-n0`. This forces the PM to gate every
merge at `-n0` (`-o addopts= -p no:xdist`), which is slower and a standing workaround.

Separately, `-n auto` recursive subprocesses × nested joblib `n_jobs=-1` were a PC-crash root
cause; `pyproject.toml` is pinned to `-n 2 --dist loadfile` (`a7f2bac`) as a memory-safety cap.

## Goal

Make the suite **parallel-safe** so `-n2`/`-n auto` gives the same results as `-n0`, removing
the need for the `-n0` workaround at merge gate — WITHOUT regressing the crash-safety cap.

## Acceptance criteria

1. Root-cause the isolation failures: identify the shared mutable state (DB path, module-level
   singletons, file handles, working dir, Optuna study names, env vars) leaking across xdist
   workers. Produce a `file:line` diagnosis BEFORE any fix (diagnose-before-fixing).
2. The full tree passes identically at `-n0` and `-n2` (≥10 runs each, no flaky delta) — flake
   replication on BOTH counts before declaring fixed.
3. The crash-safety story is preserved: no unbounded subprocess/joblib nesting reintroduced;
   memory stays bounded under the parallel run.
4. Fixes target the real isolation defect (conftest fixtures, per-worker temp DBs, `--dist
   loadfile` grouping), not blanket test skips.

## Approach

Likely a non-TDD diagnosis team first (auditors + synthesizer) to root-cause, then a Toxic Pair
to fix conftest/fixtures. Watch the known DB sentinel + `_isolate_db` autouse fixture +
`pytest_configure` DB_PATH wiring.

## Dependencies / cautions

- NEVER run two pytest invocations at once (crash history).
- Respect the `database._db_file()` pytest sentinel.
- This is the kind of work that unblocks faster merge gates for ALL other features — but it is
  lower priority than Epic A.
