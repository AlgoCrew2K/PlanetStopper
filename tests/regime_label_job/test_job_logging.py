"""The regime label job's log lines: one per symphony, one summary, reason codes, no secrets.

Lines come from the ``alphabot.regime_label`` logger (a child of the daemon's ``alphabot`` logger,
so they reach the daemon log file by propagation):
  per symphony  ``regime_label symphony=<hash> name=<repr> session=<D> label=<label>
                 window=<first>..<last> sessions=<n>`` or ``... reason=<code>``
  per run       ``regime_label summary session=<D> seen=<n> labelled=<n> <reason>=<count>...``
                (``failed=<ClassName>`` when the run fails before handling a symphony)

The tests read the lines by key, not by whole-line equality, so the field order is free.
Data provenance is in tests/regime_label_job/conftest.py.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import requests

import database
import regime_classifier
import regime_label_job
from tests.regime_label_job import _sip
from tests.regime_label_job.conftest import (
    ID_EIGHT,
    ID_FIVE,
    ID_FOUR,
    ID_ONE,
    ID_SEVEN,
    ID_SIX,
    ID_THREE,
    ID_TWO,
    KEY_SENTINEL,
    SECRET_SENTINEL,
    captured_entry,
    golden_closes,
    golden_label,
)

ET = ZoneInfo("America/New_York")
D_ISO = _sip.TARGET_D.isoformat()
LOGGER = "alphabot.regime_label"
BODY_SENTINEL = "RESPONSEBODYSENTINELH7K2"
EXC_SENTINEL = "EXCEPTIONTEXTSENTINELB5N9"


@pytest.fixture()
def log(caplog):
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="alphabot")
    return caplog


def _job_records(log) -> list[logging.LogRecord]:
    """The job's INFO-and-above records; a DEBUG record is not one of the lines pinned here."""
    return [r for r in log.records if r.name == LOGGER and r.levelno >= logging.INFO]


def _summary_lines(log) -> list[str]:
    return [r.getMessage() for r in _job_records(log) if "summary" in r.getMessage().split()[:2]]


def _symphony_lines(log) -> dict[str, str]:
    """{symphony hash: line} for the per-symphony lines."""
    found: dict[str, str] = {}
    for record in _job_records(log):
        message = record.getMessage()
        match = re.search(r"\bsymphony=(\S+)", message)
        if match and "summary" not in message.split()[:2]:
            found[match.group(1)] = message
    return found


def _count(line: str, key: str) -> int | None:
    match = re.search(rf"\b{key}=(\d+)", line)
    return int(match.group(1)) if match else None


def _short(kind: str, sessions: int) -> dict:
    """The newest `sessions` closes of a golden series: a ticker with a short history."""
    closes = golden_closes(kind)
    newest = sorted(closes)[-sessions:]
    return {d: closes[d] for d in newest}


def _entries_for(*items) -> dict:
    return {sid: captured_entry(name, _sip.holdings_of(*pairs)) for sid, name, pairs in items}


class TestPerSymphonyAndSummaryLines:
    def test_two_labelled_and_one_short_history_give_three_lines_and_one_summary(
        self, job_harness, log
    ):
        job_harness.serve(
            {
                "AAA": golden_closes("trending"),
                "BBB": golden_closes("mean-reverting"),
                "EEE": _short("high-vol", 15),
            }
        )
        entries = _entries_for(
            (ID_ONE, "Alpha Test Symphony", [("AAA", 1.0)]),
            (ID_TWO, "Beta Test Symphony", [("BBB", 1.0)]),
            (ID_THREE, "Gamma Test Symphony", [("EEE", 1.0)]),
        )

        job_harness.run(entries)

        records = _job_records(log)
        assert len(records) == 4, [r.getMessage() for r in records]
        assert {r.levelno for r in records} == {logging.INFO}
        lines = _symphony_lines(log)
        assert set(lines) == {ID_ONE, ID_TWO, ID_THREE}
        sessions = _sip.sessions_ending(_sip.NEWEST_ELIGIBLE, 21)
        window = f"window={sessions[1].isoformat()}..{sessions[-1].isoformat()}"
        for sid, kind in ((ID_ONE, "trending"), (ID_TWO, "mean-reverting")):
            assert f"session={D_ISO}" in lines[sid]
            assert f"label={golden_label(kind)}" in lines[sid]
            assert window in lines[sid]
            assert _count(lines[sid], "sessions") == 20
            assert "reason=" not in lines[sid]
        assert "reason=insufficient_history" in lines[ID_THREE]
        assert "label=" not in lines[ID_THREE]
        (summary,) = _summary_lines(log)
        assert f"session={D_ISO}" in summary
        assert (_count(summary, "seen"), _count(summary, "labelled")) == (3, 2)
        assert _count(summary, "insufficient_history") == 1

    def test_a_display_name_with_a_newline_cannot_split_or_forge_a_line(self, job_harness, log):
        job_harness.serve({"AAA": golden_closes("trending")})
        forged = "Evil\nregime_label summary session=2026-11-04 seen=99 labelled=99\r"
        entries = _entries_for((ID_ONE, forged, [("AAA", 1.0)]))

        job_harness.run(entries)

        records = _job_records(log)
        assert len(records) == 2, [r.getMessage() for r in records]
        for record in records:
            assert "\n" not in record.getMessage() and "\r" not in record.getMessage()
        assert len(_summary_lines(log)) == 1
        assert _count(_summary_lines(log)[0], "seen") == 1


