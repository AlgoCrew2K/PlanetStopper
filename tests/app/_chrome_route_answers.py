"""The answers the real Flask app gives to the requests the header's controls send.

TEST-ONLY. Used by ``tests/app/test_chrome_header_honesty.py`` and by the base-record script
``tests/fixtures/chrome/capture_panic_request.py``. Every answer is taken from the real app
through its test client at call time, so the harness is never fed a hand-typed body. Each
function returns ``{"status", "content_type", "body"}`` (the body as the server sent it), the
shape ``tests/app/_chrome_node_harness`` scripts.

Nothing here reads or writes the developer ``.env``: ``dotenv_values`` (the route's reader) is
patched to a dict the caller controls, and the account list reads a temporary env file.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from unittest.mock import patch

import app as app_module

# The credentials the sell route reads from ``.env`` by file; placeholders, never real ones.
_ENV_LIVE_OFF = {
    "LIVE_EXECUTION": "False",
    "COMPOSER_KEY_ID": "placeholder-key-id",
    "COMPOSER_SECRET": "placeholder-secret",
    "DISCORD_WEBHOOK_URL": "",
}
_ENV_NO_CREDENTIALS = {
    "LIVE_EXECUTION": "False",
    "COMPOSER_KEY_ID": "",
    "COMPOSER_SECRET": "",
    "DISCORD_WEBHOOK_URL": "",
}
_ENV_LIVE_ON = dict(_ENV_LIVE_OFF, LIVE_EXECUTION="True")


def to_answer(response) -> dict:
    return {
        "status": response.status_code,
        "content_type": response.headers.get("Content-Type", ""),
        "body": response.get_data(as_text=True),
    }


def message_of(answer: dict) -> str:
    """The ``message`` a JSON answer carries (the text the operator is shown today)."""
    return json.loads(answer["body"])["message"]


def sell_payload(account_id: str, **overrides) -> dict:
    payload = {
        "account_id": account_id,
        "confirm_account_id": account_id,
        "confirm_phrase": "LIQUIDATE",
    }
    payload.update(overrides)
    return payload


@contextmanager
def _client(*, csrf: bool = False, auth: bool = False, propagate: bool | None = None):
    previous_csrf = app_module._csrf_check_enabled
    previous_auth = app_module._auth_check_enabled
    previous_propagate = app_module.app.config.get("PROPAGATE_EXCEPTIONS")
    previous_testing = app_module.app.config.get("TESTING")
    app_module._csrf_check_enabled = csrf
    app_module._auth_check_enabled = auth
    app_module.app.config["TESTING"] = True
    if propagate is not None:
        app_module.app.config["PROPAGATE_EXCEPTIONS"] = propagate
    try:
        with app_module.app.test_client() as client:
            yield client
    finally:
        app_module._csrf_check_enabled = previous_csrf
        app_module._auth_check_enabled = previous_auth
        app_module.app.config["TESTING"] = previous_testing
        app_module.app.config["PROPAGATE_EXCEPTIONS"] = previous_propagate


def csrf_token_answer() -> dict:
    with _client() as client:
        return to_answer(client.get("/api/csrf-token"))


def accounts_answer(account_ids: list[str]) -> dict:
    """``GET /api/accounts`` over a temporary env file holding ``account_ids`` (up to three)."""
    keys = ["ACCOUNT_INDIVIDUAL", "ACCOUNT_ROTH", "ACCOUNT_TRAD"]
    lines = [f"{key}={value}" for key, value in zip(keys, account_ids, strict=False)]
    handle, path = tempfile.mkstemp(suffix=".env")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        with patch.object(app_module, "ENV_FILE_PATH", path), _client() as client:
            return to_answer(client.get("/api/accounts"))
    finally:
        os.unlink(path)


def sell_answer(payload: dict, *, env: dict | None = None) -> dict:
    """``POST /api/sell_account`` with the server's checks as they stand, liquidation inert.

    ``env`` is what the route reads from ``.env`` (default: credentials present, live off, so a
    request that passes the checks gets the dry-run answer). The Discord call and any
    liquidation thread are replaced, so nothing leaves the process.
    """
    env = _ENV_LIVE_OFF if env is None else env
    with (
        patch.object(app_module, "dotenv_values", lambda *_a, **_k: dict(env)),
        patch.object(app_module.requests, "post"),
        patch.object(app_module.threading, "Thread") as thread,
        _client() as client,
    ):
        answer = to_answer(client.post("/api/sell_account", json=payload))
        assert not thread.called, "no liquidation thread may start in these answers"
    return answer


def sell_answer_live_started(payload: dict) -> dict:
    """The accept answer when ``LIVE_EXECUTION`` is on; the liquidation thread is replaced."""
    with (
        patch.object(app_module, "dotenv_values", lambda *_a, **_k: dict(_ENV_LIVE_ON)),
        patch.object(app_module.requests, "post"),
        patch.object(app_module.threading, "Thread") as thread,
        _client() as client,
    ):
        answer = to_answer(client.post("/api/sell_account", json=payload))
        assert thread.return_value.start.called, "the live branch must start its thread"
    return answer


def sell_answer_without_credentials(payload: dict) -> dict:
    return sell_answer(payload, env=_ENV_NO_CREDENTIALS)


def sell_answer_server_error(payload: dict) -> dict:
    """The 500 Flask's own handler gives when the route's ``.env`` read raises."""

    def boom(*_a, **_k):
        raise RuntimeError("env read failed")

    with (
        patch.object(app_module, "dotenv_values", boom),
        _client(propagate=False) as client,
    ):
        return to_answer(client.post("/api/sell_account", json=payload))


def sell_answer_csrf_refused(payload: dict) -> dict:
    """The 403 the CSRF hook gives a POST without the token."""
    with _client(csrf=True) as client:
        return to_answer(client.post("/api/sell_account", json=payload))


def sell_answer_unauthenticated(payload: dict) -> dict:
    """The 401 the auth hook gives an API request with no session."""
    with (
        patch.dict(os.environ, {"DASHBOARD_PASSWORD": "placeholder-password", "SECRET_KEY": "k"}),
        _client(auth=True) as client,
    ):
        return to_answer(client.post("/api/sell_account", json=payload))


def sell_answer_misconfigured(payload: dict) -> dict:
    """The 503 the auth hook gives when no dashboard credential is configured."""
    environ = {k: v for k, v in os.environ.items() if not k.startswith("DASHBOARD_PASSWORD")}
    environ["SECRET_KEY"] = "k"
    with patch.dict(os.environ, environ, clear=True), _client(auth=True) as client:
        return to_answer(client.post("/api/sell_account", json=payload))


def trigger_answer() -> dict:
    with _client() as client:
        return to_answer(client.post("/api/trigger"))


def trigger_answer_csrf_refused() -> dict:
    with _client(csrf=True) as client:
        return to_answer(client.post("/api/trigger"))


def trigger_answer_unauthenticated() -> dict:
    with (
        patch.dict(os.environ, {"DASHBOARD_PASSWORD": "placeholder-password", "SECRET_KEY": "k"}),
        _client(auth=True) as client,
    ):
        return to_answer(client.post("/api/trigger"))


def chrome_oninput_gate() -> str:
    """The Confirm gate the rendered chrome carries on the phrase input (``oninput`` value)."""
    import html
    import re

    with _client() as client:
        page = client.get("/history").get_data(as_text=True)
    match = re.search(r'id="panic-phrase"[^>]*?\soninput="([^"]*)"', page, flags=re.DOTALL)
    assert match, "the rendered chrome has no phrase input with an oninput gate"
    return html.unescape(match.group(1))
