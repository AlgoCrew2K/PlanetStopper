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


def _run_child(script: str, *, env: dict | None = None, timeout: int = 30) -> subprocess.CompletedProcess:
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
            b[::4096] = bytes([1] * (len(b) // 4096 + 1))[:len(b) // 4096 + 1]
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
    """Total-job cap catches fan-out that per-process cap misses (AC-3).

    This is the exact gap that crashed the host on 2026-06-21.

    Setup: a 60 MB TOTAL-JOB cap.
    - Parent process: allocates ~40 MB and HOLDS it (does not exit).
    - While parent holds, it spawns a child process that tries to allocate ~30 MB.
    - Live job total at child alloc time: ~40 MB (parent) + ~30 MB (child) = ~70 MB > 60 MB.
    - Child's allocation must fail with MemoryError.

    With a per-process cap of, say, 40 MB: parent (40 MB, at limit) would pass;
    child (30 MB, under 40 MB limit) would also pass — the FAN-OUT is NOT caught.
    Only a TOTAL-JOB cap catches the combination.

    Safety: each individual allocation is < 50 MB.  Total across both processes
    is ~70 MB, well under the host ceiling (67.8 GB).  The outer test process
    itself allocates nothing.
    """
    cap_mb = 60
    parent_hold_mb = 40   # parent allocates this and holds it
    child_alloc_mb = 30   # child tries to allocate this; total would be 70 MB > 60 MB cap

    # The parent script:
    #   1. Installs the job cap on itself (and all future children, which inherit the job).
    #   2. Allocates parent_hold_mb and touches every page (force commit).
    #   3. Spawns a child that tries to allocate child_alloc_mb.
    #   4. Checks whether the child got MemoryError.
    #   5. Exits 0 if child got MemoryError (cap working), exits 3 if child succeeded (cap broken).
    parent_script = f"""
        import sys, subprocess, textwrap, os
        sys.path.insert(0, r"{_WORKTREE}")
        from tests import _mem_cap

        cap_bytes = {cap_mb} * 1024 * 1024
        _mem_cap.install_total_memory_cap(cap_bytes)

        # Allocate parent_hold_mb and HOLD it while spawning the child.
        hold = bytearray({parent_hold_mb} * 1024 * 1024)
        hold[::4096] = bytes([1] * (len(hold) // 4096 + 1))[:len(hold) // 4096 + 1]

        # Now spawn the child while still holding the committed memory.
        child_code = textwrap.dedent(\"\"\"
            import sys
            sys.path.insert(0, r"{_WORKTREE}")
            try:
                b = bytearray({child_alloc_mb} * 1024 * 1024)
                b[::4096] = bytes([1] * (len(b) // 4096 + 1))[:len(b) // 4096 + 1]
                print("CHILD_OK", flush=True)
                sys.exit(0)
            except MemoryError:
                print("CHILD_MEMORYERROR", flush=True)
                sys.exit(1)
        \"\"\")

        child_env = os.environ.copy()
        child_env["PYTHONPATH"] = r"{_WORKTREE}"
        result = subprocess.run(
            [sys.executable, "-c", child_code],
            capture_output=True, text=True, timeout=20,
            env=child_env,
        )

        if "CHILD_MEMORYERROR" in result.stdout or result.returncode == 1:
            # Child got MemoryError — total-job cap is working correctly.
            print("PARENT_PASS", flush=True)
            sys.exit(0)
        else:
            # Child succeeded — cap is NOT bounding the total job.
            print(f"PARENT_FAIL child_stdout={{result.stdout!r}}", flush=True)
            sys.exit(3)
    """

    result = _run_child(parent_script, timeout=30)

    assert "PARENT_PASS" in result.stdout, (
        f"Total-job fan-out cap did NOT fire.\n"
        f"Parent stdout: {result.stdout!r}\nParent stderr: {result.stderr!r}\n"
        f"Parent exit: {result.returncode}\n\n"
        f"Expected: parent holds {parent_hold_mb} MB + child tries {child_alloc_mb} MB "
        f"= {parent_hold_mb + child_alloc_mb} MB total > {cap_mb} MB cap → child MemoryError.\n"
        f"Got: child succeeded, meaning the cap is per-process (not total-job) "
        f"or not installed at all.  Check that install_total_memory_cap uses "
        f"JOB_OBJECT_LIMIT_JOB_MEMORY (0x200) and sets info.JobMemoryLimit, "
        f"NOT info.ProcessMemoryLimit."
    )
    assert result.returncode == 0, (
        f"Parent script exited {result.returncode}. stdout: {result.stdout!r}"
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
    assert cap >= 8, (
        f"DEFAULT_CAP_GB must be >= 8 GB (a pytest run needs headroom), got {cap}"
    )
    assert cap <= 64, (
        f"DEFAULT_CAP_GB must be <= 64 GB (well under the 67.8 GB host ceiling), got {cap}"
    )
