"""Write ``ref_capture.json``: what the five pages show, request and pin at the reference commit.

Run ONCE per reference, in a checkout whose production paths equal the reference commit (the script
refuses otherwise, and refuses when any of them has an uncommitted change). Before any production
file changes: a capture written after one has changed would make every comparison agree with the
head it was taken at. From the repository root:

    python tests/fixtures/page_explainers/produce_ref_capture.py

State is written through the real writers from captured records (``seed_world``), the app runs in
this process on a scratch database, every request to anything but that server is refused, and the
developer ``.env`` is never read. The reading code is ``tests/ui/_page_explainers.py``, the module
the checks import, so a difference between this record and a head is a difference between the trees
and never between two readers. Provenance: ``PROVENANCE.md`` in this directory.

What the record holds, per seeded world and host: the figure-element texts and the control
inventory at 1920, 1440 and 390 px, the stacked-row form of every table at 1000, 390 and 320 px, the
section-heading order, the banner markup, the login and logout pages, the bodies of the read-only
routes; once: one Discord payload, the content pins of the engine files, ``database.py`` and
``advisors/tenure_review.py``, the migration listing, the hashes of the no-trade-boundary test files,
the route table and the scheduler digest, the buttons no script handles, the rendered text of the
kept rows, and the Firefox version. The document heights of an earlier capture are not recorded.
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

REFERENCE_COMMIT = "c0760e70"
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
    "advisors/tenure_review.py",
    "migrations",
)
# Every production path equals the reference: nothing is allowed to differ.
ALLOWED_TO_DIFFER: tuple[str, ...] = ()


def _git_succeeds(*args: str) -> bool:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True).returncode == 0


def _git_head() -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def refuse_unless_production_equals_the_reference() -> None:
    checked = [p for p in PRODUCTION_PATHS if p not in ALLOWED_TO_DIFFER]
    if not _git_succeeds("diff", "--quiet", REFERENCE_COMMIT, "--", *checked):
        raise SystemExit(f"refusing to write: {checked} differ from {REFERENCE_COMMIT}")
    dirty = subprocess.run(
        ["git", "-C", str(REPO), "status", "--porcelain", "--", *PRODUCTION_PATHS],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if dirty:
        raise SystemExit(f"refusing to write: production paths have uncommitted changes: {dirty}")


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
        "head_at_capture": _git_head(),
        "captured_at_utc": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "capture_instant": harness.CAPTURE_INSTANT.isoformat(),
        "firefox_version": firefox_version,
        "worlds": worlds,
        "auth_pages": auth_pages,
        "discord_payload": discord,
        "kept_rows": kept_rows,
        "pins": {
            "hashes_at_reference": {
                name: harness.git_object_hash(REFERENCE_COMMIT, name)
                for name in harness.PINNED_FILES
            },
            "migrations_at_reference": harness.migration_listing_at(REFERENCE_COMMIT),
            "protected_test_hashes_at_reference": harness.protected_test_hashes_at(
                REFERENCE_COMMIT
            ),
            "route_table": harness.route_table_facts(),
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