class TestEveryReasonCode:
    def _universe(self, job_harness, monkeypatch):
        """Eight symphonies, one outcome each, in one run. The price tickers fill the first batch
        exactly and the refused symphony's ticker sorts and loads last, alone in the second."""
        batch = regime_label_job.SIP_BATCH_SIZE
        fillers = [f"FIL{i:02d}" for i in range(batch - 2)]
        closes = {t: golden_closes("trending", base=40.0 + i) for i, t in enumerate(fillers)}
        closes["SHORT1"] = _short("trending", 15)
        broken = dict(golden_closes("trending"))
        broken[sorted(broken)[10]] = 0.0
        closes["NANC1"] = broken
        closes["ZZFAIL"] = golden_closes("trending")
        entries = {
            ID_ONE: captured_entry("Write Test Symphony", _sip.holdings_of((fillers[0], 1.0))),
            ID_TWO: captured_entry(
                "Labelled Test Symphony",
                _sip.holdings_of(*[(t, 1.0 / len(fillers)) for t in fillers]),
            ),
            ID_THREE: captured_entry("Late Test Symphony", _sip.holdings_of((fillers[1], 1.0))),
            ID_FOUR: captured_entry("Short Test Symphony", _sip.holdings_of(("SHORT1", 1.0))),
            ID_FIVE: captured_entry("Nan Test Symphony", _sip.holdings_of(("NANC1", 1.0))),
            ID_SIX: captured_entry("Empty Test Symphony", []),
            ID_SEVEN: captured_entry("Broken Test Symphony", [{"allocation": 0.5}]),
            ID_EIGHT: captured_entry("Refused Test Symphony", _sip.holdings_of(("ZZFAIL", 1.0))),
        }

        def refuse_last_batch(params):
            if "ZZFAIL" in params.get("symbols", "").split(","):
                return _sip.Resp(503, {"message": "unavailable"})
            return None

        http = job_harness.serve(closes, rule=refuse_last_batch)
        now = {"value": _sip.JOB_START}
        real_save = database.save_regime_label

        def save(symphony_id, label, as_of_date):
            if symphony_id == ID_ONE:
                raise sqlite3.OperationalError("database is locked")
            real_save(symphony_id, label, as_of_date)
            if symphony_id == ID_TWO:
                now["value"] = datetime(2026, 11, 4, 9, 31, tzinfo=ET)

        monkeypatch.setattr(database, "save_regime_label", save)
        return http, entries, now

    def test_each_reason_is_named_on_its_own_line_and_counted_once_in_the_summary(
        self, job_harness, monkeypatch, log
    ):
        http, entries, now = self._universe(job_harness, monkeypatch)

        job_harness.run(entries, clock=lambda: now["value"])

        assert any(c["params"]["symbols"] == "ZZFAIL" for c in http.calls), (
            "test premise: the refused symphony's ticker must travel in a batch of its own"
        )
        expected = {
            ID_ONE: "write_failed",
            ID_THREE: "after_open",
            ID_FOUR: "insufficient_history",
            ID_FIVE: "no_classifier_label",
            ID_SIX: "no_holdings",
            ID_SEVEN: "unexpected_error",
            ID_EIGHT: "price_fetch_failed",
        }
        lines = _symphony_lines(log)
        assert set(lines) == set(expected) | {ID_TWO}
        for sid, reason in expected.items():
            assert f"reason={reason}" in lines[sid], (sid, lines[sid])
        assert "label=" in lines[ID_TWO] and "reason=" not in lines[ID_TWO]
        for sid in (ID_ONE, ID_EIGHT, ID_SEVEN):
            assert re.search(r"\b(status|error)=\S+", lines[sid]), lines[sid]
        (summary,) = _summary_lines(log)
        assert _count(summary, "seen") == 8
        assert _count(summary, "labelled") == 1
        for reason in expected.values():
            assert _count(summary, reason) == 1, (reason, summary)


