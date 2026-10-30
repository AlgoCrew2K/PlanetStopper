"""RED tests: one timing line per Composer account-totals call, and no secret in any log line.

Contract for ``app._refresh_account_totals`` (the one function behind the ``:00`` scheduler
refresh, the post-cycle refresh thread and the flush-resync thread):

  - Every ``requests.get`` it issues logs one ``composer`` line through ``timing_log.emit``:
    ``composer GET /portfolio/accounts/{account}/total-stats status=<code> ms=<int>``, or
    ``error=<ExceptionTypeName>`` in place of ``status=``. The route is the template with
    ``{account}``, never the id. Each call measures its own duration (concurrent calls do not
    share a start time).
  - The line holds no account id, key id, secret or bearer token.
  - The generic failure line keeps its message text but names the exception type only; the
    exception text and the traceback appear only in a DEBUG record. The F-010 compact read-timeout
    warning and its counter are unchanged.
  - The flush-resync route's failure line, reached only when starting the refresh thread raises,
    follows the same rule.

Mocks sit at the network boundary (``requests.get``) through the shared total-stats stand-in in
``tests/app/conftest.py``, which serves the captured producer body
(``tests/fixtures/composer/v3-audit/total_stats.json``). The 503 and the timeout are derived from
that record as recorded in ``tests/fixtures/refresh_timing/05-c2/PROVENANCE.md``. Durations are
what the test advances ``timing_log.clock`` by.
"""

from __future__ import annotations

import logging
import threading
from unittest.mock import patch

import pytest
import requests

import app as app_module
import timing_log
from tests._timing_capture import FakeClock

_TOTAL_STATS_ROUTE = "/portfolio/accounts/{account}/total-stats"
# Real formats: a UUID key id and a 40-character secret (the account id is the captured UUID).
_KEY_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
_SECRET = "qT9vXk2LmZpR8wYdN4cB7hJfGaE5sUoI1nVx0KrD"
_WAIT_S = 10.0


@pytest.fixture()
def composer(monkeypatch, composer_total_stats, timing_clock):
    """The total-stats stand-in, taking `seconds` on the timing clock before it answers."""
    monkeypatch.setenv("COMPOSER_KEY_ID", _KEY_ID)
    monkeypatch.setenv("COMPOSER_SECRET", _SECRET)

    def install(seconds: float):
        def slow_get(*args, **kwargs):
            timing_clock.advance(seconds)
            return composer_total_stats(*args, **kwargs)

        monkeypatch.setattr(app_module.requests, "get", slow_get)
        return composer_total_stats

    return install


def _composer_lines(timing_records):
    return timing_records.of("composer")


def _the_line(timing_records):
    lines = _composer_lines(timing_records)
    assert len(lines) == 1, f"expected exactly one composer line, got {timing_records.lines!r}"
    return lines[0]


class TestAccountTotalsLatencyLine:
    def test_a_200_logs_the_route_template_status_and_duration(self, composer, timing_records):
        composer(0.625)

        app_module._refresh_account_totals()

        line = _the_line(timing_records)
        assert line.words == ("GET", _TOTAL_STATS_ROUTE)
        assert line.fields == {"status": "200", "ms": "625"}

    def test_a_read_timeout_logs_the_exception_type_and_the_wait_and_keeps_the_compact_warning(
        self, composer, timing_records, caplog
    ):
        stub = composer(30.0)
        stub.mode = "read_timeout"

        with caplog.at_level(logging.DEBUG, logger="alphabot"):
            app_module._refresh_account_totals()

        line = _the_line(timing_records)
        assert line.words == ("GET", _TOTAL_STATS_ROUTE)
        assert line.fields == {"error": "ReadTimeout", "ms": "30000"}
        alphabot = [r for r in caplog.records if r.name == "alphabot"]
        assert len(alphabot) == 1, "the timing line must not add a record under 'alphabot'"
        assert alphabot[0].levelno == logging.WARNING
        assert "read-timeout" in alphabot[0].getMessage()
        assert alphabot[0].exc_info is None

    def test_a_503_logs_its_status_and_duration(self, composer, timing_records):
        stub = composer(0.25)
        stub.mode = "status_503"

        app_module._refresh_account_totals()

        line = _the_line(timing_records)
        assert line.fields == {"status": "503", "ms": "250"}

    def test_a_connection_error_logs_its_type_and_duration(self, composer, timing_records):
        stub = composer(0.125)
        stub.mode = "connection_error"

        app_module._refresh_account_totals()

        line = _the_line(timing_records)
        assert line.fields == {"error": "ConnectionError", "ms": "125"}

    def test_each_call_logs_exactly_one_line(self, composer, timing_records):
        composer(0.5)

        app_module._refresh_account_totals()
        app_module._refresh_account_totals()

        assert len(_composer_lines(timing_records)) == 2


class TestNoSecretReachesTheLine:
    @pytest.mark.parametrize("mode", ["ok", "status_503", "read_timeout", "connection_error"])
    def test_no_account_id_key_id_secret_or_bearer_token_is_logged(
        self, composer, timing_records, mode
    ):
        stub = composer(0.5)
        stub.mode = mode

        app_module._refresh_account_totals()

        assert stub.urls, "the call must reach Composer"
        assert stub.account_id in stub.urls[0], "the request URL carries the account id"
        assert _composer_lines(timing_records), "the call must be logged"
        for text in timing_records.lines:
            for secret in (stub.account_id, _KEY_ID, _SECRET, "Bearer", "x-api-key"):
                assert secret not in text, f"{secret!r} leaked into a timing line: {text!r}"


