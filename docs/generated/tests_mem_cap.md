# tests/_mem_cap

> Windows Job Object total-tree memory cap for the pytest suite — installs a hard OS-level limit on the entire process tree so over-cap allocations fail with `MemoryError` instead of crashing the host.

**Source:** `tests/_mem_cap.py`
**Last updated:** 2026-06-21

## Overview

`tests/_mem_cap.py` implements the host-safety guard introduced in DE-TEST-MEMCAP-001 after a full `python -m pytest` run committed ~238 GB of virtual memory on 2026-06-21, triggering a hard host reboot (Windows Kernel-Power 41, ~238 GB committed > 67.8 GB ceiling).

The module installs a Windows Job Object using `JOB_OBJECT_LIMIT_JOB_MEMORY` — the **total committed bytes across ALL processes in the job** — before xdist workers spawn. This is distinct from `JOB_OBJECT_LIMIT_PROCESS_MEMORY`, which caps each process individually and does NOT bound a fan-out of many medium processes. The 2026-06-21 crash was caused by exactly that fan-out: `-n 2` xdist workers + ~15 subprocess-spawned child interpreters, each loading the heavy scientific stack (numpy/scipy/pandas/optuna/matplotlib), whose combined committed memory exceeded the host ceiling. Only a total-job cap catches the combination.

On non-Windows (Linux, macOS, CI), all functions are clean no-ops. `ctypes` Win32 symbols are imported lazily inside `install_total_memory_cap` — never at module top level — so this module imports cleanly on Linux/CI with no platform-specific hard dependency.

Called from `tests/conftest.py:pytest_configure` via `install_from_env()`. The handle to the Job Object is kept alive at module scope (`_JOB_HANDLE`) so the job and its limits persist for the full process lifetime.

## API Reference

### `install_total_memory_cap(cap_bytes: int) -> None`

Installs a Windows Job Object with `JOB_OBJECT_LIMIT_JOB_MEMORY` (total-tree cap) capping the committed memory of the current process and all its children.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `cap_bytes` | `int` | Maximum total committed bytes across the entire job tree |

**Returns:** `None`

**Behavior:**
- On non-Windows: returns immediately, no-op.
- On Windows: `CreateJobObjectW` → `SetInformationJobObject(JobObjectExtendedLimitInformation)` with `LimitFlags = JOB_OBJECT_LIMIT_JOB_MEMORY | _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION` and `JobMemoryLimit = cap_bytes` → `AssignProcessToJobObject(GetCurrentProcess())`.
- Sets `_JOB_HANDLE` (module-level) to the new job handle to keep it alive process-lifetime.
- Sets `_CAP_INSTALLED = True` on success.
- **Idempotent:** if `AssignProcessToJobObject` returns `ERROR_ACCESS_DENIED` (5) or `ERROR_INVALID_PARAMETER` (87) — the process is already in an ancestor job — the error is swallowed and `_CAP_INSTALLED` is set `True`. This handles Win8+ nested jobs and xdist worker re-runs of `pytest_configure`.
- Prints a confirmation line to stdout on success: `[mem-cap] total-job memory cap installed: X.XX GB (JOB_OBJECT_LIMIT_JOB_MEMORY=0x00000200)`.

**Effect when cap is exceeded:** Any allocation (in the controller, any xdist worker, or any subprocess child) that would push the total committed bytes past `cap_bytes` raises `MemoryError` at the exact allocation site. The host commit never approaches the ceiling.

---

### `install_from_env() -> None`

Reads `ALPHABOT_TEST_MEM_CAP_GB` from the environment and calls `install_total_memory_cap`. This is the entrypoint called by `tests/conftest.py:pytest_configure`.

**Environment variable:** `ALPHABOT_TEST_MEM_CAP_GB`

| Value | Behavior |
|-------|----------|
| Missing / empty | Uses `DEFAULT_CAP_GB` (24.0 GB) |
| Positive float/int string | Converts to bytes and installs the cap |
| `"0"` or any value ≤ 0 | Emits a loud `UserWarning` and returns WITHOUT installing (explicit opt-out — never silent) |
| Garbled (non-numeric) | Emits a loud `UserWarning` and falls back to `DEFAULT_CAP_GB` |

On non-Windows: delegates to `install_total_memory_cap` which no-ops immediately.

## Constants