class TestRunLevelFailureAndEmptyRoster:
    def test_a_state_that_cannot_be_read_gives_one_summary_naming_the_class_only(
        self, job_harness, log
    ):
        def unreadable():
            raise sqlite3.OperationalError(f"database is locked {EXC_SENTINEL}")

        run = regime_label_job.run_regime_label_job(unreadable, clock=lambda: _sip.JOB_START)

        assert run.failure == "OperationalError"
        assert tuple(run.outcomes) == ()
        assert _symphony_lines(log) == {}
        (summary,) = _summary_lines(log)
        assert "failed=OperationalError" in summary
        assert _count(summary, "labelled") == 0
        assert EXC_SENTINEL not in log.text and "database is locked" not in log.text

    def test_an_empty_roster_gives_no_symphony_line_and_a_summary_of_zero(self, job_harness, log):
        run = job_harness.run({})

        assert tuple(run.outcomes) == ()
        assert _symphony_lines(log) == {}
        (summary,) = _summary_lines(log)
        assert (_count(summary, "seen"), _count(summary, "labelled")) == (0, 0)


class TestNoSecretReachesTheLog:
    @staticmethod
    def _one_symphony(job_harness, closes=None):
        job_harness.serve(closes or {"AAA": golden_closes("trending")})
        return _entries_for((ID_ONE, "Alpha Test Symphony", [("AAA", 1.0)]))

    def test_a_clean_run_never_logs_the_credentials_it_sent(self, job_harness, log):
        entries = self._one_symphony(job_harness)

        job_harness.run(entries)

        sent = job_harness.http.calls[0]["headers"]
        assert KEY_SENTINEL in sent.values() and SECRET_SENTINEL in sent.values(), (
            "test premise: the sentinels must be the credentials the request carried"
        )
        assert KEY_SENTINEL not in log.text and SECRET_SENTINEL not in log.text
        assert "APCA-API" not in log.text

    @pytest.mark.parametrize(
        "case",
        [
            "body-of-a-refused-request",
            "body-of-a-server-error",
            "transport-error-message",
            "write-error-message",
            "classifier-error-message",
        ],
    )
    def test_a_failure_logs_its_class_name_and_status_never_its_text(
        self, job_harness, monkeypatch, log, case
    ):
        entries = self._one_symphony(job_harness)
        refusal = {"body-of-a-refused-request": 403, "body-of-a-server-error": 500}
        if case in refusal:
            job_harness.serve(
                {"AAA": golden_closes("trending")},
                rule=lambda _p: _sip.Resp(refusal[case], {"message": BODY_SENTINEL}),
            )
        elif case == "transport-error-message":
            job_harness.serve(
                {"AAA": golden_closes("trending")},
                rule=lambda _p: requests.ConnectionError(EXC_SENTINEL),
            )
        elif case == "write-error-message":

            def failing_save(*_args):
                raise sqlite3.OperationalError(EXC_SENTINEL)

            monkeypatch.setattr(database, "save_regime_label", failing_save)
        else:

            def failing_classifier(*_args, **_kwargs):
                raise RuntimeError(EXC_SENTINEL)

            monkeypatch.setattr(regime_classifier, "classify_regime", failing_classifier)

        run = job_harness.run(entries)

        (outcome,) = run.outcomes
        assert outcome.reason is not None, "test premise: the failure must have been reached"
        for secret in (BODY_SENTINEL, EXC_SENTINEL, KEY_SENTINEL, SECRET_SENTINEL):
            assert secret not in log.text, f"{secret} reached the log"
        (line,) = _symphony_lines(log).values()
        assert f"reason={outcome.reason}" in line


class TestRunStartedInsideATradingSession:
    """AC-1 S-1.5 / AC-7 S-7.2 (PM ruling): the in-session skip IS that run's summary line."""

    def test_an_in_session_run_emits_one_summary_line_with_the_reason_and_no_symphony_line(
        self, job_harness, log
    ):
        in_session = datetime(2026, 11, 4, 10, 0, tzinfo=ET)
        entries = _entries_for((ID_ONE, "Alpha Test Symphony", [("AAA", 1.0)]))

        run = job_harness.run(entries, clock=lambda: in_session)

        assert run.session is None
        records = _job_records(log)
        assert len(records) == 1, [r.getMessage() for r in records]
        assert records[0].levelno == logging.INFO
        (summary,) = _summary_lines(log)
        assert "reason=session_in_progress" in summary
        assert "session=none" in summary
        assert (_count(summary, "seen"), _count(summary, "labelled")) == (0, 0)
        assert _symphony_lines(log) == {}
