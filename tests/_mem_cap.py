"""Windows Job Object total-tree memory cap for the pytest suite.

Installs a hard OS-level memory limit on the ENTIRE process tree
(controller + xdist workers + any subprocess children) so that any
over-cap allocation fails with MemoryError at the exact site instead
of crashing the host.

CRITICAL: uses JOB_OBJECT_LIMIT_JOB_MEMORY (0x00000200) — total committed
bytes across ALL processes in the job — NOT JOB_OBJECT_LIMIT_PROCESS_MEMORY
(0x00000100), which caps each process individually and DOES NOT bound fan-out.
The 2026-06-21 host hard-reboot (Kernel-Power 41, ~238 GB committed) was caused
by a fan-out of many medium processes each under a per-process cap.  Only a
total-job cap catches the combination.

Reference: MSDN JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
.design-handoff/memfix/job_cap_harness.py (adapted — per-process → total-job).
See DE-TEST-MEMCAP-001 in DECISIONS.md.
"""

from __future__ import annotations

import os
import warnings

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

# Default cap in gigabytes.  Generous enough that a normal bounded suite never
# trips it; well under the 67.8 GB dev-host ceiling (63.8 GB RAM + 4 GB pf).
DEFAULT_CAP_GB: float = 24.0

# Win32 Job Object limit flags.
# These are module-level constants so tests can pin the correct flag value and
# catch any regression to the weaker per-process flag.
JOB_OBJECT_LIMIT_JOB_MEMORY: int = 0x00000200  # total committed across the whole job tree
JOB_OBJECT_LIMIT_PROCESS_MEMORY: int = 0x00000100  # per-process (weaker; DO NOT use for total cap)

# Internal Win32 constants used by install_total_memory_cap.
_JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION: int = 0x00000400
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: int = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS: int = 9  # JobObjectExtendedLimitInformation

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

# Set to True after a successful install_total_memory_cap() call.
# Tests check this sentinel to confirm the cap was installed.
_CAP_INSTALLED: bool = False

# Win32 HANDLE for the Job Object.  Kept alive at module scope so the Job
# Object is not destroyed when the function returns (closing the last handle
# destroys the job and releases all limits).
_JOB_HANDLE = None


# ---------------------------------------------------------------------------
# AC-5 testability seam
# ---------------------------------------------------------------------------


