"""``sleeves.tick_orchestrator._compute_live_gates`` flag parse (hygiene-waves-3-4, AC-3, S-3.9).

``live_mode`` is true only for a sleeve whose stored status is ``LIVE`` AND whose
``SLEEVE_LIVE_EXECUTION`` environment variable parses true (``tests/_env_flag_spellings``, the
reference expression copied verbatim). The environment is read only for a ``LIVE`` row: a ``PAPER``
or ``SHADOW`` row short-circuits before the read, and that call order is pinned by counting
``os.getenv`` calls for the flag per row status.

Rows are written by the real sleeve writers into the per-test database that ``tests/conftest.py``
isolates; ``SLEEVE_LIVE_EXECUTION`` and the live-key variables are set by monkeypatch. These cases
pass on the reference code by design (characterization).
"""

from __future__ import annotations

import os

import pytest

import database
from sleeves import tick_orchestrator
from tests._env_flag_spellings import ABSENT, ALL_CASES, case_id, ref_is_true

_KEY_VARIABLES = ("ALPACA_LIVE_KEY", "ALPACA_LIVE_SECRET")


def _sleeve_row(status: str, name: str) -> dict:
    sleeve_id = database.create_sleeve(name, 5000.0, envelope_json="{}")
    if status != "SHADOW":
        database.update_sleeve_status(sleeve_id, status)
    row = database.get_sleeve(sleeve_id)
    assert row["status"] == status
    return row


def _set_flag(monkeypatch, value) -> None:
    if value is ABSENT:
        monkeypatch.delenv("SLEEVE_LIVE_EXECUTION", raising=False)
    else:
        monkeypatch.setenv("SLEEVE_LIVE_EXECUTION", value)


class TestComputeLiveGatesFlagParse:
    @pytest.mark.parametrize("status", ["LIVE", "PAPER", "SHADOW"])
    @pytest.mark.parametrize("value", ALL_CASES, ids=case_id)
    def test_live_mode_is_status_live_and_the_flag_parse_of_the_reference(
        self, monkeypatch, status, value
    ):
        row = _sleeve_row(status, f"gate-{status}-{case_id(value)}")
        _set_flag(monkeypatch, value)
        flag = "False" if value is ABSENT else value
        live_mode, _keys = tick_orchestrator._compute_live_gates(row)
        assert live_mode is (status == "LIVE" and ref_is_true(flag))

    @pytest.mark.parametrize("present", [True, False])
    def test_live_keys_present_follows_both_key_variables_whatever_the_flag(
        self, monkeypatch, present
    ):
        row = _sleeve_row("LIVE", f"gate-keys-{present}")
        monkeypatch.setenv("SLEEVE_LIVE_EXECUTION", "true")
        for variable in _KEY_VARIABLES:
            if present:
                monkeypatch.setenv(variable, "test-live-value")
            else:
                monkeypatch.delenv(variable, raising=False)
        assert tick_orchestrator._compute_live_gates(row)[1] is present

    @pytest.mark.parametrize(
        ("status", "reads_expected"), [("LIVE", 1), ("PAPER", 0), ("SHADOW", 0)]
    )
    def test_flag_is_read_from_the_environment_only_for_a_live_row(
        self, monkeypatch, status, reads_expected
    ):
        row = _sleeve_row(status, f"gate-reads-{status}")
        _set_flag(monkeypatch, "true")
        reads: list = []
        real_getenv = os.getenv

        def _counting_getenv(key, *args, **kwargs):
            if key == "SLEEVE_LIVE_EXECUTION":
                reads.append(key)
            return real_getenv(key, *args, **kwargs)

        monkeypatch.setattr(tick_orchestrator.os, "getenv", _counting_getenv)
        tick_orchestrator._compute_live_gates(row)
        assert len(reads) == reads_expected
