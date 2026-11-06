"""Write ``ref_capture.json``: what the five pages show, request and pin at the reference commit.

Run ONCE, in a throwaway worktree whose production paths equal the reference commit (the script
refuses otherwise). From the repository root:

    git worktree add --detach <path> 7e6afd02            # production paths equal 1988ce5f
    # copy into <path>, at the same relative paths:
    #   tests/fixtures/page_explainers/produce_ref_capture.py, copy.json (the --pass-one output)
    #   tests/ui/_page_explainers.py
    cd <path> && python tests/fixtures/page_explainers/produce_ref_capture.py
    git worktree remove <path>                           # plain, never forced

The reading code is ``tests/ui/_page_explainers.py``, the module the checks import, so a
difference between this record and a head is a difference between the trees and never between
two readers. State is written through the real writers from captured records
(``seed_world``), the app runs in this process on a scratch database, every request to anything
but that server is refused, and the developer ``.env`` is never read. Provenance:
``PROVENANCE.md`` in this directory.

What the record holds, per seeded world and host: the document height and the heights of the
elements the copy tables reword at 375 px (the AC-7 baseline), the figure-element texts, the
control inventory at 1440 px and 375 px (S-5.1, S-5.2), the section-heading order, the banner
markup (S-5.3, S-5.5), the bodies of the seven read-only routes (S-5.6); once: the login and
logout pages and one Discord payload (S-5.9), the content pins of the engine files and
``database.py`` and the migration listing (S-5.13, S-5.7), the route table and the scheduler
digest (S-5.7), the buttons no script handles (S-5.15), the rendered text of the kept rows, and
the Firefox version.
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
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))

REFERENCE_COMMIT = "1988ce5f"
BUILD_BASE_COMMIT = "8f8be28d"
PRODUCTION_PATHS = (
    "app.py",
    "templates",
    "static",
    "analytics.py",
    "database.py",
    "market_calendar.py",
    "alpha_bot_execution.py",
    "math_engine.py",
    "reporting.py",
)
# database.py is pinned at the build base (the merged security head adds lines to it), so it is
# the one production path allowed to differ from the reference here.
ALLOWED_TO_DIFFER = ("database.py",)


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)


def refuse_unless_production_equals_the_reference() -> None:
    checked = [p for p in PRODUCTION_PATHS if p not in ALLOWED_TO_DIFFER]
    if _git("diff", "--quiet", REFERENCE_COMMIT, "--", *checked).returncode != 0:
        raise SystemExit(f"refusing to write: {checked} differ from {REFERENCE_COMMIT}")
    dirty = _git("status", "--porcelain", "--", *PRODUCTION_PATHS).stdout.strip()
    if dirty:
        raise SystemExit(f"refusing to write: uncommitted change in the production paths:\n{dirty}")


def main() -> None:
    refuse_unless_production_equals_the_reference()
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="page_explainers_ref_"))
    os.environ["DB_PATH"] = str(scratch / "state.db")
    os.environ.setdefault("ATLAS_CACHE_DB_PATH", str(scratch / "atlas.db"))

    import database

    database.init_db()
    database.run_migrations()

    import app as app_module
    from tests.ui import _page_explainers as harness
    from tests.ui._browser_server import running_server

    harness.credential_free_environment(
        scratch / "page.env", setattr, lambda n: os.environ.pop(n, None)
    )
    app_module._auth_check_enabled = False
    app_module._csrf_check_enabled = False

    from playwright.sync_api import sync_playwright

    worlds: dict = {}
    kept_rows: dict = {}
    auth_pages: dict = {}
    discord: list = []
    with running_server() as server, sync_playwright() as playwright:
        browser = playwright.firefox.launch(headless=True)
        try:
            for name, hosts in harness.WORLD_HOSTS.items():
                folder = scratch / name
                folder.mkdir()
                os.environ["DB_PATH"] = str(folder / "state.db")
                database.init_db()
                database.run_migrations()
                harness.seed_world(name, folder, setattr)
                client = app_module.app.test_client()
                record = harness.read_server_side(client, folder, setattr)
                if name == "full":
                    kept_rows = harness.read_kept_rows(client)
                    auth_pages = record["auth_pages"]
                    discord = harness.read_discord_payload(folder, setattr)
                record.pop("auth_pages")
                record["hosts"] = {
                    host: harness.read_host_in_browser(browser, server, host) for host in hosts
                }
                worlds[name] = record
            firefox_version = browser.version
        finally:
            browser.close()

    out = {
        "reference_commit": REFERENCE_COMMIT,
        "build_base_commit": BUILD_BASE_COMMIT,
        "head_at_capture": _git("rev-parse", "HEAD").stdout.strip(),
        "captured_at_utc": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "capture_instant": harness.CAPTURE_INSTANT.isoformat(),
        "firefox_version": firefox_version,
        "worlds": worlds,
        "auth_pages": auth_pages,
        "discord_payload": discord,
        "kept_rows": kept_rows,
        "pins": {
            "engine_hashes_at_reference": {
                name: harness.git_object_hash(REFERENCE_COMMIT, name)
                for name in ("alpha_bot_execution.py", "math_engine.py")
            },
            "database_hash_at_build_base": harness.git_object_hash(
                BUILD_BASE_COMMIT, "database.py"
            ),
            "migrations_at_build_base": harness.migration_listing_at(BUILD_BASE_COMMIT),
            "url_map": harness.url_map_facts(),
            "scheduler_digest": harness.scheduler_slot_digest(),
            "handlerless_buttons": harness.handlerless_buttons(),
        },
    }
    target = pathlib.Path(os.environ.get("REF_CAPTURE_OUT", HERE / "ref_capture.json"))
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(out, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    hosts_read = sum(len(w["hosts"]) for w in worlds.values())
    print(
        f"wrote {target.name} at {REFERENCE_COMMIT}: {len(worlds)} worlds, {hosts_read} host "
        f"readings, Firefox {firefox_version}"
    )


if __name__ == "__main__":
    main()