def _is_process_in_job_seam(cur_handle, job_handle) -> bool:
    """Verify the current process is a member of the given job (AC-5 seam).

    Wraps the Win32 IsProcessInJob call so tests can monkeypatch this function
    to simulate confirmed or unconfirmed membership without fighting ctypes internals.

    Signature: (cur_handle, job_handle) -> bool
      Returns True if IsProcessInJob confirms membership, False otherwise.

    This function is only called on Windows (install_total_memory_cap guards it).
    It is defined at module level so monkeypatch can target it by name.
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes as wintypes  # noqa: PLC0415

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.IsProcessInJob.restype = wintypes.BOOL
    kernel32.IsProcessInJob.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.BOOL),
    ]
    result = wintypes.BOOL(0)
    kernel32.IsProcessInJob(cur_handle, job_handle, ctypes.pointer(result))
    return bool(result)


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------


def install_total_memory_cap(cap_bytes: int) -> None:
    """Install a Windows Job Object with JOB_OBJECT_LIMIT_JOB_MEMORY (total tree cap).

    On non-Windows: returns immediately (no-op).  No ctypes Win32 symbols are
    imported at module top level so this module imports cleanly on Linux/CI.

    On Windows:
      - Creates a new Job Object via CreateJobObjectW.
      - Sets LimitFlags = JOB_OBJECT_LIMIT_JOB_MEMORY | die-on-unhandled-exception.
      - Sets JobMemoryLimit = cap_bytes (NOT ProcessMemoryLimit — that is per-process).
      - Assigns the current process to the job via AssignProcessToJobObject.
      - Keeps the job handle alive in _JOB_HANDLE so limits persist process-lifetime.
      - Sets _CAP_INSTALLED = True.

    Idempotent: if the process is already in a job (e.g. called twice, or inherited
    from a parent), AssignProcessToJobObject may return ERROR_ACCESS_DENIED (87) on
    older Windows or succeed (nested jobs on Win8+).  Either outcome is treated as
    "already capped" — the error is swallowed and _CAP_INSTALLED is set True.
    """
    global _CAP_INSTALLED, _JOB_HANDLE

    if os.name != "nt":
        # Linux / macOS / CI — cgroup limits handle containment there.
        return

    import ctypes
    from ctypes import wintypes

    # --- ctypes structure definitions (lifted from job_cap_harness.py) ------

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.POINTER(wintypes.ULONG)),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),  # per-process cap field (not used here)
            ("JobMemoryLimit", ctypes.c_size_t),  # total-job cap field (this is what we set)
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # Create a new anonymous Job Object.
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())

    # Set the total-job memory limit.
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = (
        JOB_OBJECT_LIMIT_JOB_MEMORY  # total committed across the whole tree
        | _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
        | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE  # AC-4: orphaned children die when controller exits
    )
    # CRITICAL: JobMemoryLimit (total) — NOT ProcessMemoryLimit (per-process).
    info.JobMemoryLimit = cap_bytes

    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    ok = kernel32.SetInformationJobObject(
        job,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())

    # Assign the current process (and thus all future children) to the job.
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    cur = kernel32.GetCurrentProcess()

    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    ok = kernel32.AssignProcessToJobObject(job, cur)
    if not ok:
        # Any failure from AssignProcessToJobObject — including ERROR_ACCESS_DENIED (5)
        # or ERROR_INVALID_PARAMETER (87) — falls through to IsProcessInJob verification
        # below (AC-5).  We do NOT raise here because the process may already be bounded
        # by an ancestor job, which IsProcessInJob will confirm.
        pass

    # AC-5: Verify actual job membership before claiming the cap is installed.
    # Delegates to _is_process_in_job_seam (module-level, patchable in tests).
    # On the happy path (AssignProcessToJobObject succeeded) this returns True.
    # On the assignment-failed path (already in an ancestor job) it may still
    # return True (ancestor's limits apply — treat as bounded).  If False, the
    # cap could not be confirmed — emit a loud warning and leave _CAP_INSTALLED False.
    confirmed = _is_process_in_job_seam(cur, job)

    if not confirmed:
        # Cap could not be verified — emit a loud warning, leave _CAP_INSTALLED False.
        warnings.warn(
            "[mem-cap] WARNING: install_total_memory_cap could not confirm job membership "
            "via IsProcessInJob — the memory cap is NOT installed.  "
            "The host crash-protection cap is inactive.  "
            "Set ALPHABOT_TEST_MEM_CAP_GB=0 to suppress this warning and opt out explicitly.",
            UserWarning,
            stacklevel=2,
        )
        return

    # Keep the handle alive — closing it would destroy the Job Object and release limits.
    _JOB_HANDLE = job
    _CAP_INSTALLED = True

    print(
        f"[mem-cap] total-job memory cap installed: "
        f"{cap_bytes / 1024 / 1024 / 1024:.2f} GB "
        f"(JOB_OBJECT_LIMIT_JOB_MEMORY=0x{JOB_OBJECT_LIMIT_JOB_MEMORY:08x})",
        flush=True,
    )


def install_from_env() -> None:
    """Read ALPHABOT_TEST_MEM_CAP_GB from env and call install_total_memory_cap.

    - Missing/empty env var: uses DEFAULT_CAP_GB.
    - "0" or parses to <= 0: emits a loud UserWarning and returns WITHOUT
      installing the cap (explicit operator opt-out — never silent).
    - Garbled value (ValueError on float()): emits a loud UserWarning and
      falls back to DEFAULT_CAP_GB.
    - On non-Windows: delegates to install_total_memory_cap which no-ops.
    """
    raw = os.environ.get("ALPHABOT_TEST_MEM_CAP_GB", "").strip()

    if raw == "":
        cap_gb = DEFAULT_CAP_GB
    else:
        try:
            cap_gb = float(raw)
        except ValueError:
            warnings.warn(
                f"ALPHABOT_TEST_MEM_CAP_GB={raw!r} is not a valid number; "
                f"falling back to DEFAULT_CAP_GB={DEFAULT_CAP_GB} GB.",
                UserWarning,
                stacklevel=2,
            )
            cap_gb = DEFAULT_CAP_GB

    if cap_gb <= 0:
        warnings.warn(
            f"ALPHABOT_TEST_MEM_CAP_GB={raw!r} disables the memory cap (value <= 0). "
            f"The test-host memory cap is OFF — host crash protection is not active. "
            f"Set ALPHABOT_TEST_MEM_CAP_GB to a positive number to re-enable.",
            UserWarning,
            stacklevel=2,
        )
        return

    cap_bytes = int(cap_gb * 1024 * 1024 * 1024)
    install_total_memory_cap(cap_bytes)
