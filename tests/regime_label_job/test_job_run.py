"""Run orchestration of the daily regime label job, through the slot and the job entry point.

What these tests pin (job side, batch B of the regime label producer):
  - the slot callable ``app._run_regime_label_tick`` writes one row per symphony, keyed by the
    Composer hash, dated the session whose open follows the run, with the label the classifier
    gives the symphony's stored holdings;
  - a repeated run is idempotent, a stale row is replaced, metadata keys of the stored state are
    never symphonies, and the job writes nothing but ``regime_label_cache``;
  - D is fixed when the job starts and a row is never written at or after D's 09:30 ET open;
  - one symphony's failure (price batch, empty or malformed holdings, write) never stops the others.

Data provenance is in tests/regime_label_job/conftest.py: captured bot-state entries, closes
derived from the golden classifier series. Patch targets the production code must keep:
``requests.get`` and ``time.sleep`` (the stdlib module attributes), ``database.save_regime_label``
called as a module attribute, and ``regime_label_job.run_regime_label_job`` looked up on the module
at call time by ``app._regime_label_job_worker`` after its lazy import.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import requests

import app as app_module
import database
import regime_label_job
from tests.regime_label_job import _sip
from tests.regime_label_job.conftest import (
    ID_FOUR,
    ID_ONE,
    ID_THREE,
    ID_TWO,
    captured_entry,
    golden_closes,
    golden_label,
)

ET = ZoneInfo("America/New_York")
D = _sip.TARGET_D
D_ISO = D.isoformat()
D_OPEN = datetime(D.year, D.month, D.day, 9, 30, tzinfo=ET)

# Three symphonies whose single holding is a ticker carrying one golden series each, so the label
# each must receive is the golden file's own `expected`.
TRENDING = ("trending", "AAA", ID_ONE, "Alpha Test Symphony")
MEAN_REVERTING = ("mean-reverting", "BBB", ID_TWO, "Beta Test Symphony")
HIGH_VOL = ("high-vol", "CCC", ID_THREE, "Gamma Test Symphony")


def _entry(case) -> dict:
    kind, ticker, _sid, name = case
    return captured_entry(name, _sip.holdings_of((ticker, 1.0)))


def _closes(*cases) -> dict:
    return {ticker: golden_closes(kind) for kind, ticker, _sid, _name in cases}


def _entries(*cases) -> dict:
    return {sid: _entry((kind, ticker, sid, name)) for kind, ticker, sid, name in cases}


def _outcomes(run) -> dict:
    return {o.symphony_id: o for o in run.outcomes}


def _requested_symbols(http) -> set[str]:
    return {s for call in http.calls for s in call["params"].get("symbols", "").split(",") if s}


def _run_slot(monkeypatch) -> None:
    """Invoke the slot callable as the scheduler does and wait for its worker thread."""
    tick = getattr(app_module, "_run_regime_label_tick", None)
    assert tick is not None, "app._run_regime_label_tick is missing"
    real_run = regime_label_job.run_regime_label_job
    monkeypatch.setattr(
        regime_label_job,
        "run_regime_label_job",
        lambda load, **_kw: real_run(load, clock=lambda: _sip.JOB_START),
    )
    real_thread = threading.Thread
    started: list[threading.Thread] = []

    class _Recorded(real_thread):
        def start(self):
            started.append(self)
            super().start()

    monkeypatch.setattr(threading, "Thread", _Recorded)
    tick()
    monkeypatch.setattr(threading, "Thread", real_thread)
    assert started, "the slot callable started no thread"
    for thread in started:
        thread.join(timeout=30)
        assert not thread.is_alive(), "the slot's worker thread did not finish"


class TestSlotWritesRows:
    def test_the_slot_writes_one_row_per_symphony_keyed_by_hash_with_its_own_label(
        self, job_harness, monkeypatch
    ):
        database.save_state(_entries(TRENDING, MEAN_REVERTING))
        job_harness.serve(_closes(TRENDING, MEAN_REVERTING))

        _run_slot(monkeypatch)

        rows = job_harness.rows()
        assert set(rows) == {ID_ONE, ID_TWO}, (
            "rows must be keyed by the Composer hash, one per symphony, and by nothing else"
        )
        assert rows[ID_ONE][:2] == (golden_label("trending"), D_ISO)
        assert rows[ID_TWO][:2] == (golden_label("mean-reverting"), D_ISO)

    def test_each_symphony_gets_the_label_of_its_own_holdings(self, job_harness):
        job_harness.serve(_closes(TRENDING, MEAN_REVERTING, HIGH_VOL))

        run = job_harness.run(_entries(TRENDING, MEAN_REVERTING, HIGH_VOL))

        rows = job_harness.rows()
        assert {sid: rows[sid][0] for sid in rows} == {
            ID_ONE: golden_label("trending"),
            ID_TWO: golden_label("mean-reverting"),
            ID_THREE: golden_label("high-vol"),
        }
        assert run.session == D
        assert len(set(golden_label(k) for k in ("trending", "mean-reverting", "high-vol"))) == 3


class TestRepeatedRuns:
    def test_a_second_run_keeps_one_row_per_symphony_with_the_same_label_and_a_later_stamp(
        self, job_harness
    ):
        job_harness.serve(_closes(TRENDING, MEAN_REVERTING))
        entries = _entries(TRENDING, MEAN_REVERTING)
        job_harness.run(entries)
        first = job_harness.rows()
        conn = sqlite3.connect(database._db_file())
        try:
            conn.execute("UPDATE regime_label_cache SET updated_at = '2000-01-01 00:00:00'")
            conn.commit()
        finally:
            conn.close()

        job_harness.run(entries)

        second = job_harness.rows()
        assert set(second) == set(first) == {ID_ONE, ID_TWO}
        for sid in second:
            assert second[sid][:2] == first[sid][:2]
            assert second[sid][2] > "2000-01-01 00:00:00", "the repeated run must restamp the row"

    def test_rows_dated_the_previous_session_are_replaced_by_the_new_label_and_date(
        self, job_harness
    ):
        # Swapped labels, so a job that skipped an existing row would leave a visible mismatch.
        database.save_regime_label(ID_ONE, golden_label("mean-reverting"), "2026-11-03")
        database.save_regime_label(ID_TWO, golden_label("trending"), "2026-11-03")
        job_harness.serve(_closes(TRENDING, MEAN_REVERTING))

        job_harness.run(_entries(TRENDING, MEAN_REVERTING))

        rows = job_harness.rows()
        assert set(rows) == {ID_ONE, ID_TWO}
        assert rows[ID_ONE][:2] == (golden_label("trending"), D_ISO)
        assert rows[ID_TWO][:2] == (golden_label("mean-reverting"), D_ISO)


class TestStoredStateShape:
    _METADATA = {
        "date": "2026-11-03",
        "last_execution_mode": "dry_run",
        "last_market_close_snapshot": {"captured_at": "2026-11-03T21:00:00+00:00", "total": 0},
        "last_successful_cycle_at": "2026-11-03T20:59:00+00:00",
        "post_mortem_run": {"date": "2026-11-03", "ran": True},
    }

    def test_the_slot_loader_returns_symphony_entries_only(self):
        loader = getattr(app_module, "_regime_label_symphony_entries", None)
        assert loader is not None, "app._regime_label_symphony_entries is missing"
        database.save_state({**_entries(TRENDING, MEAN_REVERTING), **self._METADATA})

        loaded = loader()

        assert set(loaded) == {ID_ONE, ID_TWO}

    def test_the_slot_loader_reads_through_the_read_only_connection(self, monkeypatch):
        loader = getattr(app_module, "_regime_label_symphony_entries", None)
        assert loader is not None, "app._regime_label_symphony_entries is missing"
        database.save_state(_entries(TRENDING))
        opened: list[bool] = []
        real_ro = database.get_ro_connection

        def _spy():
            opened.append(True)
            return real_ro()

        monkeypatch.setattr(database, "get_ro_connection", _spy)

        loader()

        assert opened, "the loader must read the bot state through database.get_ro_connection"

    def test_metadata_keys_get_no_row_and_no_log_line(self, job_harness, monkeypatch, caplog):
        database.save_state({**_entries(TRENDING, MEAN_REVERTING), **self._METADATA})
        job_harness.serve(_closes(TRENDING, MEAN_REVERTING))

        with caplog.at_level(logging.INFO, logger="alphabot"):
            _run_slot(monkeypatch)

        assert set(job_harness.rows()) == {ID_ONE, ID_TWO}
        text = "\n".join(r.getMessage() for r in caplog.records if "regime_label" in r.name)
        for key in self._METADATA:
            assert f"symphony={key}" not in text, f"metadata key {key!r} was treated as a symphony"
        assert text.count("symphony=") == 2


class TestNothingElseIsWritten:
    def test_every_other_table_and_the_post_mortem_files_are_unchanged(
        self, job_harness, monkeypatch, tmp_path
    ):
        import analytics

        post_dir = tmp_path / "post_mortems"
        post_dir.mkdir()
        (post_dir / "post_mortem_2026-11-03.json").write_text('{"triggers": []}', encoding="utf-8")
        monkeypatch.setattr(analytics, "_POST_MORTEMS_DIR", str(post_dir))
        database.save_state(_entries(TRENDING, MEAN_REVERTING))
        job_harness.serve(_closes(TRENDING, MEAN_REVERTING))
        files_before = {p.name: p.read_bytes() for p in post_dir.iterdir()}
        tables_before = job_harness.dump_other_tables()

        _run_slot(monkeypatch)

        assert job_harness.rows(), "the run must have written its rows for this check to count"
        assert job_harness.dump_other_tables() == tables_before
        assert {p.name: p.read_bytes() for p in post_dir.iterdir()} == files_before


class TestSessionOpenCutoff:
    """D is fixed at job start; a row is written only strictly before D's 09:30 ET open."""

    def test_a_symphony_finished_after_the_open_gets_no_row_while_the_one_before_it_keeps_its_own(
        self, job_harness, monkeypatch
    ):
        now = {"value": _sip.JOB_START}
        real_save = database.save_regime_label

        def save_then_open(symphony_id, label, as_of_date):
            real_save(symphony_id, label, as_of_date)
            if symphony_id == ID_ONE:
                now["value"] = datetime(D.year, D.month, D.day, 9, 31, tzinfo=ET)

        monkeypatch.setattr(database, "save_regime_label", save_then_open)
        job_harness.serve(_closes(TRENDING, MEAN_REVERTING))

        run = job_harness.run(_entries(TRENDING, MEAN_REVERTING), clock=lambda: now["value"])

        rows = job_harness.rows()
        assert set(rows) == {ID_ONE}
        assert rows[ID_ONE][:2] == (golden_label("trending"), D_ISO)
        outcomes = _outcomes(run)
        assert outcomes[ID_TWO].reason == "after_open"
        assert outcomes[ID_ONE].reason is None
        assert run.session == D, "D is fixed when the job starts"

    @pytest.mark.parametrize(
        ("instant", "written"),
        [
            (datetime(D.year, D.month, D.day, 9, 29, 59, tzinfo=ET), True),
            (datetime(D.year, D.month, D.day, 9, 30, 0, tzinfo=ET), False),
            (datetime(D.year, D.month, D.day, 9, 30, 1, tzinfo=ET), False),
            (datetime(D.year, D.month, D.day, 16, 5, tzinfo=ET), False),
            (datetime(D.year, D.month, D.day + 1, 10, 0, tzinfo=ET), False),
        ],
        ids=["one-second-before", "at-the-open", "one-second-after", "after-close", "next-day"],
    )
    def test_the_open_arriving_during_the_price_fetch_decides_every_write(
        self, job_harness, instant, written
    ):
        now = {"value": _sip.JOB_START}

        def open_during_fetch(_params):
            now["value"] = instant

        job_harness.serve(
            _closes(TRENDING, MEAN_REVERTING), rule=lambda params: open_during_fetch(params)
        )

        run = job_harness.run(_entries(TRENDING, MEAN_REVERTING), clock=lambda: now["value"])

        rows = job_harness.rows()
        if written:
            assert {sid: row[1] for sid, row in rows.items()} == {ID_ONE: D_ISO, ID_TWO: D_ISO}
        else:
            assert rows == {}, "no row may be written at or after D's open, nor for any other date"
            assert {o.reason for o in run.outcomes} == {"after_open"}
        assert run.session == D, "a run that outlives the open still names the session it began for"