| Name | Value | Description |
|------|-------|-------------|
| `DEFAULT_CAP_GB` | `24.0` | Default cap in GB — generous but well under the 67.8 GB dev-host ceiling |
| `JOB_OBJECT_LIMIT_JOB_MEMORY` | `0x00000200` | Win32 flag: total committed bytes across the whole job tree (correct flag) |
| `JOB_OBJECT_LIMIT_PROCESS_MEMORY` | `0x00000100` | Win32 flag: per-process cap (weaker — does NOT bound fan-out; exported as a named constant so tests can pin that the implementation uses the correct flag) |

## Module-level State

| Name | Type | Description |
|------|------|-------------|
| `_CAP_INSTALLED` | `bool` | `True` after a successful `install_total_memory_cap` call; `False` at module load. Used by guard tests to confirm the cap was installed. |
| `_JOB_HANDLE` | Win32 `HANDLE` or `None` | Kept alive so the Job Object is not destroyed when the installer function returns. Closing the last handle destroys the job and releases all limits. |

## Guard Tests

`tests/mem_cap/test_total_job_memory_cap.py` — 9 tests across 4 groups:

**Group 1 — Module contract (Windows-gated):**
- `test_install_total_job_cap_callable_on_windows` — `install_total_memory_cap` is exported and callable (AC-1).
- `test_install_total_job_cap_uses_job_memory_flag_not_per_process_flag` — both flag constants exist and `JOB_OBJECT_LIMIT_JOB_MEMORY != JOB_OBJECT_LIMIT_PROCESS_MEMORY`; pins against regression to the per-process flag (AC-3).
- `test_install_total_job_cap_sets_sentinel` — `_CAP_INSTALLED` is `True` and `_JOB_HANDLE` is non-`None` after install (AC-1).
- `test_install_total_job_cap_is_idempotent` — calling install twice must not raise; sentinel stays `True` (AC-1, edge case).

**Group 2 — Safety semantics (Windows-gated, bounded by the cap itself):**
- `test_over_cap_allocation_raises_memory_error_not_host_crash` — a child subprocess installs a 64 MB cap and attempts a 96 MB allocation (page-touched); must print `MemoryError` and exit 0. The test process itself allocates nothing toward the host ceiling (AC-2).
- `test_total_job_semantics_bounds_fanout_not_just_per_process` — **structural proof via `QueryInformationJobObject`**: asserts `LimitFlags` has `JOB_OBJECT_LIMIT_JOB_MEMORY` (0x200) set, `JobMemoryLimit == cap_bytes`, and `ProcessMemoryLimit == 0`. This approach was chosen over a subprocess fan-out experiment because Windows nested job child-inheritance is version-dependent and complex; a structural query of the installed job object is a reliable, host-safe proof that the correct field is set (AC-3).

**Group 3 — Linux/CI no-op (cross-platform, no skip guard):**
- `test_install_total_job_cap_is_noop_on_non_windows` — monkeypatches `os.name = 'posix'`; `install_total_memory_cap` must not raise and must not import any Win32-only symbol (AC-4). Runs on CI as a regression guard.

**Group 4 — Env knob (cross-platform):**
- `test_alphabot_test_mem_cap_gb_env_knob_zero_disables_with_warning` — `ALPHABOT_TEST_MEM_CAP_GB=0` must emit a `UserWarning` via `warnings.warn` (opt-out never silent) (AC-1/AC-7).
- `test_alphabot_test_mem_cap_gb_default_is_present_and_sane` — `DEFAULT_CAP_GB` is a named module-level constant, numeric, `>= 8` and `<= 64` (AC-1/AC-7).

## Wiring in conftest.py

```python
# tests/conftest.py:pytest_configure (lines 77–83)
# Install a Windows Job Object total-tree memory cap before xdist workers spawn.
# On Linux/CI this is a no-op.  Cap value comes from ALPHABOT_TEST_MEM_CAP_GB
# (default 24 GB); set to 0 to disable with a loud warning.
# See tests/_mem_cap.py and DE-TEST-MEMCAP-001 in DECISIONS.md.
if os.name == "nt":
    from tests import _mem_cap
    _mem_cap.install_from_env()
```

The `os.name == "nt"` guard is belt-and-suspenders — `install_total_memory_cap` already no-ops on non-Windows — but it prevents the `from tests import _mem_cap` import from occurring at all on Linux, keeping CI startup clean.

## Internal Dependencies

- `os` — env var reads and `os.name` platform check (stdlib)
- `warnings` — `UserWarning` on disabled cap (stdlib)
- `ctypes`, `ctypes.wintypes` — Win32 Job Object API; imported lazily inside `install_total_memory_cap` only on Windows
