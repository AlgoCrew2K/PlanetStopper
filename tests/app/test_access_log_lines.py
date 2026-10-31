"""RED tests: one access line per request the daemon answers, and no secret on it.

``timing_log.AccessLogMiddleware`` wraps ``app.wsgi_app``. Contract:

  - One line per answered request:
    ``access <METHOD> <path>[?<name>=***&...] status=<code> ms=<int> bytes=<int>``.
  - It reads ``REQUEST_METHOD``, ``PATH_INFO`` and ``QUERY_STRING`` and nothing else: no cookie,
    no header, no body, no query value reaches a line. Every word passes the emitter's sanitizer,
    so a path cannot split or forge a line.
  - An event stream is logged when it opens (``stream=open``) and when it closes
    (``stream=closed open_s=<one decimal> bytes=<int>``). The response iterable is never consumed
    eagerly and the inner iterable's ``close()`` runs before the closing line, because the SSE
    generator's cleanup depends on it.
  - A route that raises answers 500 and is logged once; an exception that escapes the wrapped app
    is logged (``status=500 bytes=0 error=<type>``, no message) and re-raised.

The Flask test client supplies the real responses (nothing is recorded); sizes are the length of
the response the client received and durations are what the test advances ``timing_log.clock`` by.
Non-stream requests use ``buffered=True`` because the test client otherwise defers ``close()``.
Auth is re-enabled where a scenario needs a real 401, because the suite disables it by default.
"""

from __future__ import annotations

import logging
import re
import sys
import threading
from unittest.mock import patch

import pytest
from werkzeug.test import EnvironBuilder

import app as app_module
import timing_log
from tests.app.conftest import CAPTURED_SYMPHONY_ID

_LINE = re.compile(
    r"^access (?P<method>[A-Z]+) (?P<target>\S+) "
    r"status=(?P<status>\d{3}) ms=(?P<ms>\d+) bytes=(?P<size>\d+)$"
)
_PASSWORD = "pw-marker-7d41c9e2a0b8"
_CSRF_TOKEN = "csrf-marker-31f0e6aa5d92"
_SESSION_COOKIE = "cookie-marker-9be2c4d17f60"
_BODY_MARKER = "alpha-body-marker-83ad"
_ROUTE_ERROR_MARKER = "route-failure-marker-4417bb"
_ACCOUNT_ID = "1a2b3c4d-0000-4000-8000-000000000000"
_WAIT_S = 10.0
_STUB_STATE = {
    "bot_state": {},
    "is_locked": False,
    "port_state": {},
    "exit_authority": "per_symphony",
    "daemon_started_at": None,
}


@pytest.fixture()
def auth_enabled(monkeypatch):
    """The real auth gate, as tests/app/test_health_endpoint.py re-enables it."""
    monkeypatch.setenv("DASHBOARD_PASSWORD", _PASSWORD)
    monkeypatch.setenv("SECRET_KEY", "secret-key-for-access-log-tests")
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    app_module._AUTH_FAILED_ATTEMPTS.clear()
    yield
    app_module._AUTH_FAILED_ATTEMPTS.clear()


@pytest.fixture()
def mock_database():
    with patch.object(app_module, "database") as db_mock:
        db_mock.load_state.return_value = {}
        db_mock.load_chart_history.return_value = {"symphonies": {CAPTURED_SYMPHONY_ID: []}}
        db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
        db_mock.get_symphony_strategy.return_value = {"params": {}, "locked_vars": []}
        db_mock.save_symphony_strategy.return_value = None
        yield db_mock


def _access(timing_records, path: str | None = None):
    """The parsed access lines, optionally only those for `path` (query string ignored)."""
    lines = timing_records.of("access")
    if path is None:
        return lines
    return [line for line in lines if line.words[1].split("?")[0] == path]


def _one(lines):
    """The only line of a list, asserted, so a missing line reads as a failed assertion."""
    assert len(lines) == 1, (
        f"expected exactly one line, got {[getattr(x, 'text', x) for x in lines]!r}"
    )
    return lines[0]


def _assert_whole_line(text: str, method: str):
    match = _LINE.match(text)
    assert match, f"not a whole access line: {text!r}"
    assert match.group("method") == method


