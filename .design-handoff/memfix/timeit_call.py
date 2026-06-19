"""Time ONE assemble_advisor_context call with network mocked; time each sub-call."""

import os
import sys
import time
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _REPO_ROOT)
from job_cap_harness import install_cap

install_cap(3 * 1024**3)
from dotenv import load_dotenv

load_dotenv(os.path.join(_REPO_ROOT, ".env"))
import ai_advisor


class _FakeResp:
    status_code = 429
    headers = {}

    def json(self):
        import json

        raise json.JSONDecodeError("x", "", 0)

    def raise_for_status(self):
        from requests.exceptions import HTTPError

        raise HTTPError("429")


def _fake_get(*a, **k):
    return _FakeResp()


FA = {
    "run_timestamp": "t",
    "symphony_id": "s",
    "oos_alpha": 1.5,
    "train_alpha": 3.0,
    "baseline_decision": "Adopted AI",
    "fallback_oos_alpha": 0.8,
    "default_oos_alpha": 0.2,
}
FL = {"asset_universe": ["AAA"], "indicators_used": {}, "tree_depth": 1}

with patch.object(ai_advisor.requests, "get", _fake_get):
    # Time each producer individually.
    for name in (
        "_build_suggestible_surface",
        "_read_current_strategy",
        "_build_sentiment_section",
        "_build_macro_section",
        "_build_fundamentals_section",
        "_build_technicals_section",
        "_build_derivatives_section",
    ):
        fn = getattr(ai_advisor, name)
        t0 = time.perf_counter()
        try:
            if name in ("_build_suggestible_surface", "_read_current_strategy"):
                fn("s")
            else:
                fn()
        except Exception as e:
            print(f"  {name}: ERR {type(e).__name__}", flush=True)
            continue
        print(f"  {name}: {time.perf_counter() - t0:.3f}s", flush=True)

    print("--- full assemble x3 ---", flush=True)
    for i in range(3):
        with (
            patch.object(ai_advisor.database, "get_latest_autotune_run", return_value=FA),
            patch.object(ai_advisor.symphony_logic, "get_condensed_logic", return_value=FL),
        ):
            t0 = time.perf_counter()
            ai_advisor.assemble_advisor_context(scope="symphony", symphony_id="s")
            print(f"  assemble[{i}]: {time.perf_counter() - t0:.3f}s", flush=True)
print("DONE", flush=True)
