"""Directly measure each lens producer's HTTP response: time, bytes, json size.

Capped at 3GB. Isolates which producer's response is pathological.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)
from job_cap_harness import install_cap

install_cap(3 * 1024**3)

from dotenv import load_dotenv

load_dotenv(os.path.join(_REPO_ROOT, ".env"))

import requests

import ai_advisor


def measure(label, url, headers=None, params=None):
    t0 = time.perf_counter()
    try:
        resp = requests.get(url, headers=headers or {}, params=params, timeout=15)
        body = resp.content
        dt = time.perf_counter() - t0
        clen = resp.headers.get("Content-Length")
        enc = resp.headers.get("Content-Encoding")
        print(
            f"[{label}] status={resp.status_code} time={dt:.2f}s "
            f"body_bytes={len(body):,} Content-Length={clen} "
            f"Content-Encoding={enc} ctype={resp.headers.get('Content-Type')}",
            flush=True,
        )
        # Try json parse + measure parsed object footprint roughly.
        t1 = time.perf_counter()
        try:
            data = resp.json()
            import json

            j = json.dumps(data)
            print(
                f"    json_parse={time.perf_counter() - t1:.2f}s "
                f"json_str_len={len(j):,} top_type={type(data).__name__}",
                flush=True,
            )
            if isinstance(data, dict):
                arts = data.get("articles")
                if arts is not None:
                    print(f"    articles={len(arts)}", flush=True)
        except Exception as exc:
            print(f"    json parse FAILED: {type(exc).__name__}: {str(exc)[:120]}", flush=True)
    except Exception as exc:
        print(
            f"[{label}] FETCH FAILED time={time.perf_counter() - t0:.2f}s "
            f"{type(exc).__name__}: {str(exc)[:120]}",
            flush=True,
        )


measure("GDELT", ai_advisor._GDELT_ARTLIST_URL)

fred_key = os.environ.get("FRED_API_KEY", "").strip()
print(f"\nFRED key present: {bool(fred_key)}", flush=True)
if fred_key:
    measure(
        "FRED-DGS10",
        ai_advisor._FRED_OBSERVATIONS_URL,
        params={
            "series_id": "DGS10",
            "api_key": fred_key,
            "file_type": "json",
            "limit": 10,
            "sort_order": "desc",
        },
    )

print("\nDONE", flush=True)