class TestOneSymphonyNeverStopsTheOthers:
    @staticmethod
    def _batch_failure_universe(job_harness):
        """A fills the first batch with its own tickers, C adds the last ticker of that batch and
        B's only ticker sorts and loads last, alone in the second batch."""
        batch = regime_label_job.SIP_BATCH_SIZE
        a_tickers = [f"TRD{i:02d}" for i in range(batch - 1)]
        closes = {t: golden_closes("trending", base=50.0 + i) for i, t in enumerate(a_tickers)}
        closes["MRC1"] = golden_closes("mean-reverting")
        closes["ZZFAIL"] = golden_closes("high-vol")
        entries = {
            ID_ONE: captured_entry(
                "Alpha Test Symphony",
                _sip.holdings_of(*[(t, 1.0 / len(a_tickers)) for t in a_tickers]),
            ),
            ID_THREE: captured_entry("Gamma Test Symphony", _sip.holdings_of(("MRC1", 1.0))),
            ID_TWO: captured_entry("Beta Test Symphony", _sip.holdings_of(("ZZFAIL", 1.0))),
        }

        def refuse_the_last_batch(params):
            if "ZZFAIL" in params.get("symbols", "").split(","):
                return _sip.Resp(500, {"message": "upstream unavailable"})
            return None

        return job_harness.serve(closes, rule=refuse_the_last_batch), entries

    def test_a_price_batch_that_fails_costs_only_the_symphony_it_covers(self, job_harness):
        http, entries = self._batch_failure_universe(job_harness)

        run = job_harness.run(entries)

        assert any(c["params"]["symbols"] == "ZZFAIL" for c in http.calls), (
            "test premise: the failing symphony's ticker must travel in a batch of its own"
        )
        rows = job_harness.rows()
        assert {sid: rows[sid][:2] for sid in rows} == {
            ID_ONE: (golden_label("trending"), D_ISO),
            ID_THREE: (golden_label("mean-reverting"), D_ISO),
        }
        failed = _outcomes(run)[ID_TWO]
        assert failed.reason == "price_fetch_failed"
        assert "500" in str(failed.detail)
        assert _outcomes(run)[ID_ONE].reason is None
        assert _outcomes(run)[ID_THREE].reason is None

    @pytest.mark.parametrize("case", ["empty-list", "key-absent"])
    def test_a_symphony_without_holdings_gets_no_row_and_adds_nothing_to_the_fetch(
        self, job_harness, case
    ):
        bare = captured_entry("Delta Test Symphony", [])
        if case == "key-absent":
            del bare["current_holdings"]
        entries = {ID_ONE: _entry(TRENDING), ID_FOUR: bare}
        http = job_harness.serve(_closes(TRENDING))

        run = job_harness.run(entries)

        assert set(job_harness.rows()) == {ID_ONE}
        assert _outcomes(run)[ID_FOUR].reason == "no_holdings"
        assert _outcomes(run)[ID_ONE].reason is None
        assert _requested_symbols(http) == {"AAA"}

    def test_an_unreachable_price_source_leaves_every_symphony_unlabelled(self, job_harness):
        job_harness.serve(
            _closes(TRENDING, MEAN_REVERTING),
            rule=lambda _params: requests.ConnectionError("host unreachable"),
        )

        run = job_harness.run(_entries(TRENDING, MEAN_REVERTING))

        assert job_harness.rows() == {}
        assert [o.reason for o in run.outcomes] == ["price_fetch_failed", "price_fetch_failed"]
        assert {o.detail for o in run.outcomes} == {"ConnectionError"}

    @pytest.mark.parametrize(
        "malformed",
        [
            [{"ticker": "BADTK", "allocation": 0.5}, {"allocation": 0.5}],
            [{"ticker": "BADTK", "allocation": 0.5}, "BADTK2"],
            [{"ticker": "BADTK", "allocation": 0.5}, {"ticker": "BADTK2"}],
        ],
        ids=["holding-without-ticker", "holding-not-a-mapping", "holding-without-allocation"],
    )
    def test_a_malformed_holding_is_an_unexpected_error_for_that_symphony_alone(
        self, job_harness, malformed
    ):
        entries = {
            ID_ONE: _entry(TRENDING),
            ID_FOUR: captured_entry("Delta Test Symphony", malformed),
            ID_TWO: _entry(MEAN_REVERTING),
        }
        closes = _closes(TRENDING, MEAN_REVERTING)
        closes["BADTK"] = golden_closes("high-vol")
        http = job_harness.serve(closes)

        run = job_harness.run(entries)

        assert set(job_harness.rows()) == {ID_ONE, ID_TWO}
        bad = _outcomes(run)[ID_FOUR]
        assert bad.reason == "unexpected_error"
        assert str(bad.detail).isidentifier(), "the detail is an exception class name, never text"
        assert "BADTK" not in _requested_symbols(http), (
            "a malformed entry must add no ticker to the price request"
        )

    def test_a_failed_write_costs_only_that_symphony_and_the_rest_are_still_written(
        self, job_harness, monkeypatch
    ):
        real_save = database.save_regime_label

        def locked_for_the_first(symphony_id, label, as_of_date):
            if symphony_id == ID_ONE:
                raise sqlite3.OperationalError("database is locked")
            real_save(symphony_id, label, as_of_date)

        monkeypatch.setattr(database, "save_regime_label", locked_for_the_first)
        job_harness.serve(_closes(TRENDING, MEAN_REVERTING, HIGH_VOL))

        run = job_harness.run(_entries(TRENDING, MEAN_REVERTING, HIGH_VOL))

        assert set(job_harness.rows()) == {ID_TWO, ID_THREE}
        failed = _outcomes(run)[ID_ONE]
        assert failed.reason == "write_failed"
        assert failed.detail == "OperationalError"

    def test_a_missing_cache_table_fails_each_write_and_the_run_still_completes(self, job_harness):
        conn = sqlite3.connect(database._db_file())
        try:
            conn.execute("DROP TABLE regime_label_cache")
            conn.commit()
        finally:
            conn.close()
        job_harness.serve(_closes(TRENDING, MEAN_REVERTING))

        run = job_harness.run(_entries(TRENDING, MEAN_REVERTING))

        assert [o.reason for o in run.outcomes] == ["write_failed", "write_failed"]
        assert run.session == D


