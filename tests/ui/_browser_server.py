"""In-process HTTP server for browser tests (the ``browser`` marker).

A browser test that seeds its own data starts the Flask app in this process on
an ephemeral loopback port, so Firefox talks to the same code and the same
per-test database the test wrote to. The server reads ``DB_PATH`` at request
time, so the per-test database the root conftest sets is the one it serves.

Nothing here imports Playwright; a test imports it inside its own fixture.
Outbound network from the page is refused by the test's route handler, not by
this module.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass

_SHUTDOWN_JOIN_SECONDS = 10


@dataclass
class BrowserServer:
    """A running in-process server: ``base_url`` has no trailing slash."""

    base_url: str
    _server: object
    _thread: threading.Thread

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=_SHUTDOWN_JOIN_SECONDS)


def start_server() -> BrowserServer:
    """Serve ``app.app`` on 127.0.0.1 at a free port, threaded so static files load in parallel."""
    from werkzeug.serving import make_server

    import app as app_module

    server = make_server("127.0.0.1", 0, app_module.app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, name="browser-test-server", daemon=True)
    thread.start()
    return BrowserServer(
        base_url=f"http://127.0.0.1:{server.server_port}", _server=server, _thread=thread
    )


@contextmanager
def running_server():
    server = start_server()
    try:
        yield server
    finally:
        server.stop()
