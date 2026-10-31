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
