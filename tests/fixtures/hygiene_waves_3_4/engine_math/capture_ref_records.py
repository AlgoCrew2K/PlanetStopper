"""Capture of the reference records of C1 Batch A (run by ``produce_ref_records.py``).

Not collected by a default run (the file name does not match the collection pattern); the producer
passes it to pytest explicitly so the ``tests/conftest.py`` isolation (temp state database, auth
and CSRF off) applies. Each function writes one JSON record beside this file. A record holds only
math outputs, engine transitions on harness ids, and route outcomes (status or exception type): no
credential, no account id.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import app as app_module
from tests.app._env_flag_sites import BARE_KEY_SITES, bare_key_outcome, refuse_network
from tests.execution._dip_sequence import run_sequences
from tests.execution.test_main_pipeline import patched_environment  # noqa: F401
from tests.execution.test_regime_label_session_read import engine  # noqa: F401
from tests.math_engine._zscore_inputs import record_outputs

HERE = Path(__file__).resolve().parent


def _write(name: str, payload: dict) -> None:
    (HERE / name).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_capture_zscore_outputs():
    _write("zscore_ref_outputs.json", record_outputs())


def test_capture_engine_sequences(engine):  # noqa: F811
    _write("engine_ref_sequences.json", run_sequences(engine))


@pytest.mark.parametrize("site", BARE_KEY_SITES)
def test_capture_bare_key_outcome(site, monkeypatch, tmp_path):
    refuse_network(monkeypatch)
    app_module.app.config["TESTING"] = True
    path = HERE / "env_flag_bare_key_outcomes.json"
    record = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    with app_module.app.test_client() as client:
        record[site] = bare_key_outcome(site, monkeypatch, client, tmp_path)
    _write(path.name, record)
