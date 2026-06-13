"""Hard committed-memory cap harness via a Windows Job Object.

Caps the CURRENT process (and any children) to a per-process committed-memory
limit using JOBOBJECT_EXTENDED_LIMIT_INFORMATION.ProcessMemoryLimit +
JOB_OBJECT_LIMIT_PROCESS_MEMORY. When the cap is hit, the offending
allocation FAILS with MemoryError at the exact line — unlike an RSS-poll
watchdog, which the runaway reservation outruns.

Usage:
    python job_cap_harness.py <cap_gb> -- <pytest args...>

It installs the cap, then runs pytest IN-PROCESS so the MemoryError traceback
points at the real allocation site inside the test.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

# --- Win32 constants ------------------------------------------------------
JobObjectExtendedLimitInformation = 9
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001


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
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def install_cap(cap_bytes: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = (
        JOB_OBJECT_LIMIT_PROCESS_MEMORY
        | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
    )
    info.ProcessMemoryLimit = cap_bytes

    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]
    ok = kernel32.SetInformationJobObject(
        job,
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())

    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    cur = kernel32.GetCurrentProcess()

    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    ok = kernel32.AssignProcessToJobObject(job, cur)
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())

    # Keep the handle alive for the lifetime of the process.
    globals()["_JOB_HANDLE"] = job
    print(f"[job-cap] committed-memory cap installed: {cap_bytes/1024/1024/1024:.2f} GB",
          flush=True)


def _self_test(cap_bytes: int) -> int:
    """Prove the cap fires: allocate-and-touch past the cap, expect MemoryError."""
    print("[self-test] allocating 1 GB chunks until MemoryError...", flush=True)
    chunks = []
    try:
        for i in range(64):
            # bytearray of 1 GiB, then touch it to force commit.
            b = bytearray(1024 * 1024 * 1024)
            b[::4096] = b"\x01" * (len(b) // 4096 + (1 if len(b) % 4096 else 0))
            chunks.append(b)
            print(f"[self-test] committed ~{i+1} GiB", flush=True)
    except MemoryError:
        print(f"[self-test] PASS: MemoryError fired after ~{len(chunks)} GiB "
              f"(cap={cap_bytes/1024/1024/1024:.2f} GB)", flush=True)
        return 0
    print("[self-test] FAIL: no MemoryError — cap did NOT fire", flush=True)
    return 1


def main() -> int:
    if "--" not in sys.argv:
        print("usage: python job_cap_harness.py <cap_gb> -- <pytest args...|--self-test>")
        return 2
    split = sys.argv.index("--")
    cap_gb = float(sys.argv[1])
    rest = sys.argv[split + 1:]
    cap_bytes = int(cap_gb * 1024 * 1024 * 1024)

    install_cap(cap_bytes)

    if rest == ["--self-test"]:
        return _self_test(cap_bytes)

    import pytest
    print(f"[job-cap] running: pytest {' '.join(rest)}", flush=True)
    return pytest.main(rest)


if __name__ == "__main__":
    sys.exit(main())
