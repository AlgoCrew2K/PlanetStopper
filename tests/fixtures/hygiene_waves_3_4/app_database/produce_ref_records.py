"""Record the URL map, hook names, endpoint sets, migration digests and fresh-schema SQL at the base.

Run once at RED, from the repository root:

    python tests/fixtures/hygiene_waves_3_4/app_database/produce_ref_records.py

It refuses unless the production files it reads equal the base commit, so a record can only
describe the base. It writes ``url_map.json`` and ``schema.json`` beside itself and records no
row, no environment value and no credential: structure only (see PROVENANCE.md).
"""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[3]
BASE_SHA = "7adc2c56953a3873c013425ebb06033375b62c53"
WATCHED = ("app.py", "database.py", "analytics.py", "migrations", "sleeves")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)


def refuse_unless_production_files_equal_the_base() -> None:
    if _git("diff", "--quiet", BASE_SHA, "--", *WATCHED).returncode != 0:
        raise SystemExit(f"refusing: {WATCHED} differ from {BASE_SHA}")
    dirty = _git("status", "--porcelain", "--", *WATCHED).stdout.strip()
    if dirty:
        raise SystemExit(f"refusing: {WATCHED} have uncommitted changes")


def main() -> None:
    refuse_unless_production_files_equal_the_base()
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="hygiene_surface_ref_"))
    os.environ["DB_PATH"] = str(scratch / "state.db")
    os.environ.setdefault("ATLAS_CACHE_DB_PATH", str(scratch / "atlas.db"))
    sys.path.insert(0, str(REPO))

    import database

    database.init_db()
    database.run_migrations()

    import app as app_module
    from tests import _hygiene_surface_facts as facts

    url_map = {"base_sha": BASE_SHA, **facts.url_map_facts(app_module)}
    conn = sqlite3.connect(f"file:{scratch / 'state.db'}?mode=ro", uri=True)
    try:
        schema = {"base_sha": BASE_SHA, **facts.schema_facts(database, conn)}
    finally:
        conn.close()

    for name, payload in (("url_map.json", url_map), ("schema.json", schema)):
        (HERE / name).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
    print(f"wrote {len(url_map['rules'])} rules and {len(schema['sqlite_master'])} schema entries")


if __name__ == "__main__":
    main()