class TestConcurrentCallsMeasureTheirOwnDuration:
    def test_two_overlapping_refreshes_each_log_their_own_duration(
        self, monkeypatch, composer_total_stats
    ):
        from tests._timing_capture import capture_timing_records

        clock = FakeClock(per_thread=True)
        monkeypatch.setattr(timing_log, "clock", clock)
        delays = {"refresh-a": 0.5, "refresh-b": 2.0}
        both_in_flight = threading.Barrier(2, timeout=_WAIT_S)

        def get(*args, **kwargs):
            both_in_flight.wait()
            clock.advance(delays[threading.current_thread().name])
            return composer_total_stats(*args, **kwargs)

        monkeypatch.setattr(app_module.requests, "get", get)
        threads = [
            threading.Thread(target=app_module._refresh_account_totals, name=name)
            for name in delays
        ]

        with capture_timing_records() as capture:
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=_WAIT_S)

        lines = capture.of("composer")
        assert sorted(line.fields["ms"] for line in lines) == ["2000", "500"], (
            f"two calls, each with its own duration, got {capture.lines!r}"
        )


class TestFailureLinesNameTheTypeOnly:
    ACCOUNT_URL_MESSAGE = (
        "HTTPSConnectionPool(host='api.composer.trade', port=443): "
        "Max retries exceeded with url: {url}"
    )

    @pytest.fixture()
    def raising(self, monkeypatch, composer_total_stats):
        """requests.get raising `exc_type` with a message built from the request URL."""

        def install(exc_type, template):
            def get(url, *args, **kwargs):
                raise exc_type(template.format(url=url))

            monkeypatch.setattr(app_module.requests, "get", get)

        return install

    @pytest.mark.parametrize(
        ("exc_type", "template"),
        [
            (requests.exceptions.ConnectionError, ACCOUNT_URL_MESSAGE),
            (ValueError, "bad payload from {url}"),
        ],
    )
    def test_the_error_line_names_the_type_and_the_detail_is_debug_only(
        self, raising, composer_total_stats, caplog, exc_type, template
    ):
        raising(exc_type, template)
        account_id = composer_total_stats.account_id

        with caplog.at_level(logging.DEBUG, logger="alphabot"):
            app_module._refresh_account_totals()

        records = [r for r in caplog.records if r.name == "alphabot"]
        loud = [r for r in records if r.levelno >= logging.WARNING]
        assert loud, "a failed refresh must still be logged at WARNING or above"
        for record in loud:
            text = record.getMessage()
            assert "_refresh_account_totals failed" in text, "the existing message text stays"
            assert text.endswith(exc_type.__name__), f"names the type only: {text!r}"
            assert account_id not in text and "composer.trade" not in text
            assert record.exc_info is None and record.exc_text is None
            assert record.stack_info is None
        detail = [r for r in records if r.levelno == logging.DEBUG and r.exc_info]
        assert detail, "the traceback must be kept, at DEBUG"
        assert any(account_id in str(r.exc_info[1]) for r in detail), (
            "the exception text must be available at DEBUG"
        )


class TestFlushResyncFailureLine:
    MARKER = "marker-resync-detail-5f2c8e1a"

    @pytest.fixture()
    def thread_that_cannot_start(self, monkeypatch):
        """app.threading with a Thread whose start() raises.

        Only app's own reference is replaced: the executor and every other module keep the real
        threading.Thread, which patching the shared module would break.
        """
        marker = self.MARKER

        class FailingThread:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                raise RuntimeError(marker)

            def join(self, timeout=None):
                return None

        class Proxy:
            Thread = FailingThread

            def __getattr__(self, name):
                return getattr(threading, name)

        monkeypatch.setattr(app_module, "threading", Proxy())

    def test_the_resync_failure_names_the_type_and_keeps_the_detail_at_debug(
        self, thread_that_cannot_start, tmp_path, monkeypatch, caplog
    ):
        import analytics

        monkeypatch.setattr(analytics, "_POST_MORTEMS_DIR", str(tmp_path / "pm"))
        write_completed = threading.Event()
        app_module.app.config["TESTING"] = True

        with (
            patch.object(app_module, "database") as db_mock,
            caplog.at_level(logging.DEBUG, logger="alphabot"),
            app_module.app.test_client() as client,
        ):
            db_mock.load_state.return_value = {}
            db_mock.save_state.side_effect = lambda _state: write_completed.set()
            response = client.post("/api/settings/flush-resync")
            write_completed.wait(timeout=_WAIT_S)

        assert response.status_code == 200
        records = [r for r in caplog.records if r.name == "alphabot"]
        loud = [r for r in records if r.levelno >= logging.WARNING]
        resync = [r for r in loud if "Composer resync failed" in r.getMessage()]
        assert len(resync) == 1, f"one resync failure line, got {[r.getMessage() for r in loud]!r}"
        assert resync[0].getMessage().endswith("RuntimeError")
        for record in loud:
            assert self.MARKER not in record.getMessage()
            assert record.exc_info is None and record.exc_text is None
        detail = [r for r in records if r.levelno == logging.DEBUG]
        assert any(
            self.MARKER in r.getMessage() or (r.exc_info and self.MARKER in str(r.exc_info[1]))
            for r in detail
        ), "the exception text must be available at DEBUG"
