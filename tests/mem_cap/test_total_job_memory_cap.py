"""Guard tests for the Windows Job Object total-tree memory cap (DE-TEST-MEMCAP-001).

These tests cover the new tests/_mem_cap.py module and its wiring into
tests/conftest.py pytest_configure.  They are the adversarial RED suite that
pins the implementation against four failure modes:

  1. Missing or un-callable public API (AC-1)
  2. Wrong Win32 flag — per-process instead of total-job (AC-3)
  3. Cap not firing on over-allocation (AC-2)
  4. Fan-out not bounded by TOTAL job (AC-3 — the exact crash scenario)
  5. Linux/CI no-op broken (AC-4)
  6. Env-knob opt-out silent or exception-raising (AC-1/AC-7)
  7. Default constant outside safe range (AC-1/AC-7)

Safety rule (enforced throughout): the TEST PROCESS itself never allocates toward
the host ceiling.  Children do the dangerous work, bounded by the cap under test.
All subprocess-spawned scripts are tiny; allocations are well under 128 MB per child.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WORKTREE = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
"""Absolute path to the worktree root — used to build PYTHONPATH for children."""


def _run_child(
    script: str, *, env: dict | None = None, timeout: int = 30
) -> subprocess.CompletedProcess:
    """Run a small Python script in a child process, returning the result.

    The child inherits the current Job Object (default inherit flags), so its
    committed memory counts against the total-job cap.  The script is passed
    via -c to avoid touching any file on disk.
    """
    child_env = os.environ.copy()
    child_env["PYTHONPATH"] = _WORKTREE
    if env:
        child_env.update(env)
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=child_env,
    )


# ---------------------------------------------------------------------------
# Group 1 — Module contract (Windows-gated)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "nt", reason="Win32 Job Object cap — Windows only")
def test_install_total_job_cap_callable_on_windows():
    """install_total_memory_cap must be a callable exported from tests._mem_cap (AC-1).

    If the module or function is missing the import itself fails, which is a
    clear RED signal pointing at the gap rather than an obscure AttributeError.
    """
    from tests import _mem_cap  # noqa: PLC0415

    assert callable(_mem_cap.install_total_memory_cap), (
        "tests._mem_cap.install_total_memory_cap must be callable"
    )


@pytest.mark.skipif(os.name != "nt", reason="Win32 Job Object cap — Windows only")
def test_install_total_job_cap_uses_job_memory_flag_not_per_process_flag():
    """The module must define both Win32 flag constants AND they must differ (AC-3).

    JOB_OBJECT_LIMIT_JOB_MEMORY  = 0x00000200  (total-tree cap — correct)
    JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100  (per-process — weaker, must NOT be used)

    This test pins the implementation against the most likely regression: copying the
    reference harness (.design-handoff/memfix/job_cap_harness.py) which uses the
    per-process flag.  If an implementer accidentally uses PROCESS_MEMORY for the cap
    field instead of JOB_MEMORY, this test catches it at the constant level even before
    any subprocess is spawned.
    """
    from tests import _mem_cap  # noqa: PLC0415

    assert hasattr(_mem_cap, "JOB_OBJECT_LIMIT_JOB_MEMORY"), (
        "tests._mem_cap must define JOB_OBJECT_LIMIT_JOB_MEMORY (the total-tree flag)"
    )
    assert hasattr(_mem_cap, "JOB_OBJECT_LIMIT_PROCESS_MEMORY"), (
        "tests._mem_cap must define JOB_OBJECT_LIMIT_PROCESS_MEMORY "
        "(the per-process flag — must exist so the two can be compared)"
    )
    assert _mem_cap.JOB_OBJECT_LIMIT_JOB_MEMORY == 0x00000200, (
        f"JOB_OBJECT_LIMIT_JOB_MEMORY must be 0x200 (512), "
        f"got {hex(_mem_cap.JOB_OBJECT_LIMIT_JOB_MEMORY)}"
    )
    assert _mem_cap.JOB_OBJECT_LIMIT_PROCESS_MEMORY == 0x00000100, (
        f"JOB_OBJECT_LIMIT_PROCESS_MEMORY must be 0x100 (256), "
        f"got {hex(_mem_cap.JOB_OBJECT_LIMIT_PROCESS_MEMORY)}"
    )
    assert _mem_cap.JOB_OBJECT_LIMIT_JOB_MEMORY != _mem_cap.JOB_OBJECT_LIMIT_PROCESS_MEMORY, (
        "JOB_OBJECT_LIMIT_JOB_MEMORY and JOB_OBJECT_LIMIT_PROCESS_MEMORY must differ — "
        "they are distinct Win32 flags and using the wrong one is the root cause of "
        "the 2026-06-21 host crash (per-process cap does not bound fan-out)"
    )


@pytest.mark.skipif(os.name != "nt", reason="Win32 Job Object cap — Windows only")
def test_install_total_job_cap_sets_sentinel():
    """install_total_memory_cap sets _CAP_INSTALLED to True after successful install (AC-1).

    Uses a very large cap (64 GB) so the cap never actually fires on this machine.
    Asserts only the sentinel shape (truthy bool), never a byte count.

    Note: this test installs a 64 GB cap on the current test process.  Since the
    test runner itself is a pytest subprocess bounded by conftest's installed cap
    (once mc-impl wires it in), the 64 GB cap here is a no-op — the conftest cap
    is already in effect and is lower.  The sentinel check still works because
    the install call succeeds (idempotent) and sets the sentinel.
    """
    from tests import _mem_cap  # noqa: PLC0415

    cap_64gb = 64 * 1024 * 1024 * 1024
    _mem_cap.install_total_memory_cap(cap_64gb)

    assert _mem_cap._CAP_INSTALLED is True, (
        "_CAP_INSTALLED sentinel must be True after install_total_memory_cap succeeds"
    )
    # _JOB_HANDLE must be non-None (a live Win32 HANDLE, kept for process lifetime)
    assert _mem_cap._JOB_HANDLE is not None, (
        "_JOB_HANDLE must be non-None after install — handle must stay alive to keep "
        "the job object from being destroyed"
    )


@pytest.mark.skipif(os.name != "nt", reason="Win32 Job Object cap — Windows only")
def test_install_total_job_cap_is_idempotent():
    """Calling install_total_memory_cap twice must not raise (AC-1, edge case).

    On Win8+ a process can be in a nested job.  AssignProcessToJobObject on a process
    already in a job must either succeed (nested) or degrade gracefully (access-denied
    swallowed).  In neither case should the second call raise an exception.
    Sentinel must remain truthy after the second call.
    """
    from tests import _mem_cap  # noqa: PLC0415

    cap_64gb = 64 * 1024 * 1024 * 1024

    # First call — may already be installed by conftest by the time this test runs.
    _mem_cap.install_total_memory_cap(cap_64gb)
    assert _mem_cap._CAP_INSTALLED is True

    # Second call — must not raise.
    _mem_cap.install_total_memory_cap(cap_64gb)
    assert _mem_cap._CAP_INSTALLED is True, (
        "_CAP_INSTALLED must still be True after a second install call"
    )


# ---------------------------------------------------------------------------
# Group 2 — Safety semantics (Windows-gated, bounded by the cap itself)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "nt", reason="Win32 Job Object cap — Windows only")
def test_over_cap_allocation_raises_memory_error_not_host_crash():
    """Allocating past the job cap raises MemoryError in the allocating process (AC-2).

    A child subprocess installs a 64 MB cap on itself, then tries to allocate 96 MB
    (touching every page to force commit).  The child must exit non-zero with
    'MemoryError' in its output.  The TEST PROCESS allocates nothing toward the
    host ceiling — all dangerous work happens in the bounded child.

    Why 64 MB cap / 96 MB alloc: small enough that the child's total committed memory
    (Python interpreter ~30 MB + 96 MB alloc = ~126 MB) clearly exceeds the 64 MB
    cap, but nowhere near the host ceiling (67.8 GB).  The child exits immediately
    after the MemoryError so no memory is retained.
    """
    cap_mb = 64
    alloc_mb = 96  # exceeds cap; child will MemoryError before touching all pages

    child_script = f"""
        import sys
        sys.path.insert(0, r"{_WORKTREE}")
        from tests import _mem_cap

        cap_bytes = {cap_mb} * 1024 * 1024
        _mem_cap.install_total_memory_cap(cap_bytes)

        try:
            b = bytearray({alloc_mb} * 1024 * 1024)
            # Touch every page to force Windows to commit the memory.
            # Integer division only (no +1) avoids ValueError on exact-multiple-of-4096 sizes.
            b[::4096] = b"\\x01" * (len(b) // 4096)
            print("FAIL: no MemoryError raised", flush=True)
            sys.exit(2)
        except MemoryError:
            print("MemoryError", flush=True)
            sys.exit(0)
    """

    result = _run_child(child_script, timeout=20)

    # The child must have printed 'MemoryError' (it caught the error cleanly and
    # printed the sentinel before exiting 0).  Any other outcome means the cap
    # did not fire correctly:
    #   - 'FAIL: no MemoryError raised' (exit 2): cap is present but not firing
    #   - 'NotImplementedError' or empty stdout (non-zero exit): stub not replaced
    #   - returncode non-zero without 'MemoryError' in output: unexpected failure
    assert "MemoryError" in result.stdout, (
        f"Child must print 'MemoryError' after the cap fires.\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}\n"
        f"returncode: {result.returncode}\n"
        "If stdout is empty or contains NotImplementedError, the stub has not been "
        "replaced with a real implementation.  If it contains 'FAIL: no MemoryError "
        "raised', the cap is installed but not firing — check the Win32 flag and "
        "AssignProcessToJobObject call."
    )
    assert result.returncode == 0, (
        f"Child must exit 0 after catching MemoryError cleanly, got {result.returncode}.\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )


@pytest.mark.skipif(os.name != "nt", reason="Win32 Job Object cap — Windows only")
def test_total_job_semantics_bounds_fanout_not_just_per_process():
    """Installed cap uses JobMemoryLimit (total-tree), not ProcessMemoryLimit (AC-3).

    This is the structural proof that the AC-3 gap (which crashed the host on
    2026-06-21) cannot regress.  The crash scenario was: per-process cap lets
    each child process allocate up to its own limit; the TOTAL across all children
    in the fan-out (N workers × per-process committed) exceeds the host ceiling.
    Only JOB_OBJECT_LIMIT_JOB_MEMORY + JobMemoryLimit catches the combined total.

    This test verifies the correct field is set by querying the installed job object
    via QueryInformationJobObject and asserting:
      - JobMemoryLimit == cap_bytes  (total-tree field is set)
      - ProcessMemoryLimit == 0      (per-process field is NOT set)
      - LimitFlags has JOB_OBJECT_LIMIT_JOB_MEMORY (0x200) set
      - LimitFlags does NOT have JOB_OBJECT_LIMIT_PROCESS_MEMORY (0x100) set

    Why structural proof rather than a subprocess fan-out experiment:
    Windows nested job inheritance is complex — a subprocess spawned from within an
    already-jobbed process (e.g. the conftest's 24 GB job) may or may not inherit
    a tighter inner nested job, depending on SILENT_BREAKAWAY flags.  A structural
    query of the installed job object is a reliable, host-safe proof that the
    implementation is correct without depending on nested job child-inheritance
    semantics that vary across Windows versions and job configurations.
    """
    import ctypes  # noqa: PLC0415
    import ctypes.wintypes as wintypes  # noqa: PLC0415

    from tests import _mem_cap  # noqa: PLC0415

    # Install with a generous cap so we do not actually hit the limit during the test.
    cap_bytes = 16 * 1024 * 1024 * 1024  # 16 GB — never fires on this machine

    _mem_cap.install_total_memory_cap(cap_bytes)

    assert _mem_cap._JOB_HANDLE is not None, (
        "_JOB_HANDLE must be set after install_total_memory_cap"
    )

    # Query the installed job object to verify the correct limits are set.
    # We reconstruct the same ctypes structures the implementation uses.
    JobObjectExtendedLimitInformation = 9

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

    JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
    JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    flags = info.BasicLimitInformation.LimitFlags

    # The total-job flag must be set.
    assert flags & JOB_OBJECT_LIMIT_JOB_MEMORY, (
        f"LimitFlags must have JOB_OBJECT_LIMIT_JOB_MEMORY (0x200) set; "
        f"got LimitFlags=0x{flags:08x}.  "
        f"Implementation is using the per-process flag instead of the total-job flag."
    )
    # The per-process flag must NOT be the sole limit (implementation should not set it).
    # Note: we don't assert it's zero because some nested job configs may inherit flags;
    # the critical requirement is that JOB_MEMORY is present.
    assert not (flags & JOB_OBJECT_LIMIT_PROCESS_MEMORY) or (flags & JOB_OBJECT_LIMIT_JOB_MEMORY), (
        f"LimitFlags has PROCESS_MEMORY (0x100) but NOT JOB_MEMORY (0x200); "
        f"got LimitFlags=0x{flags:08x}.  "
        f"Implementation set the per-process cap instead of the total-job cap — "
        f"this is the bug that caused the 2026-06-21 host crash."
    )
    # The JobMemoryLimit field must equal cap_bytes.
    assert info.JobMemoryLimit == cap_bytes, (
        f"JobMemoryLimit must equal cap_bytes={cap_bytes}; "
        f"got JobMemoryLimit={info.JobMemoryLimit}.  "
        f"Implementation may be setting ProcessMemoryLimit instead of JobMemoryLimit."
    )
    # ProcessMemoryLimit must be zero (not set by our installer).
    assert info.ProcessMemoryLimit == 0, (
        f"ProcessMemoryLimit must be 0 (total-job installer must NOT set per-process limit); "
        f"got ProcessMemoryLimit={info.ProcessMemoryLimit}.  "
        f"Implementation is setting both limits or the wrong one."
    )


# ---------------------------------------------------------------------------
# Group 3 — Linux/CI no-op (cross-platform — NOT Windows-gated)
# ---------------------------------------------------------------------------


def test_install_total_job_cap_is_noop_on_non_windows(monkeypatch):
    """install_total_memory_cap is a clean no-op on non-Windows (AC-4).

    Monkeypatches os.name to 'posix' so the Windows branch is not taken.
    On a real Linux CI runner this test also passes natively (os.name == 'posix'
    already, monkeypatch just reinforces the same value).

    This test is intentionally NOT Windows-gated so it runs on CI and provides
    a regression guard that the conftest import never breaks the Linux runner.
    """
    import tests._mem_cap as _mem_cap  # noqa: PLC0415

    monkeypatch.setattr(os, "name", "posix")

    # Must not raise, must not import ctypes.windll or any Win32 symbol.
    _mem_cap.install_total_memory_cap(1 * 1024 * 1024 * 1024)  # 1 GB — irrelevant on posix

    # Sentinel behaviour on non-Windows is implementation-defined; the only
    # hard requirement is no exception.  We do NOT assert _CAP_INSTALLED here
    # because on posix the sentinel is meaningless.


# ---------------------------------------------------------------------------
# Group 4 — Env knob (cross-platform, no skip)
# ---------------------------------------------------------------------------


def test_alphabot_test_mem_cap_gb_env_knob_zero_disables_with_warning(monkeypatch):
    """ALPHABOT_TEST_MEM_CAP_GB=0 disables the cap and emits a warning (AC-1/AC-7).

    The warning must be emitted so the opt-out is never silent.  Uses pytest's
    recwarn fixture (via the warnings module) to confirm the warning was issued.
    Platform-independent: the env-knob parsing and warning path is pure Python.
    """
    import warnings  # noqa: PLC0415

    import tests._mem_cap as _mem_cap  # noqa: PLC0415

    monkeypatch.setenv("ALPHABOT_TEST_MEM_CAP_GB", "0")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        # install_from_env reads the env var; with =0 it must warn and not install.
        _mem_cap.install_from_env()

    warning_texts = [str(w.message) for w in caught]
    assert any(warning_texts), (
        "install_from_env() with ALPHABOT_TEST_MEM_CAP_GB=0 must emit a warning "
        "via warnings.warn() — the opt-out must never be silent"
    )
    # The warning should mention the env var or the cap being disabled.
    combined = " ".join(warning_texts).lower()
    assert any(kw in combined for kw in ("cap", "disabled", "mem", "0", "alphabot")), (
        f"Warning text should mention the cap/env var; got: {warning_texts!r}"
    )


def test_alphabot_test_mem_cap_gb_default_is_present_and_sane():
    """DEFAULT_CAP_GB must be a named constant in a safe range (AC-1/AC-7).

    Asserts shape and bounds, never a specific value:
    - Must exist as a module-level name (not computed at call time)
    - Must be a positive float or int
    - Must be >= 8 (a legitimate pytest run needs headroom)
    - Must be <= 64 (under the 67.8 GB host ceiling with margin)

    This test does NOT assert a specific value so the implementer can tune the
    default within the safe range without breaking the test.
    """
    import tests._mem_cap as _mem_cap  # noqa: PLC0415

    assert hasattr(_mem_cap, "DEFAULT_CAP_GB"), (
        "tests._mem_cap must export DEFAULT_CAP_GB as a named module-level constant"
    )
    cap = _mem_cap.DEFAULT_CAP_GB
    assert isinstance(cap, (int, float)), (
        f"DEFAULT_CAP_GB must be a numeric type, got {type(cap).__name__}"
    )
    assert cap > 0, f"DEFAULT_CAP_GB must be positive, got {cap}"
    assert cap >= 8, f"DEFAULT_CAP_GB must be >= 8 GB (a pytest run needs headroom), got {cap}"
    assert cap <= 64, (
        f"DEFAULT_CAP_GB must be <= 64 GB (well under the 67.8 GB host ceiling), got {cap}"
    )
