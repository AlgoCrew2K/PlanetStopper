"""Tests for the state-capture cases, against temporary databases built from the repo's migrations.

The rows are synthetic and exist to exercise selection and verbatim copying; they are
not fixtures any production test reads. Run serially (`-n0`).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

SNAPSHOT_KEY = "last_market_close_snapshot"

DAY = "2026-01-05"
PREV_DAY = "2026-01-02"
OLDER_DAY = "2025-12-31"

# 24-character ids sort in the order their prefix suggests: F < B < C < A < E < X.
F_EXIT_TODAY = "0f" * 12
B_UNTRIGGERED = "1b" * 12
C_DROPS_OUT = "2c" * 12
A_TRIGGERED = "3a" * 12
E_NEWER_EXIT = "4e" * 12
X_NOT_IN_NEWEST = "5d" * 12


def _ts(day: str, hhmm: str, second: str = "05") -> tuple[str, str, str]:
    """(ts_utc, ts_et, cycle_id) for an ET wall time in January (UTC-5)."""
    hour, minute = int(hhmm[:2]), int(hhmm[2:])
    utc_hour = hour + 5
    ymd = day.replace("-", "")
    return (
        f"{day}T{utc_hour:02d}:{minute:02d}:{second}Z",
        f"{day}T{hour:02d}:{minute:02d}:{second}",
        f"{ymd}_{hhmm}",
    )


def _shadow(builder, conn, symphony_id, day, hhmm, **kw):
    ts_utc, ts_et, cycle_id = _ts(day, hhmm)
    builder.shadow(
        conn,
        symphony_id=symphony_id,
        trading_day=day,
        cycle_id=kw.pop("cycle_id", cycle_id),
        ts_utc=ts_utc,
        ts_et=ts_et,
        **kw,
    )


def _exit(builder, conn, symphony_id, day, hhmm, **kw):
    ts_utc, ts_et, _ = _ts(day, hhmm, "00")
    builder.exit(conn, symphony_id=symphony_id, ts_utc=ts_utc, ts_et=ts_et, **kw)


@pytest.fixture
def scenario(tmp_path, db_builder):
    """A state database with three cycles on DAY, the newest still in flight.

    Cycle 0931 and 0932 hold six symphonies; cycle 0933 holds four (C and X have not
    written yet). A has is_post_trigger rows and the oldest exit; E, F and X hold newer
    exits. One row of A on DAY carries no cycle_id.
    """
    db = tmp_path / "state.db"
    conn = db_builder.build(db)
    everyone = (
        F_EXIT_TODAY,
        B_UNTRIGGERED,
        C_DROPS_OUT,
        A_TRIGGERED,
        E_NEWER_EXIT,
        X_NOT_IN_NEWEST,
    )
    for hhmm in ("0931", "0932"):
        for sid in everyone:
            _shadow(
                db_builder,
                conn,
                sid,
                DAY,
                hhmm,
                current_return=-0.123456789012345 if sid == A_TRIGGERED else 0.25,
                shadow_return=0.5,
                is_post_trigger=1 if sid == A_TRIGGERED else 0,
            )
    for sid in (F_EXIT_TODAY, B_UNTRIGGERED, A_TRIGGERED, E_NEWER_EXIT):
        _shadow(
            db_builder,
            conn,
            sid,
            DAY,
            "0933",
            current_return=0.75,
            is_post_trigger=1 if sid == A_TRIGGERED else 0,
        )
    _shadow(db_builder, conn, A_TRIGGERED, DAY, "0940", cycle_id=None, current_return=9.99)

    # Earlier days: PREV_DAY has two cycles for A and B; OLDER_DAY has one row for B only.
    for hhmm, value in (("1529", 1.1), ("1530", 1.2)):
        _shadow(db_builder, conn, A_TRIGGERED, PREV_DAY, hhmm, current_return=value)
        _shadow(db_builder, conn, B_UNTRIGGERED, PREV_DAY, hhmm, current_return=value)
    _shadow(db_builder, conn, B_UNTRIGGERED, OLDER_DAY, "1530", current_return=0.4)

    # Exits, oldest to newest by ts_utc. A has two rows sharing one timestamp.
    _exit(db_builder, conn, A_TRIGGERED, PREV_DAY, "1000", reason="Take Profit")
    _exit(
        db_builder,
        conn,
        A_TRIGGERED,
        PREV_DAY,
        "1100",
        reason="Trailing Stop",
        gate_state_json='{"hwm": 3.25,  "ticks": [1, 2]}',
    )
    _exit(db_builder, conn, A_TRIGGERED, PREV_DAY, "1100", reason="Regime Exit", at_return=-2.5)
    _exit(db_builder, conn, E_NEWER_EXIT, PREV_DAY, "1500")
    _exit(db_builder, conn, F_EXIT_TODAY, DAY, "1000")
    _exit(db_builder, conn, X_NOT_IN_NEWEST, DAY, "1500")
    conn.commit()
    return db, conn


def _run(capture, case, db, out, *extra) -> int:
    return capture.main([case, "--db", str(db), "--out", str(out), *extra])


def _sidecar(out: Path, folder: str, name: str) -> dict:
    return json.loads((out / folder / f"{name}.capture.json").read_text(encoding="utf-8"))


def _all_rows(conn, sql, params=()):
    conn.row_factory = sqlite3.Row
    return [{k: r[k] for k in r.keys()} for r in conn.execute(sql, params)]


# ---------------------------------------------------------------------------
# bot_state row
# ---------------------------------------------------------------------------


def _chain(n: int) -> dict:
    snapshot = {"trading_day": PREV_DAY, "accounts_map": {}}
    for _ in range(n - 1):
        snapshot = {"trading_day": PREV_DAY, SNAPSHOT_KEY: snapshot}
    return snapshot


def _state_db(path: Path, db_builder, data) -> Path:
    conn = db_builder.build(path)
    conn.execute("INSERT INTO bot_state (id, data) VALUES (1, ?)", (data,))
    conn.commit()
    conn.close()
    return path


class TestBotStateRow:
    CASE = "01-c1-bot-state-row"

    def _state_text(self, chain_length=5) -> str:
        state = {
            "date": PREV_DAY,
            "a" * 24: {"name": "Alpha é", "account": "acct-1", "current_return": 1.5},
            "b" * 24: {"name": "Beta"},
            "last_successful_cycle_at": f"{PREV_DAY}T16:00:03-05:00",
            SNAPSHOT_KEY: _chain(chain_length),
        }
        return json.dumps(state)

    def test_record_is_the_stored_bytes_gzipped_and_the_source_is_not_modified(
        self, capture, db_builder, tmp_path
    ):
        text = self._state_text()
        db = _state_db(tmp_path / "state.db", db_builder, text)
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        out = tmp_path / "out"

        assert _run(capture, self.CASE, db, out) == 0

        assert gzip.decompress(
            (out / "01-c1" / "nested_bot_state_row.json.gz").read_bytes()
        ) == text.encode("utf-8")
        assert hashlib.sha256(db.read_bytes()).hexdigest() == before

    def test_sidecar_names_source_row_and_states_size_and_round_trip(
        self, capture, db_builder, tmp_path
    ):
        text = self._state_text(chain_length=5)
        db = _state_db(tmp_path / "state.db", db_builder, text)
        out = tmp_path / "out"
        _run(capture, self.CASE, db, out)

        sidecar = _sidecar(out, "01-c1", "nested_bot_state_row")
        facts = sidecar["facts"]
        assert sidecar["source"] == {
            "database": "state.db",
            "table": "bot_state",
            "row_key": "id = 1",
        }
        assert facts["raw_bytes"] == len(text.encode("utf-8"))
        assert facts["raw_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert facts["json_round_trip_identical"] is True
        assert facts["storage_class"] == "text"
        assert facts[f"{SNAPSHOT_KEY}_occurrences"] == 5
        assert facts["symphony_entry_count"] == 2
        assert sorted(facts["non_symphony_top_level_keys"]) == sorted(
            ["date", "last_successful_cycle_at", SNAPSHOT_KEY]
        )
        assert (
            facts["gzip_bytes"] == (out / "01-c1" / "nested_bot_state_row.json.gz").stat().st_size
        )

    def test_text_that_json_would_re_serialize_differently_is_kept_and_flagged(
        self, capture, db_builder, tmp_path
    ):
        text = json.dumps(json.loads(self._state_text()), indent=2) + "\n"
        db = _state_db(tmp_path / "state.db", db_builder, text)
        out = tmp_path / "out"
        _run(capture, self.CASE, db, out)

        assert gzip.decompress(
            (out / "01-c1" / "nested_bot_state_row.json.gz").read_bytes()
        ) == text.encode("utf-8")
        assert (
            _sidecar(out, "01-c1", "nested_bot_state_row")["facts"]["json_round_trip_identical"]
            is False
        )

    def test_text_deeper_than_the_parser_allows_is_recorded_with_the_error_type(
        self, capture, db_builder, tmp_path
    ):
        text = '{"a":' * 20000 + "1" + "}" * 20000
        db = _state_db(tmp_path / "state.db", db_builder, text)
        out = tmp_path / "out"

        assert _run(capture, self.CASE, db, out) == 0

        facts = _sidecar(out, "01-c1", "nested_bot_state_row")["facts"]
        assert facts["parse_error"] == "RecursionError"
        assert gzip.decompress(
            (out / "01-c1" / "nested_bot_state_row.json.gz").read_bytes()
        ) == text.encode("utf-8")

    def test_bytes_stored_as_a_blob_are_copied_exactly_even_when_not_utf8(
        self, capture, db_builder, tmp_path
    ):
        raw = b'{"k": "\xff\xfe"}'
        db = _state_db(tmp_path / "state.db", db_builder, raw)
        out = tmp_path / "out"

        assert _run(capture, self.CASE, db, out) == 0

        facts = _sidecar(out, "01-c1", "nested_bot_state_row")["facts"]
        assert gzip.decompress((out / "01-c1" / "nested_bot_state_row.json.gz").read_bytes()) == raw
        assert facts["storage_class"] == "blob"
        assert facts["utf8_decodable"] is False

    def test_no_row_gives_status_2_and_writes_nothing(self, capture, db_builder, tmp_path):
        db = tmp_path / "state.db"
        db_builder.build(db).close()
        out = tmp_path / "out"
        assert _run(capture, self.CASE, db, out) == 2
        assert not out.exists()

    def test_second_run_into_the_same_directory_is_refused_until_forced(
        self, capture, db_builder, tmp_path
    ):
        db = _state_db(tmp_path / "state.db", db_builder, self._state_text())
        out = tmp_path / "out"
        assert _run(capture, self.CASE, db, out) == 0
        assert _run(capture, self.CASE, db, out) == 2
        assert _run(capture, self.CASE, db, out, "--force") == 0


# ---------------------------------------------------------------------------
# shadow rows
# ---------------------------------------------------------------------------


class TestShadowRows:
    CASE = "01-c2-shadow-rows"

    def _capture(self, capture, scenario, tmp_path, *extra):
        db, _ = scenario
        out = tmp_path / "out"
        code = _run(capture, self.CASE, db, out, *extra)
        return code, out

    def _record(self, out):
        return json.loads(
            (out / "01-c2" / "shadow_rows_two_cycles.json").read_text(encoding="utf-8")
        )

    def test_takes_the_two_newest_cycles_of_the_capture_day_oldest_first(
        self, capture, scenario, tmp_path
    ):
        code, out = self._capture(capture, scenario, tmp_path)
        assert code == 0
        record = self._record(out)
        assert record["capture_day"] == DAY
        assert [c["cycle_id"] for c in record["cycles"]] == ["20260105_0932", "20260105_0933"]

    def test_picks_the_post_trigger_symphony_over_newer_exits_and_a_lowest_id_untriggered_one(
        self, capture, scenario, tmp_path
    ):
        _, out = self._capture(capture, scenario, tmp_path)
        record = self._record(out)
        assert record["symphonies"]["triggered"]["symphony_id"] == A_TRIGGERED
        assert record["symphonies"]["untriggered"]["symphony_id"] == B_UNTRIGGERED
        selection = _sidecar(out, "01-c2", "shadow_rows_two_cycles")["facts"]["selection"]
        assert selection["triggered_selection"].startswith(
            "newest exit_triggers row among symphonies with is_post_trigger"
        )
        assert selection["triggered_has_post_trigger_rows_in_cycles"] is True
        assert selection["candidates_in_both_cycles"] == 4

    def test_rows_are_only_the_two_symphonies_in_the_two_cycles_and_equal_the_stored_rows(
        self, capture, scenario, tmp_path
    ):
        _, conn = scenario
        _, out = self._capture(capture, scenario, tmp_path)
        record = self._record(out)
        expected = {
            cycle_id: _all_rows(
                conn,
                "SELECT * FROM shadow_history WHERE trading_day = ? AND cycle_id = ? AND symphony_id IN (?, ?) "
                "ORDER BY ts_utc, id",
                (DAY, cycle_id, A_TRIGGERED, B_UNTRIGGERED),
            )
            for cycle_id in ("20260105_0932", "20260105_0933")
        }
        assert {c["cycle_id"]: c["rows"] for c in record["cycles"]} == expected
        assert all(len(rows) == 2 for rows in expected.values())
        a_row = (
            expected["20260105_0932"][0]
            if expected["20260105_0932"][0]["symphony_id"] == A_TRIGGERED
            else expected["20260105_0932"][1]
        )
        assert a_row["current_return"] == -0.123456789012345  # a float survives unrounded
        assert a_row["position_epoch"] is None  # NULL stays null, not 0 or ""

    def test_a_row_with_no_cycle_id_is_in_no_cycle_and_is_counted(
        self, capture, scenario, tmp_path
    ):
        _, out = self._capture(capture, scenario, tmp_path)
        text = (out / "01-c2" / "shadow_rows_two_cycles.json").read_text(encoding="utf-8")
        assert "9.99" not in text
        assert (
            _sidecar(out, "01-c2", "shadow_rows_two_cycles")["facts"][
                "rows_on_day_without_cycle_id"
            ]
            == 1
        )

    def test_in_flight_cycle_shows_fewer_symphonies_and_the_sidecar_counts_match_the_database(
        self, capture, scenario, tmp_path
    ):
        _, conn = scenario
        _, out = self._capture(capture, scenario, tmp_path)
        cycles = _sidecar(out, "01-c2", "shadow_rows_two_cycles")["facts"]["cycles_oldest_first"]
        for cycle in cycles:
            stored = _all_rows(
                conn,
                "SELECT COUNT(*) AS n, COUNT(DISTINCT symphony_id) AS s FROM shadow_history WHERE cycle_id = ?",
                (cycle["cycle_id"],),
            )[0]
            assert cycle["rows_all_symphonies"] == stored["n"]
            assert cycle["symphonies_all"] == stored["s"]
            assert cycle["rows_for_the_two_symphonies"] == 2
        assert [c["symphonies_all"] for c in cycles] == [6, 4]

    def test_previous_day_row_is_the_symphonys_newest_earlier_row(
        self, capture, scenario, tmp_path
    ):
        _, out = self._capture(capture, scenario, tmp_path)
        symphonies = self._record(out)["symphonies"]
        a_prev = symphonies["triggered"]["previous_day_last_row"]
        b_prev = symphonies["untriggered"]["previous_day_last_row"]
        assert (a_prev["trading_day"], a_prev["cycle_id"], a_prev["current_return"]) == (
            PREV_DAY,
            "20260102_1530",
            1.2,
        )
        # B wrote on PREV_DAY too, so it is also the newest earlier day for B.
        assert (b_prev["trading_day"], b_prev["cycle_id"]) == (PREV_DAY, "20260102_1530")

    def test_previous_day_row_falls_back_to_an_older_day_and_is_null_when_none_exists(
        self, capture, scenario, tmp_path
    ):
        db, conn = scenario
        conn.execute(
            "DELETE FROM shadow_history WHERE symphony_id = ? AND trading_day = ?",
            (B_UNTRIGGERED, PREV_DAY),
        )
        conn.execute(
            "DELETE FROM shadow_history WHERE symphony_id = ? AND trading_day < ?",
            (A_TRIGGERED, DAY),
        )
        conn.commit()
        _, out = self._capture(capture, scenario, tmp_path)
        symphonies = self._record(out)["symphonies"]
        assert symphonies["untriggered"]["previous_day_last_row"]["trading_day"] == OLDER_DAY
        assert symphonies["triggered"]["previous_day_last_row"] is None

    def test_without_post_trigger_rows_the_newest_exit_among_present_symphonies_is_chosen(
        self, capture, scenario, tmp_path
    ):
        db, conn = scenario
        conn.execute("UPDATE shadow_history SET is_post_trigger = 0")
        conn.commit()
        _, out = self._capture(capture, scenario, tmp_path)
        record = self._record(out)
        # X has the newest exit but is absent from the newest cycle, so F (exit that day) is next.
        assert record["symphonies"]["triggered"]["symphony_id"] == F_EXIT_TODAY
        selection = _sidecar(out, "01-c2", "shadow_rows_two_cycles")["facts"]["selection"]
        assert (
            selection["triggered_selection"]
            == "newest exit_triggers row among symphonies present in both cycles"
        )
        assert selection["triggered_has_post_trigger_rows_in_cycles"] is False
        assert record["symphonies"]["untriggered"]["symphony_id"] == B_UNTRIGGERED

    def test_an_untriggered_symphony_never_has_an_exit_that_day_or_a_post_trigger_row(
        self, capture, scenario, tmp_path
    ):
        db, conn = scenario
        conn.execute(
            "DELETE FROM shadow_history WHERE symphony_id = ? AND trading_day = ?",
            (B_UNTRIGGERED, DAY),
        )
        conn.commit()
        _, out = self._capture(capture, scenario, tmp_path)
        # B is gone; F has an exit that day and A is triggered, so E is the only eligible symphony.
        assert self._record(out)["symphonies"]["untriggered"]["symphony_id"] == E_NEWER_EXIT

    def test_overrides_replace_the_selection_and_are_recorded(self, capture, scenario, tmp_path):
        _, out = self._capture(
            capture,
            scenario,
            tmp_path,
            "--triggered-id",
            E_NEWER_EXIT,
            "--untriggered-id",
            C_DROPS_OUT,
        )
        record = self._record(out)
        assert record["symphonies"]["triggered"]["symphony_id"] == E_NEWER_EXIT
        assert record["symphonies"]["untriggered"]["symphony_id"] == C_DROPS_OUT
        selection = _sidecar(out, "01-c2", "shadow_rows_two_cycles")["facts"]["selection"]
        assert "override" in selection["triggered_selection"]
        assert "override" in selection["untriggered_selection"]
        assert selection["untriggered_in_both_cycles"] is False  # C wrote in the older cycle only

    def test_day_option_captures_another_day(self, capture, scenario, tmp_path):
        _, out = self._capture(capture, scenario, tmp_path, "--day", PREV_DAY)
        record = self._record(out)
        assert record["capture_day"] == PREV_DAY
        assert [c["cycle_id"] for c in record["cycles"]] == ["20260102_1529", "20260102_1530"]

    def test_a_day_with_fewer_than_two_cycles_gives_status_2_and_writes_nothing(
        self, capture, scenario, tmp_path
    ):
        code, out = self._capture(capture, scenario, tmp_path, "--day", OLDER_DAY)
        assert code == 2
        assert not out.exists()

    def test_database_without_exit_rows_for_present_symphonies_asks_for_an_override(
        self, capture, scenario, tmp_path, capsys
    ):
        _, conn = scenario
        conn.execute("DELETE FROM exit_triggers")
        conn.commit()
        code, out = self._capture(capture, scenario, tmp_path)
        assert code == 2
        assert "--triggered-id" in capsys.readouterr().err
        assert not out.exists()

    def test_source_database_is_unchanged(self, capture, scenario, tmp_path):
        db, conn = scenario
        conn.close()
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        _, out = self._capture(capture, scenario, tmp_path)
        assert hashlib.sha256(db.read_bytes()).hexdigest() == before


# ---------------------------------------------------------------------------
# exit trigger
# ---------------------------------------------------------------------------


class TestExitTrigger:
    CASE = "01-c2-exit-trigger"

    def _record(self, out):
        return json.loads((out / "01-c2" / "exit_trigger_row.json").read_text(encoding="utf-8"))

    def test_record_is_the_triggered_symphonys_newest_row_and_ties_go_to_the_higher_id(
        self, capture, scenario, tmp_path
    ):
        db, conn = scenario
        out = tmp_path / "out"
        assert _run(capture, self.CASE, db, out) == 0

        expected = _all_rows(
            conn,
            "SELECT * FROM exit_triggers WHERE symphony_id = ? ORDER BY ts_utc DESC, id DESC",
            (A_TRIGGERED,),
        )
        record = self._record(out)
        assert record == expected[0]
        assert (
            record["triggered_reason"] == "Regime Exit"
        )  # the later-inserted of two rows with one timestamp
        assert record["also_true_json"] is None and record["port_trigger_id"] is None
        assert (
            _sidecar(out, "01-c2", "exit_trigger_row")["facts"]["exit_triggers_rows_for_symphony"]
            == 3
        )

    def test_gate_state_text_is_copied_without_re_serialising(self, capture, scenario, tmp_path):
        db, conn = scenario
        conn.execute(
            "UPDATE exit_triggers SET gate_state_json = ? WHERE symphony_id = ? AND triggered_reason = 'Regime Exit'",
            ('{"hwm": 3.25,  "ticks": [1, 2]}', A_TRIGGERED),
        )
        conn.commit()
        out = tmp_path / "out"
        _run(capture, self.CASE, db, out)
        assert self._record(out)["gate_state_json"] == '{"hwm": 3.25,  "ticks": [1, 2]}'

    def test_symphony_comes_from_the_shadow_capture_in_the_same_output_directory(
        self, capture, scenario, tmp_path, db_builder
    ):
        db, conn = scenario
        out = tmp_path / "out"
        assert _run(capture, "01-c2-shadow-rows", db, out) == 0

        # The live database moves on: E now has post-trigger rows and the newest eligible exit.
        _shadow(db_builder, conn, E_NEWER_EXIT, DAY, "0933", is_post_trigger=1, current_return=0.8)
        conn.commit()

        assert _run(capture, self.CASE, db, out) == 0
        assert self._record(out)["symphony_id"] == A_TRIGGERED
        assert (
            _sidecar(out, "01-c2", "exit_trigger_row")["facts"]["symphony_source"]
            == "shadow-rows capture sidecar in --out"
        )

        fresh_out = tmp_path / "fresh"
        assert _run(capture, self.CASE, db, fresh_out) == 0
        assert self._record(fresh_out)["symphony_id"] == E_NEWER_EXIT
        assert _sidecar(fresh_out, "01-c2", "exit_trigger_row")["facts"][
            "symphony_source"
        ].startswith("selected afresh")

    def test_override_symphony_wins_over_the_shadow_capture(self, capture, scenario, tmp_path):
        db, _ = scenario
        out = tmp_path / "out"
        _run(capture, "01-c2-shadow-rows", db, out)
        assert _run(capture, self.CASE, db, out, "--triggered-id", F_EXIT_TODAY) == 0
        assert self._record(out)["symphony_id"] == F_EXIT_TODAY

    def test_symphony_with_no_exit_row_gives_status_2_and_writes_nothing(
        self, capture, scenario, tmp_path
    ):
        db, _ = scenario
        out = tmp_path / "out"
        assert _run(capture, self.CASE, db, out, "--triggered-id", B_UNTRIGGERED) == 2
        assert not (out / "01-c2" / "exit_trigger_row.json").exists()

    def test_unreadable_shadow_sidecar_is_an_error_and_not_a_silent_fresh_selection(
        self, capture, scenario, tmp_path
    ):
        db, _ = scenario
        out = tmp_path / "out"
        (out / "01-c2").mkdir(parents=True)
        (out / "01-c2" / "shadow_rows_two_cycles.capture.json").write_text("{}", encoding="utf-8")
        assert _run(capture, self.CASE, db, out) == 2
        assert not (out / "01-c2" / "exit_trigger_row.json").exists()
