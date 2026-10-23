"""Committed fixture records must hash, as read from disk, to the digest their sidecar states.

A line-ending rewrite on checkout changes a record's bytes and silently breaks the
provenance claim; this reads the working-copy bytes so that drift fails here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).resolve().parent
SIDECARS = sorted(FIXTURE_DIR.rglob("*.capture.json"))


def test_sidecars_are_discovered():
    assert SIDECARS, "no *.capture.json sidecar found under the fixtures folder"


@pytest.mark.parametrize("sidecar", SIDECARS, ids=lambda p: str(p.relative_to(FIXTURE_DIR)))
def test_record_bytes_on_disk_match_sidecar_sha256(sidecar: Path):
    record = json.loads(sidecar.read_bytes())["record"]
    path = sidecar.parent / record["file"]

    assert path.is_file(), f"{sidecar.name} names a record that is not on disk: {record['file']}"
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == record["sha256"], (
        f"{record['file']} bytes differ from its sidecar digest (line-ending rewrite?)"
    )
    assert len(data) == record["bytes"], f"{record['file']} size differs from its sidecar"
