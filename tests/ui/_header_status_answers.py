"""Answers of the real ``GET /api/header-status`` route and of the real hooks, for page-script tests.

TEST-ONLY. Every body is produced at call time by the real Flask app through its test client, at a
server instant the caller names, so no header body is ever typed into a test. The route reads the
Eastern clock through ``app._now_et`` (the daemon's one wall-clock seam), its liveness fields from
``app._health_status`` and the engine's last cycle from ``database.load_state``; the mode and
account come from ``_build_meta`` over a patched ``.env`` reader, so the developer ``.env`` is never
read. An answer is a route spec for ``tests/ui/_page_js_harness`` with ``date`` set to the server
instant, as a real response's ``Date`` header is.
"""

from __future__ import annotations

import os
import re
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import app as app_module
from tests.ui._page_js_harness import answer, render_page

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
CHROME_JS = Path(__file__).resolve().parents[2] / "static" / "chrome.js"

# Account ids are placeholders in the shape the .env holds.
ACCOUNT_ENV = {"ACCOUNT_INDIVIDUAL": "0123abcd-0000-4000-8000-000000000001"}


def et(hour: int, minute: int, second: int = 0, *, day: int = 22) -> datetime:
    """A Thursday-style trading instant in October 2026, Eastern time."""
    return datetime(2026, 10, day, hour, minute, second, tzinfo=ET)


def wall_of(moment: datetime) -> str:
    """The ISO text the page harness takes as its starting wall clock."""
    return moment.astimezone(UTC).isoformat()


def chrome_text_map(name: str) -> dict[str, str]:
    """The ``name = { key: 'text', ... }`` object literal of ``static/chrome.js``, parsed."""
    source = CHROME_JS.read_text(encoding="utf-8")
    block = re.search(rf"var {name} = \{{(.*?)\n\}};", source, flags=re.S)
    assert block, f"static/chrome.js defines no {name} object"
    return dict(re.findall(r"(\w+):\s*'([^']*)'", block.group(1)))


def chrome_text(name: str) -> str:
    source = CHROME_JS.read_text(encoding="utf-8")
    match = re.search(rf"var {name} = '([^']*)';", source)
    assert match, f"static/chrome.js defines no {name} string"
    return match.group(1)


def header_answer(
    at: datetime,
    *,
    cycle_age_s: float | None = 20.0,
    reasons: list[str] | None = None,
    live: bool = False,
    accounts: dict | None = None,
    daemon_started_at: str | None = None,
    delay_ms: int | None = None,
    hold: str | None = None,
) -> dict:
    """What ``GET /api/header-status`` answers at server instant ``at`` (a route spec)."""
    env = {"LIVE_EXECUTION": "True" if live else "False", **(accounts or {})}
    state = {}
    if cycle_age_s is not None:
        state["last_successful_cycle_at"] = (at - timedelta(seconds=cycle_age_s)).isoformat()
    with ExitStack() as stack:
        stack.enter_context(patch.object(app_module, "_now_et", lambda: at))
        stack.enter_context(patch.object(app_module.database, "load_state", lambda: dict(state)))
        stack.enter_context(
            patch.object(app_module._dotenv_module, "dotenv_values", lambda *_a, **_k: dict(env))
        )
        if reasons is not None:
            stack.enter_context(
                patch.object(
                    app_module,
                    "_health_status",
                    lambda: {
                        "status": "degraded" if reasons else "ok",
                        "degraded_reasons": list(reasons),
                        "scheduler_last_tick_at": at.isoformat(),
                        "engine_running_since": None,
                        "last_cycle": None,
                    },
                )
            )
        if daemon_started_at is not None:
            stack.enter_context(patch.object(app_module, "_DAEMON_STARTED_AT", daemon_started_at))
        app_module.app.config["TESTING"] = True
        with app_module.app.test_client() as client:
            response = client.get("/api/header-status")
    assert response.status_code != 404, "GET /api/header-status is not registered"
    assert response.status_code == 200, f"GET /api/header-status answered {response.status_code}"
    extra: dict = {"date": at.timestamp() * 1000}
    if delay_ms:
        extra["delay_ms"] = delay_ms
    if hold:
        extra["hold"] = hold
    return answer(response, **extra)


def lapsed_session_answer(path: str = "/api/header-status") -> dict:
    """The 401 the real auth hook gives an API request that has no session."""
    previous = app_module._auth_check_enabled
    app_module._auth_check_enabled = True
    try:
        with (
            patch.dict(
                os.environ, {"DASHBOARD_PASSWORD": "placeholder-password", "SECRET_KEY": "k"}
            ),
            app_module.app.test_client() as client,
        ):
            response = client.get(path)
    finally:
        app_module._auth_check_enabled = previous
    assert response.status_code == 401, f"the auth hook answered {response.status_code}, not 401"
    return answer(response)


def server_error_answer() -> dict:
    """The 500 the real route gives when its state read raises (Flask's handler, exceptions off)."""

    def boom():
        raise RuntimeError("state read failed: /private/path/state.db")

    previous = app_module.app.config.get("PROPAGATE_EXCEPTIONS")
    app_module.app.config["PROPAGATE_EXCEPTIONS"] = False
    app_module.app.config["TESTING"] = True
    try:
        with (
            patch.object(app_module, "_now_et", lambda: et(10, 43)),
            patch.object(app_module.database, "load_state", boom),
            app_module.app.test_client() as client,
        ):
            response = client.get("/api/header-status")
    finally:
        app_module.app.config["PROPAGATE_EXCEPTIONS"] = previous
    assert response.status_code != 404, "GET /api/header-status is not registered"
    assert response.status_code == 500, f"a failed state read answered {response.status_code}"
    return answer(response)


PAGES_WITH_A_HEADER = ("/", "/history", "/performance", "/ai-advisor", "/settings")


def nav_markup(path: str) -> str:
    """The header ``<nav>`` element the real app renders for ``path`` at a frozen host clock.

    The clock stands on a Saturday (market closed), so every server-rendered word in the header is
    the same on every run.
    """
    from tests.ui._history_answers import frozen_host_clock

    with frozen_host_clock(stdlib=False):
        html = render_page(path)
    match = re.search(r'<nav data-testid="studio-nav".*?</nav>', html, flags=re.S)
    assert match, f"{path} renders no header nav"
    return match.group(0)
