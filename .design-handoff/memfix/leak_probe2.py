"""Fast cumulative-growth probe for assemble_advisor_context.

Two modes (env MODE):
  MODE=mocknet   -> requests.get patched to an instant fake Response (no network).
                    Isolates any PYTHON-LEVEL accumulation in repeated calls.
  MODE=realnet   -> real network (slow). Few iters.

Reports committed (PrivateUsage) + RSS + handles + threads + gc obj count +
tracemalloc top growers between iterations. Hard-capped at CAP_GB.
"""

from __future__ import annotations

import ctypes
import gc
import os
import sys
import threading
import tracemalloc
from ctypes import wintypes
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)
from job_cap_harness import install_cap  # noqa: E402

CAP_GB = float(os.environ.get("CAP_GB", "3"))
install_cap(int(CAP_GB * 1024 * 1024 * 1024))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(_REPO_ROOT, ".env"))

import ai_advisor  # noqa: E402


class _PMC(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
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


_psapi = ctypes.WinDLL("psapi")
_k32 = ctypes.WinDLL("kernel32")
_psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
_psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
_k32.GetCurrentProcess.restype = wintypes.HANDLE
_k32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_k32.GetProcessHandleCount.restype = wintypes.BOOL


def mem():
    c = _PMC()
    c.cb = ctypes.sizeof(c)
    _psapi.GetProcessMemoryInfo(_k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.PrivateUsage / 1024 / 1024, c.WorkingSetSize / 1024 / 1024


def handles():
    n = ctypes.c_ulong(0)
    _k32.GetProcessHandleCount(_k32.GetCurrentProcess(), ctypes.byref(n))
    return n.value


FAKE_AUTOTUNE = {
    "run_timestamp": "2026-05-11T16:00:00",
    "symphony_id": "test-symphony-a",
    "oos_alpha": 1.5,
    "train_alpha": 3.0,
    "baseline_decision": "Adopted AI",
    "fallback_oos_alpha": 0.8,
    "default_oos_alpha": 0.2,
}
FAKE_LOGIC = {"asset_universe": ["AAA"], "indicators_used": {}, "tree_depth": 1}

MODE = os.environ.get("MODE", "mocknet")
N = int(os.environ.get("N_ITERS", "50"))


class _FakeResp:
    status_code = 429
    headers = {}

    def json(self):
        import json as _j

        raise _j.JSONDecodeError("Expecting value", "", 0)

    def raise_for_status(self):
        from requests.exceptions import HTTPError

        raise HTTPError("429")


def _fake_get(*a, **k):
    return _FakeResp()


tracemalloc.start(25)
prev = None
c0, w0 = mem()
print(
    f"MODE={MODE} N={N} baseline committed={c0:.1f}MB rss={w0:.1f}MB "
    f"handles={handles()} threads={threading.active_count()}",
    flush=True,
)

net_patch = patch.object(ai_advisor.requests, "get", _fake_get) if MODE == "mocknet" else None
if net_patch:
    net_patch.start()

for i in range(N):
    with (
        patch.object(ai_advisor.database, "get_latest_autotune_run", return_value=FAKE_AUTOTUNE),
        patch.object(ai_advisor.symphony_logic, "get_condensed_logic", return_value=FAKE_LOGIC),
    ):
        ai_advisor.assemble_advisor_context(scope="symphony", symphony_id="test-symphony-a")
    if i % 5 == 0 or i == N - 1:
        gc.collect()
        c, w = mem()
        snap = tracemalloc.take_snapshot()
        print(
            f"iter {i:3d}: committed={c:8.1f}MB rss={w:8.1f}MB "
            f"handles={handles():5d} threads={threading.active_count()} "
            f"gc_objs={len(gc.get_objects()):7d}",
            flush=True,
        )
        if prev is not None:
            for d in snap.compare_to(prev, "lineno")[:6]:
                print(f"     {d}", flush=True)
        prev = snap

if net_patch:
    net_patch.stop()

cf, wf = mem()
print(
    f"\nFINAL committed={cf:.1f}MB (delta={cf - c0:+.1f}MB) rss={wf:.1f}MB "
    f"handles={handles()} threads={threading.active_count()}",
    flush=True,
)
print("DONE", flush=True)
