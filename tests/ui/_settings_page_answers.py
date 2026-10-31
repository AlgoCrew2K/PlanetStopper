"""Real answers for the /settings page tests.

TEST-ONLY. ``GET /api/settings`` and ``POST /api/settings`` are the real routes over a temporary
``.env`` file (``app.ENV_FILE_PATH`` is replaced, so the developer file is neither read nor written)
and the captured production ``bot_state`` written through ``database.save_state``;
``armed_answer`` is the real ``GET /api/armed-symphonies`` over the same state with a few arming flags
set (named-field edits of real records, ``tests/fixtures/refresh_timing/04-c6/PROVENANCE.md``).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import app as app_module
import database
from tests._ux_truth_support import load_captured_bot_state
from tests.ui._page_js_harness import answer

ENV_TEXT = "LIVE_EXECUTION=False\nEXECUTION_START_TIME=09:30\nEXIT_AUTHORITY=per_symphony\n"


def seed_state(*, flags: bool = False) -> dict:
    state = load_captured_bot_state()
    ids = sorted(k for k, v in state.items() if isinstance(v, dict) and "name" in v)
    if flags:
        state[ids[0]]["armed"] = True
        state[ids[1]]["tp_armed"] = True
        state[ids[2]]["triggered"] = True
    database.save_state(state)
    return state


def _with_env(call):
    handle, name = tempfile.mkstemp(suffix=".env")
    os.close(handle)
    path = Path(name)
    try:
        path.write_text(ENV_TEXT, encoding="utf-8")
        with patch.object(app_module, "ENV_FILE_PATH", str(path)):
            return call()
    finally:
        path.unlink(missing_ok=True)


def settings_get_answer() -> dict:
    def call():
        with app_module.app.test_client() as client:
            return client.get("/api/settings")

    response = _with_env(call)
    assert response.status_code == 200, f"GET /api/settings answered {response.status_code}"
    return answer(response)


def settings_save_answer() -> dict:
    """The 200 ``POST /api/settings`` gives a valid change of the start time."""

    def call():
        with app_module.app.test_client() as client:
            return client.post(
                "/api/settings",
                json={"globals": {"EXECUTION_START_TIME": "09:35"}, "symphonies": {}},
            )

    response = _with_env(call)
    assert response.status_code == 200, f"POST /api/settings answered {response.status_code}"
    return answer(response)


def armed_answer() -> dict:
    with app_module.app.test_client() as client:
        response = client.get("/api/armed-symphonies")
    assert response.status_code != 404, "GET /api/armed-symphonies is not registered"
    assert response.status_code == 200, f"GET /api/armed-symphonies answered {response.status_code}"
    return answer(response)


def server_error_settings_answer() -> dict:
    """The 500 page Flask gives when the settings read raises (the env reader fails)."""

    def boom(*_a, **_k):
        raise RuntimeError("cannot read /private/path/.env")

    previous = app_module.app.config.get("PROPAGATE_EXCEPTIONS")
    app_module.app.config["PROPAGATE_EXCEPTIONS"] = False
    try:
        with (
            patch.object(app_module, "dotenv_values", boom),
            app_module.app.test_client() as client,
        ):
            response = client.get("/api/settings")
    finally:
        app_module.app.config["PROPAGATE_EXCEPTIONS"] = previous
    assert response.status_code == 500, f"the failed read answered {response.status_code}"
    return answer(response)
