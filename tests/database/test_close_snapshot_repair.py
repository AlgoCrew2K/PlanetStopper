"""The one-time repair of the stored close-snapshot chain, run at daemon start.

``database.repair_close_snapshot_chain()`` rewrites ``bot_state.last_market_close_snapshot``
without its nested earlier snapshots, after saving a backup file of the pre-repair row,
and returns an outcome token. Inputs are the captured production row (86 snapshots deep)
and the records derived from it; every expected row is recomputed from the captured
text, never typed. The stored row is a real row in the per-test temporary state database.

Seams the implementation must keep (patched by these tests):
  * ``database.acquire_lock`` / ``database.release_lock`` are called as module globals.
  * ``database._snapshot_repair_backup_path()`` is called to name the backup file.
  * ``database._SNAPSHOT_REPAIR_LOCK_WAIT_S`` and ``database._SNAPSHOT_REPAIR_DB_TIMEOUT_S``
    are read at call time.
  * the backup is flushed with ``os.fsync``.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import alpha_bot_execution
import database
from tests.engine.test_close_snapshot_builder import (
    count_snapshot_shaped_dicts,
    load_record_text,
    read_stored_state,
    symphony_entries,
    walk_keys,
    write_stored_state,
)

_RECORDS = Path(__file__).resolve().parents[1] / "fixtures" / "refresh_timing" / "01-c1"
_APP = Path(__file__).resolve().parents[2] / "app.py"
_SNAPSHOT_KEY = "last_market_close_snapshot"
_OUTCOMES = {
    "not_needed",
    "repaired",
    "engine_busy",
    "backup_failed",
    "write_failed",
    "not_byte_stable",
}
_NESTED = "nested_bot_state_row.json.gz"
_FLAT = "flat_bot_state_row.json.gz"


def read_raw_row() -> str:
    conn = sqlite3.connect(database._db_file())
    raw = conn.execute("SELECT data FROM bot_state WHERE id = 1").fetchone()[0]
    conn.close()
    return raw


def write_raw_row(text: str) -> None:
    conn = sqlite3.connect(database._db_file())
    conn.execute("UPDATE bot_state SET data = ? WHERE id = 1", (text,))
    conn.commit()
    conn.close()


def backup_files() -> list[Path]:
    db_path = Path(database._db_file())
    return sorted(db_path.parent.glob(f"{db_path.name}.pre-snapshot-repair-*.backup"))


def expected_repaired_text(raw: str) -> str:
    """The repaired row, recomputed from the stored text: the top snapshot's account map
    keeps only entries that carry a name and drops a key emptied by that removal."""
    state = json.loads(raw)
    accounts_map = state[_SNAPSHOT_KEY]["accounts_map"]
    for account in list(accounts_map):
        kept = [e for e in accounts_map[account] if "name" in e]
        if kept:
            accounts_map[account] = kept
        elif accounts_map[account]:
            del accounts_map[account]
    return json.dumps(state)


@pytest.fixture()
def update_counter():
    """Count every UPDATE on bot_state, whatever the written content (a trigger)."""
    conn = sqlite3.connect(database._db_file())
    conn.execute("CREATE TABLE IF NOT EXISTS _bot_state_writes (n INTEGER)")
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS _count_bot_state_writes AFTER UPDATE ON bot_state "
        "BEGIN INSERT INTO _bot_state_writes VALUES (1); END"
    )
    conn.commit()
    conn.close()

    def count() -> int:
        c = sqlite3.connect(database._db_file())
        n = c.execute("SELECT COUNT(*) FROM _bot_state_writes").fetchone()[0]
        c.close()
        return n

    return count


def lock_is_held() -> bool:
    conn = sqlite3.connect(database._db_file())
    held = conn.execute("SELECT is_locked FROM execution_lock WHERE id = 1").fetchone()[0]
    conn.close()
    return bool(held)


def error_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


class TestCapturedRecordIsUsable:
    def test_captured_row_nests_earlier_snapshots_and_round_trips_byte_for_byte(self):
        text = load_record_text(_NESTED)
        sidecar = json.loads((_RECORDS / "nested_bot_state_row.capture.json").read_text("utf-8"))

        assert hashlib.sha256(text.encode("utf-8")).hexdigest() == sidecar["facts"]["raw_sha256"]
        assert json.dumps(json.loads(text)) == text
        assert count_snapshot_shaped_dicts(json.loads(text)) >= 2

    def test_derived_records_equal_their_recorded_derivation(self):
        nested = json.loads(load_record_text(_NESTED))
        flat = json.loads(load_record_text(_FLAT))
        expected_flat = json.loads(load_record_text(_NESTED))
        del expected_flat[_SNAPSHOT_KEY]["accounts_map"]["Unknown"]
        no_snapshot = json.loads(load_record_text("no_snapshot_bot_state_row.json.gz"))
        without_account = json.loads(
            load_record_text("symphony_without_account_bot_state_row.json.gz")
        )
        with_alert = json.loads(load_record_text("dict_without_name_bot_state_row.json.gz"))
        alert_shape = json.loads(
            (
                _RECORDS.parents[2]
                / "fixtures"
                / "engine"
                / "fleet_correlation"
                / "alert_state_shape.json"
            ).read_text("utf-8")
        )

        assert flat == expected_flat
        assert no_snapshot == {k: v for k, v in nested.items() if k != _SNAPSHOT_KEY}
        changed = [k for k, v in symphony_entries(flat).items() if without_account[k] != v]
        assert len(changed) == 1
        assert without_account[changed[0]] == {
            k: v for k, v in flat[changed[0]].items() if k != "account"
        }
        assert {k: v for k, v in without_account.items() if k != changed[0]} == {
            k: v for k, v in flat.items() if k != changed[0]
        }
        assert with_alert["fleet_correlation_alert"] == alert_shape["example"]
        assert {k: v for k, v in with_alert.items() if k != "fleet_correlation_alert"} == flat


class TestRepairFlattensTheStoredChain:
    def test_captured_chain_is_flattened_and_everything_else_is_byte_equal(self):
        raw = load_record_text(_NESTED)
        write_raw_row(raw)

        outcome = database.repair_close_snapshot_chain()

        assert outcome == "repaired"
        after_raw = read_raw_row()
        before, after = json.loads(raw), json.loads(after_raw)
        assert count_snapshot_shaped_dicts(after) == 1
        assert _SNAPSHOT_KEY not in walk_keys(after[_SNAPSHOT_KEY])
        assert "Unknown" not in after[_SNAPSHOT_KEY]["accounts_map"]
        for field in (
            "trading_day",
            "captured_at_et",
            "data_as_of",
            "portfolio_strip",
            "shadow_divergence",
        ):
            assert json.dumps(after[_SNAPSHOT_KEY][field]) == json.dumps(
                before[_SNAPSHOT_KEY][field]
            ), f"snapshot field {field!r} changed"
        for sym_id, entry in symphony_entries(before).items():
            assert json.dumps(after[sym_id]) == json.dumps(entry)
        for key in before.keys() - {_SNAPSHOT_KEY}:
            assert json.dumps(after[key]) == json.dumps(before[key]), f"key {key!r} changed"
        assert after_raw == expected_repaired_text(raw)
        assert after_raw == load_record_text(_FLAT), (
            "must equal the recorded flat row byte for byte"
        )

    def test_symphony_entries_with_a_name_stay_under_unknown_and_empty_lists_stay(self):
        state = json.loads(load_record_text(_NESTED))
        accounts_map = state[_SNAPSHOT_KEY]["accounts_map"]
        real_account = next(a for a in accounts_map if a != "Unknown")
        kept_entry = dict(accounts_map[real_account][0])
        accounts_map["Unknown"] = [accounts_map["Unknown"][0], kept_entry]
        accounts_map["EmptyAccount"] = []
        raw = json.dumps(state)
        write_raw_row(raw)

        assert database.repair_close_snapshot_chain() == "repaired"

        after = json.loads(read_raw_row())[_SNAPSHOT_KEY]["accounts_map"]
        assert after["Unknown"] == [kept_entry]
        assert after["EmptyAccount"] == []
        assert read_raw_row() == expected_repaired_text(raw)

    def test_flatten_helper_reports_the_removed_entry_count(self):
        nested = json.loads(load_record_text(_NESTED))
        flat = json.loads(load_record_text(_FLAT))

        repaired, removed = database._flatten_close_snapshot(nested)
        untouched, none_removed = database._flatten_close_snapshot(flat)

        assert removed == 1
        assert json.dumps(repaired) == load_record_text(_FLAT)
        assert none_removed == 0
        assert untouched == flat

    @pytest.mark.parametrize(
        "record",
        [
            "no_snapshot_bot_state_row.json.gz",
            "flat_bot_state_row.json.gz",
            "symphony_without_account_bot_state_row.json.gz",
            "dict_without_name_bot_state_row.json.gz",
        ],
    )
    def test_state_with_nothing_to_repair_is_neither_backed_up_nor_written(
        self, record, update_counter
    ):
        raw = load_record_text(record)
        write_raw_row(raw)
        writes_before = update_counter()

        outcome = database.repair_close_snapshot_chain()

        assert outcome == "not_needed"
        assert read_raw_row() == raw
        assert update_counter() == writes_before, (
            "the row was written although nothing needed repair"
        )
        assert backup_files() == []

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda s: s.__setitem__(_SNAPSHOT_KEY, "not-a-dict"),
            lambda s: s[_SNAPSHOT_KEY].pop("accounts_map"),
            lambda s: s[_SNAPSHOT_KEY].__setitem__("accounts_map", {"Unknown": "not-a-list"}),
            lambda s: s[_SNAPSHOT_KEY].__setitem__("accounts_map", ["not-a-dict"]),
        ],
        ids=["snapshot-not-a-dict", "no-account-map", "account-value-not-a-list", "map-not-a-dict"],
    )
    def test_malformed_snapshot_is_left_alone_and_never_raises(self, mutate, update_counter):
        state = json.loads(load_record_text(_FLAT))
        mutate(state)
        raw = json.dumps(state)
        write_raw_row(raw)
        writes_before = update_counter()

        outcome = database.repair_close_snapshot_chain()

        assert outcome == "not_needed"
        assert read_raw_row() == raw
        assert update_counter() == writes_before
        assert backup_files() == []

    def test_second_run_after_a_repair_changes_nothing_and_saves_no_second_backup(
        self, update_counter
    ):
        write_raw_row(load_record_text(_NESTED))
        assert database.repair_close_snapshot_chain() == "repaired"
        repaired_raw = read_raw_row()
        writes_after_first = update_counter()
        backups_after_first = [p.read_bytes() for p in backup_files()]

        second = database.repair_close_snapshot_chain()

        assert second == "not_needed"
        assert read_raw_row() == repaired_raw
        assert update_counter() == writes_after_first
        assert [p.read_bytes() for p in backup_files()] == backups_after_first
        assert len(backups_after_first) == 1

    def test_row_the_json_round_trip_would_change_is_refused(self, update_counter):
        compact = json.dumps(json.loads(load_record_text(_NESTED)), separators=(",", ":"))
        assert json.dumps(json.loads(compact)) != compact
        write_raw_row(compact)
        writes_before = update_counter()

        outcome = database.repair_close_snapshot_chain()

        assert outcome == "not_byte_stable"
        assert read_raw_row() == compact
        assert update_counter() == writes_before
        assert backup_files() == []

    def test_unreachable_database_returns_an_outcome_and_never_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(database, "_db_file", lambda: str(tmp_path / "missing_dir" / "x.db"))

        outcome = database.repair_close_snapshot_chain()

        assert outcome in _OUTCOMES - {"repaired", "not_needed"}


class TestBackupBeforeRepair:
    def test_backup_is_byte_equal_to_the_pre_repair_row_and_restoring_it_gives_the_row_back(self):
        raw = load_record_text(_NESTED)
        write_raw_row(raw)

        assert database.repair_close_snapshot_chain() == "repaired"

        backups = backup_files()
        assert len(backups) == 1
        assert backups[0].read_bytes() == raw.encode("utf-8")
        write_raw_row(backups[0].read_bytes().decode("utf-8"))
        assert read_raw_row() == raw

    def test_backup_name_is_beside_the_state_database_dated_and_git_ignored(self):
        write_raw_row(load_record_text(_NESTED))
        assert database.repair_close_snapshot_chain() == "repaired"

        (backup,) = backup_files()

        assert backup.parent == Path(database._db_file()).parent
        assert re.fullmatch(
            re.escape(Path(database._db_file()).name)
            + r"\.pre-snapshot-repair-\d{8}T\d{6}Z\.backup",
            backup.name,
        )
        production_name = backup.name.replace(Path(database._db_file()).name, "alphabot_state.db")
        ignored = subprocess.run(
            [
                "git",
                "check-ignore",
                "-q",
                str(Path(__file__).resolve().parents[2] / production_name),
            ],
            cwd=Path(__file__).resolve().parents[2],
            check=False,
        )
        assert ignored.returncode == 0, "the droplet checkout would show the backup as untracked"

    def test_backup_is_flushed_before_the_row_is_written_and_the_engine_lock_is_held(
        self, monkeypatch
    ):
        raw = load_record_text(_NESTED)
        write_raw_row(raw)
        seen: list[tuple[str, bool]] = []
        real_fsync = os.fsync

        def watching_fsync(fd):
            seen.append((read_raw_row(), lock_is_held()))
            return real_fsync(fd)

        monkeypatch.setattr(os, "fsync", watching_fsync)

        assert database.repair_close_snapshot_chain() == "repaired"

        assert seen, "the backup was never flushed with os.fsync"
        for stored_then, locked_then in seen:
            assert stored_then == raw, "the state row was written before the backup was flushed"
            assert locked_then, "the engine lock was not held while the repair worked"
        assert not lock_is_held(), "the repair left the engine lock held"

    def test_success_logs_one_info_line_naming_the_backup_file(self, caplog):
        write_raw_row(load_record_text(_NESTED))
        with caplog.at_level(logging.INFO):
            assert database.repair_close_snapshot_chain() == "repaired"

        (backup,) = backup_files()
        assert any(
            r.levelno == logging.INFO and backup.name in r.getMessage() for r in caplog.records
        )
        assert error_records(caplog) == []


class TestBackupFailureBlocksTheRepair:
    @staticmethod
    def _assert_untouched_and_quiet(raw, caplog, tmp_path, update_counter, writes_before):
        assert read_raw_row() == raw
        assert update_counter() == writes_before
        errors = error_records(caplog)
        assert len(errors) == 1, f"expected one ERROR line, got {[r.getMessage() for r in errors]}"
        message = errors[0].getMessage()
        assert errors[0].exc_info is None, "the log line must not carry a traceback"
        assert str(tmp_path) not in message, "the log line leaked a path"
        for leaked in ("No such file", "No space left", "Not a directory", "exists"):
            assert leaked not in message
        stored = json.loads(raw)
        first = next(iter(symphony_entries(stored).values()))
        for leaked in (first["account"], first["name"]):
            assert leaked not in message, "the line leaked row content"
        assert not lock_is_held(), "the failed repair left the engine lock held"

    def test_unwritable_backup_directory_blocks_the_repair_and_logs_the_type_name_only(
        self, monkeypatch, tmp_path, caplog, update_counter
    ):
        raw = load_record_text(_NESTED)
        write_raw_row(raw)
        writes_before = update_counter()
        monkeypatch.setattr(
            database,
            "_snapshot_repair_backup_path",
            lambda *a, **k: str(tmp_path / "no_such_dir" / "x.backup"),
        )

        with caplog.at_level(logging.INFO):
            outcome = database.repair_close_snapshot_chain()

        assert outcome == "backup_failed"
        self._assert_untouched_and_quiet(raw, caplog, tmp_path, update_counter, writes_before)
        assert "FileNotFoundError" in error_records(caplog)[0].getMessage()

    def test_next_start_retries_the_backup_and_the_repair(self, monkeypatch, tmp_path):
        raw = load_record_text(_NESTED)
        write_raw_row(raw)
        with monkeypatch.context() as broken:
            broken.setattr(
                database,
                "_snapshot_repair_backup_path",
                lambda *a, **k: str(tmp_path / "no_such_dir" / "x.backup"),
            )
            assert database.repair_close_snapshot_chain() == "backup_failed"

        assert database.repair_close_snapshot_chain() == "repaired"

        assert read_raw_row() == expected_repaired_text(raw)
        assert len(backup_files()) == 1

    def test_failing_flush_removes_the_partial_backup_and_blocks_the_repair(
        self, monkeypatch, tmp_path, caplog, update_counter
    ):
        raw = load_record_text(_NESTED)
        write_raw_row(raw)
        writes_before = update_counter()

        def full_disk(fd):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(os, "fsync", full_disk)

        with caplog.at_level(logging.INFO):
            outcome = database.repair_close_snapshot_chain()

        assert outcome == "backup_failed"
        assert backup_files() == [], "a partial backup was left behind"
        self._assert_untouched_and_quiet(raw, caplog, tmp_path, update_counter, writes_before)
        assert "OSError" in error_records(caplog)[0].getMessage()

    def test_existing_file_at_the_backup_path_is_never_overwritten_or_removed(
        self, monkeypatch, tmp_path, caplog, update_counter
    ):
        raw = load_record_text(_NESTED)
        write_raw_row(raw)
        writes_before = update_counter()
        occupied = tmp_path / "earlier.backup"
        occupied.write_bytes(b"an earlier backup the operator may need")
        monkeypatch.setattr(database, "_snapshot_repair_backup_path", lambda *a, **k: str(occupied))

        with caplog.at_level(logging.INFO):
            outcome = database.repair_close_snapshot_chain()

        assert outcome == "backup_failed"
        assert occupied.read_bytes() == b"an earlier backup the operator may need"
        self._assert_untouched_and_quiet(raw, caplog, tmp_path, update_counter, writes_before)
        assert "FileExistsError" in error_records(caplog)[0].getMessage()


class TestWriteFailureLeavesTheRowAlone:
    def test_locked_row_leaves_it_unchanged_logs_one_error_and_retries_next_start(
        self, monkeypatch, tmp_path, caplog
    ):
        raw = load_record_text(_NESTED)
        write_raw_row(raw)
        released: list[bool] = []
        monkeypatch.setattr(database, "acquire_lock", lambda: True)
        monkeypatch.setattr(database, "release_lock", lambda: released.append(True))
        monkeypatch.setattr(database, "_SNAPSHOT_REPAIR_DB_TIMEOUT_S", 0.2)
        blocker = sqlite3.connect(database._db_file(), isolation_level=None)
        blocker.execute("BEGIN EXCLUSIVE")
        try:
            with caplog.at_level(logging.INFO):
                outcome = database.repair_close_snapshot_chain()
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()

        assert outcome == "write_failed"
        assert read_raw_row() == raw
        errors = error_records(caplog)
        assert len(errors) == 1
        assert "OperationalError" in errors[0].getMessage()
        assert errors[0].exc_info is None
        assert "database is locked" not in errors[0].getMessage()
        assert str(tmp_path) not in errors[0].getMessage()
        assert released, "the engine lock was not released after the failed write"

        assert database.repair_close_snapshot_chain() == "repaired"
        assert read_raw_row() == expected_repaired_text(raw)


class TestRepairYieldsToTheEngine:
    def test_engine_save_made_while_the_repair_waits_for_the_lock_survives_the_repair(
        self,
    ):
        write_raw_row(load_record_text(_NESTED))
        before = json.loads(load_record_text(_NESTED))
        engine_has_lock = threading.Event()
        repair_is_waiting = threading.Event()
        engine_saved: dict = {}

        def engine_cycle():
            assert database.acquire_lock()
            state = database.load_state()
            first_symphony = next(iter(symphony_entries(state)))
            state["last_successful_cycle_at"] = "2026-10-23T11:31:00.000000-04:00"
            state[first_symphony]["current_return"] = state[first_symphony]["current_return"] + 1.0
            engine_has_lock.set()
            repair_is_waiting.wait(timeout=10)
            database.save_state(state)
            engine_saved["state"] = json.loads(json.dumps(state))
            database.release_lock()

        engine = threading.Thread(target=engine_cycle)
        engine.start()
        assert engine_has_lock.wait(timeout=10)
        result: list[str] = []
        repair = threading.Thread(
            target=lambda: result.append(database.repair_close_snapshot_chain())
        )
        repair.start()
        time.sleep(0.6)  # the repair is polling for the lock the engine holds
        assert repair.is_alive(), "the repair did not wait for the engine's lock"
        repair_is_waiting.set()
        engine.join(timeout=30)
        repair.join(timeout=30)

        assert result == ["repaired"]
        after = read_stored_state()
        assert after["last_successful_cycle_at"] == "2026-10-23T11:31:00.000000-04:00"
        expected_flat = json.loads(expected_repaired_text(json.dumps(engine_saved["state"])))
        assert after == expected_flat
        assert count_snapshot_shaped_dicts(after) == 1
        assert before[_SNAPSHOT_KEY]["trading_day"] == after[_SNAPSHOT_KEY]["trading_day"]

    def test_engine_still_running_for_the_whole_wait_means_no_repair_this_start(
        self, monkeypatch, caplog, update_counter
    ):
        raw = load_record_text(_NESTED)
        write_raw_row(raw)
        writes_before = update_counter()
        monkeypatch.setattr(database, "_SNAPSHOT_REPAIR_LOCK_WAIT_S", 0.3)
        assert database.acquire_lock()  # an engine run that does not finish

        with caplog.at_level(logging.INFO):
            outcome = database.repair_close_snapshot_chain()

        assert outcome == "engine_busy"
        assert read_raw_row() == raw
        assert update_counter() == writes_before
        assert backup_files() == []
        assert lock_is_held(), "the repair released a lock it never acquired"
        database.release_lock()
        assert database.repair_close_snapshot_chain() == "repaired"


class TestDaemonStartRunsTheRepairBeforeTheScheduler:
    @staticmethod
    def _run_start_block(monkeypatch, repair):
        import app as app_module

        events: list[str] = []
        tree = ast.parse(_APP.read_text(encoding="utf-8"))
        guard = next(
            n
            for n in tree.body
            if isinstance(n, ast.If)
            and ast.unparse(n.test).replace('"', "'") == "__name__ == '__main__'"
        )
        block = ast.fix_missing_locations(ast.Module(body=guard.body, type_ignores=[]))

        class RecordingThread:
            def __init__(self, target=None, **kwargs):
                self.target = getattr(target, "__name__", str(target))

            def start(self):
                events.append(f"scheduler_thread:{self.target}")

        namespace = dict(vars(app_module))
        namespace.update(
            sys=SimpleNamespace(stdout=SimpleNamespace(buffer=io.BytesIO())),
            threading=SimpleNamespace(Thread=RecordingThread),
            app=SimpleNamespace(run=lambda **kwargs: events.append("serve")),
            _acquire_daemon_singleton=lambda path: events.append("singleton"),
        )
        monkeypatch.setattr(
            alpha_bot_execution,
            "ensure_bot_state_seeded",
            lambda: events.append("seed"),
        )

        def recording_repair():
            events.append("repair")
            return repair()

        monkeypatch.setattr(database, "repair_close_snapshot_chain", recording_repair)
        exec(compile(block, "app_daemon_start_block", "exec"), namespace)  # noqa: S102
        return events

    def test_repair_runs_once_after_the_seed_and_before_the_scheduler_thread(self, monkeypatch):
        events = self._run_start_block(monkeypatch, lambda: "repaired")

        assert events.count("repair") == 1
        assert events.index("singleton") < events.index("seed") < events.index("repair")
        assert events.index("repair") < events.index("scheduler_thread:run_scheduler")
        assert events.index("scheduler_thread:run_scheduler") < events.index("serve")

    def test_a_repair_that_raises_still_lets_the_daemon_start(self, monkeypatch):
        def broken():
            raise RuntimeError("repair bug")

        events = self._run_start_block(monkeypatch, broken)

        assert events.count("repair") == 1
        assert "scheduler_thread:run_scheduler" in events
        assert events[-1] == "serve"


class TestRepairedChainServesAsOneEntryPerSymphony:
    def test_frozen_state_after_the_repair_has_no_snapshot_entry(self, monkeypatch):
        import app as app_module

        raw = load_record_text(_NESTED)
        write_raw_row(raw)
        assert database.repair_close_snapshot_chain() == "repaired"
        symphony_ids = set(symphony_entries(json.loads(raw)))
        monkeypatch.setattr(
            app_module, "get_market_state", lambda *a, **k: "closed_frozen", raising=False
        )
        app_module.app.config["TESTING"] = True

        with app_module.app.test_client() as client:
            response = client.get("/api/state")

        assert response.status_code == 200
        body = response.get_json()
        assert set(body["bot_state"]) == symphony_ids
        assert set(body["state"]) - {"date"} == symphony_ids
        assert {
            entry["id"] for entries in body["accounts_map"].values() for entry in entries
        } == symphony_ids
        assert _SNAPSHOT_KEY not in body["bot_state"]
        assert _SNAPSHOT_KEY not in body["state"]
