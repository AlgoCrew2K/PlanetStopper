"""Write ``baseline.json``: the computed style of every heading and panel child at the base.

Run on a tree whose production files equal the base commit (the script refuses otherwise):

    DB_PATH=<scratch>/state.db ATLAS_CACHE_DB_PATH=<scratch>/atlas.db \
        python tests/fixtures/dashboard/heading_margin_baseline/produce_heading_margin_baseline.py

The reading code is ``tests/ui/_heading_margin_capture.py``, the module the checking test
imports. State, browser and server follow the suite's in-process pattern (the app in this
process on a scratch database, written through the real writers from captured records, every
request to anything but the server refused). Provenance: ``PROVENANCE.md`` in this directory.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[3]
sys.path.insert(0, str(REPO))

BASE_SHA = "758f7f9551ff3a15a292debb7bfd4f00b3ecf1e7"
WATCHED = ["static", "templates", "app.py"]

_CREDENTIAL_VARIABLES = (
    "LIVE_EXECUTION",
    "EXIT_AUTHORITY",
    "EXECUTION_START_TIME",
    "COMPOSER_KEY_ID",
    "COMPOSER_SECRET",
    "ALPACA_KEY",
    "ALPACA_SECRET",
    "DISCORD_WEBHOOK_URL",
    "ANTHROPIC_API_KEY",
    "ACCOUNT_INDIVIDUAL",
    "ACCOUNT_ROTH",
    "ACCOUNT_TRAD",
    "ADVISOR_SUGGESTION_MODEL",
    "ADVISOR_SYNTHESIS_MODEL",
)


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)


def refuse_unless_production_files_equal_the_base() -> None:
    differs = _git("diff", "--quiet", BASE_SHA, "--", *WATCHED)
    if differs.returncode != 0:
        raise SystemExit(f"refusing: {WATCHED} differ from {BASE_SHA}")
    dirty = _git("status", "--porcelain", "--", *WATCHED).stdout.strip()
    if dirty:
        raise SystemExit(f"refusing: {WATCHED} have uncommitted changes:\n{dirty}")


def main() -> None:
    refuse_unless_production_files_equal_the_base()
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="heading_margin_baseline_"))
    os.environ.setdefault("DB_PATH", str(scratch / "state.db"))
    os.environ.setdefault("ATLAS_CACHE_DB_PATH", str(scratch / "atlas.db"))

    import database

    database.init_db()
    database.run_migrations()

    import app as app_module
    from tests.ui import _heading_margin_capture as capture
    from tests.ui._browser_server import running_server

    # After the imports: importing the app loads the developer's .env, which would put the
    # credentials back. The pages read them (the Frontrunner tab shows a notice without a key), so
    # the capture runs with none, as the checking test does (advisor_server).
    for name in _CREDENTIAL_VARIABLES:
        os.environ.pop(name, None)

    env_file = scratch / "heading_margin.env"
    env_file.write_text("", encoding="utf-8")
    app_module.ENV_FILE_PATH = str(env_file)
    app_module._auth_check_enabled = False
    app_module._csrf_check_enabled = False
    capture.prepare(scratch, setattr)

    from playwright.sync_api import sync_playwright

    with running_server() as server, sync_playwright() as playwright:
        browser = playwright.firefox.launch(headless=True)
        try:
            record = capture.capture_everything(browser, server)
        finally:
            browser.close()

    out = {
        "base_sha": BASE_SHA,
        "captured_at_utc": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "capture_instant": capture.CAPTURE_INSTANT.isoformat(),
        **record,
    }
    # LF line ends on every host: a CRLF file differs byte for byte on the CI runner.
    with open(HERE / "baseline.json", "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(out, indent=1) + "\n")
    counts = {k: len(v) for k, v in record["views"].items()}
    print(
        f"wrote baseline.json at {BASE_SHA}: {len(counts)} views, "
        f"{sum(counts.values())} elements, Firefox {record['browser_version']}"
    )


if __name__ == "__main__":
    main()
