# Test-Footprint Reduction + Memory-Cap Hardening

Status: ready

## Summary
Reduce the pytest suite's process/memory footprint so it is genuinely lightweight (sits far below the 24 GB cap rather than merely bounded by it), and harden the Windows total-job memory cap so it can never silently become a placebo. Crash prevention is the operator's #1 priority after two PC hard-reboots from test fan-out. This cycle is behavior-preserving test refactors + additive safety-code hardening. **NO production app code changes.**

⛔ **SAFETY MANDATE (every team member):** NEVER run the full / uncapped / `-n2` / `-n auto` pytest suite on this Windows host — it fans out to ~238 GB and reboots the box. Verify ONLY with bounded per-file `-n0` runs through the conftest cap (this worktree forks from origin/main `5597eb5`, which HAS the cap). The full-suite regression gate is CLOUD CI, never a local run.

## Acceptance Criteria
- **AC-1 (R-1 footprint):** All `node --check` JS-syntax-guard spawns (~14, scattered across ~17 test files) are consolidated into ONE parametrized module `tests/js_syntax/test_js_syntax.py` that runs `node --check` over every JS file under `static/`. The `shutil.which("node") is None` skip is preserved. The originating per-file `node --check` test methods are removed. Coverage is identical (each JS file still gets a syntax check asserting exit code 0).
- **AC-2 (R-2 footprint):** The 2 `pytest --collect-only` subprocess spawns in `tests/execution/test_orphan_port_modules_removed.py:524,540` (each imports `app` → ~1-2 GB child) are replaced with in-process `importlib.import_module(...)` + path-existence checks proving the target modules import cleanly. No subprocess. The test still FAILS if a target file is missing or fails to import.
- **AC-3 (R-3 footprint):** The `_init_db_at` subprocess helper in `tests/advisors/test_prism_dotenv_hardening.py:88-103` (spawns `python -c "import database; database.init_db()"`) is replaced with a direct in-process DB init (DB_PATH controlled via monkeypatch). The ESSENTIAL dotenv-discovery subprocess tests (which require a fresh `os.environ` and a real subprocess) are UNCHANGED.
- **AC-4 (C5 cap):** `_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (0x2000) is added to the LimitFlags OR-expression in `tests/_mem_cap.py install_total_memory_cap` so orphaned children die when the controller exits. A test asserts the flag is present.
- **AC-5 (C3 cap — verify-or-fail-loud):** `install_total_memory_cap` VERIFIES the cap actually applied: after `AssignProcessToJobObject`, confirm via `IsProcessInJob(GetCurrentProcess(), job)` that the process is in OUR job. Set `_CAP_INSTALLED=True` ONLY when membership is confirmed. If assignment failed AND the process is not confirmed in our job, emit a LOUD warning and leave `_CAP_INSTALLED=False` — NEVER silently claim the cap is installed. (Preserve the legitimate already-nested success case only when membership is confirmed.) Tests cover: success path → confirmed + True; unconfirmed path → loud + False.
- **AC-6 (R-5 guard):** `tests/conftest.py pytest_configure` rejects with a loud `SystemExit` when the xdist worker count (`config.option.numprocesses`) is `"auto"` or `> 4`, message citing the 67.8 GB host ceiling. Default `-n 2` and any `-n <=4` pass. OS-agnostic (runs on Linux too; only the cap itself is Windows-only).
- **AC-7 (no regression):** `tests/mem_cap/test_total_job_memory_cap.py` still passes — the cap still fires on over-allocation and total-job (not per-process) semantics are preserved.

## Architecture
- `tests/_mem_cap.py`: add `KILL_ON_JOB_CLOSE` to LimitFlags (AC-4); add `IsProcessInJob` membership verification after `AssignProcessToJobObject`, restructure the err-5/87 swallow path so `_CAP_INSTALLED=True` is set ONLY on confirmed membership, else loud-warn + leave False (AC-5).
- `tests/conftest.py`: add the xdist-worker-count guard in `pytest_configure` reading `config.option.numprocesses` (AC-6).
- New `tests/js_syntax/test_js_syntax.py`: parametrized `node --check` over a discovered `static/*.js` list (AC-1).
- Refactor the 3 footprint sites: remove per-file node-check methods (AC-1), in-process importlib (AC-2), inline DB init (AC-3).

## Edge Cases
- `node` absent → skip preserved (AC-1).
- `IsProcessInJob`: after our successful `AssignProcessToJobObject`, the process is in our job → confirmed. If assignment was denied (already in a non-nestable ancestor job, rare on Win11) we cannot read the ancestor's limit, so "confirmed in OUR job" is the only signal we can verify; failing that → loud + False (AC-5).
- Linux/CI: cap code no-ops; the AC-6 guard still runs.
- R-2 equivalence: `importlib.import_module` proves import-cleanness, which is what `--collect-only` verified for these simple modules; assert the same outcome.

## Security Considerations
- Pure test-infra. No secrets, no network, no production code. No new attack surface.

## Testing Strategy
- RED tests first for AC-4 (flag present), AC-5 (verify-or-fail-loud logic), AC-6 (guard rejects `-n auto`/`>4`, accepts `<=4`).
- AC-1/AC-2/AC-3 are behavior-preserving refactors verified by identical assertions + bounded per-file `-n0` runs through the cap.
- AC-7 regression: re-run `tests/mem_cap/ -n0` (bounded, safe — single dir).
- ⛔ NEVER a full/`-n2` local run. Footprint impact verified on cloud CI.

## Scope Boundaries
- NO production app code (app.py, alpha_bot_execution.py, math_engine.py, autotuner.py, database.py, advisors/ — all untouched).
- The 2 meta-test full-suite spawns stay deselected (unchanged).
- Cap DEFAULT stays 24 GB (do not lower in this cycle).
- Wave 2/3/4 cleanup (file hygiene, code de-duplication, dead-code) is OUT of scope.