class TestUnusablePageTokenFailsOnlyItsBatch:
    """A non-empty list as the first page's ``next_page_token`` cannot be checked or sent: that
    batch fails, naming the exception type, and the other batch is fetched and labelled."""

    BASE_TICKS = 7  # an arbitrary default count the engine passes when a label is absent

    def _universe(self, job_harness):
        """31 tickers sort into two batches: the 30 FIL tickers, then ZZLATE alone."""
        batch = regime_label_job.SIP_BATCH_SIZE
        fillers = [f"FIL{i:02d}" for i in range(batch)]
        closes = {t: golden_closes("trending", base=40.0 + i) for i, t in enumerate(fillers)}
        closes["ZZLATE"] = golden_closes("mean-reverting")
        entries = {
            ID_ONE: captured_entry("Early Test Symphony", _sip.holdings_of((fillers[0], 1.0))),
            ID_TWO: captured_entry(
                "Mixed Test Symphony", _sip.holdings_of((fillers[1], 0.5), ("ZZLATE", 0.5))
            ),
            ID_THREE: captured_entry("Late Test Symphony", _sip.holdings_of(("ZZLATE", 1.0))),
        }

        def unusable_token_on_the_first_batch(params):
            symbols = params.get("symbols", "").split(",")
            if fillers[0] in symbols and "page_token" not in params:
                body = _sip.page_body({fillers[0]: closes[fillers[0]]}, ["next-page"])
                return _sip.Resp(200, body)
            return None

        http = job_harness.serve(closes, rule=unusable_token_on_the_first_batch)
        return http, entries

    def test_the_failed_batch_names_the_exception_type_and_the_other_batch_is_labelled(
        self, job_harness, caplog
    ):
        http, entries = self._universe(job_harness)
        caplog.set_level(logging.INFO, logger="alphabot.regime_label")

        run = job_harness.run(entries)

        batches = [call["params"]["symbols"].split(",") for call in http.calls]
        assert any(batch == ["ZZLATE"] for batch in batches), (
            "the second batch must still be requested after the first one failed"
        )
        outcomes = _outcomes(run)
        for sid in (ID_ONE, ID_TWO):
            assert outcomes[sid].reason == "price_fetch_failed", sid
            assert outcomes[sid].detail == "TypeError", sid
        assert outcomes[ID_THREE].reason is None
        assert outcomes[ID_THREE].label == golden_label("mean-reverting")
        rows = job_harness.rows()
        assert {sid: rows[sid][:2] for sid in rows} == {
            ID_THREE: (golden_label("mean-reverting"), D_ISO)
        }

        lines = [r.getMessage() for r in caplog.records if r.name == "alphabot.regime_label"]
        symphony_lines = [line for line in lines if " symphony=" in line]
        summaries = [line for line in lines if line.startswith("regime_label summary")]
        assert len(symphony_lines) == 3, lines
        assert len(summaries) == 1, lines
        assert "seen=3" in summaries[0] and "labelled=1" in summaries[0]
        assert "price_fetch_failed=2" in summaries[0]

    def test_the_engine_applies_the_default_count_for_a_symphony_whose_batch_failed(
        self, job_harness
    ):
        import math_engine

        _http, entries = self._universe(job_harness)

        job_harness.run(entries)

        failed = database.get_cached_regime_label(ID_ONE, session_date=D_ISO)
        labelled = database.get_cached_regime_label(ID_THREE, session_date=D_ISO)
        assert failed is None, "no row is dated the session for a symphony whose batch failed"
        assert math_engine.apply_regime_exit_adjustment(failed, self.BASE_TICKS) == self.BASE_TICKS
        assert labelled == golden_label("mean-reverting")
