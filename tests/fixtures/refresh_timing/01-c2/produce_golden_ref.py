"""Produces `golden_ref_api_state_<branch>.json` from the reference commit.

Not collected in this tree (no `test_` prefix). To regenerate, check the reference commit
out in a throwaway worktree, copy this file and `tests/app/_refresh_timing_state_world.py`
and this folder's records into the same relative paths there, then run, in that worktree:

    GOLDEN_OUT=<existing directory> python -m pytest -n0 tests/app/produce_golden_ref.py

Each golden is the whole `/api/state` body the reference commit serves for the seeded world
of one market state (`html` dropped: it is the rendered page, not a figure). The world is
the one the tests in this tree seed, so the reference and the tree under test read the same
stored state, the same shadow rows, the same account totals and the same frozen clock.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.app._refresh_timing_state_world import BRANCHES, seeded_world


@pytest.mark.parametrize("branch", BRANCHES)
def test_write_golden(monkeypatch, tmp_path, branch):
    world = seeded_world(monkeypatch, tmp_path, branch)
    body = world.poll()
    body.pop("html", None)
    out = Path(os.environ["GOLDEN_OUT"]) / f"golden_ref_api_state_{branch}.json"
    out.write_text(
        json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
