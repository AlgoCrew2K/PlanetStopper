"""Write the request every operator POST control sends, at the plan's base.

Run from the repository root with DB_PATH and ATLAS_CACHE_DB_PATH set to scratch files:

    python tests/fixtures/refresh_timing/04-c9/capture_post_requests.py

It drives each control of the census (``tests/ui/_post_controls``) once with fixed inputs over the real
scripts and templates, answers the POST with a plain success, and records the request the page sent
(URL, method, headers with the token's value replaced by ``<token>``, body, ``keepalive``, the
init object's keys). It refuses to run when ``static/``, ``templates/`` or ``app.py`` differ from
``HEAD``, so the record is always the base's.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

OUT = Path(__file__).with_name("post_requests_at_base.json")
GUARDED = ("static", "templates", "app.py")


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
    from tests.ui import _post_controls

    controls = _post_controls.drive_all()
    OUT.write_text(
        json.dumps(
            {
                "base_sha": head,
                "captured_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "token_placeholder": _post_controls.PLACEHOLDER,
                "controls": controls,
            },
            indent=1,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT} with {len(controls)} controls")


if __name__ == "__main__":
    main()
