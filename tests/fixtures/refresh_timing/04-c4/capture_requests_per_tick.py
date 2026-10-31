"""Write the request list /performance sends on its 60-second tick, at the plan's base.

Run from the repository root with DB_PATH and ATLAS_CACHE_DB_PATH set to scratch files:

    python tests/fixtures/refresh_timing/04-c4/capture_requests_per_tick.py

It evaluates the real ``static/performance.js`` unedited in ``tests/ui/_page_js_harness`` over the
page the real app renders, with every answer taken from the real routes over the captured
production records (``tests/ui/_performance_page``), advances the page's clock by 60 seconds and
writes the data requests the tick sent. It refuses to run when ``static/performance.js`` or
``templates/performance.html`` differs from ``HEAD``, so the record is always the base's.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

OUT = Path(__file__).with_name("requests_per_tick_at_base.json")
GUARDED = ("static/performance.js", "templates/performance.html")


def main() -> None:
    dirty = subprocess.run(
        ["git", "-C", str(REPO), "diff", "--name-only", "HEAD", "--", *GUARDED],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    if dirty:
        raise SystemExit(f"refusing to capture: {dirty} differ from HEAD")
    head = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()

    import app as app_module
    import database
    from tests.ui._performance_page import one_minute_tick_requests, seed_world

    # Outside pytest the dashboard's hooks are on; the page's answers are read with them off, as the
    # test suite reads them (tests/conftest.py turns both off for every test).
    app_module._auth_check_enabled = False
    app_module._csrf_check_enabled = False
    database.init_db()
    seed_world()
    requests = one_minute_tick_requests()
    OUT.write_text(
        json.dumps(
            {
                "base_sha": head,
                "captured_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "tick_requests": requests,
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT} with {len(requests)} requests")


if __name__ == "__main__":
    main()
