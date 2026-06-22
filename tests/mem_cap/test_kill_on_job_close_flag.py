"""RED tests for AC-4: _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE (0x2000) in LimitFlags.

Pins the requirement that orphaned children die when the controller process
exits — the "kill-on-job-close" semantic.  Without this flag a crashed or
OOM-killed pytest controller leaves worker children running indefinitely,
continuing to accumulate committed memory against the host ceiling.

All three tests are Windows-gated because the Win32 Job Object API does not
exist on Linux/CI.
"""

from __future__ import annotations

import os

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Win32 Job Object — Windows only")
def test_kill_on_job_close_flag_defined_as_named_constant():
    """_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE must be a named module-level constant in _mem_cap (AC-4).

    Requiring a named constant (not a magic number inlined into the OR-expression)
    means the flag value is auditable and test-pinnable independently of the
    install logic.  An implementer who only inlines 0x2000 without the name
    fails this test, which forces them to also export the name.
    """
    from tests import _mem_cap  # noqa: PLC0415

    assert hasattr(_mem_cap, "_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE"), (
        "tests._mem_cap must define _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE as a "
        "named module-level constant — not a magic number inlined into install_total_memory_cap"
    )


@pytest.mark.skipif(os.name != "nt", reason="Win32 Job Object — Windows only")
def test_kill_on_job_close_flag_value_is_0x2000():
    """_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE must equal 0x2000, not 0x200 (AC-4).

    0x2000 = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE (children die when last job handle closes)
    0x0200 = JOB_OBJECT_LIMIT_JOB_MEMORY        (total memory cap — a different flag)

    These values differ by a factor of 16.  A transposition during the AC-4 edit
    (copy-paste from the adjacent JOB_MEMORY constant) is the most likely regression
    and this test catches it at the constant level before any subprocess is involved.
    """
    from tests import _mem_cap  # noqa: PLC0415

    flag = _mem_cap._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    assert flag == 0x2000, (
        f"_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE must equal 0x2000 (8192 decimal), "
        f"got {hex(flag)} ({flag} decimal).  "
        "0x0200 is JOB_OBJECT_LIMIT_JOB_MEMORY — a common transposition error."
    )
    # Belt-and-suspenders: verify it differs from the adjacent JOB_MEMORY flag.
    assert flag != _mem_cap.JOB_OBJECT_LIMIT_JOB_MEMORY, (
        "KILL_ON_JOB_CLOSE (0x2000) and JOB_MEMORY (0x0200) must be distinct constants; "
        "they appear to be identical — check for a copy-paste transposition"
    )


@pytest.mark.skipif(os.name != "nt", reason="Win32 Job Object — Windows only")
def test_kill_on_job_close_flag_present_in_limit_flags_after_install():
    """After install_total_memory_cap, QueryInformationJobObject must show 0x2000 in LimitFlags (AC-4).

    This is the structural proof that the flag is wired into the OR-expression passed
    to SetInformationJobObject — not merely defined as a constant.  Querying the live
    job object after install eliminates any doubt about whether the flag was actually
    applied.

    Uses a generous 16 GB cap so the cap never fires during the test itself.
    Reconstructs the JOBOBJECT_EXTENDED_LIMIT_INFORMATION ctypes structure to
    call QueryInformationJobObject (the same approach used in
    test_total_job_semantics_bounds_fanout_not_just_per_process).
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes as wintypes  # noqa: PLC0415

    from tests import _mem_cap  # noqa: PLC0415

    cap_bytes = 16 * 1024 * 1024 * 1024  # 16 GB — never fires on this machine
    _mem_cap.install_total_memory_cap(cap_bytes)

    assert _mem_cap._JOB_HANDLE is not None, (
        "_JOB_HANDLE must be non-None after install_total_memory_cap"
    )

    # Reconstruct the same ctypes structures the implementation uses to query
    # back the limit info from the OS.
    class _BASIC_LIMIT(ctypes.Structure):
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

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _EXT_LIMIT(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BASIC_LIMIT),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    JobObjectExtendedLimitInformation = 9

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]

    info = _EXT_LIMIT()
    ret_len = wintypes.DWORD(0)
    ok = kernel32.QueryInformationJobObject(
        _mem_cap._JOB_HANDLE,
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
        ctypes.byref(ret_len),
    )
    assert ok, f"QueryInformationJobObject failed: {ctypes.WinError(ctypes.get_last_error())}"

    flags = info.BasicLimitInformation.LimitFlags
    KILL_ON_JOB_CLOSE = 0x2000

    assert flags & KILL_ON_JOB_CLOSE, (
        f"LimitFlags must have JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE (0x2000) set after install; "
        f"got LimitFlags=0x{flags:08x}.  "
        "Add _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE to the OR-expression in "
        "install_total_memory_cap's SetInformationJobObject call (AC-4)."
    )
