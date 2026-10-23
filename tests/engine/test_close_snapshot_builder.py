"""The engine's EOD close-snapshot builder writes only symphonies, and the other
writers of the stored state keep a flat snapshot flat.

Harness: the real engine ``main()`` against a real temporary state database
(``tests/conftest.py`` points ``DB_PATH`` at a per-test file), so every assertion reads
the stored row the engine's own ``database.save_state`` wrote. Only the network
boundaries (Composer, Alpaca), the clock, reporting and the autotuner are patched.
Inputs are the captured production row and the records derived from it (see
``tests/fixtures/refresh_timing/01-c1/PROVENANCE.md``); every expected value is
recomputed from those records or read from the golden, never typed.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

import alpha_bot_execution
import analytics
import database

_RECORDS = Path(__file__).resolve().parents[1] / "fixtures" / "refresh_timing" / "01-c1"
_ET = ZoneInfo("America/New_York")
_CLOSE_DAY = "2026-10-23"  # a Friday, a trading day on the real XNYS calendar
_EOD_CLOCK = datetime(2026, 10, 23, 16, 2, 0, tzinfo=_ET)
_SATURDAY_NOON = datetime(2026, 10, 24, 12, 0, 0, tzinfo=_ET)
_MIDDAY_CLOCK = datetime(2026, 10, 23, 11, 30, 0, tzinfo=_ET)
_PHANTOM_ID = "last_market_close_snapshot"
_SNAPSHOT_KEY = "last_market_close_snapshot"


def load_record_text(name: str) -> str:
    """The verbatim stored row text of a gzipped record in the 01-c1 fixture folder."""
    return gzip.decompress((_RECORDS / name).read_bytes()).decode("utf-8")


def symphony_entries(state: dict) -> dict:
    """The symphony entries of a stored state: dict values that carry a name."""
    return {k: v for k, v in state.items() if isinstance(v, dict) and "name" in v}


def state_for_day(record_name: str, day: str) -> dict:
    """A record as the engine holds it on day ``day``: its first tick of the day has
    already set ``date``, so the new-day wipe does not run at the close."""
    state = json.loads(load_record_text(record_name))
    state["date"] = day
    return state


def write_stored_state(state: dict) -> None:
    conn = sqlite3.connect(database._db_file())
    conn.execute("UPDATE bot_state SET data = ? WHERE id = 1", (json.dumps(state),))
    conn.commit()
    conn.close()


def read_stored_state() -> dict:
    conn = sqlite3.connect(database._db_file())
    raw = conn.execute("SELECT data FROM bot_state WHERE id = 1").fetchone()[0]
    conn.close()
    return json.loads(raw)


def account_of_symphonies(state: dict) -> str:
    """An account id carried by the stored symphony entries (the cycle fans out over it)."""
    return next(e["account"] for e in symphony_entries(state).values() if "account" in e)


def composer_payloads(state: dict) -> list[dict]:
    """Composer-shaped fetch results for the symphonies of ``state`` that carry an
    account, built from each entry's own fields so the cycle reports every symphony
    where it already stands. An entry with no account is one Composer did not list
    under any account, so it is not fetched and the cycle never assigns it one."""
    return [
        {
            "id": sym_id,
            "symphony_id": sym_id,
            "name": entry["name"],
            "last_percent_change": entry["current_return"] / 100.0,
            "current_value": entry["current_value"],
            "holdings": [
                {"ticker": h["ticker"], "allocation": h["allocation"]}
                for h in entry.get("current_holdings", [])
            ],
        }
        for sym_id, entry in symphony_entries(state).items()
        if "account" in entry
    ]


def walk_keys(value) -> set[str]:
    """Every dict key at every depth (iterative: the captured chain is 260 levels deep)."""
    keys: set[str] = set()
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            keys.update(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return keys


def count_snapshot_shaped_dicts(value) -> int:
    """Dicts carrying ``captured_at_et``: the snapshots a chain is made of."""
    count = 0
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            count += "captured_at_et" in item
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return count


def expected_accounts_map(stored_after: dict) -> dict:
    """What the account map must hold: each symphony entry as stored after the cycle,
    under its account (``Unknown`` when it carries none), in stored key order."""
    expected: dict = {}
    for sym_id, entry in symphony_entries(stored_after).items():
        expected.setdefault(entry.get("account", "Unknown"), []).append({"id": sym_id, **entry})
    return expected


@pytest.fixture()
def engine_cycle(tmp_path, monkeypatch):
    """Run one engine ``main()`` at a chosen ET instant over the stored state and
    return the stored state afterwards. The Composer fan-out reports every symphony
    of the stored state where it stands."""
    pm_dir = tmp_path / "post_mortems"
    monkeypatch.setattr(analytics, "_POST_MORTEMS_DIR", str(pm_dir))
    monkeypatch.setattr(analytics, "DB_FILE", database._db_file())

    def run(clock: datetime, *, force: bool = False) -> dict:
        stored = read_stored_state()
        history = {clock.strftime("%Y-%m-%d"): {"SPY": {"c": 500.0, "daily_ret": 0.001}}}
        with (
            patch.object(alpha_bot_execution, "get_current_et", return_value=clock),
            patch.object(
                alpha_bot_execution, "fetch_symphony_stats", return_value=composer_payloads(stored)
            ),
            patch.object(alpha_bot_execution, "fetch_alpaca_history", return_value=history),
            patch.object(alpha_bot_execution, "fetch_intraday_vwaps", return_value={}),
            patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", [account_of_symphonies(stored)]),
            patch.object(alpha_bot_execution, "COMPOSER_KEY_ID", "key"),
            patch.object(alpha_bot_execution, "ALPACA_KEY", "key"),
            patch.object(alpha_bot_execution, "LIVE_EXECUTION", False),
            patch.object(alpha_bot_execution, "reporting", MagicMock()),
            patch.object(alpha_bot_execution.autotuner, "run_autotuner", return_value=None),
            patch.object(alpha_bot_execution.time, "sleep"),
            patch.object(
                alpha_bot_execution.sys,
                "argv",
                ["alpha_bot_execution.py"] + (["--force"] if force else []),
            ),
        ):
            alpha_bot_execution.main()
        return read_stored_state()

    run.post_mortem_path = pm_dir / f"post_mortem_{_CLOSE_DAY}.json"
    return run


def eod_observation(after: dict, post_mortem_path: Path) -> dict:
    """What an EOD step leaves behind, apart from the account map's content: the
    snapshot's own fields, the day markers, every other stored key and the
    post-mortem file."""
    snapshot = after[_SNAPSHOT_KEY]
    return {
        "snapshot_without_accounts_map": {k: v for k, v in snapshot.items() if k != "accounts_map"},
        "post_mortem_run": after["post_mortem_run"],
        "last_successful_cycle_at": after["last_successful_cycle_at"],
        "state_without_snapshot": {k: v for k, v in after.items() if k != _SNAPSHOT_KEY},
        "post_mortem_file": json.loads(post_mortem_path.read_text(encoding="utf-8")),
    }


class TestEodStepWritesOnlySymphonies:
    def test_nested_chain_is_not_carried_into_the_new_snapshot(self, engine_cycle):
        before = state_for_day("nested_bot_state_row.json.gz", _CLOSE_DAY)
        write_stored_state(before)

        after = engine_cycle(_EOD_CLOCK)

        snapshot = after[_SNAPSHOT_KEY]
        assert snapshot["trading_day"] == _CLOSE_DAY, "the EOD step did not write day D's snapshot"
        listed = [entry for entries in snapshot["accounts_map"].values() for entry in entries]
        assert sorted(entry["id"] for entry in listed) == sorted(symphony_entries(before)), (
            "the account map must list exactly the symphony entries of bot_state"
        )
        assert _PHANTOM_ID not in {entry["id"] for entry in listed}
        assert _SNAPSHOT_KEY not in walk_keys(snapshot)
        assert count_snapshot_shaped_dicts(after) == 1, "an earlier snapshot is nested in day D's"

    def test_account_map_holds_each_symphony_entry_exactly_as_stored(self, engine_cycle):
        write_stored_state(state_for_day("nested_bot_state_row.json.gz", _CLOSE_DAY))

        after = engine_cycle(_EOD_CLOCK)

        assert after[_SNAPSHOT_KEY]["accounts_map"] == expected_accounts_map(after)

    def test_flat_stored_snapshot_stays_flat_when_the_next_day_is_written(self, engine_cycle):
        before = state_for_day("flat_bot_state_row.json.gz", _CLOSE_DAY)
        write_stored_state(before)
        assert count_snapshot_shaped_dicts(before) == 1

        after = engine_cycle(_EOD_CLOCK)

        assert after[_SNAPSHOT_KEY]["trading_day"] == _CLOSE_DAY
        assert count_snapshot_shaped_dicts(after) == 1, (
            "the stored row carries the previous day's snapshot inside day D's"
        )
        assert after[_SNAPSHOT_KEY]["accounts_map"] == expected_accounts_map(after)

    def test_symphony_with_no_account_is_kept_under_unknown(self, engine_cycle):
        before = state_for_day("symphony_without_account_bot_state_row.json.gz", _CLOSE_DAY)
        write_stored_state(before)
        no_account = [k for k, v in symphony_entries(before).items() if "account" not in v]
        assert len(no_account) == 1

        after = engine_cycle(_EOD_CLOCK)

        accounts_map = after[_SNAPSHOT_KEY]["accounts_map"]
        assert [entry["id"] for entry in accounts_map["Unknown"]] == no_account, (
            "the real symphony with no account must be the only entry under Unknown"
        )
        assert accounts_map == expected_accounts_map(after)

    def test_dict_valued_key_with_no_name_is_not_in_the_account_map(self, engine_cycle):
        before = state_for_day("dict_without_name_bot_state_row.json.gz", _CLOSE_DAY)
        write_stored_state(before)
        no_name_dict_keys = [
            k
            for k, v in before.items()
            if isinstance(v, dict) and "name" not in v and k != _SNAPSHOT_KEY
        ]
        assert no_name_dict_keys, "the record must hold a dict-valued key with no name"

        after = engine_cycle(_EOD_CLOCK)

        listed_ids = {
            entry["id"]
            for entries in after[_SNAPSHOT_KEY]["accounts_map"].values()
            for entry in entries
        }
        assert listed_ids.isdisjoint(no_name_dict_keys)
        assert listed_ids == set(symphony_entries(before))
        for key in no_name_dict_keys:
            assert after[key] == before[key], "the EOD step must leave that key as it found it"


class TestEodStepKeepsEverythingElse:
    def test_eod_output_equals_the_reference_golden(self, engine_cycle):
        golden = json.loads((_RECORDS / "golden_ref_eod_snapshot.json").read_text(encoding="utf-8"))
        write_stored_state(state_for_day("nested_bot_state_row.json.gz", _CLOSE_DAY))

        after = engine_cycle(_EOD_CLOCK)

        assert eod_observation(after, engine_cycle.post_mortem_path) == golden["observation"]

    def test_second_eod_run_on_the_same_day_keeps_the_first_snapshot(self, engine_cycle):
        write_stored_state(state_for_day("flat_bot_state_row.json.gz", _CLOSE_DAY))
        first = engine_cycle(_EOD_CLOCK)

        second = engine_cycle(_EOD_CLOCK, force=True)

        assert first["post_mortem_run"] == _CLOSE_DAY
        assert second[_SNAPSHOT_KEY] == first[_SNAPSHOT_KEY]


class TestRepairedSnapshotThroughTheOtherWriters:
    """A flat stored snapshot stays flat and byte-equal through every other writer of
    the stored state, until the next EOD step writes a new flat one."""

    @staticmethod
    def _flat_row() -> dict:
        return state_for_day("flat_bot_state_row.json.gz", _CLOSE_DAY)

    def test_flush_resync_reset_keeps_the_snapshot(self, tmp_path, monkeypatch):
        import app as app_module

        before = self._flat_row()
        write_stored_state(before)
        monkeypatch.setattr(analytics, "_POST_MORTEMS_DIR", str(tmp_path / "pm"))
        monkeypatch.setattr(app_module, "_refresh_account_totals", lambda *a, **k: None)
        app_module.app.config["TESTING"] = True

        with app_module.app.test_client() as client:
            response = client.post("/api/settings/flush-resync")
        app_module._DISMISS_EXECUTOR.submit(lambda: None).result(timeout=30)  # drain the reset

        assert response.status_code == 200
        after = read_stored_state()
        assert all(set(e) <= {"name", "account"} for e in symphony_entries(after).values()), (
            "the background reset did not save bot_state, so this test proves nothing"
        )
        assert json.dumps(after[_SNAPSHOT_KEY]) == json.dumps(before[_SNAPSHOT_KEY])

    def test_startup_seed_leaves_a_seeded_state_byte_equal(self, monkeypatch):
        before = self._flat_row()
        write_stored_state(before)
        fetch = MagicMock(return_value=[])
        monkeypatch.setattr(alpha_bot_execution, "fetch_symphony_stats", fetch)
        monkeypatch.setattr(alpha_bot_execution, "ACCOUNT_UUIDS", [account_of_symphonies(before)])

        alpha_bot_execution.ensure_bot_state_seeded()

        fetch.assert_not_called()
        assert read_stored_state() == before

    def test_startup_seed_adds_no_entry_for_a_reserved_key(self, monkeypatch):
        full = self._flat_row()
        symphonies = symphony_entries(full)
        before = {k: v for k, v in full.items() if k not in symphonies}
        write_stored_state(before)
        seeded = {"id": "seeded-sym", "name": "Seeded", "last_percent_change": 0.01}
        monkeypatch.setattr(alpha_bot_execution, "fetch_symphony_stats", lambda a: [seeded])
        monkeypatch.setattr(alpha_bot_execution, "ACCOUNT_UUIDS", [account_of_symphonies(full)])

        alpha_bot_execution.ensure_bot_state_seeded()

        after = read_stored_state()
        assert set(after) - set(before) == {seeded["id"]}
        assert json.dumps(after[_SNAPSHOT_KEY]) == json.dumps(before[_SNAPSHOT_KEY])
        assert all(after[k] == before[k] for k in before)

    def test_engine_closed_path_save_keeps_the_snapshot(self, engine_cycle):
        before = self._flat_row()
        write_stored_state(before)

        after = engine_cycle(_SATURDAY_NOON)

        assert json.dumps(after[_SNAPSHOT_KEY]) == json.dumps(before[_SNAPSHOT_KEY])

    def test_engine_per_cycle_save_keeps_the_snapshot(self, engine_cycle):
        before = state_for_day("flat_bot_state_row.json.gz", _CLOSE_DAY)
        write_stored_state(before)

        after = engine_cycle(_MIDDAY_CLOCK)

        assert after["last_successful_cycle_at"].startswith(_CLOSE_DAY), "no cycle save happened"
        assert json.dumps(after[_SNAPSHOT_KEY]) == json.dumps(before[_SNAPSHOT_KEY])