class TestOneLinePerRequest:
    def test_get_api_state_logs_method_path_status_duration_and_size(
        self, client, timing_records, timing_clock
    ):
        def state():
            timing_clock.advance(0.125)
            return dict(_STUB_STATE)

        # A context manager inside the test is undone before the suite's autouse stub of the
        # same name; a monkeypatch undo runs after it and would put the stub back for good.
        with patch.object(app_module, "get_api_state_dict", state):
            response = client.get("/api/state", buffered=True)

        assert response.status_code == 200
        line = _one(_access(timing_records, "/api/state"))
        _assert_whole_line(line.text, "GET")
        assert line.words == ("GET", "/api/state")
        assert line.fields == {"status": "200", "ms": "125", "bytes": str(len(response.data))}

    def test_an_unauthenticated_request_is_logged_with_its_401(
        self, client, auth_enabled, timing_records
    ):
        response = client.get("/api/state", buffered=True)

        assert response.status_code == 401
        line = _one(_access(timing_records, "/api/state"))
        assert line.fields["status"] == "401"
        assert line.fields["bytes"] == str(len(response.data))

    def test_a_route_that_raises_answers_500_and_is_logged_once_without_the_error_text(
        self, client, timing_records, monkeypatch
    ):
        def failing_view():
            raise RuntimeError(_ROUTE_ERROR_MARKER)

        monkeypatch.setitem(app_module.app.view_functions, "health", failing_view)
        monkeypatch.setitem(app_module.app.config, "PROPAGATE_EXCEPTIONS", False)

        response = client.get("/health", buffered=True)

        assert response.status_code == 500
        line = _one(_access(timing_records, "/health"))
        assert line.fields["status"] == "500"
        assert _ROUTE_ERROR_MARKER not in "\n".join(timing_records.lines)

    def test_thirty_simultaneous_requests_log_thirty_whole_lines(self, timing_records):
        statuses: list[int] = []
        start = threading.Barrier(30, timeout=_WAIT_S)

        def one_request():
            with app_module.app.test_client() as c:
                start.wait()
                statuses.append(c.get("/health", buffered=True).status_code)

        with patch.object(app_module.database, "load_state", return_value={}):
            threads = [threading.Thread(target=one_request) for _ in range(30)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=_WAIT_S)

        assert statuses == [200] * 30
        lines = [line.text for line in _access(timing_records, "/health")]
        assert len(lines) == 30
        for text in lines:
            _assert_whole_line(text, "GET")

    def test_werkzeug_keeps_its_request_log_off_so_each_request_has_one_line(self):
        assert logging.getLogger("werkzeug").getEffectiveLevel() >= logging.ERROR


class TestOtherRoutesAreLogged:
    def test_a_static_asset_and_a_polled_json_route_each_log_status_duration_and_size(
        self, client, mock_database, timing_records
    ):
        asset = client.get("/static/index.js", buffered=True)
        chart = client.get(f"/api/chart/{CAPTURED_SYMPHONY_ID}", buffered=True)

        assert asset.status_code == 200 and chart.status_code == 200
        for path, response in (
            ("/static/index.js", asset),
            (f"/api/chart/{CAPTURED_SYMPHONY_ID}", chart),
        ):
            line = _one(_access(timing_records, path))
            _assert_whole_line(line.text, "GET")
            assert line.fields["status"] == str(response.status_code)
            assert line.fields["bytes"] == str(len(response.data))

    @pytest.mark.parametrize("path", ["/login", "/", "/history", "/performance", "/settings"])
    def test_a_page_is_logged_with_the_status_and_size_the_client_received(
        self, client, timing_records, monkeypatch, path
    ):
        monkeypatch.setitem(app_module.app.config, "PROPAGATE_EXCEPTIONS", False)

        response = client.get(path, buffered=True)

        line = _one(_access(timing_records, path))
        assert line.fields["status"] == str(response.status_code)
        assert line.fields["bytes"] == str(len(response.data))

    def test_a_settings_post_logs_method_path_status_duration_and_size_but_no_body_or_token(
        self, client, mock_database, timing_records, tmp_path, monkeypatch
    ):
        env_file = tmp_path / ".env"
        env_file.write_text("", encoding="utf-8")
        monkeypatch.setattr(app_module, "ENV_FILE_PATH", str(env_file))
        monkeypatch.setattr(app_module, "set_key", lambda path, key, val, *a, **k: (True, key, val))
        payload = {
            "globals": {"EXIT_AUTHORITY": "per_symphony"},
            "symphonies": {_BODY_MARKER: {"params": {}, "locked_vars": []}},
        }

        response = client.post(
            "/api/settings", json=payload, headers={"X-CSRF-Token": _CSRF_TOKEN}, buffered=True
        )

        assert response.status_code == 200
        line = _one(_access(timing_records, "/api/settings"))
        _assert_whole_line(line.text, "POST")
        assert line.fields["bytes"] == str(len(response.data))
        everything = "\n".join(timing_records.lines)
        assert _BODY_MARKER not in everything and _CSRF_TOKEN not in everything


