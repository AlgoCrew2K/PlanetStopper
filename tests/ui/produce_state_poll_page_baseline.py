"""Produces `baseline.json` for the dashboard page from the reference commit.

The reference commit is `REFERENCE_COMMIT` below, not the one `tests/app/_state_poll_worlds.py` names
for the golden `/api/state` bodies (that file is not edited here).

Not collected by default (no `test_` prefix, marker `browser`). The file sits in `tests/ui/` so the
directory conftest applies as it does to `test_state_poll_page_unchanged_browser.py`. To
regenerate, check the reference commit out in a throwaway worktree, copy this file, the capture
module, `tests/app/_state_poll_worlds.py` and `tests/fixtures/state_poll_no_table/` (records) into
the same relative paths, then run there:

    BASELINE_OUT=<existing directory> python -m pytest -m browser -n0         tests/ui/produce_state_poll_page_baseline.py

One run writes one record per scenario of `tests/ui/_state_poll_page_capture.py` plus the Firefox
version into `baseline.json` (each scenario adds its own record to the file, so the first one
starts it afresh). The run refuses unless the production paths equal the reference commit.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

import pytest

from tests.app import _state_poll_worlds as worlds
from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui._shell_support import (  # noqa: F401  (fixtures: the two overrides, empty directory)
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    firefox,
)
from tests.ui._state_poll_page_capture import SCENARIOS, capture_scenario

pytestmark = pytest.mark.browser

FIRST_SCENARIO = next(iter(SCENARIOS))
# The page baseline is read at the commit whose page carries the page descriptions, disclosures and
# the denser layout: its text is what the page gained since the Cash Now removal, and the golden
# producer keeps the poll cleanup reference.
REFERENCE_COMMIT = "ff188e270efab9b4e1a39103c27fc20e2eb5dcf5"


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_write_scenario(firefox, monkeypatch, tmp_path, name):  # noqa: F811
    monkeypatch.setattr(worlds, "REFERENCE_COMMIT", REFERENCE_COMMIT)
    worlds.refuse_unless_production_equals_the_reference()
    out = Path(os.environ["BASELINE_OUT"]) / "baseline.json"
    if name == FIRST_SCENARIO or not out.exists():
        recorded = {
            "reference_commit": REFERENCE_COMMIT,
            "firefox_version": firefox.version,
            "captured_at_utc": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "scenarios": {},
        }
    else:
        recorded = json.loads(out.read_text(encoding="utf-8"))
    recorded["scenarios"][name] = capture_scenario(firefox, name, monkeypatch, tmp_path)
    out.write_text(
        json.dumps(recorded, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
