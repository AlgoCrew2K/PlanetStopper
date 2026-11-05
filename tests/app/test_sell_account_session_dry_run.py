"""A signed-in operator posting the liquidation request on a dry-run install: the route starts
no liquidation thread, calls no liquidation function and opens no connection.

The panic-confirm module is byte-pinned, so these cases live beside it and reuse its helpers.
"""

from __future__ import annotations

import socket
from unittest.mock import MagicMock

import pytest

import app as app_module
from tests.app.test_sell_account_panic_confirm import _live_env, _load

# ---------------------------------------------------------------------------
# A signed-in operator with the CSRF token: dry run starts no liquidation thread
# ---------------------------------------------------------------------------

_OPERATOR_PASSWORD = "panic-session-pass-5d2e9a"
_OPERATOR_SECRET = "panic-session-secret-3c7f1b"


@pytest.fixture
def operator(monkeypatch, tmp_path):
    """A browser signed in through the real login with the auth and CSRF gates on."""
    monkeypatch.setenv("DASHBOARD_PASSWORD", _OPERATOR_PASSWORD)
    monkeypatch.setenv("SECRET_KEY", _OPERATOR_SECRET)
    for name in ("DASHBOARD_PASSWORD_HASH", "FLASK_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("EXECUTION_START_TIME=09:35\n", encoding="utf-8")
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", str(env_file))
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    monkeypatch.setitem(app_module.app.config, "SESSION_COOKIE_SECURE", False)
    app_module.app.config["TESTING"] = True
    browser = app_module.app.test_client()
    login = browser.post("/login", data={"password": _OPERATOR_PASSWORD})
    assert login.status_code == 302, f"login answered {login.status_code}"
    monkeypatch.setattr(app_module, "_csrf_check_enabled", True)
    token = browser.get("/api/csrf-token").get_json()["csrf_token"]
    assert token, "GET /api/csrf-token returned no token"
    return browser, token


class _Recorders:
    """Everything the liquidation route could start or reach, replaced by recorders."""

    def __init__(self) -> None:
        self.threads: list[dict] = []
        self.liquidations: list[tuple] = []
        self.posts: list[str] = []
        self.gets: list[str] = []

    def liquidation(self, *args, **kwargs):
        self.liquidations.append(args)

    def install(self, monkeypatch, env: dict) -> None:
        recorders = self

        class RecordingThread:
            def __init__(self, target=None, args=(), **kw):
                recorders.threads.append({"target": target, "args": args})

            def start(self):
                pass

        def _post(url, *a, **kw):
            recorders.posts.append(url)
            return MagicMock(status_code=200)

        def _get(url, *a, **kw):
            recorders.gets.append(url)
            return MagicMock(status_code=200)

        def _no_socket(*a, **kw):
            raise AssertionError("the route opened a network connection")

        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: env)
        monkeypatch.setattr(app_module.threading, "Thread", RecordingThread)
        monkeypatch.setattr(app_module, "perform_account_liquidation", self.liquidation)
        monkeypatch.setattr(app_module.requests, "post", _post)
        monkeypatch.setattr(app_module.requests, "get", _get)
        monkeypatch.setattr(socket.socket, "connect", _no_socket)


def _post_liquidation(browser, token):
    return browser.post(
        "/api/sell_account",
        json=_load("valid_live_request.json"),
        headers={"X-CSRF-Token": token},
    )


def test_sell_account_dry_run_with_a_session_and_csrf_token_starts_no_thread_and_no_liquidation(
    operator, monkeypatch
):
    """The Then has two legs: the dry-run answer, and no thread or liquidation call behind it."""
    browser, token = operator
    recorders = _Recorders()
    with monkeypatch.context() as patched:
        recorders.install(patched, _live_env(False))
        resp = _post_liquidation(browser, token)

    assert resp.status_code != 404, "/api/sell_account is not routed"
    assert resp.status_code == 200, f"answered {resp.status_code}: {resp.get_data(as_text=True)}"
    body = resp.get_json()
    assert body.get("status") == "dry_run"
    assert body.get("executed") is False
    assert recorders.threads == [], "dry run constructed a thread"
    assert recorders.liquidations == [], "dry run called perform_account_liquidation"
    assert not [u for u in recorders.posts if "go-to-cash" in u], recorders.posts


def test_sell_account_live_control_reaches_the_same_recorders_and_no_network(operator, monkeypatch):
    """With LIVE_EXECUTION true the same request constructs the thread on the liquidation function,
    so the recorders in the dry-run test can fire; the Discord audit post reaches only a recorder."""
    browser, token = operator
    recorders = _Recorders()
    with monkeypatch.context() as patched:
        recorders.install(patched, _live_env(True))
        resp = _post_liquidation(browser, token)

    assert resp.status_code == 200, f"answered {resp.status_code}: {resp.get_data(as_text=True)}"
    assert resp.get_json().get("status") == "success"
    assert len(recorders.threads) == 1, "the live request did not construct the liquidation thread"
    assert recorders.threads[0]["target"] == recorders.liquidation
    assert recorders.posts == [_live_env(True)["DISCORD_WEBHOOK_URL"]]
