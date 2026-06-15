"""Reproduce the cumulative growth across repeated assemble_advisor_context calls.

Mirrors the test mocking (only the two DB accessors patched; the network lens
producers UNMOCKED, exactly as tests/ai_advisor/test_ai_advisor.py does). Loops
the call and reports committed-memory + open-handle growth + tracemalloc top
allocators between iterations.

Run under the job cap so a runaway is contained:
    python job_cap_harness.py 3 -- --leak-probe
(the harness has no hook for this; instead run directly AFTER importing the cap)
"""

from __future__ import annotations

import ctypes
import gc
import os
import sys
import threading
import tracemalloc
from unittest.mock import patch

# Install the hard cap first (import the harness installer).
sys.path.insert(0, os.path.dirname(__file__))
# Repo root (3 levels up: memfix/ -> .design-handoff/ -> repo root).
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)
from job_cap_harness import install_cap  # noqa: E402

CAP_GB = float(os.environ.get("CAP_GB", "3"))
install_cap(int(CAP_GB * 1024 * 1024 * 1024))

import ai_advisor  # noqa: E402
import time as _time  # noqa: E402

# Optionally load .env so FRED/network keys match the pytest environment.
if os.environ.get("LOAD_ENV") == "1":
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(_REPO_ROOT, ".env"))
        print("[leak-probe] .env loaded", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[leak-probe] .env load failed: {exc}", flush=True)


def _time_producers():
    for name in ("_build_sentiment_section", "_build_macro_section", "_build_fundamentals_section"):
        fn = getattr(ai_advisor, name)
        t0 = _time.perf_counter()
        fn()
        print(f"    {name}: {_time.perf_counter() - t0:.2f}s", flush=True)


def _committed_mb() -> float:
    """Current process committed (private) bytes via GetProcessMemoryInfo."""

    class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    psapi = ctypes.WinDLL("psapi")
    kernel32 = ctypes.WinDLL("kernel32")
    counters = PROCESS_MEMORY_COUNTERS_EX()
    counters.cb = ctypes.sizeof(counters)
    psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    return counters.PrivateUsage / 1024 / 1024


def _handle_count() -> int:
    kernel32 = ctypes.WinDLL("kernel32")
    count = ctypes.c_ulong(0)
    kernel32.GetProcessHandleCount(kernel32.GetCurrentProcess(), ctypes.byref(count))
    return count.value


fake_autotune_run = {
    "run_timestamp": "2026-05-11T16:00:00",
    "symphony_id": "test-symphony-a",
    "oos_alpha": 1.5,
    "train_alpha": 3.0,
    "baseline_decision": "Adopted AI",
    "fallback_oos_alpha": 0.8,
    "default_oos_alpha": 0.2,
}
fake_condensed_logic = {"asset_universe": ["AAA"], "indicators": [], "tree": {}}

N = int(os.environ.get("N_ITERS", "8"))
tracemalloc.start(25)
prev_snap = None

for i in range(N):
    with (
        patch.object(
            ai_advisor.database, "get_latest_autotune_run", return_value=fake_autotune_run
        ),
        patch.object(
            ai_advisor.symphony_logic, "get_condensed_logic", return_value=fake_condensed_logic
        ),
    ):
        ai_advisor.assemble_advisor_context(scope="symphony", symphony_id="test-symphony-a")
    if os.environ.get("TIME_PRODUCERS") == "1":
        _time_producers()
    gc.collect()
    snap = tracemalloc.take_snapshot()
    print(
        f"\n=== iter {i}: committed={_committed_mb():.1f}MB "
        f"handles={_handle_count()} threads={threading.active_count()} "
        f"gc_objs={len(gc.get_objects())} ===",
        flush=True,
    )
    if prev_snap is not None:
        diffs = snap.compare_to(prev_snap, "lineno")[:8]
        for d in diffs:
            print(f"  {d}", flush=True)
    prev_snap = snap

print("\n[leak-probe] DONE", flush=True)
