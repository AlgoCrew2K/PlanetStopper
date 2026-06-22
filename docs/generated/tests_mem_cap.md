# tests/_mem_cap

> Windows Job Object total-tree memory cap for the pytest suite — installs a hard OS-level limit on the entire process tree so over-cap allocations fail with `MemoryError` instead of crashing the host.

**Source:** `tests/_mem_cap.py`
**Last updated:** 2026-06-22

## Overview

`tests/_mem_cap.py` implements the host-safety guard introduced in DE-TEST-MEMCAP-001 after a full `python -m pytest` run committed ~238 GB of virtual memory on 2026-06-21, triggering a hard host reboot (Windows Kernel-Power 41, ~238 GB committed > 67.8 GB ceiling). Hardened further in DE-TEST-MEMCAP-002 (2026-06-22): orphaned-child cleanup via `KILL_ON_JOB_CLOSE`, IsProcessInJob verify-or-fail-loud path, and the `_assert_safe_worker_count` xdist guard in `conftest.py`.

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
- On Windows: `CreateJobObjectW` → `SetInformationJobObject(JobObjectExtendedLimitInformation)` with `LimitFlags = JOB_OBJECT_LIMIT_JOB_MEMORY | _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and `JobMemoryLimit = cap_bytes` → `AssignProcessToJobObject(GetCurrentProcess())`.
- After `AssignProcessToJobObject`, calls `_is_process_in_job_seam(cur, job)` to verify membership. Sets `_CAP_INSTALLED = True` only if membership is confirmed. If not confirmed, emits a loud `UserWarning` and returns without setting the sentinel (verify-or-fail-loud — DE-TEST-MEMCAP-002 AC-5).
- Sets `_JOB_HANDLE` (module-level) to the new job handle to keep it alive process-lifetime.
- **Idempotent for nested jobs:** if the process is already a member of an ancestor job (Win8+ nested job assignment), `_is_process_in_job_seam` returns True and `_CAP_INSTALLED` is set True — the ancestor's limits apply.
- Prints a confirmation line to stdout on success: `[mem-cap] total-job memory cap installed: X.XX GB (JOB_OBJECT_LIMIT_JOB_MEMORY=0x00000200)`.

**Effect when cap is exceeded:** Any allocation (in the controller, any xdist worker, or any subprocess child) that would push the total committed bytes past `cap_bytes` raises `MemoryError` at the exact allocation site. The host commit never approaches the ceiling.

---

### `_is_process_in_job_seam(cur_handle, job_handle) -> bool`

Wraps the Win32 `IsProcessInJob` call so tests can monkeypatch membership verification without fighting ctypes internals. Module-level so `monkeypatch` can target it by name.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `cur_handle` | Win32 `HANDLE` | Handle to the current process (from `GetCurrentProcess()`) |
| `job_handle` | Win32 `HANDLE` | Handle to the job object just created |

**Returns:** `bool` — `True` if `IsProcessInJob` confirms membership, `False` otherwise.

Only called on Windows (guarded inside `install_total_memory_cap`). Lazy-imports `ctypes` / `ctypes.wintypes` so it is safe to define at module level on all platforms.

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
| `_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` | `0x00002000` | Win32 flag: kill all job members when the last job handle closes — prevents orphaned child processes from bypassing the cap after the controller exits (DE-TEST-MEMCAP-002 AC-4) |

## Module-level State

| Name | Type | Description |
|------|------|-------------|
| `_CAP_INSTALLED` | `bool` | `True` after a successful `install_total_memory_cap` call where `_is_process_in_job_seam` confirmed membership; `False` at module load or if verification failed. Used by guard tests to confirm the cap was installed. |
| `_JOB_HANDLE` | Win32 `HANDLE` or `None` | Kept alive so the Job Object is not destroyed when the installer function returns. Closing the last handle destroys the job and releases all limits. |

## Guard Tests

`tests/mem_cap/test_total_job_memory_cap.py` — 9 tests across 4 groups (DE-TEST-MEMCAP-001):

**Group 1 — Module contract (Windows-gated):**
- `test_install_total_job_cap_callable_on_windows` — `install_total_memory_cap` is exported and callable.
- `test_install_total_job_cap_uses_job_memory_flag_not_per_process_flag` — both flag constants exist and `JOB_OBJECT_LIMIT_JOB_MEMORY != JOB_OBJECT_LIMIT_PROCESS_MEMORY`; pins against regression to the per-process flag.
- `test_install_total_job_cap_sets_sentinel` — `_CAP_INSTALLED` is `True` and `_JOB_HANDLE` is non-`None` after install.
- `test_install_total_job_cap_is_idempotent` — calling install twice must not raise; sentinel stays `True`.

**Group 2 — Safety semantics (Windows-gated, bounded by the cap itself):**
- `test_over_cap_allocation_raises_memory_error_not_host_crash` — a child subprocess installs a 64 MB cap and attempts a 96 MB allocation (page-touched); must print `MemoryError` and exit 0.
- `test_total_job_semantics_bounds_fanout_not_just_per_process` — structural proof via `QueryInformationJobObject`: asserts `LimitFlags` has `JOB_OBJECT_LIMIT_JOB_MEMORY` (0x200) set, `JobMemoryLimit == cap_bytes`, and `ProcessMemoryLimit == 0`.

**Group 3 — Linux/CI no-op (cross-platform, no skip guard):**
- `test_install_total_job_cap_is_noop_on_non_windows` — monkeypatches `os.name = 'posix'`; `install_total_memory_cap` must not raise and must not import any Win32-only symbol.

**Group 4 — Env knob (cross-platform):**
- `test_alphabot_test_mem_cap_gb_env_knob_zero_disables_with_warning` — `ALPHABOT_TEST_MEM_CAP_GB=0` must emit a `UserWarning`.
- `test_alphabot_test_mem_cap_gb_default_is_present_and_sane` — `DEFAULT_CAP_GB` is a named module-level constant, numeric, `>= 8` and `<= 64`.

`tests/mem_cap/test_kill_on_job_close_flag.py` — 3 tests (DE-TEST-MEMCAP-002 AC-4):

- `test_kill_on_job_close_flag_defined_as_named_constant` — `_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` is exported from `_mem_cap`.
- `test_kill_on_job_close_flag_value_is_0x2000` — value equals `0x2000` exactly.
- `test_kill_on_job_close_flag_present_in_limit_flags_after_install` — after `install_total_memory_cap`, `QueryInformationJobObject` confirms bit 0x2000 is set in `LimitFlags`.

`tests/mem_cap/test_cap_install_verify_or_fail_loud.py` — 5 tests (DE-TEST-MEMCAP-002 AC-5):

- `test_cap_installed_true_and_membership_confirmed_on_success_path` — on the happy path, sentinel is True and `_is_process_in_job_seam` returns True.
- `test_cap_install_uses_is_process_in_job_for_verification` — monkeypatch confirms `_is_process_in_job_seam` is called after `AssignProcessToJobObject`.
- `test_cap_installed_false_and_warning_when_membership_not_confirmed` — monkeypatching `_is_process_in_job_seam` to return False causes `_CAP_INSTALLED` to stay False and emits a `UserWarning`.
- `test_cap_installed_true_when_already_nested_and_membership_confirmed` — simulates Win8+ nested-job scenario: `AssignProcessToJobObject` fails but `_is_process_in_job_seam` returns True → sentinel is set True.
- `test_cap_install_is_noop_on_non_windows` — on non-Windows, function returns without calling any Win32 API.

## Wiring in conftest.py

`tests/conftest.py:pytest_configure` runs the following sequence (DE-TEST-MEMCAP-001 + DE-TEST-MEMCAP-002):

```python
# AC-6: Reject unsafe xdist worker counts EARLY — before install_from_env().
_assert_safe_worker_count(getattr(config.option, "numprocesses", None))

