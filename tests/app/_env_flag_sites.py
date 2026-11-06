"""Drivers for the app's boolean environment-flag sites.

One implementation shared by ``tests/app/test_env_flag_parse_sites.py`` and by the ref-record
producer ``tests/fixtures/hygiene_waves_3_4/engine_math/produce_ref_records.py``, so the producer
records exactly the outcome the test later compares.

Every driver reads the ``.env`` through a controlled mapping (both ``app.dotenv_values`` and
``app._dotenv_module.dotenv_values`` are replaced, so a site reading either name sees only the
mapping and the developer ``.env`` is never read). Nothing here reaches a network: the HTTP layer
refuses every call that is not a recorded fake.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import requests

import app as app_module
import database

PANIC_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "app"
    / "panic_confirm"
    / "valid_live_request.json"
)

# The sites that call ``.lower()`` on the raw value with no guard (a bare key raises), by the
# name the record file keys them with.
BARE_KEY_SITES = (
    "header_status",
    "page_dashboard",
    "page_history",
    "page_performance",
    "page_settings",
    "api_state_open",
    "api_state_frozen",
    "sell_account",
    "symphony_settings",
)
_PAGE_PATHS = {
    "page_dashboard": "/",
    "page_history": "/history",
    "page_performance": "/performance",
    "page_settings": "/settings",
}
# The route stamps its audit post with the wall-clock second; two drives compared with each other
# would differ whenever they straddle a second boundary, so only this stamp is masked.
_AUDIT_STAMP = re.compile(r"at \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ET")
_AUDIT_STAMP_MASK = "at <stamp> ET"


def _mask_audit_stamp(body):
    """Mask the wall-clock stamp inside a recorded post body's ``content`` string."""
    if isinstance(body, dict) and isinstance(body.get("content"), str):
        return {**body, "content": _AUDIT_STAMP.sub(_AUDIT_STAMP_MASK, body["content"])}
    return body


_WORLDS = {"api_state_open": "open_two_accounts", "api_state_frozen": "closed_frozen_two_accounts"}


def install_env(monkeypatch, mapping: dict) -> None:
    """Every ``.env`` read of the app sees ``mapping`` (a fresh copy per read)."""
    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: dict(mapping))
    monkeypatch.setattr(app_module._dotenv_module, "dotenv_values", lambda *_a, **_k: dict(mapping))


def refuse_network(monkeypatch) -> list:
    """Any HTTP call not replaced by a fake raises and is recorded. Returns the record."""
    attempted: list = []

    def _refuse(self, method, url, *args, **kwargs):
        attempted.append((method, url))
        raise AssertionError("an outgoing HTTP call reached the real transport")

    monkeypatch.setattr(requests.sessions.Session, "request", _refuse)
    return attempted


def outcome_of(call) -> list:
    """``["status", code]`` for an answered request, ``["raised", type name]`` for an exception."""
    try:
        response = call()
    except Exception as exc:  # noqa: BLE001 - the exception type IS the recorded outcome
        return ["raised", type(exc).__name__]
    return ["status", response.status_code]


def capture_pages(monkeypatch) -> dict:
    """Replace ``render_template`` with a capture of the ``meta`` each page renders with."""
    seen: dict = {}

    def _capture(template_name, **context):
        seen[template_name] = context.get("meta")
        return ""

    monkeypatch.setattr(app_module, "render_template", _capture)
    return seen


def sell_account_drive(monkeypatch, client, mapping: dict) -> dict:
    """POST /api/sell_account with a confirmed request. Discord and the thread are fakes."""
    install_env(monkeypatch, mapping)
    posts: list = []
    threads: list = []

    class _CapturingThread:
        def __init__(self, target=None, args=(), **_kw):
            threads.append({"target": getattr(target, "__name__", None), "args": tuple(args)})

        def start(self):
            pass

    def _fake_post(url, **kwargs):
        posts.append({"url": url, "json": _mask_audit_stamp(kwargs.get("json"))})
        reply = MagicMock()
        reply.status_code = 200
        return reply

    monkeypatch.setattr(app_module.threading, "Thread", _CapturingThread)
    monkeypatch.setattr(app_module.requests, "post", _fake_post)
    payload = json.loads(PANIC_FIXTURE.read_text(encoding="utf-8"))
    response = client.post("/api/sell_account", json=payload)
    return {
        "status": response.status_code,
        "body": response.get_json(),
        "posts": posts,
        "threads": threads,
    }


def arm_live_drive(monkeypatch, client, mapping: dict, name: str) -> dict:
    """POST the arm-live ceremony for a fresh sleeve; the row lives in the per-test database."""
    install_env(monkeypatch, mapping)
    sleeve_id = database.create_sleeve(name, 5000.0, envelope_json="{}")
    before = database.get_sleeve(sleeve_id)["status"]
    response = client.post(
        f"/api/sleeves/{sleeve_id}/arm-live",
        json={"confirm_sleeve_id": sleeve_id, "confirm_phrase": "ARM LIVE TRADING"},
    )
    return {
        "status": response.status_code,
        "message": (response.get_json() or {}).get("message"),
        "before": before,
        "after": database.get_sleeve(sleeve_id)["status"],
    }


def bare_key_outcome(site: str, monkeypatch, client, tmp_path) -> list:
    """The outcome of one site when ``.env`` holds ``LIVE_EXECUTION`` with no ``=``."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    mapping = {"LIVE_EXECUTION": None}
    if site in _WORLDS:
        from tests.app._state_poll_worlds import WORLDS

        world = WORLDS[_WORLDS[site]](monkeypatch, tmp_path)
        install_env(monkeypatch, mapping)
        return outcome_of(lambda: world.get("/api/state"))
    if site == "header_status":
        now = datetime(2026, 10, 22, 10, 43, 12, tzinfo=ZoneInfo("America/New_York"))
        monkeypatch.setattr(app_module, "_now_et", lambda: now)
        monkeypatch.setattr(
            app_module.database,
            "load_state",
            lambda: {"last_successful_cycle_at": (now - timedelta(seconds=20)).isoformat()},
        )
        install_env(monkeypatch, mapping)
        return outcome_of(lambda: client.get("/api/header-status"))
    if site in _PAGE_PATHS:
        install_env(monkeypatch, mapping)
        capture_pages(monkeypatch)
        return outcome_of(lambda: client.get(_PAGE_PATHS[site]))
    if site == "sell_account":
        install_env(
            monkeypatch,
            {
                **mapping,
                "COMPOSER_KEY_ID": "test-key-id",
                "COMPOSER_SECRET": "test-secret",
                "DISCORD_WEBHOOK_URL": "https://discord.test/webhook",
            },
        )
        monkeypatch.setattr(app_module.requests, "post", lambda *a, **k: MagicMock())
        payload = json.loads(PANIC_FIXTURE.read_text(encoding="utf-8"))
        return outcome_of(lambda: client.post("/api/sell_account", json=payload))
    if site == "symphony_settings":
        install_env(monkeypatch, mapping)
        return outcome_of(lambda: client.get("/api/symphony-settings/alpha-grove"))
    raise KeyError(site)
