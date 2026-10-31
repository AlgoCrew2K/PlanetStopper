"""Capture what the app-shell work compares with its base, from the real pages.

Run on an UNMODIFIED checkout (the script refuses otherwise) to write ``baseline.json`` beside
this file:

    DB_PATH=<scratch>/state.db ATLAS_CACHE_DB_PATH=<scratch>/atlas.db \
        python tests/fixtures/dashboard/shell_baseline/capture_shell_baseline.py

``baseline.json`` holds, from the pages the app served at ``base_sha``:

* ``dashboard.structure_outside_top_bar``: the tag and sorted class tokens of every body element
  of ``GET /`` except the shared header subtree, from the real route over the captured bot state;
* ``dashboard.resolved``: the declarations of every selector that names a dashboard primitive
  class, resolved over the sheets that page loads in document order;
* ``pages_1440``: for each of the five pages at 1440x900 in Firefox, the header (nav) height and,
  for each table in document order, whether the box that holds it scrolls horizontally; for
  ``/settings``, also the request Save sends after the first algorithm parameter is edited to
  ``tests.ui._shell_support.SETTINGS_EDIT_VALUE`` (the request is intercepted in the browser and
  answered there: nothing is written).

The reading code is ``tests/ui/_shell_capture.py`` and
``tests/ui/_dashboard_primitives_capture.py``, the modules the checking tests import. Browser and
server follow the suite's in-process pattern: the app in this process on a scratch database, the
state written through the real writers from captured records, every request to anything but the
server refused. Provenance: ``PROVENANCE.md`` in this directory.
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

WATCHED = ["templates", "static", "app.py"]


def rewrite_structure_only(structure: list, resolved: dict) -> None:
    """Replace only ``dashboard.structure_outside_top_bar``, for a page that grew elements.

    The header measurements and Save's request of ``pages_1440`` are the shell work's base and
    are kept as they are, and so is ``base_sha``. The resolved primitive rules must equal the
    recorded ones (the run stops otherwise: a rule that changed is not a change of structure).
    The recapture is recorded under ``structure_revision`` (the head it was taken at, the time,
    the element counts before and after).
    """
    path = HERE / "baseline.json"
    recorded = json.loads(path.read_text(encoding="utf-8"))
    if recorded["dashboard"]["resolved"] != resolved:
        raise SystemExit("refusing: the resolved primitive rules differ from the recorded ones")
    before = recorded["dashboard"]["structure_outside_top_bar"]
    recorded["dashboard"]["structure_outside_top_bar"] = structure
    recorded["structure_revision"] = {
        "head_sha": subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip(),
        "captured_at_utc": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "elements_before": len(before),
        "elements_after": len(structure),
    }
    path.write_text(json.dumps(recorded, indent=1) + "\n", encoding="utf-8")
    print(
        f"rewrote dashboard.structure_outside_top_bar: {len(before)} -> {len(structure)} elements; "
        f"{len(resolved)} resolved primitive selectors unchanged"
    )


def rewrite_resolved_only(resolved: dict) -> None:
    """Replace only ``dashboard.resolved``, for a primitive a later plan changed on purpose.

    The structure, the header measurements and Save's request are kept as they are, and so is
    ``base_sha``. The revision is recorded under ``resolved_revision`` (the head it was taken at,
    the time, the selector counts before and after).
    """
    path = HERE / "baseline.json"
    recorded = json.loads(path.read_text(encoding="utf-8"))
    before = recorded["dashboard"]["resolved"]
    recorded["dashboard"]["resolved"] = resolved
    recorded["resolved_revision"] = {
        "head_sha": subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip(),
        "captured_at_utc": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "selectors_before": len(before),
        "selectors_after": len(resolved),
    }
    path.write_text(json.dumps(recorded, indent=1) + "\n", encoding="utf-8")
    print(f"rewrote dashboard.resolved: {len(before)} -> {len(resolved)} selectors")


def main() -> None:
    dirty = subprocess.run(
        ["git", "-C", str(REPO), "status", "--porcelain", "--", *WATCHED],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if dirty:
        raise SystemExit(f"refusing to capture: the base files are modified:\n{dirty}")

    scratch = pathlib.Path(tempfile.mkdtemp(prefix="shell_baseline_"))
    os.environ.setdefault("DB_PATH", str(scratch / "state.db"))
    os.environ.setdefault("ATLAS_CACHE_DB_PATH", str(scratch / "atlas.db"))

    import database

    database.init_db()
    database.run_migrations()

    import app as app_module
    from tests.ui import _dashboard_primitives_capture as cap
    from tests.ui import _shell_capture as shell
    from tests.ui import _shell_support as support
    from tests.ui._browser_server import running_server

    html = cap.render_dashboard_html()
    structure = shell.body_structure_outside_top_bar(html)
    if shell.body_structure_outside_top_bar(cap.render_dashboard_html()) != structure:
        raise SystemExit("the dashboard's element structure is not stable across two renders")
    resolved = cap.resolved_primitive_rules(html)

    if "--structure-only" in sys.argv[1:]:
        rewrite_structure_only(structure, resolved)
        return
    if "--resolved-only" in sys.argv[1:]:
        rewrite_resolved_only(resolved)
        return

    env_file = scratch / "shell.env"
    env_file.write_text("", encoding="utf-8")
    app_module.ENV_FILE_PATH = str(env_file)
    app_module._auth_check_enabled = False
    app_module._csrf_check_enabled = False
    support.seed_every_page(scratch)

    from playwright.sync_api import sync_playwright

    pages: dict[str, dict] = {}
    with running_server() as server, sync_playwright() as playwright:
        browser = playwright.firefox.launch(headless=True)
        try:
            for path in support.PAGES:
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                page = context.new_page()
                page.route(
                    "**/*",
                    lambda r: (
                        r.continue_() if r.request.url.startswith(server.base_url) else r.abort()
                    ),
                )
                probe = type(
                    "Probe",
                    (),
                    {"page": page, "server": server, "page_errors": [], "console_errors": []},
                )()
                support.open_page(probe, path)
                page.wait_for_timeout(1500)
                pages[path] = {
                    "nav_height": page.evaluate(shell.NAV_HEIGHT_JS),
                    "tables": page.evaluate(shell.TABLE_OVERFLOW_JS),
                    "browser_version": browser.version,
                }
                if path == "/settings":
                    pages[path]["save_request"] = support.settings_edit_and_save(probe)["request"]
                context.close()
        finally:
            browser.close()

    out = {
        "base_sha": subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip(),
        "captured_at_utc": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dashboard": {"structure_outside_top_bar": structure, "resolved": resolved},
        "pages_1440": pages,
    }
    (HERE / "baseline.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(
        f"wrote baseline.json at {out['base_sha']}: {len(structure)} dashboard body elements "
        f"outside the header, {len(resolved)} resolved primitive selectors; "
        + ", ".join(
            f"{p}: nav {v['nav_height']:.0f}px, {len(v['tables'])} tables" for p, v in pages.items()
        )
    )


if __name__ == "__main__":
    main()
