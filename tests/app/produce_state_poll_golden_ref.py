"""Produces `golden_ref_<case>.json` from the reference commit. Not collected (no `test_` prefix).

The file sits in `tests/app/` so that the directory conftest applies to it exactly as it applies
to `test_api_state_without_table.py` (the scheduler run state each test starts from decides the
body's `degraded_reasons` and `engine_phase`).

To regenerate later, check the reference commit out in a throwaway worktree, copy this file,
`tests/app/_state_poll_worlds.py` and `tests/fixtures/state_poll_no_table/` (records) into the same
relative paths there, then run, in that worktree:

    GOLDEN_OUT=<existing directory> python -m pytest -n0 tests/app/produce_state_poll_golden_ref.py

Each golden is the whole `/api/state` body the reference commit serves for one named world and
request (`GOLDEN_CASES` of the worlds module), with `html` dropped: it is the table the cleanup
retires, not a figure. The run refuses unless the production paths equal the reference commit.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.app._state_poll_worlds import (
    GOLDEN_CASES,
    WORLDS,
    refuse_unless_production_equals_the_reference,
)


@pytest.mark.parametrize("case", list(GOLDEN_CASES))
def test_write_golden(monkeypatch, tmp_path, case):
    refuse_unless_production_equals_the_reference()
    world_name, request_for = GOLDEN_CASES[case]
    built = WORLDS[world_name](monkeypatch, tmp_path)
    body = built.body(request_for(built))
    body.pop("html", None)
    out = Path(os.environ["GOLDEN_OUT"]) / f"golden_ref_{case}.json"
    out.write_text(
        json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
