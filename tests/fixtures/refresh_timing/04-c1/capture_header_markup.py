"""Write the header ``<nav>`` markup each page renders, at the plan's base.

Run from the repository root with DB_PATH and ATLAS_CACHE_DB_PATH set to scratch files:

    python tests/fixtures/refresh_timing/04-c1/capture_header_markup.py

The real app renders every page through its test client with the host clock frozen on a Saturday
(``tests/ui/_history_answers.frozen_host_clock``) and the developer ``.env`` unread, so each word the
server writes into the header is stable. It refuses to run when ``templates/`` or ``app.py`` differ
from ``HEAD``, so the record is always the base's.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

OUT = Path(__file__).with_name("header_markup_at_base.json")
GUARDED = ("templates", "app.py", "static/chrome.js")


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

    app_module._auth_check_enabled = False
    app_module._csrf_check_enabled = False
    database.init_db()
    from tests.ui._header_status_answers import PAGES_WITH_A_HEADER, nav_markup

    navs = {path: nav_markup(path) for path in PAGES_WITH_A_HEADER}
    OUT.write_text(
        json.dumps(
            {
                "base_sha": head,
                "captured_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "navs": navs,
            },
            indent=1,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT} with {len(navs)} pages")


if __name__ == "__main__":
    main()
