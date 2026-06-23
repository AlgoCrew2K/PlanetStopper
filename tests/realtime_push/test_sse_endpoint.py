"""RED tests — AC-2: SSE endpoint exists and streams (dashboard-realtime-push).

These tests fail until rt-impl adds:
  - GET /api/events route returning text/event-stream
  - Auth gate applied (unauthenticated → 401 JSON)
  - _sse_clients module-level list registered on the route

No live network, no subprocess, no real DB writes.
All Composer / auth seams are mocked via conftest autouse fixtures.
"""

from __future__ import annotations

import app as app_module
import pytest


# ---------------------------------------------------------------------------
# AC-2: The SSE endpoint returns text/event-stream with 200 on authed request
# ---------------------------------------------------------------------------


class TestSseEndpointExists:
    def test_sse_endpoint_route_returns_200_with_event_stream_content_type(self, client):
        """GET /api/events must exist, return 200, and stream text/event-stream.

        Fails until the route is registered in app.py.
        Uses the test client's streaming mode — we don't consume the stream,
        just assert the response headers and status code.
        """
        # Abort the stream after the first chunk to avoid blocking the test.
        response = client.get("/api/events")
        assert response.status_code == 200, (
            f"GET /api/events must return 200; got {response.status_code}. "
            "rt-impl: add `GET /api/events` route returning a streaming response."
        )
        content_type = response.content_type or response.headers.get("Content-Type", "")
        assert "text/event-stream" in content_type, (
            f"GET /api/events must return Content-Type: text/event-stream; "
            f"got '{content_type}'. "
            "rt-impl: use Response(generate(), mimetype='text/event-stream')."
        )

    def test_sse_endpoint_returns_cache_control_no_cache_header(self, client):
        """GET /api/events must include Cache-Control: no-cache.

        SSE requires this so proxies don't buffer the event stream.
        Fails until the header is set on the response.
        """
        response = client.get("/api/events")
        cc = response.headers.get("Cache-Control", "")
        assert "no-cache" in cc, (
            f"GET /api/events must set 'Cache-Control: no-cache'; got '{cc}'. "
            "rt-impl: set headers['Cache-Control'] = 'no-cache' on the Response."
        )

    def test_sse_endpoint_returns_x_accel_buffering_no_header(self, client):
        """GET /api/events must include X-Accel-Buffering: no.

        Prevents nginx from buffering the stream on the droplet.
        Fails until the header is added.
        """
        response = client.get("/api/events")
        xab = response.headers.get("X-Accel-Buffering", "")
        assert xab.lower() == "no", (
            f"GET /api/events must set 'X-Accel-Buffering: no'; got '{xab}'. "
            "rt-impl: set headers['X-Accel-Buffering'] = 'no' on the Response."
        )

    def test_sse_endpoint_returns_401_when_auth_disabled(self, client_no_auth):
        """GET /api/events must return 401 JSON for unauthenticated requests.

        SSE is an /api/ route — the existing auth gate applies.
        Unauthenticated browser EventSource connections must be rejected with 401,
        consistent with all other /api/ routes.

        Fails until the route is under the _auth_before_request hook.
        """
        response = client_no_auth.get(
            "/api/events", headers={"Accept": "application/json, text/event-stream"}
        )
        assert response.status_code == 401, (
            f"Unauthenticated GET /api/events must return 401; got {response.status_code}. "
            "rt-impl: do NOT add /api/events to _AUTH_EXEMPT_ENDPOINTS — "
            "it must be protected by the existing _auth_before_request hook."
        )


class TestSseClientRegistry:
    def test_sse_clients_list_exists_on_app_module(self):
        """app module must expose _sse_clients (a list or similar container).

        This is the server-side registry of connected SSE generators.
        Fails until rt-impl adds `_sse_clients = []` (or equivalent) at module level.
        """
        assert hasattr(app_module, "_sse_clients"), (
            "app module must have a module-level `_sse_clients` attribute. "
            "rt-impl: add `_sse_clients: list = []` near the top of app.py."
        )

    def test_sse_clients_lock_exists_on_app_module(self):
        """app module must expose _sse_clients_lock (a threading.Lock or RLock).

        Required to safely append/remove from _sse_clients across threads.
        Fails until rt-impl adds the lock at module level.
        """
        import threading

        lock = getattr(app_module, "_sse_clients_lock", None)
        assert lock is not None, (
            "app module must have a module-level `_sse_clients_lock`. "
            "rt-impl: add `_sse_clients_lock = threading.Lock()` near _sse_clients."
        )
        assert isinstance(lock, (threading.Lock().__class__, threading.RLock().__class__)), (
            f"_sse_clients_lock must be a threading.Lock or RLock; got {type(lock).__name__}."
        )

    def test_notify_cycle_complete_function_exists(self):
        """app module must expose _notify_cycle_complete().

        Called by trigger_alpha_bot after subprocess exit to fan out cycle-complete
        events to all connected SSE clients.
        Fails until rt-impl adds this function.
        """
        assert hasattr(app_module, "_notify_cycle_complete"), (
            "app module must have `_notify_cycle_complete()`. "
            "rt-impl: add a non-blocking function that puts a sentinel on each "
            "registered client queue in _sse_clients."
        )
        assert callable(app_module._notify_cycle_complete), (
            "_notify_cycle_complete must be callable."
        )
