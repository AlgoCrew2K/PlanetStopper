"""Real answers of ``/api/history/<days>`` and of the hooks that refuse it, at a frozen host clock.

TEST-ONLY. The History bodies are produced by the real route over the captured post-mortem days
(``tests/ui/_shell_support.seed_history_exits``: the six captured days, each trigger carrying the one
key the read-time validity guard needs). The host clock stands at 2026-07-25 12:00 ET, so the 30-day
window holds the last three captured days and the 90-day window all six, whatever day the test runs.
"""

from __future__ import annotations

import datetime as _datetime_module
import os
from contextlib import ExitStack, contextmanager
from unittest.mock import patch
from zoneinfo import ZoneInfo

import analytics
import app as app_module
from tests.ui._page_js_harness import answer

ET = ZoneInfo("America/New_York")
NOW_ET = _datetime_module.datetime(2026, 7, 25, 12, 0, tzinfo=ET)


@contextmanager
def frozen_host_clock(instant=NOW_ET, *, stdlib=True):
    """The host clock reads ``instant`` for every date the app and analytics take.

    With ``stdlib=False`` only the names ``app`` and ``analytics`` hold are replaced, not the standard
    library's own classes: pages that build the market calendar need the real ones.
    """
    real_date = _datetime_module.date

    class _FrozenDate(real_date):
        @classmethod
        def today(cls):
            return instant.astimezone(_datetime_module.UTC).date()

    class _FrozenDatetime(_datetime_module.datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return instant.astimezone(tz)
            return instant.astimezone(_datetime_module.UTC).replace(tzinfo=None)

    with ExitStack() as stack:
        if stdlib:
            stack.enter_context(patch.object(_datetime_module, "datetime", _FrozenDatetime))
            stack.enter_context(patch.object(_datetime_module, "date", _FrozenDate))
        for module in (analytics, app_module):
            stack.enter_context(patch.object(module, "datetime", _FrozenDatetime, create=True))
            stack.enter_context(patch.object(module, "date", _FrozenDate, create=True))
        stack.enter_context(patch.object(app_module, "_now_et", lambda: instant.astimezone(ET)))
        yield instant


def history_body(days) -> dict:
    """The JSON ``GET /api/history/<days>`` returns at the frozen clock."""
    with frozen_host_clock(), app_module.app.test_client() as client:
        response = client.get(f"/api/history/{days}")
    assert response.status_code == 200, f"GET /api/history/{days} answered {response.status_code}"
    return response.get_json()


def history_answer(days, **extra) -> dict:
    return {"status": 200, "json": history_body(days), **extra}


def server_error_answer() -> dict:
    """The 500 HTML page Flask gives when the route's aggregation raises."""
    previous = app_module.app.config.get("PROPAGATE_EXCEPTIONS")
    app_module.app.config["PROPAGATE_EXCEPTIONS"] = False

    def boom(*_a, **_k):
        raise RuntimeError("aggregation failed at /private/state")

    try:
        with (
            patch.object(analytics, "get_history_summary", boom),
            app_module.app.test_client() as client,
        ):
            response = client.get("/api/history/30")
    finally:
        app_module.app.config["PROPAGATE_EXCEPTIONS"] = previous
    assert response.status_code == 500, f"the failed aggregation answered {response.status_code}"
    return answer(response)


def unavailable_answer(path: str) -> dict:
    """The 503 JSON the auth hook gives an API request when no credential is configured."""
    environ = {k: v for k, v in os.environ.items() if not k.startswith("DASHBOARD_PASSWORD")}
    environ["SECRET_KEY"] = "k"
    previous = app_module._auth_check_enabled
    app_module._auth_check_enabled = True
    try:
        with patch.dict(os.environ, environ, clear=True), app_module.app.test_client() as client:
            response = client.get(path)
    finally:
        app_module._auth_check_enabled = previous
    assert response.status_code == 503, f"the auth hook answered {response.status_code}, not 503"
    return answer(response)
