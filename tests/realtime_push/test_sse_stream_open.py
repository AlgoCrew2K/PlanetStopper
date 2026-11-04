"""RED tests: /api/events opens at once, carries a retry interval and frames each event.

The stream used to block in its first queue wait before yielding anything, so the status
line and headers only reached the client after the 15 s heartbeat. These tests connect with
a raw socket against a real threaded werkzeug server (the test client buffers the response
and cannot show when the first bytes leave) and, for framing, drive the generator seam.

No producer data: the stream carries only what the test publishes through a real CycleRecord.
"""

from __future__ import annotations

import json
import re
import socket
import threading
import time
from datetime import timedelta

import pytest
from werkzeug.serving import make_server

import app as app_module

_FIRST_BYTES_BUDGET_S = 1.0
_RETRY_LINE = b"retry: 5000\n\n"


@pytest.fixture()
def live_server(account_refresh_spy):
    """A real threaded HTTP server over the app; registered SSE clients are cleaned afterwards."""
    app_module.app.config["TESTING"] = True
    with app_module._sse_clients_lock:
        registered_before = list(app_module._sse_clients)
    server = make_server("127.0.0.1", 0, app_module.app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    sockets: list[socket.socket] = []

    def _connect(headers: str = "Accept: text/event-stream\r\n") -> socket.socket:
        sock = socket.create_connection(("127.0.0.1", server.server_port), timeout=3)
        sockets.append(sock)
        sock.sendall(
            f"GET /api/events HTTP/1.1\r\nHost: 127.0.0.1\r\n{headers}\r\n".encode("ascii")
        )
        return sock

    server.connect = _connect  # type: ignore[attr-defined]
    yield server
    for sock in sockets:
        sock.close()
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
    with app_module._sse_clients_lock:
        app_module._sse_clients[:] = registered_before


def _read_until(sock: socket.socket, needle: bytes, budget_s: float) -> tuple[bytes, float]:
    """Receive until `needle` is in the buffer or the budget runs out; returns (bytes, seconds)."""
    started = time.monotonic()
    buffer = b""
    while needle not in buffer:
        remaining = budget_s - (time.monotonic() - started)
        if remaining <= 0:
            break
        sock.settimeout(remaining)
        try:
            chunk = sock.recv(4096)
        except TimeoutError:
            break
        if not chunk:
            break
        buffer += chunk
    return buffer, time.monotonic() - started


_EVENT_FRAME = re.compile(rb"event: cycle-complete\ndata: (\{[^\n]*\})\n\n")


def _read_event(sock: socket.socket, budget_s: float) -> bytes:
    """Receive until one complete cycle-complete frame is in the buffer or the budget ends."""
    started = time.monotonic()
    buffer = b""
    while not _EVENT_FRAME.search(buffer):
        remaining = budget_s - (time.monotonic() - started)
        if remaining <= 0:
            break
        sock.settimeout(remaining)
        try:
            chunk = sock.recv(4096)
        except TimeoutError:
            break
        if not chunk:
            break
        buffer += chunk
    return buffer


class TestStreamOpensAtOnce:
    def test_status_headers_and_retry_arrive_within_a_second(self, live_server):
        sock = live_server.connect()

        data, elapsed = _read_until(sock, _RETRY_LINE, _FIRST_BYTES_BUDGET_S)

        assert _RETRY_LINE in data, (
            f"no `retry: 5000` within {_FIRST_BYTES_BUDGET_S}s of connecting (got {data!r}); "
            "the generator must yield it before its first queue wait"
        )
        head = data.split(b"\r\n\r\n", 1)[0].decode("latin-1")
        status_line = head.split("\r\n", 1)[0]
        assert re.match(r"HTTP/1\.[01] 200\b", status_line), status_line
        assert "content-type: text/event-stream" in head.lower()
        assert b": heartbeat" not in data, "the retry line must precede any heartbeat"
        assert elapsed < _FIRST_BYTES_BUDGET_S

    def test_a_client_that_has_the_retry_line_is_already_registered_for_events(
        self, live_server, make_cycle_record
    ):
        """Publishing right after the retry line is read must not lose the event: the queue is
        registered before the first yield, so there is no connect-to-register gap."""
        sock = live_server.connect()
        _, _ = _read_until(sock, _RETRY_LINE, _FIRST_BYTES_BUDGET_S)
        record = make_cycle_record(outcome="cycle", changed=True)

        app_module._publish_cycle(record)
        data = _read_event(sock, 2.0)

        match = _EVENT_FRAME.search(data)
        assert match, f"the event published right after the retry line never arrived: {data!r}"
        assert json.loads(match.group(1)) == record.event_payload()

    def test_a_heartbeat_follows_after_the_configured_silence(self, live_server, monkeypatch):
        assert app_module._SSE_HEARTBEAT_S == 15, (
            "heartbeats stay every 15 s unless a test shortens them"
        )
        monkeypatch.setattr(app_module, "_SSE_HEARTBEAT_S", 0.2)
        sock = live_server.connect()

        data, _ = _read_until(sock, b": heartbeat\n\n", 3.0)

        assert b": heartbeat\n\n" in data, f"no heartbeat after the patched silence: {data!r}"
        assert data.index(b"retry: 5000") < data.index(b": heartbeat"), (
            "the retry line comes first, then heartbeats"
        )

    def test_an_unauthenticated_client_gets_401_and_no_stream(self, live_server, monkeypatch):
        """The auth gate still runs before the stream opens: no retry line, no registration."""
        monkeypatch.setenv("DASHBOARD_PASSWORD", "rt-stream-test-pass-abc123")
        monkeypatch.setenv("SECRET_KEY", "rt-stream-test-secret-key-xyz789")
        monkeypatch.setattr(app_module, "_auth_check_enabled", True)
        with app_module._sse_clients_lock:
            registered = len(app_module._sse_clients)
        sock = live_server.connect("Accept: application/json, text/event-stream\r\n")

        data, _ = _read_until(sock, b"\r\n\r\n", 2.0)

        assert data.startswith(b"HTTP/1."), data
        assert b" 401 " in data.split(b"\r\n", 1)[0], data
        assert b"text/event-stream" not in data.lower()
        assert b"retry:" not in data
        with app_module._sse_clients_lock:
            assert len(app_module._sse_clients) == registered


# ---------------------------------------------------------------------------
# Generator seam: framing of each chunk and client deregistration
# ---------------------------------------------------------------------------


def _next_chunk(gen, budget_s: float = 1.0) -> str:
    """Pull one chunk without letting a blocked generator hang the test run."""
    box: dict = {}

    def _pull():
        try:
            box["chunk"] = next(gen)
        except BaseException as exc:  # noqa: BLE001  (re-raised on the test thread)
            box["error"] = exc

    puller = threading.Thread(target=_pull, daemon=True)
    puller.start()
    puller.join(budget_s)
    assert not puller.is_alive(), f"the generator produced no chunk within {budget_s}s"
    if "error" in box:
        raise box["error"]
    chunk = box["chunk"]
    return chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk


@pytest.fixture()
def open_stream():
    """Open the /api/events generator the way a request does; restore the client registry."""
    with app_module._sse_clients_lock:
        registered_before = list(app_module._sse_clients)
    generators: list = []

    def _open():
        with app_module.app.test_request_context("/api/events"):
            gen = iter(app_module.sse_events().response)
        generators.append(gen)
        return gen

    yield _open
    for gen in generators:
        try:
            gen.close()
        except ValueError:
            pass  # a puller thread still holds it: only reachable on a failed assertion
    with app_module._sse_clients_lock:
        app_module._sse_clients[:] = registered_before


class TestStreamFraming:
    def test_first_chunk_is_the_retry_line_and_the_queue_is_registered(self, open_stream):
        with app_module._sse_clients_lock:
            registered = len(app_module._sse_clients)

        gen = open_stream()
        chunk = _next_chunk(gen)

        assert chunk == _RETRY_LINE.decode()
        with app_module._sse_clients_lock:
            assert len(app_module._sse_clients) == registered + 1, (
                "the client queue must be registered before the first yield"
            )

    def test_an_event_is_written_as_name_line_then_one_json_data_line(
        self, open_stream, make_cycle_record, account_refresh_spy
    ):
        gen = open_stream()
        _next_chunk(gen)
        record = make_cycle_record(outcome="closed", changed=False)

        app_module._notify_cycle_complete(record)
        chunk = _next_chunk(gen)

        assert chunk.startswith("event: cycle-complete\ndata: ")
        assert chunk.endswith("\n\n")
        data_line = chunk[len("event: cycle-complete\ndata: ") : -2]
        assert "\n" not in data_line, "the JSON payload must stay on one data line"
        assert json.loads(data_line) == record.event_payload()

    def test_a_heartbeat_chunk_is_a_comment_line(self, open_stream, monkeypatch):
        monkeypatch.setattr(app_module, "_SSE_HEARTBEAT_S", 0.05)
        gen = open_stream()
        _next_chunk(gen)

        assert _next_chunk(gen) == ": heartbeat\n\n"

    def test_a_closed_client_is_removed_and_the_other_still_receives_the_event(
        self, open_stream, make_cycle_record, account_refresh_spy
    ):
        staying = open_stream()
        leaving = open_stream()
        _next_chunk(staying)
        _next_chunk(leaving)
        with app_module._sse_clients_lock:
            registered = len(app_module._sse_clients)
        leaving.close()
        record = make_cycle_record(outcome="cycle", changed=True)

        app_module._notify_cycle_complete(record)

        with app_module._sse_clients_lock:
            assert len(app_module._sse_clients) == registered - 1, (
                "closing the generator must deregister its queue"
            )
        chunk = _next_chunk(staying)
        assert json.loads(chunk.split("data: ", 1)[1]) == record.event_payload()


# ---------------------------------------------------------------------------
# An open stream ends with its session (revoked by a logout, or past its lifetime)
# ---------------------------------------------------------------------------
#
# The auth gate checks a session only when the stream is requested, so a stream opened before a
# logout kept delivering to a revoked cookie. The generator checks the session instant it captured
# when it was opened before it yields an event or a heartbeat, so a revoked or expired stream closes
# within one heartbeat and never sends another byte. The stream is opened the way a request opens
# it, carrying the cookie a real login issued; the clock is the session clock seam.

_SESSION_PASSWORD = "sse-session-pass-31c9"
_SESSION_SECRET = "sse-session-secret-b7d2"
_NS = 1_000_000_000
_SSE_DETAIL = "sentinel-sse-database-detail-5d0a"


class _SessionClock:
    def __init__(self) -> None:
        self.now = time.time_ns()

    def __call__(self) -> int:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += int(seconds * _NS)


@pytest.fixture()
def session_world(monkeypatch):
    """Auth gate on, a known credential and secret, the 12 hour lifetime, the session clock held."""
    monkeypatch.setenv("DASHBOARD_PASSWORD", _SESSION_PASSWORD)
    monkeypatch.setenv("SECRET_KEY", _SESSION_SECRET)
    for name in ("DASHBOARD_PASSWORD_HASH", "FLASK_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    monkeypatch.setitem(app_module.app.config, "SESSION_COOKIE_SECURE", False)
    monkeypatch.setattr(app_module.app, "permanent_session_lifetime", timedelta(hours=12))
    app_module.app.config["TESTING"] = True
    if not hasattr(app_module, "_session_now_ns"):
        pytest.fail("app.py must expose _session_now_ns() -> int, the clock seam of the session")
    clock = _SessionClock()
    monkeypatch.setattr(app_module, "_session_now_ns", clock)
    return clock


def _sign_in():
    """A client signed in by the real login, and the cookie value it holds."""
    client = app_module.app.test_client()
    response = client.post("/login", data={"password": _SESSION_PASSWORD})
    assert response.status_code == 302, f"login answered {response.status_code}"
    return client, client.get_cookie(app_module.app.config["SESSION_COOKIE_NAME"]).value


def _sign_out(client) -> None:
    response = client.post("/logout")
    assert response.status_code in (302, 303), f"POST /logout answered {response.status_code}"


@pytest.fixture()
def open_session_stream():
    """Open the /api/events generator in a request that carries a session cookie."""
    with app_module._sse_clients_lock:
        registered_before = list(app_module._sse_clients)
    generators: list = []

    def _open(cookie_value: str):
        name = app_module.app.config["SESSION_COOKIE_NAME"]
        with app_module.app.test_request_context(
            "/api/events", headers={"Cookie": f"{name}={cookie_value}"}
        ):
            gen = iter(app_module.sse_events().response)
        generators.append(gen)
        return gen

    yield _open
    for gen in generators:
        try:
            gen.close()
        except ValueError:
            pass
    with app_module._sse_clients_lock:
        app_module._sse_clients[:] = registered_before


def _assert_ends(gen) -> None:
    with pytest.raises(StopIteration):
        _next_chunk(gen)


class TestAnOpenStreamEndsWithItsSession:
    def test_a_stream_on_a_valid_session_keeps_its_retry_heartbeats_and_events(
        self,
        session_world,
        open_session_stream,
        monkeypatch,
        make_cycle_record,
        account_refresh_spy,
    ):
        monkeypatch.setattr(app_module, "_SSE_HEARTBEAT_S", 0.05)
        _, cookie = _sign_in()
        gen = open_session_stream(cookie)

        assert _next_chunk(gen) == _RETRY_LINE.decode()
        assert _next_chunk(gen) == ": heartbeat\n\n"
        record = make_cycle_record(outcome="cycle", changed=True)
        app_module._notify_cycle_complete(record)
        chunk = _next_chunk(gen)
        assert json.loads(chunk.split("data: ", 1)[1]) == record.event_payload()
        assert _next_chunk(gen) == ": heartbeat\n\n"

    def test_signing_out_closes_the_stream_before_another_heartbeat(
        self, session_world, open_session_stream, monkeypatch
    ):
        monkeypatch.setattr(app_module, "_SSE_HEARTBEAT_S", 0.05)
        client, cookie = _sign_in()
        gen = open_session_stream(cookie)
        assert _next_chunk(gen) == _RETRY_LINE.decode()
        session_world.advance(1)

        _sign_out(client)

        _assert_ends(gen)

    def test_a_closed_stream_is_deregistered(self, session_world, open_session_stream, monkeypatch):
        monkeypatch.setattr(app_module, "_SSE_HEARTBEAT_S", 0.05)
        client, cookie = _sign_in()
        with app_module._sse_clients_lock:
            registered = len(app_module._sse_clients)
        gen = open_session_stream(cookie)
        _next_chunk(gen)
        with app_module._sse_clients_lock:
            assert len(app_module._sse_clients) == registered + 1
        session_world.advance(1)
        _sign_out(client)

        _assert_ends(gen)

        with app_module._sse_clients_lock:
            assert len(app_module._sse_clients) == registered

    def test_a_stream_ends_when_its_session_passes_the_lifetime(
        self, session_world, open_session_stream, monkeypatch
    ):
        monkeypatch.setattr(app_module, "_SSE_HEARTBEAT_S", 0.05)
        _, cookie = _sign_in()
        session_world.advance(12 * 3600 - 10)
        gen = open_session_stream(cookie)
        assert _next_chunk(gen) == _RETRY_LINE.decode()
        assert _next_chunk(gen) == ": heartbeat\n\n", (
            "10 seconds before the lifetime it still lives"
        )

        session_world.advance(20)

        _assert_ends(gen)

    def test_both_streams_of_two_sessions_close_when_one_signs_out(
        self, session_world, open_session_stream, monkeypatch
    ):
        monkeypatch.setattr(app_module, "_SSE_HEARTBEAT_S", 0.05)
        client_a, cookie_a = _sign_in()
        session_world.advance(1)
        _, cookie_b = _sign_in()
        session_world.advance(1)
        stream_a, stream_b = open_session_stream(cookie_a), open_session_stream(cookie_b)
        _next_chunk(stream_a)
        _next_chunk(stream_b)

        _sign_out(client_a)

        _assert_ends(stream_a)
        _assert_ends(stream_b)

    def test_a_revoked_stream_is_sent_no_event_and_a_stream_of_a_later_login_is(
        self,
        session_world,
        open_session_stream,
        monkeypatch,
        make_cycle_record,
        account_refresh_spy,
    ):
        monkeypatch.setattr(app_module, "_SSE_HEARTBEAT_S", 5)
        client_a, cookie_a = _sign_in()
        session_world.advance(1)
        revoked = open_session_stream(cookie_a)
        _next_chunk(revoked)
        _sign_out(client_a)
        session_world.advance(1)
        _, cookie_c = _sign_in()
        valid = open_session_stream(cookie_c)
        _next_chunk(valid)
        record = make_cycle_record(outcome="cycle", changed=True)

        app_module._notify_cycle_complete(record)

        chunk = _next_chunk(valid)
        assert json.loads(chunk.split("data: ", 1)[1]) == record.event_payload()
        _assert_ends(revoked)

    def test_a_stream_still_closes_when_the_revocation_could_not_be_recorded(
        self, session_world, open_session_stream, monkeypatch
    ):
        import sqlite3

        import database

        monkeypatch.setattr(app_module, "_SSE_HEARTBEAT_S", 0.05)
        client, cookie = _sign_in()
        gen = open_session_stream(cookie)
        _next_chunk(gen)

        def _raise(_floor):
            raise sqlite3.OperationalError(_SSE_DETAIL)

        monkeypatch.setattr(database, "record_session_revocation", _raise)
        session_world.advance(1)
        _sign_out(client)

        _assert_ends(gen)

    def test_with_the_gate_off_a_stream_without_a_session_keeps_going(
        self, open_stream, monkeypatch
    ):
        """The suite's default (gate off) must not end streams that carry no session."""
        monkeypatch.setattr(app_module, "_auth_check_enabled", False)
        monkeypatch.setattr(app_module, "_SSE_HEARTBEAT_S", 0.05)
        gen = open_stream()
        assert _next_chunk(gen) == _RETRY_LINE.decode()
        assert _next_chunk(gen) == ": heartbeat\n\n"
        assert _next_chunk(gen) == ": heartbeat\n\n"


class TestAReconnectWithARevokedCookieIsRefused:
    def test_a_revoked_cookie_gets_401_and_no_stream(self, session_world, live_server):
        client, cookie = _sign_in()
        session_world.advance(1)
        _sign_out(client)
        with app_module._sse_clients_lock:
            registered = len(app_module._sse_clients)
        name = app_module.app.config["SESSION_COOKIE_NAME"]
        sock = live_server.connect(
            f"Cookie: {name}={cookie}\r\nAccept: application/json, text/event-stream\r\n"
        )

        data, _ = _read_until(sock, b"\r\n\r\n", 2.0)

        assert b" 401 " in data.split(b"\r\n", 1)[0], data
        assert b"text/event-stream" not in data.lower() and b"retry:" not in data
        with app_module._sse_clients_lock:
            assert len(app_module._sse_clients) == registered

    def test_a_valid_cookie_gets_the_stream(self, session_world, live_server):
        _, cookie = _sign_in()
        name = app_module.app.config["SESSION_COOKIE_NAME"]
        sock = live_server.connect(f"Cookie: {name}={cookie}\r\nAccept: text/event-stream\r\n")

        data, _ = _read_until(sock, _RETRY_LINE, _FIRST_BYTES_BUDGET_S)

        assert re.match(rb"HTTP/1\.[01] 200\b", data), data
        assert _RETRY_LINE in data