class TestNoSecretReachesTheLine:
    def test_a_login_post_logs_neither_the_password_the_token_nor_the_cookie(
        self, client, auth_enabled, timing_records
    ):
        response = client.post(
            "/login",
            data={"password": _PASSWORD, "csrf_token": _CSRF_TOKEN},
            headers={"X-CSRF-Token": _CSRF_TOKEN, "Cookie": f"session={_SESSION_COOKIE}"},
            buffered=True,
        )

        line = _one(_access(timing_records, "/login"))
        _assert_whole_line(line.text, "POST")
        assert line.fields["status"] == str(response.status_code)
        for record in timing_records.records:
            seen = f"{record.msg} {record.args} {record.getMessage()}"
            for secret in (_PASSWORD, _CSRF_TOKEN, _SESSION_COOKIE):
                assert secret not in seen, f"{secret!r} reached a timing record"

    def test_a_request_carrying_a_session_cookie_logs_without_it(self, client, timing_records):
        client.get("/health", headers={"Cookie": f"session={_SESSION_COOKIE}"}, buffered=True)

        assert _access(timing_records, "/health"), "the request must be logged"
        assert _SESSION_COOKIE not in "\n".join(timing_records.lines)
        assert "session=" not in "\n".join(timing_records.lines)


class TestPathAndQueryAreSanitized:
    def test_a_query_value_is_masked_but_its_name_is_kept(self, client, timing_records):
        client.get(f"/api/state?account={_ACCOUNT_ID}&flag=", buffered=True)

        line = _one(_access(timing_records, "/api/state"))
        assert line.words == ("GET", "/api/state?account=***&flag=***")
        assert _ACCOUNT_ID not in "\n".join(timing_records.lines)

    def test_a_path_cannot_split_or_forge_a_line(self, client, mock_database, timing_records):
        response = client.get(
            "/api/chart/x%20y%0D%0Aaccess%20GET%20/forged%20status=200%25", buffered=True
        )

        lines = timing_records.lines
        assert len(lines) == 1, f"one request, one line: {lines!r}"
        assert "\r" not in lines[0] and "\n" not in lines[0]
        assert re.search(r"(?i)%0D%0A", lines[0]), "the CR LF must be percent-encoded"
        assert len(timing_records.of("access")) == 1, "no forged second event"
        line = _one(timing_records.of("access"))
        assert line.fields["status"] == str(response.status_code)

    def test_a_query_name_cannot_split_a_line(self, client, timing_records):
        client.get("/api/state?x%0D%0Aevil=1", buffered=True)

        text = _one(timing_records.lines)
        assert "\r" not in text and "\n" not in text


class TestEventStream:
    def test_the_stream_is_logged_at_open_and_at_close_with_how_long_it_stayed_open(
        self, client, timing_records, timing_clock
    ):
        before = len(app_module._sse_clients)

        response = client.get("/api/events")
        opened = [line.fields for line in _access(timing_records, "/api/events")]
        first_chunk = next(iter(response.response))
        timing_clock.advance(720.0)
        response.close()

        assert opened == [{"status": "200", "stream": "open"}], "the open line is logged at once"
        assert [line.fields for line in _access(timing_records, "/api/events")] == [
            {"status": "200", "stream": "open"},
            {
                "status": "200",
                "stream": "closed",
                "open_s": "720.0",
                "bytes": str(len(first_chunk)),
            },
        ]
        assert len(app_module._sse_clients) == before, (
            "closing the response must still reach the generator's cleanup"
        )


