---
name: run-tests
description: Run the pytest suite with sensible defaults. Reports summary, surfaces failures, and offers diff-only mode for fast feedback.
allowed-tools: Read, Glob, Bash
---

## Dynamic Context

```
!`python -m pytest --version 2>/dev/null || echo "pytest not installed"`
!`ls tests/ 2>/dev/null || echo "no tests dir"`
```

## Steps

1. **Check pytest** — run `python -m pytest --version`. If missing, print:
   > pytest not found. Install with: `pip install pytest pytest-cov pytest-xdist`
   Then stop. Do NOT install anything.

2. **Check tests/** — if `tests/` does not exist, print:
   > No tests/ directory yet. Create one and add test_*.py files under tests/.
   Then stop.

3. **Build the command** — start with `python -m pytest`.
   - No args → full suite in parallel (xdist `-n 2 --dist loadfile` is in addopts; worker count is CAPPED AT 2 — never raise it or use `-n auto`, which caused PC-crashing memory blowups; see pyproject.toml notes). Run only ONE pytest invocation at a time across the whole fleet.
   - `<path>` arg → append the path.
   - `-k <expr>` → append `-k <expr>`.
   - `--fast` → append `-x --ff`.
   - Always append `-v --tb=short` for readable output.
   - Always exclude live tests: append `--ignore=tests` patterns or use `--deselect` for any `test_live_*.py` unless `--include-live` was passed.
   - For single-file or small targeted runs, pass `-n0` to disable parallelism (faster startup, no worker overhead).

4. **Run** — execute the built command via Bash.

5. **Parse results** — extract the summary line (e.g. `5 passed`, `2 failed`).
   - On failure: surface the first 3 failed test IDs and their assertion messages.
   - Suggest `/run-tests --fast` if any tests failed and `--fast` was not already used.

### Serial / meta-test invocation

`test_full_suite_reports_zero_skips_and_zero_xfails` is **deselected from the default run** because it
spawns the entire suite as a subprocess (50-minute timeout). Run it explicitly:
```
python -m pytest tests/meta/test_zero_skip_xfail_close.py -n0 -v --tb=short
```

## Total-job Memory Cap (automatic — no wrapper needed)

`tests/conftest.py:pytest_configure` installs a Windows Job-Object total-tree memory cap **before xdist workers spawn**. This is automatic on every `python -m pytest` invocation; no special wrapper or env var is required for the cap to be active.

**Env knob:** `ALPHABOT_TEST_MEM_CAP_GB` (default: 24 GB). Set it in the environment to override:
- `set ALPHABOT_TEST_MEM_CAP_GB=32` — raise the cap (e.g. on a host with more RAM)
- `set ALPHABOT_TEST_MEM_CAP_GB=0` — disable the cap (explicit operator opt-out; loud warning logged)

**What the cap covers:** The Windows Job Object bounds the TOTAL committed memory of the entire process tree — controller + all `-n` xdist workers + any subprocess-spawned child interpreters. An over-cap allocation raises `MemoryError` at the exact allocation site rather than crashing the host.

**Linux/CI:** The cap installer is a clean no-op on non-Windows. CI relies on the runner's own cgroup limits. The default cap is intentionally high enough that a legitimately-bounded suite does not spuriously `MemoryError`.

**Context (DE-TEST-MEMCAP-001):** A full `python -m pytest` run committed ~238 GB of virtual memory on 2026-06-21, triggering a hard host reboot (Windows Kernel-Power 41). The Jun-13 memfix (env-bounded joblib/optuna + forced-single-process meta tests) was necessary but insufficient — the total fan-out still exceeded the host ceiling. The total-job cap is the durable guard; it bounds ANY fan-out regardless of source.

## What You Must NOT Do

- Never install packages (`pip install`, `poetry add`, etc.)
- Never modify any test file
- Never run `test_live_*.py` files unless the user explicitly passes `--include-live`
- Never run tests against live external APIs without `--include-live`
- Never run two pytest invocations concurrently (fan-out compounds across runs)

## Examples

**`/run-tests`** — full suite, no live tests:
```
python -m pytest -v --tb=short <exclude live files>
```
Output: summary line + up to 3 failure details if any failed.

**`/run-tests tests/test_math_engine.py`** — single file:
```
python -m pytest tests/test_math_engine.py -v --tb=short
```

**`/run-tests -k volatility --fast`** — keyword filter, stop on first fail:
```
python -m pytest -k volatility -x --ff -v --tb=short
```
