"""Shared pieces of the dashboard refresh browser modules (marker ``browser``).

The page is the real ``GET /`` render of the harness world (tests/dashboard/_index_js_harness.py) and
every API answer is the real producer's output for that world, served by Playwright route handlers; the
app in this process (tests/ui/_browser_server.py) serves the static scripts and nothing else. Outbound
requests from the page (Chart.js's CDN tag) are refused, so the charts are absent and the scripts run as
they do when the library fails to load. Never skips: a missing Playwright or Firefox fails with a message.

What a handler does is set per test: ``probe.state`` is the body ``/api/state`` answers (a dict, or a
callable ``route -> None`` for a failure), ``probe.events`` answers ``/api/events`` (held by default: the
stream stays CONNECTING with no events), ``probe.strips`` and ``probe.summaries`` map a window token to
its body. ``probe.requests`` logs ``(method, path-with-query)`` for every request the page sends to the app.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from tests.dashboard import _index_js_harness as hx
from tests.ui._browser_server import running_server
from tests.ui.test_dashboard_charts_browser import firefox  # noqa: F401  (module-scoped fixture)

PAGE_TIMEOUT_MS = 20_000


@pytest.fixture(scope="module")
def world() -> dict:
    return hx.get_world("default")


@pytest.fixture
def server():
    with running_server() as running:
        yield running


def _json(route, body, status: int = 200) -> None:
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def new_probe(browser, server, world, *, context=None):
    """A page wired to answer the dashboard's requests from ``world``. Returns the probe namespace."""
    context = context or browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.set_default_timeout(PAGE_TIMEOUT_MS)
    probe = SimpleNamespace(
        page=page,
        context=context,
        server=server,
        world=world,
        state=world["state"],
        events=None,
        strips=dict(world["strips"]),
        summaries=dict(world["summaries"]),
        requests=[],
        held=[],
        navigations=[],
        page_errors=[],
    )

    def serve(route):
        request = route.request
        parts = urlsplit(request.url)
        if not request.url.startswith(server.base_url):
            route.abort()
            return
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        probe.requests.append((request.method, path))
        if parts.path == "/":
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=world["html"])
        elif parts.path.startswith("/static/"):
            route.continue_()
        elif parts.path == "/api/csrf-token":
            _json(route, {"csrf_token": "browser-test-token"})
        elif parts.path == "/api/state":
            if callable(probe.state):
                probe.state(route)
            else:
                _json(route, probe.state)
        elif parts.path.startswith("/api/strip/"):
            _json(route, probe.strips[parts.path.rsplit("/", 1)[1]])
        elif parts.path == "/api/guard-alpha-summary":
            token = re.search(r"window=([a-z0-9]+)", parts.query or "")
            _json(route, probe.summaries[token.group(1) if token else "all"])
        elif parts.path.startswith("/api/hero-chart/"):
            _json(route, world["hero"][parts.path.rsplit("/", 1)[1]])
        elif parts.path.startswith("/api/chart/"):
            _json(route, world["charts"][parts.path.rsplit("/", 1)[1]])
        elif parts.path == "/api/candidate-alert":
            _json(route, {"new_valid_count": 0, "last_run": None})
        elif parts.path == "/api/events":
            if callable(probe.events):
                probe.events(route)
            else:
                probe.held.append(route)  # never answered: the stream stays CONNECTING
        else:
            _json(route, {})

    page.route("**/*", serve)
    page.on("pageerror", lambda exc: probe.page_errors.append(str(exc)))
    page.on("framenavigated", lambda frame: probe.navigations.append(frame.url))
    return probe


def wait_refresh(probe, count: int) -> None:
    probe.page.wait_for_function(
        "(n) => document.body && document.body.dataset.refreshCount === String(n)", arg=count
    )


def text(probe, selector: str) -> str:
    return " ".join((probe.page.locator(selector).first.inner_text() or "").split())


def count_requests(probe, method: str, prefix: str, since: int = 0) -> int:
    return sum(1 for m, p in probe.requests[since:] if m == method and p.startswith(prefix))