class _SpyEnviron(dict):
    """An environ that records which keys the middleware reads (iteration counts as every key)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.read: set[str] = set()

    def __getitem__(self, key):
        self.read.add(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        self.read.add(key)
        return super().get(key, default)

    def __contains__(self, key):
        self.read.add(key)
        return super().__contains__(key)

    def _everything(self):
        self.read.add("<iterated>")
        return list(super().keys())

    def __iter__(self):
        return iter(self._everything())

    def items(self):
        self._everything()
        return super().items()

    def keys(self):
        return self._everything()

    def values(self):
        self._everything()
        return super().values()


class TestMiddlewareContract:
    @staticmethod
    def _environ(path: str = "/x", **extra):
        environ = EnvironBuilder(path=path).get_environ()
        environ.update(extra)
        return environ

    @staticmethod
    def _start_response(status, headers, exc_info=None):
        return lambda data: None

    def test_only_the_method_path_and_query_are_read(self, timing_records):
        class UnreadableBody:
            def read(self, *_a):
                raise AssertionError("the request body must never be read")

        def inner(_environ, start_response):
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"ok"]

        environ = _SpyEnviron(
            self._environ(
                "/x?token=abc",
                HTTP_COOKIE=f"session={_SESSION_COOKIE}",
                HTTP_AUTHORIZATION="Bearer abc",
                CONTENT_LENGTH="3",
            )
        )
        environ["wsgi.input"] = UnreadableBody()
        wrapped = timing_log.AccessLogMiddleware(inner)

        result = wrapped(environ, self._start_response)
        list(result)
        getattr(result, "close", lambda: None)()

        assert environ.read <= {"REQUEST_METHOD", "PATH_INFO", "QUERY_STRING"}, (
            f"the middleware read more of the request than it may: {sorted(environ.read)}"
        )
        line = _one(timing_records.of("access"))
        assert line.words == ("GET", "/x?token=***")

    def test_the_response_is_not_consumed_until_the_server_iterates_and_close_is_forwarded(
        self, timing_records
    ):
        state = {"yielded": 0, "closed": False}

        def body():
            try:
                for chunk in (b"abc", b"de"):
                    state["yielded"] += 1
                    yield chunk
            finally:
                state["closed"] = True

        def inner(environ, start_response):
            start_response("200 OK", [("Content-Type", "text/plain")])
            return body()

        result = timing_log.AccessLogMiddleware(inner)(self._environ(), self._start_response)

        assert state["yielded"] == 0, "the middleware must not read the response eagerly"
        assert next(iter(result)) == b"abc"
        result.close()

        assert state["closed"] is True, "close() must reach the inner iterable"
        line = _one(timing_records.of("access"))
        assert line.fields["bytes"] == "3", "the size counts only what the server read"
        assert line.fields["status"] == "200"

    def test_an_exception_escaping_the_app_is_logged_by_type_only_and_re_raised(
        self, timing_records
    ):
        def inner(environ, start_response):
            raise ValueError(_ROUTE_ERROR_MARKER)

        wrapped = timing_log.AccessLogMiddleware(inner)

        with pytest.raises(ValueError):
            wrapped(self._environ("/x?token=abc"), self._start_response)

        line = _one(timing_records.of("access"))
        assert line.words == ("GET", "/x?token=***")
        assert line.fields["status"] == "500"
        assert line.fields["bytes"] == "0"
        assert line.fields["error"] == "ValueError"
        assert _ROUTE_ERROR_MARKER not in "\n".join(timing_records.lines)

    def test_a_second_start_response_with_exc_info_updates_the_status(self, timing_records):
        def inner(environ, start_response):
            start_response("200 OK", [("Content-Type", "text/plain")])
            try:
                raise OSError("late failure")
            except OSError:
                start_response("500 Internal Server Error", [], sys.exc_info())
            return [b"x"]

        result = timing_log.AccessLogMiddleware(inner)(self._environ(), self._start_response)
        list(result)
        getattr(result, "close", lambda: None)()

        line = _one(timing_records.of("access"))
        assert line.fields["status"] == "500"
