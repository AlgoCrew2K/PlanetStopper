"""Write the golden bodies of GET /api/guard-alpha-preconditions at the plan's base.

Run from anywhere, with nothing else touching the repository:

    python tests/fixtures/preconditions_replay/capture_goldens.py

For each named state it builds the world through the helper the tests use
(``tests/app/_preconditions_replay_world.golden_world``: a scratch working directory with its own
``cache`` directory and its own database, the ET clock frozen at Sunday 2026-11-15 12:00 through
``frozen_datetime_class`` into ``app.datetime`` and ``autotuner.datetime``), requests the route
through the Flask test client with the auth and CSRF hooks off (as the suite runs them), and
writes the response bytes to ``goldens/<state>.json``. ``full_plus_60s`` is the same world's body
after the clock moves 60 seconds.

It refuses to run when ``app.py``, ``autotuner.py``, ``synthetic_history.py``, ``analytics.py``,
``database.py`` or ``guard_preconditions.py`` differs from the plan's base commit, so a golden is
always the unchanged route's answer and never a later build's.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BASE_SHA = "aafe476d62401f041aeae67c563742f884d8b4af"
GUARDED = (
    "app.py",
    "autotuner.py",
    "synthetic_history.py",
    "analytics.py",
    "database.py",
    "guard_preconditions.py",
)
OUT = Path(__file__).resolve().parent / "goldens"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def main() -> None:
    differing = _git("diff", "--name-only", BASE_SHA, "--", *GUARDED).split()
    if differing:
        raise SystemExit(f"refusing to capture: {differing} differ from the base {BASE_SHA[:8]}")
    head = _git("rev-parse", "HEAD")

    scratch = Path(tempfile.mkdtemp(prefix="preconditions-goldens-"))
    os.environ["DB_PATH"] = str(scratch / "boot.db")
    os.environ["ATLAS_CACHE_DB_PATH"] = str(scratch / "atlas.db")
    sys.path.insert(0, str(REPO))

    import pytest

    import app as app_module
    import database
    from tests.app._preconditions_replay_world import GOLDEN_NAMES, ROUTE, golden_world

    # Outside pytest the dashboard's hooks are on; the suite reads the route with them off.
    app_module._auth_check_enabled = False
    app_module._csrf_check_enabled = False

    OUT.mkdir(exist_ok=True)
    written = []
    for name in GOLDEN_NAMES:
        if name == "full_plus_60s":
            continue
        state_dir = scratch / name
        state_dir.mkdir()
        os.chdir(state_dir)
        os.environ["DB_PATH"] = str(state_dir / "state.db")
        database.init_db()
        with pytest.MonkeyPatch.context() as monkeypatch:
            world = golden_world(name, monkeypatch)
            resp = world.client.get(ROUTE)
            assert resp.status_code == 200, f"{name}: {resp.status_code}"
            (OUT / f"{name}.json").write_bytes(resp.data)
            written.append(name)
            if name == "full":
                world.advance(60)
                later = world.client.get(ROUTE)
                assert later.status_code == 200
                (OUT / "full_plus_60s.json").write_bytes(later.data)
                written.append("full_plus_60s")
    os.chdir(REPO)
    print(f"base {BASE_SHA}, checked out at {head}")
    print(f"wrote {len(written)} goldens to {OUT}: {', '.join(written)}")


if __name__ == "__main__":
    main()