# Install a Windows Job Object total-tree memory cap before xdist workers spawn.
# On Linux/CI this is a no-op.  Cap value comes from ALPHABOT_TEST_MEM_CAP_GB
# (default 24 GB); set to 0 to disable with a loud warning.
# See tests/_mem_cap.py and DE-TEST-MEMCAP-001/DE-TEST-MEMCAP-002 in DECISIONS.md.
if os.name == "nt":
    from tests import _mem_cap
    _mem_cap.install_from_env()
```

`_assert_safe_worker_count` is a top-level helper in `conftest.py` (not in `_mem_cap.py`) that raises `SystemExit` when `numprocesses` is `"auto"` or an integer > 4. It runs before `install_from_env()` and before the `ALPHABOT_MAX_JOBS` / `OPTUNA_N_JOBS` setdefaults, ensuring no uncapped fan-out run can proceed on any platform including CI.

The `os.name == "nt"` guard is belt-and-suspenders — `install_total_memory_cap` already no-ops on non-Windows — but it prevents the `from tests import _mem_cap` import from occurring at all on Linux, keeping CI startup clean.

## Internal Dependencies

- `os` — env var reads and `os.name` platform check (stdlib)
- `warnings` — `UserWarning` on disabled cap or verify failure (stdlib)
- `ctypes`, `ctypes.wintypes` — Win32 Job Object API; imported lazily inside `install_total_memory_cap` and `_is_process_in_job_seam` only on Windows
